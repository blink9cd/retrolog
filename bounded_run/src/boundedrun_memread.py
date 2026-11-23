"""Read a ``bounded_run`` child's tree footprint: the tree walk and each platform's reader.

Private to ``bounded_run``: it sits in that tool's own ``src/`` folder, so nothing outside the
tool folder imports it. Its name carries the tool's prefix, so it cannot shadow another tool's
module on ``sys.path``. ``boundedrun_memory`` polls through it; the two sit apart to keep each
file to a reviewable length.

macOS reads ``phys_footprint`` (what Activity Monitor calls Memory) through ``libproc``; Linux
reads the resident set from ``/proc/<pid>/statm``. A process that is gone reads as 0, not as an
error: the tree is always racing its own members' exits. Gone means exactly that: ``ESRCH`` from
libproc, or a ``/proc/<pid>`` folder that no longer exists (for the run's own child, only
once it has been reaped: ``boundedrun_rootwatch``). Anything else that stops a read
raises ``OSError``, so the watchdog kills the tree rather than run blind. That includes a member
the reader may not inspect (``EPERM``: another uid, or a sandbox denying ``process-info*``),
which once read as gone and let the bound fail open.

Which processes are the tree
----------------------------
The same ones the tree kill reaches (``boundedrun_posix_tree.tree_groups``): every descendant
found through parent links, and every member of every process group one of them is in. The
group link is what reaches a descendant whose own parent exited, re-parented to init/launchd
but still in its group, which the kill would end and so the bound must count. Group ids below 2
(the caller's own, init's) are never followed, as the kill never signals them; the caller's own
group cannot be reached otherwise, since a group never spans two sessions and every child
starts a session of its own.

How long one walk may take
--------------------------
A child that forks faster than the walk reads would keep one walk from ever ending, and the
limit is only compared once a walk returns. So the walk compares its running total to the
limit as it goes, returning as soon as the total is over it (a breach needs no full sum), and
stops at :data:`MAX_WALK_PROCESSES` processes or :data:`MAX_WALK_SECONDS` on the monotonic
clock, raising :class:`TreeWalkOverCap`: an ``OSError``, so the watchdog fails closed and
kills the tree it can no longer measure.

Entry point assumptions
-----------------------
Imported by ``boundedrun_memory`` alone, with this ``src/`` already on ``sys.path``. Standard
library only. Each reader takes its source as an argument (the loaded library, the ``/proc``
root), so both run on every host against stand-ins; only binding the real ``libproc`` is
macOS's alone.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, Protocol

#: ``proc_pid_rusage``'s flavor for the oldest record, the one that already has phys_footprint.
RUSAGE_INFO_V0 = 0

#: How many child pids the first ``proc_listchildpids`` call has room for; a full buffer doubles.
_CHILD_CAPACITY = 64

#: Where macOS keeps libproc, should ``find_library`` not resolve it.
_LIBPROC_PATH = "/usr/lib/libproc.dylib"

#: ``proc_listpids``'s type for "every member of the group named by typeinfo".
PROC_PGRP_ONLY = 2

#: ``proc_pidinfo``'s flavor for ``struct proc_bsdshortinfo``, which holds the process group.
PROC_PIDTBSDSHORTINFO = 13

#: The lowest group id ever followed: 0 means the caller's own group to ``killpg``, and 1 is
#: init/launchd's. Neither is a descendant's, as ``boundedrun_posix_tree`` reasons too.
_MIN_GROUP = 2

#: The most processes one walk reads before it gives up and fails the watch. Far past any tree a
#: bounded tool should start, and short of what a fork bomb reaches between two polls.
MAX_WALK_PROCESSES = 10_000

#: The most seconds one walk may take before it fails the watch: two orders of magnitude past a
#: walk of a normal tree, even through a ``/proc`` scan per group on a loaded host.
MAX_WALK_SECONDS = 5.0

#: The libproc functions read here: (name, restype, argtypes).
LIBPROC_SIGNATURES = (
    ("proc_pid_rusage", ctypes.c_int, (ctypes.c_int, ctypes.c_int, ctypes.c_void_p)),
    ("proc_listchildpids", ctypes.c_int, (ctypes.c_int, ctypes.c_void_p, ctypes.c_int)),
    (
        "proc_pidinfo",
        ctypes.c_int,
        (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int),
    ),
    (
        "proc_listpids",
        ctypes.c_int,
        (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int),
    ),
)


class RusageInfoV0(ctypes.Structure):
    """``struct rusage_info_v0`` from ``<sys/resource.h>``: a uuid, then ten uint64 fields."""

    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
    ]


class ProcBsdShortInfo(ctypes.Structure):
    """``struct proc_bsdshortinfo`` from ``<sys/proc_info.h>``: ids, a 16-byte name, more ids."""

    _fields_ = [
        ("pbsi_pid", ctypes.c_uint32),
        ("pbsi_ppid", ctypes.c_uint32),
        ("pbsi_pgid", ctypes.c_uint32),
        ("pbsi_status", ctypes.c_uint32),
        ("pbsi_comm", ctypes.c_char * 16),
        ("pbsi_flags", ctypes.c_uint32),
        ("pbsi_uid", ctypes.c_uint32),
        ("pbsi_gid", ctypes.c_uint32),
        ("pbsi_ruid", ctypes.c_uint32),
        ("pbsi_rgid", ctypes.c_uint32),
        ("pbsi_svuid", ctypes.c_uint32),
        ("pbsi_svgid", ctypes.c_uint32),
        ("pbsi_rfu", ctypes.c_uint32),
    ]


class TreeReader(Protocol):
    """What the watchdog reads: footprints in bytes, and the two links that make a tree."""

    def footprint(self, pid: int) -> int:
        """Bytes *pid* uses now; 0 once it is gone."""

    def children(self, pid: int) -> list[int]:
        """The pids whose parent is *pid*; empty once it is gone."""

    def group(self, pid: int) -> int | None:
        """*pid*'s process group; None once it is gone or cannot be read."""

    def group_members(self, pgid: int) -> list[int]:
        """The pids in group *pgid*; empty once it has none."""


