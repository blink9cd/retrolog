"""A stand-in for macOS's ``libproc``: C function pointers answering from fixed tables.

Not a test module (its name does not match ``test_*.py``), so discovery never loads it; the
reader suites import it. Each function is built from the reader module's own signature table,
so every call crosses real ctypes marshaling on any host. Each one also sets the fake's
``errno`` the way the real library leaves ``errno`` (measured on macOS 27.2): a pid that is gone
is ``ESRCH``, and a pid the caller may not inspect (another uid, or a sandbox denying
``process-info*``) is ``EPERM``.
"""

from __future__ import annotations

import ctypes
import errno
import sys
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import boundedrun_memread
from boundedrun_memread import RusageInfoV0


class FakeLibproc(SimpleNamespace):
    """The four libproc functions over the tables, and the errno the last one left."""

    errno: int


def fake_libproc(
    footprints: Mapping[int, int],
    children: Mapping[int, Sequence[int]],
    groups: Mapping[int, int] | None = None,
    denied: Collection[int] = (),
) -> FakeLibproc:
    """C function pointers with libproc's signatures, answering from the three tables.

    An unknown pid reads as libproc reads a dead one: rusage -1 with ``ESRCH``, no children,
    and no BSD info (``ESRCH``); a group with no members lists none. A pid in *denied* (and a
    group id in it, for ``proc_listpids``) reads as a sandbox denies it: every call fails or
    lists nothing, with ``EPERM``, the shape that once read as "gone".
    """
    group_of = dict(groups or {})
    lib = FakeLibproc(errno=0)
    prototypes = {
        name: ctypes.CFUNCTYPE(restype, *argtypes)
        for name, restype, argtypes in boundedrun_memread.LIBPROC_SIGNATURES
    }

    def fail(code: int, value: int) -> int:
        lib.errno = code
        return value

    def rusage(pid: int, flavor: int, info: ctypes.c_void_p) -> int:
        if pid in denied:
            return fail(errno.EPERM, -1)
        if pid not in footprints or flavor != boundedrun_memread.RUSAGE_INFO_V0:
            return fail(errno.ESRCH, -1)
        record = ctypes.cast(info, ctypes.POINTER(RusageInfoV0)).contents
        record.ri_phys_footprint = footprints[pid]
        record.ri_resident_size = 1  # never the field the reader should use
        return 0

    def listchildren(pid: int, buffer: ctypes.c_void_p, size: int) -> int:
        if pid in denied:
            return fail(errno.EPERM, 0)
        kids = list(children.get(pid, ()))[: size // ctypes.sizeof(ctypes.c_int)]
        array = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))
        for index, kid in enumerate(kids):
            array[index] = kid
        return len(kids)

    def pidinfo(
        pid: int, flavor: int, arg: int, buffer: ctypes.c_void_p, size: int
    ) -> int:
        wanted = boundedrun_memread.PROC_PIDTBSDSHORTINFO
        record_size = ctypes.sizeof(boundedrun_memread.ProcBsdShortInfo)
        if pid in denied:
            return fail(errno.EPERM, 0)
        if pid not in group_of or flavor != wanted or size < record_size:
            return fail(errno.ESRCH, 0)  # a zombie, or a pid that is gone
        record = ctypes.cast(
            buffer, ctypes.POINTER(boundedrun_memread.ProcBsdShortInfo)
        ).contents
        record.pbsi_pid = pid
        record.pbsi_pgid = group_of[pid]
        return record_size

    def listpids(kind: int, pgid: int, buffer: ctypes.c_void_p, size: int) -> int:
        if pgid in denied or kind != boundedrun_memread.PROC_PGRP_ONLY:
            return fail(errno.EPERM, 0)
        members = [pid for pid, group in group_of.items() if group == pgid]
        width = ctypes.sizeof(ctypes.c_int)
        kept = members[: size // width]
        array = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))
        for index, pid in enumerate(kept):
            array[index] = pid
        return len(kept) * width  # proc_listpids counts bytes, not pids

    lib.proc_pid_rusage = prototypes["proc_pid_rusage"](rusage)
    lib.proc_listchildpids = prototypes["proc_listchildpids"](listchildren)
    lib.proc_pidinfo = prototypes["proc_pidinfo"](pidinfo)
    lib.proc_listpids = prototypes["proc_listpids"](listpids)
    return lib


def reader_over(
    lib: FakeLibproc, capacity: int = 64
) -> boundedrun_memread.LibprocReader:
    """A reader over the fake *lib*, reading and clearing the fake's own errno."""

    def clear(value: int) -> int:
        previous, lib.errno = lib.errno, value
        return previous

    return boundedrun_memread.LibprocReader(
        lib,  # type: ignore[arg-type]  # the fake stands in for the CDLL by duck typing
        initial_capacity=capacity,
        get_errno=lambda: lib.errno,
        set_errno=clear,
    )
