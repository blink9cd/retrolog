"""Read a Word .docx list: the formatting of every run, its symbol characters and images.

Old lists often mark a rating with formatting rather than words: a colored title, a
highlighted or shaded run, a thumb drawn from a symbol font, or a small picture. This
module reports all of that per run so a later step can see which conventions a file uses.

It reads the raw WordprocessingML rather than python-docx's ``Run.font`` API for two
reasons. A symbol-font thumb in a file converted from an old Word document is usually a
``w:sym`` element (a font name plus a private-use code such as ``F043``), which the ``Run``
API does not expose. And a run's color may come from a character style, so the style's
name is reported beside any direct color.

Paragraphs are numbered 1, 2, ... in document order, with table cells read row by row
where the table stands, so the number matches the line a person sees when reading the
document top to bottom. Content controls (``w:sdt``) and custom-XML wrappers are read
through in place. Where ``mc:AlternateContent`` offers two renderings of the same content
(a text box drawn two ways, say), only the preferred ``mc:Choice`` is read, so nothing is
counted twice.

The archive itself is unpacked by ``survey_unpack`` under the size limit, and any
failure to read is raised as ``BadDocx`` (defined there and re-exported here), whose
message is the underlying exception's class name or a fixed reason: never a path,
never document text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import docx
from docx.document import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.opc.part import Part
from docx.oxml.exceptions import InvalidXmlError
from docx.oxml.ns import qn
from docx.oxml.text.paragraph import CT_P
from docx.oxml.text.run import CT_R
from docx.text.paragraph import Paragraph
from lxml import etree

# BadDocx and MAX_MEMBERS are re-exported: callers know them as survey_docx's.
from survey_unpack import MAX_MEMBERS as MAX_MEMBERS
from survey_unpack import BadDocx as BadDocx
from survey_unpack import unpack

# How many leading hex digits of an image's SHA-256 identify it in a report.
_HASH_DIGITS = 12
# The w:color / w:shd value meaning "no explicit color".
_AUTO = "auto"
# Elements that place a picture inside a run: DrawingML (modern) and VML (legacy).
_BLIP = qn("a:blip")
_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
# The relationship-id attributes those two elements use to name their image part.
_BLIP_REL = qn("r:embed")
_IMAGEDATA_REL = qn("r:id")
# Wrappers that hold block content (paragraphs, tables, rows or cells) in place.
_SDT = qn("w:sdt")
_SDT_CONTENT = qn("w:sdtContent")
_CUSTOM_XML = qn("w:customXml")
_P = qn("w:p")
_TBL = qn("w:tbl")
_RUN = qn("w:r")
# The second, fallback rendering inside mc:AlternateContent; mc:Choice is the one read.
_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_PICTURES = frozenset({_BLIP, _IMAGEDATA})
_RUNS = frozenset({_RUN})
# What python-docx can raise parsing the repacked package: a missing part (KeyError,
# PackageNotFoundError), a part of the wrong content type (ValueError) or malformed XML
# (XMLSyntaxError).
_PACKAGE_ERRORS = (
    PackageNotFoundError,
    KeyError,
    ValueError,
    etree.XMLSyntaxError,
)

# What resolving one paragraph's style name can raise on a damaged document: a styles
# relationship pointing at a part python-docx did not load as a styles part, so it has
# no `.styles` (AttributeError); a w:pStyle or w:name missing its required w:val
# (InvalidXmlError); a style whose w:type is not a known style type (ValueError); and a
# lookup in a damaged style table (KeyError).
_STYLE_ERRORS = (AttributeError, InvalidXmlError, ValueError, KeyError)


@dataclass(frozen=True)
class RunInfo:
    """One ``w:r`` run: its text and every formatting signal a rating might use."""

    text: str
    font: str | None  # w:rFonts @w:ascii, else @w:hAnsi, else @w:cs
    style: str | None  # w:rStyle @w:val (color may come from a character style)
    color: str | None  # "RRGGBB" from w:color @w:val, "theme:<name>" from @w:themeColor
    highlight: str | None  # w:highlight @w:val, e.g. "yellow"
    shading: str | None  # w:shd @w:fill when not "auto"
    symbol: str | None  # "<font>/<hex>" from w:sym, e.g. "Wingdings/F043"
    images: int  # a:blip and v:imagedata elements inside the run
    # The sha256_12 of each embedded image those elements show, in order, repeats kept.
    # A linked picture, or one naming a part that is missing or not an image, is
    # counted in `images` but has no id here.
    image_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParagraphInfo:
    """One paragraph, numbered in document order with table cells included."""

    index: int  # 1-based, document order, table cells included: the source line
    style: str | None  # paragraph style name
    runs: tuple[RunInfo, ...]


@dataclass(frozen=True)
class ImageInfo:
    """One embedded image part, identified without its bytes."""

    content_type: str
    byte_size: int
    sha256_12: str  # first 12 hex digits


@dataclass(frozen=True)
class DocxInfo:
    """Everything read from one .docx file."""

    paragraphs: tuple[ParagraphInfo, ...]
    images: tuple[ImageInfo, ...]
    blobs: tuple[bytes, ...]  # image bytes, same order; written to --out/images/


def _attr(parent: etree._Element | None, child: str, attribute: str) -> str | None:
    """The `attribute` of `parent`'s first `child` element, or None if either is absent."""
    if parent is None:
        return None
    element = parent.find(qn(child))
    if element is None:
        return None
    return element.get(qn(attribute))