class TreeWalkOverCap(OSError):
    """One walk passed :data:`MAX_WALK_PROCESSES` or :data:`MAX_WALK_SECONDS`.

    An ``OSError``, as any other failed read, so the watchdog kills the tree and the run raises
    ``MemoryWatchFailed``: a tree too large or too fast to measure is never let run unbounded.
    """


class _TreeWalk:
    """One poll's walk: the pids and groups found so far, and what is left to visit."""

    def __init__(self, root: int) -> None:
        """Start from *root*, which leads its own group, as every ``run_bounded`` child does."""
        self.seen = {root}
        self.pending = [root]
        self.groups = {root}
        self.pending_groups = [root]

    def add_pid(self, pid: int) -> None:
        """Queue *pid* unless it was found already, by either link."""
        if pid not in self.seen:
            self.seen.add(pid)
            self.pending.append(pid)

    def add_group(self, pgid: int | None) -> None:
        """Queue *pgid*'s members unless it is unreadable, too low to follow, or found."""
        if pgid is not None and pgid >= _MIN_GROUP and pgid not in self.groups:
            self.groups.add(pgid)
            self.pending_groups.append(pgid)


class TreeUsage(NamedTuple):
    """One poll's reading of a tree: the bytes it uses, and how many processes it holds."""

    bytes: int
    processes: int


def _check_walk_caps(read: int, deadline: float, clock: Callable[[], float]) -> None:
    """Raise :class:`TreeWalkOverCap` once *read* processes or the *deadline* is passed."""
    if read > MAX_WALK_PROCESSES:
        raise TreeWalkOverCap(
            f"tree walk passed {MAX_WALK_PROCESSES} processes; the tree cannot be measured"
        )
    if clock() > deadline:
        raise TreeWalkOverCap(
            f"tree walk passed {MAX_WALK_SECONDS} seconds; the tree cannot be measured"
        )


