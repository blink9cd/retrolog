"""Tests for parse_rules: loading and checking the operator's rules file."""

import datetime
import sys
import unicodedata
import unittest
from collections.abc import Mapping
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_fixtures  # noqa: E402
import parse_rules  # noqa: E402

_SAMPLE = _TOOL / "samples" / "rules.example.toml"
# The fixture's [shapes] table, whole, for the cases that replace or drop it.
_SHAPES_BLOCK = '[shapes]\n"Wingdings/F041" = ["3"]\n"Wingdings/F042" = ["1", "2"]\n'
# The fixture's [[terms]] and [[overrides]] entries, whole.
_TERMS_BLOCK = (
    '[[terms]]\nfile = "31-32/seen.txt"\nlines = [1, 2]\ndate = 2031-12-31\n'
    'expect = "first film - k"\n'
)
_OVERRIDES_BLOCK = (
    '[[overrides]]\nfile = "33/read.txt"\nline = 4\nexpect = "odd book - q"\n'
    'action = "rating"\nvalue = "3"\n'
)
_MEDIA_BLOCK = '[media]\nbook = ["*read*"]\nfilm = ["*seen*"]\n'


def _load(replace: Mapping[str, str] | None = None) -> parse_rules.Rules:
    """Load the fixture rules with ``replace``'s substitutions applied."""
    return parse_rules.load_rules(parse_fixtures.rules_bytes(replace))


def _outcome(data: bytes) -> parse_rules.Rules | parse_rules.RulesError:
    """What ``load_rules`` makes of ``data``: its Rules, or the RulesError it raised.

    Any other exception escapes and fails the calling test, which is the property.
    """
    try:
        return parse_rules.load_rules(data)
    except parse_rules.RulesError as error:
        return error


