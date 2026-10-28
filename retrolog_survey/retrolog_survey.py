"""retrolog_survey: a read-only survey of a folder of old .txt, .doc and .docx lists.

The command-line entry point. It walks ``--path`` without writing to it, surveys every
text and Word file it finds (a .doc through a converted copy), and writes
``report.json``, ``report.md`` and the documents' distinct images to ``--out``, which
it first checks is a safe place for reports that quote private text. The stages live
in ``src/`` beside this file; ``retrolog_survey.md`` describes the tool and its report.
"""

from __future__ import annotations

import argparse
import enum
import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[1] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")

import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

# The private stages, reachable only through this entry point.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import survey_convert  # noqa: E402 - after the src/ insert
import survey_guard  # noqa: E402 - after the src/ insert
import survey_report  # noqa: E402 - after the src/ insert
from survey_walk import walk_source  # noqa: E402 - after the src/ insert

_EXIT_OK, _EXIT_FILE_ERRORS, _EXIT_USAGE = 0, 1, 2
_BYTES_PER_MB = 1024 * 1024
_IMAGES = "images"
# The file extension for an image of any type but a raster one below.
_UNKNOWN_EXTENSION = "bin"
# The raster image subtypes (after any "x-" prefix) written under their own extension.
# Anything else, SVG and HTML above all, is written as .bin: an SVG or HTML file a
# browser opens can run its script, and the type is whatever the surveyed file claims.
_RASTER_SUBTYPES = frozenset({"png", "jpeg", "jpg", "gif", "bmp", "tiff", "webp"})
_SKIPPED = {"textutil": "skipped", "libreoffice": "skipped"}


class _Unset(enum.Enum):
    """The type of ``_UNSET``: "find LibreOffice yourself"."""

    TOKEN = enum.auto()


# Passed as ``soffice`` to mean "call survey_convert.locate_soffice()".
_UNSET = _Unset.TOKEN


def _count(minimum: int) -> Callable[[str], int]:
    """An argparse type: an integer no smaller than ``minimum``."""

    def parse(text: str) -> int:
        value = int(text)
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}")
        return value

    return parse


def _seconds(text: str) -> float:
    """An argparse type: a number of seconds greater than zero."""
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The CLI. Every argument is a named option, and every default is shown."""
    parser = argparse.ArgumentParser(
        description="Survey a folder of old .txt, .doc and .docx lists, read only.",
        # Shows each default, and none on a required option (the stock formatter
        # stamps "(default: None)" there before CPython 3.13; the floor is 3.12).
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Prerequisites: macOS for .doc files (its built-in textutil converts a "
            "copy of each); LibreOffice is optional, and when it is installed its "
            "conversion is compared with textutil's and preferred. .txt and .docx "
            "files need neither.\n\n"
            "Run order: run this survey, read report.md, then run the parse step "
            "that reads report.json.\n\n"
            "Exit codes: 0 every file surveyed - 1 report written, but one or more "
            "files could not be surveyed (listed under errors) - 2 bad arguments, "
            "--path is not a directory, --out or the temporary directory was "
            "refused, or the report could not be written."
        ),
    )
    parser.add_argument(
        "--path", type=Path, required=True, help="Source tree to survey (read only)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Report directory; refused inside --path or a git work tree.",
    )
    parser.add_argument(
        "--group-depth",
        type=_count(0),
        default=1,
        help="Directory levels that form a report group.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="GLOB",
        help="Glob over the relative path, case-insensitive, repeatable; matching "
        "files are counted as excluded and not read.",
    )
    parser.add_argument(
        "--samples",
        type=_count(1),
        default=5,
        help="Sample lines per group per line kind (at least 1).",
    )
    parser.add_argument(
        "--no-convert",
        action="store_true",
        help="Skip .doc conversion (.doc files are then counted only).",
    )
    parser.add_argument(
        "--max-file-mb",
        type=_count(1),
        default=50,
        help=(
            "Per-file size limit "
            "(text bytes, .doc file size or Word uncompressed size)."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_seconds,
        default=120,
        help="Seconds per converter or git call.",
    )
    return parser


def _converters(
    convert: bool,
    soffice: Path | None | _Unset,
    which: Callable[[str], str | None],
) -> tuple[Path | None, dict[str, str]]:
    """The LibreOffice program to use, and the report's converter availability."""
    if not convert:
        return None, dict(_SKIPPED)
    if isinstance(soffice, _Unset):
        soffice = survey_convert.locate_soffice()
    found = {
        "textutil": "present" if which("textutil") else "absent",
        "libreoffice": "present" if soffice is not None else "absent",
    }
    return soffice, found


