"""Tests for parse_rows: source lines and the rules in, rows and a review list out."""

import ast
import re
import sys
import unittest
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_entries  # noqa: E402
import parse_review  # noqa: E402
import parse_fixtures  # noqa: E402
import parse_rows  # noqa: E402
import parse_rules  # noqa: E402
from parse_rows import Row  # noqa: E402
from parse_types import ReviewItem, SourceLine  # noqa: E402
from rows_fixtures import kinds, legend_line, marked_line, text_lines  # noqa: E402

RULES = parse_rules.load_rules(parse_fixtures.rules_bytes())
# The fixture override's action and value lines, which override_rules replaces.
_OVERRIDE_ACTION = 'action = "rating"\nvalue = "3"\n'
# Line kinds that carry nothing a person must look at.
_LAYOUT_KINDS = frozenset({"blank", "divider", "heading", "section"})
# A note name as parse_entries spells one: lower-case words joined by dashes.
_NOTE_NAME = re.compile(r"[a-z]+(?:-[a-z]+)+")

Built = tuple[tuple[Row, ...], tuple[ReviewItem, ...]]


def build_lines(
    files: Mapping[str, tuple[SourceLine, ...]], rules: parse_rules.Rules = RULES
) -> Built:
    """``build_rows`` over ready-made source lines."""
    return parse_rows.build_rows(files, rules)


def build(
    files: Mapping[str, Sequence[str]], rules: parse_rules.Rules = RULES
) -> Built:
    """``build_rows`` over plain-text files given as lists of line texts."""
    return build_lines(
        {path: text_lines(path, texts) for path, texts in files.items()}, rules
    )


def override_rules(action: str, value: str | None) -> parse_rules.Rules:
    """The fixture rules with the override's action (and value) replaced."""
    new = f'action = "{action}"\n' + ("" if value is None else f'value = "{value}"\n')
    return parse_rules.load_rules(parse_fixtures.rules_bytes({_OVERRIDE_ACTION: new}))


