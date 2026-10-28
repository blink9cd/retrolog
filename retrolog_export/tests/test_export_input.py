"""Tests for export_input: reading resolve's film CSV files back as rows."""

from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL))
# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_run")
import bounded_run  # noqa: E402 - after the sys.path inserts above
import export_input  # noqa: E402 - after the sys.path inserts above
import parse_rows  # noqa: E402 - after the sys.path inserts above
import retrolog_export  # noqa: E402 - after the sys.path inserts above
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    csv_text,
    resolved_row,
    write_resolved,
)
from parse_output import COLUMNS  # noqa: E402 - after the sys.path inserts above


class ReadResolvedTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.resolved = Path(holder.name) / "resolved"

    def _refusal(self, files: dict[str, str]) -> str:
        """The InputError message ``read_resolved`` gives for ``files``."""
        write_resolved(self.resolved, files)
        with self.assertRaises(export_input.InputError) as caught:
            export_input.read_resolved(self.resolved, "film")
        message = str(caught.exception)
        self.assertNotIn(str(self.resolved.parent), message)
        return message

    def _one(self, **cells: str) -> str:
        """The refusal for one film-2010.csv row with ``cells`` changed."""
        return self._refusal({"film-2010.csv": csv_text([resolved_row(**cells)])})

    def test_input_columns_are_resolves(self) -> None:
        self.assertEqual(export_input.INPUT_COLUMNS[: len(COLUMNS)], COLUMNS)
        self.assertEqual(
            export_input.INPUT_COLUMNS[len(COLUMNS) :],
            (
                "resolve_status",
                "isbn13",
                "ol_work",
                "tmdb_id",
                "tmdb_title",
                "tmdb_original_title",
            ),
        )

    def test_tmdb_titles_are_read_as_title_keys(self) -> None:
        row = resolved_row(
            tmdb_title="Placeholder Story 2", tmdb_original_title="Histoire Factice 2"
        )
        write_resolved(self.resolved, {"film-2010.csv": csv_text([row])})
        (only,) = export_input.read_resolved(self.resolved, "film")
        self.assertEqual(
            only.tmdb_title_keys, ("placeholder story 2", "histoire factice 2")
        )

    def test_a_tmdb_title_is_also_keyed_without_accents_and_with_and(self) -> None:
        row = resolved_row(tmdb_title="Placeholder Café & Co", tmdb_original_title="")
        write_resolved(self.resolved, {"film-2010.csv": csv_text([row])})
        (only,) = export_input.read_resolved(self.resolved, "film")
        self.assertEqual(
            only.tmdb_title_keys, ("placeholder café co", "placeholder cafe and co")
        )

    def test_a_repeated_or_letterless_tmdb_title_is_kept_once_or_dropped(self) -> None:
        cases = {
            ("Metropolis", "Metropolis"): ("metropolis",),
            ("Metropolis", ""): ("metropolis",),
            ("!!!", "Metropolis"): ("metropolis",),
            ("", ""): (),
        }
        for (title, original), keys in cases.items():
            with self.subTest(title=title, original=original):
                row = resolved_row(tmdb_title=title, tmdb_original_title=original)
                write_resolved(self.resolved, {"film-2010.csv": csv_text([row])})
                (only,) = export_input.read_resolved(self.resolved, "film")
                self.assertEqual(only.tmdb_title_keys, keys)

    def test_a_tmdb_title_with_no_tmdb_id_is_refused(self) -> None:
        for column in ("tmdb_title", "tmdb_original_title"):
            with self.subTest(column=column):
                cells = {"tmdb_id": "", "resolve_status": "not-found", column: "X"}
                self.assertEqual(
                    self._one(**cells),
                    f"film-2010.csv: line 2: {column} must be empty with no tmdb_id",
                )

    def test_an_older_resolve_header_is_refused_with_a_rerun_hint(self) -> None:
        # Regression: a CSV from before the TMDb title columns must not be read as
        # if no film had a TMDb title, which would match the account on list titles.
        older = export_input.INPUT_COLUMNS[:-2]
        message = self._refusal(
            {"film-2010.csv": csv_text([resolved_row()], header=older)}
        )
        self.assertIn("rerun retrolog_resolve", message)

    def test_rows_keep_resolve_values(self) -> None:
        row = resolved_row(
            title="Metropolis",
            creator="Fritz Lang & Placeholder Second",
            year="1927",
            rating_raw="2/3",
            comment='a "quoted", comma',
            rewatch="true",
            resolve_status="override",
            tmdb_id="424242",
            source_file="10/seen.txt",
            source_line="7",
        )
        write_resolved(self.resolved, {"film-2010.csv": csv_text([row])})
        (only,) = export_input.read_resolved(self.resolved, "film")
        self.assertEqual(only.source_csv, "film-2010.csv")
        self.assertEqual(only.title, "Metropolis")
        self.assertEqual(only.title_key, parse_rows.title_key("Metropolis"))
        self.assertEqual(only.year, 1927)
        self.assertEqual(only.date, datetime.date(2010, 3, 4))
        self.assertEqual(only.rating_raw, "2/3")
        self.assertEqual(only.comment, 'a "quoted", comma')
        self.assertTrue(only.rewatch)
        self.assertEqual(only.creator, "Fritz Lang & Placeholder Second")
        self.assertEqual(only.status, "override")
        self.assertEqual(only.tmdb_id, "424242")
        self.assertEqual((only.source_file, only.source_line), ("10/seen.txt", 7))
        self.assertEqual(only.where, "10/seen.txt:7")

    def test_empty_cells_read_as_none_or_empty(self) -> None:
        row = resolved_row(
            year="", rating_raw="", tmdb_id="", creator="", resolve_status="not-found"
        )
        write_resolved(self.resolved, {"film-2010.csv": csv_text([row])})
        (only,) = export_input.read_resolved(self.resolved, "film")
        self.assertIsNone(only.year)
        self.assertEqual((only.rating_raw, only.tmdb_id, only.creator), ("", "", ""))
        self.assertFalse(only.rewatch)
        self.assertEqual(only.status, "not-found")

    def test_undated_csv_rows_have_no_date(self) -> None:
        row = resolved_row(title="Metropolis", date="")
        write_resolved(self.resolved, {"film-undated.csv": csv_text([row])})
        (only,) = export_input.read_resolved(self.resolved, "film")
        self.assertIsNone(only.date)
        self.assertEqual(only.source_csv, "film-undated.csv")

    def test_files_are_read_in_name_order_rows_in_file_order(self) -> None:
        first = resolved_row(title="Placeholder Film One", date="2009-01-01")
        second = resolved_row(title="Placeholder Film Two", date="2009-01-02")
        later = resolved_row(title="Placeholder Film Three", date="2010-05-06")
        undated = resolved_row(title="Placeholder Film Four", date="")
        write_resolved(
            self.resolved,
            {
                "film-undated.csv": csv_text([undated]),
                "film-2010.csv": csv_text([later]),
                "film-2009.csv": csv_text([first, second]),
            },
        )
        rows = export_input.read_resolved(self.resolved, "film")
        self.assertEqual(
            [row.title for row in rows],
            [
                "Placeholder Film One",
                "Placeholder Film Two",
                "Placeholder Film Three",
                "Placeholder Film Four",
            ],
        )

    def test_book_csvs_are_ignored(self) -> None:
        # A damaged book CSV beside a good film one: never opened, so never refused.
        write_resolved(
            self.resolved,
            {
                "book-2010.csv": "not, resolve's, header\n",
                "film-2010.csv": csv_text([resolved_row()]),
            },
        )
        rows = export_input.read_resolved(self.resolved, "film")
        self.assertEqual([row.source_csv for row in rows], ["film-2010.csv"])

    def test_other_files_and_folders_are_ignored(self) -> None:
        write_resolved(
            self.resolved,
            {
                "film-2010.csv": csv_text([resolved_row()]),
                "review.json": "{}",
                "film-10.csv": "damaged",
                "Film-2011.csv": "damaged",
            },
        )
        (self.resolved / "film-2012.csv").mkdir()
        rows = export_input.read_resolved(self.resolved, "film")
        self.assertEqual([row.source_csv for row in rows], ["film-2010.csv"])

    def test_missing_folder_is_refused(self) -> None:
        with self.assertRaises(export_input.InputError) as caught:
            export_input.read_resolved(self.resolved, "film")
        self.assertEqual(str(caught.exception), "--resolved: no such folder")

    def test_folder_without_a_film_csv_is_refused(self) -> None:
        message = self._refusal({"book-2010.csv": csv_text([resolved_row()])})
        self.assertEqual(message, "--resolved: holds no film CSV file")

    def test_unlistable_folder_is_refused(self) -> None:
        write_resolved(self.resolved, {"film-2010.csv": csv_text([resolved_row()])})
        with (
            mock.patch.object(Path, "iterdir", side_effect=PermissionError("x")),
            self.assertRaises(export_input.InputError) as caught,
        ):
            export_input.read_resolved(self.resolved, "film")
        self.assertEqual(str(caught.exception), "--resolved: cannot be read")

    def test_header_other_than_resolves_is_refused(self) -> None:
        message = self._refusal(
            {"film-2010.csv": csv_text([resolved_row()], header=COLUMNS)}
        )
        self.assertEqual(
            message,
            "film-2010.csv: header is not resolve's columns, in order; rerun "
            "retrolog_resolve to rewrite it",
        )

    def test_empty_file_is_refused_by_its_header(self) -> None:
        message = self._refusal({"film-2010.csv": ""})
        self.assertEqual(
            message,
            "film-2010.csv: header is not resolve's columns, in order; rerun "
            "retrolog_resolve to rewrite it",
        )

    def test_bad_rewatch_cell_is_refused(self) -> None:
        for cell in ("", "yes", "True", "1"):
            with self.subTest(cell=cell):
                message = self._one(rewatch=cell)
                self.assertEqual(
                    message, "film-2010.csv: line 2: rewatch must be true or false"
                )

    def test_medium_other_than_the_files_is_refused(self) -> None:
        message = self._one(medium="book")
        self.assertEqual(
            message, "film-2010.csv: line 2: medium differs from the file name's"
        )

    def test_bad_year_is_refused(self) -> None:
        for cell in ("19x9", "-1", " 1999", "１９９９"):
            with self.subTest(cell=cell):
                self.assertEqual(
                    self._one(year=cell),
                    "film-2010.csv: line 2: year must be empty or one to four digits",
                )

    def test_an_overlong_year_or_line_is_refused_not_raised(self) -> None:
        # Regression: int() raises ValueError past 4,300 digits, which escaped
        # as a traceback instead of the file's refusal.
        long = "1" * 5000
        self.assertEqual(
            self._one(year=long),
            "film-2010.csv: line 2: year must be empty or one to four digits",
        )
        self.assertEqual(
            self._one(source_line=long),
            "film-2010.csv: line 2: source_line must be a positive whole number",
        )

    def test_a_year_of_five_digits_or_a_line_of_ten_is_refused(self) -> None:
        self.assertIn("year must be", self._one(year="12345"))
        self.assertIn("source_line must be", self._one(source_line="1" * 10))
        write_resolved(
            self.resolved,
            {"film-2010.csv": csv_text([resolved_row(source_line="9" * 9)])},
        )
        (only,) = export_input.read_resolved(self.resolved, "film")
        self.assertEqual(only.source_line, 999_999_999)

    def test_bad_date_is_refused(self) -> None:
        for cell in ("", "2010-02-30", "2010-3-04", "next tuesday"):
            with self.subTest(cell=cell):
                self.assertEqual(
                    self._one(date=cell),
                    "film-2010.csv: line 2: date must be a date such as 2001-12-31",
                )

    def test_date_outside_the_files_year_is_refused(self) -> None:
        self.assertEqual(
            self._one(date="2011-01-01"),
            "film-2010.csv: line 2: date is not in the year the file name gives",
        )

    def test_dated_row_in_the_undated_file_is_refused(self) -> None:
        message = self._refusal({"film-undated.csv": csv_text([resolved_row()])})
        self.assertEqual(
            message, "film-undated.csv: line 2: date must be empty in an undated file"
        )

    def test_bad_line_is_refused(self) -> None:
        for cell in ("", "0", "07", "x"):
            with self.subTest(cell=cell):
                self.assertEqual(
                    self._one(source_line=cell),
                    "film-2010.csv: line 2: source_line must be a positive whole "
                    "number",
                )

    def test_bad_status_is_refused(self) -> None:
        for cell in ("", "Resolved", "pending"):
            with self.subTest(cell=cell):
                self.assertEqual(
                    self._one(resolve_status=cell),
                    "film-2010.csv: line 2: resolve_status is not one of resolve's",
                )

    def test_bad_tmdb_id_is_refused(self) -> None:
        for cell in ("12a", " 12", "-1"):
            with self.subTest(cell=cell):
                self.assertEqual(
                    self._one(tmdb_id=cell),
                    "film-2010.csv: line 2: tmdb_id must be empty or digits",
                )

    def test_wrong_cell_count_is_refused(self) -> None:
        text = csv_text([resolved_row()])
        for damaged in (text.rstrip("\n") + ",extra\n", text.rsplit(",", 1)[0] + "\n"):
            with self.subTest(damaged=damaged[-12:]):
                self.assertEqual(
                    self._refusal({"film-2010.csv": damaged}),
                    "film-2010.csv: line 2: has a different number of cells than "
                    "the header",
                )

    def test_line_numbers_count_physical_lines(self) -> None:
        rows = [resolved_row(comment="two\nlines"), resolved_row(rewatch="no")]
        message = self._refusal({"film-2010.csv": csv_text(rows)})
        self.assertTrue(message.startswith("film-2010.csv: line 4: "), message)

    def test_non_utf8_file_is_refused(self) -> None:
        self.resolved.mkdir()
        header = ",".join(export_input.INPUT_COLUMNS).encode()
        (self.resolved / "film-2010.csv").write_bytes(header + b"\n\xff\xfe\n")
        with self.assertRaises(export_input.InputError) as caught:
            export_input.read_resolved(self.resolved, "film")
        self.assertEqual(str(caught.exception), "film-2010.csv: not UTF-8")

    def test_invalid_csv_is_refused(self) -> None:
        text = csv_text([resolved_row()]) + '"an unclosed quote\n'
        self.assertEqual(
            self._refusal({"film-2010.csv": text}), "film-2010.csv: not valid CSV"
        )

    def test_unreadable_file_is_refused(self) -> None:
        write_resolved(self.resolved, {"film-2010.csv": csv_text([resolved_row()])})
        with (
            mock.patch.object(Path, "open", side_effect=PermissionError("x")),
            self.assertRaises(export_input.InputError) as caught,
        ):
            export_input.read_resolved(self.resolved, "film")
        self.assertEqual(str(caught.exception), "film-2010.csv: cannot be read")

    def test_refusal_never_quotes_the_cell(self) -> None:
        message = self._one(year="Placeholder Secret Text")
        self.assertNotIn("Placeholder Secret Text", message)


