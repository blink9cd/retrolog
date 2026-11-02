"""Convert a copy of an old Word .doc to .docx and report what each converter kept.

A Word 97 .doc is a binary format this tool cannot read directly, so it is converted to
.docx first. Two converters are tried: macOS's built-in ``textutil`` and, when it is
installed, LibreOffice's ``soffice`` running headless. Each result is read with
``survey_docx.read_docx`` and reduced to a set of formatting features (fonts, colors,
highlights, symbol characters, images), so a report can show which converter preserved
the formatting a list uses to mark a rating, and which lost it.

The source file is never handed to a converter. It is first copied into
``<workdir>/in/``, and both converters read that copy and write under the work
directory, so neither can leave a lock file, a temporary file or an output beside the
source. LibreOffice also gets a private profile directory inside the work directory: a
headless call that shares the default profile with an open LibreOffice window is
otherwise handed to that window and never runs.

Every result's ``detail`` names an exit code or an exception class only, never a path,
so a result can be written into a report as is.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
# bounded_run is what makes a converter's timeout bind: ``soffice`` is a launcher whose
# real work runs in a grandchild, and on a timeout subprocess.run kills only the direct
# child, then blocks reading pipes the grandchild still holds open; bounded_run kills
# the whole process tree before it reads. It puts its own stdio_utf8 on the path.
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_run")

import bounded_run  # noqa: E402 - after the bootstrap above

# survey_docx is a sibling in src/, which the caller has already put on sys.path.
from survey_docx import BadDocx, DocxInfo, read_docx  # noqa: E402 - after the bootstrap above

# A converter call: argv and a timeout in seconds in, the process exit code out.
Runner = Callable[[Sequence[str], float], int]
Converter = Literal["textutil", "libreoffice"]
Status = Literal["ok", "failed", "absent"]

# Where the LibreOffice macOS app bundle keeps its command-line program, relative to an
# Applications folder.
_SOFFICE_IN_APPLICATIONS = Path("LibreOffice.app/Contents/MacOS/soffice")
# The system-wide Applications folder (a fixed macOS location, not this machine's).
_SYSTEM_APPLICATIONS = Path("/Applications")
# The format both converters are asked to produce, and its file suffix.
_TARGET_FORMAT = "docx"
_TARGET_SUFFIX = ".docx"
# The detail of a converter that exited 0 but wrote no output file.
_NO_OUTPUT = "no output"
# What a converter call can raise instead of returning: it ran past its timeout (the
# whole process tree is killed first), or the program could not be started at all
# (missing, not executable).
_RUN_ERRORS = (subprocess.TimeoutExpired, OSError)


@dataclass(frozen=True)
class ConversionResult:
    """One converter's outcome on one file, and the features its output kept."""

    converter: Converter
    status: Status
    detail: str  # "exit <code>", an exception class name, or ""; never a path
    features: frozenset[str]


def default_soffice_candidates() -> tuple[Path, ...]:
    """Where LibreOffice's app bundle puts ``soffice``: system-wide, then per-user."""
    return (
        _SYSTEM_APPLICATIONS / _SOFFICE_IN_APPLICATIONS,
        Path.home() / "Applications" / _SOFFICE_IN_APPLICATIONS,
    )


def locate_soffice(
    which: Callable[[str], str | None] = shutil.which,
    candidates: Sequence[Path] | None = None,
) -> Path | None:
    """Find LibreOffice's ``soffice``: on PATH first, then the first candidate file.

    ``candidates`` defaults to ``default_soffice_candidates()``, because the macOS app
    bundle does not put ``soffice`` on PATH.
    """
    on_path = which("soffice")
    if on_path is not None:
        return Path(on_path)
    if candidates is None:
        candidates = default_soffice_candidates()
    return next((path for path in candidates if path.is_file()), None)


def features_of(info: DocxInfo) -> frozenset[str]:
    """Every formatting feature used by any run, as ``kind:value`` strings.

    The kinds are ``font``, ``style``, ``color``, ``hl`` (highlight), ``shd``
    (shading), ``sym`` (``<font>/<hex>`` symbol characters) and ``image`` (an embedded
    picture's content type).
    """
    features: set[str] = set()
    for paragraph in info.paragraphs:
        for run in paragraph.runs:
            for kind, value in (
                ("font", run.font),
                ("style", run.style),
                ("color", run.color),
                ("hl", run.highlight),
                ("shd", run.shading),
                ("sym", run.symbol),
            ):
                if value is not None:
                    features.add(f"{kind}:{value}")
    features.update(f"image:{image.content_type}" for image in info.images)
    return frozenset(features)


