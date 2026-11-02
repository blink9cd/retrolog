"""retrolog_probe: ask Letterboxd which of export's films it has, before the import.

An optional step after ``retrolog_export``. It reads export's Letterboxd import files
from ``--files`` without writing there, asks Letterboxd about each distinct TMDb id
once, one request at a time at a slow fixed pace, and writes ``--out``: a report of
every row whose film Letterboxd lacks, so the operator can leave those rows out of the
import, and of every row it could not settle. It runs only when started; it sends only
the ids (no cookie, no account); and a 429 is never read as missing.

The stages live in ``src/`` beside this file; retrolog_export's ``src/`` supplies the
import files' columns and the raised CSV field limit, retrolog_parse's the Markdown
cell escape (and the title key export's account reader takes), and retrolog_survey's
the output guard and atomic writes. Probe also needs retrolog_resolve's
``src/``, for two reasons: it uses resolve's TMDb id rule, and ``export_letterboxd``,
which it imports, takes resolve's creator joiner. All of them, and the shared
libraries this file imports, are put on ``sys.path`` from this file's own location,
so the tool can be moved. ``retrolog_probe.md`` describes the tool, its cache, and
its report.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
import time
from collections.abc import Callable, Mapping
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
# This tool's private modules, then export's (the import files' columns, the CSV field
# limit), resolve's (the TMDb id rule, and export_letterboxd's creator joiner), parse's
# (the cell escape, and the title key export's account reader takes), and the survey's
# (the output guard, atomic writes).
SOURCE_DIRS = (
    _HERE / "src",
    _HERE.parent / "retrolog_export" / "src",
    _HERE.parent / "retrolog_resolve" / "src",
    _HERE.parent / "retrolog_parse" / "src",
    _HERE.parent / "retrolog_survey" / "src",
)
sys.path[:0] = [str(folder) for folder in SOURCE_DIRS if str(folder) not in sys.path]

import survey_guard  # noqa: E402 - after the src/ inserts
from probe_cache import CacheError, Settled, load_cache, save_cache  # noqa: E402
from probe_client import Head, Prober  # noqa: E402 - after the src/ inserts
from probe_ids import is_tmdb_id  # noqa: E402 - after the src/ inserts
from probe_input import ImportRows, InputError, read_imports  # noqa: E402
from probe_report import report_markdown, summary_line  # noqa: E402
from probe_run import ControlFailed, RunResult, probe_all  # noqa: E402
from survey_write import describe_error, write_atomic  # noqa: E402

_EXIT_OK, _EXIT_FOUND, _EXIT_USAGE = 0, 1, 2
# Metropolis (1927): public domain, on TMDb and on Letterboxd since both began.
_DEFAULT_CONTROL = "19"
_DEFAULT_INTERVAL = 2.0
_MIN_INTERVAL = 1.0
_DEFAULT_TIMEOUT = 30.0
# Seconds the git work-tree question may take.
_GIT_TIMEOUT = 15.0
_OUT_EXISTS = "refused --out: it exists, and a report is never overwritten"
# The help text's own width; the formatter keeps these paragraph breaks as written.
_HELP_WIDTH = 79

_DESCRIPTION = (
    "Ask Letterboxd about each TMDb id in retrolog_export's import files, and write "
    "a report of the rows whose film Letterboxd lacks, so they can be left out of "
    "the import."
)
_TERMS = (
    "Terms: Letterboxd's terms of use forbid automated access to the site without "
    "its permission. This tool sends one request per film to Letterboxd; whether to "
    "run it is your own decision. It runs only when you start it, sends only the ids "
    "(no cookies, no account), and a 429 (too many requests) is never read as missing."
)
_PREREQUISITES = (
    "Prerequisites: Python 3.12 or later, standard library only (nothing to "
    "install); retrolog_export's --out folder; a network connection to "
    "letterboxd.com, unless every id is in --cache."
)
_RUN_ORDER = (
    "Run order: after retrolog_export, before uploading the import files. Leave "
    "out the rows the report lists as missing (add its omit entries to the export "
    "config, or delete the rows from their file); run again later, with "
    "the same --cache and a new --out, for the rows it lists as unknown."
)
_EXIT_CODES = (
    "Exit codes: 0 means every film is present. 1 means the report lists a "
    "missing or unknown film. 2 means bad arguments; a missing --files folder or "
    "one with no import file; an import file whose header is not export's, a "
    "row of the wrong width, or a tmdbID that is not a TMDb id; an unusable "
    "--cache; --out existing, in a missing folder, or inside a git work tree; the control id not answered as present (the check itself is not "
    "working); or the report or cache could not be written."
)


class _Refused(Exception):
    """The run may not start or finish; the message names no path and no title."""


def _paragraphs(*texts: str) -> str:
    """``texts`` wrapped to the help width, a blank line between each."""
    return "\n\n".join(textwrap.fill(text, _HELP_WIDTH) for text in texts)


def _seconds(low: float, inclusive: bool) -> Callable[[str], float]:
    """An argparse type: a finite number of seconds above (or from) ``low``."""

    def parse(text: str) -> float:
        value = float(text)
        if not value < float("inf") or value < low or (value == low and not inclusive):
            bound = f"at least {low}" if inclusive else f"greater than {low:g}"
            raise argparse.ArgumentTypeError(f"must be {bound} seconds")
        return value

    return parse


def _tmdb_id(text: str) -> str:
    """An argparse type: an id the rule accepts."""
    if not is_tmdb_id(text):
        raise argparse.ArgumentTypeError(
            "must be a TMDb id: 1 to 10 digits, no leading 0"
        )
    return text


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description=_paragraphs(_DESCRIPTION),
        # Shows each default, none on a required option or for an option with no
        # value to show, and keeps the epilog's paragraphs as written.
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=_paragraphs(_TERMS, _PREREQUISITES, _RUN_ORDER, _EXIT_CODES),
    )
    parser.add_argument(
        "--files",
        type=Path,
        required=True,
        help="retrolog_export's --out folder, holding its letterboxd-NNN.csv "
        "import files (read only).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="The report to write. Refused when it exists, when its folder does not, "
        "inside the --files folder, or inside a git work tree: it names your films.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="A verdict cache: ids already settled are not asked about again, and "
        "each new settled verdict is added as it arrives. Left out, nothing is cached.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ask about every id again, whatever --cache holds (still writing it).",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Send nothing: an id not in --cache is reported as unknown.",
    )
    parser.add_argument(
        "--control-id",
        type=_tmdb_id,
        default=_DEFAULT_CONTROL,
        help="A TMDb id Letterboxd is known to have, asked about before any other; "
        "unless it comes back present, the run stops and calls nothing missing.",
    )
    parser.add_argument(
        "--interval",
        type=_seconds(_MIN_INTERVAL, inclusive=True),
        default=_DEFAULT_INTERVAL,
        help=f"Seconds between any two requests, at least {_MIN_INTERVAL}.",
    )
    parser.add_argument(
        "--timeout",
        type=_seconds(0.0, inclusive=False),
        default=_DEFAULT_TIMEOUT,
        help="Seconds each request may wait on the network.",
    )
    return parser


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _check_flags(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 through argparse for flags that contradict each other."""
    if args.refresh and args.cache_only:
        parser.error("--refresh and --cache-only cannot both be given")
    if (args.refresh or args.cache_only) and args.cache is None:
        parser.error("--refresh and --cache-only need --cache")
    if args.cache is not None and args.cache.resolve() == args.out.resolve():
        parser.error("--cache and --out must be different files")


