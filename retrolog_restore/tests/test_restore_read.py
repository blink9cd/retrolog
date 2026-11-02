"""Tests for restore_read: an account export's diary, reviews, and likes, read back."""

from __future__ import annotations

import contextlib
import csv
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The tool's own src/ first, then export's, parse's, and the survey's: the order
# retrolog_restore.SOURCE_DIRS puts them on sys.path.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_export" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
]
import export_account  # noqa: E402 - after the sys.path inserts above
import restore_read  # noqa: E402 - after the sys.path inserts above
from restore_fixtures import (  # noqa: E402 - after the sys.path inserts above
    entry,
    export_files,
    write_folder,
    write_zip,
)
from restore_read import DiaryEntry, RestoreError  # noqa: E402 - after the inserts

_OPTION = "--backup"
_DIARY = restore_read.DIARY_NEEDED
_REVIEWS = restore_read.REVIEWS_NEEDED
_LIKES = restore_read.LIKES_NEEDED
# csv's default field limit, which a review one character longer breaks unless raised.
_CSV_DEFAULT_LIMIT = 131072


class ParseEntriesCsvTest(unittest.TestCase):
    def test_every_column_is_read_by_its_header_name(self) -> None:
        text = (
            "Watched Date,Review,Tags,Rewatch,Rating,Letterboxd URI,Year,Name,Date\n"
            "2026-01-05,Fine.,a,Yes,4,https://boxd.it/x,2001,Placeholder,2026-02-01\n"
        )
        rows = restore_read.parse_entries_csv(text, needed=_DIARY)
        self.assertEqual(
            rows,
            (
                DiaryEntry(
                    name="Placeholder",
                    year="2001",
                    uri="https://boxd.it/x",
                    watched_date="2026-01-05",
                    rating="4",
                    rewatch="Yes",
                    tags="a",
                    review="Fine.",
                ),
            ),
        )

    def test_absent_optional_columns_read_as_empty(self) -> None:
        rows = restore_read.parse_entries_csv(
            "Name,Year,Letterboxd URI,Watched Date\nPlaceholder,2001,u,d\n",
            needed=_DIARY,
        )
        self.assertEqual(
            rows, (DiaryEntry("Placeholder", "2001", "u", watched_date="d"),)
        )

    def test_a_short_row_reads_its_missing_cells_as_empty(self) -> None:
        rows = restore_read.parse_entries_csv(
            "Name,Year,Letterboxd URI,Watched Date\nPlaceholder\n", needed=_DIARY
        )
        self.assertEqual(rows, (DiaryEntry("Placeholder"),))

    def test_blank_lines_are_skipped_and_order_kept(self) -> None:
        text = "Name,Year\nPlaceholder B,2\n\nPlaceholder A,1\n"
        rows = restore_read.parse_entries_csv(text, needed=_LIKES)
        self.assertEqual([row.name for row in rows], ["Placeholder B", "Placeholder A"])

    def test_a_byte_order_mark_is_accepted(self) -> None:
        text = "\N{ZERO WIDTH NO-BREAK SPACE}Name,Year\nPlaceholder,1\n"
        rows = restore_read.parse_entries_csv(text, needed=_LIKES)
        self.assertEqual(rows, (DiaryEntry("Placeholder", "1"),))

    def test_quoted_cells_keep_commas_quotes_and_line_breaks(self) -> None:
        text = 'Name,Year,Review\nPlaceholder,1,"a, ""b""\nc"\n'
        rows = restore_read.parse_entries_csv(text, needed=_LIKES)
        self.assertEqual(rows[0].review, 'a, "b"\nc')

    def test_a_diary_header_lacking_a_needed_column_is_refused_naming_it(self) -> None:
        full = list(_DIARY)
        for column in full:
            header = ",".join(name for name in full if name != column)
            with self.subTest(column=column), self.assertRaises(RestoreError) as raised:
                restore_read.parse_entries_csv(header + "\nP,1,u,d\n", needed=_DIARY)
            self.assertEqual(str(raised.exception), f"header lacks a {column} column")

    def test_a_reviews_header_lacking_watched_date_or_review_is_refused(self) -> None:
        for header, missing in (
            ("Name,Year,Review", "Watched Date"),
            ("Name,Year,Watched Date", "Review"),
        ):
            with (
                self.subTest(missing=missing),
                self.assertRaises(RestoreError) as raised,
            ):
                restore_read.parse_entries_csv(header + "\nP,1,x\n", needed=_REVIEWS)
            self.assertEqual(str(raised.exception), f"header lacks a {missing} column")

    def test_several_missing_columns_are_all_named(self) -> None:
        with self.assertRaises(RestoreError) as raised:
            restore_read.parse_entries_csv("Name,Year\nP,1\n", needed=_REVIEWS)
        self.assertEqual(
            str(raised.exception), "header lacks the Watched Date and Review columns"
        )
        with self.assertRaises(RestoreError) as raised:
            restore_read.parse_entries_csv("Name\nP\n", needed=_DIARY)
        self.assertEqual(
            str(raised.exception),
            "header lacks the Year, Letterboxd URI, and Watched Date columns",
        )

    def test_an_empty_file_lacks_every_needed_column(self) -> None:
        with self.assertRaises(RestoreError) as raised:
            restore_read.parse_entries_csv("", needed=_LIKES)
        self.assertEqual(
            str(raised.exception), "header lacks the Name and Year columns"
        )

    def test_a_header_only_file_with_the_needed_columns_reads_as_no_rows(self) -> None:
        text = "Name,Year,Watched Date,Review\n"
        self.assertEqual(restore_read.parse_entries_csv(text, needed=_REVIEWS), ())

    def test_the_uri_column_is_optional_when_not_needed(self) -> None:
        rows = restore_read.parse_entries_csv("Name,Year\nP,1\n", needed=_LIKES)
        self.assertEqual(rows, (DiaryEntry("P", "1"),))

    def test_a_review_past_csvs_default_field_limit_round_trips(self) -> None:
        review = "x" * (_CSV_DEFAULT_LIMIT + 1)
        text = f"Name,Year,Watched Date,Review\nP,1,d,{review}\n"
        rows = restore_read.parse_entries_csv(text, needed=_REVIEWS)
        self.assertEqual(rows[0].review, review)

    def test_the_process_field_limit_is_left_as_it_was(self) -> None:
        before = csv.field_size_limit()
        restore_read.parse_entries_csv("Name,Year\nP,1\n", needed=_LIKES)
        with self.assertRaises(RestoreError):
            restore_read.parse_entries_csv('Name,Year\n"P"x,1\n', needed=_LIKES)
        self.assertEqual(csv.field_size_limit(), before)

    def test_the_parse_raises_the_limit_by_exports_own_context(self) -> None:
        # One raise-and-restore for both tools' CSV reads: restore parses inside
        # export_account.csv_field_limit rather than keeping a copy of it.
        entered: list[bool] = []

        @contextlib.contextmanager
        def recording() -> Iterator[None]:
            with export_account.csv_field_limit():
                entered.append(True)
                yield

        with mock.patch.object(restore_read, "csv_field_limit", recording):
            restore_read.parse_entries_csv("Name,Year\nP,1\n", needed=_LIKES)
        self.assertEqual(entered, [True])
        self.assertIs(restore_read.csv_field_limit, export_account.csv_field_limit)

    def test_invalid_csv_is_refused_without_quoting_it(self) -> None:
        with self.assertRaises(RestoreError) as raised:
            restore_read.parse_entries_csv(
                'Name,Year\n"Placeholder Secret"x,1\n', needed=_LIKES
            )
        self.assertEqual(str(raised.exception), "not valid CSV")

    @given(st.text(max_size=200), st.sampled_from((_DIARY, _REVIEWS, _LIKES)))
    def test_fuzz_parse_entries_csv(self, text: str, needed: tuple[str, ...]) -> None:
        # Any text reads as entries or raises RestoreError, and nothing else.
        try:
            rows = restore_read.parse_entries_csv(text, needed=needed)
        except RestoreError:
            return
        self.assertTrue(all(isinstance(row, DiaryEntry) for row in rows))


class ReadExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _refusal(self, path: Path) -> str:
        """The message of the ``RestoreError`` reading ``path`` raises."""
        with self.assertRaises(RestoreError) as raised:
            restore_read.read_export(path, _OPTION)
        return str(raised.exception)

    def test_a_zip_gives_its_diary_reviews_and_likes(self) -> None:
        path = write_zip(
            self.root / "backup.zip",
            export_files(
                [entry()],
                reviews=[entry(Review="Fine.")],
                likes=[entry(Name="Placeholder Film Two")],
            ),
        )
        export = restore_read.read_export(path, _OPTION)
        self.assertEqual([row.name for row in export.diary], ["Placeholder Film One"])
        self.assertEqual([row.review for row in export.reviews], ["Fine."])
        self.assertEqual([row.name for row in export.likes], ["Placeholder Film Two"])

    def test_a_folder_without_reviews_or_likes_reads_them_as_none(self) -> None:
        folder = write_folder(self.root / "backup", export_files([entry()]))
        export = restore_read.read_export(folder, _OPTION)
        self.assertEqual(len(export.diary), 1)
        self.assertEqual((export.reviews, export.likes), ((), ()))

    def test_likes_and_reviews_need_no_uri_column(self) -> None:
        files = export_files([entry()]) | {
            "reviews.csv": "Name,Year,Watched Date,Review\nP,1,d,r\n",
            "likes/films.csv": "Name,Year\nP,1\n",
        }
        export = restore_read.read_export(write_folder(self.root / "b", files), _OPTION)
        self.assertEqual((len(export.reviews), len(export.likes)), (1, 1))

    def test_an_export_without_a_diary_is_refused(self) -> None:
        folder = write_folder(self.root / "backup", {"watched.csv": "Name,Year\n"})
        self.assertEqual(self._refusal(folder), "--backup: holds no diary.csv")

    def test_a_diary_without_a_uri_column_is_refused_naming_the_file(self) -> None:
        folder = write_folder(self.root / "backup", {"diary.csv": "Name,Year\nP,1\n"})
        self.assertEqual(
            self._refusal(folder),
            "--backup: diary.csv: header lacks the Letterboxd URI and Watched Date"
            " columns",
        )

    def test_a_diary_without_a_watched_date_column_is_refused(self) -> None:
        text = "Name,Year,Letterboxd URI\nP,1,u\n"
        folder = write_folder(self.root / "backup", {"diary.csv": text})
        self.assertEqual(
            self._refusal(folder),
            "--backup: diary.csv: header lacks a Watched Date column",
        )

    def test_reviews_without_a_review_column_are_refused_not_dropped(self) -> None:
        files = export_files([entry()]) | {
            "reviews.csv": "Name,Year,Watched Date\nP,1,2026-01-02\n"
        }
        self.assertEqual(
            self._refusal(write_folder(self.root / "backup", files)),
            "--backup: reviews.csv: header lacks a Review column",
        )

    def test_reviews_without_a_year_column_are_refused_naming_the_file(self) -> None:
        files = export_files([entry()]) | {
            "reviews.csv": "Name,Watched Date,Review\nP,d,r\n"
        }
        self.assertEqual(
            self._refusal(write_folder(self.root / "backup", files)),
            "--backup: reviews.csv: header lacks a Year column",
        )

    def test_only_the_named_files_are_read(self) -> None:
        # --current's reviews.csv is never used, so a damaged one is never read.
        files = export_files([entry()], likes=[entry()]) | {
            "reviews.csv": b"\xff\xfeNo,Header\n"
        }
        folder = write_folder(self.root / "current", files)
        export = restore_read.read_export(
            folder, "--current", restore_read.CURRENT_FILES
        )
        self.assertEqual((len(export.diary), export.reviews), (1, ()))
        self.assertEqual(len(export.likes), 1)

    def test_the_backup_reads_all_three_files(self) -> None:
        self.assertEqual(restore_read.BACKUP_FILES, restore_read.EXPORT_FILES)
        self.assertNotIn(restore_read.REVIEWS, restore_read.CURRENT_FILES)

    def test_an_unreadable_export_names_the_option_once(self) -> None:
        path = self.root / "backup.zip"
        path.write_bytes(b"not a zip archive")
        self.assertEqual(self._refusal(path), "--backup: not a readable folder or zip")

    def test_a_member_error_is_given_the_option(self) -> None:
        folder = write_folder(self.root / "backup", {"diary.csv": b"\xff\xfe"})
        self.assertEqual(self._refusal(folder), "--backup: diary.csv: not UTF-8")

    def test_a_refusal_names_no_path_and_no_title(self) -> None:
        folder = write_folder(
            self.root / "backup",
            {
                "diary.csv": "Name,Year,Letterboxd URI,Watched Date\n"
                '"Placeholder Secret"x,1,u,d\n'
            },
        )
        message = self._refusal(folder)
        self.assertNotIn("Placeholder Secret", message)
        self.assertNotIn(str(self.root), message)


if __name__ == "__main__":
    unittest.main()
