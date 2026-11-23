"""The shared rows and helpers of the export_select example tests.

The importing test puts the tool's, resolve's, parse's, and the survey's src/ folders
and this folder on ``sys.path`` before importing this module. Every title is invented
or a public-domain film, and every id is made up.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Sequence
from types import MappingProxyType
from typing import TypedDict, Unpack

import export_select
from export_letterboxd_select import LetterboxdRules
from export_match import OnService
from export_services import LETTERBOXD
from export_types import (
    Account,
    Cutoff,
    FilmKey,
    LetterboxdConfig,
    OmitEntry,
    ResolvedRow,
)

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


def imported(keys: frozenset[FilmKey]) -> Account:
    """An account whose diary holds ``keys``, each tagged by an earlier import."""
    return Account(logged=keys, imported=keys)


def letterboxd_select(
    rows: Sequence[ResolvedRow],
    *,
    cutoff: Cutoff | None,
    account: Account,
    on_service: OnService,
    watched_dates: bool,
    omit: Sequence[OmitEntry] = (),
) -> export_select.Selection:
    """``select`` as Letterboxd's spec runs it, its films matched by ``on_service``.

    The config is a bare ``[letterboxd]`` table whose ``watched_date`` is
    ``watched_dates``: the only key of it selection reads besides the cutoff and omit
    list, which are passed as the CLI passes them.
    """
    rules = LetterboxdRules(on_service)
    spec = dataclasses.replace(
        LETTERBOXD,
        account_fate=rules.account_fate,
        grow_holds=rules.grow_holds,
    )
    config = LetterboxdConfig(
        half_stars=MappingProxyType({}),
        rating_tags=MappingProxyType({}),
        tags=("retrolog",),
        watched_date=watched_dates,
        review_note="",
        review_note_rows="commented",
        cutoff=cutoff,
        omit=tuple(omit),
    )
    return export_select.select(
        rows, spec=spec, config=config, account=account, cutoff=cutoff, omit=omit
    )


def select(
    rows: Sequence[ResolvedRow],
    *,
    cutoff: Cutoff | None = None,
    account: frozenset[FilmKey] | Account = frozenset(),
    watched_dates: bool = True,
) -> export_select.Selection:
    """``select`` with an on_service matching the account as ``in_account`` does.

    A bare set of keys is an account an earlier import made (``imported``), so its
    films are skipped whole, as every film on the account once was.
    """
    whole = account if isinstance(account, Account) else imported(account)
    return letterboxd_select(
        rows,
        cutoff=cutoff,
        account=whole,
        on_service=in_account,
        watched_dates=watched_dates,
    )


def sent(chosen: export_select.Selection) -> list[str]:
    """Every sent row's position, film by film."""
    return [row.where for film in chosen.films for row in film.rows]


def kinds(chosen: export_select.Selection) -> list[tuple[str, tuple[str, ...]]]:
    """Each review entry's kind and rows, in order."""
    return [(entry.kind, entry.rows) for entry in chosen.review]
