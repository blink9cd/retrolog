"""The verdict cache: which ids an earlier run settled, so a rerun asks only the rest.

One JSON object, ``{"schema": 1, "verdicts": {"<id>": "present" | "missing"}}``, and
nothing else: no title, no row, no date. Only a settled verdict is ever stored; an
``unknown`` one is asked about again next run. The file is read strictly: anything
but that exact layout is refused rather than discarded, because discarding it would
throw away the operator's progress and send every id again. It is written whole,
atomically, so an interrupted run leaves the last complete cache behind.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from probe_ids import is_tmdb_id
from survey_write import write_atomic_bytes

Settled = Literal["present", "missing"]
SCHEMA = 1
_SETTLED = frozenset({"present", "missing"})
_KEYS = frozenset({"schema", "verdicts"})
_NOT_A_CACHE = "--cache is not a verdict cache"


class CacheError(Exception):
    """The cache cannot be used; the message names no path."""


def _is_settled(tmdb_id: object, verdict: object) -> bool:
    """Whether one entry is an id the rule accepts and a settled verdict."""
    return (
        isinstance(tmdb_id, str)
        and is_tmdb_id(tmdb_id)
        and isinstance(verdict, str)
        and verdict in _SETTLED
    )


# fuzz-target
def parse_cache(data: bytes) -> dict[str, Settled]:
    """The verdicts ``data`` holds; ``CacheError`` for anything but the one layout."""
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise CacheError(_NOT_A_CACHE) from None
    if not isinstance(document, dict) or document.keys() != _KEYS:
        raise CacheError(_NOT_A_CACHE)
    schema, verdicts = document["schema"], document["verdicts"]
    # type(), not isinstance: True is an int to isinstance, and is not schema 1.
    if type(schema) is not int or schema != SCHEMA or not isinstance(verdicts, dict):
        raise CacheError(_NOT_A_CACHE)
    if not all(_is_settled(key, value) for key, value in verdicts.items()):
        raise CacheError(_NOT_A_CACHE)
    return dict(verdicts)


def cache_bytes(verdicts: Mapping[str, Settled]) -> bytes:
    """``verdicts`` in the cache's layout, ids in a stable order."""
    document = {"schema": SCHEMA, "verdicts": dict(sorted(verdicts.items()))}
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def load_cache(path: Path) -> dict[str, Settled]:
    """The verdicts at ``path``; an empty cache when no file is there yet."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise CacheError(f"could not read --cache: {type(error).__name__}") from None
    return parse_cache(data)


def save_cache(path: Path, verdicts: Mapping[str, Settled]) -> None:
    """Write ``verdicts`` to ``path`` atomically; ``OSError`` when it cannot."""
    write_atomic_bytes(path, cache_bytes(verdicts))
