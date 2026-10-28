"""The shared setup of the command-line tests: a source tree, fakes and a runner.

`CliCase` builds a temporary source tree holding every kind of file the survey
reads or counts, with an ``--out`` beside it, and runs ``retrolog_survey.main`` on it
with fake converters and a fake git unless a test passes real ones.
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
from collections.abc import Callable, Sequence
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent
_TOOL = _FIXTURES.parent.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL))
import build_fixtures  # noqa: E402 - after the sys.path inserts above
import retrolog_survey  # noqa: E402 - after the sys.path inserts above
import survey_convert  # noqa: E402 - after the sys.path inserts above
import survey_guard  # noqa: E402 - after the sys.path inserts above
from report_shapes import as_dict, as_list  # noqa: E402 - after the sys.path inserts above

# git's exit code outside any repository.
NOT_A_REPO = 128
# Seconds a live converter or git call may take.
TIMEOUT = 60.0
PNG = build_fixtures.make_tiny_png()
PNG_SHA = hashlib.sha256(PNG).hexdigest()[:12]
EXIT_OK, EXIT_FILE_ERRORS, EXIT_USAGE = 0, 1, 2


def fake_git(code: int = NOT_A_REPO) -> survey_guard.GitRunner:
    """A git stand-in that answers every question with `code`."""

    def run(argv: Sequence[str], cwd: Path, timeout: float) -> int:
        return code

    return run


class FakeRunner:
    """Stands in for both converters: each copies a prepared .docx to its output."""

    def __init__(self, produce: Path) -> None:
        """Copy ``produce`` as every conversion's output; count the calls."""
        self.produce = produce
        self.calls = 0

    def __call__(self, argv: Sequence[str], timeout: float) -> int:
        """Write the output where the converter's argv says it goes; exit 0."""
        self.calls += 1
        if argv[0] == "textutil":
            target = Path(argv[argv.index("-output") + 1])
        else:
            target = Path(argv[argv.index("--outdir") + 1]) / (
                Path(argv[-1]).stem + ".docx"
            )
        shutil.copyfile(self.produce, target)
        return 0


def snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """Every file and directory under `root`, hidden ones included.

    A file maps to its size, mtime and content hash; a directory to a fixed marker, so
    a directory created, removed or renamed under `root` changes the snapshot too.
    """
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


class CliCase(unittest.TestCase):
    """A temporary source tree holding every kind of file, and an --out beside it."""

    def setUp(self) -> None:
        """Make the temporary folder, the tree and the fake converter."""
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.tree = self.base / "tree"
        self.out = self.base / "out"
        self.produce = self.base / "converted.docx"
        build_fixtures.make_rated_docx(self.produce)
        self.build_tree()
        self.runner = FakeRunner(self.produce)

    def tearDown(self) -> None:
        """Remove the temporary folder and everything in it."""
        self._tmp.cleanup()

    def build_tree(self) -> None:
        """Write the source tree: two text files, a .docx, a .doc and hidden files."""
        films, books = self.tree / "films", self.tree / "books"
        films.mkdir(parents=True)
        books.mkdir()
        build_fixtures.write_text_fixture(
            films / "list.txt",
            "Film A \N{EN DASH} g\nFilm B\n",
            encoding="mac_roman",
            newline="\r",
        )
        build_fixtures.write_text_fixture(
            books / "list.txt",
            "Book A \N{LEFT SINGLE QUOTATION MARK}b\N{RIGHT SINGLE QUOTATION MARK}\n",
            encoding="cp1252",
            newline="\r\n",
        )
        build_fixtures.make_rated_docx(films / "rated.docx")
        (films / "old.doc").write_bytes(b"a stand-in .doc the fake converter replaces")
        (self.tree / ".DS_Store").write_bytes(b"\x00\x01")
        (films / ".DS_Store").write_bytes(b"\x00\x02")

    def add_broken_docx(self) -> None:
        """Add a .docx that is not a zip archive."""
        (self.tree / "films" / "broken.docx").write_bytes(b"not a zip archive")

    def main(
        self,
        *extra: str,
        out: Path | None = None,
        run: survey_convert.Runner | None = None,
        run_git: survey_guard.GitRunner | None = None,
        soffice: Path | None | retrolog_survey._Unset = None,
        which: Callable[[str], str | None] = lambda name: "/usr/bin/" + name,
    ) -> int:
        """Run the CLI on the tree, keeping its output; fakes fill any seam not given."""
        argv = ["--path", str(self.tree), "--out", str(out or self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = retrolog_survey.main(
                argv,
                run=run or self.runner,
                run_git=run_git or fake_git(),
                soffice=soffice,
                which=which,
            )
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def report(self) -> dict[str, object]:
        """The written report.json."""
        return as_dict(
            json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        )

    def groups(self) -> dict[str, dict[str, object]]:
        """The written report's groups, by name."""
        groups = [as_dict(group) for group in as_list(self.report()["groups"])]
        return {str(group["group"]): group for group in groups}
