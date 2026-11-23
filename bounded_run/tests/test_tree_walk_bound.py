"""One poll's tree walk is bounded: a breach ends it early, and a walk past its cap fails closed.

A child that forks faster than the walk reads its tree once kept a poll from ever finishing, so
the sum never reached the limit check and the bound never fired. The walk now compares its
running total to the limit as it goes, and stops at a process count and a monotonic deadline,
raising the reader-failure path that kills the tree. Each test reads an endless stand-in tree,
so nothing here forks.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import boundedrun_memory
import boundedrun_memread

#: How long a test waits on something the watchdog should do at once.
_PROMPT_SECONDS = 10.0
#: A small process cap, patched in so an endless walk ends fast.
_SMALL_CAP = 50


class _EndlessReader:
    """A tree that never ends: every pid has one child, the next pid, each of *size* bytes."""

    def __init__(self, size: int) -> None:
        self._size = size
        self.reads = 0

    def footprint(self, pid: int) -> int:
        self.reads += 1
        return self._size

    def children(self, pid: int) -> list[int]:
        return [pid + 1]

    def group(self, pid: int) -> int | None:
        return None

    def group_members(self, pgid: int) -> list[int]:
        return []


class _StepClock:
    """A monotonic clock that moves *step* seconds on every reading."""

    def __init__(self, step: float) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        self._now += self._step
        return self._now


class TreeWalkBoundTests(unittest.TestCase):
    def test_a_total_over_the_limit_ends_the_walk_at_once(self) -> None:
        reader = _EndlessReader(10)
        usage = boundedrun_memread.tree_usage(1, reader, limit=95)
        self.assertEqual(usage.bytes, 100)
        self.assertEqual(reader.reads, 10)

    def test_a_walk_past_the_process_cap_fails_closed(self) -> None:
        reader = _EndlessReader(0)
        with mock.patch.object(boundedrun_memread, "MAX_WALK_PROCESSES", _SMALL_CAP):
            with self.assertRaises(boundedrun_memread.TreeWalkOverCap) as caught:
                boundedrun_memread.tree_usage(1, reader, limit=1 << 40)
        self.assertIsInstance(caught.exception, OSError)
        self.assertIn(str(_SMALL_CAP), str(caught.exception))
        self.assertLessEqual(reader.reads, _SMALL_CAP + 1)

    def test_a_walk_past_its_deadline_fails_closed(self) -> None:
        clock = _StepClock(boundedrun_memread.MAX_WALK_SECONDS / 4)
        with self.assertRaises(boundedrun_memread.TreeWalkOverCap):
            boundedrun_memread.tree_usage(1, _EndlessReader(0), clock=clock)

    def test_a_walk_under_both_caps_still_sums_the_whole_tree(self) -> None:
        reader = _EndlessReader(1)
        reader.children = lambda pid: [pid + 1] if pid < 5 else []  # type: ignore[method-assign]
        usage = boundedrun_memread.tree_usage(1, reader, limit=1 << 40)
        self.assertEqual((usage.bytes, usage.processes), (5, 5))


class WatchdogWalkTests(unittest.TestCase):
    def test_a_tree_forking_past_the_limit_is_a_breach(self) -> None:
        killed = threading.Event()
        watchdog = boundedrun_memory.MemoryWatchdog(
            42, 100, _EndlessReader(10), lambda pid: killed.set(), interval=0.001
        )
        watchdog.start()
        self.assertTrue(killed.wait(timeout=_PROMPT_SECONDS))
        watchdog.stop()
        self.assertTrue(watchdog.breached)
        self.assertGreater(watchdog.peak, 100)
        self.assertIsNone(watchdog.error)

    def test_a_tree_forking_past_the_cap_raises_memory_watch_failed(self) -> None:
        killed = threading.Event()

        def drain() -> tuple[str, str]:
            assert killed.wait(timeout=_PROMPT_SECONDS)
            return "", ""

        with mock.patch.object(boundedrun_memread, "MAX_WALK_PROCESSES", _SMALL_CAP):
            with self.assertRaises(boundedrun_memory.MemoryWatchFailed) as caught:
                boundedrun_memory.drain_watched(
                    42,
                    ["argv"],
                    1 << 40,
                    _EndlessReader(0),
                    lambda pid: killed.set(),
                    drain,
                    lambda: None,
                    interval=0.001,
                )
        self.assertIsInstance(caught.exception, subprocess.SubprocessError)
        self.assertIsInstance(
            caught.exception.__cause__, boundedrun_memread.TreeWalkOverCap
        )


if __name__ == "__main__":
    unittest.main()
