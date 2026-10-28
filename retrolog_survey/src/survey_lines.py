"""Sort the lines of an old media list into kinds, so a survey can count them.

A hand-kept list marks its entries in many ways over the years: a glyph from a symbol
font, a small picture, a colored run, an emoji, or a single letter written before or
after a title. This module decides which of those marks a line carries. It does no
I/O: a caller describes each line as a `LineView` -- built from a plain-text line or
from a word-processor paragraph -- and `classify_line` names its kind.

The kinds are tried in a fixed order and the first match wins, so a line carrying
several marks is counted once, under the earliest:

``blank`` -> ``symbol`` -> ``image`` -> ``colored`` -> ``emoji`` -> ``letter_code``
-> ``unclassified``.

A letter code is deliberately generous: a title that begins with the word "A" counts as
one. The survey only counts what it sees; telling a code from an article is left to a
later step that has more evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Fonts whose glyphs are pictures rather than letters, casefolded for comparison.
SYMBOL_FONTS: frozenset[str] = frozenset(
    {
        "wingdings",
        "wingdings 2",
        "wingdings 3",
        "webdings",
        "symbol",
        "zapf dingbats",
    }
)

LineKind = Literal[
    "blank", "symbol", "image", "colored", "emoji", "letter_code", "unclassified"
]

# Thumbs up and thumbs down are named on their own, even though they also fall inside
# the pictograph range below, because they are the marks a list is likeliest to use.
_THUMBS: frozenset[int] = frozenset({0x1F44D, 0x1F44E})
_PICTOGRAPH_FIRST = 0x1F300
_PICTOGRAPH_LAST = 0x1FAFF

# A single ASCII letter standing alone at the start or the end of a line.
_START_LETTER = re.compile(r"^\s*([A-Za-z])(?:\W|$)")
_END_LETTER = re.compile(r"(?:^|\W)([A-Za-z])\s*$")


@dataclass(frozen=True)
class LineView:
    """What the classifier needs to know about one line.

    `has_symbol_run` is true when any run is set in one of `SYMBOL_FONTS`;
    `has_image` when the line holds an inline picture; `has_color` when any non-space
    run carries a font color, a highlight or shading. Plain text lines leave all three
    false.
    """

    text: str
    has_symbol_run: bool = False
    has_image: bool = False
    has_color: bool = False


def _is_emoji(char: str) -> bool:
    """True when `char` is a thumbs mark or lies in the pictograph range."""
    code = ord(char)
    return code in _THUMBS or _PICTOGRAPH_FIRST <= code <= _PICTOGRAPH_LAST


def find_emoji(text: str) -> tuple[str, ...]:
    """Return the emoji in `text` as ``U+1F44D``-style codes, in order, repeats kept."""
    return tuple(f"U+{ord(char):04X}" for char in text if _is_emoji(char))


# fuzz-target
def letter_tokens(text: str) -> tuple[str, ...]:
    """Return the lone letters at the start and end of `text`, lowercased.

    At most two tokens come back: the start letter, then the end letter. A line that
    is a single letter yields it once, since both patterns found the same character.
    """
    tokens: list[str] = []
    start = _START_LETTER.match(text)
    if start:
        tokens.append(start.group(1).lower())
    end = _END_LETTER.search(text)
    if end and not (start and start.start(1) == end.start(1)):
        tokens.append(end.group(1).lower())
    return tuple(tokens)


def classify_line(view: LineView) -> LineKind:
    """Name the kind of `view` by the first-match order in the module docstring."""
    if not view.text.strip() and not view.has_image:
        return "blank"
    if view.has_symbol_run:
        return "symbol"
    if view.has_image:
        return "image"
    if view.has_color:
        return "colored"
    if find_emoji(view.text):
        return "emoji"
    if letter_tokens(view.text):
        return "letter_code"
    return "unclassified"
