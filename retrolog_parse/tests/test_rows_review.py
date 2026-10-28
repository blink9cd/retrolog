"""Tests for the rule checks, through parse_rows, and the review details that name a fix.

The rule checks: a term block or override naming a file the run never walked, and the
NFC spelling of a rule's path. The details: a mark's glyph and color, a legend's
colors and ratings, and a heading or section line that also carries a rating.
"""

import sys
import unicodedata
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_entries  # noqa: E402
import parse_fixtures  # noqa: E402
import parse_rows  # noqa: E402
import parse_rules  # noqa: E402
from parse_types import ReviewItem, SourceLine  # noqa: E402
from rows_fixtures import kinds, legend_line, marked_line, text_lines  # noqa: E402

RULES = parse_rules.load_rules(parse_fixtures.rules_bytes())
# The fixture's term block file and override file, which these tests walk or not.
_TERM_FILE = "31-32/seen.txt"
_OVERRIDE_FILE = "33/read.txt"
# An accented override path, spelled precomposed (NFC) as the walk spells it.
_ACCENTED = unicodedata.normalize("NFC", "33/café read.txt")


def build(
    files: Mapping[str, Sequence[str]],
    rules: parse_rules.Rules = RULES,
    walked: frozenset[str] | None = None,
) -> tuple[tuple[parse_rows.Row, ...], tuple[ReviewItem, ...]]:
    """``build_rows`` over plain-text files, with the walked set passed on."""
    lines = {path: text_lines(path, texts) for path, texts in files.items()}
    return parse_rows.build_rows(lines, rules, walked=walked)


def one_line(line: SourceLine) -> tuple[ReviewItem, ...]:
    """The review items of a file holding just ``line``."""
    return parse_rows.build_rows({line.rel_path: (line,)}, RULES)[1]


def details(review: Sequence[ReviewItem], kind: str) -> list[str]:
    """The details of every item of ``kind``, in order."""
    return [item.detail for item in review if item.kind == kind]


class RuleFileMissingTest(unittest.TestCase):
    def test_rules_for_files_never_walked_are_errors(self) -> None:
        _, review = build({"34/read.txt": ["a - k"]}, walked=frozenset({"34/read.txt"}))
        missing = [item for item in review if item.kind == "rule-file-missing"]
        self.assertEqual(
            [(i.rel_path, i.line, i.text, i.error) for i in missing],
            [(_TERM_FILE, 1, "", True), (_OVERRIDE_FILE, 4, "", True)],
        )
        self.assertIn("term block", missing[0].detail)
        self.assertIn("override", missing[1].detail)
        self.assertIn("rule-file-missing", parse_rows.ERROR_KINDS)

    def test_a_walked_file_that_was_not_read_is_not_flagged(self) -> None:
        walked = frozenset({"34/read.txt", _TERM_FILE, _OVERRIDE_FILE})
        _, review = build({"34/read.txt": ["a - k"]}, walked=walked)
        self.assertEqual(kinds(review), [])

    def test_without_a_walked_set_absent_files_are_not_checked(self) -> None:
        _, review = build({"34/read.txt": ["a - k"]})
        self.assertEqual(kinds(review), [])

    def test_an_nfd_override_path_applies_to_the_nfc_file(self) -> None:
        decomposed = unicodedata.normalize("NFD", _ACCENTED)
        self.assertNotEqual(decomposed, _ACCENTED)
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes({f'"{_OVERRIDE_FILE}"': f'"{decomposed}"'})
        )
        lines = ["a - k", "b - k", "c - k", "odd book - q"]
        rows, review = build({_ACCENTED: lines}, rules, frozenset({_ACCENTED}))
        self.assertEqual(rows[3].rating_raw, "3")
        self.assertIn("override-applied", kinds(review))
        self.assertNotIn("override-mismatch", kinds(review))


class MarkDetailTest(unittest.TestCase):
    def test_entry_notes_name_the_glyph_color_and_rating(self) -> None:
        line = marked_line(
            "34/seen.docx",
            [("Wingdings/F042", "778899", "odd"), ("Wingdings/F041", "FF00FF", "out"),
             ("Wingdings/F041", None, "bare")],
        )  # fmt: skip
        review = one_line(line)
        self.assertEqual(
            details(review, "shape-conflict"),
            ["mark Wingdings/F042 in 778899 gives rating 3, "
             "which [shapes] does not allow for that glyph"],
        )  # fmt: skip
        self.assertEqual(
            details(review, "color-outlier"),
            ["mark Wingdings/F041 in FF00FF is near no rules color, "
             "or equally near two"],
        )  # fmt: skip
        self.assertEqual(
            details(review, "no-color"),
            ["mark Wingdings/F041 has no color to rate it by"],
        )

    def test_an_override_keeps_the_marks_own_rating_in_the_detail(self) -> None:
        line = marked_line("34/seen.docx", [("Wingdings/F042", "778899", "x")], 4)
        lines = {"34/seen.docx": (line,)}
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes(
                {f'"{_OVERRIDE_FILE}"': '"34/seen.docx"', '"odd book - q"': '"x"'}
            )
        )
        rows, review = parse_rows.build_rows(lines, rules)
        self.assertEqual(rows[0].rating_raw, "3")
        self.assertIn("gives rating 3", details(review, "shape-conflict")[0])

    def test_legend_details_list_each_color_and_rating(self) -> None:
        review = one_line(legend_line(["778899", "445566", "112233"]))
        self.assertEqual(
            details(review, "legend"),
            ["legend marks (color = rating): 778899 = 3, 445566 = 2, 112233 = 1"],
        )
        line = marked_line(
            "34/seen.docx",
            [("Wingdings/F041", "112233", "-"), ("Wingdings/F041", None, "-")],
        )
        self.assertEqual(
            details(one_line(line), "legend-order"),
            ["legend marks (color = rating) 112233 = 1, no color = ? "
             "differ from the rules' order 3, 2, 1"],
        )  # fmt: skip

    def test_details_name_no_path(self) -> None:
        line = marked_line("34/seen.docx", [("Wingdings/F041", "FF00FF", "out")])
        for item in one_line(line):
            self.assertNotIn("34/seen.docx", item.detail)


class HeadingWithRatingTest(unittest.TestCase):
    def test_a_heading_or_section_with_a_code_is_reported(self) -> None:
        _, review = build(
            {"34/seen.txt": ["List of films - k", "Work", "List of films"]}
        )
        found = [(i.kind, i.line, i.error) for i in review]
        self.assertEqual(found, [("heading-with-rating", 1, False)])

    def test_a_marked_heading_or_section_is_reported(self) -> None:
        for text in ("List of films", "Work"):
            with self.subTest(text=text):
                line = marked_line("34/seen.docx", [("Wingdings/F041", "778899", text)])
                parsed = parse_entries.parse_line(line, RULES)
                self.assertIn(parsed.kind, ("heading", "section"))
                self.assertEqual(kinds(one_line(line)), ["heading-with-rating"])

    def test_a_heading_with_a_code_and_comment_is_reported(self) -> None:
        _, review = build({"34/seen.txt": ["List of films - k (all)"]})
        self.assertEqual(kinds(review), ["heading-with-rating"])

    def test_an_unknown_code_on_a_heading_is_not_reported(self) -> None:
        _, review = build({"34/seen.txt": ["List of films - q"]})
        self.assertEqual(kinds(review), [])


if __name__ == "__main__":
    unittest.main()
