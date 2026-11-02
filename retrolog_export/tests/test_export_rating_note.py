"""Tests for the review note's rating sentence: its config keys and the review text.

The rating sentence is a second, optional template beside the review note, holding
``{rating}``, the word ``rating_names`` gives the row's rating key. It is added after
the note on a rated row only, so an unrated row keeps its note and loses only the
sentence.
"""

from __future__ import annotations

import dataclasses
import datetime
import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_config  # noqa: E402 - after the sys.path inserts above
import export_letterboxd  # noqa: E402 - after the sys.path inserts above
from export_fixtures import config_toml  # noqa: E402 - after the sys.path inserts
from export_types import (  # noqa: E402 - after the sys.path inserts above
    LetterboxdConfig,
    ResolvedRow,
)

# DEFAULT_RATINGS' keys, "1" and "2", each given a word.
_NAMES = {"1": "low", "2": "mid"}
_SENTENCE = "Original rating: {rating}."


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


class RatingNoteConfigTest(unittest.TestCase):
    def test_names_and_sentence_load(self) -> None:
        config = _letterboxd(
            config_toml(rating_names=_NAMES, review_note_rating=_SENTENCE)
        )
        self.assertEqual(dict(config.rating_names), _NAMES)
        self.assertEqual(config.review_note_rating, _SENTENCE)

    def test_both_default_to_empty(self) -> None:
        config = _letterboxd(config_toml())
        self.assertEqual(dict(config.rating_names), {})
        self.assertEqual(config.review_note_rating, "")

    def test_names_are_read_only(self) -> None:
        config = _letterboxd(config_toml(rating_names=_NAMES))
        with self.assertRaises(TypeError):
            config.rating_names["1"] = "x"  # type: ignore[index] # the write must fail

    def test_a_name_for_an_unmapped_key_is_refused(self) -> None:
        self.assertEqual(
            _refusal(config_toml(rating_names={"9": "violet"})),
            "letterboxd.rating_names.9: not a key of letterboxd.ratings",
        )

    def test_a_bad_name_is_refused_by_its_key(self) -> None:
        for name in ("", "n" * 51, "low\nline", 3):
            with self.subTest(name=name):
                message = _refusal(config_toml(rating_names={"1": name}))
                self.assertTrue(message.startswith("letterboxd.rating_names.1: "))

    def test_a_name_may_hold_a_slash_and_spaces(self) -> None:
        config = _letterboxd(config_toml(rating_names={"1": "mid / high"}))
        self.assertEqual(config.rating_names["1"], "mid / high")

    def test_the_sentence_names_only_rating(self) -> None:
        for sentence in ("Seen {year}", "{rating!r}", "{rating", "{}"):
            with self.subTest(sentence=sentence):
                message = _refusal(
                    config_toml(rating_names=_NAMES, review_note_rating=sentence)
                )
                self.assertEqual(
                    message,
                    "letterboxd.review_note_rating: may hold only the placeholder "
                    "{rating}, and at most 500 characters",
                )

    def test_the_sentence_is_capped_and_must_be_a_string(self) -> None:
        self.assertTrue(
            _refusal(
                config_toml(rating_names=_NAMES, review_note_rating="n" * 501)
            ).startswith("letterboxd.review_note_rating: ")
        )
        self.assertEqual(
            _refusal(config_toml(review_note_rating=False)),
            "letterboxd.review_note_rating: must be a string",
        )

    def test_the_sentence_needs_a_name_for_every_rating_key(self) -> None:
        self.assertEqual(
            _refusal(
                config_toml(rating_names={"1": "low"}, review_note_rating=_SENTENCE)
            ),
            "letterboxd.rating_names.2: missing, and review_note_rating names {rating}",
        )

    def test_a_sentence_without_rating_needs_no_names(self) -> None:
        config = _letterboxd(config_toml(review_note_rating="Rated at the time."))
        self.assertEqual(config.review_note_rating, "Rated at the time.")


# One row every review test starts from: dated, rated "c", commented.
_ROW = ResolvedRow(
    source_csv="film-2009.csv",
    title="Metropolis",
    title_key="metropolis",
    year=1927,
    date=datetime.date(2009, 3, 14),
    rating_raw="c",
    comment="Fine.",
    rewatch=False,
    creator="",
    status="resolved",
    tmdb_id="1000001",
    source_file="09/seen.txt",
    source_line=1,
)
_CONFIG = LetterboxdConfig(
    half_stars={"c": 7, "b/c": 6},
    rating_tags={},
    tags=("retrolog",),
    watched_date=True,
    review_note="Retrolog review. Original watch: {year}.",
    review_note_rows="commented",
    cutoff=None,
    rating_names={"c": "high", "b/c": "mid/high"},
    review_note_rating=_SENTENCE,
)


