"""Tests for a book sent although the account has it shelved: it is removed first.

Covers the config's ``[goodreads.shelf_tags]`` table, the account reader keeping each
shelved book's shelf, ``shelved_on``'s match, the ``remove-first`` review entry, and
the tag a mapped shelf adds to the row's Bookshelves. Every title, author, shelf and
ISBN-13 is invented.
"""

from __future__ import annotations

import string
import sys
import unittest
from pathlib import Path
from types import MappingProxyType

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TESTS))
import _retrolog_export_fuzz_profile  # noqa: E402, F401 - loads the deterministic profile before any @given
import export_config  # noqa: E402 - after the sys.path inserts above
import export_goodreads  # noqa: E402 - after the sys.path inserts above
import export_goodreads_account as gr  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
from export_select import Item  # noqa: E402 - after the sys.path inserts above
from export_types import (  # noqa: E402 - after the sys.path inserts above
    GoodreadsConfig,
    ReviewEntry,
)
from goodreads_account_fixtures import (  # noqa: E402 - after the sys.path inserts above
    book_row,
    isbn13,
    library_book,
    library_csv,
)
from goodreads_fixtures import (  # noqa: E402 - after the sys.path inserts above
    goodreads_config,
    goodreads_toml,
)

# The account match's own pair for the fixture's default book.
_PAIR = ("placeholder book one", "author")
_SHELF = "donation-shelf"
_TAG = "donation-shelf-read"
_REMOVE = (
    "on the account's donation-shelf shelf; remove it from Goodreads before importing"
)
_REMOVE_UNNAMED = (
    "on a shelf of the account other than read; remove it from Goodreads before "
    "importing"
)
_TAGGED = goodreads_config(shelf_tags=MappingProxyType({_SHELF: _TAG}))
_BOOKSHELVES = export_goodreads.COLUMNS.index("Bookshelves")


def _account(*books: dict[str, str]) -> gr.GoodreadsAccount:
    """The account a library export of ``books`` parses as."""
    return gr.parse_library_csv(library_csv(books))


def _shelved(shelf: str = _SHELF, **cells: str) -> dict[str, str]:
    """A library row on ``shelf``, unrated and never read: shelved."""
    return library_book(**{"Exclusive Shelf": shelf, **cells})


def _refusal(data: bytes) -> str:
    """The ConfigError message ``load_config`` gives for ``data``."""
    try:
        export_config.load_config(data)
    except export_config.ConfigError as error:
        return str(error)
    raise AssertionError("load_config accepted the config")


def _tags(data: bytes) -> dict[str, str]:
    """The ``shelf_tags`` ``load_config`` reads from ``data``."""
    config = export_config.load_config(data).goodreads
    assert config is not None
    return dict(config.shelf_tags)


