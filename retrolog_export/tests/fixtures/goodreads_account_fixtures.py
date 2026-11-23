"""Build invented Goodreads library exports and book rows at test time.

A Goodreads "Export Library" download is one CSV, ``goodreads_library_export.csv``;
this module renders one from invented books (``Placeholder Book One`` by
``Invented Author``) and writes it into a temporary folder. Every ISBN-13 is made here
from a small number with its check digit computed, so none is copied from a real
list, and every work key is invented (``/works/OL1W``).

The importing test puts the tool's, resolve's, parse's, and the survey's src/ folders
and this folder on ``sys.path`` before importing this module. It uses the standard
library only, so the sample's builder (``samples/goodreads_inputs.py``) imports it too.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime
import io
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict, Unpack

from export_types import ResolvedRow
from parse_rows import title_key

# The columns of a Goodreads library export, in the order its download gives them.
# This is the service's published file format; no cell of any real export is here.
LIBRARY_HEADER = (
    "Book Id",
    "Title",
    "Author",
    "Author l-f",
    "Additional Authors",
    "ISBN",
    "ISBN13",
    "My Rating",
    "Average Rating",
    "Publisher",
    "Binding",
    "Number of Pages",
    "Year Published",
    "Original Publication Year",
    "Date Read",
    "Date Added",
    "Bookshelves",
    "Bookshelves with positions",
    "Exclusive Shelf",
    "My Review",
    "Spoiler",
    "Private Notes",
    "Read Count",
    "Owned Copies",
)
# The columns the reader requires: a header missing one is refused.
REQUIRED_COLUMNS = (
    "Title",
    "Author",
    "ISBN13",
    "My Rating",
    "Exclusive Shelf",
    "Read Count",
)
# The file name Goodreads gives its download.
LIBRARY_FILE = "goodreads_library_export.csv"
# An ISBN-13's prefix, and how many digits follow it before the check digit.
_PREFIX, _BODY_DIGITS = "978", 9
# The ISBN-13 check digit's weights, alternating from the first digit.
_WEIGHTS = (1, 3)


def isbn13(number: int) -> str:
    """An invented ISBN-13: ``978``, ``number`` zero-padded to nine digits, its check.

    The check digit is the standard one, so the result passes every ISBN-13 check
    export makes, though it names no real book.
    """
    body = f"{_PREFIX}{number:0{_BODY_DIGITS}d}"
    total = sum(int(digit) * _WEIGHTS[at % 2] for at, digit in enumerate(body))
    return f"{body}{(10 - total % 10) % 10}"


def wrapped(isbn: str) -> str:
    """``isbn`` as Goodreads writes it in a cell, ``="<isbn>"``."""
    return f'="{isbn}"'


# One library row's defaults: an unread, unrated book on the to-read shelf.
_BOOK_DEFAULTS: Mapping[str, str] = {
    "Book Id": "1",
    "Title": "Placeholder Book One",
    "Author": "Invented Author",
    "ISBN": '=""',
    "ISBN13": wrapped(isbn13(1)),
    "My Rating": "0",
    "Exclusive Shelf": "to-read",
    "Read Count": "0",
}


def library_book(**cells: str) -> dict[str, str]:
    """One library row, the defaults replaced by ``cells``; keys are column names.

    A column named neither here nor in ``cells`` is empty. Python keywords cannot hold
    a space, so a column such as ``My Rating`` is given through ``**{...}``.
    """
    unknown = set(cells) - set(LIBRARY_HEADER)
    if unknown:
        raise KeyError(f"not a library column: {sorted(unknown)}")
    return {**_BOOK_DEFAULTS, **cells}


def library_csv(
    books: Sequence[Mapping[str, str]], header: Sequence[str] = LIBRARY_HEADER
) -> str:
    """A library export's text: ``header``, then one row per book in its columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for book in books:
        writer.writerow([book.get(column, "") for column in header])
    return buffer.getvalue()


def write_library(folder: Path, text: str, name: str = LIBRARY_FILE) -> Path:
    """``text`` written as UTF-8 to ``folder / name``; returns the file's path."""
    path = folder / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


# One resolve book row: resolved by Open Library, with an ISBN-13 and a work key.
BOOK_BASE = ResolvedRow(
    source_csv="book-2010.csv",
    title="Placeholder Book One",
    title_key=title_key("Placeholder Book One"),
    year=None,
    date=datetime.date(2010, 3, 4),
    rating_raw="2",
    comment="",
    rewatch=False,
    creator="Invented Author",
    status="resolved",
    tmdb_id="",
    source_file="10/read.txt",
    source_line=1,
    isbn13=isbn13(1),
    ol_work="/works/OL1W",
)


class BookChanges(TypedDict, total=False):
    """The ResolvedRow fields a book test changes, typed as the dataclass has them."""

    title: str
    creator: str
    status: str
    isbn13: str
    ol_work: str
    source_line: int


def book_row(**changes: Unpack[BookChanges]) -> ResolvedRow:
    """``BOOK_BASE`` with ``changes`` applied; a changed title carries its own key."""
    row = dataclasses.replace(BOOK_BASE, **changes)
    return dataclasses.replace(row, title_key=title_key(row.title))
