"""The shared setup of the retrolog_parse command-line tests: a tree, rules and fakes.

`ParseCliCase` writes a temporary source tree that the fixture rules read without an
error, the rules file beside it and an ``--out`` beside both, then runs
``retrolog_parse.main`` with a fake git and a fake converter unless a test passes real
ones.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent
_TOOL = _FIXTURES.parent.parent
sys.path.insert(0, str(_TOOL))
import parse_fixtures  # noqa: E402 - beside this file
import retrolog_parse  # noqa: E402 - after the sys.path insert above
import survey_convert  # noqa: E402 - retrolog_parse puts its folder on sys.path
import survey_guard  # noqa: E402 - retrolog_parse puts its folder on sys.path

# git's exit code outside any repository.
NOT_A_REPO = 128
EXIT_OK, EXIT_ERRORS, EXIT_USAGE = 0, 1, 2
# The fixture override's line in 33/read.txt, which the clean tree matches.
OVERRIDE_LINE = "odd book - q"
SEEN_TEXT = "list of films\nfirst film - k\nsecond film - o\n"
READ_TEXT = f"a book - x\nb book - o\nc book - k\n{OVERRIDE_LINE}\n"
# The fixture's term block, which names a file this tree does not hold: the rules
# file leaves it out, so every rule it keeps names a file the tree holds.
TERM_BLOCK = (
    '[[terms]]\nfile = "31-32/seen.txt"\nlines = [1, 2]\ndate = 2031-12-31\n'
    'expect = "first film - k"\n'
)
# One Word line rated by text color: "778899" is the fixture's key "3".
DOCX_RUNS = [[("docx film", None, "778899")]]


def fake_git(code: int = NOT_A_REPO) -> survey_guard.GitRunner:
    """A git stand-in that answers every question with `code`."""

    def run(argv: Sequence[str], cwd: Path, timeout: float) -> int:
        return code

    return run


class FakeRunner:
    """Stands in for textutil: each call copies a prepared .docx to its output."""

    def __init__(self, produce: Path) -> None:
        """Copy ``produce`` as every conversion's output; count the calls."""
        self.produce = produce
        self.calls = 0

    def __call__(self, argv: Sequence[str], timeout: float) -> int:
        """Write the output where textutil's argv says it goes; exit 0."""
        self.calls += 1
        shutil.copyfile(self.produce, argv[argv.index("-output") + 1])
        return 0


def snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """Every file and directory under `root`: size, mtime and hash, or a dir marker."""
    found: dict[str, tuple[int, int, str]] = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs:
            path = Path(dirpath) / name
            found[path.relative_to(root).as_posix()] = (0, 0, "directory")
        for name in files:
            path = Path(dirpath) / name
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            found[path.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                digest,
            )
    return found


class ParseCliCase(unittest.TestCase):
    """A clean source tree, the fixture rules and an --out, all in one temp folder."""

    def setUp(self) -> None:
        """Make the temporary folder, the tree, the rules file and the fake converter."""
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.tree = self.base / "tree"
        self.out = self.base / "out"
        self.rules = self.base / "rules.toml"
        self.rules.write_bytes(parse_fixtures.rules_bytes({TERM_BLOCK: ""}))
        self.produce = self.base / "converted.docx"
        self.produce.write_bytes(parse_fixtures.docx_bytes(DOCX_RUNS))
        self.runner = FakeRunner(self.produce)
        self.write("33/seen.txt", SEEN_TEXT)
        self.write("33/read.txt", READ_TEXT)
        self.write_bytes("33/more seen.docx", parse_fixtures.docx_bytes(DOCX_RUNS))

    def tearDown(self) -> None:
        """Remove the temporary folder and everything in it."""
        self._tmp.cleanup()

    def write(self, rel_path: str, text: str) -> None:
        """Write a UTF-8 text file into the tree."""
        self.write_bytes(rel_path, text.encode("utf-8"))

    def write_bytes(self, rel_path: str, data: bytes) -> None:
        """Write a file into the tree, making its folder."""
        path = self.tree / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def main(
        self,
        *extra: str,
        out: Path | None = None,
        run: survey_convert.Runner | None = None,
        run_git: survey_guard.GitRunner | None = None,
        soffice: Path | None = None,
    ) -> int:
        """Run the CLI on the tree, keeping its output; fakes fill any seam not given."""
        argv = ["--path", str(self.tree), "--rules", str(self.rules)]
        argv += ["--out", str(out or self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = retrolog_parse.main(
                argv,
                run=run or self.runner,
                run_git=run_git or fake_git(),
                soffice=soffice,
            )
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def review(self) -> dict[str, object]:
        """The written review.json."""
        data = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data

    def kinds(self) -> list[tuple[str, str, int]]:
        """Every written review item as (kind, file, line)."""
        items = self.review()["items"]
        assert isinstance(items, list)
        return [(i["kind"], i["rel_path"], i["line"]) for i in items]