class LoadRulesTest(unittest.TestCase):
    def test_fixture_loads(self) -> None:
        rules = parse_rules.load_rules(parse_fixtures.rules_bytes())
        self.assertEqual(rules.letters, {"x": "1", "o": "2", "k": "3"})
        self.assertEqual(rules.colors["445566"], "2")
        self.assertEqual(rules.shapes["Wingdings/F042"], frozenset({"1", "2"}))
        self.assertEqual(rules.terms[0].date, datetime.date(2031, 12, 31))
        self.assertEqual((rules.terms[0].first_line, rules.terms[0].last_line), (1, 2))
        self.assertEqual(rules.overrides[0].action, "rating")
        self.assertEqual(rules.dates.seasons, {"harvest": 10})

    def test_codes(self) -> None:
        rules = parse_rules.load_rules(parse_fixtures.rules_bytes())
        self.assertEqual(parse_rules.rating_for_code(rules, "K"), "3")
        self.assertEqual(parse_rules.rating_for_code(rules, "k/o"), "3/2")
        self.assertEqual(parse_rules.rating_for_code(rules, "o/k"), "2/3")
        self.assertIsNone(parse_rules.rating_for_code(rules, "q"))
        self.assertIsNone(parse_rules.rating_for_code(rules, "k/k"))
        self.assertIsNone(parse_rules.rating_for_code(rules, "k/q"))

    def test_medium_by_file_name_glob_case_insensitive(self) -> None:
        rules = parse_rules.load_rules(parse_fixtures.rules_bytes())
        self.assertEqual(parse_rules.medium_for(rules, "Books READ.txt"), "book")
        self.assertEqual(parse_rules.medium_for(rules, "films seen.doc"), "film")
        self.assertIsNone(parse_rules.medium_for(rules, "notes.txt"))

    def test_refusals_name_the_key(self) -> None:
        cases = {
            "schema = 1": ("schema = 2", "schema"),
            "color_tolerance = 40": ("color_tolerance = -1", "color_tolerance"),
            '"112233" = "1"': ('"11223" = "1"', "colors"),
            'x = "1"': ('xy = "1"', "letters"),
            "lines = [1, 2]": ("lines = [2, 1]", "terms"),
            'action = "rating"': ('action = "rename"', "overrides"),
            "progress = ": ("progresss = ", "lines"),
            "earlier_words": ("earlier_wordz", "dates"),
        }
        for old, (new, key) in cases.items():
            with (
                self.subTest(key=key),
                self.assertRaises(parse_rules.RulesError) as caught,
            ):
                parse_rules.load_rules(parse_fixtures.rules_bytes({old: new}))
            self.assertIn(key, str(caught.exception))

    def test_progress_pattern_needs_a_title_group(self) -> None:
        bad = parse_fixtures.rules_bytes({"(?P<title>.*?)": "(.*?)"})
        with self.assertRaises(parse_rules.RulesError):
            parse_rules.load_rules(bad)

    def test_deeply_nested_array_is_a_rules_error(self) -> None:
        # tomllib parses nested arrays recursively and raises RecursionError, which
        # neither UnicodeDecodeError nor TOMLDecodeError covers; the fuzz test's
        # 400-byte inputs are too short to nest this deep.
        depth = 2000
        data = b"schema = 1\nx = " + b"[" * depth + b"]" * depth + b"\n"
        with self.assertRaises(parse_rules.RulesError) as caught:
            parse_rules.load_rules(data)
        self.assertEqual(str(caught.exception), "rules file: not valid UTF-8 TOML")

    def test_an_overlong_toml_integer_is_a_rules_error(self) -> None:
        # Regression: tomllib raises a bare ValueError, not TOMLDecodeError, for an
        # integer past int()'s 4,300-digit limit, which escaped as a traceback.
        with self.assertRaises(parse_rules.RulesError) as caught:
            parse_rules.load_rules(b"schema = " + b"1" * 5000 + b"\n")
        self.assertEqual(str(caught.exception), "rules file: not valid UTF-8 TOML")

    def test_invalid_toml_and_bad_regex_are_rules_errors(self) -> None:
        for data in (b"schema = ", parse_fixtures.rules_bytes({"^list of ": "(["})):
            with (
                self.subTest(data=data[:20]),
                self.assertRaises(parse_rules.RulesError),
            ):
                parse_rules.load_rules(data)

    def test_split_needs_two_known_letters(self) -> None:
        rules = parse_rules.load_rules(parse_fixtures.rules_bytes())
        self.assertIsNone(parse_rules.rating_for_code(rules, "/"))
        self.assertIsNone(parse_rules.rating_for_code(rules, "x/o/k"))

    @given(st.binary(max_size=400))
    def test_fuzz_load_rules(self, data: bytes) -> None:
        self.assertIsInstance(
            _outcome(data), (parse_rules.Rules, parse_rules.RulesError)
        )

    @given(st.integers(min_value=0), st.integers(min_value=0, max_value=40))
    def test_damaged_fixture_is_rules_or_error(self, start: int, length: int) -> None:
        """Deleting any slice of a valid file yields Rules or RulesError, nothing else."""
        text = parse_fixtures.RULES_TOML
        start %= len(text)
        damaged = (text[:start] + text[start + length :]).encode("utf-8")
        outcome = _outcome(damaged)
        self.assertIsInstance(outcome, (parse_rules.Rules, parse_rules.RulesError))


