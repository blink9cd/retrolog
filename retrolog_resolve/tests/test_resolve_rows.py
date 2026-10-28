"""Tests for resolve_rows' ordering rules: overrides, grouping, and every row kept.

Every request goes to ``resolve_fixtures.Answering``, which answers only the URLs a
test lists, so each test also pins exactly which requests a run makes: an unlisted
one fails the test. Each test class is one of ``resolve_all``'s ordering rules; the
lookups themselves (rule 4) are in ``test_resolve_lookups``.
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
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    EMPTY_ANSWER,
    input_row,
    ol_edition,
    ol_payload,
    ol_work,
    tmdb_result,
    tmdb_search_payload,
)
from resolve_rows_case import (  # noqa: E402 - after the sys.path inserts above
    EMPTY_OL,
    EMPTY_TMDB,
    ISBN,
    STEP1,
    STEP2,
    STEP3,
    RowsCase,
    kinds,
    ol_url,
    overrides_of,
)
from resolve_types import InputRow  # noqa: E402 - after the sys.path inserts above


class VocabularyTest(unittest.TestCase):
    def test_error_kinds_lead_the_reviewkinds(self) -> None:
        errors = resolve_rows.REVIEW_KINDS[: len(resolve_rows.ERROR_KINDS)]
        self.assertEqual(set(errors), resolve_rows.ERROR_KINDS)

    def test_resolve_columns(self) -> None:
        self.assertEqual(
            resolve_rows.RESOLVE_COLUMNS,
            (
                "resolve_status",
                "isbn13",
                "ol_work",
                "tmdb_id",
                "tmdb_title",
                "tmdb_original_title",
            ),
        )


class LineOverrideTest(RowsCase):
    """Rule 1: an override naming a file and line settles that row alone."""

    _FILM = 'medium = "film"\ntitle = "Metropolis"\nfile = "10/seen.txt"\nline = 7\n'

    def test_a_line_override_is_applied_with_no_request(self) -> None:
        row = input_row("Metropolis", medium="film", source_file="10/seen.txt", line=7)
        rows, review = self.run_all([row], overrides_of(self._FILM + 'tmdb_id = "9"'))
        self.assertEqual(self.status(rows[0]), ("override", "", "", "9"))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(kinds(review), [("override-applied", "Metropolis")])
        self.assertEqual(review[0].rows, ("10/seen.txt:7",))
        self.assertFalse(review[0].error)

    def test_a_different_title_on_that_line_is_a_mismatch(self) -> None:
        row = input_row("Other Film", medium="film", source_file="10/seen.txt", line=7)
        pages = {resolve_films.search_url("Other Film"): EMPTY_TMDB}
        rows, review = self.run_all(
            [row], overrides_of(self._FILM + 'tmdb_id = "9"'), pages
        )
        self.assertEqual(self.status(rows[0]), ("not-found", "", "", ""))
        mismatch = review[0]
        self.assertEqual((mismatch.kind, mismatch.error), ("override-mismatch", True))
        self.assertEqual(
            (mismatch.title, mismatch.rows), ("Other Film", ("10/seen.txt:7",))
        )
        self.assertIn("overrides[0]", mismatch.detail)
        self.assertNotIn("override-unused", [entry.kind for entry in review])

    def test_another_medium_on_that_line_is_a_mismatch(self) -> None:
        row = input_row("Metropolis", source_file="10/seen.txt", line=7)
        pages = {ol_url("Metropolis", step): EMPTY_OL for step in (STEP1, STEP2, STEP3)}
        _, review = self.run_all(
            [row], overrides_of(self._FILM + 'tmdb_id = "9"'), pages
        )
        self.assertEqual(review[0].kind, "override-mismatch")

    def test_the_other_entry_sharing_a_word_line_is_not_a_mismatch(self) -> None:
        rows = [
            input_row(title, medium="film", source_file="10/seen.txt", line=7)
            for title in ("Other Film", "Metropolis")
        ]
        pages = {resolve_films.search_url("Other Film"): EMPTY_TMDB}
        out, review = self.run_all(
            rows, overrides_of(self._FILM + 'tmdb_id = "9"'), pages
        )
        self.assertEqual(
            [r.cells["resolve_status"] for r in out], ["not-found", "override"]
        )
        self.assertEqual(
            kinds(review),
            [("not-found", "Other Film"), ("override-applied", "Metropolis")],
        )

    def test_a_line_override_beats_a_title_override(self) -> None:
        rows = [
            input_row("Metropolis", medium="film", source_file="10/seen.txt", line=7),
            input_row("Metropolis", medium="film", source_file="10/seen.txt", line=8),
        ]
        overrides = overrides_of(
            self._FILM + 'tmdb_id = "9"',
            'medium = "film"\ntitle = "Metropolis"\ntmdb_id = "8"',
        )
        out, review = self.run_all(rows, overrides)
        self.assertEqual([r.cells["tmdb_id"] for r in out], ["9", "8"])
        self.assertEqual(
            [(e.kind, e.rows) for e in review],
            [
                ("override-applied", ("10/seen.txt:7",)),
                ("override-applied", ("10/seen.txt:8",)),
            ],
        )

    def test_a_line_override_on_an_empty_line_is_unused(self) -> None:
        _, review = self.run_all([], overrides_of(self._FILM + "unresolvable = true"))
        unused = review[0]
        self.assertEqual((unused.kind, unused.error), ("override-unused", True))
        self.assertEqual((unused.medium, unused.rows), ("film", ("10/seen.txt:7",)))
        self.assertIn("overrides[0]", unused.detail)


class GroupingTest(RowsCase):
    """Rule 2: one lookup per (medium, title_key, work year, date year)."""

    def test_rows_of_one_title_and_year_share_one_lookup(self) -> None:
        rows = [
            input_row("Some Film", medium="film", line=1),
            input_row("some film!", medium="film", line=2),
        ]
        pages = {resolve_films.search_url("Some Film"): EMPTY_TMDB}
        out, review = self.run_all(rows, pages=pages)
        self.assertEqual(len(self.fetch.calls), 1)
        self.assertEqual([r.cells["resolve_status"] for r in out], ["not-found"] * 2)
        self.assertEqual(review[0].rows, ("10/read.txt:1", "10/read.txt:2"))

    def test_a_rewatch_in_another_year_is_bounded_by_its_own_year(self) -> None:
        rows = [
            input_row("Some Film", medium="film", date="2010-01-01", line=1),
            input_row("Some Film", medium="film", date="2012-01-01", line=2),
        ]
        found = tmdb_search_payload(
            tmdb_result(5, "Some Film", release_date="2011-05-05")
        )
        with tempfile.TemporaryDirectory() as cache:
            out, _ = self.run_all(
                rows,
                pages={resolve_films.search_url("Some Film"): found},
                cache=Path(cache),
            )
        # The second lookup's URL repeats, so the cache answers it.
        self.assertEqual(len(self.fetch.calls), 1)
        self.assertEqual(
            [self.status(r) for r in out],
            [("not-found", "", "", ""), ("resolved", "", "", "5")],
        )

    def test_every_row_comes_out_in_input_order(self) -> None:
        rows = [
            input_row("B Film", medium="film", line=1),
            input_row("A Film", medium="film", line=2),
            input_row("B Film", medium="film", line=3),
        ]
        # "A Film" opens with an article, so the re-search would send it again: off here,
        # where the count is of one search per group.
        out, _ = self.run_all(rows, default=EMPTY_ANSWER, research=False)
        self.assertEqual([r.cells["source_line"] for r in out], ["1", "2", "3"])
        self.assertEqual(len(self.fetch.calls), 2)


class TitleOverrideTest(RowsCase):
    """Rule 3: a title-only override decides every group of its title, unasked."""

    def test_a_title_override_sets_the_ids_with_no_request(self) -> None:
        rows = [
            input_row("Moby Dick", date="2010-01-01", line=1),
            input_row("moby  dick", date="2012-01-01", line=2),
        ]
        overrides = overrides_of(
            f'medium = "book"\ntitle = "Moby Dick"\nisbn13 = "{ISBN}"\nol_work = "/works/OL5W"'
        )
        out, review = self.run_all(rows, overrides)
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(
            [self.status(r) for r in out], [("override", ISBN, "/works/OL5W", "")] * 2
        )
        # One entry per override, covering every row it settled.
        self.assertEqual(kinds(review), [("override-applied", "Moby Dick")])
        self.assertEqual(review[0].rows, ("10/read.txt:1", "10/read.txt:2"))
        self.assertIn("isbn13", review[0].detail)

    def test_unresolvable_is_an_override_with_no_id(self) -> None:
        row = input_row("Moby Dick")
        overrides = overrides_of(
            'medium = "book"\ntitle = "Moby Dick"\nunresolvable = true'
        )
        out, review = self.run_all([row], overrides)
        self.assertEqual(self.status(out[0]), ("override", "", "", ""))
        self.assertIn("unresolvable", review[0].detail)

    def test_a_title_override_of_the_other_medium_does_not_apply(self) -> None:
        row = input_row("Metropolis", medium="film")
        overrides = overrides_of(
            'medium = "book"\ntitle = "Metropolis"\nunresolvable = true'
        )
        pages = {resolve_films.search_url("Metropolis"): EMPTY_TMDB}
        _, review = self.run_all([row], overrides, pages)
        self.assertEqual(
            kinds(review),
            [("not-found", "Metropolis"), ("override-unused", "metropolis")],
        )


class UnusedOverrideTest(RowsCase):
    """Rule 5: an override that settled no row is an error."""

    def test_a_title_override_with_no_row_is_unused(self) -> None:
        overrides = overrides_of('medium = "film"\ntitle = "Gone Film"\ntmdb_id = "3"')
        _, review = self.run_all([], overrides)
        self.assertEqual(
            [(e.kind, e.rows, e.error) for e in review], [("override-unused", (), True)]
        )
        self.assertEqual(
            review[0].detail, "overrides[0]: no row has this medium and title"
        )

    def test_a_title_override_every_row_of_which_has_a_line_override_is_unused(
        self,
    ) -> None:
        row = input_row("Metropolis", medium="film", source_file="10/seen.txt", line=7)
        overrides = overrides_of(
            LineOverrideTest._FILM + 'tmdb_id = "9"',
            'medium = "film"\ntitle = "Metropolis"\ntmdb_id = "8"',
        )
        _, review = self.run_all([row], overrides)
        self.assertEqual(
            [entry.kind for entry in review], ["override-applied", "override-unused"]
        )
        self.assertIn("overrides[1]", review[1].detail)
        self.assertEqual(
            review[1].detail,
            "overrides[1]: every row with this title is settled by a line override",
        )
        self.assertEqual(review[1].rows, ())


class TmdbTitleTest(RowsCase):
    """A resolved film's row carries the chosen candidate's TMDb titles.

    Export matches the account on them as well as on the list's title, so a film
    resolve settled under another title is not sent over a live rating.
    """

    def test_a_resolved_film_carries_both_tmdb_titles(self) -> None:
        url = resolve_films.search_url("Metropolis")
        found = tmdb_result(19, "Metropolis", original_title="Metropolis (Original)")
        pages = {url: tmdb_search_payload(found)}
        out, _ = self.run_all([input_row("Metropolis", medium="film")], pages=pages)
        self.assertEqual(
            self.tmdb_titles(out[0]), ("Metropolis", "Metropolis (Original)")
        )

    def test_a_variant_match_carries_the_candidates_own_title(self) -> None:
        url = resolve_films.search_url("Placeholder Story II")
        pages = {url: tmdb_search_payload(tmdb_result(7, "Placeholder Story 2"))}
        row = input_row("Placeholder Story II", medium="film")
        out, _ = self.run_all([row], pages=pages)
        self.assertEqual(self.status(out[0]), ("resolved", "", "", "7"))
        self.assertEqual(
            self.tmdb_titles(out[0]), ("Placeholder Story 2", "Placeholder Story 2")
        )

    def test_an_override_leaves_both_empty(self) -> None:
        override = 'medium = "film"\ntitle = "Metropolis"\ntmdb_id = "9"'
        row = input_row("Metropolis", medium="film")
        out, _ = self.run_all([row], overrides_of(override))
        self.assertEqual(self.status(out[0]), ("override", "", "", "9"))
        self.assertEqual(self.tmdb_titles(out[0]), ("", ""))

    def test_an_unresolved_film_leaves_both_empty(self) -> None:
        pages = {resolve_films.search_url("Metropolis"): EMPTY_TMDB}
        out, _ = self.run_all([input_row("Metropolis", medium="film")], pages=pages)
        self.assertEqual(self.status(out[0])[0], "not-found")
        self.assertEqual(self.tmdb_titles(out[0]), ("", ""))

    def test_a_resolved_book_leaves_both_empty(self) -> None:
        pages = {
            ol_url("Moby Dick", STEP1): ol_payload(
                ol_work(editions=(ol_edition(ISBN),))
            )
        }
        out, _ = self.run_all([input_row("Moby Dick")], pages=pages)
        self.assertEqual(self.status(out[0])[0], "resolved")
        self.assertEqual(self.tmdb_titles(out[0]), ("", ""))


_TITLES = st.sampled_from(
    ["Moby Dick", "Metropolis", "Placeholder One", "placeholder one!"]
)
_ROWS = st.lists(
    st.builds(
        input_row,
        _TITLES,
        medium=st.sampled_from(["book", "film"]),
        work_year=st.one_of(st.none(), st.integers(1900, 2020)),
        date=st.sampled_from(["", "2010-01-01", "2012-06-30"]),
        line=st.integers(1, 5),
    ),
    max_size=8,
)


class PropertyTest(RowsCase):
    @settings(max_examples=60, deadline=None)
    @given(_ROWS)
    def test_fuzz_resolve_all(self, rows: list[InputRow]) -> None:
        """Every input row comes out once, in order, its parse cells unchanged."""
        out, review = self.run_all(rows, default=EMPTY_ANSWER)
        self.assertEqual(len(out), len(rows))
        for row, written in zip(rows, out, strict=True):
            self.assertEqual(written.source_csv, row.source_csv)
            self.assertEqual({k: written.cells[k] for k in row.cells}, dict(row.cells))
            self.assertEqual(written.cells["resolve_status"], "not-found")
        self.assertTrue(all(entry.kind == "not-found" for entry in review))


if __name__ == "__main__":
    unittest.main()
