"""Tests for export_account's per-file reading: the tags and watched dates.

``read_account`` keeps apart what each account file says of a film: watched only,
logged (in the diary, the ratings, or the reviews), already imported (a diary or review
row tagged ``retrolog``), and the dates of its diary entries. These tests hold each
part, the tag rule, and that the two optional columns never break a file without them.
"""

from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The tool's own src/ first, then resolve's, parse's, and the survey's: the order
# retrolog_export.SOURCE_DIRS puts them on sys.path.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
    str(_TOOL),
]
import export_account  # noqa: E402 - after the sys.path inserts above
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    account_csv,
    logged_csv,
    write_account_folder,
)
from export_types import (  # noqa: E402 - after the sys.path inserts above
    IMPORT_TAG_PREFIX,
    Account,
    FilmKey,
)

_ONE = FilmKey("placeholder film one", 1999)
_TWO = FilmKey("metropolis", 1927)
_DAY = datetime.date(2010, 5, 6)


def _row(tags: str = "", watched: str = "") -> export_account.AccountRow:
    """The one row ``parse_account_csv`` reads from a diary of Placeholder Film One."""
    [row] = export_account.parse_account_csv(
        logged_csv([("Placeholder Film One", "1999", tags, watched)])
    )
    return row


class ParseRowsTest(unittest.TestCase):
    def test_a_row_carries_its_keys_tag_and_date(self) -> None:
        row = _row("retrolog, retrolog-high", "2010-05-06")
        self.assertEqual(row.keys, frozenset({_ONE}))
        self.assertTrue(row.retrolog_tagged)
        self.assertEqual(row.watched_date, _DAY)

    def test_a_file_without_the_columns_reads_no_tag_and_no_date(self) -> None:
        [row] = export_account.parse_account_csv(
            account_csv([("Placeholder Film One", "1999")])
        )
        self.assertEqual(row.keys, frozenset({_ONE}))
        self.assertFalse(row.retrolog_tagged)
        self.assertIsNone(row.watched_date)

    def test_a_tag_is_matched_by_prefix_ignoring_case_and_spaces(self) -> None:
        for tags in ("retrolog", "  RetroLog-high ", "other, retrologue", "x,RETROLOG"):
            with self.subTest(tags=tags):
                self.assertTrue(_row(tags).retrolog_tagged)

    def test_other_tags_are_not_the_import(self) -> None:
        for tags in ("", "other", "my retrolog", "re-trolog, x", ", ,"):
            with self.subTest(tags=tags):
                self.assertFalse(_row(tags).retrolog_tagged)

    def test_the_prefix_is_the_tools_name(self) -> None:
        self.assertEqual(export_account.IMPORT_TAG_PREFIX, "retrolog")
        # One definition: the config's refusal and the account read cannot drift.
        self.assertIs(export_account.IMPORT_TAG_PREFIX, IMPORT_TAG_PREFIX)

    def test_a_date_that_is_not_iso_reads_as_none(self) -> None:
        for text in ("", "2010-13-01", "06/05/2010", "2010-5-6", "١٩٩٩-01-01"):
            with self.subTest(text=text):
                self.assertIsNone(_row(watched=text).watched_date)

    def test_a_short_row_reads_no_tag_and_no_date(self) -> None:
        text = "Name,Year,Tags,Watched Date\nMetropolis,1927\n"
        [row] = export_account.parse_account_csv(text)
        self.assertEqual(row.keys, frozenset({_TWO}))
        self.assertFalse(row.retrolog_tagged)
        self.assertIsNone(row.watched_date)

    def test_a_row_with_no_name_is_skipped(self) -> None:
        text = logged_csv([("", "1999", "retrolog", "2010-05-06")])
        self.assertEqual(export_account.parse_account_csv(text), ())

    @given(
        st.lists(
            st.tuples(
                st.text(max_size=12),
                st.text(max_size=5),
                st.text(max_size=20),
                st.text(max_size=12),
            ),
            max_size=6,
        )
    )
    def test_any_cells_in_the_four_columns_parse_or_refuse(
        self, entries: list[tuple[str, str, str, str]]
    ) -> None:
        """Any cells in the four columns read as rows or refuse, never anything else."""
        try:
            rows = export_account.parse_account_csv(logged_csv(entries))
        except export_account.AccountError:
            return
        for row in rows:
            self.assertTrue(row.keys)
            self.assertIsInstance(row.retrolog_tagged, bool)
            if row.watched_date is not None:
                self.assertIsInstance(row.watched_date, datetime.date)


class ReadAccountClassesTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)

    def _read(self, files: dict[str, str | bytes]) -> Account:
        return export_account.read_account(write_account_folder(self.root, files))

    def test_watched_is_kept_apart_from_logged(self) -> None:
        account = self._read(
            {
                "watched.csv": account_csv([("Placeholder Film One", "1999")]),
                "ratings.csv": account_csv([("Metropolis", "1927")]),
            }
        )
        self.assertEqual(account.watched, frozenset({_ONE}))
        self.assertEqual(account.logged, frozenset({_TWO}))
        self.assertEqual(account.imported, frozenset())
        self.assertEqual(dict(account.diary_dates), {})

    def test_diary_and_reviews_are_logged_and_give_the_import(self) -> None:
        account = self._read(
            {
                "diary.csv": logged_csv(
                    [("Placeholder Film One", "1999", "retrolog", "2010-05-06")]
                ),
                "reviews.csv": logged_csv([("Metropolis", "1927", "RETROLOG-x", "")]),
            }
        )
        self.assertEqual(account.logged, frozenset({_ONE, _TWO}))
        self.assertEqual(account.imported, frozenset({_ONE, _TWO}))
        self.assertEqual(dict(account.diary_dates), {_DAY: frozenset({_ONE})})

    def test_a_tag_only_in_reviews_marks_the_import(self) -> None:
        account = self._read(
            {
                "diary.csv": logged_csv([("Metropolis", "1927", "other", "")]),
                "reviews.csv": logged_csv([("Metropolis", "1927", "retrolog", "")]),
            }
        )
        self.assertEqual(account.imported, frozenset({_TWO}))

    def test_a_tag_in_watched_or_ratings_is_not_the_import(self) -> None:
        tagged = logged_csv([("Metropolis", "1927", "retrolog", "2010-05-06")])
        account = self._read({"watched.csv": tagged, "ratings.csv": tagged})
        self.assertEqual(account.imported, frozenset())
        self.assertEqual(dict(account.diary_dates), {})
        self.assertEqual(account.watched, frozenset({_TWO}))
        self.assertEqual(account.logged, frozenset({_TWO}))

    def test_only_diary_dates_are_kept_by_date(self) -> None:
        account = self._read(
            {
                "diary.csv": logged_csv(
                    [
                        ("Placeholder Film One", "1999", "", "2010-05-06"),
                        ("Metropolis", "1927", "", "2010-05-06"),
                        ("Metropolis", "1927", "", ""),
                    ]
                ),
                "reviews.csv": logged_csv(
                    [("Placeholder Film One", "1999", "", "2011-01-01")]
                ),
            }
        )
        self.assertEqual(dict(account.diary_dates), {_DAY: frozenset({_ONE, _TWO})})

    def test_tagged_diary_entries_are_kept_by_date(self) -> None:
        account = self._read(
            {
                "diary.csv": logged_csv(
                    [
                        ("Placeholder Film One", "2000", "retrolog", "2010-05-06"),
                        ("Metropolis", "1927", "", "2010-05-06"),
                        ("Metropolis", "1927", "retrolog", ""),
                        ("Metropolis", "1927", "retrolog", "2010-13-01"),
                    ]
                ),
                "reviews.csv": logged_csv(
                    [("Metropolis", "1927", "retrolog", "2010-05-06")]
                ),
            }
        )
        self.assertEqual(
            dict(account.imported_dates),
            {_DAY: frozenset({FilmKey("placeholder film one", 2000)})},
        )

    def test_an_account_without_tags_has_no_imported_dates(self) -> None:
        account = self._read(
            {"diary.csv": logged_csv([("Metropolis", "1927", "", "2010-05-06")])}
        )
        self.assertEqual(dict(account.imported_dates), {})

    def test_the_dates_are_read_only(self) -> None:
        account = self._read(
            {"diary.csv": logged_csv([("Metropolis", "1927", "", "2010-05-06")])}
        )
        with self.assertRaises(TypeError):
            account.diary_dates[_DAY] = frozenset()  # type: ignore[index] # the test
        with self.assertRaises(TypeError):
            account.imported_dates[_DAY] = frozenset()  # type: ignore[index] # the test


if __name__ == "__main__":
    unittest.main()
