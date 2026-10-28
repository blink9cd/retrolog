"""Tests for resolve_isbn: checking and normalizing ISBN-13 and ISBN-10 text."""

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
import resolve_isbn  # noqa: E402

# The ISBN-10 check value written as "X".
_X_VALUE = 10


def _isbn13_check(first12: str) -> str:
    """The ISBN-13 check digit, computed independently of the subject."""
    total = sum(int(d) * (3 if place % 2 else 1) for place, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def _isbn10_check(first9: str) -> str:
    """The ISBN-10 check digit, "X" for ten."""
    total = sum((10 - place) * int(digit) for place, digit in enumerate(first9))
    check = (11 - total % 11) % 11
    return "X" if check == _X_VALUE else str(check)


@st.composite
def _valid_isbns(draw: st.DrawFn) -> str:
    """A valid ISBN-10 or ISBN-13, sometimes printed with hyphens, so the fuzz
    property's inner assertions run on real answers, not only on random text."""
    digits = st.text(alphabet="0123456789", min_size=9, max_size=9)
    if draw(st.booleans()):
        first12 = draw(st.sampled_from(("978", "979"))) + draw(digits)
        isbn = first12 + _isbn13_check(first12)
    else:
        first9 = draw(digits)
        isbn = first9 + _isbn10_check(first9)
    return "-".join((isbn[:3], isbn[3:])) if draw(st.booleans()) else isbn


class Isbn13OrNoneTest(unittest.TestCase):
    def test_valid_isbn13_is_kept(self) -> None:
        self.assertEqual(resolve_isbn.isbn13_or_none("9780306406157"), "9780306406157")

    def test_979_prefix_is_kept(self) -> None:
        self.assertEqual(resolve_isbn.isbn13_or_none("9791234567896"), "9791234567896")

    def test_hyphens_and_spaces_are_ignored(self) -> None:
        self.assertEqual(
            resolve_isbn.isbn13_or_none(" 978-0-306 40615-7 "), "9780306406157"
        )

    def test_isbn10_converts_to_isbn13(self) -> None:
        self.assertEqual(resolve_isbn.isbn13_or_none("0-306-40615-2"), "9780306406157")

    def test_isbn10_with_x_check_digit(self) -> None:
        self.assertEqual(resolve_isbn.isbn13_or_none("080442957X"), "9780804429573")

    def test_bad_check_digit_is_none(self) -> None:
        self.assertIsNone(resolve_isbn.isbn13_or_none("9780306406158"))

    def test_isbn10_bad_check_digit_is_none(self) -> None:
        self.assertIsNone(resolve_isbn.isbn13_or_none("0306406153"))

    def test_isbn13_outside_the_book_prefixes_is_none(self) -> None:
        # A valid EAN-13 check digit, but not a 978/979 ISBN.
        self.assertIsNone(resolve_isbn.isbn13_or_none("4006381333931"))

    def test_x_anywhere_but_the_isbn10_check_digit_is_none(self) -> None:
        for text in ("X306406152", "978030640615X", "080442957x"):
            with self.subTest(text=text):
                self.assertIsNone(resolve_isbn.isbn13_or_none(text))

    def test_other_lengths_and_characters_are_none(self) -> None:
        for text in ("", "978030640615", "97803064061570", "978.0306406157"):
            with self.subTest(text=text):
                self.assertIsNone(resolve_isbn.isbn13_or_none(text))

    def test_non_ascii_digits_are_none(self) -> None:
        # Arabic-Indic digits pass str.isdigit; an ISBN is ASCII only.
        arabic = "".join(chr(0x0660 + int(d)) for d in "9780306406157")
        self.assertIsNone(resolve_isbn.isbn13_or_none(arabic))

    @given(st.one_of(st.text(max_size=40), _valid_isbns()))
    def test_fuzz_isbn13_or_none(self, text: str) -> None:
        result = resolve_isbn.isbn13_or_none(text)
        if result is not None:  # every answer is itself a valid ISBN-13
            self.assertEqual(resolve_isbn.isbn13_or_none(result), result)
            self.assertRegex(result, r"^97[89]\d{10}$")

    @given(_valid_isbns())
    def test_every_valid_isbn_is_accepted(self, text: str) -> None:
        result = resolve_isbn.isbn13_or_none(text)
        self.assertIsNotNone(result)
        bare = text.replace("-", "")
        if len(bare) == 10:  # an ISBN-10 keeps its nine body digits after 978
            self.assertEqual(str(result)[:12], "978" + bare[:9])
        else:
            self.assertEqual(result, bare)


if __name__ == "__main__":
    unittest.main()