class LoadedValuesTest(unittest.TestCase):
    def test_colors_are_stored_upper_case(self) -> None:
        rules = _load({'"112233" = "1"': '"aabbcc" = "1"'})
        self.assertEqual(rules.colors["AABBCC"], "1")

    def test_glyph_hex_is_stored_upper_case(self) -> None:
        rules = _load({"Wingdings/F041": "Wingdings/f0a1"})
        self.assertEqual(rules.shapes["Wingdings/F0A1"], frozenset({"3"}))

    def test_patterns_ignore_case(self) -> None:
        rules = _load()
        self.assertTrue(rules.headings[0].search("LIST OF films"))
        match = rules.progress.search("Long Book - 3/9")
        self.assertIsNotNone(match)
        assert match is not None  # narrows the type for the line below
        self.assertEqual(match.group("title"), "Long Book")

    def test_words_and_sections_are_casefolded(self) -> None:
        rules = _load({'"work"': '"WORK"', '"first half"': '"First Half"'})
        self.assertEqual(rules.sections, ("work", "fun"))
        self.assertEqual(rules.dates.earlier_words, ("first half",))

    def test_media_keep_file_order(self) -> None:
        rules = _load({_MEDIA_BLOCK: '[media]\nfilm = ["*seen*"]\nbook = ["*read*"]\n'})
        self.assertEqual(rules.media, (("film", ("*seen*",)), ("book", ("*read*",))))
        self.assertEqual(parse_rules.medium_for(rules, "read and seen.txt"), "film")

    def test_optional_tables_default_to_empty(self) -> None:
        rules = _load(
            {
                _SHAPES_BLOCK: "",
                _TERMS_BLOCK: "",
                _OVERRIDES_BLOCK: "",
                'legend_order = ["3", "2", "1"]\n': "",
            }
        )
        self.assertEqual(
            (rules.shapes, rules.terms, rules.overrides, rules.legend_order),
            ({}, (), (), ()),
        )

    def test_accepted_override_values(self) -> None:
        cases = {
            ('value = "3"', 'value = "3/2"'): "3/2",
            ('action = "rating"\nvalue = "3"', 'action = "title"\nvalue = "A Name"'): (
                "A Name"
            ),
            ('action = "rating"\nvalue = "3"', 'action = "skip"'): None,
            ('action = "rating"\nvalue = "3"', 'action = "unrated"'): None,
        }
        for (old, new), value in cases.items():
            with self.subTest(new=new):
                self.assertEqual(_load({old: new}).overrides[0].value, value)

    def test_key_known_only_through_colors_is_accepted(self) -> None:
        rules = _load({'"778899" = "3"': '"778899" = "9"', '["3"]': '["9"]'})
        self.assertEqual(rules.shapes["Wingdings/F041"], frozenset({"9"}))

    def test_entry_module_puts_both_source_folders_on_the_path(self) -> None:
        sys.path.insert(0, str(_TOOL))
        self.addCleanup(sys.path.remove, str(_TOOL))
        import retrolog_parse

        self.assertEqual(
            [folder.name for folder in retrolog_parse.SOURCE_DIRS], ["src", "src"]
        )
        self.assertTrue(
            all(str(folder) in sys.path for folder in retrolog_parse.SOURCE_DIRS)
        )
        self.assertTrue((retrolog_parse.SOURCE_DIRS[1] / "survey_guard.py").is_file())

    def test_sample_rules_load(self) -> None:
        rules = parse_rules.load_rules(_SAMPLE.read_bytes())
        self.assertEqual(parse_rules.medium_for(rules, "my movies.txt"), "film")
        self.assertEqual(parse_rules.rating_for_code(rules, "a/d"), "1/4")


