"""Choose the films to send: the cutoff, the holds, the account diff, and the order.

``select`` takes every row export_input read and decides each one's fate, in this
order: rows at or after the operator's cutoff are cut and counted; the rest are
grouped into films; a film one of whose rows, cut or kept, the config's omit list
names is left out and listed, before it can hold anything back; a film with an
``ambiguous`` or ``failed`` row is held whole, and so is a film whose title and year
would hide a held one on the next run's diff, or, with watched dates on, whose title
and a row's date would (by the tagged-date rule), which passes the hold on in turn;
then a film with no title the account could know it by is held, and so is each film
of a title whose rows split between a tmdbID film and a title-only film, passing the
hold on the same way; then each film's fate follows from the account files that list it: one an
earlier import tagged is skipped whole, and so is one with such a tagged diary entry
on the date of one of its rows, in any year; one the owner logged (a diary entry, a
rating, or a review) is sent without stars, less any row on the date of one of its
diary entries; one only marked watched, and one the account lacks, are sent in
full. The logged, watched, and same-date checks also accept an entry of the film's
title a year either side of a row's (``export_match``). Every input row is cut,
omitted, held, skipped, dropped, or sent exactly once.

A film is matched against the account, and against the held films and a split title,
by each row's list title and each TMDb title resolve recorded for it, always with
the row's year: resolve can settle a film under a title other than the list's. Each
title counts by its ``title_key`` and by its folded and hyphen keys
(``export_titles``), so an accent, an "&" against "and", or a hyphen against a
space alone does not make two titles differ.

The account check is a parameter, ``on_service``, so this module never reads an
account export, and the cutoff is ``export_cutoff``'s. Every review entry's detail is
path-free and names no title: the entry's own ``title`` and ``rows`` fields carry
those.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from export_cutoff import apply_cutoff
from export_match import (
    ON_SERVICE,
    Match,
    account_match,
    hides_by_tagged_date,
    imported_detail,
    near_detail,
    same_date_rows,
)
from export_titles import match_keys
from export_types import (
    Account,
    Cutoff,
    FilmKey,
    OmitEntry,
    ResolvedRow,
    ReviewEntry,
)

# Every kind of review entry, the errors first.
REVIEW_KINDS = (
    "held-ambiguous",
    "held-failed",
    "held-split",
    "held-no-title",
    "omitted",
    "on-service",
    "same-date",
    "logged-no-stars",
    "watched-only",
    "override-unchecked",
    "by-title",
    "unrated",
    "undated",
    "formula-cell",
)
# The kinds that make the run's exit code 1: the rest of the films are still sent.
ERROR_KINDS = frozenset(
    {"held-ambiguous", "held-failed", "held-split", "held-no-title"}
)
# The resolve statuses that hold a film back, and the review kind each gives.
_HELD_KINDS = {"ambiguous": "held-ambiguous", "failed": "held-failed"}
_DETAILS = {
    "held-ambiguous": "resolve did not settle on one match; settle it with an override",
    "held-failed": "resolve could not look it up; run resolve again",
    "held-no-title": "its title has no letter or digit, so the account cannot be "
    "checked for it; add it by hand",
    "on-service": ON_SERVICE,
    "omitted": "named in letterboxd.omit, so not sent; remove the entry to consider it "
    "again",
    "same-date": "a diary entry on the account has this date; Letterboxd would update "
    "it rather than add one, so this row is not sent",
    "logged-no-stars": "already logged on the account, so sent without stars to keep "
    "its rating",
    "watched-only": "on the account as watched only, with no rating or diary entry, so "
    "sent in full",
    "override-unchecked": "sent by an override's tmdbID, with no TMDb title to check "
    "the account by; confirm by hand it is not already there",
    "by-title": "sent by title with no tmdbID; check it in the import preview",
    "unrated": "sent with no rating; rate it by hand",
    "undated": "sent with no watched date",
}
# The override-unchecked detail when no unchecked row is an override: resolve never
# writes such a row, but a hand-edited CSV can.
_NO_TMDB_TITLE = (
    "sent by its tmdbID, with no TMDb title to check the account by; confirm by hand "
    "it is not already there"
)
# One title's rows split between a tmdbID film and a title-only film. Each half's
# detail, keyed by whether that half has the tmdbID.
_SPLIT_DETAILS = {
    True: "other rows of this title have no tmdbID; settle them in resolve with an "
    "override, then rerun",
    False: "other rows of this title have a tmdbID; settle these in resolve with an "
    "override, then rerun",
}
# A film sent now would put a held film's title on the account, and the next run would
# then skip the held one as on-service: so it waits too, under that film's kind.
_HELD_WITH_ANOTHER = "held with another film of this title until resolve settles it"
# A held film's review kind, and its detail when not the kind's own.
Hold = tuple[str, str | None]
# A sort key: undated rows first, then date, source file, and line.
RowOrder = tuple[bool, datetime.date, str, int]


@dataclass(frozen=True)
class Film:
    """One film to send, and every one of its rows."""

    key: str  # "tmdb:<id>" or "title:<title_key>:<year or ''>"
    rows: tuple[ResolvedRow, ...]  # upload order: see row_order
    # True for a film the owner logged on the account: its rows go with no stars, so
    # the import keeps the rating the account gave it.
    withhold_rating: bool = False


@dataclass(frozen=True)
class Selection:
    """What ``select`` decided: the films to send, the review list, and the cut."""

    films: tuple[Film, ...]  # ordered by their first sent row
    # Film by film, in the grouped films' order: a film whose same-date drop moves
    # its first row later keeps its place here, though not in ``films``.
    review: tuple[ReviewEntry, ...]
    cut: int  # rows the cutoff left out


def row_order(row: ResolvedRow) -> RowOrder:
    """The upload order's sort key.

    Undated rows first, then by date, source file, and line.
    """
    undated = row.date is None
    date = datetime.date.min if row.date is None else row.date
    return (not undated, date, row.source_file, row.source_line)


def film_key(row: ResolvedRow) -> str:
    """The film a row belongs to: its tmdbID, else its title key and year."""
    if row.tmdb_id:
        return f"tmdb:{row.tmdb_id}"
    year = "" if row.year is None else str(row.year)
    return f"title:{row.title_key}:{year}"


def group_films(rows: Iterable[ResolvedRow]) -> tuple[Film, ...]:
    """The rows as films, each film's rows and the films in upload order."""
    grouped: dict[str, list[ResolvedRow]] = {}
    for row in rows:
        grouped.setdefault(film_key(row), []).append(row)
    films = [
        Film(key, tuple(sorted(members, key=row_order)))
        for key, members in grouped.items()
    ]
    return tuple(sorted(films, key=lambda film: row_order(film.rows[0])))


