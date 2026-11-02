"""The review list's vocabulary: every item kind, which are errors, and what each means.

A review item names one source line (or a file, at line 0) and says what about it needs
the operator's eye. Every kind has a path-free explanation here; an item's text is the
source line's own text, never a path. The constructors below are the only way the row
stage and the rule checks build items, so an item's error flag always follows its kind.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from parse_types import ReviewItem, SourceLine

REVIEW_KINDS = (
    "read-error",
    "no-medium",
    "rule-file-missing",
    "override-mismatch",
    "term-mismatch",  # the errors
    "unparsed",
    "unrated",
    "unplaced",
    "color-outlier",
    "shape-conflict",
    "unknown-code",
    "stray-mark",
    "no-color",
    "heading-with-rating",
    "legend",
    "legend-order",
    "override-applied",
    "progress-skipped",
    "progress-unmatched",
    "skipped",
)
ERROR_KINDS = frozenset(
    {
        "read-error",
        "no-medium",
        "rule-file-missing",
        "override-mismatch",
        "term-mismatch",
    }
)

# Each kind's path-free explanation. Indexed, never defaulted: a note parse_entries adds
# later without a kind here fails loudly instead of reaching the list unexplained.
DETAILS: Mapping[str, str] = MappingProxyType(
    {
        "read-error": "the file could not be read",
        "no-medium": "the file name matches no [media] glob, so no line was read",
        "rule-file-missing": "a rule names a file the run did not find",
        "override-mismatch": "an override does not fit this line, so it was not applied",
        "term-mismatch": "a term block's first line differs from its expect text",
        "unparsed": "no entry, heading or section could be read from this line",
        "unrated": "this entry has no rating",
        "unplaced": "no term block, folder or file name dates this entry",
        "color-outlier": "the mark's color is near no rules color, or equally near two",
        "shape-conflict": "the mark's glyph may not carry the rating its color gives",
        "unknown-code": "the letter code after the dash is not one the rules define",
        "stray-mark": "an untitled mark stands beside this entry",
        "no-color": "the entry's mark has no color to rate it by",
        "heading-with-rating": "a heading or section line that carries a mark or a "
        "rating code; no row was written for it",
        "legend": "a legend of marks",
        "legend-order": "a legend whose ratings differ from the rules' legend order",
        "override-applied": "an override changed this line",
        "progress-skipped": "a progress note finished by a later rated entry",
        "progress-unmatched": "a progress note no later rated entry finishes",
        "skipped": "an override skips this line",
    }
)


def review_item(
    kind: str, rel_path: str, line: int, text: str, detail: str | None = None
) -> ReviewItem:
    """A review item of ``kind``; an error exactly when the kind is one."""
    return ReviewItem(
        kind, rel_path, line, text, detail or DETAILS[kind], kind in ERROR_KINDS
    )


def line_item(kind: str, line: SourceLine, detail: str | None = None) -> ReviewItem:
    """A review item about one source line, carrying its text."""
    return review_item(kind, line.rel_path, line.number, line.text, detail)
