"""Tests for survey_report: surveying one file, aggregating a report, and writing it."""

import hashlib
import shutil
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import build_fixtures  # noqa: E402 - after the sys.path insert above
from report_shapes import as_dict as _dict  # noqa: E402 - after the sys.path insert above
from report_shapes import as_list as _list  # noqa: E402 - after the sys.path insert above
import survey_docx  # noqa: E402 - after the sys.path insert above
import survey_report  # noqa: E402 - after the sys.path insert above
from survey_walk import FileEntry, FileKind  # noqa: E402 - after the sys.path insert above

# Generous for every fixture here; the size-limit tests set their own.
_MAX_BYTES = 10_000_000
# Seconds a fake converter may take.
_TIMEOUT = 60.0
_PNG_SHA = hashlib.sha256(build_fixtures.make_tiny_png()).hexdigest()[:12]
# describe_paragraph of each paragraph of make_rated_docx, in document order.
_DESCRIBED = (
    "Film A [Wingdings/F043 #0070C0]",
    "Film B [#00B050]",
    "Film C [hl:yellow]",
    "Film D [shd:FF0000]",
    "Film E \N{THUMBS UP SIGN}",
    f"[img:{_PNG_SHA}]",
    "g Film F",
    "C [Wingdings 'C' U+0043]",
)
_KINDS = (
    "symbol", "colored", "colored", "colored", "emoji", "image", "letter_code", "symbol",
)  # fmt: skip


def _run(
    text: str = "",
    *,
    font: str | None = None,
    style: str | None = None,
    color: str | None = None,
    images: int = 0,
    image_ids: tuple[str, ...] = (),
) -> survey_docx.RunInfo:
    """A RunInfo with no formatting except what the keywords name."""
    return survey_docx.RunInfo(
        text, font, style, color, None, None, None, images, image_ids
    )


class FakeRunner:
    """Stands in for the converters: copies a prepared .docx to each one's output."""

    def __init__(self, produce: Path | None, ok: Sequence[str]) -> None:
        self.produce, self.ok = produce, set(ok)
        self.calls: list[str] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> int:
        name = "textutil" if argv[0] == "textutil" else "libreoffice"
        self.calls.append(name)
        if name not in self.ok or self.produce is None:
            return 1
        if name == "textutil":
            target = Path(argv[argv.index("-output") + 1])
        else:
            target = Path(argv[argv.index("--outdir") + 1]) / (
                Path(argv[-1]).stem + ".docx"
            )
        shutil.copyfile(self.produce, target)
        return 0


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve() / "tree"
        self.root.mkdir()
        self.work = Path(self._tmp.name).resolve() / "work"
        self.work.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def context(
        self,
        *,
        runner: FakeRunner | None = None,
        convert: bool = True,
        max_bytes: int = _MAX_BYTES,
    ) -> survey_report.SurveyContext:
        return survey_report.SurveyContext(
            max_bytes=max_bytes,
            timeout=_TIMEOUT,
            run=runner or FakeRunner(None, ()),
            soffice=Path("soffice"),
            workdir=self.work if convert else None,
        )

    def survey(
        self,
        rel_path: str,
        kind: FileKind,
        ctx: survey_report.SurveyContext | None = None,
    ) -> survey_report.FileSurvey:
        size = (self.root / rel_path).stat().st_size
        entry = FileEntry(rel_path, rel_path.split("/")[0], kind, size)
        return survey_report.survey_file(entry, self.root, ctx or self.context())


class DescribeParagraphTest(unittest.TestCase):
    info: survey_docx.DocxInfo

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rated.docx"
            build_fixtures.make_rated_docx(path)
            cls.info = survey_docx.read_docx(path, max_uncompressed=_MAX_BYTES)

    def test_each_fixture_paragraph(self) -> None:
        self.assertEqual(
            tuple(map(survey_report.describe_paragraph, self.info.paragraphs)),
            _DESCRIBED,
        )

    def test_each_fixture_paragraph_kind(self) -> None:
        kinds = tuple(survey_report.classify_paragraph(p) for p in self.info.paragraphs)
        self.assertEqual(kinds, _KINDS)

    def test_theme_color_style_and_unresolved_image(self) -> None:
        runs = (
            _run("Book A", color="theme:accent1", style="Rated"),
            _run("", images=2, image_ids=("abc123abc123",)),
        )
        paragraph = survey_docx.ParagraphInfo(1, None, runs)
        self.assertEqual(
            survey_report.describe_paragraph(paragraph),
            "Book A [theme:accent1 style:Rated] [img:abc123abc123 img]",
        )

    def test_symbol_font_text_lists_every_character(self) -> None:
        paragraph = survey_docx.ParagraphInfo(1, None, (_run("CD", font="Webdings"),))
        self.assertEqual(
            survey_report.describe_paragraph(paragraph),
            "CD [Webdings 'CD' U+0043 U+0044]",
        )

    def test_blank_symbol_font_run_is_not_a_symbol(self) -> None:
        paragraph = survey_docx.ParagraphInfo(
            1, None, (_run("Film A"), _run(" ", font="Wingdings"))
        )
        self.assertNotEqual(survey_report.classify_paragraph(paragraph), "symbol")
        self.assertEqual(survey_report.describe_paragraph(paragraph), "Film A ")
        info = survey_docx.DocxInfo((paragraph,), (), ())
        self.assertEqual(survey_report.docx_counts(info)["symbols"], {})

    def test_view_of_ignores_color_on_a_blank_run(self) -> None:
        paragraph = survey_docx.ParagraphInfo(
            1, None, (_run("Film A"), _run(" ", color="FF0000"))
        )
        view = survey_report.view_of(paragraph)
        self.assertEqual(
            (view.text, view.has_color, view.has_symbol_run, view.has_image),
            ("Film A ", False, False, False),
        )


