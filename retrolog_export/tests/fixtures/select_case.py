"""The shared rows and helpers of the export_select example tests.

The importing test puts the tool's, resolve's, parse's and the survey's src/ folders
and this folder on ``sys.path`` before importing this module. Every title is invented
or a public-domain film, and every id is made up.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Sequence
from typing import TypedDict, Unpack

import export_select
from export_types import Cutoff, FilmKey, ResolvedRow

BASE = ResolvedRow(
    source_csv="film-2010.csv",
    title="Placeholder Film One",
    title_key="placeholder film one",
    year=1999,
    date=datetime.date(2010, 3, 4),
    rating_raw="2",
    comment="",
    rewatch=False,
    creator="Placeholder Director",
    status="resolved",
    tmdb_id="1000001",
    source_file="10/seen.txt",
    source_line=1,
)


class RowChanges(TypedDict, total=False):
    """The ResolvedRow fields a test changes, typed as the dataclass has them."""

    title: str
    title_key: str
    year: int | None
    date: datetime.date | None
    rating_raw: str
    rewatch: bool
    status: str
    tmdb_id: str
    tmdb_title_keys: tuple[str, ...]
    source_file: str
    source_line: int


class FilmIdentity(TypedDict):
    """A film's identity alone, so spreading it into ``make_row`` sets nothing else."""

    title: str
    title_key: str
    year: int | None
    tmdb_id: str


METROPOLIS: FilmIdentity = {
    "title": "Metropolis",
    "title_key": "metropolis",
    "year": 1927,
    "tmdb_id": "1000002",
}


def make_row(**changes: Unpack[RowChanges]) -> ResolvedRow:
    """``BASE`` with ``changes`` applied.

    A ``resolved`` row with a tmdbID whose changes name no ``tmdb_title_keys`` gets
    its own title key as its one TMDb title key, as resolve writes a film its
    exact-title rule settled; a test wanting none passes ``tmdb_title_keys=()``.
    """
    row = dataclasses.replace(BASE, **changes)
    if "tmdb_title_keys" in changes or row.status != "resolved" or not row.tmdb_id:
        return row
    keys = (row.title_key,) if row.title_key else ()
    return dataclasses.replace(row, tmdb_title_keys=keys)


def day(day: int) -> datetime.date:
    """A date in March 2010."""
    return datetime.date(2010, 3, day)


def never(_key: FilmKey, _account: frozenset[FilmKey]) -> bool:
    """An ``on_service`` that finds nothing on the account."""
    return False


def in_account(key: FilmKey, account: frozenset[FilmKey]) -> bool:
    """An ``on_service`` matching on exact key, or on title alone for a yearless entry."""
    return key in account or FilmKey(key.title_key, None) in account


def select(
    rows: Sequence[ResolvedRow],
    *,
    cutoff: Cutoff | None = None,
    account: frozenset[FilmKey] = frozenset(),
) -> export_select.Selection:
    """``select`` with an on_service matching the account as ``in_account`` does."""
    return export_select.select(
        rows, cutoff=cutoff, account=account, on_service=in_account
    )


def sent(chosen: export_select.Selection) -> list[str]:
    """Every sent row's position, film by film."""
    return [row.where for film in chosen.films for row in film.rows]


def kinds(chosen: export_select.Selection) -> list[tuple[str, tuple[str, ...]]]:
    """Each review entry's kind and rows, in order."""
    return [(entry.kind, entry.rows) for entry in chosen.review]
