"""Read resolve's CSV files back as rows, with the fields export needs parsed out.

``read_resolved`` reads each ``<medium>-<year>.csv`` and ``<medium>-undated.csv``
directly in the resolve output folder, in name order, and ignores every other file
there (``review.json``, ``review.md``, the cache folder) and every CSV of another
medium, so a resolve folder holding older book CSVs beside fresh film ones is read
correctly: the book files are never opened. Every cell export uses is checked, and a
cell that does not hold what resolve writes is refused rather than guessed at.

Every ``InputError`` names the CSV by its own name and the line, never a path above the
folder and never the cell's text, which can be the operator's private list content.
"""

from __future__ import annotations

import csv
import datetime
import re
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from export_titles import match_keys
from export_types import ResolvedRow
from parse_output import COLUMNS, UNDATED
from parse_rows import title_key
from resolve_columns import DATE_CELL, LINE_CELL, RESOLVE_COLUMNS, YEAR_CELL
from resolve_types import Status

# Every column of a resolve CSV, in order: parse's, then the ones resolve adds.
INPUT_COLUMNS = COLUMNS + RESOLVE_COLUMNS
CSV_NAME = re.compile(r"^(book|film)-([0-9]{4}|undated)\.csv$", re.ASCII)
# The tmdb_id cell: ASCII digits, kept as text and never passed to int(). The year,
# date and line cells take resolve_columns' bounded shapes, which resolve's own reader
# of parse's CSVs checks too.
_DIGITS = re.compile(r"[0-9]+")
_BOOLEANS: Mapping[str, bool] = {"true": True, "false": False}
_STATUSES = frozenset(typing.get_args(Status))
# The columns holding a resolved film's TMDb titles; resolve fills them only
# beside a tmdb_id.
_TMDB_TITLES = ("tmdb_title", "tmdb_original_title")


class InputError(Exception):
    """A resolve output file is unusable; the message names the CSV, never a path."""


@dataclass(frozen=True)
class _CsvFile:
    """One CSV file to read: its own name and the year its name gives."""

    name: str  # "film-2010.csv"
    medium: str
    year: str  # "2010", or UNDATED


def _year(where: str, text: str) -> int | None:
    """The ``year`` cell: empty for none, else one to four ASCII digits."""
    if not text:
        return None
    if not YEAR_CELL.fullmatch(text):
        raise InputError(f"{where}: year must be empty or one to four digits")
    return int(text)


def _date(where: str, text: str, file_year: str) -> datetime.date | None:
    """The ``date`` cell, which must agree with the file's own name."""
    if file_year == UNDATED:
        if text:
            raise InputError(f"{where}: date must be empty in an undated file")
        return None
    try:
        if not DATE_CELL.fullmatch(text):
            raise ValueError
        date = datetime.date.fromisoformat(text)
    except ValueError:
        raise InputError(f"{where}: date must be a date such as 2001-12-31") from None
    if str(date.year) != file_year:
        raise InputError(f"{where}: date is not in the year the file name gives")
    return date


def _checked_cells(where: str, cells: Mapping[str, str], csv_file: _CsvFile) -> None:
    """Refuse a cell export reads that holds what resolve would not write.

    The medium, line, status, rewatch, tmdb_id and TMDb title cells are checked.
    """
    if cells["medium"] != csv_file.medium:
        raise InputError(f"{where}: medium differs from the file name's")
    if not LINE_CELL.fullmatch(cells["source_line"]):
        raise InputError(f"{where}: source_line must be a positive whole number")
    if cells["resolve_status"] not in _STATUSES:
        raise InputError(f"{where}: resolve_status is not one of resolve's")
    if cells["rewatch"] not in _BOOLEANS:
        raise InputError(f"{where}: rewatch must be true or false")
    if cells["tmdb_id"] and not _DIGITS.fullmatch(cells["tmdb_id"]):
        raise InputError(f"{where}: tmdb_id must be empty or digits")
    for column in _TMDB_TITLES:
        if cells[column] and not cells["tmdb_id"]:
            raise InputError(f"{where}: {column} must be empty with no tmdb_id")


def _tmdb_title_keys(cells: Mapping[str, str]) -> tuple[str, ...]:
    """Each TMDb title's match keys, once, in column order; an empty one dropped."""
    keys = (key for column in _TMDB_TITLES for key in match_keys(cells[column]))
    # dict.fromkeys keeps first sightings in order, dropping a repeat.
    return tuple(dict.fromkeys(key for key in keys if key))


