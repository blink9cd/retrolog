"""Aggregator: imports every tests/ suite so one coverage run measures them as one unit.

The floor below is deliberately RESTATED, not derived. A floor computed from discovery
compares discovery against itself and passes for an empty suite, which is the defect
this guard exists to catch. What IS derived is the file SET: `_tests_by_module`
flattens the suite `load_tests` actually folds into the real run into a per-file count,
and `GroupedSuiteDiscoveryTest.test_grouped_suite_meets_its_own_per_file_floor` checks
that count's key set against `_MIN_TESTS_BY_FILE`'s -- so a new tests/test_*.py arrives
unfloored loudly, not silently absorbed into a combined total.

``_SUITE_DIR`` is the discovery root for ``_grouped_suite()``, with
``top_level_dir=str(_SUITE_DIR)`` so a grouped module's ``__module__`` is its bare
filename (``test_walk``, not ``tests.test_walk``). That avoids the mypy "Source file
found twice under different module names" conflict a package-style ``from tests import
test_walk`` import produces the moment ``discover()`` and an explicit import disagree
on one file's qualified name.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"

# Restated, not derived (see module docstring): a floor computed from discovery would
# compare discovery against itself and pass for an empty suite.
_MIN_TESTS_BY_FILE: dict[str, int] = {
    "test_walk.py": 13,
    "test_text.py": 19,
    "test_lines.py": 10,
    "test_docx.py": 29,
    "test_docx_damage.py": 23,
    "test_docx_hostile.py": 7,
    "test_convert.py": 24,
    "test_guard.py": 43,
    "test_report.py": 23,
    "test_report_build.py": 13,
    "test_cli.py": 28,
    "test_cli_usage.py": 14,
}
# This module's OWN tests: the guards below, which are the only reason the file exists.
# Kept apart from the grouped floors rather than summed into one total, so no amount of
# grouped growth pays for a deleted guard. Restated, not counted off the class below: a
# count taken from that class falls by one when a guard is deleted, so it would pass for
# exactly the deletion it exists to catch. Exact rather than a floor: under ``>=`` an
# added guard becomes room a later deletion can hide in.
_OWN_GUARD_TESTS = 2


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/, discovered by walking the directory."""
    loader = unittest.TestLoader()
    return loader.discover(
        str(_SUITE_DIR), pattern="test_*.py", top_level_dir=str(_SUITE_DIR)
    )


def _tests_by_module(suite: unittest.TestSuite) -> dict[str, int]:
    """Flatten a (possibly nested) test suite into a per-module test count.

    ``unittest.TestLoader.discover`` returns a suite of suites (one layer per
    directory/module it walked), so this recurses through the nesting rather than
    assuming a fixed depth. Each leaf test case's ``__module__`` (e.g. ``"test_walk"``)
    becomes a ``"test_walk.py"`` key, matching ``_MIN_TESTS_BY_FILE``'s keys.
    """
    counts: dict[str, int] = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for module, count in _tests_by_module(item).items():
                counts[module] = counts.get(module, 0) + count
        else:
            filename = f"{type(item).__module__}.py"
            counts[filename] = counts.get(filename, 0) + 1
    return counts


class GroupedSuiteDiscoveryTest(unittest.TestCase):
    """Fail loudly when THIS MODULE's real test load is missing a grouped suite --
    not merely when `tests/`, read directly off disk, is missing one.

    A guard that called `_grouped_suite()` directly would share its subject's failure
    mode: deleting or renaming `load_tests` (which silently stops every suite under
    `tests/` from running in the real invocation) would leave it re-deriving the same
    count off disk and staying green. `unittest.TestLoader().loadTestsFromModule` is
    what `python test_retrolog_survey.py` and a coverage run use to build
    the real suite, and it honors the `load_tests` protocol, so this is what actually
    depends on that wiring.
    """

    def test_module_load_includes_the_grouped_suites(self) -> None:
        loaded = _tests_by_module(
            unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        )
        own_found = loaded.pop(f"{__name__}.py", 0)
        self.assertEqual(
            own_found,
            _OWN_GUARD_TESTS,
            f"this module contributed {own_found} of its own tests, expected exactly "
            f"{_OWN_GUARD_TESTS} -- a guard above may have been deleted, or a new one "
            f"added without raising _OWN_GUARD_TESTS",
        )
        # Derived from the per-file floors rather than restated as a second total: that
        # dict is a written-down literal, not a count taken from discovery.
        grouped_found = sum(loaded.values())
        grouped_floor = sum(_MIN_TESTS_BY_FILE.values())
        self.assertGreaterEqual(
            grouped_found,
            grouped_floor,
            f"loading this module yielded {grouped_found} grouped tests, expected at "
            f"least {grouped_floor} -- load_tests may have been removed or renamed, "
            f"silently dropping a tests/ suite from the real run",
        )

    def test_grouped_suite_meets_its_own_per_file_floor(self) -> None:
        """Fail loudly when ONE grouped file has lost tests, and when a new
        tests/test_*.py arrives with no floor entry in `_MIN_TESTS_BY_FILE` at all.
        """
        found_by_file = _tests_by_module(_grouped_suite())
        # The full key set is compared both ways: an unmapped new file (or a mapped
        # file that no longer exists) fails here, before any count is compared.
        self.assertEqual(
            found_by_file.keys(),
            _MIN_TESTS_BY_FILE.keys(),
            "tests/ discovery and _MIN_TESTS_BY_FILE disagree on which files exist: "
            f"found {sorted(found_by_file)}, mapped {sorted(_MIN_TESTS_BY_FILE)}",
        )
        for filename, floor in _MIN_TESTS_BY_FILE.items():
            found = found_by_file.get(filename, 0)
            self.assertGreaterEqual(
                found,
                floor,
                f"tests/{filename} yielded {found} tests, expected at least "
                f"{floor} -- a test may have been removed from it",
            )


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold the grouped suite in beside this file's own guards."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
