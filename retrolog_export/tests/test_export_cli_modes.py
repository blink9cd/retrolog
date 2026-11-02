"""Tests for retrolog_export's two other runs, the dry run and the one-film test run.

``--dry-run`` checks everything a normal run checks and prints the same summary, but
writes nothing, not even ``--out``. ``--test-film`` writes ``letterboxd-test.csv``
holding every row of the ``logged-no-stars`` film listed first in the review, and no
other file. The omit list's command-line behavior is here too: a stale entry stops
the run before anything is written.
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
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL / "samples"))
sys.path.insert(0, str(_TOOL))
import build_export_inputs  # noqa: E402 - after the sys.path inserts above
import retrolog_export  # noqa: E402 - after the sys.path inserts above
from export_cli_case import (  # noqa: E402 - after the sys.path inserts above
    EXIT_ERRORS,
    EXIT_OK,
    EXIT_USAGE,
    FILM_ONE,
    HEADER,
    IN_A_REPO,
    MARKER,
    METROPOLIS,
    CliCase,
    fake_git,
    snapshot,
)
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    TomlValue,
    account_csv,
    config_toml,
    csv_text,
    logged_csv,
    resolved_row,
    write_account_folder,
    write_resolved,
)

_TEST_FILE = "letterboxd-test.csv"
# Metropolis's second watch: the film whose rows the test file must hold together.
_METROPOLIS_AGAIN = resolved_row(
    title="Metropolis",
    year="1927",
    tmdb_id="1000002",
    date="2010-05-06",
    rewatch="true",
    source_line="3",
)
_HELD = resolved_row(
    title="Placeholder Film Two",
    tmdb_id="",
    resolve_status="ambiguous",
    source_line="4",
)


class DryRunTest(CliCase):
    def test_a_dry_run_prints_the_summary_and_creates_nothing(self) -> None:
        self.assertEqual(self.main("--dry-run"), EXIT_OK, self.stderr)
        dry = self.stdout
        self.assertFalse(self.out.exists())
        self.assertEqual(self.stderr, "")
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(dry, self.stdout)

    def test_a_dry_run_leaves_an_earlier_output_as_it_was(self) -> None:
        self.assertEqual(self.main("--rows-per-file", "1"), EXIT_OK, self.stderr)
        before = snapshot(self.out)
        self.assertEqual(self.main("--dry-run"), EXIT_OK, self.stderr)
        self.assertEqual(snapshot(self.out), before)
        self.assertIn("files: 1", self.stdout)

    def test_a_held_film_exits_1_and_points_past_the_dry_run(self) -> None:
        self.write_rows([FILM_ONE, METROPOLIS, _HELD])
        self.assertEqual(self.main("--dry-run"), EXIT_ERRORS)
        self.assertIn("review held-ambiguous: 1", self.stdout)
        self.assertIn("1 error item(s)", self.stderr)
        self.assertIn("without --dry-run", self.stderr)
        self.assertNotIn("see review.md", self.stderr)
        self.assertFalse(self.out.exists())

    def test_a_dry_run_still_refuses_a_bad_out_and_a_bad_config(self) -> None:
        code = self.main("--dry-run", run_git=fake_git(IN_A_REPO))
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("refused --out", self.stderr)
        self.config.write_bytes(config_toml(ratings={"1": 1.0}))
        self.assertEqual(self.main("--dry-run"), EXIT_USAGE)
        self.assertIn("letterboxd.ratings.2: missing", self.stderr)
        self.assertFalse(self.out.exists())

    def test_a_dry_run_still_refuses_a_film_too_large_for_one_file(self) -> None:
        long = resolved_row(comment="x" * 2_000, tmdb_id="1000009", source_line="5")
        self.write_rows([FILM_ONE, long])
        self.assertEqual(self.main("--dry-run", "--max-bytes", "1024"), EXIT_USAGE)
        self.assertIn("--max-bytes", self.stderr)

    def test_dry_run_and_test_film_cannot_be_combined(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.main("--dry-run", "--test-film")
        self.assertEqual(caught.exception.code, EXIT_USAGE)


class TestFilmTest(CliCase):
    """A logged account: both films sent without stars, Metropolis first by title."""

    def setUp(self) -> None:
        super().setUp()
        self.write_rows([FILM_ONE, METROPOLIS, _METROPOLIS_AGAIN])
        diary = logged_csv(
            [
                ("Placeholder Film One", "1999", "", "2015-01-01"),
                ("Metropolis", "1927", "", "2015-01-01"),
            ]
        )
        write_account_folder(self.account, {"diary.csv": diary})

    def test_the_test_file_holds_every_row_of_the_first_logged_film(self) -> None:
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(
            self.read(_TEST_FILE),
            HEADER
            + "1000002,Metropolis,1927,Placeholder Director,,2010-03-04,,retrolog,\n"
            + "1000002,Metropolis,1927,Placeholder Director,,2010-05-06,true,"
            "retrolog,\n",
        )
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()), [MARKER, _TEST_FILE]
        )
        self.assertEqual(self.stdout, f"test film rows: 2\nfile: {_TEST_FILE}\n")
        self.assertEqual(self.stderr, "")
        self.assert_no_absolute_path()

    def test_the_choice_follows_the_review_not_the_upload_order(self) -> None:
        # Placeholder Film One is sent first, but Metropolis leads the review's table.
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        entries = json.loads(self.read("review.json"))["entries"]
        logged = [e["title"] for e in entries if e["kind"] == "logged-no-stars"]
        self.assertEqual(logged[0], "Metropolis")
        self.assertTrue(self.read("letterboxd-001.csv").startswith(HEADER + "1000001"))

    def _with_undated_metropolis(self, *, both: bool = False, **cells: str) -> None:
        """Film One and Metropolis, Metropolis's second watch (or ``both``) undated.

        Each undated watch also takes ``cells``.
        """
        # A dated list's rows must carry dates, so the undated watches share a list.
        undated = {"date": "", **cells}
        dated = [FILM_ONE] if both else [FILM_ONE, METROPOLIS]
        watches = [METROPOLIS, _METROPOLIS_AGAIN] if both else [_METROPOLIS_AGAIN]
        write_resolved(
            self.resolved,
            {
                "film-2010.csv": csv_text(dated),
                "film-undated.csv": csv_text([{**w, **undated} for w in watches]),
            },
        )

    def test_a_film_the_import_would_not_tag_at_all_is_passed_over(self) -> None:
        # Neither of Metropolis's undated, uncommented watches becomes a diary entry
        # or a review, so the film keeps no tag, and the next run would read it as
        # logged and send it again starless.
        self._with_undated_metropolis(both=True)
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(self.stdout, f"test film rows: 1\nfile: {_TEST_FILE}\n")
        self.assertTrue(self.read(_TEST_FILE).startswith(HEADER + "1000001,"))

    def test_one_tagged_row_is_enough_to_choose_a_film(self) -> None:
        # The dated watch carries the tag, and the next run skips a tagged film whole,
        # so the undated, uncommented watch is not sent again either.
        self._with_undated_metropolis()
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(self.stdout, f"test film rows: 2\nfile: {_TEST_FILE}\n")
        self.assertTrue(self.read(_TEST_FILE).startswith(HEADER + "1000002,"))

    def test_review_text_lets_an_undated_row_carry_the_tag(self) -> None:
        self._with_undated_metropolis(both=True, comment="noted")
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(self.stdout, f"test film rows: 2\nfile: {_TEST_FILE}\n")

    def test_with_no_film_tagged_the_review_order_still_decides(self) -> None:
        self.config.write_bytes(config_toml(watched_date=False))
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(self.stdout, f"test film rows: 2\nfile: {_TEST_FILE}\n")

    def test_a_test_run_removes_an_earlier_runs_files_and_a_run_removes_it(
        self,
    ) -> None:
        self.assertEqual(self.main("--rows-per-file", "2"), EXIT_OK, self.stderr)
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()), [MARKER, _TEST_FILE]
        )
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertNotIn(_TEST_FILE, {p.name for p in self.out.iterdir()})

    def test_a_held_film_does_not_stop_the_test_run(self) -> None:
        self.write_rows([FILM_ONE, METROPOLIS, _HELD])
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(self.stdout, f"test film rows: 1\nfile: {_TEST_FILE}\n")

    def test_no_logged_film_exits_2_and_writes_nothing(self) -> None:
        write_account_folder(
            self.account, {"diary.csv": logged_csv([]), "watched.csv": account_csv([])}
        )
        self.assertEqual(self.main("--test-film"), EXIT_USAGE)
        self.assertIn("no logged-no-stars film to test", self.stderr)
        self.assertFalse(self.out.exists())

    def test_a_refused_test_run_leaves_an_earlier_output_as_it_was(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        before = snapshot(self.out)
        write_account_folder(self.account, {"diary.csv": logged_csv([])})
        self.assertEqual(self.main("--test-film"), EXIT_USAGE)
        self.assertEqual(snapshot(self.out), before)

    def test_a_formula_cell_in_the_test_file_is_counted(self) -> None:
        formula = resolved_row(
            title="Metropolis", year="1927", tmdb_id="1000002", comment="@noted"
        )
        self.write_rows([formula])
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        self.assertEqual(
            self.stdout,
            f"test film rows: 1\nfile: {_TEST_FILE}\nreview formula-cell: 1\n",
        )

    def test_a_failed_test_file_write_exits_2_naming_no_path(self) -> None:
        denied = PermissionError(13, "denied", str(self.out / _TEST_FILE))
        with mock.patch.object(retrolog_export, "write_atomic", side_effect=denied):
            self.assertEqual(self.main("--test-film"), EXIT_USAGE)
        self.assertIn("could not write the output", self.stderr)
        self.assert_no_absolute_path()

    def test_a_test_film_too_large_for_one_file_exits_2(self) -> None:
        long = resolved_row(
            title="Metropolis", year="1927", tmdb_id="1000002", comment="x" * 2_000
        )
        self.write_rows([long])
        self.assertEqual(self.main("--test-film", "--max-bytes", "1024"), EXIT_USAGE)
        self.assertIn("--max-bytes", self.stderr)
        self.assertFalse(self.out.exists())


class SampleTestFilmTest(CliCase):
    def test_the_samples_test_file_matches_the_committed_one(self) -> None:
        work = self.base / "work"
        self.assertEqual(build_export_inputs.main(["--out", str(work)]), EXIT_OK)
        self.resolved, self.account = work / "resolved", work / "account"
        self.config = work / "export.toml"
        self.assertEqual(self.main("--test-film"), EXIT_OK, self.stderr)
        expected = _TOOL / "samples" / "expected-test" / _TEST_FILE
        self.assertEqual(self.read(_TEST_FILE), expected.read_text("utf-8"))


class OmitRunTest(CliCase):
    def test_an_omitted_film_is_listed_and_not_written(self) -> None:
        self.config.write_bytes(
            config_toml(omit_films=[{"title": "Metropolis", "year": 1927}])
        )
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertNotIn("Metropolis", self.read("letterboxd-001.csv"))
        self.assertIn("review omitted: 1", self.stdout)
        self.assertIn("## omitted (1)", self.read("review.md"))

    def test_an_entry_naming_only_a_cut_row_is_not_stale(self) -> None:
        # Metropolis is the cutoff's own row, so it is cut, yet the entry naming it
        # still names a row read: moving a cutoff never makes an entry stale.
        cutoff: dict[str, TomlValue] = {
            "file": "10/seen.txt",
            "line": 2,
            "title": "Metropolis",
        }
        omit: dict[str, TomlValue] = {"title": "Metropolis", "year": 1927}
        self.config.write_bytes(config_toml(cutoff=cutoff, omit_films=[omit]))
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertIn("rows cut: 1", self.stdout)
        self.assertNotIn("review omitted", self.stdout)

    def test_a_stale_entry_exits_2_and_writes_nothing(self) -> None:
        self.config.write_bytes(
            config_toml(omit_films=[{"title": "Metropolis", "year": 1928}])
        )
        self.assertEqual(self.main(), EXIT_USAGE)
        self.assertIn(
            "invalid --config: letterboxd.omit[1]: matches no row", self.stderr
        )
        self.assertNotIn("1928", self.stderr)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