# Seconds a child interpreter may take to import the tool's modules.
_CHILD_TIMEOUT = 60


class EntryPointTest(unittest.TestCase):
    """The entry point alone puts every module export_input and export_config need in
    reach, so the command line built on it needs no path setup of its own.
    """

    def test_every_source_dir_exists_and_is_on_the_path(self) -> None:
        for folder in retrolog_export.SOURCE_DIRS:
            with self.subTest(folder=folder.name):
                self.assertTrue(folder.is_dir())
                self.assertIn(str(folder), sys.path)

    def test_entry_point_alone_reaches_the_modules(self) -> None:
        # -I -S: no PYTHONPATH, no site-packages and no script folder, so only the
        # entry point's own inserts can make the imports below resolve, on the
        # standard library alone. run_bounded kills the whole process tree on a
        # timeout before it reads the captured pipes.
        script = (
            "import runpy, sys; runpy.run_path(sys.argv[1]); "
            "import export_config, export_input"
        )
        entry = str(Path(retrolog_export.__file__))
        result = bounded_run.run_bounded(
            [sys.executable, "-I", "-S", "-c", script, entry],
            timeout=_CHILD_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ReadResolvedFuzzTest(unittest.TestCase):
    """Whatever bytes a film CSV holds, reading it yields rows or InputError only."""

    @settings(deadline=None)
    @given(st.binary(max_size=400), st.booleans())
    def test_fuzz_read_resolved(self, body: bytes, with_header: bool) -> None:
        header = ",".join(export_input.INPUT_COLUMNS).encode() + b"\n"
        with tempfile.TemporaryDirectory() as holder:
            resolved = Path(holder)
            (resolved / "film-2010.csv").write_bytes(
                (header if with_header else b"") + body
            )
            try:
                rows = export_input.read_resolved(resolved, "film")
            except export_input.InputError:
                return
            self.assertTrue(all(row.source_csv == "film-2010.csv" for row in rows))


if __name__ == "__main__":
    unittest.main()
