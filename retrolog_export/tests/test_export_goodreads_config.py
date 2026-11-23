"""Tests for the export config's ``[goodreads]`` table and the checks run on its rows."""

from __future__ import annotations

import datetime
import string
import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TESTS))
import _retrolog_export_fuzz_profile  # noqa: E402, F401 - loads the deterministic profile before any @given
import export_config  # noqa: E402 - after the sys.path inserts above
from export_fixtures import TomlValue, config_toml, toml_string  # noqa: E402 - after the sys.path inserts
from export_types import (  # noqa: E402 - after the sys.path inserts above
    Cutoff,
    GoodreadsConfig,
    OmitEntry,
)
from goodreads_fixtures import (  # noqa: E402 - after the sys.path inserts above
    book_row,
    goodreads_config,
    goodreads_toml,
)

_SAMPLE = _TOOL / "samples" / "export.example.toml"
_NAMES = {"1": "low", "2": "mid"}


def _refusal(data: bytes) -> str:
    """The ConfigError message ``load_config`` gives for ``data``."""
    try:
        export_config.load_config(data)
    except export_config.ConfigError as error:
        return str(error)
    raise AssertionError("load_config accepted the config")


def _goodreads(data: bytes) -> GoodreadsConfig:
    """The ``[goodreads]`` table ``load_config`` reads from ``data``."""
    config = export_config.load_config(data).goodreads
    assert config is not None
    return config


class LoadGoodreadsTest(unittest.TestCase):
    def test_full_table_loads(self) -> None:
        config = _goodreads(
            goodreads_toml(
                ratings={"1": 1.0, "2": 2.5, "3": 3.5, "4": 5},
                rating_names={"1": "low", "2": "mid", "3": "high", "4": "top"},
                cutoff={"file": "2019/books.txt", "line": 3, "title": "Placeholder"},
                omit_books=[{"title": "Placeholder Book Nine", "year": 1950}],
                shelves=["retrolog", "retrolog-import_2"],
                undated_date_added=datetime.date(1900, 1, 1),
                private_note="Imported. Read {year} ({date}).",
                private_note_rating="Lists said: {rating}.",
                private_note_earlier="Earlier reads: {years}.",
            )
        )
        self.assertEqual(dict(config.half_stars), {"1": 2, "2": 5, "3": 7, "4": 10})
        self.assertEqual(config.shelves, ("retrolog", "retrolog-import_2"))
        self.assertEqual(config.undated_date_added, datetime.date(1900, 1, 1))
        self.assertEqual(config.private_note, "Imported. Read {year} ({date}).")
        self.assertEqual(config.private_note_rating, "Lists said: {rating}.")
        self.assertEqual(config.private_note_earlier, "Earlier reads: {years}.")
        self.assertEqual(dict(config.rating_names)["3"], "high")
        self.assertEqual(
            config.cutoff,
            Cutoff(file="2019/books.txt", line=3, title_key="placeholder"),
        )
        self.assertEqual(
            config.omit,
            (OmitEntry(title_key="placeholder book nine", year=1950),),
        )

    def test_minimal_table_takes_the_defaults(self) -> None:
        config = _goodreads(goodreads_toml())
        self.assertEqual(config.shelves, ("retrolog",))
        self.assertIsNone(config.undated_date_added)
        self.assertEqual(
            (config.private_note, config.private_note_rating),
            ("", ""),
        )
        self.assertEqual(config.private_note_earlier, "")
        self.assertEqual(dict(config.rating_names), {})
        self.assertIsNone(config.cutoff)
        self.assertEqual(config.omit, ())

    def test_ratings_and_shelves_are_required(self) -> None:
        for key in ("ratings", "shelves"):
            with self.subTest(key=key):
                self.assertEqual(
                    _refusal(goodreads_toml(omit=[key])), f"goodreads.{key}: missing"
                )

    def test_unknown_key_is_refused(self) -> None:
        self.assertEqual(
            _refusal(goodreads_toml(tags=["retrolog"])),
            "goodreads: unknown key 'tags'",
        )

    def test_star_value_off_the_half_grid_names_the_goodreads_key(self) -> None:
        message = _refusal(goodreads_toml(ratings={"3": 3.25}))
        self.assertIn("goodreads.ratings.3", message)
        self.assertNotIn("3.25", message)

    def test_a_goodreads_only_file_has_no_letterboxd_table(self) -> None:
        config = export_config.load_config(goodreads_toml())
        self.assertIsNone(config.letterboxd)
        self.assertIsNotNone(config.goodreads)

    def test_a_letterboxd_only_file_has_no_goodreads_table(self) -> None:
        config = export_config.load_config(config_toml())
        self.assertIsNotNone(config.letterboxd)
        self.assertIsNone(config.goodreads)

    def test_a_file_may_hold_both_tables(self) -> None:
        both = config_toml() + goodreads_toml(head="")
        config = export_config.load_config(both)
        self.assertIsNotNone(config.letterboxd)
        self.assertIsNotNone(config.goodreads)

    def test_the_example_config_holds_a_goodreads_table(self) -> None:
        config = _goodreads(_SAMPLE.read_bytes())
        self.assertIn("retrolog", config.shelves)
        self.assertIsNotNone(config.undated_date_added)
        self.assertIsNotNone(config.cutoff)
        self.assertEqual(len(config.omit), 1)
        self.assertIn("{years}", config.private_note_earlier)
        self.assertEqual(set(config.rating_names), set(config.half_stars))


