"""Load and check the operator's resolve overrides file.

The file is ``schema = 1`` plus ``[[overrides]]`` tables. Each names a ``medium`` and a
``title``, matched by parse's ``title_key`` so spacing, case and punctuation do not
matter, and optionally a source ``file`` and ``line`` that narrow it to one row. It then
gives exactly one answer: ``isbn13``, ``ol_work`` (a book may give both), ``tmdb_id``
(a film only) or ``unresolvable = true``.

The value checks are parse's ``parse_checks`` helpers, so a refusal reads the way a
rules-file refusal does: it names the TOML key (``overrides[3].tmdb_id``) and never
quotes the value, which can be the operator's own list text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import parse_checks as checks
from parse_checks import RulesError
from parse_rows import title_key
from resolve_isbn import isbn13_or_none
from resolve_types import Medium, ResolveOverride

_SCHEMA = 1
_TOP_KEYS = frozenset({"schema", "overrides"})
_REQUIRED = frozenset({"medium", "title"})
_ID_KEYS = ("isbn13", "ol_work", "tmdb_id", "unresolvable")
_ENTRY_KEYS = _REQUIRED | {"file", "line"} | set(_ID_KEYS)
# Which id keys each medium may give. A book may give isbn13 and ol_work together.
_MEDIUM_IDS: Mapping[Medium, frozenset[str]] = {
    "book": frozenset({"isbn13", "ol_work"}),
    "film": frozenset({"tmdb_id"}),
}
_OTHER_MEDIUM: Mapping[Medium, str] = {"book": "a film", "film": "a book"}
# ASCII only: \d would also accept other scripts' digits, which no id is written in.
_OL_WORK = re.compile(r"/works/OL[0-9]+W")
_TMDB_ID = re.compile(r"[0-9]+")
# The key an entry's (medium, title_key, file, line) scope is compared by.
_Scope = tuple[str, str, str | None, int | None]


class OverridesError(Exception):
    """The overrides file is unusable; the message names the TOML key, never a value."""


def _keyed(where: str, value: object, allowed: frozenset[str]) -> dict[str, object]:
    """A table holding only ``allowed`` keys."""
    table = checks.table(where, value)
    unknown = sorted(set(table) - allowed)
    if unknown:
        prefix = f"{where}: " if where else ""
        raise OverridesError(f"{prefix}unknown key {unknown[0]!r}")
    return table


def _medium(where: str, value: object) -> Medium:
    """``medium``: "book" or "film"."""
    if value == "book":
        return "book"
    if value == "film":
        return "film"
    raise OverridesError(f"{where}: must be book or film")


def _title_key(where: str, value: object) -> str:
    """``title`` as its title_key, which must keep a letter or digit to match by."""
    key = title_key(checks.string(where, value))
    if not key:
        raise OverridesError(f"{where}: has no letter or digit to match a row by")
    return key


def _scope(where: str, entry: Mapping[str, object]) -> tuple[str | None, int | None]:
    """``file`` and ``line``: both given, or neither."""
    if "file" in entry and "line" not in entry:
        raise OverridesError(f"{where}.line: required with file")
    if "line" in entry and "file" not in entry:
        raise OverridesError(f"{where}.file: required with line")
    if "file" not in entry:
        return None, None
    return (
        checks.relative_file(f"{where}.file", entry["file"]),
        checks.integer(f"{where}.line", entry["line"], 1, checks.NO_LIMIT),
    )


def _check_id_keys(where: str, medium: Medium, entry: Mapping[str, object]) -> None:
    """Refuse an id the medium cannot take, or other than exactly one answer."""
    given = [key for key in _ID_KEYS if key in entry]
    for key in given:
        if key != "unresolvable" and key not in _MEDIUM_IDS[medium]:
            raise OverridesError(f"{where}.{key}: only for {_OTHER_MEDIUM[medium]}")
    ids = [key for key in given if key != "unresolvable"]
    if ("unresolvable" in entry) == bool(ids):
        raise OverridesError(
            f"{where}: needs exactly one of isbn13, ol_work, tmdb_id or unresolvable"
        )
    if "unresolvable" in entry and entry["unresolvable"] is not True:
        raise OverridesError(f"{where}.unresolvable: must be true")


def _id_value(
    where: str, entry: Mapping[str, object], key: str, pattern: re.Pattern[str]
) -> str | None:
    """``entry[key]``, when given, as a string matching ``pattern`` whole."""
    if key not in entry:
        return None
    text = entry[key]
    if not isinstance(text, str) or not pattern.fullmatch(text):
        raise OverridesError(f"{where}.{key}: must be {_ID_SHAPES[key]}")
    return text


def _isbn13(where: str, entry: Mapping[str, object]) -> str | None:
    """``isbn13``, when given, checked and normalized by ``isbn13_or_none``."""
    if "isbn13" not in entry:
        return None
    text = entry["isbn13"]
    isbn = isbn13_or_none(text) if isinstance(text, str) else None
    if isbn is None:
        raise OverridesError(f"{where}.isbn13: must be {_ID_SHAPES['isbn13']}")
    return isbn


# How each id key's refusal describes the value it needs.
_ID_SHAPES: Mapping[str, str] = {
    "isbn13": "a string holding an ISBN-13 or ISBN-10 with a valid check digit",
    "ol_work": "a string /works/OL<digits>W",
    "tmdb_id": "a string of digits",
}


def _entry(index: int, value: object) -> ResolveOverride:
    """One ``[[overrides]]`` table as a ``ResolveOverride``."""
    where = f"overrides[{index}]"
    entry = _keyed(where, value, frozenset(_ENTRY_KEYS))
    missing = sorted(_REQUIRED - set(entry))
    if missing:
        raise OverridesError(f"{where}.{missing[0]}: missing")
    medium = _medium(f"{where}.medium", entry["medium"])
    _check_id_keys(where, medium, entry)
    file, line = _scope(where, entry)
    return ResolveOverride(
        index=index,
        medium=medium,
        title_key=_title_key(f"{where}.title", entry["title"]),
        file=file,
        line=line,
        isbn13=_isbn13(where, entry),
        ol_work=_id_value(where, entry, "ol_work", _OL_WORK),
        tmdb_id=_id_value(where, entry, "tmdb_id", _TMDB_ID),
        unresolvable="unresolvable" in entry,
    )


def _refuse_duplicates(entries: tuple[ResolveOverride, ...]) -> None:
    """Refuse two entries for one (medium, title_key, file, line).

    Keeping either would drop the other's answer silently, so the operator settles it.
    """
    seen: dict[_Scope, int] = {}
    for entry in entries:
        scope = (entry.medium, entry.title_key, entry.file, entry.line)
        if scope in seen:
            raise OverridesError(
                f"overrides[{entry.index}]: the same medium, title, file and line as "
                f"overrides[{seen[scope]}]"
            )
        seen[scope] = entry.index


def _document(data: bytes) -> dict[str, object]:
    """The file's top-level table, its schema checked."""
    try:
        decoded = checks.decode(data)
    except RulesError:
        raise OverridesError("overrides file: not valid UTF-8 TOML") from None
    document = _keyed("", decoded, _TOP_KEYS)
    if "schema" not in document:
        raise OverridesError("schema: missing")
    schema = document["schema"]
    if isinstance(schema, bool) or schema != _SCHEMA:
        raise OverridesError(f"schema: this version reads schema {_SCHEMA} only")
    return document


# fuzz-target
def load_overrides(data: bytes) -> tuple[ResolveOverride, ...]:
    """Check the overrides file's bytes and return its entries in file order.

    Raises ``OverridesError`` naming the first key at fault.
    """
    document = _document(data)
    tables = document.get("overrides", [])
    if not isinstance(tables, list):
        raise OverridesError("overrides: must be an array of tables [[overrides]]")
    try:
        entries = tuple(_entry(index, value) for index, value in enumerate(tables))
    except RulesError as error:
        # parse_checks' helpers name the key the same way; only the class differs.
        raise OverridesError(str(error)) from None
    _refuse_duplicates(entries)
    return entries
