"""Tests for parse_source: turning one walked file into source lines and segments."""

import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_fixtures  # noqa: E402
import parse_source  # noqa: E402
import survey_docx  # noqa: E402
from parse_fixtures import RunSpec  # noqa: E402
from parse_types import Mark, Segment, SourceLine  # noqa: E402
from survey_report import workdir_name  # noqa: E402
from survey_walk import FileEntry, FileKind  # noqa: E402

W = "Wingdings"
# The private-use glyph Word 97 files draw in a Wingdings run.
GLYPH = "\uf041"
# Generous for a tiny fixture; only a pathological file comes near it.
_MAX_BYTES = 10_000_000
# Seconds a fake converter may take.
_TIMEOUT = 60.0


def _run(
    text: str,
    font: str | None = None,
    color: str | None = None,
    symbol: str | None = None,
) -> survey_docx.RunInfo:
    """A RunInfo with only the fields parse_source reads set."""
    return survey_docx.RunInfo(text, font, None, color, None, None, symbol, 0)


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TextLinesTest(unittest.TestCase):
    def test_one_plain_segment_per_line(self) -> None:
        got = parse_source.lines_from_text("31/read.txt", ["a - k", "", "b\t\tx"])
        self.assertEqual([line.number for line in got], [1, 2, 3])
        self.assertEqual(
            got[2].segments, (Segment(marks=(), text="b\t\tx", text_colors=()),)
        )
        self.assertEqual(got[0].text, "a - k")
        self.assertEqual(got[0].rel_path, "31/read.txt")

    @given(st.lists(st.text()))
    def test_fuzz_lines_from_text(self, lines: list[str]) -> None:
        got = parse_source.lines_from_text("r.txt", lines)
        self.assertEqual([line.number for line in got], list(range(1, len(lines) + 1)))
        for line, text in zip(got, lines, strict=True):
            self.assertEqual(line.segments, (Segment((), text, ()),))
            self.assertEqual(line.text, text)


class MarksOfTest(unittest.TestCase):
    def test_symbol_font_glyphs_skip_spaces(self) -> None:
        got = parse_source.marks_of(_run(f"{GLYPH} a", W, "aabbcc"))
        self.assertEqual(
            got, (Mark("Wingdings/F041", "AABBCC"), Mark("Wingdings/0061", "AABBCC"))
        )

    def test_sym_element_in_a_plain_font(self) -> None:
        got = parse_source.marks_of(_run("", "Arial", None, "Wingdings/F043"))
        self.assertEqual(got, (Mark("Wingdings/F043", None),))

    def test_theme_color_is_none(self) -> None:
        got = parse_source.marks_of(_run(GLYPH, W, "theme:accent1"))
        self.assertEqual(got, (Mark("Wingdings/F041", None),))

    def test_plain_font_and_no_font_draw_no_marks(self) -> None:
        self.assertEqual(parse_source.marks_of(_run("x", "Arial")), ())
        self.assertEqual(parse_source.marks_of(_run("x")), ())


