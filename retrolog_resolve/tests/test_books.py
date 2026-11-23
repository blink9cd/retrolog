"""Tests for resolve_books: Open Library request URLs and response parsing.

The search, then an override book's work and author requests. Every key and name is
invented.
"""

import re
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
import resolve_isbn  # noqa: E402
from resolve_fixtures import (  # noqa: E402
    ol_author_body,
    ol_edition,
    ol_payload,
    ol_work,
    ol_work_body,
)
from resolve_strategies import json_values  # noqa: E402

# The fields every query asks for, restated here so a change to them fails a test.
_FIELDS = (
    "key,title,author_name,first_publish_year,editions,editions.isbn,"
    "editions.language,editions.publish_year"
)
# A valid ISBN-10 and the ISBN-13 it converts to.
_ISBN10 = "0-306-40615-2"
_ISBN13 = "9780306406157"
# Invented Open Library work and author keys, and the URLs naming them.
_WORK = "/works/OL1W"
_OTHER_WORK = "/works/OL2W"
_FIRST = "/authors/OL1A"
_SECOND = "/authors/OL2A"
_WORK_URL = "https://openlibrary.org/works/OL1W.json"
_FIRST_URL = "https://openlibrary.org/authors/OL1A.json"
_TITLE = "Placeholder Book One"
_KEY_SHAPES = re.compile(r"/authors/OL[0-9]+A")
# Every step of the chain, in order.
_STEPS: tuple[resolve_books.Step, ...] = (
    "edition-by-year",
    "edition-after",
    "work-only",
)


@st.composite
def _payloads(draw: st.DrawFn) -> dict[str, object]:
    """A well-formed answer whose fields hold arbitrary values, so the fuzz property's
    inner assertions run on real candidates, not only on payloads it rejects whole."""
    isbn_texts = st.lists(st.one_of(st.text(), st.sampled_from((_ISBN10, _ISBN13))))
    edition = st.builds(
        lambda isbns, language: ol_edition(*isbns, language=language),
        isbn_texts,
        st.lists(st.sampled_from(("eng", "fre", ""))),
    )
    work = st.builds(
        ol_work,
        key=st.text(),
        title=st.text(),
        authors=st.lists(st.text()),
        year=st.none() | st.integers(),
        editions=st.lists(edition, max_size=2),
    )
    return ol_payload(*draw(st.lists(work, max_size=3)))


def _query(url: str) -> dict[str, list[str]]:
    """The decoded query parameters of ``url``, after checking its endpoint."""
    parts = urllib.parse.urlsplit(url)
    endpoint = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    assert endpoint == resolve_books.SEARCH, endpoint
    return urllib.parse.parse_qs(parts.query, keep_blank_values=True)


class SolrPhraseTest(unittest.TestCase):
    def test_plain_title_is_quoted(self) -> None:
        self.assertEqual(resolve_books.solr_phrase("Moby Dick"), '"Moby Dick"')

    def test_quote_and_backslash_are_escaped(self) -> None:
        self.assertEqual(
            resolve_books.solr_phrase('A "Quoted" \\ Title'),
            '"A \\"Quoted\\" \\\\ Title"',
        )


class SearchUrlTest(unittest.TestCase):
    def _assert_common(self, query: dict[str, list[str]]) -> None:
        self.assertEqual(query["fields"], [_FIELDS])
        self.assertEqual(query["limit"], ["10"])

    def test_dated_edition_by_year(self) -> None:
        query = _query(resolve_books.search_url("Moby Dick", 2010, "edition-by-year"))
        self.assertEqual(
            query["q"],
            [
                'title:"Moby Dick" first_publish_year:[* TO 2010] language:eng '
                "isbn:* publish_year:[* TO 2010]"
            ],
        )
        self.assertEqual(query["editions.sort"], ["new"])
        self._assert_common(query)

    def test_dated_edition_after(self) -> None:
        query = _query(resolve_books.search_url("Moby Dick", 2010, "edition-after"))
        self.assertEqual(
            query["q"],
            ['title:"Moby Dick" first_publish_year:[* TO 2010] language:eng isbn:*'],
        )
        self.assertEqual(query["editions.sort"], ["old"])
        self._assert_common(query)

    def test_dated_work_only(self) -> None:
        query = _query(resolve_books.search_url("Moby Dick", 2010, "work-only"))
        self.assertEqual(
            query["q"], ['title:"Moby Dick" first_publish_year:[* TO 2010]']
        )
        self.assertNotIn("editions.sort", query)
        self._assert_common(query)

    def test_undated_leaves_every_year_clause_out(self) -> None:
        expected: dict[resolve_books.Step, tuple[str, list[str] | None]] = {
            "edition-by-year": ('title:"Metropolis" language:eng isbn:*', ["new"]),
            "edition-after": ('title:"Metropolis" language:eng isbn:*', ["old"]),
            "work-only": ('title:"Metropolis"', None),
        }
        for step, (q, sort) in expected.items():
            with self.subTest(step=step):
                query = _query(resolve_books.search_url("Metropolis", None, step))
                self.assertEqual(query["q"], [q])
                self.assertEqual(query.get("editions.sort"), sort)
                self._assert_common(query)

    def test_title_with_quote_and_backslash_round_trips(self) -> None:
        query = _query(resolve_books.search_url('Say "Hi" \\o/', None, "work-only"))
        self.assertEqual(query["q"], ['title:"Say \\"Hi\\" \\\\o/"'])

    def test_url_carries_only_title_and_year(self) -> None:
        url = resolve_books.search_url("Moby Dick", 2010, "edition-by-year")
        self.assertEqual(set(_query(url)), {"q", "fields", "limit", "editions.sort"})


