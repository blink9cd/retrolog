"""Tests for survey_lines: the kind of each list line, its emoji and letter tokens."""

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
import survey_lines  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_order(self) -> None:
        cases = [
            (survey_lines.LineView(text="  "), "blank"),
            (
                survey_lines.LineView(text="C x", has_symbol_run=True, has_color=True),
                "symbol",
            ),
            (survey_lines.LineView(text="", has_image=True), "image"),
            (survey_lines.LineView(text="Film", has_color=True), "colored"),
            (survey_lines.LineView(text="Film \U0001f44e"), "emoji"),
            (survey_lines.LineView(text="g Film"), "letter_code"),
            (survey_lines.LineView(text="Film, g"), "letter_code"),
            (survey_lines.LineView(text="Saw it twice"), "unclassified"),
        ]
        for view, kind in cases:
            with self.subTest(text=view.text):
                self.assertEqual(survey_lines.classify_line(view), kind)

    def test_find_emoji(self) -> None:
        self.assertEqual(
            survey_lines.find_emoji("ok \U0001f44d \U0001f3ac"),
            ("U+1F44D", "U+1F3AC"),
        )

    def test_find_emoji_keeps_duplicates_and_range_edges(self) -> None:
        text = "\U0001f300 a \U0001faff \U0001f300 \U0001fb00 ❤"
        self.assertEqual(
            survey_lines.find_emoji(text), ("U+1F300", "U+1FAFF", "U+1F300")
        )

    def test_symbol_fonts_are_casefolded(self) -> None:
        self.assertIn("wingdings 2", survey_lines.SYMBOL_FONTS)
        self.assertTrue(all(f == f.casefold() for f in survey_lines.SYMBOL_FONTS))


class LetterTokensTest(unittest.TestCase):
    def test_both_ends_lowercased(self) -> None:
        self.assertEqual(survey_lines.letter_tokens("R Film - b"), ("r", "b"))

    def test_none_in_plain_words(self) -> None:
        self.assertEqual(survey_lines.letter_tokens("Saw it twice"), ())

    def test_lone_letter_counted_once(self) -> None:
        self.assertEqual(survey_lines.letter_tokens("  Q  "), ("q",))

    def test_two_letters_are_two_tokens(self) -> None:
        self.assertEqual(survey_lines.letter_tokens("g g"), ("g", "g"))

    def test_non_ascii_letter_is_not_a_token(self) -> None:
        self.assertEqual(survey_lines.letter_tokens("é Film"), ())

    @given(st.text(max_size=200))
    def test_fuzz_letter_tokens(self, text: str) -> None:
        tokens = survey_lines.letter_tokens(text)
        self.assertLessEqual(len(tokens), 2)
        self.assertTrue(
            all(len(t) == 1 and t.isascii() and t.islower() for t in tokens)
        )


if __name__ == "__main__":
    unittest.main()
