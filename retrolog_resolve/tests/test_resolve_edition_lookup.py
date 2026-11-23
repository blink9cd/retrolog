"""Tests for a book's ISBN-13 picked from its work's edition list.

An override book with a work key and no ISBN-13 asks Open Library for the work's
editions (``/works/<id>/editions.json``, paged) and keeps the pick's ISBN-13; a book
the search resolved asks the same, and the pick replaces the search's first edition.
A failed, empty or unusable list fails nothing: the row keeps what it had. Every
request goes to ``resolve_fixtures.Answering``, so each test also pins which requests a
run makes. Every key, title and ISBN here is invented.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_books  # noqa: E402 - after the sys.path inserts above
import resolve_editions  # noqa: E402 - after the sys.path inserts above
import resolve_types  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    http_error,
    input_row,
    isbn13_of,
    ol_author_body,
    ol_edition,
    ol_edition_entry,
    ol_editions_page,
    ol_payload,
    ol_work,
    ol_work_body,
)
from resolve_rows_case import (  # noqa: E402 - after the sys.path inserts above
    ISBN,
    STEP1,
    STEP3,
    RowsCase,
    kinds,
    ol_url,
    overrides_of,
)

_WORK = "/works/OL1W"
_AUTHOR = "/authors/OL1A"
_WORK_URL = "https://openlibrary.org/works/OL1W.json"
_AUTHOR_URL = "https://openlibrary.org/authors/OL1A.json"
_TITLE = "Placeholder Book One"
_BY_WORK = f'medium = "book"\ntitle = "{_TITLE}"\nol_work = "{_WORK}"'
_WITH_ISBN = f'{_BY_WORK}\nisbn13 = "{ISBN}"'
_DETAILS: dict[str, object] = {
    _WORK_URL: ol_work_body(_WORK, _AUTHOR, first_publish_date="1901"),
    _AUTHOR_URL: ol_author_body(_AUTHOR, "Invented Author"),
}
_A, _B, _C = (isbn13_of(n) for n in (1, 2, 3))
_CREATESPACE = ("CreateSpace Independent Publishing Platform",)


def _page_url(page: int, work: str = _WORK) -> str:
    """The URL of the ``page``-th page of ``work``'s editions."""
    return resolve_books.editions_url(work, page)


def _one_page(*entries: dict[str, object]) -> dict[str, object]:
    """The work details and a one-page edition list holding ``entries``."""
    return {**_DETAILS, _page_url(0): ol_editions_page(*entries)}


