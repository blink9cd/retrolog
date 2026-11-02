"""Tests for the retrolog_restore command line: what it reads, writes, and returns.

Each test runs ``main`` end to end on two temporary account exports, with a git
stand-in, so no test reaches a real repository or a real account.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The tool's own src/ first, then export's, resolve's, parse's, and the survey's: the
# order retrolog_restore.SOURCE_DIRS puts them on sys.path.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_export" / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
    str(_TOOL / "samples"),
    str(_TOOL),
]
import build_restore_inputs  # noqa: E402 - after the sys.path inserts above
import retrolog_restore  # noqa: E402 - after the sys.path inserts above
import survey_guard  # noqa: E402 - after the sys.path inserts above
from restore_fixtures import (  # noqa: E402 - after the sys.path inserts above
    entry,
    export_files,
    write_folder,
    write_zip,
)

EXIT_OK, EXIT_NOTHING, EXIT_USAGE = 0, 1, 2
# git's exit code outside any repository, and inside one.
NOT_A_REPO, IN_A_REPO = 128, 0
_MARKER = ".retrolog_restore"
_CSV, _MD = "letterboxd-restore.csv", "restore.md"
_SECRET = "Placeholder Secret Title"
_KEPT = entry(Name="Placeholder Film One", **{"Letterboxd URI": "u1"})
_GONE = entry(
    Name=_SECRET,
    Year="2001",
    Rewatch="Yes",
    Tags="a, b",
    **{"Letterboxd URI": "u2", "Watched Date": "2026-01-05"},
)


def fake_git(code: int = NOT_A_REPO) -> survey_guard.GitRunner:
    """A git stand-in answering every question with ``code``."""

    def run(argv: Sequence[str], cwd: Path, timeout: float) -> int:
        return code

    return run


class RestoreCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        review = entry(
            Name=_SECRET,
            Year="2001",
            Review='Good, "very".\nYes.',
            **{"Watched Date": "2026-01-05"},
        )
        self.backup = write_zip(
            self.base / "backup.zip",
            export_files([_KEPT, _GONE], reviews=[review], likes=[_GONE]),
        )
        self.current = write_folder(self.base / "current", export_files([_KEPT]))
        self.out = self.base / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def main(
        self,
        *,
        backup: Path | None = None,
        current: Path | None = None,
        out: Path | None = None,
        run_git: survey_guard.GitRunner | None = None,
    ) -> int:
        """Run the CLI on the case's exports."""
        argv = ["--backup", str(backup or self.backup)]
        argv += ["--current", str(current or self.current)]
        argv += ["--out", str(out or self.out)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = retrolog_restore.main(argv, run_git=run_git or fake_git())
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def read(self, name: str) -> str:
        """One written output file's text."""
        return (self.out / name).read_text(encoding="utf-8")

    def assert_private(self) -> None:
        """Neither stream names the temporary folder or a title."""
        for text in (self.stdout, self.stderr):
            self.assertNotIn(str(self.base), text)
            self.assertNotIn(self._tmp.name, text)
            self.assertNotIn(_SECRET, text)

    def assert_refused(self, code: int, words: str) -> None:
        """Exit 2, ``words`` in the message, and no output written."""
        self.assertEqual(code, EXIT_USAGE, self.stderr)
        self.assertIn(words, self.stderr)
        self.assertFalse((self.out / _CSV).exists())
        self.assert_private()

    def test_the_deleted_entry_is_written_with_its_review(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(
            self.read(_CSV),
            "Title,Year,WatchedDate,Rating,Rewatch,Tags,Review\n"
            f'{_SECRET},2001,2026-01-05,3,true,"a, b","Good, ""very"".\nYes."\n',
        )
        self.assertIn(f"| {_SECRET} | 2001 | 2026-01-05 | yes |", self.read(_MD))
        self.assertTrue((self.out / _MARKER).is_file())

    def test_stdout_gives_counts_only(self) -> None:
        self.main()
        lines = self.stdout.splitlines()
        self.assertEqual(lines[:2], ["entries restored: 1", "to like again: 1"])
        self.assertIn(_MD, lines[2])
        self.assertEqual(self.stderr, "")
        self.assert_private()

    def test_a_formula_start_is_counted_and_written_unchanged(self) -> None:
        gone = entry(Name="=Placeholder", **{"Letterboxd URI": "u9"})
        backup = write_folder(self.base / "b", export_files([_KEPT, gone]))
        self.assertEqual(self.main(backup=backup), EXIT_OK, self.stderr)
        self.assertIn("review formula-cell: 1", self.stdout.splitlines())
        self.assertIn("\n=Placeholder,", self.read(_CSV))

    def test_a_zip_current_and_a_folder_backup_work_too(self) -> None:
        backup = write_folder(self.base / "b", export_files([_KEPT, _GONE]))
        current = write_zip(self.base / "c.zip", export_files([_KEPT]))
        self.assertEqual(self.main(backup=backup, current=current), EXIT_OK)
        self.assertIn(_SECRET, self.read(_CSV))

    def test_nothing_deleted_exits_1_hints_a_swap_and_writes_nothing(self) -> None:
        self.assertEqual(self.main(backup=self.current, current=self.backup), 1)
        self.assertIn("nothing to restore", self.stderr)
        self.assertIn("swapped", self.stderr)
        self.assertEqual(self.stdout, "")
        self.assertFalse(self.out.exists())
        self.assert_private()

    def test_an_export_without_a_diary_is_refused(self) -> None:
        current = write_folder(self.base / "c", {"watched.csv": "Name,Year\n"})
        code = self.main(current=current)
        self.assert_refused(code, "--current: holds no diary.csv")

    def test_an_unreadable_export_is_refused(self) -> None:
        self.backup.write_bytes(b"not a zip archive")
        self.assert_refused(self.main(), "--backup: not a readable folder or zip")

    def test_the_current_exports_reviews_are_not_read(self) -> None:
        files = export_files([_KEPT]) | {"reviews.csv": "Name\nx\n"}
        current = write_folder(self.base / "c", files)
        self.assertEqual(self.main(current=current), EXIT_OK, self.stderr)

    def test_a_film_still_liked_on_the_account_is_not_to_be_liked_again(
        self,
    ) -> None:
        current = write_folder(self.base / "c", export_files([_KEPT], likes=[_GONE]))
        self.assertEqual(self.main(current=current), EXIT_OK, self.stderr)
        self.assertIn(f"| {_SECRET} | 2001 | 2026-01-05 | |", self.read(_MD))
        self.assertIn("to like again: 0", self.stdout)

    def test_an_odd_rewatch_cell_is_refused_naming_the_backup(self) -> None:
        odd = entry(Name=_SECRET, Rewatch="No", **{"Letterboxd URI": "u3"})
        backup = write_folder(self.base / "b", export_files([_KEPT, odd]))
        code = self.main(backup=backup)
        self.assert_refused(code, "--backup: diary.csv: a restored entry's Rewatch")

    def test_an_entry_with_two_reviews_is_refused(self) -> None:
        review = entry(Name=_SECRET, Year="2001", **{"Watched Date": "2026-01-05"})
        files = export_files([_KEPT, _GONE], reviews=[review, review])
        code = self.main(backup=write_folder(self.base / "b", files))
        self.assert_refused(code, "--backup: reviews.csv: 1 restored entry matches")

    def test_out_inside_a_git_work_tree_is_refused(self) -> None:
        code = self.main(run_git=fake_git(IN_A_REPO))
        self.assert_refused(code, "refused --out")
        self.assertFalse(self.out.exists())

    def test_out_inside_an_export_folder_is_refused(self) -> None:
        backup = write_folder(self.base / "b", export_files([_KEPT, _GONE]))
        for out in (self.current / "out", backup / "out"):
            with self.subTest(out=out.name):
                code = self.main(backup=backup, out=out)
                self.assertEqual(code, EXIT_USAGE)
                self.assertIn("refused --out", self.stderr)
                self.assertFalse(out.exists())

    def test_a_folder_holding_other_files_is_refused(self) -> None:
        self.out.mkdir()
        (self.out / "notes.txt").write_bytes(b"x")
        self.assert_refused(self.main(), "refused --out")

    def test_out_is_checked_before_nothing_to_restore_is_reported(self) -> None:
        code = self.main(current=self.backup, run_git=fake_git(IN_A_REPO))
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("refused --out", self.stderr)
        self.assertIn("inside a git work tree", self.stderr)

    def test_a_rerun_replaces_the_earlier_output(self) -> None:
        self.assertEqual(self.main(), EXIT_OK)
        (self.out / _MD).write_bytes(b"stale")
        self.assertEqual(self.main(), EXIT_OK)
        self.assertNotIn("stale", self.read(_MD))

    def test_a_link_in_place_of_an_output_is_replaced_not_followed(self) -> None:
        self.assertEqual(self.main(), EXIT_OK)
        target = self.base / "target.txt"
        target.write_bytes(b"kept")
        (self.out / _MD).unlink()
        (self.out / _MD).symlink_to(target)
        self.assertEqual(self.main(), EXIT_OK)
        self.assertEqual(target.read_text(), "kept")
        self.assertFalse((self.out / _MD).is_symlink())

    def test_a_failed_write_exits_2_naming_no_path(self) -> None:
        error = PermissionError(13, "Permission denied", str(self.out / _CSV))
        with mock.patch.object(retrolog_restore, "write_atomic", side_effect=error):
            code = self.main()
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("could not write the output: PermissionError", self.stderr)
        self.assertFalse((self.out / _MD).exists())
        self.assert_private()

    def test_a_given_timeout_reaches_the_git_check(self) -> None:
        seen: list[float] = []

        def run(argv: Sequence[str], cwd: Path, timeout: float) -> int:
            seen.append(timeout)
            return NOT_A_REPO

        argv = ["--backup", str(self.backup), "--current", str(self.current)]
        argv += ["--out", str(self.out), "--timeout", "3"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(retrolog_restore.main(argv, run_git=run), EXIT_OK)
        self.assertEqual(set(seen), {3.0})

    def test_an_out_that_cannot_be_prepared_is_refused(self) -> None:
        refused = survey_guard.OutRefused("cannot inspect the output location")
        with mock.patch.object(survey_guard, "prepare_out_dir", side_effect=refused):
            code = self.main()
        self.assert_refused(code, "refused --out: cannot inspect")

    def test_the_timeout_must_be_positive_finite_and_bounded(self) -> None:
        # Regression: subprocess timeouts reject an infinite, a NaN, or a huge value
        # with a traceback, so each must be refused as a usage error instead.
        for value in ("0", "-1", "inf", "nan", "3601"):
            argv = ["--backup", "b", "--current", "c", "--out", "o", "--timeout", value]
            with self.subTest(value=value):
                stderr = io.StringIO()
                with (
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as raised,
                ):
                    retrolog_restore.main(argv, run_git=fake_git())
                self.assertEqual(raised.exception.code, EXIT_USAGE)
                self.assertIn("at most 3600", stderr.getvalue())

    def test_the_longest_timeout_is_accepted(self) -> None:
        args = retrolog_restore.build_parser().parse_args(
            ["--backup", "b", "--current", "c", "--out", "o", "--timeout", "3600"]
        )
        self.assertEqual(args.timeout, 3600.0)

    def test_help_shows_the_default_timeout_and_the_run_order(self) -> None:
        text = retrolog_restore.build_parser().format_help()
        self.assertIn("15.0", text)
        self.assertIn("at most 3600", text)
        self.assertIn("Run order", text)


class SampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, argv: Sequence[str]) -> Mapping[str, str]:
        """Run the CLI, return its exit code and streams."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = retrolog_restore.main(list(argv), run_git=fake_git())
        return {"code": str(code), "out": stdout.getvalue(), "err": stderr.getvalue()}

    def test_sample_output_is_reproduced(self) -> None:
        work = self.base / "work"
        self.assertEqual(build_restore_inputs.main(["--out", str(work)]), EXIT_OK)
        argv = ["--backup", str(work / "account" / "backup.zip")]
        argv += ["--current", str(work / "account" / "export-1")]
        argv += ["--out", str(work / "restore")]
        done = self._run(argv)
        self.assertEqual(done["code"], str(EXIT_OK), done["err"])
        self.assertEqual(
            done["out"].splitlines()[:2], ["entries restored: 4", "to like again: 1"]
        )
        expected = _TOOL / "samples" / "expected"
        out = work / "restore"
        written = sorted(p.name for p in out.iterdir() if p.name != _MARKER)
        self.assertEqual(written, sorted(p.name for p in expected.iterdir()))
        for name in written:
            self.assertEqual(
                (out / name).read_bytes(), (expected / name).read_bytes(), name
            )

    def test_sample_builder_refuses_a_non_empty_out(self) -> None:
        (self.base / "notes.txt").write_bytes(b"")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = build_restore_inputs.main(["--out", str(self.base)])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("must be missing or empty", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
