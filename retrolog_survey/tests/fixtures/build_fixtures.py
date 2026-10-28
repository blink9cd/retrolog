"""Build survey test inputs at test time, so no source file is ever committed.

Each section writes one kind of input. Every text it writes is invented by the calling
test or by this module; nothing here holds real list content.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import docx
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.run import Run

# ---------------------------------------------------------------------------
# Text files
# ---------------------------------------------------------------------------


def write_text_fixture(path: Path, text: str, *, encoding: str, newline: str) -> None:
    """Write `text` to `path` in `encoding`, with each line feed replaced by `newline`.

    Bytes are written directly so no platform newline translation interferes: the file
    holds exactly the line endings the test asked for.
    """
    path.write_bytes(text.replace("\n", newline).encode(encoding))


# ---------------------------------------------------------------------------
# Word files
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# IHDR for a 1x1 image: width, height, bit depth 8, color type 2 (truecolor), then
# compression, filter and interlace methods, all 0.
_PNG_IHDR_FORMAT = ">IIBBBBB"
_PNG_TRUECOLOR = 2
_PNG_BIT_DEPTH = 8
# One scanline: filter byte 0 (none), then one red pixel.
_PNG_SCANLINE = b"\x00\xff\x00\x00"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    """One PNG chunk: length, type, data, and the CRC-32 of type plus data."""
    crc = zlib.crc32(kind + data)
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def make_tiny_png() -> bytes:
    """Return a valid 1x1 red PNG, assembled by hand so no imaging library is needed."""
    ihdr = struct.pack(_PNG_IHDR_FORMAT, 1, 1, _PNG_BIT_DEPTH, _PNG_TRUECOLOR, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(_PNG_SCANLINE))
        + _png_chunk(b"IEND", b"")
    )


def _add_sym(run: Run, font: str, char: str) -> None:
    """Append a ``w:sym`` symbol character to `run`; python-docx has no API for it."""
    sym = OxmlElement("w:sym")
    sym.set(qn("w:font"), font)
    sym.set(qn("w:char"), char)
    run._r.append(sym)


def _add_shading(run: Run, fill: str) -> None:
    """Give `run` a solid background fill; python-docx has no API for run shading."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    run._r.get_or_add_rPr().append(shd)


def make_rated_docx(path: Path) -> None:
    """Write a .docx holding each way an old list marks a rating, one per paragraph.

    Paragraphs, in order: a ``w:sym`` Wingdings thumb colored blue; a green run; a
    highlighted run; a shaded run; an emoji; an inline picture; a one-cell table; and a
    run whose font is Wingdings (the other way a thumb is stored).
    """
    document = docx.Document()
    first = document.add_paragraph("Film A ")
    thumb = first.add_run()
    thumb.font.color.rgb = RGBColor.from_string("0070C0")
    _add_sym(thumb, "Wingdings", "F043")
    document.add_paragraph().add_run("Film B").font.color.rgb = RGBColor.from_string(
        "00B050"
    )
    highlighted = document.add_paragraph().add_run("Film C")
    highlighted.font.highlight_color = WD_COLOR_INDEX.YELLOW
    _add_shading(document.add_paragraph().add_run("Film D"), "FF0000")
    document.add_paragraph().add_run("Film E \N{THUMBS UP SIGN}")
    document.add_paragraph().add_run().add_picture(io.BytesIO(make_tiny_png()))
    document.add_table(rows=1, cols=1).cell(0, 0).text = "g Film F"
    document.add_paragraph().add_run("C").font.name = "Wingdings"
    document.save(str(path))
