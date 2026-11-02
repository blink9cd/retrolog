"""Join a round's new override tables onto an overrides file that already exists.

``append_overrides`` keeps the existing file's bytes exactly as they are (comments,
line endings and all) and adds the new ``[[overrides]]`` tables after them, with no
second ``schema = 1``. retrolog_resolve's own ``load_overrides`` checks the existing
file alone, then the joined result, so the duplicate rule is resolve's: two entries
with the same medium, title key, file, and line are refused, and a title differing
only in case or punctuation counts as the same title.

A refusal carries resolve's message, which names entries by their index in the file
(``overrides[2]``) and never quotes a title or an id.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass

from resolve_overrides import OverridesError, load_overrides


_INLINE_ARRAY = (
    "the existing file holds its overrides as an inline array (overrides = [...]), "
    "which [[overrides]] tables cannot be added to"
)


class AppendError(Exception):
    """The append is refused; the message names indexes and keys, never a value."""


@dataclass(frozen=True)
class Appended:
    """The whole file to write, and how many entries it then holds."""

    data: bytes
    held: int


def _joined(existing: bytes, tables: Sequence[str]) -> bytes:
    """``existing``, a final newline if it lacks one, then a blank line and each table."""
    if not tables:
        return existing
    ending = b"" if existing.endswith(b"\n") else b"\n"
    added = "".join(f"\n{table}" for table in tables).encode("utf-8")
    return existing + ending + added


def _refuse_inline_array(data: bytes) -> None:
    """``AppendError`` when the joined ``data`` is not TOML at all.

    The existing file passed resolve's check and the tables are ones resolve accepts,
    so the only way joining them breaks the TOML is an existing ``overrides`` that
    is an inline array: a ``[[overrides]]`` table may not extend one. Saying so
    beats resolve's bare "not valid TOML", which would blame a file it accepts.
    """
    try:
        tomllib.loads(data.decode("utf-8"))
    except tomllib.TOMLDecodeError:
        raise AppendError(_INLINE_ARRAY) from None


def append_overrides(existing: bytes, tables: Sequence[str]) -> Appended:
    """``existing`` with ``tables`` appended, both checked by resolve's loader.

    Raises ``AppendError`` when the existing file fails resolve's check, or when the
    joined file would: a table repeating an existing entry's scope, say.
    """
    try:
        load_overrides(existing)
    except OverridesError as error:
        raise AppendError(f"the existing file fails resolve's check: {error}") from None
    data = _joined(existing, tables)
    _refuse_inline_array(data)
    try:
        held = len(load_overrides(data))
    except OverridesError as error:
        raise AppendError(
            f"the appended file would fail resolve's check: {error}"
        ) from None
    return Appended(data=data, held=held)
