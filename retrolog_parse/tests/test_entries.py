"""Tests for parse_entries: one source line and the rules in, one parsed line out."""

import sys
import unittest
from collections.abc import Sequence
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_entries  # noqa: E402
import parse_fixtures  # noqa: E402
import parse_rules  # noqa: E402
from parse_entries import ParsedEntry  # noqa: E402
from parse_types import Mark, Segment, SourceLine  # noqa: E402

RULES = parse_rules.load_rules(parse_fixtures.rules_bytes())
# The source-file path every built line claims; invented, like all fixture values.
_REL_PATH = "30-31/x.docx"
# The characters the fuzz property ignores when comparing a title with its input.
_IGNORED = frozenset("*()[]")

MarkSpec = tuple[str, str | None]


def text_line(text: str) -> SourceLine:
    """A plain-text line, as the .txt reader builds one."""
    return SourceLine(_REL_PATH, 1, text, (Segment((), text, ()),))


def marked_line(pairs: Sequence[tuple[MarkSpec, str]]) -> SourceLine:
    """A Word line of segments, each opened by one (glyph, color) mark."""
    segments = tuple(
        Segment((Mark(glyph, color),), text, ()) for (glyph, color), text in pairs
    )
    return SourceLine(_REL_PATH, 1, "".join(text for _, text in pairs), segments)


def entry(text: str) -> ParsedEntry | None:
    """``parse_text_entry`` against the fixture rules."""
    return parse_entries.parse_text_entry(text, RULES)


def _squeezed(text: str) -> str:
    """``text`` with whitespace and the ignored characters removed."""
    return "".join(char for char in text if char not in _IGNORED and not char.isspace())