def review_entry(film: Film, kind: str, detail: str | None = None) -> ReviewEntry:
    """A review entry of ``kind`` covering every row of ``film``.

    ``detail`` is the entry's own, else the kind's from ``_DETAILS``.
    """
    return ReviewEntry(
        kind=kind,
        title=film.rows[0].title,
        rows=tuple(
            row.where
            for row in sorted(
                film.rows, key=lambda row: (row.source_file, row.source_line)
            )
        ),
        detail=_DETAILS[kind] if detail is None else detail,
        error=kind in ERROR_KINDS,
    )


def held_kind(film: Film) -> str | None:
    """The hold a film's first ``ambiguous`` or ``failed`` row gives it, if any."""
    for row in film.rows:
        if row.status in _HELD_KINDS:
            return _HELD_KINDS[row.status]
    return None


def _unchecked(row: ResolvedRow) -> bool:
    """Whether ``row`` is sent by a tmdbID with no TMDb title to match an account by."""
    return bool(row.tmdb_id) and not row.tmdb_title_keys


def _unchecked_detail(film: Film) -> str:
    """The ``override-unchecked`` detail: an override's own, else a plain row's."""
    overridden = any(_unchecked(row) and row.status == "override" for row in film.rows)
    return _DETAILS["override-unchecked"] if overridden else _NO_TMDB_TITLE


def sent_entries(film: Film) -> list[ReviewEntry]:
    """The review entries a sent film earns, in ``REVIEW_KINDS`` order.

    A row sent by a tmdbID with no TMDb title to check, by title, unrated, and
    undated.
    """
    entries = []
    if any(_unchecked(row) for row in film.rows):
        entries.append(
            review_entry(film, "override-unchecked", _unchecked_detail(film))
        )
    if any(not row.tmdb_id for row in film.rows):
        entries.append(review_entry(film, "by-title"))
    if any(not row.rating_raw for row in film.rows):
        entries.append(review_entry(film, "unrated"))
    if any(row.date is None for row in film.rows):
        entries.append(review_entry(film, "undated"))
    return entries


def _title_keys(row: ResolvedRow) -> tuple[str, ...]:
    """Every key ``row``'s titles give: its list title's and its TMDb titles'.

    The list title counts by its ``title_key`` and its match keys; the TMDb title
    keys already hold both. An empty key is left out: it names no title, so it would
    match every other empty one, and the account never holds it.
    """
    keys = (row.title_key, *match_keys(row.title), *row.tmdb_title_keys)
    return tuple(dict.fromkeys(key for key in keys if key))


