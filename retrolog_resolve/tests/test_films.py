"""Tests for resolve_films: TMDb URL building and response parsing, with no network."""

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_films  # noqa: E402
from resolve_fixtures import (  # noqa: E402
    tmdb_credits_payload,
    tmdb_crew,
    tmdb_result,
    tmdb_search_payload,
)
from resolve_strategies import json_values  # noqa: E402
from resolve_types import Candidate  # noqa: E402

# Search results that are well formed, so the fuzz property's assertions on the
# candidates run on real answers, not only on rejected noise.
_RESULTS = st.builds(
    tmdb_result,
    st.integers(min_value=-5, max_value=10**9),
    st.text(max_size=10),
    original_title=st.text(max_size=10),
    release_date=st.one_of(st.just(""), st.text(max_size=10), st.dates().map(str)),
    vote_count=st.one_of(st.integers(-3, 10**6), st.none(), st.booleans()),
)
# Details bodies whose id is often the one asked for, so a match is reached often.
_DETAILS = st.builds(
    tmdb_result,
    st.one_of(st.integers(-2, 3), st.text(max_size=2), st.booleans()),
    st.one_of(st.text(max_size=10), st.none(), st.integers()),
    original_title=st.one_of(st.text(max_size=10), st.integers()),
    release_date=st.one_of(st.just(""), st.text(max_size=10), st.dates().map(str)),
)
_SEARCHES = st.lists(_RESULTS, max_size=4).map(lambda r: tmdb_search_payload(*r))
_CREWS = st.lists(
    st.builds(
        tmdb_crew,
        st.sampled_from(("", " ", "Ann Placeholder", "Zed Placeholder", "Cy")),
        st.sampled_from(("Director", "Writer")),
    ),
    max_size=5,
).map(lambda c: tmdb_credits_payload(*c))


class SearchUrlTest(unittest.TestCase):
    def test_search_url_carries_exactly_the_four_parameters(self) -> None:
        url = resolve_films.search_url("Metropolis & Sons?")
        parts = urlsplit(url)
        self.assertEqual(
            f"{parts.scheme}://{parts.netloc}{parts.path}",
            "https://api.themoviedb.org/3/search/movie",
        )
        self.assertEqual(
            parse_qsl(parts.query),
            [
                ("query", "Metropolis & Sons?"),
                ("include_adult", "false"),
                ("language", "en-US"),
                ("page", "1"),
            ],
        )

    def test_search_url_has_no_year_and_no_fragment(self) -> None:
        parts = urlsplit(resolve_films.search_url("Metropolis"))
        self.assertNotIn("year", parts.query)
        self.assertEqual(parts.fragment, "")


class CreditsUrlTest(unittest.TestCase):
    def test_credits_url_names_the_movie(self) -> None:
        self.assertEqual(
            resolve_films.credits_url("424242"),
            "https://api.themoviedb.org/3/movie/424242/credits",
        )

    def test_credits_url_refuses_anything_but_ascii_digits(self) -> None:
        arabic_one = chr(0x0661)
        for text in ("", "12/../34", "12a", " 12", "-12", arabic_one):
            with self.subTest(text=text), self.assertRaises(ValueError):
                resolve_films.credits_url(text)


