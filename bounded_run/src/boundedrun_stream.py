"""Stream a ``bounded_run`` child's stdout as it comes, under the same tree kill and bounds.

Private to ``bounded_run``: it sits in that tool's own ``src/`` folder, so nothing outside the
tool folder imports it. Its name carries the tool's prefix, so it cannot shadow another tool's
module on ``sys.path``. ``bounded_run.stream_bounded`` is its public face.

Why a stream needs its own shape
--------------------------------
``run_bounded`` holds the whole output until the child ends, which a caller reading a decoder's
raw frames cannot afford. A stream hands the caller the stdout pipe instead, so the bounds must
hold while the caller, not this module, does the reading. A blocking pipe read takes no timeout,
so a deadline thread kills the tree when the timeout passes, which ends the read with EOF; the
memory watchdog kills it the same way on a breach. A drain thread reads stderr as the child
writes it, keeping only the last :data:`STDERR_TAIL_BYTES`, so a child writing a lot there never
blocks on a full pipe, and an unbounded stderr cannot grow this process either.

How a stream ends
-----------------
:class:`BoundedStream` is a context manager, and nothing starts until it is entered. Leaving it
after reading to the end waits, in what is left of the timeout, for the child's exit and for
stderr to close, which a descendant left behind can hold open; past the deadline the tree is
killed and the run is timed out, as ``run_bounded``'s is. Leaving it any other way, by stopping
early or by raising, kills the whole tree at once, a descendant whose leader already exited
included. Either way the leader is reaped, the watchdog stopped and the job released before the
``with`` statement ends, an interrupt landing mid-ending included: it kills and reaps first.
Only a body that raised nothing then sees the outcome raised: the breach first, as
``MemoryLimitExceeded`` or ``MemoryWatchFailed``, else ``subprocess.TimeoutExpired`` for a run
past its deadline or one whose kill left it unreaped. A caller's own exception always wins.

The timeout is measured on ``time.monotonic()``, which stops while a Mac sleeps, as
``run_bounded`` without ``clock=`` does.

Entry point assumptions
-----------------------
Imported by ``bounded_run`` alone, with this ``src/`` already on ``sys.path``. Standard library
and this ``src/``'s siblings only.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import NamedTuple

import boundedrun_live
import boundedrun_memory
from boundedrun_drain import KILL_GRACE_SECONDS as KILL_GRACE_SECONDS
from boundedrun_drain import KillTreeFn

#: The stderr bytes a stream keeps: the tail, for an error message, never the whole of it.
STDERR_TAIL_BYTES = 64 * 1024

#: How much the stderr drain reads at a time: one pipe buffer's worth.
_READ_BYTES = 64 * 1024


class StreamSpawn(NamedTuple):
    """How ``bounded_run`` starts and contains a stream's child, decided before it spawns.

    *contain* puts the started child in its tree (a Windows job; nothing on POSIX, where the
    process group is the tree), and *release* forgets that containment by pid.
    """

    cwd: Path | str | None
    env: Mapping[str, str] | None
    creationflags: int
    contain: Callable[[subprocess.Popen[bytes]], None]
    release: Callable[[int], None]


def _started(target: Callable[[], None], name: str) -> threading.Thread:
    """A daemon thread running *target*, already started."""
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread


class BoundedStream:
    """A child whose stdout the caller reads, bounded as ``run_bounded``'s children are.

    Use it as a context manager, once. :meth:`read` reads stdout; after the ``with`` statement
    ``returncode`` is the exit status (``None`` if a failed kill left the child unreaped) and
    ``stderr`` the last :data:`STDERR_TAIL_BYTES` of its stderr.
    """

    def __init__(
        self,
        argv: Sequence[str],
        timeout: float,
        kill_tree_fn: KillTreeFn,
        memory: boundedrun_memory.MemoryBound | None,
        spawn: StreamSpawn,
    ) -> None:
        """Hold a validated call; nothing is spawned until the stream is entered."""
        self._argv = list(argv)
        self._timeout = timeout
        self._kill_tree_fn = kill_tree_fn
        self._memory = memory
        self._spawn = spawn
        self._proc: subprocess.Popen[bytes] | None = None
        self._entered = False
        self._eof = False
        self._deadline = 0.0
        self._stopped = threading.Event()
        self._kill_lock = threading.Lock()
        self._guard: threading.Thread | None = None
        self._drain: threading.Thread | None = None
        self._watchdog: boundedrun_memory.MemoryWatchdog | None = None
        self._tail = bytearray()
        self._timed_out = False
        self._unreaped = False
        self._kill_error: Exception | None = None
        self.returncode: int | None = None

    @property
    def pid(self) -> int:
        """The child's pid; RuntimeError before the stream is entered."""
        return self._child().pid

    @property
    def stderr(self) -> bytes:
        """The tail of what the child wrote to stderr, so far."""
        return bytes(self._tail)

    def read(self, size: int) -> bytes:
        """Up to *size* bytes of stdout, blocking until that many or EOF; ``b""`` at the end.

        Fewer than *size* bytes means the stream has ended. A *size* below 1 is a ValueError,
        since an empty read could not be told from the end.
        """
        if size < 1:
            raise ValueError(f"a read must ask for at least one byte, not {size}")
        stdout = self._child().stdout
        assert stdout is not None  # set by stdout=PIPE in __enter__
        data = stdout.read(size)
        if len(data) < size:
            self._eof = True
        return data

    def _child(self) -> subprocess.Popen[bytes]:
        """The started child; RuntimeError before the stream is entered."""
        if self._proc is None:
            raise RuntimeError("enter the stream first: nothing has been started")
        return self._proc

    def __enter__(self) -> BoundedStream:
        """Start the child, register and contain it, then start its watchers.

        ``Popen`` and the registration are consecutive statements in this one frame, as in
        ``boundedrun_spawn``, so no frame return falls between them. Anything that fails after
        ``Popen`` returned, an interrupt included, kills and reaps the child before it propagates.
        """
        if self._entered:
            raise RuntimeError("a stream is entered once")
        self._entered = True
        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(
                self._argv,
                cwd=self._spawn.cwd,
                env=self._spawn.env,
                # Never the caller's stdin: a child reading it would eat the caller's own.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                creationflags=self._spawn.creationflags,
            )
            boundedrun_live.track(proc.pid)
            self._proc = proc
            self._spawn.contain(proc)
            self._start_watchers(proc)
        except BaseException:
            if proc is not None:
                self._proc = proc
                self._end(kill=True)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End the child (see the module docstring), then raise its outcome if the body did not."""
        self._end(kill=exc_type is not None or not self._eof)
        if exc_type is None:
            self._raise_outcome()

    def _start_watchers(self, proc: subprocess.Popen[bytes]) -> None:
        """Start the memory watchdog, if bounded, then the stderr drain and the deadline."""
        self._deadline = time.monotonic() + self._timeout
        if self._memory is not None:
            watchdog = boundedrun_memory.MemoryWatchdog(
                proc.pid,
                self._memory.limit,
                self._memory.reader,
                self._kill_tree_fn,
                unreaped=lambda: proc.returncode is None,
                observer=self._memory.observer,
            )
            watchdog.start()
            self._watchdog = watchdog
        self._drain = _started(self._read_stderr, "bounded_run stream stderr")
        self._guard = _started(self._guard_deadline, "bounded_run stream deadline")

    def _read_stderr(self) -> None:
        """Read stderr to its end, keeping the tail; the drain thread's body, which closes it."""
        pipe = self._child().stderr
        assert pipe is not None  # set by stderr=PIPE in __enter__
        with pipe:
            while chunk := pipe.read(_READ_BYTES):
                self._tail += chunk
                del self._tail[:-STDERR_TAIL_BYTES]

    def _remaining(self) -> float:
        """Seconds left before the deadline, never below 0."""
        return max(0.0, self._deadline - time.monotonic())

    def _guard_deadline(self) -> None:
        """Kill the tree as timed out once the deadline passes, unless the stream ended first."""
        if self._stopped.wait(self._remaining()):
            return
        with self._kill_lock:
            # Checked under the lock the ending takes, so no kill begins once it has begun.
            if self._stopped.is_set():
                return
            self._timed_out = True
            self._kill(self._child())

    def _kill(self, proc: subprocess.Popen[bytes]) -> None:
        """Kill *proc*'s tree; should that raise, keep the error and kill the child alone.

        The child's own death closes its end of the pipe, which ends a read a grandchild is
        not also holding open; the kept error becomes the cause of the TimeoutExpired raised.
        """
        try:
            self._kill_tree_fn(proc.pid)
        except Exception as error:  # kept, and raised as the outcome's cause
            self._kill_error = error
            proc.kill()

    def _end(self, *, kill: bool) -> None:
        """Stop the deadline, settle the tree, and release the child exactly once.

        Anything raised while settling, an interrupt above all (``bounded_run.md``,
        property 7), kills the tree if any of it may still run, reaps the leader under its
        bound, and propagates; the release runs however the ending ends.
        """
        proc = self._child()
        with self._kill_lock:
            self._stopped.set()
        try:
            self._settle(proc, kill=kill)
        except BaseException:
            if self._tree_may_live(proc):
                self._kill(proc)
            self._reap(proc)
            raise
        finally:
            self._release(proc)

    def _settle(self, proc: subprocess.Popen[bytes], *, kill: bool) -> None:
        """Kill the tree at once, or wait for it to end within the deadline; then reap.

        The kill is unconditional: the leader is not yet reaped, so its group id is still its
        own, and a descendant left in the group dies even when the leader already exited.
        A tree the deadline killed is not killed again: ``_timed_out`` is final here, since
        ``_end`` set the stop under the kill lock, so that kill is already complete. Its
        leader may still be exiting, and the drain still reading stderr's end, which a look
        with no time left mistook for a live tree; the reap below gives it the grace instead.
        Unless that kill raised: it then killed the leader alone, so the ending still tries
        the tree, its last chance to reach a descendant.
        """
        if self._guard is not None:
            self._guard.join(timeout=KILL_GRACE_SECONDS)
        if self._timed_out and self._kill_error is None:
            pass  # the deadline's kill reached the tree; only the reap is left
        elif kill:
            self._kill(proc)
        else:
            self._await_tree(proc)
        self._reap(proc)

    def _await_tree(self, proc: subprocess.Popen[bytes]) -> None:
        """Wait, within what is left of the deadline, for the leader and every stderr holder.

        stdout has already ended, so only stderr can still be held, by a descendant the
        leader left behind. Like ``communicate``, the run lasts until both pipes close; past
        the deadline it is timed out and the tree killed. A descendant that holds neither
        pipe is out of reach here, as it is for ``run_bounded``.
        """
        drain = self._drain
        assert drain is not None  # started on entry, and only an entered stream waits
        try:
            proc.wait(timeout=self._remaining())
            drain.join(timeout=self._remaining())
        except subprocess.TimeoutExpired:
            pass  # the leader outlived the deadline; the check below kills it
        if proc.returncode is None or drain.is_alive():
            self._timed_out = True
            self._kill(proc)

    def _tree_may_live(self, proc: subprocess.Popen[bytes]) -> bool:
        """Whether a kill could still reach a process: an unreaped leader, or a pipe holder.

        A reaped leader whose pipes are drained leaves no tree, and its pid may be reused.
        """
        holding = self._drain is not None and self._drain.is_alive()
        return proc.returncode is None or holding

    def _reap(self, proc: subprocess.Popen[bytes]) -> None:
        """Reap the leader under ``KILL_GRACE_SECONDS``; past it, record it as unreaped."""
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._unreaped = True  # the kill did not work; raised as a timeout, unread

    def _release(self, proc: subprocess.Popen[bytes]) -> None:
        """Stop the watchdog and the drain, close stdout, and forget the child's pid and job."""
        if self._watchdog is not None:
            self._watchdog.stop()
        assert (
            proc.stdout is not None and proc.stderr is not None
        )  # both piped in __enter__
        if self._drain is not None:
            self._drain.join(timeout=KILL_GRACE_SECONDS)
        else:
            proc.stderr.close()  # no drain started, so none will close it
        proc.stdout.close()
        self._spawn.release(proc.pid)
        boundedrun_live.forget(proc.pid)
        self.returncode = proc.returncode

    def _raise_outcome(self) -> None:
        """Raise the breach, else the timeout, that ended the child; nothing for a clean run."""
        if self._watchdog is not None and self._memory is not None:
            verdict = boundedrun_memory._verdict(
                self._watchdog, self._argv, self._memory.limit, None, self.stderr
            )
            if verdict is not None:
                raise verdict from self._watchdog.error
        if self._timed_out or self._unreaped:
            raise subprocess.TimeoutExpired(
                self._argv, self._timeout, stderr=self.stderr
            ) from self._kill_error
