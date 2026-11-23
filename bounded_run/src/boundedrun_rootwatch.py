"""Tell a root that is gone from one the memory reader cannot see.

Private to ``bounded_run``: it sits in that tool's own ``src/`` folder, so nothing outside the
tool folder imports it. Its name carries the tool's prefix, so it cannot shadow another tool's
module on ``sys.path``.

Why the root, and only the root
-------------------------------
The tree walk reads a process that is gone as 0 bytes, since the tree races its own members'
exits. For a descendant, "gone" has no second witness. For the root it has one: the run's own
``Popen``, which stays unreaped until ``subprocess`` reaps it. A Linux reader pointed at a
``/proc`` that does not hold the child (an empty directory, a mount of another pid namespace)
sees no root folder for a child that is still running, and the walk sums 0 bytes, so the bound
fails open. A root the reader cannot see while the run has not reaped it is therefore a read
failure, which the watchdog turns into its kill and ``MemoryWatchFailed``.

A zombie root is not a failure: Linux keeps its ``/proc`` folder until it is reaped, so the
probe sees it. macOS's libproc reader has no probe and is never checked here: its ``ESRCH``
comes from the kernel's own process table, which cannot be the wrong source, and a zombie root
returns ``ESRCH`` there too, so a check would fail every run whose child exits before its pipes
close. Group reads are never checked either, for the same zombie reason.

The reap race
-------------
``subprocess`` reaps the child (``waitpid`` returns, the folder goes) a moment before it records
``returncode``. A root seen missing while still "unreaped" may be that moment, so the check
polls *unreaped* every :data:`REAP_POLL_SECONDS` for :data:`REAP_GRACE_SECONDS`, and fails only
if it never turns. A real read failure costs that grace, about a second, before the kill.

The grace is measured on ``time.monotonic()``, never counted in polls. A throttled process (a
background-QoS sweep, a coalesced timer) sleeps far past each short wait: one of 5 ms was
measured near 200 ms, which stretched a grace counted as 200 polls to some 40 seconds, past the
life of a child whose watch had failed, which then ran on unwatched to its own end.

Entry point assumptions
-----------------------
Imported by ``boundedrun_memory`` alone, with this ``src/`` already on ``sys.path``. Standard
library only.
"""

from __future__ import annotations

import errno
import time
from collections.abc import Callable

#: Seconds between two looks at *unreaped* while a missing root may be mid-reap.
REAP_POLL_SECONDS = 0.005

#: Seconds a missing, unreaped root is given to turn reaped before it is a read failure.
REAP_GRACE_SECONDS = 1.0


def check_root(
    reader: object,
    pid: int,
    unreaped: Callable[[], bool],
    wait: Callable[[float], bool],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Raise OSError when *reader* cannot see root *pid* though the run has not reaped it.

    A reader without a ``root_present`` probe is not checked (the module docstring says why).
    *wait* sleeps up to its argument and returns True once the watchdog was stopped, which
    ends the check with no verdict: the run is over, and nothing would be killed anyway.
    The grace runs for :data:`REAP_GRACE_SECONDS` on *clock*, however long each wait took.
    """
    present: Callable[[int], bool] | None = getattr(reader, "root_present", None)
    if present is None or present(pid):
        return
    expires = clock() + REAP_GRACE_SECONDS
    while True:
        if not unreaped() or wait(REAP_POLL_SECONDS):
            return
        if clock() >= expires:
            break
    raise OSError(
        errno.ESRCH,
        f"the memory reader cannot see the child {pid}, which has not been reaped",
    )
