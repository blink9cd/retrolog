"""Tests for export_config: loading and checking the operator's export config."""

from __future__ import annotations

import datetime
import math
import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_config  # noqa: E402 - after the sys.path inserts above
import parse_checks  # noqa: E402 - after the sys.path inserts above
import parse_rows  # noqa: E402 - after the sys.path inserts above
from export_fixtures import TomlValue, config_toml  # noqa: E402 - after the inserts
from export_strategies import star_values  # noqa: E402 - after the sys.path inserts
from export_types import (  # noqa: E402 - after the sys.path inserts above
    Cutoff,
    LetterboxdConfig,
    ResolvedRow,
)

_SAMPLE = _TOOL / "samples" / "export.example.toml"
_CUTOFF: dict[str, TomlValue] = {
    "file": "2019/films.txt",
    "line": 12,
    "title": "Placeholder Film Nine",
}


def _refusal(data: bytes) -> str:
    """The ConfigError message ``load_config`` gives for ``data``."""
    try:
        export_config.load_config(data)
    except export_config.ConfigError as error:
        return str(error)
    raise AssertionError("load_config accepted the config")


def _letterboxd(data: bytes) -> LetterboxdConfig:
    """The ``[letterboxd]`` table ``load_config`` reads from ``data``."""
    config = export_config.load_config(data).letterboxd
    assert config is not None
    return config


class LoadConfigTest(unittest.TestCase):
    def test_full_config_loads(self) -> None:
        config = _letterboxd(
            config_toml(
                ratings={"1": 1.0, "2": 2.5, "2/3": 5},
                rating_tags={"1": "retrolog-low"},
                cutoff=_CUTOFF,
                tags=["retrolog-import", "retrolog"],
                watched_date=False,
                review_note="Retrolog review. Original watch: {year}, {date}",
                review_note_rows="all",
            )
        )
        self.assertEqual(dict(config.half_stars), {"1": 2, "2": 5, "2/3": 10})
        self.assertEqual(dict(config.rating_tags), {"1": "retrolog-low"})
        self.assertEqual(config.tags, ("retrolog-import", "retrolog"))
        self.assertFalse(config.watched_date)
        self.assertEqual(
            config.review_note, "Retrolog review. Original watch: {year}, {date}"
        )
        self.assertEqual(config.review_note_rows, "all")
        self.assertEqual(
            config.cutoff,
            Cutoff("2019/films.txt", 12, parse_rows.title_key("Placeholder Film Nine")),
        )

    def test_optional_tables_default_to_none_and_empty(self) -> None:
        config = _letterboxd(config_toml())
        self.assertEqual(dict(config.rating_tags), {})
        self.assertIsNone(config.cutoff)
        self.assertTrue(config.watched_date)
        self.assertEqual(config.review_note_rows, "commented")
        self.assertEqual(config.review_note, "")

    def test_config_without_letterboxd_has_none(self) -> None:
        self.assertIsNone(export_config.load_config(b"schema = 1\n").letterboxd)

    def test_tables_are_read_only(self) -> None:
        config = _letterboxd(config_toml(rating_tags={"1": "retrolog-low"}))
        with self.assertRaises(TypeError):
            config.half_stars["1"] = 4  # type: ignore[index] # the write must fail
        with self.assertRaises(TypeError):
            config.rating_tags["1"] = "x"  # type: ignore[index] # the write must fail

    def test_sample_loads(self) -> None:
        config = _letterboxd(_SAMPLE.read_bytes())
        self.assertTrue(config.half_stars)
        self.assertIsNotNone(config.cutoff)