class TextEntryTest(unittest.TestCase):
    def test_letter_code_forms(self) -> None:
        for text in (
            "*a film - k",
            "* a film - k",
            "*a film -k",
            "a film - k",
            "a film\t\tk",
            "a film      k",
            "a film k",
            "a film\t\t k\t",
            "a film- k",
        ):
            with self.subTest(text=text):
                got = entry(text)
                assert got is not None
                self.assertEqual(
                    (got.title, got.rating, got.code), ("a film", "3", "k")
                )

    def test_split_code(self) -> None:
        got = entry("*a film - k/o")
        assert got is not None
        self.assertEqual((got.rating, got.code), ("3/2", "k/o"))

    def test_brackets_mean_rewatch(self) -> None:
        for text in (
            "(a film - k)",
            "[a film - k]",
            "(* a film - k)",
            "* (a film - k)",
            "(*a film - k)",
            "(a film) - k",
        ):
            with self.subTest(text=text):
                got = entry(text)
                assert got is not None
                self.assertEqual(
                    (got.title, got.rating, got.rewatch), ("a film", "3", True)
                )

    def test_a_rewatch_bracket_pairs_with_its_own_closer(self) -> None:
        for text in ("(a film) - k (great)", "(a film - k) (great)"):
            with self.subTest(text=text):
                got = entry(text)
                assert got is not None
                self.assertEqual(
                    (got.title, got.rating, got.rewatch, got.comment),
                    ("a film", "3", True, "great"),
                )

    def test_a_leading_parenthesized_word_is_not_a_rewatch(self) -> None:
        got = entry("(500) days - k")
        assert got is not None
        self.assertEqual(
            (got.title, got.rating, got.rewatch), ("(500) days", "3", False)
        )

    def test_inner_parentheses_are_not_a_rewatch(self) -> None:
        got = entry("*a film (part two) - k")
        assert got is not None
        self.assertEqual((got.title, got.rewatch), ("a film (part two)", False))

    def test_an_unclosed_bracket_is_not_a_rewatch(self) -> None:
        got = entry("(a film - k")
        assert got is not None
        self.assertEqual((got.title, got.rating, got.rewatch), ("(a film", "3", False))

    def test_trailing_comment(self) -> None:
        got = entry("* a film -  o (too long, though)")
        assert got is not None
        self.assertEqual(
            (got.title, got.rating, got.comment), ("a film", "2", "too long, though")
        )

    def test_trailing_parentheses_after_no_code_stay_in_the_title(self) -> None:
        for text in ("a film q (part two)", "a film Q (part two)"):
            with self.subTest(text=text):
                got = entry(text)
                assert got is not None
                self.assertEqual((got.title, got.comment, got.rating), (text, "", None))

    def test_work_year(self) -> None:
        got = entry("*a film (1968) - k")
        assert got is not None
        self.assertEqual((got.title, got.work_year), ("a film", 1968))

    def test_a_year_out_of_range_stays_in_the_title(self) -> None:
        got = entry("a film (1869) - k")
        assert got is not None
        self.assertEqual((got.title, got.work_year), ("a film (1869)", None))

    def test_no_code_is_an_unrated_entry(self) -> None:
        for text in ("*a book", "a book", "a book volume i", "a book ii"):
            with self.subTest(text=text):
                got = entry(text)
                assert got is not None
                self.assertEqual(
                    (got.title, got.rating, got.code), (text.lstrip("*"), None, None)
                )

    def test_upper_case_letters_are_not_codes(self) -> None:
        got = entry("a film K")
        assert got is not None
        self.assertEqual((got.title, got.rating, got.notes), ("a film K", None, ()))

    def test_a_title_starting_with_a_code_letter_keeps_it(self) -> None:
        got = entry("k is for killer - o")
        assert got is not None
        self.assertEqual((got.title, got.rating), ("k is for killer", "2"))

    def test_unknown_code_letter_stays_in_the_title_and_is_noted(self) -> None:
        got = entry("*odd book - q")
        assert got is not None
        self.assertEqual(
            (got.title, got.rating, got.notes),
            ("odd book - q", None, ("unknown-code",)),
        )

    def test_every_unknown_code_form_stays_in_the_title_and_is_noted(self) -> None:
        for text in (
            "a film - K",
            "a film -K",
            "a film - q",
            "a film - k/q",
            "a film - k/k",
            "a film - q (note)",
        ):
            with self.subTest(text=text):
                got = entry(text)
                assert got is not None
                self.assertEqual(
                    (got.title, got.rating, got.comment, got.notes),
                    (text, None, "", ("unknown-code",)),
                )

    def test_strip_unknown_code_removes_every_noted_form(self) -> None:
        for text in (
            "a film - K",
            "a film -K",
            "a film - q",
            "a film - k/q",
            "a film - k/k",
            "a film - q (note)",
        ):
            with self.subTest(text=text):
                got = entry(text)
                assert got is not None
                self.assertEqual(
                    parse_entries.strip_unknown_code(got.title, RULES), "a film"
                )

    def test_strip_unknown_code_keeps_a_title_it_would_empty_or_without_one(
        self,
    ) -> None:
        for title in ("- q", "Malcolm X", "a film"):
            with self.subTest(title=title):
                self.assertEqual(parse_entries.strip_unknown_code(title, RULES), title)

    def test_an_upper_case_last_word_without_a_dash_is_not_noted(self) -> None:
        got = entry("Malcolm X")
        assert got is not None
        self.assertEqual((got.title, got.rating, got.notes), ("Malcolm X", None, ()))

    def test_a_year_after_the_code_is_a_comment_not_a_work_year(self) -> None:
        # Pinned as built: only a year inside the title, before the code, is a work year.
        got = entry("a film - k (1968)")
        assert got is not None
        self.assertEqual(
            (got.title, got.rating, got.comment, got.work_year),
            ("a film", "3", "1968", None),
        )

    def test_whitespace_runs_collapse(self) -> None:
        got = entry("a   long\tfilm - k")
        assert got is not None
        self.assertEqual(got.title, "a long film")

    def test_progress_note(self) -> None:
        got = entry("*a long book - 1/2")
        assert got is not None
        self.assertEqual(
            (got.title, got.progress, got.rating), ("a long book", True, None)
        )

    def test_progress_with_no_title_is_not_an_entry(self) -> None:
        self.assertIsNone(entry(" - 1/2"))

    def test_nothing_left_is_not_an_entry(self) -> None:
        for text in ("", "   ", "*", "( )", "- k", "k", "~~~", "(---) - k"):
            with self.subTest(text=text):
                self.assertIsNone(entry(text))

    @given(st.text(max_size=60))
    def test_fuzz_parse_text_entry(self, text: str) -> None:
        got = entry(text)
        if got is not None:
            self.assertTrue(got.title)
            self.assertEqual(got.title, got.title.strip())
            self.assertIn(_squeezed(got.title), _squeezed(text))


