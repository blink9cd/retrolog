"""Tests for export_match and the hyphen fold of export_titles.

export_match finds a film on the account beyond an exact key: by a year one either
side of the row's, and by an earlier import's tagged diary entry on a row's date.
export_titles reads a hyphen as a space in the folded key, so "40-Year-Old" meets the
account's "40 Year Old".
"""

from __future__ import annotations

import datetime
import sys
import unittest
from collections.abc import Iterator, Mapping
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
import export_match  # noqa: E402 - after the sys.path inserts above
from export_titles import (  # noqa: E402 - after the sys.path inserts above
    fold_hyphens,
    fold_title,
    match_keys,
)
from export_types import FilmKey  # noqa: E402 - after the sys.path inserts above
from parse_rows import title_key  # noqa: E402 - after the sys.path inserts above
from select_case import day, make_row  # noqa: E402 - after the sys.path inserts above

_ONE = FilmKey("placeholder film one", 1999)


class HyphenFoldTest(unittest.TestCase):
    def test_a_hyphen_folds_to_a_space(self) -> None:
        # Regression: "-" was dropped by title_key alone, so a hyphenated list title
        # never met the account's spaced one, and the film was sent a second time.
        self.assertEqual(
            match_keys("The 40-Year-Old Placeholder"),
            ("the 40yearold placeholder", "the 40 year old placeholder"),
        )

    def test_the_unicode_hyphens_fold_too(self) -> None:
        for hyphen in ("‐", "‑"):
            with self.subTest(hyphen=hyphen):
                spaced = title_key(fold_hyphens(f"Run{hyphen}Away"))
                self.assertEqual(spaced, "run away")

    def test_the_accent_and_and_fold_leaves_a_hyphen_alone(self) -> None:
        self.assertEqual(fold_title("Café-Bar & Co"), "Cafe-Bar  and  Co")

    def test_the_hyphen_fold_only_adds_a_match(self) -> None:
        # Regression: the hyphen fold replaced the accent-and-"&" fold rather than
        # adding a key, so a hyphenated title no longer met its unhyphenated twin.
        pairs = (
            ("Placeholder Café-Bar", "Placeholder Cafebar"),
            ("Placeholder & Run-Away", "Placeholder and Runaway"),
            ("The 40-Year-Old Placeholder", "The 40 Year Old Placeholder"),
        )
        for listed, account in pairs:
            with self.subTest(listed=listed):
                shared = set(match_keys(listed)) & set(match_keys(account))
                self.assertTrue(shared)

    def test_the_keys_come_plain_then_folded_then_hyphens_spaced(self) -> None:
        self.assertEqual(
            match_keys("Placeholder Café-Bar"),
            ("placeholder cafébar", "placeholder cafebar", "placeholder cafe bar"),
        )

    def test_the_plain_key_is_unchanged_and_first(self) -> None:
        self.assertEqual(title_key("Run-Away"), "runaway")
        self.assertEqual(match_keys("Run-Away")[0], "runaway")

    def test_the_account_and_the_list_meet_across_a_hyphen(self) -> None:
        [row] = export_account.parse_account_csv(
            "Name,Year\nThe 40 Year Old Placeholder,2005\n"
        )
        listed = match_keys("The 40-Year-Old Placeholder")
        self.assertTrue(any(FilmKey(key, 2005) in row.keys for key in listed))


class NearYearKeysTest(unittest.TestCase):
    def test_a_key_has_the_years_either_side(self) -> None:
        self.assertEqual(
            export_match.near_year_keys(_ONE),
            (
                FilmKey("placeholder film one", 1998),
                FilmKey("placeholder film one", 2000),
            ),
        )

    def test_a_key_with_no_year_has_none(self) -> None:
        self.assertEqual(
            export_match.near_year_keys(FilmKey("placeholder film one", None)), ()
        )


class AccountMatchTest(unittest.TestCase):
    def _match(self, *account: FilmKey) -> str | None:
        return export_match.account_match(
            [_ONE], frozenset(account), export_account.on_service
        )

    def test_an_exact_key_is_exact(self) -> None:
        self.assertEqual(self._match(_ONE), "exact")

    def test_a_year_one_apart_is_near(self) -> None:
        for year in (1998, 2000):
            with self.subTest(year=year):
                self.assertEqual(
                    self._match(FilmKey("placeholder film one", year)), "near-year"
                )

    def test_exact_wins_over_near(self) -> None:
        self.assertEqual(
            self._match(FilmKey("placeholder film one", 2000), _ONE), "exact"
        )

    def test_two_years_apart_or_another_title_is_no_match(self) -> None:
        self.assertIsNone(self._match(FilmKey("placeholder film one", 2001)))
        self.assertIsNone(self._match(FilmKey("metropolis", 1999)))


class _AnswersEveryDate(Mapping[datetime.date, frozenset[FilmKey]]):
    """A date mapping that hands back ``entries`` for any key at all, None included."""

    def __init__(self, entries: frozenset[FilmKey]) -> None:
        self._entries = entries

    def __getitem__(self, _date: object) -> frozenset[FilmKey]:
        return self._entries

    def __iter__(self) -> Iterator[datetime.date]:
        return iter(())

    def __len__(self) -> int:
        return 0


class ImportedDateTest(unittest.TestCase):
    def _dates(self, *dated: tuple[datetime.date, FilmKey]) -> MappingProxyType:
        by_date: dict[datetime.date, frozenset[FilmKey]] = {}
        for date, key in dated:
            by_date[date] = by_date.get(date, frozenset()) | {key}
        return MappingProxyType(by_date)

    def test_a_tagged_entry_on_a_rows_date_in_any_year_is_found(self) -> None:
        rows = (make_row(date=day(1), source_line=1), make_row(date=day(2)))
        dates = self._dates((day(2), FilmKey("placeholder film one", 2004)))
        found = export_match.imported_date(rows, {"placeholder film one"}, dates)
        self.assertEqual(found, day(2))

    def test_another_title_or_date_finds_nothing(self) -> None:
        dates = self._dates((day(1), FilmKey("metropolis", 1999)))
        titles = {"placeholder film one"}
        for row in (make_row(date=day(1)), make_row(date=day(2))):
            with self.subTest(date=row.date):
                self.assertIsNone(export_match.imported_date((row,), titles, dates))

    def test_an_undated_row_is_skipped_not_matched(self) -> None:
        # The mapping answers every key, None included. Without the row.date guard
        # the undated row would match first and hand back its own None date, hiding
        # the dated row after it, so the film's import would go unseen.
        dates = _AnswersEveryDate(frozenset({FilmKey("placeholder film one", 1999)}))
        titles = {"placeholder film one"}
        rows = (make_row(date=None), make_row(date=day(3)))
        self.assertEqual(export_match.imported_date(rows, titles, dates), day(3))

    def test_the_tagged_detail_names_the_date(self) -> None:
        detail = export_match.tagged_detail(day(4))
        self.assertIn("retrolog diary entry on 2010-03-04", detail)
        self.assertNotIn("placeholder", detail.casefold())


if __name__ == "__main__":
    unittest.main()