class StarsTest(unittest.TestCase):
    def test_star_value_off_the_half_grid_is_refused(self) -> None:
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.load_config(config_toml(ratings={"3": 3.25}))
        self.assertIn("letterboxd.ratings.3", str(caught.exception))
        self.assertNotIn("3.25", str(caught.exception))

    def test_star_values_outside_the_range_or_type_are_refused(self) -> None:
        refused: tuple[TomlValue, ...] = (
            0,
            0.0,
            5.5,
            6,
            -0.5,
            True,
            "3",
            math.inf,
            math.nan,
            [1],
        )
        for value in refused:
            with self.subTest(value=value):
                self.assertEqual(
                    _refusal(config_toml(ratings={"3": value})),
                    "letterboxd.ratings.3: must be stars from 0.5 to 5 in halves",
                )

    def test_star_values_are_doubled(self) -> None:
        config = _letterboxd(config_toml(ratings={"a": 0.5, "b": 5, "c": 3.0}))
        self.assertEqual(dict(config.half_stars), {"a": 1, "b": 10, "c": 6})
        self.assertTrue(all(type(v) is int for v in config.half_stars.values()))

    def test_empty_rating_key_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(ratings={"": 1.0})),
            "letterboxd.ratings: a rating key must be non-empty",
        )

    def test_ratings_not_a_table_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(omit=["ratings"], ratings=None) + b"ratings = 3\n"),
            "letterboxd.ratings: must be a table",
        )

    @given(star_values)
    def test_fuzz_star_value(self, value: TomlValue) -> None:
        """A star value loads as its doubled grid step, or is refused by its key."""
        try:
            config = _letterboxd(config_toml(ratings={"k": value}))
        except export_config.ConfigError as error:
            self.assertEqual(
                str(error),
                "letterboxd.ratings.k: must be stars from 0.5 to 5 in halves",
            )
            return
        self.assertNotIsInstance(value, bool)
        self.assertEqual(config.half_stars["k"], value * 2)
        self.assertIn(config.half_stars["k"], range(1, 11))


class TagsTest(unittest.TestCase):
    def test_tag_with_a_comma_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(tags=["retrolog", "retrolog one, two"])),
            "letterboxd.tags: a tag must be 1 to 50 characters with no comma, "
            "quote or control character",
        )

    def test_bad_tags_are_refused(self) -> None:
        bad = ("", 'say "x"', "it's", "tab\there", "x" * 51, "line\nbreak")
        for tag in (f"retrolog{text}" if text else text for text in bad):
            with self.subTest(tag=tag):
                message = _refusal(config_toml(tags=[tag]))
                self.assertTrue(message.startswith("letterboxd.tags: "), message)

    def test_fifty_characters_and_spaces_are_a_tag(self) -> None:
        fifty = "retrolog" + "x" * 42
        config = _letterboxd(config_toml(tags=[fifty, "retrolog two words"]))
        self.assertEqual(config.tags, (fifty, "retrolog two words"))

    def test_a_tag_not_starting_with_retrolog_is_refused(self) -> None:
        # Regression: every tag starts with retrolog, so none collides
        # with the account's own tags and one tag page finds the whole import.
        for tag in ("auto-import", "Retrolog", "my-retrolog", " retrolog"):
            with self.subTest(tag=tag):
                message = _refusal(config_toml(tags=["retrolog", tag]))
                self.assertEqual(
                    message, "letterboxd.tags: a tag must start with retrolog"
                )

    def test_a_rating_tag_not_starting_with_retrolog_is_refused(self) -> None:
        message = _refusal(config_toml(rating_tags={"1": "low"}))
        self.assertEqual(
            message, "letterboxd.rating_tags.1: a tag must start with retrolog"
        )

    def test_tags_not_a_list_of_strings_are_refused(self) -> None:
        not_lists: tuple[TomlValue, ...] = ("auto-import", [3])
        for value in not_lists:
            with self.subTest(value=value):
                self.assertEqual(
                    _refusal(config_toml(tags=value)),
                    "letterboxd.tags: must be a list of strings",
                )

    def test_empty_tag_list_is_no_tags(self) -> None:
        self.assertEqual(_letterboxd(config_toml(tags=[])).tags, ())

    def test_bad_rating_tag_is_refused_by_its_key(self) -> None:
        message = _refusal(config_toml(rating_tags={"1": "a,b"}))
        self.assertTrue(message.startswith("letterboxd.rating_tags.1: "), message)
        self.assertNotIn("a,b", message)

    def test_rating_tag_not_a_string_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(rating_tags={"1": 3})),
            "letterboxd.rating_tags.1: must be a non-empty string",
        )

    def test_rating_tag_for_an_unmapped_key_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(rating_tags={"9": "retrolog-x"})),
            "letterboxd.rating_tags.9: not a key of letterboxd.ratings",
        )


