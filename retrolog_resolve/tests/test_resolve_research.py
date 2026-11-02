"""Tests for the re-search: a film with no exact title searched once more, unarticled.

The pure half (``resolve_match.research_query`` and ``research_verdict``) is tested on
invented candidates; the wired half runs ``resolve_all`` against
``resolve_fixtures.Answering``, so each test pins exactly which requests a lookup
makes. Titles are invented.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_films  # noqa: E402 - after the sys.path inserts above
import resolve_http  # noqa: E402 - after the sys.path inserts above
import resolve_match  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from parse_rows import title_key  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    candidate,
    http_error,
    input_row,
    tmdb_credits_payload,
    tmdb_crew,
    tmdb_result,
    tmdb_search_payload,
)
from resolve_rows_case import RowsCase, kinds  # noqa: E402 - after the inserts
from resolve_types import (  # noqa: E402 - after the sys.path inserts above
    Basis,
    Candidate,
    Directors,
    Lookup,
)

_TITLE = "The Placeholder Hour"
_BARE = "Placeholder Hour"
_FIRST_URL = resolve_films.search_url(_TITLE)
_RESEARCH_URL = resolve_films.search_url(_BARE)
_ONE_MATCH = "found by searching without a leading article"
_FAILED_SUFFIX = "; the search without a leading article failed"


def _not_found() -> Lookup:
    """What an empty first search comes to."""
    return Lookup("not-found", None, (), "no candidate within the year bounds")


def _query(
    lookup: Lookup,
    found: list[Candidate],
    title: str = _TITLE,
    *,
    work_year: int | None = None,
) -> str | None:
    """``research_query`` for a row titled ``title``, read in 2010."""
    return resolve_match.research_query(
        lookup, found, title, title_key(title), work_year=work_year, date_year=2010
    )


def _research(
    found: list[Candidate], title: str = _TITLE, first: list[Candidate] | None = None
) -> Lookup | None:
    """``research_verdict`` for a row titled ``title``, read in 2010.

    ``found`` is the second search's answer and ``first`` the first's, empty by default.
    """
    return resolve_match.research_verdict(
        first or [], found, title, work_year=None, date_year=2010
    )


class ResearchQueryTest(unittest.TestCase):
    """When a re-search is sent, and for what: its trigger."""

    def test_an_empty_search_asks_again_without_the_article(self) -> None:
        self.assertEqual(_query(_not_found(), []), _BARE)

    def test_no_exact_title_among_the_kept_asks_again(self) -> None:
        found = [candidate("Placeholder Hourglass", 2000)]
        first = Lookup("ambiguous", None, tuple(found), "no exact title among 1")
        self.assertEqual(_query(first, found), _BARE)

    def test_a_variant_ambiguous_title_asks_again(self) -> None:
        title = "The Placeholder & Hour"
        found = [
            candidate("The Placeholder and Hour", 1990),
            candidate("The Placeholder and Hour", 2000),
        ]
        first = Lookup("ambiguous", None, tuple(found), "2 titles match")
        self.assertEqual(_query(first, found, title), "Placeholder & Hour")

    def test_an_exact_title_outside_the_year_bound_still_asks_again(self) -> None:
        found = [candidate(_TITLE, 1990)]
        self.assertEqual(_query(_not_found(), found, work_year=1980), _BARE)

    def test_a_collision_never_asks_again(self) -> None:
        found = [candidate(_TITLE, 1990), candidate(_TITLE, 2000)]
        first = Lookup("ambiguous", None, tuple(found), "2 exact titles")
        self.assertIsNone(_query(first, found))

    def test_a_resolved_title_never_asks_again(self) -> None:
        bases: tuple[Basis, ...] = ("exact", "variant")
        for basis in bases:
            with self.subTest(basis=basis):
                found = [candidate(_BARE, 2000)]
                first = Lookup("resolved", found[0], (), "one", basis)
                self.assertIsNone(_query(first, found))

    def test_a_title_empty_without_its_article_asks_nothing(self) -> None:
        # "!" would normalize to "" and meet any candidate whose title does too.
        for title in ("The !", "A ?", "An ..."):
            with self.subTest(title=title):
                self.assertIsNone(_query(_not_found(), [], title))

    def test_a_title_with_no_leading_article_asks_nothing(self) -> None:
        for title in ("Placeholder Hour", "Theory Hour", "The", "An", "Anon Hour"):
            with self.subTest(title=title):
                self.assertIsNone(_query(_not_found(), [], title))

    def test_each_article_is_dropped_in_any_case(self) -> None:
        for title in (
            "A Placeholder Hour",
            "an Placeholder Hour",
            "THE Placeholder Hour",
        ):
            with self.subTest(title=title):
                self.assertEqual(_query(_not_found(), [], title), _BARE)


class ResearchVerdictTest(unittest.TestCase):
    """What the re-search's candidates come to."""

    def test_one_match_resolves_as_re_searched(self) -> None:
        match = candidate(_BARE, 2001, ident="7")
        lookup = _research([match, candidate("Placeholder Hourglass", 2002)])
        self.assertEqual(
            lookup, Lookup("resolved", match, (match,), _ONE_MATCH, "re-searched")
        )

    def test_an_original_title_or_an_articled_title_matches(self) -> None:
        by_original = candidate("Other Words", 2001, original_title=_BARE)
        articled = candidate("A Placeholder Hour", 2003)
        for match in (by_original, articled):
            with self.subTest(match=match.title):
                lookup = _research([match])
                assert lookup is not None
                self.assertEqual((lookup.status, lookup.chosen), ("resolved", match))

    def test_several_matches_are_ambiguous_listing_the_first_three(self) -> None:
        found = [candidate(_BARE, year) for year in (1990, 1995, 2000, 2005)]
        lookup = _research(found)
        self.assertEqual(
            lookup,
            Lookup(
                "ambiguous",
                None,
                tuple(found[: resolve_match.SHOWN]),
                "4 titles match a search without a leading article",
            ),
        )

    def test_the_first_searchs_matches_count_with_the_seconds(self) -> None:
        one, two = (
            candidate(_BARE, 1990, ident="1"),
            candidate("A Placeholder Hour", 1995, ident="2"),
        )
        lookup = _research([one], first=[one, two])
        assert lookup is not None
        self.assertEqual((lookup.status, lookup.shown), ("ambiguous", (one, two)))
        self.assertEqual(
            lookup.detail, "2 titles match a search without a leading article"
        )

    def test_a_candidate_both_searches_return_counts_once(self) -> None:
        match = candidate(_BARE, 1990, ident="1")
        again = candidate(_BARE, 1990, ident="1")
        lookup = _research([again], first=[match, candidate("Other", 1990)])
        assert lookup is not None
        self.assertEqual((lookup.status, lookup.chosen), ("resolved", match))

    def test_no_match_or_none_within_the_years_is_none(self) -> None:
        self.assertIsNone(_research([candidate("Placeholder Hourglass", 2000)]))
        self.assertIsNone(_research([candidate(_BARE, 2011)]))
        self.assertIsNone(_research([]))

    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(
            st.builds(
                candidate,
                st.sampled_from((_BARE, _TITLE, "A Placeholder Hour", "Other", "")),
                st.none() | st.integers(1980, 2020),
                original_title=st.sampled_from(("", _BARE, "Other")),
                ident=st.text(max_size=3),
            ),
            max_size=8,
        ),
        st.sampled_from((_TITLE, "A Placeholder Hour", _BARE, "The", "")) | st.text(),
        st.integers(0, 8),
    )
    def test_fuzz_research_verdict(
        self, drawn: list[Candidate], title: str, split: int
    ) -> None:
        first, found = drawn[:split], drawn[split:]
        lookup = _research(found, title, first)
        if lookup is None:
            return
        self.assertLessEqual(len(lookup.shown), resolve_match.SHOWN)
        if lookup.status == "resolved":
            self.assertIn(lookup.chosen, drawn)
            self.assertEqual(lookup.basis, "re-searched")
        else:
            self.assertEqual((lookup.status, lookup.chosen), ("ambiguous", None))


