"""retrolog_export: turn retrolog_resolve's CSVs into a service's import files.

The fourth step of the retrolog tools (survey, parse, resolve, export). It reads the
CSV files ``retrolog_resolve`` wrote, without writing there, the operator's export
config, and a fresh export of the operator's account, and writes to ``--out`` the
numbered import files for every item no earlier import sent, plus ``review.json`` and
``review.md``: films for Letterboxd, without stars for a film the owner already
logged there, and books for Goodreads, each once, leaving out a book the account has
read. Nothing is sent anywhere: the operator uploads the files. ``--dry-run`` checks
the same and writes nothing; ``--test-film``, Letterboxd's alone, writes only
``letterboxd-test.csv``, one logged film's rows.

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
    Item,
    Selection,
    select,
)
from export_select import REVIEW_KINDS as REVIEW_KINDS  # noqa: E402
from export_services import SERVICES, ServiceSpec  # noqa: E402 - after the inserts
from export_test_film import any_tagged, pick_test_film  # noqa: E402 - after inserts
from export_types import (  # noqa: E402 - after the src/ inserts
    ResolvedRow,
    ReviewEntry,
    ServiceTable,
)
from export_write import (  # noqa: E402 - after the sys.path inserts above
    other_imports,
    write_alone,
    write_outputs,
)
from resolve_args import MAX_SECONDS, positive  # noqa: E402 - after the src/ inserts
from survey_write import describe_error  # noqa: E402 - after the src/ inserts

TOOL = "retrolog_export"
_EXIT_OK, _EXIT_ERRORS, _EXIT_USAGE = 0, 1, 2
_DEFAULT_TIMEOUT = 15.0
# Each service's file caps are its spec's; the options take up to the largest, and
# a run refuses a value above its own service's.
_MAX_ROWS = max(spec.default_rows for spec in SERVICES.values())
_MIN_BYTES = 1_024
_MAX_BYTES = max(spec.default_bytes for spec in SERVICES.values())
# The one-item test run's file: <file_stem>-test.csv, so a later run's cleanup of
# every <file_stem>-*.csv removes it.
_TEST_SUFFIX = "test"
# The cells of one item's import rows, and of every item's, in the items' order.
_Cells = Sequence[Sequence[str]]
_AllCells = Sequence[_Cells]


class _Refused(Exception):
    """The run may not start or finish; the message names no path and no value."""


def _caps_text(field: str) -> str:
    """Each service's cap ``field``, as ``letterboxd 1900, goodreads 10000``."""
    return ", ".join(f"{name} {getattr(SERVICES[name], field)}" for name in SERVICES)


_EPILOG = (
    "Prerequisites: Python 3.12 or later, standard library only (nothing to "
    "install); retrolog_resolve's output folder; the export config; and a fresh "
    "export of the account, downloaded after the last import: Letterboxd's "
    '"Export your data", as its .zip or unpacked folder, or Goodreads\' '
    '"Export Library", the one goodreads_library_export.csv.\n\n'
    "Run order: after retrolog_resolve (settle its review.md first); then upload "
    "the import files in number order, and download a fresh account export before "
    "the next run. --dry-run first checks the config and prints the counts; "
    "for Letterboxd, --test-film writes one logged-no-stars film to import before "
    "the rest.\n\n"
    "Exit codes: 0 means written (or, with --dry-run, checked), with no error in "
    "the review. 1 means written, but the review lists a held item (a held-* "
    "kind), and every other item was written (--test-film exits 0 here). 2 means "
    "bad arguments, --test-film for a service other than letterboxd, or a "
    "--rows-per-file or --max-bytes above the service's own; an unreadable resolve "
    "folder, config, or account export; a CSV in the resolve folder that is not "
    "resolve's (an older resolve's included: rerun retrolog_resolve); a config "
    "missing a rating key a row carries, with an omit entry naming no row, with no "
    "table for --service, or, for Goodreads, with no undated_date_added while a "
    "row is undated; a cutoff naming no row; an item too large for one file; "
    "--test-film with no logged-no-stars film; --out refused; or the output could "
    "not be written."
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description="Write the import files for every film or book "
        "retrolog_resolve settled that no earlier import sent, and a review list.",
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
        help="A fresh export of the account (read only): Letterboxd's .zip or the "
        "folder it unpacks to; Goodreads' goodreads_library_export.csv.",
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="The export config file."
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory; refused inside --resolved, an --account folder, or a "
        "git work tree, or when holding another service's import files.",
    )
    # No default here: each service's is its own, so the help names them and the
    # run reads the spec's when the option is absent.
    parser.add_argument(
        "--rows-per-file",
        type=whole_number(1, _MAX_ROWS),
        default=argparse.SUPPRESS,
        help="The most data rows one import file holds, from 1 to the service's "
        f"most, which is also its default: {_caps_text('default_rows')}.",
    )
    parser.add_argument(
        "--max-bytes",
        type=whole_number(_MIN_BYTES, _MAX_BYTES),
        default=argparse.SUPPRESS,
        help="The most bytes one import file holds, header included, from "
        f"{_MIN_BYTES} to the service's most, which is also its default: "
        f"{_caps_text('default_bytes')}.",
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
        help="Letterboxd only: write only letterboxd-test.csv, every row of the "
        "logged-no-stars film review.md lists first, to import before the rest.",
    )
    return parser


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _caps[C, A](args: argparse.Namespace, spec: ServiceSpec[C, A]) -> tuple[int, int]:
    """The run's rows and bytes per file: the options', else the service's defaults.

    ``_Refused`` for a value above the service's own most, or ``--test-film`` for a
    service with no test run.
    """
    if args.test_film and not spec.test_run:
        raise _Refused(f"--test-film: {spec.name} has no test run")
    rows = getattr(args, "rows_per_file", spec.default_rows)
    size = getattr(args, "max_bytes", spec.default_bytes)
    if rows > spec.default_rows:
        raise _Refused(f"--rows-per-file: {spec.name} takes 1 to {spec.default_rows}")
    if size > spec.default_bytes:
        raise _Refused(
            f"--max-bytes: {spec.name} takes {_MIN_BYTES} to {spec.default_bytes}"
        )
    return rows, size