def _row(
    csv_file: _CsvFile, line: int, record: Mapping[str | None, object]
) -> ResolvedRow:
    """One record as a ``ResolvedRow``, its cells checked against the file's name."""
    where = f"{csv_file.name}: line {line}"
    # DictReader keys surplus cells under None and fills missing ones with None.
    if None in record or any(value is None for value in record.values()):
        raise InputError(f"{where}: has a different number of cells than the header")
    cells = {column: str(record[column]) for column in INPUT_COLUMNS}
    _checked_cells(where, cells, csv_file)
    return ResolvedRow(
        source_csv=csv_file.name,
        title=cells["title"],
        title_key=title_key(cells["title"]),
        year=_year(where, cells["year"]),
        date=_date(where, cells["date"], csv_file.year),
        rating_raw=cells["rating_raw"],
        comment=cells["comment"],
        rewatch=_BOOLEANS[cells["rewatch"]],
        creator=cells["creator"],
        status=cells["resolve_status"],
        tmdb_id=cells["tmdb_id"],
        source_file=cells["source_file"],
        source_line=int(cells["source_line"]),
        tmdb_title_keys=_tmdb_title_keys(cells),
    )


def _read_csv(path: Path, csv_file: _CsvFile) -> list[ResolvedRow]:
    """Every record of one CSV file, its header first checked against INPUT_COLUMNS.

    Its twin is resolve_input's ``_read_csv``, as ``_csv_files`` and ``_year`` are:
    each reader checks another header, builds another row and raises its own
    ``InputError``, so only the cell shapes in resolve_columns are shared.

    ``reader.line_num`` is the physical line a record ends on, the header being line
    1, so a message names the line an editor shows even after a multi-line cell.
    """
    name = csv_file.name
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            # strict: resolve writes plain CSV, so a stray or unclosed quote is damage.
            reader = csv.DictReader(handle, strict=True)
            if tuple(reader.fieldnames or ()) != INPUT_COLUMNS:
                # A header other than resolve's: a rerun of resolve rewrites the file.
                raise InputError(
                    f"{name}: header is not resolve's columns, in order; rerun "
                    "retrolog_resolve to rewrite it"
                )
            return [_row(csv_file, reader.line_num, record) for record in reader]
    except UnicodeDecodeError:
        raise InputError(f"{name}: not UTF-8") from None
    except csv.Error:
        raise InputError(f"{name}: not valid CSV") from None
    except OSError:
        raise InputError(f"{name}: cannot be read") from None


def _csv_files(resolved: Path, medium: str) -> list[_CsvFile]:
    """The ``medium`` CSV_NAME files directly in ``resolved``, in name order.

    Every other entry is ignored, the other medium's CSVs included, so a damaged one
    of those cannot stop a run that does not read it. A folder that cannot be listed
    is refused as a whole: an OSError's message carries the absolute path it failed on.
    """
    found = []
    try:
        for entry in resolved.iterdir():
            match = CSV_NAME.fullmatch(entry.name)
            if match and match.group(1) == medium and entry.is_file():
                found.append(_CsvFile(entry.name, medium, match.group(2)))
    except OSError:
        raise InputError("--resolved: cannot be read") from None
    return sorted(found, key=lambda csv_file: csv_file.name)


def read_resolved(resolved: Path, medium: Literal["film"]) -> tuple[ResolvedRow, ...]:
    """Every row of every ``medium`` CSV in ``resolved``, files in name order.

    Raises ``InputError`` when ``resolved`` is not a folder, cannot be listed or holds
    no ``medium`` CSV, or when a file is unreadable, not UTF-8, not valid CSV, has a
    header other than ``INPUT_COLUMNS`` (an older resolve's included), or holds a
    medium, year, date, line, status, rewatch, tmdb_id or TMDb title cell resolve
    does not write.
    """
    if not resolved.is_dir():
        raise InputError("--resolved: no such folder")
    csv_files = _csv_files(resolved, medium)
    if not csv_files:
        raise InputError(f"--resolved: holds no {medium} CSV file")
    return tuple(
        row
        for csv_file in csv_files
        for row in _read_csv(resolved / csv_file.name, csv_file)
    )
