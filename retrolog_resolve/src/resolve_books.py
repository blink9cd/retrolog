"""Open Library's requests: the URL for each, and its answer parsed.

Pure: nothing here touches the network. The caller fetches a URL built here and hands
the matching parser the decoded JSON body, whatever shape it has; no parser raises.

- The search (``search_url``, ``parse_search``): a title is looked up in at most three
  steps (``next_step``), each asking less of the editions, so a work with no English
  edition carrying an ISBN is still found by the last step. Each URL carries only the
  title and the read year.
- An override book's details (``work_url`` and ``parse_work``, then ``author_url`` and
  ``parse_author`` for each of its authors). Each URL carries only a key, and a key of
  any other shape is refused before a URL is built.
- A work's edition list (``editions_url``, one page at a time), which
  ``resolve_editions`` parses and picks an ISBN-13 from; its URL carries only the
  work key and the paging.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Final, Literal

import resolve_isbn
from resolve_columns import CREATOR_JOINER
from resolve_types import Candidate, WorkDetails

SITE: Final = "https://openlibrary.org"
SEARCH: Final = f"{SITE}/search.json"
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
# A work's key. The search index also lists some orphan editions as works, under
# /works/ with an edition's M suffix; such a doc is skipped, since overrides and the
# review page take only this shape and export would send a key that is not a work.
_WORK_KEY: Final = re.compile(r"/works/OL[0-9]+W")
# Open Library's code for English, as its editions' language lists hold it.
_ENGLISH: Final = "eng"
# An author's key, as a work body's author entries name it.
_AUTHOR_KEY: Final = re.compile(r"/authors/OL[0-9]+A")
# A four-digit run standing alone in a first_publish_date ("1901", "April 10, 1901").
_FOUR_DIGIT_YEAR: Final = re.compile(r"(?<![0-9])[0-9]{4}(?![0-9])")
# A /works/{id}.json or /authors/{id}.json answer's suffix.
_JSON_SUFFIX: Final = ".json"
# Editions asked for per /works/{id}/editions.json page. The endpoint's own default is
# 50; 200 reads a large work in a quarter of the paced requests, and at the roughly
# 800 bytes an edition takes, a full page is about 160 KB, far inside bounded_fetch's
# body limit.
EDITIONS_PAGE_SIZE: Final = 200
# The most author entries a work body may list. Each author is one paced request, and
# the names are joined into the row's creator, whose first name export matches on and
# whose others become Goodreads' Additional Authors: a book has a few, an anthology a
# few dozen, and a body listing more is not a book's author list (a 4 MiB body could
# list about 100,000). Such a body is unusable, as one naming no author is.
MAX_WORK_AUTHORS: Final = 40


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
    """The step tried after ``step``, or None after the last.

    The chain moves on when ``step`` found no exact title with an ISBN.
    """
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
    """One work doc as a Candidate, or None without an object, a work key and a title."""
    if not isinstance(work, dict):
        return None
    key, title = work.get("key"), work.get("title")
    if not isinstance(key, str) or not _WORK_KEY.fullmatch(key):
        return None
    if not isinstance(title, str) or not title:
        return None
    return Candidate(
        id=key,
        title=title,
        original_title="",
        year=_year(work.get("first_publish_year")),
        creator=CREATOR_JOINER.join(_texts(work.get("author_name"))),
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

    A doc missing its title, or a work key (``/works/OL<digits>W``), is skipped; any
    other unexpected shape, at any level, gives nothing for that level rather than
    raising. Only steps whose query kept English editions with an ISBN record one; a
    work-only step never does.
    """
    found = (_candidate(work, step) for work in _works(payload))
    return tuple(candidate for candidate in found if candidate is not None)


def _key_url(key: str, shape: re.Pattern[str], what: str) -> str:
    """The JSON URL of ``key``; ``ValueError`` unless ``key`` has ``shape`` exactly.

    The key is spliced into a URL path, so nothing but the documented shape (ASCII
    digits between the prefix and the suffix letter) may reach it.
    """
    if not shape.fullmatch(key):
        raise ValueError(f"an Open Library {what} key is {shape.pattern}")
    return f"{SITE}{key}{_JSON_SUFFIX}"


def work_url(ol_work: str) -> str:
    """The /works/{id}.json URL; ``ValueError`` unless ``ol_work`` is a work key."""
    return _key_url(ol_work, _WORK_KEY, "work")


def editions_url(ol_work: str, page: int) -> str:
    """The ``page``-th (from 0) /works/{id}/editions.json URL of ``ol_work``.

    It carries the work key and the paging alone. ``ValueError`` unless ``ol_work``
    is a work key. The next page is built here from its number, never taken from the
    answer's own ``links.next``, so no text an answer holds reaches a URL.
    """
    base = work_url(ol_work).removesuffix(_JSON_SUFFIX)
    params = {"limit": EDITIONS_PAGE_SIZE, "offset": page * EDITIONS_PAGE_SIZE}
    return f"{base}/editions{_JSON_SUFFIX}?{urllib.parse.urlencode(params)}"


def author_url(author_key: str) -> str:
    """The /authors/{id}.json URL; ``ValueError`` unless ``author_key`` is one."""
    return _key_url(author_key, _AUTHOR_KEY, "author")


def _author_key(entry: object) -> str | None:
    """The key of one author-role entry (``{"author": {"key": ...}}``), or None."""
    if not isinstance(entry, dict):
        return None
    author = entry.get("author")
    key = author.get("key") if isinstance(author, dict) else None
    return key if isinstance(key, str) and _AUTHOR_KEY.fullmatch(key) else None


def _author_keys(value: object) -> tuple[str, ...]:
    """Each usable author key of a work's ``authors`` list, in order, each once."""
    entries = value if isinstance(value, list) else []
    keys = (_author_key(entry) for entry in entries)
    return tuple(dict.fromkeys(key for key in keys if key is not None))


def publish_year(value: object) -> int | None:
    """The first four-digit run of a publication date, or None without one.

    A work's first_publish_date and an edition's publish_date are both free text
    ("1901", "April 10, 1901"), so either is read the same way.
    """
    found = _FOUR_DIGIT_YEAR.search(value) if isinstance(value, str) else None
    return None if found is None else int(found.group())


# fuzz-target
def parse_work(payload: object, ol_work: str) -> WorkDetails | None:
    """A /works/{id}.json body as its author keys and year, or None; never raises.

    None unless the body is an object whose ``key`` is ``ol_work`` (so a mixed-up
    answer never lends a row another work's authors), its ``authors`` list holds at
    most ``MAX_WORK_AUTHORS`` entries (each is one request), and it names at least
    one usable author (so a redirect record, which names none, reads as None too).
    An author entry of another shape is skipped.
    """
    if not isinstance(payload, dict) or payload.get("key") != ol_work:
        return None
    authors = payload.get("authors")
    if isinstance(authors, list) and len(authors) > MAX_WORK_AUTHORS:
        return None
    keys = _author_keys(authors)
    if not keys:
        return None
    return WorkDetails(keys, publish_year(payload.get("first_publish_date")))


def _stripped_text(value: object) -> str | None:
    """``value`` stripped when it is text holding more than whitespace, else None."""
    text = value.strip() if isinstance(value, str) else ""
    return text or None


# fuzz-target
def parse_author(payload: object) -> str | None:
    """An /authors/{id}.json body's name, stripped, else its personal name; never raises.

    None when the body is not an object or neither field is non-blank text.
    """
    if not isinstance(payload, dict):
        return None
    name = _stripped_text(payload.get("name"))
    return name if name is not None else _stripped_text(payload.get("personal_name"))
