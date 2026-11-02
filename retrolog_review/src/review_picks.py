"""Check the picks saved from the review page, and turn them into an overrides file.

``build_overrides`` reads ``picks.json``'s bytes against the checked review: the
digest must be the review's, each pick must name one decidable entry by its medium,
title, and rows together, no entry may be picked twice, and each answer must be one of
the page's (``"keep"``, a ``tmdb_id``, an ``ol_work`` and/or ``isbn13``, or
``unresolvable``). Every pick other than keep becomes one line override per row its
entry lists, naming the row's ``file`` and ``line``: a title-only override would also
move the title's rows resolve settled on its own, which the review never showed. The tables are rendered as TOML and then checked by retrolog_resolve's
own ``load_overrides``, so a file this module returns is one resolve accepts.

A refusal names the pick's index and key (``picks[3].answer.tmdb_id``) and never
quotes a value: titles and ids are the operator's private data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from resolve_isbn import isbn13_or_none
from resolve_overrides import OverridesError, load_overrides
from review_read import (
    OL_WORK,
    OL_WORK_SHAPE,
    TMDB_ID,
    TMDB_ID_SHAPE,
    NotJson,
    Review,
    Scope,
    decode_json,
    scope_of,
    split_row,
)

_SCHEMA = 1
_KEEP = "keep"
_TOP_KEYS = frozenset({"schema", "review_sha256", "picks"})
_PICK_KEYS = frozenset({"medium", "title", "rows", "answer"})
_MEDIA = ("book", "film")
# The answer objects a pick may hold, by their key sets.
_ANSWER_SHAPES = frozenset(
    {
        frozenset({"tmdb_id"}),
        frozenset({"ol_work"}),
        frozenset({"isbn13"}),
        frozenset({"ol_work", "isbn13"}),
        frozenset({"unresolvable"}),
    }
)
_ANSWER_KEYS = frozenset({"tmdb_id", "ol_work", "isbn13", "unresolvable"})
# The order an answer's keys are written in.
_WRITE_ORDER = ("ol_work", "isbn13", "tmdb_id", "unresolvable")
_SHAPE_RULE = (
    'must be "keep" or an object with tmdb_id, ol_work and/or isbn13, or unresolvable'
)
# The medium each id key belongs to; unresolvable belongs to either.
_KEY_MEDIUM: Mapping[str, str] = {
    "tmdb_id": "film",
    "ol_work": "book",
    "isbn13": "book",
}
# TOML basic-string escapes with a short form; other controls take \uXXXX.
_SHORT_ESCAPES: Mapping[str, str] = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
_DELETE = "\x7f"
_FIRST_PRINTABLE = " "
# An answer as written: each key to its string value, or True for unresolvable.
Answer = Mapping[str, str | bool]


class PicksError(Exception):
    """picks.json is unusable; the message names the index and key, never a value."""


@dataclass(frozen=True)
class Pick:
    """One checked pick: the entry it names and its answer (None for keep)."""

    medium: str
    title: str
    rows: tuple[str, ...]  # the entry's rows, each <source_file>:<source_line>
    answer: Answer | None


@dataclass(frozen=True)
class Overrides:
    """The overrides file to write, and what the picks came to."""

    toml: str
    tables: tuple[str, ...]  # the [[overrides]] tables alone, for appending elsewhere
    written: int  # [[overrides]] tables written, one per row of each picked entry
    picked: int  # picks that wrote overrides (any answer but keep)
    kept: int  # picks left on keep
    undecided: int  # decidable entries with no pick


def toml_string(text: str) -> str:
    """``text`` as a TOML basic string, quotes, backslashes and controls escaped.

    ``text`` must be valid Unicode (no lone surrogate): TOML cannot hold one.
    """
    escaped = "".join(
        _SHORT_ESCAPES.get(char)
        or (
            f"\\u{ord(char):04X}"
            if char < _FIRST_PRINTABLE or char == _DELETE
            else char
        )
        for char in text
    )
    return f'"{escaped}"'


def _keys(
    where: str, table: Mapping[str, object], keys: frozenset[str], prefix: str
) -> None:
    """Refuse a key outside ``keys``, then a missing one named ``<prefix><key>``."""
    unknown = sorted(set(table) - keys)
    if unknown:
        raise PicksError(f"{where}: unknown key {unknown[0]!r}")
    missing = [key for key in sorted(keys) if key not in table]
    if missing:
        raise PicksError(f"{prefix}{missing[0]}: missing")


def _id_value(where: str, key: str, value: object) -> str | bool:
    """One answer key's value, checked against what that key takes."""
    if key == "unresolvable":
        if value is not True:
            raise PicksError(f"{where}: must be true")
        return True
    if key == "isbn13":
        if not isinstance(value, str) or isbn13_or_none(value) is None:
            raise PicksError(
                f"{where}: must be a string holding an ISBN-13 or ISBN-10 with a "
                "valid check digit"
            )
        return value
    pattern, shape = (
        (TMDB_ID, TMDB_ID_SHAPE) if key == "tmdb_id" else (OL_WORK, OL_WORK_SHAPE)
    )
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PicksError(f"{where}: must be {shape}")
    return value


