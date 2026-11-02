"""Tests for export_select's account checks beyond an exact title key and year.

A film is already imported when an earlier import's tagged diary entry of one of its
titles sits on one of its rows' dates, whatever either year is. The logged, watched-only
and same-date checks accept an account entry of its title a year either side of the
row's. The imported check itself, and the held films, stay exact.
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
import export_account  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
from export_types import Account, FilmKey, ResolvedRow  # noqa: E402 - after inserts
from select_case import day, kinds, make_row, sent  # noqa: E402 - after the inserts

_ROWS = [
    make_row(date=day(1), source_line=1),
    make_row(date=day(2), source_line=2, rewatch=True),
]
_BOTH = ("10/seen.txt:1", "10/seen.txt:2")
_NEXT_YEAR = FilmKey("placeholder film one", 2000)
_TWO_YEARS = FilmKey("placeholder film one", 2001)


def _by_date(*dated: tuple[datetime.date, FilmKey]) -> MappingProxyType:
    """A date -> keys mapping for entries of ``(date, key)``."""
    found: dict[datetime.date, frozenset[FilmKey]] = {}
    for date, key in dated:
        found[date] = found.get(date, frozenset()) | {key}
    return MappingProxyType(found)


def _tagged(*dated: tuple[datetime.date, FilmKey]) -> Account:
    """An account whose diary holds ``dated``, each entry tagged by an import."""
    keys = frozenset(key for _, key in dated)
    return Account(
        logged=keys,
        imported=keys,
        diary_dates=_by_date(*dated),
        imported_dates=_by_date(*dated),
    )


def _select(
    rows: list[ResolvedRow], account: Account, *, watched_dates: bool = True
) -> export_select.Selection:
    """``select`` with export_account's own ``on_service``, as the CLI runs it."""
    return export_select.select(
        rows,
        cutoff=None,
        account=account,
        on_service=export_account.on_service,
        watched_dates=watched_dates,
    )


class ImportedByTaggedDateTest(unittest.TestCase):
    def test_a_year_one_apart_with_a_tagged_entry_that_day_is_skipped(self) -> None:
        # Regression: Letterboxd dated the film a year before TMDb did, so the exact
        # imported check missed it and a re-run would send it a second time.
        chosen = _select(_ROWS, _tagged((day(2), _NEXT_YEAR)))
        self.assertEqual(chosen.films, ())
        [entry] = chosen.review
        self.assertEqual((entry.kind, entry.rows), ("on-service", _BOTH))
        self.assertIn("retrolog diary entry on 2010-03-02", entry.detail)

    def test_a_year_two_apart_with_a_tagged_entry_that_day_is_skipped(self) -> None:
        chosen = _select(_ROWS, _tagged((day(1), _TWO_YEARS)))
        self.assertEqual(kinds(chosen), [("on-service", _BOTH)])
        self.assertIn("2010-03-01", chosen.review[0].detail)

    def test_a_hyphen_apart_with_a_tagged_entry_that_day_is_skipped(self) -> None:
        listed = "The 40-Year-Old Placeholder"
        row = make_row(title=listed, title_key="the 40yearold placeholder", date=day(1))
        account_key = FilmKey("the 40 year old placeholder", 1999)
        chosen = _select([row], _tagged((day(1), account_key)))
        self.assertEqual(kinds(chosen), [("on-service", ("10/seen.txt:1",))])

    def test_a_tmdb_title_counts_as_the_films_title(self) -> None:
        row = make_row(date=day(1), tmdb_title_keys=("placeholder tmdb title",))
        account_key = FilmKey("placeholder tmdb title", 2003)
        chosen = _select([row], _tagged((day(1), account_key)))
        self.assertEqual(chosen.films, ())

    def test_a_tagged_entry_on_another_day_is_not_the_import(self) -> None:
        chosen = _select(_ROWS, _tagged((day(5), _TWO_YEARS)))
        self.assertEqual(sent(chosen), list(_BOTH))
        self.assertNotIn("on-service", [kind for kind, _ in kinds(chosen)])

    def test_the_rule_is_off_without_watched_dates(self) -> None:
        chosen = _select(_ROWS, _tagged((day(1), _TWO_YEARS)), watched_dates=False)
        self.assertEqual(sent(chosen), list(_BOTH))

    def test_the_imported_check_takes_no_year_either_side(self) -> None:
        # An import's entry a year apart, with no tagged entry on a row's date, is
        # seen only by the logged check: the film goes again, without stars.
        account = Account(
            imported=frozenset({_NEXT_YEAR}), logged=frozenset({_NEXT_YEAR})
        )
        for watched_dates in (True, False):
            with self.subTest(watched_dates=watched_dates):
                chosen = _select(_ROWS, account, watched_dates=watched_dates)
                self.assertEqual(kinds(chosen), [("logged-no-stars", _BOTH)])
                self.assertTrue(chosen.films[0].withhold_rating)

    def test_an_exact_import_keeps_its_own_detail(self) -> None:
        key = FilmKey("placeholder film one", 1999)
        [entry] = _select(_ROWS, _tagged((day(1), key))).review
        self.assertEqual(entry.detail, "an earlier import sent it, so not sent again")


