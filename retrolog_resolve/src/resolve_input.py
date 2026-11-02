"""Read parse's CSV files back as rows, keeping every cell exactly as it was written.

``read_parsed`` reads each ``<medium>-<year>.csv`` and ``<medium>-undated.csv`` directly
in the parse output folder, the names ``parse_output.CSV_NAME`` matches, by medium and
then year, and ignores every other file there
(``review.json``, ``review.md``, the tool's marker), and every CSV of a medium the run
did not select. Each cell stays text, so the row a later step writes back is
byte-identical in parse's columns; only the medium, title, years, and source line are
parsed out, and a cell that cannot be is refused rather than guessed at.

Every ``InputError`` names the CSV by its own name and the line, never a path above the
folder and never the cell's text, which can be the operator's private list content.
"""

from __future__ import annotations

import csv
import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from parse_output import COLUMNS, CSV_NAME, UNDATED, year_order
from parse_rows import title_key
from resolve_columns import DATE_CELL, LINE_CELL, YEAR_CELL
from resolve_types import InputRow, Medium, RunMedium


class InputError(Exception):
    """A parse output file is unusable; the message names the CSV, never a path."""


def _year(where: str, text: str) -> int | None:
    """The ``year`` cell: empty for none, else one to four ASCII digits."""
    if not text:
        return None
    if not YEAR_CELL.fullmatch(text):
        raise InputError(f"{where}: year must be empty or one to four digits")
    return int(text)


def _date_year(where: str, text: str, file_year: str) -> int | None:
    """The year of the ``date`` cell, which must agree with the file's own name."""
    if not text and file_year == UNDATED:
        return None
    try:
        if not DATE_CELL.fullmatch(text):
            raise ValueError
        year = datetime.date.fromisoformat(text).year
    except ValueError:
        raise InputError(f"{where}: date must be a date such as 2001-12-31") from None
    if str(year) != file_year:
        raise InputError(f"{where}: date is not in the year the file name gives")
    return year


@dataclass(frozen=True)
class _CsvFile:
    """One CSV file to read: its own name and what that name says of its rows."""

    name: str  # "book-2010.csv"
    medium: Medium
    year: str  # "2010", or UNDATED


def _row(
    csv_file: _CsvFile, line: int, record: Mapping[str | None, object]
) -> InputRow:
    """One record as an ``InputRow``, its cells checked against the file's name."""
    where = f"{csv_file.name}: line {line}"
    # DictReader keys surplus cells under None and fills missing ones with None.
    if None in record or any(value is None for value in record.values()):
        raise InputError(f"{where}: has a different number of cells than the header")
    cells = {column: str(record[column]) for column in COLUMNS}
    if cells["medium"] != csv_file.medium:
        raise InputError(f"{where}: medium differs from the file name's")
    if not LINE_CELL.fullmatch(cells["source_line"]):
        raise InputError(f"{where}: source_line must be a positive whole number")
    return InputRow(
        source_csv=csv_file.name,
        cells=MappingProxyType(cells),
        medium=csv_file.medium,
        title=cells["title"],
        title_key=title_key(cells["title"]),
        work_year=_year(where, cells["year"]),
        date_year=_date_year(where, cells["date"], csv_file.year),
        source_file=cells["source_file"],
        source_line=int(cells["source_line"]),
    )


def _read_csv(path: Path, csv_file: _CsvFile) -> list[InputRow]:
    """Every record of one CSV file, its header first checked against ``COLUMNS``.

    Its twin is export_input's ``_read_csv``, as ``_csv_files`` and ``_year`` are:
    each reader checks another header, builds another row and raises its own
    ``InputError``, so only the cell shapes in resolve_columns are shared.

    ``reader.line_num`` is the physical line a record ends on, the header being line
    1, so a message names the line an editor shows even after a multi-line cell.
    """
    name = csv_file.name
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            # strict: parse writes plain CSV, so a stray or unclosed quote is damage.
            reader = csv.DictReader(handle, strict=True)
            if tuple(reader.fieldnames or ()) != COLUMNS:
                raise InputError(f"{name}: header is not parse's columns, in order")
            return [_row(csv_file, reader.line_num, record) for record in reader]
    except UnicodeDecodeError:
        raise InputError(f"{name}: not UTF-8") from None
    except csv.Error:
        raise InputError(f"{name}: not valid CSV") from None
    except OSError:
        raise InputError(f"{name}: cannot be read") from None


def _csv_files(parsed: Path, selected: RunMedium) -> list[_CsvFile]:
    """The ``selected`` CSV_NAME files directly in ``parsed``, by medium, then year.

    Every other entry is ignored, the other medium's CSVs included, so a damaged one
    of those cannot stop a run that does not read it.

    A folder that cannot be listed, or an entry whose type cannot be read, is refused
    as a whole: an OSError's message carries the absolute path it failed on.
    """
    found = []
    try:
        for entry in parsed.iterdir():
            match = CSV_NAME.fullmatch(entry.name)
            if not match or selected not in ("all", match.group(1)):
                continue
            if entry.is_file():
                medium: Medium = "book" if match.group(1) == "book" else "film"
                found.append(_CsvFile(entry.name, medium, match.group(2)))
    except OSError:
        raise InputError("--parsed: cannot be read") from None
    return sorted(found, key=lambda each: (each.medium, year_order(each.year)))


def read_parsed(parsed: Path, selected: RunMedium = "all") -> tuple[InputRow, ...]:
    """Every row of every ``selected`` CSV_NAME file in ``parsed``, files in year order.

    Other files, the unselected medium's CSVs and directories are ignored. Raises
    ``InputError`` when ``parsed`` is not a folder, cannot be listed or holds no
    selected CSV_NAME file (a wrong path must not reach the step that empties an
    earlier ``--out``), or when a file's header, medium, year, date or line cell is
    not parse's.
    """
    if not parsed.is_dir():
        raise InputError("--parsed: no such folder")
    csv_files = _csv_files(parsed, selected)
    if not csv_files:
        kind = "parse" if selected == "all" else selected
        raise InputError(f"--parsed: holds no {kind} CSV file")
    return tuple(
        row
        for csv_file in csv_files
        for row in _read_csv(parsed / csv_file.name, csv_file)
    )
