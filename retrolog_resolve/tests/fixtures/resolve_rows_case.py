"""The shared harness of the resolve_rows tests: helpers and a case running resolve_all.

The importing test puts the tool's, parse's and the survey's src/ folders and this
folder on ``sys.path`` before importing this module; resolve_http finds bounded_fetch
itself.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

import resolve_books
import resolve_http
import resolve_rows
from resolve_fixtures import (
    HTTP_TOKEN,
    Answering,
    FakeClock,
    ol_payload,
    override_toml,
    tmdb_search_payload,
)
from resolve_overrides import load_overrides
from resolve_types import Directors, InputRow, ResolveOverride, ReviewEntry

# The query chain's steps, in order.
STEP1: resolve_books.Step = "edition-by-year"
STEP2: resolve_books.Step = "edition-after"
STEP3: resolve_books.Step = "work-only"
ISBN = "9780306406157"
EMPTY_OL = ol_payload()
EMPTY_TMDB = tmdb_search_payload()
# The status and id columns ``RowsCase.status`` reads, in RESOLVE_COLUMNS' order.
_STATUS_COLUMNS = ("resolve_status", "isbn13", "ol_work", "tmdb_id")


def ol_url(title: str, step: resolve_books.Step, year: int | None = 2010) -> str:
    """The Open Library URL a row read in ``year`` sends at ``step``."""
    return resolve_books.search_url(title, year, step)


def overrides_of(*entries: str) -> tuple[ResolveOverride, ...]:
    """Loaded overrides, from each entry's TOML body."""
    return load_overrides(override_toml(*entries))


def kinds(entries: Sequence[ReviewEntry]) -> list[tuple[str, str]]:
    """Each entry as (kind, title), in the order given."""
    return [(entry.kind, entry.title) for entry in entries]


class RowsCase(unittest.TestCase):
    """Runs ``resolve_all`` against a fetch answering ``pages``, with no cache.

    ``research`` defaults to on, as the command line's ``--research`` does.
    """

    def run_all(
        self,
        rows: Sequence[InputRow],
        overrides: Sequence[ResolveOverride] = (),
        pages: Mapping[str, object] | None = None,
        *,
        default: object = None,
        directors: Directors = "none",
        vote_ratio: float = 10.0,
        research: bool = True,
        tmdb: resolve_http.Service | None = None,
        cache: Path | None = None,
    ) -> tuple[tuple[resolve_rows.OutputRow, ...], tuple[ReviewEntry, ...]]:
        """The run's rows and review; the requests made are kept in ``self.fetch``."""
        self.fetch = Answering(pages, default=default)
        time = FakeClock()
        client = resolve_http.Client(
            cache, timeout=1.0, fetch=self.fetch, sleep=time.sleep, clock=time.clock
        )
        return resolve_rows.resolve_all(
            rows,
            overrides,
            client=client,
            open_library=resolve_http.open_library(None),
            tmdb=resolve_http.tmdb(HTTP_TOKEN) if tmdb is None else tmdb,
            directors=directors,
            vote_ratio=vote_ratio,
            research=research,
        )

    def status(self, row: resolve_rows.OutputRow) -> tuple[str, ...]:
        """A row's status and id cells: ``RESOLVE_COLUMNS`` before the TMDb titles."""
        return tuple(row.cells[c] for c in _STATUS_COLUMNS)

    def tmdb_titles(self, row: resolve_rows.OutputRow) -> tuple[str, str]:
        """A row's ``tmdb_title`` and ``tmdb_original_title`` cells."""
        return (row.cells["tmdb_title"], row.cells["tmdb_original_title"])
