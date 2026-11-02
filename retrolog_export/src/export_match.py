"""How a film meets the account: exactly, by a near year, or by an import's tagged date.

The account export carries no TMDb id, so a film is matched there by title and year,
and Letterboxd dates a film by its first release while TMDb can give a later year.
``export_select`` asks this module, of a film's row keys:

- ``imported_detail``: whether an earlier import sent it, and so it is skipped whole.
  An exact match against the tagged rows counts, and so, when rows go with their
  WatchedDate, does a tagged diary entry of one of its titles on one of its rows'
  dates (``imported_date``), whatever year either side gives it.
- ``account_match``: whether its keys meet one of the account's sets exactly or only
  with a year one either side of a row's (``near_year_keys``). The logged, the
  watched-only, and the same-date checks (``same_date_rows``) accept the near year;
  the imported check and the held films do not.
- ``hides_by_tagged_date``: whether sending one film would make the next run's
  tagged-date rule skip a held one, so the first waits with it.

Each answer comes with its review entry's wording; like every detail it is path-free
and names no title.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal

from export_types import IMPORT_TAG_PREFIX, Account, FilmKey, ResolvedRow

# How far either side of a row's year an account entry may be and still be the film.
# One year catches the common gap between Letterboxd's first-release year and TMDb's,
# while a wider window would let a same-titled film from another year match. A wider
# gap is left to the tagged-date rule, which needs no year at all.
NEAR_YEARS = 1
# How a film's keys met an account set: by an exact key, or only by a near year.
Match = Literal["exact", "near-year"]
# Is a film, as one of its keys, in a set of the account's films (export_account's).
OnService = Callable[[FilmKey, frozenset[FilmKey]], bool]
# The on-service detail of an exact match, and the start of every other one.
ON_SERVICE = "an earlier import sent it, so not sent again"
# An account row with no year matches its title in any year, and a row with no
# year matches any account row of its title; the entry says which.
_ON_SERVICE_BY_TITLE = (
    f"{ON_SERVICE}; the account entry has no year, so matched by title alone"
)
_ON_SERVICE_NO_YEAR = f"{ON_SERVICE}; the row has no year, so matched by title alone"
# The detail of each account kind whose match was a near year.
NEAR_YEAR_DETAILS = {
    "same-date": "a diary entry on the account has this date, under a year one apart; "
    "Letterboxd would update it rather than add one, so this row is not sent",
    "logged-no-stars": "already logged on the account under a year one apart, so "
    "sent without stars to keep its rating",
    "watched-only": "on the account as watched only, under a year one apart, with no "
    "rating or diary entry, so sent in full",
}


def near_year_keys(key: FilmKey) -> tuple[FilmKey, ...]:
    """``key``'s title in each year ``NEAR_YEARS`` either side; none with no year."""
    if key.year is None:
        return ()
    return tuple(
        FilmKey(key.title_key, key.year + offset)
        for offset in range(-NEAR_YEARS, NEAR_YEARS + 1)
        if offset
    )


def account_match(
    keys: Sequence[FilmKey], account: frozenset[FilmKey], on_service: OnService
) -> Match | None:
    """How ``keys``, a film's row keys, meet ``account``: exactly or by a near year.

    None when they meet it neither way. An exact match is looked for across every key
    before a near one, so a film both ways is ``exact`` and keeps its kind's own detail.
    """
    if any(on_service(key, account) for key in keys):
        return "exact"
    near = (near for key in keys for near in near_year_keys(key))
    if any(on_service(key, account) for key in near):
        return "near-year"
    return None


def near_detail(kind: str, match: Match | None) -> str | None:
    """``kind``'s near-year detail when ``match`` was a near year; else None.

    None leaves the review entry its kind's own detail.
    """
    return NEAR_YEAR_DETAILS[kind] if match == "near-year" else None


