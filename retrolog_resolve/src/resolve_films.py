"""Build TMDb request URLs and read TMDb's answers into candidates; no network here.

``search_url`` and ``credits_url`` name the two requests resolve makes per film: a
title search and one film's credits. The search sends only the title: TMDb has no
"released before" filter, so the year bound is applied to each candidate's
``release_date`` by the caller, not sent. The token never enters a URL; the caller
sends it as an ``Authorization`` header.

``parse_search`` and ``parse_directors`` take a decoded JSON body of any shape and never
raise: a malformed result is skipped and a malformed body reads as no answer, since a
response is untrusted input.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from resolve_types import Candidate

API = "https://api.themoviedb.org/3"
# The fixed search parameters, after the title, in the order they are sent.
_SEARCH_PARAMS = (("include_adult", "false"), ("language", "en-US"), ("page", "1"))
# Only ASCII digits: str.isdigit and \d without re.ASCII also accept other scripts'
# digits, and a TMDb id is spliced into a URL path.
_TMDB_ID = re.compile(r"[0-9]+")
# A full release date; a bare year or anything else is read as no year.
_RELEASE_DATE = re.compile(r"([0-9]{4})-[0-9]{2}-[0-9]{2}")
_DIRECTOR_JOB = "Director"
_CREATOR_JOINER = " & "


def search_url(title: str) -> str:
    """The /search/movie URL for ``title``: the title and the fixed parameters only."""
    return f"{API}/search/movie?" + urlencode((("query", title), *_SEARCH_PARAMS))


def credits_url(tmdb_id: str) -> str:
    """The /movie/{id}/credits URL; ``ValueError`` unless ``tmdb_id`` is ASCII digits."""
    if not _TMDB_ID.fullmatch(tmdb_id):
        raise ValueError("a TMDb id is ASCII digits only")
    return f"{API}/movie/{tmdb_id}/credits"


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

    The id must be a non-negative ``int`` and not a ``bool`` (which is an ``int`` in
    Python), so every candidate id is ASCII digits; the title must be text. A
    ``vote_count`` held to the same test is kept, and anything else reads as unknown.
    """
    if not isinstance(result, dict):
        return None
    tmdb_id = result.get("id")
    title = result.get("title")
    if not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool) or tmdb_id < 0:
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
    return _CREATOR_JOINER.join(sorted(names))