class SurveyFileTest(_TempDirCase):
    def test_text_file(self) -> None:
        build_fixtures.write_text_fixture(
            self.root / "a.txt",
            "Film A g\n\nFilm B \N{EN DASH} note\nFilm C \N{THUMBS UP SIGN}\n",
            encoding="utf-8",
            newline="\r\n",
        )
        result = self.survey("a.txt", "txt")
        self.assertEqual((result.encoding, result.ambiguous), ("utf-8", False))
        self.assertEqual(result.line_endings, {"crlf": 4, "cr": 0, "lf": 0})
        self.assertEqual(
            [(line.number, line.kind) for line in result.lines],
            [(1, "letter_code"), (2, "blank"), (3, "unclassified"), (4, "emoji")],
        )
        self.assertEqual(result.counts["letter_tokens"], {"g": 1})
        self.assertEqual(result.counts["emoji"], {"U+1F44D": 1})
        self.assertEqual(
            result.counts["non_ascii"],
            {"U+2013 EN DASH": 1, "U+1F44D THUMBS UP SIGN": 1},
        )
        self.assertIsNone(result.error)

    def test_word_file(self) -> None:
        build_fixtures.make_rated_docx(self.root / "rated.docx")
        result = self.survey("rated.docx", "docx")
        self.assertEqual(tuple(line.text for line in result.lines), _DESCRIBED)
        self.assertEqual(
            result.counts["symbols"], {"Wingdings/F043": 1, "Wingdings/0043": 1}
        )
        self.assertEqual(result.counts["colors"], {"0070C0": 1, "00B050": 1})
        self.assertEqual(result.counts["highlights"], {"yellow": 1})
        self.assertEqual(result.counts["shadings"], {"FF0000": 1})
        self.assertEqual(result.counts["fonts"], {"Wingdings": 1})
        self.assertEqual(result.counts["styles"], {"paragraph:Normal": 8})
        self.assertEqual(result.image_refs, {_PNG_SHA: 1})
        self.assertEqual(result.blobs, {_PNG_SHA: build_fixtures.make_tiny_png()})
        self.assertEqual(result.images[0].content_type, "image/png")

    def test_run_style_is_counted(self) -> None:
        paragraph = survey_docx.ParagraphInfo(1, "Title", (_run("x", style="Rated"),))
        unstyled = survey_docx.ParagraphInfo(2, None, (_run("y"),))
        info = survey_docx.DocxInfo((paragraph, unstyled), (), ())
        counts = survey_report.docx_counts(info)
        self.assertEqual(counts["styles"], {"paragraph:Title": 1, "run:Rated": 1})

    def test_oversized_text_is_an_error(self) -> None:
        (self.root / "big.txt").write_bytes(b"x" * 100)
        result = self.survey("big.txt", "txt", self.context(max_bytes=10))
        self.assertEqual(
            result.error, "FileTooLarge: 100 bytes exceeds the 10-byte limit"
        )

    def test_broken_word_file_is_an_error(self) -> None:
        (self.root / "broken.docx").write_bytes(b"not a zip")
        self.assertEqual(
            self.survey("broken.docx", "docx").error, "BadDocx: BadZipFile"
        )

    def test_os_error_names_no_path(self) -> None:
        (self.root / "gone.txt").write_bytes(b"x")
        entry = FileEntry("gone.txt", ".", "txt", 1)
        (self.root / "gone.txt").unlink()
        error = survey_report.survey_file(entry, self.root, self.context()).error
        # The OS's own wording: POSIX's strerror, and Windows' message for its error 2.
        if sys.platform == "win32":
            reason = "The system cannot find the file specified"
        else:
            reason = "No such file or directory"
        self.assertEqual(error, f"FileNotFoundError: {reason}")

    def test_unreadable_entry_keeps_its_class_name(self) -> None:
        entry = FileEntry("locked", "locked", "unreadable", 0, "PermissionError")
        result = survey_report.survey_file(entry, self.root, self.context())
        self.assertEqual(result.error, "PermissionError")

    def test_unread_kinds_are_not_opened(self) -> None:
        kinds: tuple[FileKind, ...] = ("other", "symlink", "excluded")
        for kind in kinds:
            entry = FileEntry(f"missing.{kind}", ".", kind, 0)
            result = survey_report.survey_file(entry, self.root, self.context())
            self.assertEqual(
                (result.kind, result.error, result.lines), (kind, None, ())
            )

    def test_unexpected_reader_failure_fails_one_file(self) -> None:
        # Regression: an exception class no reader was written to expect aborted the
        # whole run. It now fails that one file, by class name only.
        def reader(*_args: object) -> survey_report.FileSurvey:
            raise LookupError("document text, or a path")

        entry = FileEntry("a.txt", ".", "txt", 0)
        with mock.patch.dict(survey_report._READERS, {"txt": reader}):
            result = survey_report.survey_file(entry, self.root, self.context())
        self.assertEqual(result.error, "LookupError")


