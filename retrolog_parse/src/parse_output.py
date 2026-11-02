"""Render rows as CSV files and the review list as JSON and Markdown text.

Pure: every function returns text, and the caller writes it. The CSV columns are the
normalized format the later tools read (``COLUMNS``, one per ``Row`` field, in order);
rows go to one file per medium and year, ``<medium>-<year>.csv``, or
``<medium>-undated.csv`` for rows no rule could date. ``CSV_NAME`` is the one pattern
that matches every such name, for the later tools that read them, and ``year_order``
the one key they sort such names' years by.

The review text quotes source lines, which are untrusted: the Markdown escapes the
characters that could break its layout (``MARKUP``) and keeps each value on one line,
by the survey report's own rules, imported from ``survey_markdown``.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import re
import typing
from collections.abc import Mapping, Sequence
from dataclasses import asdict, astuple

from parse_rows import ERROR_KINDS, REVIEW_KINDS, Row
from parse_types import Medium, ReviewItem
from survey_markdown import BREAK_MARK, LINE_BREAK, MARKUP

COLUMNS = (
    "medium",
    "title",
    "creator",
    "year",
    "date",
    "date_is_approximate",
    "color",
    "rating_raw",
    "comment",
    "rewatch",
    "section",
    "source_file",
    "source_line",
)
# The year part of an undated row's file name.
UNDATED = "undated"
# Every name csv_name writes, and nothing else: groups (medium, year or UNDATED). The
# year is unpadded and may have any number of digits, since dates.century can put a
# row before the year 1000; ASCII, so no other script's digit counts. Use fullmatch.
CSV_NAME = re.compile(
    rf"({'|'.join(typing.get_args(Medium))})-(\d+|{UNDATED})\.csv", re.ASCII
)
# The review.json layout version; a later incompatible layout bumps it.
SCHEMA_VERSION = 1
# Sorts an undated row's missing date; every row of one undated file has none.
_NO_DATE = datetime.date.min
_TITLE = "# retrolog_parse review"
_TABLE_HEAD = ("| File | Line | Text | Detail |", "| --- | --- | --- | --- |")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _cell(value: object) -> str:
    """One CSV cell: ISO dates, ``true``/``false``, empty for None, else the text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def csv_name(row: Row) -> str:
    """The file a row belongs in: ``<medium>-<year>.csv`` or ``<medium>-undated.csv``."""
    year = UNDATED if row.date is None else str(row.date.year)
    return f"{row.medium}-{year}.csv"


def year_order(year: str) -> tuple[bool, int, str]:
    """The sort key of a ``CSV_NAME`` year part: dated years in order, then UNDATED.

    The year is unpadded, so ``film-150.csv`` would sort after ``film-1499.csv`` as
    text; a shorter year string is the earlier year, since csv_name never writes a
    leading zero.
    """
    undated = year == UNDATED
    return (undated, 0 if undated else len(year), year)


def _sort_key(row: Row) -> tuple[datetime.date, str, int]:
    """Date, then source file, then line."""
    return (row.date or _NO_DATE, row.source_file, row.source_line)


def _csv_text(rows: Sequence[Row]) -> str:
    """A header of ``COLUMNS``, then one record per row in date order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(
        [_cell(value) for value in astuple(row)] for row in sorted(rows, key=_sort_key)
    )
    return buffer.getvalue()


def csv_files(rows: Sequence[Row]) -> dict[str, str]:
    """Every CSV file the rows fill: file name -> CSV text, names sorted."""
    grouped: dict[str, list[Row]] = {}
    for row in rows:
        grouped.setdefault(csv_name(row), []).append(row)
    return {name: _csv_text(grouped[name]) for name in sorted(grouped)}


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def review_counts(review: Sequence[ReviewItem]) -> dict[str, int]:
    """How many items of each kind, every kind present, in ``REVIEW_KINDS`` order."""
    counts = dict.fromkeys(REVIEW_KINDS, 0)
    for item in review:
        counts[item.kind] += 1
    return counts


def _item_key(item: ReviewItem) -> tuple[str, int, str]:
    """File, then line, then kind."""
    return (item.rel_path, item.line, item.kind)


def review_json(review: Sequence[ReviewItem], counts: Mapping[str, int]) -> str:
    """``{"schema_version", "counts", "items"}``, keys sorted, items by file and line."""
    document = {
        "schema_version": SCHEMA_VERSION,
        "counts": dict(counts),
        "items": [asdict(item) for item in sorted(review, key=_item_key)],
    }
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def escape_cell(value: object) -> str:
    """``value`` as Markdown table text: trimmed, one line, markup characters escaped.

    Trimmed because a table cell drops its edge spaces anyway, and padding inside the
    pipes breaks the compact table style a Markdown linter holds the page to.
    """
    text = LINE_BREAK.sub(BREAK_MARK, str(value)).strip()
    return MARKUP.sub(r"\\\1", text)


def _table_row(item: ReviewItem) -> str:
    """One item as a table row: file, line, text and detail."""
    cells = (item.rel_path, item.line, item.text, item.detail)
    # An empty cell is one space between its pipes, the compact style's spelling.
    padded = (f" {text} " if text else " " for text in map(escape_cell, cells))
    return "|" + "|".join(padded) + "|"


def _kind_section(kind: str, items: Sequence[ReviewItem]) -> list[str]:
    """One kind's heading and table, its items by file and line."""
    label = f"{len(items)}, error" if kind in ERROR_KINDS else str(len(items))
    rows = [_table_row(item) for item in sorted(items, key=_item_key)]
    return ["", f"## {kind} ({label})", "", *_TABLE_HEAD, *rows]


def review_markdown(review: Sequence[ReviewItem], counts: Mapping[str, int]) -> str:
    """A count line, then one table per kind present, in ``REVIEW_KINDS`` order.

    ``REVIEW_KINDS`` lists the error kinds first, so errors lead the page.
    """
    errors = sum(counts.get(kind, 0) for kind in ERROR_KINDS)
    lines = [
        _TITLE,
        "",
        count_line(sum(counts.values()), errors),
    ]
    for kind in REVIEW_KINDS:
        items = [item for item in review if item.kind == kind]
        if items:
            lines.extend(_kind_section(kind, items))
    return "\n".join(lines) + "\n"


def count_line(total: int, errors: int) -> str:
    """A review's count line, each noun singular for a count of one.

    Public so resolve's review, and export's after its sent counts, print the same
    line from the same code.
    """
    items = "1 review item" if total == 1 else f"{total} review items"
    of_them = "1 of them an error" if errors == 1 else f"{errors} of them errors"
    return f"{items}, {of_them}."
