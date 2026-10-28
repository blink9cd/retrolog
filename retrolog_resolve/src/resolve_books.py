"""Open Library's search: the URL for each step of the query chain, and its answer parsed.

Pure: nothing here touches the network. The caller fetches ``search_url``'s URL and
hands ``parse_search`` the decoded JSON body, whatever shape it has. A title is looked
up in at most three steps (``next_step``), each asking less of the editions, so a work
with no English edition carrying an ISBN is still found by the last step. Each
URL carries only the title and the read year.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Final, Literal

import resolve_isbn
from resolve_types import Candidate

SEARCH: Final = "https://openlibrary.org/search.json"
# The query chain's steps, in the order they are tried.
Step = Literal["edition-by-year", "edition-after", "work-only"]
_NEXT: Final[Mapping[Step, Step | None]] = {
    "edition-by-year": "edition-after",
    "edition-after": "work-only",
    "work-only": None,
}
# The steps whose query keeps only English editions with an ISBN, so the first edition
# they return is the one resolve records. A work-only answer's editions are unfiltered.
_EDITION_STEPS: Final[frozenset[Step]] = frozenset({"edition-by-year", "edition-after"})
# Which edition comes first in a work's editions sub-object: the latest in or before
# the read year, else the earliest after it.
_EDITION_SORT: Final[Mapping[Step, str]] = {
    "edition-by-year": "new",
    "edition-after": "old",
}
# Every field matching and review read, and no more.
_FIELDS: Final = (
    "key,title,author_name,first_publish_year,editions,editions.isbn,"
    "editions.language,editions.publish_year"
)
_LIMIT: Final = 10
# Open Library's code for English, as its editions' language lists hold it.
_ENGLISH: Final = "eng"
_AUTHOR_JOIN: Final = " & "


def solr_phrase(title: str) -> str:
    """``title`` as a quoted Solr phrase, its backslashes and double quotes escaped."""
    escaped = title.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _query(title: str, date_year: int | None, step: Step) -> str:
    """The Solr ``q`` for one step; every year clause is left out when undated."""
    clauses = [f"title:{solr_phrase(title)}"]
    if date_year is not None:
        clauses.append(f"first_publish_year:[* TO {date_year}]")
    if step in _EDITION_STEPS:
        clauses += [f"language:{_ENGLISH}", "isbn:*"]
    if step == "edition-by-year" and date_year is not None:
        clauses.append(f"publish_year:[* TO {date_year}]")
    return " ".join(clauses)


def search_url(title: str, date_year: int | None, step: Step) -> str:
    """The /search.json URL for ``title`` read in ``date_year`` at ``step`` of the chain."""
    params = {"q": _query(title, date_year, step), "fields": _FIELDS, "limit": _LIMIT}
    sort = _EDITION_SORT.get(step)
    if sort is not None:
        params["editions.sort"] = sort
    return f"{SEARCH}?{urllib.parse.urlencode(params)}"


def next_step(step: Step) -> Step | None:
    """The step tried when ``step`` found no exact title, or None after the last."""
    return _NEXT[step]


def _texts(value: object) -> list[str]:
    """The strings of ``value`` when it is a list, in order; anything else gives []."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _first_edition(work: Mapping[str, object]) -> Mapping[str, object] | None:
    """The first doc of ``work``'s editions sub-object, or None when it has none."""
    editions = work.get("editions")
    if not isinstance(editions, dict):
        return None
    docs = editions.get("docs")
    if not isinstance(docs, list) or not docs or not isinstance(docs[0], dict):
        return None
    return docs[0]


def _edition_isbn13(work: Mapping[str, object], step: Step) -> str:
    """The recorded ISBN-13 for ``work``, or "" when it has none.

    That is the first verified ISBN of its first edition, when that edition is English
    and ``step`` is one whose query filtered the editions.
    """
    edition = _first_edition(work) if step in _EDITION_STEPS else None
    if edition is None or _ENGLISH not in _texts(edition.get("language")):
        return ""
    for text in _texts(edition.get("isbn")):
        isbn13 = resolve_isbn.isbn13_or_none(text)
        if isbn13 is not None:
            return isbn13
    return ""


def _year(value: object) -> int | None:
    """``value`` as a year when it is an integer; bool is not one, though Python says so."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _candidate(work: object, step: Step) -> Candidate | None:
    """One work doc as a Candidate, or None when it is not an object with a key and title."""
    if not isinstance(work, dict):
        return None
    key, title = work.get("key"), work.get("title")
    if not isinstance(key, str) or not key or not isinstance(title, str) or not title:
        return None
    return Candidate(
        id=key,
        title=title,
        original_title="",
        year=_year(work.get("first_publish_year")),
        creator=_AUTHOR_JOIN.join(_texts(work.get("author_name"))),
        isbn13=_edition_isbn13(work, step),
    )


def _works(payload: object) -> Sequence[object]:
    """The ``docs`` list of a /search.json answer, or () for any other shape."""
    if not isinstance(payload, dict):
        return ()
    docs = payload.get("docs")
    return docs if isinstance(docs, list) else ()


# fuzz-target
def parse_search(payload: object, step: Step) -> tuple[Candidate, ...]:
    """Every work in a decoded /search.json answer ``step`` asked for, as Candidates.

    A doc missing its key or title is skipped; any other unexpected shape, at any
    level, gives nothing for that level rather than raising. Only steps whose query
    kept English editions with an ISBN record one; a work-only step never does.
    """
    found = (_candidate(work, step) for work in _works(payload))
    return tuple(candidate for candidate in found if candidate is not None)