class LineKindTest(unittest.TestCase):
    def kind(self, text: str) -> str:
        return parse_entries.parse_line(text_line(text), RULES).kind

    def test_kinds(self) -> None:
        self.assertEqual(self.kind(""), "blank")
        self.assertEqual(self.kind("  -  "), "divider")
        self.assertEqual(self.kind("---"), "divider")
        self.assertEqual(self.kind("List of things"), "heading")
        self.assertEqual(self.kind("Work:"), "section")
        self.assertEqual(self.kind("FUN"), "section")
        self.assertEqual(self.kind("a film - k"), "entries")

    def test_section_carries_its_casefolded_name(self) -> None:
        parsed = parse_entries.parse_line(text_line("  Work: "), RULES)
        self.assertEqual(
            (parsed.section, parsed.entries, parsed.legend), ("work", (), ())
        )

    def test_a_line_with_no_entry_is_unparsed(self) -> None:
        self.assertEqual(self.kind("~~~"), "unparsed")

    def test_a_lone_mark_on_a_dash_line_is_not_a_divider(self) -> None:
        line = marked_line([(("Wingdings/F041", "778899"), " - ")])
        self.assertEqual(parse_entries.parse_line(line, RULES).kind, "unparsed")


class TextColorTest(unittest.TestCase):
    def colored(self, text: str, colors: tuple[str, ...]) -> ParsedEntry:
        line = SourceLine(_REL_PATH, 1, text, (Segment((), text, colors),))
        (got,) = parse_entries.parse_line(line, RULES).entries
        return got

    def test_first_known_color_rates_an_uncoded_entry(self) -> None:
        got = self.colored("A Film", ("FFFFFF", "445566", "778899"))
        self.assertEqual((got.rating, got.color), ("2", "445566"))

    def test_a_code_wins_over_a_color(self) -> None:
        got = self.colored("A Film - k", ("445566",))
        self.assertEqual((got.rating, got.color), ("3", None))

    def test_no_known_color_leaves_it_unrated(self) -> None:
        got = self.colored("A Film", ("FFFFFF",))
        self.assertEqual((got.rating, got.color), (None, None))

    def test_a_progress_entry_takes_no_color(self) -> None:
        got = self.colored("A Long Book - 1/2", ("445566",))
        self.assertEqual((got.rating, got.color, got.progress), (None, None, True))


