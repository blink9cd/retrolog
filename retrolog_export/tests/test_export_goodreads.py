"""Tests for export_goodreads: one book as one Goodreads import row, and the reread merge."""

from __future__ import annotations

import datetime
import random
import sys
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

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
import export_goodreads  # noqa: E402 - after the sys.path inserts above
from export_types import (  # noqa: E402 - after the sys.path inserts above
    EARLIER_FIELDS,
    GoodreadsConfig,
    ResolvedRow,
)
from goodreads_fixtures import (  # noqa: E402 - after the sys.path inserts above
    GOODREADS,
    book_row,
    goodreads_config,
    isbn13,
)

_NOTES = goodreads_config(
    private_note="Imported. Read {year}.",
    private_note_rating="Lists said: {rating}.",
    rating_names=MappingProxyType({"1": "w", "2": "x", "3": "y", "4": "z"}),
    private_note_earlier="Earlier reads: {years}.",
)


def _cells(
    row: ResolvedRow,
    config: GoodreadsConfig = GOODREADS,
    earlier_years: Sequence[str] = (),
) -> Mapping[str, str]:
    """``row_cells`` keyed by column name."""
    cells = export_goodreads.row_cells(row, config, earlier_years=tuple(earlier_years))
    return dict(zip(export_goodreads.COLUMNS, cells, strict=True))


class ColumnsTest(unittest.TestCase):
    def test_the_columns_are_in_the_ruled_order(self) -> None:
        self.assertEqual(
            export_goodreads.COLUMNS,
            (
                "Title",
                "Author",
                "Additional Authors",
                "ISBN",
                "ISBN13",
                "My Rating",
                "Date Read",
                "Date Added",
                "Bookshelves",
                "Exclusive Shelf",
                "My Review",
                "Private Notes",
            ),
        )

    def test_a_resolved_book_fills_every_column(self) -> None:
        self.assertEqual(
            _cells(book_row(comment="A real comment.")),
            {
                "Title": "Placeholder Book One",
                "Author": "Invented Author",
                "Additional Authors": "",
                "ISBN": "",
                "ISBN13": isbn13("979000000001"),
                "My Rating": "2.5",
                "Date Read": "2010/03/04",
                "Date Added": "2010/03/04",
                "Bookshelves": "retrolog",
                "Exclusive Shelf": "read",
                "My Review": "A real comment.",
                "Private Notes": "",
            },
        )

    def test_an_override_with_a_work_key_and_an_author(self) -> None:
        cells = _cells(book_row(status="override", isbn13="", ol_work="/works/OL2W"))
        self.assertEqual(
            (cells["Title"], cells["Author"], cells["ISBN13"]),
            ("Placeholder Book One", "Invented Author", ""),
        )

    def test_a_by_title_book_has_no_isbn(self) -> None:
        cells = _cells(book_row(status="not-found", isbn13="", ol_work=""))
        self.assertEqual((cells["ISBN"], cells["ISBN13"]), ("", ""))
        self.assertEqual(cells["Author"], "Invented Author")

    def test_a_title_only_book_has_no_author(self) -> None:
        cells = _cells(
            book_row(status="unresolvable", isbn13="", ol_work="", creator="")
        )
        self.assertEqual(
            (cells["Title"], cells["Author"], cells["Additional Authors"]),
            ("Placeholder Book One", "", ""),
        )

    def test_a_two_author_creator_splits_into_author_and_additional(self) -> None:
        cells = _cells(book_row(creator="Invented Author & Second Invented"))
        self.assertEqual(
            (cells["Author"], cells["Additional Authors"]),
            ("Invented Author", "Second Invented"),
        )

    def test_three_authors_join_the_rest_with_commas(self) -> None:
        cells = _cells(book_row(creator="One Author & Two Author & Three Author"))
        self.assertEqual(cells["Additional Authors"], "Two Author, Three Author")

    def test_every_shelf_is_written_joined_by_commas(self) -> None:
        config = goodreads_config(shelves=("retrolog", "retrolog-books"))
        self.assertEqual(
            _cells(book_row(), config)["Bookshelves"], "retrolog, retrolog-books"
        )

    def test_the_review_is_the_comment_alone(self) -> None:
        cells = _cells(book_row(comment="Loved it."), _NOTES)
        self.assertEqual(cells["My Review"], "Loved it.")
        self.assertNotIn("Loved it.", cells["Private Notes"])


