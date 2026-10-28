"""Look one film title up on TMDb: the search, the re-search, then the credits.

``film_lookup`` is the entry point, and ``resolve_rows`` its only caller. It sends the
title search, judges it with ``resolve_match.film_verdict``, sends
at most one more search under the title without a leading article when that found no
exact title (``research_query`` and ``research_verdict``), and then the credits
requests ``--directors`` asks for. Every request goes through the run's one
``Client``, so each is cached by its URL.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import resolve_films as films
from resolve_http import LookupFailed, Service
from resolve_match import film_verdict, research_query, research_verdict
from resolve_types import Candidate, InputRow, Lookup, Services

# The --directors settings that look credits up for the candidates a review entry lists
# (an ambiguous film's, or a loosened match's), and the one that also looks them up for
# a film the exact rule resolved.
_DIRECTORS_FOR_SHOWN = frozenset({"ambiguous", "all"})
_DIRECTORS_FOR_CHOSEN = "all"
_NO_TMDB = "tmdb: no token was given"
# Added to a listed film's detail when a shown candidate's credits failed.
_DIRECTORS_UNAVAILABLE = "; directors unavailable"
# Added to the first search's detail when the second search failed. Path-free: the
# failure's own message is dropped, so the detail never names the URL.
_RESEARCH_FAILED = "; the search without a leading article failed"


def _directed(
    candidate: Candidate, services: Services, tmdb: Service
) -> Candidate | None:
    """``candidate`` with its creator set from its TMDb credits, or None on failure.

    A failed credits request is the director's loss alone: the director only helps
    the operator choose, so the caller keeps the candidate as it was, and the
    Client never caches the failure, so a re-run asks again. ``TokenRefused`` is not
    a ``LookupFailed`` and still ends the run.

    ``credits_url`` refuses an id that is not ASCII digits, and ``parse_search``
    builds every film candidate's id from a non-negative integer, so it never does.
    """
    try:
        payload = services.client.get_json(tmdb, films.credits_url(candidate.id))
    except LookupFailed:
        return None
    return dataclasses.replace(candidate, creator=films.parse_directors(payload))


def _lists_candidates(lookup: Lookup) -> bool:
    """Whether ``lookup``'s review entry lists candidates for the operator to check.

    An ambiguous film's do, and so do a film's the most-votes rule, a variant or the
    re-search resolved: none of those was a single exact title.
    """
    return lookup.status == "ambiguous" or lookup.basis != "exact"


def _with_directors(lookup: Lookup, services: Services, tmdb: Service) -> Lookup:
    """``lookup`` with directors added to the candidates ``--directors`` names.

    A candidate whose credits could not be read keeps its blank creator; a listing
    lookup's detail then says so, so a blank is not read as "no director". A loosened
    match's chosen candidate is always among those it shows, so it takes its credited
    copy from there with no second request, and the row's ``creator`` is filled.
    """
    if _lists_candidates(lookup) and services.directors in _DIRECTORS_FOR_SHOWN:
        directed = [_directed(c, services, tmdb) for c in lookup.shown]
        shown = tuple(d or c for d, c in zip(directed, lookup.shown, strict=True))
        detail = lookup.detail
        if None in directed:
            detail = f"{detail}{_DIRECTORS_UNAVAILABLE}"
        listed = lookup.chosen
        if listed is not None:
            listed = shown[lookup.shown.index(listed)]
        return dataclasses.replace(lookup, chosen=listed, shown=shown, detail=detail)
    chosen = lookup.chosen
    if chosen is not None and services.directors == _DIRECTORS_FOR_CHOSEN:
        directed_chosen = _directed(chosen, services, tmdb) or chosen
        return dataclasses.replace(lookup, chosen=directed_chosen)
    return lookup


def _researched(
    row: InputRow,
    query: str,
    first: Lookup,
    found: Sequence[Candidate],
    services: Services,
    tmdb: Service,
) -> Lookup:
    """The re-search: ``first``, or what one more search, for ``query``, comes to instead.

    ``found`` is the first search's candidates, judged with the second's. A failed
    request keeps ``first`` and says so in its detail; ``TokenRefused`` is not a
    ``LookupFailed`` and still ends the run.
    """
    try:
        payload = services.client.get_json(tmdb, films.search_url(query))
    except LookupFailed:
        return dataclasses.replace(first, detail=f"{first.detail}{_RESEARCH_FAILED}")
    again = research_verdict(
        found,
        films.parse_search(payload),
        row.title,
        work_year=row.work_year,
        date_year=row.date_year,
    )
    return first if again is None else again


def film_lookup(row: InputRow, services: Services) -> Lookup:
    """The TMDb search for ``row``'s title, judged, maybe searched again, then credits.

    Raises ``LookupFailed`` when no token was given or the first search fails.
    """
    tmdb = services.tmdb
    if tmdb is None:
        raise LookupFailed(_NO_TMDB)
    payload = services.client.get_json(tmdb, films.search_url(row.title))
    found = films.parse_search(payload)
    work_year, date_year = row.work_year, row.date_year
    lookup = film_verdict(
        found,
        row.title,
        row.title_key,
        work_year=work_year,
        date_year=date_year,
        vote_ratio=services.vote_ratio,
    )
    query = research_query(
        lookup,
        found,
        row.title,
        row.title_key,
        work_year=work_year,
        date_year=date_year,
    )
    if services.research and query is not None:
        lookup = _researched(row, query, lookup, found, services, tmdb)
    return _with_directors(lookup, services, tmdb)
