"""Tests for export_select's account classes: what each account file decides.

A film on the account only as watched is sent in full; one the owner logged (a diary
entry, a rating, or a review with no ``retrolog`` tag) is sent without stars, less any
row on a diary entry's date; one an earlier import tagged is skipped whole.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path
from types import MappingProxyType

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
import export_select  # noqa: E402 - after the sys.path inserts above
from export_types import Account, FilmKey  # noqa: E402 - after the sys.path inserts
from select_case import METROPOLIS  # noqa: E402 - after the sys.path inserts above
from select_case import day, kinds, make_row, select, sent  # noqa: E402

_ONE = FilmKey("placeholder film one", 1999)
_OTHER = FilmKey("metropolis", 1927)
_ROWS = [
    make_row(date=day(1), source_line=1),
    make_row(date=day(2), source_line=2, rewatch=True),
]
_BOTH = ("10/seen.txt:1", "10/seen.txt:2")


def _diary(*dated: tuple[datetime.date, FilmKey]) -> MappingProxyType:
    """``Account.diary_dates`` for diary entries of ``(date, key)``."""
    by_date: dict[datetime.date, set[FilmKey]] = {}
    for date, key in dated:
        by_date.setdefault(date, set()).add(key)
    return MappingProxyType({date: frozenset(keys) for date, keys in by_date.items()})


def _logged(*dated: tuple[datetime.date, FilmKey]) -> Account:
    """An account whose diary logs Placeholder Film One, untagged, on ``dated``."""
    return Account(logged=frozenset({_ONE}), diary_dates=_diary(*dated))


class KindsTest(unittest.TestCase):
    def test_the_new_kinds_follow_on_service_and_are_not_errors(self) -> None:
        kinds_ = export_select.REVIEW_KINDS
        at = kinds_.index("on-service")
        self.assertEqual(
            kinds_[at : at + 4],
            ("on-service", "same-date", "logged-no-stars", "watched-only"),
        )
        for kind in ("same-date", "logged-no-stars", "watched-only"):
            self.assertNotIn(kind, export_select.ERROR_KINDS)


class NotOnAccountTest(unittest.TestCase):
    def test_a_film_on_no_account_file_is_sent_with_its_stars(self) -> None:
        chosen = select(_ROWS, account=Account(watched=frozenset({_OTHER})))
        self.assertEqual(sent(chosen), list(_BOTH))
        [film] = chosen.films
        self.assertFalse(film.withhold_rating)
        self.assertEqual(kinds(chosen), [])


class WatchedOnlyTest(unittest.TestCase):
    def test_a_film_only_watched_is_sent_in_full_and_listed(self) -> None:
        chosen = select(_ROWS, account=Account(watched=frozenset({_ONE})))
        self.assertEqual(sent(chosen), list(_BOTH))
        [film] = chosen.films
        self.assertFalse(film.withhold_rating)
        [entry] = chosen.review
        self.assertEqual((entry.kind, entry.rows), ("watched-only", _BOTH))
        self.assertFalse(entry.error)
        self.assertIn("watched only", entry.detail)

    def test_its_other_entries_follow(self) -> None:
        row = make_row(rating_raw="", date=None, source_file="u/seen.txt")
        chosen = select([row], account=Account(watched=frozenset({_ONE})))
        self.assertEqual(
            [kind for kind, _ in kinds(chosen)], ["watched-only", "unrated", "undated"]
        )


class LoggedTest(unittest.TestCase):
    def test_a_ratings_only_match_is_sent_without_stars(self) -> None:
        chosen = select(_ROWS, account=Account(logged=frozenset({_ONE})))
        self.assertEqual(sent(chosen), list(_BOTH))
        [film] = chosen.films
        self.assertTrue(film.withhold_rating)
        [entry] = chosen.review
        self.assertEqual((entry.kind, entry.rows), ("logged-no-stars", _BOTH))
        self.assertFalse(entry.error)
        self.assertIn("without stars", entry.detail)

    def test_watched_and_logged_is_logged(self) -> None:
        account = Account(watched=frozenset({_ONE}), logged=frozenset({_ONE}))
        chosen = select(_ROWS, account=account)
        self.assertEqual(kinds(chosen), [("logged-no-stars", _BOTH)])
        self.assertTrue(chosen.films[0].withhold_rating)

    def test_an_unrated_row_is_still_listed_unrated(self) -> None:
        chosen = select(
            [make_row(rating_raw="")], account=Account(logged=frozenset({_ONE}))
        )
        self.assertEqual(
            [kind for kind, _ in kinds(chosen)], ["logged-no-stars", "unrated"]
        )

    def test_a_film_sent_alongside_keeps_its_stars(self) -> None:
        rows = [*_ROWS, make_row(date=day(3), source_line=3, **METROPOLIS)]
        chosen = select(rows, account=Account(logged=frozenset({_ONE})))
        self.assertEqual([film.withhold_rating for film in chosen.films], [True, False])


class ImportedTest(unittest.TestCase):
    def test_a_film_tagged_by_an_import_is_skipped_whole(self) -> None:
        account = Account(
            watched=frozenset({_ONE}),
            logged=frozenset({_ONE}),
            imported=frozenset({_ONE}),
            diary_dates=_diary((day(1), _ONE)),
        )
        chosen = select(_ROWS, account=account)
        self.assertEqual(chosen.films, ())
        self.assertEqual(kinds(chosen), [("on-service", _BOTH)])
        self.assertEqual(
            chosen.review[0].detail, "an earlier import sent it, so not sent again"
        )

    def test_an_import_with_no_year_says_so(self) -> None:
        key = FilmKey("placeholder film one", None)
        account = Account(logged=frozenset({key}), imported=frozenset({key}))
        [entry] = select(_ROWS, account=account).review
        self.assertEqual(
            entry.detail,
            "an earlier import sent it, so not sent again; the account entry has no "
            "year, so matched by title alone",
        )


class SameDateTest(unittest.TestCase):
    def test_a_row_on_a_diary_entrys_date_is_dropped(self) -> None:
        chosen = select(_ROWS, account=_logged((day(1), _ONE)))
        self.assertEqual(sent(chosen), ["10/seen.txt:2"])
        self.assertEqual(
            kinds(chosen),
            [
                ("same-date", ("10/seen.txt:1",)),
                ("logged-no-stars", ("10/seen.txt:2",)),
            ],
        )
        [same] = [entry for entry in chosen.review if entry.kind == "same-date"]
        self.assertFalse(same.error)
        self.assertIn("diary entry", same.detail)

    def test_a_film_with_every_row_dropped_is_not_sent(self) -> None:
        chosen = select(_ROWS, account=_logged((day(1), _ONE), (day(2), _ONE)))
        self.assertEqual(chosen.films, ())
        self.assertEqual(kinds(chosen), [("same-date", _BOTH)])

    def test_another_films_diary_date_drops_nothing(self) -> None:
        account = Account(
            logged=frozenset({_ONE, _OTHER}), diary_dates=_diary((day(1), _OTHER))
        )
        chosen = select(_ROWS, account=account)
        self.assertEqual(sent(chosen), list(_BOTH))

    def test_an_undated_row_is_never_dropped(self) -> None:
        row = make_row(date=None, source_file="u/seen.txt")
        chosen = select([row], account=_logged((day(1), _ONE)))
        self.assertEqual(sent(chosen), ["u/seen.txt:1"])

    def test_no_row_is_dropped_when_no_watched_date_is_written(self) -> None:
        chosen = select(_ROWS, account=_logged((day(1), _ONE)), watched_dates=False)
        self.assertEqual(sent(chosen), list(_BOTH))
        self.assertEqual(kinds(chosen), [("logged-no-stars", _BOTH)])

    def test_the_diary_entry_matches_by_any_title_of_the_film(self) -> None:
        rows = [
            make_row(date=day(1), source_line=1),
            make_row(
                date=day(2), source_line=2, title="Metropolis", title_key="metropolis"
            ),
        ]
        account = Account(
            logged=frozenset({_ONE}),
            diary_dates=_diary((day(1), FilmKey("metropolis", None))),
        )
        chosen = select(rows, account=account)
        self.assertEqual(sent(chosen), ["10/seen.txt:2"])

    def test_a_watched_only_film_is_never_dropped(self) -> None:
        account = Account(watched=frozenset({_ONE}), diary_dates=_diary((day(1), _ONE)))
        chosen = select(_ROWS, account=account)
        self.assertEqual(sent(chosen), list(_BOTH))

    def test_films_stay_ordered_by_their_first_sent_row(self) -> None:
        rows = [*_ROWS, make_row(date=day(1), source_line=5, **METROPOLIS)]
        chosen = select(rows, account=_logged((day(1), _ONE)))
        self.assertEqual(sent(chosen), ["10/seen.txt:5", "10/seen.txt:2"])


if __name__ == "__main__":
    unittest.main()