def _pages(first: object, research: object, **credits: str) -> dict[str, object]:
    """The first search's answer, the re-search's, and a credits answer per ``id<n>=``."""
    pages: dict[str, object] = {_FIRST_URL: first, _RESEARCH_URL: research}
    for tmdb_id, name in credits.items():
        url = resolve_films.credits_url(tmdb_id.removeprefix("id"))
        pages[url] = tmdb_credits_payload(tmdb_crew(name))
    return pages


_HOURGLASS = tmdb_result(5, "Placeholder Hourglass", release_date="1999-01-01")
_MATCH = tmdb_result(6, _BARE, release_date="2003-04-05")
_ROW = input_row(_TITLE, medium="film")


class ResearchWiredTest(RowsCase):
    """The re-search as ``resolve_all`` runs it: one more search, through the same Client."""

    def test_one_match_resolves_with_a_non_error_re_searched_entry(self) -> None:
        pages = _pages(
            tmdb_search_payload(_HOURGLASS), tmdb_search_payload(_HOURGLASS, _MATCH)
        )
        out, review = self.run_all([_ROW], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "6"))
        self.assertEqual(out[0].cells["year"], "2003")
        self.assertEqual(kinds(review), [("re-searched", _TITLE)])
        self.assertFalse(review[0].error)
        self.assertEqual(review[0].detail, _ONE_MATCH)
        self.assertEqual([c.id for c in review[0].candidates], ["6"])
        self.assertEqual(self.fetch.calls, [_FIRST_URL, _RESEARCH_URL])

    def test_several_matches_are_ambiguous(self) -> None:
        again = tmdb_result(8, _BARE, release_date="1980-01-01")
        pages = _pages(tmdb_search_payload(), tmdb_search_payload(_MATCH, again))
        out, review = self.run_all([_ROW], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "ambiguous")
        self.assertEqual(kinds(review), [("ambiguous", _TITLE)])
        self.assertEqual(
            review[0].detail, "2 titles match a search without a leading article"
        )
        self.assertEqual([c.id for c in review[0].candidates], ["6", "8"])

    def test_two_first_search_matches_stay_ambiguous_when_one_returns(self) -> None:
        bare = tmdb_result(1, "Foo", release_date="1990-01-01")
        articled = tmdb_result(2, "A Foo", release_date="1995-01-01")
        first, again = (
            resolve_films.search_url("The Foo"),
            resolve_films.search_url("Foo"),
        )
        pages = {
            first: tmdb_search_payload(bare, articled),
            again: tmdb_search_payload(bare),
        }
        out, review = self.run_all([input_row("The Foo", medium="film")], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "ambiguous")
        self.assertEqual([c.id for c in review[0].candidates], ["1", "2"])
        self.assertEqual(self.fetch.calls, [first, again])

    def test_no_match_leaves_the_first_answer(self) -> None:
        pages = _pages(tmdb_search_payload(), tmdb_search_payload(_HOURGLASS))
        out, review = self.run_all([_ROW], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "not-found")
        self.assertEqual(review[0].detail, "no candidate within the year bounds")
        self.assertEqual(len(self.fetch.calls), 2)

    def test_a_failed_re_search_keeps_the_first_answer_and_says_so(self) -> None:
        pages = _pages(tmdb_search_payload(_HOURGLASS), http_error(500))
        out, review = self.run_all([_ROW], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "ambiguous")
        self.assertEqual(kinds(review), [("ambiguous", _TITLE)])
        self.assertEqual(
            review[0].detail, f"no exact title among 1 candidates{_FAILED_SUFFIX}"
        )
        self.assertNotIn("http", review[0].detail)
        self.assertEqual([c.id for c in review[0].candidates], ["5"])

    def test_a_refused_token_on_the_re_search_ends_the_run(self) -> None:
        pages = _pages(tmdb_search_payload(), http_error(401))
        with self.assertRaises(resolve_http.TokenRefused):
            self.run_all([_ROW], pages=pages)

    def test_research_off_sends_no_second_search(self) -> None:
        pages = _pages(tmdb_search_payload(), tmdb_search_payload(_MATCH))
        out, _ = self.run_all([_ROW], pages=pages, research=False)
        self.assertEqual(out[0].cells["resolve_status"], "not-found")
        self.assertEqual(self.fetch.calls, [_FIRST_URL])

    def test_an_exact_title_or_a_collision_sends_no_second_search(self) -> None:
        exact = tmdb_result(9, _TITLE, release_date="2000-01-01", vote_count=40)
        twin = tmdb_result(10, _TITLE, release_date="2001-01-01")
        for found, status in (((exact,), "resolved"), ((exact, twin), "ambiguous")):
            with self.subTest(status=status):
                pages = _pages(tmdb_search_payload(*found), tmdb_search_payload())
                out, _ = self.run_all([_ROW], pages=pages, vote_ratio=0)
                self.assertEqual(out[0].cells["resolve_status"], status)
                self.assertEqual(self.fetch.calls, [_FIRST_URL])

    def test_the_re_search_is_cached_by_its_url(self) -> None:
        pages = _pages(tmdb_search_payload(), tmdb_search_payload(_MATCH))
        with tempfile.TemporaryDirectory() as cache:
            self.run_all([_ROW], pages=pages, cache=Path(cache))
            out, _ = self.run_all([_ROW], pages=pages, cache=Path(cache))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "6"))