def _convert(
    converter: Converter,
    argv: Sequence[str],
    out: Path,
    *,
    run: Runner,
    max_uncompressed: int,
    timeout: float,
) -> ConversionResult:
    """Run one converter and read its output file, turning every failure into a result.

    Any file already at ``out`` is removed first, so an earlier run's output in a reused
    work directory cannot pass for this one: ``soffice`` exits 0 even when it cannot
    load its input, which leaves a missing output as the only sign of failure. A failed
    run's partial or unreadable output is removed too.
    """
    out.unlink(missing_ok=True)
    result = _run_and_read(converter, argv, out, run, max_uncompressed, timeout)
    if result.status == "failed":
        out.unlink(missing_ok=True)
    return result


def _run_and_read(
    converter: Converter,
    argv: Sequence[str],
    out: Path,
    run: Runner,
    max_uncompressed: int,
    timeout: float,
) -> ConversionResult:
    """Run the converter, then read ``out``; every failure becomes a ``failed`` result."""
    try:
        code = run(argv, timeout)
    except _RUN_ERRORS as error:
        return ConversionResult(converter, "failed", type(error).__name__, frozenset())
    if code != 0:
        return ConversionResult(converter, "failed", f"exit {code}", frozenset())
    if not out.is_file():
        return ConversionResult(converter, "failed", _NO_OUTPUT, frozenset())
    try:
        info = read_docx(out, max_uncompressed=max_uncompressed)
    except BadDocx as error:
        return ConversionResult(converter, "failed", type(error).__name__, frozenset())
    return ConversionResult(converter, "ok", f"exit {code}", features_of(info))


def converted_path(workdir: Path, src: Path, converter: Converter) -> Path:
    """Where ``compare_converters`` leaves ``converter``'s output for ``src``.

    An ``ok`` result's output stays there for the caller to read until the caller
    removes ``workdir``; a ``failed`` result's output is removed.
    """
    return workdir / converter / (src.stem + _TARGET_SUFFIX)


def _fresh_dir(path: Path) -> Path:
    """Create ``path`` (and its parents) if missing, and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class _Job:
    """One ``.doc``'s conversion: its source, the copy converted, and the limits."""

    src: Path
    copy: Path
    workdir: Path
    run: Runner
    max_uncompressed: int
    timeout: float


def _textutil_argv(job: _Job, out: Path) -> list[str]:
    """The textutil command converting the copy to ``out``."""
    return [
        "textutil", "-convert", _TARGET_FORMAT, "-output", str(out), str(job.copy)
    ]  # fmt: skip


def _soffice_argv(soffice: Path, job: _Job, out: Path) -> list[str]:
    """LibreOffice's headless command converting the copy into ``out``'s folder.

    It runs with a private profile in the work directory: a headless call sharing the
    normal profile with an open LibreOffice window is handed to it and never runs.
    """
    profile = (job.workdir / "lo_profile").resolve().as_uri()
    return [
        str(soffice), "--headless", f"-env:UserInstallation={profile}",
        "--convert-to", _TARGET_FORMAT, "--outdir", str(out.parent), str(job.copy),
    ]  # fmt: skip


def _run_converter(
    job: _Job, converter: Converter, argv_for: Callable[[_Job, Path], list[str]]
) -> ConversionResult:
    """Make ``converter``'s output folder, then run it and read what it wrote."""
    out = converted_path(job.workdir, job.src, converter)
    _fresh_dir(out.parent)
    return _convert(
        converter,
        argv_for(job, out),
        out,
        run=job.run,
        max_uncompressed=job.max_uncompressed,
        timeout=job.timeout,
    )


def compare_converters(
    src: Path,
    workdir: Path,
    *,
    run: Runner,
    soffice: Path | None,
    max_uncompressed: int,
    timeout: float,
) -> tuple[ConversionResult, ...]:
    """Convert a copy of ``src`` with textutil and LibreOffice; return both results.

    ``src`` is copied to ``workdir/in/`` first and only the copy is converted. The
    results come back in a fixed order, textutil then LibreOffice; LibreOffice's is
    ``absent`` when ``soffice`` is None.

    ``workdir`` belongs to the caller, who creates it and removes it afterwards: it
    holds the copy, both outputs and LibreOffice's private profile. It may be reused;
    each converter's expected output is removed before that converter runs.
    """
    copy = _fresh_dir(workdir / "in") / src.name
    shutil.copyfile(src, copy)
    job = _Job(src, copy, workdir, run, max_uncompressed, timeout)
    textutil = _run_converter(job, "textutil", _textutil_argv)
    if soffice is None:
        return textutil, ConversionResult("libreoffice", "absent", "", frozenset())
    return textutil, _run_converter(job, "libreoffice", partial(_soffice_argv, soffice))


def real_run(
    argv: Sequence[str], timeout: float
) -> int:  # pragma: no cover - live seam
    """Run a converter for real, discarding its output, and return its exit code.

    Raises ``subprocess.TimeoutExpired`` once ``timeout`` passes, after killing the
    converter's whole process tree. Not covered by the unit tests, which replace it
    wholesale with a fake runner; the macOS live test exercises it against textutil.
    """
    return bounded_run.run_bounded_bytes(argv, timeout=timeout).returncode
