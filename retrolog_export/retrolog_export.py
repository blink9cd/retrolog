"""retrolog_export: turn retrolog_resolve's film CSVs into a service's import files.

The fourth step of the retrolog tools (survey, parse, resolve, export). It reads the
CSV files ``retrolog_resolve`` wrote, without writing there, the operator's export
config and a fresh export of the operator's account, and writes to ``--out`` the
numbered import files for every film the account does not hold yet, plus
``review.json`` and ``review.md``. Nothing is sent anywhere: the operator uploads the
files.

The stages live in ``src/`` beside this file; the sibling retrolog_resolve's ``src/``
supplies the columns it adds and its statuses, retrolog_parse's the CSV columns, title
matching and value checks, and retrolog_survey's the output guard and atomic writes.
All of them, and the shared libraries this file imports, are put on ``sys.path`` from
this file's own location, so the tool can be moved.
``retrolog_export.md`` describes the tool, its config and its output.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
# Nothing here fetches, and the resolve modules export reads (its columns and
# statuses) import no network code, so the hardened fetch library is not asked for.
sys.path.append(str(Path(__file__).resolve().parents[1] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")

import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_HERE = Path(__file__).resolve().parent
# This tool's private modules, then resolve's (its columns and statuses), parse's
# (columns, title_key, the value checks) and the survey's (the output guard, atomic
# writes).
SOURCE_DIRS = (
    _HERE / "src",
    _HERE.parent / "retrolog_resolve" / "src",
    _HERE.parent / "retrolog_parse" / "src",
    _HERE.parent / "retrolog_survey" / "src",
)
sys.path[:0] = [str(folder) for folder in SOURCE_DIRS if str(folder) not in sys.path]

import survey_guard  # noqa: E402 - after the src/ inserts
from export_account import AccountError  # noqa: E402 - after the src/ inserts
from export_config import ConfigError, check_keys, load_config  # noqa: E402
from export_input import InputError, read_resolved  # noqa: E402 - after the inserts
from export_output import (  # noqa: E402 - after the src/ inserts
    import_files,
    review_counts,
    review_json,
    review_markdown,
)
from export_pack import PackError, pack  # noqa: E402 - after the src/ inserts

# REVIEW_KINDS is re-exported (the "as" form) so a reader of the entry point, and its
# tests, find the review vocabulary here.
from export_select import (  # noqa: E402 - after the src/ inserts
    ERROR_KINDS,
    CutoffError,
    Film,
    formula_entries,
    select,
)
from export_select import REVIEW_KINDS as REVIEW_KINDS  # noqa: E402
from export_services import SERVICES, ServiceSpec  # noqa: E402 - after the inserts
from export_types import (  # noqa: E402 - after the src/ inserts
    FilmKey,
    LetterboxdConfig,
    ResolvedRow,
    ReviewEntry,
)
from survey_write import describe_error, write_atomic  # noqa: E402

TOOL = "retrolog_export"
_EXIT_OK, _EXIT_ERRORS, _EXIT_USAGE = 0, 1, 2
_DEFAULT_TIMEOUT = 15.0
# Letterboxd's importer takes up to 1,900 titles a file, and files of at most 1 MB;
# 1,000,000 bytes is under 1 MB however the megabyte is counted.
_MAX_ROWS, _DEFAULT_ROWS = 1_900, 1_900
_MIN_BYTES, _MAX_BYTES = 1_024, 1_000_000
# The review's two files, in the order written: always after every import file.
_REVIEW_NAMES = ("review.json", "review.md")


class _Refused(Exception):
    """The run may not start or finish; the message names no path and no value."""


def _positive(text: str) -> float:
    """An argparse type: a number of seconds greater than zero."""
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _whole_number(low: int, high: int) -> Callable[[str], int]:
    """An argparse type: a whole number from ``low`` to ``high``, both included."""

    def parse(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be {low} to {high}")
        return value

    return parse


_EPILOG = (
    "Prerequisites: Python 3.12 or later, standard library only (nothing to "
    "install); retrolog_resolve's output folder; the export config; and a fresh "
    'export of the account (Letterboxd\'s "Export your data"), '
    "downloaded after the last import, as its .zip or unpacked folder.\n\n"
    "Run order: after retrolog_resolve (settle its review.md first); then upload "
    "the import files in number order, and download a fresh account export before "
    "the next run.\n\n"
    "Exit codes: 0 written, no error in the review - 1 written, but the review "
    "lists a held film (a held-* kind), and every other film was written - 2 bad "
    "arguments; an unreadable resolve folder, config, or account export; a CSV "
    "in the resolve folder that is not resolve's (an older resolve's included: "
    "rerun retrolog_resolve); a config missing a rating key a row carries, or "
    "with no table for --service; a cutoff naming no row; a film too large for "
    "one file; --out refused; or the output could not be written."
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description="Write the import files for every film retrolog_resolve "
        "settled that the account does not hold yet, and a review list.",
        # Shows each default, and none on a required option (the stock formatter
        # stamps "(default: None)" there before CPython 3.13; the floor is 3.12).
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--service",
        choices=sorted(SERVICES),
        required=True,
        help="The service to write import files for.",
    )
    parser.add_argument(
        "--resolved",
        type=Path,
        required=True,
        help="retrolog_resolve's output folder (read only).",
    )
    parser.add_argument(
        "--account",
        type=Path,
        required=True,
        help="A fresh export of the account: its .zip, or the folder it unpacks "
        "to (read only).",
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="The export config file."
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory; refused inside --resolved, inside an --account "
        "folder, or inside a git work tree.",
    )
    parser.add_argument(
        "--rows-per-file",
        type=_whole_number(1, _MAX_ROWS),
        default=_DEFAULT_ROWS,
        help=f"The most data rows one import file holds, 1 to {_MAX_ROWS}.",
    )
    parser.add_argument(
        "--max-bytes",
        type=_whole_number(_MIN_BYTES, _MAX_BYTES),
        default=_MAX_BYTES,
        help=f"The most bytes one import file holds, header included, {_MIN_BYTES} "
        f"to {_MAX_BYTES}.",
    )
    parser.add_argument(
        "--timeout",
        type=_positive,
        default=_DEFAULT_TIMEOUT,
        help="Seconds per git call of the --out check.",
    )
    return parser


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _load_config(path: Path, spec: ServiceSpec) -> LetterboxdConfig:
    """The config's table for ``spec``; ``_Refused`` naming the key, never the path."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise _Refused(f"could not read --config: {type(error).__name__}") from None
    try:
        config = load_config(data)
    except ConfigError as error:
        raise _Refused(f"invalid --config: {error}") from None
    if config.letterboxd is None:
        raise _Refused(f"--config: has no [{spec.name}] table")
    return config.letterboxd


