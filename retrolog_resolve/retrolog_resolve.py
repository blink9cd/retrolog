"""retrolog_resolve: give each parsed row a catalog id, from Open Library or TMDb.

The third step of the retrolog tools (survey, parse, resolve, export). It reads the
CSV files ``retrolog_parse`` wrote to ``--parsed`` without writing there, applies the
operator's overrides file, looks each distinct book title up on Open Library and each
film title on TMDb, and writes every CSV back to ``--out`` with six columns added,
plus ``review.json`` and ``review.md``. Every answer is cached under ``<out>/cache``,
so a re-run after editing the overrides sends only what no earlier run had answered:
a new override's ``tmdb_id`` details, the search for a title whose override was
removed, and any request that failed.

The stages live in ``src/`` beside this file; the sibling retrolog_parse's ``src/``
supplies the CSV columns and title matching, and retrolog_survey's the output guard
and atomic writes. All three are put on ``sys.path`` from this file's own location,
and the shared libraries (bounded_fetch among them) through the project's bootstrap,
so the tool can be moved.
``retrolog_resolve.md`` describes the tool, its overrides file and its output.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import typing
from collections.abc import Callable, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[1] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter", "bounded_fetch")

import bounded_fetch  # noqa: E402 - after the bootstrap above
import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_HERE = Path(__file__).resolve().parent
# This tool's private modules, then parse's (columns, title_key, the value checks) and
# the survey's (the output guard, atomic writes).
SOURCE_DIRS = (
    _HERE / "src",
    _HERE.parent / "retrolog_parse" / "src",
    _HERE.parent / "retrolog_survey" / "src",
)
sys.path[:0] = [str(folder) for folder in SOURCE_DIRS if str(folder) not in sys.path]

import resolve_args  # noqa: E402 - after the src/ inserts
import resolve_http  # noqa: E402 - after the src/ inserts
import survey_guard  # noqa: E402 - after the src/ inserts
from resolve_input import InputError, read_parsed  # noqa: E402 - after the inserts
from resolve_output import (  # noqa: E402 - after the src/ inserts
    csv_files,
    review_counts,
    review_json,
    review_markdown,
)
from resolve_overrides import OverridesError, load_overrides  # noqa: E402
from resolve_rows import (  # noqa: E402 - after the src/ inserts
    ERROR_KINDS,
    OutputRow,
    resolve_all,
)

# REVIEW_KINDS is re-exported (the "as" form) so a reader of the entry point, and its
# tests, find the review vocabulary here.
from resolve_rows import REVIEW_KINDS as REVIEW_KINDS  # noqa: E402
from resolve_token import TokenFileError, read_token_file  # noqa: E402
from resolve_types import (  # noqa: E402 - after the src/ inserts
    Directors,
    InputRow,
    ResolveOverride,
    ReviewEntry,
    RunMedium,
    Status,
)
from survey_write import describe_error, write_atomic  # noqa: E402

TOOL = "retrolog_resolve"
_EXIT_OK, _EXIT_ERRORS, _EXIT_USAGE = 0, 1, 2
# The environment variables this tool reads. Neither value is ever printed.
TOKEN_ENV = "RETROLOG_TMDB_TOKEN"
CONTACT_ENV = "RETROLOG_CONTACT"
# Where the TMDb token is read from when TOKEN_ENV is unset. Kept unexpanded, so
# --help shows the tilde form and no machine path; expanded only when it is read.
DEFAULT_TOKEN_FILE = "~/.config/retrolog/tmdb.env"
# What a refusal calls a token that came from the file, never its path.
_TOKEN_FILE_SOURCE = "the token file"
_DEFAULT_TIMEOUT = 15.0
# A colliding film goes to the most-voted when it has this many times the
# runner-up's votes.
_DEFAULT_VOTE_RATIO = 10.0
# A single exact title with fewer TMDb votes than this goes to review.
_DEFAULT_VOTE_FLOOR = 1
_CACHE_DIR = "cache"
# What a CSV this tool writes is named like, per medium, so a re-run can remove one
# that parse no longer writes: "<medium>-<year>.csv". A single-medium run removes only
# its own medium's, leaving the other medium's output from an earlier run as it was.
_CSV_GLOBS: Mapping[RunMedium, tuple[str, ...]] = {
    "book": ("book-*.csv",),
    "film": ("film-*.csv",),
    "all": ("book-*.csv", "film-*.csv"),
}
_NEEDS_TOKEN = (
    "a film row needs a TMDb token: put it in the token file (--token-file), set "
    f"{TOKEN_ENV}, or run with --cache-only"
)


class _Refused(Exception):
    """The run may not start or finish; the message names no path and no secret."""


_EPILOG = (
    "Prerequisites: Python 3.12 or later, standard library only (nothing to "
    "install); retrolog_parse's output folder; for film rows, a TMDb API read "
    f"access token (not needed with --cache-only): in {TOKEN_ENV}, which wins when "
    "set, or else in the --token-file file as one "
    f"{TOKEN_ENV}=<token> line, readable by its owner only (mode 600; a missing "
    "default file is no token, a missing named one is refused). "
    f"{CONTACT_ENV}, when set, is sent to Open Library inside the User-Agent so "
    "it can reach the operator.\n\n"
    "Run order: after retrolog_parse (read its review.md first), before "
    "retrolog_export; re-run with --overrides until review.md is settled.\n\n"
    "Exit codes: 0 means written, with no error in the review. 1 means written, "
    "but the review lists an error (override-unused, override-mismatch, or "
    "failed). 2 means bad arguments, an unreadable parse folder or overrides "
    "file, a missing or refused token, an unreadable, malformed, or too-open "
    "token file, an unusable contact, "
    "--out refused, or the output could not be written."
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description="Give each row retrolog_parse wrote a catalog id: an ISBN-13 "
        "and Open Library work for a book, a TMDb id for a film.",
        # Shows each default, and none on a required option (the stock formatter
        # stamps "(default: None)" there before CPython 3.13; the floor is 3.12).
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--parsed",
        type=Path,
        required=True,
        help="retrolog_parse's output folder (read only).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory; refused inside --parsed or a git work tree.",
    )
    parser.add_argument(
        "--medium",
        choices=typing.get_args(RunMedium),
        default="all",
        help="Which media to read and resolve: book or film reads only that "
        "medium's CSVs, sends no request for the other, and leaves the other's "
        "earlier output in --out as it was.",
    )
    parser.add_argument("--overrides", type=Path, help="The overrides file.")
    parser.add_argument(
        "--token-file",
        type=Path,
        default=DEFAULT_TOKEN_FILE,
        help=f"The TMDb token file, read only when {TOKEN_ENV} is unset or empty "
        "and a film row needs a request.",
    )
    parser.add_argument(
        "--directors",
        choices=typing.get_args(Directors),
        default="ambiguous",
        help="Which films get their directors from TMDb credits: none, the "
        "candidates an ambiguous film lists, or those and every resolved film.",
    )
    parser.add_argument(
        "--vote-ratio",
        type=resolve_args.vote_ratio,
        default=_DEFAULT_VOTE_RATIO,
        help="A film whose year-kept candidates hold two or more exact titles "
        "resolves to the most-voted when its TMDb votes are at least this many times "
        "every other's; 0 turns the rule off, and any other value must be a finite "
        "number above 1. Books never take it.",
    )
    parser.add_argument(
        "--vote-floor",
        type=resolve_args.vote_floor,
        default=_DEFAULT_VOTE_FLOOR,
        help="A film's single exact title with fewer TMDb votes than this, or an "
        "unknown count, goes to review instead of settling; 0 turns this one signal "
        "off. Must be a whole number of 0 or more. Books never take it.",
    )
    parser.add_argument(
        "--research",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Send at most one extra TMDb search per film with no exact title, for "
        "its title without a leading The, A, or An. Books never take it.",
    )
    parser.add_argument(
        "--timeout",
        type=resolve_args.positive,
        default=_DEFAULT_TIMEOUT,
        help="Seconds per request or git call: greater than 0 and at most "
        f"{resolve_args.MAX_SECONDS:g}.",
    )
    cache = parser.add_mutually_exclusive_group()
    cache.add_argument(
        "--refresh",
        action="store_true",
        help="Ask every service again, ignoring what earlier runs cached and "
        "rewriting it.",
    )
    cache.add_argument(
        "--cache-only",
        action="store_true",
        help="Send no request: every lookup the cache cannot answer is failed. "
        "No token is needed.",
    )
    return parser


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _load_overrides(
    path: Path | None, medium: RunMedium
) -> tuple[ResolveOverride, ...]:
    """The checked overrides for ``medium``, or none; ``_Refused`` never naming the path.

    The whole file is checked, but an entry for a medium the run did not select is
    dropped: it is out of scope for this run, not unused.
    """
    if path is None:
        return ()
    try:
        data = path.read_bytes()
    except OSError as error:
        raise _Refused(f"could not read --overrides: {type(error).__name__}") from None
    try:
        overrides = load_overrides(data)
    except OverridesError as error:
        raise _Refused(f"invalid --overrides: {error}") from None
    return tuple(entry for entry in overrides if medium in ("all", entry.medium))


def _read_rows(parsed: Path, medium: RunMedium) -> tuple[InputRow, ...]:
    """The parse folder's ``medium`` rows; ``_Refused`` naming the CSV, never a path."""
    try:
        return read_parsed(parsed, medium)
    except InputError as error:
        raise _Refused(str(error)) from None


