"""Turn every source line into normalized rows and a review list, by the rules alone.

``build_rows`` joins the earlier stages: each line is parsed (``parse_entries``) and
dated (``parse_dates``), the rules' term blocks and overrides are checked against the
lines they name (``parse_rule_checks``), sections are tracked within each file, and
progress notes ("halfway through") are resolved across the whole tree. Pure: it reads
nothing but its arguments.

Nothing is dropped silently. Every line ends up in exactly one place: a row, a review
item that names it, or a layout line (blank, divider, heading or section) that carries
nothing to review. Every unrated row also gets an "unrated" item. A review item's detail
never names a path; its text is the source line's own text.

A progress note finished by a later rated entry is dropped, and its other review items
(an "unplaced", say) go with it: its line is still named by its "progress-skipped" item.
"Later" means a later date when both are known, a later line of the same file, or, when
either date is unknown, a later-sorting file.

Rows keep source order; sorting them is the output stage's job.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import cast

from parse_dates import Placement, place
from parse_entries import (
    ParsedEntry,
    ParsedLine,
    clean_title,
    parse_line,
    strip_unknown_code,
)

# The review vocabulary lives in parse_review; ERROR_KINDS and REVIEW_KINDS are
# re-exported (the "as" form) because the output stage and the CLI read them here.
from parse_review import ERROR_KINDS as ERROR_KINDS
from parse_review import REVIEW_KINDS as REVIEW_KINDS
from parse_review import line_item, review_item
from parse_rule_checks import check_missing, check_overrides, check_terms
from parse_rules import Override, Rules, medium_for
from parse_types import Medium, RatingKey, ReviewItem, SourceLine

# Notes an applied override settles: it rates or renames the entry, so a code left in
# the title is no longer a question.
_SETTLED_BY_OVERRIDE = frozenset({"unknown-code"})
# Parsed-line kinds that carry nothing to review.
_LAYOUT_KINDS = frozenset({"blank", "divider", "heading", "section"})
# How a legend detail shows a mark whose color means no rating, or that has no color.
_NO_RATING = "?"
_NO_COLOR = "no color"


@dataclass(frozen=True)
class Row:
    """One normalized entry: what was logged, when, and how it was rated."""

    medium: Medium
    title: str
    creator: str  # always "" from parse
    year: int | None  # the work's year
    date: datetime.date | None
    date_is_approximate: bool
    color: str  # "RRGGBB" that decided the rating, else ""
    rating_raw: RatingKey  # "" when unrated
    comment: str
    rewatch: bool
    section: str  # the casefolded section in force, else ""
    source_file: str
    source_line: int


@dataclass(frozen=True)
class _Draft:
    """A row on its way out, with its line's text and the items it raises."""

    row: Row
    text: str  # the source line's text, for the review items
    progress: bool  # a progress note, resolved across the tree
    items: tuple[ReviewItem, ...]


# A stretch of output in source order: a row in progress, or a finished review item.
_Piece = _Draft | ReviewItem


def title_key(title: str) -> str:
    """``title`` casefolded, punctuation and symbols dropped, spaces collapsed."""
    kept = "".join(
        char for char in title.casefold() if char.isalnum() or char.isspace()
    )
    return " ".join(kept.split())


# ---------------------------------------------------------------------------
# Review items
# ---------------------------------------------------------------------------


def _draft_item(kind: str, draft: _Draft) -> ReviewItem:
    """A review item about the line a draft row came from."""
    row = draft.row
    return review_item(kind, row.source_file, row.source_line, draft.text)


def _legend_item(line: SourceLine, parsed: ParsedLine, rules: Rules) -> ReviewItem:
    """A legend line's item: "legend" in the rules' order (or none set), else "legend-order".

    The detail lists each mark as ``<color> = <rating>``, in order, so a color the
    rules lack, or one they key differently, can be read off it.
    """
    shown = ", ".join(
        f"{_NO_COLOR if color is None else color} = "
        f"{_NO_RATING if rating is None else rating}"
        for color, rating in zip(parsed.legend_colors, parsed.legend, strict=True)
    )
    if not rules.legend_order or parsed.legend == rules.legend_order:
        return line_item("legend", line, f"legend marks (color = rating): {shown}")
    expected = ", ".join(rules.legend_order)
    detail = (
        f"legend marks (color = rating) {shown} differ from the rules' order {expected}"
    )
    return line_item("legend-order", line, detail)


