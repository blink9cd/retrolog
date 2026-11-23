"""``stream_bounded``: a child's stdout read as it comes, under the timeout and memory bound.

The live classes run real interpreters and shells: the tree kill, the deadline and the memory
watchdog are the module's own. A child that holds its stdout open past its bound would hang
the read for good, so every test that expects a kill also asserts how long it took. Windows
refuses ``memory_limit=`` before spawning, which ``RefusalTests`` pins on every host.
"""

from __future__ import annotations

import os
import subprocess
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
import boundedrun_stream

_MIB = 1024 * 1024

#: The allocator fixture: chunks of touched memory, one stdout line per chunk.
_ALLOC = Path(__file__).resolve().parent.parent / "samples" / "alloc_child.py"

#: A bound far past anything here should take, so a kill is told apart from the clock.
_GENEROUS_TIMEOUT = 120.0

#: A bound a sleeping child outlives, and the slack its measured run may take past it.
_SHORT_TIMEOUT, _SLACK = 0.5, 3.0

#: Seconds a child left to sleep would hold its pipe: far past any kill here.
_SLEEP = 30

#: The platforms with a memory reader.
_READABLE = sys.platform in ("darwin", "linux")


def _python(code: str) -> list[str]:
    """This interpreter, running *code*."""
    return [sys.executable, "-c", code]


