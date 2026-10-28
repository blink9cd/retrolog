"""Build retrolog_export test inputs at test time, so no resolve output is ever committed.

A resolve directory is written into a temporary folder as CSV files with resolve's own
columns (``export_input.INPUT_COLUMNS``), and an export config is rendered as TOML
bytes. Every title is invented (``Placeholder Film One``) or a well-known public-domain
work, and every id is made up: nothing here comes from a real list. Letterboxd account
exports are built here too, as CSV text written into a folder or a ``.zip``.

A later sample builder may import this module, so it uses the standard library only:
the hypothesis strategies the fuzz tests share are in ``export_strategies``.
"""

from __future__ import annotations

import csv
import io
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from export_input import INPUT_COLUMNS

# One resolve film row whose every cell is filled the way resolve writes it. A test
# changes the cells it cares about through ``resolved_row``'s keyword arguments.
_ROW_DEFAULTS: Mapping[str, str] = {
    "medium": "film",
    "title": "Placeholder Film One",
    "creator": "Placeholder Director",
    "year": "1999",
    "date": "2010-03-04",
    "date_is_approximate": "false",
    "color": "",
    "rating_raw": "2",
    "comment": "",
    "rewatch": "false",
    "section": "",
    "source_file": "10/seen.txt",
    "source_line": "1",
    "resolve_status": "resolved",
    "isbn13": "",
    "ol_work": "",
    "tmdb_id": "1000001",
    "tmdb_title": "",
    "tmdb_original_title": "",
}


def resolved_row(**cells: str) -> dict[str, str]:
    """A full resolve row, the defaults replaced by ``cells``.

    A ``resolved`` row with a tmdbID and no ``tmdb_title`` given carries its title as
    its TMDb title, as resolve writes a film its exact-title rule settled.
    """
    unknown = set(cells) - set(INPUT_COLUMNS)
    if unknown:
        raise KeyError(f"not a resolve column: {sorted(unknown)}")
    row = {**_ROW_DEFAULTS, **cells}
    if "tmdb_title" not in cells and row["tmdb_id"]:
        if row["resolve_status"] == "resolved":
            row["tmdb_title"] = row["title"]
    return row


def csv_text(
    rows: Sequence[Mapping[str, str]], header: Sequence[str] = INPUT_COLUMNS
) -> str:
    """CSV text the way resolve writes it: ``header``, then each row's cells in order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([row.get(name, "") for name in header] for row in rows)
    return buffer.getvalue()


def write_resolved(folder: Path, files: Mapping[str, str]) -> Path:
    """Write each ``name -> text`` into ``folder`` (created), and return ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8", newline="")
    return folder


# ---------------------------------------------------------------------------
# The export config
# ---------------------------------------------------------------------------

# A TOML value a test may write: what tomllib reads back, nested in lists.
TomlValue = bool | int | float | str | list["TomlValue"]


def toml_value(value: TomlValue) -> str:
    """``value`` as TOML source text; a float's ``repr`` is TOML's spelling of it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return repr(value)


def toml_string(text: str) -> str:
    """``text`` as a TOML basic string.

    Not ``json.dumps``: JSON writes a character beyond U+FFFF as a surrogate pair,
    which TOML refuses. Every control character is escaped, as TOML requires.
    """
    escaped = "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char) == "Cc" else char
        for char in text.replace("\\", "\\\\").replace('"', '\\"')
    )
    return f'"{escaped}"'


def _table(name: str, entries: Mapping[str, TomlValue]) -> str:
    """One ``[name]`` table, each key quoted so any key text survives."""
    lines = [f"\n[{name}]"]
    lines += [
        f"{toml_string(key)} = {toml_value(value)}" for key, value in entries.items()
    ]
    return "\n".join(lines) + "\n"


# The [letterboxd] keys a test leaves at their default, in the sample's order.
_LETTERBOXD_DEFAULTS: Mapping[str, TomlValue] = {
    "watched_date": True,
    "tags": ["retrolog"],
    "review_note": "",
    "review_note_rows": "commented",
}
DEFAULT_RATINGS: Mapping[str, TomlValue] = {"1": 1.0, "2": 2.5}


def config_toml(
    *,
    ratings: Mapping[str, TomlValue] | None = None,
    rating_tags: Mapping[str, TomlValue] | None = None,
    rating_names: Mapping[str, TomlValue] | None = None,
    cutoff: Mapping[str, TomlValue] | None = None,
    omit: Sequence[str] = (),
    head: str = "schema = 1\n",
    **letterboxd: TomlValue,
) -> bytes:
    """An export config of ``head`` and a ``[letterboxd]`` table.

    ``letterboxd`` replaces or adds plain keys of the table; ``omit`` drops any key
    of it, ``ratings`` included. ``rating_tags``, ``rating_names`` and ``cutoff`` are
    written only when given.
    """
    plain = {**_LETTERBOXD_DEFAULTS, **letterboxd}
    text = head + _table(
        "letterboxd", {k: v for k, v in plain.items() if k not in omit}
    )
    if "ratings" not in omit:
        text += _table(
            "letterboxd.ratings", DEFAULT_RATINGS if ratings is None else ratings
        )
    if rating_tags is not None:
        text += _table("letterboxd.rating_tags", rating_tags)
    if rating_names is not None:
        text += _table("letterboxd.rating_names", rating_names)
    if cutoff is not None:
        text += _table("letterboxd.cutoff", cutoff)
    return text.encode()


# ---------------------------------------------------------------------------
# Account exports
# ---------------------------------------------------------------------------

# The columns Letterboxd's "Export your data" writes in watched.csv; diary.csv and the
# others add more after them, which the reader ignores.
ACCOUNT_HEADER = ("Date", "Name", "Year", "Letterboxd URI")


def account_csv(
    films: Sequence[tuple[str, str]], header: Sequence[str] = ACCOUNT_HEADER
) -> str:
    """An account export CSV of ``(Name, Year)`` films, under ``header``."""
    rows = [
        {
            "Date": "2020-01-02",
            "Name": name,
            "Year": year,
            "Letterboxd URI": "https://boxd.it/placeholder",
        }
        for name, year in films
    ]
    return csv_text(rows, header)


def write_account_folder(folder: Path, files: Mapping[str, str | bytes]) -> Path:
    """An unpacked account export: each ``relative name -> text or bytes`` in ``folder``."""
    for name, content in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)
    return folder


def write_account_zip(path: Path, members: Mapping[str, str | bytes]) -> Path:
    """An account export ``.zip`` holding each ``member name -> text or bytes``."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path
