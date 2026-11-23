"""What the memory watchdog saw of the tree: ``tree_observer=``, and no kill once the child is reaped.

The watchdog walks the child's whole tree on every poll, so it can say whether the tree ever
held more than the child itself: a caller that trusts a clean exit (a stress harness) needs to
know whether some descendant ran whose own end it never saw. ``TreeObserver`` carries that out
of a run without changing what the run returns.
"""

from __future__ import annotations

import sys
import threading
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bounded_run
import boundedrun_memory
import boundedrun_memread

#: Bound on a live run here: far past what each takes, so only a hang reaches it.
_LIVE_TIMEOUT = 60.0
#: A memory limit no child here comes near.
_ROOMY = 1 << 31

#: A child that runs a grandchild for a second and waits for it: two processes at once.
_SPAWNS_A_GRANDCHILD = (
    "import subprocess, sys\n"
    "subprocess.run([sys.executable, '-c', 'import time; time.sleep(1)'], check=True)\n"
)


class _TableReader:
    """Footprints, children and groups from fixed tables, each by pid."""

    def __init__(
        self,
        sizes: Mapping[int, int],
        children: Mapping[int, Sequence[int]],
        groups: Mapping[int, int],
    ) -> None:
        self._sizes = sizes
        self._children = children
        self._groups = groups

    def footprint(self, pid: int) -> int:
        return self._sizes.get(pid, 0)

    def children(self, pid: int) -> list[int]:
        return list(self._children.get(pid, ()))

    def group(self, pid: int) -> int | None:
        return self._groups.get(pid)

    def group_members(self, pgid: int) -> list[int]:
        return [pid for pid, group in self._groups.items() if group == pgid]


class TreeUsageTests(unittest.TestCase):
    def test_counts_each_process_once_however_it_was_reached(self) -> None:
        reader = _TableReader(
            {10: 1, 11: 2, 20: 4}, {10: [11, 11]}, {10: 10, 11: 10, 20: 10}
        )
        usage = boundedrun_memread.tree_usage(10, reader)
        self.assertEqual((usage.bytes, usage.processes), (7, 3))
        self.assertEqual(boundedrun_memread.tree_footprint(10, reader), 7)

    def test_a_lone_root_is_one_process(self) -> None:
        usage = boundedrun_memread.tree_usage(10, _TableReader({10: 5}, {}, {}))
        self.assertEqual(usage.processes, 1)


@unittest.skipIf(sys.platform == "win32", "memory_limit= is refused on Windows")
class LiveObserverTests(unittest.TestCase):
    def test_a_leaf_child_is_one_process(self) -> None:
        observer = bounded_run.TreeObserver()
        done = bounded_run.run_bounded(
            [sys.executable, "-c", "import time; time.sleep(0.3)"],
            timeout=_LIVE_TIMEOUT,
            memory_limit=_ROOMY,
            tree_observer=observer,
        )
        self.assertEqual(done.returncode, 0)
        self.assertEqual(observer.max_processes, 1)

    def test_a_child_that_ran_a_grandchild_is_seen_as_two(self) -> None:
        observer = bounded_run.TreeObserver()
        done = bounded_run.run_bounded_bytes(
            [sys.executable, "-c", _SPAWNS_A_GRANDCHILD],
            timeout=_LIVE_TIMEOUT,
            memory_limit=_ROOMY,
            tree_observer=observer,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertGreaterEqual(observer.max_processes, 2)

    def test_a_breach_still_reports_what_was_seen(self) -> None:
        observer = bounded_run.TreeObserver()
        child = "import time\nheld = b'x' * (256 << 20)\ntime.sleep(30)\n"
        with self.assertRaises(bounded_run.MemoryLimitExceeded):
            bounded_run.run_bounded(
                [sys.executable, "-c", child],
                timeout=_LIVE_TIMEOUT,
                memory_limit=64 << 20,
                tree_observer=observer,
            )
        self.assertGreaterEqual(observer.max_processes, 1)


class ObserverRefusalTests(unittest.TestCase):
    def test_an_observer_without_a_memory_limit_is_refused_before_spawning(
        self,
    ) -> None:
        # The argv names no program: had anything been spawned, it would raise OSError.
        for run in (bounded_run.run_bounded, bounded_run.run_bounded_bytes):
            with (
                self.subTest(run=run.__name__),
                self.assertRaises(ValueError) as caught,
            ):
                run(  # type: ignore[operator]
                    ["no-such-program-anywhere"],
                    timeout=1.0,
                    tree_observer=bounded_run.TreeObserver(),
                )
            self.assertIn("memory_limit", str(caught.exception))

    def test_a_fresh_observer_has_seen_nothing(self) -> None:
        self.assertEqual(bounded_run.TreeObserver().max_processes, 0)


class _BreachingReader:
    """Reads one total past every limit, as the root's own footprint."""

    def __init__(self) -> None:
        self.polled = threading.Event()

    def footprint(self, pid: int) -> int:
        self.polled.set()
        return 150

    def children(self, pid: int) -> list[int]:
        return []

    def group(self, pid: int) -> int | None:
        return None

    def group_members(self, pgid: int) -> list[int]:
        return []


class ReapedChildTests(unittest.TestCase):
    """Once the child is reaped its pid may be reused, so no kill may begin."""

    def test_a_breach_read_after_the_reap_kills_nothing(self) -> None:
        killed: list[int] = []
        reader = _BreachingReader()
        watchdog = boundedrun_memory.MemoryWatchdog(
            42, 100, reader, killed.append, interval=0.001, unreaped=lambda: False
        )
        watchdog.start()
        self.assertTrue(reader.polled.wait(timeout=5))
        watchdog.stop()
        self.assertEqual(killed, [])
        self.assertFalse(watchdog.breached)

    def test_an_unreaped_child_is_still_killed(self) -> None:
        killed: list[int] = []
        done = threading.Event()

        def kill(pid: int) -> None:
            killed.append(pid)
            done.set()

        watchdog = boundedrun_memory.MemoryWatchdog(
            42, 100, _BreachingReader(), kill, interval=0.001
        )
        watchdog.start()
        self.assertTrue(done.wait(timeout=5))
        watchdog.stop()
        self.assertEqual(killed, [42])
        self.assertTrue(watchdog.breached)


if __name__ == "__main__":
    unittest.main()
