"""Survey each file the walk found, and gather the results into one report.

`survey_file` reads one `FileEntry` (a text file, a Word .docx, or a .doc through a
converted copy) and returns a `FileSurvey`: its lines, each classified and described,
and counts of every formatting signal it uses. `build_report` groups those surveys
into the report dictionary that is written as ``report.json`` (its layout is
``SCHEMA_VERSION``), and `render_markdown` turns that dictionary into ``report.md`` for
a person to read. `write_atomic` writes either file so that a reader never sees half
of it.

Describing and counting a line lives in `survey_describe`, the Markdown layout in
`survey_markdown`, and the atomic writers and `describe_error` in `survey_write`;
`view_of`, `describe_paragraph`, `render_markdown` and those three are re-exported
here so the report's whole surface is one import.

A file that cannot be read is not fatal: its `FileSurvey` carries an ``error`` string
instead, built by `describe_error` from the exception's class name and its short
reason, never a path. The report lists those under ``errors``.
"""

from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import get_args

from survey_convert import ConversionResult, Runner, compare_converters, converted_path
from survey_describe import (
    RUN_TABLES,
    TEXT_TABLES,
    classify_paragraph,
    describe_paragraph,
    docx_counts,
    text_counts,
)
from survey_describe import view_of  # noqa: F401 - re-exported
from survey_docx import BadDocx, DocxInfo, ImageInfo, read_docx

from survey_markdown import render_markdown  # noqa: F401 - re-exported
from survey_lines import LineKind, LineView, classify_line
from survey_text import FileTooLarge, read_text
from survey_walk import FileEntry, FileKind

# Re-exported, so this module's importers keep one import. retrolog_resolve imports
# survey_write directly instead, because it must run without a .docx reader.
from survey_write import describe_error as describe_error
from survey_write import write_atomic as write_atomic
from survey_write import write_atomic_bytes as write_atomic_bytes

# The layout of the report dictionary; a reader checks it before trusting any key.
SCHEMA_VERSION = 1
_FILE_KINDS: tuple[FileKind, ...] = get_args(FileKind)
_LINE_KINDS: tuple[LineKind, ...] = get_args(LineKind)
_LINE_ENDINGS = ("crlf", "cr", "lf")
# Count tables a group sums from its files' surveys, in report order.
COUNT_TABLES = (*RUN_TABLES, *TEXT_TABLES)
# Blank lines carry nothing worth reading, so they are counted but never sampled.
_UNSAMPLED: frozenset[str] = frozenset({"blank"})
# Hex digits of a .doc's relative-path hash that name its private work directory.
_WORKDIR_DIGITS = 16
# What reading one file can raise. Anything else is a defect, not a damaged input,
# and `survey_file` still fails only that one file, by class name.
_FILE_ERRORS = (OSError, FileTooLarge, BadDocx)
_NO_CONVERSION = "ConversionFailed: no converter produced a readable .docx"


@dataclass(frozen=True)
class Options:
    """The options a report records, so a reader knows how it was produced."""

    group_depth: int
    samples: int
    converters: bool


@dataclass(frozen=True)
class SurveyContext:
    """What `survey_file` needs beyond the entry itself.

    ``workdir`` is the caller's temporary directory for .doc conversions; None means
    conversion is off, and a .doc is then counted but not read.
    """

    max_bytes: int
    timeout: float
    run: Runner
    soffice: Path | None
    workdir: Path | None


@dataclass(frozen=True)
class SurveyLine:
    """One line (a text line or a Word paragraph): its number, kind and described text."""

    number: int
    kind: LineKind
    text: str


@dataclass(frozen=True)
class FileSurvey:
    """Everything the report needs from one walked file."""

    rel_path: str
    group: str
    kind: FileKind
    encoding: str | None = None
    ambiguous: bool = False
    line_endings: Mapping[str, int] = field(default_factory=dict)
    lines: tuple[SurveyLine, ...] = ()
    counts: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    images: tuple[ImageInfo, ...] = ()
    image_refs: Mapping[str, int] = field(default_factory=dict)  # sha256_12 -> uses
    blobs: Mapping[str, bytes] = field(default_factory=dict)  # sha256_12 -> bytes
    conversion: Mapping[str, object] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------