class BuildRowsTest(unittest.TestCase):
    def test_entry_row_fields(self) -> None:
        rows, review = build({"34/read.txt": ["*a book (1999) - k"]})
        self.assertEqual(
            rows,
            (
                Row(
                    "book", "a book", "", 1999, date(2034, 12, 31), True, "",
                    "3", "", False, "", "34/read.txt", 1,
                ),
            ),
        )  # fmt: skip
        self.assertEqual(review, ())

    def test_unrated_entries_become_rows_and_review_items(self) -> None:
        rows, review = build({"34/read.txt": ["*a book"]})
        self.assertEqual(rows[0].rating_raw, "")
        self.assertEqual(kinds(review), ["unrated"])
        self.assertEqual((review[0].line, review[0].text), (1, "*a book"))

    def test_override_rating_applies_when_the_text_matches(self) -> None:
        rows, review = build({"33/read.txt": ["a", "b", "c", "odd book - q"]})
        self.assertEqual((rows[3].title, rows[3].rating_raw), ("odd book", "3"))
        self.assertIn("override-applied", kinds(review))
        # The override settled the unknown code, so it is not listed again.
        self.assertNotIn("unknown-code", kinds(review))

    def test_override_mismatch_is_an_error_and_is_not_applied(self) -> None:
        rows, review = build({"33/read.txt": ["a", "b", "c", "other book - q"]})
        item = next(r for r in review if r.kind == "override-mismatch")
        self.assertTrue(item.error)
        self.assertEqual((item.line, item.text), (4, "other book - q"))
        self.assertNotEqual(rows[-1].rating_raw, "3")
        self.assertEqual(rows[-1].title, "other book - q")
        self.assertIn("unknown-code", kinds(review))

    def test_override_keeps_the_work_year_before_a_stripped_code(self) -> None:
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes(
                {'expect = "odd book - q"': 'expect = "odd book (1999) - q"'}
            )
        )
        rows, _ = build({"33/read.txt": ["a", "b", "c", "odd book (1999) - q"]}, rules)
        self.assertEqual(
            (rows[3].title, rows[3].year, rows[3].rating_raw), ("odd book", 1999, "3")
        )

    def test_override_keeps_a_work_year_parse_already_took(self) -> None:
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes(
                {
                    'expect = "odd book - q"': 'expect = "odd book (1999)"',
                    _OVERRIDE_ACTION: 'action = "unrated"\n',
                }
            )
        )
        rows, _ = build({"33/read.txt": ["a", "b", "c", "odd book (1999)"]}, rules)
        self.assertEqual((rows[3].title, rows[3].year), ("odd book", 1999))

    def test_override_never_cleans_a_title_away(self) -> None:
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes(
                {'expect = "odd book - q"': 'expect = "- (1999) - q"'}
            )
        )
        rows, _ = build({"33/read.txt": ["a", "b", "c", "- (1999) - q"]}, rules)
        self.assertEqual((rows[3].title, rows[3].year), ("- (1999)", None))

    def test_override_actions(self) -> None:
        for action, value, expected in (
            ("skip", None, None),
            ("title", "Right Title", ("Right Title", "")),
            ("unrated", None, ("odd book", "")),
        ):
            with self.subTest(action=action):
                rules = override_rules(action, value)
                rows, review = build(
                    {"33/read.txt": ["a", "b", "c", "odd book - q"]}, rules
                )
                line4 = [(r.title, r.rating_raw) for r in rows if r.source_line == 4]
                self.assertEqual(line4, [] if expected is None else [expected])
                kind = "skipped" if action == "skip" else "override-applied"
                self.assertIn(kind, kinds(review))

    def test_override_on_a_missing_line_is_a_mismatch(self) -> None:
        _, review = build({"33/read.txt": ["a - k"]})
        item = next(r for r in review if r.kind == "override-mismatch")
        self.assertEqual((item.line, item.text, item.error), (4, "", True))

    def test_override_on_a_line_without_one_entry_is_a_mismatch(self) -> None:
        # The text matches, but "~~~" holds no entry to rate: parsed as written.
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes({'expect = "odd book - q"': 'expect = "~~~"'})
        )
        rows, review = build({"33/read.txt": ["a - k", "b - k", "c - k", "~~~"]}, rules)
        self.assertEqual(len(rows), 3)
        self.assertEqual(kinds(review), ["override-mismatch", "unparsed"])
        self.assertTrue(review[0].error)

    def test_skip_override_drops_every_entry_of_its_line(self) -> None:
        rules = override_rules("skip", None)
        line = marked_line(
            "33/read.txt",
            [
                ("Wingdings/F041", "778899", "odd book"),
                ("Wingdings/F041", "778899", " - q"),
            ],
            number=4,
        )
        lines = (*text_lines("33/read.txt", ["a - k", "b - k", "c - k"]), line)
        rows, review = build_lines({"33/read.txt": lines}, rules)
        self.assertEqual([r.source_line for r in rows], [1, 2, 3])
        self.assertEqual(kinds(review), ["skipped"])

    def test_a_second_override_for_one_line_is_a_mismatch(self) -> None:
        second = '\n[[overrides]]\nfile = "33/read.txt"\nline = 4\nexpect = "odd book - q"\naction = "skip"\n'
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes({_OVERRIDE_ACTION: _OVERRIDE_ACTION + second})
        )
        rows, review = build(
            {"33/read.txt": ["a - k", "b - k", "c - k", "odd book - q"]}, rules
        )
        self.assertEqual(rows[3].rating_raw, "3")
        self.assertEqual(kinds(review), ["override-mismatch", "override-applied"])

    def test_absent_override_and_term_files_are_not_checked(self) -> None:
        _, review = build({"34/read.txt": ["a - k"]})
        self.assertEqual(review, ())

    def test_term_expect_mismatch_is_an_error(self) -> None:
        _, review = build({"31-32/seen.txt": ["another film - k", "x - k"]})
        item = next(r for r in review if r.kind == "term-mismatch")
        self.assertEqual(
            (item.line, item.text, item.error), (1, "another film - k", True)
        )

    def test_term_expect_match_is_quiet_and_dates_the_block(self) -> None:
        rows, review = build({"31-32/seen.txt": ["  first film - k ", "x - k"]})
        self.assertEqual(review, ())
        self.assertEqual([r.date for r in rows], [date(2031, 12, 31)] * 2)

    def test_term_on_a_missing_line_is_a_mismatch(self) -> None:
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes({"lines = [1, 2]": "lines = [5, 6]"})
        )
        _, review = build({"31-32/seen.txt": ["x - k"]}, rules)
        item = next(r for r in review if r.kind == "term-mismatch")
        self.assertEqual((item.line, item.text), (5, ""))

    def test_sections_apply_until_the_next_section(self) -> None:
        rows, _ = build({"34/read.txt": ["Work", "a - k", "Fun:", "b - k"]})
        self.assertEqual(
            [(r.title, r.section) for r in rows], [("a", "work"), ("b", "fun")]
        )

    def test_sections_reset_per_file(self) -> None:
        rows, _ = build(
            {"34/read.txt": ["Work", "a - k"], "34/read more.txt": ["b - k"]}
        )
        self.assertEqual(rows[1].section, "")

    def test_rewatch_comment_and_split_code_reach_the_row(self) -> None:
        rows, _ = build({"34/seen.txt": ["(a film) - k/o (again)"]})
        self.assertEqual(
            (rows[0].medium, rows[0].rewatch, rows[0].comment, rows[0].rating_raw),
            ("film", True, "again", "3/2"),
        )


