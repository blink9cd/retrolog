"""retrolog_restore: rebuild the diary entries deleted before an import, as an import file.

An import tops the account's activity feed, so an operator who cares about the feed
backs up the account export, deletes the latest few diary entries, imports the
backfill, and then restores the deleted entries with one more import. This tool
compares the backup export (``--backup``) with an export taken after the deletion
(``--current``) and writes to ``--out`` the import file for every diary entry the
backup holds and the later export lacks, with its review, plus ``restore.md``, which
lists each entry and marks the films to like again: an import cannot restore a like,
and a film the later export still likes kept its heart.
Nothing is sent anywhere: the operator uploads the file.

The stages live in ``src/`` beside this file; the sibling retrolog_export's ``src/``
supplies the bounded export reader, the CSV writer, and the formula-cell
characters, retrolog_resolve's the
``--timeout`` rule, retrolog_parse's the Markdown cell escape, and retrolog_survey's
the output guard and atomic writes. All of them,
and the shared libraries this file imports, are put on ``sys.path`` from this file's
own location, so the tool can be moved. ``retrolog_restore.md`` describes the tool.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[1] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")

import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_HERE = Path(__file__).resolve().parent
# This tool's private modules, then export's (the export reader, the CSV writer, the
# formula-cell characters), resolve's (the --timeout rule export shares), parse's (the
# cell escape, the title key export's reader uses), and the survey's (the output guard,
# atomic writes).
SOURCE_DIRS = (
    _HERE / "src",
    _HERE.parent / "retrolog_export" / "src",
    _HERE.parent / "retrolog_resolve" / "src",
    _HERE.parent / "retrolog_parse" / "src",
    _HERE.parent / "retrolog_survey" / "src",
)
sys.path[:0] = [str(folder) for folder in SOURCE_DIRS if str(folder) not in sys.path]

import survey_guard  # noqa: E402 - after the src/ inserts
from resolve_args import MAX_SECONDS, positive  # noqa: E402 - after the src/ inserts
from restore_build import Restored, restore_entries  # noqa: E402 - after the inserts
from restore_output import (  # noqa: E402 - after the src/ inserts
    CSV_NAME,
    MARKDOWN_NAME,
    restore_csv,
    restore_markdown,
    summary_lines,
)
from restore_read import (  # noqa: E402 - after the src/ inserts
    BACKUP_FILES,
    CURRENT_FILES,
    ExportFiles,
    RestoreError,
    read_export,
)
from survey_write import describe_error, write_atomic  # noqa: E402

TOOL = "retrolog_restore"
_EXIT_OK, _EXIT_NOTHING, _EXIT_USAGE = 0, 1, 2
_DEFAULT_TIMEOUT = 15.0
# The two export options as messages name them; build_parser spells each as a literal,
# the form a reader of the parser, and the doc check, looks for.
_BACKUP, _CURRENT = "--backup", "--current"
# The two outputs, in the order written: restore.md last, so its presence marks a
# finished run.
_OUTPUT_NAMES = (CSV_NAME, MARKDOWN_NAME)


class _Refused(Exception):
    """The run may not start or finish; the message names no path and no title."""


class _NothingDeleted(Exception):
    """Every backup diary entry is still on the account: nothing to write."""


_EPILOG = (
    "Prerequisites: Python 3.12 or later, standard library only (nothing to "
    'install); two exports of the Letterboxd account ("Export your data"), each '
    "as its .zip or unpacked folder: one downloaded before deleting the diary "
    "entries, and one after.\n\n"
    "Run order: back up the account export; delete the latest diary entries on "
    "Letterboxd; download a second export; run retrolog_export on that second "
    "export and import its files; then run this tool and import "
    f"{CSV_NAME} last, and like again each film {MARKDOWN_NAME} marks (one "
    "liked in --backup and not in --current).\n\n"
    "Exit codes: 0 means written. 1 means nothing was deleted, so nothing was "
    "written (check that --backup and --current are not swapped). 2 means bad "
    "arguments; an unreadable or "
    "invalid export, or one holding no diary.csv; a restored entry with a Rewatch "
    "cell other than Yes or empty, or matching more than one review; --out "
    "refused; or the output could not be written."
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description="Write the Letterboxd import file that restores the diary "
        "entries deleted between two account exports, with their reviews, and a "
        "list of them marking the films to like again.",
        # Shows each default, and none on a required option (the stock formatter
        # stamps "(default: None)" there before CPython 3.13; the floor is 3.12).
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--backup",
        type=Path,
        required=True,
        help="The account export taken before the deletion: its .zip, or the "
        "folder it unpacks to (read only).",
    )
    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="An account export taken after the deletion: its .zip, or the folder "
        "it unpacks to (read only).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory; refused inside a --backup or --current folder, or "
        "inside a git work tree.",
    )
    parser.add_argument(
        "--timeout",
        type=positive,
        default=_DEFAULT_TIMEOUT,
        help="Seconds per git call of the --out check: greater than 0 and at most "
        f"{MAX_SECONDS:g}.",
    )
    return parser


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _read(path: Path, option: str, names: tuple[str, ...]) -> ExportFiles:
    """One export's ``names``; ``_Refused`` naming the option and file, never a path."""
    try:
        return read_export(path, option, names)
    except RestoreError as error:
        raise _Refused(f"unusable account export: {error}") from None