class ShelfTagsConfigTest(unittest.TestCase):
    def test_the_table_maps_an_account_shelf_to_its_tag(self) -> None:
        tags = _tags(goodreads_toml(shelf_tags={_SHELF: _TAG, "to-read": "x_1"}))
        self.assertEqual(tags, {_SHELF: _TAG, "to-read": "x_1"})

    def test_the_table_is_optional(self) -> None:
        self.assertEqual(_tags(goodreads_toml()), {})

    def test_a_tag_needs_no_retrolog_prefix(self) -> None:
        self.assertEqual(_tags(goodreads_toml(shelf_tags={"a": "b"})), {"a": "b"})

    def test_not_a_table_of_strings_is_refused(self) -> None:
        for data in (
            goodreads_toml(shelf_tags={_SHELF: 1}),
            goodreads_toml().replace(
                b"[goodreads]\n", b'[goodreads]\nshelf_tags = "donation-shelf"\n'
            ),
        ):
            with self.subTest(data=data):
                self.assertEqual(
                    _refusal(data),
                    "goodreads.shelf_tags: must be a table of strings",
                )

    def test_an_account_shelf_breaking_the_rule_is_refused_by_number(self) -> None:
        for bad in ("Donation-Shelf", "a,b", "a b", "x" * 51):
            with self.subTest(bad=bad):
                message = _refusal(goodreads_toml(shelf_tags={"ok": "ok", bad: _TAG}))
                self.assertEqual(
                    message,
                    "goodreads.shelf_tags[2]: the account shelf: a shelf must be 1 to "
                    "50 characters, each a lowercase letter, a digit, a hyphen, or an "
                    "underscore",
                )

    def test_a_tag_breaking_the_rule_is_refused_by_number(self) -> None:
        for bad in ("Read", "a,b", "", "x" * 51):
            with self.subTest(bad=bad):
                message = _refusal(goodreads_toml(shelf_tags={_SHELF: bad}))
                self.assertEqual(
                    message,
                    "goodreads.shelf_tags[1]: the tag: a shelf must be 1 to 50 "
                    "characters, each a lowercase letter, a digit, a hyphen, or an "
                    "underscore",
                )

    @given(st.text(alphabet=string.printable + "éA,", max_size=60))
    def test_fuzz_shelf_tags(self, tag: str) -> None:
        """Any tag text loads only when it obeys the shelf rule, else refuses by key."""
        allowed = set(string.ascii_lowercase + string.digits + "-_")
        valid = 1 <= len(tag) <= 50 and set(tag) <= allowed
        try:
            tags = _tags(goodreads_toml(shelf_tags={_SHELF: tag}))
        except export_config.ConfigError as error:
            self.assertFalse(valid)
            self.assertTrue(str(error).startswith("goodreads.shelf_tags[1]: "))
        else:
            self.assertTrue(valid)
            self.assertEqual(tags, {_SHELF: tag})


class AccountShelfTest(unittest.TestCase):
    def test_a_shelved_book_keeps_its_shelf(self) -> None:
        account = _account(_shelved())
        self.assertEqual(dict(account.shelved_isbns), {isbn13(1): _SHELF})
        self.assertEqual(account.shelved_titles[_PAIR], _SHELF)

    def test_the_shelf_is_stripped_and_folded(self) -> None:
        account = _account(_shelved(" Donation-Shelf "))
        self.assertEqual(dict(account.shelved_isbns), {isbn13(1): _SHELF})

    def test_a_shelf_breaking_the_rule_is_kept_unnamed(self) -> None:
        for cell in ("", "a shelf", "`x`", "x" * 51):
            with self.subTest(cell=cell):
                account = _account(_shelved(cell))
                self.assertEqual(dict(account.shelved_isbns), {isbn13(1): ""})

    def test_a_title_on_two_shelves_keeps_the_first(self) -> None:
        account = _account(_shelved(), _shelved("to-read", ISBN13='=""'))
        self.assertEqual(account.shelved_titles[_PAIR], _SHELF)


class ShelvedOnTest(unittest.TestCase):
    def test_a_book_matched_by_isbn_names_its_shelf(self) -> None:
        book = Item("book", (book_row(),))
        self.assertEqual(gr.shelved_on(book, _account(_shelved())), _SHELF)

    def test_a_book_matched_by_title_and_author_names_its_shelf(self) -> None:
        book = Item("book", (book_row(isbn13=""),))
        account = _account(_shelved(ISBN13='=""'))
        self.assertEqual(gr.shelved_on(book, account), _SHELF)

    def test_an_authorless_book_matched_by_title_alone_names_its_shelf(self) -> None:
        book = Item("book", (book_row(isbn13="", creator=""),))
        account = _account(_shelved(ISBN13='=""'))
        self.assertEqual(gr.shelved_on(book, account), _SHELF)

    def test_the_isbn_match_wins_over_the_title_match(self) -> None:
        book = Item("book", (book_row(),))
        account = _account(
            _shelved("to-read", ISBN13='=""'),
            _shelved(Title="Another Title", Author="Other Writer"),
        )
        self.assertEqual(gr.shelved_on(book, account), _SHELF)

    def test_a_book_not_shelved_is_none(self) -> None:
        book = Item("book", (book_row(title="Placeholder Book Two", isbn13=""),))
        account = _account(_shelved(), _shelved(ISBN13='=""', Title="Unrelated"))
        self.assertIsNone(gr.shelved_on(book, account))
        authorless = Item("book", (book_row(isbn13="", creator="", title="Other"),))
        self.assertIsNone(gr.shelved_on(authorless, account))