class ReviewNoteTest(unittest.TestCase):
    def test_review_note_unknown_placeholder_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(review_note="Seen {title}")),
            "letterboxd.review_note: may hold only the placeholders {year} and "
            "{date}, and at most 500 characters",
        )

    def test_bad_review_notes_are_refused(self) -> None:
        for note in ("{}", "{0}", "{year:>8}", "{year!r}", "{year", "year}", "{x.y}"):
            with self.subTest(note=note):
                message = _refusal(config_toml(review_note=note))
                self.assertTrue(message.startswith("letterboxd.review_note: "))

    def test_review_note_length_is_capped(self) -> None:
        self.assertEqual(
            _letterboxd(config_toml(review_note="n" * 500)).review_note, "n" * 500
        )
        message = _refusal(config_toml(review_note="n" * 501))
        self.assertTrue(message.startswith("letterboxd.review_note: "))

    def test_review_note_keeps_escaped_braces(self) -> None:
        note = "{{retrolog}} {date} {year}"
        self.assertEqual(_letterboxd(config_toml(review_note=note)).review_note, note)

    def test_review_note_not_a_string_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(review_note=False)),
            "letterboxd.review_note: must be a string",
        )

    def test_review_note_rows_must_be_commented_or_all(self) -> None:
        for value in ("some", "", 1):
            with self.subTest(value=value):
                self.assertEqual(
                    _refusal(config_toml(review_note_rows=value)),
                    "letterboxd.review_note_rows: must be commented or all",
                )

    def test_non_boolean_watched_date_is_refused(self) -> None:
        for value in ("true", 1, 0):
            with self.subTest(value=value):
                self.assertEqual(
                    _refusal(config_toml(watched_date=value)),
                    "letterboxd.watched_date: must be true or false",
                )


class CutoffTest(unittest.TestCase):
    def test_cutoff_title_is_stored_as_title_key(self) -> None:
        cutoff = _letterboxd(
            config_toml(cutoff={**_CUTOFF, "title": "  PLACEHOLDER film, Nine! "})
        ).cutoff
        self.assertIsNotNone(cutoff)
        assert cutoff is not None
        self.assertEqual(cutoff.title_key, "placeholder film nine")
        self.assertEqual((cutoff.file, cutoff.line), ("2019/films.txt", 12))

    def test_cutoff_title_without_a_letter_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(cutoff={**_CUTOFF, "title": "?!"})),
            "letterboxd.cutoff.title: has no letter or digit to match a row by",
        )

    def test_bad_cutoff_file_and_line_are_refused(self) -> None:
        cases: dict[str, tuple[TomlValue, ...]] = {
            "file": ("../outside.txt", "/abs.txt", "a\\b.txt", ""),
            "line": (0, -1, "12", True),
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    message = _refusal(config_toml(cutoff={**_CUTOFF, key: value}))
                    self.assertTrue(
                        message.startswith(f"letterboxd.cutoff.{key}: "), message
                    )

    def test_cutoff_keys_are_all_required(self) -> None:
        for key in _CUTOFF:
            with self.subTest(key=key):
                partial = {k: v for k, v in _CUTOFF.items() if k != key}
                self.assertEqual(
                    _refusal(config_toml(cutoff=partial)),
                    f"letterboxd.cutoff.{key}: missing",
                )

    def test_unknown_cutoff_key_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(cutoff={**_CUTOFF, "date": "x"})),
            "letterboxd.cutoff: unknown key 'date'",
        )


