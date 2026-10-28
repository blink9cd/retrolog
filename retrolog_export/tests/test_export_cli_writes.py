"""Tests for what retrolog_export leaves in --out: stale files, write order, cell flags.

A run removes the previous run's review and every earlier import file before it writes
anything, then writes the import files, then the review last, so a folder with no
review is an incomplete run. The review also lists a sent row whose cell a
spreadsheet would read as a formula, leaving the cell untouched.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL))
import retrolog_export  # noqa: E402 - after the sys.path inserts above
import survey_write  # noqa: E402 - retrolog_export puts its folder on sys.path
from export_cli_case import (  # noqa: E402 - after the sys.path inserts above
    EXIT_OK,
    EXIT_USAGE,
    FILM_ONE,
    METROPOLIS,
    CliCase,
)
from export_fixtures import resolved_row  # noqa: E402 - after the sys.path inserts

_REVIEWS = ("review.json", "review.md")


class _FailingWrite:
    """A ``write_atomic`` that writes ``good`` files for real, then fails each call."""

    def __init__(self, good: int) -> None:
        self.good = good
        self.names: list[str] = []

    def __call__(self, path: Path, text: str) -> None:
        self.names.append(path.name)
        if len(self.names) > self.good:
            raise PermissionError(13, "denied")
        survey_write.write_atomic(path, text)


class StaleOutputTest(CliCase):
    def test_a_stale_import_file_is_removed_and_other_files_kept(self) -> None:
        self.main()
        for name in ("letterboxd-002.csv", "notes.csv", "film-2010.csv"):
            (self.out / name).write_bytes(b"old\n")
        (self.out / "letterboxd-003.csv").symlink_to(self.base / "elsewhere")
        (self.out / "letterboxd-004.csv").mkdir()
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        names = {path.name for path in self.out.iterdir()}
        self.assertFalse({"letterboxd-002.csv", "letterboxd-003.csv"} & names)
        self.assertTrue(
            {"notes.csv", "film-2010.csv", "letterboxd-001.csv"} <= names, names
        )
        self.assertTrue((self.out / "letterboxd-004.csv").is_dir())


class WriteOrderTest(CliCase):
    """A write failing midway leaves no review and no file of an earlier run."""

    def _second_run_failing_after(self, good: int) -> _FailingWrite:
        """Run once with a file per film, then again with writes failing after ``good``."""
        self.assertEqual(self.main("--rows-per-file", "1"), EXIT_OK, self.stderr)
        return self._run_failing_after(good)

    def _run_failing_after(self, good: int) -> _FailingWrite:
        """Run with a file per film, every write after the first ``good`` failing."""
        failing = _FailingWrite(good)
        with mock.patch.object(retrolog_export, "write_atomic", side_effect=failing):
            self.assertEqual(self.main("--rows-per-file", "1"), EXIT_USAGE)
        self.assertIn("could not write the output", self.stderr)
        return failing

    def test_a_failed_write_leaves_no_review_of_an_earlier_run(self) -> None:
        # Regression: the previous run's review stayed beside the new files, reading
        # as if this run had finished.
        self._second_run_failing_after(1)
        names = {path.name for path in self.out.iterdir()}
        self.assertFalse(set(_REVIEWS) & names, names)

    def test_a_failed_write_leaves_no_import_file_of_an_earlier_run(self) -> None:
        # Regression: an earlier run's numbered files were removed only after every
        # new one was written, so a failure mixed old files with new ones.
        self._second_run_failing_after(1)
        self.assertEqual(self.import_names(), ["letterboxd-001.csv"])

    def test_the_import_files_are_written_before_the_review(self) -> None:
        recording = _FailingWrite(good=10)
        with mock.patch.object(retrolog_export, "write_atomic", side_effect=recording):
            self.assertEqual(self.main("--rows-per-file", "1"), EXIT_OK, self.stderr)
        self.assertEqual(
            recording.names, ["letterboxd-001.csv", "letterboxd-002.csv", *_REVIEWS]
        )

    def test_a_review_that_is_a_link_is_removed_not_followed(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        target = self.base / "elsewhere.json"
        target.write_bytes(b"{}")
        (self.out / "review.json").unlink()
        (self.out / "review.json").symlink_to(target)
        self._run_failing_after(0)
        self.assertFalse((self.out / "review.json").is_symlink())
        self.assertEqual(target.read_bytes(), b"{}")


class FormulaCellTest(CliCase):
    """A sent cell a spreadsheet would run as a formula is listed, not changed."""

    def test_a_formula_start_is_listed_and_the_cell_is_untouched(self) -> None:
        formula = resolved_row(title="=Placeholder Sum", tmdb_id="1000003")
        self.write_rows([FILM_ONE, METROPOLIS, formula])
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertIn("=Placeholder Sum", self.read("letterboxd-001.csv"))
        entries = json.loads(self.read("review.json"))["entries"]
        flagged = [e for e in entries if e["kind"] == "formula-cell"]
        self.assertEqual([e["title"] for e in flagged], ["=Placeholder Sum"])
        self.assertFalse(flagged[0]["error"])
        self.assertIn("Title", flagged[0]["detail"])
        self.assertIn("review formula-cell: 1", self.stdout)

    def test_a_directors_or_review_cell_is_checked_too(self) -> None:
        rows = [
            resolved_row(creator="-Placeholder Director"),
            resolved_row(comment="@noted", tmdb_id="1000004", source_line="2"),
        ]
        self.write_rows(rows)
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        entries = json.loads(self.read("review.json"))["entries"]
        details = sorted(e["detail"] for e in entries if e["kind"] == "formula-cell")
        self.assertEqual(len(details), 2)
        self.assertIn("Directors", details[0])
        self.assertIn("Review", details[1])


if __name__ == "__main__":
    unittest.main()
