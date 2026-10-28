"""Tests for resolve_input: reading parse's CSV files back as rows."""

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_rows  # noqa: E402
import resolve_input  # noqa: E402
from parse_output import COLUMNS  # noqa: E402
from resolve_fixtures import csv_text, parse_row, write_parsed  # noqa: E402
from resolve_types import RunMedium  # noqa: E402


class ReadParsedTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.parsed = Path(holder.name) / "parsed"

    def _refusal(self, files: dict[str, str]) -> str:
        """The InputError message ``read_parsed`` gives for ``files``."""
        write_parsed(self.parsed, files)
        with self.assertRaises(resolve_input.InputError) as caught:
            resolve_input.read_parsed(self.parsed)
        return str(caught.exception)

    def test_rows_keep_their_cells_and_csv_name(self) -> None:
        row = parse_row(title="Moby Dick", comment='a "quoted", comma', year="1851")
        write_parsed(self.parsed, {"book-2010.csv": csv_text([row])})
        (only,) = resolve_input.read_parsed(self.parsed)
        self.assertEqual(only.source_csv, "book-2010.csv")
        self.assertEqual(dict(only.cells), row)
        self.assertEqual(tuple(only.cells), COLUMNS)
        self.assertEqual(only.medium, "book")
        self.assertEqual(only.title, "Moby Dick")
        self.assertEqual(only.title_key, parse_rows.title_key("Moby Dick"))
        self.assertEqual((only.work_year, only.date_year), (1851, 2010))
        self.assertEqual((only.source_file, only.source_line), ("10/read.txt", 1))

    def test_cells_are_read_as_text_not_reinterpreted(self) -> None:
        row = parse_row(title=" 007 ", rating_raw="0010", section="  ")
        write_parsed(self.parsed, {"book-2010.csv": csv_text([row])})
        (only,) = resolve_input.read_parsed(self.parsed)
        self.assertEqual(dict(only.cells), row)

    def test_undated_csv_rows_have_no_date_year(self) -> None:
        row = parse_row(medium="film", title="Metropolis", date="")
        write_parsed(self.parsed, {"film-undated.csv": csv_text([row])})
        (only,) = resolve_input.read_parsed(self.parsed)
        self.assertIsNone(only.date_year)
        self.assertIsNone(only.work_year)
        self.assertEqual(only.medium, "film")

    def test_files_are_read_in_name_order_and_others_ignored(self) -> None:
        book = parse_row(title="Placeholder Book One", date="2011-01-01")
        film = parse_row(medium="film", title="Placeholder Film One")
        write_parsed(
            self.parsed,
            {
                "film-2010.csv": csv_text([film]),
                "book-2011.csv": csv_text([book]),
                "review.json": "{}",
                "book-2011.csv.bak": "not read",
                "book-20111.csv": "not read",
                "notes.csv": "not read",
            },
        )
        (self.parsed / "book-2012.csv").mkdir()
        rows = resolve_input.read_parsed(self.parsed)
        self.assertEqual(
            [r.source_csv for r in rows], ["book-2011.csv", "film-2010.csv"]
        )

    def test_rows_keep_their_file_order(self) -> None:
        first = parse_row(title="Placeholder Book Two", source_line="9")
        second = parse_row(title="Placeholder Book One", source_line="2")
        write_parsed(self.parsed, {"book-2010.csv": csv_text([first, second])})
        rows = resolve_input.read_parsed(self.parsed)
        self.assertEqual([r.source_line for r in rows], [9, 2])

    def test_a_folder_with_no_parse_csv_is_refused(self) -> None:
        # A wrong --parsed (an empty folder, or one holding only parse's review)
        # must stop the run before --out is touched, not empty an earlier --out.
        for files in ({}, {"review.json": "{}", "notes.csv": "a\n"}):
            with self.subTest(files=sorted(files)):
                message = self._refusal(files)
                self.assertEqual(message, "--parsed: holds no parse CSV file")
                self.assertNotIn(str(self.parsed), message)

    def test_one_medium_reads_only_its_own_csv_files(self) -> None:
        book = parse_row(title="Placeholder Book One")
        film = parse_row(medium="film", title="Placeholder Film One")
        write_parsed(
            self.parsed,
            {"book-2010.csv": csv_text([book]), "film-2010.csv": csv_text([film])},
        )
        cases: tuple[tuple[RunMedium, list[str]], ...] = (
            ("book", ["book-2010.csv"]),
            ("film", ["film-2010.csv"]),
            ("all", ["book-2010.csv", "film-2010.csv"]),
        )
        for medium, expected in cases:
            with self.subTest(medium=medium):
                rows = resolve_input.read_parsed(self.parsed, medium)
                self.assertEqual([r.source_csv for r in rows], expected)

    def test_the_other_mediums_csv_is_not_even_read(self) -> None:
        # A damaged book CSV would be refused if it were read at all.
        film = parse_row(medium="film", title="Placeholder Film One")
        write_parsed(
            self.parsed,
            {"book-2010.csv": "not,parse\n", "film-2010.csv": csv_text([film])},
        )
        (only,) = resolve_input.read_parsed(self.parsed, "film")
        self.assertEqual(only.source_csv, "film-2010.csv")

    def test_a_folder_with_none_of_one_mediums_csv_is_refused(self) -> None:
        write_parsed(self.parsed, {"book-2010.csv": csv_text([parse_row()])})
        with self.assertRaises(resolve_input.InputError) as caught:
            resolve_input.read_parsed(self.parsed, "film")
        self.assertEqual(str(caught.exception), "--parsed: holds no film CSV file")

    def test_an_unreadable_folder_is_refused_without_its_path(self) -> None:
        # Regression: the scan sat outside every try, so the PermissionError (and the
        # absolute path its message carries) escaped and the run exited 1.
        self.parsed.mkdir()
        denied = PermissionError(13, "Permission denied", str(self.parsed))
        with mock.patch.object(Path, "iterdir", side_effect=denied):
            with self.assertRaises(resolve_input.InputError) as caught:
                resolve_input.read_parsed(self.parsed)
        self.assertEqual(str(caught.exception), "--parsed: cannot be read")

    def test_missing_folder_is_refused(self) -> None:
        with self.assertRaises(resolve_input.InputError) as caught:
            resolve_input.read_parsed(self.parsed)
        self.assertNotIn(str(self.parsed.parent), str(caught.exception))

    def test_header_other_than_parse_columns_is_refused(self) -> None:
        row = parse_row()
        for header in (COLUMNS[:-1], (*COLUMNS, "extra"), tuple(reversed(COLUMNS))):
            with self.subTest(header=header[:2]):
                message = self._refusal({"book-2010.csv": csv_text([row], header)})
                self.assertIn("book-2010.csv", message)
                self.assertIn("header", message)

    def test_empty_file_is_refused_for_its_header(self) -> None:
        self.assertIn("header", self._refusal({"book-2010.csv": ""}))

    def test_medium_cell_other_than_the_file_names_is_refused(self) -> None:
        row = parse_row(medium="film")
        message = self._refusal({"book-2010.csv": csv_text([row])})
        self.assertIn("book-2010.csv: line 2: medium", message)

    def test_bad_year_cells_are_refused(self) -> None:
        for year in ("18x1", "-5", "١٨٥١", " 1851"):
            with self.subTest(year=year):
                message = self._refusal(
                    {"book-2010.csv": csv_text([parse_row(year=year)])}
                )
                self.assertIn("line 2: year", message)
                self.assertNotIn(year, message)

    def test_bad_date_cells_are_refused(self) -> None:
        for date in ("2010-13-01", "20100304", "2010-03-04T00:00", "soon"):
            with self.subTest(date=date):
                message = self._refusal(
                    {"book-2010.csv": csv_text([parse_row(date=date)])}
                )
                self.assertIn("line 2: date", message)

    def test_date_other_than_the_file_names_year_is_refused(self) -> None:
        cases = {
            "book-2010.csv": "2011-01-01",
            "book-undated.csv": "2010-01-01",
        }
        for name, date in cases.items():
            with self.subTest(name=name):
                self.parsed = self.parsed.with_name(name)  # a fresh folder per case
                message = self._refusal({name: csv_text([parse_row(date=date)])})
                self.assertIn(f"{name}: line 2: date", message)

    def test_undated_row_in_a_dated_file_is_refused(self) -> None:
        message = self._refusal({"book-2010.csv": csv_text([parse_row(date="")])})
        self.assertIn("line 2: date", message)

    def test_bad_source_line_cells_are_refused(self) -> None:
        for line in ("0", "", "1.5", "01"):
            with self.subTest(line=line):
                rows = [parse_row(source_line=line)]
                message = self._refusal({"book-2010.csv": csv_text(rows)})
                self.assertIn("line 2: source_line", message)

    def test_an_overlong_year_or_line_is_refused_not_raised(self) -> None:
        # Regression: int() raises ValueError past 4,300 digits, which escaped
        # as a traceback instead of the file's refusal.
        long = "1" * 5000
        for cells, column in (
            ({"year": long}, "year"),
            ({"source_line": long}, "source_line"),
        ):
            with self.subTest(column=column):
                rows = [parse_row(**cells)]
                message = self._refusal({"book-2010.csv": csv_text(rows)})
                self.assertIn(f"line 2: {column}", message)

    def test_a_year_of_five_digits_or_a_line_of_ten_is_refused(self) -> None:
        for cells, column in (
            ({"year": "12345"}, "year"),
            ({"source_line": "1" * 10}, "source_line"),
        ):
            with self.subTest(column=column):
                rows = [parse_row(**cells)]
                message = self._refusal({"book-2010.csv": csv_text(rows)})
                self.assertIn(f"line 2: {column}", message)
        write_parsed(
            self.parsed,
            {"book-2010.csv": csv_text([parse_row(year="9999", source_line="9" * 9)])},
        )
        (only,) = resolve_input.read_parsed(self.parsed)
        self.assertEqual((only.work_year, only.source_line), (9999, 999_999_999))

    def test_record_with_the_wrong_cell_count_is_refused(self) -> None:
        good = csv_text([parse_row()])
        for extra in ("a,b\n", good.splitlines()[1] + ",spare\n"):
            with self.subTest(extra=extra[:5]):
                message = self._refusal({"book-2010.csv": good + extra})
                self.assertIn("book-2010.csv: line 3:", message)
                self.assertIn("cells", message)

    def test_record_lines_count_physical_lines(self) -> None:
        first = parse_row(comment="two\nlines")
        second = parse_row(year="later")
        message = self._refusal({"book-2010.csv": csv_text([first, second])})
        self.assertIn("line 4: year", message)

    def test_undecodable_file_is_refused(self) -> None:
        self.parsed.mkdir()
        (self.parsed / "book-2010.csv").write_bytes(b"\xff\xfe" + b"medium\n")
        with self.assertRaises(resolve_input.InputError) as caught:
            resolve_input.read_parsed(self.parsed)
        self.assertIn("book-2010.csv: not UTF-8", str(caught.exception))

    def test_malformed_csv_is_refused(self) -> None:
        good = csv_text([parse_row()])
        message = self._refusal({"book-2010.csv": good + 'book,"unclosed\n'})
        self.assertIn("book-2010.csv", message)
        self.assertIn("not valid CSV", message)

    def test_a_read_the_os_refuses_is_refused_by_name(self) -> None:
        """The OS's refusal raised directly, so this runs where chmod cannot cause it."""
        write_parsed(self.parsed, {"book-2010.csv": csv_text([parse_row()])})
        target = self.parsed / "book-2010.csv"
        real_open = Path.open

        def refusing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path == target:
                raise PermissionError(
                    errno.EACCES, os.strerror(errno.EACCES), str(path)
                )
            return real_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", refusing_open):
            message = self._refusal({})
        self.assertIn("book-2010.csv: cannot be read", message)

    @unittest.skipIf(
        sys.platform == "win32" or os.geteuid() == 0,
        "chmod cannot deny the superuser, and on Windows sets only a read-only attribute",
    )
    def test_unreadable_file_is_refused_by_name(self) -> None:
        write_parsed(self.parsed, {"book-2010.csv": csv_text([parse_row()])})
        target = self.parsed / "book-2010.csv"
        target.chmod(0)
        self.addCleanup(target.chmod, 0o600)
        message = self._refusal({})
        self.assertIn("book-2010.csv: cannot be read", message)

    def test_fixture_refuses_a_cell_that_is_not_a_parse_column(self) -> None:
        # Guards the fixture itself: a misspelled column would otherwise be dropped
        # by csv_text and the test would silently check the default instead.
        with self.assertRaises(KeyError):
            parse_row(titel="Moby Dick")

    def test_messages_never_name_the_parsed_folder(self) -> None:
        message = self._refusal({"book-2010.csv": csv_text([parse_row(year="x")])})
        self.assertNotIn(str(self.parsed), message)


if __name__ == "__main__":
    unittest.main()
