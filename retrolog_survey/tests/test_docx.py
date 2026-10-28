"""Tests for survey_docx: run formatting, symbol characters and images in a .docx."""

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import docx
from docx.document import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import build_fixtures  # noqa: E402
import survey_docx  # noqa: E402


class ReadDocxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "rated.docx"
        build_fixtures.make_rated_docx(self.path)
        self.info = survey_docx.read_docx(self.path, max_uncompressed=10_000_000)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _runs(self, index: int) -> tuple[survey_docx.RunInfo, ...]:
        return next(p.runs for p in self.info.paragraphs if p.index == index)

    def test_sym_element_is_read_with_its_color(self) -> None:
        sym = [r for r in self._runs(1) if r.symbol]
        self.assertEqual(
            [(r.symbol, r.color) for r in sym], [("Wingdings/F043", "0070C0")]
        )

    def test_color_highlight_shading(self) -> None:
        self.assertEqual(self._runs(2)[0].color, "00B050")
        self.assertEqual(self._runs(3)[0].highlight, "yellow")
        self.assertEqual(self._runs(4)[0].shading, "FF0000")

    def test_emoji_text_survives(self) -> None:
        self.assertIn("👍", self._runs(5)[0].text)

    def test_image_counted_and_hashed(self) -> None:
        self.assertEqual(sum(r.images for r in self._runs(6)), 1)
        self.assertEqual(self.info.images[0].content_type, "image/png")
        self.assertEqual(len(self.info.images[0].sha256_12), 12)

    def test_table_cell_paragraph_is_included_in_order(self) -> None:
        self.assertEqual(self._runs(7)[0].text, "g Film F")

    def test_symbol_font_run(self) -> None:
        self.assertEqual(
            (self._runs(8)[0].font, self._runs(8)[0].text), ("Wingdings", "C")
        )

    def test_expansion_limit(self) -> None:
        with self.assertRaises(survey_docx.BadDocx):
            survey_docx.read_docx(self.path, max_uncompressed=10)

    def test_not_a_zip(self) -> None:
        bad = Path(self._tmp.name) / "bad.docx"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(survey_docx.BadDocx):
            survey_docx.read_docx(bad, max_uncompressed=10_000_000)

    def test_image_bytes_are_kept_and_hashed(self) -> None:
        png = build_fixtures.make_tiny_png()
        self.assertEqual(self.info.blobs, (png,))
        self.assertEqual(self.info.images[0].byte_size, len(png))
        self.assertEqual(
            self.info.images[0].sha256_12, hashlib.sha256(png).hexdigest()[:12]
        )

    def test_paragraphs_are_numbered_through_the_table(self) -> None:
        self.assertEqual([p.index for p in self.info.paragraphs], list(range(1, 9)))
        self.assertEqual(self.info.paragraphs[0].style, "Normal")

    def test_zip_that_is_not_a_word_file(self) -> None:
        other = Path(self._tmp.name) / "other.docx"
        with zipfile.ZipFile(other, "w") as archive:
            archive.writestr("notes.txt", "Film A")
        with self.assertRaises(survey_docx.BadDocx) as caught:
            survey_docx.read_docx(other, max_uncompressed=10_000_000)
        # The class name only: no path, no document text.
        self.assertNotIn(self._tmp.name, str(caught.exception))
        self.assertNotIn("Film", str(caught.exception))


def _element(tag: str, **attributes: str) -> etree._Element:
    """A new WordprocessingML element with namespaced attributes (``w_val`` style)."""
    element = OxmlElement(tag)
    for name, value in attributes.items():
        element.set(qn(name.replace("_", ":", 1)), value)
    return element


def _run_with(*rpr_children: etree._Element) -> etree._Element:
    """A ``w:r`` whose ``w:rPr`` holds `rpr_children`, with the text ``Film A``."""
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    rpr.extend(rpr_children)
    run.append(rpr)
    text = OxmlElement("w:t")
    text.text = "Film A"
    run.append(text)
    return run