def _gone(pid: int) -> bool:
    """Poll up to ten seconds for *pid* to be reaped or gone; whether it went."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _kill_and_reap(stream: bounded_run.BoundedStream) -> int:
    """Kill the tree a failed kill left behind, and reap its leader; the exit status.

    The stream never reaped it, so the leader stays a zombie of this process until reaped,
    through the stream's own child: the only handle that can.
    """
    bounded_run.kill_tree(stream.pid)
    return stream._child().wait(timeout=10.0)


class StreamTests(unittest.TestCase):
    """What a caller reads, and what the stream records once it ends."""

    def test_stdout_is_read_while_the_child_still_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            go = Path(tmp) / "go"
            code = (
                "import pathlib, sys, time; out = sys.stdout.buffer; "
                "out.write(b'abc'); out.flush(); "
                f"p = pathlib.Path({str(go)!r}); "
                "[time.sleep(0.01) for _ in iter(lambda: p.exists(), True)]; "
                "out.write(b'def')"
            )
            with bounded_run.stream_bounded(
                _python(code), timeout=_GENEROUS_TIMEOUT
            ) as stream:
                # The child waits for the file, so this read can only be incremental.
                self.assertEqual(stream.read(3), b"abc")
                self.assertIn(stream.pid, bounded_run.live_pids())
                go.touch()
                self.assertEqual(stream.read(3), b"def")
                self.assertEqual(stream.read(3), b"")
        self.assertEqual(stream.returncode, 0)
        self.assertNotIn(stream.pid, bounded_run.live_pids())

    def test_a_short_read_is_the_end_of_the_stream(self) -> None:
        code = "import sys; sys.stdout.buffer.write(b'abcdefg')"
        with bounded_run.stream_bounded(
            _python(code), timeout=_GENEROUS_TIMEOUT
        ) as stream:
            self.assertEqual(
                [stream.read(3), stream.read(3), stream.read(3)],
                [b"abc", b"def", b"g"],
            )
        self.assertEqual(stream.returncode, 0)

    def test_a_flood_on_stderr_never_blocks_and_its_tail_is_kept(self) -> None:
        code = (
            "import sys; sys.stderr.write('x' * (1 << 20) + 'END'); "
            "sys.stderr.flush(); sys.stdout.buffer.write(b'abc'); sys.exit(3)"
        )
        with bounded_run.stream_bounded(
            _python(code), timeout=_GENEROUS_TIMEOUT
        ) as stream:
            self.assertEqual(stream.read(3), b"abc")
            self.assertEqual(stream.read(3), b"")
        self.assertEqual(stream.returncode, 3)
        self.assertTrue(stream.stderr.endswith(b"END"))
        self.assertEqual(len(stream.stderr), boundedrun_stream.STDERR_TAIL_BYTES)

    def test_the_child_reads_an_empty_stdin(self) -> None:
        code = "import sys; sys.stdout.write(str(len(sys.stdin.buffer.read())))"
        with bounded_run.stream_bounded(
            _python(code), timeout=_GENEROUS_TIMEOUT
        ) as stream:
            self.assertEqual(stream.read(8), b"0")

    def test_a_read_must_ask_for_at_least_one_byte(self) -> None:
        with bounded_run.stream_bounded(
            _python("pass"), timeout=_GENEROUS_TIMEOUT
        ) as stream:
            for size in (0, -1):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    stream.read(size)
            self.assertEqual(stream.read(1), b"")

    def test_a_stream_is_entered_once(self) -> None:
        stream = bounded_run.stream_bounded(_python("pass"), timeout=_GENEROUS_TIMEOUT)
        with stream:
            self.assertEqual(stream.read(1), b"")
        with self.assertRaises(RuntimeError):
            stream.__enter__()

    def test_nothing_starts_until_the_stream_is_entered(self) -> None:
        with mock.patch.object(subprocess, "Popen") as popen:
            stream = bounded_run.stream_bounded(
                _python("pass"), timeout=_GENEROUS_TIMEOUT
            )
        popen.assert_not_called()
        with self.assertRaises(RuntimeError):
            stream.read(1)
        with self.assertRaises(RuntimeError):
            _ = stream.pid

    def test_a_program_that_cannot_start_raises_and_leaves_nothing_tracked(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "no-such-program")
            with self.assertRaises(FileNotFoundError):
                with bounded_run.stream_bounded([missing], timeout=_GENEROUS_TIMEOUT):
                    self.fail("the body ran with no child")
        self.assertEqual(bounded_run.live_pids(), ())


class TimeoutTests(unittest.TestCase):
    """Past its deadline the whole tree is killed, and the stream raises TimeoutExpired."""

    def _assert_times_out(self, argv: list[str]) -> subprocess.TimeoutExpired:
        """Read *argv* to its end under the short bound; assert it raised in time; the error."""
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            with bounded_run.stream_bounded(argv, timeout=_SHORT_TIMEOUT) as stream:
                while stream.read(64):
                    pass
        self.assertLess(time.monotonic() - started, _SHORT_TIMEOUT + _SLACK)
        self.assertEqual(caught.exception.cmd, argv)
        self.assertEqual(caught.exception.timeout, _SHORT_TIMEOUT)
        self.assertTrue(_gone(stream.pid), "the child outlived its deadline")
        return caught.exception

    def test_a_silent_child_is_killed_at_its_deadline(self) -> None:
        self._assert_times_out(_python(f"import time; time.sleep({_SLEEP})"))

    def test_a_grandchild_holding_stdout_is_killed_too(self) -> None:
        # The shell exits at once; only the sleep, in its group, holds the pipe open. It
        # records its pid, so the probe is that the sleep died, not only the shell.
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "sleep.pid"
            script = f'sleep {_SLEEP} & echo $! > "{pid_file}"; exit 0'
            self._assert_times_out(["/bin/sh", "-c", script])
            self.assertTrue(_gone(int(pid_file.read_text(encoding="utf-8"))))

    def test_a_child_that_closed_stdout_is_still_held_to_the_deadline(self) -> None:
        code = f"import os, time; os.close(1); time.sleep({_SLEEP})"
        self._assert_times_out(_python(code))

    def test_a_deadline_kill_that_raises_falls_back_to_the_child_and_is_the_cause(
        self,
    ) -> None:
        failure = OSError("no process table")

        def refuse(_pid: int) -> None:
            raise failure

        argv = _python(f"import time; time.sleep({_SLEEP})")
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            with bounded_run.stream_bounded(
                argv, timeout=_SHORT_TIMEOUT, kill_tree_fn=refuse
            ) as stream:
                self.assertEqual(stream.read(64), b"")
        self.assertIs(caught.exception.__cause__, failure)
        self.assertTrue(_gone(stream.pid))


class _EndedAsTheDeadlinePassed:
    """An event whose wait timed out, though the stream ended before the lock was taken."""

    def wait(self, _timeout: float) -> bool:
        return False

    def is_set(self) -> bool:
        return True


class DeadlineRaceTests(unittest.TestCase):
    def test_a_deadline_that_loses_the_race_to_the_ending_kills_nothing(self) -> None:
        # The ending sets the event under the kill lock; a deadline that woke just before
        # must see it there, or it would kill a child the ending may already have reaped.
        killed: list[int] = []
        stream = bounded_run.stream_bounded(
            _python("pass"), timeout=_GENEROUS_TIMEOUT, kill_tree_fn=killed.append
        )
        stream._stopped = _EndedAsTheDeadlinePassed()  # type: ignore[assignment]  # the race
        stream._guard_deadline()
        self.assertEqual(killed, [])


class _DyingLeader:
    """A leader the deadline's kill reached but that has not exited yet: never reaped here.

    It has no ``kill``: a fallback kill of the leader alone would fail the test loudly.
    """

    pid = 4242
    returncode: int | None = None

    def wait(self, timeout: float) -> int:
        raise subprocess.TimeoutExpired("leader", timeout)


class DeadlineKillOnceTests(unittest.TestCase):
    """An ending that follows the deadline's kill reaps what it killed, and kills nothing more.

    Once the deadline has killed the tree, the leader may still be exiting and the drain still
    reading stderr's end when the ending looks. An ending that took either for a tree still
    alive killed it a second time, only when the dying outran its look: under a slowed host.
    """

    def _ended(self, *, kill: bool, failed: bool = False) -> list[int]:
        """The kills an ending makes after the deadline's, for a leader still exiting.

        *failed* makes the deadline's tree kill one that raised, so only the leader died.
        """
        killed: list[int] = []
        stream = bounded_run.stream_bounded(
            _python("pass"), timeout=_GENEROUS_TIMEOUT, kill_tree_fn=killed.append
        )
        holding = threading.Event()
        drain = threading.Thread(target=holding.wait, args=(_SLACK,), daemon=True)
        drain.start()
        self.addCleanup(drain.join, _SLACK)
        self.addCleanup(holding.set)
        stream._drain = drain  # stderr's end not yet read, as just after a kill
        stream._timed_out = True  # the deadline's kill, already made
        if failed:
            stream._kill_error = OSError("the deadline's tree kill failed")
        stream._settle(_DyingLeader(), kill=kill)  # type: ignore[arg-type]  # the race
        self.assertTrue(
            stream._unreaped, "the leader was never reaped, so it is recorded"
        )
        return killed

    def test_an_ending_read_to_the_end_does_not_kill_again(self) -> None:
        self.assertEqual(self._ended(kill=False), [])

    def test_an_ending_left_early_does_not_kill_again(self) -> None:
        self.assertEqual(self._ended(kill=True), [])

    def test_a_failed_deadline_kill_is_retried_by_either_ending(self) -> None:
        # The deadline's tree kill raised, so it killed the leader alone; a descendant may
        # still run, and the ending is the last chance to reach it.
        for kill in (False, True):
            with self.subTest(kill=kill):
                self.assertEqual(len(self._ended(kill=kill, failed=True)), 1)


class EarlyStopTests(unittest.TestCase):
    """A caller that stops reading, or raises, never leaves the child running."""

    _WRITE_THEN_SLEEP = _python(
        "import sys, time; sys.stdout.buffer.write(b'abc'); sys.stdout.flush(); "
        f"time.sleep({_SLEEP})"
    )

    def test_leaving_before_the_end_kills_the_child_without_raising(self) -> None:
        started = time.monotonic()
        with bounded_run.stream_bounded(
            self._WRITE_THEN_SLEEP, timeout=_GENEROUS_TIMEOUT
        ) as stream:
            self.assertEqual(stream.read(3), b"abc")
        self.assertLess(time.monotonic() - started, _SLACK)
        self.assertIsNotNone(stream.returncode)
        self.assertNotEqual(stream.returncode, 0)
        self.assertNotIn(stream.pid, bounded_run.live_pids())

    def test_a_caller_that_raises_kills_the_child_and_its_error_propagates(
        self,
    ) -> None:
        failure = LookupError("the caller's own")
        with self.assertRaises(LookupError) as caught:
            with bounded_run.stream_bounded(
                self._WRITE_THEN_SLEEP, timeout=_GENEROUS_TIMEOUT
            ) as stream:
                stream.read(3)
                raise failure
        self.assertIs(caught.exception, failure)
        self.assertIsNotNone(stream.returncode)

    def test_a_kill_that_leaves_the_child_running_raises_without_hanging(
        self,
    ) -> None:
        with (
            mock.patch.object(boundedrun_stream, "KILL_GRACE_SECONDS", 0.2),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            with bounded_run.stream_bounded(
                self._WRITE_THEN_SLEEP,
                timeout=_GENEROUS_TIMEOUT,
                kill_tree_fn=lambda _pid: None,
            ) as stream:
                stream.read(3)
        self.assertIsNone(stream.returncode)
        self.assertNotEqual(_kill_and_reap(stream), 0)

    def test_a_failed_reap_behind_the_callers_error_lets_that_error_through(
        self,
    ) -> None:
        with (
            mock.patch.object(boundedrun_stream, "KILL_GRACE_SECONDS", 0.2),
            self.assertRaises(LookupError),
        ):
            with bounded_run.stream_bounded(
                self._WRITE_THEN_SLEEP,
                timeout=_GENEROUS_TIMEOUT,
                kill_tree_fn=lambda _pid: None,
            ) as stream:
                raise LookupError
        self.assertNotEqual(_kill_and_reap(stream), 0)


@unittest.skipUnless(_READABLE, "a memory reader exists on macOS and Linux only")
class MemoryTests(unittest.TestCase):
    """A tree past its bound is killed, and the stream raises the breach, not a timeout."""

    def _alloc(self, total_mib: int, limit_mib: int) -> bounded_run.BoundedStream:
        """A stream over the allocator, read to its end."""
        argv = [sys.executable, str(_ALLOC), "--total-mib", str(total_mib)]
        with bounded_run.stream_bounded(
            argv + ["--hold-seconds", "60" if total_mib > limit_mib else "0"],
            timeout=_GENEROUS_TIMEOUT,
            memory_limit=limit_mib * _MIB,
        ) as stream:
            while stream.read(64):
                pass
        return stream

    def test_a_breach_kills_the_tree_and_raises_with_the_peak(self) -> None:
        started = time.monotonic()
        with self.assertRaises(bounded_run.MemoryLimitExceeded) as caught:
            self._alloc(2048, 256)
        self.assertLess(time.monotonic() - started, _GENEROUS_TIMEOUT / 2)
        self.assertGreater(caught.exception.peak, 256 * _MIB)
        self.assertLess(caught.exception.peak, 2048 * _MIB)
        self.assertEqual(caught.exception.limit, 256 * _MIB)

    def test_a_child_under_its_bound_streams_to_the_end(self) -> None:
        stream = self._alloc(128, 4096)
        self.assertEqual(stream.returncode, 0)

    def test_a_watchdog_that_cannot_start_kills_and_reaps_the_child(self) -> None:
        failure = RuntimeError("can't start new thread")
        seen: list[int] = []

        def refuse(_watchdog: object) -> None:
            seen.extend(bounded_run.live_pids())
            raise failure

        with (
            mock.patch.object(boundedrun_memory.MemoryWatchdog, "start", refuse),
            self.assertRaises(RuntimeError) as caught,
        ):
            with bounded_run.stream_bounded(
                ["/bin/sleep", str(_SLEEP)],
                timeout=_GENEROUS_TIMEOUT,
                memory_limit=256 * _MIB,
            ):
                self.fail("the body ran with no watchdog")
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(seen), 1)
        self.assertTrue(_gone(seen[0]), "the child survived")
        self.assertNotIn(seen[0], bounded_run.live_pids())

    def test_an_observer_sees_the_tree(self) -> None:
        observer = bounded_run.TreeObserver()
        with bounded_run.stream_bounded(
            _python("import time; time.sleep(0.3)"),
            timeout=_GENEROUS_TIMEOUT,
            memory_limit=1024 * _MIB,
            tree_observer=observer,
        ) as stream:
            self.assertEqual(stream.read(1), b"")
        self.assertGreaterEqual(observer.max_processes, 1)


class RefusalTests(unittest.TestCase):
    """A bad call is refused when the stream is made, before anything is spawned."""

    def _assert_refused(self, platform: str, **options: object) -> str:
        """Make a stream on *platform* with *options*; assert ValueError and no Popen."""
        with (
            mock.patch.object(sys, "platform", platform),
            mock.patch.object(subprocess, "Popen") as popen,
            self.assertRaises(ValueError) as caught,
        ):
            bounded_run.stream_bounded(["true"], timeout=1.0, **options)  # type: ignore[arg-type]  # the bad values are the point
        popen.assert_not_called()
        return str(caught.exception)

    def test_windows_refuses_a_memory_limit(self) -> None:
        message = self._assert_refused("win32", memory_limit=256 * _MIB)
        self.assertIn("Windows", message)

    def test_a_limit_that_is_not_a_positive_int_is_refused(self) -> None:
        for limit in (0, -1, 1.5, True):
            with self.subTest(limit=limit):
                self._assert_refused("darwin", memory_limit=limit)

    def test_an_observer_without_a_limit_is_refused(self) -> None:
        self._assert_refused("darwin", tree_observer=bounded_run.TreeObserver())

    def test_a_string_argv_is_refused(self) -> None:
        with self.assertRaises(TypeError):
            bounded_run.stream_bounded("ls -l", timeout=1.0)  # type: ignore[arg-type]  # the bad value is the point


if __name__ == "__main__":
    unittest.main()