def _read_rows(
    resolved: Path, spec: ServiceSpec, config: LetterboxdConfig
) -> tuple[ResolvedRow, ...]:
    """The resolve folder's rows of ``spec``'s medium, every rating key configured."""
    try:
        rows = read_resolved(resolved, spec.medium)
    except InputError as error:
        raise _Refused(str(error)) from None
    try:
        check_keys(config, rows)
    except ConfigError as error:
        raise _Refused(f"invalid --config: {error}") from None
    return rows


def _read_account(path: Path, spec: ServiceSpec) -> frozenset[FilmKey]:
    """The films the account export lists; ``_Refused`` naming no path or title."""
    try:
        return spec.read_account(path)
    except AccountError as error:
        raise _Refused(f"unusable account export: {error}") from None


def _check_out(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> None:
    """Refuse an ``--out`` inside ``--resolved``, an ``--account`` folder or git.

    Nothing is created: the folder is made only once the files to write are known.
    """
    sources = [args.resolved]
    if args.account.is_dir():
        sources.append(args.account)
    try:
        for source in sources:
            survey_guard.check_out_dir(
                args.out, source, run_git=run_git, timeout=args.timeout, tool=TOOL
            )
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None


# ---------------------------------------------------------------------------
# Act
# ---------------------------------------------------------------------------


def _import_cells(
    spec: ServiceSpec, config: LetterboxdConfig, films: Sequence[Film]
) -> list[list[tuple[str, ...]]]:
    """Each film's import rows, one cell per ``spec.columns`` entry."""
    return [[spec.row_cells(row, config) for row in film.rows] for film in films]


def _formula_review(
    spec: ServiceSpec, films: Sequence[Film], cells: Sequence[Sequence[Sequence[str]]]
) -> list[ReviewEntry]:
    """The ``formula-cell`` entries for the films to send."""
    checked = {name: spec.columns.index(name) for name in spec.formula_columns}
    return formula_entries(films, cells, checked)


def _import_texts(
    args: argparse.Namespace,
    spec: ServiceSpec,
    cells: Sequence[Sequence[Sequence[str]]],
) -> tuple[str, ...]:
    """Each import file's text, the films' rows packed whole under the two caps."""
    try:
        return pack(
            spec.columns, cells, max_rows=args.rows_per_file, max_bytes=args.max_bytes
        )
    except PackError as error:
        raise _Refused(f"{error}: raise --rows-per-file or --max-bytes") from None


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


def _clear_previous(out: Path, spec: ServiceSpec) -> None:
    """Remove an earlier run's review and every ``<file_stem>-*.csv``, before writing.

    So a run that fails partway leaves no file of an earlier run beside its own, and
    no review: the review is written last, and its absence marks an incomplete run.
    Only a file or a link is removed, never a folder, and a link is removed rather
    than followed. ``--out`` holds this tool's marker, so these are its own files.
    """
    for path in out.iterdir():
        named = path.name in _REVIEW_NAMES or fnmatchcase(
            path.name, f"{spec.file_stem}-*.csv"
        )
        if named and (path.is_symlink() or path.is_file()):
            path.unlink()


def _write_outputs(
    out: Path,
    spec: ServiceSpec,
    files: Mapping[str, str],
    review: tuple[str, str],
) -> None:
    """Clear the earlier run's files, write every import file, then the review."""
    _clear_previous(out, spec)
    for name, text in files.items():
        write_atomic(out / name, text)
    for name, text in zip(_REVIEW_NAMES, review, strict=True):
        write_atomic(out / name, text)


def _prepare_out(args: argparse.Namespace) -> Path:
    """Create the checked ``--out`` with its marker, and return it resolved."""
    try:
        survey_guard.prepare_out_dir(args.out, tool=TOOL)
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None
    return args.out.resolve()


def _summary(counts: Mapping[str, int]) -> list[str]:
    """The run's totals, then each review kind that occurred: counts only."""
    lines = [
        f"films sent: {counts['films-sent']}",
        f"rows sent: {counts['rows-sent']}",
        f"rows cut: {counts['rows-cut']}",
        f"files: {counts['files']}",
    ]
    return lines + [
        f"review {kind}: {counts[kind]}" for kind in REVIEW_KINDS if counts[kind]
    ]


def _record(
    args: argparse.Namespace,
    spec: ServiceSpec,
    files: Mapping[str, str],
    review: tuple[str, str],
    counts: Mapping[str, int],
) -> None:
    """Write the outputs and print the summary; ``_Refused`` on a write failure."""
    out = _prepare_out(args)
    try:
        _write_outputs(out, spec, files, review)
    except OSError as error:
        # describe_error keeps the class and reason only: the message names the path.
        raise _Refused(f"could not write the output: {describe_error(error)}") from None
    for line in _summary(counts):
        print(line)


def _run(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> int:
    """Validate -> act -> record; the number of error entries, or ``_Refused``."""
    spec = SERVICES[args.service]
    config = _load_config(args.config, spec)
    rows = _read_rows(args.resolved, spec, config)
    account = _read_account(args.account, spec)
    _check_out(args, run_git)
    try:
        selection = select(
            rows, cutoff=config.cutoff, account=account, on_service=spec.on_service
        )
    except CutoffError as error:
        raise _Refused(str(error)) from None
    cells = _import_cells(spec, config, selection.films)
    entries = [*selection.review, *_formula_review(spec, selection.films, cells)]
    texts = _import_texts(args, spec, cells)
    counts = review_counts(
        entries,
        films_sent=len(selection.films),
        rows_sent=sum(len(film.rows) for film in selection.films),
        rows_cut=selection.cut,
        files=len(texts),
    )
    review = (
        review_json(entries, counts=counts),
        review_markdown(entries, counts=counts),
    )
    _record(args, spec, import_files(spec, texts), review, counts)
    return sum(1 for entry in entries if entry.kind in ERROR_KINDS)


def main(
    argv: list[str] | None = None,
    *,
    run_git: survey_guard.GitRunner = survey_guard.real_git,
) -> int:
    """Export ``--resolved`` into ``--out`` and return the exit code.

    ``run_git`` is the seam the tests replace: the git runner of the ``--out`` check.
    """
    # First, before anything is printed: output piped to another program would
    # otherwise use the locale's encoding.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        errors = _run(args, run_git)
    except _Refused as refused:
        print(f"{parser.prog}: {refused}", file=sys.stderr)
        return _EXIT_USAGE
    if errors:
        print(f"{parser.prog}: {errors} error item(s); see review.md", file=sys.stderr)
        return _EXIT_ERRORS
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
