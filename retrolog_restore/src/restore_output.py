"""Render the restore import file, ``restore.md``, and the printed summary.

Pure: every function returns text, and the caller writes it. The import file is
written by export's own CSV writer (minimal quoting, Unix line ends), so a cell with
a comma, a double quote, or a line break is quoted as Letterboxd's importer reads it.
``restore.md`` quotes the operator's titles, which are untrusted text, so each cell is
escaped by the table-cell rule every retrolog review list uses. The summary gives
counts only, never a title, and counts the entries whose name, tags, or review starts
with a character a spreadsheet reads as a formula (export's ``FORMULA_STARTS``). Such
a cell is written as it is, since Letterboxd's importer reads every cell as text.
"""

from __future__ import annotations

from collections.abc import Sequence

from export_formula import FORMULA_STARTS
from export_pack import csv_text
from parse_output import escape_cell
from restore_build import Restored

CSV_NAME, MARKDOWN_NAME = "letterboxd-restore.csv", "restore.md"
# The columns Letterboxd's importer reads, in the order written.
HEADER = ("Title", "Year", "WatchedDate", "Rating", "Rewatch", "Tags", "Review")
_TITLE = "# retrolog_restore"
_TABLE_HEAD = (
    "| Title | Year | Watched date | Like again |",
    "| --- | --- | --- | --- |",
)
_LIKED = "yes"


def _cells(item: Restored) -> tuple[str, ...]:
    """One restored entry's import row, one cell per ``HEADER`` column."""
    entry = item.entry
    return (
        entry.name,
        entry.year,
        entry.watched_date,
        entry.rating,
        item.rewatch,
        entry.tags,
        item.review,
    )


def restore_csv(restored: Sequence[Restored]) -> str:
    """The import file: ``HEADER``, then one row per restored entry, in order."""
    return csv_text(HEADER, [_cells(item) for item in restored])


def _entries(count: int) -> str:
    """``count`` entries, in the singular for exactly one."""
    return "1 entry" if count == 1 else f"{count} entries"


def _liked_count(restored: Sequence[Restored]) -> int:
    """How many restored entries are of a film to like again."""
    return sum(1 for item in restored if item.liked)


def _intro(restored: Sequence[Restored]) -> list[str]:
    """The count line and what to do with the file and the likes."""
    liked = _liked_count(restored)
    of_them = (
        f"{liked} of them to like again" if liked else "none of them to like again"
    )
    return [
        f"{_entries(len(restored))} to restore in `{CSV_NAME}`, {of_them}.",
        "",
        f"Import `{CSV_NAME}` last, with all four switches on (diary entries,",
        "reviews, ratings, and tags), and read its preview: the file has no tmdbID,",
        "so Letterboxd matches each row by title and year.",
        "",
        "An import cannot restore a like. Deleting a diary entry leaves its film's",
        "heart, and clicking a filled heart removes it, so a film still liked is not",
        "marked: only one liked in the backup and not liked now is. Once the import",
        "is done, open the page of each film marked below and click the heart.",
    ]


def _table_row(item: Restored) -> str:
    """One restored entry as a table row: title, year, watched date, like again."""
    entry = item.entry
    cells = (entry.name, entry.year, entry.watched_date, _LIKED if item.liked else "")
    # An empty cell is one space between its pipes, the compact style's spelling.
    padded = (f" {text} " if text else " " for text in map(escape_cell, cells))
    return "|" + "|".join(padded) + "|"


def restore_markdown(restored: Sequence[Restored]) -> str:
    """``restore.md``: the counts, the steps left, and a table of every entry."""
    rows = [_table_row(item) for item in restored]
    lines = [_TITLE, "", *_intro(restored), "", *_TABLE_HEAD, *rows]
    return "\n".join(lines) + "\n"


def _starts_formula(item: Restored) -> bool:
    """Whether a free-text cell of ``item`` (name, tags, review) starts a formula."""
    return any(
        text.startswith(FORMULA_STARTS)
        for text in (item.entry.name, item.entry.tags, item.review)
    )


def summary_lines(restored: Sequence[Restored]) -> list[str]:
    """The lines printed after a write: counts, then where the list is.

    A formula-cell count follows, worded as export prints it, only when one occurs.
    """
    lines = [
        f"entries restored: {len(restored)}",
        f"to like again: {_liked_count(restored)}",
        f"see {MARKDOWN_NAME} for every restored entry and the films to like again",
    ]
    if flagged := sum(1 for item in restored if _starts_formula(item)):
        lines.append(f"review formula-cell: {flagged}")
    return lines