class RawXmlTest(unittest.TestCase):
    """Formatting python-docx cannot write, built straight into the body XML."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.document: Document = docx.Document()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _paragraph(self, *runs: etree._Element) -> None:
        """Append a paragraph holding `runs` to the document under construction."""
        p = self.document.add_paragraph()
        p._p.extend(runs)

    def _read(self) -> survey_docx.DocxInfo:
        path = Path(self._tmp.name) / "raw.docx"
        self.document.save(str(path))
        return survey_docx.read_docx(path, max_uncompressed=10_000_000)

    def _only_run(self, *rpr_children: etree._Element) -> survey_docx.RunInfo:
        self._paragraph(_run_with(*rpr_children))
        return self._read().paragraphs[0].runs[0]

    def test_font_falls_back_to_high_ansi(self) -> None:
        run = self._only_run(_element("w:rFonts", w_hAnsi="Symbol"))
        self.assertEqual(run.font, "Symbol")

    def test_font_falls_back_to_complex_script(self) -> None:
        run = self._only_run(_element("w:rFonts", w_cs="Webdings"))
        self.assertEqual(run.font, "Webdings")

    def test_fonts_element_without_a_name(self) -> None:
        self.assertIsNone(self._only_run(_element("w:rFonts")).font)

    def test_theme_color_wins_over_its_fallback_value(self) -> None:
        color = _element("w:color", w_val="4472C4", w_themeColor="accent1")
        self.assertEqual(self._only_run(color).color, "theme:accent1")

    def test_auto_color_is_no_color(self) -> None:
        self.assertIsNone(self._only_run(_element("w:color", w_val="auto")).color)

    def test_auto_shading_is_no_shading(self) -> None:
        run = self._only_run(_element("w:shd", w_val="clear", w_fill="auto"))
        self.assertIsNone(run.shading)

    def test_character_style(self) -> None:
        run = self._only_run(_element("w:rStyle", w_val="Strong"))
        self.assertEqual(run.style, "Strong")

    def test_plain_run_has_no_formatting(self) -> None:
        self._paragraph(_run_with())
        bare = OxmlElement("w:r")
        self._paragraph(bare)
        runs = [p.runs[0] for p in self._read().paragraphs]
        for run in runs:
            self.assertEqual(
                (run.font, run.style, run.color, run.highlight, run.shading),
                (None, None, None, None, None),
            )
            self.assertEqual((run.symbol, run.images), (None, 0))
        self.assertEqual([r.text for r in runs], ["Film A", ""])

    def test_tab_is_read_as_text(self) -> None:
        run = OxmlElement("w:r")
        run.append(OxmlElement("w:tab"))
        self._paragraph(run)
        self.assertEqual(self._read().paragraphs[0].runs[0].text, "\t")

    def _picture_rel(self) -> str:
        """Add an inline picture paragraph and return its relationship id."""
        run = self.document.add_paragraph().add_run()
        run.add_picture(io.BytesIO(build_fixtures.make_tiny_png()))
        blip = next(run._r.iter(qn("a:blip")))
        return str(blip.get(qn("r:embed")))

    def test_vml_image_and_a_repeat_are_one_image(self) -> None:
        rel = self._picture_rel()
        run = OxmlElement("w:r")
        pict = OxmlElement("w:pict")
        imagedata = etree.SubElement(pict, "{urn:schemas-microsoft-com:vml}imagedata")
        imagedata.set(qn("r:id"), rel)
        run.append(pict)
        self._paragraph(run)
        info = self._read()
        self.assertEqual([p.runs[0].images for p in info.paragraphs], [1, 1])
        self.assertEqual(len(info.images), 1)
        sha = info.images[0].sha256_12
        self.assertEqual(
            [p.runs[0].image_ids for p in info.paragraphs], [(sha,), (sha,)]
        )

    def test_linked_and_unknown_pictures_are_counted_not_collected(self) -> None:
        self._picture_rel()
        for attribute, value in (("r:link", "rId1"), ("r:embed", "rId999")):
            run = OxmlElement("w:r")
            blip = etree.SubElement(run, qn("a:blip"))
            blip.set(qn(attribute), value)
            self._paragraph(run)
        info = self._read()
        self.assertEqual([p.runs[0].images for p in info.paragraphs], [1, 1, 1])
        self.assertEqual(len(info.images), 1)
        self.assertEqual([p.runs[0].image_ids for p in info.paragraphs][1:], [(), ()])

    def test_nested_table_cells_are_read_in_place(self) -> None:
        outer = self.document.add_table(rows=1, cols=2)
        outer.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = "Film B"
        outer.cell(0, 1).text = "Film C"
        self.document.add_paragraph("Film D")
        texts = ["".join(r.text for r in p.runs) for p in self._read().paragraphs]
        # The first cell holds its own empty paragraph before the nested table.
        self.assertEqual(texts, ["", "Film B", "", "Film C", "Film D"])

    def test_paragraph_style_name(self) -> None:
        self.document.add_paragraph("Film A", style="Title")
        self.assertEqual(self._read().paragraphs[0].style, "Title")

    def test_no_default_paragraph_style_is_none(self) -> None:
        self.document.add_paragraph("Film A")
        styles = self.document.styles.element
        for style in styles.findall(qn("w:style")):
            if style.get(qn("w:default")) == "1":
                styles.remove(style)
        self.assertIsNone(self._read().paragraphs[0].style)


_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _paragraph_of(text: str) -> etree._Element:
    """A bare ``w:p`` holding one run with `text`."""
    p = OxmlElement("w:p")
    run = etree.SubElement(p, qn("w:r"))
    etree.SubElement(run, qn("w:t")).text = text
    return p


def _wrapped(tag: str, *children: etree._Element) -> etree._Element:
    """A content control (``w:sdt``) or custom-XML wrapper around `children`."""
    wrapper = OxmlElement(tag)
    if tag == "w:sdt":
        wrapper.append(OxmlElement("w:sdtPr"))
        content = etree.SubElement(wrapper, qn("w:sdtContent"))
        content.extend(children)
    else:
        wrapper.extend(children)
    return wrapper


def _texts(info: survey_docx.DocxInfo) -> list[str]:
    return ["".join(r.text for r in p.runs) for p in info.paragraphs]


class WrapperTest(unittest.TestCase):
    """Block wrappers, alternate content and stray relationships."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.document: Document = docx.Document()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _block(self, element: etree._Element) -> None:
        """Put `element` at the end of the body, before the section properties."""
        placeholder = self.document.add_paragraph()._p
        placeholder.addprevious(element)
        placeholder.getparent().remove(placeholder)

    def _read(self) -> survey_docx.DocxInfo:
        path = Path(self._tmp.name) / "wrapped.docx"
        self.document.save(str(path))
        return survey_docx.read_docx(path, max_uncompressed=10_000_000)

    def test_body_level_wrappers_are_read_in_place(self) -> None:
        self._block(_paragraph_of("Film A"))
        self._block(_wrapped("w:sdt", _paragraph_of("Film B")))
        self._block(_wrapped("w:customXml", _paragraph_of("Film C")))
        self._block(OxmlElement("w:sdt"))  # a content control with no content
        self._block(_paragraph_of("Film D"))
        self.assertEqual(_texts(self._read()), ["Film A", "Film B", "Film C", "Film D"])

    def test_cell_and_row_level_wrappers_are_read_in_place(self) -> None:
        table = self.document.add_table(rows=1, cols=1)
        cell = table.cell(0, 0)._tc
        cell.append(_wrapped("w:sdt", _paragraph_of("Film B")))
        row = table.rows[0]._tr
        extra_cell = OxmlElement("w:tc")
        extra_cell.append(_paragraph_of("Film C"))
        row.append(_wrapped("w:sdt", extra_cell))
        extra_row = OxmlElement("w:tr")
        last_cell = etree.SubElement(extra_row, qn("w:tc"))
        last_cell.append(_paragraph_of("Film D"))
        table._tbl.append(_wrapped("w:customXml", extra_row))
        self.assertEqual(_texts(self._read()), ["", "Film B", "Film C", "Film D"])

    def test_alternate_content_is_read_once(self) -> None:
        # mc:Choice and mc:Fallback hold two renderings of the same content; only
        # the preferred one (Choice) is read.
        run = OxmlElement("w:r")
        alternate = etree.SubElement(run, f"{_MC}AlternateContent")
        for branch in ("Choice", "Fallback"):
            holder = etree.SubElement(alternate, f"{_MC}{branch}")
            etree.SubElement(holder, qn("a:blip")).set(qn("r:embed"), "rId999")
            box = etree.SubElement(holder, qn("w:txbxContent"))
            box.append(_paragraph_of("Film E"))
        p = OxmlElement("w:p")
        p.append(run)
        self._block(p)
        runs = self._read().paragraphs[0].runs
        self.assertEqual([(r.text, r.images) for r in runs], [("", 1), ("Film E", 0)])

    def test_non_image_relationship_is_counted_not_collected(self) -> None:
        styles = next(
            rel_id
            for rel_id, rel in self.document.part.rels.items()
            if rel.reltype.endswith("/styles")
        )
        run = OxmlElement("w:r")
        etree.SubElement(run, qn("a:blip")).set(qn("r:embed"), styles)
        p = OxmlElement("w:p")
        p.append(run)
        self._block(p)
        info = self._read()
        self.assertEqual(info.paragraphs[0].runs[0].images, 1)
        self.assertEqual((info.images, info.blobs), ((), ()))
        self.assertEqual(info.paragraphs[0].runs[0].image_ids, ())


if __name__ == "__main__":
    unittest.main()