def _open_library(environ: Mapping[str, str]) -> resolve_http.Service:
    """Open Library, its User-Agent carrying ``CONTACT_ENV`` when that is set."""
    try:
        return resolve_http.open_library(environ.get(CONTACT_ENV))
    except ValueError as error:
        raise _Refused(f"{CONTACT_ENV}: {error}") from None


def _file_token(token_file: Path) -> str | None:
    """The token ``--token-file`` holds, or None; ``_Refused`` naming no path.

    A missing file at the default path is no token, so a run without one ends with
    the needs-a-token refusal; a missing file the operator named is refused.
    """
    required = token_file != Path(DEFAULT_TOKEN_FILE)
    try:
        return read_token_file(token_file.expanduser(), required=required)
    except TokenFileError as error:
        raise _Refused(str(error)) from None


def _tmdb(
    environ: Mapping[str, str],
    rows: Sequence[InputRow],
    cache_only: bool,
    token_file: Path,
) -> resolve_http.Service | None:
    """TMDb, or None when no row is a film.

    Under ``--cache-only`` no request is sent, so no token is sent either and the
    cache alone answers. Otherwise a film row needs ``TOKEN_ENV`` or, when that
    is unset or empty, the token file, which is then read once and only then.
    """
    if not any(row.medium == "film" for row in rows):
        return None
    if cache_only:
        return resolve_http.tmdb(None)
    token, source = environ.get(TOKEN_ENV) or None, TOKEN_ENV
    if token is None:
        token, source = _file_token(token_file), _TOKEN_FILE_SOURCE
    if token is None:
        raise _Refused(_NEEDS_TOKEN)
    try:
        return resolve_http.tmdb(token)
    except ValueError as error:
        raise _Refused(f"{source}: {error}") from None


