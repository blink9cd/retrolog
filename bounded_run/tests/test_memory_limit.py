"""``memory_limit=``, end to end: a real child that allocates past its bound is killed and raises.

Nothing in the live classes is faked: the children are real interpreters allocating and touching
real memory, the footprint is read from the kernel, and the kill is the module's own tree kill.
The live classes run on macOS and Linux, the two platforms with a reader; Windows refuses the
option before spawning, which ``RefusalTests`` pins on every host.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bounded_run
import boundedrun_memory

_MIB = 1024 * 1024

#: The allocator fixture: chunks of touched memory, one stdout line per chunk.
_ALLOC = Path(__file__).resolve().parent.parent / "samples" / "alloc_child.py"

#: A bound far past anything here should take, so a breach is told apart from a timeout.
_GENEROUS_TIMEOUT = 120.0

#: The orphan test's bound: past it, the run raises TimeoutExpired, which is how a walk that
#: missed the orphan fails, so it is kept short of the generous one to fail promptly.
_ORPHAN_TIMEOUT = 30.0

#: How long a killed grandchild may take to disappear from the process table.
_GONE_SECONDS = 10.0

#: The platforms whose reader these live tests exercise.
_READABLE = sys.platform in ("darwin", "linux")


def _alloc_argv(*, total_mib: int, hold: float = 0.0, pid_file: str = "") -> list[str]:
    """The allocator, as a child argv, allocating *total_mib* then holding it *hold* seconds."""
    argv = [sys.executable, str(_ALLOC), "--total-mib", str(total_mib)]
    argv += ["--hold-seconds", str(hold)]
    return argv + (["--pid-file", pid_file] if pid_file else [])


def _alive(pid: int) -> bool:
    """Whether *pid* still names a process this user may signal."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _await_gone(pid: int) -> bool:
    """Poll until *pid* is gone or :data:`_GONE_SECONDS` pass; whether it went."""
    deadline = time.monotonic() + _GONE_SECONDS
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _runs(pid: int, marker: str) -> bool:
    """Whether *pid*'s command line still holds *marker*, rather than a process that reused it."""
    try:
        listed = bounded_run.run_bounded(
            ["ps", "-p", str(pid), "-o", "command="], timeout=_GONE_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return marker in listed.stdout


@contextlib.contextmanager
def _pid_files(count: int) -> Iterator[list[Path]]:
    """*count* pid-file paths; any allocator they name that is still alive is killed after.

    The pid is checked to still run the fixture first: one that already died may have been
    reused by an unrelated process, which must not be killed in its place.
    """
    with tempfile.TemporaryDirectory() as tmp:
        paths = [Path(tmp) / f"alloc{index}.pid" for index in range(count)]
        try:
            yield paths
        finally:
            for path in paths:
                with contextlib.suppress(OSError, ValueError):
                    pid = int(path.read_text(encoding="utf-8"))
                    if _runs(pid, str(_ALLOC)):
                        os.kill(pid, 9)


@unittest.skipUnless(_READABLE, "a memory reader exists on macOS and Linux only")
class LiveBreachTests(unittest.TestCase):
    """A child allocating past its bound is killed, and the call raises instead of returning."""

    def test_a_breach_raises_with_the_peak_and_partial_output(self) -> None:
        limit = 256 * _MIB
        started = time.monotonic()
        with (
            _pid_files(1) as (pid_file,),
            self.assertRaises(bounded_run.MemoryLimitExceeded) as caught,
        ):
            argv = _alloc_argv(total_mib=2048, hold=60.0, pid_file=str(pid_file))
            try:
                bounded_run.run_bounded(
                    argv, timeout=_GENEROUS_TIMEOUT, memory_limit=limit
                )
            finally:
                pid = int(pid_file.read_text(encoding="utf-8"))
                self.assertTrue(_await_gone(pid), "the allocator survived its breach")
        elapsed = time.monotonic() - started
        error = caught.exception
        self.assertIsInstance(error, subprocess.SubprocessError)
        self.assertNotIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual(error.limit, limit)
        self.assertGreater(error.peak, limit)
        self.assertLess(error.peak, 2048 * _MIB, "killed before it finished allocating")
        self.assertLess(
            elapsed, _GENEROUS_TIMEOUT / 2, "raised by the bound, not the clock"
        )
        self.assertIn("allocated", error.stdout or "")
        self.assertNotIn("done", error.stdout or "")
        self.assertEqual(error.cmd, argv)

    def test_the_bytes_entry_point_raises_the_same_way(self) -> None:
        with self.assertRaises(bounded_run.MemoryLimitExceeded) as caught:
            bounded_run.run_bounded_bytes(
                _alloc_argv(total_mib=2048),
                timeout=_GENEROUS_TIMEOUT,
                memory_limit=256 * _MIB,
            )
        self.assertGreater(caught.exception.peak, 256 * _MIB)
        self.assertIn(b"allocated", caught.exception.output or b"")

    def test_a_child_well_under_its_bound_completes_normally(self) -> None:
        result = bounded_run.run_bounded(
            _alloc_argv(total_mib=128),
            timeout=_GENEROUS_TIMEOUT,
            memory_limit=4096 * _MIB,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("done", result.stdout)

    def test_the_sum_over_a_tree_trips_the_bound_and_kills_every_member(self) -> None:
        # Two siblings under a shell, each holding 192 MiB: neither alone crosses 320 MiB,
        # so only a reader that sums the tree can raise here.
        with _pid_files(2) as pid_files:
            siblings = [
                shlex.join(_alloc_argv(total_mib=192, hold=60.0, pid_file=str(path)))
                for path in pid_files
            ]
            script = f"{siblings[0]} & {siblings[1]} & wait"
            with self.assertRaises(bounded_run.MemoryLimitExceeded) as caught:
                bounded_run.run_bounded(
                    ["/bin/sh", "-c", script],
                    timeout=_GENEROUS_TIMEOUT,
                    memory_limit=320 * _MIB,
                )
            self.assertGreater(caught.exception.peak, 320 * _MIB)
            pids = [int(path.read_text(encoding="utf-8")) for path in pid_files]
            self.assertTrue(all(_await_gone(pid) for pid in pids), "a sibling survived")

    def test_a_grandchild_orphaned_in_the_childs_group_is_counted_and_killed(
        self,
    ) -> None:
        # The subshell forks the allocator and exits, then the shell exits too: the allocator
        # is re-parented to init/launchd, so no parent link reaches it, but it stays in the
        # child's group and holds the pipes. Only a walk of that group can count it.
        with _pid_files(1) as (pid_file,):
            grandchild = shlex.join(
                _alloc_argv(total_mib=1024, hold=60.0, pid_file=str(pid_file))
            )
            with self.assertRaises(bounded_run.MemoryLimitExceeded) as caught:
                bounded_run.run_bounded(
                    ["/bin/sh", "-c", f"({grandchild} &); exit 0"],
                    timeout=_ORPHAN_TIMEOUT,
                    memory_limit=256 * _MIB,
                )
            self.assertGreater(caught.exception.peak, 256 * _MIB)
            pid = int(pid_file.read_text(encoding="utf-8"))
            self.assertTrue(_await_gone(pid), "the orphaned grandchild survived")

    def test_a_watchdog_that_cannot_start_kills_and_reaps_the_child(self) -> None:
        # The child would otherwise run on, unwatched and unregistered, after the call raised.
        failure = RuntimeError("can't start new thread")
        started: list[int] = []

        def refuse_to_start(watchdog: object) -> None:
            started.extend(bounded_run.live_pids())
            raise failure

        killed: list[int] = []

        def kill(pid: int) -> None:
            killed.append(pid)
            bounded_run.kill_tree(pid)

        with (
            mock.patch.object(
                boundedrun_memory.MemoryWatchdog, "start", refuse_to_start
            ),
            self.assertRaises(RuntimeError) as caught,
        ):
            bounded_run.run_bounded(
                ["/bin/sleep", "60"],
                timeout=_GENEROUS_TIMEOUT,
                kill_tree_fn=kill,
                memory_limit=256 * _MIB,
            )
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(started), 1)
        self.addCleanup(_kill_if_sleeping, started[0])
        self.assertEqual(killed, started)
        self.assertTrue(_await_gone(started[0]), "the child survived")


def _kill_if_sleeping(pid: int) -> None:
    """Kill *pid* if it still runs the test's ``sleep``; nothing if it is gone or reused."""
    if _runs(pid, "sleep 60"):
        os.kill(pid, 9)


class RefusalTests(unittest.TestCase):
    """Every bad memory_limit is refused before anything is spawned."""

    def _assert_refused_unspawned(self, limit: object, platform: str) -> str:
        """Run with *limit* on *platform*; assert ValueError and no Popen; the message."""
        with (
            mock.patch.object(sys, "platform", platform),
            mock.patch.object(subprocess, "Popen") as popen,
            self.assertRaises(ValueError) as caught,
        ):
            bounded_run.run_bounded(
                ["true"],
                timeout=1.0,
                memory_limit=limit,  # type: ignore[arg-type]  # the bad values are the point
            )
        popen.assert_not_called()
        return str(caught.exception)

    def test_zero_negative_and_non_int_limits_are_refused(self) -> None:
        for limit in (0, -1, 1.5, "1024", True):
            with self.subTest(limit=limit):
                self._assert_refused_unspawned(limit, "darwin")

    def test_windows_refuses_any_limit_and_names_what_is_missing(self) -> None:
        message = self._assert_refused_unspawned(256 * _MIB, "win32")
        self.assertIn("Windows", message)
        self.assertIn("job-object memory cap, which is not built", message)

    def test_a_platform_with_no_reader_refuses(self) -> None:
        message = self._assert_refused_unspawned(256 * _MIB, "sunos5")
        self.assertIn("sunos5", message)

    def test_the_bytes_entry_point_refuses_before_spawning(self) -> None:
        with (
            mock.patch.object(subprocess, "Popen") as popen,
            self.assertRaises(ValueError),
        ):
            bounded_run.run_bounded_bytes(["true"], timeout=1.0, memory_limit=0)
        popen.assert_not_called()


class NoLimitTests(unittest.TestCase):
    """Without memory_limit, no watchdog starts and nothing changes."""

    def test_no_watchdog_starts_without_a_limit(self) -> None:
        with mock.patch.object(boundedrun_memory, "MemoryWatchdog") as watchdog:
            result = bounded_run.run_bounded(
                [sys.executable, "-c", "print('hi')"], timeout=_GENEROUS_TIMEOUT
            )
        watchdog.assert_not_called()
        self.assertEqual(result.stdout, "hi\n")


if __name__ == "__main__":
    unittest.main()
