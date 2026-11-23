"""Drain a registered ``bounded_run`` child: contain it, wait under its bounds, release it.

Private to ``bounded_run``: it sits in that tool's own ``src/`` folder, so nothing outside the
tool folder imports it. Its name carries the tool's prefix, so it cannot shadow another tool's
module on ``sys.path``.

Why it sits apart
-----------------
It keeps ``bounded_run.py``, which holds the streaming entry point too, to a reviewable length.
This is only the order a capturing run's drain follows: containment first, then the timed
wait (``boundedrun_drain``), under the memory watchdog when a limit is set
(``boundedrun_memory``), and the release however it ends. The containment itself and the
release stay in ``bounded_run.py``, beside the spawn they are part of, and arrive here as
arguments.

Entry point assumptions
-----------------------
Imported by ``bounded_run`` alone, with this ``src/`` already on ``sys.path``. Standard library
and this ``src/``'s siblings only.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

import boundedrun_memory
from boundedrun_drain import (
    Clock,
    KillTreeFn,
    _communicate_bounded,
    _kill_and_reap,
    _Stream,
)


def drain_contained(
    proc: subprocess.Popen[_Stream],
    argv: Sequence[str],
    timeout: float,
    kill_tree_fn: KillTreeFn,
    stdin_data: _Stream | None,
    clock: Clock | None,
    memory: boundedrun_memory.MemoryBound | None,
    *,
    contain: Callable[[subprocess.Popen[_Stream]], None],
    release: Callable[[int], None],
) -> tuple[_Stream, _Stream]:
    """*contain* the registered *proc*, drain it under the bound, and *release* it after.

    With a *memory* bound the drain runs under its watchdog, which starts only now, once the
    child is registered and contained, and stops when the drain ends.
    """

    def drain() -> tuple[_Stream, _Stream]:
        """The time-bounded drain, the whole of it when no memory bound is set."""
        return _communicate_bounded(
            proc, argv, timeout, kill_tree_fn, stdin_data, clock
        )

    try:
        contain(proc)
        if memory is None:
            return drain()
        return boundedrun_memory.drain_watched(
            proc.pid,
            argv,
            memory.limit,
            memory.reader,
            kill_tree_fn,
            drain,
            lambda: _kill_and_reap(proc, kill_tree_fn),
            unreaped=lambda: proc.returncode is None,
            observer=memory.observer,
        )
    finally:
        release(proc.pid)
