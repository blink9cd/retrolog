"""Bound a ``bounded_run`` child's memory: a watchdog thread that kills its tree on a breach.

Private to ``bounded_run``: it sits in that tool's own ``src/`` folder, so nothing outside the
tool folder imports it. Its name carries the tool's prefix, so it cannot shadow another tool's
module on ``sys.path``.

Why a watchdog and not a resource limit
---------------------------------------
macOS refuses any lowered ``RLIMIT_DATA`` or ``RLIMIT_AS`` with ``EINVAL`` (measured on macOS
27.2), so the kernel holds no memory bound there at all. Linux would honor ``RLIMIT_AS``, but a
breach then makes ``malloc`` return NULL, and a tool that handles that by exiting cleanly
non-zero looks exactly like one that rejected its input. So both platforms use the same
parent-side watchdog: a thread polls the child's whole process tree every
:data:`POLL_SECONDS` and, once the sum passes the limit, kills the tree with the run's own tree
kill. The run then raises :class:`MemoryLimitExceeded`, never an ordinary result. A watchdog
whose read fails kills the tree too, at once, and the run raises :class:`MemoryWatchFailed`:
the bound fails closed, never open. So does a reader that cannot see the child at all while it
is unreaped (``boundedrun_rootwatch``).

What a poll cannot promise
--------------------------
The bound is checked between polls, so a child can overshoot it by what it allocates in one
interval: allocation rate times :data:`POLL_SECONDS`, plus the kill's own latency. Measured on
macOS with a 10 ms poll, a child capped at 512 MiB was killed at 535 MiB. Each poll walks the
same tree the kill reaches, parent links and process groups both, so a descendant whose parent
already exited is still counted while it stays in its group. The same walk counts the tree's
processes, which a :class:`TreeObserver` carries out of the run: a descendant that lived and
ended between two polls is never seen.

The walk and each platform's footprint reader live in ``boundedrun_memread``.

Entry point assumptions
-----------------------
Imported by ``bounded_run`` alone, with this ``src/`` already on ``sys.path``. Standard library
only.
"""

from __future__ import annotations

import mmap
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple, Optional, TypeVar, Union

from boundedrun_memread import (
    LibprocReader,
    ProcfsReader,
    TreeReader,
    bind_libproc,
    tree_usage,
)
from boundedrun_rootwatch import check_root

#: Seconds between two reads of the tree's footprint. The overshoot past the limit is about
#: the child's allocation rate times this; a shorter poll costs one tree walk per interval.
POLL_SECONDS = 0.02

#: Bound on each of stop's two waits, for a kill under way and for the thread: longer than a
#: whole tree kill (``bounded_run.KILL_TIMEOUT_SECONDS`` plus an overdue snapshot's reap
#: grace), which a test asserts rather than this restating, so stopping never cuts a kill
#: short and never hangs the run.
STOP_JOIN_SECONDS = 15.0

#: Where Linux mounts the process table.
_PROC_ROOT = Path("/proc")

#: The streams' type: str from ``run_bounded``, bytes from ``run_bounded_bytes``.
_Stream = TypeVar("_Stream", str, bytes)

#: A tree kill: pid -> None, the same seam the timeout path uses.
_KillTreeFn = Callable[[int], None]

#: Captured output as the two entry points give it.
_Output = Optional[Union[str, bytes]]


class MemoryLimitExceeded(subprocess.SubprocessError):
    """The child's process tree used more than *limit* bytes, and was killed for it.

    Carries the argv as *cmd*, the *limit*, the largest total the watchdog observed as *peak*
    (past the limit by up to one poll's allocation), and whatever output the run captured
    before the kill, as ``output``/``stdout`` and ``stderr``, the way ``TimeoutExpired`` does.
    """

    def __init__(
        self,
        cmd: Sequence[str],
        limit: int,
        peak: int,
        output: _Output = None,
        stderr: _Output = None,
    ) -> None:
        """Record the run that breached and what it left behind."""
        super().__init__(cmd, limit, peak)
        self.cmd = list(cmd)
        self.limit = limit
        self.peak = peak
        self.output = output
        self.stderr = stderr

    def __str__(self) -> str:
        """Name the command, the limit and the peak, in bytes."""
        return (
            f"Command '{self.cmd}' exceeded its memory limit of {self.limit} bytes "
            f"(peak {self.peak} bytes observed) and was killed"
        )

    @property
    def stdout(self) -> _Output:
        """The captured stdout: an alias for ``output``, as on ``TimeoutExpired``."""
        return self.output


