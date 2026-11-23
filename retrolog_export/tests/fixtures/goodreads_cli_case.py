"""The shared case of the ``--service goodreads`` command-line tests.

The importing test puts the tool's folder, its src/ folders, resolve's, parse's, and
the survey's src/ folders, and this folder on ``sys.path`` before importing this
module. ``GoodreadsCliCase`` writes a temporary resolve folder of book CSVs, a
Goodreads library export, and an export config, and runs ``main`` with a git
stand-in, so no test reaches a real repository, a real account, or the operator's own
config. Every title, author, ISBN-13, and work key is invented.
"""

from __future__ import annotations

import contextlib
import csv
import datetime
import io
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

import retrolog_export
import survey_guard
from export_cli_case import fake_git
from export_fixtures import csv_text, resolved_row, write_resolved
from goodreads_account_fixtures import (
    isbn13,
    library_book,
    library_csv,
    wrapped,
    write_library,
)
from goodreads_fixtures import goodreads_toml

STEM = "goodreads"


def book_row(title: str, number: int | None, **cells: str) -> dict[str, str]:
    """A resolved book row of ``title``, with ISBN-13 ``isbn13(number)`` unless None.

    ``cells`` replaces any other resolve cell; a book carries no tmdbID.
    """
    defaults = {
        "medium": "book",
        "title": title,
        "creator": "Invented Author",
        "year": "1950",
        "rating_raw": "a",
        "source_file": "10/read.txt",
        "tmdb_id": "",
        "isbn13": "" if number is None else isbn13(number),
        "ol_work": "",
    }
    return resolved_row(**{**defaults, **cells})


def read_book(title: str, number: int | None, **cells: str) -> dict[str, str]:
    """A library export row for ``title`` on the ``read`` shelf, its ISBN wrapped."""
    isbn = "" if number is None else isbn13(number)
    defaults = {
        "Title": title,
        "ISBN13": wrapped(isbn),
        "Exclusive Shelf": "read",
        "Read Count": "1",
    }
    return library_book(**{**defaults, **cells})


# The case's books, file by file: every resolve kind a book can show (see the class).
BOOKS_2010 = [
    book_row("Placeholder Book One", 1, ol_work="/works/OL1W", source_line="1"),
    book_row(
        "Placeholder Book Two",
        2,
        resolve_status="override",
        ol_work="/works/OL2W",
        rating_raw="b",
        source_line="2",
    ),
    book_row(
        "Placeholder Book Three",
        None,
        resolve_status="override",
        ol_work="/works/OL3W",
        creator="Invented Writer",
        source_line="3",
    ),
    book_row(
        "Placeholder Book Four",
        None,
        resolve_status="override",
        creator="",
        source_line="4",
    ),
    book_row("Placeholder Book Five", 5, date="2010-05-06", source_line="5"),
    book_row("Placeholder Book Six", 6, rating_raw="", source_line="6"),
    book_row("Placeholder Book Seven", 7, source_line="7"),
    book_row("Placeholder Book Eight", 8, source_line="8"),
    book_row("Placeholder Book Nine", 9, source_line="9"),
]
BOOKS_2019 = [
    book_row(
        "Placeholder Book Five",
        5,
        date="2019-07-08",
        rating_raw="b",
        rewatch="true",
        source_file="19/read.txt",
        source_line="1",
    ),
]
# The library export: Seven read, Eight rated on a custom shelf (read too), Nine on a
# custom shelf with no rating (shelved, so sent).
LIBRARY = [
    read_book("Placeholder Book Seven", 7),
    read_book(
        "Placeholder Book Eight",
        8,
        **{"Exclusive Shelf": "custom-shelf", "My Rating": "4", "Read Count": "0"},
    ),
    read_book(
        "Placeholder Book Nine",
        9,
        **{"Exclusive Shelf": "custom-shelf", "My Rating": "0", "Read Count": "0"},
    ),
]
# The books the case sends, each once.
SENT = (
    "Placeholder Book One",
    "Placeholder Book Two",
    "Placeholder Book Three",
    "Placeholder Book Four",
    "Placeholder Book Five",
    "Placeholder Book Six",
    "Placeholder Book Nine",
)
EARLIER = "Earlier reads: {years}."
# The rating keys the case's rows carry, as invented as the rest.
RATINGS: Mapping[str, float] = {"a": 1.0, "b": 2.5}
UNDATED_ADDED = datetime.date(1900, 1, 1)
# Nine's custom shelf mapped to the tag its row adds.
SHELF_TAGS = {"custom-shelf": "custom-shelf-read"}


class GoodreadsCliCase(unittest.TestCase):
    """Nine books in two book CSVs, a library export, a ``[goodreads]`` config.

    One resolved, one override with an ISBN-13, one override sent by title and
    author, one override with neither (``title-only``), one read twice, one unrated,
    one read on the account, one rated on a custom shelf, and one on a custom shelf
    unrated.
    """

    def setUp(self) -> None:
        """Write the case's inputs into a fresh temporary folder."""
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.resolved = self.base / "resolved"
        self.write_books({"book-2010.csv": BOOKS_2010, "book-2019.csv": BOOKS_2019})
        self.account_dir = self.base / "account"
        self.account_dir.mkdir()
        self.account = write_library(self.account_dir, library_csv(LIBRARY))
        self.config = self.base / "export.toml"
        self.config.write_bytes(
            goodreads_toml(
                ratings=RATINGS,
                private_note_earlier=EARLIER,
                undated_date_added=UNDATED_ADDED,
                shelf_tags=SHELF_TAGS,
            )
        )
        self.out = self.base / "out"

    def tearDown(self) -> None:
        """Remove the temporary folder and everything the run wrote there."""
        self._tmp.cleanup()

    def write_books(self, files: Mapping[str, Sequence[Mapping[str, str]]]) -> None:
        """Make each ``name -> rows`` one of the resolve folder's book CSVs."""
        write_resolved(
            self.resolved, {name: csv_text(rows) for name, rows in files.items()}
        )

    def write_library(self, books: Sequence[Mapping[str, str]]) -> None:
        """Make ``books`` the library export's rows."""
        write_library(self.account_dir, library_csv(books))

    def main(
        self,
        *extra: str,
        out: Path | None = None,
        account: Path | None = None,
        run_git: survey_guard.GitRunner | None = None,
    ) -> int:
        """Run the CLI for ``goodreads`` on the case's inputs, plus ``extra``."""
        argv = ["--service", STEM, "--resolved", str(self.resolved)]
        argv += ["--account", str(account or self.account)]
        argv += ["--config", str(self.config), "--out", str(out or self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = retrolog_export.main(argv, run_git=run_git or fake_git())
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def import_names(self) -> list[str]:
        """The Goodreads import files ``--out`` holds, sorted."""
        return sorted(p.name for p in self.out.glob(f"{STEM}-*.csv"))

    def import_rows(self) -> list[dict[str, str]]:
        """Every data row of every import file, files in name order, as column -> cell."""
        rows: list[dict[str, str]] = []
        for name in self.import_names():
            text = (self.out / name).read_text(encoding="utf-8")
            rows += list(csv.DictReader(io.StringIO(text)))
        return rows

    def assert_no_absolute_path(self) -> None:
        """No output file, nor stdout or stderr, names the temporary folder."""
        texts = [self.stdout, self.stderr]
        if self.out.is_dir():
            texts += [p.read_text("utf-8") for p in self.out.iterdir() if p.is_file()]
        for text in texts:
            self.assertNotIn(str(self.base), text)
            self.assertNotIn(self._tmp.name, text)
