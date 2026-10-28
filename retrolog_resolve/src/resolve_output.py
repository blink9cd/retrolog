"""Render resolved rows as CSV files and the review as JSON and Markdown text.

Pure: every function returns text, and the caller writes it. Each CSV is one of
parse's, written back under its own name with parse's ``COLUMNS`` first, unchanged,
and ``RESOLVE_COLUMNS`` after them; rows keep their input order.

The review quotes the operator's titles, which are untrusted text: the Markdown is
escaped by parse's own table-cell rule, so both tools' review pages read alike and a
title cannot start markup or end a cell.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import asdict

# parse's cell escaping is imported rather than restated: one rule for every review
# page the tools write, so a fix to it reaches both.
from parse_output import COLUMNS, escape_cell
from resolve_columns import RESOLVE_COLUMNS
from resolve_rows import ERROR_KINDS, REVIEW_KINDS, OutputRow
from resolve_types import Candidate, RunMedium, ReviewEntry

HEADER = COLUMNS + RESOLVE_COLUMNS
# The review.json layout version; a later incompatible layout bumps it.
SCHEMA_VERSION = 1
_TITLE = "# retrolog_resolve review"
_TABLE_HEAD = (
    "| Medium | Title | Rows | Detail | Candidates |",
    "| --- | --- | --- | --- | --- |",
)
# What a single-medium run's review.md says of the other medium's CSV files: they may
# still be in --out from an earlier run, and this review does not cover them.
_OTHER_MEDIUM = {"book": "film", "film": "book"}
_PART_JOIN = ", "
_LIST_JOIN = "; "


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _csv_text(rows: Sequence[OutputRow]) -> str:
    """A header of ``HEADER``, then one record per row, in the order given."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows([row.cells[column] for column in HEADER] for row in rows)
    return buffer.getvalue()


def csv_files(rows: Sequence[OutputRow]) -> dict[str, str]:
    """Every CSV the rows fill: its parse name -> CSV text, names sorted."""
    grouped: dict[str, list[OutputRow]] = {}
    for row in rows:
        grouped.setdefault(row.source_csv, []).append(row)
    return {name: _csv_text(grouped[name]) for name in sorted(grouped)}


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def review_counts(entries: Sequence[ReviewEntry]) -> dict[str, int]:
    """How many entries of each kind, every kind present, in ``REVIEW_KINDS`` order."""
    counts = dict.fromkeys(REVIEW_KINDS, 0)
    for entry in entries:
        counts[entry.kind] += 1
    return counts


def _entry_key(entry: ReviewEntry) -> tuple[str, str, str, tuple[str, ...]]:
    """Medium, then title, then kind; rows settle a tie, so the order is total."""
    return (entry.medium, entry.title, entry.kind, entry.rows)


def review_json(entries: Sequence[ReviewEntry], medium: RunMedium = "all") -> str:
    """``{"schema_version", "medium", "counts", "entries"}``, keys sorted.

    ``medium`` is the media the run covered (``--medium``); entries go by title.
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "medium": medium,
        "counts": review_counts(entries),
        "entries": [asdict(entry) for entry in sorted(entries, key=_entry_key)],
    }
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _vote_count(votes: int) -> str:
    """``votes`` as "<n> votes", singular for exactly one."""
    return "1 vote" if votes == 1 else f"{votes} votes"


def _describe(candidate: Candidate) -> str:
    """One candidate as "title (original title), year, creator, id, ISBN, <n> votes".

    Gaps are left out; a film's votes are shown whenever known, 0 included, since the
    most-votes rule compared them.
    """
    title = candidate.title
    if candidate.original_title and candidate.original_title != title:
        title = f"{title} ({candidate.original_title})"
    year = "" if candidate.year is None else str(candidate.year)
    votes = "" if candidate.votes is None else _vote_count(candidate.votes)
    parts = (title, year, candidate.creator, candidate.id, candidate.isbn13, votes)
    return _PART_JOIN.join(part for part in parts if part)


def _table_row(entry: ReviewEntry) -> str:
    """One entry as a table row: medium, title, rows, detail and candidates."""
    candidates = _LIST_JOIN.join(_describe(c) for c in entry.candidates)
    cells = (entry.medium, entry.title, ", ".join(entry.rows), entry.detail, candidates)
    # An empty cell is one space between its pipes, the compact style's spelling.
    padded = (f" {text} " if text else " " for text in map(escape_cell, cells))
    return "|" + "|".join(padded) + "|"


def _kind_section(kind: str, entries: Sequence[ReviewEntry]) -> list[str]:
    """One kind's heading and table, its entries by medium and title."""
    label = f"{len(entries)}, error" if kind in ERROR_KINDS else str(len(entries))
    rows = [_table_row(entry) for entry in sorted(entries, key=_entry_key)]
    return ["", f"## {kind} ({label})", "", *_TABLE_HEAD, *rows]


def _scope_lines(medium: RunMedium) -> list[str]:
    """For a single-medium run, the sentence saying what the review leaves out."""
    if medium == "all":
        return []
    return [
        "",
        f"This run resolved {medium} rows only; any {_OTHER_MEDIUM[medium]} CSV files "
        "in this folder are from an earlier run, and this review does not cover them.",
    ]


def review_markdown(entries: Sequence[ReviewEntry], medium: RunMedium = "all") -> str:
    """A count line, the scope line of a single-medium run, then one table per kind.

    The tables go in ``REVIEW_KINDS`` order, which lists the error kinds first, so
    errors lead the page.
    """
    counts = review_counts(entries)
    errors = sum(counts[kind] for kind in ERROR_KINDS)
    lines = [_TITLE, "", f"{len(entries)} review items, {errors} of them errors."]
    lines += _scope_lines(medium)
    for kind in REVIEW_KINDS:
        found = [entry for entry in entries if entry.kind == kind]
        if found:
            lines.extend(_kind_section(kind, found))
    return "\n".join(lines) + "\n"
