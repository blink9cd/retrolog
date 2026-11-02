"""Tests for resolve_match: the year filters and the exact-title verdict."""

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_match  # noqa: E402
from parse_rows import title_key  # noqa: E402
from resolve_fixtures import candidate as cand  # noqa: E402
from resolve_types import Candidate  # noqa: E402

# A small title alphabet, so drawn candidates often share a title with the drawn query
# and the fuzz property reaches "resolved" and "ambiguous", not only "not-found".
_TITLES = st.sampled_from(("Glass Orchard", "glass  orchard!", "Echo Lake", "", "?"))


@st.composite
def candidates(draw: st.DrawFn) -> Candidate:
    """An invented search result, its year sometimes unknown."""
    return cand(
        draw(_TITLES | st.text(max_size=12)),
        draw(st.none() | st.integers(1800, 2100)),
        original_title=draw(st.just("") | _TITLES),
        ident=draw(st.text(max_size=4)),
        votes=draw(st.none() | st.integers(0, 1000)),
    )


class FilterYearsTest(unittest.TestCase):
    def test_date_year_drops_only_later_years(self) -> None:
        kept = resolve_match.filter_years(
            [cand("A", 2011), cand("B", 2010), cand("C", None), cand("D", 1990)],
            work_year=None,
            date_year=2010,
        )
        self.assertEqual(kept, (cand("B", 2010), cand("C", None), cand("D", 1990)))

    def test_work_year_drops_unknown_years(self) -> None:
        kept = resolve_match.filter_years(
            [cand("A", None), cand("B", 1968), cand("C", 1969)],
            work_year=1968,
            date_year=None,
        )
        self.assertEqual(kept, (cand("B", 1968),))

    def test_no_bound_keeps_everything_in_order(self) -> None:
        given_order = (cand("B", 2050), cand("A", None))
        kept = resolve_match.filter_years(given_order, work_year=None, date_year=None)
        self.assertEqual(kept, given_order)


class ExactTest(unittest.TestCase):
    def test_empty_original_title_never_matches_an_empty_key(self) -> None:
        self.assertEqual(resolve_match.exact([cand("Moby Dick", 1851)], ""), ())

    def test_punctuation_only_title_matches_an_empty_key(self) -> None:
        self.assertEqual(
            resolve_match.exact([cand("?!", 1851)], ""), (cand("?!", 1851),)
        )


