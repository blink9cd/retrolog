"""Tests for export_goodreads_account's match: a book's fate, its holds, and its key.

Each book is built from ``goodreads_account_fixtures.book_row`` (invented titles,
authors, ISBN-13s and work keys), and each account directly as a
``GoodreadsAccount``, so these tests read no file.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The tool's own src/ first, then resolve's, parse's, and the survey's: the order
# retrolog_export.SOURCE_DIRS puts them on sys.path.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
    str(_TOOL),
]
import export_goodreads_account as gr  # noqa: E402 - after the sys.path inserts above
from export_select import Fate, Hold, Item  # noqa: E402 - after the sys.path inserts
from export_types import ResolvedRow  # noqa: E402 - after the sys.path inserts above
from goodreads_account_fixtures import (  # noqa: E402 - after the sys.path inserts above
    book_row,
    isbn13,
)

# The account match's own pair for the fixture's default book.
_PAIR = ("placeholder book one", "author")
# The config the functions take and ignore: any service table fits.
_CONFIG = object()


def _book(*rows: ResolvedRow, key: str = "book") -> Item:
    """An item of ``rows`` under ``key``; one default book row when none is given."""
    return Item(key, rows or (book_row(),))


def _account(
    *,
    isbns: frozenset[str] = frozenset(),
    titles: frozenset[tuple[str, str]] = frozenset(),
    shelved_isbns: Mapping[str, str] = MappingProxyType({}),
) -> gr.GoodreadsAccount:
    """An account that read ``isbns`` and ``titles`` and shelved ``shelved_isbns``."""
    return gr.GoodreadsAccount(
        read_isbns=isbns,
        read_titles=titles,
        shelved_isbns=shelved_isbns,
        shelved_titles=MappingProxyType({}),
    )


class AccountFateTest(unittest.TestCase):
    def assert_on_service(self, fate: Fate, *, title_alone: bool = False) -> None:
        """``fate`` skips the book, with one on-service entry worded for its match."""
        sent, entries = fate
        self.assertIsNone(sent)
        self.assertEqual([entry.kind for entry in entries], ["on-service"])
        self.assertFalse(entries[0].error)
        self.assertEqual("title alone" in entries[0].detail, title_alone)
        self.assertIn("already read or rated on the account", entries[0].detail)

    def test_an_isbn_on_the_read_list_is_on_service(self) -> None:
        fate = gr.account_fate(_book(), _account(isbns=frozenset({isbn13(1)})), _CONFIG)
        self.assert_on_service(fate)

    def test_any_rows_isbn_counts(self) -> None:
        book = _book(book_row(), book_row(isbn13=isbn13(9), source_line=2))
        fate = gr.account_fate(book, _account(isbns=frozenset({isbn13(9)})), _CONFIG)
        self.assert_on_service(fate)

    def test_a_title_and_author_on_the_read_list_is_on_service(self) -> None:
        book = _book(book_row(isbn13=""))
        self.assert_on_service(
            gr.account_fate(book, _account(titles=frozenset({_PAIR})), _CONFIG)
        )

    def test_a_subtitle_or_series_dropped_still_matches(self) -> None:
        for title in (
            "Placeholder Book One: A Novel",
            "Placeholder Book One (Invented Series, #1)",
        ):
            with self.subTest(title=title):
                book = _book(book_row(title=title, isbn13=""))
                fate = gr.account_fate(
                    book, _account(titles=frozenset({_PAIR})), _CONFIG
                )
                self.assert_on_service(fate)

    def test_the_first_author_counts_with_accents_and_initials_drifted(self) -> None:
        book = _book(book_row(creator="I. Authör & Ann Placeholder", isbn13=""))
        self.assert_on_service(
            gr.account_fate(book, _account(titles=frozenset({_PAIR})), _CONFIG)
        )

    def test_another_author_of_the_title_is_sent(self) -> None:
        book = _book(book_row(creator="Ann Placeholder", isbn13=""))
        sent, entries = gr.account_fate(
            book, _account(titles=frozenset({_PAIR})), _CONFIG
        )
        self.assertEqual((sent, entries), (book, []))

    def test_a_book_with_no_author_matches_by_title_alone(self) -> None:
        book = _book(book_row(creator="", isbn13=""))
        fate = gr.account_fate(book, _account(titles=frozenset({_PAIR})), _CONFIG)
        self.assert_on_service(fate, title_alone=True)

    def test_a_book_with_no_author_and_no_such_title_is_sent(self) -> None:
        book = _book(book_row(creator="", isbn13="", title="Placeholder Book Two"))
        sent, entries = gr.account_fate(
            book, _account(titles=frozenset({_PAIR})), _CONFIG
        )
        self.assertEqual((sent, entries), (book, []))

    def test_an_exact_match_beats_a_title_alone_one(self) -> None:
        book = _book(
            book_row(isbn13=""), book_row(creator="", isbn13="", source_line=2)
        )
        self.assert_on_service(
            gr.account_fate(book, _account(titles=frozenset({_PAIR})), _CONFIG)
        )

    def test_a_shelved_book_is_sent_with_no_entries(self) -> None:
        book = _book()
        account = _account(shelved_isbns={isbn13(1): "custom-shelf"})
        self.assertEqual(gr.account_fate(book, account, _CONFIG), (book, []))

    def test_a_book_the_account_lacks_is_sent_with_no_entries(self) -> None:
        book = _book()
        self.assertEqual(gr.account_fate(book, _account(), _CONFIG), (book, []))


def _grown(books: list[Item], holds: dict[str, Hold]) -> dict[str, Hold]:
    """``holds`` after ``grow_holds`` grew it in place over ``books``."""
    gr.grow_holds(books, holds, _CONFIG)
    return holds


class GrowHoldsTest(unittest.TestCase):
    def test_a_free_book_of_a_held_books_title_and_author_is_held(self) -> None:
        held = _book(book_row(status="ambiguous", isbn13=""), key="held")
        free = _book(book_row(isbn13=isbn13(2)), key="free")
        holds = _grown([held, free], {"held": ("held-ambiguous", None)})
        self.assertEqual(holds["free"], ("held-ambiguous", gr.HELD_WITH_ANOTHER))
        self.assertIn("another book", gr.HELD_WITH_ANOTHER)

    def test_a_subtitle_dropped_still_shares_the_hold(self) -> None:
        held = _book(book_row(title="Placeholder Book One: A Novel"), key="held")
        free = _book(book_row(isbn13=isbn13(2)), key="free")
        holds = _grown([free, held], {"held": ("held-failed", None)})
        self.assertEqual(holds["free"][0], "held-failed")

    def test_a_hold_passes_on_to_a_fixed_point(self) -> None:
        held = _book(book_row(title="Placeholder Book Two"), key="held")
        bridge = _book(
            book_row(title="Placeholder Book Two"),
            book_row(title="Placeholder Book Three", source_line=2),
            key="bridge",
        )
        last = _book(book_row(title="Placeholder Book Three"), key="last")
        holds = _grown([last, bridge, held], {"held": ("held-failed", None)})
        self.assertEqual(set(holds), {"held", "bridge", "last"})
        self.assertEqual(holds["last"], ("held-failed", gr.HELD_WITH_ANOTHER))

    def test_another_author_of_the_title_stays_free(self) -> None:
        held = _book(book_row(), key="held")
        free = _book(book_row(creator="Ann Placeholder"), key="free")
        holds = _grown([held, free], {"held": ("held-ambiguous", None)})
        self.assertNotIn("free", holds)

    def test_a_held_book_with_no_author_is_hidden_by_its_title_alone(self) -> None:
        held = _book(book_row(creator=""), key="held")
        free = _book(book_row(creator="Ann Placeholder"), key="free")
        holds = _grown([held, free], {"held": ("held-ambiguous", None)})
        self.assertEqual(holds["free"][0], "held-ambiguous")

    def test_a_free_book_with_no_author_joins_an_authorless_hold(self) -> None:
        # Sent, it lands on the account under some author, and the held book's
        # title alone would then match it: so it waits with the held one.
        held = _book(
            book_row(status="ambiguous", isbn13="", ol_work="", creator=""),
            key="held",
        )
        free = _book(book_row(isbn13=isbn13(2), creator=""), key="free")
        holds = _grown([held, free], {"held": ("held-ambiguous", None)})
        self.assertEqual(holds["free"], ("held-ambiguous", gr.HELD_WITH_ANOTHER))

    def test_a_free_book_with_no_author_joins_a_hold_with_one(self) -> None:
        held = _book(book_row(), key="held")
        free = _book(book_row(isbn13=isbn13(2), creator=""), key="free")
        holds = _grown([held, free], {"held": ("held-failed", None)})
        self.assertEqual(holds["free"], ("held-failed", gr.HELD_WITH_ANOTHER))

    def test_a_free_book_with_no_author_and_another_title_stays_free(self) -> None:
        held = _book(book_row(creator=""), key="held")
        free = _book(
            book_row(title="Placeholder Book Two", isbn13=isbn13(2), creator=""),
            key="free",
        )
        holds = _grown([held, free], {"held": ("held-ambiguous", None)})
        self.assertNotIn("free", holds)

    def test_a_held_book_keeps_its_own_kind(self) -> None:
        first = _book(book_row(), key="first")
        second = _book(book_row(), key="second")
        holds = _grown(
            [first, second],
            {"first": ("held-ambiguous", None), "second": ("held-failed", None)},
        )
        self.assertEqual(holds["second"], ("held-failed", None))

    def test_no_hold_holds_nothing(self) -> None:
        self.assertEqual(_grown([_book()], {}), {})


class BookKeyTest(unittest.TestCase):
    def test_a_book_is_keyed_by_isbn_then_work_then_title_and_author(self) -> None:
        cases = (
            (book_row(), f"isbn:{isbn13(1)}"),
            (book_row(isbn13=""), "work:/works/OL1W"),
            (book_row(isbn13="", ol_work=""), "title:placeholder book one:author"),
            (
                book_row(isbn13="", ol_work="", creator=""),
                "title:placeholder book one:",
            ),
        )
        for row, key in cases:
            with self.subTest(key=key):
                self.assertEqual(gr.book_key(row), key)

    def test_the_functions_fit_a_service_spec(self) -> None:
        # Typed as a ServiceSpec field takes them, so the checker holds the fit.
        key: Callable[[ResolvedRow], str] = gr.book_key
        fate: Callable[[Item, gr.GoodreadsAccount, int], Fate] = gr.account_fate
        read: Callable[[Path], gr.GoodreadsAccount] = gr.read_account
        grow: Callable[[Sequence[Item], dict[str, Hold], int], None] = gr.grow_holds
        self.assertTrue(all(map(callable, (key, fate, read, grow))))


if __name__ == "__main__":
    unittest.main()