class ShelvesTest(unittest.TestCase):
    def test_a_repeated_shelf_is_kept_once(self) -> None:
        config = _goodreads(goodreads_toml(shelves=["retrolog", "retrolog"]))
        self.assertEqual(config.shelves, ("retrolog",))

    def test_no_shelf_is_refused(self) -> None:
        self.assertEqual(
            _refusal(goodreads_toml(shelves=[])),
            "goodreads.shelves: must hold at least one shelf",
        )

    def test_shelves_not_a_list_of_strings_are_refused(self) -> None:
        values: tuple[TomlValue, ...] = ("retrolog", [1], ["retrolog", True])
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(
                    _refusal(goodreads_toml(shelves=value)),
                    "goodreads.shelves: must be a list of strings",
                )

    def test_a_shelf_breaking_the_goodreads_rule_is_refused_by_number(self) -> None:
        for bad in (
            "retrolog,import",
            "Retrolog",
            "retrolog-Import",
            "retrolog import",
            "retrolog.import",
            "retrologé",
            "",
            "retrolog" + "x" * 43,
        ):
            with self.subTest(bad=bad):
                message = _refusal(goodreads_toml(shelves=["retrolog", bad]))
                self.assertEqual(
                    message,
                    "goodreads.shelves[2]: a shelf must be 1 to 50 characters, each "
                    "a lowercase letter, a digit, a hyphen, or an underscore",
                )

    def test_fifty_characters_is_the_longest_shelf(self) -> None:
        longest = "retrolog" + "x" * 42
        self.assertEqual(
            _goodreads(goodreads_toml(shelves=[longest])).shelves, (longest,)
        )

    def test_a_shelf_without_the_prefix_is_refused(self) -> None:
        for bad in ("import", "my-retrolog", "retro"):
            with self.subTest(bad=bad):
                message = _refusal(goodreads_toml(shelves=[bad]))
                self.assertEqual(
                    message, "goodreads.shelves[1]: a shelf must start with retrolog"
                )

    @given(st.text(alphabet=string.printable + "éA,", max_size=60))
    def test_fuzz_shelves(self, shelf: str) -> None:
        """Any shelf text loads only when it obeys every rule, else refuses by key."""
        allowed = set(string.ascii_lowercase + string.digits + "-_")
        valid = (
            1 <= len(shelf) <= 50
            and set(shelf) <= allowed
            and shelf.startswith("retrolog")
        )
        try:
            config = _goodreads(goodreads_toml(shelves=[shelf]))
        except export_config.ConfigError as error:
            self.assertFalse(valid)
            self.assertTrue(str(error).startswith("goodreads.shelves[1]: "))
        else:
            self.assertTrue(valid)
            self.assertEqual(config.shelves, (shelf,))


