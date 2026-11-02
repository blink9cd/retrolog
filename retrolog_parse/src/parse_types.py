"""The shapes every retrolog_parse module passes between them.

Each is a frozen dataclass, so a value read from a source file cannot change on its way
from the reader through the entry parser and the dating rules to the output rows.

``RatingKey`` is opaque here: parse only carries the key the rules file assigns to a
letter, a color or a split of two, and never interprets it. Mapping a key to a rating on
some scale is the export step's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The kinds of list an entry can come from, chosen by the source file's name.
Medium = Literal["book", "film"]
# A key the rules define, such as "3", or "<key>/<key>" for a split code.
RatingKey = str


@dataclass(frozen=True)
class Mark:
    """One symbol glyph in a Word paragraph, with the color it was drawn in."""

    glyph: str  # "<Font>/<HEX4>", e.g. "Wingdings/F043", "Webdings/0061"
    color: str | None  # "RRGGBB" upper-case; None for auto, theme or no color


@dataclass(frozen=True)
class Segment:
    """A stretch of one line: the glyph that opens it and the text that follows."""

    marks: tuple[Mark, ...]  # the one glyph that opens this segment; () for plain text
    text: str  # the segment's non-symbol text, unstripped
    text_colors: tuple[str, ...]  # "RRGGBB" of the visible non-symbol runs, in order


@dataclass(frozen=True)
class SourceLine:
    """One line of a source file, as every later stage sees it."""

    rel_path: str  # POSIX, NFC, relative to --path
    number: int  # 1-based: file line (.txt) or paragraph index (Word)
    text: str  # the whole line's text, symbol glyphs removed
    # .txt: one Segment(marks=(), text=line, text_colors=())
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class ReviewItem:
    """Something a person should look at: a doubtful line, or a whole file."""

    kind: str  # see parse_review.REVIEW_KINDS
    rel_path: str
    line: int  # 0 when the item concerns a whole file
    text: str  # the source line's text ("" for a whole file)
    detail: str  # path-free explanation
    error: bool  # True makes the run exit 1