def _refuse_existing(out: Path) -> None:
    """Refuse an ``out`` that exists, even as a dangling link.

    ``lstat`` rather than ``exists``: it sees a link without following it, and it
    raises for a location that cannot be examined (a name too long, a file where a
    folder should be) where ``exists`` on a newer Python answers False.
    """
    try:
        os.lstat(out)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _Refused(f"could not write --out: {describe_error(error)}") from None
    raise _Refused(_OUT_EXISTS)


def _check_out(out: Path, files: Path, run_git: survey_guard.GitRunner) -> None:
    """Refuse an ``out`` that exists, whose folder does not, inside ``files``, or git.

    The report names the operator's films, so it may not land where it could be
    committed; nor among the import files, where it could be uploaded with them and
    the next export run deletes it. The other tools refuse their output in the same
    words, through the same check: ``out`` is known not to exist by then, so only that
    check's source-tree and work-tree rules can apply, never its folder rules.
    """
    _refuse_existing(out)
    if not out.parent.is_dir():
        raise _Refused("refused --out: its folder does not exist")
    try:
        survey_guard.check_out_dir(out, files, run_git=run_git, timeout=_GIT_TIMEOUT)
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}") from None


def _read(files: Path) -> ImportRows:
    """The import files' rows; ``_Refused`` naming the file and row, never a value."""
    try:
        return read_imports(files)
    except InputError as error:
        raise _Refused(str(error)) from None


