"""Grouped suite: this project's sys.path bootstrap never reaches above its own root.

Each tool asks ``retrolog_bootstrap`` for the shared libraries, and the bootstrap puts
each on ``sys.path`` from ONE fixed place: the folder of its name at the project root,
derived from its own ``__file__`` and appended. Nothing here searches the ancestors, so
no folder above a checkout can be imported in place of the real one, and a missing copy
stops the tool before it imports anything. ``retrolog_bootstrap.md`` says why. The
planted look-alikes are of ``stdio_utf8``, the library every tool asks for first.

Run through ``../test_retrolog_bootstrap.py``'s ``load_tests`` hook, which is how this
file runs.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

#: A generous ceiling for each black-box child: a hung CLI must fail this suite
#: rather than hang it.
_SUBPROCESS_TIMEOUT = 120

#: This project's root: two levels above this file, which sits in retrolog_bootstrap/tests/.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The planted library's folder name, which is also the name an ancestor search looks for.
_LIBRARY = "stdio_utf8"

#: The bootstrap's own folder: the module under test, not a tool that runs it.
_BOOTSTRAP = "retrolog_bootstrap"

#: Every entry point to run, relative to the root, with the arguments that run it to a
#: clean exit: each ``retrolog_<name>/retrolog_<name>.py`` at the root but the
#: bootstrap's, found from the layout rather than listed, so a new tool is run without
#: an edit here. Each tool's ``--help`` makes it import all it needs, then exit 0: between
#: them they reach every file outside ``samples/`` whose bootstrap puts a shared library
#: on sys.path, ``bounded_run.py`` (through the survey's converter), ``resolve_http.py``,
#: and ``probe_verdict.py`` (through the probe's client) included. The samples' builders
#: bootstrap the same way, through the same module, and are not run here.
_BOOTSTRAPPED: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (f"{folder.name}/{folder.name}.py", ("--help",))
    for folder in sorted(_PROJECT_ROOT.glob("retrolog_*"))
    if folder.name != _BOOTSTRAP and (folder / f"{folder.name}.py").is_file()
)

#: Folders a clone may hold that are not this project's modules: installed packages, some
#: of which search their ancestors for reasons of their own.
_NOT_SHIPPED = frozenset({"node_modules"})


def _write_lf(path: Path, text: str) -> None:
    """Write *text* to *path* as UTF-8 with LF line endings, on any platform."""
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _shipped_modules(root: Path) -> list[Path]:
    """Every Python file under *root*, sorted, skipping hidden and installed folders."""
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not any(
            part.startswith(".") or part in _NOT_SHIPPED
            for part in path.relative_to(root).parts
        )
    ]


def _changes_sys_path(node: ast.AST) -> bool:
    """Whether *node* assigns to ``sys.path`` or calls one of its mutating methods."""
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(ast.unparse(t).startswith("sys.path") for t in targets)
    if isinstance(node, ast.Call):
        return ast.unparse(node.func) in {
            "sys.path.append",
            "sys.path.extend",
            "sys.path.insert",
        }
    return False


def _iterates_parents(node: ast.AST) -> list[int]:
    """The lines where *node* itself iterates a ``.parents`` sequence."""
    if isinstance(node, (ast.For, ast.AsyncFor)):
        iters = [node.iter]
    else:
        iters = [clause.iter for clause in getattr(node, "generators", [])]
    return [
        it.lineno
        for it in iters
        if isinstance(it, ast.Attribute) and it.attr == "parents"
    ]


def _comprehension_walks(node: ast.AST) -> list[int]:
    """The lines of every comprehension over ``.parents`` anywhere inside *node*."""
    return [
        line
        for inner in ast.walk(node)
        if not isinstance(inner, (ast.For, ast.AsyncFor))
        for line in _iterates_parents(inner)
    ]


def _walks_feeding_the_path(tree: ast.AST, changes: list[ast.AST]) -> list[int]:
    """Walks whose result reaches a ``sys.path`` change in *changes*.

    A ``for`` over ``.parents`` whose body changes the path, and a comprehension over
    it assigned to a name that a path change then reads.
    """
    fed = {
        name.id
        for change in changes
        for name in ast.walk(change)
        if isinstance(name, ast.Name)
    }
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)) and any(
            _changes_sys_path(inner) for inner in ast.walk(node)
        ):
            lines += _iterates_parents(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in fed for target in node.targets
        ):
            lines += _comprehension_walks(node.value)
    return lines


def _ancestor_walk_lines(source: str) -> list[int]:
    """Lines where an iteration of a ``.parents`` sequence feeds ``sys.path``.

    That is the shape of an unbounded upward search for a folder to import from, in
    three spellings: a comprehension inside the statement that changes the path, a
    ``for`` statement whose body changes it, and a comprehension assigned to a name a
    later path change reads. A single ``parents[N]`` index is a subscript rather than
    an iteration, so a bounded path reads as clean, and a walk that only asks a
    question of the ancestors (is this temporary folder inside a repository?) puts
    nothing on the path and is not counted. A ``.parents`` sequence handed to a
    function that iterates it elsewhere is not seen: the scan follows no call.
    """
    tree = ast.parse(source)
    changes = [node for node in ast.walk(tree) if _changes_sys_path(node)]
    lines = [line for change in changes for line in _comprehension_walks(change)]
    return sorted(set(lines + _walks_feeding_the_path(tree, changes)))


class TestBootstrapStaysInsideTheProject(unittest.TestCase):
    """No bootstrap may import a module from above this project's root.

    A search of the ancestors would also take a same-named folder planted ABOVE the
    checkout (a shared temporary directory, say), and would take it outright when the
    project's own copy is missing. These copy the project under a parent holding such
    folders and run each tool in a real subprocess; the planted modules record that they
    ran.
    """

    def _plant_decoys(self, parent: Path) -> Path:
        """Plant the folders an ancestor search would take, holding recording modules.

        Two shapes: ``<parent>/stdio_utf8/``, as this project lays its libraries out,
        and the same folder inside ``<parent>/utilities/``, the layout of a larger tree
        the tools could be placed in, where a search would look for a ``utilities/``
        folder. A ``stdio_utf8.py`` in each records if it is imported in place of the project's
        own. ``json.py`` beside the first records if that folder lands ahead of the
        standard library: the tools import ``json`` after the bootstrap has run, so a
        stranger's folder left early on the path captures that import even when the
        library itself still comes from the right place. Returns the path any decoy
        creates when it runs.
        """
        marker = parent / "decoy-ran"
        record = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        flat = parent / _LIBRARY
        nested = parent / "utilities" / _LIBRARY
        for folder in (flat, nested):
            folder.mkdir(parents=True)
            _write_lf(folder / f"{_LIBRARY}.py", record)
        _write_lf(flat / "json.py", record)
        return marker

    def _run(self, script: Path, *args: str) -> tuple[int, str]:
        """Run *script* to exit with no inherited PYTHON* setting, bounded.

        Its output goes to a file rather than a pipe, so the timeout holds whatever the
        child does, and comes back as text so a failure here can name its own cause.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "child.log"
            with log.open("wb") as sink:
                # reliability: ignore[RL005] a file, not a pipe: nothing is left to read
                done = subprocess.run(
                    [sys.executable, str(script), *args],
                    stdout=sink,
                    stderr=sink,
                    env=env,
                    timeout=_SUBPROCESS_TIMEOUT,
                    check=False,
                )
            output = log.read_text(encoding="utf-8", errors="replace")
        return done.returncode, output

    def _staged_copy(self, parent: Path, *, with_library: bool = True) -> Path:
        """This project copied to ``<parent>/project``, without caches or hidden files.

        *with_library* False leaves out the project's own copy of the library, which
        is the one case where an ancestor search takes the planted folder outright.
        """
        project = parent / "project"
        shutil.copytree(
            _PROJECT_ROOT,
            project,
            ignore=shutil.ignore_patterns(".*", "__pycache__", *_NOT_SHIPPED),
        )
        if not with_library:
            shutil.rmtree(project / _LIBRARY)
        return project

    def test_every_tool_entry_point_is_run(self) -> None:
        """The derived list's own control: an empty one would pass every test below.

        Every entry point at ``<folder>/<folder>.py`` that asks the bootstrap for a
        library is in it, and nothing else is.
        """
        asking = sorted(
            path.relative_to(_PROJECT_ROOT).as_posix()
            for path in _PROJECT_ROOT.glob("*/*.py")
            if path.stem == path.parent.name != _BOOTSTRAP
            and "retrolog_bootstrap.add_shared(" in path.read_text(encoding="utf-8")
        )
        self.assertNotEqual(asking, [])
        self.assertEqual([script for script, _ in _BOOTSTRAPPED], asking)

    def test_no_bootstrap_imports_a_module_planted_above_the_root(self) -> None:
        for script, args in _BOOTSTRAPPED:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as td:
                parent = Path(td)
                marker = self._plant_decoys(parent)
                code, output = self._run(self._staged_copy(parent) / script, *args)
                self.assertFalse(marker.exists(), f"{script} ran a planted module")
                # Also proves the tool really ran: a broken bootstrap exits non-zero.
                self.assertEqual(code, 0, output)

    def test_a_missing_library_stops_the_tool(self) -> None:
        """With its own copy gone, each tool stops rather than looking upward.

        A search of the parent directories would find a planted folder and import it; a
        fixed path finds nothing, and the bootstrap names the library it could not find.
        """
        for script, args in _BOOTSTRAPPED:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as td:
                parent = Path(td)
                marker = self._plant_decoys(parent)
                project = self._staged_copy(parent, with_library=False)
                code, output = self._run(project / script, *args)
                self.assertFalse(marker.exists(), f"{script} ran a planted module")
                self.assertNotEqual(code, 0, output)
                self.assertIn(f"shared library {_LIBRARY!r} not found", output)

    def _probe(self, project: Path, bootstrap: str) -> Path:
        """A script inside *project* that runs *bootstrap*, then imports as a tool does."""
        script = project / "probe" / "probe.py"
        script.parent.mkdir()
        program = (
            "import sys\nfrom pathlib import Path\n"
            + bootstrap
            + f"import {_LIBRARY}  # noqa: E402, F401\n"
            + "import json  # noqa: E402, F401\n"
        )
        _write_lf(script, program)
        return script

    def _search(self, *folder: str, keep: str = "[:1]", into: str = "+=") -> str:
        """An upward search for ``<ancestor>/<folder...>``, as source text."""
        joined = " / ".join(repr(part) for part in folder)
        return (
            f"sys.path{'[:0]' if into == '=' else ''} {into} [\n"
            "    str(folder)\n"
            "    for ancestor in Path(__file__).resolve().parents\n"
            f"    for folder in (ancestor / {joined},)\n"
            f"    if (folder / {_LIBRARY + '.py'!r}).is_file()\n"
            f"]{keep}\n"
        )

    def test_the_decoy_fires_for_a_search_past_a_missing_library(self) -> None:
        """The control for a missing library: a nearest-match search takes the decoy.

        Without it a clean result above could mean a dead decoy rather than a fixed path.
        """
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            marker = self._plant_decoys(parent)
            project = self._staged_copy(parent, with_library=False)
            self._run(self._probe(project, self._search(_LIBRARY)))
            self.assertTrue(marker.exists(), "the decoy never ran; the test is blind")

    def test_the_decoy_fires_for_a_search_of_a_larger_tree(self) -> None:
        """The control for the other shape: a search for ``utilities/<name>/``.

        That is how a bootstrap would search a larger tree the tools could be placed in,
        so this proves the nested decoy would catch such a search, the library present.
        """
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            marker = self._plant_decoys(parent)
            search = self._search("utilities", _LIBRARY)
            self._run(self._probe(self._staged_copy(parent), search))
            self.assertTrue(marker.exists(), "the decoy never ran; the test is blind")

    def test_the_decoy_fires_for_a_search_that_prepends(self) -> None:
        """The control for a present library: a search that prepends every match.

        The project's own copy still wins its own name there, so this is the ``json``
        decoy firing: the planted folder, left ahead of the standard library, captures
        the next import.
        """
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            marker = self._plant_decoys(parent)
            search = self._search(_LIBRARY, keep="", into="=")
            self._run(self._probe(self._staged_copy(parent), search))
            self.assertTrue(marker.exists(), "the decoy never ran; the test is blind")

    def test_no_shipped_module_searches_every_ancestor(self) -> None:
        """Structural, so a walk of any shape the detector names fails here if added.

        The subprocess tests above cover the bootstraps that exist today; this one reads
        every shipped module.
        """
        offenders = [
            f"{path.relative_to(_PROJECT_ROOT)}:{line}"
            for path in _shipped_modules(_PROJECT_ROOT)
            for line in _ancestor_walk_lines(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], "an unbounded ancestor search reached a module")

    def test_the_module_scan_skips_hidden_and_installed_folders(self) -> None:
        """The scan's own control: a module in ``.venv/`` or ``node_modules/`` is not ours."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hidden = root / ".venv" / "lib"
            installed = root / "tool" / "node_modules" / "pkg"
            for folder in (hidden, installed):
                folder.mkdir(parents=True)
            for path in (
                root / "tool.py",
                hidden / "installed.py",
                installed / "gyp.py",
                root / "src.py",
            ):
                _write_lf(path, "")
            self.assertEqual(
                _shipped_modules(root), [root / "src.py", root / "tool.py"]
            )

    def test_the_walk_detector_names_an_unbounded_search(self) -> None:
        """The detector's own control: it must fire on the shapes it exists to refuse."""
        source = (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path[:0] = [\n"
            "    str(a / 'x') for a in Path(__file__).resolve().parents\n"
            "]\n"
            "sys.path.extend(str(a / 'y') for a in Path(__file__).resolve().parents)\n"
        )
        self.assertEqual(_ancestor_walk_lines(source), [4, 6])

    def test_the_walk_detector_names_a_for_loop_over_the_ancestors(self) -> None:
        """A ``for`` statement is the other spelling of the same search."""
        source = (
            "import sys\n"
            "from pathlib import Path\n"
            "for a in Path(__file__).resolve().parents:\n"
            "    if (a / 'x').is_dir():\n"
            "        sys.path.append(str(a / 'x'))\n"
            "        break\n"
        )
        self.assertEqual(_ancestor_walk_lines(source), [3])

    def test_the_walk_detector_names_a_search_kept_for_later(self) -> None:
        """A walk whose result reaches ``sys.path`` a statement later is still a walk."""
        source = (
            "import sys\n"
            "from pathlib import Path\n"
            "found = [a for a in Path(__file__).parents if (a / 'x').is_dir()]\n"
            "sys.path.append(str(found[0] / 'x'))\n"
        )
        self.assertEqual(_ancestor_walk_lines(source), [3])

    def test_the_walk_detector_passes_a_for_loop_that_leaves_the_path(self) -> None:
        """A ``for`` over the ancestors whose body leaves the path alone is a question."""
        source = (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.append('x')\n"
            "for a in Path.cwd().parents:\n"
            "    if (a / '.git').exists():\n"
            "        break\n"
        )
        self.assertEqual(_ancestor_walk_lines(source), [])

    def test_the_walk_detector_passes_a_question_beside_a_path_change(self) -> None:
        """The shape the live tests use: a skip check in a module that sets its path."""
        source = (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, 'src')\n"
            "inside = any((f / '.git').exists() for f in Path.cwd().parents)\n"
        )
        self.assertEqual(_ancestor_walk_lines(source), [])

    def test_the_walk_detector_passes_a_bounded_index(self) -> None:
        """``parents[N]`` is the bounded form, and must not read as a walk."""
        source = (
            "import sys\nfrom pathlib import Path\n"
            "sys.path.append(str(Path(__file__).resolve().parents[1] / 'x'))\n"
        )
        self.assertEqual(_ancestor_walk_lines(source), [])

    def test_the_walk_detector_passes_a_question_about_the_ancestors(self) -> None:
        """A walk that only tests the ancestors puts nothing on the path."""
        source = (
            "from pathlib import Path\n"
            "inside = any((f / '.git').exists() for f in Path.cwd().parents)\n"
        )
        self.assertEqual(_ancestor_walk_lines(source), [])


if __name__ == "__main__":
    unittest.main()
