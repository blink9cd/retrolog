"""retrolog_export: turn retrolog_resolve's film CSVs into a service's import files.

The fourth step of the retrolog tools (survey, parse, resolve, export). It reads the
CSV files ``retrolog_resolve`` wrote, without writing there, the operator's export
config, and a fresh export of the operator's account, and writes to ``--out`` the
numbered import files for every film no earlier import sent, without stars for a film
the owner already logged there, plus ``review.json`` and ``review.md``. Nothing is
sent anywhere: the operator uploads the files. ``--dry-run`` checks the same and writes
nothing; ``--test-film`` writes only ``letterboxd-test.csv``, one logged film's rows.

The stages live in ``src/`` here; retrolog_resolve's ``src/`` supplies its columns,
statuses, creator joiner, and ``--timeout`` rule, retrolog_parse's the CSV columns and
names, title matching, and value checks, and retrolog_survey's the output guard,
atomic writes, and plural helper. All of them, and the shared libraries this file
imports, go on ``sys.path`` from its own location, so the tool can be moved.
``retrolog_export.md`` describes the tool, its config, and its output.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
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
# This tool's private modules, then resolve's, parse's, and the survey's (see above).
SOURCE_DIRS = (
    _HERE / "src",
    _HERE.parent / "retrolog_resolve" / "src",
    _HERE.parent / "retrolog_parse" / "src",
    _HERE.parent / "retrolog_survey" / "src",
)
sys.path[:0] = [str(folder) for folder in SOURCE_DIRS if str(folder) not in sys.path]

import survey_guard  # noqa: E402 - after the src/ inserts
from export_args import whole_number  # noqa: E402 - after the src/ inserts
from export_account import AccountError  # noqa: E402 - after the src/ inserts
from export_config import (  # noqa: E402 - after the src/ inserts
    ConfigError,
    check_keys,
    check_omit,
    load_config,
)
from export_cutoff import CutoffError  # noqa: E402 - after the src/ inserts
from export_formula import formula_entries  # noqa: E402 - after the src/ inserts
from export_input import InputError, read_resolved  # noqa: E402 - after the inserts
from export_output import (  # noqa: E402 - after the src/ inserts
    import_files,
    review_counts,
    review_json,
    review_markdown,
    summary_lines,
)
from export_pack import PackError, pack  # noqa: E402 - after the src/ inserts

# REVIEW_KINDS is re-exported (the "as" form) so a reader of the entry point, and its
# tests, find the review vocabulary here.
from export_select import (  # noqa: E402 - after the src/ inserts
    ERROR_KINDS,
    Film,
    Selection,
    select,
)
from export_select import REVIEW_KINDS as REVIEW_KINDS  # noqa: E402
from export_services import SERVICES, ServiceSpec  # noqa: E402 - after the inserts
from export_test_film import pick_test_film  # noqa: E402 - after the src/ inserts
from export_types import (  # noqa: E402 - after the src/ inserts
    Account,
    LetterboxdConfig,
    ResolvedRow,
    ReviewEntry,
)
from resolve_args import MAX_SECONDS, positive  # noqa: E402 - after the src/ inserts
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
# The one-film test run's file: <file_stem>-test.csv, so a later run's cleanup of
# every <file_stem>-*.csv removes it.
_TEST_SUFFIX = "test"


class _Refused(Exception):
    """The run may not start or finish; the message names no path and no value."""


_EPILOG = (
    "Prerequisites: Python 3.12 or later, standard library only (nothing to "
    "install); retrolog_resolve's output folder; the export config; and a fresh "
    'export of the account (Letterboxd\'s "Export your data"), '
    "downloaded after the last import, as its .zip or unpacked folder.\n\n"
    "Run order: after retrolog_resolve (settle its review.md first); then upload "
    "the import files in number order, and download a fresh account export before "
    "the next run. --dry-run first checks the config and prints the counts; "
    "--test-film writes one logged-no-stars film to import before the rest.\n\n"
    "Exit codes: 0 means written (or, with --dry-run, checked), with no error in "
    "the review. 1 means written, but the review lists a held film (a held-* "
    "kind), and every other film was written (--test-film exits 0 here). 2 means "
    "bad "
    "arguments; an unreadable resolve folder, config, or account export; a CSV "
    "in the resolve folder that is not resolve's (an older resolve's included: "
    "rerun retrolog_resolve); a config missing a rating key a row carries, with an "
    "omit entry naming no row, or with no table for --service; a cutoff naming no "
    "row; a film too large for one file; --test-film with no logged-no-stars film; "
    "--out refused; or the output could not be written."
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description="Write the import files for every film retrolog_resolve "
        "settled that no earlier import sent, without stars for a film already "
        "logged on the account, and a review list.",
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
        type=whole_number(1, _MAX_ROWS),
        default=_DEFAULT_ROWS,
        help=f"The most data rows one import file holds, 1 to {_MAX_ROWS}.",
    )
    parser.add_argument(
        "--max-bytes",
        type=whole_number(_MIN_BYTES, _MAX_BYTES),
        default=_MAX_BYTES,
        help=f"The most bytes one import file holds, header included, {_MIN_BYTES} "
        f"to {_MAX_BYTES}.",
    )
    parser.add_argument(
        "--timeout",
        type=positive,
        default=_DEFAULT_TIMEOUT,
        help="Seconds per git call of the --out check: greater than 0 and at most "
        f"{MAX_SECONDS:g}.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Check everything a run checks and print its counts, but write "
        "nothing, not even --out.",
    )
    mode.add_argument(
        "--test-film",
        action="store_true",
        help="Write only letterboxd-test.csv: every row of the logged-no-stars film "
        "review.md lists first, to import before the rest.",
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
    """The resolve folder's rows of ``spec``'s medium.

    Every rating key is configured, and every omit entry names one of the rows.
    """
    try:
        rows = read_resolved(resolved, spec.medium)
    except InputError as error:
        raise _Refused(str(error)) from None
    try:
        check_keys(config, rows)
        check_omit(config, rows)
    except ConfigError as error:
        raise _Refused(f"invalid --config: {error}") from None
    return rows


def _read_account(path: Path, spec: ServiceSpec) -> Account:
    """What the account export lists, file by file; ``_Refused`` naming no path or title."""
    try:
        return spec.read_account(path)
    except AccountError as error:
        raise _Refused(f"unusable account export: {error}") from None


def _check_out(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> None:
    """Refuse an ``--out`` inside ``--resolved``, an ``--account`` folder, or git.

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
    """Each film's import rows, one cell per ``spec.columns`` entry.

    A film the owner logged on the account goes with its ratings withheld.
    """
    return [
        [spec.row_cells(row, config, film.withhold_rating) for row in film.rows]
        for film in films
    ]


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
    for line in summary_lines(counts):
        print(line)