def _load(cache: Path | None) -> dict[str, Settled]:
    """What ``cache`` holds, or nothing when there is no cache."""
    if cache is None:
        return {}
    if not cache.parent.is_dir():
        raise _Refused("refused --cache: its folder does not exist")
    try:
        return load_cache(cache)
    except CacheError as error:
        raise _Refused(str(error)) from None


# ---------------------------------------------------------------------------
# Act
# ---------------------------------------------------------------------------


def _recorder(cache: Path | None) -> Callable[[Mapping[str, Settled]], None]:
    """What saves the cache after each settled verdict: nothing without ``--cache``."""

    def record(verdicts: Mapping[str, Settled]) -> None:
        if cache is None:
            return
        try:
            save_cache(cache, verdicts)
        except OSError as error:
            raise _Refused(
                f"could not write --cache: {describe_error(error)}"
            ) from None

    return record


def _probe(
    args: argparse.Namespace,
    rows: ImportRows,
    cached: Mapping[str, Settled],
    prober: Prober,
    record: Callable[[Mapping[str, Settled]], None],
) -> RunResult:
    """Every id settled; ``_Refused`` when the control says the check is not working."""
    try:
        return probe_all(
            rows.distinct_ids(),
            cached=cached,
            prober=prober,
            control_id=args.control_id,
            refresh=args.refresh,
            cache_only=args.cache_only,
            record=record,
        )
    except ControlFailed as failed:
        raise _Refused(
            f"the check itself is not working: Letterboxd answered unexpectedly "
            f"({failed}), so nothing is called missing and no report is written"
        ) from None


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


def _write_report(out: Path, text: str) -> None:
    """Write the report, refusing again a file that appeared during the run."""
    _refuse_existing(out)
    try:
        write_atomic(out, text)
    except OSError as error:
        raise _Refused(f"could not write --out: {describe_error(error)}") from None


def _run(
    args: argparse.Namespace,
    run_git: survey_guard.GitRunner,
    prober: Prober,
) -> RunResult:
    """Validate -> act -> record; the run's result, or ``_Refused``."""
    _check_out(args.out, args.files, run_git)
    rows = _read(args.files)
    cached = _load(args.cache)
    record = _recorder(args.cache)
    if not args.cache_only:
        # Saved once before any request, so a cache that cannot be written is refused
        # up front, like every other refusal, rather than after ids have gone out.
        record(cached)
    result = _probe(args, rows, cached, prober, record)
    _write_report(args.out, report_markdown(rows, result))
    print(summary_line(result))
    print(f"report: {args.out}")
    return result


def main(
    argv: list[str] | None = None,
    *,
    run_git: survey_guard.GitRunner = survey_guard.real_git,
    head: Head = bounded_fetch.head_url,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Check ``--files`` against Letterboxd, write ``--out``, and return the exit code.

    ``run_git``, ``head``, ``sleep``, and ``clock`` are the seams the tests replace:
    the --out guard's git call, the request, and the pace's waits and readings.
    """
    # First, before anything is printed: output piped to another program would
    # otherwise use the locale's encoding.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    _check_flags(parser, args)
    prober = Prober(
        timeout=args.timeout,
        interval=args.interval,
        head=head,
        sleep=sleep,
        clock=clock,
    )
    try:
        result = _run(args, run_git, prober)
    except _Refused as refused:
        print(f"{parser.prog}: {refused}", file=sys.stderr)
        return _EXIT_USAGE
    settled = all(outcome.verdict == "present" for outcome in result.outcomes.values())
    return _EXIT_OK if settled else _EXIT_FOUND


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
