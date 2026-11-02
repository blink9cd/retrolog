"""Tests for resolve_columns' shared rules: the TMDb id shape and the creator joiner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
import resolve_columns  # noqa: E402 - after the sys.path insert above


class TmdbIdTest(unittest.TestCase):
    def test_a_tmdb_id_is_read(self) -> None:
        for text in ("1", "9", "603", "1234567890"):
            with self.subTest(text=text):
                self.assertTrue(resolve_columns.is_tmdb_id(text))

    def test_anything_else_is_refused(self) -> None:
        arabic_one = chr(0x0661)
        for text in ("", "0", "0603", "12345678901", "12a", " 12", "-12", arabic_one):
            with self.subTest(text=text):
                self.assertFalse(resolve_columns.is_tmdb_id(text))

    @given(st.integers(min_value=1, max_value=9_999_999_999))
    def test_every_positive_id_of_ten_digits_or_fewer_is_read(self, value: int) -> None:
        self.assertTrue(resolve_columns.is_tmdb_id(str(value)))

    # Any text at all, printable or not, digits of every script included: the cell is
    # read from a file the operator can edit, so the rule must answer, never raise.
    @given(st.one_of(st.text(), st.text(alphabet="0123456789", max_size=12)))
    def test_fuzz_is_tmdb_id(self, text: str) -> None:
        ascii_digits = text.isascii() and text.isdigit()
        expected = ascii_digits and 1 <= len(text) <= 10 and text[0] != "0"
        self.assertEqual(resolve_columns.is_tmdb_id(text), expected)


class CreatorJoinerTest(unittest.TestCase):
    def test_the_joiner_is_an_ampersand_between_spaces(self) -> None:
        self.assertEqual(resolve_columns.CREATOR_JOINER, " & ")


if __name__ == "__main__":
    unittest.main()
