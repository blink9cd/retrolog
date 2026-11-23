"""The memory watchdog and the watched drain, on a real thread against scripted readers.

The readers themselves are pinned in ``test_memory_readers.py``; here each poll reads a
scripted total, so every outcome the watchdog can reach (a breach, a failed kill, a failed
read, a stop) is driven deterministically.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bounded_run
import boundedrun_memory
import boundedrun_posix_tree
from boundedrun_memory import MemoryWatchdog

#: How long a test waits on something the watchdog should do at once.
_PROMPT_SECONDS = 5.0


class _ScriptedReader:
    """Reads the next total from a script on each poll, as the root's own footprint."""

    def __init__(self, totals: Sequence[int]) -> None:
        self._totals = list(totals)
        self.polled = threading.Event()

    def footprint(self, pid: int) -> int:
        self.polled.set()
        if not self._totals:
            raise OSError("script ran out")
        return self._totals.pop(0) if len(self._totals) > 1 else self._totals[0]

    def children(self, pid: int) -> list[int]:
        return []

    def group(self, pid: int) -> int | None:
        return None

    def group_members(self, pgid: int) -> list[int]:
        return []


class _BlockedReader(_ScriptedReader):
    """Reads one total past every limit, but only once released, so a stop can come first."""

    def __init__(self) -> None:
        super().__init__([150])
        self.release = threading.Event()

    def footprint(self, pid: int) -> int:
        self.polled.set()
        assert self.release.wait(timeout=10)
        return 150


class WatchdogTests(unittest.TestCase):
    def _watch(
        self, reader: boundedrun_memory.TreeReader, kill: Callable[[int], None]
    ) -> MemoryWatchdog:
        return MemoryWatchdog(42, 100, reader, kill, interval=0.001)

    def test_a_breach_records_the_peak_then_kills_once(self) -> None:
        killed: list[int] = []
        done = threading.Event()

        def kill(pid: int) -> None:
            killed.append(pid)
            done.set()

        watchdog = self._watch(_ScriptedReader([10, 50, 150, 150]), kill)
        watchdog.start()
        self.assertTrue(done.wait(timeout=10))
        watchdog.stop()
        self.assertTrue(watchdog.breached)
        self.assertEqual(watchdog.peak, 150)
        self.assertEqual(killed, [42])
        self.assertIsNone(watchdog.error)
        self.assertFalse(watchdog._thread.is_alive(), "stop() left the thread running")

    def test_stop_ends_a_watch_that_never_breaches(self) -> None:
        reader = _ScriptedReader([10])
        watchdog = self._watch(reader, lambda pid: None)
        watchdog.start()
        self.assertTrue(reader.polled.wait(timeout=10))
        watchdog.stop()
        watchdog.stop()  # a second stop is harmless
        self.assertFalse(watchdog.breached)
        self.assertEqual(watchdog.peak, 10)
        self.assertFalse(watchdog._thread.is_alive(), "stop() left the thread running")

    def test_a_failed_kill_is_kept_for_the_caller(self) -> None:
        done = threading.Event()

        def refuse(pid: int) -> None:
            done.set()
            raise ValueError("refused")

        watchdog = self._watch(_ScriptedReader([150]), refuse)
        watchdog.start()
        self.assertTrue(done.wait(timeout=10))
        watchdog.stop()
        self.assertTrue(watchdog.breached)
        self.assertIsInstance(watchdog.error, ValueError)

    def test_a_failed_read_kills_the_tree_at_once(self) -> None:
        killed = threading.Event()
        watchdog = self._watch(_ScriptedReader([]), lambda pid: killed.set())
        watchdog.start()
        # Before any stop: the kill is the watchdog's own, not the run's end.
        self.assertTrue(killed.wait(timeout=_PROMPT_SECONDS), "the tree was not killed")
        watchdog.stop()
        self.assertFalse(watchdog.breached)
        self.assertIsInstance(watchdog.error, OSError)
        self.assertIsNone(watchdog.kill_error)

    def test_a_failed_read_whose_kill_also_failed_keeps_both(self) -> None:
        def refuse(pid: int) -> None:
            raise ValueError("refused")

        watchdog = self._watch(_ScriptedReader([]), refuse)
        watchdog.start()
        watchdog._thread.join(timeout=_PROMPT_SECONDS)
        watchdog.stop()
        self.assertIsInstance(watchdog.error, OSError)
        self.assertIsInstance(watchdog.kill_error, ValueError)

    def test_no_kill_begins_once_stop_has_begun(self) -> None:
        killed: list[int] = []
        reader = _BlockedReader()
        watchdog = self._watch(reader, killed.append)
        watchdog.start()
        self.assertTrue(reader.polled.wait(timeout=10))
        stopper = threading.Thread(target=watchdog.stop)
        stopper.start()
        deadline = time.monotonic() + _PROMPT_SECONDS
        while not watchdog._stopped.is_set() and time.monotonic() < deadline:
            time.sleep(0.001)  # the poll interval while stop() takes hold
        reader.release.set()  # the read now returns a breach, after stop() began
        stopper.join(timeout=10)
        self.assertEqual(killed, [], "a kill began after stop()")
        self.assertFalse(watchdog.breached)

    def test_stop_waits_out_a_kill_in_progress_only_up_to_its_bound(self) -> None:
        in_kill = threading.Event()
        finish = threading.Event()

        def slow_kill(pid: int) -> None:
            in_kill.set()
            assert finish.wait(timeout=10)

        watchdog = self._watch(_ScriptedReader([150]), slow_kill)
        watchdog.start()
        self.assertTrue(in_kill.wait(timeout=10))
        with mock.patch.object(boundedrun_memory, "STOP_JOIN_SECONDS", 0.05):
            started = time.monotonic()
            watchdog.stop()
            self.assertLess(time.monotonic() - started, _PROMPT_SECONDS)
        finish.set()
        watchdog._thread.join(timeout=10)
        self.assertTrue(watchdog.breached)

    def test_the_stop_bound_outlasts_a_whole_tree_kill(self) -> None:
        # Not restated: the bound must cover the kill's own bound plus an overdue snapshot's
        # reap, or stopping could abandon a kill midway and return while it still runs.
        kill_bound = (
            bounded_run.KILL_TIMEOUT_SECONDS + boundedrun_posix_tree._REAP_GRACE_SECONDS
        )
        self.assertGreater(boundedrun_memory.STOP_JOIN_SECONDS, kill_bound)