class OverrideEditionTest(RowsCase):
    """An override book with a work key and no ISBN-13 takes the pick's."""

    def test_a_work_key_alone_takes_the_picked_isbn(self) -> None:
        pages = _one_page(
            ol_edition_entry(isbn13=[_A], publish_date="1990"),
            ol_edition_entry(isbn13=[_B], publish_date="2008"),
            ol_edition_entry(isbn13=[_C], publish_date="2015"),
        )
        out, review = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        self.assertEqual(self.status(out[0]), ("override", _B, _WORK, ""))
        self.assertEqual(out[0].cells["creator"], "Invented Author")
        self.assertEqual(self.fetch.calls, [_WORK_URL, _AUTHOR_URL, _page_url(0)])
        self.assertEqual(kinds(review), [("override-applied", _TITLE)])

    def test_an_override_with_an_isbn_keeps_it_and_asks_no_editions(self) -> None:
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_WITH_ISBN), _DETAILS)
        self.assertEqual(self.status(out[0]), ("override", ISBN, _WORK, ""))
        self.assertEqual(self.fetch.calls, [_WORK_URL, _AUTHOR_URL])

    def test_an_isbn_alone_or_unresolvable_asks_nothing(self) -> None:
        for answer in (f'isbn13 = "{ISBN}"', "unresolvable = true"):
            with self.subTest(answer=answer):
                override = f'medium = "book"\ntitle = "{_TITLE}"\n{answer}'
                self.run_all([input_row(_TITLE)], overrides_of(override))
                self.assertEqual(self.fetch.calls, [])

    def test_print_on_demand_is_passed_over_in_a_mixed_list(self) -> None:
        pages = _one_page(
            ol_edition_entry(isbn13=[_A], publish_date="2009", publishers=_CREATESPACE),
            ol_edition_entry(isbn13=[_B], publish_date="1990"),
        )
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        self.assertEqual(out[0].cells["isbn13"], _B)

    def test_a_print_on_demand_only_list_still_gives_one(self) -> None:
        pages = _one_page(
            ol_edition_entry(isbn13=[_A], publish_date="2009", publishers=_CREATESPACE),
            ol_edition_entry(publish_date="2001"),
        )
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        self.assertEqual(out[0].cells["isbn13"], _A)

    def test_a_list_with_no_isbn13_keeps_the_row_as_it_was(self) -> None:
        pages = _one_page(
            ol_edition_entry(publish_date="2001"),
            ol_edition_entry(isbn13=[_A], languages=("fre",)),
        )
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        self.assertEqual(self.status(out[0]), ("override", "", _WORK, ""))

    def test_an_empty_list_keeps_the_row_as_it_was(self) -> None:
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), _one_page())
        self.assertEqual(self.status(out[0]), ("override", "", _WORK, ""))

    def test_a_failed_fetch_fails_nothing(self) -> None:
        for failure in (http_error(500), {"error": "not an editions page"}):
            with self.subTest(failure=failure):
                pages = {**_DETAILS, _page_url(0): failure}
                out, review = self.run_all(
                    [input_row(_TITLE)], overrides_of(_BY_WORK), pages
                )
                self.assertEqual(self.status(out[0]), ("override", "", _WORK, ""))
                self.assertEqual(out[0].cells["creator"], "Invented Author")
                self.assertEqual(kinds(review), [("override-applied", _TITLE)])

    def test_failed_work_details_still_take_the_isbn(self) -> None:
        pages = {
            _WORK_URL: http_error(500),
            _page_url(0): ol_editions_page(ol_edition_entry(isbn13=[_A])),
        }
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        self.assertEqual(self.status(out[0]), ("override", _A, _WORK, ""))
        self.assertEqual(out[0].cells["creator"], "")

    def test_rows_read_in_different_years_ask_once_and_share_the_newest_pick(
        self,
    ) -> None:
        """Regression: each row once took its own year's pick, two ISBN-13s a work.

        Export groups a book's reads by ISBN-13, so the reread went to Goodreads as
        two rows; one pick per work, by the newest read year, keeps it one.
        """
        pages = _one_page(
            ol_edition_entry(isbn13=[_A], publish_date="1990"),
            ol_edition_entry(isbn13=[_B], publish_date="2011"),
        )
        rows = [input_row(_TITLE), input_row(_TITLE, date="2012-01-01", line=2)]
        out, _ = self.run_all(rows, overrides_of(_BY_WORK), pages)
        self.assertEqual([row.cells["isbn13"] for row in out], [_B, _B])
        self.assertEqual(self.fetch.calls, [_WORK_URL, _AUTHOR_URL, _page_url(0)])

    def test_an_undated_row_takes_the_latest(self) -> None:
        pages = _one_page(
            ol_edition_entry(isbn13=[_A], publish_date="2015"),
            ol_edition_entry(isbn13=[_B], publish_date="1990"),
        )
        rows = [input_row(_TITLE, date="")]
        out, _ = self.run_all(rows, overrides_of(_BY_WORK), pages)
        self.assertEqual(out[0].cells["isbn13"], _A)

    def test_the_editions_request_names_only_the_key(self) -> None:
        pages = _one_page(ol_edition_entry(isbn13=[_A]))
        self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        url = self.fetch.calls[-1]
        for leaked in ("Placeholder", "2010", "read.txt", _A):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, url)


class PagingTest(RowsCase):
    """The list follows ``links.next`` page by page, up to ``PAGE_CAP`` pages."""

    def test_the_next_page_is_read_and_picked_from(self) -> None:
        size = resolve_books.EDITIONS_PAGE_SIZE
        pages = {
            **_DETAILS,
            _page_url(0): ol_editions_page(
                ol_edition_entry(isbn13=[_A], publish_date="1990"), next_offset=size
            ),
            _page_url(1): ol_editions_page(
                ol_edition_entry(isbn13=[_B], publish_date="2008")
            ),
        }
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        self.assertEqual(out[0].cells["isbn13"], _B)
        self.assertEqual(self.fetch.calls[-2:], [_page_url(0), _page_url(1)])

    def test_no_more_than_the_cap_is_read(self) -> None:
        cap = resolve_editions.PAGE_CAP
        endless = {
            _page_url(page): ol_editions_page(
                ol_edition_entry(isbn13=[isbn13_of(10 + page)], publish_date="2000"),
                next_offset=page + 1,
            )
            for page in range(cap + 1)
        }
        out, _ = self.run_all(
            [input_row(_TITLE)], overrides_of(_BY_WORK), {**_DETAILS, **endless}
        )
        editions_calls = [url for url in self.fetch.calls if "/editions.json" in url]
        self.assertEqual(editions_calls, [_page_url(page) for page in range(cap)])
        self.assertEqual(out[0].cells["isbn13"], isbn13_of(10))

    def test_a_failed_later_page_fails_the_whole_list(self) -> None:
        pages = {
            **_DETAILS,
            _page_url(0): ol_editions_page(
                ol_edition_entry(isbn13=[_A]), next_offset=1
            ),
            _page_url(1): http_error(500),
        }
        out, _ = self.run_all([input_row(_TITLE)], overrides_of(_BY_WORK), pages)
        self.assertEqual(out[0].cells["isbn13"], "")