class NextStepTest(unittest.TestCase):
    def test_chain_runs_three_steps_then_stops(self) -> None:
        self.assertEqual(resolve_books.next_step("edition-by-year"), "edition-after")
        self.assertEqual(resolve_books.next_step("edition-after"), "work-only")
        self.assertIsNone(resolve_books.next_step("work-only"))


class ParseSearchTest(unittest.TestCase):
    def test_a_work_becomes_a_candidate(self) -> None:
        payload = ol_payload(
            ol_work(
                key="/works/OL42W",
                title="Moby Dick",
                authors=("Author One", "Author Two"),
                year=1851,
                editions=(ol_edition(_ISBN13),),
            )
        )
        (candidate,) = resolve_books.parse_search(payload, "edition-by-year")
        self.assertEqual(candidate.id, "/works/OL42W")
        self.assertEqual(candidate.title, "Moby Dick")
        self.assertEqual(candidate.original_title, "")
        self.assertEqual(candidate.year, 1851)
        self.assertEqual(candidate.creator, "Author One & Author Two")
        self.assertEqual(candidate.isbn13, _ISBN13)

    def test_isbn10_only_edition_gives_the_converted_isbn13(self) -> None:
        payload = ol_payload(ol_work(editions=(ol_edition(_ISBN10),)))
        (candidate,) = resolve_books.parse_search(payload, "edition-after")
        self.assertEqual(candidate.isbn13, _ISBN13)

    def test_first_accepted_isbn_is_taken(self) -> None:
        payload = ol_payload(ol_work(editions=(ol_edition("12345", _ISBN10),)))
        (candidate,) = resolve_books.parse_search(payload, "edition-by-year")
        self.assertEqual(candidate.isbn13, _ISBN13)

    def test_edition_with_no_valid_isbn_gives_empty(self) -> None:
        payload = ol_payload(ol_work(editions=(ol_edition("12345", "9780306406150"),)))
        (candidate,) = resolve_books.parse_search(payload, "edition-by-year")
        self.assertEqual(candidate.isbn13, "")

    def test_only_the_first_edition_is_read(self) -> None:
        editions = (ol_edition("12345"), ol_edition(_ISBN13))
        payload = ol_payload(ol_work(editions=editions))
        (candidate,) = resolve_books.parse_search(payload, "edition-by-year")
        self.assertEqual(candidate.isbn13, "")

    def test_non_english_edition_gives_no_isbn(self) -> None:
        payload = ol_payload(
            ol_work(editions=(ol_edition(_ISBN13, language=("fre",)),))
        )
        (candidate,) = resolve_books.parse_search(payload, "edition-by-year")
        self.assertEqual(candidate.isbn13, "")

    def test_english_among_several_languages_counts(self) -> None:
        edition = ol_edition(_ISBN13, language=("fre", "eng"))
        payload = ol_payload(ol_work(editions=(edition,)))
        (candidate,) = resolve_books.parse_search(payload, "edition-after")
        self.assertEqual(candidate.isbn13, _ISBN13)

    def test_work_only_step_never_records_an_isbn(self) -> None:
        payload = ol_payload(ol_work(editions=(ol_edition(_ISBN13),)))
        (candidate,) = resolve_books.parse_search(payload, "work-only")
        self.assertEqual(candidate.isbn13, "")

    def test_missing_year_is_none(self) -> None:
        (candidate,) = resolve_books.parse_search(
            ol_payload(ol_work(year=None)), "work-only"
        )
        self.assertIsNone(candidate.year)

    def test_boolean_year_is_none(self) -> None:
        work = ol_work()
        work["first_publish_year"] = True
        (candidate,) = resolve_books.parse_search(ol_payload(work), "work-only")
        self.assertIsNone(candidate.year)

    def test_non_text_authors_are_dropped(self) -> None:
        work = ol_work()
        work["author_name"] = ["Author One", 7, None, "Author Two"]
        (candidate,) = resolve_books.parse_search(ol_payload(work), "work-only")
        self.assertEqual(candidate.creator, "Author One & Author Two")

    def test_authors_not_a_list_give_empty_creator(self) -> None:
        work = ol_work()
        work["author_name"] = "Author One"
        (candidate,) = resolve_books.parse_search(ol_payload(work), "work-only")
        self.assertEqual(candidate.creator, "")

    def test_doc_missing_key_or_title_is_skipped(self) -> None:
        no_key = ol_work()
        del no_key["key"]
        empty_title = ol_work(title="")
        number_title = ol_work()
        number_title["title"] = 5
        kept = ol_work(key="/works/OL9W")
        candidates = resolve_books.parse_search(
            ol_payload(no_key, empty_title, number_title, kept), "work-only"
        )
        self.assertEqual([c.id for c in candidates], ["/works/OL9W"])

    def test_doc_whose_key_is_not_a_work_is_skipped(self) -> None:
        # Open Library's search index lists some orphan editions as works, with an
        # edition key (an M suffix) under /works/; a candidate must carry a work key,
        # the one shape overrides and the review page accept.
        shapes = ("/works/OL12M", "/books/OL12M", "/works/OL12", "OL12W", "/works/OLW")
        docs = [ol_work(key=key) for key in shapes]
        payload = ol_payload(*docs, ol_work(key="/works/OL9W"))
        candidates = resolve_books.parse_search(payload, "work-only")
        self.assertEqual([c.id for c in candidates], ["/works/OL9W"])

    def test_non_object_doc_is_skipped(self) -> None:
        payload = {"docs": ["text", 3, None, ol_work(key="/works/OL9W")]}
        candidates = resolve_books.parse_search(payload, "work-only")
        self.assertEqual([c.id for c in candidates], ["/works/OL9W"])

    def test_unexpected_top_level_shapes_give_nothing(self) -> None:
        shapes: tuple[object, ...] = (
            None,
            [],
            "docs",
            3,
            {},
            {"docs": None},
            {"docs": {}},
        )
        for payload in shapes:
            with self.subTest(payload=payload):
                self.assertEqual(
                    resolve_books.parse_search(payload, "edition-by-year"), ()
                )

    def test_unexpected_edition_shapes_give_no_isbn(self) -> None:
        bad_editions: tuple[object, ...] = (
            None,
            [],
            {"docs": None},
            {"docs": []},
            {"docs": ["x"]},
            {"docs": [{"isbn": "9780306406157", "language": ["eng"]}]},
            {"docs": [{"isbn": [_ISBN13], "language": "eng"}]},
            {"docs": [{"isbn": [7, None], "language": ["eng"]}]},
            {"docs": [{"isbn": [_ISBN13], "language": [1]}]},
        )
        for editions in bad_editions:
            with self.subTest(editions=editions):
                work = ol_work()
                work["editions"] = editions
                (candidate,) = resolve_books.parse_search(
                    ol_payload(work), "edition-by-year"
                )
                self.assertEqual(candidate.isbn13, "")

    def test_missing_editions_gives_no_isbn(self) -> None:
        work = ol_work()
        del work["editions"]
        (candidate,) = resolve_books.parse_search(ol_payload(work), "edition-by-year")
        self.assertEqual(candidate.isbn13, "")

    @given(st.one_of(json_values, _payloads()))
    def test_fuzz_parse_search(self, payload: object) -> None:
        for step in _STEPS:
            for candidate in resolve_books.parse_search(payload, step):
                self.assertTrue(candidate.id and candidate.title)
                self.assertTrue(
                    candidate.isbn13 == ""
                    or resolve_isbn.isbn13_or_none(candidate.isbn13) == candidate.isbn13
                )


