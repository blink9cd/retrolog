"""Write a run's files into its checked ``--out``: the import files, then the review.

The entry point has already refused an ``--out`` inside the inputs or a git work tree,
and created it with the tool's marker, so every file named here is this tool's own.
Each write is atomic (``survey_write.write_atomic``), and every function lets an
``OSError`` through for the entry point to report without its path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path

from survey_write import write_atomic

# The review's two files, in the order written: always after every import file.
REVIEW_NAMES = ("review.json", "review.md")


def other_imports(out: Path, file_stem: str, stems: Iterable[str]) -> tuple[str, ...]:
    """Each of ``stems`` but ``file_stem`` with a ``<stem>-*.csv`` in ``out``, in order.

    None when ``out`` does not exist yet. A run clears only its own service's files,
    so another service's files there would outlive that service's review.
    """
    if not out.is_dir():
        return ()
    names = [path.name for path in out.iterdir()]
    return tuple(
        stem
        for stem in stems
        if stem != file_stem
        and any(fnmatchcase(name, f"{stem}-*.csv") for name in names)
    )


def clear_previous(out: Path, file_stem: str) -> None:
    """Remove an earlier run's review and every ``<file_stem>-*.csv``, before writing.

    So a run that fails partway leaves no file of an earlier run beside its own, and
    no review: the review is written last, and its absence marks an incomplete run.
    Only a file or a link is removed, never a folder, and a link is removed rather
    than followed.
    """
    for path in out.iterdir():
        named = path.name in REVIEW_NAMES or fnmatchcase(
            path.name, f"{file_stem}-*.csv"
        )
        if named and (path.is_symlink() or path.is_file()):
            path.unlink()


def write_outputs(
    out: Path, file_stem: str, files: Mapping[str, str], review: tuple[str, str]
) -> None:
    """Clear the earlier run's files, write every import file, then the review."""
    clear_previous(out, file_stem)
    for name, text in files.items():
        write_atomic(out / name, text)
    for name, text in zip(REVIEW_NAMES, review, strict=True):
        write_atomic(out / name, text)


def write_alone(out: Path, file_stem: str, name: str, text: str) -> None:
    """Clear the earlier run's files, then write ``name`` alone, with no review."""
    clear_previous(out, file_stem)
    write_atomic(out / name, text)