class DocxLinesTest(_TempDirCase):
    def lines(self, *paragraphs: Sequence[RunSpec]) -> tuple[SourceLine, ...]:
        path = self.tmp / "x.docx"
        path.write_bytes(parse_fixtures.docx_bytes(paragraphs))
        info = survey_docx.read_docx(path, max_uncompressed=_MAX_BYTES)
        return parse_source.lines_from_docx("30-31/x.docx", info)

    def test_mark_then_title(self) -> None:
        (line,) = self.lines([(GLYPH, W, "778899"), ("Some Film", None, None)])
        self.assertEqual(
            line.segments,
            (Segment((Mark("Wingdings/F041", "778899"),), "Some Film", ()),),
        )
        self.assertEqual(line.text, "Some Film")
        self.assertEqual((line.rel_path, line.number), ("30-31/x.docx", 1))

    def test_legend_and_entry_share_a_line(self) -> None:
        (line,) = self.lines(
            [
                (GLYPH, W, "778899"), ("-", None, None),
                (GLYPH, W, "445566"), ("-", None, None),
                (GLYPH, W, "112233"),
                (GLYPH, W, "778899"), ("Late Film", None, None),
            ]
        )  # fmt: skip
        self.assertEqual([s.text for s in line.segments], ["-", "-", "", "Late Film"])
        self.assertEqual(line.segments[3].marks, (Mark("Wingdings/F041", "778899"),))
        self.assertEqual(line.text, "--Late Film")

    def test_two_entries_on_one_line(self) -> None:
        (line,) = self.lines(
            [
                (GLYPH, W, "778899"), ("First ", None, None),
                (GLYPH, W, "445566"), ("Second", None, None),
            ]
        )  # fmt: skip
        self.assertEqual([s.text for s in line.segments], ["First ", "Second"])

    def test_leading_text_before_any_mark_is_its_own_segment(self) -> None:
        (line,) = self.lines(
            [(" ", None, "778899"), (GLYPH, W, "778899"), ("T", None, None)]
        )
        self.assertEqual(line.segments[0].marks, ())
        self.assertEqual(line.segments[0].text_colors, ())
        self.assertEqual(line.segments[1].text, "T")

    def test_blank_symbol_runs_and_auto_color(self) -> None:
        (line,) = self.lines([(" ", W, "112233"), (GLYPH, W, None), ("T", None, None)])
        self.assertEqual(
            line.segments, (Segment((Mark("Wingdings/F041", None),), "T", ()),)
        )

    def test_colored_title_text_is_recorded(self) -> None:
        (line,) = self.lines([("Red Title", None, "112233")])
        self.assertEqual(line.segments, (Segment((), "Red Title", ("112233",)),))

    def test_plain_letter_in_a_symbol_font(self) -> None:
        (line,) = self.lines([("a", "Webdings", "445566"), ("Film", None, None)])
        self.assertEqual(line.segments[0].marks, (Mark("Webdings/0061", "445566"),))
        self.assertEqual(line.text, "Film")

    def test_two_glyphs_in_one_run_open_two_segments(self) -> None:
        (line,) = self.lines([(GLYPH * 2, W, "778899"), ("T", None, None)])
        self.assertEqual([s.text for s in line.segments], ["", "T"])

    def test_paragraphs_are_numbered_and_an_empty_one_has_no_segments(self) -> None:
        got = self.lines([("A", None, None)], [], [("B", None, None)])
        self.assertEqual([line.number for line in got], [1, 2, 3])
        self.assertEqual((got[1].segments, got[1].text), ((), ""))

    def test_sym_element_in_a_plain_run_adds_no_text(self) -> None:
        document = docx.Document()
        run = document.add_paragraph().add_run("Film")
        run.font.name = "Arial"
        sym = OxmlElement("w:sym")
        sym.set(qn("w:font"), W)
        sym.set(qn("w:char"), "F041")
        run._r.insert(1, sym)  # after w:rPr, before w:t, as Word writes it
        path = self.tmp / "sym.docx"
        document.save(str(path))
        info = survey_docx.read_docx(path, max_uncompressed=_MAX_BYTES)
        (line,) = parse_source.lines_from_docx("30-31/sym.docx", info)
        self.assertEqual(
            line.segments, (Segment((Mark("Wingdings/F041", None),), "Film", ()),)
        )
        self.assertEqual(line.text, "Film")


class FakeConverters:
    """Stands in for both converters: writes each one's prepared .docx to its output.

    ``produce`` maps a converter name ("textutil", "libreoffice") to the bytes it
    writes; a converter missing from it writes nothing and exits 1.
    """

    def __init__(self, produce: Mapping[str, bytes]) -> None:
        self.produce = produce
        self.calls: list[str] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> int:
        name = "textutil" if argv[0] == "textutil" else "libreoffice"
        self.calls.append(name)
        if name not in self.produce:
            return 1
        if name == "textutil":
            target = Path(argv[argv.index("-output") + 1])
        else:
            target = Path(argv[argv.index("--outdir") + 1]) / (
                Path(argv[-1]).stem + ".docx"
            )
        target.write_bytes(self.produce[name])
        return 0


def _one_line_docx(text: str) -> bytes:
    """A .docx whose only paragraph is ``text`` in a plain run."""
    return parse_fixtures.docx_bytes([[(text, None, None)]])