def _extension(content_type: str) -> str:
    """A safe file extension from an image content type the document declared.

    ``image/png`` gives ``png`` and ``image/x-bmp`` gives ``bmp``; every type that is
    not a known raster one, ``image/svg+xml`` and ``image/x-emf`` included, gives
    ``bin``, so no written image is one a browser would run.
    """
    kind, _, subtype = content_type.lower().partition("/")
    subtype = subtype.removeprefix("x-")
    if kind != "image" or subtype not in _RASTER_SUBTYPES:
        return _UNKNOWN_EXTENSION
    return subtype


def _image_files(files: Sequence[survey_report.FileSurvey]) -> dict[str, bytes]:
    """``<sha256_12>.<ext>`` -> bytes, for each distinct image once."""
    named: dict[str, bytes] = {}
    for survey in files:
        for image in survey.images:
            name = f"{image.sha256_12}.{_extension(image.content_type)}"
            named[name] = survey.blobs[image.sha256_12]
    return named


def _write_images(out: Path, named: Mapping[str, bytes]) -> None:
    """Write each image into ``out/images``, removing files an earlier run left there.

    A link in place of the folder is removed, not followed, so nothing is written or
    deleted outside ``out``.
    """
    folder = out / _IMAGES
    if folder.is_symlink():
        folder.unlink()
    if folder.is_dir():
        for stale in folder.iterdir():
            if stale.name not in named and not stale.is_dir():
                stale.unlink()
    elif named:
        folder.mkdir()
    for name, data in named.items():
        survey_report.write_atomic_bytes(folder / name, data)


def _write_outputs(
    out: Path, report: Mapping[str, object], files: Sequence[survey_report.FileSurvey]
) -> None:
    """Write the images, then report.json and report.md, each file atomically.

    The images go first, so a failure part way leaves no new report naming images
    that were never written.
    """
    _write_images(out, _image_files(files))
    text = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    survey_report.write_atomic(out / "report.json", text)
    survey_report.write_atomic(out / "report.md", survey_report.render_markdown(report))


def _printable(name: str) -> str:
    r"""``name`` with each surrogate escape (a non-UTF-8 name byte) as ``\udcXX``.

    Standard output is strict UTF-8, which cannot encode a surrogate.
    """
    return name.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _summary_lines(report: Mapping[str, object]) -> list[str]:
    """One line per group, counts only: no file names and no text."""
    groups = report["groups"]
    assert isinstance(groups, list)
    return [
        f"{_printable(group['group'])}: {sum(group['files'].values())} files, "
        f"{sum(group['line_kinds'].values())} lines, {len(group['errors'])} errors"
        for group in groups
    ]


def _inside_path_or_out(folder: Path, args: argparse.Namespace) -> bool:
    """Whether ``folder`` is ``--path`` or ``--out``, or lies under either."""
    resolved = folder.resolve()
    return any(
        resolved.is_relative_to(root.resolve()) for root in (args.path, args.out)
    )


def _refusal(args: argparse.Namespace, run_git: survey_guard.GitRunner) -> str | None:
    """Why the survey may not start, as a message naming no path; None when it may.

    ``--out`` must pass the guard. The temporary directory for .doc conversions
    follows TMPDIR, so it is checked too: one under ``--path`` would put converter
    files into the source tree, and one under ``--out`` would leave them beside the
    report. It is checked before it is created, so nothing is written there.
    """
    try:
        survey_guard.check_out_dir(
            args.out, args.path, run_git=run_git, timeout=args.timeout
        )
    except survey_guard.OutRefused as refused:
        return f"refused --out: {refused}"
    if _inside_path_or_out(Path(tempfile.gettempdir()), args):
        return "refused the temporary directory: it lies inside --path or --out"
    return None


