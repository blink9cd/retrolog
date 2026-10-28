"""Tests for export_select: the cutoff, the held films, the account diff and the order."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The same order as retrolog_export.SOURCE_DIRS: the tool's own src/ first.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
    str(_TOOL),
]
import export_account  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
from export_types import (  # noqa: E402 - after the sys.path inserts above
    Cutoff,
    FilmKey,
    ReviewEntry,
)
from select_case import (  # noqa: E402 - after the sys.path inserts above
    METROPOLIS as _METROPOLIS,
)
from select_case import day as _day  # noqa: E402 - after the sys.path inserts above
from select_case import kinds as _kinds  # noqa: E402 - after the sys.path inserts
from select_case import make_row as _row  # noqa: E402 - after the sys.path inserts
from select_case import select as _select  # noqa: E402 - after the sys.path inserts
from select_case import sent as _sent  # noqa: E402 - after the sys.path inserts above


class KindsTest(unittest.TestCase):
    def test_review_and_error_kinds(self) -> None:
        self.assertEqual(
            export_select.REVIEW_KINDS,
            (
                "held-ambiguous",
                "held-failed",
                "held-split",
                "held-no-title",
                "on-service",
                "override-unchecked",
                "by-title",
                "unrated",
                "undated",
                "formula-cell",
            ),
        )
        self.assertEqual(
            export_select.ERROR_KINDS,
            frozenset({"held-ambiguous", "held-failed", "held-split", "held-no-title"}),
        )


class CutoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            _row(date=_day(1), source_line=1),
            _row(date=_day(2), source_line=2, **_METROPOLIS),
            _row(date=_day(3), source_line=3),
            _row(date=None, source_file="undated/seen.txt", source_line=4),
        ]

    def test_rows_at_and_after_the_cutoff_are_cut(self) -> None:
        cutoff = Cutoff("10/seen.txt", 2, "metropolis")
        chosen = _select(self.rows, cutoff=cutoff)
        self.assertEqual(chosen.cut, 2)
        self.assertEqual(_sent(chosen), ["undated/seen.txt:4", "10/seen.txt:1"])

    def test_cut_follows_date_then_file_then_line(self) -> None:
        rows = [
            _row(date=_day(5), source_file="11/seen.txt", source_line=1),
            _row(date=_day(5), source_file="10/seen.txt", source_line=9),
            _row(date=_day(5), source_file="10/seen.txt", source_line=3),
            _row(date=_day(4), source_file="12/seen.txt", source_line=1),
        ]
        cutoff = Cutoff("10/seen.txt", 9, "placeholder film one")
        chosen = _select(rows, cutoff=cutoff)
        self.assertEqual(chosen.cut, 2)
        self.assertEqual(_sent(chosen), ["12/seen.txt:1", "10/seen.txt:3"])

    def test_undated_rows_are_never_cut(self) -> None:
        cutoff = Cutoff("10/seen.txt", 1, "placeholder film one")
        chosen = _select(self.rows, cutoff=cutoff)
        self.assertEqual(chosen.cut, 3)
        self.assertEqual(_sent(chosen), ["undated/seen.txt:4"])
        self.assertEqual(_kinds(chosen), [("undated", ("undated/seen.txt:4",))])

    def test_cutoff_naming_another_title_is_an_error_and_cuts_nothing(self) -> None:
        cutoff = Cutoff("10/seen.txt", 2, "placeholder film one")
        with self.assertRaises(export_select.CutoffError) as caught:
            _select(self.rows, cutoff=cutoff)
        message = str(caught.exception)
        self.assertIn("letterboxd.cutoff", message)
        self.assertNotIn("10/seen.txt", message)
        self.assertNotIn("placeholder", message)

    def test_cutoff_naming_no_row_is_an_error(self) -> None:
        with self.assertRaises(export_select.CutoffError):
            _select(self.rows, cutoff=Cutoff("10/seen.txt", 99, "metropolis"))

    def test_cutoff_naming_an_undated_row_is_an_error(self) -> None:
        cutoff = Cutoff("undated/seen.txt", 4, "placeholder film one")
        with self.assertRaises(export_select.CutoffError):
            _select(self.rows, cutoff=cutoff)

    def test_no_cutoff_cuts_nothing(self) -> None:
        self.assertEqual(_select(self.rows).cut, 0)


class HoldTest(unittest.TestCase):
    def test_an_ambiguous_row_holds_its_whole_film(self) -> None:
        rows = [
            _row(tmdb_id="", status="not-found", source_line=1),
            _row(tmdb_id="", status="ambiguous", source_line=2),
            _row(source_line=3, **_METROPOLIS),
        ]
        chosen = _select(rows)
        self.assertEqual(_sent(chosen), ["10/seen.txt:3"])
        [entry] = chosen.review
        self.assertEqual(entry.kind, "held-ambiguous")
        self.assertEqual(entry.rows, ("10/seen.txt:1", "10/seen.txt:2"))
        self.assertEqual(entry.title, "Placeholder Film One")
        self.assertTrue(entry.error)

    def test_a_failed_row_holds_its_whole_film(self) -> None:
        rows = [_row(tmdb_id="", status="failed")]
        chosen = _select(rows)
        self.assertEqual(chosen.films, ())
        self.assertEqual(_kinds(chosen), [("held-failed", ("10/seen.txt:1",))])
        self.assertTrue(chosen.review[0].error)

    def test_a_film_holding_both_is_one_entry_named_by_its_first_problem(self) -> None:
        rows = [
            _row(tmdb_id="", status="failed", date=_day(9), source_line=1),
            _row(tmdb_id="", status="ambiguous", date=_day(2), source_line=2),
        ]
        chosen = _select(rows)
        self.assertEqual(
            _kinds(chosen),
            [("held-ambiguous", ("10/seen.txt:1", "10/seen.txt:2"))],
        )

    def test_a_held_film_is_not_checked_against_the_account(self) -> None:
        rows = [_row(tmdb_id="", status="ambiguous")]
        account = frozenset({FilmKey("placeholder film one", 1999)})
        chosen = _select(rows, account=account)
        self.assertEqual([entry.kind for entry in chosen.review], ["held-ambiguous"])

    def test_a_sent_film_sharing_a_held_films_title_and_year_is_held(self) -> None:
        # Regression: an override row with an id and an ambiguous row of the same title
        # and year are two films. Sending the first put the title on the account, so
        # the held row was skipped as on-service once resolve settled it.
        rows = [
            _row(status="override", tmdb_id="123", source_line=1),
            _row(status="ambiguous", tmdb_id="", source_line=2),
        ]
        chosen = _select(rows)
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            _kinds(chosen),
            [
                ("held-ambiguous", ("10/seen.txt:1",)),
                ("held-ambiguous", ("10/seen.txt:2",)),
            ],
        )
        self.assertTrue(all(entry.error for entry in chosen.review))
        self.assertIn("another film of this title", chosen.review[0].detail)

    def test_a_film_held_by_a_failed_film_takes_its_kind(self) -> None:
        rows = [
            _row(status="failed", tmdb_id="", source_line=1),
            _row(source_line=2, year=None),
        ]
        chosen = _select(rows)
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            [entry.kind for entry in chosen.review], ["held-failed", "held-failed"]
        )

    def test_a_film_held_with_another_passes_the_hold_on(self) -> None:
        # Regression: the shared hold was not transitive. Rows 2 and 3 share a tmdbID
        # and are held through row 1's title; row 4 shares row 3's title. Sending row 4
        # put that title on the account, and once row 1 settled to the same id the
        # merged film of rows 1 to 3 was skipped as on-service: their watches were lost.
        rows = [
            _row(status="ambiguous", tmdb_id="", date=_day(1), source_line=1),
            _row(tmdb_id="5", date=_day(2), source_line=2),
            _row(
                tmdb_id="5",
                date=_day(3),
                source_line=3,
                title="Metropolis",
                title_key="metropolis",
                year=1927,
            ),
            _row(
                status="not-found",
                tmdb_id="",
                date=_day(4),
                source_line=4,
                title="Metropolis",
                title_key="metropolis",
                year=1927,
            ),
        ]
        chosen = export_select.select(
            rows,
            cutoff=None,
            account=frozenset(),
            on_service=export_account.on_service,
        )
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            _kinds(chosen),
            [
                ("held-ambiguous", ("10/seen.txt:1",)),
                ("held-ambiguous", ("10/seen.txt:2", "10/seen.txt:3")),
                ("held-ambiguous", ("10/seen.txt:4",)),
            ],
        )
        self.assertTrue(all(entry.error for entry in chosen.review))

    def test_a_film_of_another_title_is_not_held(self) -> None:
        rows = [
            _row(status="ambiguous", tmdb_id="", source_line=1),
            _row(source_line=2, **_METROPOLIS),
        ]
        self.assertEqual(_sent(_select(rows)), ["10/seen.txt:2"])


class AccountTest(unittest.TestCase):
    def test_a_film_on_the_account_is_skipped_whole(self) -> None:
        rows = [
            _row(date=_day(1), source_line=1),
            _row(date=_day(2), source_line=2, rewatch=True),
            _row(date=_day(3), source_line=3, **_METROPOLIS),
        ]
        account = frozenset({FilmKey("placeholder film one", 1999)})
        chosen = _select(rows, account=account)
        self.assertEqual(_sent(chosen), ["10/seen.txt:3"])
        [entry] = chosen.review
        self.assertEqual(entry.kind, "on-service")
        self.assertEqual(entry.rows, ("10/seen.txt:1", "10/seen.txt:2"))
        self.assertFalse(entry.error)
        self.assertNotIn("year", entry.detail)

    def test_an_account_entry_without_a_year_says_so(self) -> None:
        account = frozenset({FilmKey("placeholder film one", None)})
        chosen = _select([_row()], account=account)
        [entry] = chosen.review
        self.assertEqual(entry.kind, "on-service")
        self.assertIn("no year", entry.detail)

    def test_a_film_is_on_the_account_by_any_of_its_rows(self) -> None:
        # Regression: only the first row was asked, so a film whose later row carries
        # the title the account knows it by (an original title, an override) was sent.
        rows = [
            _row(date=_day(1), source_line=1),
            _row(
                date=_day(2), source_line=2, title="Metropolis", title_key="metropolis"
            ),
        ]
        account = frozenset({FilmKey("metropolis", None)})
        chosen = _select(rows, account=account)
        self.assertEqual(chosen.films, ())
        [entry] = chosen.review
        self.assertEqual(entry.kind, "on-service")
        self.assertEqual(entry.rows, ("10/seen.txt:1", "10/seen.txt:2"))
        self.assertIn("no year", entry.detail)

    def test_on_service_is_asked_with_the_film_key_and_account(self) -> None:
        asked: list[tuple[FilmKey, frozenset[FilmKey]]] = []
        account = frozenset({FilmKey("other", 2001)})

        def record(key: FilmKey, given_account: frozenset[FilmKey]) -> bool:
            asked.append((key, given_account))
            return False

        export_select.select(
            [_row(year=None)], cutoff=None, account=account, on_service=record
        )
        self.assertEqual(asked, [(FilmKey("placeholder film one", None), account)])


class SentTest(unittest.TestCase):
    def test_not_found_is_sent_and_listed_by_title(self) -> None:
        chosen = _select([_row(tmdb_id="", status="not-found")])
        self.assertEqual(_sent(chosen), ["10/seen.txt:1"])
        self.assertEqual(chosen.films[0].key, "title:placeholder film one:1999")
        self.assertEqual(_kinds(chosen), [("by-title", ("10/seen.txt:1",))])
        self.assertFalse(chosen.review[0].error)

    def test_an_override_with_no_id_is_listed_by_title(self) -> None:
        chosen = _select([_row(tmdb_id="", status="override", year=None)])
        self.assertEqual(chosen.films[0].key, "title:placeholder film one:")
        self.assertEqual(_kinds(chosen), [("by-title", ("10/seen.txt:1",))])

    def test_a_resolved_film_is_keyed_by_its_id_and_not_listed(self) -> None:
        # The override row carries a TMDb title, so it is not listed as unchecked.
        override = _row(
            status="override", tmdb_title_keys=("placeholder film one",), source_line=2
        )
        chosen = _select([_row(), override])
        self.assertEqual([film.key for film in chosen.films], ["tmdb:1000001"])
        self.assertEqual(chosen.review, ())

    def test_an_unrated_row_lists_its_film(self) -> None:
        rows = [_row(source_line=1), _row(rating_raw="", source_line=2)]
        chosen = _select(rows)
        self.assertEqual(
            _kinds(chosen), [("unrated", ("10/seen.txt:1", "10/seen.txt:2"))]
        )

    def test_a_film_listed_three_ways_has_three_entries(self) -> None:
        rows = [_row(tmdb_id="", status="not-found", rating_raw="", date=None)]
        chosen = _select(rows)
        self.assertEqual(
            [entry.kind for entry in chosen.review], ["by-title", "unrated", "undated"]
        )
        self.assertTrue(all(entry.detail for entry in chosen.review))


class FormulaCellTest(unittest.TestCase):
    """A sent row whose checked cell a spreadsheet reads as a formula is listed."""

    _COLUMNS = ("Title", "Directors", "Review")

    def _entries(self, *cells: tuple[str, ...]) -> list[ReviewEntry]:
        """``formula_entries`` for one film of ``len(cells)`` rows with ``cells``."""
        rows = tuple(_row(source_line=line) for line in range(1, len(cells) + 1))
        film = export_select.Film("tmdb:1000001", rows)
        checked = {name: at for at, name in enumerate(self._COLUMNS)}
        return export_select.formula_entries([film], [list(cells)], checked)

    def test_each_formula_start_is_listed(self) -> None:
        for start in ("=", "+", "-", "@", "\t", "\r"):
            with self.subTest(start=start):
                [entry] = self._entries((f"{start}x", "", ""))
                self.assertEqual(entry.kind, "formula-cell")
                self.assertFalse(entry.error)
                self.assertEqual(entry.rows, ("10/seen.txt:1",))
                self.assertIn("Title", entry.detail)
                self.assertNotIn(f"{start}x", entry.detail)

    def test_only_the_rows_with_one_are_listed_naming_each_column(self) -> None:
        [entry] = self._entries(("ok", "", ""), ("ok", "-x", "@y"), ("ok", "", ""))
        self.assertEqual(entry.rows, ("10/seen.txt:2",))
        self.assertIn("Directors, Review", entry.detail)

    def test_a_formula_character_inside_a_cell_is_not_listed(self) -> None:
        self.assertEqual(self._entries(("a=b", "x-y", " @z")), [])


class OrderTest(unittest.TestCase):
    def test_rewatch_rows_stay_together_oldest_first(self) -> None:
        rows = [
            _row(date=_day(9), source_line=1, rewatch=True),
            _row(date=_day(5), source_line=2, **_METROPOLIS),
            _row(date=_day(1), source_file="11/seen.txt", source_line=7),
        ]
        chosen = _select(rows)
        self.assertEqual(
            [[row.where for row in film.rows] for film in chosen.films],
            [["11/seen.txt:7", "10/seen.txt:1"], ["10/seen.txt:2"]],
        )

    def test_undated_row_sorts_before_dated_rows_of_its_film(self) -> None:
        rows = [
            _row(date=_day(1), source_line=1),
            _row(date=None, source_file="undated/seen.txt", source_line=2),
        ]
        chosen = _select(rows)
        self.assertEqual(_sent(chosen), ["undated/seen.txt:2", "10/seen.txt:1"])

    def test_same_date_rows_sort_by_file_then_line(self) -> None:
        rows = [
            _row(source_file="11/seen.txt", source_line=1),
            _row(source_line=8),
            _row(source_line=3),
        ]
        chosen = _select(rows)
        self.assertEqual(
            _sent(chosen), ["10/seen.txt:3", "10/seen.txt:8", "11/seen.txt:1"]
        )

    def test_review_entries_follow_the_film_order(self) -> None:
        rows = [
            _row(tmdb_id="", status="not-found", date=_day(9), source_line=1),
            _row(
                tmdb_id="",
                status="failed",
                date=_day(2),
                source_line=2,
                title="Metropolis",
                title_key="metropolis",
                year=1927,
            ),
        ]
        chosen = _select(rows)
        self.assertEqual(
            [entry.kind for entry in chosen.review], ["held-failed", "by-title"]
        )

    def test_rows_without_an_id_group_by_title_and_year(self) -> None:
        rows = [
            _row(tmdb_id="", status="not-found", source_line=1),
            _row(tmdb_id="", status="not-found", source_line=2, year=2005),
            _row(tmdb_id="", status="not-found", source_line=3),
        ]
        chosen = _select(rows)
        self.assertEqual(
            [film.key for film in chosen.films],
            ["title:placeholder film one:1999", "title:placeholder film one:2005"],
        )
        self.assertEqual(len(chosen.films[0].rows), 2)


if __name__ == "__main__":
    unittest.main()
