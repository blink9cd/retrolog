"""Build retrolog_resolve test inputs at test time, so no parse output is ever committed.

A parse directory is written into a temporary folder as CSV files with parse's own
``COLUMNS``. Every title is invented (``Placeholder Book One``) or a well-known
public-domain work, and every id is made up: nothing here comes from a real list.

The sample builder imports this module too, so it uses the standard library only: the
hypothesis strategies the fuzz tests share are in ``resolve_strategies``.
"""

from __future__ import annotations

import csv
import email.message
import io
import json
import urllib.error
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from parse_output import COLUMNS, UNDATED
from parse_rows import title_key
from resolve_types import Candidate, InputRow, Medium

# One parse row whose every cell is filled the way parse writes it. A test changes the
# cells it cares about through ``parse_row``'s keyword arguments.
_ROW_DEFAULTS: Mapping[str, str] = {
    "medium": "book",
    "title": "Placeholder Book One",
    "creator": "",
    "year": "",
    "date": "2010-03-04",
    "date_is_approximate": "false",
    "color": "",
    "rating_raw": "x",
    "comment": "",
    "rewatch": "false",
    "section": "",
    "source_file": "10/read.txt",
    "source_line": "1",
}

# An overrides file holding one title-only film entry. Its title has doubled spaces and
# punctuation so a test can show it is matched by title_key, not by its spelling.
OVERRIDE_TOML_SOME_FILM = b"""
schema = 1

[[overrides]]
medium = "film"
title = "Some  Film!"
tmdb_id = "424242"
"""

# One valid entry of each shape, in file order: book by ISBN-10, book by work key and
# ISBN together, a film for one row only, and an unresolvable title.
OVERRIDE_TOML_EACH_SHAPE = b"""
schema = 1

[[overrides]]
medium = "book"
title = "Moby Dick"
isbn13 = "0-306-40615-2"

[[overrides]]
medium = "book"
title = "Placeholder Book One"
ol_work = "/works/OL123W"
isbn13 = "9780306406157"

[[overrides]]
medium = "film"
title = "Metropolis"
file = "10/seen.txt"
line = 7
tmdb_id = "999"

[[overrides]]
medium = "film"
title = "Placeholder Film Two"
unresolvable = true
"""


def parse_row(**cells: str) -> dict[str, str]:
    """A full parse row, the defaults replaced by ``cells``."""
    unknown = set(cells) - set(COLUMNS)
    if unknown:
        raise KeyError(f"not a parse column: {sorted(unknown)}")
    return {**_ROW_DEFAULTS, **cells}


def csv_text(rows: Sequence[Mapping[str, str]], header: Sequence[str] = COLUMNS) -> str:
    """CSV text the way parse writes it: ``header``, then each row's cells in order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([row.get(name, "") for name in header] for row in rows)
    return buffer.getvalue()


def write_parsed(folder: Path, files: Mapping[str, str]) -> Path:
    """Write each ``name -> text`` into ``folder`` (created), and return ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8", newline="")
    return folder


def override_toml(*entries: str) -> bytes:
    """An overrides file of ``schema = 1`` and each entry's body as one [[overrides]]."""
    tables = "".join(f"\n[[overrides]]\n{body}\n" for body in entries)
    return f"schema = 1\n{tables}".encode()


# ---------------------------------------------------------------------------
# Matching (resolve_match): candidates a search returned
# ---------------------------------------------------------------------------


def candidate(
    title: str,
    year: int | None,
    *,
    original_title: str = "",
    ident: str = "",
    votes: int | None = None,
) -> Candidate:
    """A search result with an invented id.

    The id is derived from its title and year unless ``ident`` is given, so two equal
    calls build equal candidates.
    """
    made_up = ident or f"{title}|{year}"
    return Candidate(
        id=made_up,
        title=title,
        original_title=original_title,
        year=year,
        creator="",
        isbn13="",
        votes=votes,
    )


# Open Library payloads
#
# The shapes /search.json answers with, reduced to the fields resolve asks for: a
# top-level object whose "docs" are works, each carrying an "editions" sub-object whose
# own "docs" are the editions the query's clauses kept, best first.


def ol_edition(*isbns: str, language: Sequence[str] = ("eng",)) -> dict[str, object]:
    """One edition doc holding ``isbns`` in order, in ``language``."""
    return {"key": "/books/OL1M", "isbn": list(isbns), "language": list(language)}


