"""The ``[goodreads]`` table's own keys and the checks no Letterboxd key shares.

``export_config.load_config`` reads the table: its ratings, rating words, note
templates, cutoff, and omit list go through the same helpers as ``[letterboxd]``'s, and
this module adds what is Goodreads' alone: the shelves, the tag shelf each account
shelf adds to a book removed first, and the undated rows' Date Added.
Each check raises ``parse_checks.RulesError`` naming the key and never the value, which
``load_config`` turns into its ``ConfigError``; so this module needs nothing of
``export_config`` and that module may import it.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping
from types import MappingProxyType

import parse_checks as checks
from export_types import IMPORT_TAG_PREFIX
from parse_checks import RulesError

GOODREADS_REQUIRED = frozenset({"ratings", "shelves"})
GOODREADS_KEYS = GOODREADS_REQUIRED | {
    "undated_date_added",
    "private_note",
    "private_note_rating",
    "rating_names",
    "private_note_earlier",
    "cutoff",
    "omit",
    "shelf_tags",
}
# Goodreads' own rule for a shelf name: lowercase ASCII letters, digits, hyphens, and
# underscores, so no comma (Bookshelves is a comma list), space, or capital, which the
# site would rewrite and a rerun would then not find.
_MAX_SHELF_LENGTH = 50
SHELF_NAME = re.compile(rf"[a-z0-9_-]{{1,{_MAX_SHELF_LENGTH}}}", re.ASCII)
_SHELF_MESSAGE = (
    f"a shelf must be 1 to {_MAX_SHELF_LENGTH} characters, each a lowercase letter, "
    "a digit, a hyphen, or an underscore"
)
# The prefix every Letterboxd tag carries too: the shelf finds or undoes the import,
# so it can never collide with a shelf the account already uses.
_PREFIX_MESSAGE = f"a shelf must start with {IMPORT_TAG_PREFIX}"
_NO_SHELVES_MESSAGE = "must hold at least one shelf"
_TAGS_TABLE_MESSAGE = "must be a table of strings"


# fuzz-target
def shelves(where: str, value: object) -> tuple[str, ...]:
    """The ``shelves`` list: at least one shelf, each obeying the shelf rule, each once.

    A refusal names the shelf by its place in the list, from 1, never by its text.
    """
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RulesError(f"{where}: must be a list of strings")
    if not value:
        raise RulesError(f"{where}: {_NO_SHELVES_MESSAGE}")
    for number, shelf in enumerate(value, start=1):
        if not SHELF_NAME.fullmatch(shelf):
            raise RulesError(f"{where}[{number}]: {_SHELF_MESSAGE}")
        if not shelf.startswith(IMPORT_TAG_PREFIX):
            raise RulesError(f"{where}[{number}]: {_PREFIX_MESSAGE}")
    # dict.fromkeys keeps first sightings in order, dropping later repeats.
    return tuple(dict.fromkeys(value))


def undated_date_added(where: str, value: object) -> datetime.date | None:
    """The optional ``undated_date_added``: a TOML local date, or None when absent."""
    return None if value is None else checks.date(where, value)


# fuzz-target
def shelf_tags(where: str, value: object) -> Mapping[str, str]:
    """The optional ``shelf_tags`` table: an account shelf mapped to its tag shelf.

    A book sent though the account has it on one of these shelves also gets that
    shelf's tag in Bookshelves. Each key and each tag obeys the shelf rule, but a tag
    needs no ``retrolog`` prefix: it keeps the account's own grouping, not the
    import's. A refusal names the entry by its place in the table, from 1, never by
    its text.
    """
    if not isinstance(value, dict) or not all(
        isinstance(v, str) for v in value.values()
    ):
        raise RulesError(f"{where}: {_TAGS_TABLE_MESSAGE}")
    for number, (shelf, tag) in enumerate(value.items(), start=1):
        if not SHELF_NAME.fullmatch(shelf):
            raise RulesError(f"{where}[{number}]: the account shelf: {_SHELF_MESSAGE}")
        if not SHELF_NAME.fullmatch(tag):
            raise RulesError(f"{where}[{number}]: the tag: {_SHELF_MESSAGE}")
    return MappingProxyType(dict(value))