def same_date_rows(
    rows: Iterable[ResolvedRow],
    keys: Sequence[FilmKey],
    diary_dates: Mapping[datetime.date, frozenset[FilmKey]],
    on_service: OnService,
) -> tuple[tuple[ResolvedRow, ...], Match | None]:
    """The ``rows`` on the date of a diary entry matching ``keys``, and how it matched.

    ``keys`` are the film's row keys and ``diary_dates`` is ``Account.diary_dates``.
    An entry a year either side of a row's matches too, as for the logged check; the
    match given is ``near-year`` when any row met its entry only that way.
    """
    found = [
        (row, match)
        for row in rows
        if row.date is not None
        and (entries := diary_dates.get(row.date))
        and (match := account_match(keys, entries, on_service)) is not None
    ]
    near = any(match == "near-year" for _, match in found)
    weakest: Match | None = "near-year" if near else None
    return tuple(row for row, _ in found), weakest


def imported_date(
    rows: Iterable[ResolvedRow],
    titles: Iterable[str],
    imported_dates: Mapping[datetime.date, frozenset[FilmKey]],
) -> datetime.date | None:
    """The first of ``rows``' dates holding a tagged diary entry of one of ``titles``.

    ``titles`` are the film's title keys, every row's; ``imported_dates`` is
    ``Account.imported_dates``. Either year is ignored: an entry this tool's import
    wrote on the very date of one of the film's rows is that film.
    """
    wanted = frozenset(titles)
    for row in rows:
        entries = imported_dates.get(row.date) if row.date is not None else None
        if entries and any(entry.title_key in wanted for entry in entries):
            return row.date
    return None


def hides_by_tagged_date(
    sent: tuple[Iterable[ResolvedRow], Iterable[FilmKey]],
    held: tuple[Iterable[ResolvedRow], Iterable[FilmKey]],
) -> bool:
    """Whether sending one film would make the next run skip another by tagged date.

    Each side is a film's rows and row keys. Sent with their WatchedDate, the sent
    film's rows leave a tagged diary entry of its titles on each of their dates, and
    the next run's ``imported_date`` then skips the held film when one of its titles
    sits on one of those dates, in any year.
    """
    rows, keys = sent
    entries = frozenset(keys)
    tagged = {row.date: entries for row in rows if row.date is not None}
    held_rows, held_keys = held
    titles = (key.title_key for key in held_keys)
    return imported_date(held_rows, titles, tagged) is not None


def tagged_detail(date: datetime.date) -> str:
    """The ``on-service`` detail for a film found by its tagged diary entry's date."""
    return (
        f"{ON_SERVICE}; recognized by its {IMPORT_TAG_PREFIX} diary entry on "
        f"{date.isoformat()}"
    )


def _exact_detail(key: FilmKey, account: frozenset[FilmKey]) -> str:
    """Say whether the account matched this film by its year or by its title alone.

    ``key`` is the film's key ``on_service`` matched. Not in the account as it is, it
    met either an account entry with no year or, having no year itself, an entry of
    its title in some year.
    """
    if key in account:
        return ON_SERVICE
    if FilmKey(key.title_key, None) in account:
        return _ON_SERVICE_BY_TITLE
    return _ON_SERVICE_NO_YEAR


def imported_detail(
    rows: Iterable[ResolvedRow],
    keys: Sequence[FilmKey],
    account: Account,
    on_service: OnService,
    *,
    watched_dates: bool,
) -> str | None:
    """The ``on-service`` detail of a film an earlier import sent, else None.

    ``keys`` are the film's row keys, every row asked, since rows sharing a tmdbID
    can carry different titles. Matched exactly against the tagged rows first,
    keeping that detail; then, only when ``watched_dates`` (rows go with their
    WatchedDate), by ``imported_date``. With dates off, an imported entry's date is
    the import's day, so it says nothing of the row's. No year either side is
    accepted here: the tagged date covers the year drift without a guess.
    """
    imported = account.imported
    if (key := next((k for k in keys if on_service(k, imported)), None)) is not None:
        return _exact_detail(key, imported)
    if not watched_dates:
        return None
    titles = (key.title_key for key in keys)
    date = imported_date(rows, titles, account.imported_dates)
    return None if date is None else tagged_detail(date)
