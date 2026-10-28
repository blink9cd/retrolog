"""retrolog_review: pick each unsettled title's film or book on a local page.

A sibling of the retrolog tools, run after ``retrolog_resolve``. ``--mode page``
turns resolve's ``review.json`` into one self-contained page for picking each
title's work; the page saves the picks as ``picks.json``. ``--mode overrides`` turns
those picks into an overrides file in the format ``retrolog_resolve`` reads, checked
by resolve's own loader before it is written.

The page's template, stylesheet and scripts are read from ``page/`` beside this file.
The stages live in ``src/`` beside it; retrolog_resolve's and retrolog_parse's
``src/`` supply the overrides check, and retrolog_survey's the atomic writes and the
git work-tree guard. All are
put on ``sys.path`` from this file's own location, so the tool can be moved.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import textwrap
from collections.abc import Callable
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[1] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")

import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_HERE = Path(__file__).resolve().parent
# This tool's private modules, then resolve's (the overrides check) and parse's (the
# value checks it uses), and the survey's (atomic writes, the git work-tree guard).
SOURCE_DIRS = (
    _HERE / "src",
    _HERE.parent / "retrolog_resolve" / "src",
    _HERE.parent / "retrolog_parse" / "src",
    _HERE.parent / "retrolog_survey" / "src",
)
sys.path[:0] = [str(folder) for folder in SOURCE_DIRS if str(folder) not in sys.path]

from review_page import DATA_OPENER, PageError, build_page  # noqa: E402 - src/ first
from review_picks import PicksError, build_overrides  # noqa: E402
from review_read import (  # noqa: E402 - after the src/ inserts
    Review,
    ReviewError,
    page_data,
    read_review,
)
import survey_guard  # noqa: E402 - after the src/ inserts
from survey_write import describe_error, write_atomic  # noqa: E402

# The folder the page's template, stylesheet and script are read from. A module
# constant so the tests can put a stand-in folder in its place.
PAGE_DIR = _HERE / "page"
_TEMPLATE, _STYLE = "review.html", "review.css"
# The page's scripts, inlined in this order into its one <script>: review.js reads
# the storage helpers review_storage.js sets on the global object.
_SCRIPTS = ("review_storage.js", "review.js")
_EXIT_OK, _EXIT_REFUSED, _EXIT_USAGE = 0, 1, 2
_MODES = ("page", "overrides")
_OUT_EXISTS = "refused --out: it exists, and an overrides file is never overwritten"
_OUT_FOREIGN = "refused --out: it exists and is not a page this tool wrote"
# What marks a page this tool wrote: the data block's opening tag, which build_page
# puts in every page. An --out holding it may be replaced; any other file is refused.
PAGE_FINGERPRINT = DATA_OPENER.encode("ascii")
# Bytes read at a time while looking for the fingerprint, so a large file at --out
# is scanned without being held in memory whole.
_SCAN_CHUNK = 1 << 16
# Seconds the git work-tree question may take: retrolog_resolve's default --timeout.
_GIT_TIMEOUT = 15.0

_PREREQUISITES = (
    "Prerequisites: Python 3.12 or later, standard library only (nothing to "
    "install); retrolog_resolve's review.json; the page/ folder beside this script "
    f"({_TEMPLATE}, {_STYLE}, {', '.join(_SCRIPTS)}); a browser, which opens the page from "
    "file:// and sends nothing."
)
_RUN_ORDER = (
    "Run order: after retrolog_resolve. --mode page writes the page; open it, pick "
    "each title's work, and save picks.json with its Save picks file button. --mode "
    "overrides then writes the overrides file for retrolog_resolve --overrides."
)
_EXIT_CODES = (
    "Exit codes: 0 written - 1 an input refused (a bad review.json or picks.json, "
    "picks made against another review, an --out that exists in overrides mode or "
    "is not an earlier page in page mode, or picks whose overrides resolve would "
    "refuse) - 2 bad arguments, a missing or "
    "unreadable file, a page template missing a marker, an --out inside a git work "
    "tree, or the output could not be written."
)
_DESCRIPTION = (
    "Turn retrolog_resolve's review.json into a local page for picking each title's "
    "film or book, and the picks saved from it into an overrides file."
)
# The help text's own width; the formatter keeps these paragraph breaks as written.
_HELP_WIDTH = 79


def _paragraphs(*texts: str) -> str:
    """``texts`` wrapped to the help width, a blank line between each."""
    return "\n\n".join(textwrap.fill(text, _HELP_WIDTH) for text in texts)


class _Refused(Exception):
    """The run stops with ``code``; the message names no path, title or id."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description=_paragraphs(_DESCRIPTION),
        # Shows each default, none on a required option (the stock formatter stamps
        # "(default: None)" there before CPython 3.13; the floor is 3.12) and none
        # for --picks, which has no value to show; keeps the epilog's paragraphs.
        formatter_class=help_formatter.RawNoneHidingFormatter,
        epilog=_paragraphs(_PREREQUISITES, _RUN_ORDER, _EXIT_CODES),
    )
    parser.add_argument(
        "--mode",
        choices=_MODES,
        required=True,
        help="page: write the review page; overrides: write the overrides file "
        "from the picks saved on it.",
    )
    parser.add_argument(
        "--review",
        type=Path,
        required=True,
        help="retrolog_resolve's review.json (read only).",
    )
    parser.add_argument(
        "--picks",
        type=Path,
        help="The picks.json the page saved; overrides mode only, and required there.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="The page to write (refused when it exists, unless it is a page this "
        "tool wrote, which is replaced atomically), or the overrides file to write "
        "(refused when it exists). Refused inside a git work tree: both name "
        "the operator's titles.",
    )
    return parser


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _check_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 2 through argparse when ``--picks`` is given or missing for the mode."""
    if args.mode == "overrides" and args.picks is None:
        parser.error("--mode overrides needs --picks")
    if args.mode == "page" and args.picks is not None:
        parser.error("--picks is only for --mode overrides")


def _check_out_folder(out: Path, run_git: survey_guard.GitRunner) -> None:
    """Exit 2 when ``--out``'s folder lies inside a git work tree.

    Both outputs name the operator's titles, so neither may land where it could be
    committed; retrolog_resolve refuses its own --out the same way, in the same words.
    """
    try:
        survey_guard.check_outside_work_tree(
            out.parent, run_git=run_git, timeout=_GIT_TIMEOUT
        )
    except survey_guard.OutRefused as refused:
        raise _Refused(f"refused --out: {refused}", _EXIT_USAGE) from None


def _read_bytes(path: Path, flag: str) -> bytes:
    """``path``'s bytes; ``_Refused`` (exit 2) naming the flag and error class only."""
    try:
        return path.read_bytes()
    except OSError as error:
        raise _Refused(
            f"could not read {flag}: {type(error).__name__}", _EXIT_USAGE
        ) from None


def _review(path: Path) -> tuple[bytes, Review]:
    """The review file's bytes and its checked contents."""
    data = _read_bytes(path, "--review")
    try:
        return data, read_review(data)
    except ReviewError as error:
        raise _Refused(f"invalid --review: {error}", _EXIT_REFUSED) from None


def _page_text(name: str) -> str:
    """One page file's text, read from ``PAGE_DIR`` as UTF-8."""
    try:
        return (PAGE_DIR / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _Refused(
            f"could not read the page file {name}: {type(error).__name__}",
            _EXIT_USAGE,
        ) from None


# ---------------------------------------------------------------------------
# Act and record
# ---------------------------------------------------------------------------


def write_new(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, refusing to replace anything there.

    The data goes to a temporary file in the same folder, which is then hard-linked
    to ``path``: a link fails with ``FileExistsError`` when ``path`` exists, even if
    it appeared after the caller's own check, and a reader sees the whole file or
    none. The temporary name is removed either way.
    """
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_failed(error: OSError) -> _Refused:
    """The refusal for a failed write, keeping the error's class and reason only."""
    return _Refused(f"could not write --out: {describe_error(error)}", _EXIT_USAGE)


def _holds_fingerprint(path: Path) -> bool:
    """Whether ``path``'s bytes hold ``PAGE_FINGERPRINT``, read a chunk at a time.

    Each read keeps the previous one's last bytes, so a fingerprint straddling two
    reads is still found.
    """
    keep = len(PAGE_FINGERPRINT) - 1
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(_SCAN_CHUNK):
            window = tail + chunk
            if PAGE_FINGERPRINT in window:
                return True
            tail = window[-keep:]
    return False


def _refuse_foreign(out: Path) -> None:
    """``_Refused`` (exit 1) when ``out`` exists and is not a page this tool wrote.

    A folder, a dangling link or any other non-file is refused as well. A location
    that cannot be examined or read is the write failure it would become: exit 2.
    """
    try:
        if not (out.exists() or out.is_symlink()):
            return
        is_page = out.is_file() and _holds_fingerprint(out)
    except OSError as error:
        raise _write_failed(error) from None
    if not is_page:
        raise _Refused(_OUT_FOREIGN, _EXIT_REFUSED)


def _run_page(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> str:
    """Write the page; the summary line, or ``_Refused``."""
    # First, before anything is read: an --out inside a work tree is refused before
    # even its own bytes are, then a file this tool did not write is never replaced.
    _check_out_folder(args.out, run_git)
    _refuse_foreign(args.out)
    _, review = _review(args.review)
    template, css = (_page_text(name) for name in (_TEMPLATE, _STYLE))
    js = "\n".join(_page_text(name) for name in _SCRIPTS)
    try:
        page = build_page(template, css, js, page_data(review))
    except PageError as error:
        raise _Refused(f"the page template: {error}", _EXIT_USAGE) from None
    # Again, as close to the write as it can be, for a file that appeared meanwhile.
    _refuse_foreign(args.out)
    try:
        write_atomic(args.out, page)
    except OSError as error:
        raise _write_failed(error) from None
    return (
        f"{_count(len(review.entries), 'entry', 'entries')} on the page; "
        f"{_count(review.errors, 'error')} and "
        f"{_count(review.applied, 'applied override')} not shown."
    )


def _count(number: int, singular: str, plural: str = "") -> str:
    """``number`` and its noun, singular for exactly one: ``1 pick``, ``2 picks``."""
    noun = singular if number == 1 else plural or f"{singular}s"
    return f"{number} {noun}"


def _refuse_existing(out: Path) -> None:
    """``_Refused`` (exit 1) when ``out`` exists, even as a dangling link.

    A location that cannot be examined (its folder unsearchable, say) is the write
    failure it would become, reported the same way: exit 2, its class and reason only.
    """
    try:
        exists = out.exists() or out.is_symlink()
    except OSError as error:
        raise _write_failed(error) from None
    if exists:
        raise _Refused(_OUT_EXISTS, _EXIT_REFUSED)


def _run_overrides(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> str:
    """Write the overrides file; the summary line, or ``_Refused``."""
    # First, before anything is read: an existing file is never replaced.
    _refuse_existing(args.out)
    _check_out_folder(args.out, run_git)
    _, review = _review(args.review)
    picks = _read_bytes(args.picks, "--picks")
    try:
        built = build_overrides(picks, review)
    except PicksError as error:
        raise _Refused(f"invalid --picks: {error}", _EXIT_REFUSED) from None
    try:
        write_new(args.out, built.toml.encode("utf-8"))
    except FileExistsError:
        raise _Refused(_OUT_EXISTS, _EXIT_REFUSED) from None
    except OSError as error:
        raise _write_failed(error) from None
    return (
        f"{_count(built.written, 'override')} written for "
        f"{_count(built.picked, 'pick')}, {built.kept} kept, {built.undecided} undecided."
    )


# A mode's runner: the parsed arguments and the git runner in, the summary line out.
_Runner = Callable[[argparse.Namespace, survey_guard.GitRunner], str]


def main(
    argv: list[str] | None = None,
    *,
    run_git: survey_guard.GitRunner = survey_guard.real_git,
) -> int:
    """Run the chosen mode and return the exit code.

    ``run_git`` is the seam the tests replace: the git call behind the --out guard.
    """
    # First, before anything is printed: output piped to another program would
    # otherwise use the locale's encoding.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    _check_mode(parser, args)
    run: _Runner = _run_page if args.mode == "page" else _run_overrides
    try:
        summary = run(args, run_git)
    except _Refused as refused:
        print(f"{parser.prog}: {refused}", file=sys.stderr)
        return refused.code
    print(summary)
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
