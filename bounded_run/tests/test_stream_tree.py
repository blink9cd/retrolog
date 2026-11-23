"""``stream_bounded``'s ending reaches the whole tree, and survives an interrupt mid-ending.

A grandchild left in the child's group after the shell exits is the shape a bound exists to
catch: leaving the stream early must kill it, and one holding stderr alone must hold the
stream to its deadline, as ``run_bounded`` holds a capture to it. An interrupt that lands
while the ending waits must still kill, reap and release the child (property 7). Each
grandchild records its pid, so the tests probe that it died rather than that the shell did.
"""

from __future__ import annotations

import _thread
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bounded_run
import boundedrun_stream

_MIB = 1024 * 1024

#: The allocator fixture: chunks of touched memory, one stdout line per chunk.
_ALLOC = Path(__file__).resolve().parent.parent / "samples" / "alloc_child.py"

#: A bound far past anything here should take, so a kill is told apart from the clock.
_GENEROUS_TIMEOUT = 60.0

#: A short bound, and the slack a measured run may take past what it states.
_SHORT_TIMEOUT, _SLACK = 1.0, 2.0

#: Seconds a grandchild would live unkilled: far past any kill here.
_SLEEP = 30

#: When the interrupt lands, after the body has handed the ending its wait.
_INTERRUPT_AFTER = 1.0

#: The platforms with a memory reader, and with ``/bin/sh``.
_READABLE = sys.platform in ("darwin", "linux")
_POSIX = sys.platform != "win32"


def _alive(pid: int) -> bool:
    """Whether *pid* still names a process this user may signal."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone(pid: int) -> bool:
    """Poll up to ten seconds for *pid* to disappear; whether it went."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@contextmanager
def _grandchild(redirect: str) -> Iterator[tuple[list[str], Path]]:
    """A shell that prints, starts a sleep with *redirect* in its group, records it, and exits.

    The sleep is killed after the test if it still runs, so a failing test leaks nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        pid_file = Path(tmp) / "grandchild.pid"
        script = f'echo hi; sleep {_SLEEP} {redirect} & echo $! > "{pid_file}"; exit 0'
        try:
            yield ["/bin/sh", "-c", script], pid_file
        finally:
            if pid_file.exists():
                pid = int(pid_file.read_text(encoding="utf-8"))
                if _alive(pid):
                    os.kill(pid, 9)


def _recorded(pid_file: Path) -> int:
    """The pid the shell recorded, waiting briefly for it to be written."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        text = pid_file.read_text(encoding="utf-8") if pid_file.exists() else ""
        if text.endswith("\n"):
            return int(text)
        time.sleep(0.02)
    raise AssertionError("the shell never recorded its grandchild")


def _kill_if_unreaped(stream: boundedrun_stream.BoundedStream) -> None:
    """Kill and reap a child a failed ending left behind; its pid is still its own."""
    child = stream._child()
    if child.returncode is None:
        child.kill()
        child.wait(timeout=10.0)


@unittest.skipUnless(_POSIX, "the probes run a POSIX shell")
class GrandchildTests(unittest.TestCase):
    """The ending kills the group, not only its leader."""

    def test_leaving_early_kills_a_grandchild_left_in_the_group(self) -> None:
        with _grandchild("") as (argv, pid_file):
            started = time.monotonic()
            with bounded_run.stream_bounded(argv, timeout=_GENEROUS_TIMEOUT) as stream:
                self.assertEqual(stream.read(3), b"hi\n")
                grandchild = _recorded(pid_file)
            self.assertLess(time.monotonic() - started, _SLACK)
            self.assertTrue(_gone(grandchild), "the grandchild outlived the stream")

    def test_a_grandchild_holding_stderr_holds_the_stream_to_its_deadline(self) -> None:
        # stdout reaches its end when the shell exits; only the sleep keeps stderr open.
        with _grandchild(">/dev/null") as (argv, pid_file):
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                with bounded_run.stream_bounded(argv, timeout=_SHORT_TIMEOUT) as stream:
                    while stream.read(64):
                        pass
                    grandchild = _recorded(pid_file)
            self.assertLess(time.monotonic() - started, _SHORT_TIMEOUT + _SLACK)
            self.assertTrue(_gone(grandchild), "the grandchild outlived its deadline")