class BookFateTest(unittest.TestCase):
    def test_a_shelved_book_is_sent_with_its_shelf_and_a_remove_first_entry(
        self,
    ) -> None:
        book = Item("book", (book_row(),))
        sent, entries = export_goodreads.book_fate(book, _account(_shelved()), _TAGGED)
        assert sent is not None
        self.assertEqual(sent.account_shelf, _SHELF)
        self.assertEqual(
            entries[0],
            ReviewEntry(
                kind="remove-first",
                title="Placeholder Book One",
                rows=("10/read.txt:1",),
                detail=_REMOVE,
                error=False,
            ),
        )

    def test_a_book_the_account_lacks_has_no_shelf_and_no_entry(self) -> None:
        book = Item("book", (book_row(title="Placeholder Book Two", isbn13=""),))
        sent, entries = export_goodreads.book_fate(book, _account(_shelved()), _TAGGED)
        assert sent is not None
        self.assertIsNone(sent.account_shelf)
        self.assertNotIn("remove-first", [entry.kind for entry in entries])

    def test_a_read_book_stays_on_service(self) -> None:
        book = Item("book", (book_row(),))
        account = _account(_shelved(**{"Read Count": "1"}))
        sent, entries = export_goodreads.book_fate(book, account, _TAGGED)
        self.assertIsNone(sent)
        self.assertEqual([entry.kind for entry in entries], ["on-service"])


class RemoveFirstEntryTest(unittest.TestCase):
    def test_remove_first_is_a_non_error_book_kind_before_by_title(self) -> None:
        self.assertIn("remove-first", export_select.BOOK_KINDS)
        self.assertNotIn("remove-first", export_select.ERROR_KINDS)
        kinds = export_select.review_kinds("book")
        self.assertEqual(kinds.index("remove-first"), kinds.index("on-service") + 1)
        self.assertNotIn("remove-first", export_select.review_kinds("film"))

    def test_an_unnamed_shelf_gets_the_generic_detail(self) -> None:
        book = Item("book", (book_row(),), account_shelf="")
        entries = export_goodreads.book_entries(book)
        self.assertEqual(entries[0].kind, "remove-first")
        self.assertEqual(entries[0].detail, _REMOVE_UNNAMED)

    def test_remove_first_leads_the_books_entries(self) -> None:
        book = Item("book", (book_row(isbn13=""),), account_shelf=_SHELF)
        kinds = [entry.kind for entry in export_goodreads.book_entries(book)]
        self.assertEqual(kinds, ["remove-first", "by-title"])


class TagShelfTest(unittest.TestCase):
    def bookshelves(self, shelf: str | None, config: GoodreadsConfig = _TAGGED) -> str:
        """The Bookshelves cell ``item_cells`` writes for a book on ``shelf``."""
        book = Item("book", (book_row(),), account_shelf=shelf)
        (cells,) = export_goodreads.item_cells(book, config)
        return cells[_BOOKSHELVES]

    def test_a_mapped_shelf_adds_its_tag(self) -> None:
        self.assertEqual(self.bookshelves(_SHELF), f"retrolog, {_TAG}")

    def test_an_unmapped_shelf_adds_no_tag(self) -> None:
        self.assertEqual(self.bookshelves("to-read"), "retrolog")
        self.assertEqual(self.bookshelves(""), "retrolog")

    def test_a_book_not_shelved_adds_no_tag(self) -> None:
        self.assertEqual(self.bookshelves(None), "retrolog")

    def test_a_tag_already_among_the_shelves_is_written_once(self) -> None:
        config = goodreads_config(shelf_tags=MappingProxyType({_SHELF: "retrolog"}))
        self.assertEqual(self.bookshelves(_SHELF, config), "retrolog")


if __name__ == "__main__":
    unittest.main()
