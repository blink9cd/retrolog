"""Build TMDb request URLs and read TMDb's answers into candidates; no network here.

``search_url``, ``credits_url`` and ``details_url`` name the requests resolve makes
for films: a title search, one film's credits, and one film's details for a row an
override settled by its TMDb id. The search sends only the title: TMDb has no
"released before" filter, so the year bound is applied to each candidate's
``release_date`` by the caller, not sent. The token never enters a URL; the caller
sends it as an ``Authorization`` header.

``parse_search``, ``parse_directors`` and ``parse_details`` take a decoded JSON body of
any shape and never raise: a malformed result is skipped and a malformed body reads as
no answer, since a response is untrusted input.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from resolve_columns import CREATOR_JOINER, is_tmdb_id
from resolve_types import Candidate

API = "https://api.themoviedb.org/3"
# The fixed search parameters, after the title, in the order they are sent.
_SEARCH_PARAMS = (("include_adult", "false"), ("language", "en-US"), ("page", "1"))
# A full release date; a bare year or anything else is read as no year.
_RELEASE_DATE = re.compile(r"([0-9]{4})-[0-9]{2}-[0-9]{2}")
_DIRECTOR_JOB = "Director"
# A loose bound on a search result's integer id, checked before str() reads it.
_ID_BITS = 64


def search_url(title: str) -> str:
    """The /search/movie URL for ``title``: the title and the fixed parameters only."""
    return f"{API}/search/movie?" + urlencode((("query", title), *_SEARCH_PARAMS))


def details_url(tmdb_id: str) -> str:
    """The /movie/{id} URL; ``ValueError`` unless ``tmdb_id`` is a TMDb id.

    A TMDb id is ``resolve_columns.TMDB_ID``'s shape: ASCII digits only (it is spliced
    into a URL path), no leading zero, at most ten.
    """
    if not is_tmdb_id(tmdb_id):
        raise ValueError("a TMDb id is 1 to 10 ASCII digits with no leading zero")
    return f"{API}/movie/{tmdb_id}"


def credits_url(tmdb_id: str) -> str:
    """The /movie/{id}/credits URL; ``ValueError`` unless ``tmdb_id`` is a TMDb id."""
    return f"{details_url(tmdb_id)}/credits"


def _list_under(payload: object, key: str) -> list[object]:
    """``payload[key]`` when ``payload`` is an object and that is a list, else []."""
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _release_year(value: object) -> int | None:
    """The year of a "YYYY-MM-DD" release date, or ``None`` for anything else."""
    if isinstance(value, str):
        match = _RELEASE_DATE.fullmatch(value)
        if match:
            return int(match.group(1))
    return None


def _count_or_none(value: object) -> int | None:
    """``value`` when it is a non-negative ``int`` and not a ``bool``, else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _candidate_or_none(result: object) -> Candidate | None:
    """One search result as a ``Candidate``, or ``None`` when it is unusable.

    The id must be an ``int``, not a ``bool`` (which is an ``int`` in Python), whose
    digits are a TMDb id by ``is_tmdb_id`` (positive, at most ten digits), so every
    candidate id is one ``credits_url`` takes; the title must be text. A
    ``vote_count`` that is a non-negative ``int`` and not a ``bool`` is kept, and
    anything else reads as unknown.
    """
    if not isinstance(result, dict):
        return None
    tmdb_id = result.get("id")
    title = result.get("title")
    if not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool):
        return None
    # The size check first: str() refuses an int past its digit limit, and an id of
    # more than 64 bits is far past the ten digits is_tmdb_id allows anyway.
    if tmdb_id.bit_length() > _ID_BITS or not is_tmdb_id(str(tmdb_id)):
        return None
    if not isinstance(title, str):
        return None
    original = result.get("original_title")
    return Candidate(
        id=str(tmdb_id),
        title=title,
        original_title=original if isinstance(original, str) else "",
        year=_release_year(result.get("release_date")),
        creator="",
        isbn13="",
        votes=_count_or_none(result.get("vote_count")),
    )


# fuzz-target
def parse_search(payload: object) -> tuple[Candidate, ...]:
    """Every usable result of a /search/movie body, in TMDb's order; never raises."""
    candidates = (
        _candidate_or_none(result) for result in _list_under(payload, "results")
    )
    return tuple(c for c in candidates if c is not None)


# fuzz-target
def parse_directors(payload: object) -> str:
    """A credits body's directors, sorted, deduplicated and joined " & "; never raises.

    "" when the body names no director. A crew member counts only when its job is
    exactly "Director" and its name is non-blank text.
    """
    names = {
        member["name"]
        for member in _list_under(payload, "crew")
        if isinstance(member, dict)
        and member.get("job") == _DIRECTOR_JOB
        and isinstance(member.get("name"), str)
        and member["name"].strip()
    }
    return CREATOR_JOINER.join(sorted(names))


# fuzz-target
def parse_details(payload: object, tmdb_id: str) -> Candidate | None:
    """A /movie/{id} body as the film's ``Candidate``, or ``None``; never raises.

    The body has a search result's fields, so it is read by the same rule: an
    unusable id or title is ``None``, a missing original title reads as "", and an
    unreadable release date as no year. A body naming another film than ``tmdb_id`` is
    ``None`` too, so a mixed-up answer never lends a row another film's titles. The
    ids are compared as digit strings with leading zeros dropped (a candidate's id is
    ``str`` of an ``int``, so it has none), which ``int()`` could refuse past its
    digit limit.
    """
    candidate = _candidate_or_none(payload)
    if candidate is None or candidate.id != (tmdb_id.lstrip("0") or "0"):
        return None
    return candidate