def _survey_txt(base: FileSurvey, path: Path, ctx: SurveyContext) -> FileSurvey:
    """Read a text file: its encoding guess, line endings, lines and counts."""
    info = read_text(path, max_bytes=ctx.max_bytes)
    lines = tuple(
        SurveyLine(number, classify_line(LineView(text)), text)
        for number, text in enumerate(info.lines, start=1)
    )
    return replace(
        base,
        encoding=info.guess.encoding,
        ambiguous=info.guess.ambiguous,
        line_endings=info.line_endings,
        lines=lines,
        counts=text_counts(info.lines),
    )


def _with_docx(base: FileSurvey, info: DocxInfo) -> FileSurvey:
    """``base`` filled in from a read Word document."""
    lines = tuple(
        SurveyLine(p.index, classify_paragraph(p), describe_paragraph(p))
        for p in info.paragraphs
    )
    refs = Counter(
        image_id
        for p in info.paragraphs
        for run in p.runs
        for image_id in run.image_ids
    )
    blobs = {image.sha256_12: blob for image, blob in zip(info.images, info.blobs)}
    return replace(
        base,
        lines=lines,
        counts=docx_counts(info),
        images=info.images,
        image_refs=dict(refs),
        blobs=blobs,
    )


def _survey_docx(base: FileSurvey, path: Path, ctx: SurveyContext) -> FileSurvey:
    """Read a Word .docx file."""
    return _with_docx(base, read_docx(path, max_uncompressed=ctx.max_bytes))


def conversion_entry(
    rel_path: str, results: Sequence[ConversionResult]
) -> dict[str, object]:
    """The report's record of what each converter kept, and what both kept."""
    textutil, libreoffice = results
    both = textutil.features & libreoffice.features
    entry: dict[str, object] = {"file": rel_path, "both": sorted(both)}
    for result in results:
        entry[result.converter] = {
            "status": result.status,
            "detail": result.detail,
            "only": sorted(result.features - both),
        }
    return entry


def preferred(results: Sequence[ConversionResult]) -> ConversionResult | None:
    """The result whose output is surveyed: LibreOffice's when it worked, else textutil's.

    None when neither worked. Public so retrolog_parse reads the same output the
    survey reported on; ``compare_converters`` lists textutil first, LibreOffice last.
    """
    return next((r for r in reversed(results) if r.status == "ok"), None)


def workdir_name(rel_path: str) -> str:
    """The name of the private work directory for the .doc at ``rel_path``.

    A hash of the path, so the name holds no text from it and two files never share
    one. The hash is of the path's original bytes: a name that is not valid UTF-8
    reaches Python with surrogate escapes, which ``surrogateescape`` turns back into
    the bytes the file system holds instead of raising.
    """
    encoded = rel_path.encode("utf-8", errors="surrogateescape")
    return hashlib.sha256(encoded).hexdigest()[:_WORKDIR_DIGITS]


def _survey_doc(base: FileSurvey, path: Path, ctx: SurveyContext) -> FileSurvey:
    """Convert a copy of a .doc and survey the preferred converter's output."""
    if ctx.workdir is None:
        return base
    if path.stat().st_size > ctx.max_bytes:
        raise FileTooLarge(f"larger than the {ctx.max_bytes}-byte limit")
    workdir = ctx.workdir / workdir_name(base.rel_path)
    try:
        results = compare_converters(
            path,
            workdir,
            run=ctx.run,
            soffice=ctx.soffice,
            max_uncompressed=ctx.max_bytes,
            timeout=ctx.timeout,
        )
        conversion = conversion_entry(base.rel_path, results)
        chosen = preferred(results)
        if chosen is None:
            return replace(base, conversion=conversion, error=_NO_CONVERSION)
        output = converted_path(workdir, path, chosen.converter)
        surveyed = _survey_docx(base, output, ctx)
        return replace(surveyed, conversion=conversion)
    finally:
        # Frees the copy, both outputs and LibreOffice's profile as soon as this file
        # is done. Errors are ignored because nothing is lost: the caller removes the
        # whole temporary directory, and anything left in it, when the survey ends.
        shutil.rmtree(workdir, ignore_errors=True)


_Reader = Callable[[FileSurvey, Path, SurveyContext], FileSurvey]
_READERS: dict[str, _Reader] = {
    "txt": _survey_txt,
    "docx": _survey_docx,
    "doc": _survey_doc,
}