# Each case: the substitutions that break the fixture, and a text the message must hold.
_REFUSALS: tuple[tuple[Mapping[str, str], str], ...] = (
    ({"schema = 1": "schema = 1\nextra = 1"}, "extra"),
    ({"schema = 1": 'schema = "1"'}, "schema"),
    ({"schema = 1": "schema = true"}, "schema"),
    ({_MEDIA_BLOCK: ""}, "media"),
    ({_MEDIA_BLOCK: "media = 1\n"}, "media"),
    ({_MEDIA_BLOCK: "[media]\n"}, "media"),
    ({'book = ["*read*"]': 'tv = ["*read*"]'}, "media"),
    ({'book = ["*read*"]': 'book = "*read*"'}, "media.book"),
    ({'book = ["*read*"]': "book = []"}, "media.book"),
    ({'book = ["*read*"]': "book = [1]"}, "media.book"),
    ({'letters = { x = "1", o = "2", k = "3" }': "letters = 1"}, "ratings.letters"),
    ({'x = "1"': 'X = "1"'}, "ratings.letters"),
    ({'x = "1"': 'x = ""'}, "ratings.letters"),
    ({'x = "1"': 'x = "1/2"'}, "ratings.letters"),
    ({'x = "1"': "x = 1"}, "ratings.letters"),
    ({'split_separator = "/"': 'split_separator = ""'}, "split_separator"),
    ({'split_separator = "/"': 'split_separator = "q"'}, "split_separator"),
    ({'split_separator = "/"': "split_separator = 1"}, "split_separator"),
    ({'"112233" = "1"': '"112233" = 1'}, "ratings.colors"),
    ({"color_tolerance = 40": "color_tolerance = true"}, "color_tolerance"),
    ({"color_tolerance = 40": "color_tolerance = 443"}, "color_tolerance"),
    ({'["3", "2", "1"]': '["3", "2", "9"]'}, "legend_order"),
    ({'legend_order = ["3", "2", "1"]': 'legend_order = "3"'}, "legend_order"),
    ({"Wingdings/F041": "Wingdings-F041"}, "shapes"),
    ({'"Wingdings/F041" = ["3"]': '"Wingdings/F041" = ["9"]'}, "shapes"),
    ({'"Wingdings/F041" = ["3"]': '"Wingdings/F041" = []'}, "shapes"),
    ({"schema = 1": "schema = 1\nshapes = 1", _SHAPES_BLOCK: ""}, "shapes"),
    ({"headings = ['^list of ']": "headings = '^list of '"}, "lines.headings"),
    ({'sections = ["work", "fun"]': 'sections = ["work", 1]'}, "lines.sections"),
    ({"progress = ": "progress = 1 # "}, "lines.progress"),
    ({"century = 2000": "century = 2050"}, "dates.century"),
    ({"century = 2000": "century = 0"}, "dates.century"),
    ({"(\\d{2})-(\\d{2})": "(\\d{2})-\\d{2}"}, "dates.two_year_folder"),
    ({"'^(\\d{2})$'": "'^(\\d)(\\d)$'"}, "dates.one_year_folder"),
    ({"#(\\d{2})(?!\\d)": "#\\d{2}"}, "dates.explicit_year"),
    ({'["first half"]': "[1]"}, "dates.earlier_words"),
    ({"harvest = 10": "harvest = 13"}, "dates.seasons"),
    ({"harvest = 10": 'harvest = "10"'}, "dates.seasons"),
    ({"seasons = { harvest = 10 }": "seasons = 10"}, "dates.seasons"),
    ({"[[terms]]": "[terms]"}, "terms"),
    ({"schema = 1": "schema = 1\nterms = [1]", _TERMS_BLOCK: ""}, "terms[0]"),
    ({'expect = "first film - k"': 'expect = "first film - k"\nx = 1'}, "terms[0]"),
    ({"date = 2031-12-31\n": ""}, "terms[0].date"),
    ({"date = 2031-12-31": 'date = "2031-12-31"'}, "terms[0].date"),
    ({"date = 2031-12-31": "date = 2031-12-31T00:00:00"}, "terms[0].date"),
    ({"lines = [1, 2]": "lines = [0, 1]"}, "terms[0].lines"),
    ({"lines = [1, 2]": "lines = [1]"}, "terms[0].lines"),
    ({"lines = [1, 2]": 'lines = ["1", "2"]'}, "terms[0].lines"),
    ({'"31-32/seen.txt"': '"/31-32/seen.txt"'}, "terms[0].file"),
    ({'"31-32/seen.txt"': '"../seen.txt"'}, "terms[0].file"),
    ({'"31-32/seen.txt"': "'31-32\\seen.txt'"}, "terms[0].file"),
    ({'"31-32/seen.txt"': '"31-32//seen.txt"'}, "terms[0].file"),
    ({'"31-32/seen.txt"': '"./seen.txt"'}, "terms[0].file"),
    ({'"31-32/seen.txt"': '""'}, "terms[0].file"),
    ({'expect = "first film - k"': "expect = 1"}, "terms[0].expect"),
    ({"schema = 1": "schema = 1\noverrides = [1]", _OVERRIDES_BLOCK: ""}, "overrides"),
    ({"line = 4": "line = 0"}, "overrides[0].line"),
    ({"line = 4": "line = true"}, "overrides[0].line"),
    ({'value = "3"\n': ""}, "overrides[0].value"),
    ({'action = "rating"': 'action = "skip"'}, "overrides[0].value"),
    ({'value = "3"': 'value = "9"'}, "overrides[0].value"),
    ({'value = "3"': 'value = "3/3"'}, "overrides[0].value"),
    ({'value = "3"': 'value = "3/9"'}, "overrides[0].value"),
    ({'action = "rating"\nvalue = "3"': 'action = "title"\nvalue = ""'}, "value"),
    ({'value = "3"': "value = 3"}, "overrides[0].value"),
    ({'action = "rating"': "action = 1"}, "overrides[0].action"),
)


