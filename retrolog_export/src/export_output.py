"""Name the import files and render the review as JSON and Markdown text.

Pure: every function returns text or names, and the caller writes them. The import
files are numbered in the order they are to be uploaded. The review quotes the
operator's titles, which are untrusted text, so the Markdown is escaped by the same
table-cell rule resolve's and parse's review pages use, and a title cannot start
markup or end a cell.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from export_select import ERROR_KINDS, REVIEW_KINDS
from export_services import ServiceSpec
from export_types import ReviewEntry

# parse's cell escaping, the rule resolve's review page uses too, is imported rather
# than restated: one rule for every review page the tools write, so a fix to it
# reaches all of them.
from parse_output import escape_cell

# The review.json layout version; a later incompatible layout bumps it.
SCHEMA_VERSION = 1
# The totals counted beside the review kinds.
TOTALS = ("films-sent", "rows-sent", "rows-cut", "files")
_TITLE = "# retrolog_export review"
_TABLE_HEAD = ("| Title | Rows | Detail |", "| --- | --- | --- |")
_ROWS_JOIN = ", "


def import_files(spec: ServiceSpec, texts: Sequence[str]) -> dict[str, str]:
    """Each file's text under its upload-order name, ``<file_stem>-001.csv`` onward."""
    return {
        f"{spec.file_stem}-{number:03d}.csv": text
        for number, text in enumerate(texts, start=1)
    }


def review_counts(
    entries: Sequence[ReviewEntry],
    *,
    films_sent: int,
    rows_sent: int,
    rows_cut: int,
    files: int,
) -> dict[str, int]:
    """Every review kind's count, zero included, then the run's totals (``TOTALS``)."""
    counts = dict.fromkeys(REVIEW_KINDS, 0)
    for entry in entries:
        counts[entry.kind] += 1
    totals = (films_sent, rows_sent, rows_cut, files)
    return counts | dict(zip(TOTALS, totals, strict=True))


def _entry_key(entry: ReviewEntry) -> tuple[str, str, tuple[str, ...]]:
    """Title, then kind; rows settle a tie, so the order is total."""
    return (entry.title, entry.kind, entry.rows)


def review_json(entries: Sequence[ReviewEntry], *, counts: Mapping[str, int]) -> str:
    """``{"schema_version", "counts", "entries"}``, keys sorted, entries by title."""
    document = {
        "schema_version": SCHEMA_VERSION,
        "counts": dict(counts),
        "entries": [asdict(entry) for entry in sorted(entries, key=_entry_key)],
    }
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _table_row(entry: ReviewEntry) -> str:
    """One entry as a table row: title, rows and detail."""
    cells = (entry.title, _ROWS_JOIN.join(entry.rows), entry.detail)
    # An empty cell is one space between its pipes, the compact style's spelling.
    padded = (f" {text} " if text else " " for text in map(escape_cell, cells))
    return "|" + "|".join(padded) + "|"


def _kind_section(kind: str, entries: Sequence[ReviewEntry]) -> list[str]:
    """One kind's heading and table, its entries by title."""
    label = f"{len(entries)}, error" if kind in ERROR_KINDS else str(len(entries))
    rows = [_table_row(entry) for entry in sorted(entries, key=_entry_key)]
    return ["", f"## {kind} ({label})", "", *_TABLE_HEAD, *rows]


def _count_line(entries: Sequence[ReviewEntry], counts: Mapping[str, int]) -> str:
    """What was sent and cut, then how many review items and errors."""
    errors = sum(1 for entry in entries if entry.kind in ERROR_KINDS)
    return (
        f"{_counted(counts['rows-sent'], 'row')} of "
        f"{_counted(counts['films-sent'], 'film')} sent in "
        f"{_counted(counts['files'], 'file')}; "
        f"{_counted(counts['rows-cut'], 'row')} cut. "
        f"{_counted(len(entries), 'review item')}, "
        f"{errors} of them {'an error' if errors == 1 else 'errors'}."
    )


def _counted(count: int, noun: str) -> str:
    """``count`` and ``noun``, plural unless the count is exactly one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def review_markdown(
    entries: Sequence[ReviewEntry], *, counts: Mapping[str, int]
) -> str:
    """A count line, then one table per kind present, in ``REVIEW_KINDS`` order.

    ``REVIEW_KINDS`` lists the error kinds first, so errors lead the page.
    """
    lines = [_TITLE, "", _count_line(entries, counts)]
    for kind in REVIEW_KINDS:
        found = [entry for entry in entries if entry.kind == kind]
        if found:
            lines.extend(_kind_section(kind, found))
    return "\n".join(lines) + "\n"