class MemoryWatchFailed(subprocess.SubprocessError):
    """The watchdog could not read the child's tree, so it killed the tree rather than run blind.

    A sibling of :class:`MemoryLimitExceeded`, not a kind of it: no breach was observed. Its
    ``__cause__`` is the read error. Carries the argv as *cmd*, the *limit* that could not be
    held, the output captured before the kill as ``output``/``stdout`` and ``stderr``, and
    *kill_error*: the kill's own error when that kill raised too, else None.
    """

    def __init__(
        self,
        cmd: Sequence[str],
        limit: int,
        output: _Output = None,
        stderr: _Output = None,
        kill_error: Exception | None = None,
    ) -> None:
        """Record the run whose watch failed and what it left behind."""
        super().__init__(cmd, limit)
        self.cmd = list(cmd)
        self.limit = limit
        self.output = output
        self.stderr = stderr
        self.kill_error = kill_error

    def __str__(self) -> str:
        """Name the command and the limit that could not be held."""
        return (
            f"Command '{self.cmd}' was killed: its memory limit of {self.limit} bytes "
            "could not be watched (the tree's footprint could not be read)"
        )

    @property
    def stdout(self) -> _Output:
        """The captured stdout: an alias for ``output``, as on ``TimeoutExpired``."""
        return self.output


class TreeObserver:
    """What a watched run's watchdog saw of the child's tree, filled in as it polls.

    Pass one as ``tree_observer=`` beside ``memory_limit=``; after the run, returned or raised,
    ``max_processes`` is the most processes one poll found in the tree, the child included:
    0 before any poll, 1 for a child that ran alone, more once any descendant was seen. A
    descendant that started and ended between two polls is never seen.
    """

    def __init__(self) -> None:
        """Start having seen nothing."""
        self.max_processes = 0


class MemoryBound(NamedTuple):
    """A validated limit, the reader that measures it here, and any observer to fill in."""

    limit: int
    reader: TreeReader
    observer: TreeObserver | None = None


def _libproc_reader() -> TreeReader:
    """The macOS reader over the real ``libproc``."""
    return LibprocReader(bind_libproc())


def _procfs_reader() -> TreeReader:
    """The Linux reader over the real ``/proc``."""
    return ProcfsReader(_PROC_ROOT, mmap.PAGESIZE)


#: The reader each ``sys.platform`` value is watched with. Windows has none: see memory_bound.
_READERS: dict[str, Callable[[], TreeReader]] = {
    "darwin": _libproc_reader,
    "linux": _procfs_reader,
}


def memory_bound(
    limit: object, platform: str, observer: TreeObserver | None = None
) -> MemoryBound | None:
    """Validate *limit* for *platform* and pair it with a reader; None when no limit is given.

    Called before spawning, so every refusal leaves nothing running. A *limit* that is not a
    positive ``int`` of bytes raises ValueError (``bool`` included: ``True`` is not a size).
    So does Windows, where no reader exists yet (it needs a job-object memory cap, not built),
    any platform not in the reader table, and an *observer* without a limit: only the memory
    watchdog walks the tree, so it would report nothing seen.
    """
    if limit is None:
        if observer is not None:
            raise ValueError(
                "tree_observer= needs memory_limit=: only the memory watchdog walks the tree"
            )
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(f"memory_limit must be a positive int of bytes, not {limit!r}")
    if platform == "win32":
        raise ValueError(
            "memory_limit= is not supported on Windows yet: it needs a job-object "
            "memory cap, which is not built"
        )
    factory = _READERS.get(platform)
    if factory is None:
        raise ValueError(f"memory_limit= has no memory reader on {platform!r}")
    return MemoryBound(limit, factory(), observer)


