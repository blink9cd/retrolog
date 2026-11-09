"""Aggregator: imports every tests/ suite so one coverage run measures them as one unit.

The floor below is deliberately restated, not derived. A floor computed from discovery
compares discovery against itself and passes for an empty suite, which is the defect
this guard exists to catch. What is derived is the file set: `_tests_by_module`
flattens the suite `load_tests` actually folds into the real run into a per-file count,
and `GroupedSuiteDiscoveryTest.test_grouped_suite_meets_its_own_per_file_floor` checks
that count's key set against `_MIN_TESTS_BY_FILE`'s, so a new tests/test_*.py arrives
unfloored loudly, not silently absorbed into a combined total.

``_SUITE_DIR`` is the discovery root for ``_grouped_suite()``, with
``top_level_dir=str(_SUITE_DIR)`` so a grouped module's ``__module__`` is its bare
filename (``test_probe_input``, not ``tests.test_probe_input``), which keeps mypy from
finding one file under two module names.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SUITE_DIR = _HERE / "tests"

# Restated, not derived (see module docstring).
_MIN_TESTS_BY_FILE: dict[str, int] = {
    # The id rule (probe_ids) and reading export's import files (probe_input).
    "test_probe_input.py": 20,
    # What one answer means (probe_verdict).
    "test_probe_verdict.py": 10,
    # What is sent, the pace, and the 429 back-off (probe_client).
    "test_probe_client.py": 16,
    # The verdict cache (probe_cache).
    "test_probe_cache.py": 10,
    # The control, the cache, and the stop after repeated 429s (probe_run).
    "test_probe_run.py": 11,
    # The report (probe_report).
    "test_probe_report.py": 3,
    # The command line end to end, and the committed sample.
    "test_probe_cli.py": 33,
    # Two real requests, skipped offline.
    "test_probe_live.py": 1,
}
# This module's OWN tests: the guards below. Kept apart from the grouped floors, and
# exact rather than a floor, so no grouped growth pays for a deleted guard.
_OWN_GUARD_TESTS = 2


def _grouped_suite() -> unittest.TestSuite:
    """Every test in tests/, discovered by walking the directory."""
    loader = unittest.TestLoader()
    return loader.discover(
        str(_SUITE_DIR), pattern="test_*.py", top_level_dir=str(_SUITE_DIR)
    )


def _tests_by_module(suite: unittest.TestSuite) -> dict[str, int]:
    """Flatten a (possibly nested) test suite into a per-module test count.

    ``discover`` returns a suite of suites, so this recurses rather than assuming a
    fixed depth. Each leaf's ``__module__`` becomes a ``<name>.py`` key.
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
    """Fail loudly when this module's real test load is missing a grouped suite.

    ``loadTestsFromModule`` is what a bare run and a coverage run use, and it honors
    the ``load_tests`` protocol, so the first guard depends on that wiring rather than
    re-deriving the count off disk.
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
            f"{_OWN_GUARD_TESTS}",
        )
        grouped_found = sum(loaded.values())
        grouped_floor = sum(_MIN_TESTS_BY_FILE.values())
        self.assertGreaterEqual(
            grouped_found,
            grouped_floor,
            f"loading this module yielded {grouped_found} grouped tests, expected at "
            f"least {grouped_floor}; load_tests may have been removed or renamed",
        )

    def test_grouped_suite_meets_its_own_per_file_floor(self) -> None:
        """Fail when one grouped file has lost tests, or a new one has no floor."""
        found_by_file = _tests_by_module(_grouped_suite())
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
                f"tests/{filename} yielded {found} tests, expected at least {floor}",
            )


def load_tests(
    _loader: unittest.TestLoader, tests: unittest.TestSuite, _pattern: str | None
) -> unittest.TestSuite:
    """unittest's hook: fold the grouped suite in beside this file's own guards."""
    return unittest.TestSuite([tests, _grouped_suite()])


if __name__ == "__main__":
    unittest.main()
