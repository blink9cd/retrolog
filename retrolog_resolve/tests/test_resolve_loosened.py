"""Tests for the most-voted and variant matches as resolve_all wires them.

A loosened match resolves its row and adds a non-error review entry of its own kind,
listing the candidates it chose among; ``--directors`` treats those candidates as it
treats an ambiguous film's. Every request goes to ``resolve_fixtures.Answering``, so
each test pins exactly which requests a lookup makes. Titles are invented.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_films  # noqa: E402 - after the sys.path inserts above
import resolve_match  # noqa: E402 - after the sys.path inserts above
import resolve_output  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from parse_rows import title_key  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    candidate,
    http_error,
    input_row,
    ol_edition,
    ol_payload,
    ol_work,
    tmdb_credits_payload,
    tmdb_crew,
    tmdb_result,
    tmdb_search_payload,
)
from resolve_rows_case import (  # noqa: E402 - after the sys.path inserts above
    ISBN,
    STEP1,
    STEP2,
    STEP3,
    RowsCase,
    kinds,
    ol_url,
    overrides_of,
)
from resolve_types import ReviewEntry  # noqa: E402 - after the sys.path inserts above

_COLLIDING = "Placeholder"
_SEQUEL = "Placeholder II"


def _film_pages(title: str, *results: object, **credits: object) -> dict[str, object]:
    """The search answer for ``title``, and a credits answer per ``id<n>=`` given.

    A credits value that is an exception is raised for that id instead.
    """
    pages: dict[str, object] = {
        resolve_films.search_url(title): tmdb_search_payload(*results)
    }
    for tmdb_id, answer in credits.items():
        url = resolve_films.credits_url(tmdb_id.removeprefix("id"))
        is_error = isinstance(answer, BaseException)
        pages[url] = answer if is_error else tmdb_credits_payload(tmdb_crew(answer))
    return pages


def _collision(**credits: object) -> dict[str, object]:
    """Two exact titles, the second with fifty times the first's votes."""
    return _film_pages(
        _COLLIDING,
        tmdb_result(1, _COLLIDING, release_date="1990-01-01", vote_count=10),
        tmdb_result(2, _COLLIDING, release_date="2001-05-01", vote_count=500),
        **credits,
    )


def _sequel(**credits: object) -> dict[str, object]:
    """No exact title for "Placeholder II", one candidate under the sequel numeral."""
    return _film_pages(
        _SEQUEL,
        tmdb_result(7, "Placeholder 2", release_date="2004-02-02", vote_count=3),
        tmdb_result(8, "Placeholder Returns", vote_count=90),
        **credits,
    )


class MostVotedTest(RowsCase):
    """A collision far outvoted resolves, with a ``most-voted`` entry."""

    def test_the_most_voted_film_resolves_with_a_non_error_entry(self) -> None:
        out, review = self.run_all(
            [input_row(_COLLIDING, medium="film")], pages=_collision()
        )
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "2"))
        self.assertEqual(out[0].cells["year"], "2001")
        self.assertEqual(kinds(review), [("most-voted", _COLLIDING)])
        entry = review[0]
        self.assertFalse(entry.error)
        self.assertEqual(entry.detail, "most votes: 500 against 10")
        self.assertEqual(
            [(c.id, c.votes) for c in entry.candidates], [("2", 500), ("1", 10)]
        )
        self.assertEqual(len(self.fetch.calls), 1)

    def test_a_ratio_of_zero_keeps_the_collision_ambiguous(self) -> None:
        out, review = self.run_all(
            [input_row(_COLLIDING, medium="film")], pages=_collision(), vote_ratio=0
        )
        self.assertEqual(out[0].cells["resolve_status"], "ambiguous")
        self.assertEqual(kinds(review), [("ambiguous", _COLLIDING)])

    def test_directors_ambiguous_credits_the_shown_and_fills_the_creator(self) -> None:
        pages = _collision(id1="Director One", id2="Director Two")
        out, review = self.run_all(
            [input_row(_COLLIDING, medium="film")], pages=pages, directors="ambiguous"
        )
        self.assertEqual(out[0].cells["creator"], "Director Two")
        self.assertEqual(
            [c.creator for c in review[0].candidates], ["Director Two", "Director One"]
        )
        self.assertEqual(review[0].detail, "most votes: 500 against 10")

    def test_directors_all_asks_no_more_than_the_shown_credits(self) -> None:
        pages = _collision(id1="Director One", id2="Director Two")
        out, _ = self.run_all(
            [input_row(_COLLIDING, medium="film")], pages=pages, directors="all"
        )
        self.assertEqual(out[0].cells["creator"], "Director Two")
        self.assertEqual(len(self.fetch.calls), 3)

    def test_directors_none_sends_no_credits_request(self) -> None:
        out, review = self.run_all(
            [input_row(_COLLIDING, medium="film")], pages=_collision()
        )
        self.assertEqual(out[0].cells["creator"], "")
        self.assertEqual([c.creator for c in review[0].candidates], ["", ""])
        self.assertEqual(len(self.fetch.calls), 1)

    def test_a_failed_credits_request_says_directors_unavailable(self) -> None:
        pages = _collision(id1=http_error(503), id2="Director Two")
        out, review = self.run_all(
            [input_row(_COLLIDING, medium="film")], pages=pages, directors="ambiguous"
        )
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "2"))
        self.assertEqual(out[0].cells["creator"], "Director Two")
        self.assertEqual(
            review[0].detail, "most votes: 500 against 10; directors unavailable"
        )

    def test_the_chosen_film_failing_its_credits_keeps_it_resolved(self) -> None:
        pages = _collision(id1="Director One", id2=http_error(503))
        out, review = self.run_all(
            [input_row(_COLLIDING, medium="film")], pages=pages, directors="ambiguous"
        )
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "2"))
        self.assertEqual(out[0].cells["creator"], "")
        self.assertTrue(review[0].detail.endswith("; directors unavailable"))


