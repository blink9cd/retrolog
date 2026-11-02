"""Tests for retrolog_bootstrap: which shared library folders it puts on sys.path.

Each shared library is the folder of its name at this repository's root, one level
above this file's own folder. These run against those real folders and restore
``sys.path`` after each one. That no tool imports a library from above the root is
proven black-box, in a copy of the repository, by ``tests/test_bootstrap_black_box.py``,
which this file's ``load_tests`` hook runs with these: a runner that launches each
folder's top-level ``test_*.py`` reaches ``tests/`` through that hook alone.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"
sys.path.insert(0, str(_HERE))
import retrolog_bootstrap  # noqa: E402 - after the sys.path insert above

#: This repository's root: the folder above this file's own.
_REPO_ROOT = _HERE.parent

#: What each grouped file under ``tests/`` must yield. Restated, not derived: a floor
#: computed from discovery would compare discovery against itself and pass for an empty
#: suite, and a file added there later fails the key comparison until it is floored.
_MIN_TESTS_BY_FILE: dict[str, int] = {"test_bootstrap_black_box.py": 14}


def _load_module_from(path: Path) -> types.ModuleType:
    """Load a copy of the bootstrap from ``path`` under a name of its own."""
    spec = importlib.util.spec_from_file_location("retrolog_bootstrap_copy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered for the import only: a dataclass in the copy would read
    # sys.modules[cls.__module__] while its body runs, and finds None when unregistered.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


class TestSharedFolder(unittest.TestCase):
    def test_each_library_is_the_folder_of_its_name_at_the_root(self) -> None:
        for name in sorted(retrolog_bootstrap.SHARED):
            with self.subTest(name=name):
                self.assertEqual(
                    retrolog_bootstrap.shared_folder(name), _REPO_ROOT / name
                )

    def test_a_name_with_no_folder_at_the_root_is_none(self) -> None:
        self.assertIsNone(retrolog_bootstrap.shared_folder("no_such_library"))

    def test_a_folder_without_its_module_is_none(self) -> None:
        # The candidate is kept only when it holds <name>.py, so a folder of the name
        # that lacks the module is not taken. Asked for from a copy of this file's
        # folder with the module removed, the lookup finds nothing.
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / "retrolog_bootstrap"
            copy.mkdir()
            shutil.copy2(_HERE / "retrolog_bootstrap.py", copy)
            (Path(scratch) / "stdio_utf8").mkdir()
            loaded = _load_module_from(copy / "retrolog_bootstrap.py")
            self.assertIsNone(loaded.shared_folder("stdio_utf8"))


class TestAddShared(unittest.TestCase):
    def setUp(self) -> None:
        saved = list(sys.path)
        self.addCleanup(sys.path.__setitem__, slice(None), saved)

    def test_appends_each_named_library_once(self) -> None:
        retrolog_bootstrap.add_shared("stdio_utf8", "bounded_fetch")
        before = list(sys.path)
        retrolog_bootstrap.add_shared("stdio_utf8")
        self.assertEqual(sys.path, before)
        for name in ("stdio_utf8", "bounded_fetch"):
            self.assertEqual(sys.path.count(str(_REPO_ROOT / name)), 1, name)

    def test_appends_after_the_standard_library(self) -> None:
        stdlib = str(Path(unittest.__file__).resolve().parents[1])
        retrolog_bootstrap.add_shared("help_formatter")
        added = sys.path.index(str(_REPO_ROOT / "help_formatter"))
        self.assertGreater(added, sys.path.index(stdlib))

    def test_an_unknown_name_is_refused_before_any_change(self) -> None:
        before = list(sys.path)
        with self.assertRaisesRegex(ValueError, "not a shared library: 'json'"):
            retrolog_bootstrap.add_shared("stdio_utf8", "json")
        self.assertEqual(sys.path, before)

    def test_a_missing_library_raises_naming_it(self) -> None:
        with (
            mock.patch.object(retrolog_bootstrap, "shared_folder", return_value=None),
            self.assertRaisesRegex(ModuleNotFoundError, "bounded_run") as caught,
        ):
            retrolog_bootstrap.add_shared("bounded_run")
        self.assertEqual(caught.exception.name, "bounded_run")


def _grouped_suite() -> unittest.TestSuite:
    """Every ``test_*.py`` under ``tests/``, loaded as top-level modules."""
    loader = unittest.TestLoader()
    return loader.discover(str(_SUITE_DIR), top_level_dir=str(_SUITE_DIR))


def _tests_by_module(suite: unittest.TestSuite) -> dict[str, int]:
    """A (possibly nested) suite flattened into a per-file test count."""
    counts: dict[str, int] = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for module, count in _tests_by_module(item).items():
                counts[module] = counts.get(module, 0) + count
        else:
            filename = f"{type(item).__module__}.py"
            counts[filename] = counts.get(filename, 0) + 1
    return counts


class TestGroupedSuite(unittest.TestCase):
    def test_module_load_includes_the_grouped_suite(self) -> None:
        # Through loadTestsFromModule, which honors load_tests as the real run does, so
        # a removed or renamed hook fails here rather than dropping tests/ silently.
        loaded = _tests_by_module(
            unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        )
        loaded.pop(f"{__name__}.py", None)
        self.assertGreaterEqual(sum(loaded.values()), sum(_MIN_TESTS_BY_FILE.values()))

    def test_each_grouped_file_meets_its_floor(self) -> None:
        found = _tests_by_module(_grouped_suite())
        self.assertEqual(found.keys(), _MIN_TESTS_BY_FILE.keys())
        for filename, floor in _MIN_TESTS_BY_FILE.items():
            with self.subTest(filename=filename):
                self.assertGreaterEqual(found[filename], floor)


def load_tests(
    loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    """unittest's own hook: fold the grouped suite in beside this file's own tests."""
    standard_tests.addTests(_grouped_suite())
    return standard_tests


if __name__ == "__main__":
    unittest.main()