def _prepare_out(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> Path:
    """Check ``--out``, create it with its marker, and return it resolved.

    Done before any lookup, so the cache has a folder to go in and an interrupted
    run leaves an ``--out`` the next run accepts.
    """
    try:
        survey_guard.check_out_dir(
            args.out, args.parsed, run_git=run_git, timeout=args.timeout, tool=TOOL
        )
        survey_guard.prepare_out_dir(args.out, tool=TOOL)
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None
    return args.out.resolve()


# ---------------------------------------------------------------------------
# Act
# ---------------------------------------------------------------------------


def _resolve(
    args: argparse.Namespace,
    rows: Sequence[InputRow],
    overrides: Sequence[ResolveOverride],
    services: tuple[resolve_http.Service, resolve_http.Service | None],
    client: resolve_http.Client,
) -> tuple[tuple[OutputRow, ...], tuple[ReviewEntry, ...]]:
    """Every row resolved; a refused token ends the run as ``_Refused``."""
    open_library, tmdb = services
    try:
        return resolve_all(
            rows,
            overrides,
            client=client,
            open_library=open_library,
            tmdb=tmdb,
            directors=args.directors,
            vote_ratio=args.vote_ratio,
            vote_floor=args.vote_floor,
            research=args.research,
        )
    except resolve_http.TokenRefused as refused:
        raise _Refused(
            f"{refused}: check {_TOKEN_FILE_SOURCE} or {TOKEN_ENV}"
        ) from None


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


def _remove_stale(out: Path, keep: Mapping[str, str], medium: RunMedium) -> None:
    """Remove each ``medium`` CSV an earlier run left that this one did not write.

    Only a file or a link is removed, never a folder, and a link is removed rather
    than followed. ``--out`` holds this tool's marker, so these are its own files.
    """
    for path in out.iterdir():
        named = any(fnmatchcase(path.name, glob) for glob in _CSV_GLOBS[medium])
        stale = named and path.name not in keep
        if stale and (path.is_symlink() or path.is_file()):
            path.unlink()


def _write_outputs(
    out: Path,
    rows: Sequence[OutputRow],
    review: Sequence[ReviewEntry],
    medium: RunMedium,
) -> None:
    """Write every CSV, remove stale ones, then review.json and review.md.

    The review covers this run's ``medium`` only and says so: entries a single-medium
    run did not check are not carried over from an earlier review.
    """
    csvs = csv_files(rows)
    for name, text in csvs.items():
        write_atomic(out / name, text)
    _remove_stale(out, csvs, medium)
    write_atomic(out / "review.json", review_json(review, medium))
    write_atomic(out / "review.md", review_markdown(review, medium))


def _summary(rows: Sequence[OutputRow], review: Sequence[ReviewEntry]) -> list[str]:
    """The row count, then each status and review kind that occurred: counts only."""
    statuses = dict.fromkeys(typing.get_args(Status), 0)
    for row in rows:
        statuses[row.cells["resolve_status"]] += 1
    lines = [f"rows: {len(rows)}"]
    lines += [f"status {status}: {n}" for status, n in statuses.items() if n]
    lines += [f"review {kind}: {n}" for kind, n in review_counts(review).items() if n]
    return lines


def _record(
    out: Path,
    rows: Sequence[OutputRow],
    review: Sequence[ReviewEntry],
    medium: RunMedium,
) -> None:
    """Write the outputs and print the summary; ``_Refused`` on a write failure."""
    try:
        _write_outputs(out, rows, review, medium)
    except OSError as error:
        # describe_error keeps the class and reason only: the message names the path.
        raise _Refused(f"could not write the output: {describe_error(error)}") from None
    for line in _summary(rows, review):
        print(line)


def _run(
    args: argparse.Namespace,
    fetch: resolve_http.RawFetch,
    run_git: survey_guard.GitRunner,
    sleep: Callable[[float], None],
) -> int:
    """Validate -> act -> record; the number of error entries, or ``_Refused``."""
    overrides = _load_overrides(args.overrides, args.medium)
    # Only the selected medium's rows are read, so the services below see no row of
    # the other: no request goes to it, and a book run never looks for a TMDb token.
    rows = _read_rows(args.parsed, args.medium)
    services = (
        _open_library(os.environ),
        _tmdb(os.environ, rows, args.cache_only, args.token_file),
    )
    out = _prepare_out(args, run_git)
    client = resolve_http.Client(
        out / _CACHE_DIR,
        timeout=args.timeout,
        refresh=args.refresh,
        cache_only=args.cache_only,
        fetch=fetch,
        sleep=sleep,
    )
    written, review = _resolve(args, rows, overrides, services, client)
    _record(out, written, review, args.medium)
    return sum(1 for entry in review if entry.kind in ERROR_KINDS)


def main(
    argv: list[str] | None = None,
    *,
    fetch: resolve_http.RawFetch = bounded_fetch.fetch_url,
    run_git: survey_guard.GitRunner = survey_guard.real_git,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Resolve ``--parsed`` into ``--out`` and return the exit code.

    ``fetch``, ``run_git`` and ``sleep`` are the seams the tests replace: the HTTPS
    fetch, the git runner and the pacing wait.
    """
    # First, before anything is printed: output piped to another program would
    # otherwise use the locale's encoding.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        errors = _run(args, fetch, run_git, sleep)
    except _Refused as refused:
        print(f"{parser.prog}: {refused}", file=sys.stderr)
        return _EXIT_USAGE
    if errors:
        print(f"{parser.prog}: {errors} error item(s); see review.md", file=sys.stderr)
        return _EXIT_ERRORS
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