def ol_work(
    key: str = "/works/OL1W",
    title: str = "Moby Dick",
    authors: Sequence[str] = ("Placeholder Author",),
    year: int | None = 1851,
    editions: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """One work doc; ``year`` None leaves first_publish_year out, as Open Library does."""
    doc: dict[str, object] = {
        "key": key,
        "title": title,
        "author_name": list(authors),
        "editions": {"numFound": len(editions), "docs": list(editions)},
    }
    if year is not None:
        doc["first_publish_year"] = year
    return doc


def ol_payload(*works: Mapping[str, object]) -> dict[str, object]:
    """A /search.json answer holding ``works``."""
    return {"numFound": len(works), "start": 0, "docs": list(works)}


# TMDb payloads
#
# Response bodies shaped like TMDb's /search/movie and /movie/{id}/credits answers, built
# per test from invented titles and ids, so no real response is ever committed.


# Marks a keyword argument the caller did not pass, where ``None`` is a value to send.
_ABSENT = object()


def tmdb_result(
    tmdb_id: object,
    title: object,
    *,
    original_title: object = None,
    release_date: object = "",
    vote_count: object = _ABSENT,
) -> dict[str, object]:
    """One /search/movie result; ``original_title`` defaults to ``title``.

    ``vote_count`` is left out of the result unless given, so a test can send any
    value for it, ``None`` included.
    """
    result: dict[str, object] = {
        "id": tmdb_id,
        "title": title,
        "original_title": title if original_title is None else original_title,
        "release_date": release_date,
        "adult": False,
        "popularity": 1.5,
    }
    if vote_count is not _ABSENT:
        result["vote_count"] = vote_count
    return result


def tmdb_search_payload(*results: object) -> dict[str, object]:
    """A /search/movie body holding ``results`` on its first and only page."""
    return {
        "page": 1,
        "results": list(results),
        "total_pages": 1,
        "total_results": len(results),
    }


def tmdb_crew(name: object, job: object = "Director") -> dict[str, object]:
    """One crew member of a /movie/{id}/credits body."""
    return {"name": name, "job": job, "department": "Directing"}


def tmdb_credits_payload(*crew: object) -> dict[str, object]:
    """A /movie/{id}/credits body holding ``crew`` and no cast."""
    return {"id": 1, "cast": [], "crew": list(crew)}


# ---------------------------------------------------------------------------
# HTTP (resolve_http): a recording fetch, a fake clock and HTTP error statuses
# ---------------------------------------------------------------------------
#
# The fake fetch stands in for bounded_fetch.fetch_url wholesale, so nothing reaches the
# network, and the fake clock moves only when sleep is called, so nothing waits. Errors
# are the real urllib HTTPError type, so one left unclosed surfaces as a ResourceWarning.

# Obvious fakes: neither may ever appear outside the request's own headers.
HTTP_TOKEN = "tok-SECRET"
HTTP_CONTACT = "operator@example.invalid"
OL_URL = "https://openlibrary.org/search.json?q=moby+dick"
OL_URL_2 = "https://openlibrary.org/search.json?q=metropolis"
TMDB_URL = "https://api.themoviedb.org/3/search/movie?query=metropolis"
HTTP_BODY = b'{"docs": [{"title": "Moby Dick"}]}'
HTTP_DECODED = {"docs": [{"title": "Moby Dick"}]}


class FakeClock:
    """A monotonic clock that only ``sleep`` (or a test setting ``now``) moves."""

    def __init__(self) -> None:
        """Start at time zero, with no sleep recorded."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        """The current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record the wait and advance the clock by it."""
        self.sleeps.append(seconds)
        self.now += seconds


class FakeFetch:
    """Answers each request with the next queued outcome and records what was sent."""

    def __init__(self, *outcomes: bytes | BaseException) -> None:
        """Queue ``outcomes``: a body to return or an exception to raise, in order."""
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, float, dict[str, str]]] = []

    def __call__(self, url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
        """Record the request, then return or raise the next outcome."""
        self.calls.append((url, timeout, dict(headers)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ClosingHTTPError(urllib.error.HTTPError):
    """The real HTTPError type, counting every ``close`` across all instances."""

    closes = 0

    def close(self) -> None:
        """Count the close, then close the wrapped body as usual."""
        type(self).closes += 1
        super().close()


def http_error(code: int, retry_after: str | None = None) -> ClosingHTTPError:
    """An HTTP error status as urllib raises it, optionally carrying a Retry-After."""
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return ClosingHTTPError(OL_URL, code, "refused", headers, io.BytesIO(b""))


# ---------------------------------------------------------------------------
# Assembly (resolve_rows): rows built in memory and a fetch answering by URL
# ---------------------------------------------------------------------------
#
# ``Answering`` is the ``_answering(pages)`` pattern: each URL a test expects maps to
# the JSON it answers with (or an exception it raises), so a test names exactly the
# requests a run may make, and an unexpected one fails the test instead of passing
# quietly as "not found".

# An answer holding nothing, in both services' shapes: every lookup comes to not-found.
EMPTY_ANSWER: Mapping[str, object] = {"docs": [], "results": []}


def input_row(
    title: str,
    *,
    medium: Medium = "book",
    work_year: int | None = None,
    date: str = "2010-03-04",
    source_file: str = "10/read.txt",
    line: int = 1,
) -> InputRow:
    """An ``InputRow`` as ``read_parsed`` would give it, its CSV named from its date."""
    cells = parse_row(
        medium=medium,
        title=title,
        year="" if work_year is None else str(work_year),
        date=date,
        source_file=source_file,
        source_line=str(line),
    )
    year = date[:4] if date else UNDATED
    return InputRow(
        source_csv=f"{medium}-{year}.csv",
        cells=MappingProxyType(cells),
        medium=medium,
        title=title,
        title_key=title_key(title),
        work_year=work_year,
        date_year=int(date[:4]) if date else None,
        source_file=source_file,
        source_line=line,
    )


class Answering:
    """A ``RawFetch`` answering each URL in ``pages``; any other URL fails the test.

    With ``default`` given, an unlisted URL answers it instead. Every URL asked for
    is recorded in ``calls``, and the headers sent with it in ``headers``, in order.
    """

    def __init__(
        self, pages: Mapping[str, object] | None = None, *, default: object = None
    ) -> None:
        """Answer ``pages``; ``default`` (when not None) answers every other URL."""
        self.pages = dict(pages or {})
        self.default = default
        self.calls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
        """The listed answer as JSON bytes, or the listed exception raised."""
        self.calls.append(url)
        self.headers.append(dict(headers))
        answer = self.pages.get(url, self.default)
        if answer is None:
            # AssertionError is no transport failure the Client catches, so it
            # surfaces as the test failing rather than as a "failed" lookup.
            raise AssertionError(f"unexpected request: {url}")
        if isinstance(answer, BaseException):
            raise answer
        return json.dumps(answer).encode("utf-8")