class UrlTest(unittest.TestCase):
    """The two request URLs carry the key alone, and refuse anything but a key."""

    def test_the_work_url_is_the_work_s_json(self) -> None:
        self.assertEqual(resolve_books.work_url(_WORK), _WORK_URL)

    def test_the_author_url_is_the_author_s_json(self) -> None:
        self.assertEqual(resolve_books.author_url(_FIRST), _FIRST_URL)

    def test_a_malformed_work_key_is_refused(self) -> None:
        for key in (
            _TITLE,
            "OL1W",
            "/works/OL1M",
            "/works/OLW",
            "/works/OL1W/../authors",
            "/works/OL1W\n",
            "/works/OL١W",
            _FIRST,
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve_books.work_url(key)

    def test_a_malformed_author_key_is_refused(self) -> None:
        for key in (
            "Invented Author",
            "OL1A",
            "/authors/OL1A?x=1",
            "/authors/OL1A\n",
            _WORK,
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve_books.author_url(key)


class ParseWorkTest(unittest.TestCase):
    """``parse_work`` reads a work body's author keys, in order, and its year."""

    def test_the_authors_in_order_and_the_year(self) -> None:
        body = ol_work_body(_WORK, _SECOND, _FIRST, first_publish_date="1901")
        work = resolve_books.parse_work(body, _WORK)
        assert work is not None
        self.assertEqual(work.author_keys, (_SECOND, _FIRST))
        self.assertEqual(work.year, 1901)

    def test_an_author_listed_twice_is_kept_once(self) -> None:
        work = resolve_books.parse_work(ol_work_body(_WORK, _FIRST, _FIRST), _WORK)
        assert work is not None
        self.assertEqual(work.author_keys, (_FIRST,))

    def test_the_year_is_a_four_digit_run_in_the_date(self) -> None:
        dates = {
            "1901": 1901,
            "April 10, 1901": 1901,
            "c. 1901?": 1901,
            "190": None,
            "19011": None,
            "nineteenth century": None,
            "": None,
        }
        for date, year in dates.items():
            with self.subTest(date=date):
                body = ol_work_body(_WORK, _FIRST, first_publish_date=date)
                work = resolve_books.parse_work(body, _WORK)
                assert work is not None
                self.assertEqual(work.year, year)

    def test_a_missing_or_odd_date_is_no_year(self) -> None:
        for date in (None, 1901, ["1901"]):
            with self.subTest(date=date):
                body = ol_work_body(_WORK, _FIRST)
                if date is not None:
                    body["first_publish_date"] = date
                work = resolve_books.parse_work(body, _WORK)
                assert work is not None
                self.assertIsNone(work.year)

    def test_a_malformed_author_entry_is_skipped(self) -> None:
        body = ol_work_body(_WORK, _FIRST)
        entries = body["authors"]
        assert isinstance(entries, list)
        entries[:0] = [
            "not an entry",
            {"type": {"key": "/type/author_role"}},
            {"author": "/authors/OL3A"},
            {"author": {"key": 3}},
            {"author": {"key": "Invented Author"}},
            {"author": {"key": "/authors/OL3A/../x"}},
        ]
        work = resolve_books.parse_work(body, _WORK)
        assert work is not None
        self.assertEqual(work.author_keys, (_FIRST,))

    def test_an_unusable_body_is_none(self) -> None:
        no_authors = ol_work_body(_WORK)
        del no_authors["authors"]
        bodies: tuple[object, ...] = (
            None,
            ["not", "an", "object"],
            no_authors,
            ol_work_body(_WORK),
            {**ol_work_body(_WORK, _FIRST), "authors": "not a list"},
            ol_work_body(_OTHER_WORK, _FIRST),
            {"key": _WORK, "type": {"key": "/type/redirect"}, "location": _OTHER_WORK},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(resolve_books.parse_work(body, _WORK))

    @given(
        st.one_of(
            json_values,
            st.builds(
                ol_work_body,
                st.just(_WORK),
                st.text(),
                first_publish_date=st.one_of(st.none(), st.text()),
            ),
            st.builds(
                lambda author: {"key": _WORK, "authors": [{"author": author}]},
                json_values,
            ),
        )
    )
    def test_fuzz_parse_work(self, payload: object) -> None:
        work = resolve_books.parse_work(payload, _WORK)
        if work is not None:
            self.assertTrue(work.author_keys)
            for key in work.author_keys:
                self.assertTrue(_KEY_SHAPES.fullmatch(key))
            self.assertTrue(work.year is None or 1000 <= work.year <= 9999)


class ParseAuthorTest(unittest.TestCase):
    """``parse_author`` reads an author body's name, else its personal name."""

    def test_the_name_stripped(self) -> None:
        body = ol_author_body(_FIRST, "  Invented Author ")
        self.assertEqual(resolve_books.parse_author(body), "Invented Author")

    def test_the_personal_name_when_the_name_is_unusable(self) -> None:
        for name in (None, "", "   ", 3):
            with self.subTest(name=name):
                body = {"name": name, "personal_name": " Invented Person "}
                self.assertEqual(resolve_books.parse_author(body), "Invented Person")

    def test_no_usable_name_is_none(self) -> None:
        bodies: tuple[object, ...] = (
            None,
            ["Invented Author"],
            {},
            {"name": " ", "personal_name": ["Invented Person"]},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(resolve_books.parse_author(body))

    @given(
        st.one_of(json_values, st.builds(ol_author_body, st.just(_FIRST), st.text()))
    )
    def test_fuzz_parse_author(self, payload: object) -> None:
        name = resolve_books.parse_author(payload)
        if name is not None:
            self.assertEqual(name, name.strip())
            self.assertTrue(name)


if __name__ == "__main__":
    unittest.main()