def _review(**changes: object) -> str:
    """``review_text`` of the base row with ``changes`` applied, under ``_CONFIG``."""
    # Each test's keywords are real ResolvedRow fields, checked when the row is built.
    row = dataclasses.replace(_ROW, **changes)  # type: ignore[arg-type] # see above
    return export_letterboxd.review_text(row, _CONFIG)


class RatingSentenceTest(unittest.TestCase):
    def test_a_rated_row_has_the_note_then_the_sentence(self) -> None:
        self.assertEqual(
            _review(),
            "Retrolog review. Original watch: 2009. Original rating: high.\n\nFine.",
        )

    def test_a_split_key_takes_its_own_word(self) -> None:
        self.assertIn("Original rating: mid/high.", _review(rating_raw="b/c"))

    def test_an_unrated_row_keeps_the_note_alone(self) -> None:
        self.assertEqual(
            _review(rating_raw=""),
            "Retrolog review. Original watch: 2009.\n\nFine.",
        )

    def test_an_undated_row_keeps_the_sentence_alone(self) -> None:
        self.assertEqual(_review(date=None), "Original rating: high.\n\nFine.")

    def test_an_uncommented_row_has_neither_by_default(self) -> None:
        self.assertEqual(_review(comment=""), "")

    def test_the_sentence_alone_when_there_is_no_note(self) -> None:
        config = dataclasses.replace(_CONFIG, review_note="")
        self.assertEqual(
            export_letterboxd.review_text(_ROW, config),
            "Original rating: high.\n\nFine.",
        )

    def test_a_rated_key_with_no_word_fails_loudly(self) -> None:
        # Regression: a lookup with a default printed "Original rating: ." when
        # load_config's guarantee that every key has a word was broken; the broken
        # invariant must stop the run, not write a blank into every review.
        config = dataclasses.replace(_CONFIG, rating_names={})
        with self.assertRaises(KeyError):
            export_letterboxd.review_text(_ROW, config)

    def test_a_sentence_naming_no_rating_needs_no_word(self) -> None:
        # load_config lets a sentence without {rating} stand with no rating_names,
        # so the word is looked up only when the sentence prints it.
        config = dataclasses.replace(
            _CONFIG, rating_names={}, review_note_rating="Rated on paper."
        )
        self.assertIn("Rated on paper.", export_letterboxd.review_text(_ROW, config))

    def test_no_sentence_template_leaves_the_note_as_it_was(self) -> None:
        config = dataclasses.replace(_CONFIG, review_note_rating="")
        self.assertEqual(
            export_letterboxd.review_text(_ROW, config),
            "Retrolog review. Original watch: 2009.\n\nFine.",
        )


class WithheldRatingTest(unittest.TestCase):
    """A film the owner logged is sent with no stars, and nothing else changes."""

    def _cells(self, *, withhold: bool) -> dict[str, str]:
        config = dataclasses.replace(_CONFIG, rating_tags={"c": "retrolog-high"})
        cells = export_letterboxd.row_cells(_ROW, config, withhold)
        return dict(zip(export_letterboxd.COLUMNS, cells, strict=True))

    def test_the_rating_cell_is_empty(self) -> None:
        self.assertEqual(self._cells(withhold=False)["Rating"], "3.5")
        self.assertEqual(self._cells(withhold=True)["Rating"], "")

    def test_every_other_cell_is_unchanged(self) -> None:
        kept = self._cells(withhold=False)
        withheld = self._cells(withhold=True)
        self.assertEqual(
            {name: cell for name, cell in withheld.items() if name != "Rating"},
            {name: cell for name, cell in kept.items() if name != "Rating"},
        )
        self.assertEqual(withheld["Tags"], "retrolog-high, retrolog")
        self.assertIn("Original rating: high.", withheld["Review"])
        self.assertEqual(withheld["WatchedDate"], "2009-03-14")

    def test_stars_are_kept_by_default(self) -> None:
        cells = export_letterboxd.row_cells(_ROW, _CONFIG)
        self.assertEqual(cells[export_letterboxd.COLUMNS.index("Rating")], "3.5")


if __name__ == "__main__":  # pragma: no cover - entry point
    unittest.main()
