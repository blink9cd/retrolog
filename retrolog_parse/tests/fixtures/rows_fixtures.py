"""Source lines built by hand for the parse_rows suites, and a review-kind reader.

Shared by ``test_rows.py`` and ``test_rows_review.py``, so the two suites build their
lines the same way the readers do. Every path and title is invented.
"""

from __future__ import annotations

from collections.abc import Sequence

from parse_types import Mark, ReviewItem, Segment, SourceLine


def text_lines(path: str, texts: Sequence[str]) -> tuple[SourceLine, ...]:
    """A .txt file's lines, numbered from 1, as the text reader builds them."""
    return tuple(
        SourceLine(path, number, text, (Segment((), text, ()),))
        for number, text in enumerate(texts, start=1)
    )


def marked_line(
    path: str, pairs: Sequence[tuple[str, str | None, str]], number: int = 1
) -> SourceLine:
    """A Word line of segments, each opened by one (glyph, color) mark and its text."""
    segments = tuple(
        Segment((Mark(glyph, color),), text, ()) for glyph, color, text in pairs
    )
    return SourceLine(path, number, "".join(text for _, _, text in pairs), segments)


def legend_line(colors: Sequence[str], path: str = "34/seen.docx") -> SourceLine:
    """One Word line of untitled marks, one per color, in order."""
    return marked_line(path, [("Wingdings/F041", color, "-") for color in colors])


def kinds(review: Sequence[ReviewItem]) -> list[str]:
    """The kinds of ``review``, in order."""
    return [item.kind for item in review]
