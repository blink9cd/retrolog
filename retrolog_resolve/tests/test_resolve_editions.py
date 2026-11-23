"""Tests for resolve_editions and the editions URL: one work's editions, and the pick.

A /works/{id}/editions.json page is parsed to the few facts the pick reads (an
edition's verified ISBN-13, its year, whether it is English, whether a print-on-demand
imprint published it), and ``pick_isbn13`` keeps the read year's edition, passing
over print-on-demand editions unless they are the only ones with an ISBN-13. Every key,
ISBN and edition here is invented.
"""

import sys
import unittest
import urllib.parse
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TESTS))
import _retrolog_resolve_fuzz_profile  # noqa: E402, F401 - loads the deterministic profile before any @given
import resolve_books  # noqa: E402
import resolve_editions  # noqa: E402
import resolve_isbn  # noqa: E402
from resolve_editions import Edition  # noqa: E402
from resolve_fixtures import (  # noqa: E402
    isbn13_of,
    ol_edition_entry,
    ol_editions_page,
)
from resolve_strategies import json_values  # noqa: E402

_WORK = "/works/OL1W"
# A valid ISBN-10 and the ISBN-13 it converts to.
_ISBN10 = "0-306-40615-2"
_CONVERTED = "9780306406157"
_A, _B, _C, _D = (isbn13_of(n) for n in (1, 2, 3, 4))
_CREATESPACE = "CreateSpace Independent Publishing Platform"


def _edition(
    isbn13: str,
    year: int | None,
    *,
    english: bool = True,
    print_on_demand: bool = False,
) -> Edition:
    """An already-parsed edition."""
    return Edition(
        isbn13=isbn13, year=year, english=english, print_on_demand=print_on_demand
    )


def _only(entry: dict[str, object]) -> Edition:
    """The one edition a page holding ``entry`` parses to."""
    page = resolve_editions.parse_editions_page(ol_editions_page(entry))
    assert page is not None
    (edition,) = page.editions
    return edition


class EditionsUrlTest(unittest.TestCase):
    """The editions URL carries the work key alone, and pages by offset."""

    def test_the_first_page(self) -> None:
        url = resolve_books.editions_url(_WORK, 0)
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(
            (parts.scheme, parts.netloc, parts.path),
            ("https", "openlibrary.org", "/works/OL1W/editions.json"),
        )
        query = urllib.parse.parse_qs(parts.query)
        self.assertEqual(
            query,
            {"limit": [str(resolve_books.EDITIONS_PAGE_SIZE)], "offset": ["0"]},
        )

    def test_a_later_page_offsets_by_whole_pages(self) -> None:
        url = resolve_books.editions_url(_WORK, 2)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query["offset"], [str(2 * resolve_books.EDITIONS_PAGE_SIZE)])

    def test_another_key_shape_is_refused(self) -> None:
        for key in ("/books/OL1M", "/works/OL1W?x=1", "/works/OL1W/../x", ""):
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve_books.editions_url(key, 0)

    def test_the_page_cap_covers_nine_hundred_editions(self) -> None:
        pages, size = resolve_editions.PAGE_CAP, resolve_books.EDITIONS_PAGE_SIZE
        self.assertGreaterEqual(pages * size, 900)


class PrintOnDemandTest(unittest.TestCase):
    """The print-on-demand imprints, matched as words, case ignored."""

    def test_each_spelling_seen_is_one(self) -> None:
        for name in (
            "CreateSpace",
            _CREATESPACE,
            "Createspace Independent Pub",
            "[CreateSpace]",
            "Brand: CreateSpace Independent Publishing Platform",
            "Independently Published",
            "independently published ",
            "\u200e Independently published",
            "Lulu.com",
            "Lulu Press, Inc.",
            "LULU",
        ):
            with self.subTest(name=name):
                self.assertTrue(resolve_editions.is_print_on_demand([name]))

    def test_other_publishers_are_not(self) -> None:
        for names in (
            ["Placeholder Press"],
            ["Tallulah House"],
            ["Independent Press"],
            [],
        ):
            with self.subTest(names=names):
                self.assertFalse(resolve_editions.is_print_on_demand(names))

    def test_any_one_of_several_publishers_counts(self) -> None:
        names = ["Placeholder Press", "Lulu.com"]
        self.assertTrue(resolve_editions.is_print_on_demand(names))

    def test_a_value_of_another_shape_is_not(self) -> None:
        for value in (None, "Lulu.com", 3, [3, None], {"name": "Lulu"}):
            with self.subTest(value=value):
                self.assertFalse(resolve_editions.is_print_on_demand(value))


