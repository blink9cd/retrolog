"""Choose the films to send: the cutoff, the held films, the account diff and the order.

``select`` takes every row export_input read and decides each one's fate, in this
order: rows at or after the operator's cutoff are cut and counted; the rest are
grouped into films; a film with an ``ambiguous`` or ``failed`` row is held whole,
and so is a film whose title and year would hide a held one on the next run's
diff, which passes the hold on in turn; then a film with no title the account could
know it by is held, and so is each film of a title whose rows split between a
tmdbID film and a title-only film, passing the hold on the same way; a film
already on the account is skipped whole; every other film is sent. Every input
row is cut, held, skipped or sent exactly once.

A film is matched against the account, and against the held films and a split title,
by each row's list title and each TMDb title resolve recorded for it, always with
the row's year: resolve can settle a film under a title other than the list's. Each
title counts by its ``title_key`` and by its folded key (``export_titles``), so an
accent or an "&" against "and" alone does not make two titles differ.

The account check is a parameter, ``on_service``, so this module never reads an
account export. Every review entry's detail is path-free and names no title: the
entry's own ``title`` and ``rows`` fields carry those.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from export_titles import match_keys
from export_types import Cutoff, FilmKey, ResolvedRow, ReviewEntry

# Every kind of review entry, the errors first.
REVIEW_KINDS = (
    "held-ambiguous",
    "held-failed",
    "held-split",
    "held-no-title",
    "on-service",
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
    "held-ambiguous": "resolve found more than one match; settle it with an override",
    "held-failed": "resolve could not look it up; run resolve again",
    "held-no-title": "its title has no letter or digit, so the account cannot be "
    "checked for it; add it by hand",
    "on-service": "already on the account, so not sent",
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
# An account row with no year matches its title in any year, and a row with no
# year matches any account row of its title; the entry says which.
_ON_SERVICE_BY_TITLE = "already on the account with no year, matched by title alone"
_ON_SERVICE_NO_YEAR = (
    "already on the account; the row has no year, so matched by title alone"
)
# One title's rows split between a tmdbID film and a title-only film. Each half's
# detail, keyed by whether that half has the tmdbID.
_SPLIT_DETAILS = {
    True: "other rows of this title have no tmdbID; settle them in resolve with an "
    "override, then rerun",
    False: "other rows of this title have a tmdbID; settle these in resolve with an "
    "override, then rerun",
}
# The characters a spreadsheet reads as the start of a formula when it opens a
# CSV. Export leaves such a cell as it is, since the importer reads it as text, and
# lists the row so the operator never opens the file in a spreadsheet first.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_FORMULA_DETAIL = (
    "cell starts with a character a spreadsheet reads as a formula; upload the file "
    "without opening it in one"
)
# A film sent now would put a held film's title on the account, and the next run would
# then skip the held one as on-service: so it waits too, under that film's kind.
_HELD_WITH_ANOTHER = "held with another film of this title until resolve settles it"
# A held film's review kind, and its detail when not the kind's own.
Hold = tuple[str, str | None]
# A sort key: undated rows first, then date, source file and line.
RowOrder = tuple[bool, datetime.date, str, int]


class CutoffError(ValueError):
    """The cutoff names no dated row with its title, so nothing can be cut safely."""


@dataclass(frozen=True)
class Film:
    """One film to send, and every one of its rows."""

    key: str  # "tmdb:<id>" or "title:<title_key>:<year or ''>"
    rows: tuple[ResolvedRow, ...]  # upload order: see row_order


@dataclass(frozen=True)
class Selection:
    """What ``select`` decided: the films to send, the review list and the cut count."""

    films: tuple[Film, ...]  # ordered by their first row
    review: tuple[ReviewEntry, ...]  # film by film, in film order
    cut: int  # rows the cutoff left out


def row_order(row: ResolvedRow) -> RowOrder:
    """The upload order's sort key: undated rows first, then by date, source file and line."""
    undated = row.date is None
    date = datetime.date.min if row.date is None else row.date
    return (not undated, date, row.source_file, row.source_line)


def _cut_order(row: ResolvedRow) -> tuple[datetime.date, str, int]:
    """Parse's order for dated rows (date, file, line), which the cutoff follows.

    Only dated rows are ever compared to the cutoff; the ``date.min`` stand-in is for
    the type checker, not a position an undated row could take.
    """
    return (row.date or datetime.date.min, row.source_file, row.source_line)


def apply_cutoff(
    rows: Sequence[ResolvedRow], cutoff: Cutoff | None
) -> tuple[list[ResolvedRow], int]:
    """The rows the cutoff keeps, and how many it cut.

    Raises ``CutoffError`` when no dated row sits at the cutoff's file and line with
    its title, since a numbering slip must not silently cut the wrong rows. The
    message names the config key only, never the position or the title.
    """
    if cutoff is None:
        return list(rows), 0
    named = [
        row
        for row in rows
        if row.date is not None
        and (row.source_file, row.source_line, row.title_key)
        == (cutoff.file, cutoff.line, cutoff.title_key)
    ]
    if not named:
        raise CutoffError(
            "letterboxd.cutoff names no dated row with that title at that position"
        )
    start = min(_cut_order(row) for row in named)
    kept = [row for row in rows if row.date is None or _cut_order(row) < start]
    return kept, len(rows) - len(kept)


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


def _entry(film: Film, kind: str, detail: str | None = None) -> ReviewEntry:
    """A review entry of ``kind`` covering every row of ``film``."""
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


