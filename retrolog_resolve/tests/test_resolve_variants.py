"""Tests for resolve_variants: the six title variants, each on invented titles."""

import sys
import unittest
from collections.abc import Callable
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
import resolve_variants  # noqa: E402
from parse_rows import title_key  # noqa: E402

# Text that often holds what the variants look for, so the fuzz properties reach every
# rewrite and not only the unchanged case.
_PIECES = st.sampled_from(
    ("The", "an", "&", "'s", "s’", "(", ")", "[", "]", "II", "X", "2", "ten", " ", "?")
)
_TEXTS = st.text(max_size=20) | st.lists(_PIECES | st.text(max_size=3)).map("".join)


def _key(variant: Callable[[str], str], title: str) -> str:
    """``title`` under ``variant``, then normalized as matching compares it."""
    return title_key(variant(title))


class OrderTest(unittest.TestCase):
    def test_variants_run_in_r53_order_with_their_names(self) -> None:
        self.assertEqual(
            tuple(name for name, _ in resolve_variants.VARIANTS),
            (
                "an ampersand",
                "a trailing note",
                "a sequel numeral",
                "a leading article",
                "a possessive",
                "number words",
            ),
        )


class AmpersandTest(unittest.TestCase):
    def test_an_ampersand_reads_as_and(self) -> None:
        self.assertEqual(
            _key(resolve_variants.ampersand, "Placeholder & Friend"),
            title_key("Placeholder and Friend"),
        )

    def test_a_tight_ampersand_still_splits_words(self) -> None:
        self.assertEqual(
            _key(resolve_variants.ampersand, "Placeholder&Friend"),
            title_key("Placeholder and Friend"),
        )


class TrailingNoteTest(unittest.TestCase):
    def test_a_parenthetical_or_bracketed_note_is_dropped(self) -> None:
        for title in ("Placeholder (Director Name)", "Placeholder [Special Edition]"):
            with self.subTest(title=title):
                self.assertEqual(
                    _key(resolve_variants.trailing_note, title),
                    title_key("Placeholder"),
                )

    def test_a_title_that_is_only_a_note_is_left(self) -> None:
        self.assertEqual(
            resolve_variants.trailing_note("(Placeholder)"), "(Placeholder)"
        )

    def test_a_note_not_last_is_left(self) -> None:
        title = "Placeholder (Cut) Returns"
        self.assertEqual(resolve_variants.trailing_note(title), title)


class SequelNumeralTest(unittest.TestCase):
    def test_roman_and_digit_numerals_meet(self) -> None:
        self.assertEqual(
            _key(resolve_variants.sequel_numeral, "Placeholder II"),
            _key(resolve_variants.sequel_numeral, "Placeholder 2"),
        )

    def test_every_roman_numeral_two_to_ten_meets_its_digits(self) -> None:
        romans = ("II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")
        for number, roman in enumerate(romans, start=2):
            with self.subTest(roman=roman):
                self.assertEqual(
                    _key(resolve_variants.sequel_numeral, f"Placeholder {roman}"),
                    _key(resolve_variants.sequel_numeral, f"Placeholder {number}"),
                )

    def test_part_numerals_meet_too(self) -> None:
        self.assertEqual(
            _key(resolve_variants.sequel_numeral, "Placeholder: Part III"),
            _key(resolve_variants.sequel_numeral, "Placeholder Part 3"),
        )

    def test_a_lower_case_numeral_meets_its_digits(self) -> None:
        for title in ("placeholder ii", "Placeholder Ii", "PLACEHOLDER iii."):
            with self.subTest(title=title):
                digits = title.lower().replace("iii", "3").replace("ii", "2")
                self.assertEqual(
                    _key(resolve_variants.sequel_numeral, title),
                    _key(resolve_variants.sequel_numeral, digits),
                )

    def test_a_non_ascii_case_match_is_left_as_written(self) -> None:
        # "ı" (dotless i) matches "I" case-insensitively and upper-cases to it, while
        # "İ" matches "i" but upper-cases to itself: neither may raise.
        self.assertEqual(
            resolve_variants.sequel_numeral("Placeholder İİ"), "Placeholder İİ"
        )

    def test_a_numeral_not_last_is_left(self) -> None:
        title = "Placeholder II Returns"
        self.assertEqual(resolve_variants.sequel_numeral(title), title)

    def test_a_numeral_inside_a_word_is_left(self) -> None:
        self.assertEqual(
            resolve_variants.sequel_numeral("PlaceholderII"), "PlaceholderII"
        )


