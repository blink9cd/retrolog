"""Tests for a book split between two items: one book is never sent as two rows.

Goodreads keeps one record per book and a re-import overwrites it, so two reads of
one book keyed apart (an ISBN-13 against an override with none, two ISBN-13s of one
work) would send two rows of which the second clobbers the first. Such a book is
``held-split``, as a film split by a tmdbID is. Each row is built from
``goodreads_fixtures.book_row`` (invented titles, authors, ISBN-13s and work keys)
and the account is an empty ``GoodreadsAccount``, so these tests read no file.
"""

from __future__ import annotations

import dataclasses
import datetime
import sys
import unittest
from pathlib import Path
from types import MappingProxyType

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_goodreads_account  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
from export_goodreads_account import (  # noqa: E402 - after the sys.path inserts above
    GoodreadsAccount,
)
from export_services import GOODREADS  # noqa: E402 - after the sys.path inserts above
from export_types import ResolvedRow  # noqa: E402 - after the sys.path inserts above
from goodreads_fixtures import (  # noqa: E402 - after the sys.path inserts above
    GOODREADS as CONFIG,
)
from goodreads_fixtures import (  # noqa: E402 - after the sys.path inserts above
    book_row,
    isbn13,
)

# An account that has read nothing, so every free book is sent.
_EMPTY = GoodreadsAccount(
    read_isbns=frozenset(),
    read_titles=frozenset(),
    shelved_isbns=MappingProxyType({}),
    shelved_titles=MappingProxyType({}),
)
# The book split's own detail, so a reworded one shows up here.
_SPLIT_DETAIL = (
    "another read of this title and author, or of its Open Library work, is keyed "
    "by a different ISBN-13 or work; if they are one book, give every read the "
    "same answer in resolve, with one override for each list title they carry, "
    "then rerun; if they are different books, name one in [[goodreads.omit]]"
)


def _second_read(**changes: object) -> ResolvedRow:
    """A later read of the fixture book, on line 2, with ``changes`` applied."""
    later = {"source_line": 2, "date": datetime.date(2012, 5, 6)}
    return book_row(**(later | changes))


def _select(
    *rows: ResolvedRow, account: GoodreadsAccount = _EMPTY
) -> export_select.Selection:
    """``select`` on the Goodreads spec, the fixture config, and ``account``."""
    return export_select.select(
        rows, spec=GOODREADS, config=CONFIG, account=account, cutoff=None
    )


def _account(
    read: frozenset[str], shelved: frozenset[str] = frozenset()
) -> GoodreadsAccount:
    """An account that has read the ``read`` ISBN-13s and shelved ``shelved``."""
    return GoodreadsAccount(
        read_isbns=read,
        read_titles=frozenset(),
        shelved_isbns=MappingProxyType(dict.fromkeys(shelved, "to-read")),
        shelved_titles=MappingProxyType({}),
    )


def _volume(line: int, subtitle: str, stem: str) -> ResolvedRow:
    """The fixture book under ``subtitle``, read on ``line``, its own ISBN-13 and work."""
    title = f"Placeholder Book One: {subtitle}"
    return book_row(
        source_line=line,
        date=datetime.date(2010 + line, 3, 4),
        title=title,
        title_key=title.casefold().replace(":", ""),
        isbn13=isbn13(stem),
        ol_work=f"/works/OL{line}0W",
    )


def _kinds(chosen: export_select.Selection) -> list[tuple[str, tuple[str, ...]]]:
    """Each review entry's kind and rows, in the selection's order."""
    return [(entry.kind, entry.rows) for entry in chosen.review]


