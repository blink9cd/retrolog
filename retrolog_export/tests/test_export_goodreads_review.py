"""Tests for export_goodreads' book review kinds: what a sent book's review list says."""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_goodreads  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
from export_select import Item  # noqa: E402 - after the sys.path inserts above
from export_types import (  # noqa: E402 - after the sys.path inserts above
    ResolvedRow,
    ReviewEntry,
)
from goodreads_fixtures import book_row  # noqa: E402 - after the sys.path inserts above

# An override row whose work lookup gave no author, as a failed lookup leaves it.
_AUTHORLESS_OVERRIDE = {"status": "override", "creator": ""}
# The fixture book's title and its first row, as a review entry names them.
_TITLE = "Placeholder Book One"
_ROW_ONE = ("10/read.txt:1",)
# Each kind's exact detail, so a reworded one shows up here.
_BY_TITLE = (
    "sent by Title and Author with no ISBN-13; check after the import that "
    "Goodreads matched the right edition"
)
_TITLE_ONLY = (
    "sent by Title alone, with no ISBN-13 or author; check after the import that "
    "Goodreads found it"
)
_AUTHOR_MISSING = (
    "an override whose Open Library work gave no author; run resolve again to fill "
    "it in"
)
_ONE_EARLIER = "merged with 1 earlier read: sent once, as the newest read"
_TWO_EARLIER = "merged with 2 earlier reads: sent once, as the newest read"
_UNRATED = "sent with no rating; rate it by hand"
_UNDATED = "sent with no read date"


def _book(*rows: ResolvedRow) -> Item:
    """One book of ``rows``, keyed as an invented key: the kinds never read the key."""
    return Item("book:invented", rows)


def _entries(book: Item) -> list[ReviewEntry]:
    """The entries ``book_entries`` gives ``book``, in its order."""
    return export_goodreads.book_entries(book)


def _expected(
    kind: str, detail: str, rows: tuple[str, ...] = _ROW_ONE, title: str = _TITLE
) -> ReviewEntry:
    """The whole entry of ``kind`` a book is expected to earn: never an error."""
    return ReviewEntry(kind=kind, title=title, rows=rows, detail=detail, error=False)


class RegisteredTest(unittest.TestCase):
    def test_every_book_kind_is_a_non_error_review_kind(self) -> None:
        for kind in export_select.BOOK_KINDS:
            self.assertIn(kind, export_select.REVIEW_KINDS)
            self.assertNotIn(kind, export_select.ERROR_KINDS)

    def test_the_book_only_kinds(self) -> None:
        self.assertEqual(
            export_select.BOOK_KINDS,
            frozenset(
                {"remove-first", "title-only", "author-missing", "reread-merged"}
            ),
        )

    def test_the_film_only_kinds(self) -> None:
        self.assertEqual(
            export_select.FILM_KINDS,
            frozenset(
                {
                    "same-date",
                    "logged-no-stars",
                    "watched-only",
                    "override-unchecked",
                }
            ),
        )

    def test_a_film_run_carries_every_kind_but_the_book_only_ones(self) -> None:
        self.assertEqual(
            export_select.review_kinds("film"),
            (
                "held-ambiguous",
                "held-failed",
                "held-split",
                "held-no-title",
                "omitted",
                "on-service",
                "same-date",
                "logged-no-stars",
                "watched-only",
                "override-unchecked",
                "by-title",
                "unrated",
                "undated",
                "formula-cell",
            ),
        )

    def test_a_book_run_carries_every_kind_but_the_film_only_ones(self) -> None:
        self.assertEqual(
            export_select.review_kinds("book"),
            (
                "held-ambiguous",
                "held-failed",
                "held-split",
                "held-no-title",
                "omitted",
                "on-service",
                "remove-first",
                "by-title",
                "title-only",
                "author-missing",
                "reread-merged",
                "unrated",
                "undated",
                "formula-cell",
            ),
        )


class IdentityKindsTest(unittest.TestCase):
    def test_a_resolved_book_earns_no_entry(self) -> None:
        self.assertEqual(_entries(_book(book_row())), [])

    def test_no_isbn_but_an_author_is_by_title(self) -> None:
        book = _book(book_row(isbn13=""))
        self.assertEqual(_entries(book), [_expected("by-title", _BY_TITLE)])

    def test_no_isbn_and_no_author_is_title_only(self) -> None:
        book = _book(book_row(isbn13="", creator=""))
        self.assertEqual(_entries(book), [_expected("title-only", _TITLE_ONLY)])

    def test_no_book_detail_names_an_import_preview(self) -> None:
        # Goodreads shows no preview before an import; Letterboxd does.
        book = _book(
            book_row(isbn13="", creator="", rating_raw="", date=None),
            book_row(isbn13="", creator="", rating_raw="", date=None, source_line=2),
        )
        for entry in [*_entries(book), *_entries(_book(book_row(isbn13="")))]:
            self.assertNotIn("preview", entry.detail)

    def test_an_authorless_override_with_a_work_and_an_isbn_is_author_missing(
        self,
    ) -> None:
        book = _book(book_row(**_AUTHORLESS_OVERRIDE))
        self.assertEqual(_entries(book), [_expected("author-missing", _AUTHOR_MISSING)])

    def test_title_only_wins_over_author_missing(self) -> None:
        book = _book(book_row(isbn13="", **_AUTHORLESS_OVERRIDE))
        self.assertEqual(_entries(book), [_expected("title-only", _TITLE_ONLY)])

    def test_an_authorless_override_with_no_work_is_not_author_missing(self) -> None:
        book = _book(book_row(ol_work="", **_AUTHORLESS_OVERRIDE))
        self.assertEqual(_entries(book), [])

    def test_an_authorless_resolved_book_is_not_author_missing(self) -> None:
        self.assertEqual(_entries(_book(book_row(creator=""))), [])

    def test_an_override_with_an_author_is_not_author_missing(self) -> None:
        self.assertEqual(_entries(_book(book_row(status="override"))), [])