class DetailsUrlTest(unittest.TestCase):
    def test_details_url_names_the_movie_alone(self) -> None:
        self.assertEqual(
            resolve_films.details_url("424242"),
            "https://api.themoviedb.org/3/movie/424242",
        )

    def test_details_url_refuses_anything_but_ascii_digits(self) -> None:
        arabic_one = chr(0x0661)
        for text in (
            *("", "12/../34", "12a", " 12", "-12", "12?x=1", arabic_one),
            *("0", "0603", "12345678901"),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                resolve_films.details_url(text)


class ParseDetailsTest(unittest.TestCase):
    """A /movie/{id} body read as the candidate an override row takes its titles from."""

    def test_a_details_body_reads_as_its_candidate(self) -> None:
        body = tmdb_result(
            603,
            "Placeholder Night",
            original_title="Nuit Placeholder",
            release_date="1999-03-31",
        )
        self.assertEqual(
            resolve_films.parse_details(body, "603"),
            Candidate(
                id="603",
                title="Placeholder Night",
                original_title="Nuit Placeholder",
                year=1999,
                creator="",
                isbn13="",
            ),
        )

    def test_leading_zeros_in_the_asked_id_still_match(self) -> None:
        candidate = resolve_films.parse_details(tmdb_result(9, "Nine"), "009")
        self.assertIsNotNone(candidate)

    def test_a_missing_original_title_or_date_reads_as_empty(self) -> None:
        body: dict[str, object] = {"id": 7, "title": "Placeholder Seven"}
        candidate = resolve_films.parse_details(body, "7")
        assert candidate is not None
        self.assertEqual((candidate.original_title, candidate.year), ("", None))

    def test_an_unusable_body_reads_as_none(self) -> None:
        bodies: tuple[object, ...] = (
            None,
            [],
            "details",
            {},
            {"id": 7},
            {"id": 7, "title": None},
            {"id": "7", "title": "Seven"},
            {"id": True, "title": "Seven"},
            {"id": 8, "title": "Eight, not the id asked for"},
            {"success": False, "status_code": 34},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(resolve_films.parse_details(body, "7"))

    @given(st.one_of(json_values, _DETAILS), st.integers(0, 3).map(str))
    def test_fuzz_parse_details(self, payload: object, tmdb_id: str) -> None:
        candidate = resolve_films.parse_details(payload, tmdb_id)
        if candidate is not None:
            self.assertEqual(int(candidate.id), int(tmdb_id))
            self.assertIsInstance(candidate.title, str)
            self.assertIsInstance(candidate.original_title, str)
            self.assertTrue(candidate.year is None or isinstance(candidate.year, int))


class ParseSearchTest(unittest.TestCase):
    @staticmethod
    def _one(
        tmdb_id: object = 7, original_title: object = None, release_date: object = ""
    ) -> tuple[Candidate, ...]:
        """parse_search over a body of one "Metropolis" result."""
        result = tmdb_result(
            tmdb_id,
            "Metropolis",
            original_title=original_title,
            release_date=release_date,
        )
        return resolve_films.parse_search(tmdb_search_payload(result))

    def test_a_result_becomes_a_candidate(self) -> None:
        payload = tmdb_search_payload(
            tmdb_result(
                19, "Metropolis", original_title="Metropolis", release_date="1927-01-10"
            ),
            tmdb_result(77, "Placeholder Film Two", original_title="Platzhalter Zwei"),
        )
        self.assertEqual(
            resolve_films.parse_search(payload),
            (
                Candidate("19", "Metropolis", "Metropolis", 1927, "", ""),
                Candidate(
                    "77", "Placeholder Film Two", "Platzhalter Zwei", None, "", ""
                ),
            ),
        )

    def test_release_date_year_only_from_a_full_date(self) -> None:
        for date, year in (("", None), ("1927", None), ("1927-01-10", 1927)):
            with self.subTest(date=date):
                (candidate,) = self._one(release_date=date)
                self.assertEqual(candidate.year, year)

    def test_malformed_release_dates_give_no_year(self) -> None:
        for date in (None, 1927, "1927-1-10", "1927-01-10T00:00", "abcd-01-10"):
            with self.subTest(date=date):
                (candidate,) = self._one(release_date=date)
                self.assertIsNone(candidate.year)

    def test_a_bool_or_string_id_is_skipped(self) -> None:
        # TMDb ids are integers, and True is an int in Python.
        for tmdb_id in (True, False, "19", 19.0, None, -3):
            with self.subTest(tmdb_id=tmdb_id):
                self.assertEqual(self._one(tmdb_id=tmdb_id), ())

    def test_an_id_no_url_may_carry_is_skipped(self) -> None:
        # credits_url refuses these, so a candidate holding one would end the run.
        for tmdb_id in (0, 10**10, 10**20):
            with self.subTest(tmdb_id=tmdb_id):
                self.assertEqual(self._one(tmdb_id=tmdb_id), ())

    def test_vote_count_zero_and_positive_are_read(self) -> None:
        for count in (0, 1, 31337):
            with self.subTest(count=count):
                payload = tmdb_search_payload(
                    tmdb_result(7, "Placeholder", vote_count=count)
                )
                (candidate,) = resolve_films.parse_search(payload)
                self.assertEqual(candidate.votes, count)

    def test_a_malformed_vote_count_is_unknown(self) -> None:
        # True is an int in Python, so a bool must be refused by name.
        for count in (None, True, False, -1, 12.0, "12", [12]):
            with self.subTest(count=count):
                payload = tmdb_search_payload(
                    tmdb_result(7, "Placeholder", vote_count=count)
                )
                (candidate,) = resolve_films.parse_search(payload)
                self.assertIsNone(candidate.votes)

    def test_a_missing_vote_count_is_unknown(self) -> None:
        (candidate,) = self._one()
        self.assertIsNone(candidate.votes)

    def test_a_result_with_no_text_title_is_skipped(self) -> None:
        payload = tmdb_search_payload(tmdb_result(5, None), tmdb_result(6, 42))
        self.assertEqual(resolve_films.parse_search(payload), ())

    def test_a_missing_original_title_is_empty(self) -> None:
        (candidate,) = self._one(original_title=["not text"])
        self.assertEqual(candidate.original_title, "")

    def test_non_object_results_are_skipped(self) -> None:
        payload = tmdb_search_payload("x", 3, None, [], tmdb_result(8, "Metropolis"))
        self.assertEqual(
            resolve_films.parse_search(payload),
            (Candidate("8", "Metropolis", "Metropolis", None, "", ""),),
        )

    def test_a_body_without_a_results_list_is_empty(self) -> None:
        payloads: tuple[object, ...] = (
            None,
            [],
            "results",
            {},
            {"results": None},
            {"results": {}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(resolve_films.parse_search(payload), ())

    @given(st.one_of(json_values, _SEARCHES))
    def test_fuzz_parse_search(self, payload: object) -> None:
        candidates = resolve_films.parse_search(payload)
        self.assertIsInstance(candidates, tuple)
        for candidate in candidates:
            self.assertRegex(candidate.id, r"\A[1-9][0-9]{0,9}\Z")
            self.assertIsInstance(candidate.title, str)
            self.assertIsInstance(candidate.original_title, str)
            self.assertTrue(candidate.year is None or isinstance(candidate.year, int))
            self.assertEqual((candidate.creator, candidate.isbn13), ("", ""))
            votes = candidate.votes
            self.assertTrue(
                votes is None or (type(votes) is int and votes >= 0),  # never a bool
            )


class ParseDirectorsTest(unittest.TestCase):
    def test_directors_sorted_deduplicated_and_joined(self) -> None:
        payload = tmdb_credits_payload(
            tmdb_crew("Zed Placeholder"),
            tmdb_crew("Ann Placeholder"),
            tmdb_crew("Zed Placeholder"),
            tmdb_crew("Bea Placeholder", job="Writer"),
        )
        self.assertEqual(
            resolve_films.parse_directors(payload), "Ann Placeholder & Zed Placeholder"
        )

    def test_one_director(self) -> None:
        payload = tmdb_credits_payload(tmdb_crew("Ann Placeholder"))
        self.assertEqual(resolve_films.parse_directors(payload), "Ann Placeholder")

    def test_no_director_is_empty(self) -> None:
        payload = tmdb_credits_payload(tmdb_crew("Bea Placeholder", job="Writer"))
        self.assertEqual(resolve_films.parse_directors(payload), "")

    def test_malformed_crew_entries_are_skipped(self) -> None:
        payload = tmdb_credits_payload(
            "x",
            None,
            tmdb_crew(None),
            tmdb_crew(""),
            tmdb_crew("   "),
            tmdb_crew("Cy Placeholder", job=None),
            tmdb_crew("Ann Placeholder"),
        )
        self.assertEqual(resolve_films.parse_directors(payload), "Ann Placeholder")

    def test_a_body_without_a_crew_list_is_empty(self) -> None:
        payloads: tuple[object, ...] = (
            None,
            [],
            "crew",
            {},
            {"crew": None},
            {"crew": {}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(resolve_films.parse_directors(payload), "")

    @given(st.one_of(json_values, _CREWS))
    def test_fuzz_parse_directors(self, payload: object) -> None:
        self.assertIsInstance(resolve_films.parse_directors(payload), str)

    @given(_CREWS)
    def test_every_director_appears_once_in_order(
        self, payload: dict[str, object]
    ) -> None:
        crew = payload["crew"]
        assert isinstance(crew, list)
        expected = sorted(
            {m["name"] for m in crew if m["job"] == "Director" and m["name"].strip()}
        )
        self.assertEqual(resolve_films.parse_directors(payload), " & ".join(expected))


if __name__ == "__main__":
    unittest.main()
