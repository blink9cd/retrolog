"""Describe and count what a line or a Word paragraph carries.

`describe_paragraph` writes a Word paragraph as its text with each formatted run's
annotations in brackets, which is how a report shows a sample from a document.
`view_of` reduces a paragraph to the `LineView` the classifier reads, and
`text_counts` and `docx_counts` tally the emoji, lone letters, non-ASCII characters
and run formatting a report sums per group. No function here does any I/O.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable

from survey_docx import DocxInfo, ParagraphInfo, RunInfo
from survey_lines import (
    SYMBOL_FONTS,
    LineKind,
    LineView,
    classify_line,
    find_emoji,
    letter_tokens,
)

# Count tables taken from Word runs, and from any line's text, in report order.
RUN_TABLES = ("fonts", "symbols", "colors", "highlights", "shadings", "styles")
TEXT_TABLES = ("emoji", "letter_tokens", "non_ascii")
_ASCII_LIMIT = 0x80


def _is_symbol_font(run: RunInfo) -> bool:
    """Whether the run is set in a font whose glyphs are pictures."""
    return run.font is not None and run.font.casefold() in SYMBOL_FONTS


def _shows_something(run: RunInfo) -> bool:
    """Whether the run puts anything visible on the page."""
    return bool(run.text.strip() or run.symbol or run.images)


def _is_symbol_text(run: RunInfo) -> bool:
    """Whether the run draws symbol-font glyphs: set in such a font, showing something.

    A run of spaces in Wingdings draws nothing, so it marks nothing; the same visible
    test decides whether a run's color counts.
    """
    return _is_symbol_font(run) and _shows_something(run)


def view_of(p: ParagraphInfo) -> LineView:
    """The classifier's view of a Word paragraph."""
    return LineView(
        text="".join(run.text for run in p.runs),
        has_symbol_run=any(run.symbol or _is_symbol_text(run) for run in p.runs),
        has_image=any(run.images for run in p.runs),
        has_color=any(
            (run.color or run.highlight or run.shading) and _shows_something(run)
            for run in p.runs
        ),
    )


def classify_paragraph(p: ParagraphInfo) -> LineKind:
    """The line kind of a Word paragraph."""
    return classify_line(view_of(p))


def _codes(text: str) -> str:
    """``U+0043 U+0044`` for ``CD``."""
    return " ".join(f"U+{ord(char):04X}" for char in text)


def _run_notes(run: RunInfo) -> list[str]:
    """The bracketed annotations for one run, in a fixed order."""
    notes: list[str] = []
    if run.symbol:
        notes.append(run.symbol)
    if _is_symbol_text(run) and run.text.strip():
        # The letter stands for a glyph, so show the font, the letter and its code.
        notes.append(f"{run.font} '{run.text}' {_codes(run.text)}")
    if run.color:
        notes.append(run.color if run.color.startswith("theme:") else f"#{run.color}")
    if run.highlight:
        notes.append(f"hl:{run.highlight}")
    if run.shading:
        notes.append(f"shd:{run.shading}")
    if run.style:
        notes.append(f"style:{run.style}")
    notes.extend(f"img:{image_id}" for image_id in run.image_ids)
    notes.extend("img" for _ in range(run.images - len(run.image_ids)))
    return notes


def describe_paragraph(p: ParagraphInfo) -> str:
    """The paragraph's text, each formatted run followed by ``[...]`` annotations."""
    described = ""
    for run in p.runs:
        described += run.text
        notes = _run_notes(run)
        if notes:
            gap = "" if not described or described[-1].isspace() else " "
            described += f"{gap}[{' '.join(notes)}]"
    return described


def _non_ascii_key(char: str) -> str:
    """``U+2019 RIGHT SINGLE QUOTATION MARK``, or just the code for an unnamed one."""
    name = unicodedata.name(char, "")
    return f"U+{ord(char):04X} {name}".rstrip()


def text_counts(texts: Iterable[str]) -> dict[str, Counter[str]]:
    """Emoji, lone-letter and non-ASCII character counts over plain line texts."""
    counts: dict[str, Counter[str]] = {
        "emoji": Counter(),
        "letter_tokens": Counter(),
        "non_ascii": Counter(),
    }
    for text in texts:
        counts["emoji"].update(find_emoji(text))
        counts["letter_tokens"].update(letter_tokens(text))
        counts["non_ascii"].update(
            _non_ascii_key(char) for char in text if ord(char) >= _ASCII_LIMIT
        )
    return counts


def _run_signals(run: RunInfo) -> Iterable[tuple[str, str]]:
    """``(table, key)`` for each formatting signal one run carries."""
    for table, value in (
        ("fonts", run.font),
        ("symbols", run.symbol),
        ("colors", run.color),
        ("highlights", run.highlight),
        ("shadings", run.shading),
    ):
        if value is not None:
            yield table, value
    if run.style is not None:
        yield "styles", f"run:{run.style}"
    if _is_symbol_text(run):
        # Whitespace inside the run is spacing, not a glyph.
        glyphs = (c for c in run.text if not c.isspace())
        yield from (("symbols", f"{run.font}/{ord(c):04X}") for c in glyphs)


def docx_counts(info: DocxInfo) -> dict[str, Counter[str]]:
    """Every count table for a Word document: its runs' formatting and its text."""
    counts = text_counts(view_of(p).text for p in info.paragraphs)
    for table in RUN_TABLES:
        counts[table] = Counter()
    for paragraph in info.paragraphs:
        if paragraph.style is not None:
            counts["styles"][f"paragraph:{paragraph.style}"] += 1
        for run in paragraph.runs:
            for table, key in _run_signals(run):
                counts[table][key] += 1
    return counts
