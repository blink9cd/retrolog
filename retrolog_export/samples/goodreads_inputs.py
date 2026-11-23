"""The Goodreads half of export's sample: invented book CSVs and a library export.

``build_export_inputs.py --service goodreads`` calls ``write_inputs``. Every title is
a placeholder (``Placeholder Book One``), every author too, every work key is made up,
and every ISBN-13 is built from an invented number with its check digit computed, so
no real book is paired with an id that is not its own. ``write_inputs(out)`` writes:

- ``out/resolved/``: three book CSV files in resolve's columns, one of them undated;
- ``out/account/goodreads_library_export.csv``: a library export whose four books are
  one read, one rated on a custom shelf, one only shelved there, unrated, and one on
  ``to-read``;
- ``out/export.toml``: ``export.example.toml`` beside this file, unchanged. Its
  ``[goodreads]`` cutoff names one of the rows, so it and the row after it are cut,
  and its omit list names another.

The README's table says what each book shows.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

import export_fixtures as fx
from goodreads_account_fixtures import (
    LIBRARY_FILE,
    isbn13,
    library_book,
    library_csv,
    wrapped,
)

_HERE = Path(__file__).resolve().parent
_READ, _LATER, _UNDATED = "2010/books.txt", "2019/books.txt", "misc/books.txt"


def _book(
    title: str, source_file: str, line: int, number: int | None, **cells: str
) -> dict[str, str]:
    """One resolve book row of ``title``, ISBN-13 ``isbn13(number)`` unless None.

    Every cell not given is empty or resolve's default; a book has no tmdbID.
    """
    defaults = {
        "medium": "book",
        "creator": "",
        "year": "",
        "rating_raw": "",
        "tmdb_id": "",
        "isbn13": "" if number is None else isbn13(number),
        "resolve_status": "resolved",
    }
    return fx.resolved_row(
        title=title,
        source_file=source_file,
        source_line=str(line),
        **(defaults | cells),
    )


# Each CSV's rows, in file order. The comment on each says what the row shows.
_FILES: Mapping[str, list[dict[str, str]]] = {
    "book-2010.csv": [
        # resolved, with a comment: My Review holds it, Private Notes the note.
        _book(
            "Placeholder Book One",
            _READ,
            2,
            1,
            date="2010-02-03",
            year="1925",
            creator="Author One",
            rating_raw="a",
            comment="Read on a train.",
            ol_work="/works/OL1W",
        ),
        # an override with an ISBN-13 and two authors: the second goes to
        # Additional Authors.
        _book(
            "Placeholder Book Two",
            _READ,
            4,
            2,
            date="2010-03-04",
            year="1930",
            creator="Author Two & Author Three",
            rating_raw="c",
            resolve_status="override",
            ol_work="/works/OL2W",
        ),
        # an override with a work key and an author but no ISBN-13: by-title. Its
        # comment starts with "=", which a spreadsheet reads as a formula.
        _book(
            "Placeholder Book Three",
            _READ,
            5,
            None,
            date="2010-03-04",
            year="1935",
            creator="Author Four",
            rating_raw="b",
            comment="=two readings in one",
            resolve_status="override",
            ol_work="/works/OL3W",
        ),
        # an unresolvable override with no author: title-only.
        _book(
            "Placeholder Book Four",
            _READ,
            6,
            None,
            date="2010-04-05",
            rating_raw="d",
            resolve_status="override",
        ),
        # an override whose work gave no author: author-missing.
        _book(
            "Placeholder Book Five",
            _READ,
            7,
            5,
            date="2010-04-05",
            year="1940",
            rating_raw="b",
            resolve_status="override",
            ol_work="/works/OL5W",
        ),
        # read in 2010 and again in 2019 (book-2019.csv): one row, the newer read's.
        _book(
            "Placeholder Book Six",
            _READ,
            8,
            6,
            date="2010-05-06",
            year="1945",
            creator="Author Five",
            rating_raw="b",
        ),
        # read on the account: on-service, not sent.
        _book(
            "Placeholder Book Seven",
            _READ,
            9,
            7,
            date="2010-06-07",
            year="1950",
            creator="Author Six",
            rating_raw="c",
        ),
        # ambiguous: held back, an error.
        _book(
            "Placeholder Book Ten",
            _READ,
            10,
            None,
            date="2010-06-07",
            rating_raw="a",
            resolve_status="ambiguous",
        ),
        # named in the config's omit list: omitted.
        _book(
            "Placeholder Book Nine",
            _READ,
            11,
            9,
            date="2010-07-08",
            year="1955",
            creator="Author Seven",
            rating_raw="a",
        ),
        # rated on the account's custom shelf: read there, so on-service.
        _book(
            "Placeholder Book Eleven",
            _READ,
            12,
            11,
            date="2010-08-09",
            year="1960",
            creator="Author Eight",
            rating_raw="d",
        ),
        # on the account's custom shelf, unrated there: shelved, so sent and listed
        # remove-first, its row tagged with the shelf's tag. Unrated in the lists
        # too: an unrated entry.
        _book(
            "Placeholder Book Twelve",
            _READ,
            13,
            12,
            date="2010-09-10",
            year="1965",
            creator="Author Nine",
        ),
        # on the account's to-read shelf: sent and listed remove-first, with no tag,
        # since the example config maps no tag to to-read.
        _book(
            "Placeholder Book Fifteen",
            _READ,
            14,
            15,
            date="2010-10-11",
            year="1985",
            creator="Author Thirteen",
            rating_raw="b",
        ),
    ],
    "book-2019.csv": [
        # Placeholder Book Six again: the newest read, so its date and rating go.
        _book(
            "Placeholder Book Six",
            _LATER,
            2,
            6,
            date="2019-03-04",
            year="1945",
            creator="Author Five",
            rating_raw="c",
            rewatch="true",
        ),
        # The row the example config's cutoff names: cut, with every row after it.
        _book(
            "Placeholder Book Eight",
            _LATER,
            4,
            8,
            date="2019-06-07",
            year="1970",
            creator="Author Ten",
            rating_raw="b",
        ),
        _book(
            "Placeholder Book Thirteen",
            _LATER,
            6,
            13,
            date="2019-08-09",
            year="1975",
            creator="Author Eleven",
            rating_raw="a",
        ),
    ],
    "book-undated.csv": [
        # undated: never cut; Date Read blank, Date Added the config's early date.
        _book(
            "Placeholder Book Fourteen",
            _UNDATED,
            2,
            14,
            date="",
            year="1980",
            creator="Author Twelve",
            rating_raw="c",
        ),
    ],
}
# The library export: one book read, one rated on a custom shelf, one only on a
# custom shelf, unrated, and one on to-read.
_LIBRARY = [
    library_book(
        Title="Placeholder Book Seven",
        Author="Author Six",
        ISBN13=wrapped(isbn13(7)),
        **{"Exclusive Shelf": "read", "My Rating": "4", "Read Count": "1"},
    ),
    library_book(
        Title="Placeholder Book Eleven",
        Author="Author Eight",
        ISBN13=wrapped(isbn13(11)),
        **{"Exclusive Shelf": "custom-shelf", "My Rating": "5", "Read Count": "0"},
    ),
    library_book(
        Title="Placeholder Book Twelve",
        Author="Author Nine",
        ISBN13=wrapped(isbn13(12)),
        **{"Exclusive Shelf": "custom-shelf", "My Rating": "0", "Read Count": "0"},
    ),
    library_book(
        Title="Placeholder Book Fifteen",
        Author="Author Thirteen",
        ISBN13=wrapped(isbn13(15)),
        **{"Exclusive Shelf": "to-read", "My Rating": "0", "Read Count": "0"},
    ),
]


def write_inputs(out: Path) -> None:
    """Write ``resolved/``, ``account/``, and ``export.toml`` under ``out``."""
    fx.write_resolved(
        out / "resolved", {name: fx.csv_text(rows) for name, rows in _FILES.items()}
    )
    account = out / "account"
    account.mkdir(parents=True, exist_ok=True)
    (account / LIBRARY_FILE).write_text(
        library_csv(_LIBRARY), encoding="utf-8", newline=""
    )
    shutil.copyfile(_HERE / "export.example.toml", out / "export.toml")
