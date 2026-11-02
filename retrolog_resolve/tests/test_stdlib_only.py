"""retrolog_resolve and its sample builder run on a stock Python with no installed package.

Each test runs a script in a child interpreter started with ``-I -S``: ``-S`` leaves
every site-packages folder (the test runner's own included) off the path and ``-I``
ignores PYTHONPATH, so an import of python-docx (through the survey's modules) or of
hypothesis (through the test fixtures) fails the run, as it would for an operator.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent
# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_run")
import bounded_run  # noqa: E402 - after the bootstrap above

_EXIT_OK, _EXIT_ERRORS = 0, 1
# The variables the tool reads, kept out of the child so a developer's own token
# can never reach it.
_TOOL_ENV = ("RETROLOG_TMDB_TOKEN", "RETROLOG_CONTACT")
# Seconds one bare-interpreter run may take: it builds or resolves the small sample.
_BARE_RUN_TIMEOUT = 120


def run_bare(script: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """``script`` run with ``argv`` under this interpreter, with no site-packages.

    Through ``run_bounded``, which kills the whole process tree on a timeout before it
    reads the captured pipes, so the bound holds even if a descendant kept them open.
    """
    env = {name: value for name, value in os.environ.items() if name not in _TOOL_ENV}
    return bounded_run.run_bounded(
        [sys.executable, "-I", "-S", str(script), *argv],
        timeout=_BARE_RUN_TIMEOUT,
        env=env,
    )


class StandardLibraryOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_help_needs_no_installed_package(self) -> None:
        done = run_bare(_TOOL / "retrolog_resolve.py", "--help")
        self.assertEqual(done.returncode, _EXIT_OK, done.stderr)

    def test_the_sample_builds_and_runs_with_no_installed_package(self) -> None:
        work = self.base / "work"
        builder = _TOOL / "samples" / "build_resolve_inputs.py"
        built = run_bare(builder, "--out", str(work))
        self.assertEqual(built.returncode, _EXIT_OK, built.stderr)
        argv = ["--parsed", str(work / "parsed"), "--out", str(work / "out")]
        argv += ["--overrides", str(work / "overrides.toml"), "--cache-only"]
        done = run_bare(_TOOL / "retrolog_resolve.py", *argv)
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual(done.returncode, _EXIT_ERRORS, done.stderr)
        expected = _TOOL / "samples" / "expected"
        for path in expected.iterdir():
            written = work / "out" / path.name
            self.assertEqual(written.read_bytes(), path.read_bytes(), path.name)


if __name__ == "__main__":
    unittest.main()