class ReadSourceTest(_TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "src"
        self.root.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()

    def ctx(
        self,
        produce: Mapping[str, bytes] | None = None,
        *,
        soffice: Path | None = Path("soffice"),
        max_bytes: int = _MAX_BYTES,
    ) -> parse_source.SourceContext:
        self.runner = FakeConverters(produce or {})
        return parse_source.SourceContext(
            self.work, self.runner, soffice, max_bytes, _TIMEOUT
        )

    def entry(self, rel_path: str, data: bytes, kind: FileKind) -> FileEntry:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return FileEntry(rel_path, ".", kind, len(data))

    def read(self, entry: FileEntry, ctx: parse_source.SourceContext) -> list[str]:
        return [line.text for line in parse_source.read_source(entry, self.root, ctx)]

    def error(self, entry: FileEntry, ctx: parse_source.SourceContext) -> str:
        with self.assertRaises(parse_source.SourceError) as caught:
            parse_source.read_source(entry, self.root, ctx)
        return str(caught.exception)

    def test_txt_reads_through_read_text(self) -> None:
        entry = self.entry("31/read.txt", b"one - k\r\ntwo\r\n", "txt")
        self.assertEqual(self.read(entry, self.ctx()), ["one - k", "two"])

    def test_docx_reads_through_read_docx(self) -> None:
        entry = self.entry("31/seen.docx", _one_line_docx("Film"), "docx")
        self.assertEqual(self.read(entry, self.ctx()), ["Film"])

    def test_doc_prefers_libreoffice(self) -> None:
        entry = self.entry("31/seen.doc", b"old word", "doc")
        ctx = self.ctx(
            {"textutil": _one_line_docx("TU"), "libreoffice": _one_line_docx("LO")}
        )
        self.assertEqual(self.read(entry, ctx), ["LO"])
        self.assertEqual(self.runner.calls, ["textutil", "libreoffice"])
        self.assertFalse((self.work / workdir_name("31/seen.doc")).exists())
        self.assertEqual((self.root / "31" / "seen.doc").read_bytes(), b"old word")

    def test_doc_falls_back_to_textutil(self) -> None:
        entry = self.entry("31/seen.doc", b"old word", "doc")
        self.assertEqual(
            self.read(entry, self.ctx({"textutil": _one_line_docx("TU")})), ["TU"]
        )

    def test_doc_without_soffice_uses_textutil(self) -> None:
        entry = self.entry("31/seen.doc", b"old word", "doc")
        ctx = self.ctx({"textutil": _one_line_docx("TU")}, soffice=None)
        self.assertEqual(self.read(entry, ctx), ["TU"])
        self.assertEqual(self.runner.calls, ["textutil"])

    def test_doc_no_converter_produced_output(self) -> None:
        entry = self.entry("31/seen.doc", b"old word", "doc")
        self.assertEqual(self.error(entry, self.ctx()), "no converter produced output")
        self.assertFalse((self.work / workdir_name("31/seen.doc")).exists())

    def test_doc_too_large(self) -> None:
        entry = self.entry("31/seen.doc", b"old word", "doc")
        self.assertEqual(self.error(entry, self.ctx(max_bytes=3)), "FileTooLarge")
        self.assertEqual(self.runner.calls, [])

    def test_txt_too_large(self) -> None:
        entry = self.entry("31/read.txt", b"a long line", "txt")
        self.assertEqual(self.error(entry, self.ctx(max_bytes=3)), "FileTooLarge")

    def test_bad_docx(self) -> None:
        entry = self.entry("31/seen.docx", b"not a zip", "docx")
        self.assertEqual(self.error(entry, self.ctx()), "BadDocx")

    def test_missing_file_is_an_os_error(self) -> None:
        entry = FileEntry("31/gone.txt", ".", "txt", 0)
        self.assertEqual(self.error(entry, self.ctx()), "FileNotFoundError")

    def test_other_kinds_are_refused(self) -> None:
        entry = self.entry("31/x.pdf", b"%PDF", "other")
        self.assertEqual(self.error(entry, self.ctx()), "not a source file")

    def test_error_message_holds_no_path(self) -> None:
        entry = FileEntry("31/gone.doc", ".", "doc", 0)
        message = self.error(entry, self.ctx())
        self.assertNotIn(str(self.root), message)
        self.assertEqual(message, "FileNotFoundError")


if __name__ == "__main__":
    unittest.main()