class BookSplitTest(unittest.TestCase):
    def assert_held_split(self, chosen: export_select.Selection) -> None:
        """Nothing sent, and both reads held ``held-split`` as errors, each alone."""
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            _kinds(chosen),
            [("held-split", ("10/read.txt:1",)), ("held-split", ("10/read.txt:2",))],
        )
        for entry in chosen.review:
            self.assertTrue(entry.error)
            self.assertEqual(entry.detail, _SPLIT_DETAIL)

    def test_an_isbn_read_and_an_empty_override_of_its_title_are_held(self) -> None:
        empty = _second_read(status="override", isbn13="", ol_work="")
        self.assert_held_split(_select(book_row(), empty))

    def test_an_isbn_read_and_an_isbn_only_override_are_held(self) -> None:
        override = _second_read(
            status="override", isbn13=isbn13("979000000002"), ol_work=""
        )
        self.assert_held_split(_select(book_row(), override))

    def test_two_isbns_of_one_title_and_author_are_held(self) -> None:
        other = _second_read(isbn13=isbn13("979000000003"), ol_work="/works/OL2W")
        self.assert_held_split(_select(book_row(), other))

    def test_two_isbns_of_one_work_under_other_titles_are_held(self) -> None:
        other = _second_read(
            isbn13=isbn13("979000000004"),
            title="Placeholder Book Uno",
            title_key="placeholder book uno",
            creator="Invented Translator",
        )
        self.assert_held_split(_select(book_row(), other))

    def test_a_read_with_no_author_splits_its_title_by_any_author(self) -> None:
        authorless = _second_read(isbn13="", ol_work="", creator="")
        self.assert_held_split(_select(book_row(), authorless))

    def test_reads_sharing_one_key_still_merge(self) -> None:
        chosen = _select(book_row(), _second_read())
        [sent] = chosen.films
        self.assertEqual(len(sent.rows), 1)
        self.assertEqual(len(sent.merged), 1)
        self.assertIn("reread-merged", [kind for kind, _ in _kinds(chosen)])
        self.assertNotIn("held-split", [kind for kind, _ in _kinds(chosen)])

    def test_other_books_are_sent_apart(self) -> None:
        other = _second_read(
            isbn13=isbn13("979000000005"),
            ol_work="/works/OL5W",
            title="Placeholder Book Five",
            title_key="placeholder book five",
        )
        chosen = _select(book_row(), other)
        self.assertEqual(len(chosen.films), 2)
        self.assertNotIn("held-split", [kind for kind, _ in _kinds(chosen)])

    def test_another_author_of_the_title_is_sent_apart(self) -> None:
        other = _second_read(
            isbn13=isbn13("979000000006"),
            ol_work="/works/OL6W",
            creator="Different Writer",
        )
        self.assertEqual(len(_select(book_row(), other).films), 2)

    def test_book_splits_names_every_item_of_a_split(self) -> None:
        first = export_select.Item("isbn:1", (book_row(),))
        second = export_select.Item("title:x", (_second_read(isbn13="", ol_work=""),))
        lone = export_select.Item(
            "isbn:9",
            (
                _second_read(
                    title="Placeholder Book Nine",
                    title_key="placeholder book nine",
                    ol_work="/works/OL9W",
                ),
            ),
        )
        splits = export_goodreads_account.book_splits(
            [first, second, lone], _EMPTY, CONFIG
        )
        self.assertEqual(
            dict(splits), {"isbn:1": _SPLIT_DETAIL, "title:x": _SPLIT_DETAIL}
        )

    def test_two_volumes_of_one_title_are_sent_apart(self) -> None:
        one = _volume(1, "Volume One", "979000000011")
        two = _volume(2, "Volume Two", "979000000012")
        chosen = _select(one, two)
        self.assertEqual(len(chosen.films), 2)
        self.assertNotIn("held-split", [kind for kind, _ in _kinds(chosen)])

    def test_a_subtitled_override_with_no_work_is_held_with_its_title(self) -> None:
        # The search found the title bare, with its work; an override names the
        # same book's subtitled title and an ISBN-13 but no work. Their full titles
        # differ, yet only one carries a work, so nothing tells them apart.
        title = "Placeholder Book One: A Novel"
        subtitled = {"title": title, "title_key": title.casefold().replace(":", "")}
        override = _second_read(
            status="override", isbn13=isbn13("979000000014"), ol_work="", **subtitled
        )
        self.assert_held_split(_select(book_row(), override))
        no_ids = _second_read(status="override", isbn13="", ol_work="", **subtitled)
        self.assert_held_split(_select(book_row(), no_ids))

    def test_one_full_title_under_two_works_is_held(self) -> None:
        # Open Library often keeps two work records for one book.
        other = _second_read(isbn13=isbn13("979000000015"), ol_work="/works/OL15W")
        self.assert_held_split(_select(book_row(), other))

    def test_one_full_title_with_no_works_is_held(self) -> None:
        other = _second_read(isbn13=isbn13("979000000016"), ol_work="")
        self.assert_held_split(_select(book_row(ol_work=""), other))

    def test_two_subtitles_of_one_title_with_one_work_are_held(self) -> None:
        novel = _volume(1, "A Novel", "979000000017")
        story = dataclasses.replace(_volume(2, "A Story", "979000000018"), ol_work="")
        self.assert_held_split(_select(novel, story))

    def test_a_titled_read_with_no_ids_holds_both_volumes(self) -> None:
        # Distinct works under distinct full titles part only from each other: a
        # read of the bare title with no work could be either volume.
        one = _volume(1, "Volume One", "979000000011")
        two = _volume(2, "Volume Two", "979000000012")
        bare = _second_read(source_line=3, isbn13="", ol_work="")
        chosen = _select(one, two, bare)
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            {kind for kind, _ in _kinds(chosen)}, {"held-split"}, _kinds(chosen)
        )

    def test_one_full_title_under_two_isbns_is_still_held(self) -> None:
        one = _volume(1, "Volume One", "979000000011")
        again = dataclasses.replace(
            _volume(2, "Volume One", "979000000012"), ol_work=""
        )
        self.assert_held_split(_select(one, again))

    def test_a_series_parenthetical_alone_does_not_tell_books_apart(self) -> None:
        # Goodreads appends "(Series, #n)" to a title, so title_keys drops it; a
        # split keys by the same series-free title and holds the two reads.
        series = _second_read(
            isbn13=isbn13("979000000013"),
            ol_work="",
            title="Placeholder Book One (Invented Series, #1)",
            title_key="placeholder book one invented series 1",
        )
        self.assert_held_split(_select(book_row(), series))

    def test_a_split_the_account_read_whole_is_on_service(self) -> None:
        other = _second_read(isbn13=isbn13("979000000003"), ol_work="/works/OL2W")
        account = _account(frozenset({book_row().isbn13, other.isbn13}))
        chosen = _select(book_row(), other, account=account)
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            _kinds(chosen),
            [("on-service", ("10/read.txt:1",)), ("on-service", ("10/read.txt:2",))],
        )
        self.assertFalse(any(entry.error for entry in chosen.review))

    def test_a_split_with_one_read_unknown_to_the_account_is_held(self) -> None:
        other = _second_read(isbn13=isbn13("979000000003"), ol_work="/works/OL2W")
        account = _account(frozenset({book_row().isbn13}))
        self.assert_held_split(_select(book_row(), other, account=account))

    def test_a_split_with_one_read_shelved_on_the_account_is_held(self) -> None:
        other = _second_read(isbn13=isbn13("979000000003"), ol_work="/works/OL2W")
        account = _account(
            frozenset({book_row().isbn13}), shelved=frozenset({other.isbn13})
        )
        self.assert_held_split(_select(book_row(), other, account=account))

    def test_held_split_is_a_book_kind_too(self) -> None:
        self.assertIn("held-split", export_select.review_kinds("book"))
        self.assertIn("held-split", export_select.review_kinds("film"))


if __name__ == "__main__":
    unittest.main()
