"""Tests for probe_ids and probe_input: which ids are probed, and from which rows.

The import files are written at test time with export's own header and CSV writer.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_export" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
# parse's: export's account reader, whose field limit probe_input reads under, keys
# titles with parse's rule.
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import probe_ids  # noqa: E402 - after the sys.path inserts above
import probe_input  # noqa: E402 - after the sys.path inserts above
import resolve_columns  # noqa: E402 - after the sys.path inserts above
from export_letterboxd import COLUMNS  # noqa: E402 - after the sys.path inserts above
from probe_fixtures import import_row, write_import  # noqa: E402 - after the inserts

# csv's field limit when nothing has raised it, the one probe read with before.
_CSV_DEFAULT_FIELD_LIMIT = 131_072


class TmdbIdTest(unittest.TestCase):
    """An id is one to ten ASCII digits with no leading zero."""

    def test_the_rule_is_resolves(self) -> None:
        # One rule for every tool that reads or writes a tmdb_id, so a film resolve
        # settles is one probe asks about.
        self.assertIs(probe_ids.is_tmdb_id, resolve_columns.is_tmdb_id)

    def test_accepted(self) -> None:
        for text in ("1", "19", "999999999", "1234567890"):
            with self.subTest(text=text):
                self.assertTrue(probe_ids.is_tmdb_id(text))

    def test_refused(self) -> None:
        for text in (
            "",
            "0",
            "019",
            "12345678901",
            "1 ",
            " 1",
            "1\n",
            "-1",
            "1.0",
            "١٩",  # Arabic-Indic digits: digits to str.isdigit, not to the rule
            "１９",  # fullwidth digits
            "19/../1",
        ):
            with self.subTest(text=text):
                self.assertFalse(probe_ids.is_tmdb_id(text))

    @given(st.text())
    def test_an_accepted_id_has_the_id_shape(self, text: str) -> None:
        if probe_ids.is_tmdb_id(text):
            self.assertTrue(text.isascii() and text.isdigit())
            self.assertNotEqual(text[0], "0")
            self.assertLessEqual(len(text), 10)

    @given(st.integers(1, 9_999_999_999))
    def test_every_id_in_range_is_accepted(self, number: int) -> None:
        self.assertTrue(probe_ids.is_tmdb_id(str(number)))


class _FolderCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name) / "out"
        self.folder.mkdir()

    def refusal(self) -> str:
        with self.assertRaises(probe_input.InputError) as caught:
            probe_input.read_imports(self.folder)
        return str(caught.exception)


class ReadImportsTest(_FolderCase):
    def test_rows_are_read_from_every_import_file_in_number_order(self) -> None:
        write_import(self.folder, "letterboxd-002.csv", [import_row("30", "Third")])
        write_import(
            self.folder,
            "letterboxd-001.csv",
            [import_row("10", "First", "1950"), import_row("20", "Second")],
        )
        rows = probe_input.read_imports(self.folder)
        self.assertEqual(rows.files, ("letterboxd-001.csv", "letterboxd-002.csv"))
        self.assertEqual(rows.rows, 3)
        self.assertEqual(rows.by_title, 0)
        self.assertEqual(
            rows.id_rows,
            (
                probe_input.IdRow("letterboxd-001.csv", 1, "10", "First", "1950"),
                probe_input.IdRow("letterboxd-001.csv", 2, "20", "Second", "1999"),
                probe_input.IdRow("letterboxd-002.csv", 1, "30", "Third", "1999"),
            ),
        )

    def test_numbers_sort_as_numbers_past_three_digits(self) -> None:
        write_import(self.folder, "letterboxd-1000.csv", [import_row("2", "Later")])
        write_import(self.folder, "letterboxd-999.csv", [import_row("1", "Earlier")])
        rows = probe_input.read_imports(self.folder)
        self.assertEqual(rows.files, ("letterboxd-999.csv", "letterboxd-1000.csv"))

    def test_other_files_are_not_read(self) -> None:
        write_import(self.folder, "letterboxd-001.csv", [import_row("10", "First")])
        for name in ("review.md", "letterboxd-test.csv", "letterboxd-01.csv"):
            (self.folder / name).write_text(
                "not an import file", encoding="utf-8", newline="\n"
            )
        (self.folder / "letterboxd-002.csv").mkdir()
        rows = probe_input.read_imports(self.folder)
        self.assertEqual(rows.files, ("letterboxd-001.csv",))

    def test_a_row_number_counts_records_not_lines(self) -> None:
        # A review spanning lines is one row: the row after it is row 2, not line 4.
        multi = list(import_row("10", "First"))
        multi[COLUMNS.index("Review")] = "line one\nline two"
        write_import(
            self.folder, "letterboxd-001.csv", [multi, import_row("20", "Second")]
        )
        rows = probe_input.read_imports(self.folder)
        self.assertEqual([row.row for row in rows.id_rows], [1, 2])

    def test_a_review_past_csvs_default_field_limit_is_read(self) -> None:
        # Export writes a review cell of any length its file cap allows, past csv's
        # default limit of 131,072 characters; probe must read it, not refuse it.
        long = list(import_row("10", "First"))
        long[COLUMNS.index("Review")] = "x" * (_CSV_DEFAULT_FIELD_LIMIT + 1)
        write_import(self.folder, "letterboxd-001.csv", [long])
        rows = probe_input.read_imports(self.folder)
        self.assertEqual([row.tmdb_id for row in rows.id_rows], ["10"])
        self.assertEqual(csv.field_size_limit(), _CSV_DEFAULT_FIELD_LIMIT)

    def test_a_by_title_row_is_counted_and_skipped(self) -> None:
        write_import(
            self.folder,
            "letterboxd-001.csv",
            [import_row("", "By Title"), import_row("20", "Second")],
        )
        rows = probe_input.read_imports(self.folder)
        self.assertEqual((rows.rows, rows.by_title), (2, 1))
        self.assertEqual([row.tmdb_id for row in rows.id_rows], ["20"])

    def test_distinct_ids_keep_first_seen_order_across_files_and_rewatches(
        self,
    ) -> None:
        write_import(
            self.folder,
            "letterboxd-001.csv",
            [import_row("20", "B"), import_row("10", "A"), import_row("20", "B")],
        )
        write_import(self.folder, "letterboxd-002.csv", [import_row("10", "A")])
        rows = probe_input.read_imports(self.folder)
        self.assertEqual(rows.distinct_ids(), ("20", "10"))


class RefusalTest(_FolderCase):
    def test_a_missing_folder(self) -> None:
        self.folder.rmdir()
        self.assertEqual(self.refusal(), "--files: no such folder")

    def test_a_folder_with_no_import_file(self) -> None:
        (self.folder / "review.md").write_text("x", encoding="utf-8", newline="\n")
        self.assertIn("no letterboxd-NNN.csv", self.refusal())

    def test_a_header_other_than_exports(self) -> None:
        for header in (
            COLUMNS[:-1],
            (*COLUMNS[1:], COLUMNS[0]),
            ("tmdbid", *COLUMNS[1:]),
        ):
            with self.subTest(header=header):
                write_import(self.folder, "letterboxd-001.csv", [], header=header)
                self.assertEqual(
                    self.refusal(),
                    "letterboxd-001.csv: its header is not retrolog_export's",
                )

    def test_an_empty_file(self) -> None:
        (self.folder / "letterboxd-001.csv").write_bytes(b"")
        self.assertEqual(
            self.refusal(), "letterboxd-001.csv: its header is not retrolog_export's"
        )

    def test_a_row_of_the_wrong_width(self) -> None:
        write_import(self.folder, "letterboxd-001.csv", [import_row("1", "A")[:-1]])
        self.assertEqual(
            self.refusal(), "letterboxd-001.csv row 1: not one cell per column"
        )

    def test_an_id_the_rule_refuses_names_the_row_not_the_value(self) -> None:
        write_import(
            self.folder,
            "letterboxd-001.csv",
            [import_row("1", "A"), import_row("0123", "B")],
        )
        message = self.refusal()
        self.assertEqual(
            message, "letterboxd-001.csv row 2: its tmdbID is not a TMDb id"
        )
        self.assertNotIn("0123", message)

    def test_text_that_is_not_utf8(self) -> None:
        (self.folder / "letterboxd-001.csv").write_bytes(b"\xff\xfe")
        self.assertEqual(self.refusal(), "letterboxd-001.csv: not UTF-8 text")

    def test_a_file_the_csv_reader_refuses(self) -> None:
        header = ",".join(COLUMNS)
        (self.folder / "letterboxd-001.csv").write_text(
            f'{header}\n"unclosed', encoding="utf-8", newline="\n"
        )
        # The csv module's strict mode refuses an unclosed quote at the end.
        self.assertEqual(self.refusal(), "letterboxd-001.csv: not a CSV file (Error)")

    def test_an_unreadable_file(self) -> None:
        path = write_import(self.folder, "letterboxd-001.csv", [import_row("1", "A")])
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        self.assertEqual(
            self.refusal(), "could not read letterboxd-001.csv: PermissionError"
        )


if __name__ == "__main__":
    unittest.main()