def _font(rpr: etree._Element | None) -> str | None:
    """The run's font: the ASCII slot, else the high-ANSI slot, else the complex-script."""
    for slot in ("w:ascii", "w:hAnsi", "w:cs"):
        name = _attr(rpr, "w:rFonts", slot)
        if name is not None:
            return name
    return None


def _color(rpr: etree._Element | None) -> str | None:
    """A theme color as ``theme:<name>`` (what Word renders), else a direct RRGGBB.

    ``auto`` means "the default text color", which is no color signal at all.
    """
    theme = _attr(rpr, "w:color", "w:themeColor")
    if theme is not None:
        return f"theme:{theme}"
    value = _attr(rpr, "w:color", "w:val")
    return None if value == _AUTO else value


def _shading(rpr: etree._Element | None) -> str | None:
    """The run's background fill, unless it is the automatic (none) fill."""
    fill = _attr(rpr, "w:shd", "w:fill")
    return None if fill == _AUTO else fill


def _symbol(run: CT_R) -> str | None:
    """The run's first ``w:sym`` as ``<font>/<code>``, or None if it has none."""
    sym = run.find(qn("w:sym"))
    if sym is None:
        return None
    return f"{sym.get(qn('w:font'), '')}/{sym.get(qn('w:char'), '')}"


def _walk(
    element: etree._Element, wanted: frozenset[str], stop: frozenset[str]
) -> Iterator[etree._Element]:
    """Descendants whose tag is in `wanted`, in document order, never entering `stop`.

    A wanted element is searched too, so a run inside a text box inside a run is found.
    """
    for child in element.iterchildren():
        if child.tag in stop:
            continue
        if child.tag in wanted:
            yield child
        yield from _walk(child, wanted, stop)


def _pictures(run: CT_R) -> list[etree._Element]:
    """The run's own picture elements: not a nested run's, not a fallback copy's."""
    return list(_walk(run, _PICTURES, frozenset({_FALLBACK, _RUN})))


def _image_refs(run: CT_R) -> list[str]:
    """Relationship ids of every picture element in the run, in document order.

    A picture linked to an outside file carries no embed id; it is counted by the
    caller but contributes no id here.
    """
    refs: list[str] = []
    for element in _pictures(run):
        rel = element.get(_BLIP_REL if element.tag == _BLIP else _IMAGEDATA_REL)
        if rel is not None:
            refs.append(rel)
    return refs


def _read_run(run: CT_R) -> RunInfo:
    """Collect one run's text and formatting from its raw XML."""
    rpr = run.find(qn("w:rPr"))
    return RunInfo(
        text=run.text,
        font=_font(rpr),
        style=_attr(rpr, "w:rStyle", "w:val"),
        color=_color(rpr),
        highlight=_attr(rpr, "w:highlight", "w:val"),
        shading=_shading(rpr),
        symbol=_symbol(run),
        images=len(_pictures(run)),
    )


def _unwrapped(
    container: etree._Element, tags: frozenset[str]
) -> Iterator[etree._Element]:
    """Children of `container` with a tag in `tags`, reading through block wrappers.

    A content control's content and a custom-XML element's children stand where the
    wrapper stands, at any level: around paragraphs and tables, rows, or cells.
    """
    for child in container.iterchildren():
        if child.tag == _SDT:
            content = child.find(_SDT_CONTENT)
            if content is not None:
                yield from _unwrapped(content, tags)
        elif child.tag == _CUSTOM_XML:
            yield from _unwrapped(child, tags)
        elif child.tag in tags:
            yield child


