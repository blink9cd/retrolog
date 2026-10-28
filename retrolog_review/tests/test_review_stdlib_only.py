"""retrolog_review runs on a stock Python with no installed package, end to end.

Each test runs the script in a child interpreter started with ``-I -S``: ``-S`` leaves
every site-packages folder off the path and ``-I`` ignores PYTHONPATH, so an import of
an installed package (hypothesis, through the test fixtures, say) fails the run, as it
would for an operator. This is the live process-boundary test the unit tests' direct
``main`` calls cannot be. The page mode is run only for its refusal, because the real
page/ folder belongs to the front end and these tests must not depend on it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TESTS / "fixtures"))
# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_run")
import bounded_run  # noqa: E402 - after the bootstrap above
from review_fixtures import (  # noqa: E402 - after the sys.path inserts above
    FILM_TITLE,
    entry,
    pick,
    picks,
    review,
)

_SCRIPT = _TOOL / "retrolog_review.py"
_EXIT_OK, _EXIT_REFUSED, _EXIT_USAGE = 0, 1, 2
# Seconds one bare-interpreter run may take: it reads two small files.
_BARE_RUN_TIMEOUT = 60


def run_bare(*argv: str) -> subprocess.CompletedProcess[str]:
    """The script run with ``argv`` under this interpreter, with no site-packages."""
    return bounded_run.run_bounded(
        [sys.executable, "-I", "-S", str(_SCRIPT), *argv], timeout=_BARE_RUN_TIMEOUT
    )


class StandardLibraryOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_help_needs_no_installed_package(self) -> None:
        done = run_bare("--help")
        self.assertEqual(done.returncode, _EXIT_OK, done.stderr)
        self.assertIn("Prerequisites:", done.stdout)

    def test_overrides_mode_runs_end_to_end(self) -> None:
        # Written compact rather than by as_bytes: any bytes are a review.
        data = json.dumps(review([entry()])).encode("utf-8")
        (self.base / "review.json").write_bytes(data)
        document = picks(data, [pick({"tmdb_id": "1000003"})])
        (self.base / "picks.json").write_text(
            json.dumps(document), encoding="utf-8", newline="\n"
        )
        out = self.base / "overrides.toml"
        done = run_bare(
            "--mode", "overrides",
            "--review", str(self.base / "review.json"),
            "--picks", str(self.base / "picks.json"),
            "--out", str(out),
        )  # fmt: skip
        self.assertEqual(done.returncode, _EXIT_OK, done.stderr)
        self.assertEqual(
            done.stdout, "1 override written for 1 pick, 0 kept, 0 undecided.\n"
        )
        written = tomllib.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["overrides"][0]["title"], FILM_TITLE)

    @unittest.skipIf(shutil.which("git") is None, "the live guard test needs git")
    def test_out_inside_a_real_work_tree_is_refused(self) -> None:
        """The guard asks the real git, across the process boundary."""
        tree = self.base / "tree"
        tree.mkdir()
        made = bounded_run.run_bounded(
            ["git", "init", "--quiet", str(tree)], timeout=_BARE_RUN_TIMEOUT
        )
        self.assertEqual(made.returncode, _EXIT_OK, made.stderr)
        # The folder --out names does not exist yet: git is asked from the nearest
        # one that does, as resolve's guard asks it.
        (self.base / "review.json").write_bytes(b"{}")
        done = run_bare(
            "--mode", "page",
            "--review", str(self.base / "review.json"),
            "--out", str(tree / "deeper" / "page.html"),
        )  # fmt: skip
        self.assertEqual(done.returncode, _EXIT_USAGE, done.stderr)
        self.assertIn("refused --out: inside a git work tree", done.stderr)
        self.assertNotIn(str(tree), done.stderr)

    def test_page_mode_refusal_crosses_the_boundary(self) -> None:
        (self.base / "review.json").write_bytes(b"[]")
        done = run_bare(
            "--mode", "page",
            "--review", str(self.base / "review.json"),
            "--out", str(self.base / "page.html"),
        )  # fmt: skip
        self.assertEqual(done.returncode, _EXIT_REFUSED, done.stderr)
        self.assertIn("invalid --review: review file: must be an object", done.stderr)


if __name__ == "__main__":
    unittest.main()
