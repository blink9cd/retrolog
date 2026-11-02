"""retrolog_parse: turn a folder of old media lists into dated, rated rows.

The second step of the retrolog tools (survey, parse, resolve, export). It reads every
``.txt``, ``.doc`` and ``.docx`` file under ``--path`` without writing there (a
``.doc`` through a converted copy in a temporary directory), applies the operator's
rules file, and writes one CSV per medium and year, ``review.json`` and ``review.md``
to ``--out``, which it first checks is a safe place for files that quote private text.

The stages live in ``src/`` beside this file; the sibling retrolog_survey's ``src/``
supplies the walk, the readers, the ``.doc`` conversion and the output guard. Both are
put on ``sys.path`` from this file's own location, so the tool can be moved.
``retrolog_parse.md`` describes the tool, its rules file and its output.
"""

from __future__ import annotations

import argparse
import enum
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[1] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")

import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_HERE = Path(__file__).resolve().parent
# This tool's private modules, then the survey's, which supply the readers and guard.
SOURCE_DIRS = (_HERE / "src", _HERE.parent / "retrolog_survey" / "src")
sys.path[:0] = [str(folder) for folder in SOURCE_DIRS if str(folder) not in sys.path]

import survey_convert  # noqa: E402 - after the src/ inserts
import survey_guard  # noqa: E402 - after the src/ inserts

# COLUMNS, ERROR_KINDS and REVIEW_KINDS are re-exported (the "as" form) so a reader of the
# entry point, and its tests, find the CSV and review vocabulary here.
from parse_output import (  # noqa: E402 - after the src/ inserts
    COLUMNS as COLUMNS,
)
from parse_output import (  # noqa: E402 - after the src/ inserts
    csv_files,
    review_counts,
    review_json,
    review_markdown,
)
from parse_rows import (  # noqa: E402 - after the src/ inserts
    ERROR_KINDS as ERROR_KINDS,
)
from parse_rows import (  # noqa: E402 - after the src/ inserts
    REVIEW_KINDS as REVIEW_KINDS,
)
from parse_rows import build_rows  # noqa: E402 - after the src/ inserts
from parse_rules import Rules, RulesError, load_rules, medium_for  # noqa: E402
from parse_source import SourceContext, SourceError, read_source  # noqa: E402
from parse_types import ReviewItem, SourceLine  # noqa: E402 - after the src/ inserts
from survey_report import describe_error, write_atomic  # noqa: E402
from survey_walk import FileEntry, walk_source  # noqa: E402

TOOL = "retrolog_parse"
_EXIT_OK, _EXIT_ERRORS, _EXIT_USAGE = 0, 1, 2
# The default per-file size limit, in bytes.
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
# The walk kinds this tool reads; every other kind is the survey's to report.
_READ_KINDS = frozenset({"txt", "doc", "docx"})
# The walk kinds a rule may name without a rule-file-missing error: those read, and
# those the walk found but did not read because they were excluded or unreadable.
_WALKED_KINDS = _READ_KINDS | {"excluded", "unreadable"}
# What a CSV this tool writes is named like, for each medium the rules name, so a
# re-run can remove one it no longer writes: "<medium>-<year>.csv".
_CSV_GLOB = "{medium}-*.csv"
_READ_ERROR = "read-error"
_READ_DETAIL = "the file could not be read ({reason})"


class _Unset(enum.Enum):
    """The type of ``_UNSET``: "find LibreOffice yourself"."""

    TOKEN = enum.auto()


# Passed as ``soffice`` to mean "call survey_convert.locate_soffice()".
_UNSET = _Unset.TOKEN


class _Refused(Exception):
    """The run may not start or finish; the message names no path."""