class LeadingArticleTest(unittest.TestCase):
    def test_a_leading_article_is_dropped(self) -> None:
        for title in (
            "The Placeholder",
            "A Placeholder",
            "An Placeholder",
            "the Placeholder",
        ):
            with self.subTest(title=title):
                self.assertEqual(
                    _key(resolve_variants.leading_article, title),
                    title_key("Placeholder"),
                )

    def test_a_one_word_article_is_left(self) -> None:
        self.assertEqual(resolve_variants.leading_article("The"), "The")

    def test_an_article_starting_a_word_is_left(self) -> None:
        self.assertEqual(resolve_variants.leading_article("Theory"), "Theory")


class PossessiveTest(unittest.TestCase):
    def test_a_possessive_marker_is_dropped(self) -> None:
        cases = (
            ("Placeholder's Day", "Placeholder Day"),
            ("Placeholder’s Day", "Placeholder Day"),
            ("Placeholders' Day", "Placeholders Day"),
            ("Placeholders’ Day", "Placeholders Day"),
        )
        for title, plain in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    _key(resolve_variants.possessive, title), title_key(plain)
                )

    def test_an_apostrophe_inside_a_word_is_left(self) -> None:
        self.assertEqual(
            _key(resolve_variants.possessive, "Placeholder's"),
            title_key("Placeholder"),
        )
        self.assertEqual(resolve_variants.possessive("Pla'ceholder"), "Pla'ceholder")


class NumberWordsTest(unittest.TestCase):
    def test_number_words_and_digits_meet(self) -> None:
        self.assertEqual(
            _key(resolve_variants.number_words, "Twelve Placeholders"),
            _key(resolve_variants.number_words, "12 Placeholders"),
        )

    def test_zero_to_twenty_all_meet(self) -> None:
        words = (
            "zero one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
        ).split()
        for number, word in enumerate(words):
            with self.subTest(word=word):
                self.assertEqual(
                    _key(resolve_variants.number_words, f"{word} Placeholders"),
                    _key(resolve_variants.number_words, f"{number} Placeholders"),
                )

    def test_a_word_matched_only_by_unicode_case_is_left(self) -> None:
        # "\u0130" (a dotted capital I) matches "i" case-insensitively, but casefolds
        # to two characters, so the word is not "six".
        title = "S\u0130X Placeholders"
        self.assertEqual(resolve_variants.number_words(title), title)

    def test_only_whole_words_are_read(self) -> None:
        for title in ("Someone Placeholder", "Twelves Placeholder", "Often"):
            with self.subTest(title=title):
                self.assertEqual(resolve_variants.number_words(title), title)


class FuzzTest(unittest.TestCase):
    @given(_TEXTS)
    def test_fuzz_ampersand(self, title: str) -> None:
        self.assertIsInstance(resolve_variants.ampersand(title), str)

    @given(_TEXTS)
    def test_fuzz_trailing_note(self, title: str) -> None:
        dropped = resolve_variants.trailing_note(title)
        # Dropping a note never leaves a blank title behind.
        self.assertTrue(dropped == title or dropped.strip())

    @given(_TEXTS)
    def test_fuzz_sequel_numeral(self, title: str) -> None:
        self.assertIsInstance(resolve_variants.sequel_numeral(title), str)

    @given(_TEXTS)
    def test_fuzz_leading_article(self, title: str) -> None:
        dropped = resolve_variants.leading_article(title)
        self.assertTrue(dropped == title or dropped.strip())

    @given(_TEXTS)
    def test_fuzz_possessive(self, title: str) -> None:
        self.assertIsInstance(resolve_variants.possessive(title), str)

    @given(_TEXTS)
    def test_fuzz_number_words(self, title: str) -> None:
        self.assertIsInstance(resolve_variants.number_words(title), str)


if __name__ == "__main__":
    unittest.main()
