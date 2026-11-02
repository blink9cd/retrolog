"""Read and check retrolog_resolve's review.json, and build the review page's data block.

``read_review`` takes the file's bytes, checks every field the page and the picks
reader rely on, and keeps only the entries with a decision in them (the decidable
kinds); the error kinds and ``override-applied`` are counted instead, since the
operator fixes an error by editing the overrides file by hand and an applied override
needs nothing. ``page_data`` turns the result into the object the page embeds.

Each row an entry lists is ``<source_file>:<source_line>``, and a pick writes one line
override per row, so every row must split into a file and a line, and no two
decidable entries may share a medium, title and rows: a pick names its entry by all
three. Resolve groups rows by both years as well as the title, so one title can make
several entries, told apart by their rows.

Each candidate's ``id`` must be its catalog's id, as resolve's overrides take it: a
film's TMDb id is ``resolve_columns.TMDB_ID``'s (one to ten ASCII digits, no leading
zero); a book's Open Library work is ``/works/OL<digits>W``.
The page builds a catalog link from the id, so an id of any other shape is refused
rather than linked. A book's ``isbn13`` is empty or the ISBN-13 resolve writes, its
check digit valid; a film's is empty.

A refusal names the JSON key at fault (``entries[3].candidates[1].votes``) and never
quotes a value: titles are the operator's private data.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from resolve_columns import TMDB_ID as TMDB_ID
from resolve_isbn import isbn13_or_none

# The kinds the page shows, each with candidates and a typed id to pick from. Python
# owns this list; the page starts the kinds in its own KEEP_KINDS on Keep, and the rest
# undecided: ambiguous, suspect-exact, and not-found.
DECIDABLE_KINDS = (
    "ambiguous",
    "suspect-exact",
    "not-found",
    "most-voted",
    "variant",
    "re-searched",
    "no-isbn",
)
# resolve's error kinds, counted on the page and fixed by hand.
ERROR_KINDS = frozenset({"override-unused", "override-mismatch", "failed"})
# An override settled these rows: counted, nothing to decide.
APPLIED_KIND = "override-applied"
_KINDS = frozenset(DECIDABLE_KINDS) | ERROR_KINDS | {APPLIED_KIND}

_SCHEMA_VERSION = 1
# The data block's own schema: the page's readData shows its unreadable state for any
# other (review.js's PAGE_SCHEMA must match).
PAGE_SCHEMA = 1
_MEDIA = ("book", "film")
_RUN_MEDIA = (*_MEDIA, "all")
_REQUIRED_TOP = ("schema_version", "medium", "entries")
_ENTRY_KEYS = frozenset(
    {"candidates", "detail", "error", "kind", "medium", "rows", "title"}
)
_STRING_FIELDS = frozenset({"creator", "id", "isbn13", "original_title", "title"})
_NUMBER_FIELDS = frozenset({"votes", "year"})
_CANDIDATE_KEYS = _STRING_FIELDS | _NUMBER_FIELDS
# A row's line, after its last colon: ASCII digits only (\d would take other scripts'),
# and no sign or space, which int() would forgive. Bounded before int() reads it, which
# refuses a string past its 4,300-digit limit with a ValueError: no list file runs to a
# billion lines.
_LINE = re.compile(r"[0-9]{1,9}")
_ROW_SEPARATOR = ":"
_ROW_RULE = "must be <file>:<line>, the line a whole number of 1 or more"
# The catalog ids resolve's overrides accept, ASCII only: \d would also take other
# scripts' digits, which no id is written in. The film id is resolve's own TMDB_ID,
# re-exported (the "as" import above) with OL_WORK and the two descriptions, since
# review_picks checks a pick's ids by these.
OL_WORK = re.compile(r"/works/OL[0-9]+W")
TMDB_ID_SHAPE = "a string of 1 to 10 digits with no leading zero"
OL_WORK_SHAPE = "a string /works/OL<digits>W"
# Each medium's id pattern, and how a refusal describes it.
_CANDIDATE_ID: Mapping[str, tuple[re.Pattern[str], str]] = {
    "film": (TMDB_ID, TMDB_ID_SHAPE),
    "book": (OL_WORK, OL_WORK_SHAPE),
}
# An entry's scope, which a pick names it by: medium, title and rows.
Scope = tuple[str, str, tuple[str, ...]]


class ReviewError(Exception):
    """review.json is unusable; the message names the key at fault, never a value."""


class NotJson(Exception):
    """The bytes are not UTF-8 JSON (or nest too deeply to parse)."""


@dataclass(frozen=True)
class Review:
    """A checked review: its digest, its medium, and what the page shows and counts."""

    sha256: str  # hex SHA-256 of the file's bytes, which keys the picks to it
    medium: str  # "book", "film", or "all", as resolve wrote it
    # The decidable entries in file order, each without its "error" key, the rest in
    # the file's key order.
    entries: tuple[Mapping[str, object], ...]
    errors: int  # entries of an error kind
    applied: int  # override-applied entries


def decode_json(data: bytes) -> object:
    """``data`` as UTF-8 JSON; ``NotJson`` otherwise, with no parser text (it quotes).

    json parses nesting recursively, so a deep enough nesting raises RecursionError
    rather than a JSONDecodeError; it is refused the same way.
    """
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise NotJson from None


def _object(where: str, value: object) -> dict[str, object]:
    """``value`` as a JSON object."""
    if not isinstance(value, dict):
        raise ReviewError(f"{where}: must be an object")
    return value


def _exact_keys(where: str, table: Mapping[str, object], keys: frozenset[str]) -> None:
    """Refuse a key outside ``keys`` and a missing one, the unknown first."""
    unknown = sorted(set(table) - keys)
    if unknown:
        raise ReviewError(f"{where}: unknown key {unknown[0]!r}")
    missing = sorted(keys - set(table))
    if missing:
        raise ReviewError(f"{where}.{missing[0]}: missing")


def _string(where: str, value: object) -> str:
    """``value`` as a string (empty allowed)."""
    if not isinstance(value, str):
        raise ReviewError(f"{where}: must be a string")
    return value


def _list(where: str, value: object) -> list[object]:
    """``value`` as a JSON list."""
    if not isinstance(value, list):
        raise ReviewError(f"{where}: must be a list")
    return value


def split_row(row: str) -> tuple[str, int] | None:
    """``row``'s source file and line, split at its LAST colon; None if it has none.

    The file may hold a colon itself; the line cannot. The file must be non-empty and
    the line a whole number of 1 or more, as resolve's overrides loader requires.
    """
    file, separator, line = row.rpartition(_ROW_SEPARATOR)
    if not separator or not file or not _LINE.fullmatch(line) or int(line) < 1:
        return None
    return file, int(line)


def scope_of(entry: Mapping[str, object]) -> Scope:
    """The (medium, title, rows) a checked entry is named by."""
    # _check_rows has made it a list of strings before any caller gets the entry.
    rows = cast(list[str], entry["rows"])
    return str(entry["medium"]), str(entry["title"]), tuple(rows)


def _check_rows(where: str, value: object, decidable: bool) -> None:
    """A list of ``<file>:<line>`` strings, at least one when the entry is decidable."""
    if not isinstance(value, list) or not all(isinstance(row, str) for row in value):
        raise ReviewError(f"{where}: must be a list of strings")
    for index, row in enumerate(value):
        if split_row(row) is None:
            raise ReviewError(f"{where}[{index}]: {_ROW_RULE}")
    if decidable and not value:
        raise ReviewError(f"{where}: a decidable entry must list at least one row")


def _check_candidate_ids(where: str, table: Mapping[str, object], medium: str) -> None:
    """A candidate's ``id`` in its medium's catalog shape, and its ``isbn13`` with it.

    The string fields are already checked to be strings.
    """
    pattern, shape = _CANDIDATE_ID[medium]
    if not pattern.fullmatch(str(table["id"])):
        raise ReviewError(f"{where}.id: must be {shape}")
    isbn = str(table["isbn13"])
    if medium == "film" and isbn:
        raise ReviewError(f"{where}.isbn13: must be empty for a film")
    # resolve writes the normalized ISBN-13, so anything else was not written by it.
    if isbn and isbn13_or_none(isbn) != isbn:
        raise ReviewError(
            f"{where}.isbn13: must be empty or an ISBN-13 with a valid check digit"
        )


def _check_candidate(where: str, value: object, medium: str) -> None:
    """One candidate: its string fields and ids, and ``votes`` and ``year`` int or null."""
    table = _object(where, value)
    _exact_keys(where, table, _CANDIDATE_KEYS)
    for key in sorted(_STRING_FIELDS):
        _string(f"{where}.{key}", table[key])
    for key in sorted(_NUMBER_FIELDS):
        number = table[key]
        # A JSON true is a Python bool, which is an int; it is not a count.
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int)
        ):
            raise ReviewError(f"{where}.{key}: must be an integer or null")
    _check_candidate_ids(where, table, medium)


def _kind(where: str, table: Mapping[str, object]) -> str:
    """The entry's kind, and its ``error`` flag checked against it."""
    kind = table["kind"]
    # The type is checked first: a list or object is unhashable, so a set lookup
    # would raise TypeError on it.
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ReviewError(f"{where}.kind: must be a review kind")
    error = table["error"]
    if not isinstance(error, bool):
        raise ReviewError(f"{where}.error: must be true or false")
    if error != (kind in ERROR_KINDS):
        raise ReviewError(f"{where}.error: must be true for an error kind, else false")
    return kind