def _positive(kind: Callable[[str], float]) -> Callable[[str], float]:
    """An argparse type: a number of ``kind`` greater than zero."""

    def parse(text: str) -> float:
        value = kind(text)
        if value <= 0:
            raise argparse.ArgumentTypeError("must be greater than 0")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description="Turn a folder of old .txt, .doc and .docx media lists into "
        "dated, rated CSV rows and a review list, by a rules file.",
        # Shows each default, and none on a required option (the stock formatter
        # stamps "(default: None)" there before CPython 3.13; the floor is 3.12).
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Prerequisites: a rules file (start from samples/rules.example.toml); "
            "macOS's built-in textutil for .doc files (LibreOffice is used instead "
            "when it is installed and its conversion works). .txt and .docx files "
            "need neither.\n\n"
            "Run order: run retrolog_survey first and read its report, write the "
            "rules file, then run this and read review.md.\n\n"
            "Exit codes: 0 means written, with no error in the review. 1 means "
            "written, but the review lists an error (read-error, no-medium, "
            "rule-file-missing, override-mismatch, or term-mismatch). 2 means bad "
            "arguments, --path missing or not a directory, an unreadable or "
            "invalid rules file, --out or the temporary directory refused, or the output could "
            "not be written."
        ),
    )
    parser.add_argument(
        "--path", type=Path, required=True, help="Source tree to read (read only)."
    )
    parser.add_argument("--rules", type=Path, required=True, help="The rules file.")
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory; refused inside --path or a git work tree.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Glob over the relative path, case-insensitive, repeatable; matching "
        "files are not read.",
    )
    parser.add_argument(
        "--max-bytes",
        type=_positive(int),
        default=_DEFAULT_MAX_BYTES,
        help="Per-file size limit (text bytes, .doc file size or Word "
        "uncompressed size).",
    )
    parser.add_argument(
        "--timeout",
        type=_positive(float),
        default=120.0,
        help="Seconds per converter or git call.",
    )
    return parser


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _load(path: Path) -> Rules:
    """The checked rules file; ``_Refused`` naming the failure, never the path."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise _Refused(f"could not read --rules: {type(error).__name__}") from None
    try:
        return load_rules(data)
    except RulesError as error:
        raise _Refused(f"invalid --rules: {error}") from None


def _inside_path_or_out(folder: Path, args: argparse.Namespace) -> bool:
    """Whether ``folder`` is ``--path`` or ``--out``, or lies under either."""
    resolved = folder.resolve()
    return any(
        resolved.is_relative_to(root.resolve()) for root in (args.path, args.out)
    )


def _check_out(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> None:
    """Refuse an unsafe ``--out``, or a temporary directory under --path or --out.

    The temporary directory, where .doc copies are converted, follows TMPDIR: one
    under ``--path`` would put converter files into the source tree.
    """
    try:
        survey_guard.check_out_dir(
            args.out, args.path, run_git=run_git, timeout=args.timeout, tool=TOOL
        )
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None
    if _inside_path_or_out(Path(tempfile.gettempdir()), args):
        raise _Refused(
            "refused the temporary directory: it lies inside --path or --out"
        )


# ---------------------------------------------------------------------------
# Act
# ---------------------------------------------------------------------------


def _read_error(rel_path: str, reason: str) -> ReviewItem:
    """The error item for a file that could not be read."""
    detail = _READ_DETAIL.format(reason=reason)
    return ReviewItem(_READ_ERROR, rel_path, 0, "", detail, True)


def _read_one(
    entry: FileEntry, root: Path, rules: Rules, ctx: SourceContext
) -> tuple[SourceLine, ...] | ReviewItem:
    """One entry's lines, a read-error item, or no lines for a file of no medium.

    A file no ``[media]`` glob names is not read at all: ``build_rows`` reports it
    as "no-medium" from its name alone, so a music file never reaches a converter.
    """
    if entry.kind == "unreadable":
        return _read_error(entry.rel_path, entry.error or "unreadable")
    if medium_for(rules, PurePosixPath(entry.rel_path).name) is None:
        return ()
    try:
        return read_source(entry, root, ctx)
    except SourceError as error:
        return _read_error(entry.rel_path, str(error))


def _read_all(
    args: argparse.Namespace,
    rules: Rules,
    run: survey_convert.Runner,
    soffice: Path | None,
) -> tuple[dict[str, tuple[SourceLine, ...]], list[ReviewItem], frozenset[str]]:
    """Every readable file's lines by relative path, the read errors, the walked paths.

    The walked paths are those a rule may name without a rule-file-missing error:
    every file read, and every one excluded or unreadable.

    The temporary directory is the system's, never under ``--path`` or ``--out``, and
    is removed before returning; a failure to remove it is ignored rather than
    reported, since the error would name the path.
    """
    entries = walk_source(args.path, group_depth=1, exclude=args.exclude)
    files: dict[str, tuple[SourceLine, ...]] = {}
    errors: list[ReviewItem] = []
    with tempfile.TemporaryDirectory(
        prefix=f"{TOOL}_", ignore_cleanup_errors=True
    ) as work:
        ctx = SourceContext(Path(work), run, soffice, args.max_bytes, args.timeout)
        for entry in entries:
            if entry.kind not in _READ_KINDS | {"unreadable"}:
                continue  # the survey reports links, exclusions and other files
            read = _read_one(entry, args.path, rules, ctx)
            if isinstance(read, ReviewItem):
                errors.append(read)
            else:
                files[entry.rel_path] = read
    walked = frozenset(e.rel_path for e in entries if e.kind in _WALKED_KINDS)
    return files, errors, walked


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


def _remove_stale(out: Path, keep: Mapping[str, str], media: Sequence[str]) -> None:
    """Remove each ``<medium>-*.csv`` an earlier run left that this one did not write.

    Only the media the rules name count, so an operator's ``notes-2020.csv`` stays.
    Only a file or a link is removed, never a folder, and a link is removed rather
    than followed. ``--out`` holds this tool's marker, so these are its own files.
    """
    globs = [_CSV_GLOB.format(medium=medium) for medium in media]
    for path in out.iterdir():
        named = any(fnmatchcase(path.name, glob) for glob in globs)
        stale = named and path.name not in keep
        if stale and (path.is_symlink() or path.is_file()):
            path.unlink()


def _write_outputs(
    out: Path,
    review: Sequence[ReviewItem],
    csvs: Mapping[str, str],
    media: Sequence[str],
) -> None:
    """Write every CSV, then review.json and review.md, each atomically."""
    for name, text in csvs.items():
        write_atomic(out / name, text)
    _remove_stale(out, csvs, media)
    counts = review_counts(review)
    write_atomic(out / "review.json", review_json(review, counts))
    write_atomic(out / "review.md", review_markdown(review, counts))


def _summary(rows: int, review: Sequence[ReviewItem]) -> list[str]:
    """The row count, then one line per review kind present: counts only."""
    counts = review_counts(review)
    return [f"rows: {rows}"] + [
        f"{kind}: {count}" for kind, count in counts.items() if count
    ]


def _record(
    args: argparse.Namespace,
    rows: int,
    review: Sequence[ReviewItem],
    csvs: Mapping[str, str],
    media: Sequence[str],
) -> None:
    """Prepare ``--out`` and write into it; ``_Refused`` on any failure.

    ``media`` names the rules' media, whose stale CSVs an earlier run left are removed.
    """
    try:
        survey_guard.prepare_out_dir(args.out, tool=TOOL)
        _write_outputs(args.out.resolve(), review, csvs, media)
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None
    except OSError as error:
        # describe_error keeps the class and reason only: the message names the path.
        raise _Refused(f"could not write the output: {describe_error(error)}") from None
    for line in _summary(rows, review):
        print(line)


def _run(
    args: argparse.Namespace,
    run: survey_convert.Runner,
    run_git: survey_guard.GitRunner,
    soffice: Path | None | _Unset,
) -> int:
    """Validate -> act -> record; the number of error items, or ``_Refused``."""
    rules = _load(args.rules)
    _check_out(args, run_git)
    if isinstance(soffice, _Unset):
        soffice = survey_convert.locate_soffice()
    files, read_errors, walked = _read_all(args, rules, run, soffice)
    rows, items = build_rows(files, rules, walked=walked)
    review = [*read_errors, *items]
    media = [medium for medium, _ in rules.media]
    _record(args, len(rows), review, csv_files(rows), media)
    return sum(1 for item in review if item.kind in ERROR_KINDS)


def main(
    argv: list[str] | None = None,
    *,
    run: survey_convert.Runner = survey_convert.real_run,
    run_git: survey_guard.GitRunner = survey_guard.real_git,
    soffice: Path | None | _Unset = _UNSET,
) -> int:
    """Parse ``--path`` into ``--out`` by ``--rules`` and return the exit code.

    ``run``, ``run_git`` and ``soffice`` are the seams the tests replace: the
    converter runner, the git runner and LibreOffice's program (``_UNSET`` finds it).
    """
    # First, before anything is printed: output piped to another program would
    # otherwise use the locale's encoding.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.path.is_dir():
        parser.error("--path must be an existing directory")
    try:
        errors = _run(args, run, run_git, soffice)
    except _Refused as refused:
        print(f"{parser.prog}: {refused}", file=sys.stderr)
        return _EXIT_USAGE
    if errors:
        print(f"{parser.prog}: {errors} error item(s); see review.md", file=sys.stderr)
        return _EXIT_ERRORS
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
