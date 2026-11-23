"""Look a book's details up on Open Library: work, authors, and edition list.

An override book takes its work's authors; every book takes an ISBN-13 picked from
its work's edition list.

``override_book_details`` is the entry point, and ``resolve_rows`` its only caller,
for a row an override settled by its work key: the book counterpart of
``resolve_film_lookup.override_details``. It sends the work's request, then one
request per author the work names, through the run's one ``Client`` and the Open
Library service the search uses, so each is cached by its URL and paced like every
other. Each URL carries only a key (``resolve_books.work_url`` and ``author_url``).

``edition_isbn13`` picks a book's ISBN-13 from its work's edition list
(``resolve_editions``) for one read year; ``resolve_work_isbn`` asks it once per work
per run, for a search-resolved book and an override book alike. The list is read page
by page through the same ``Client`` (``resolve_books.editions_url``, the key and the
paging alone), at most ``resolve_editions.PAGE_CAP`` pages.
"""

from __future__ import annotations

from collections.abc import MutableMapping

import resolve_books as books
import resolve_editions as editions
from resolve_columns import CREATOR_JOINER
from resolve_http import LookupFailed
from resolve_types import Candidate, Services


def _author_name(
    key: str, services: Services, names: MutableMapping[str, str | None]
) -> str | None:
    """The name of the author ``key`` names, from ``names`` or one request.

    The answer is kept in ``names``, a failure as None, so an author two works share
    is asked once in a run even when no cache is kept; the Client caches no failure,
    so a re-run asks again.
    """
    if key not in names:
        try:
            payload = services.client.get_json(
                services.open_library, books.author_url(key)
            )
        except LookupFailed:
            names[key] = None
        else:
            names[key] = books.parse_author(payload)
    return names[key]


def _creator(
    author_keys: tuple[str, ...],
    services: Services,
    names: MutableMapping[str, str | None],
) -> str | None:
    """Every author's name joined, in the work's order, or None when one is unread.

    A partial list would be a wrong author list, so the first unread name ends the
    lookup, and asks for no later author.
    """
    found: list[str] = []
    for key in author_keys:
        name = _author_name(key, services, names)
        if name is None:
            return None
        found.append(name)
    return CREATOR_JOINER.join(found)


def override_book_details(
    ol_work: str,
    services: Services,
    *,
    authors: MutableMapping[str, str | None] | None = None,
) -> Candidate | None:
    """The work ``ol_work`` names as a ``Candidate``, or None when it cannot be read.

    Its ``creator`` is the authors' names joined in the work's order, its ``year`` the
    work's first publication year, and its ``isbn13`` empty, so the override's own
    ISBN-13 stays the row's; its title is empty, since a book row's title is parse's.
    A ``LookupFailed`` (a cache miss under ``--cache-only`` included), an unusable work
    or author body, or a work naming no author all read as None, and the row stays as
    the override set it. ``authors`` holds the author names already read this run,
    keyed by author key; the caller passes one mapping for the whole run.

    Raises ``ValueError``, sending nothing, unless ``ol_work`` is a work key; the
    overrides loader refuses any other ``ol_work`` by the same rule, so it never does.
    """
    names: MutableMapping[str, str | None] = {} if authors is None else authors
    url = books.work_url(ol_work)
    try:
        work = books.parse_work(
            services.client.get_json(services.open_library, url), ol_work
        )
    except LookupFailed:
        return None
    if work is None:
        return None
    creator = _creator(work.author_keys, services, names)
    if creator is None:
        return None
    return Candidate(
        id=ol_work,
        title="",
        original_title="",
        year=work.year,
        creator=creator,
        isbn13="",
    )


def _read_editions(
    ol_work: str, services: Services
) -> tuple[editions.Edition, ...] | None:
    """Every edition of ``ol_work`` on its first ``PAGE_CAP`` pages, or None.

    Pages are read while the last says more follow. Any page that fails or is not an
    editions page makes the whole list None, so a pick is never made from a list cut
    short by a failure (only by the cap).
    """
    found: list[editions.Edition] = []
    for page in range(editions.PAGE_CAP):
        url = books.editions_url(ol_work, page)
        try:
            payload = services.client.get_json(services.open_library, url)
        except LookupFailed:
            return None
        parsed = editions.parse_editions_page(payload)
        if parsed is None:
            return None
        found.extend(parsed.editions)
        if not parsed.more:
            break
    return tuple(found)


def edition_isbn13(ol_work: str, date_year: int | None, services: Services) -> str:
    """The ISBN-13 picked from ``ol_work``'s editions for a row read in ``date_year``.

    "" when the list cannot be read, is empty, or holds no English edition with an
    ISBN-13; a failure fails nothing. Each call reads the list (the Client's cache,
    when kept, answers a repeated page), so the caller asks once per work. Raises
    ``ValueError``, sending nothing, unless ``ol_work`` is a work key.
    """
    found = _read_editions(ol_work, services)
    return "" if found is None else editions.pick_isbn13(found, date_year)