def _load_config[C, A](path: Path, spec: ServiceSpec[C, A]) -> C:
    """The config's table for ``spec``; ``_Refused`` naming the key, never the path."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise _Refused(f"could not read --config: {type(error).__name__}") from None
    try:
        config = load_config(data)
    except ConfigError as error:
        raise _Refused(f"invalid --config: {error}") from None
    if (table := spec.config(config)) is None:
        raise _Refused(f"--config: has no [{spec.name}] table")
    return table


def _read_rows[C: ServiceTable, A](
    resolved: Path, spec: ServiceSpec[C, A], config: C
) -> tuple[ResolvedRow, ...]:
    """The resolve folder's rows of ``spec``'s medium.

    Every rating key is configured, every omit entry names one of the rows, and the
    service's own checks pass.
    """
    try:
        rows = read_resolved(resolved, spec.medium)
    except InputError as error:
        raise _Refused(str(error)) from None
    try:
        check_keys(config, rows, where=spec.name)
        check_omit(config, rows, where=spec.name)
        spec.check_rows(config, rows)
    except ConfigError as error:
        raise _Refused(f"invalid --config: {error}") from None
    return rows


def _read_account[C, A](path: Path, spec: ServiceSpec[C, A]) -> A:
    """What the account export lists; ``_Refused`` naming no path or title."""
    try:
        return spec.read_account(path)
    except AccountError as error:
        raise _Refused(f"unusable account export: {error}") from None


def _check_out(
    args: argparse.Namespace, run_git: survey_guard.GitRunner, file_stem: str
) -> None:
    """Refuse an ``--out`` inside ``--resolved``, an ``--account`` folder, or git.

    Or one holding another service's import files, which a run would leave unreviewed
    (it clears only its own). Nothing is created until the files to write are known.
    """
    stems = [service.file_stem for service in SERVICES.values()]
    if others := other_imports(args.out, file_stem, stems):
        raise _Refused(f"refused --out: it holds {', '.join(others)} import files")
    sources = [args.resolved, *([args.account] if args.account.is_dir() else [])]
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


def _formula_review[C, A](
    spec: ServiceSpec[C, A], items: Sequence[Item], cells: _AllCells
) -> list[ReviewEntry]:
    """The ``formula-cell`` entries for the items to send."""
    checked = {name: spec.columns.index(name) for name in spec.formula_columns}
    return formula_entries(items, cells, checked)


def _import_texts[C, A](
    spec: ServiceSpec[C, A], cells: _AllCells, caps: tuple[int, int]
) -> tuple[str, ...]:
    """Each import file's text, the items' rows packed whole under the two caps."""
    max_rows, max_bytes = caps
    try:
        return pack(
            spec.columns,
            cells,
            max_rows=max_rows,
            max_bytes=max_bytes,
            noun=spec.medium,
        )
    except PackError as error:
        raise _Refused(f"{error}: raise --rows-per-file or --max-bytes") from None


def _select[C: ServiceTable, A](
    rows: Sequence[ResolvedRow], spec: ServiceSpec[C, A], config: C, account: A
) -> Selection:
    """``export_select.select`` on the run's inputs; ``_Refused`` for a bad cutoff."""
    try:
        return select(
            rows,
            spec=spec,
            config=config,
            account=account,
            cutoff=config.cutoff,
            omit=config.omit,
        )
    except CutoffError as error:
        raise _Refused(str(error)) from None


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


