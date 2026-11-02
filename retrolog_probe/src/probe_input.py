"""Read retrolog_export's Letterboxd import files: which rows carry which TMDb id.

Every ``letterboxd-NNN.csv`` in the folder is read, in number order, and must carry
export's exact header. A row with an empty ``tmdbID`` goes to Letterboxd by title and
year, so there is no id to check: it is counted and skipped. Every other row's id must
pass ``probe_ids.is_tmdb_id``, or the whole run is refused.

A row's number counts the file's data rows from 1, the header not counted. It is not a
line number: a review cell can span lines, and a row is one CSV record however many
lines it takes.

Every refusal names the file and row, never a path or a cell's value.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from export_account import csv_field_limit
from export_letterboxd import COLUMNS
from probe_ids import is_tmdb_id

# The name export gives each import file: "letterboxd-" and a number of at least three
# digits ("letterboxd-001.csv"). Its own test file and review files do not match.
_IMPORT_NAME = re.compile(r"letterboxd-([0-9]{3,})\.csv")
_ID, _TITLE, _YEAR = (COLUMNS.index(name) for name in ("tmdbID", "Title", "Year"))


class InputError(Exception):
    """The import files cannot be checked; the message names no path and no value."""


@dataclass(frozen=True)
class IdRow:
    """One import row that carries a tmdbID: where it is, and what it names."""

    file: str
    row: int
    tmdb_id: str
    title: str
    year: str


@dataclass(frozen=True)
class ImportRows:
    """What the import files hold: their names, row counts, and the rows with an id."""

    files: tuple[str, ...]
    rows: int
    by_title: int
    id_rows: tuple[IdRow, ...]

    def distinct_ids(self) -> tuple[str, ...]:
        """Each id once, in the order it first appears."""
        return tuple(dict.fromkeys(row.tmdb_id for row in self.id_rows))


def _import_files(folder: Path) -> list[Path]:
    """The folder's import files, in number order; ``InputError`` when there are none."""
    if not folder.is_dir():
        raise InputError("--files: no such folder")
    found = [
        (int(match.group(1)), path)
        for path in folder.iterdir()
        if (match := _IMPORT_NAME.fullmatch(path.name)) and path.is_file()
    ]
    if not found:
        raise InputError("--files: holds no letterboxd-NNN.csv import file")
    return [path for _, path in sorted(found)]


def _records(path: Path) -> list[list[str]]:
    """``path``'s CSV records, the header first.

    Read under export's raised field limit: export writes a review cell of any length,
    past csv's default limit, and that limit would refuse the whole file.
    """
    name = path.name
    try:
        with path.open(encoding="utf-8", newline="") as handle, csv_field_limit():
            return list(csv.reader(handle, strict=True))
    except UnicodeDecodeError:
        raise InputError(f"{name}: not UTF-8 text") from None
    except csv.Error as error:
        raise InputError(f"{name}: not a CSV file ({type(error).__name__})") from None
    except OSError as error:
        raise InputError(f"could not read {name}: {type(error).__name__}") from None


def _id_row(name: str, number: int, cells: list[str]) -> IdRow | None:
    """Data row ``number`` of file ``name``, or None for a by-title row."""
    if len(cells) != len(COLUMNS):
        raise InputError(f"{name} row {number}: not one cell per column")
    tmdb_id = cells[_ID]
    if not tmdb_id:
        return None
    if not is_tmdb_id(tmdb_id):
        raise InputError(f"{name} row {number}: its tmdbID is not a TMDb id")
    return IdRow(name, number, tmdb_id, cells[_TITLE], cells[_YEAR])


def read_imports(folder: Path) -> ImportRows:
    """Every import file in ``folder``; ``InputError`` for anything export would not write."""
    files: list[str] = []
    rows = 0
    id_rows: list[IdRow] = []
    for path in _import_files(folder):
        records = _records(path)
        if not records or tuple(records[0]) != COLUMNS:
            raise InputError(f"{path.name}: its header is not retrolog_export's")
        files.append(path.name)
        for number, cells in enumerate(records[1:], start=1):
            rows += 1
            found = _id_row(path.name, number, cells)
            if found is not None:
                id_rows.append(found)
    return ImportRows(tuple(files), rows, rows - len(id_rows), tuple(id_rows))