def _row_keys(film: Film) -> list[FilmKey]:
    """Every ``FilmKey`` an account could know a film's rows by, each once.

    Each row's ``_title_keys``, with the row's year.
    """
    keys = (FilmKey(key, row.year) for row in film.rows for key in _title_keys(row))
    return list(dict.fromkeys(keys))


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


# A film's fate on the account: the film to send, if any, and its review entries.
Fate = tuple[Film | None, list[ReviewEntry]]


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


def _split_titles(films: Iterable[Film]) -> frozenset[str]:
    """The title keys some tmdbID film and some title-only film both carry.

    A row's every ``_title_keys`` counts, its TMDb titles' among them: a title-only
    film named as a resolved film's TMDb title is the same split.
    """
    by_id: set[str] = set()
    by_title: set[str] = set()
    for film in films:
        side = by_id if film.rows[0].tmdb_id else by_title
        side.update(key for row in film.rows for key in _title_keys(row))
    return frozenset(by_id & by_title)


def _title_holds(films: Sequence[Film], holds: Mapping[str, Hold]) -> dict[str, Hold]:
    """The holds a film's own titles give it, for each film ``holds`` does not hold.

    A film with no key to match the account by is held as ``held-no-title``: it
    could never be found on the account, so it would be sent on every run. A film of
    a title split between a tmdbID film and a title-only film is ``held-split``.
    """
    split = _split_titles(films)
    found: dict[str, Hold] = {}
    for film in films:
        if film.key in holds:
            continue
        if not _row_keys(film):
            found[film.key] = ("held-no-title", None)
        elif any(key in split for row in film.rows for key in _title_keys(row)):
            found[film.key] = ("held-split", _SPLIT_DETAILS[bool(film.rows[0].tmdb_id)])
    return found


def held_films(
    films: Sequence[Film],
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool],
    *,
    watched_dates: bool,
) -> dict[str, Hold]:
    """Every held film's key, with its hold's kind and detail (``None``: the kind's own).

    Seeded with the films an ``ambiguous`` or ``failed`` row holds and grown to
    a fixed point (``_grow_holds``, by date too when ``watched_dates``); then the
    films still free that ``_title_holds`` holds are added, and the holds grown
    again. A film an ambiguous or failed one already holds keeps that kind, so the
    operator settles the lookup first.
    """
    holds: dict[str, Hold] = {
        film.key: (hold, None)
        for film in films
        if (hold := held_kind(film)) is not None
    }
    _grow_holds(films, holds, on_service, watched_dates)
    holds |= _title_holds(films, holds)
    _grow_holds(films, holds, on_service, watched_dates)
    return holds


# fuzz-target
def select(
    rows: Sequence[ResolvedRow],
    *,
    cutoff: Cutoff | None,
    account: Account,
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool],
    watched_dates: bool,
    omit: Sequence[OmitEntry] = (),
) -> Selection:
    """Cut, omit, hold, skip, drop, and order ``rows``, as the module docstring says.

    ``on_service`` answers whether a film, as ``FilmKey(title_key, year)`` of one of
    its rows, is in one of ``account``'s sets of films. ``watched_dates`` is whether
    rows go with their WatchedDate, so a row can meet a diary entry's date. ``omit``
    names the films left out (``OmitEntry.names``): one row named leaves out its whole
    film, since a film is sent whole or not at all. Raises
    ``CutoffError`` before deciding anything when the cutoff names no dated row with
    its title.
    """
    kept, cut = apply_cutoff(rows, cutoff)
    grouped = group_films(kept)
    # Named from every row read, the cut ones included, as check_omit accepts an
    # entry: an entry whose only named row was cut still leaves out its film's kept
    # rows, so moving the cutoff never turns an entry into one that omits nothing.
    left_out = {
        film_key(row) for row in rows if any(entry.names(row) for entry in omit)
    }
    # An omitted film is never sent, so it can hide no held film, nor split a title.
    free = [film for film in grouped if film.key not in left_out]
    holds = held_films(free, on_service, watched_dates=watched_dates)
    check = _AccountCheck(account, on_service, watched_dates)
    films: list[Film] = []
    review: list[ReviewEntry] = []
    for film in grouped:
        if film.key in left_out:
            review.append(review_entry(film, "omitted"))
            continue
        if film.key in holds:
            review.append(review_entry(film, *holds[film.key]))
            continue
        sent, entries = _account_fate(film, check)
        review += entries
        if sent is not None:
            films.append(sent)
    # A dropped same-date row can move a film's first row later, so order again.
    ordered = sorted(films, key=lambda film: row_order(film.rows[0]))
    return Selection(tuple(ordered), tuple(review), cut)