class SearchedBookEditionTest(RowsCase):
    """A book the search resolved takes the same pick, over its first edition."""

    def _search(
        self, *isbns: str, step: resolve_books.Step = STEP1
    ) -> dict[str, object]:
        """The search answer at ``step``: the work, its first edition holding ``isbns``."""
        editions = (ol_edition(*isbns),) if isbns else ()
        work = ol_work(key=_WORK, title=_TITLE, editions=editions)
        return {ol_url(_TITLE, step): ol_payload(work)}

    def test_the_pick_replaces_a_print_on_demand_first_edition(self) -> None:
        pages = {
            **self._search(_A),
            _page_url(0): ol_editions_page(
                ol_edition_entry(
                    isbn13=[_A], publish_date="2009", publishers=_CREATESPACE
                ),
                ol_edition_entry(isbn13=[_B], publish_date="1990"),
            ),
        }
        out, review = self.run_all([input_row(_TITLE)], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", _B, _WORK, ""))
        self.assertEqual(self.fetch.calls, [ol_url(_TITLE, STEP1), _page_url(0)])
        self.assertEqual(review, ())

    def test_a_failed_or_empty_list_keeps_the_search_isbn(self) -> None:
        for answer in (http_error(500), ol_editions_page()):
            with self.subTest(answer=answer):
                pages = {**self._search(_A), _page_url(0): answer}
                out, _ = self.run_all([input_row(_TITLE)], pages=pages)
                self.assertEqual(self.status(out[0]), ("resolved", _A, _WORK, ""))

    def test_a_work_only_resolution_can_gain_an_isbn(self) -> None:
        empty = ol_payload()
        pages = {
            ol_url(_TITLE, "edition-by-year"): empty,
            ol_url(_TITLE, "edition-after"): empty,
            **self._search(step=STEP3),
            _page_url(0): ol_editions_page(ol_edition_entry(isbn13=[_C])),
        }
        out, review = self.run_all([input_row(_TITLE)], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", _C, _WORK, ""))
        self.assertEqual(review, ())

    def test_a_work_only_resolution_with_no_isbn_is_still_listed(self) -> None:
        empty = ol_payload()
        pages = {
            ol_url(_TITLE, "edition-by-year"): empty,
            ol_url(_TITLE, "edition-after"): empty,
            **self._search(step=STEP3),
            _page_url(0): ol_editions_page(),
        }
        out, review = self.run_all([input_row(_TITLE)], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", "", _WORK, ""))
        self.assertEqual(kinds(review), [("no-isbn", _TITLE)])

    def test_an_unresolved_title_asks_no_editions(self) -> None:
        other = ol_work(key="/works/OL2W", title="Placeholder Other", editions=())
        pages = {
            ol_url(_TITLE, step): ol_payload(other, other)
            for step in ("edition-by-year", "edition-after", "work-only")
        }
        out, _ = self.run_all([input_row(_TITLE)], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "ambiguous")
        self.assertNotIn("/editions.json", " ".join(self.fetch.calls))


class OneIsbnPerWorkTest(RowsCase):
    """Every row resolved to one work in a run carries one ISBN-13.

    The pick is made once per work, by the newest read year among its rows, so
    export's one-row-per-book grouping by ISBN-13 sees a reread as one book.
    """

    _OTHER = "Placeholder Book Two"

    def _search(self, isbn: str, year: int) -> dict[str, object]:
        """The first search step for a row read in ``year``: the work, one edition."""
        work = ol_work(key=_WORK, title=_TITLE, editions=(ol_edition(isbn),))
        return {ol_url(_TITLE, STEP1, year): ol_payload(work)}

    def _two_years(self, editions: object) -> dict[str, object]:
        """Searches for 2010 (first edition A) and 2012 (C), and ``editions``."""
        return {
            **self._search(_A, 2010),
            **self._search(_C, 2012),
            _page_url(0): editions,
        }

    def _rows(self) -> list[resolve_types.InputRow]:
        """The work read in 2010 and again in 2012."""
        return [input_row(_TITLE), input_row(_TITLE, date="2012-01-01", line=2)]

    def test_a_searched_work_read_in_two_years_takes_the_newest_pick(self) -> None:
        pages = self._two_years(
            ol_editions_page(
                ol_edition_entry(isbn13=[_A], publish_date="1990"),
                ol_edition_entry(isbn13=[_B], publish_date="2011"),
            )
        )
        out, review = self.run_all(self._rows(), pages=pages)
        self.assertEqual(
            [self.status(row) for row in out], [("resolved", _B, _WORK, "")] * 2
        )
        editions_calls = [url for url in self.fetch.calls if "/editions.json" in url]
        self.assertEqual(editions_calls, [_page_url(0)])
        self.assertEqual(review, ())

    def test_a_failed_list_gives_every_row_the_newest_read_s_search_isbn(
        self,
    ) -> None:
        for answer in (http_error(500), ol_editions_page()):
            with self.subTest(answer=answer):
                out, _ = self.run_all(self._rows(), pages=self._two_years(answer))
                self.assertEqual([row.cells["isbn13"] for row in out], [_C, _C])

    def test_an_undated_row_does_not_outrank_a_dated_one(self) -> None:
        pages = _one_page(
            ol_edition_entry(isbn13=[_A], publish_date="1990"),
            ol_edition_entry(isbn13=[_B], publish_date="2015"),
        )
        rows = [input_row(_TITLE, date=""), input_row(_TITLE, line=2)]
        out, _ = self.run_all(rows, overrides_of(_BY_WORK), pages)
        self.assertEqual([row.cells["isbn13"] for row in out], [_A, _A])

    def test_an_override_and_a_search_of_one_work_share_the_pick(self) -> None:
        override = f'medium = "book"\ntitle = "{self._OTHER}"\nol_work = "{_WORK}"'
        editions = ol_editions_page(
            ol_edition_entry(isbn13=[_A], publish_date="1990"),
            ol_edition_entry(isbn13=[_B], publish_date="2011"),
        )
        rows = [input_row(_TITLE), input_row(self._OTHER, date="2012-01-01", line=2)]
        pages = {**_DETAILS, **self._search(_A, 2010), _page_url(0): editions}
        out, _ = self.run_all(rows, overrides_of(override), pages)
        self.assertEqual([row.cells["isbn13"] for row in out], [_B, _B])

    def test_with_no_list_an_override_takes_the_search_s_isbn(self) -> None:
        override = f'medium = "book"\ntitle = "{self._OTHER}"\nol_work = "{_WORK}"'
        rows = [input_row(_TITLE), input_row(self._OTHER, date="2012-01-01", line=2)]
        pages = {**_DETAILS, **self._search(_A, 2010), _page_url(0): http_error(500)}
        out, _ = self.run_all(rows, overrides_of(override), pages)
        self.assertEqual(
            [self.status(row)[:2] for row in out], [("resolved", _A), ("override", _A)]
        )

    def _own_isbn(self, title: str, isbn: str) -> str:
        """An override settling ``title`` by the work key and its own ``isbn``."""
        return (
            f'medium = "book"\ntitle = "{title}"\nol_work = "{_WORK}"\n'
            f'isbn13 = "{isbn}"'
        )

    def test_an_override_s_own_isbn_is_its_work_s_pick(self) -> None:
        """Regression: the search row once took the list's pick beside it, two books."""
        rows = [input_row(_TITLE), input_row(self._OTHER, date="2012-01-01", line=2)]
        pages = {**_DETAILS, **self._search(_A, 2010)}
        out, _ = self.run_all(
            rows, overrides_of(self._own_isbn(self._OTHER, ISBN)), pages
        )
        self.assertEqual([row.cells["isbn13"] for row in out], [ISBN, ISBN])
        self.assertNotIn("/editions.json", " ".join(self.fetch.calls))

    def test_of_two_disagreeing_overrides_the_newest_read_s_wins(self) -> None:
        third = "Placeholder Book Three"
        overrides = overrides_of(
            self._own_isbn(self._OTHER, ISBN), self._own_isbn(third, _C)
        )
        pages = {**_DETAILS, **self._search(_A, 2010)}
        for other_date, third_date, wanted in (
            ("2012-01-01", "2011-01-01", ISBN),
            ("", "2009-01-01", _C),
        ):
            with self.subTest(other=other_date, third=third_date):
                rows = [
                    input_row(_TITLE),
                    input_row(self._OTHER, date=other_date, line=2),
                    input_row(third, date=third_date, line=3),
                ]
                out, _ = self.run_all(rows, overrides, pages)
                self.assertEqual(
                    [row.cells["isbn13"] for row in out], [wanted, ISBN, _C]
                )


if __name__ == "__main__":
    unittest.main()