def _check_entry(where: str, value: object, medium: str) -> dict[str, object]:
    """One entry, checked whole; returned as its table."""
    table = _object(where, value)
    _exact_keys(where, table, _ENTRY_KEYS)
    kind = _kind(where, table)
    entry_medium = table["medium"]
    if not isinstance(entry_medium, str) or entry_medium not in _MEDIA:
        raise ReviewError(f"{where}.medium: must be book or film")
    if medium not in ("all", entry_medium):
        raise ReviewError(f"{where}.medium: outside the review's medium")
    _string(f"{where}.title", table["title"])
    _string(f"{where}.detail", table["detail"])
    _check_rows(f"{where}.rows", table["rows"], kind in DECIDABLE_KINDS)
    candidates = _list(f"{where}.candidates", table["candidates"])
    for index, candidate in enumerate(candidates):
        _check_candidate(f"{where}.candidates[{index}]", candidate, entry_medium)
    return table


def _document(data: bytes) -> dict[str, object]:
    """The file's top-level object, its schema version, medium and entries present."""
    try:
        document = _object("review file", decode_json(data))
    except NotJson:
        raise ReviewError("review file: not valid UTF-8 JSON") from None
    for key in _REQUIRED_TOP:
        if key not in document:
            raise ReviewError(f"{key}: missing")
    version = document["schema_version"]
    if isinstance(version, bool) or version != _SCHEMA_VERSION:
        raise ReviewError(
            f"schema_version: this version reads schema_version {_SCHEMA_VERSION} only"
        )
    if not isinstance(document["medium"], str) or document["medium"] not in _RUN_MEDIA:
        raise ReviewError("medium: must be book, film, or all")
    return document