class ProgressTest(unittest.TestCase):
    def test_progress_note_skipped_when_rated_later(self) -> None:
        rows, review = build(
            {"34/read.txt": ["*Long Book - 1/2"], "35/read.txt": ["* long book - k"]}
        )
        self.assertEqual(
            [(r.title, r.source_file) for r in rows], [("long book", "35/read.txt")]
        )
        item = next(r for r in review if r.kind == "progress-skipped")
        self.assertEqual(
            (item.rel_path, item.line, item.text),
            ("34/read.txt", 1, "*Long Book - 1/2"),
        )

    def test_progress_note_kept_unrated_when_never_finished(self) -> None:
        rows, review = build({"34/read.txt": ["*Long Book - 1/2"]})
        self.assertEqual((rows[0].title, rows[0].rating_raw), ("Long Book", ""))
        self.assertEqual(kinds(review), ["progress-unmatched", "unrated"])

    def test_a_later_line_of_the_same_file_finishes_a_progress_note(self) -> None:
        rows, review = build({"34/read.txt": ["long book - 1/2", "Long Book - k"]})
        self.assertEqual([r.source_line for r in rows], [2])
        self.assertEqual(kinds(review), ["progress-skipped"])

    def test_a_same_dated_later_file_does_not_finish_a_progress_note(self) -> None:
        rows, review = build(
            {"34/read.txt": ["long book - 1/2"], "34/read2.txt": ["long book - k"]}
        )
        self.assertEqual(rows[0].date, rows[1].date)
        self.assertEqual(len(rows), 2)
        self.assertIn("progress-unmatched", kinds(review))

    def test_an_earlier_dated_later_file_does_not_finish_a_progress_note(self) -> None:
        rows, review = build(
            {"35/read.txt": ["long book - 1/2"], "35/read2 #34.txt": ["long book - k"]}
        )
        self.assertEqual(rows[1].date, date(2034, 12, 31))
        self.assertEqual(len(rows), 2)
        self.assertIn("progress-unmatched", kinds(review))

    def test_an_earlier_line_of_the_same_file_does_not_finish_one(self) -> None:
        rows, review = build({"34/read.txt": ["long book - k", "long book - 1/2"]})
        self.assertEqual(len(rows), 2)
        self.assertIn("progress-unmatched", kinds(review))

    def test_undated_progress_follows_line_order_then_file_order(self) -> None:
        rows, review = build({"31-32/read.txt": ["long book - 1/2", "long book - k"]})
        self.assertEqual([r.source_line for r in rows], [2])
        self.assertIn("progress-skipped", kinds(review))
        rows, review = build(
            {
                "31-32/read2.txt": ["long book - 1/2"],
                "31-32/read.txt": ["long book - k"],
            }
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("progress-unmatched", kinds(review))

    def test_a_later_file_finishes_an_undated_progress_note(self) -> None:
        rows, review = build(
            {
                "31-32/read.txt": ["Long, Book! - 1/2"],
                "31-32/read2.txt": ["long book - k"],
            }
        )
        self.assertEqual([r.source_file for r in rows], ["31-32/read2.txt"])
        self.assertIn("progress-skipped", kinds(review))

    def test_an_earlier_rating_or_another_medium_does_not_finish_one(self) -> None:
        for files in (
            {"35/read.txt": ["long book - 1/2"], "34/read.txt": ["long book - k"]},
            {"34/read.txt": ["long book - 1/2"], "35/seen.txt": ["long book - k"]},
            {"34/read.txt": ["long book - 1/2"], "35/read.txt": ["long book"]},
        ):
            with self.subTest(files=files):
                rows, review = build(files)
                self.assertEqual(len(rows), 2)
                self.assertIn("progress-unmatched", kinds(review))


class ReviewItemsTest(unittest.TestCase):
    def test_file_with_no_medium_is_one_error_item(self) -> None:
        rows, review = build({"33/notes.txt": ["a - k"]})
        self.assertEqual(rows, ())
        self.assertEqual(
            [(r.kind, r.line, r.error) for r in review], [("no-medium", 0, True)]
        )

    def test_every_non_entry_line_is_accounted_for(self) -> None:
        lines = ["List of films", "", "-", "Work", "a - k", "~~~"]
        rows, review = build({"34/seen.txt": lines})
        self.assertEqual(len(rows), 1)
        self.assertEqual(kinds(review), ["unparsed"])  # "~~~"
        self.assertEqual((review[0].line, review[0].text), (6, "~~~"))

    def test_legend_items_and_order_check(self) -> None:
        in_order = legend_line(["778899", "445566", "112233"])  # keys 3, 2, 1
        reversed_order = legend_line(["112233", "445566", "778899"])
        _, review = build_lines({"34/seen.docx": (in_order,)})
        self.assertEqual(kinds(review), ["legend"])
        self.assertFalse(review[0].error)
        _, review = build_lines({"34/seen.docx": (reversed_order,)})
        self.assertEqual(kinds(review), ["legend-order"])
        self.assertFalse(review[0].error)

    def test_any_legend_order_is_fine_when_the_rules_set_none(self) -> None:
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes({'legend_order = ["3", "2", "1"]\n': ""})
        )
        line = legend_line(["112233", "445566", "000000"])
        _, review = build_lines({"34/seen.docx": (line,)}, rules)
        self.assertEqual(kinds(review), ["legend"])
        self.assertIn("112233 = 1, 445566 = 2, 000000 = ?", review[0].detail)

    def test_a_legend_line_keeps_its_trailing_entry(self) -> None:
        line = marked_line(
            "34/seen.docx",
            [("Wingdings/F041", "778899", "-"), ("Wingdings/F041", "445566", "-"),
             ("Wingdings/F041", "778899", "a film")],
        )  # fmt: skip
        rows, review = build_lines({"34/seen.docx": (line,)})
        self.assertEqual([(r.title, r.color) for r in rows], [("a film", "778899")])
        self.assertEqual(kinds(review), ["legend-order"])

    def test_each_entry_note_is_a_review_item(self) -> None:
        line = marked_line(
            "34/seen.docx",
            [("Wingdings/F041", "112233", "outlier"), ("Wingdings/F041", "FF00FF", "odd"),
             ("Wingdings/F041", None, "bare"), ("Wingdings/F041", "778899", "-")],
        )  # fmt: skip
        rows, review = build_lines({"34/seen.docx": (line,)})
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            kinds(review),
            [
                "shape-conflict",
                "color-outlier",
                "unrated",
                "no-color",
                "stray-mark",
                "unrated",
            ],
        )
        self.assertFalse(any(item.error for item in review))

    def test_unplaced_rows_keep_no_date(self) -> None:
        rows, review = build({"31-32/read.txt": ["a - k"]})
        self.assertIsNone(rows[0].date)
        self.assertIn("unplaced", kinds(review))

    def test_only_error_kinds_are_errors(self) -> None:
        self.assertLessEqual(parse_rows.ERROR_KINDS, set(parse_rows.REVIEW_KINDS))
        self.assertEqual(
            len(set(parse_rows.REVIEW_KINDS)), len(parse_rows.REVIEW_KINDS)
        )
        _, review = build(
            {
                "33/notes.txt": ["x"],
                "31-32/seen.txt": ["y - q", "~~~"],
                "33/read.txt": ["z"],
            }
        )
        self.assertTrue(review)
        for item in review:
            self.assertEqual(item.error, item.kind in parse_rows.ERROR_KINDS, item.kind)
            self.assertIn(item.kind, parse_rows.REVIEW_KINDS)

    def test_every_kind_has_a_detail(self) -> None:
        self.assertEqual(set(parse_review.DETAILS), set(parse_review.REVIEW_KINDS))

    def test_every_entry_note_has_a_review_kind(self) -> None:
        # Read off parse_entries' source, so a note added there later cannot be
        # dropped silently here: each dashed string literal it holds is a note name.
        tree = ast.parse(Path(parse_entries.__file__).read_text(encoding="utf-8"))
        notes = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _NOTE_NAME.fullmatch(node.value)
        }
        self.assertTrue(notes)
        self.assertLessEqual(
            notes, set(parse_rows.REVIEW_KINDS) - parse_rows.ERROR_KINDS
        )