class ResearchDirectorsTest(RowsCase):
    """A re-searched match lists its candidate, so ``--directors`` credits it."""

    def _run(self, directors: Directors) -> tuple[str, str]:
        pages = _pages(
            tmdb_search_payload(), tmdb_search_payload(_MATCH), id6="Director Six"
        )
        out, review = self.run_all([_ROW], pages=pages, directors=directors)
        return out[0].cells["creator"], review[0].candidates[0].creator

    def test_ambiguous_and_all_credit_the_match_once(self) -> None:
        listing: tuple[Directors, ...] = ("ambiguous", "all")
        for directors in listing:
            with self.subTest(directors=directors):
                self.assertEqual(self._run(directors), ("Director Six",) * 2)
                self.assertEqual(len(self.fetch.calls), 3)

    def test_none_sends_no_credits_request(self) -> None:
        self.assertEqual(self._run("none"), ("", ""))
        self.assertEqual(len(self.fetch.calls), 2)


class ReSearchedKindTest(unittest.TestCase):
    def test_re_searched_follows_variant_and_is_no_error(self) -> None:
        kinds_ = resolve_rows.REVIEW_KINDS
        self.assertEqual(kinds_.index("re-searched"), kinds_.index("variant") + 1)
        self.assertNotIn("re-searched", resolve_rows.ERROR_KINDS)


if __name__ == "__main__":
    unittest.main()