class NearYearLoggedTest(unittest.TestCase):
    def test_a_logged_film_a_year_apart_is_sent_without_stars(self) -> None:
        # Regression: missed by a year, the film went with stars and overwrote the
        # owner's own rating.
        chosen = _select(_ROWS, Account(logged=frozenset({_NEXT_YEAR})))
        [film] = chosen.films
        self.assertTrue(film.withhold_rating)
        [entry] = chosen.review
        self.assertEqual((entry.kind, entry.rows), ("logged-no-stars", _BOTH))
        self.assertIn("a year one apart", entry.detail)

    def test_an_exact_logged_film_keeps_its_own_detail(self) -> None:
        exact = FilmKey("placeholder film one", 1999)
        [entry] = _select(_ROWS, Account(logged=frozenset({exact, _NEXT_YEAR}))).review
        self.assertNotIn("a year one apart", entry.detail)

    def test_a_logged_film_two_years_apart_is_sent_with_stars(self) -> None:
        chosen = _select(_ROWS, Account(logged=frozenset({_TWO_YEARS})))
        self.assertFalse(chosen.films[0].withhold_rating)
        self.assertEqual(kinds(chosen), [])

    def test_logged_a_year_apart_beats_watched_exactly(self) -> None:
        exact = FilmKey("placeholder film one", 1999)
        account = Account(watched=frozenset({exact}), logged=frozenset({_NEXT_YEAR}))
        chosen = _select(_ROWS, account)
        self.assertTrue(chosen.films[0].withhold_rating)

    def test_a_watched_only_film_a_year_apart_is_listed_so(self) -> None:
        chosen = _select(_ROWS, Account(watched=frozenset({_NEXT_YEAR})))
        self.assertFalse(chosen.films[0].withhold_rating)
        [entry] = chosen.review
        self.assertEqual(entry.kind, "watched-only")
        self.assertIn("a year one apart", entry.detail)

    def test_an_exact_watched_only_film_keeps_its_own_detail(self) -> None:
        exact = FilmKey("placeholder film one", 1999)
        [entry] = _select(_ROWS, Account(watched=frozenset({exact}))).review
        self.assertNotIn("a year one apart", entry.detail)