class RefusalTest(unittest.TestCase):
    def test_every_broken_rule_is_refused_by_name(self) -> None:
        for replace, key in _REFUSALS:
            with (
                self.subTest(replace=replace),
                self.assertRaises(parse_rules.RulesError) as caught,
            ):
                _load(replace)
            self.assertIn(key, str(caught.exception))

    def test_non_utf8_is_refused(self) -> None:
        with self.assertRaises(parse_rules.RulesError) as caught:
            parse_rules.load_rules(b"\xff\xfe")
        self.assertEqual(str(caught.exception), "rules file: not valid UTF-8 TOML")
        self.assertIsNone(caught.exception.__cause__)

    def test_overlong_or_deep_patterns_are_refused(self) -> None:
        """re.compile raises OverflowError and RecursionError, not only re.error."""
        for bad in ("a{99999999999}", "(" * 5000 + ")" * 5000):
            with (
                self.subTest(bad=bad[:16]),
                self.assertRaises(parse_rules.RulesError) as caught,
            ):
                _load({"^list of ": bad})
            self.assertEqual(
                str(caught.exception), "lines.headings: not a valid pattern"
            )
            self.assertIsNone(caught.exception.__cause__)

    def test_keys_equal_after_normalizing_are_refused(self) -> None:
        cases = (
            ('"112233" = "1"', '"aabbcc" = "1", "AABBCC" = "2"', "ratings.colors"),
            (
                '"Wingdings/F041" = ["3"]',
                '"Wingdings/f041" = ["3"]\n"Wingdings/F041" = ["3"]',
                "shapes",
            ),
            ("harvest = 10", "harvest = 10, Harvest = 11", "dates.seasons"),
        )
        for old, new, key in cases:
            with (
                self.subTest(key=key),
                self.assertRaises(parse_rules.RulesError) as caught,
            ):
                _load({old: new})
            self.assertIn(key, str(caught.exception))
            self.assertIn("twice", str(caught.exception))

    def test_bad_regex_names_its_key(self) -> None:
        with self.assertRaises(parse_rules.RulesError) as caught:
            _load({"#(\\d{2})(?!\\d)": "#(["})
        self.assertIn("dates.explicit_year", str(caught.exception))


class TermBlockTest(unittest.TestCase):
    def second_block(self, file: str, lines: str) -> str:
        """The fixture's term block followed by a second one for ``file``."""
        return _TERMS_BLOCK + _TERMS_BLOCK.replace("31-32/seen.txt", file).replace(
            "[1, 2]", lines
        )

    def test_overlapping_blocks_for_one_file_are_refused(self) -> None:
        for lines in ("[2, 3]", "[1, 1]", "[1, 9]"):
            with (
                self.subTest(lines=lines),
                self.assertRaises(parse_rules.RulesError) as caught,
            ):
                _load({_TERMS_BLOCK: self.second_block("31-32/seen.txt", lines)})
            message = str(caught.exception)
            self.assertIn("terms", message)
            self.assertNotIn("seen", message)

    def test_disjoint_blocks_or_other_files_are_accepted(self) -> None:
        for file, lines in (("31-32/seen.txt", "[3, 9]"), ("33/seen.txt", "[1, 2]")):
            with self.subTest(file=file, lines=lines):
                rules = _load({_TERMS_BLOCK: self.second_block(file, lines)})
                self.assertEqual(len(rules.terms), 2)

    def test_rule_paths_are_stored_nfc(self) -> None:
        decomposed = unicodedata.normalize("NFD", "33/caf\u00e9 read.txt")
        rules = _load(
            {'"31-32/seen.txt"': f'"{decomposed}"', '"33/read.txt"': f'"{decomposed}"'}
        )
        composed = unicodedata.normalize("NFC", decomposed)
        self.assertEqual(
            (rules.terms[0].file, rules.overrides[0].file), (composed,) * 2
        )


if __name__ == "__main__":
    unittest.main()
