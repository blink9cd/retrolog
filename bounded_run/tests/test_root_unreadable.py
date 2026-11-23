"""A root the reader cannot see while it is still unreaped fails the watch, never reads as "gone".

The ``/proc`` reader reads a process as gone once its ``/proc/<pid>`` folder is gone. Pointed at
a ``/proc`` that does not hold the child (an empty directory, a mount of another pid namespace),
every read looked like a child that had already exited: the walk summed 0 bytes for a live
child, and the bound failed open. The root is the one process whose life the run knows
independently, through its own unreaped ``Popen``, so a root the reader cannot see while it is
unreaped is a read failure. A zombie root is not: Linux keeps its folder until it is reaped, and
macOS's libproc reader is never asked, since ``ESRCH`` there is the kernel's own answer and a
zombie root returns it too.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bounded_run
import boundedrun_memory
import boundedrun_memread
import boundedrun_rootwatch

_PAGE = 4096

#: How long a test waits on something the watchdog should do promptly.
_PROMPT_SECONDS = 10.0

#: Bound on a live run here: far past what each takes, so only a hang reaches it.
_LIVE_TIMEOUT = 60.0

#: A child that outlives its run's own bound, so only a kill ends it before that bound: one
#: that ended on its own first would leave the failed watch nothing to kill, which a slowed
#: watchdog once let happen to a five-second child, and the run then raised nothing.
_UNWATCHED_SLEEP = 2 * _LIVE_TIMEOUT

#: How long a throttled host really slept for one of the grace's short waits (measured).
_OVERSLEPT_WAIT_SECONDS = 0.2

#: A zombie's ``statm``: Linux keeps the file, with every field 0.
_ZOMBIE_STATM = "0 0 0 0 0 0 0\n"


def _lay_out_zombie(root: Path, pid: int) -> None:
    """Lay out ``<root>/<pid>`` as Linux shows an exited, unreaped process."""
    (root / str(pid) / "task" / str(pid)).mkdir(parents=True)
    (root / str(pid) / "statm").write_text(
        _ZOMBIE_STATM, encoding="utf-8", newline="\n"
    )
    (root / str(pid) / "stat").write_text(
        f"{pid} (x) Z 1 {pid} {pid}", encoding="utf-8", newline="\n"
    )


class _TempRoot(unittest.TestCase):
    """A fresh, empty directory standing in for ``/proc``."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)


class RootPresentTests(_TempRoot):
    def test_a_root_missing_from_the_proc_folder_is_not_present(self) -> None:
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertFalse(reader.root_present(os.getpid()))

    def test_a_zombie_root_is_present(self) -> None:
        _lay_out_zombie(self.root, 7)
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertTrue(reader.root_present(7))
        self.assertEqual(reader.footprint(7), 0)