def survey_file(entry: FileEntry, root: Path, ctx: SurveyContext) -> FileSurvey:
    """Survey one walked entry; a read failure becomes the survey's ``error``.

    Only text, .docx and .doc files are opened. Every other kind is passed through
    to be counted, and an ``unreadable`` entry keeps the walk's error class name.
    """
    base = FileSurvey(entry.rel_path, entry.group, entry.kind)
    if entry.kind == "unreadable":
        return replace(base, error=entry.error)
    reader = _READERS.get(entry.kind)
    if reader is None:
        return base
    try:
        return reader(base, root / entry.rel_path, ctx)
    except _FILE_ERRORS as error:
        return replace(base, error=describe_error(error))
    except Exception as error:
        # The last resort. Every reader turns the failures it knows into the errors
        # above, so reaching here means a defect or an input nobody foresaw. One file
        # must still not end a survey of thousands, so it fails that file alone, by
        # class name only: an unforeseen message may quote a path or document text.
        return replace(base, error=type(error).__name__)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _zero_filled(keys: Sequence[str], counts: Mapping[str, int]) -> dict[str, int]:
    """``counts`` with every one of ``keys`` present, in that order."""
    return {key: counts.get(key, 0) for key in keys}


def _summed(tables: Iterable[Mapping[str, int]]) -> dict[str, int]:
    """The key-by-key sum of several count tables."""
    total: Counter[str] = Counter()
    for table in tables:
        total.update(table)
    return dict(total)


def _images(members: Sequence[FileSurvey]) -> list[dict[str, object]]:
    """Each distinct image once, with how many times the group's runs show it."""
    infos = {image.sha256_12: image for f in members for image in f.images}
    uses = _summed(f.image_refs for f in members)
    return [
        {
            "content_type": infos[sha].content_type,
            "byte_size": infos[sha].byte_size,
            "sha256_12": sha,
            "count": uses.get(sha, 0),
        }
        for sha in sorted(infos)
    ]


def _samples(
    members: Sequence[FileSurvey], limit: int
) -> dict[str, list[dict[str, object]]]:
    """The first ``limit`` lines of each kind, by file then line; blanks are skipped."""
    samples: dict[str, list[dict[str, object]]] = {}
    for f in members:
        for line in f.lines:
            if line.kind in _UNSAMPLED:
                continue
            taken = samples.setdefault(line.kind, [])
            if len(taken) < limit:
                taken.append(
                    {"file": f.rel_path, "line": line.number, "text": line.text}
                )
    return {kind: taken for kind, taken in samples.items() if taken}


def _group_report(
    name: str, members: Sequence[FileSurvey], limit: int
) -> dict[str, object]:
    """One group's section of the report."""
    group: dict[str, object] = {
        "group": name,
        "files": _zero_filled(_FILE_KINDS, Counter(f.kind for f in members)),
        "encodings": dict(Counter(f.encoding for f in members if f.encoding)),
        "ambiguous_encodings": sum(f.ambiguous for f in members),
        "line_endings": _zero_filled(
            _LINE_ENDINGS, _summed(f.line_endings for f in members)
        ),
        "line_kinds": _zero_filled(
            _LINE_KINDS, Counter(line.kind for f in members for line in f.lines)
        ),
    }
    for table in COUNT_TABLES:
        group[table] = _summed(f.counts.get(table, {}) for f in members)
    group["images"] = _images(members)
    group["conversions"] = [dict(f.conversion) for f in members if f.conversion]
    group["samples"] = _samples(members, limit)
    group["errors"] = [
        {"file": f.rel_path, "error": f.error} for f in members if f.error
    ]
    return group


def build_report(
    files: Sequence[FileSurvey], options: Options, converters: dict[str, str]
) -> dict[str, object]:
    """The whole report: options, converter availability, and one section per group."""
    by_group: dict[str, list[FileSurvey]] = {}
    for f in sorted(files, key=lambda survey: survey.rel_path):
        by_group.setdefault(f.group, []).append(f)
    return {
        "schema_version": SCHEMA_VERSION,
        "options": {
            "group_depth": options.group_depth,
            "samples": options.samples,
            "converters": options.converters,
        },
        "converters": dict(converters),
        "groups": [
            _group_report(name, by_group[name], options.samples)
            for name in sorted(by_group)
        ],
    }
