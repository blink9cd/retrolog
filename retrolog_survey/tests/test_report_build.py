"""Tests for survey_report's report: aggregating surveys, rendering and writing them."""

import dataclasses
import functools
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_run")
import bounded_run  # noqa: E402 - after the bootstrap above
import survey_docx  # noqa: E402 - after the sys.path insert above
import survey_report  # noqa: E402 - after the sys.path insert above
import survey_write  # noqa: E402 - after the sys.path insert above
from report_shapes import as_dict as _dict  # noqa: E402 - after the sys.path insert above
from report_shapes import as_list as _list  # noqa: E402 - after the sys.path insert above
from survey_lines import LineKind  # noqa: E402 - after the sys.path insert above

# Seconds a bare-interpreter import check may take.
_BARE_RUN_TIMEOUT = 60

# A hand-built FileSurvey in group "films"; every other field is a keyword.
_survey = functools.partial(survey_report.FileSurvey, group="films")


def _line(number: int, kind: LineKind, text: str) -> survey_report.SurveyLine:
    return survey_report.SurveyLine(number, kind, text)


_IMAGE = survey_docx.ImageInfo("image/png", 9, "abc123abc123")


class BuildReportTest(unittest.TestCase):
    def setUp(self) -> None:
        first = _survey(
            "films/a.txt",
            kind="txt",
            encoding="mac_roman",
            ambiguous=True,
            line_endings={"crlf": 0, "cr": 3, "lf": 0},
            lines=(
                _line(1, "unclassified", "Film A"),
                _line(2, "blank", ""),
                _line(3, "letter_code", "Film B g"),
            ),
            counts={"letter_tokens": {"g": 1}, "non_ascii": {"U+00E9 X": 2}},
        )
        second = _survey(
            "films/b.docx",
            kind="docx",
            lines=(_line(4, "unclassified", "Film C"), _line(5, "image", "[img:abc]")),
            counts={"letter_tokens": {"g": 2}, "fonts": {"Times": 1}},
            images=(_IMAGE,),
            image_refs={"abc123abc123": 2},
            error="BadDocx: something",
        )
        self.files = [second, first, _survey("films/c.pdf", kind="other")]
        options = survey_report.Options(group_depth=1, samples=1, converters=False)
        self.report = survey_report.build_report(
            self.files, options, {"textutil": "skipped", "libreoffice": "skipped"}
        )

    def group(self) -> dict[str, object]:
        groups = _list(self.report["groups"])
        self.assertEqual(len(groups), 1)
        return _dict(groups[0])

    def test_header(self) -> None:
        self.assertEqual(self.report["schema_version"], 1)
        self.assertEqual(
            self.report["options"],
            {"group_depth": 1, "samples": 1, "converters": False},
        )

    def test_counts_are_summed(self) -> None:
        group = self.group()
        self.assertEqual(group["group"], "films")
        self.assertEqual(
            group["files"],
            {"txt": 1, "doc": 0, "docx": 1, "other": 1, "symlink": 0,
             "excluded": 0, "unreadable": 0},
        )  # fmt: skip
        self.assertEqual(group["encodings"], {"mac_roman": 1})
        self.assertEqual(group["ambiguous_encodings"], 1)
        self.assertEqual(group["line_endings"], {"crlf": 0, "cr": 3, "lf": 0})
        line_kinds = _dict(group["line_kinds"])
        self.assertEqual(
            (line_kinds["unclassified"], line_kinds["blank"], line_kinds["symbol"]),
            (2, 1, 0),
        )
        self.assertEqual(group["letter_tokens"], {"g": 3})
        self.assertEqual(group["fonts"], {"Times": 1})
        self.assertEqual(group["colors"], {})

    def test_images_errors_and_conversions(self) -> None:
        group = self.group()
        self.assertEqual(
            group["images"],
            [{"content_type": "image/png", "byte_size": 9,
              "sha256_12": "abc123abc123", "count": 2}],
        )  # fmt: skip
        self.assertEqual(
            group["errors"], [{"file": "films/b.docx", "error": "BadDocx: something"}]
        )
        self.assertEqual(group["conversions"], [])

    def test_samples_take_the_first_lines_per_kind_and_skip_blanks(self) -> None:
        self.assertEqual(
            self.group()["samples"],
            {
                "unclassified": [{"file": "films/a.txt", "line": 1, "text": "Film A"}],
                "letter_code": [{"file": "films/a.txt", "line": 3, "text": "Film B g"}],
                "image": [{"file": "films/b.docx", "line": 5, "text": "[img:abc]"}],
            },
        )

    def test_groups_are_sorted(self) -> None:
        options = survey_report.Options(group_depth=1, samples=0, converters=True)
        report = survey_report.build_report(
            [
                _survey("b/x.txt", kind="txt"),
                dataclasses.replace(_survey("a/y.txt", kind="txt"), group="a"),
            ],
            options,
            {},
        )
        groups = [_dict(group) for group in _list(report["groups"])]
        self.assertEqual([g["group"] for g in groups], ["a", "films"])
        self.assertEqual(groups[0]["samples"], {})


