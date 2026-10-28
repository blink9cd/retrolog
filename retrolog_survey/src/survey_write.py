"""Write an output file atomically, and describe a failure without naming its path.

`write_atomic_bytes` and `write_atomic` replace a file so that a reader sees the old
file or the new one, never part of either; `describe_error` turns an exception into a
``Class: reason`` string that holds no path. They use the standard library only, so
retrolog_resolve, which imports this module directly, needs nothing the survey's .docx
reader does; `survey_report` re-exports all three for its own importers.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def describe_error(error: Exception) -> str:
    """``Class: reason`` for a report, never a path.

    An OSError's message embeds the path it failed on, so only its ``strerror`` is
    kept; the survey's and its callers' own exceptions carry no path by construction.
    """
    reason = error.strerror if isinstance(error, OSError) else str(error)
    name = type(error).__name__
    return f"{name}: {reason}" if reason else name


def write_atomic_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temporary file in the same directory.

    ``os.replace`` swaps the finished file in, so a reader sees the old file or the
    new one, never part of either; it also replaces a link at ``path`` rather than
    writing through it. On failure the temporary file is removed.
    """
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_atomic(path: Path, text: str) -> None:
    r"""Write ``text`` as UTF-8 to ``path`` atomically (see `write_atomic_bytes`).

    A file name that is not valid UTF-8 reaches a report as a relative path holding
    surrogate escapes, which UTF-8 cannot encode; ``backslashreplace`` writes each as
    a visible ``\udcXX`` escape rather than failing the whole report on one name.
    """
    write_atomic_bytes(path, text.encode("utf-8", errors="backslashreplace"))