class VerdictTest(unittest.TestCase):
    def test_one_exact_title_resolves(self) -> None:
        lookup = resolve_match.verdict(
            [cand("Harbor Lights", 2006), cand("Harbor Lights Revisited", 1957)],
            title_key("harbor lights"),
            work_year=None,
            date_year=2010,
        )
        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.chosen, cand("Harbor Lights", 2006))

    def test_two_exact_titles_are_ambiguous(self) -> None:
        first = cand("Glass Orchard", 1990, ident="1")
        second = cand("Glass Orchard!", 2001, ident="2")
        lookup = resolve_match.verdict(
            [first, cand("Glass Orchards", 1995), second],
            title_key("Glass Orchard"),
            work_year=None,
            date_year=2010,
        )
        self.assertEqual(lookup.status, "ambiguous")
        self.assertIsNone(lookup.chosen)
        self.assertEqual(lookup.shown, (first, second))

    def test_later_work_is_dropped_by_the_date(self) -> None:
        lookup = resolve_match.verdict(
            [cand("Echo Lake", 2021), cand("Echo Lake", 1984)],
            title_key("Echo Lake"),
            work_year=None,
            date_year=2010,
        )
        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.chosen, cand("Echo Lake", 1984))

    def test_same_year_is_kept(self) -> None:
        # An approximate December 31 date never excludes a work of that year.
        lookup = resolve_match.verdict(
            [cand("Echo Lake", 2010)],
            title_key("Echo Lake"),
            work_year=None,
            date_year=2010,
        )
        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.chosen, cand("Echo Lake", 2010))

    def test_work_year_keeps_only_that_year(self) -> None:
        # The row's title read "Metropolis (1968)": parse took 1968 as the work year.
        lookup = resolve_match.verdict(
            [
                cand("Metropolis", 1927),
                cand("Metropolis", 1968),
                cand("Metropolis", None),
            ],
            title_key("Metropolis"),
            work_year=1968,
            date_year=2010,
        )
        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.chosen, cand("Metropolis", 1968))

    def test_undated_row_skips_the_date_filter(self) -> None:
        lookup = resolve_match.verdict(
            [cand("Echo Lake", 2099)],
            title_key("Echo Lake"),
            work_year=None,
            date_year=None,
        )
        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.chosen, cand("Echo Lake", 2099))

    def test_no_exact_title_lists_the_top_three(self) -> None:
        found = [cand(f"Harbor Lights {n}", 2000 + n) for n in range(5)]
        lookup = resolve_match.verdict(
            found, title_key("Harbor Lights"), work_year=None, date_year=2010
        )
        self.assertEqual(lookup.status, "ambiguous")
        self.assertIsNone(lookup.chosen)
        self.assertEqual(lookup.shown, tuple(found[: resolve_match.SHOWN]))

    def test_shown_exact_titles_are_capped_at_three(self) -> None:
        found = [cand("Echo Lake", 1980 + n) for n in range(5)]
        lookup = resolve_match.verdict(
            found, title_key("Echo Lake"), work_year=None, date_year=2010
        )
        self.assertEqual(lookup.status, "ambiguous")
        self.assertEqual(lookup.shown, tuple(found[:3]))

    def test_empty_search_is_not_found(self) -> None:
        lookup = resolve_match.verdict(
            [], title_key("Echo Lake"), work_year=None, date_year=2010
        )
        self.assertEqual(lookup.status, "not-found")
        self.assertIsNone(lookup.chosen)
        self.assertEqual(lookup.shown, ())

    def test_every_candidate_filtered_out_is_not_found(self) -> None:
        lookup = resolve_match.verdict(
            [cand("Echo Lake", 2021)],
            title_key("Echo Lake"),
            work_year=None,
            date_year=2010,
        )
        self.assertEqual(lookup.status, "not-found")

    def test_original_title_matches(self) -> None:
        film = cand("The Harbor", 1960, original_title="Le Port Gris")
        lookup = resolve_match.verdict(
            [cand("Grey Port", 1961), film],
            title_key("le port gris"),
            work_year=None,
            date_year=2010,
        )
        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.chosen, film)

    def test_detail_is_set_for_every_status(self) -> None:
        for found in ([], [cand("Echo Lake", 1984)], [cand("Echo", 1984)]):
            lookup = resolve_match.verdict(
                found, title_key("Echo Lake"), work_year=None, date_year=None
            )
            self.assertTrue(lookup.detail, lookup.status)

    @given(
        st.lists(candidates(), max_size=12),
        _TITLES | st.text(max_size=20),
        st.none() | st.integers(1800, 2100),
        st.none() | st.integers(1800, 2100),
    )
    def test_fuzz_verdict(
        self,
        cands: list[Candidate],
        title: str,
        work_year: int | None,
        date_year: int | None,
    ) -> None:
        lookup = resolve_match.verdict(
            cands, title_key(title), work_year=work_year, date_year=date_year
        )
        self.assertLessEqual(len(lookup.shown), resolve_match.SHOWN)
        # A resolved lookup's choice passed every filter and matched the title.
        if lookup.status == "resolved":
            chosen = lookup.chosen
            assert chosen is not None
            self.assertIn(chosen, cands)
            self.assertIn(
                title_key(title),
                {title_key(chosen.title), title_key(chosen.original_title)},
            )
            if work_year is not None:
                self.assertEqual(chosen.year, work_year)
            if date_year is not None and chosen.year is not None:
                self.assertLessEqual(chosen.year, date_year)
        else:
            self.assertIsNone(lookup.chosen)


def _film(
    title: str, ident: str, votes: int | None, year: int | None = 2000, **kw: str
) -> Candidate:
    """An invented film candidate with a given id and vote count."""
    return cand(title, year, ident=ident, votes=votes, **kw)


def _film_verdict(
    found: list[Candidate],
    title: str,
    *,
    vote_ratio: float = 10.0,
    vote_floor: int = 1,
    work_year: int | None = None,
    date_year: int | None = 2010,
) -> resolve_match.Lookup:
    """film_verdict for a row titled ``title``, the default most-votes ratio unless given."""
    return resolve_match.film_verdict(
        found,
        title,
        title_key(title),
        work_year=work_year,
        date_year=date_year,
        vote_ratio=vote_ratio,
        vote_floor=vote_floor,
    )


class FilmVerdictExactTest(unittest.TestCase):
    def test_one_exact_title_resolves_as_before(self) -> None:
        film = _film("Placeholder", "1", 5)
        lookup = _film_verdict(
            [film, _film("Placeholder Two", "2", 900)], "Placeholder"
        )
        self.assertEqual((lookup.status, lookup.chosen), ("resolved", film))
        self.assertEqual((lookup.basis, lookup.detail), ("exact", "one exact title"))

    def test_nothing_within_the_years_is_not_found(self) -> None:
        lookup = _film_verdict([_film("Placeholder", "1", 5, 2020)], "Placeholder")
        self.assertEqual(lookup.status, "not-found")
        self.assertEqual(lookup.shown, ())


