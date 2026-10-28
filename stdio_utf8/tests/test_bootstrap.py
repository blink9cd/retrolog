"""Grouped suite: the import ``stdio_utf8.md`` documents resolves this library's own copy.

The companion doc's "How this project's tools import it" section shows the lines a tool
runs to reach this library. These take that code block from the doc itself, run it in a
real child from a tool folder beside a copy of this library, and check what it imported:
this folder's ``stdio_utf8.py``, appended after the standard library. A doc whose
snippet stopped working, or put the folder first, fails here. That no tool reaches above
the repository's root is ``retrolog_bootstrap``'s own suite to prove.

Run through ``../test_stdio_utf8.py``'s ``load_tests`` hook, which is how this file runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

#: A generous ceiling for the child: a hung import must fail this suite rather than
#: hang it.
_SUBPROCESS_TIMEOUT = 120

#: This library's folder, and the repository root above it.
_LIBRARY_DIR = Path(__file__).resolve().parents[1]
_ROOT = _LIBRARY_DIR.parent

#: The heading whose first ``python`` block is the documented import.
_SECTION = "## How this project's tools import it"
_FENCE = "```"

#: The folders the documented snippet reaches: the bootstrap, and each library it names.
_FOLDERS = ("retrolog_bootstrap", "stdio_utf8", "help_formatter")


def documented_import(doc: str) -> str:
    """The first ``python`` code block after :data:`_SECTION` in *doc*, as source."""
    after = doc.split(_SECTION, 1)[1]
    block = after.split(f"{_FENCE}python\n", 1)[1]
    return block.split(_FENCE, 1)[0]


def _write_lf(path: Path, text: str) -> None:
    """Write *text* to *path* as UTF-8 with LF line endings, on any platform."""
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


class TestDocumentedImport(unittest.TestCase):
    def _run_documented_import(self, root: Path) -> list[str]:
        """Run the documented snippet from ``<root>/probe/probe.py``; its output lines.

        The child prints the file it imported, its own standard-library folder (as its
        ``sys.path`` spells it, unresolved), then its ``sys.path``.
        """
        snippet = documented_import(
            (_LIBRARY_DIR / "stdio_utf8.md").read_text(encoding="utf-8")
        )
        probe = root / "probe" / "probe.py"
        probe.parent.mkdir()
        _write_lf(
            probe,
            "import sys\nfrom pathlib import Path\n"
            + snippet
            + "import os\n"
            + "print(stdio_utf8.__file__)\n"
            + "print(os.path.dirname(os.__file__))\n"
            + "print(*sys.path, sep='\\n')\n",
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        log = root / "child.log"
        with log.open("wb") as sink:
            # reliability: ignore[RL005] a file, not a pipe: nothing is left to read
            done = subprocess.run(
                [sys.executable, str(probe)],
                stdout=sink,
                stderr=sink,
                env=env,
                timeout=_SUBPROCESS_TIMEOUT,
                check=False,
            )
        output = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(done.returncode, 0, output)
        return output

    def test_the_documented_import_takes_this_library_after_the_stdlib(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            for name in _FOLDERS:
                (root / name).mkdir()
                shutil.copy2(_ROOT / name / f"{name}.py", root / name)
            imported, stdlib, *path = self._run_documented_import(root)
            library = root / "stdio_utf8"
            self.assertEqual(Path(imported), library / "stdio_utf8.py")
            self.assertGreater(path.index(str(library)), path.index(stdlib))

    def test_the_snippet_is_read_from_the_named_section(self) -> None:
        doc = (
            "## Another section\n\n```python\nwrong = 1\n```\n\n"
            f"{_SECTION}\n\nProse first.\n\n```text\nnot python\n```\n\n"
            "```python\nright = 1\n```\n"
        )
        self.assertEqual(documented_import(doc), "right = 1\n")


if __name__ == "__main__":
    unittest.main()
