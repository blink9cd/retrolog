"""Read one walked source file into ``SourceLine``s.

A ``.txt`` line carries its text only, as one mark-free segment. A Word paragraph is
split into segments, each opened by one symbol glyph, because the survey found a legend
and an entry, and two entries, sharing one line; every later stage reads the segments
rather than re-reading runs.

A ``.doc`` is never read directly: a copy is converted to ``.docx`` inside a private
directory under the caller's work directory (``SourceContext.workdir``, outside the
source tree), LibreOffice's output is preferred when it worked, and the private
directory is removed afterward. The sibling retrolog_survey ``src/`` must already be on
``sys.path``; ``retrolog_parse.py`` and the tests put it there.

Every failure becomes a ``SourceError`` whose message is an exception class name or a
fixed phrase, never a path or document text.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from parse_types import Mark, Segment, SourceLine
from survey_convert import ConversionResult, Runner, compare_converters, converted_path
from survey_docx import BadDocx, DocxInfo, RunInfo, read_docx
from survey_lines import SYMBOL_FONTS
from survey_report import preferred, workdir_name
from survey_text import FileTooLarge, read_text
from survey_walk import FileEntry

# survey_docx's prefix for a theme color, which names no RGB value to compare.
_THEME = "theme:"
# What a reader can raise that fails one file: renamed to SourceError by class name.
_READ_ERRORS = (BadDocx, FileTooLarge, OSError)
_NO_CONVERSION = "no converter produced output"
_NOT_SOURCE = "not a source file"


@dataclass(frozen=True)
class SourceContext:
    """What reading a file needs beyond the file itself."""

    workdir: Path  # the caller's temporary directory, outside --path
    run: Runner
    soffice: Path | None
    max_bytes: int
    timeout: float


class SourceError(Exception):
    """One file could not be read; the message is a class name or a fixed phrase."""


# fuzz-target
def lines_from_text(rel_path: str, lines: Sequence[str]) -> tuple[SourceLine, ...]:
    """One ``SourceLine`` per text line, numbered from 1, each one plain segment."""
    return tuple(
        SourceLine(rel_path, number, text, (Segment((), text, ()),))
        for number, text in enumerate(lines, start=1)
    )


def _color(run: RunInfo) -> str | None:
    """The run's RGB color, upper-case, or None for auto, a theme color or none."""
    if run.color is None or run.color.startswith(_THEME):
        return None
    return run.color.upper()


def _is_symbol_run(run: RunInfo) -> bool:
    """True when the run's font draws pictures rather than letters."""
    return run.font is not None and run.font.casefold() in SYMBOL_FONTS


def marks_of(run: RunInfo) -> tuple[Mark, ...]:
    """One Mark per visible glyph the run draws in a symbol font, plus its w:sym."""
    marks: list[Mark] = []
    if run.symbol is not None:
        marks.append(Mark(run.symbol, _color(run)))
    if _is_symbol_run(run) and run.font is not None:
        marks.extend(
            Mark(f"{run.font}/{ord(ch):04X}", _color(run))
            for ch in run.text
            if not ch.isspace()
        )
    return tuple(marks)


class _SegmentBuilder:
    """Collects one paragraph's segments as its runs are walked in order."""

    def __init__(self) -> None:
        self.done: list[Segment] = []
        self.marks: tuple[Mark, ...] = ()
        self.parts: list[str] = []
        self.colors: list[str] = []

    def close(self) -> None:
        """End the current segment, dropping it only if it is an empty leading one.

        Only the leading segment can lack marks, since every later one is opened by a
        mark, so "no marks and no text" means exactly that empty leading segment.
        """
        text = "".join(self.parts)
        if self.marks or text:
            self.done.append(Segment(self.marks, text, tuple(self.colors)))
        self.marks, self.parts, self.colors = (), [], []

    def add(self, run: RunInfo) -> None:
        """Every mark opens a new segment; plain-font text joins the current one."""
        for mark in marks_of(run):
            self.close()
            self.marks = (mark,)
        if _is_symbol_run(run):
            return  # a symbol font's characters are glyphs, never text
        self.parts.append(run.text)
        color = _color(run)
        if run.text.strip() and color is not None:
            self.colors.append(color)


def _segments(runs: Sequence[RunInfo]) -> tuple[Segment, ...]:
    """A paragraph's runs, split into segments each opened by one glyph."""
    builder = _SegmentBuilder()
    for run in runs:
        builder.add(run)
    builder.close()
    return tuple(builder.done)


def lines_from_docx(rel_path: str, info: DocxInfo) -> tuple[SourceLine, ...]:
    """One ``SourceLine`` per paragraph, numbered by its document-order index."""
    lines: list[SourceLine] = []
    for paragraph in info.paragraphs:
        segments = _segments(paragraph.runs)
        text = "".join(segment.text for segment in segments)
        lines.append(SourceLine(rel_path, paragraph.index, text, segments))
    return tuple(lines)


def _read_txt(
    entry: FileEntry, path: Path, ctx: SourceContext
) -> tuple[SourceLine, ...]:
    """A text file's lines, decoded the way the survey decodes them."""
    return lines_from_text(
        entry.rel_path, read_text(path, max_bytes=ctx.max_bytes).lines
    )


def _read_docx(
    entry: FileEntry, path: Path, ctx: SourceContext
) -> tuple[SourceLine, ...]:
    """A .docx file's paragraphs."""
    info = read_docx(path, max_uncompressed=ctx.max_bytes)
    return lines_from_docx(entry.rel_path, info)


def _preferred(results: Sequence[ConversionResult]) -> ConversionResult:
    """The output the survey reported on: ``survey_report.preferred``'s choice.

    Raises ``SourceError`` when neither converter produced output.
    """
    chosen = preferred(results)
    if chosen is None:
        raise SourceError(_NO_CONVERSION)
    return chosen


def _read_doc(
    entry: FileEntry, path: Path, ctx: SourceContext
) -> tuple[SourceLine, ...]:
    """Convert a copy of a .doc in a private work directory and read the result."""
    if path.stat().st_size > ctx.max_bytes:
        raise FileTooLarge(f"larger than the {ctx.max_bytes}-byte limit")
    workdir = ctx.workdir / workdir_name(entry.rel_path)
    try:
        results = compare_converters(
            path,
            workdir,
            run=ctx.run,
            soffice=ctx.soffice,
            max_uncompressed=ctx.max_bytes,
            timeout=ctx.timeout,
        )
        output = converted_path(workdir, path, _preferred(results).converter)
        return _read_docx(entry, output, ctx)
    finally:
        # The copy, both outputs and LibreOffice's profile go as soon as this file is
        # done; anything left is removed with the caller's temporary directory.
        shutil.rmtree(workdir, ignore_errors=True)


_Reader = Callable[[FileEntry, Path, SourceContext], tuple[SourceLine, ...]]
_READERS: dict[str, _Reader] = {"txt": _read_txt, "docx": _read_docx, "doc": _read_doc}


def read_source(
    entry: FileEntry, root: Path, ctx: SourceContext
) -> tuple[SourceLine, ...]:
    """Read one walked ``.txt``, ``.docx`` or ``.doc`` entry into its lines.

    Raises ``SourceError`` for any other kind and for every read failure, naming the
    failure's class only: an OS message can carry an absolute path.
    """
    reader = _READERS.get(entry.kind)
    if reader is None:
        raise SourceError(_NOT_SOURCE)
    try:
        return reader(entry, root / entry.rel_path, ctx)
    except _READ_ERRORS as error:
        raise SourceError(type(error).__name__) from error
