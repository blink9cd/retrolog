"""Tests for parse_dates: placing an entry in time from terms, file names and folders."""

import re
import sys
import unittest
from datetime import date
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_dates  # noqa: E402
import parse_fixtures  # noqa: E402
import parse_rules  # noqa: E402
from parse_dates import Placement  # noqa: E402


def load(**changes: str) -> parse_rules.Rules:
    """The fixture rules with each named [dates] value replaced by its TOML text."""
    replace = {}
    for key, value in changes.items():
        line = re.search(rf"^{key} = .*$", parse_fixtures.RULES_TOML, re.MULTILINE)
        assert line is not None, key  # a key the fixture lacks is a broken test
        replace[line.group(0)] = f"{key} = {value}"
    return parse_rules.load_rules(parse_fixtures.rules_bytes(replace))


RULES = load()


def place(path: str, line: int = 1) -> Placement:
    """Place ``path``'s ``line`` under the fixture rules."""
    return parse_dates.place(RULES, path, line)


class PlaceTest(unittest.TestCase):
    def test_term_block_wins(self) -> None:
        self.assertEqual(
            place("31-32/seen.txt", 2), Placement(date(2031, 12, 31), True, "term")
        )
        self.assertNotEqual(place("31-32/seen.txt", 3).basis, "term")

    def test_term_block_needs_the_same_file(self) -> None:
        self.assertNotEqual(place("31-32/read.txt", 1).basis, "term")
        self.assertNotEqual(place("31-32/seen.txt", 0).basis, "term")

    def test_term_for_returns_the_block_or_none(self) -> None:
        self.assertEqual(
            parse_dates.term_for(RULES, "31-32/seen.txt", 1), RULES.terms[0]
        )
        self.assertIsNone(parse_dates.term_for(RULES, "31-32/seen.txt", 3))

    def test_one_year_folder_is_december_31(self) -> None:
        self.assertEqual(
            place("33/read.txt"), Placement(date(2033, 12, 31), True, "one-year-folder")
        )

    def test_season_in_a_one_year_folder(self) -> None:
        self.assertEqual(place("33/read harvest.txt").date, date(2033, 10, 31))

    def test_break_words_pick_the_year(self) -> None:
        self.assertEqual(
            place("31-32/seen first half.txt"),
            Placement(date(2031, 12, 31), True, "break-word"),
        )
        self.assertEqual(place("31-32/seen second half.txt").date, date(2032, 12, 31))

    def test_explicit_year_wins_over_the_folder(self) -> None:
        got = place("31-32/read harvest #35.txt")
        self.assertEqual(got, Placement(date(2035, 10, 31), True, "explicit-year"))

    def test_explicit_year_needs_no_folder(self) -> None:
        self.assertEqual(place("read #36.txt").date, date(2036, 12, 31))

    def test_season_month_end_uses_the_months_last_day(self) -> None:
        rules = load(seasons="{ thaw = 2 }")
        self.assertEqual(
            parse_dates.place(rules, "32/x thaw.txt", 1).date, date(2032, 2, 29)
        )

    def test_earliest_season_word_in_the_name_wins(self) -> None:
        rules = load(seasons="{ thaw = 2, harvest = 10 }")
        self.assertEqual(
            parse_dates.place(rules, "33/harvest then thaw.txt", 1).date,
            date(2033, 10, 31),
        )

    def test_both_break_words_is_unplaced(self) -> None:
        self.assertEqual(
            place("31-32/first half and second half.txt").basis, "unplaced"
        )

    def test_two_year_folder_without_a_word_is_unplaced(self) -> None:
        self.assertEqual(place("31-32/read.txt"), Placement(None, True, "unplaced"))

    def test_no_dated_folder_is_unplaced(self) -> None:
        self.assertEqual(place("misc/read.txt").basis, "unplaced")
        self.assertEqual(place("read.txt").basis, "unplaced")
        self.assertEqual(place("").basis, "unplaced")

    def test_words_match_whole_words_case_insensitively(self) -> None:
        self.assertEqual(place("31-32/Seen FIRST HALF.txt").date, date(2031, 12, 31))
        self.assertEqual(place("31-32/seen first halfway.txt").basis, "unplaced")

    def test_only_the_file_name_is_read_for_words(self) -> None:
        self.assertEqual(place("31-32/first half/read.txt").basis, "unplaced")

    def test_a_year_outside_the_calendar_is_no_year(self) -> None:
        rules = load(century="9900", one_year_folder="'^(\\d{3})$'")
        self.assertEqual(parse_dates.place(rules, "123/read.txt", 1).basis, "unplaced")
        self.assertEqual(
            parse_dates.place(rules, "read #99.txt", 1).date, date(9999, 12, 31)
        )

    def test_a_group_that_is_not_digits_is_no_year(self) -> None:
        rules = load(explicit_year="['#([a-z]{2})']")
        self.assertEqual(
            parse_dates.place(rules, "33/read #ab.txt", 1).basis, "one-year-folder"
        )

    def test_an_overlong_digit_group_is_no_year(self) -> None:
        # Regression: int() raises ValueError past 4,300 digits, which escaped
        # place() as a traceback; a group of five or more digits is no year.
        rules = load(explicit_year="['#(\\d+)']")
        for digits in ("1" * 5000, "00033"):
            with self.subTest(digits=len(digits)):
                got = parse_dates.place(rules, f"33/read #{digits}.txt", 1)
                self.assertEqual(got.basis, "one-year-folder")
        self.assertEqual(
            parse_dates.place(rules, "read #0033.txt", 1).date, date(2033, 12, 31)
        )

    @given(st.text(max_size=60), st.integers())
    def test_fuzz_place(self, path: str, line: int) -> None:
        got = parse_dates.place(RULES, path, line)
        self.assertIsInstance(got, Placement)
        self.assertEqual(got.date is None, got.basis == "unplaced")


if __name__ == "__main__":
    unittest.main()