def _refuse_shared_scopes(tables: list[dict[str, object]]) -> None:
    """Refuse two decidable entries with one medium, title and rows.

    A pick names its entry by those three, so two such entries could not be told
    apart. Error and applied entries are never picked, so they are not compared.
    """
    seen: dict[Scope, int] = {}
    for index, table in enumerate(tables):
        if table["kind"] not in DECIDABLE_KINDS:
            continue
        scope = scope_of(table)
        if scope in seen:
            raise ReviewError(
                f"entries[{index}]: the same medium, title, and rows as "
                f"entries[{seen[scope]}]"
            )
        seen[scope] = index


# fuzz-target
def read_review(data: bytes) -> Review:
    """Check review.json's bytes and return what the page shows and counts.

    Raises ``ReviewError`` naming the first key at fault.
    """
    document = _document(data)
    medium = str(document["medium"])
    tables = [
        _check_entry(f"entries[{index}]", value, medium)
        for index, value in enumerate(_list("entries", document["entries"]))
    ]
    _refuse_shared_scopes(tables)
    kinds = [str(table["kind"]) for table in tables]
    return Review(
        sha256=hashlib.sha256(data).hexdigest(),
        medium=medium,
        entries=tuple(
            {key: value for key, value in table.items() if key != "error"}
            for table in tables
            if table["kind"] in DECIDABLE_KINDS
        ),
        errors=sum(1 for kind in kinds if kind in ERROR_KINDS),
        applied=kinds.count(APPLIED_KIND),
    )


def page_data(review: Review) -> dict[str, object]:
    """The object the page embeds in its ``review-data`` block, as the page reads it."""
    return {
        "schema": PAGE_SCHEMA,
        "review_sha256": review.sha256,
        "medium": review.medium,
        "not_shown": {"errors": review.errors, "applied": review.applied},
        "entries": [dict(entry) for entry in review.entries],
    }
