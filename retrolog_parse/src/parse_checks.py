"""Shape checks for the rules file's TOML values, each refusing by the key's name.

Every helper takes ``where``, the dotted key a value came from (``ratings.colors``,
``terms[0].lines``), and raises ``RulesError`` naming it when the value has the wrong
type or range. No message quotes the value itself: a rules file holds the operator's
own list text and paths, and a refusal must be safe to print as is.
"""

from __future__ import annotations

import datetime
import re
import sys
import tomllib
import unicodedata
from pathlib import PurePosixPath
from typing import cast

# A [first, last] span, and a split code or rating, each name exactly two parts.
PAIR = 2
# No upper limit on a line number: the largest index a sequence can have.
NO_LIMIT = sys.maxsize
# What re.compile raises for a pattern it cannot build: a syntax error, a repeat count
# too large for the engine, or nesting deeper than the parser's recursion allows.
_PATTERN_ERRORS = (re.error, OverflowError, RecursionError)
# The path parts a relative source-file path may not hold.
_BAD_PARTS = frozenset({"", ".", ".."})


class RulesError(Exception):
    """The rules file is unusable; the message names the TOML key, never a path."""


def table(where: str, value: object) -> dict[str, object]:
    """``value`` as a TOML table, or a RulesError naming ``where``."""
    if not isinstance(value, dict):
        raise RulesError(f"{where}: must be a table")
    return cast(dict[str, object], value)


def string(where: str, value: object) -> str:
    """``value`` as a non-empty string."""
    if not isinstance(value, str) or not value:
        raise RulesError(f"{where}: must be a non-empty string")
    return value


def integer(where: str, value: object, low: int, high: int) -> int:
    """``value`` as an integer from ``low`` to ``high`` (a TOML boolean is not one)."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise RulesError(f"{where}: must be an integer from {low} to {high}")
    return value


def strings(where: str, value: object, *, allow_empty: bool = True) -> tuple[str, ...]:
    """``value`` as a list of non-empty strings."""
    if not isinstance(value, list) or not (allow_empty or value):
        raise RulesError(f"{where}: must be a list of strings")
    return tuple(string(where, item) for item in value)


def pattern(where: str, source: str, groups: int | None = None) -> re.Pattern[str]:
    """Compile ``source`` case-insensitively; ``groups`` is the exact count required."""
    try:
        compiled = re.compile(source, re.IGNORECASE)
    except _PATTERN_ERRORS:
        raise RulesError(f"{where}: not a valid pattern") from None
    if groups is not None and compiled.groups != groups:
        raise RulesError(f"{where}: must have exactly {groups} group(s)")
    return compiled


def patterns(
    where: str, value: object, groups: int | None = None
) -> tuple[re.Pattern[str], ...]:
    """A list of patterns, each compiled by ``pattern``."""
    return tuple(pattern(where, text, groups) for text in strings(where, value))


def relative_file(where: str, value: object) -> str:
    """A relative POSIX path with no empty, ``.`` or ``..`` part and no backslash.

    Returned in Unicode NFC, the form the walk gives every source path, so a name
    the rules file spells decomposed (NFD) still names the file it means.
    """
    text = unicodedata.normalize("NFC", string(where, value))
    if (
        "\\" in text
        or PurePosixPath(text).is_absolute()
        or any(part in _BAD_PARTS for part in text.split("/"))
    ):
        raise RulesError(f"{where}: must be a relative path with / and no . or ..")
    return text


def line_span(where: str, value: object) -> tuple[int, int]:
    """``lines = [first, last]`` with ``1 <= first <= last``."""
    if not isinstance(value, list) or len(value) != PAIR:
        raise RulesError(f"{where}: must be [first, last]")
    first = integer(where, value[0], 1, NO_LIMIT)
    last = integer(where, value[1], first, NO_LIMIT)
    return first, last


def date(where: str, value: object) -> datetime.date:
    """A TOML local date; a date-time (a subclass of date) is refused."""
    if type(value) is not datetime.date:
        raise RulesError(f"{where}: must be a date such as 2001-12-31")
    return value


def unique(where: str, keys: list[str]) -> None:
    """Refuse ``keys`` (already normalized) when one appears twice.

    Two keys the file spells differently, such as ``aabbcc`` and ``AABBCC``, can mean
    the same thing once normalized; keeping the last would drop the first silently.
    """
    if len(set(keys)) != len(keys):
        raise RulesError(f"{where}: the same key appears twice, differing only in case")


def decode(data: bytes) -> dict[str, object]:
    """Parse ``data`` as UTF-8 TOML; no error's text (it can quote input) escapes.

    tomllib parses nested arrays and inline tables recursively, so a deep enough
    nesting raises RecursionError rather than TOMLDecodeError; and an integer past
    int()'s 4,300-digit limit raises a bare ValueError, of which TOMLDecodeError is a
    subclass. Both are refused the same.
    """
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RulesError("rules file: not valid UTF-8 TOML") from None