class ParseEditionsPageTest(unittest.TestCase):
    """A page's entries parse to editions; a page of another shape is None."""

    def test_an_edition_with_an_isbn13(self) -> None:
        edition = _only(ol_edition_entry(isbn13=[_A], publish_date="March 3, 2005"))
        self.assertEqual(edition, _edition(_A, 2005))

    def test_an_isbn10_is_converted_when_no_isbn13_verifies(self) -> None:
        for isbn13 in ((), ("9780306406158",)):
            with self.subTest(isbn13=isbn13):
                entry = ol_edition_entry(isbn13=isbn13, isbn10=[_ISBN10])
                self.assertEqual(_only(entry).isbn13, _CONVERTED)

    def test_the_first_verified_isbn13_is_kept(self) -> None:
        entry = ol_edition_entry(isbn13=["not an isbn", _B, _A], isbn10=[_ISBN10])
        self.assertEqual(_only(entry).isbn13, _B)

    def test_no_verified_isbn_gives_none(self) -> None:
        entry = ol_edition_entry(isbn13=["9780306406158"], isbn10=["0306406153"])
        self.assertEqual(_only(entry).isbn13, "")
        self.assertEqual(_only(ol_edition_entry()).isbn13, "")

    def test_english_is_read_from_the_language_keys(self) -> None:
        cases = {("eng",): True, ("fre", "eng"): True, ("fre",): False, (): False}
        for languages, english in cases.items():
            with self.subTest(languages=languages):
                entry = ol_edition_entry(isbn13=[_A], languages=languages)
                self.assertIs(_only(entry).english, english)

    def test_languages_of_another_shape_are_not_english(self) -> None:
        for languages in (None, "eng", ["/languages/eng"], [{"key": 3}]):
            with self.subTest(languages=languages):
                entry = {**ol_edition_entry(isbn13=[_A]), "languages": languages}
                self.assertFalse(_only(entry).english)

    def test_a_date_with_no_year_gives_none(self) -> None:
        for date in (None, "", "n.d.", "19th century"):
            with self.subTest(date=date):
                entry = ol_edition_entry(isbn13=[_A], publish_date=date)
                self.assertIsNone(_only(entry).year)

    def test_a_print_on_demand_publisher_is_flagged(self) -> None:
        entry = ol_edition_entry(isbn13=[_A], publishers=[_CREATESPACE])
        self.assertTrue(_only(entry).print_on_demand)

    def test_isbn_fields_of_another_shape_give_none(self) -> None:
        entry = {**ol_edition_entry(), "isbn_13": _A, "isbn_10": [3, None]}
        self.assertEqual(_only(entry).isbn13, "")

    def test_more_follows_links_next(self) -> None:
        last = resolve_editions.parse_editions_page(ol_editions_page())
        more = resolve_editions.parse_editions_page(ol_editions_page(next_offset=200))
        assert last is not None and more is not None
        self.assertEqual((last.more, more.more), (False, True))

    def test_links_of_another_shape_mean_no_more(self) -> None:
        for links in (None, "next", {"next": 3}, {"next": ""}):
            with self.subTest(links=links):
                page = resolve_editions.parse_editions_page(
                    {"entries": [], "links": links}
                )
                assert page is not None
                self.assertFalse(page.more)

    def test_an_entry_of_another_shape_is_skipped(self) -> None:
        payload = {"entries": [3, None, ol_edition_entry(isbn13=[_A])]}
        page = resolve_editions.parse_editions_page(payload)
        assert page is not None
        self.assertEqual(page.editions, (_edition(_A, 2005),))

    def test_a_page_of_another_shape_is_none(self) -> None:
        payloads: tuple[object, ...] = (
            None,
            [],
            "entries",
            {},
            {"entries": "x"},
            {"docs": []},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertIsNone(resolve_editions.parse_editions_page(payload))

    @given(
        st.one_of(
            json_values,
            st.builds(lambda entries: {"entries": entries}, st.lists(json_values)),
            st.builds(
                lambda isbns, date: {
                    "entries": [{"isbn_13": isbns, "publish_date": date}]
                },
                json_values,
                json_values,
            ),
        )
    )
    def test_fuzz_parse_editions_page(self, payload: object) -> None:
        page = resolve_editions.parse_editions_page(payload)
        if page is None:
            return
        for edition in page.editions:
            if edition.isbn13:
                self.assertEqual(
                    resolve_isbn.isbn13_or_none(edition.isbn13), edition.isbn13
                )
            self.assertTrue(edition.year is None or 1000 <= edition.year <= 9999)


class PickIsbn13Test(unittest.TestCase):
    """The read year's edition, print-on-demand ones passed over unless they are all."""

    def test_the_latest_in_or_before_the_read_year(self) -> None:
        editions = [_edition(_A, 1990), _edition(_B, 2010), _edition(_C, 2005)]
        self.assertEqual(resolve_editions.pick_isbn13(editions, 2010), _B)

    def test_else_the_earliest_after_it(self) -> None:
        editions = [_edition(_A, 2015), _edition(_B, 2012), _edition(_C, 2020)]
        self.assertEqual(resolve_editions.pick_isbn13(editions, 2010), _B)

    def test_an_undated_row_takes_the_latest(self) -> None:
        editions = [_edition(_A, 1990), _edition(_B, 2020), _edition(_C, 2005)]
        self.assertEqual(resolve_editions.pick_isbn13(editions, None), _B)

    def test_an_undated_edition_only_when_no_edition_is_dated(self) -> None:
        dated = [_edition(_A, None), _edition(_B, 2015)]
        self.assertEqual(resolve_editions.pick_isbn13(dated, 2010), _B)
        undated = [_edition(_A, None), _edition(_B, None)]
        self.assertEqual(resolve_editions.pick_isbn13(undated, 2010), _A)

    def test_a_tie_keeps_the_list_order(self) -> None:
        editions = [_edition(_A, 2005), _edition(_B, 2005)]
        self.assertEqual(resolve_editions.pick_isbn13(editions, 2010), _A)
        later = [_edition(_A, 2015), _edition(_B, 2015)]
        self.assertEqual(resolve_editions.pick_isbn13(later, 2010), _A)

    def test_an_edition_not_english_or_with_no_isbn_is_passed_over(self) -> None:
        editions = [
            _edition(_A, 2009, english=False),
            _edition("", 2008),
            _edition(_C, 1990),
        ]
        self.assertEqual(resolve_editions.pick_isbn13(editions, 2010), _C)

    def test_print_on_demand_is_passed_over_in_a_mixed_list(self) -> None:
        editions = [
            _edition(_A, 2009, print_on_demand=True),
            _edition(_B, 1990),
            _edition(_C, 2020, print_on_demand=True),
        ]
        self.assertEqual(resolve_editions.pick_isbn13(editions, 2010), _B)

    def test_a_print_on_demand_only_list_still_gives_one(self) -> None:
        editions = [
            _edition(_A, 2009, print_on_demand=True),
            _edition(_B, 1990, print_on_demand=True),
            _edition(_D, 2009, english=False),
        ]
        self.assertEqual(resolve_editions.pick_isbn13(editions, 2010), _A)

    def test_a_list_with_no_usable_edition_gives_nothing(self) -> None:
        self.assertEqual(resolve_editions.pick_isbn13([], 2010), "")
        editions = [_edition("", 2005), _edition(_A, 2005, english=False)]
        self.assertEqual(resolve_editions.pick_isbn13(editions, 2010), "")


if __name__ == "__main__":
    unittest.main()