class WorkdirNameTest(unittest.TestCase):
    def test_surrogate_escaped_name_hashes_its_original_bytes(self) -> None:
        # Regression: a file name that is not valid UTF-8, as Python decodes it on a
        # volume that allows one, raised UnicodeEncodeError while naming the .doc's
        # work directory.
        name = survey_report.workdir_name("caf\udce9.doc")
        self.assertEqual(name, hashlib.sha256(b"caf\xe9.doc").hexdigest()[:16])


class DescribeErrorTest(unittest.TestCase):
    def test_os_error_without_strerror_is_its_class_name(self) -> None:
        self.assertEqual(survey_report.describe_error(OSError()), "OSError")

    def test_other_error_without_message(self) -> None:
        self.assertEqual(survey_report.describe_error(survey_docx.BadDocx()), "BadDocx")


class SurveyDocTest(_TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.produce = self.work.parent / "converted.docx"
        build_fixtures.make_rated_docx(self.produce)
        (self.root / "films").mkdir()
        (self.root / "films" / "old.doc").write_bytes(b"a stand-in .doc")

    def test_libreoffice_output_is_preferred(self) -> None:
        runner = FakeRunner(self.produce, ("textutil", "libreoffice"))
        result = self.survey("films/old.doc", "doc", self.context(runner=runner))
        self.assertEqual(runner.calls, ["textutil", "libreoffice"])
        self.assertEqual(tuple(line.text for line in result.lines), _DESCRIBED)
        conversion = result.conversion
        assert conversion is not None
        self.assertEqual(conversion["file"], "films/old.doc")
        self.assertEqual(
            conversion["textutil"], {"status": "ok", "detail": "exit 0", "only": []}
        )
        self.assertIn("sym:Wingdings/F043", _list(conversion["both"]))
        # The per-file work directory is gone; the shared one stays for the caller.
        self.assertEqual(list(self.work.iterdir()), [])

    def test_textutil_alone_is_used(self) -> None:
        runner = FakeRunner(self.produce, ("textutil",))
        result = self.survey("films/old.doc", "doc", self.context(runner=runner))
        self.assertEqual(len(result.lines), len(_DESCRIBED))
        conversion = result.conversion
        assert conversion is not None
        self.assertEqual(conversion["both"], [])
        self.assertEqual(_dict(conversion["libreoffice"])["status"], "failed")
        self.assertIn("font:Wingdings", _list(_dict(conversion["textutil"])["only"]))

    def test_every_converter_failing_is_an_error(self) -> None:
        result = self.survey(
            "films/old.doc", "doc", self.context(runner=FakeRunner(None, ()))
        )
        self.assertEqual(
            result.error, "ConversionFailed: no converter produced a readable .docx"
        )
        self.assertIsNotNone(result.conversion)

    def test_no_convert_counts_only(self) -> None:
        runner = FakeRunner(self.produce, ("textutil",))
        result = self.survey(
            "films/old.doc", "doc", self.context(runner=runner, convert=False)
        )
        self.assertEqual(
            (result.lines, result.conversion, result.error), ((), None, None)
        )
        self.assertEqual(runner.calls, [])

    def test_oversized_doc_is_not_converted(self) -> None:
        runner = FakeRunner(self.produce, ("textutil",))
        result = self.survey(
            "films/old.doc", "doc", self.context(runner=runner, max_bytes=3)
        )
        self.assertTrue(str(result.error).startswith("FileTooLarge: "))
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