class VariantTest(RowsCase):
    """A title with no exact match resolved under a variant, with its entry."""

    def test_a_variant_match_resolves_with_a_non_error_entry(self) -> None:
        out, review = self.run_all([input_row(_SEQUEL, medium="film")], pages=_sequel())
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "7"))
        self.assertEqual(out[0].cells["year"], "2004")
        self.assertEqual(kinds(review), [("variant", _SEQUEL)])
        self.assertFalse(review[0].error)
        self.assertEqual(review[0].detail, "matched ignoring a sequel numeral")
        self.assertEqual([c.id for c in review[0].candidates], ["7"])

    def test_directors_ambiguous_fills_the_variant_match_creator(self) -> None:
        out, review = self.run_all(
            [input_row(_SEQUEL, medium="film")],
            pages=_sequel(id7="Director Seven"),
            directors="ambiguous",
        )
        self.assertEqual(out[0].cells["creator"], "Director Seven")
        self.assertEqual(review[0].candidates[0].creator, "Director Seven")
        self.assertEqual(len(self.fetch.calls), 2)


class UntouchedTest(RowsCase):
    """What neither rule reaches: books, and a title an override already decides."""

    def test_a_book_never_takes_a_variant(self) -> None:
        work = ol_work(title="Placeholder 2", editions=(ol_edition(ISBN),))
        pages = {
            ol_url(_SEQUEL, step): ol_payload(work) for step in (STEP1, STEP2, STEP3)
        }
        out, review = self.run_all([input_row(_SEQUEL)], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "ambiguous")
        self.assertEqual(kinds(review), [("ambiguous", _SEQUEL)])

    def test_a_title_only_override_wins_before_any_lookup(self) -> None:
        override = overrides_of(
            f'medium = "film"\ntitle = "{_COLLIDING}"\ntmdb_id = "9"'
        )
        out, review = self.run_all(
            [input_row(_COLLIDING, medium="film")], override, pages=_collision()
        )
        self.assertEqual(self.status(out[0]), ("override", "", "", "9"))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(kinds(review), [("override-applied", _COLLIDING)])


class ReviewKindsTest(unittest.TestCase):
    def test_the_new_kinds_sit_after_not_found_and_before_no_isbn(self) -> None:
        self.assertEqual(
            resolve_rows.REVIEW_KINDS,
            (
                "override-unused",
                "override-mismatch",
                "failed",
                "ambiguous",
                "not-found",
                "most-voted",
                "variant",
                "re-searched",
                "no-isbn",
                "override-applied",
            ),
        )
        self.assertNotIn("most-voted", resolve_rows.ERROR_KINDS)
        self.assertNotIn("variant", resolve_rows.ERROR_KINDS)


def _voted_entry(*votes: int | None) -> ReviewEntry:
    """A most-voted entry listing one invented candidate per vote count."""
    shown = tuple(
        candidate(_COLLIDING, 2000, ident=str(n), votes=count)
        for n, count in enumerate(votes)
    )
    return ReviewEntry(
        kind="most-voted",
        medium="film",
        title=_COLLIDING,
        rows=("10/seen.txt:1",),
        detail="a detail",
        candidates=shown,
        error=False,
    )


class OutputVotesTest(unittest.TestCase):
    """``review.json`` carries each candidate's votes; ``review.md`` shows them."""

    def test_review_json_carries_votes(self) -> None:
        document = json.loads(resolve_output.review_json([_voted_entry(1200, None)]))
        listed = document["entries"][0]["candidates"]
        self.assertEqual([c["votes"] for c in listed], [1200, None])
        self.assertEqual(document["counts"]["most-voted"], 1)

    def test_review_markdown_shows_known_votes_only(self) -> None:
        text = resolve_output.review_markdown([_voted_entry(1200, 0, None)])
        self.assertIn("## most-voted (1)", text)
        row = next(line for line in text.splitlines() if line.startswith("| film"))
        self.assertIn("2000, 0, 1200 votes; ", row)
        self.assertIn("2000, 1, 0 votes; ", row)
        self.assertTrue(row.endswith("2000, 2 |"), row)

    def test_one_vote_reads_singular(self) -> None:
        text = resolve_output.review_markdown([_voted_entry(1, 2)])
        row = next(line for line in text.splitlines() if line.startswith("| film"))
        self.assertIn("2000, 0, 1 vote; ", row)
        self.assertTrue(row.endswith("2000, 1, 2 votes |"), row)


class NonFiniteRatioTest(unittest.TestCase):
    """A ratio that is not a finite number turns the most-votes rule off."""

    def test_a_non_finite_ratio_never_fires(self) -> None:
        found = [
            candidate(_COLLIDING, 2000, ident="1", votes=0),
            candidate(_COLLIDING, 2000, ident="2", votes=50),
        ]
        for ratio in (math.inf, -math.inf, math.nan):
            with self.subTest(ratio=ratio):
                lookup = resolve_match.film_verdict(
                    found,
                    _COLLIDING,
                    title_key(_COLLIDING),
                    work_year=None,
                    date_year=2010,
                    vote_ratio=ratio,
                )
                self.assertEqual(lookup.status, "ambiguous")


if __name__ == "__main__":
    unittest.main()