class NearYearSameDateTest(unittest.TestCase):
    def test_a_diary_entry_a_year_apart_on_the_date_drops_the_row(self) -> None:
        account = Account(
            logged=frozenset({_NEXT_YEAR}),
            diary_dates=_by_date((day(1), _NEXT_YEAR)),
        )
        chosen = _select(_ROWS, account)
        self.assertEqual(sent(chosen), ["10/seen.txt:2"])
        [same, _] = chosen.review
        self.assertEqual((same.kind, same.rows), ("same-date", ("10/seen.txt:1",)))
        self.assertIn("a year one apart", same.detail)

    def test_an_exact_diary_entry_keeps_its_own_detail(self) -> None:
        exact = FilmKey("placeholder film one", 1999)
        account = Account(
            logged=frozenset({exact}), diary_dates=_by_date((day(1), exact))
        )
        same = _select(_ROWS, account).review[0]
        self.assertEqual(same.kind, "same-date")
        self.assertNotIn("a year one apart", same.detail)

    def test_a_diary_entry_two_years_apart_drops_nothing(self) -> None:
        exact = FilmKey("placeholder film one", 1999)
        account = Account(
            logged=frozenset({exact}), diary_dates=_by_date((day(1), _TWO_YEARS))
        )
        self.assertEqual(sent(_select(_ROWS, account)), list(_BOTH))


# 2010-05-06, the date two films of one title in different years were both watched.
_SAME_DAY = datetime.date(2010, 5, 6)


def _same_title(
    year: int, status: str, line: int, **dates: datetime.date
) -> ResolvedRow:
    """A title-only row of "Same Title" in ``year``, on ``dates["date"]`` or the day."""
    return make_row(
        title="Same Title",
        title_key="same title",
        year=year,
        tmdb_id="",
        status=status,
        date=dates.get("date", _SAME_DAY),
        source_line=line,
    )


class HeldByTaggedDateTest(unittest.TestCase):
    """With dates on, a film sharing a held film's title and a row date waits with it.

    Sent, its tagged diary entry would sit on the held film's date under its title, and
    the next run's imported check (``imported_date``, any year) would skip the held
    film for good once resolve settled it.
    """

    def test_a_sent_film_sharing_a_held_films_title_and_date_is_held(self) -> None:
        rows = [_same_title(2000, "not-found", 1), _same_title(1990, "ambiguous", 2)]
        chosen = _select(rows, Account())
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            kinds(chosen),
            [
                ("held-ambiguous", ("10/seen.txt:1",)),
                ("held-ambiguous", ("10/seen.txt:2",)),
            ],
        )
        self.assertIn("another film of this title", chosen.review[0].detail)

    def test_sending_it_would_hide_the_held_film_on_the_next_run(self) -> None:
        # Why the hold: once the 2000 film is imported and tagged, the 1990 one,
        # settled, meets that tagged entry on its date and is never sent.
        account = _tagged((_SAME_DAY, FilmKey("same title", 2000)))
        settled = [_same_title(1990, "not-found", 2)]
        self.assertEqual(kinds(_select(settled, account))[0][0], "on-service")

    def test_without_watched_dates_the_date_holds_nothing(self) -> None:
        rows = [_same_title(2000, "not-found", 1), _same_title(1990, "ambiguous", 2)]
        chosen = _select(rows, Account(), watched_dates=False)
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])

    def test_a_held_film_on_another_date_holds_nothing(self) -> None:
        rows = [
            _same_title(2000, "not-found", 1),
            _same_title(1990, "ambiguous", 2, date=day(9)),
        ]
        self.assertEqual(sent(_select(rows, Account())), ["10/seen.txt:1"])

    def test_another_title_on_the_held_films_date_is_sent(self) -> None:
        other = make_row(tmdb_id="", status="not-found", date=_SAME_DAY)
        rows = [other, _same_title(1990, "ambiguous", 2)]
        self.assertEqual(sent(_select(rows, Account())), ["10/seen.txt:1"])

    def test_a_film_held_by_date_passes_the_hold_on(self) -> None:
        # The 2000 film shares the held film's date; its second row shares a later
        # date with the 1980 film, which would then hide the 2000 film in turn.
        rows = [
            _same_title(1990, "ambiguous", 1),
            _same_title(2000, "not-found", 2),
            _same_title(2000, "not-found", 3, date=day(9)),
            _same_title(1980, "not-found", 4, date=day(9)),
        ]
        chosen = _select(rows, Account())
        self.assertEqual(chosen.films, ())
        self.assertEqual({entry.kind for entry in chosen.review}, {"held-ambiguous"})


if __name__ == "__main__":
    unittest.main()
