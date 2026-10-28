"""The shared case of the retrolog_export command-line tests: inputs, a run, checks.

The importing test puts the tool's folder, its src/ folders, resolve's, parse's and
the survey's src/ folders, this folder and the samples folder on ``sys.path`` before
importing this module. ``CliCase`` writes a temporary resolve folder, account export
and export config, and runs ``main`` with a git stand-in, so no test reaches a real
repository, a real account or the operator's own config.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

import retrolog_export
import survey_guard
from export_fixtures import (
    account_csv,
    config_toml,
    csv_text,
    resolved_row,
    write_account_folder,
    write_resolved,
)

EXIT_OK, EXIT_ERRORS, EXIT_USAGE = 0, 1, 2
# git's exit code outside any repository, and inside one.
NOT_A_REPO, IN_A_REPO = 128, 0
MARKER = ".retrolog_export"
HEADER = "tmdbID,Title,Year,Directors,Rating,WatchedDate,Rewatch,Tags,Review\n"
FILM_ONE = resolved_row()
METROPOLIS = resolved_row(
    title="Metropolis", year="1927", tmdb_id="1000002", source_line="2"
)
ON_ACCOUNT = [("Placeholder Film Nine", "2001")]


def fake_git(code: int = NOT_A_REPO) -> survey_guard.GitRunner:
    """A git stand-in answering every question with ``code``."""

    def run(argv: Sequence[str], cwd: Path, timeout: float) -> int:
        return code

    return run


def snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` by relative path, as a hash of its bytes."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class CliCase(unittest.TestCase):
    """Two resolved films, an account holding neither, a config, and an --out."""

    def setUp(self) -> None:
        """Write the case's inputs into a fresh temporary folder."""
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.resolved = self.base / "resolved"
        self.write_rows([FILM_ONE, METROPOLIS])
        self.account = self.base / "account"
        self.write_account(ON_ACCOUNT)
        self.config = self.base / "export.toml"
        self.config.write_bytes(config_toml())
        self.out = self.base / "out"

    def tearDown(self) -> None:
        """Remove the temporary folder and everything the run wrote there."""
        self._tmp.cleanup()

    def write_rows(self, rows: Sequence[Mapping[str, str]]) -> None:
        """Make ``rows`` the resolve folder's one film CSV."""
        write_resolved(self.resolved, {"film-2010.csv": csv_text(rows)})

    def write_account(self, films: Sequence[tuple[str, str]]) -> None:
        """Make ``films`` the account export's watched.csv."""
        write_account_folder(self.account, {"watched.csv": account_csv(films)})

    def main(
        self,
        *extra: str,
        out: Path | None = None,
        account: Path | None = None,
        run_git: survey_guard.GitRunner | None = None,
    ) -> int:
        """Run the CLI on the case's inputs, plus ``extra``."""
        argv = ["--service", "letterboxd", "--resolved", str(self.resolved)]
        argv += ["--account", str(account or self.account)]
        argv += ["--config", str(self.config), "--out", str(out or self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = retrolog_export.main(argv, run_git=run_git or fake_git())
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def read(self, name: str) -> str:
        """One written output file's text."""
        return (self.out / name).read_text(encoding="utf-8")

    def import_names(self) -> list[str]:
        """The import files ``--out`` holds, sorted."""
        return sorted(p.name for p in self.out.glob("letterboxd-*.csv"))

    def assert_no_absolute_path(self) -> None:
        """No output file, nor stdout or stderr, names the temporary folder."""
        texts = [self.stdout, self.stderr]
        if self.out.is_dir():
            texts += [p.read_text("utf-8") for p in self.out.iterdir() if p.is_file()]
        for text in texts:
            self.assertNotIn(str(self.base), text)
            self.assertNotIn(self._tmp.name, text)