def _write_test_file(args: argparse.Namespace, spec: ServiceSpec, text: str) -> None:
    """Clear the earlier run's files, then write the test file alone; no review."""
    out = _prepare_out(args)
    try:
        _clear_previous(out, spec)
        write_atomic(out / f"{spec.file_stem}-{_TEST_SUFFIX}.csv", text)
    except OSError as error:
        raise _Refused(f"could not write the output: {describe_error(error)}") from None


def _any_tagged(spec: ServiceSpec, rows: Sequence[Sequence[str]]) -> bool:
    """Whether the import tags at least one of a film's ``rows``: see ``tag_columns``.

    One is enough: the next run skips every row of a film any tagged row names.
    """
    at = [spec.columns.index(column) for column in spec.tag_columns]
    return any(any(cells[i] for i in at) for cells in rows)


def _test_run(
    args: argparse.Namespace,
    spec: ServiceSpec,
    films: Sequence[Film],
    cells: Sequence[Sequence[Sequence[str]]],
) -> None:
    """Write the test file of ``pick_test_film``'s film, and print its counts.

    A formula-cell count is printed too, since no review is written to list it.
    """
    index = pick_test_film(films, [_any_tagged(spec, rows) for rows in cells])
    if index is None:
        raise _Refused("no logged-no-stars film to test: run without --test-film")
    (text,) = _import_texts(args, spec, [cells[index]])
    _write_test_file(args, spec, text)
    print(f"test film rows: {len(films[index].rows)}")
    print(f"file: {spec.file_stem}-{_TEST_SUFFIX}.csv")
    if flagged := _formula_review(spec, [films[index]], [cells[index]]):
        print(f"review formula-cell: {len(flagged)}")


def _select(
    rows: Sequence[ResolvedRow],
    spec: ServiceSpec,
    config: LetterboxdConfig,
    account: Account,
) -> Selection:
    """``export_select.select`` on the run's inputs; ``_Refused`` for a bad cutoff."""
    try:
        return select(
            rows,
            cutoff=config.cutoff,
            account=account,
            on_service=spec.on_service,
            watched_dates=config.watched_date,
            omit=config.omit,
        )
    except CutoffError as error:
        raise _Refused(str(error)) from None


def _run(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> int:
    """Validate -> act -> record; the number of error entries, or ``_Refused``.

    A test run records only its one file and counts no error; a dry run records
    nothing and prints the summary a run would.
    """
    spec = SERVICES[args.service]
    config = _load_config(args.config, spec)
    rows = _read_rows(args.resolved, spec, config)
    account = _read_account(args.account, spec)
    _check_out(args, run_git)
    selection = _select(rows, spec, config, account)
    cells = _import_cells(spec, config, selection.films)
    if args.test_film:
        _test_run(args, spec, selection.films, cells)
        return 0
    entries = [*selection.review, *_formula_review(spec, selection.films, cells)]
    texts = _import_texts(args, spec, cells)
    counts = review_counts(
        entries,
        films_sent=len(selection.films),
        rows_sent=sum(len(film.rows) for film in selection.films),
        rows_cut=selection.cut,
        files=len(texts),
    )
    if args.dry_run:
        print("\n".join(summary_lines(counts)))
    else:
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
        # A dry run wrote no review.md to point at.
        where = (
            "rerun without --dry-run to write review.md"
            if args.dry_run
            else "see review.md"
        )
        print(f"{parser.prog}: {errors} error item(s); {where}", file=sys.stderr)
        return _EXIT_ERRORS
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
