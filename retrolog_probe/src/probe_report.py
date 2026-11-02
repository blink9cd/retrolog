"""Render the run as a Markdown report, and the one count line the CLI prints.

Pure: every function returns text, and the caller writes it. The report quotes the
import files' titles, which are free text the operator wrote years ago, so every cell
goes through parse's table-cell escape, the rule every review list of these tools
uses: a title cannot start markup or end a cell. The omit entries quote each title as
a TOML basic string with the backtick escaped too, so a title can end neither the
string nor the code block around it. The count line holds numbers only, never a title.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from parse_output import escape_cell
from parse_rows import title_key
from probe_input import IdRow, ImportRows
from probe_run import RunResult
from survey_markdown import counted

_TITLE = "# retrolog_probe report"
_ROW_NOTE = (
    "Row counts each file's data rows from 1, the header not counted. It is not a "
    "line number: a review can span lines."
)
_CLOSING = (
    "Leave the missing films out of the import: add the omit entries and run export "
    "again, or delete their rows from the import file before uploading it. Run the "
    "check again later for the unknown ones."
)
_OMIT_NOTE = (
    "To leave the missing films out of this and every later export, add these entries "
    "to the export config's `[letterboxd]` settings and run export again into a new "
    "folder."
)
# Escaped in a TOML basic string: the two it requires, and the backtick, which would
# otherwise let a title close the report's code block. Every other control character
# is written as \uXXXX.
_SHORT_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t"}
_ESCAPED_AS_CODE = frozenset("`\x7f")
_FIRST_PRINTABLE = " "
# export writes Year as resolve's year, an integer, or leaves it empty.
_YEAR = re.compile(r"[0-9]{1,4}")
_MISSING_HEAD = (
    "| File | Row | tmdbID | Title | Year |",
    "| --- | --- | --- | --- | --- |",
)
_UNKNOWN_HEAD = (
    "| File | Row | tmdbID | Title | Year | Reason |",
    "| --- | --- | --- | --- | --- | --- |",
)
_VERDICTS = ("present", "missing", "unknown")


def _tally(result: RunResult) -> str:
    """How many distinct ids, and how many of each verdict."""
    counts = [
        sum(1 for outcome in result.outcomes.values() if outcome.verdict == verdict)
        for verdict in _VERDICTS
    ]
    each = ", ".join(
        f"{count} {verdict}" for count, verdict in zip(counts, _VERDICTS, strict=True)
    )
    return f"{counted(len(result.outcomes), 'distinct tmdbID')}: {each}"


def summary_line(result: RunResult) -> str:
    """The count line the CLI prints: ids, verdicts, cache and requests; no title."""
    return (
        f"{_tally(result)} ({result.from_cache} from the cache, "
        f"{counted(result.requests, 'request')} sent)"
    )


def _count_paragraph(rows: ImportRows, result: RunResult) -> str:
    """What was read, and what the run found."""
    return (
        f"Read {counted(len(rows.files), 'import file')}: "
        f"{counted(rows.rows, 'row')}, {rows.by_title} of them by title, which "
        f"{'carries' if rows.by_title == 1 else 'carry'} no tmdbID and "
        f"{'is' if rows.by_title == 1 else 'are'} not checked. {_tally(result)}; "
        f"{result.from_cache} answered from the cache, "
        f"{counted(result.requests, 'request')} sent."
    )


def _table_row(cells: Sequence[object]) -> str:
    """One table row, every cell escaped; an empty cell is one space between pipes."""
    padded = (f" {text} " if text else " " for text in map(escape_cell, cells))
    return "|" + "|".join(padded) + "|"


def _section(
    heading: str, head: tuple[str, str], rows: Sequence[Sequence[object]]
) -> list[str]:
    """A heading with its row count, then the table, or "None." when it is empty.

    Rows, not films: a film watched twice is two rows to delete from the import.
    """
    body = [*head, *(_table_row(row) for row in rows)] if rows else ["None."]
    return ["", f"## {heading} ({counted(len(rows), 'row')})", "", *body]


def _cells(row: IdRow) -> list[object]:
    """The cells every table starts with: where the row is and what it names."""
    return [row.file, row.row, row.tmdb_id, row.title, row.year]


# fuzz-target
def toml_string(text: str) -> str:
    """``text`` as a TOML basic string holding no backtick; no lone surrogate allowed.

    The same rule as review's overrides writer, plus the backtick, kept apart so probe
    imports nothing of review's.
    """
    escaped = "".join(
        _SHORT_ESCAPES.get(char)
        or (
            f"\\u{ord(char):04X}"
            if char < _FIRST_PRINTABLE or char in _ESCAPED_AS_CODE
            else char
        )
        for char in text
    )
    return f'"{escaped}"'


def _entry_year(row: IdRow) -> int | None:
    """The year ``row``'s omit entry names, or None when it names none."""
    return int(row.year) if _YEAR.fullmatch(row.year) else None


def _omit_entry(row: IdRow) -> list[str]:
    """One ``[[letterboxd.omit]]`` table naming ``row``'s film; no year when it has none."""
    lines = ["[[letterboxd.omit]]", f"title = {toml_string(row.title)}"]
    year = _entry_year(row)
    if year is not None:
        lines.append(f"year = {year}")
    return lines


def _distinct_entries(missing: Sequence[IdRow]) -> list[IdRow]:
    """The rows that give the omit entries: the first of each film and of each key.

    Export keys an entry by its title's ``title_key`` and its year, and stops on two
    entries with one key, so two missing films whose titles fold alike in one year
    share one entry, which names them both. A film's later rows add none, whatever
    title they show: export leaves out every row of a film one entry names. Only a
    row that gives an entry claims its key, so a film's later row cannot shut out
    another film's entry, which would leave that film named by no entry.
    """
    films: set[str] = set()
    keys: set[tuple[str, int | None]] = set()
    kept: list[IdRow] = []
    for row in missing:
        key = (title_key(row.title), _entry_year(row))
        if row.tmdb_id not in films and key not in keys:
            kept.append(row)
            keys.add(key)
        films.add(row.tmdb_id)
    return kept


def _omit_section(missing: Sequence[IdRow]) -> list[str]:
    """The omit entries, one per missing film and title key; nothing when none.

    The heading counts the missing films, not the entries: films sharing a title and
    year share one entry.
    """
    first = _distinct_entries(missing)
    if not first:
        return []
    tables = [line for row in first for line in _omit_entry(row)]
    films = len({row.tmdb_id for row in missing})
    heading = f"## Omit entries ({counted(films, 'film')})"
    return ["", heading, "", _OMIT_NOTE, "", "```toml", *tables, "```"]


def report_markdown(rows: ImportRows, result: RunResult) -> str:
    """The whole report: the counts, the missing rows, the unknown rows, what to do."""
    verdict_of = result.outcomes
    missing_rows = [
        row for row in rows.id_rows if verdict_of[row.tmdb_id].verdict == "missing"
    ]
    missing = [_cells(row) for row in missing_rows]
    unknown = [
        [*_cells(row), verdict_of[row.tmdb_id].reason]
        for row in rows.id_rows
        if verdict_of[row.tmdb_id].verdict == "unknown"
    ]
    lines = [_TITLE, "", _count_paragraph(rows, result), "", _ROW_NOTE]
    lines += _section("Missing", _MISSING_HEAD, missing)
    lines += _omit_section(missing_rows)
    lines += _section("Unknown", _UNKNOWN_HEAD, unknown)
    lines += ["", _CLOSING]
    return "\n".join(lines) + "\n"