def _block_paragraphs(container: etree._Element) -> Iterator[CT_P]:
    """Paragraphs under a body or table cell, tables expanded row by row in place."""
    for child in _unwrapped(container, frozenset({_P, _TBL})):
        if child.tag == _P:
            yield child
            continue
        for row in _unwrapped(child, frozenset({qn("w:tr")})):
            for cell in _unwrapped(row, frozenset({qn("w:tc")})):
                yield from _block_paragraphs(cell)


def _paragraph_style(document: Document, p: CT_P) -> str | None:
    """The paragraph style's name as python-docx resolves it, or None if it has none."""
    try:
        style = Paragraph(p, document._body).style
        return None if style is None else style.name
    except _STYLE_ERRORS:
        # The paragraph's style cannot be resolved, but its text and runs can still be
        # read, so the paragraph is reported with no style rather than the file failed.
        return None


class _ImageCollector:
    """Gathers each distinct embedded image part once, in first-reference order."""

    def __init__(self, parts: dict[str, Part]) -> None:
        self._parts = parts
        self._seen: dict[str, str] = {}  # part name -> sha256_12
        self.images: list[ImageInfo] = []
        self.blobs: list[bytes] = []

    def add(self, rel: str) -> str | None:
        """Record the part `rel` names, once; return its sha256_12, or None if no image."""
        part = self._parts.get(rel)
        if part is None:
            return None
        name = str(part.partname)
        if name in self._seen:
            return self._seen[name]
        if not part.content_type.startswith("image/"):
            # A picture element pointing at some other part (styles, say) is damage,
            # not an image; it is still counted in its run.
            return None
        blob = part.blob
        digest = hashlib.sha256(blob).hexdigest()[:_HASH_DIGITS]
        self._seen[name] = digest
        self.images.append(ImageInfo(part.content_type, len(blob), digest))
        self.blobs.append(blob)
        return digest

    def ids_of(self, run: CT_R) -> tuple[str, ...]:
        """The sha256_12 of every embedded image the run shows, recording each."""
        found = (self.add(rel) for rel in _image_refs(run))
        return tuple(digest for digest in found if digest is not None)


def _open(path: Path, max_uncompressed: int) -> tuple[Document, etree._Element | None]:
    """Unpack the archive under the limit and parse it, any failure becoming BadDocx.

    Returns the document and its ``w:body``, which is None for a document with no
    body at all (nothing to read, but not damage).
    """
    try:
        document = docx.Document(unpack(path, max_uncompressed))
    except _PACKAGE_ERRORS as error:
        raise BadDocx(type(error).__name__) from None
    root = document.element
    if root.tag != qn("w:document"):
        # python-docx accepts any well-formed main part; one whose root is some other
        # element is not a Word document body.
        raise BadDocx("unexpected root element")
    return document, root.find(qn("w:body"))


# fuzz-target
def read_docx(path: Path, *, max_uncompressed: int) -> DocxInfo:
    """Read every paragraph's runs and every embedded image of the .docx at `path`.

    Raises BadDocx when the archive's members decompress to more than
    `max_uncompressed` bytes in total, or when it cannot be read as a Word document.
    """
    try:
        return _read(path, max_uncompressed)
    except BadDocx:
        raise
    except Exception as error:
        # The one broad catch, at this one public boundary. python-docx was written for
        # documents Word produced, and on damaged input the set of exceptions it can
        # raise is open-ended: a missing attribute surfaces as AttributeError, TypeError
        # or its own InvalidXmlError, from the package loader or from any later lookup.
        # A caller surveying many files needs one contract, "readable or BadDocx", so
        # anything else becomes BadDocx naming the class only: never the message, which
        # may quote a path or document text. The cost: a defect in this module's own
        # helpers is reported the same way, so the same class name on every .docx
        # points here rather than at the documents.
        raise BadDocx(type(error).__name__) from None


def _read(path: Path, max_uncompressed: int) -> DocxInfo:
    """`read_docx`'s work, which may raise whatever python-docx raises."""
    document, body = _open(path, max_uncompressed)
    collector = _ImageCollector(dict(document.part.related_parts))
    paragraphs: list[ParagraphInfo] = []
    blocks = () if body is None else _block_paragraphs(body)
    for index, p in enumerate(blocks, start=1):
        runs = tuple(
            replace(_read_run(run), image_ids=collector.ids_of(run))
            for run in _walk(p, _RUNS, frozenset({_FALLBACK}))
        )
        paragraphs.append(ParagraphInfo(index, _paragraph_style(document, p), runs))
    return DocxInfo(tuple(paragraphs), tuple(collector.images), tuple(collector.blobs))
