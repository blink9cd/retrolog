"""Tests for export_letterboxd: one resolve row as one Letterboxd import row."""

from __future__ import annotations

import csv
import dataclasses
import datetime
import io
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_letterboxd  # noqa: E402 - after the sys.path inserts above
from export_types import (  # noqa: E402 - after the sys.path inserts above
    NOTE_DATE_FIELDS,
    NOTE_FIELDS,
    LetterboxdConfig,
    ResolvedRow,
)

# One row every test starts from: resolved, dated, rated "2", with no comment.
_ROW = ResolvedRow(
    source_csv="film-2010.csv",
    title="Metropolis",
    title_key="metropolis",
    year=1927,
    date=datetime.date(2010, 3, 4),
    rating_raw="2",
    comment="",
    rewatch=False,
    creator="Placeholder Director",
    status="resolved",
    tmdb_id="1000001",
    source_file="10/seen.txt",
    source_line=1,
)
_CONFIG = LetterboxdConfig(
    half_stars=MappingProxyType({"1": 2, "2": 7, "4": 10, "2/4": 8}),
    rating_tags=MappingProxyType({}),
    tags=("auto-import",),
    watched_date=True,
    review_note="",
    review_note_rows="commented",
    cutoff=None,
)


def _row(**changes: object) -> ResolvedRow:
    """The base row with ``changes`` applied."""
    # Each test's keywords are real ResolvedRow fields, checked when the row is built.
    return dataclasses.replace(_ROW, **changes)  # type: ignore[arg-type] # see above


def _config(**changes: object) -> LetterboxdConfig:
    """The base config with ``changes`` applied."""
    # Each test's keywords are real LetterboxdConfig fields, checked when it is built.
    return dataclasses.replace(_CONFIG, **changes)  # type: ignore[arg-type] # see above


def _cells(row: ResolvedRow, config: LetterboxdConfig = _CONFIG) -> Mapping[str, str]:
    """``row_cells`` keyed by column name."""
    cells = export_letterboxd.row_cells(row, config)
    return dict(zip(export_letterboxd.COLUMNS, cells, strict=True))


class ColumnsTest(unittest.TestCase):
    def test_the_columns_are_in_the_ruled_order(self) -> None:
        self.assertEqual(
            export_letterboxd.COLUMNS,
            (
                "tmdbID",
                "Title",
                "Year",
                "Directors",
                "Rating",
                "WatchedDate",
                "Rewatch",
                "Tags",
                "Review",
            ),
        )

    def test_a_resolved_row_fills_every_column_it_has(self) -> None:
        self.assertEqual(
            _cells(_row()),
            {
                "tmdbID": "1000001",
                "Title": "Metropolis",
                "Year": "1927",
                "Directors": "Placeholder Director",
                "Rating": "3.5",
                "WatchedDate": "2010-03-04",
                "Rewatch": "",
                "Tags": "auto-import",
                "Review": "",
            },
        )

    def test_one_cell_per_column(self) -> None:
        cells = export_letterboxd.row_cells(_row(), _CONFIG)
        self.assertEqual(len(cells), len(export_letterboxd.COLUMNS))


class StarsTest(unittest.TestCase):
    def test_an_odd_count_is_a_half_star(self) -> None:
        self.assertEqual(export_letterboxd.stars(7), "3.5")
        self.assertEqual(export_letterboxd.stars(1), "0.5")

    def test_an_even_count_is_a_whole_number(self) -> None:
        self.assertEqual(export_letterboxd.stars(10), "5")
        self.assertEqual(export_letterboxd.stars(2), "1")

    def test_a_count_off_the_scale_is_refused(self) -> None:
        for count in (0, 11, -1):
            with self.subTest(count=count), self.assertRaises(ValueError):
                export_letterboxd.stars(count)


class IdTest(unittest.TestCase):
    def test_an_override_is_sent_by_id(self) -> None:
        self.assertEqual(_cells(_row(status="override"))["tmdbID"], "1000001")

    def test_a_not_found_row_has_no_id(self) -> None:
        # A stale id on a row resolve could not settle is never sent.
        self.assertEqual(_cells(_row(status="not-found"))["tmdbID"], "")

    def test_an_unresolvable_row_has_no_id(self) -> None:
        self.assertEqual(_cells(_row(status="unresolvable"))["tmdbID"], "")

    def test_a_resolved_row_without_an_id_has_none(self) -> None:
        self.assertEqual(_cells(_row(tmdb_id=""))["tmdbID"], "")


class TitleYearDirectorsTest(unittest.TestCase):
    def test_an_unknown_year_is_empty(self) -> None:
        self.assertEqual(_cells(_row(year=None))["Year"], "")

    def test_directors_are_rejoined_with_commas(self) -> None:
        cells = _cells(_row(creator="Placeholder One & Placeholder Two & Third"))
        self.assertEqual(cells["Directors"], "Placeholder One, Placeholder Two, Third")

    def test_no_creator_is_empty(self) -> None:
        self.assertEqual(_cells(_row(creator=""))["Directors"], "")


class RatingTest(unittest.TestCase):
    def test_a_split_key_takes_its_own_stars(self) -> None:
        self.assertEqual(_cells(_row(rating_raw="2/4"))["Rating"], "4")

    def test_an_unrated_row_has_no_rating(self) -> None:
        self.assertEqual(_cells(_row(rating_raw=""))["Rating"], "")