def _answer(where: str, medium: str, value: object) -> Answer | None:
    """A pick's answer: None for keep, else its checked keys."""
    if value == _KEEP:
        return None
    if not isinstance(value, dict):
        raise PicksError(f"{where}: {_SHAPE_RULE}")
    unknown = sorted(set(value) - _ANSWER_KEYS)
    if unknown:
        raise PicksError(f"{where}: unknown key {unknown[0]!r}")
    if frozenset(value) not in _ANSWER_SHAPES:
        raise PicksError(f"{where}: {_SHAPE_RULE}")
    for key in value:
        if _KEY_MEDIUM.get(key, medium) != medium:
            raise PicksError(f"{where}.{key}: only for a {_KEY_MEDIUM[key]}")
    return {key: _id_value(f"{where}.{key}", key, value[key]) for key in value}


def _pick(where: str, value: object) -> tuple[Scope, object]:
    """A pick's (medium, title, rows) and raw answer, its shape checked."""
    if not isinstance(value, dict):
        raise PicksError(f"{where}: must be an object")
    _keys(where, value, _PICK_KEYS, f"{where}.")
    medium, title, rows = value["medium"], value["title"], value["rows"]
    if not isinstance(medium, str) or medium not in _MEDIA:
        raise PicksError(f"{where}.medium: must be book or film")
    if not isinstance(title, str):
        raise PicksError(f"{where}.title: must be a string")
    try:
        title.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate, which JSON's \u escapes can spell: TOML cannot hold it.
        raise PicksError(f"{where}.title: must be valid Unicode text") from None
    if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
        raise PicksError(f"{where}.rows: must be a list of strings")
    return (medium, title, tuple(rows)), value["answer"]


def _picks(values: Sequence[object], decidable: frozenset[Scope]) -> tuple[Pick, ...]:
    """Every pick checked, each naming a distinct decidable entry."""
    seen: dict[Scope, int] = {}
    checked = []
    for index, value in enumerate(values):
        where = f"picks[{index}]"
        scope, raw = _pick(where, value)
        if scope not in decidable:
            raise PicksError(f"{where}: names no decidable entry in the review")
        if scope in seen:
            raise PicksError(
                f"{where}: a second pick for the entry that picks[{seen[scope]}] names"
            )
        seen[scope] = index
        medium, title, rows = scope
        answer = _answer(f"{where}.answer", medium, raw)
        checked.append(Pick(medium, title, rows, answer))
    return tuple(checked)


def _document(data: bytes, review: Review) -> list[object]:
    """The picks list, after the file's shape, schema and digest are checked."""
    try:
        document = decode_json(data)
    except NotJson:
        raise PicksError("picks file: not valid UTF-8 JSON") from None
    if not isinstance(document, dict):
        raise PicksError("picks file: must be an object")
    _keys("picks file", document, _TOP_KEYS, "")
    schema = document["schema"]
    if isinstance(schema, bool) or schema != _SCHEMA:
        raise PicksError(f"schema: this version reads schema {_SCHEMA} only")
    digest = document["review_sha256"]
    if not isinstance(digest, str):
        raise PicksError("review_sha256: must be a string")
    if digest != review.sha256:
        raise PicksError(
            "review_sha256: the picks were made against another review file"
        )
    values = document["picks"]
    if not isinstance(values, list):
        raise PicksError("picks: must be a list")
    return values


# fuzz-target
def read_picks(data: bytes, review: Review) -> tuple[Pick, ...]:
    """Check picks.json's bytes against ``review`` and return its picks in order.

    Raises ``PicksError`` naming the first index and key at fault.
    """
    return _picks(_document(data, review), _decidable(review))


def _decidable(review: Review) -> frozenset[Scope]:
    """The (medium, title, rows) of every entry the page shows, which a pick names.

    read_review has refused two entries with one scope, so each names one entry.
    """
    return frozenset(scope_of(entry) for entry in review.entries)


def _override(pick: Pick, row: str, answer: Answer) -> str:
    """One line ``[[overrides]]`` table for one of ``pick``'s rows."""
    # read_review checked every row splits, and a pick's rows are its entry's.
    file, line = cast(tuple[str, int], split_row(row))
    lines = [
        "[[overrides]]",
        f"medium = {toml_string(pick.medium)}",
        f"title = {toml_string(pick.title)}",
        f"file = {toml_string(file)}",
        f"line = {line}",
    ]
    for key in _WRITE_ORDER:
        if key in answer:
            value = answer[key]
            lines.append(
                f"{key} = true"
                if value is True
                else f"{key} = {toml_string(str(value))}"
            )
    return "\n".join(lines) + "\n"


def _tables(picks: Sequence[Pick]) -> list[str]:
    """One table per row of each pick that is not keep, in pick and row order."""
    return [
        _override(pick, row, pick.answer)
        for pick in picks
        if pick.answer is not None
        for row in pick.rows
    ]


def render_toml(tables: Sequence[str]) -> str:
    """The overrides file: ``schema = 1``, then ``tables``, a blank line before each."""
    return "".join(["schema = 1\n", *(f"\n{table}" for table in tables)])


def build_overrides(data: bytes, review: Review) -> Overrides:
    """The overrides file ``data``'s picks write, checked by resolve's own loader.

    Raises ``PicksError`` for a refused picks file, and for an output resolve would
    refuse (two titles one title key apart, say).
    """
    picks = read_picks(data, review)
    tables = _tables(picks)
    toml = render_toml(tables)
    try:
        load_overrides(toml.encode("utf-8"))
    except OverridesError as error:
        raise PicksError(
            f"the overrides file it would write fails resolve's check: {error}"
        ) from None
    picked = sum(1 for pick in picks if pick.answer is not None)
    return Overrides(
        toml=toml,
        tables=tuple(tables),
        written=len(tables),
        picked=picked,
        kept=len(picks) - picked,
        undecided=len(review.entries) - len(picks),
    )