class DrainWatchedTests(unittest.TestCase):
    """How the drain's outcome and the watchdog's combine into what the caller sees."""

    def _drain(
        self,
        reader: boundedrun_memory.TreeReader,
        kill: Callable[[int], None],
        drain: Callable[[], tuple[str, str]],
    ) -> tuple[str, str]:
        return boundedrun_memory.drain_watched(
            42,
            ["argv"],
            100,
            reader,
            kill,
            drain,
            lambda: self._abandoned.append(None),
            interval=0.001,
        )

    def setUp(self) -> None:
        self._abandoned: list[None] = []

    def test_streams_pass_through_when_nothing_breached(self) -> None:
        self.assertEqual(
            self._drain(_ScriptedReader([1]), print, lambda: ("o", "e")), ("o", "e")
        )

    def _drain_after_breach(
        self, outcome: tuple[str, str] | BaseException
    ) -> tuple[Callable[[], tuple[str, str]], Callable[[], None]]:
        """A drain that waits for the watchdog's verdict, then returns or raises *outcome*."""
        gate = threading.Event()

        def drain() -> tuple[str, str]:
            self.assertTrue(gate.wait(timeout=10))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return drain, gate.set

    def test_a_breach_raises_with_the_drained_output(self) -> None:
        drain, release = self._drain_after_breach(("o", "e"))
        with self.assertRaises(boundedrun_memory.MemoryLimitExceeded) as caught:
            self._drain(_ScriptedReader([150]), lambda pid: release(), drain)
        error = caught.exception
        self.assertEqual((error.cmd, error.limit, error.peak), (["argv"], 100, 150))
        self.assertEqual((error.stdout, error.stderr), ("o", "e"))
        self.assertIn("100", str(error))
        self.assertIn("150", str(error))

    def test_a_breach_that_ran_into_the_timeout_still_reads_as_a_breach(self) -> None:
        expired = subprocess.TimeoutExpired(["argv"], 1.0, output="o", stderr="e")

        def refuse(pid: int) -> None:
            release()
            raise ValueError("kill refused")

        drain, release = self._drain_after_breach(expired)
        with self.assertRaises(boundedrun_memory.MemoryLimitExceeded) as caught:
            self._drain(_ScriptedReader([150]), refuse, drain)
        self.assertEqual(caught.exception.output, "o")
        self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_a_plain_timeout_propagates_unchanged(self) -> None:
        expired = subprocess.TimeoutExpired(["argv"], 1.0)

        def drain() -> tuple[str, str]:
            raise expired

        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self._drain(_ScriptedReader([1]), print, drain)
        self.assertIs(caught.exception, expired)

    def test_a_failed_read_kills_at_once_and_raises_watch_failed(self) -> None:
        # The drain ends only once the watchdog kills; a fail-open watchdog never does.
        drain, release = self._drain_after_breach(("o", "e"))
        with self.assertRaises(boundedrun_memory.MemoryWatchFailed) as caught:
            self._drain(_ScriptedReader([]), lambda pid: release(), drain)
        error = caught.exception
        self.assertIsInstance(error, subprocess.SubprocessError)
        self.assertNotIsInstance(error, boundedrun_memory.MemoryLimitExceeded)
        self.assertIsInstance(error.__cause__, OSError)
        self.assertEqual((error.cmd, error.limit), (["argv"], 100))
        self.assertEqual((error.stdout, error.stderr), ("o", "e"))
        self.assertIsNone(error.kill_error)
        self.assertIn("100", str(error))

    def test_a_failed_read_whose_kill_failed_carries_the_timeouts_output(self) -> None:
        expired = subprocess.TimeoutExpired(["argv"], 1.0, output="o", stderr="e")

        def refuse(pid: int) -> None:
            release()
            raise ValueError("kill refused")

        drain, release = self._drain_after_breach(expired)
        with self.assertRaises(boundedrun_memory.MemoryWatchFailed) as caught:
            self._drain(_ScriptedReader([]), refuse, drain)
        self.assertEqual(caught.exception.output, "o")
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertIsInstance(caught.exception.kill_error, ValueError)

    def test_a_watchdog_that_cannot_start_abandons_the_child(self) -> None:
        drained: list[bool] = []

        def drain() -> tuple[str, str]:
            drained.append(True)
            return "o", "e"

        failure = RuntimeError("can't start new thread")
        with (
            mock.patch.object(threading.Thread, "start", side_effect=failure),
            self.assertRaises(RuntimeError) as caught,
        ):
            self._drain(_ScriptedReader([1]), print, drain)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self._abandoned, [None], "the child was not killed and reaped")
        self.assertEqual(drained, [])


if __name__ == "__main__":
    unittest.main()
