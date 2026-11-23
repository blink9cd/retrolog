"""Letterboxd's account classes, and the holds its account match grows.

``export_select`` runs the steps every service shares (the cutoff, the omit list,
the holds a resolve status gives, and the order) and asks a ``ServiceSpec`` for the
rest. Letterboxd's spec answers with ``LetterboxdRules``:
a film's fate on the account follows from the account files that list it (one an
earlier import tagged is skipped whole, and so is one with such a tagged diary entry
on the date of one of its rows, in any year; one the owner logged, by a diary entry,
a rating, or a review, is sent without stars, less any row on the date of one of its
diary entries; one only marked watched, and one the account lacks, are sent in
full), and a held film's hold passes to every film whose title and year, or with
watched dates on whose title and a row's date, would hide it on the next run's diff.
The logged, watched, and same-date checks also accept an entry of the film's title a
year either side of a row's (``export_match``).

The film match is ``LetterboxdRules.on_service``, so this module never reads an
account export: ``export_account.on_service`` in the service table, a stand-in in the
tests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from export_match import (
    Match,
    OnService,
    account_match,
    hides_by_tagged_date,
    imported_detail,
    near_detail,
    same_date_rows,
)

# row_keys under a private name: this module uses it only internally.
from export_select import Fate, Film, Hold, review_entry, sent_entries
from export_select import row_keys as _row_keys
from export_types import Account, FilmKey, LetterboxdConfig, ResolvedRow, ReviewEntry

# A film sent now would put a held film's title on the account, and the next run would
# then skip the held one as on-service: so it waits too, under that film's kind.
_HELD_WITH_ANOTHER = "held with another film of this title until resolve settles it"


def _held_keys(
    films: Iterable[Film], holds: Mapping[str, Hold]
) -> list[tuple[FilmKey, str]]:
    """Every held film's row keys, each with its film's hold kind, in film order."""
    return [
        (key, holds[film.key][0])
        for film in films
        if film.key in holds
        for key in _row_keys(film)
    ]


def _shared_hold(
    film: Film,
    held: Sequence[tuple[FilmKey, str]],
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool],
) -> str | None:
    """The hold of the first held film that sending ``film`` would hide, if any.

    Once ``film`` is sent, the next run's diff finds a held film of the same title on
    the account whenever ``on_service(held key, {this film's key})`` is true, and
    skips it for good. So ``film`` waits with it, by that same test.
    """
    for key in _row_keys(film):
        for held_key, hold in held:
            if on_service(held_key, frozenset({key})):
                return hold
    return None


def _date_hold(film: Film, held: Iterable[tuple[Film, str]]) -> str | None:
    """The hold of the first held film ``film``'s tagged diary entries would hide.

    Asked only with dates on: ``export_match.hides_by_tagged_date``.
    """
    sent = (film.rows, _row_keys(film))
    for other, hold in held:
        if hides_by_tagged_date(sent, (other.rows, _row_keys(other))):
            return hold
    return None


@dataclass(frozen=True)
class _AccountCheck:
    """What decides a film's account class: the account, its match, the dates switch."""

    account: Account
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool]
    watched_dates: bool  # whether rows go with a WatchedDate, which a diary entry meets


def _logged_fate(film: Film, check: _AccountCheck, match: Match) -> Fate:
    """A film the owner logged: its rows without stars, less the same-date rows.

    ``match`` is how the film met the logged files, for the entry's detail. A row
    goes with no WatchedDate when ``watched_dates`` is off, and then meets no entry.
    """
    dropped: tuple[ResolvedRow, ...] = ()
    dropped_match: Match | None = None
    if check.watched_dates:
        dropped, dropped_match = same_date_rows(
            film.rows, _row_keys(film), check.account.diary_dates, check.on_service
        )
    review: list[ReviewEntry] = []
    if dropped:
        detail = near_detail("same-date", dropped_match)
        review.append(review_entry(Film(film.key, dropped), "same-date", detail))
    kept = tuple(row for row in film.rows if row not in dropped)
    if not kept:
        return None, review
    sent = Film(film.key, kept, withhold_rating=True)
    detail = near_detail("logged-no-stars", match)
    return sent, [
        *review,
        review_entry(sent, "logged-no-stars", detail),
        *sent_entries(sent),
    ]


def _account_fate(film: Film, check: _AccountCheck) -> Fate:
    """What the account files that list ``film`` make of it.

    Asked in this order, imported first: a film an earlier import sent is skipped
    whole (``export_match.imported_detail``), one the owner logged is
    ``_logged_fate``'s, one only watched is sent in full and listed, and one the
    account lacks is sent as it is. The logged and watched checks accept a year one
    either side of a row's: a logged film missed by a year would go with stars and
    overwrite the owner's own rating, while a false match only sends it starless, so
    the looser match is the safe one. A logged match, even a near one, beats a
    watched one for the same reason.
    """
    account, on_service, keys = check.account, check.on_service, _row_keys(film)
    detail = imported_detail(
        film.rows, keys, account, on_service, watched_dates=check.watched_dates
    )
    if detail is not None:
        return None, [review_entry(film, "on-service", detail)]
    if (match := account_match(keys, account.logged, on_service)) is not None:
        return _logged_fate(film, check, match)
    if (match := account_match(keys, account.watched, on_service)) is not None:
        detail = near_detail("watched-only", match)
        return film, [review_entry(film, "watched-only", detail), *sent_entries(film)]
    return film, sent_entries(film)


def _grow_holds(
    films: Sequence[Film],
    holds: dict[str, Hold],
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool],
    watched_dates: bool,
) -> None:
    """Grow ``holds`` in place to a fixed point.

    A film that sending would hide a held one is held too, under that film's kind,
    and passes the hold on in turn, since its own rows can hide another film's in the
    same way. With ``watched_dates`` on, a film whose tagged entries would hide one
    (``_date_hold``) is held the same way. Stops when a pass holds nothing new.
    """
    grew = True
    while grew:
        held = _held_keys(films, holds)
        dated = [
            (f, holds[f.key][0]) for f in films if watched_dates and f.key in holds
        ]
        grew = False
        for film in films:
            if film.key in holds:
                continue
            shared = _shared_hold(film, held, on_service) or _date_hold(film, dated)
            if shared is not None:
                holds[film.key] = (shared, _HELD_WITH_ANOTHER)
                grew = True


@dataclass(frozen=True)
class LetterboxdRules:
    """Letterboxd's ``ServiceSpec.account_fate`` and ``grow_holds``, by one film match.

    ``on_service`` answers whether a film, as ``FilmKey(title_key, year)`` of one of
    its rows, is in one of the account's sets of films. The config's
    ``watched_date`` is whether rows go with their WatchedDate, so a row can meet a
    diary entry's date.
    """

    on_service: OnService

    def account_fate(
        self, film: Film, account: Account, config: LetterboxdConfig
    ) -> Fate:
        """What the account files that list ``film`` make of it: ``_account_fate``."""
        check = _AccountCheck(account, self.on_service, config.watched_date)
        return _account_fate(film, check)

    def grow_holds(
        self, films: Sequence[Film], holds: dict[str, Hold], config: LetterboxdConfig
    ) -> None:
        """Grow ``holds`` in place to a fixed point: ``_grow_holds``."""
        _grow_holds(films, holds, self.on_service, config.watched_date)