class RatingTest(unittest.TestCase):
    def test_each_rating_key_gives_its_stars(self) -> None:
        for key, expected in (("1", "1"), ("2", "2.5"), ("3", "3.5"), ("4", "5")):
            with self.subTest(key=key):
                self.assertEqual(
                    _cells(book_row(rating_raw=key))["My Rating"], expected
                )

    def test_an_unrated_row_has_no_rating(self) -> None:
        self.assertEqual(_cells(book_row(rating_raw=""))["My Rating"], "")

    def test_withholding_a_rating_is_refused_for_a_book(self) -> None:
        with self.assertRaises(ValueError):
            export_goodreads.row_cells(book_row(), GOODREADS, True)


class DatesTest(unittest.TestCase):
    def test_date_added_is_the_date_read(self) -> None:
        cells = _cells(book_row(date=datetime.date(2003, 11, 9)))
        self.assertEqual((cells["Date Read"], cells["Date Added"]), ("2003/11/09",) * 2)

    def test_an_early_year_is_written_with_four_digits(self) -> None:
        cells = _cells(book_row(date=datetime.date(999, 1, 2)))
        self.assertEqual(cells["Date Read"], "0999/01/02")

    def test_an_undated_row_takes_the_configured_date_added(self) -> None:
        cells = _cells(book_row(date=None))
        self.assertEqual((cells["Date Read"], cells["Date Added"]), ("", "1900/01/01"))

    def test_an_undated_row_with_no_configured_date_stops(self) -> None:
        config = goodreads_config(undated_date_added=None)
        with self.assertRaises(ValueError):
            export_goodreads.row_cells(book_row(date=None), config)


class PrivateNotesTest(unittest.TestCase):
    def test_note_and_rating_sentence_on_a_rated_row(self) -> None:
        self.assertEqual(
            _cells(book_row(), _NOTES)["Private Notes"],
            "Imported. Read 2010. Lists said: x.",
        )

    def test_an_unrated_uncommented_row_keeps_the_note_only(self) -> None:
        self.assertEqual(
            _cells(book_row(rating_raw=""), _NOTES)["Private Notes"],
            "Imported. Read 2010.",
        )

    def test_the_earlier_reads_sentence_follows_on_a_merged_reread(self) -> None:
        self.assertEqual(
            _cells(book_row(), _NOTES, ("2003", "2005"))["Private Notes"],
            "Imported. Read 2010. Lists said: x. Earlier reads: 2003, 2005.",
        )

    def test_no_earlier_years_adds_no_earlier_sentence(self) -> None:
        self.assertNotIn("Also", _cells(book_row(), _NOTES, ())["Private Notes"])

    def test_an_earlier_sentence_with_no_field_still_needs_years(self) -> None:
        config = goodreads_config(private_note_earlier="A reread.")
        self.assertEqual(
            _cells(book_row(), config, ("2003",))["Private Notes"], "A reread."
        )
        self.assertEqual(_cells(book_row(), config)["Private Notes"], "")

    def test_an_undated_row_drops_a_note_naming_the_date(self) -> None:
        for note in ("Read {year}.", "Read {date}."):
            with self.subTest(note=note):
                config = goodreads_config(
                    private_note=note,
                    private_note_rating="Rated.",
                )
                self.assertEqual(
                    _cells(book_row(date=None), config)["Private Notes"], "Rated."
                )

    def test_a_note_with_no_date_field_stays_on_an_undated_row(self) -> None:
        config = goodreads_config(private_note="Imported.")
        self.assertEqual(
            _cells(book_row(date=None), config)["Private Notes"], "Imported."
        )

    def test_the_date_field_is_the_iso_date(self) -> None:
        config = goodreads_config(private_note="On {date}.")
        self.assertEqual(_cells(book_row(), config)["Private Notes"], "On 2010-03-04.")

    def test_a_rating_sentence_with_no_field_prints_as_written(self) -> None:
        config = goodreads_config(private_note_rating="Rated.")
        self.assertEqual(_cells(book_row(), config)["Private Notes"], "Rated.")

    def test_the_earlier_field_is_years(self) -> None:
        self.assertEqual(EARLIER_FIELDS, frozenset({"years"}))