def tree_usage(
    root: int,
    reader: TreeReader,
    *,
    limit: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> TreeUsage:
    """Bytes used by *root*'s tree, summed, and its processes: each counted once, however found.

    The tree is everything parent links reach from *root*, plus every member of every group
    any of those is in (the module docstring says why), repeated until nothing new is found.
    *root* itself always counts, so a lone child is one process. Once the running total is
    over *limit*, the walk returns at once with the total and the processes found so far.
    Past :data:`MAX_WALK_PROCESSES` processes read, or :data:`MAX_WALK_SECONDS` by *clock*,
    it raises :class:`TreeWalkOverCap`.
    """
    walk = _TreeWalk(root)
    deadline = clock() + MAX_WALK_SECONDS
    total = 0
    read = 0
    while walk.pending or walk.pending_groups:
        if walk.pending_groups:
            for member in reader.group_members(walk.pending_groups.pop()):
                walk.add_pid(member)
            continue
        read += 1
        _check_walk_caps(read, deadline, clock)
        pid = walk.pending.pop(0)
        total += reader.footprint(pid)
        if limit is not None and total > limit:
            break
        for child in reader.children(pid):
            walk.add_pid(child)
        walk.add_group(reader.group(pid))
    return TreeUsage(total, len(walk.seen))


def tree_footprint(root: int, reader: TreeReader) -> int:
    """Bytes used by *root*'s tree, summed (:func:`tree_usage`'s first half)."""
    return tree_usage(root, reader).bytes


def listing_failed(count: int, error: int) -> bool:
    """Whether a libproc listing that returned *count* with *error* in ``errno`` failed.

    A listing with entries stands. An empty one is a failure only when ``errno`` names
    something other than a pid that is gone: a sandbox's denial lists nothing with ``EPERM``.
    A negative count is a failure unless the pid is gone.
    """
    if count > 0:
        return False
    if count == 0:
        return error not in (0, errno.ESRCH)
    return error != errno.ESRCH


def _read_error(call: str, pid: int, error: int) -> OSError:
    """The error a libproc *call* on *pid* that failed with *error* raises."""
    return OSError(error, f"{call}({pid}) failed: {os.strerror(error)}")


class LibprocReader:
    """The macOS reader, over a loaded ``libproc`` whose functions match the signature table.

    ``errno`` is cleared before each call and read after it, through *get_errno* and
    *set_errno* (ctypes' own, over a library loaded with ``use_errno``), because only it tells
    a pid that is gone (``ESRCH``) from one the reader may not see.
    """

    def __init__(
        self,
        lib: ctypes.CDLL,
        initial_capacity: int = _CHILD_CAPACITY,
        get_errno: Callable[[], int] = ctypes.get_errno,
        set_errno: Callable[[int], int] = ctypes.set_errno,
    ) -> None:
        """Read through *lib*, listing children into a buffer of *initial_capacity* first."""
        self._lib = lib
        self._capacity = initial_capacity
        self._get_errno = get_errno
        self._set_errno = set_errno

    def footprint(self, pid: int) -> int:
        """*pid*'s ``phys_footprint``; 0 once it is gone, and OSError when it cannot be read."""
        info = RusageInfoV0()
        self._set_errno(0)
        status = self._lib.proc_pid_rusage(pid, RUSAGE_INFO_V0, ctypes.byref(info))
        if status == 0:
            return int(info.ri_phys_footprint)
        error = self._get_errno()
        if error == errno.ESRCH:
            return 0
        raise _read_error("proc_pid_rusage", pid, error)

    def children(self, pid: int) -> list[int]:
        """*pid*'s children. A full buffer may have been cut short, so it doubles and retries."""
        while True:
            buffer = (ctypes.c_int * self._capacity)()
            self._set_errno(0)
            count = self._lib.proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
            error = self._get_errno()
            if listing_failed(count, error):
                raise _read_error("proc_listchildpids", pid, error)
            if count < self._capacity:
                return list(buffer[: max(count, 0)])
            self._capacity *= 2

    def group(self, pid: int) -> int | None:
        """*pid*'s group from its BSD short info; None for a zombie or a pid that is gone."""
        info = ProcBsdShortInfo()
        size = ctypes.sizeof(info)
        self._set_errno(0)
        written = self._lib.proc_pidinfo(
            pid, PROC_PIDTBSDSHORTINFO, 0, ctypes.byref(info), size
        )
        if written == size:
            return int(info.pbsi_pgid)
        error = self._get_errno()
        if error == errno.ESRCH:
            return None
        raise _read_error("proc_pidinfo", pid, error)

    def group_members(self, pgid: int) -> list[int]:
        """Group *pgid*'s pids. ``proc_listpids`` counts bytes, and a full buffer doubles."""
        width = ctypes.sizeof(ctypes.c_int)
        while True:
            buffer = (ctypes.c_int * self._capacity)()
            self._set_errno(0)
            used = self._lib.proc_listpids(
                PROC_PGRP_ONLY, pgid, buffer, ctypes.sizeof(buffer)
            )
            error = self._get_errno()
            if listing_failed(used, error):
                raise _read_error("proc_listpids", pgid, error)
            count = max(used, 0) // width
            if count < self._capacity:
                return list(buffer[:count])
            self._capacity *= 2


def bind_libproc() -> ctypes.CDLL:
    """Load macOS's ``libproc`` with :data:`LIBPROC_SIGNATURES` applied; OSError elsewhere."""
    # use_errno: only errno tells a pid that is gone from one the reader may not see.
    lib = ctypes.CDLL(ctypes.util.find_library("proc") or _LIBPROC_PATH, use_errno=True)
    for name, restype, argtypes in LIBPROC_SIGNATURES:
        function = getattr(lib, name)
        function.restype = restype
        function.argtypes = argtypes
    return lib


# fuzz-target: the statm parser over arbitrary text
def parse_statm_resident(text: str, page_size: int) -> int:
    """Bytes resident per a ``/proc/<pid>/statm`` line (its second field, in pages); else 0."""
    fields = text.split()
    if len(fields) < 2:
        return 0
    try:
        pages = int(fields[1])
    except ValueError:
        return 0
    return pages * page_size if pages >= 0 else 0


def _parse_stat_field(text: str, index: int) -> int | None:
    """Field *index* after the command name in a ``/proc/<pid>/stat`` line, or None.

    The command name sits in parentheses and may itself hold spaces and parentheses, so the
    fields are counted from the last ``)``: 0 is the state, 1 the parent pid, 2 the group.
    A missing, non-integer or negative field reads as None.
    """
    _, closing, rest = text.rpartition(")")
    fields = rest.split()
    if not closing or len(fields) <= index:
        return None
    try:
        value = int(fields[index])
    except ValueError:
        return None
    return value if value >= 0 else None


# fuzz-target: the stat parser over arbitrary text
def parse_stat_ppid(text: str) -> int | None:
    """The parent pid in a ``/proc/<pid>/stat`` line, or None when it cannot be read."""
    return _parse_stat_field(text, 1)


# fuzz-target: the stat parser over arbitrary text
def parse_stat_pgrp(text: str) -> int | None:
    """The process group in a ``/proc/<pid>/stat`` line, or None when it cannot be read."""
    return _parse_stat_field(text, 2)


class ProcfsReader:
    """The Linux reader, over a ``/proc``-shaped directory at *root*."""

    def __init__(self, root: Path, page_size: int) -> None:
        """Read processes under *root*, converting pages at *page_size* bytes each."""
        self._root = root
        self._page_size = page_size

    def _read(self, *parts: str) -> str | None:
        """A file under the root, or None when it is gone or unreadable mid-exit."""
        try:
            return self._root.joinpath(*parts).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return None

    @staticmethod
    def _read_owned(path: Path, owner: Path) -> str | None:
        """*path*'s text; None once *owner* (its process or thread) is gone, else OSError.

        A process's files stay readable while its ``/proc`` folder exists, a zombie's
        included, so a read that fails while the owner is still listed is a real failure.
        """
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            if owner.exists():
                raise
            return None

    def root_present(self, pid: int) -> bool:
        """Whether *pid*'s ``/proc`` folder exists: a zombie's does, until it is reaped.

        The watchdog asks this of the run's own child alone (``boundedrun_rootwatch``), since
        a root missing here while still unreaped means this ``/proc`` does not hold it.
        """
        return (self._root / str(pid)).is_dir()

    def footprint(self, pid: int) -> int:
        """*pid*'s resident set in bytes; 0 once it is gone, OSError when unreadable."""
        folder = self._root / str(pid)
        text = self._read_owned(folder / "statm", folder)
        return 0 if text is None else parse_statm_resident(text, self._page_size)

    def children(self, pid: int) -> list[int]:
        """*pid*'s children, from each thread's ``children`` file where the kernel has them.

        A kernel built without ``CONFIG_PROC_CHILDREN`` has no such file, and then every
        process's ``stat`` is scanned for its parent instead: slower, but nothing is missed.
        A thread that ended between the listing and the read is skipped.
        """
        task = self._root / str(pid) / "task"
        if not task.is_dir():
            return []
        files = sorted(task.glob("*/children"))
        if not files:
            return self._scan_stats(parse_stat_ppid, pid)
        texts = (self._read_owned(path, path.parent) or "" for path in files)
        return [int(field) for text in texts for field in text.split()]

    def group(self, pid: int) -> int | None:
        """*pid*'s group, the fifth field of its ``stat``; None once it is gone."""
        folder = self._root / str(pid)
        text = self._read_owned(folder / "stat", folder)
        return None if text is None else parse_stat_pgrp(text)

    def group_members(self, pgid: int) -> list[int]:
        """Every listed process whose ``stat`` names *pgid* as its group.

        ``/proc`` has no per-group index, so this reads every process's ``stat``: one scan per
        group the tree reaches, on every poll.
        """
        return self._scan_stats(parse_stat_pgrp, pgid)

    def _scan_stats(self, field: Callable[[str], int | None], value: int) -> list[int]:
        """Every listed process whose ``stat``, read through *field*, gives *value*."""
        found: list[int] = []
        for entry in self._root.iterdir():
            if not entry.name.isdigit():
                continue
            text = self._read(entry.name, "stat")
            if text is not None and field(text) == value:
                found.append(int(entry.name))
        return found