def _check_out(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> None:
    """Refuse an ``--out`` inside either export (a folder, or the zip itself) or git.

    Nothing is created: the folder is made only once there is something to write.
    """
    try:
        for source in (args.backup, args.current):
            survey_guard.check_out_dir(
                args.out, source, run_git=run_git, timeout=args.timeout, tool=TOOL
            )
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None


# ---------------------------------------------------------------------------
# Act
# ---------------------------------------------------------------------------


def _restored(backup: ExportFiles, current: ExportFiles) -> tuple[Restored, ...]:
    """The entries to restore; ``_NothingDeleted`` when there are none."""
    try:
        restored = restore_entries(backup, current)
    except RestoreError as error:
        raise _Refused(f"unusable account export: {_BACKUP}: {error}") from None
    if not restored:
        raise _NothingDeleted
    return restored


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


def _clear_previous(out: Path) -> None:
    """Remove an earlier run's outputs, so a run failing partway leaves no stale one.

    Only a file or a link is removed, never a folder, and a link is removed rather
    than followed. ``--out`` holds this tool's marker, so these are its own files.
    """
    for name in _OUTPUT_NAMES:
        path = out / name
        if path.is_symlink() or path.is_file():
            path.unlink()


def _write_outputs(out: Path, texts: Mapping[str, str]) -> None:
    """Clear the earlier run's outputs, then write each, ``restore.md`` last."""
    _clear_previous(out)
    for name in _OUTPUT_NAMES:
        write_atomic(out / name, texts[name])


def _prepare_out(args: argparse.Namespace) -> Path:
    """Create the checked ``--out`` with its marker, and return it resolved."""
    try:
        survey_guard.prepare_out_dir(args.out, tool=TOOL)
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None
    return args.out.resolve()


def _record(args: argparse.Namespace, restored: Sequence[Restored]) -> None:
    """Write the outputs and print the summary; ``_Refused`` on a write failure."""
    texts = {
        CSV_NAME: restore_csv(restored),
        MARKDOWN_NAME: restore_markdown(restored),
    }
    out = _prepare_out(args)
    try:
        _write_outputs(out, texts)
    except OSError as error:
        # describe_error keeps the class and reason only: the message names the path.
        raise _Refused(f"could not write the output: {describe_error(error)}") from None
    for line in summary_lines(restored):
        print(line)


def _run(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> None:
    """Validate -> act -> record; ``_Refused`` or ``_NothingDeleted`` to stop."""
    backup = _read(args.backup, _BACKUP, BACKUP_FILES)
    current = _read(args.current, _CURRENT, CURRENT_FILES)
    _check_out(args, run_git)
    _record(args, _restored(backup, current))


def main(
    argv: list[str] | None = None,
    *,
    run_git: survey_guard.GitRunner = survey_guard.real_git,
) -> int:
    """Write the restore file for ``--backup`` against ``--current``; the exit code.

    ``run_git`` is the seam the tests replace: the git runner of the ``--out`` check.
    """
    # First, before anything is printed: output piped to another program would
    # otherwise use the locale's encoding.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _run(args, run_git)
    except _Refused as refused:
        print(f"{parser.prog}: {refused}", file=sys.stderr)
        return _EXIT_USAGE
    except _NothingDeleted:
        print(
            f"{parser.prog}: nothing to restore: every {_BACKUP} diary entry is in "
            f"{_CURRENT}; check that the two are not swapped ({_BACKUP} is the "
            "export from before the deletion)",
            file=sys.stderr,
        )
        return _EXIT_NOTHING
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