class UndatedDateAddedTest(unittest.TestCase):
    def test_a_string_or_a_date_time_is_refused(self) -> None:
        for value in ("1900-01-01", 1900):
            with self.subTest(value=value):
                self.assertEqual(
                    _refusal(goodreads_toml(undated_date_added=value)),
                    "goodreads.undated_date_added: must be a date such as 2001-12-31",
                )
        date_time = goodreads_toml().replace(
            b"[goodreads]\n", b"[goodreads]\nundated_date_added = 1900-01-01T00:00:00\n"
        )
        self.assertEqual(
            _refusal(date_time),
            "goodreads.undated_date_added: must be a date such as 2001-12-31",
        )


class NoteKeysTest(unittest.TestCase):
    def test_each_template_takes_only_its_own_fields(self) -> None:
        cases = (
            ("private_note", "Read in {years}.", "{year} and {date}"),
            ("private_note_rating", "Read {year}.", "{rating}"),
            ("private_note_earlier", "Read {year}.", "{years}"),
            ("private_note_earlier", "Read {years!r}.", "{years}"),
        )
        for key, template, named in cases:
            with self.subTest(key=key, template=template):
                line = f"{key} = {toml_string(template)}\n".encode()
                data = goodreads_toml().replace(
                    b"[goodreads]\n", b"[goodreads]\n" + line
                )
                message = _refusal(data)
                self.assertTrue(message.startswith(f"goodreads.{key}: "))
                self.assertIn(named, message)

    def test_a_rating_sentence_needs_a_word_for_every_key(self) -> None:
        self.assertEqual(
            _refusal(
                goodreads_toml(
                    rating_names={"1": "low"},
                    private_note_rating="Lists said: {rating}.",
                )
            ),
            "goodreads.rating_names.2: missing, and private_note_rating names {rating}",
        )

    def test_a_sentence_with_no_field_needs_no_words(self) -> None:
        config = _goodreads(goodreads_toml(private_note_rating="Rated."))
        self.assertEqual(config.private_note_rating, "Rated.")

    def test_a_word_for_a_key_ratings_lacks_names_the_goodreads_ratings(self) -> None:
        self.assertEqual(
            _refusal(goodreads_toml(rating_names={**_NAMES, "9": "none"})),
            "goodreads.rating_names.9: not a key of goodreads.ratings",
        )

    def test_the_letterboxd_sentence_refusal_keeps_its_own_key(self) -> None:
        self.assertEqual(
            _refusal(
                config_toml(rating_names={"1": "low"}, review_note_rating="{rating}")
            ),
            "letterboxd.rating_names.2: missing, and review_note_rating names {rating}",
        )


class CheckUndatedTest(unittest.TestCase):
    def test_dated_rows_need_no_date(self) -> None:
        config = goodreads_config(undated_date_added=None)
        export_config.check_undated(config, [book_row()], where="goodreads")

    def test_an_undated_row_with_a_date_configured_passes(self) -> None:
        export_config.check_undated(
            goodreads_config(), [book_row(date=None)], where="goodreads"
        )

    def test_an_undated_row_with_no_date_configured_is_refused(self) -> None:
        config = goodreads_config(undated_date_added=None)
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.check_undated(
                config, [book_row(), book_row(date=None)], where="goodreads"
            )
        self.assertEqual(
            str(caught.exception),
            "goodreads.undated_date_added: missing, and a row is undated",
        )


class SharedChecksTest(unittest.TestCase):
    def test_check_keys_takes_a_goodreads_table(self) -> None:
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.check_keys(
                goodreads_config(), [book_row(rating_raw="9")], where="goodreads"
            )
        self.assertEqual(
            str(caught.exception),
            "goodreads.ratings.9: missing, and a row is rated with it",
        )

    def test_check_omit_takes_a_goodreads_table(self) -> None:
        config = goodreads_config(
            omit=(OmitEntry(title_key="placeholder book nine", year=None),)
        )
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.check_omit(config, [book_row()], where="goodreads")
        self.assertEqual(str(caught.exception), "goodreads.omit[1]: matches no row")


if __name__ == "__main__":
    unittest.main()