class DocumentTest(unittest.TestCase):
    def test_unknown_keys_are_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(head="schema = 1\ngoodreads = 1\n")),
            "unknown key 'goodreads'",
        )
        self.assertEqual(
            _refusal(config_toml(diary=True)), "letterboxd: unknown key 'diary'"
        )

    def test_missing_keys_are_refused(self) -> None:
        for key in (
            "watched_date",
            "tags",
            "review_note",
            "review_note_rows",
            "ratings",
        ):
            with self.subTest(key=key):
                self.assertEqual(
                    _refusal(config_toml(omit=[key])), f"letterboxd.{key}: missing"
                )

    def test_schema_must_be_one(self) -> None:
        for head, message in (
            ("", "schema: missing"),
            ("schema = 2\n", "schema: this version reads schema 1 only"),
            ("schema = true\n", "schema: this version reads schema 1 only"),
        ):
            with self.subTest(head=head):
                self.assertEqual(_refusal(config_toml(head=head)), message)

    def test_letterboxd_not_a_table_is_refused(self) -> None:
        self.assertEqual(
            _refusal(b"schema = 1\nletterboxd = 3\n"), "letterboxd: must be a table"
        )

    def test_not_toml_is_refused(self) -> None:
        for data in (b"schema = = 1", b"\xff", b"x = " + b"[" * 2000):
            with self.subTest(data=data[:12]):
                self.assertEqual(_refusal(data), "export config: not valid UTF-8 TOML")

    def test_refusals_are_config_errors_not_rules_errors(self) -> None:
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.load_config(config_toml(cutoff={**_CUTOFF, "line": 0}))
        self.assertNotIsInstance(caught.exception, parse_checks.RulesError)

    @given(st.binary(max_size=400))
    def test_fuzz_load_config(self, data: bytes) -> None:
        try:
            export_config.load_config(data)
        except export_config.ConfigError:
            pass  # the only exception it may raise

    @given(st.integers(min_value=0), st.integers(min_value=0, max_value=40))
    def test_damaged_sample_is_config_or_error(self, start: int, length: int) -> None:
        """Deleting any slice of the sample yields a config or ConfigError only."""
        text = _SAMPLE.read_text(encoding="utf-8")
        start %= len(text)
        try:
            export_config.load_config((text[:start] + text[start + length :]).encode())
        except export_config.ConfigError:
            pass  # the only exception it may raise


def _row(rating_raw: str, line: int) -> ResolvedRow:
    """A dated, resolved film row rated ``rating_raw``, on ``line``."""
    return ResolvedRow(
        source_csv="film-2010.csv",
        title="Placeholder Film One",
        title_key="placeholder film one",
        year=1999,
        date=datetime.date(2010, 3, 4),
        rating_raw=rating_raw,
        comment="",
        rewatch=False,
        creator="",
        status="resolved",
        tmdb_id="1000001",
        source_file="10/seen.txt",
        source_line=line,
    )


class CheckKeysTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _letterboxd(config_toml(ratings={"1": 1.0, "2": 2.5}))

    def test_unmapped_rating_key_is_named(self) -> None:
        rows = [_row("1", 1), _row("2/3", 2), _row("9", 3)]
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.check_keys(self.config, rows)
        self.assertEqual(
            str(caught.exception),
            "letterboxd.ratings.2/3: missing, and a row is rated with it",
        )

    def test_mapped_and_unrated_rows_pass(self) -> None:
        export_config.check_keys(self.config, [_row("1", 1), _row("", 2), _row("2", 3)])
        export_config.check_keys(self.config, [])


if __name__ == "__main__":
    unittest.main()