def _read(year: int | None, file: str = "10/read.txt", line: int = 1) -> ResolvedRow:
    """A read of the base book in ``year`` (None: undated), at ``file:line``."""
    date = None if year is None else datetime.date(year, 6, 1)
    return book_row(date=date, source_file=file, source_line=line)


class MergeRereadsTest(unittest.TestCase):
    def test_a_single_read_gives_no_years(self) -> None:
        row = _read(2010)
        self.assertEqual(export_goodreads.merge_rereads([row]), (row, ()))

    def test_the_newest_read_wins_and_the_earlier_year_is_kept(self) -> None:
        newest = book_row(date=datetime.date(2010, 6, 1), rating_raw="4")
        older = book_row(date=datetime.date(2004, 6, 1), rating_raw="1", source_line=7)
        self.assertEqual(
            export_goodreads.merge_rereads([older, newest]), (newest, ("2004",))
        )

    def test_years_are_once_each_and_oldest_first(self) -> None:
        rows = [
            _read(2005, line=2),
            _read(2010),
            _read(2001, line=3),
            _read(2005, line=4),
        ]
        merged, years = export_goodreads.merge_rereads(rows)
        self.assertEqual(merged.date, datetime.date(2010, 6, 1))
        self.assertEqual(years, ("2001", "2005"))

    def test_a_same_day_tie_goes_to_the_later_file_then_line(self) -> None:
        first = _read(2010, file="10/a.txt", line=9)
        later_file = _read(2010, file="10/b.txt", line=1)
        later_line = _read(2010, file="10/b.txt", line=2)
        merged, years = export_goodreads.merge_rereads([later_line, first, later_file])
        self.assertIs(merged, later_line)
        self.assertEqual(years, ("2010",))

    def test_a_dated_read_is_newer_than_an_undated_one(self) -> None:
        dated = _read(2004)
        undated = _read(None, file="undated/read.txt")
        merged, years = export_goodreads.merge_rereads([undated, dated])
        self.assertIs(merged, dated)
        self.assertEqual(years, ())

    def test_undated_reads_alone_merge_by_file_and_line(self) -> None:
        one = _read(None, file="undated/read.txt", line=1)
        two = _read(None, file="undated/read.txt", line=5)
        self.assertEqual(export_goodreads.merge_rereads([two, one]), (two, ()))

    def test_no_rows_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            export_goodreads.merge_rereads([])

    @given(
        st.lists(
            st.tuples(
                st.one_of(st.none(), st.integers(1990, 1995)),
                st.sampled_from(["10/a.txt", "10/b.txt"]),
                st.integers(1, 3),
            ),
            min_size=1,
            max_size=6,
        ),
        st.randoms(use_true_random=False),
    )
    def test_fuzz_merge_rereads(
        self, reads: list[tuple[int | None, str, int]], shuffler: random.Random
    ) -> None:
        """Any order of the same reads merges the same; no year is newer than the row."""
        rows = [_read(year, file, line) for year, file, line in reads]
        merged, years = export_goodreads.merge_rereads(rows)
        shuffled = list(rows)
        shuffler.shuffle(shuffled)
        self.assertEqual(export_goodreads.merge_rereads(shuffled), (merged, years))
        self.assertEqual(list(years), sorted(set(years)))
        if merged.date is not None:
            self.assertTrue(all(int(year) <= merged.date.year for year in years))


if __name__ == "__main__":
    unittest.main()
