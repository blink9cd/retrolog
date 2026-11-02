"""Tests for holding a suspicious single exact title for review (the suspect-exact kind).

A film the exact rule would settle is held when its one candidate has no release date,
matched on its original title alone, or falls under the vote floor. It comes out
``ambiguous`` with no ids, under a non-error review kind of its own. The tests run the
pure rule, then ``resolve_all`` with every request pinned, then the command line end to
end with ``HOME`` pointed at a temporary folder. Titles are invented.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL))
import resolve_films  # noqa: E402 - after the sys.path inserts above
import resolve_match  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
import retrolog_resolve  # noqa: E402 - after the sys.path inserts above
from parse_rows import title_key  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    HTTP_TOKEN,
    Answering,
    candidate,
    csv_text,
    input_row,
    ol_edition,
    ol_payload,
    ol_work,
    parse_row,
    tmdb_credits_payload,
    tmdb_crew,
    tmdb_result,
    tmdb_search_payload,
    write_parsed,
)
from resolve_rows_case import (  # noqa: E402 - after the sys.path inserts above
    ISBN,
    STEP1,
    RowsCase,
    ol_url,
    overrides_of,
)
from resolve_types import Candidate, Lookup  # noqa: E402 - after the sys.path inserts

_TITLE = "Moonrise"
_NO_DATE = "no release date"
_ORIGINAL_ONLY = "matched on its original title only"
_EXIT_OK, _EXIT_USAGE, _NOT_A_REPO = 0, 2, 128
_FLOOR = 5


def _reasons(
    found: Candidate, title: str = _TITLE, floor: int = _FLOOR
) -> tuple[str, ...]:
    """The reasons ``found`` is held for a row titled ``title``."""
    return resolve_match.suspect_reasons(found, title_key(title), vote_floor=floor)


def _film_verdict(found: Sequence[Candidate], title: str, floor: int = 1) -> Lookup:
    """film_verdict for a row titled ``title``, read in 2010."""
    return resolve_match.film_verdict(
        found,
        title,
        title_key(title),
        work_year=None,
        date_year=2010,
        vote_ratio=10.0,
        vote_floor=floor,
    )


def _pages(title: str, *results: object) -> dict[str, object]:
    """The one search answer a film row titled ``title`` asks for."""
    return {resolve_films.search_url(title): tmdb_search_payload(*results)}


class SuspectReasonsTest(unittest.TestCase):
    """Each signal alone, all together, and the floor's edges."""

    def test_a_dated_voted_title_match_has_no_reason(self) -> None:
        self.assertEqual(_reasons(candidate(_TITLE, 2000, votes=_FLOOR)), ())

    def test_no_release_date_alone(self) -> None:
        self.assertEqual(_reasons(candidate(_TITLE, None, votes=_FLOOR)), (_NO_DATE,))

    def test_original_title_only_alone(self) -> None:
        found = candidate("Elsewhere", 2000, original_title=_TITLE, votes=_FLOOR)
        self.assertEqual(_reasons(found), (_ORIGINAL_ONLY,))

    def test_a_film_matching_on_both_titles_is_not_original_only(self) -> None:
        found = candidate(_TITLE, 2000, original_title="moonrise!", votes=_FLOOR)
        self.assertEqual(_reasons(found), ())

    def test_votes_one_under_the_floor_alone(self) -> None:
        found = candidate(_TITLE, 2000, votes=_FLOOR - 1)
        self.assertEqual(_reasons(found), ("4 votes, under the floor of 5",))

    def test_unknown_votes_alone(self) -> None:
        found = candidate(_TITLE, 2000, votes=None)
        self.assertEqual(_reasons(found), ("votes unknown, under the floor of 5",))

    def test_all_three_in_order(self) -> None:
        found = candidate("Elsewhere", None, original_title=_TITLE, votes=0)
        self.assertEqual(
            _reasons(found),
            (_NO_DATE, _ORIGINAL_ONLY, "0 votes, under the floor of 5"),
        )

    def test_floor_zero_turns_off_only_the_votes_signal(self) -> None:
        for votes in (0, None):
            with self.subTest(votes=votes):
                self.assertEqual(
                    _reasons(candidate(_TITLE, 2000, votes=votes), floor=0), ()
                )
                self.assertEqual(
                    _reasons(candidate(_TITLE, None, votes=votes), floor=0),
                    (_NO_DATE,),
                )
        other = candidate("Elsewhere", 2000, original_title=_TITLE, votes=0)
        self.assertEqual(_reasons(other, floor=0), (_ORIGINAL_ONLY,))