class CheckRootTests(_TempRoot):
    """``check_root`` alone: when it raises, and when the reap race excuses a missing root."""

    def _never_stopped(self, seconds: float) -> bool:
        return False

    def test_a_reader_without_a_root_probe_is_never_checked(self) -> None:
        boundedrun_rootwatch.check_root(object(), 7, lambda: True, self._never_stopped)

    def test_an_unreaped_root_the_reader_cannot_see_raises(self) -> None:
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        ticks = iter(range(1_000))  # one second a look: the grace expires at once
        with self.assertRaises(OSError):
            boundedrun_rootwatch.check_root(
                reader,
                7,
                lambda: True,
                self._never_stopped,
                clock=lambda: float(next(ticks)),
            )

    def test_a_root_reaped_during_the_grace_is_not_a_failure(self) -> None:
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        answers = iter([True, True, False])
        boundedrun_rootwatch.check_root(
            reader, 7, lambda: next(answers), self._never_stopped
        )

    def test_a_stop_during_the_grace_is_not_a_failure(self) -> None:
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        boundedrun_rootwatch.check_root(reader, 7, lambda: True, lambda seconds: True)

    def test_a_present_root_is_not_a_failure(self) -> None:
        _lay_out_zombie(self.root, 7)
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        boundedrun_rootwatch.check_root(reader, 7, lambda: True, self._never_stopped)

    def test_the_grace_is_measured_on_the_clock_not_counted_in_waits(self) -> None:
        # A throttled process (a background-QoS sweep) sleeps far past each short wait: a
        # 5 ms wait measured near 200 ms. Counted in waits, the second-long grace grew 40-fold,
        # past the life of the child it should have failed, which then exited unwatched.
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        now = [0.0]
        waits: list[float] = []

        def oversleeping_wait(seconds: float) -> bool:
            waits.append(seconds)
            now[0] += _OVERSLEPT_WAIT_SECONDS
            return False

        with self.assertRaises(OSError):
            boundedrun_rootwatch.check_root(
                reader, 7, lambda: True, oversleeping_wait, clock=lambda: now[0]
            )
        grace = boundedrun_rootwatch.REAP_GRACE_SECONDS
        self.assertGreaterEqual(now[0], grace, "the grace was cut short")
        self.assertLess(now[0], grace + _OVERSLEPT_WAIT_SECONDS, "the grace overran")
        self.assertTrue(waits, "a missing root is given at least one wait")


class WatchdogRootTests(_TempRoot):
    """Through the watchdog: the existing kill and failed-watch path, and no false failure."""

    def test_an_empty_proc_kills_the_tree_and_records_a_read_error(self) -> None:
        killed: list[int] = []
        done = threading.Event()

        def kill(pid: int) -> None:
            killed.append(pid)
            done.set()

        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        watchdog = boundedrun_memory.MemoryWatchdog(
            os.getpid(), 1 << 30, reader, kill, interval=0.001
        )
        with mock.patch.object(boundedrun_rootwatch, "REAP_GRACE_SECONDS", 0.0):
            watchdog.start()
            self.assertTrue(done.wait(timeout=_PROMPT_SECONDS))
            watchdog.stop()
        self.assertEqual(killed, [os.getpid()])
        self.assertIsInstance(watchdog.error, OSError)
        self.assertFalse(watchdog.breached)

    def test_a_zombie_root_is_never_a_failed_watch(self) -> None:
        _lay_out_zombie(self.root, 7)
        killed: list[int] = []
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        watchdog = boundedrun_memory.MemoryWatchdog(
            7, 1 << 30, reader, killed.append, interval=0.001
        )
        watchdog.start()
        deadline = time.monotonic() + _PROMPT_SECONDS
        polls = 0
        while polls < 20 and time.monotonic() < deadline:
            time.sleep(0.005)
            polls += 1
        watchdog.stop()
        self.assertEqual(killed, [])
        self.assertIsNone(watchdog.error)


@unittest.skipIf(sys.platform == "win32", "memory_limit= is refused on Windows")
class EmptyProcRunTests(_TempRoot):
    """Through ``run_bounded``: a ``/proc`` that does not hold the child fails the watch."""

    def test_a_live_child_the_reader_cannot_see_raises_watch_failed(self) -> None:
        root = self.root

        def empty_procfs() -> boundedrun_memread.ProcfsReader:
            return boundedrun_memread.ProcfsReader(root, _PAGE)

        started = time.monotonic()
        with (
            mock.patch.dict(boundedrun_memory._READERS, {sys.platform: empty_procfs}),
            self.assertRaises(bounded_run.MemoryWatchFailed) as caught,
        ):
            bounded_run.run_bounded(
                [sys.executable, "-c", f"import time; time.sleep({_UNWATCHED_SLEEP})"],
                timeout=_LIVE_TIMEOUT,
                memory_limit=1 << 30,
            )
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertLess(
            time.monotonic() - started, _LIVE_TIMEOUT, "the child ran on unwatched"
        )


if __name__ == "__main__":
    unittest.main()
