"""Tests for an override row's TMDb details: its titles, and an empty year, filled.

An override names only a TMDb id, so resolve asks TMDb for that film's details, once
per distinct id, and writes its title and original title (and the year, when the row
has none) as it does for a resolved film's chosen candidate. Export matches the
account on those titles. A failed request changes nothing about the row but those
cells, which stay empty. Every request goes to ``resolve_fixtures.Answering``, so each
test also pins exactly which requests a run makes.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_films  # noqa: E402 - after the sys.path inserts above
import resolve_http  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    Answering,
    FakeClock,
    FakeFetch,
    http_error,
    input_row,
    tmdb_result,
)
from resolve_rows_case import (  # noqa: E402 - after the sys.path inserts above
    ISBN,
    RowsCase,
    kinds,
    overrides_of,
)
from resolve_types import InputRow, ResolveOverride  # noqa: E402 - after the inserts

_ID = "9"
_DETAILS_URL = "https://api.themoviedb.org/3/movie/9"
_BY_TITLE = f'medium = "film"\ntitle = "Metropolis"\ntmdb_id = "{_ID}"'
_BY_LINE = (
    'medium = "film"\ntitle = "Metropolis"\nfile = "10/seen.txt"\nline = 7\n'
    f'tmdb_id = "{_ID}"'
)
_DETAILS = tmdb_result(
    9, "Metropolis Placeholder", original_title="Metropolis Original", release_date=""
)
_DETAILS_DATED = {**_DETAILS, "release_date": "1927-01-10"}


def _film(
    *,
    work_year: int | None = None,
    date: str = "2010-03-04",
    source_file: str = "10/read.txt",
    line: int = 1,
) -> InputRow:
    """A film row titled Metropolis, with no year, read on 2010-03-04 by default."""
    return input_row(
        "Metropolis",
        medium="film",
        work_year=work_year,
        date=date,
        source_file=source_file,
        line=line,
    )


class OverrideDetailsTest(RowsCase):
    """A film override with an id takes its TMDb titles from one details request."""

    def test_the_details_url_is_the_movie_alone(self) -> None:
        self.assertEqual(resolve_films.details_url(_ID), _DETAILS_URL)

    def test_a_title_override_takes_both_titles_and_an_empty_year(self) -> None:
        pages = {_DETAILS_URL: _DETAILS_DATED}
        out, review = self.run_all([_film()], overrides_of(_BY_TITLE), pages)
        self.assertEqual(self.status(out[0]), ("override", "", "", _ID))
        self.assertEqual(
            self.tmdb_titles(out[0]), ("Metropolis Placeholder", "Metropolis Original")
        )
        self.assertEqual(out[0].cells["year"], "1927")
        self.assertEqual(self.fetch.calls, [_DETAILS_URL])
        self.assertEqual(kinds(review), [("override-applied", "Metropolis")])
        self.assertEqual(review[0].detail, "overrides[0]: tmdb_id")

    def test_a_line_override_takes_them_too(self) -> None:
        row = _film(source_file="10/seen.txt", line=7)
        out, _ = self.run_all([row], overrides_of(_BY_LINE), {_DETAILS_URL: _DETAILS})
        self.assertEqual(self.tmdb_titles(out[0])[0], "Metropolis Placeholder")

    def test_a_row_with_a_year_keeps_it(self) -> None:
        pages = {_DETAILS_URL: _DETAILS_DATED}
        out, _ = self.run_all([_film(work_year=1926)], overrides_of(_BY_TITLE), pages)
        self.assertEqual(out[0].cells["year"], "1926")

    def test_an_undated_answer_leaves_an_empty_year_empty(self) -> None:
        pages = {_DETAILS_URL: _DETAILS}
        out, _ = self.run_all([_film()], overrides_of(_BY_TITLE), pages)
        self.assertEqual(out[0].cells["year"], "")

    def test_one_request_per_distinct_id(self) -> None:
        rows = [
            _film(source_file="10/seen.txt", line=7),
            _film(date="2012-06-30"),
            _film(date="2014-01-01"),
        ]
        overrides = overrides_of(_BY_LINE, _BY_TITLE)
        out, _ = self.run_all(rows, overrides, {_DETAILS_URL: _DETAILS})
        self.assertEqual(self.fetch.calls, [_DETAILS_URL])
        self.assertEqual(
            {self.tmdb_titles(row)[0] for row in out}, {"Metropolis Placeholder"}
        )

    def test_each_id_s_details_land_on_its_own_rows(self) -> None:
        other_url = "https://api.themoviedb.org/3/movie/10"
        other = tmdb_result(10, "Nosferatu Placeholder", release_date="1922-03-04")
        metropolis = _film()
        # A parse creator must survive: the details name no director.
        metropolis = dataclasses.replace(
            metropolis, cells={**metropolis.cells, "creator": "Lang"}
        )
        nosferatu = input_row("Nosferatu", medium="film", line=2)
        overrides = overrides_of(
            _BY_TITLE, 'medium = "film"\ntitle = "Nosferatu"\ntmdb_id = "10"'
        )
        pages = {_DETAILS_URL: _DETAILS_DATED, other_url: other}
        out, _ = self.run_all([metropolis, nosferatu], overrides, pages)
        self.assertEqual(
            [(self.tmdb_titles(row)[0], row.cells["year"]) for row in out],
            [("Metropolis Placeholder", "1927"), ("Nosferatu Placeholder", "1922")],
        )
        self.assertEqual(out[0].cells["creator"], "Lang")

    def test_unresolvable_sends_nothing(self) -> None:
        override = 'medium = "film"\ntitle = "Metropolis"\nunresolvable = true'
        out, _ = self.run_all([_film()], overrides_of(override))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.tmdb_titles(out[0]), ("", ""))

    def test_a_book_override_sends_nothing(self) -> None:
        override = f'medium = "book"\ntitle = "Moby Dick"\nisbn13 = "{ISBN}"'
        out, _ = self.run_all([input_row("Moby Dick")], overrides_of(override))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.tmdb_titles(out[0]), ("", ""))

    def test_directors_all_adds_no_credits_request(self) -> None:
        pages = {_DETAILS_URL: _DETAILS}
        self.run_all([_film()], overrides_of(_BY_TITLE), pages, directors="all")
        self.assertEqual(self.fetch.calls, [_DETAILS_URL])

    def test_the_request_names_only_the_id(self) -> None:
        self.run_all([_film()], overrides_of(_BY_TITLE), {_DETAILS_URL: _DETAILS})
        self.assertNotIn("Metropolis", self.fetch.calls[0])
        self.assertNotIn("2010", self.fetch.calls[0])


class FailedDetailsTest(RowsCase):
    """A details request that fails leaves the override row as it was."""

    def _assert_untouched(self, row: resolve_rows.OutputRow) -> None:
        """The row is still an override with its id, its titles and year empty."""
        self.assertEqual(self.status(row), ("override", "", "", _ID))
        self.assertEqual(self.tmdb_titles(row), ("", ""))
        self.assertEqual(row.cells["year"], "")

    def test_a_failure_or_an_odd_answer_changes_nothing(self) -> None:
        answers: tuple[object, ...] = (
            http_error(500),
            http_error(404),
            TimeoutError(),
            ["not", "an", "object"],
            {"success": False, "status_code": 34},
            {"id": 9, "title": None},
            {"id": 10, "title": "Another film's details"},
        )
        for answer in answers:
            with self.subTest(answer=answer):
                pages = {_DETAILS_URL: answer}
                out, review = self.run_all([_film()], overrides_of(_BY_TITLE), pages)
                self._assert_untouched(out[0])
                self.assertEqual(kinds(review), [("override-applied", "Metropolis")])

    def test_an_answer_that_is_not_json_changes_nothing(self) -> None:
        out = self._run_with(FakeFetch(b"<html>not json</html>"))
        self._assert_untouched(out[0])

    def test_a_second_429_changes_nothing(self) -> None:
        out = self._run_with(FakeFetch(http_error(429, "0"), http_error(429, "0")))
        self._assert_untouched(out[0])

    def test_a_refused_token_still_ends_the_run(self) -> None:
        with self.assertRaises(resolve_http.TokenRefused):
            self.run_all(
                [_film()], overrides_of(_BY_TITLE), {_DETAILS_URL: http_error(401)}
            )

    def test_no_tmdb_service_sends_nothing(self) -> None:
        fetch = FakeFetch()
        out = self._run_with(fetch, tmdb=None)
        self.assertEqual(fetch.calls, [])
        self._assert_untouched(out[0])

    def _run_with(
        self,
        fetch: FakeFetch,
        *,
        tmdb: resolve_http.Service | None = resolve_http.tmdb("tok"),
    ) -> tuple[resolve_rows.OutputRow, ...]:
        """The rows of a run of one overridden film, through ``fetch``."""
        time = FakeClock()
        client = resolve_http.Client(
            None, timeout=1.0, fetch=fetch, sleep=time.sleep, clock=time.clock
        )
        return _resolve(client, overrides_of(_BY_TITLE), tmdb)


class CachedDetailsTest(unittest.TestCase):
    """The details answer is cached like every other: --cache-only reproduces it."""

    def test_cache_only_reads_a_cached_answer_and_misses_an_uncached_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            fetch = Answering({_DETAILS_URL: _DETAILS})
            _resolve(_client(cache, fetch), overrides_of(_BY_TITLE))
            self.assertTrue((cache / resolve_http.cache_key(_DETAILS_URL)).is_file())
            again = Answering()
            out = _resolve(
                _client(cache, again, cache_only=True), overrides_of(_BY_TITLE)
            )
            self.assertEqual(out[0].cells["tmdb_title"], "Metropolis Placeholder")
            other = overrides_of(_BY_TITLE.replace(f'"{_ID}"', '"10"'))
            missed = _resolve(_client(cache, again, cache_only=True), other)
            self.assertEqual(missed[0].cells["tmdb_title"], "")
            self.assertEqual(missed[0].cells["resolve_status"], "override")
            self.assertEqual(again.calls, [])


def _client(
    cache: Path, fetch: Answering, *, cache_only: bool = False
) -> resolve_http.Client:
    """A Client over ``cache`` and ``fetch``, on a fake clock."""
    time = FakeClock()
    return resolve_http.Client(
        cache,
        timeout=1.0,
        cache_only=cache_only,
        fetch=fetch,
        sleep=time.sleep,
        clock=time.clock,
    )


def _resolve(
    client: resolve_http.Client,
    overrides: tuple[ResolveOverride, ...],
    tmdb: resolve_http.Service | None = resolve_http.tmdb(None),
) -> tuple[resolve_rows.OutputRow, ...]:
    """The rows of one overridden Metropolis row resolved through ``client``."""
    out, _ = resolve_rows.resolve_all(
        [_film()],
        overrides,
        client=client,
        open_library=resolve_http.open_library(None),
        tmdb=tmdb,
        directors="none",
        vote_ratio=10.0,
        vote_floor=1,
        research=True,
    )
    return out


if __name__ == "__main__":
    unittest.main()