def _survey_all(
    args: argparse.Namespace,
    run: survey_convert.Runner,
    soffice: Path | None,
) -> list[survey_report.FileSurvey]:
    """Walk ``--path`` and survey every entry, converting .doc copies in a temp dir.

    The temporary directory is the system's, never under ``--path`` or ``--out``, and
    it is removed before returning, whether the survey succeeded or raised; a failure
    to remove it is ignored rather than reported, since it would name the path.
    """
    entries = walk_source(
        args.path, group_depth=args.group_depth, exclude=args.exclude or ()
    )
    # A failure removing the directory (a converter still holding a file, say) is
    # ignored: the survey is complete by then, and the error would name the path.
    with tempfile.TemporaryDirectory(
        prefix="retrolog_survey_", ignore_cleanup_errors=True
    ) as work:
        ctx = survey_report.SurveyContext(
            max_bytes=args.max_file_mb * _BYTES_PER_MB,
            timeout=args.timeout,
            run=run,
            soffice=soffice,
            workdir=None if args.no_convert else Path(work),
        )
        return [survey_report.survey_file(entry, args.path, ctx) for entry in entries]


class _Stop(Exception):
    """The survey stops with exit 2; the message names no path."""


@dataclass(frozen=True)
class _Seams:
    """What the tests replace: see ``main``."""

    run: survey_convert.Runner
    run_git: survey_guard.GitRunner
    soffice: Path | None | _Unset
    which: Callable[[str], str | None]


def _run(
    args: argparse.Namespace, seams: _Seams
) -> tuple[dict[str, object], list[survey_report.FileSurvey]]:
    """Check ``--out``, survey, prepare ``--out`` and write; ``_Stop`` when refused.

    Returns the report and the surveyed files, for the summary.
    """
    refusal = _refusal(args, seams.run_git)
    if refusal is not None:
        raise _Stop(refusal)
    try:
        program, converters = _converters(
            not args.no_convert, seams.soffice, seams.which
        )
        files = _survey_all(args, seams.run, program)
        options = survey_report.Options(
            args.group_depth, args.samples, not args.no_convert
        )
        report = survey_report.build_report(files, options, converters)
        survey_guard.prepare_out_dir(args.out)
    except survey_guard.OutRefused as refused:
        raise _Stop(f"refused --out: {refused}") from None
    try:
        _write_outputs(args.out.resolve(), report, files)
    except OSError as error:
        # describe_error keeps the class and reason only: the message names the path.
        reason = survey_report.describe_error(error)
        raise _Stop(f"could not write the report: {reason}") from None
    return report, files


def _record(
    prog: str, report: Mapping[str, object], files: Sequence[survey_report.FileSurvey]
) -> int:
    """Print the per-group summary and any failure count; the exit code."""
    for line in _summary_lines(report):
        print(line)
    failed = sum(1 for survey in files if survey.error)
    if failed:
        print(
            f"{prog}: {failed} file(s) could not be surveyed; "
            "they are listed under errors in the report",
            file=sys.stderr,
        )
        return _EXIT_FILE_ERRORS
    return _EXIT_OK


def main(
    argv: list[str] | None = None,
    *,
    run: survey_convert.Runner = survey_convert.real_run,
    run_git: survey_guard.GitRunner = survey_guard.real_git,
    soffice: Path | None | _Unset = _UNSET,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    """Survey ``--path`` into ``--out`` and return the exit code.

    validate -> check ``--out`` -> walk and survey -> prepare ``--out`` -> write ->
    summarize. ``run``, ``run_git``, ``soffice`` and ``which`` are the seams the tests
    replace: the converter runner, the git runner, LibreOffice's program (``_UNSET``
    finds it) and the program lookup used to report whether textutil is present.
    """
    # First, before anything is printed: a group name is a folder name, and output
    # piped to another program would otherwise use the locale's encoding.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.path.is_dir():
        parser.error("--path must be an existing directory")
    try:
        report, files = _run(args, _Seams(run, run_git, soffice, which))
    except _Stop as stop:
        print(f"{parser.prog}: {stop}", file=sys.stderr)
        return _EXIT_USAGE
    return _record(parser.prog, report, files)


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