class MarkedLineTest(unittest.TestCase):
    def test_color_decides_and_shape_cross_checks(self) -> None:
        line = marked_line([(("Wingdings/F041", "778899"), "A Film")])
        parsed = parse_entries.parse_line(line, RULES)
        self.assertEqual(parsed.kind, "entries")
        (got,) = parsed.entries
        self.assertEqual(
            (got.title, got.rating, got.color, got.notes), ("A Film", "3", "778899", ())
        )

    def test_shape_conflict_keeps_the_color_and_notes_it(self) -> None:
        line = marked_line([(("Wingdings/F042", "778899"), "A Film")])
        (got,) = parse_entries.parse_line(line, RULES).entries
        self.assertEqual((got.rating, got.notes), ("3", ("shape-conflict",)))

    def test_a_glyph_the_rules_do_not_shape_is_not_checked(self) -> None:
        line = marked_line([(("Webdings/0061", "778899"), "A Film")])
        (got,) = parse_entries.parse_line(line, RULES).entries
        self.assertEqual((got.rating, got.notes), ("3", ()))

    def test_nearest_color_within_tolerance(self) -> None:
        self.assertEqual(parse_entries.rating_for_color(RULES, "7A8A9A"), "3")
        self.assertEqual(parse_entries.rating_for_color(RULES, "7a8a9a"), "3")
        self.assertIsNone(parse_entries.rating_for_color(RULES, "FFFFFF"))

    def test_a_malformed_color_is_no_rating(self) -> None:
        for color in ("", "77889", "GG8899", "7788990"):
            with self.subTest(color=color):
                self.assertIsNone(parse_entries.rating_for_color(RULES, color))

    def test_a_tie_between_two_keys_is_no_rating(self) -> None:
        # 446E80 is equally far from 445566 and 448899: (0, 25, 26) from each.
        rules = parse_rules.load_rules(
            parse_fixtures.rules_bytes({'"778899" = "3"': '"448899" = "3"'})
        )
        self.assertIsNone(parse_entries.rating_for_color(rules, "446E80"))
        # A tie between two colors that mean one key is still that key.
        same = parse_rules.load_rules(
            parse_fixtures.rules_bytes({'"778899" = "3"': '"448899" = "2"'})
        )
        self.assertEqual(parse_entries.rating_for_color(same, "446E80"), "2")

    def test_outlier_color_is_unrated_and_noted(self) -> None:
        line = marked_line([(("Wingdings/F041", "FFFFFF"), "A Film")])
        (got,) = parse_entries.parse_line(line, RULES).entries
        self.assertEqual(
            (got.rating, got.color, got.notes), (None, None, ("color-outlier",))
        )

    def test_a_mark_with_no_color_is_noted(self) -> None:
        line = marked_line([(("Wingdings/F041", None), "A Film")])
        (got,) = parse_entries.parse_line(line, RULES).entries
        self.assertEqual((got.rating, got.notes), (None, ("no-color",)))

    def test_legend_then_entry(self) -> None:
        line = marked_line(
            [
                (("Wingdings/F041", "778899"), "-"),
                (("Wingdings/F042", "445566"), "- "),
                (("Wingdings/F042", "112233"), ""),
                (("Wingdings/F041", "778899"), "Late Film"),
            ]
        )
        parsed = parse_entries.parse_line(line, RULES)
        self.assertEqual((parsed.kind, parsed.legend), ("legend", ("3", "2", "1")))
        self.assertEqual([e.title for e in parsed.entries], ["Late Film"])

    def test_a_legend_shows_any_glyph_and_keeps_unknown_colors(self) -> None:
        line = marked_line(
            [
                (("Wingdings/F042", "778899"), " - "),
                (("Wingdings/F041", "FFFFFF"), " - "),
                (("Wingdings/F041", None), ""),
            ]
        )
        parsed = parse_entries.parse_line(line, RULES)
        self.assertEqual(
            (parsed.kind, parsed.legend, parsed.entries),
            ("legend", ("3", None, None), ()),
        )

    def test_two_entries(self) -> None:
        line = marked_line(
            [
                (("Wingdings/F041", "778899"), "First "),
                (("Wingdings/F042", "445566"), "Second"),
            ]
        )
        parsed = parse_entries.parse_line(line, RULES)
        self.assertEqual(
            [(e.title, e.rating) for e in parsed.entries],
            [("First", "3"), ("Second", "2")],
        )

    def test_a_marked_title_is_cleaned(self) -> None:
        line = marked_line([(("Wingdings/F041", "778899"), "  A   Film (1968) - ")])
        (got,) = parse_entries.parse_line(line, RULES).entries
        self.assertEqual((got.title, got.work_year), ("A Film", 1968))

    def test_a_stray_mark_is_noted_on_its_neighbor(self) -> None:
        cases = (
            [
                (("Wingdings/F041", "778899"), "First "),
                (("Wingdings/F042", "445566"), " "),
            ],
            [
                (("Wingdings/F042", "445566"), "- "),
                (("Wingdings/F041", "778899"), "First"),
            ],
        )
        for pairs in cases:
            with self.subTest(pairs=pairs):
                parsed = parse_entries.parse_line(marked_line(pairs), RULES)
                (got,) = parsed.entries
                self.assertEqual(
                    (parsed.kind, got.title, got.notes),
                    ("entries", "First", ("stray-mark",)),
                )

    def test_plain_text_beside_marks_is_its_own_entry(self) -> None:
        line = SourceLine(
            _REL_PATH,
            1,
            "Plain - o Marked",
            (
                Segment((), "Plain - o ", ()),
                Segment((Mark("Wingdings/F041", "778899"),), "Marked", ()),
            ),
        )
        parsed = parse_entries.parse_line(line, RULES)
        self.assertEqual(
            [(e.title, e.rating) for e in parsed.entries],
            [("Plain", "2"), ("Marked", "3")],
        )

    def test_blank_plain_text_beside_a_mark_is_ignored(self) -> None:
        line = SourceLine(
            _REL_PATH,
            1,
            " A Film",
            (
                Segment((), " ", ()),
                Segment((Mark("Wingdings/F041", "778899"),), "A Film", ()),
            ),
        )
        self.assertEqual(
            [e.title for e in parse_entries.parse_line(line, RULES).entries], ["A Film"]
        )

    def test_colored_text_without_a_mark(self) -> None:
        line = SourceLine(
            "30-31/x.docx", 1, "A Film", (Segment((), "A Film", ("445566",)),)
        )
        (got,) = parse_entries.parse_line(line, RULES).entries
        self.assertEqual(got.rating, "2")

    def test_a_lone_mark_with_no_title_is_unparsed(self) -> None:
        line = marked_line([(("Wingdings/F041", "778899"), "  ")])
        self.assertEqual(parse_entries.parse_line(line, RULES).kind, "unparsed")


if __name__ == "__main__":
    unittest.main()