def _override_title(
    override: Override, entry: ParsedEntry, rules: Rules
) -> tuple[str, int | None]:
    """The title and work year an override leaves.

    "title" sets the title as given. Otherwise the unknown code is stripped and the
    rest cleaned again, so a work year the code hid ("a (1999) - q") is taken out.
    A cut that would leave no letter or digit keeps the stripped title as it is.
    """
    if override.action == "title":
        return cast(str, override.value), entry.work_year  # load_rules requires a value
    stripped = strip_unknown_code(entry.title, rules)
    title, year = clean_title(stripped)
    if not any(char.isalnum() for char in title):
        return stripped, entry.work_year
    return title, entry.work_year if year is None else year


def _apply_override(
    override: Override, entries: Sequence[ParsedEntry], rules: Rules
) -> ParsedEntry | None:
    """The one entry of a line with a rating, title or unrated override applied.

    None when the line holds no entry or more than one, so the override cannot say
    which it means. "title" replaces the title and leaves the entry unrated;
    "rating" and "unrated" take any unknown code out of the title.
    """
    if len(entries) != 1:
        return None
    entry = entries[0]
    title, year = _override_title(override, entry, rules)
    return replace(
        entry,
        title=title,
        work_year=year,
        rating=override.value if override.action == "rating" else None,
        code=None,
        color=None,
        progress=False,  # the override is the operator's word on this entry
        notes=tuple(note for note in entry.notes if note not in _SETTLED_BY_OVERRIDE),
    )


# ---------------------------------------------------------------------------
# One file, line by line
# ---------------------------------------------------------------------------


def _layout_items(
    line: SourceLine, parsed: ParsedLine, rules: Rules
) -> list[ReviewItem]:
    """The items a line raises by its kind: unparsed, a legend, or its own notes."""
    if parsed.kind == "unparsed":
        return [line_item("unparsed", line)]
    if parsed.kind == "legend":
        return [_legend_item(line, parsed, rules)]
    return [line_item(note, line) for note in parsed.notes]


def _draft(
    line: SourceLine, entry: ParsedEntry, medium: Medium, section: str, when: Placement
) -> _Draft:
    """A draft row for one entry, with an item per note and one if it is undated."""
    row = Row(
        medium=medium,
        title=entry.title,
        creator="",
        year=entry.work_year,
        date=when.date,
        date_is_approximate=when.approximate,
        color=entry.color or "",
        rating_raw=entry.rating or "",
        comment=entry.comment,
        rewatch=entry.rewatch,
        section=section,
        source_file=line.rel_path,
        source_line=line.number,
    )
    notes = [*entry.notes, *(["unplaced"] if when.date is None else [])]
    details = dict(entry.details)
    items = tuple(line_item(note, line, details.get(note)) for note in notes)
    return _Draft(row, line.text, entry.progress, items)


def _overridden(
    line: SourceLine, parsed: ParsedLine, override: Override | None, rules: Rules
) -> tuple[tuple[ParsedEntry, ...], list[ReviewItem]]:
    """The line's entries after its override, if any, and the item that records it."""
    if override is None:
        return parsed.entries, []
    applied = _apply_override(override, parsed.entries, rules)
    if applied is None:
        detail = "the override needs a line holding exactly one entry"
        return parsed.entries, [line_item("override-mismatch", line, detail)]
    detail = f"override action {override.action!r} applied"
    return (applied,), [line_item("override-applied", line, detail)]