@unittest.skipUnless(_READABLE, "the interrupted ending stops a memory watchdog too")
class InterruptedEndingTests(unittest.TestCase):
    """An interrupt mid-ending kills, reaps and releases the child once, then propagates."""

    def test_an_interrupt_during_the_end_wait_kills_reaps_and_releases(self) -> None:
        # interrupt_main does nothing while SIGINT is ignored, and a process inherits that from
        # a non-interactive shell's `&` (Python then installs no handler of its own): the end
        # wait ran on to the child's own exit and nothing was raised. So the handler is set.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        code = f"import os, time; os.close(1); time.sleep({_SLEEP})"
        timer = threading.Timer(_INTERRUPT_AFTER, _thread.interrupt_main)
        started = time.monotonic()
        with self.assertRaises(KeyboardInterrupt):
            with bounded_run.stream_bounded(
                [sys.executable, "-c", code],
                timeout=_GENEROUS_TIMEOUT,
                memory_limit=1024 * _MIB,
            ) as stream:
                self.addCleanup(_kill_if_unreaped, stream)
                self.assertEqual(stream.read(1), b"")
                timer.start()
        timer.join(timeout=_SLACK)
        # Room for the kill's two bounded waits (the reap and the drain), so a throttled host
        # (a launchd agent with light resource limits, say) passes, yet
        # well short of the child's own exit: a run that waited for that, or chained more
        # waits than the kill path has, still fails.
        bound = _INTERRUPT_AFTER + 2 * boundedrun_stream.KILL_GRACE_SECONDS + _SLACK
        assert (
            bound < _SLEEP
        )  # the bound must still tell the kill from the child's exit
        self.assertLess(time.monotonic() - started, bound)
        self.assertIsNotNone(stream.returncode, "the child was not reaped")
        self.assertNotIn(stream.pid, bounded_run.live_pids())
        self.assertEqual(bounded_run._JOBS, {})
        assert stream._watchdog is not None  # memory_limit= starts one
        self.assertFalse(stream._watchdog._thread.is_alive())

    def test_an_interrupt_after_the_tree_is_gone_kills_nothing(self) -> None:
        # The leader is reaped and its pipes drained: no tree is left, and its pid may be
        # reused, so the interrupt's path must not signal it.
        killed: list[int] = []
        settle = boundedrun_stream.BoundedStream._settle

        def settle_then_interrupt(
            self: boundedrun_stream.BoundedStream,
            proc: subprocess.Popen[bytes],
            *,
            kill: bool,
        ) -> None:
            settle(self, proc, kill=kill)
            assert self._drain is not None  # started on entry
            self._drain.join(timeout=10.0)
            raise KeyboardInterrupt

        with (
            mock.patch.object(
                boundedrun_stream.BoundedStream, "_settle", settle_then_interrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            with bounded_run.stream_bounded(
                [sys.executable, "-c", "pass"],
                timeout=_GENEROUS_TIMEOUT,
                kill_tree_fn=killed.append,
                memory_limit=1024 * _MIB,
            ) as stream:
                self.assertEqual(stream.read(1), b"")
        self.assertEqual(killed, [])
        self.assertEqual(stream.returncode, 0)
        self.assertNotIn(stream.pid, bounded_run.live_pids())


@unittest.skipUnless(_READABLE, "a memory reader exists on macOS and Linux only")
class BreachThenEarlyExitTests(unittest.TestCase):
    def test_a_breach_before_an_early_exit_still_raises_the_breach(self) -> None:
        argv = [sys.executable, str(_ALLOC), "--total-mib", "2048"]
        with self.assertRaises(bounded_run.MemoryLimitExceeded):
            with bounded_run.stream_bounded(
                argv + ["--hold-seconds", "60"],
                timeout=_GENEROUS_TIMEOUT,
                memory_limit=256 * _MIB,
            ) as stream:
                self.assertTrue(stream.read(1))
                watchdog = stream._watchdog
                assert watchdog is not None  # memory_limit= starts one
                deadline = time.monotonic() + _GENEROUS_TIMEOUT / 2
                while not watchdog.breached and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(watchdog.breached)
                # Left here, before the end of the stream, with nothing raised.


if __name__ == "__main__":
    unittest.main()