class RenderMarkdownTest(unittest.TestCase):
    def test_shows_samples_errors_conversions_and_images(self) -> None:
        conversion = {
            "file": "films/old.doc",
            "textutil": {"status": "ok", "detail": "exit 0", "only": ["font:Times"]},
            "libreoffice": {"status": "absent", "detail": "", "only": []},
            "both": [],
        }
        files = [
            _survey(
                "films/rated.docx",
                kind="docx",
                lines=(_line(2, "colored", "Film B [#00B050]"),),
                counts={"colors": {"00B050": 1}},
                images=(_IMAGE,),
                image_refs={"abc123abc123": 1},
            ),
            _survey(
                "films/old.doc",
                kind="doc",
                conversion=conversion,
                error="ConversionFailed: x",
            ),
        ]
        options = survey_report.Options(group_depth=1, samples=5, converters=True)
        text = survey_report.render_markdown(
            survey_report.build_report(
                files, options, {"textutil": "present", "libreoffice": "absent"}
            )
        )
        for expected in (
            "## Group `films`",
            "`films/rated.docx` line 2: Film B [#00B050]",
            "| 00B050 | 1 |",
            "`films/old.doc`: ConversionFailed: x",
            "`films/old.doc`: textutil ok (exit 0), only font:Times;"
            " libreoffice absent; both: none",
            "`abc123abc123` image/png, 9 bytes, 1 reference(s)",
            "textutil present, libreoffice absent",
        ):
            self.assertIn(expected, text)
        self.assertTrue(text.endswith("\n"))

    def test_untrusted_text_cannot_break_the_layout(self) -> None:
        files = [
            _survey(
                "films/a`b.docx",
                kind="docx",
                lines=(_line(1, "unclassified", "A|B `c` <b>x</b>\nnext *y* [z](u)"),),
                counts={"fonts": {"Wing|dings": 1}},
                error="BadDocx: <odd> | `x`",
            )
        ]
        options = survey_report.Options(group_depth=1, samples=5, converters=False)
        text = survey_report.render_markdown(
            survey_report.build_report(files, options, {})
        )
        self.assertIn(
            "unclassified: ``films/a`b.docx`` line 1: "
            "A\\|B \\`c\\` \\<b\\>x\\</b\\>\N{DOWNWARDS ARROW WITH CORNER LEFTWARDS}"
            "next *y* [z]\\(u)",
            text,
        )
        self.assertIn("| Wing\\|dings | 1 |", text)
        self.assertIn("``films/a`b.docx``: BadDocx: \\<odd\\> \\| \\`x\\`", text)
        self.assertNotIn("<b>", text)

    def test_empty_group_sections_say_none(self) -> None:
        options = survey_report.Options(group_depth=0, samples=5, converters=False)
        text = survey_report.render_markdown(
            survey_report.build_report([_survey("a.txt", kind="txt")], options, {})
        )
        self.assertIn("### Errors\n\nNone.", text)


class WriteAtomicTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_writes_and_leaves_no_temporary_file(self) -> None:
        target = self.dir / "report.md"
        target.write_bytes(b"old")
        survey_report.write_atomic(target, "new \N{EN DASH}\n")
        self.assertEqual(target.read_bytes(), "new \N{EN DASH}\n".encode())
        self.assertEqual(os.listdir(self.dir), ["report.md"])

    def test_surrogate_escaped_text_is_written_escaped(self) -> None:
        # Regression: a relative path holding a non-UTF-8 file name (decoded with
        # surrogate escapes) raised UnicodeEncodeError while writing the report.
        target = self.dir / "report.md"
        survey_report.write_atomic(target, "caf\udce9.txt\n")
        self.assertEqual(target.read_bytes(), b"caf\\udce9.txt\n")

    def test_failure_leaves_no_temporary_file(self) -> None:
        target = self.dir / "report.md"
        target.mkdir()
        (target / "keep").write_bytes(b"")
        with self.assertRaises(OSError):
            survey_report.write_atomic_bytes(target, b"x")
        self.assertEqual(os.listdir(self.dir), ["report.md"])


class SurveyWriteTest(unittest.TestCase):
    """The writers live in survey_write, which needs no .docx reader."""

    def test_survey_report_re_exports_the_same_writers(self) -> None:
        for name in ("describe_error", "write_atomic", "write_atomic_bytes"):
            self.assertIs(getattr(survey_report, name), getattr(survey_write, name))

    def test_survey_write_imports_with_no_installed_package(self) -> None:
        # -S leaves every site-packages folder (python-docx's) off the path, and -I
        # ignores PYTHONPATH, as for a tool run on a stock interpreter.
        code = f"import sys; sys.path.insert(0, {str(_TESTS.parent / 'src')!r}); "
        code += "import survey_write; print('docx' in sys.modules)"
        # run_bounded kills the whole process tree on a timeout before it reads the
        # captured pipes, so the bound holds even if a descendant kept them open.
        done = bounded_run.run_bounded(
            [sys.executable, "-I", "-S", "-c", code], timeout=_BARE_RUN_TIMEOUT
        )
        self.assertEqual((done.returncode, done.stdout), (0, "False\n"), done.stderr)


if __name__ == "__main__":
    unittest.main()
