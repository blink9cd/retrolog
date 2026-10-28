"""Tests for resolve_rows' lookups (rule 4): the book chain, the film search, failures.

As in ``test_resolve_rows``, every request goes to ``resolve_fixtures.Answering``, so
each test pins exactly which requests a lookup makes.
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
import resolve_films  # noqa: E402 - after the sys.path inserts above
import resolve_http  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    Answering,
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
    EMPTY_OL,
    ISBN,
    STEP1,
    STEP2,
    STEP3,
    RowsCase,
    kinds,
    ol_url,
)


class BookChainTest(RowsCase):
    """Rule 4 for a book: the query chain, stopping at an exact title with an ISBN."""

    def _work(
        self, title: str = "Moby Dick", key: str = "/works/OL1W"
    ) -> dict[str, object]:
        """A work whose first edition is English with a valid ISBN."""
        return ol_work(key=key, title=title, editions=(ol_edition(ISBN),))

    def test_an_exact_title_with_an_isbn_at_step_one_stops_the_chain(self) -> None:
        pages = {ol_url("Moby Dick", STEP1): ol_payload(self._work(key="/works/OL7W"))}
        out, review = self.run_all([input_row("Moby Dick")], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", ISBN, "/works/OL7W", ""))
        self.assertEqual(out[0].cells["creator"], "Placeholder Author")
        self.assertEqual(out[0].cells["year"], "1851")
        self.assertEqual(review, ())
        self.assertEqual(len(self.fetch.calls), 1)

    def test_a_year_parse_read_is_kept(self) -> None:
        pages = {ol_url("Moby Dick", STEP1): ol_payload(self._work())}
        out, _ = self.run_all([input_row("Moby Dick", work_year=1851)], pages=pages)
        self.assertEqual(out[0].cells["year"], "1851")

    def test_no_exact_title_moves_on_to_the_next_step(self) -> None:
        pages = {
            ol_url("Moby Dick", STEP1): ol_payload(self._work("Moby Dick Retold")),
            ol_url("Moby Dick", STEP2): ol_payload(self._work(key="/works/OL8W")),
        }
        out, _ = self.run_all([input_row("Moby Dick")], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", ISBN, "/works/OL8W", ""))

    def test_an_exact_title_with_no_isbn_moves_on(self) -> None:
        no_isbn = ol_work(editions=(ol_edition("not an isbn"),))
        pages = {
            ol_url("Moby Dick", STEP1): ol_payload(no_isbn),
            ol_url("Moby Dick", STEP2): ol_payload(no_isbn),
            ol_url("Moby Dick", STEP3): ol_payload(ol_work(key="/works/OL9W")),
        }
        out, review = self.run_all([input_row("Moby Dick")], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", "", "/works/OL9W", ""))
        self.assertEqual(kinds(review), [("no-isbn", "Moby Dick")])
        self.assertEqual(review[0].candidates[0].id, "/works/OL9W")
        self.assertFalse(review[0].error)

    def test_two_exact_titles_with_isbns_stop_as_ambiguous(self) -> None:
        pages = {
            ol_url("Moby Dick", STEP1): ol_payload(
                self._work(key="/works/OL1W"), self._work(key="/works/OL2W")
            )
        }
        out, review = self.run_all([input_row("Moby Dick")], pages=pages)
        self.assertEqual(self.status(out[0]), ("ambiguous", "", "", ""))
        self.assertEqual(
            [c.id for c in review[0].candidates], ["/works/OL1W", "/works/OL2W"]
        )
        self.assertEqual(review[0].detail, "2 exact titles")

    def test_nothing_at_any_step_is_not_found(self) -> None:
        pages = {ol_url("Moby Dick", step): EMPTY_OL for step in (STEP1, STEP2, STEP3)}
        out, review = self.run_all([input_row("Moby Dick")], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "not-found")
        self.assertEqual(kinds(review), [("not-found", "Moby Dick")])
        self.assertEqual(len(self.fetch.calls), 3)

    def test_an_undated_row_sends_no_year(self) -> None:
        pages = {ol_url("Moby Dick", STEP1, None): ol_payload(self._work())}
        out, _ = self.run_all([input_row("Moby Dick", date="")], pages=pages)
        self.assertEqual(out[0].cells["resolve_status"], "resolved")


class FilmTest(RowsCase):
    """Rule 4 for a film: the search, the verdict and ``--directors``."""

    _URL = resolve_films.search_url("Metropolis")

    def _pages(self, *results: object, **credits: object) -> dict[str, object]:
        pages: dict[str, object] = {self._URL: tmdb_search_payload(*results)}
        for tmdb_id, name in credits.items():
            url = resolve_films.credits_url(tmdb_id.removeprefix("id"))
            pages[url] = tmdb_credits_payload(tmdb_crew(name))
        return pages

    def test_a_resolved_film_records_its_id_and_year(self) -> None:
        pages = self._pages(tmdb_result(19, "Metropolis", release_date="1927-01-10"))
        out, review = self.run_all(
            [input_row("Metropolis", medium="film")], pages=pages
        )
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "19"))
        self.assertEqual((out[0].cells["year"], out[0].cells["creator"]), ("1927", ""))
        self.assertEqual(review, ())
        self.assertIn("Authorization", self.fetch.headers[0])

    def test_directors_none_asks_for_no_credits(self) -> None:
        pages = self._pages(tmdb_result(1, "Metropolis"), tmdb_result(2, "Metropolis"))
        _, review = self.run_all([input_row("Metropolis", medium="film")], pages=pages)
        self.assertEqual([c.creator for c in review[0].candidates], ["", ""])

    def test_directors_ambiguous_names_the_shown_candidates_only(self) -> None:
        both = (tmdb_result(1, "Metropolis"), tmdb_result(2, "Metropolis"))
        pages = self._pages(*both, id1="Director One", id2="Director Two")
        _, review = self.run_all(
            [input_row("Metropolis", medium="film")], pages=pages, directors="ambiguous"
        )
        self.assertEqual(
            [c.creator for c in review[0].candidates], ["Director One", "Director Two"]
        )
        resolved = self._pages(tmdb_result(3, "Metropolis"))
        out, _ = self.run_all(
            [input_row("Metropolis", medium="film")],
            pages=resolved,
            directors="ambiguous",
        )
        self.assertEqual(out[0].cells["creator"], "")

    def test_directors_all_also_names_the_chosen_film(self) -> None:
        pages = self._pages(tmdb_result(3, "Metropolis"), id3="Director Three")
        out, _ = self.run_all(
            [input_row("Metropolis", medium="film")], pages=pages, directors="all"
        )
        self.assertEqual(out[0].cells["creator"], "Director Three")

    def test_no_tmdb_service_fails_each_film(self) -> None:
        rows, review = resolve_rows.resolve_all(
            [input_row("Metropolis", medium="film")],
            (),
            client=resolve_http.Client(None, timeout=1.0, fetch=Answering()),
            open_library=resolve_http.open_library(None),
            tmdb=None,
            directors="none",
            vote_ratio=10.0,
            research=True,
        )
        self.assertEqual(rows[0].cells["resolve_status"], "failed")
        self.assertEqual((review[0].kind, review[0].error), ("failed", True))


class FailureTest(RowsCase):
    """A failed lookup fails its group only; a refused token ends the run."""

    def test_a_failed_lookup_fails_its_group_and_the_run_goes_on(self) -> None:
        rows = [
            input_row("Lost Film", medium="film"),
            input_row("Metropolis", medium="film"),
        ]
        pages = {
            resolve_films.search_url("Lost Film"): http_error(500),
            resolve_films.search_url("Metropolis"): tmdb_search_payload(
                tmdb_result(4, "Metropolis")
            ),
        }
        out, review = self.run_all(rows, pages=pages)
        self.assertEqual(
            [r.cells["resolve_status"] for r in out], ["failed", "resolved"]
        )
        self.assertEqual(
            [(e.kind, e.detail) for e in review], [("failed", "tmdb: HTTP 500")]
        )

    def test_a_failed_credits_lookup_keeps_the_resolved_film(self) -> None:
        """The director only helps the operator choose, so its loss fails nothing."""
        pages = {
            resolve_films.search_url("Metropolis"): tmdb_search_payload(
                tmdb_result(4, "Metropolis")
            ),
            resolve_films.credits_url("4"): http_error(503),
        }
        out, review = self.run_all(
            [input_row("Metropolis", medium="film")], pages=pages, directors="all"
        )
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "4"))
        self.assertEqual(out[0].cells["creator"], "")
        self.assertEqual(review, ())

    def test_a_failed_credits_lookup_keeps_the_ambiguous_candidates(self) -> None:
        pages = {
            resolve_films.search_url("Metropolis"): tmdb_search_payload(
                tmdb_result(1, "Metropolis"), tmdb_result(2, "Metropolis")
            ),
            resolve_films.credits_url("1"): http_error(503),
            resolve_films.credits_url("2"): tmdb_credits_payload(
                tmdb_crew("Director Two")
            ),
        }
        out, review = self.run_all(
            [input_row("Metropolis", medium="film")], pages=pages, directors="ambiguous"
        )
        self.assertEqual(out[0].cells["resolve_status"], "ambiguous")
        self.assertEqual(kinds(review), [("ambiguous", "Metropolis")])
        self.assertEqual(
            [(c.id, c.creator) for c in review[0].candidates],
            [("1", ""), ("2", "Director Two")],
        )
        self.assertEqual(review[0].detail, "2 exact titles; directors unavailable")

    def test_a_refused_token_on_credits_still_propagates(self) -> None:
        pages = {
            resolve_films.search_url("Metropolis"): tmdb_search_payload(
                tmdb_result(4, "Metropolis")
            ),
            resolve_films.credits_url("4"): http_error(401),
        }
        with self.assertRaises(resolve_http.TokenRefused):
            self.run_all(
                [input_row("Metropolis", medium="film")], pages=pages, directors="all"
            )

    def test_a_refused_token_propagates(self) -> None:
        pages = {resolve_films.search_url("Metropolis"): http_error(401)}
        with self.assertRaises(resolve_http.TokenRefused):
            self.run_all([input_row("Metropolis", medium="film")], pages=pages)


if __name__ == "__main__":
    unittest.main()