def _formula_columns(
    cells: Sequence[str], checked: Mapping[str, int]
) -> tuple[str, ...]:
    """The ``checked`` column names whose cell in ``cells`` starts a formula."""
    return tuple(
        name for name, at in checked.items() if cells[at].startswith(FORMULA_STARTS)
    )


def formula_entries(
    films: Sequence[Film],
    cells: Sequence[Sequence[Sequence[str]]],
    checked: Mapping[str, int],
) -> list[ReviewEntry]:
    """One ``formula-cell`` entry per film with a row whose checked cell starts a formula.

    ``cells`` holds each film's import rows, in the order of ``films`` and of their
    rows; ``checked`` maps each column to check to its position in a row. An entry
    lists only the rows that have such a cell, and names the columns, never the text.
    """
    entries = []
    for film, rows in zip(films, cells, strict=True):
        found = [
            (row, names)
            for row, row_cells in zip(film.rows, rows, strict=True)
            if (names := _formula_columns(row_cells, checked))
        ]
        if found:
            named = ", ".join(
                dict.fromkeys(name for _, names in found for name in names)
            )
            flagged = Film(film.key, tuple(row for row, _ in found))
            entries.append(
                _entry(flagged, "formula-cell", f"its {named} {_FORMULA_DETAIL}")
            )
    return entries


def _unchecked(row: ResolvedRow) -> bool:
    """Whether ``row`` is sent by a tmdbID with no TMDb title to match an account by."""
    return bool(row.tmdb_id) and not row.tmdb_title_keys


def _unchecked_detail(film: Film) -> str:
    """The ``override-unchecked`` detail: an override's own, else a plain row's."""
    overridden = any(_unchecked(row) and row.status == "override" for row in film.rows)
    return _DETAILS["override-unchecked"] if overridden else _NO_TMDB_TITLE


def sent_entries(film: Film) -> list[ReviewEntry]:
    """The review entries a sent film earns, in ``REVIEW_KINDS`` order.

    A row sent by a tmdbID with no TMDb title to check, by title, unrated and
    undated.
    """
    entries = []
    if any(_unchecked(row) for row in film.rows):
        entries.append(_entry(film, "override-unchecked", _unchecked_detail(film)))
    if any(not row.tmdb_id for row in film.rows):
        entries.append(_entry(film, "by-title"))
    if any(not row.rating_raw for row in film.rows):
        entries.append(_entry(film, "unrated"))
    if any(row.date is None for row in film.rows):
        entries.append(_entry(film, "undated"))
    return entries


def _on_service_detail(key: FilmKey, account: frozenset[FilmKey]) -> str:
    """Say whether the account matched this film by its year or by its title alone.

    ``key`` is the film's key ``on_service`` matched. Not in the account as it is, it
    met either an account entry with no year or, having no year itself, an entry of
    its title in some year.
    """
    if key in account:
        return _DETAILS["on-service"]
    if FilmKey(key.title_key, None) in account:
        return _ON_SERVICE_BY_TITLE
    return _ON_SERVICE_NO_YEAR


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


def _on_service_key(
    film: Film,
    account: frozenset[FilmKey],
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool],
) -> FilmKey | None:
    """The first row key of ``film`` the account already holds, if any.

    Every row is asked, not the first alone: rows sharing a tmdbID can carry
    different titles (an original title, an override).
    """
    return next((key for key in _row_keys(film) if on_service(key, account)), None)


def _grow_holds(
    films: Sequence[Film],
    holds: dict[str, Hold],
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool],
) -> None:
    """Grow ``holds`` in place to a fixed point.

    A film that sending would hide a held one is held too, under that film's kind,
    and passes the hold on in turn, since its own rows can hide another film's in the
    same way. Stops when a pass holds nothing new.
    """
    grew = True
    while grew:
        held = _held_keys(films, holds)
        grew = False
        for film in films:
            if film.key in holds:
                continue
            shared = _shared_hold(film, held, on_service)
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
) -> dict[str, Hold]:
    """Every held film's key, with its hold's kind and detail (``None``: the kind's own).

    Seeded with the films an ``ambiguous`` or ``failed`` row holds and grown to
    a fixed point; then the films still free that ``_title_holds`` holds are added,
    and the holds grown again. A film an ambiguous or failed one already holds keeps
    that kind, so the operator settles the lookup first.
    """
    holds: dict[str, Hold] = {
        film.key: (hold, None)
        for film in films
        if (hold := held_kind(film)) is not None
    }
    _grow_holds(films, holds, on_service)
    holds |= _title_holds(films, holds)
    _grow_holds(films, holds, on_service)
    return holds


# fuzz-target
def select(
    rows: Sequence[ResolvedRow],
    *,
    cutoff: Cutoff | None,
    account: frozenset[FilmKey],
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool],
) -> Selection:
    """Cut, hold, skip and order ``rows``, as the module docstring sets out.

    ``on_service`` answers whether a film, as ``FilmKey(title_key, year)`` of one of
    its rows, is already in ``account``. Raises ``CutoffError`` before deciding
    anything when the cutoff names no dated row with its title.
    """
    kept, cut = apply_cutoff(rows, cutoff)
    grouped = group_films(kept)
    holds = held_films(grouped, on_service)
    films: list[Film] = []
    review: list[ReviewEntry] = []
    for film in grouped:
        if film.key in holds:
            review.append(_entry(film, *holds[film.key]))
        elif (key := _on_service_key(film, account, on_service)) is not None:
            review.append(_entry(film, "on-service", _on_service_detail(key, account)))
        else:
            films.append(film)
            review += sent_entries(film)
    return Selection(tuple(films), tuple(review), cut)