class TitleKeyTest(unittest.TestCase):
    def test_title_key_folds_case_punctuation_and_spaces(self) -> None:
        self.assertEqual(parse_rows.title_key("  The  Long, BOOK!  "), "the long book")
        self.assertEqual(
            parse_rows.title_key("Straße"), parse_rows.title_key("STRASSE")
        )


_PATHS = ("34/read.txt", "35/seen.txt", "33/read.txt", "31-32/seen.txt", "33/notes.txt")
_LINE = st.text(alphabet="ab kqo/-*()~1:", max_size=12)


class FuzzTest(unittest.TestCase):
    @given(
        st.dictionaries(
            st.sampled_from(_PATHS), st.lists(_LINE, max_size=6), max_size=3
        )
    )
    def test_fuzz_build_rows(self, files: dict[str, list[str]]) -> None:
        sources = {path: text_lines(path, texts) for path, texts in files.items()}
        rows, review = build_lines(sources)
        named = {(item.rel_path, item.line) for item in review}
        for path, lines in sources.items():
            for line in lines:
                found = [
                    r
                    for r in rows
                    if (r.source_file, r.source_line) == (path, line.number)
                ]
                self.assertLessEqual(len(found), 1)
                accounted = (
                    bool(found)
                    or (path, line.number) in named
                    or (path, 0) in named
                    or parse_entries.parse_line(line, RULES).kind in _LAYOUT_KINDS
                )
                self.assertTrue(accounted, (path, line.text))


if __name__ == "__main__":
    unittest.main()