class FilmVerdictMostVotedTest(unittest.TestCase):
    def test_ten_times_the_runner_up_resolves_most_voted(self) -> None:
        low = _film("Placeholder", "1", 10)
        high = _film("Placeholder!", "2", 100)
        lookup = _film_verdict([low, _film("Other", "3", 9000), high], "Placeholder")
        self.assertEqual((lookup.status, lookup.chosen), ("resolved", high))
        self.assertEqual(lookup.basis, "most-voted")
        self.assertEqual(lookup.shown, (high, low))
        self.assertEqual(lookup.detail, "most votes: 100 against 10")

    def test_just_under_the_ratio_is_ambiguous(self) -> None:
        low = _film("Placeholder", "1", 10)
        high = _film("Placeholder", "2", 99)
        lookup = _film_verdict([low, high], "Placeholder")
        self.assertEqual(lookup.status, "ambiguous")
        self.assertIsNone(lookup.chosen)
        self.assertEqual(lookup.shown, (low, high))
        self.assertEqual(lookup.detail, "2 exact titles")

    def test_one_vote_against_none_fires(self) -> None:
        top = _film("Placeholder", "2", 1)
        lookup = _film_verdict([_film("Placeholder", "1", 0), top], "Placeholder")
        self.assertEqual((lookup.basis, lookup.chosen), ("most-voted", top))

    def test_a_top_with_no_votes_does_not_fire(self) -> None:
        found = [_film("Placeholder", "1", 0), _film("Placeholder", "2", 0)]
        self.assertEqual(_film_verdict(found, "Placeholder").status, "ambiguous")

    def test_unknown_votes_on_any_exact_title_do_not_fire(self) -> None:
        for votes in ((None, 500), (500, None), (500, 1, None)):
            with self.subTest(votes=votes):
                found = [
                    _film("Placeholder", str(n), count) for n, count in enumerate(votes)
                ]
                self.assertEqual(
                    _film_verdict(found, "Placeholder").status, "ambiguous"
                )

    def test_unknown_votes_off_the_exact_titles_do_not_matter(self) -> None:
        top = _film("Placeholder", "2", 50)
        found = [_film("Placeholder", "1", 1), top, _film("Elsewhere", "3", None)]
        self.assertEqual(_film_verdict(found, "Placeholder").chosen, top)

    def test_a_ratio_of_zero_never_fires(self) -> None:
        found = [_film("Placeholder", "1", 0), _film("Placeholder", "2", 10**6)]
        lookup = _film_verdict(found, "Placeholder", vote_ratio=0)
        self.assertEqual(lookup.status, "ambiguous")

    def test_a_tie_never_fires_whatever_the_ratio(self) -> None:
        found = [_film("Placeholder", "1", 40), _film("Placeholder", "2", 40)]
        lookup = _film_verdict(found, "Placeholder", vote_ratio=0.5)
        self.assertEqual(lookup.status, "ambiguous")

    def test_three_exact_titles_compare_the_top_two(self) -> None:
        one = _film("Placeholder", "1", 5)
        two = _film("Placeholder", "2", 1000)
        three = _film("Placeholder", "3", 101)
        lookup = _film_verdict([one, two, three], "Placeholder")
        self.assertEqual(lookup.status, "ambiguous")
        lookup = _film_verdict(
            [one, two, _film("Placeholder", "3", 100)], "Placeholder"
        )
        self.assertEqual(lookup.chosen, two)
        self.assertEqual(lookup.detail, "most votes: 1000 against 100")

    def test_shown_is_capped_most_voted_first(self) -> None:
        found = [_film("Placeholder", str(n), n) for n in range(4)]
        found.append(_film("Placeholder", "top", 1000))
        lookup = _film_verdict(found, "Placeholder")
        self.assertEqual(lookup.chosen, found[-1])
        self.assertEqual(lookup.shown, (found[-1], found[3], found[2]))

    def test_the_years_filter_before_the_votes(self) -> None:
        kept = _film("Placeholder", "1", 3, 1990)
        later = _film("Placeholder", "2", 9000, 2020)
        lookup = _film_verdict([kept, later], "Placeholder")
        self.assertEqual((lookup.basis, lookup.chosen), ("exact", kept))


