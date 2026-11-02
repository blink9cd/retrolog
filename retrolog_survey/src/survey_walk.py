"""Walk a source tree and list what the survey will look at.

`walk_source` is the survey's only view of the file system: every later stage reads the
`FileEntry` list it returns, never the tree itself. It only reads directory listings and
file sizes, so it is safe to point at a folder that must not change.

What it reports, and why:

- Hidden names (any name starting with ".", which covers Finder's `.DS_Store` and the
  AppleDouble `._*` companions a non-Apple volume collects) are skipped outright, and a
  hidden directory is not descended into. They are file-system bookkeeping, not lists.
- A symbolic link, to a file or to a directory, is reported as one `symlink` entry and
  never followed, so a link cannot pull a second tree (or a loop) into the survey.
- A path matching an `exclude` pattern is kept with kind `excluded` rather than dropped,
  so a report can count what was left out instead of silently losing it.
- A directory whose listing fails (the walk root included, reported as "."), and a file
  or directory that cannot be examined (its `lstat` fails), are each kept as one
  `unreadable` entry carrying the error's class name, so one bad item neither aborts
  the walk nor drops a subtree unannounced.
  Exclusion is checked first: an excluded path stays `excluded` whatever went wrong.
- Paths are relative to the walk root, POSIX-separated and Unicode NFC. Some file
  systems store names decomposed (NFD); normalizing means the same name always compares
  and prints the same way, whichever volume it came from.
"""

from __future__ import annotations

import os
import stat
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

FileKind = Literal["txt", "doc", "docx", "other", "symlink", "excluded", "unreadable"]

# Lower-cased suffix -> kind. Anything not listed is "other": reported and counted, but
# not a format the survey reads.
_KIND_BY_SUFFIX: dict[str, FileKind] = {".txt": "txt", ".doc": "doc", ".docx": "docx"}

# The group of a file sitting directly in the walk root (or of any file when the
# grouping depth is 0).
_ROOT_GROUP = "."


@dataclass(frozen=True)
class FileEntry:
    """One file (or symbolic link) found under the walk root."""

    rel_path: str  # POSIX, NFC, relative to the walk root
    group: str  # first `group_depth` directory parts joined by "/", or "."
    kind: FileKind
    size: int  # bytes; 0 for a symlink or an unreadable entry
    # The failure's exception class name (e.g. "PermissionError") for an unreadable entry,
    # else None. Never the message: an OS message can carry an absolute path.
    error: str | None = None


def group_key(parts: Sequence[str], depth: int) -> str:
    """Return the report group for a file whose directory parts are `parts`."""
    head = tuple(parts[:depth])
    return "/".join(head) if head else _ROOT_GROUP


def _is_hidden(name: str) -> bool:
    """True for a name the survey never reports: `.DS_Store`, `._*` and any dot-name."""
    return name.startswith(".")


def _is_excluded(rel_path: str, exclude: Sequence[str]) -> bool:
    """True when `rel_path` matches any `exclude` glob, ignoring case.

    Both sides are casefolded and compared with `fnmatchcase`, so the result does not
    depend on the platform's own case rules the way plain `fnmatch` does.
    """
    folded = rel_path.casefold()
    return any(fnmatchcase(folded, pattern.casefold()) for pattern in exclude)


def _classify(
    path: Path, rel_path: str, exclude: Sequence[str], status: _Status
) -> FileKind:
    """Return the kind of the file, link or directory at `path`, from its `status`."""
    if _is_excluded(rel_path, exclude):
        return "excluded"
    if status.error is not None:
        return "unreadable"
    if status.link:
        return "symlink"
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), "other")


def _rel_parts(root: Path, path: Path) -> tuple[str, ...]:
    """The NFC parts of `path` relative to `root`; empty for the root itself."""
    return tuple(
        unicodedata.normalize("NFC", part) for part in path.relative_to(root).parts
    )


@dataclass(frozen=True)
class _Status:
    """What one `lstat` said about a path: link or not, its size, or why it failed."""

    link: bool = False
    size: int = 0  # bytes; 0 for a link or a failure
    error: str | None = None  # the failure's exception class name


def _status(path: Path) -> _Status:
    """`lstat` `path` once, never following a link, turning any failure into `error`.

    One call answers both "is it a link?" and "how large is it?", and it sits inside
    the `try`. `Path.is_symlink` cannot be used for the first question: on Python 3.12
    it re-raises an lstat failure such as EACCES, which every child of a directory
    that can be listed but not searched gives, and the walk would crash. A link's
    target is never read, so its size is 0: the target may not exist.
    """
    try:
        mode = os.lstat(path)
    except OSError as exc:
        # The item vanished or cannot be examined mid-walk: fail this one item only.
        return _Status(error=type(exc).__name__)
    if stat.S_ISLNK(mode.st_mode):
        return _Status(link=True)
    return _Status(size=mode.st_size)


def _make_entry(
    root: Path,
    path: Path,
    group_depth: int,
    exclude: Sequence[str],
    status: _Status | None = None,
) -> FileEntry:
    """Build the `FileEntry` for the item at `path` under `root`, examining it if needed."""
    parts = _rel_parts(root, path)
    rel_path = "/".join(parts)
    status = _status(path) if status is None else status
    return FileEntry(
        rel_path=rel_path,
        group=group_key(parts[:-1], group_depth),
        kind=_classify(path, rel_path, exclude, status),
        size=status.size,
        error=status.error,
    )


def _unreadable_dir_entry(
    root: Path, exc: OSError, group_depth: int, exclude: Sequence[str]
) -> FileEntry:
    """Build the entry for a directory whose listing raised `exc` (the root gives ".")."""
    path = Path(os.fsdecode(exc.filename))
    parts = _rel_parts(root, path)
    rel_path = "/".join(parts) or _ROOT_GROUP
    error = type(exc).__name__
    return FileEntry(
        rel_path=rel_path,
        group=group_key(parts, group_depth),
        kind=_classify(path, rel_path, exclude, _Status(error=error)),
        size=0,
        error=error,
    )


def walk_source(
    root: Path, *, group_depth: int, exclude: Sequence[str] = ()
) -> list[FileEntry]:
    """List every non-hidden file and symbolic link under `root`, sorted by `rel_path`.

    `group_depth` is how many leading directory levels name a file's report group;
    `exclude` holds case-insensitive glob patterns matched against each relative path.
    """
    entries: list[FileEntry] = []

    def record_unreadable(exc: OSError) -> None:
        # os.walk's default is to skip a directory it cannot list, silently, subtree and all.
        entries.append(_unreadable_dir_entry(root, exc, group_depth, exclude))

    for dirpath, dirnames, filenames in os.walk(
        root, onerror=record_unreadable, followlinks=False
    ):
        here = Path(dirpath)
        descend: list[str] = []
        for name in dirnames:
            if _is_hidden(name):
                continue
            status = _status(here / name)
            if status.link or status.error is not None:
                # A link is reported and pruned so the walk never enters it. A directory
                # that cannot be examined is reported as unreadable and pruned too: the
                # walk could not tell whether it is a link it must not follow.
                entries.append(
                    _make_entry(root, here / name, group_depth, exclude, status)
                )
                continue
            descend.append(name)
        # In-place assignment is how os.walk is told which subdirectories to enter.
        dirnames[:] = descend
        entries.extend(
            _make_entry(root, here / name, group_depth, exclude)
            for name in filenames
            if not _is_hidden(name)
        )
    return sorted(entries, key=lambda entry: entry.rel_path)
