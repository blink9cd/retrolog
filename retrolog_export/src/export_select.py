"""Choose the items to send: the cutoff, the holds, the account diff, and the order.

``select`` takes every row export_input read and decides each one's fate, in this
order: rows at or after the operator's cutoff are cut and counted; the rest are
grouped into items (films, or books) by the service's ``item_key``; an item one of
whose rows, cut or kept, the config's omit list names is left out and listed, before
it can hold anything back; an item with an ``ambiguous`` or ``failed`` row is held
whole, and the service's ``grow_holds`` passes each hold on to the items that would
hide a held one on the next run's diff; then an item with no title the account could
know it by is held, and so is each item the service's ``split_holds`` finds split
from another item that is the same film or book (``film_splits`` here for a film,
``export_goodreads_account.book_splits`` for a book), the holds grown again the same
way; then each free item's fate on the account is the service's ``account_fate``. Every input
row is cut, omitted, held, skipped, dropped, or sent exactly once.

Letterboxd's account classes and hold growth are ``export_letterboxd_select``'s. A
film is matched against the account, and against the held films and a split title,
by each row's list title and each TMDb title resolve recorded for it, always with
the row's year: resolve can settle a film under a title other than the list's. Each
title counts by its ``title_key`` and by its folded and hyphen keys
(``export_titles``), so an accent, an "&" against "and", or a hyphen against a
space alone does not make two titles differ.

The account check is the spec's, so this module never reads an account export, and
the cutoff is ``export_cutoff``'s. Every review entry's detail is path-free and names
no title: the entry's own ``title`` and ``rows`` fields carry those.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from export_cutoff import apply_cutoff
from export_match import ON_SERVICE
from export_titles import match_keys
from export_types import (
    Cutoff,
    FilmKey,
    Medium,
    OmitEntry,
    ResolvedRow,
    ReviewEntry,
)

if TYPE_CHECKING:
    # Annotations only: export_services imports this module to build its table.
    from export_services import ServiceSpec

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
    # Book-only (BOOK_KINDS), but placed ahead of by-title: in a book run it follows
    # on-service, since the operator acts on it before the import.
    "remove-first",
    "by-title",
    # The book-only kinds: BOOK_KINDS.
    "title-only",
    "author-missing",
    "reread-merged",
    "unrated",
    "undated",
    "formula-cell",
)
# The kinds only a book earns, export_goodreads.book_entries': a film run's review
# counts leave them out, so a film run's review.json names no book kind.
BOOK_KINDS = frozenset(
    {"remove-first", "title-only", "author-missing", "reread-merged"}
)
# The kinds only a film earns: the account classes are export_letterboxd_select's,
# and override-unchecked is sent_entries'. A book's holds grow only from the kinds a
# book is held under, so none reaches it.
FILM_KINDS = frozenset(
    {"same-date", "logged-no-stars", "watched-only", "override-unchecked"}
)
# The kinds that make the run's exit code 1: the rest of the films are still sent.
ERROR_KINDS = frozenset(
    {"held-ambiguous", "held-failed", "held-split", "held-no-title"}
)
# The unrated detail, the same for a film and a book: export_goodreads reuses it.
UNRATED_DETAIL = "sent with no rating; rate it by hand"
# The resolve statuses that hold a film back, and the review kind each gives.
_HELD_KINDS = {"ambiguous": "held-ambiguous", "failed": "held-failed"}
_DETAILS = {
    "held-ambiguous": "resolve did not settle on one match; settle it with an override",
    "held-failed": "resolve could not look it up; run resolve again",
    "held-no-title": "its title has no letter or digit, so the account cannot be "
    "checked for it; add it by hand",
    "on-service": ON_SERVICE,
    "same-date": "a diary entry on the account has this date; Letterboxd would update "
    "it rather than add one, so this row is not sent",
    "logged-no-stars": "already logged on the account, so sent without stars to keep "
    "its rating",
    "watched-only": "on the account as watched only, with no rating or diary entry, so "
    "sent in full",
    "override-unchecked": "sent by an override's tmdbID, with no TMDb title to check "
    "the account by; confirm by hand it is not already there",
    "by-title": "sent by title with no tmdbID; check it in the import preview",
    "unrated": UNRATED_DETAIL,
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
# The omitted detail, naming the service's table: "letterboxd" fills it in.
_OMITTED = "named in {}.omit, so not sent; remove the entry to consider it again"
# A held film's review kind, and its detail when not the kind's own.
Hold = tuple[str, str | None]
# A sort key: undated rows first, then date, source file, and line.
RowOrder = tuple[bool, datetime.date, str, int]


@dataclass(frozen=True)
class Item:
    """One item to send, a film or a book, and every one of its rows."""

    # The service's item_key: a film's "tmdb:<id>" or "title:<title_key>:<year or ''>"
    key: str
    rows: tuple[ResolvedRow, ...]  # upload order: see row_order
    # True for an item the owner logged on the account: its rows go with no stars, so
    # the import keeps the rating the account gave it.
    withhold_rating: bool = False
    # The rows merged into this item's one row and not sent themselves: a book read
    # more than once goes as its newest read, and these are the earlier ones, whose
    # years its row names. Empty for a film, and for a book read once.
    merged: tuple[ResolvedRow, ...] = ()
    # A book sent although the account has it on a shelf other than read: that
    # shelf's name, or "" when the name cannot be shown. None for a book the account
    # lacks, and always for a film.
    account_shelf: str | None = None


# The film service's word for an item, which its code and tests use.
Film = Item


@dataclass(frozen=True)
class Selection:
    """What ``select`` decided: the films to send, the review list, and the cut."""

    films: tuple[Film, ...]  # ordered by their first sent row
    # Film by film, in the grouped films' order: a film whose same-date drop moves
    # its first row later keeps its place here, though not in ``films``.
    review: tuple[ReviewEntry, ...]
    cut: int  # rows the cutoff left out


def review_kinds(medium: Medium) -> tuple[str, ...]:
    """The review kinds a run of ``medium`` can earn, in ``REVIEW_KINDS`` order.

    A film run leaves out ``BOOK_KINDS`` and a book run ``FILM_KINDS``.
    """
    other = BOOK_KINDS if medium == "film" else FILM_KINDS
    return tuple(kind for kind in REVIEW_KINDS if kind not in other)


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


def group_films(
    rows: Iterable[ResolvedRow], item_key: Callable[[ResolvedRow], str]
) -> tuple[Film, ...]:
    """The rows grouped by ``item_key``, rows and items in upload order."""
    grouped: dict[str, list[ResolvedRow]] = {}
    for row in rows:
        grouped.setdefault(item_key(row), []).append(row)
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


def row_keys(film: Film) -> list[FilmKey]:
    """Every ``FilmKey`` an account could know a film's rows by, each once.

    Each row's ``_title_keys``, with the row's year.
    """
    keys = (FilmKey(key, row.year) for row in film.rows for key in _title_keys(row))
    return list(dict.fromkeys(keys))


# An item's fate on the account: the item to send, if any, and its review entries.
Fate = tuple[Item | None, list[ReviewEntry]]


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


def film_splits(films: Sequence[Film]) -> dict[str, str]:
    """Each film of a title split between a tmdbID film and a title-only film.

    Keyed by the film's key, each with its ``held-split`` detail: the tmdbID half's
    or the title-only half's (``_SPLIT_DETAILS``). Letterboxd's ``split_holds``.
    """
    split = _split_titles(films)
    return {
        film.key: _SPLIT_DETAILS[bool(film.rows[0].tmdb_id)]
        for film in films
        if any(key in split for row in film.rows for key in _title_keys(row))
    }


def _title_holds(
    films: Sequence[Film], holds: Mapping[str, Hold], splits: Mapping[str, str]
) -> dict[str, Hold]:
    """The holds an item's own titles give it, for each item ``holds`` does not hold.

    An item with no key to match the account by is held as ``held-no-title``: it
    could never be found on the account, so it would be sent on every run. An item
    ``splits`` names, the service's ``split_holds``, is ``held-split`` with the
    detail it gives.
    """
    found: dict[str, Hold] = {}
    for film in films:
        if film.key in holds:
            continue
        if not row_keys(film):
            found[film.key] = ("held-no-title", None)
        elif film.key in splits:
            found[film.key] = ("held-split", splits[film.key])
    return found


def held_films[C, A](
    films: Sequence[Film], spec: ServiceSpec[C, A], config: C, account: A
) -> dict[str, Hold]:
    """Every held item's key, with its hold's kind and detail (``None``: the kind's own).

    Seeded with the items an ``ambiguous`` or ``failed`` row holds and grown to a
    fixed point by ``spec.grow_holds``; then the items still free that
    ``_title_holds`` holds are added, the splits ``spec.split_holds`` finds with
    ``account`` and ``config`` among them, and the holds grown again. An item an ambiguous
    or failed one already holds keeps that kind, so the operator settles the lookup
    first.
    """
    holds: dict[str, Hold] = {
        film.key: (hold, None)
        for film in films
        if (hold := held_kind(film)) is not None
    }
    spec.grow_holds(films, holds, config)
    holds |= _title_holds(films, holds, spec.split_holds(films, account, config))
    spec.grow_holds(films, holds, config)
    return holds


# fuzz-target
def select[C, A](
    rows: Sequence[ResolvedRow],
    *,
    spec: ServiceSpec[C, A],
    config: C,
    account: A,
    cutoff: Cutoff | None,
    omit: Sequence[OmitEntry] = (),
) -> Selection:
    """Cut, omit, hold, skip, drop, and order ``rows``, as the module docstring says.

    ``spec`` keys the items, grows the holds, and decides each free item's fate on
    ``account``, each with ``config``, its table of the export config. ``omit`` names
    the items left out (``OmitEntry.names``): one row named leaves out its whole item,
    since an item is sent whole or not at all. Raises ``CutoffError``, naming the
    spec's table, before deciding anything when the cutoff names no dated row with
    its title.
    """
    kept, cut = apply_cutoff(rows, cutoff, where=f"{spec.name}.cutoff")
    grouped = group_films(kept, spec.item_key)
    # Named from every row read, the cut ones included, as check_omit accepts an
    # entry: an entry whose only named row was cut still leaves out its film's kept
    # rows, so moving the cutoff never turns an entry into one that omits nothing.
    left_out = {
        spec.item_key(row) for row in rows if any(entry.names(row) for entry in omit)
    }
    # An omitted film is never sent, so it can hide no held film, nor split a title.
    free = [film for film in grouped if film.key not in left_out]
    holds = held_films(free, spec, config, account)
    films: list[Film] = []
    review: list[ReviewEntry] = []
    for film in grouped:
        if film.key in left_out:
            review.append(review_entry(film, "omitted", _OMITTED.format(spec.name)))
            continue
        if film.key in holds:
            review.append(review_entry(film, *holds[film.key]))
            continue
        sent, entries = spec.account_fate(film, account, config)
        review += entries
        if sent is not None:
            films.append(sent)
    # A dropped same-date row can move a film's first row later, so order again.
    ordered = sorted(films, key=lambda film: row_order(film.rows[0]))
    return Selection(tuple(ordered), tuple(review), cut)