class FilmVerdictVariantTest(unittest.TestCase):
    def test_the_first_variant_with_one_match_resolves(self) -> None:
        film = _film("Placeholder II", "1", None)
        lookup = _film_verdict(
            [_film("Placeholder Saga", "2", None), film], "Placeholder 2"
        )
        self.assertEqual((lookup.status, lookup.chosen), ("resolved", film))
        self.assertEqual(lookup.basis, "variant")
        self.assertEqual(lookup.shown, (film,))
        self.assertEqual(lookup.detail, "matched ignoring a sequel numeral")

    def test_the_row_title_is_varied_too(self) -> None:
        film = _film("Placeholder", "1", None)
        lookup = _film_verdict([film], "The Placeholder")
        self.assertEqual(lookup.chosen, film)
        self.assertEqual(lookup.detail, "matched ignoring a leading article")

    def test_an_earlier_variant_with_two_matches_is_ambiguous(self) -> None:
        # The ampersand gives two; the leading article alone would give one.
        first = _film("The Placeholder and Friend", "1", None)
        second = _film("The Placeholder and Friend!", "2", None)
        third = _film("Placeholder & Friend", "3", None)
        lookup = _film_verdict(
            [first, second, third, _film("Placeholder & Friend", "4", None, 2020)],
            "The Placeholder & Friend",
        )
        self.assertEqual(lookup.status, "ambiguous")
        self.assertIsNone(lookup.chosen)
        self.assertEqual(lookup.shown, (first, second))
        self.assertEqual(lookup.detail, "2 titles match ignoring an ampersand")

    def test_many_variant_matches_are_capped(self) -> None:
        found = [_film(f"Placeholder ({n})", str(n), None) for n in range(5)]
        lookup = _film_verdict(found, "Placeholder")
        self.assertEqual(lookup.status, "ambiguous")
        self.assertEqual(lookup.shown, tuple(found[: resolve_match.SHOWN]))
        self.assertEqual(lookup.detail, "5 titles match ignoring a trailing note")

    def test_no_variant_matching_keeps_the_no_exact_answer(self) -> None:
        found = [_film(f"Elsewhere {n}", str(n), None) for n in range(5)]
        lookup = _film_verdict(found, "Placeholder")
        self.assertEqual(lookup.status, "ambiguous")
        self.assertEqual(lookup.shown, tuple(found[: resolve_match.SHOWN]))
        self.assertEqual(lookup.detail, "no exact title among 5 candidates")

    def test_the_original_title_is_tried_under_each_variant(self) -> None:
        film = _film("Elsewhere", "1", None, original_title="Placeholder's Day")
        lookup = _film_verdict([film], "Placeholder Day")
        self.assertEqual(lookup.chosen, film)
        self.assertEqual(lookup.detail, "matched ignoring a possessive")

    def test_an_empty_original_title_never_matches(self) -> None:
        # The row's key is blank, and an unset original title must not meet it.
        lookup = _film_verdict([_film("Elsewhere", "1", None)], "?")
        self.assertEqual(lookup.status, "ambiguous")

    def test_the_years_filter_before_the_variants(self) -> None:
        lookup = _film_verdict(
            [_film("Placeholder II", "1", None, 2020), _film("Other", "2", None)],
            "Placeholder 2",
        )
        self.assertEqual(lookup.detail, "no exact title among 1 candidates")

    def test_a_detail_never_holds_a_title(self) -> None:
        found = [_film("Placeholder", "1", 1), _film("Placeholder", "2", 100)]
        for lookup in (
            _film_verdict(found, "Placeholder"),
            _film_verdict([_film("Placeholder (Cut)", "1", None)], "Placeholder"),
        ):
            with self.subTest(detail=lookup.detail):
                self.assertNotIn("placeholder", lookup.detail.casefold())

    @given(
        st.lists(candidates(), max_size=12),
        _TITLES | st.text(max_size=20),
        st.none() | st.integers(1800, 2100),
        st.none() | st.integers(1800, 2100),
        st.sampled_from((0.0, 1.5, 10.0)),
        st.sampled_from((0, 1, 50)),
    )
    def test_fuzz_film_verdict(
        self,
        cands: list[Candidate],
        title: str,
        work_year: int | None,
        date_year: int | None,
        vote_ratio: float,
        vote_floor: int,
    ) -> None:
        lookup = resolve_match.film_verdict(
            cands,
            title,
            title_key(title),
            work_year=work_year,
            date_year=date_year,
            vote_ratio=vote_ratio,
            vote_floor=vote_floor,
        )
        self.assertLessEqual(len(lookup.shown), resolve_match.SHOWN)
        key = title_key(title)
        if lookup.status == "resolved":
            chosen = lookup.chosen
            self.assertIn(chosen, cands)
            if chosen is not None and lookup.basis == "exact":
                # A resolved exact result never has any reason to be held.
                self.assertEqual(
                    resolve_match.suspect_reasons(chosen, key, vote_floor=vote_floor),
                    (),
                )
        else:
            self.assertIsNone(lookup.chosen)
            if lookup.basis == "suspect-exact":
                # A held film lists the one exact title, with at least one reason.
                self.assertEqual(lookup.status, "ambiguous")
                (held,) = lookup.shown
                self.assertTrue(
                    resolve_match.suspect_reasons(held, key, vote_floor=vote_floor)
                )
            else:
                self.assertEqual(lookup.basis, "exact")


if __name__ == "__main__":
    unittest.main()