def _pieces_for_line(
    line: SourceLine,
    parsed: ParsedLine,
    medium: Medium,
    section: str,
    override: Override | None,
    rules: Rules,
) -> list[_Piece]:
    """Everything one line contributes: override items, layout items, then its rows."""
    if override is not None and override.action == "skip":
        return [line_item("skipped", line)]
    entries, pieces = _overridden(line, parsed, override, rules)
    when = place(rules, line.rel_path, line.number)
    drafts = [_draft(line, entry, medium, section, when) for entry in entries]
    return [*pieces, *_layout_items(line, parsed, rules), *drafts]


def _rows_for_file(
    rel_path: str,
    lines: Sequence[SourceLine],
    overrides: Mapping[tuple[str, int], Override],
    rules: Rules,
) -> list[_Piece]:
    """One file's pieces in line order; a single no-medium error for an unknown file."""
    medium = medium_for(rules, PurePosixPath(rel_path).name)
    if medium is None:
        return [review_item("no-medium", rel_path, 0, "")]
    section = ""  # sections apply until the next one, within one file only
    pieces: list[_Piece] = []
    for line in lines:
        parsed = parse_line(line, rules)
        section = parsed.section if parsed.section is not None else section
        override = overrides.get((rel_path, line.number))
        pieces.extend(_pieces_for_line(line, parsed, medium, section, override, rules))
    return pieces


# ---------------------------------------------------------------------------
# Progress notes, across the whole tree
# ---------------------------------------------------------------------------


def _finishes(rated: Row, progress: Row) -> bool:
    """Whether a rated row is a later reading of the same work as a progress note."""
    if rated.medium != progress.medium or title_key(rated.title) != title_key(
        progress.title
    ):
        return False
    if rated.date is not None and progress.date is not None:
        if rated.date > progress.date:
            return True
    elif rated.source_file > progress.source_file:
        return True  # undated: a later file is the only order there is
    return (
        rated.source_file == progress.source_file
        and rated.source_line > progress.source_line
    )


def _resolved(draft: _Draft, rated: Sequence[Row]) -> _Piece:
    """A progress note dropped for a later rated row, or kept unrated and flagged."""
    if any(_finishes(row, draft.row) for row in rated):
        return _draft_item("progress-skipped", draft)
    return replace(
        draft, items=(*draft.items, _draft_item("progress-unmatched", draft))
    )


def _resolve_progress(pieces: Sequence[_Piece]) -> list[_Piece]:
    """Every progress note resolved against every rated row of the tree."""
    rated = [
        piece.row
        for piece in pieces
        if isinstance(piece, _Draft) and piece.row.rating_raw
    ]
    return [
        _resolved(piece, rated)
        if isinstance(piece, _Draft) and piece.progress
        else piece
        for piece in pieces
    ]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def _finish(pieces: Iterable[_Piece]) -> tuple[tuple[Row, ...], tuple[ReviewItem, ...]]:
    """Split pieces into rows and items, adding "unrated" for each unrated row."""
    rows: list[Row] = []
    items: list[ReviewItem] = []
    for piece in pieces:
        if isinstance(piece, ReviewItem):
            items.append(piece)
            continue
        rows.append(piece.row)
        items.extend(piece.items)
        if not piece.row.rating_raw:
            items.append(_draft_item("unrated", piece))
    return tuple(rows), tuple(items)


# fuzz-target
def build_rows(
    files: Mapping[str, tuple[SourceLine, ...]],
    rules: Rules,
    *,
    walked: frozenset[str] | None = None,
) -> tuple[tuple[Row, ...], tuple[ReviewItem, ...]]:
    """Every file's rows, in source order, and the review list that accounts for the rest.

    ``files`` maps each relative path to its lines. Term blocks and overrides are
    checked against the lines of files present here. ``walked`` is every path the
    run found, read or not (excluded, or failed to read): a term block or override
    naming a path outside it is a "rule-file-missing" error. None skips that check.
    """
    overrides, override_items = check_overrides(files, rules)
    pieces: list[_Piece] = [
        *check_missing(walked, rules),
        *check_terms(files, rules),
        *override_items,
    ]
    for rel_path, lines in files.items():
        pieces.extend(_rows_for_file(rel_path, lines, overrides, rules))
    return _finish(_resolve_progress(pieces))