class MemoryWatchdog:
    """A thread that polls *pid*'s tree and kills it once the sum passes *limit*.

    A read that raises kills the tree as well, at once: a watchdog that cannot read can no
    longer hold the bound, and letting the child run on unwatched would fail open. After
    :meth:`stop`, ``breached`` says whether a breach killed, ``peak`` is the largest sum it
    read, and ``error`` is whatever stopped it early: a breach's kill that raised (with
    ``breached`` set) or a read that raised (without). When the kill after a failed read
    raises too, that is ``kill_error``. The thread never raises; the caller decides.

    No kill begins once :meth:`stop` has begun, nor once *unreaped* says the child was reaped:
    both take one lock, and the kill checks under it. Once the run has ended its child may be
    reaped and its pid reused, so a kill that late could reach a stranger. Each poll's process
    count goes to *observer*, when one is given.
    """

    def __init__(
        self,
        pid: int,
        limit: int,
        reader: TreeReader,
        kill_tree_fn: _KillTreeFn,
        interval: float = POLL_SECONDS,
        *,
        unreaped: Callable[[], bool] = lambda: True,
        observer: TreeObserver | None = None,
    ) -> None:
        """Watch *pid* against *limit* through *reader*, every *interval* seconds."""
        self._pid = pid
        self._unreaped = unreaped
        self._observer = observer
        self._limit = limit
        self._reader = reader
        self._kill_tree_fn = kill_tree_fn
        self._interval = interval
        self._stopped = threading.Event()
        self._kill_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="bounded_run memory watchdog", daemon=True
        )
        self.breached = False
        self.peak = 0
        self.error: Exception | None = None
        self.kill_error: Exception | None = None

    def start(self) -> None:
        """Begin polling, reading the tree once at once."""
        self._thread.start()

    def stop(self) -> None:
        """Stop polling, so no kill begins after this, and wait, bounded, for the thread.

        A kill already under way holds the lock, so it is waited for, up to
        :data:`STOP_JOIN_SECONDS`. Past that the stop is recorded anyway: that kill has begun,
        and each watchdog begins at most one. Harmless twice.
        """
        acquired = self._kill_lock.acquire(timeout=STOP_JOIN_SECONDS)
        self._stopped.set()
        if acquired:
            self._kill_lock.release()
        self._thread.join(timeout=STOP_JOIN_SECONDS)

    def _run(self) -> None:
        """Poll until stopped or breached; keep any exception for the caller's thread."""
        try:
            self._watch()
        except (
            Exception
        ) as error:  # handed to drain_watched, which raises it on the run's thread
            self.error = error
            if not self.breached:
                self._kill_after_failed_read()

    def _kill_after_failed_read(self) -> None:
        """Kill the tree the watchdog can no longer read; keep a kill error for the caller."""
        try:
            self._kill_unless_stopped(breach=False)
        except Exception as error:  # the read error stays the cause; this rides along
            self.kill_error = error

    def _kill_unless_stopped(self, *, breach: bool) -> None:
        """Kill the tree, recording a *breach* first, unless the run has ended (see above)."""
        with self._kill_lock:
            if self._stopped.is_set() or not self._unreaped():
                return
            # Recorded before the kill, so the run sees it as soon as the drain returns.
            self.breached = breach
            self._kill_tree_fn(self._pid)

    def _watch(self) -> None:
        """Read the tree each interval; on a breach, kill, then end."""
        while True:
            total, processes = tree_usage(self._pid, self._reader, limit=self._limit)
            check_root(self._reader, self._pid, self._unreaped, self._stopped.wait)
            self.peak = max(self.peak, total)
            if self._observer is not None:
                self._observer.max_processes = max(
                    self._observer.max_processes, processes
                )
            if total > self._limit:
                self._kill_unless_stopped(breach=True)
                return
            if self._stopped.wait(self._interval):
                return


def _start_watchdog(
    watchdog: Callable[[], MemoryWatchdog], abandon: Callable[[], None]
) -> MemoryWatchdog:
    """Create and start a watchdog; if either fails, *abandon* the child before re-raising.

    The child is already running and registered, and nothing else would end it: the error is
    not an interrupt, so the spawn frame's guard lets it pass, and the drain never begins.
    """
    try:
        created = watchdog()
        created.start()
    except Exception:
        abandon()
        raise
    return created


def drain_watched(
    pid: int,
    argv: Sequence[str],
    limit: int,
    reader: TreeReader,
    kill_tree_fn: _KillTreeFn,
    drain: Callable[[], tuple[_Stream, _Stream]],
    abandon: Callable[[], None],
    interval: float = POLL_SECONDS,
    *,
    unreaped: Callable[[], bool] = lambda: True,
    observer: TreeObserver | None = None,
) -> tuple[_Stream, _Stream]:
    """Run *drain* while a :class:`MemoryWatchdog` watches *pid*'s tree; raise on a breach.

    A breach raises :class:`MemoryLimitExceeded` with the drained streams, whether the drain
    returned (the kill ended the child) or timed out (the kill failed, chained as the cause,
    and the timeout's partial output is carried instead). A read that failed raises
    :class:`MemoryWatchFailed` the same two ways, chained to the read error, since the
    watchdog killed the tree it could no longer bound. Otherwise the drain's outcome stands.
    A watchdog that cannot start calls *abandon* (kill the tree and reap the child) first.
    *unreaped* and *observer* go to the watchdog (:class:`MemoryWatchdog`).
    """
    watchdog = _start_watchdog(
        lambda: MemoryWatchdog(
            pid,
            limit,
            reader,
            kill_tree_fn,
            interval,
            unreaped=unreaped,
            observer=observer,
        ),
        abandon,
    )
    try:
        stdout, stderr = drain()
    except subprocess.TimeoutExpired as expired:
        watchdog.stop()
        verdict = _verdict(watchdog, argv, limit, expired.output, expired.stderr)
        if verdict is None:
            raise
        raise verdict from watchdog.error
    finally:
        watchdog.stop()
    verdict = _verdict(watchdog, argv, limit, stdout, stderr)
    if verdict is not None:
        raise verdict from watchdog.error
    return stdout, stderr


def _verdict(
    watchdog: MemoryWatchdog,
    argv: Sequence[str],
    limit: int,
    stdout: _Output,
    stderr: _Output,
) -> subprocess.SubprocessError | None:
    """What the stopped *watchdog* makes the run raise, with what it captured; None if nothing."""
    if watchdog.breached:
        return MemoryLimitExceeded(argv, limit, watchdog.peak, stdout, stderr)
    if watchdog.error is not None:
        return MemoryWatchFailed(argv, limit, stdout, stderr, watchdog.kill_error)
    return None
