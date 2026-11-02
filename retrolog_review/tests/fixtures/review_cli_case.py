"""The command-line tests' shared case: a temporary folder, a review, and a stand-in git.

``CliCase`` runs ``retrolog_review.main`` on files in a temporary folder, with the
page folder replaced by the stand-ins in this folder (``PAGE_DIR`` patched), so the
command-line suites do not change when the front end does. The importing suite puts
the tool's folder and this one on ``sys.path`` first.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

import retrolog_review
from review_fixtures import (
    BOOK_TITLE,
    FILM_TITLE,
    PAGE_FILES,
    as_bytes,
    book_entry,
    entry,
    review,
)

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2
# git's exit code outside any repository, and inside a work tree.
NOT_A_REPO, IN_A_REPO = 128, 0
IN_GIT = "refused --out: inside a git work tree"
# Every refusal opens with the program name, which argparse takes from argv[0].
PROG = retrolog_review.build_parser().prog
REVIEW_BYTES = as_bytes(
    review([entry("failed", title="Placeholder Film Nine"), entry(), book_entry()])
)


class FakeGit:
    """Stands in for git: records each call's argv and folder, returns a fixed code."""

    def __init__(self, code: int = NOT_A_REPO) -> None:
        """A stand-in answering every call with ``code``."""
        self.code = code
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv: Sequence[str], cwd: Path, timeout: float) -> int:
        """Record the call; return the fixed code."""
        self.calls.append((list(argv), cwd))
        return self.code


class CliCase(unittest.TestCase):
    """A temporary folder holding a review, and a stand-in page folder patched in."""

    def setUp(self) -> None:
        """Make the folder, its review and page files, and the stand-in git."""
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.page_dir = self.base / "page"
        self.page_dir.mkdir()
        for name, source in PAGE_FILES.items():
            shutil.copyfile(source, self.page_dir / name)
        patcher = mock.patch.object(retrolog_review, "PAGE_DIR", self.page_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.review = self.base / "review.json"
        self.review.write_bytes(REVIEW_BYTES)
        # Real git is never asked: the temporary folder is outside every work tree,
        # and a test that wants one inside says so through this stand-in.
        self.git = FakeGit()

    def tearDown(self) -> None:
        """Remove the temporary folder."""
        self._tmp.cleanup()

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        """``main(argv)``'s exit code, stdout and stderr."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = retrolog_review.main(list(argv), run_git=self.git)
            except SystemExit as exited:
                code = int(exited.code or 0)
        return code, out.getvalue(), err.getvalue()

    def assert_private(self, *texts: str) -> None:
        """No fixture title appears in any of ``texts``."""
        for text in texts:
            for title in (FILM_TITLE, BOOK_TITLE, "Placeholder Film Nine"):
                self.assertNotIn(title, text)
