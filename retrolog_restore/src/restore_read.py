"""Read an account export's diary, reviews, and likes, as retrolog_restore needs them.

``read_export`` takes Letterboxd's "Export your data" download, its ``.zip`` or the
folder it unpacks to, through export's own bounded reader
(``export_account.read_export_texts``), and reads the files of it the caller names:
``diary.csv``, which must be there, and ``reviews.csv`` and ``likes/films.csv``, each
read as empty when absent or not named. The backup's three are read; the later
export's ``reviews.csv`` is not, since no output uses it. A file that is present must
name every column of its own needed set in its header, rows or none, so a renamed
column is refused rather than read as empty in every row. Every cell is kept as the
text Letterboxd wrote, since the restore file hands it back to Letterboxd unchanged.

Every ``RestoreError`` names the option and the file, never a path (an ``OSError``'s
own message carries the absolute one) and never a title, which is the operator's
private content.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from export_account import AccountError, csv_field_limit, read_export_texts

DIARY, REVIEWS, LIKES = "diary.csv", "reviews.csv", "likes/films.csv"
EXPORT_FILES = (DIARY, REVIEWS, LIKES)
# The files read from each export: the deleted entries' reviews are the backup's, and
# the later export's likes tell which films are still liked.
BACKUP_FILES = EXPORT_FILES
CURRENT_FILES = (DIARY, LIKES)
# Each DiaryEntry field's column, located by header name in any order.
_COLUMNS: Mapping[str, str] = {
    "name": "Name",
    "year": "Year",
    "uri": "Letterboxd URI",
    "watched_date": "Watched Date",
    "rating": "Rating",
    "rewatch": "Rewatch",
    "tags": "Tags",
    "review": "Review",
}
# The columns each file's header must name. diary.csv's entries are told apart by
# their URI, and a restored row without its watched date would be logged undated;
# reviews.csv joins a review to its entry by watched date and carries the review
# itself, so without either column every review would be silently dropped.
DIARY_NEEDED = ("Name", "Year", "Letterboxd URI", "Watched Date")
REVIEWS_NEEDED = ("Name", "Year", "Watched Date", "Review")
LIKES_NEEDED = ("Name", "Year")
_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"


class RestoreError(Exception):
    """An export is unusable; the message never names a path or a title."""


@dataclass(frozen=True)
class DiaryEntry:
    """One row of diary.csv, reviews.csv, or likes/films.csv; "" for an absent cell."""

    name: str = ""
    year: str = ""
    uri: str = ""
    watched_date: str = ""
    rating: str = ""
    rewatch: str = ""
    tags: str = ""
    review: str = ""


@dataclass(frozen=True)
class ExportFiles:
    """The three files of one export, each as its rows in file order."""

    diary: tuple[DiaryEntry, ...]
    reviews: tuple[DiaryEntry, ...]
    likes: tuple[DiaryEntry, ...]


def _lacking(missing: list[str]) -> str:
    """The refusal for ``missing`` columns: "header lacks a Year column", or the list."""
    if len(missing) == 1:
        return f"header lacks a {missing[0]} column"
    if len(missing) == 2:
        listed = " and ".join(missing)
    else:
        listed = f"{', '.join(missing[:-1])}, and {missing[-1]}"
    return f"header lacks the {listed} columns"


def _positions(header: list[str], needed: tuple[str, ...]) -> dict[str, int | None]:
    """Where ``header`` puts each field's column; None for an absent one."""
    missing = [column for column in needed if column not in header]
    if missing:
        raise RestoreError(_lacking(missing))
    return {
        field: header.index(column) if column in header else None
        for field, column in _COLUMNS.items()
    }


def _entries(
    reader: Iterator[list[str]], at: Mapping[str, int | None]
) -> tuple[DiaryEntry, ...]:
    """Every non-blank row of ``reader``; a short row's missing cells read as ""."""
    return tuple(
        DiaryEntry(
            **{
                field: cells[index] if index is not None and index < len(cells) else ""
                for field, index in at.items()
            }
        )
        for cells in reader
        if cells
    )


# fuzz-target
def parse_entries_csv(text: str, *, needed: tuple[str, ...]) -> tuple[DiaryEntry, ...]:
    """The rows of one export CSV, each column located by its header name.

    Every column in ``needed`` is required, even when no row follows the header, so
    empty text is refused; every other column reads as "" when absent. A leading byte
    order mark is accepted and blank lines are skipped. A cell may be as long as the
    whole file's cap. Raises ``RestoreError`` for a header lacking a needed column, or
    text that is not valid CSV.
    """
    reader = csv.reader(io.StringIO(text.removeprefix(_BOM), newline=""), strict=True)
    # csv's process-wide field limit is short of a long review: export's own context
    # raises it to the file cap for this parse only, then puts it back.
    try:
        with csv_field_limit():
            return _entries(reader, _positions(next(reader, []), needed))
    except csv.Error:
        raise RestoreError("not valid CSV") from None


def _with_option(option: str, message: str) -> str:
    """``message`` led by ``option``, unless it already is."""
    return message if message.startswith(f"{option}:") else f"{option}: {message}"


def _parsed(
    texts: Mapping[str, str], name: str, option: str, needed: tuple[str, ...]
) -> tuple[DiaryEntry, ...]:
    """The rows of the file ``name``, () when absent; errors name option and file."""
    if name not in texts:
        return ()
    try:
        return parse_entries_csv(texts[name], needed=needed)
    except RestoreError as error:
        raise RestoreError(f"{option}: {name}: {error}") from None


def read_export(
    path: Path, option: str, names: tuple[str, ...] = EXPORT_FILES
) -> ExportFiles:
    """The diary, reviews, and likes of the export at ``path``, given as ``option``.

    Only the files in ``names`` are read (``diary.csv`` among them); any other reads
    as no rows, however damaged. Raises ``RestoreError`` when the export is
    unreadable or past export's bounds, holds no ``diary.csv``, or a file read is
    not UTF-8 or not valid CSV with the needed columns.
    """
    try:
        texts = read_export_texts(path, names, option)
    except AccountError as error:
        raise RestoreError(_with_option(option, str(error))) from None
    if DIARY not in texts:
        raise RestoreError(f"{option}: holds no {DIARY}")
    return ExportFiles(
        diary=_parsed(texts, DIARY, option, DIARY_NEEDED),
        reviews=_parsed(texts, REVIEWS, option, REVIEWS_NEEDED),
        likes=_parsed(texts, LIKES, option, LIKES_NEEDED),
    )