class HeldVerdictTest(unittest.TestCase):
    """What ``film_verdict`` makes of the reasons."""

    def _assert_held(self, lookup: Lookup, found: Candidate, detail: str) -> None:
        self.assertEqual(lookup.status, "ambiguous")
        self.assertIsNone(lookup.chosen)
        self.assertEqual(lookup.shown, (found,))
        self.assertEqual(lookup.basis, "suspect-exact")
        self.assertEqual(lookup.detail, detail)

    def test_a_short_entry_equal_to_an_obscure_film_is_held(self) -> None:
        # The obscure-film error: an undated, zero-vote film's whole title.
        found = candidate("Ash", None, ident="1", votes=0)
        self._assert_held(
            _film_verdict([found], "Ash"),
            found,
            "one exact title, held: no release date; 0 votes, under the floor of 1",
        )

    def test_a_sequel_entry_equal_to_another_films_original_title_is_held(self) -> None:
        # The original-title error: dated and voted, and still another film.
        found = candidate(
            "The Tide Turns",
            1999,
            original_title="Moonrise Part 2",
            ident="2",
            votes=40,
        )
        self._assert_held(
            _film_verdict([found], "Moonrise Part 2"),
            found,
            "one exact title, held: matched on its original title only",
        )

    def test_a_dated_voted_title_match_stays_resolved(self) -> None:
        found = candidate(_TITLE, 2000, ident="3", votes=40)
        lookup = _film_verdict([found], _TITLE)
        self.assertEqual(
            (lookup.status, lookup.chosen, lookup.basis, lookup.detail),
            ("resolved", found, "exact", "one exact title"),
        )

    def test_the_floor_edges(self) -> None:
        settled = _film_verdict([candidate(_TITLE, 2000, votes=7)], _TITLE, floor=7)
        self.assertEqual(settled.status, "resolved")
        under = _film_verdict([candidate(_TITLE, 2000, votes=6)], _TITLE, floor=7)
        self.assertEqual(under.basis, "suspect-exact")
        unknown = _film_verdict([candidate(_TITLE, 2000)], _TITLE, floor=7)
        self.assertEqual(unknown.basis, "suspect-exact")
        off = _film_verdict([candidate(_TITLE, 2000, votes=0)], _TITLE, floor=0)
        self.assertEqual(off.status, "resolved")
        undated = _film_verdict([candidate(_TITLE, None, votes=0)], _TITLE, floor=0)
        self.assertEqual(undated.basis, "suspect-exact")

    def test_the_detail_carries_no_title(self) -> None:
        found = candidate("Elsewhere", None, original_title=_TITLE, votes=None)
        detail = _film_verdict([found], _TITLE).detail
        self.assertNotIn(_TITLE, detail)
        self.assertNotIn("Elsewhere", detail)

    def test_a_year_filter_runs_first_so_a_later_film_is_not_the_one_held(self) -> None:
        later = candidate(_TITLE, 2015, ident="4", votes=900)
        kept = candidate(_TITLE, 1999, ident="5", votes=0)
        self._assert_held(
            _film_verdict([later, kept], _TITLE),
            kept,
            "one exact title, held: 0 votes, under the floor of 1",
        )

    def test_most_voted_and_variant_are_not_held(self) -> None:
        top = candidate(_TITLE, None, ident="6", votes=500)
        low = candidate(_TITLE, None, ident="7", votes=3)
        self.assertEqual(_film_verdict([top, low], _TITLE).basis, "most-voted")
        numbered = candidate("Moonrise 2", None, ident="8", votes=0)
        self.assertEqual(_film_verdict([numbered], "Moonrise II").basis, "variant")

    def test_a_held_film_sends_no_second_search(self) -> None:
        # Its title opens with an article, so only the exact title stops the search.
        title = "The Moonrise"
        found = (candidate(title, None, ident="9", votes=0),)
        lookup = _film_verdict(found, title)
        self.assertEqual(lookup.basis, "suspect-exact")
        self.assertIsNone(
            resolve_match.research_query(
                lookup, found, title, title_key(title), work_year=None, date_year=2010
            )
        )

    def test_the_verdict_for_books_is_never_held(self) -> None:
        found = candidate(_TITLE, None, ident="10")
        lookup = resolve_match.verdict(
            [found], title_key(_TITLE), work_year=None, date_year=2010
        )
        self.assertEqual((lookup.status, lookup.chosen), ("resolved", found))

    @given(
        st.none() | st.integers(1800, 2010),
        st.none() | st.integers(0, 50),
        st.integers(0, 50),
        st.sampled_from(("", _TITLE, "Elsewhere")),
        st.sampled_from((_TITLE, "Elsewhere")),
    )
    def test_a_lone_exact_title_is_held_exactly_when_it_has_a_reason(
        self,
        year: int | None,
        votes: int | None,
        floor: int,
        original: str,
        title: str,
    ) -> None:
        found = candidate(title, year, original_title=original, votes=votes)
        if not resolve_match.exact([found], title_key(_TITLE)):
            return
        lookup = _film_verdict([found], _TITLE, floor)
        reasons = _reasons(found, floor=floor)
        self.assertEqual(lookup.status == "resolved", not reasons)
        self.assertEqual(lookup.basis == "suspect-exact", bool(reasons))
        for reason in reasons:
            self.assertIn(reason, lookup.detail)


