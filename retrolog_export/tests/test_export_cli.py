"""Tests for the retrolog_export command line: what it reads, writes and returns.

Each test runs ``main`` end to end on a temporary resolve folder, account export and
export config, with a git stand-in, so no test reaches a real repository, a real
account or the operator's own config.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from collections.abc import Sequence
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
sys.path.insert(0, str(_TOOL / "samples"))
sys.path.insert(0, str(_TOOL))
import build_export_inputs  # noqa: E402 - after the sys.path inserts above
import retrolog_export  # noqa: E402 - after the sys.path inserts above
import survey_guard  # noqa: E402 - retrolog_export puts its folder on sys.path
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    account_csv,
    config_toml,
    TomlValue,
    resolved_row,
    write_account_folder,
    write_account_zip,
)

from export_cli_case import (  # noqa: E402 - after the sys.path inserts above
    EXIT_ERRORS,
    EXIT_OK,
    EXIT_USAGE,
    fake_git,
    snapshot,
)
from export_cli_case import CliCase as _CliCase  # noqa: E402 - after the sys.path inserts
from export_cli_case import FILM_ONE as _FILM_ONE  # noqa: E402 - after the sys.path inserts
from export_cli_case import HEADER as _HEADER  # noqa: E402 - after the sys.path inserts above
from export_cli_case import IN_A_REPO as _IN_A_REPO  # noqa: E402 - after the sys.path inserts
from export_cli_case import MARKER as _MARKER  # noqa: E402 - after the sys.path inserts above
from export_cli_case import METROPOLIS as _METROPOLIS  # noqa: E402 - after the inserts
from export_cli_case import NOT_A_REPO as _NOT_A_REPO  # noqa: E402 - after the inserts
from export_cli_case import ON_ACCOUNT as _ON_ACCOUNT  # noqa: E402 - after the inserts


class CleanRunTest(_CliCase):
    def test_a_clean_run_exits_0_and_writes_every_output(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            sorted([_MARKER, "letterboxd-001.csv", "review.json", "review.md"]),
        )
        self.assertEqual(self.stderr, "")
        self.assert_no_absolute_path()

    def test_the_import_file_holds_the_header_and_each_row(self) -> None:
        self.main()
        self.assertEqual(
            self.read("letterboxd-001.csv"),
            _HEADER
            + "1000001,Placeholder Film One,1999,Placeholder Director,2.5,2010-03-04,"
            ",retrolog,\n"
            + "1000002,Metropolis,1927,Placeholder Director,2.5,2010-03-04,"
            ",retrolog,\n",
        )

    def test_the_summary_counts_only(self) -> None:
        self.main()
        self.assertEqual(
            self.stdout.splitlines(),
            ["films sent: 2", "rows sent: 2", "rows cut: 0", "files: 1"],
        )

    def test_the_inputs_are_never_written(self) -> None:
        before = snapshot(self.resolved), snapshot(self.account)
        self.main()
        self.assertEqual((snapshot(self.resolved), snapshot(self.account)), before)

    def test_a_second_run_after_the_import_sends_nothing(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.write_account(_ON_ACCOUNT + [("Placeholder Film One", "1999")])
        write_account_folder(
            self.account, {"diary.csv": account_csv([("Metropolis", "1927")])}
        )
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(self.import_names(), [])
        self.assertIn("films sent: 0", self.stdout)
        self.assertIn("review on-service: 2", self.stdout)

    def test_a_film_on_the_account_by_its_tmdb_title_is_not_sent(self) -> None:
        # TMDb titles end to end: the list's title differs from TMDb's, and the account knows
        # the film by TMDb's, so the film is on the service and its rating left alone.
        sequel = resolved_row(
            title="Placeholder Story II",
            year="2006",
            tmdb_id="1000013",
            tmdb_title="Placeholder Story 2",
        )
        self.write_rows([sequel])
        self.write_account([("Placeholder Story 2", "2006")])
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(self.import_names(), [])
        self.assertIn("films sent: 0", self.stdout)
        self.assertIn("review on-service: 1", self.stdout)

    def test_a_zip_account_export_is_read(self) -> None:
        archive = write_account_zip(
            self.base / "export.zip",
            {"watched.csv": account_csv([("Metropolis", "1927")])},
        )
        self.assertEqual(self.main(account=archive), EXIT_OK, self.stderr)
        self.assertNotIn("Metropolis", self.read("letterboxd-001.csv"))

    def test_a_small_row_cap_splits_the_films(self) -> None:
        self.assertEqual(self.main("--rows-per-file", "1"), EXIT_OK, self.stderr)
        self.assertEqual(
            self.import_names(), ["letterboxd-001.csv", "letterboxd-002.csv"]
        )
        for name in self.import_names():
            self.assertTrue(self.read(name).startswith(_HEADER))
        self.assertIn("files: 2", self.stdout)

    def test_the_cutoff_leaves_rows_out_and_counts_them(self) -> None:
        cutoff: dict[str, TomlValue] = {
            "file": "10/seen.txt",
            "line": 2,
            "title": "Metropolis",
        }
        self.config.write_bytes(config_toml(cutoff=cutoff))
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertIn("rows cut: 1", self.stdout)
        self.assertNotIn("Metropolis", self.read("letterboxd-001.csv"))

    def test_the_timeout_reaches_the_git_check(self) -> None:
        seen: list[float] = []

        def run_git(argv: Sequence[str], cwd: Path, timeout: float) -> int:
            seen.append(timeout)
            return _NOT_A_REPO

        self.main("--timeout", "2.5", run_git=run_git)
        self.assertEqual(set(seen), {2.5})


class ErrorExitTest(_CliCase):
    def test_a_held_film_exits_1_and_the_rest_is_written(self) -> None:
        held = resolved_row(
            title="Placeholder Film Two",
            resolve_status="ambiguous",
            tmdb_id="",
            source_line="3",
        )
        self.write_rows([_FILM_ONE, _METROPOLIS, held])
        self.assertEqual(self.main(), EXIT_ERRORS)
        self.assertIn("review held-ambiguous: 1", self.stdout)
        self.assertIn("1 error item(s); see review.md", self.stderr)
        self.assertNotIn("Placeholder Film Two", self.read("letterboxd-001.csv"))
        self.assertIn("Placeholder Film Two", self.read("review.md"))
        self.assert_no_absolute_path()


class UsageExitTest(_CliCase):
    def assert_refused(self, code: int, *phrases: str) -> None:
        """Exit 2, a message holding each phrase, and no path in it."""
        self.assertEqual(code, EXIT_USAGE, self.stdout)
        for phrase in phrases:
            self.assertIn(phrase, self.stderr)
        self.assert_no_absolute_path()

    def test_a_missing_resolve_folder(self) -> None:
        self.resolved = self.base / "missing"
        self.assert_refused(self.main(), "--resolved: no such folder")
        self.assertFalse(self.out.exists())

    def test_a_damaged_resolve_csv(self) -> None:
        (self.resolved / "film-2010.csv").write_bytes(b"not,resolve\n")
        self.assert_refused(self.main(), "film-2010.csv: header")

    def test_an_unreadable_config(self) -> None:
        self.config = self.base / "missing.toml"
        self.assert_refused(self.main(), "could not read --config")

    def test_an_invalid_config(self) -> None:
        self.config.write_bytes(config_toml(omit=["tags"]))
        self.assert_refused(self.main(), "invalid --config", "letterboxd.tags")

    def test_a_config_with_no_letterboxd_table(self) -> None:
        self.config.write_bytes(b"schema = 1\n")
        self.assert_refused(self.main(), "--config", "[letterboxd]")

    def test_a_rating_key_the_config_lacks(self) -> None:
        self.write_rows([resolved_row(rating_raw="9")])
        self.assert_refused(self.main(), "letterboxd.ratings.9")

    def test_an_account_export_holding_no_account_file(self) -> None:
        (self.account / "watched.csv").unlink()
        self.assert_refused(self.main(), "--account: holds none")

    def test_a_damaged_account_file(self) -> None:
        (self.account / "watched.csv").write_bytes(b"\xff\n")
        self.assert_refused(self.main(), "watched.csv: not UTF-8")

    def test_a_cutoff_naming_no_row_writes_nothing(self) -> None:
        cutoff: dict[str, TomlValue] = {
            "file": "10/seen.txt",
            "line": 9,
            "title": "Metropolis",
        }
        self.config.write_bytes(config_toml(cutoff=cutoff))
        self.assert_refused(self.main(), "letterboxd.cutoff")
        self.assertFalse(self.out.exists())

    def test_a_film_too_large_for_one_file(self) -> None:
        self.write_rows([resolved_row(comment="x" * 2_000)])
        self.assert_refused(self.main("--max-bytes", "1024"), "film 1 of 1")
        self.assertFalse(self.out.exists())

    def test_an_out_inside_a_git_work_tree(self) -> None:
        self.assert_refused(self.main(run_git=fake_git(_IN_A_REPO)), "refused --out")
        self.assertFalse(self.out.exists())

    def test_an_out_inside_the_resolve_folder(self) -> None:
        self.assert_refused(self.main(out=self.resolved / "out"), "refused --out")

    def test_an_out_inside_the_account_folder(self) -> None:
        self.assert_refused(self.main(out=self.account / "out"), "refused --out")
        self.assertFalse((self.account / "out").exists())

    def test_an_out_beside_a_zip_account_is_accepted(self) -> None:
        archive = write_account_zip(
            self.base / "export.zip", {"watched.csv": account_csv(_ON_ACCOUNT)}
        )
        self.assertEqual(self.main(account=archive), EXIT_OK, self.stderr)

    def test_a_marker_that_is_a_link_is_refused(self) -> None:
        self.out.mkdir()
        (self.out / _MARKER).symlink_to(self.base / "elsewhere")
        self.assert_refused(self.main(), "refused --out")

    def test_an_out_that_cannot_be_created(self) -> None:
        # check_out_dir accepted it, but the folder changed before it was made.
        refused = survey_guard.OutRefused("cannot inspect the output location")
        with mock.patch.object(
            retrolog_export.survey_guard, "prepare_out_dir", side_effect=refused
        ):
            self.assert_refused(self.main(), "refused --out: cannot inspect")

    def test_a_write_failure(self) -> None:
        with mock.patch.object(
            retrolog_export, "write_atomic", side_effect=PermissionError(13, "denied")
        ):
            self.assert_refused(self.main(), "could not write the output")

    def test_bad_option_values_are_usage_errors(self) -> None:
        for extra in (
            ["--rows-per-file", "0"],
            ["--rows-per-file", "1901"],
            ["--rows-per-file", "x"],
            ["--max-bytes", "1023"],
            ["--max-bytes", "1000001"],
            ["--timeout", "0"],
            ["--service", "goodreads"],
        ):
            with self.subTest(extra=extra):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        self.main(*extra)
                self.assertEqual(raised.exception.code, EXIT_USAGE)

    def test_every_input_option_is_required(self) -> None:
        argv = ["--service", "letterboxd", "--resolved", "r", "--config", "c"]
        argv += ["--out", "o"]
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as raised:
                retrolog_export.main(argv, run_git=fake_git())
        self.assertEqual(raised.exception.code, EXIT_USAGE)
        self.assertIn("--account", stderr.getvalue())


class HelpTest(unittest.TestCase):
    def test_help_names_the_prerequisites_run_order_and_exit_codes(self) -> None:
        text = retrolog_export.build_parser().format_help()
        for phrase in ("Prerequisites", "Run order", "Exit codes", "1900", "1000000"):
            self.assertIn(phrase, text)


class SampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sample_output_is_reproduced(self) -> None:
        work = self.base / "work"
        self.assertEqual(build_export_inputs.main(["--out", str(work)]), EXIT_OK)
        argv = ["--service", "letterboxd", "--resolved", str(work / "resolved")]
        argv += ["--account", str(work / "account")]
        argv += ["--config", str(work / "export.toml"), "--out", str(work / "out")]
        argv += ["--rows-per-file", "4"]
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code = retrolog_export.main(argv, run_git=fake_git())
        self.assertEqual(code, EXIT_ERRORS)
        expected = _TOOL / "samples" / "expected"
        out = work / "out"
        written = sorted(p.name for p in out.iterdir() if p.name != _MARKER)
        self.assertEqual(written, sorted(p.name for p in expected.iterdir()))
        for name in written:
            self.assertEqual(
                (out / name).read_bytes(), (expected / name).read_bytes(), name
            )

    def test_the_sample_shows_a_split_and_every_review_kind(self) -> None:
        expected = _TOOL / "samples" / "expected"
        self.assertGreaterEqual(len(list(expected.glob("letterboxd-*.csv"))), 2)
        review = (expected / "review.md").read_text("utf-8")
        for kind in retrolog_export.REVIEW_KINDS:
            self.assertIn(f"## {kind} (", review)

    def test_sample_builder_refuses_a_non_empty_out(self) -> None:
        (self.base / "notes.txt").write_bytes(b"")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = build_export_inputs.main(["--out", str(self.base)])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("must be missing or empty", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