def _prepare_out(args: argparse.Namespace) -> Path:
    """Create the checked ``--out`` with its marker, and return it resolved."""
    try:
        survey_guard.prepare_out_dir(args.out, tool=TOOL)
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None
    return args.out.resolve()


def _record[C, A](
    args: argparse.Namespace,
    spec: ServiceSpec[C, A],
    files: Mapping[str, str],
    review: tuple[str, str],
    counts: Mapping[str, int],
) -> None:
    """Write the outputs and print the summary; ``_Refused`` on a write failure.

    ``--out`` holds this tool's marker, so the earlier files cleared are its own.
    """
    out = _prepare_out(args)
    try:
        write_outputs(out, spec.file_stem, files, review)
    except OSError as error:
        # describe_error keeps the class and reason only: the message names the path.
        raise _Refused(f"could not write the output: {describe_error(error)}") from None
    for line in summary_lines(counts, medium=spec.medium):
        print(line)


def _test_run[C, A](
    args: argparse.Namespace,
    spec: ServiceSpec[C, A],
    films: Sequence[Item],
    cells: _AllCells,
    caps: tuple[int, int],
) -> None:
    """Write the test file of ``pick_test_film``'s film alone, and print its counts.

    A formula-cell count is printed too, since no review is written to list it.
    """
    index = pick_test_film(films, [any_tagged(spec, rows) for rows in cells])
    if index is None:
        raise _Refused("no logged-no-stars film to test: run without --test-film")
    (text,) = _import_texts(spec, [cells[index]], caps)
    name = f"{spec.file_stem}-{_TEST_SUFFIX}.csv"
    out = _prepare_out(args)
    try:
        write_alone(out, spec.file_stem, name, text)
    except OSError as error:
        raise _Refused(f"could not write the output: {describe_error(error)}") from None
    print(f"test film rows: {len(films[index].rows)}")
    print(f"file: {name}")
    if flagged := _formula_review(spec, [films[index]], [cells[index]]):
        print(f"review formula-cell: {len(flagged)}")


def _run_service[C: ServiceTable, A](
    args: argparse.Namespace, spec: ServiceSpec[C, A], run_git: survey_guard.GitRunner
) -> int:
    """Validate -> act -> record for ``spec``; the number of error entries.

    A test run records only its one file and counts no error; a dry run records
    nothing and prints the summary a run would. ``_Refused`` stops either.
    """
    caps = _caps(args, spec)
    config = _load_config(args.config, spec)
    rows = _read_rows(args.resolved, spec, config)
    account = _read_account(args.account, spec)
    _check_out(args, run_git, spec.file_stem)
    selection = _select(rows, spec, config, account)
    cells = [spec.item_cells(item, config) for item in selection.films]
    if args.test_film:
        _test_run(args, spec, selection.films, cells, caps)
        return 0
    entries = [*selection.review, *_formula_review(spec, selection.films, cells)]
    texts = _import_texts(spec, cells, caps)
    counts = review_counts(
        entries,
        medium=spec.medium,
        items_sent=len(selection.films),
        rows_sent=sum(len(item.rows) for item in selection.films),
        rows_cut=selection.cut,
        files=len(texts),
    )
    if args.dry_run:
        print("\n".join(summary_lines(counts, medium=spec.medium)))
    else:
        review = (
            review_json(entries, counts=counts),
            review_markdown(entries, counts=counts, medium=spec.medium),
        )
        _record(args, spec, import_files(spec, texts), review, counts)
    return sum(1 for entry in entries if entry.kind in ERROR_KINDS)


def _run(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> int:
    """``_run_service`` for ``--service``'s spec, whichever service's types it has."""
    spec = SERVICES[args.service]
    return spec.apply(lambda chosen: _run_service(args, chosen, run_git))


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