class HeldRowsTest(RowsCase):
    """A held film through ``resolve_all``: its row, its entry and its requests."""

    def _held_pages(self) -> dict[str, object]:
        return _pages(_TITLE, tmdb_result(11, _TITLE, vote_count=0))

    def test_the_row_has_no_ids_and_the_entry_lists_the_one_candidate(self) -> None:
        rows = [input_row(_TITLE, medium="film")]
        out, review = self.run_all(rows, pages=self._held_pages())
        self.assertEqual(self.status(out[0]), ("ambiguous", "", "", ""))
        self.assertEqual(self.tmdb_titles(out[0]), ("", ""))
        (entry,) = review
        self.assertEqual((entry.kind, entry.error), ("suspect-exact", False))
        self.assertEqual([c.id for c in entry.candidates], ["11"])
        self.assertIn("no release date", entry.detail)
        self.assertIn("0 votes, under the floor of 1", entry.detail)
        self.assertEqual(len(self.fetch.calls), 1)

    def test_the_kind_comes_right_after_ambiguous(self) -> None:
        kinds = resolve_rows.REVIEW_KINDS
        self.assertEqual(kinds.index("suspect-exact"), kinds.index("ambiguous") + 1)
        self.assertNotIn("suspect-exact", resolve_rows.ERROR_KINDS)

    def test_the_floor_reaches_the_rule(self) -> None:
        rows = [input_row(_TITLE, medium="film")]
        pages = _pages(_TITLE, tmdb_result(11, _TITLE, release_date="2000-01-02"))
        out, review = self.run_all(rows, pages=pages, vote_floor=0)
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "11"))
        self.assertEqual(review, ())

    def test_directors_ambiguous_looks_up_the_listed_candidate(self) -> None:
        pages = {
            **self._held_pages(),
            resolve_films.credits_url("11"): tmdb_credits_payload(
                tmdb_crew("Invented Director")
            ),
        }
        rows = [input_row(_TITLE, medium="film")]
        for directors in ("ambiguous", "all"):
            with self.subTest(directors=directors):
                out, review = self.run_all(rows, pages=pages, directors=directors)
                self.assertEqual(review[0].candidates[0].creator, "Invented Director")
                self.assertEqual(self.status(out[0]), ("ambiguous", "", "", ""))
                self.assertEqual(out[0].cells["creator"], "")
                self.assertEqual(len(self.fetch.calls), 2)

    def test_directors_none_sends_no_credits_request(self) -> None:
        rows = [input_row(_TITLE, medium="film")]
        self.run_all(rows, pages=self._held_pages(), directors="none")
        self.assertEqual(len(self.fetch.calls), 1)

    def test_an_override_settles_it_before_any_lookup(self) -> None:
        overrides = overrides_of(f'medium = "film"\ntitle = "{_TITLE}"\ntmdb_id = "11"')
        pages = {resolve_films.details_url("11"): tmdb_result(11, _TITLE)}
        rows = [input_row(_TITLE, medium="film")]
        out, review = self.run_all(rows, overrides, pages=pages)
        self.assertEqual(self.status(out[0]), ("override", "", "", "11"))
        self.assertEqual([e.kind for e in review], ["override-applied"])
        self.assertEqual(self.fetch.calls, [resolve_films.details_url("11")])

    def test_a_re_searched_film_is_not_held(self) -> None:
        title = "The Moonrise"
        film = tmdb_result(12, _TITLE, vote_count=0)
        pages = {
            **_pages(title, tmdb_result(13, "Elsewhere", vote_count=0)),
            **_pages(_TITLE, film),
        }
        out, review = self.run_all([input_row(title, medium="film")], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "12"))
        self.assertEqual([e.kind for e in review], ["re-searched"])

    def test_a_book_with_no_year_is_not_held(self) -> None:
        work = ol_work(
            key="/works/OL1W", title=_TITLE, year=None, editions=(ol_edition(ISBN),)
        )
        pages = {ol_url(_TITLE, STEP1): ol_payload(work)}
        out, review = self.run_all([input_row(_TITLE)], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", ISBN, "/works/OL1W", ""))
        self.assertEqual(review, ())


def _not_a_repo(argv: Sequence[str], cwd: Path, timeout: float) -> int:
    """A git stand-in answering every question as outside any repository."""
    return _NOT_A_REPO


class VoteFloorCliTest(unittest.TestCase):
    """The option, and a held film end to end through the written output."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _main(self, *extra: str) -> int:
        """Run the CLI on one film row whose one exact title has 3 votes and a date."""
        row = parse_row(medium="film", title=_TITLE, source_file="10/seen.txt")
        parsed = write_parsed(self.base / "parsed", {"film-2010.csv": csv_text([row])})
        self.out = self.base / "out"
        pages = _pages(
            _TITLE, tmdb_result(21, _TITLE, release_date="2000-01-02", vote_count=3)
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        home = str(self.base / "home")
        environ = {"RETROLOG_TMDB_TOKEN": HTTP_TOKEN, "HOME": home, "USERPROFILE": home}
        with (
            mock.patch.dict(os.environ, environ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = retrolog_resolve.main(
                [
                    "--parsed",
                    str(parsed),
                    "--out",
                    str(self.out),
                    "--directors",
                    "none",
                    *extra,
                ],
                fetch=Answering(pages),
                run_git=_not_a_repo,
                sleep=lambda seconds: None,
            )
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def _read(self, name: str) -> str:
        return (self.out / name).read_text(encoding="utf-8")

    def test_a_held_film_is_written_and_exits_0(self) -> None:
        self.assertEqual(self._main("--vote-floor", "4"), _EXIT_OK, self.stderr)
        self.assertEqual(self.stderr, "")
        self.assertIn(",ambiguous,,,,,\n", self._read("film-2010.csv"))
        lines = self.stdout.splitlines()
        self.assertIn("status ambiguous: 1", lines)
        self.assertIn("review suspect-exact: 1", lines)
        self.assertNotIn("review ambiguous: 1", lines)
        document = json.loads(self._read("review.json"))
        self.assertEqual(document["counts"]["suspect-exact"], 1)
        (entry,) = document["entries"]
        self.assertEqual((entry["kind"], entry["error"]), ("suspect-exact", False))
        self.assertEqual(
            entry["detail"], "one exact title, held: 3 votes, under the floor of 4"
        )
        self.assertEqual([c["id"] for c in entry["candidates"]], ["21"])
        markdown = self._read("review.md")
        self.assertIn("## suspect-exact (1)", markdown)
        self.assertIn("1 review item, 0 of them errors.", markdown)

    def test_the_default_floor_settles_a_voted_film(self) -> None:
        self.assertEqual(self._main(), _EXIT_OK, self.stderr)
        self.assertIn(",resolved,,,21,", self._read("film-2010.csv"))

    def test_a_bad_floor_is_refused_with_the_rule(self) -> None:
        for value in ("-1", "1.5", "x", "", "9" * 5000):
            with self.subTest(value=value[:8]):
                argv = ["--parsed", "in", "--out", "out", f"--vote-floor={value}"]
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as raised:
                        retrolog_resolve.build_parser().parse_args(argv)
                self.assertEqual(raised.exception.code, _EXIT_USAGE)
                self.assertIn("--vote-floor", stderr.getvalue())
                self.assertIn("a whole number of 0 or more", stderr.getvalue())
                self.assertNotIn("vote_floor", stderr.getvalue())

    def test_help_shows_the_live_default(self) -> None:
        parser = retrolog_resolve.build_parser()
        action = next(a for a in parser._actions if "--vote-floor" in a.option_strings)
        self.assertEqual(action.default, 1)
        self.assertIn(
            f"(default: {action.default})", " ".join(parser.format_help().split())
        )

    def test_a_timeout_that_is_no_number_is_refused_with_the_rule(self) -> None:
        # --timeout's argparse type states its rule, as the others do.
        argv = ["--parsed", "in", "--out", "out", "--timeout=x"]
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as raised:
                retrolog_resolve.build_parser().parse_args(argv)
        self.assertEqual(raised.exception.code, _EXIT_USAGE)
        self.assertIn("must be a number greater than 0", stderr.getvalue())

    def test_zero_is_accepted(self) -> None:
        args = retrolog_resolve.build_parser().parse_args(
            ["--parsed", "in", "--out", "out", "--vote-floor", "0"]
        )
        self.assertEqual(args.vote_floor, 0)


if __name__ == "__main__":
    unittest.main()