class WatchedDateTest(unittest.TestCase):
    def test_an_undated_row_has_no_watched_date(self) -> None:
        self.assertEqual(_cells(_row(date=None))["WatchedDate"], "")

    def test_the_switch_off_leaves_every_watched_date_empty(self) -> None:
        config = _config(watched_date=False)
        for date in (datetime.date(2010, 3, 4), None):
            with self.subTest(date=date):
                self.assertEqual(_cells(_row(date=date), config)["WatchedDate"], "")


class RewatchTest(unittest.TestCase):
    def test_a_rewatch_is_true(self) -> None:
        self.assertEqual(_cells(_row(rewatch=True))["Rewatch"], "true")

    def test_a_first_watch_is_empty(self) -> None:
        self.assertEqual(_cells(_row(rewatch=False))["Rewatch"], "")


class TagsTest(unittest.TestCase):
    def test_the_rating_tag_comes_first_then_the_tags(self) -> None:
        config = _config(
            rating_tags=MappingProxyType({"2": "liked"}), tags=("auto-import", "retro")
        )
        self.assertEqual(_cells(_row(), config)["Tags"], "liked, auto-import, retro")

    def test_a_key_without_a_rating_tag_gives_the_tags_alone(self) -> None:
        config = _config(rating_tags=MappingProxyType({"4": "loved"}))
        self.assertEqual(_cells(_row(), config)["Tags"], "auto-import")

    def test_an_unrated_row_takes_no_rating_tag(self) -> None:
        config = _config(rating_tags=MappingProxyType({"2": "liked"}))
        self.assertEqual(_cells(_row(rating_raw=""), config)["Tags"], "auto-import")

    def test_a_repeated_tag_is_written_once(self) -> None:
        config = _config(
            rating_tags=MappingProxyType({"2": "retro"}),
            tags=("auto-import", "retro", "auto-import"),
        )
        self.assertEqual(_cells(_row(), config)["Tags"], "retro, auto-import")

    def test_no_tags_is_empty(self) -> None:
        self.assertEqual(_cells(_row(), _config(tags=()))["Tags"], "")


class ReviewTest(unittest.TestCase):
    _NOTE = "Retrolog review. Original watch: {year} ({date})"

    def test_the_comment_alone_when_there_is_no_note(self) -> None:
        self.assertEqual(
            export_letterboxd.review_text(_row(comment="Fine."), _CONFIG), "Fine."
        )

    def test_the_note_comes_first_on_a_commented_row(self) -> None:
        text = export_letterboxd.review_text(
            _row(comment="Fine."), _config(review_note=self._NOTE)
        )
        self.assertEqual(
            text, "Retrolog review. Original watch: 2010 (2010-03-04)\n\nFine."
        )

    def test_an_uncommented_row_has_no_note_by_default(self) -> None:
        self.assertEqual(
            export_letterboxd.review_text(_row(), _config(review_note=self._NOTE)), ""
        )

    def test_every_row_takes_the_note_with_all(self) -> None:
        config = _config(review_note=self._NOTE, review_note_rows="all")
        self.assertEqual(
            export_letterboxd.review_text(_row(), config),
            "Retrolog review. Original watch: 2010 (2010-03-04)",
        )
        self.assertEqual(
            export_letterboxd.review_text(_row(comment="Fine."), config),
            "Retrolog review. Original watch: 2010 (2010-03-04)\n\nFine.",
        )

    def test_an_undated_row_drops_a_note_naming_the_date(self) -> None:
        # Regression: the note once printed "Original watch:  ()" with nothing filled in.
        for note in ("Seen {year}", "Seen {date}"):
            with self.subTest(note=note):
                config = _config(review_note=note, review_note_rows="all")
                self.assertEqual(
                    export_letterboxd.review_text(_row(date=None), config), ""
                )
                self.assertEqual(
                    export_letterboxd.review_text(
                        _row(date=None, comment="Fine."), config
                    ),
                    "Fine.",
                )

    def test_an_undated_row_keeps_a_note_naming_no_date(self) -> None:
        config = _config(review_note="Retrolog review. {{sic}}")
        self.assertEqual(
            export_letterboxd.review_text(_row(date=None, comment="Fine."), config),
            "Retrolog review. {sic}\n\nFine.",
        )

    def test_the_fields_an_undated_row_lacks_are_note_fields(self) -> None:
        # A date field the config could never accept would make the undated check
        # look for a name no note can hold.
        self.assertLessEqual(NOTE_DATE_FIELDS, NOTE_FIELDS)

    def test_the_review_cell_is_the_review_text(self) -> None:
        config = _config(review_note=self._NOTE)
        self.assertEqual(
            _cells(_row(comment="Fine."), config)["Review"],
            export_letterboxd.review_text(_row(comment="Fine."), config),
        )

    def test_a_comment_with_a_comma_quote_and_newline_reads_back(self) -> None:
        comment = 'Long, "slow"\nand worth it.'
        cells = export_letterboxd.row_cells(_row(comment=comment), _CONFIG)
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerow(cells)
        (read_back,) = list(csv.reader(io.StringIO(buffer.getvalue())))
        self.assertEqual(read_back, list(cells))
        self.assertEqual(read_back[-1], comment)


if __name__ == "__main__":
    unittest.main()