class RereadTest(unittest.TestCase):
    def test_one_earlier_read_is_named_in_the_detail(self) -> None:
        book = _book(
            book_row(),
            book_row(date=datetime.date(2014, 5, 6), source_line=2),
        )
        rows = ("10/read.txt:1", "10/read.txt:2")
        self.assertEqual(
            _entries(book), [_expected("reread-merged", _ONE_EARLIER, rows)]
        )

    def test_two_earlier_reads_are_counted(self) -> None:
        book = _book(
            book_row(),
            book_row(date=datetime.date(2012, 1, 1), source_line=2),
            book_row(date=datetime.date(2014, 5, 6), source_line=3),
        )
        rows = ("10/read.txt:1", "10/read.txt:2", "10/read.txt:3")
        self.assertEqual(
            _entries(book), [_expected("reread-merged", _TWO_EARLIER, rows)]
        )

    def test_an_undated_earlier_read_still_merges(self) -> None:
        # merge_rereads gives such a read no year, but the book is still sent once.
        book = _book(book_row(), book_row(date=None, source_line=2))
        rows = ("10/read.txt:1", "10/read.txt:2")
        self.assertEqual(
            _entries(book), [_expected("reread-merged", _ONE_EARLIER, rows)]
        )

    def test_the_entry_names_the_sent_reads_title(self) -> None:
        # One ISBN-13 keys both reads, but the lists spelled the title two ways: the
        # entry names the newest read's, the one Goodreads is sent, not the first's.
        book = _book(
            book_row(),
            book_row(
                title="Placeholder Book One: An Invented Subtitle",
                date=datetime.date(2014, 5, 6),
                source_line=2,
            ),
        )
        rows = ("10/read.txt:1", "10/read.txt:2")
        sent = "Placeholder Book One: An Invented Subtitle"
        self.assertEqual(
            _entries(book), [_expected("reread-merged", _ONE_EARLIER, rows, sent)]
        )


class SentRowTest(unittest.TestCase):
    def test_an_unrated_sent_read_is_unrated(self) -> None:
        book = _book(book_row(rating_raw=""))
        self.assertEqual(_entries(book), [_expected("unrated", _UNRATED)])

    def test_an_unrated_earlier_read_is_not_sent_so_not_unrated(self) -> None:
        book = _book(
            book_row(date=datetime.date(2014, 5, 6)),
            book_row(rating_raw="", source_line=2),
        )
        rows = ("10/read.txt:1", "10/read.txt:2")
        self.assertEqual(
            _entries(book), [_expected("reread-merged", _ONE_EARLIER, rows)]
        )

    def test_an_undated_book_is_undated(self) -> None:
        book = _book(book_row(date=None))
        self.assertEqual(_entries(book), [_expected("undated", _UNDATED)])

    def test_the_kinds_come_in_review_kinds_order(self) -> None:
        book = _book(
            book_row(isbn13="", rating_raw="", date=None),
            book_row(isbn13="", rating_raw="", date=None, source_line=2),
        )
        rows = ("10/read.txt:1", "10/read.txt:2")
        self.assertEqual(
            _entries(book),
            [
                _expected("by-title", _BY_TITLE, rows),
                _expected("reread-merged", _ONE_EARLIER, rows),
                _expected("unrated", _UNRATED, rows),
                _expected("undated", _UNDATED, rows),
            ],
        )
        kinds = [entry.kind for entry in _entries(book)]
        order = [export_select.REVIEW_KINDS.index(kind) for kind in kinds]
        self.assertEqual(order, sorted(order))


class EntryShapeTest(unittest.TestCase):
    def test_every_entry_is_a_non_error_naming_the_title_only_in_its_field(
        self,
    ) -> None:
        book = _book(
            book_row(isbn13="", creator="", rating_raw="", date=None),
            book_row(isbn13="", creator="", rating_raw="", date=None, source_line=2),
        )
        entries = export_goodreads.book_entries(book)
        self.assertEqual(len(entries), 4)
        for entry in entries:
            self.assertFalse(entry.error)
            self.assertEqual(entry.title, "Placeholder Book One")
            self.assertNotIn("Placeholder", entry.detail)
            self.assertNotIn("/", entry.detail)

    def test_a_book_of_no_rows_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            export_goodreads.book_entries(_book())


if __name__ == "__main__":
    unittest.main()
