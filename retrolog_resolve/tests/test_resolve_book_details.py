"""Tests for an override book's Open Library details: its author, and an empty year.

An override names only a work key (and maybe an ISBN-13), so resolve asks Open Library
for that work, then for each of its authors, and writes the authors' names to
``creator`` (and the work's year, when the row has none), as it does for a resolved
book's chosen candidate. A failed request changes nothing about the row. Every request
goes to ``resolve_fixtures.Answering``, so each test also pins exactly which requests a
run makes. Every key, title and name here is invented.
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
import resolve_books  # noqa: E402 - after the sys.path inserts above
import resolve_http  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from resolve_book_lookup import override_book_details  # noqa: E402 - after the inserts
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    Answering,
    FakeClock,
    FakeFetch,
    http_error,
    input_row,
    ol_author_body,
    ol_editions_page,
    ol_work_body,
    tmdb_result,
)
from resolve_rows_case import (  # noqa: E402 - after the sys.path inserts above
    ISBN,
    RowsCase,
    kinds,
    overrides_of,
)
from resolve_types import (  # noqa: E402 - after the sys.path inserts above
    InputRow,
    ResolveOverride,
    Services,
)

_WORK = "/works/OL1W"
_OTHER_WORK = "/works/OL2W"
_FIRST = "/authors/OL1A"
_SECOND = "/authors/OL2A"
_WORK_URL = "https://openlibrary.org/works/OL1W.json"
_OTHER_WORK_URL = "https://openlibrary.org/works/OL2W.json"
_FIRST_URL = "https://openlibrary.org/authors/OL1A.json"
_SECOND_URL = "https://openlibrary.org/authors/OL2A.json"
# Each work's edition list, asked for a work key with no ISBN-13 (the pick itself
# is test_resolve_edition_lookup's): empty here, so no ISBN-13 is taken.
_EDITIONS_URL = resolve_books.editions_url(_WORK, 0)
_OTHER_EDITIONS_URL = resolve_books.editions_url(_OTHER_WORK, 0)
_TITLE = "Placeholder Book One"
_BY_TITLE = f'medium = "book"\ntitle = "{_TITLE}"\nol_work = "{_WORK}"'
_WITH_ISBN = f'{_BY_TITLE}\nisbn13 = "{ISBN}"'
_BY_LINE = f'{_BY_TITLE}\nfile = "10/shelf.txt"\nline = 7'
_OTHER = 'medium = "book"\ntitle = "Placeholder Book Two"\nol_work = "/works/OL2W"'
# A work by two authors, the second named first in no order but the work's.
_PAGES: dict[str, object] = {
    _WORK_URL: ol_work_body(_WORK, _SECOND, _FIRST, first_publish_date="1901"),
    _FIRST_URL: ol_author_body(_FIRST, "Invented Author"),
    _SECOND_URL: ol_author_body(_SECOND, "Another Invented Author"),
    _EDITIONS_URL: ol_editions_page(),
    _OTHER_EDITIONS_URL: ol_editions_page(),
}
_BOTH = "Another Invented Author & Invented Author"


def _book(
    title: str = _TITLE,
    *,
    work_year: int | None = None,
    creator: str = "",
    source_file: str = "10/read.txt",
    line: int = 1,
) -> InputRow:
    """A book row with no year and no creator, read on 2010-03-04, by default."""
    row = input_row(title, work_year=work_year, source_file=source_file, line=line)
    return dataclasses.replace(row, cells={**row.cells, "creator": creator})


def _services(fetch: Answering | FakeFetch, cache: Path | None = None) -> Services:
    """The Services a run builds, over ``fetch`` on a fake clock."""
    return Services(
        _client(fetch, cache),
        resolve_http.open_library(None),
        None,
        "none",
        10.0,
        1,
        True,
    )


def _client(
    fetch: Answering | FakeFetch, cache: Path | None = None, *, cache_only: bool = False
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


class BookDetailsTest(RowsCase):
    """A book override with a work key takes its author from the work's authors."""

    def test_a_title_override_takes_the_authors_and_an_empty_year(self) -> None:
        out, review = self.run_all([_book()], overrides_of(_WITH_ISBN), _PAGES)
        self.assertEqual(self.status(out[0]), ("override", ISBN, _WORK, ""))
        self.assertEqual(out[0].cells["creator"], _BOTH)
        self.assertEqual(out[0].cells["year"], "1901")
        self.assertEqual(self.fetch.calls, [_WORK_URL, _SECOND_URL, _FIRST_URL])
        self.assertEqual(kinds(review), [("override-applied", _TITLE)])

    def test_a_work_key_alone_with_no_isbn_edition_leaves_the_isbn_empty(self) -> None:
        out, _ = self.run_all([_book()], overrides_of(_BY_TITLE), _PAGES)
        self.assertEqual(self.status(out[0]), ("override", "", _WORK, ""))
        self.assertEqual(out[0].cells["creator"], _BOTH)

    def test_a_line_override_takes_them_too(self) -> None:
        row = _book(source_file="10/shelf.txt", line=7)
        out, _ = self.run_all([row], overrides_of(_BY_LINE), _PAGES)
        self.assertEqual(out[0].cells["creator"], _BOTH)

    def test_a_row_with_a_year_keeps_it(self) -> None:
        out, _ = self.run_all([_book(work_year=1899)], overrides_of(_BY_TITLE), _PAGES)
        self.assertEqual(out[0].cells["year"], "1899")

    def test_an_undated_work_leaves_an_empty_year_empty(self) -> None:
        pages = {**_PAGES, _WORK_URL: ol_work_body(_WORK, _FIRST)}
        out, _ = self.run_all([_book()], overrides_of(_BY_TITLE), pages)
        self.assertEqual(out[0].cells["year"], "")
        self.assertEqual(out[0].cells["creator"], "Invented Author")

    def test_one_request_per_distinct_work(self) -> None:
        rows = [
            _book(source_file="10/shelf.txt", line=7),
            input_row(_TITLE, date="2012-06-30"),
            input_row(_TITLE, date="2014-01-01"),
        ]
        out, _ = self.run_all(rows, overrides_of(_BY_LINE, _BY_TITLE), _PAGES)
        self.assertEqual(
            self.fetch.calls, [_WORK_URL, _SECOND_URL, _FIRST_URL, _EDITIONS_URL]
        )
        self.assertEqual({row.cells["creator"] for row in out}, {_BOTH})

    def test_an_author_shared_by_two_works_is_asked_once(self) -> None:
        pages = {
            **_PAGES,
            _OTHER_WORK_URL: ol_work_body(_OTHER_WORK, _FIRST),
        }
        rows = [_book(), _book("Placeholder Book Two", line=2)]
        out, _ = self.run_all(rows, overrides_of(_BY_TITLE, _OTHER), pages)
        self.assertEqual(
            self.fetch.calls,
            [
                _WORK_URL,
                _SECOND_URL,
                _FIRST_URL,
                _OTHER_WORK_URL,
                _EDITIONS_URL,
                _OTHER_EDITIONS_URL,
            ],
        )
        self.assertEqual(
            [row.cells["creator"] for row in out], [_BOTH, "Invented Author"]
        )

    def test_a_film_override_beside_it_takes_its_own_details(self) -> None:
        film = 'medium = "film"\ntitle = "Metropolis"\ntmdb_id = "9"'
        details_url = "https://api.themoviedb.org/3/movie/9"
        pages = {**_PAGES, details_url: tmdb_result(9, "Metropolis Placeholder")}
        rows = [_book(), input_row("Metropolis", medium="film", line=2)]
        out, _ = self.run_all(rows, overrides_of(_BY_TITLE, film), pages)
        self.assertEqual(out[0].cells["creator"], _BOTH)
        self.assertEqual(out[1].cells["tmdb_title"], "Metropolis Placeholder")
        self.assertEqual(out[0].cells["tmdb_title"], "")

    def test_unresolvable_sends_nothing(self) -> None:
        override = f'medium = "book"\ntitle = "{_TITLE}"\nunresolvable = true'
        out, _ = self.run_all([_book()], overrides_of(override))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(out[0].cells["creator"], "")

    def test_an_isbn_alone_sends_nothing(self) -> None:
        override = f'medium = "book"\ntitle = "{_TITLE}"\nisbn13 = "{ISBN}"'
        out, _ = self.run_all([_book()], overrides_of(override))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.status(out[0]), ("override", ISBN, "", ""))

    def test_the_requests_name_only_the_keys(self) -> None:
        row = _book(creator="Parse Creator")
        self.run_all([row], overrides_of(_WITH_ISBN), _PAGES)
        for url in self.fetch.calls:
            with self.subTest(url=url):
                for leaked in ("Placeholder", "2010", "Parse", ISBN, "read.txt"):
                    self.assertNotIn(leaked, url)
                self.assertNotIn("?", url)


class FailedBookDetailsTest(RowsCase):
    """A work or author request that fails leaves the override row as it was."""

    def _assert_untouched(self, row: resolve_rows.OutputRow) -> None:
        """The row is still an override with its ids, its parse creator and no year."""
        self.assertEqual(self.status(row), ("override", ISBN, _WORK, ""))
        self.assertEqual(row.cells["creator"], "Parse Creator")
        self.assertEqual(row.cells["year"], "")

    def _run(self, pages: dict[str, object]) -> resolve_rows.OutputRow:
        """The one row of a run of an overridden book whose parse named a creator."""
        row = _book(creator="Parse Creator")
        out, review = self.run_all([row], overrides_of(_WITH_ISBN), pages)
        self.assertEqual(kinds(review), [("override-applied", _TITLE)])
        return out[0]

    def test_a_failed_or_odd_work_answer_changes_nothing(self) -> None:
        answers: tuple[object, ...] = (
            http_error(500),
            http_error(404),
            TimeoutError(),
            ["not", "an", "object"],
            ol_work_body(_WORK),
            ol_work_body(_OTHER_WORK, _FIRST),
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self._assert_untouched(self._run({**_PAGES, _WORK_URL: answer}))
                self.assertEqual(self.fetch.calls, [_WORK_URL])

    def test_a_failed_or_nameless_author_changes_nothing(self) -> None:
        answers: tuple[object, ...] = (
            http_error(500),
            TimeoutError(),
            {"key": _SECOND},
            {"name": "   "},
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self._assert_untouched(self._run({**_PAGES, _SECOND_URL: answer}))
                # The first author's failure ends the lookup: no further request.
                self.assertEqual(self.fetch.calls, [_WORK_URL, _SECOND_URL])

    def test_a_failed_author_is_not_asked_again_for_another_work(self) -> None:
        pages = {
            **_PAGES,
            _SECOND_URL: http_error(500),
            _OTHER_WORK_URL: ol_work_body(_OTHER_WORK, _SECOND),
        }
        rows = [_book(), _book("Placeholder Book Two", line=2)]
        out, _ = self.run_all(rows, overrides_of(_BY_TITLE, _OTHER), pages)
        self.assertEqual(
            self.fetch.calls,
            [
                _WORK_URL,
                _SECOND_URL,
                _OTHER_WORK_URL,
                _EDITIONS_URL,
                _OTHER_EDITIONS_URL,
            ],
        )
        self.assertEqual([row.cells["creator"] for row in out], ["", ""])

    def test_an_answer_that_is_not_json_changes_nothing(self) -> None:
        fetch = FakeFetch(b"<html>not json</html>")
        self.assertIsNone(override_book_details(_WORK, _services(fetch)))

    def test_the_lookup_refuses_a_malformed_key_before_any_request(self) -> None:
        fetch = Answering(_PAGES)
        for key in (_TITLE, "/works/OL1W?q=x", _FIRST):
            with self.subTest(key=key), self.assertRaises(ValueError):
                override_book_details(key, _services(fetch))
        self.assertEqual(fetch.calls, [])

    def test_a_malformed_author_key_never_reaches_a_url(self) -> None:
        body = ol_work_body(_WORK, "/authors/OL1A?name=x", _FIRST)
        fetch = Answering({**_PAGES, _WORK_URL: body})
        found = override_book_details(_WORK, _services(fetch))
        assert found is not None
        self.assertEqual(found.creator, "Invented Author")
        self.assertEqual(fetch.calls, [_WORK_URL, _FIRST_URL])


class DirectLookupTest(unittest.TestCase):
    """``override_book_details`` called on its own, as a caller with no memo would."""

    def test_it_gives_a_candidate_with_no_isbn(self) -> None:
        found = override_book_details(_WORK, _services(Answering(_PAGES)))
        assert found is not None
        self.assertEqual(
            (found.id, found.creator, found.year, found.isbn13),
            (_WORK, _BOTH, 1901, ""),
        )

    def test_a_given_memo_answers_an_author_unasked(self) -> None:
        fetch = Answering({_WORK_URL: _PAGES[_WORK_URL]})
        names: dict[str, str | None] = {_FIRST: "Known", _SECOND: "Also Known"}
        found = override_book_details(_WORK, _services(fetch), authors=names)
        assert found is not None
        self.assertEqual(found.creator, "Also Known & Known")
        self.assertEqual(fetch.calls, [_WORK_URL])


class CachedBookDetailsTest(unittest.TestCase):
    """The work and author answers are cached: --cache-only reproduces them."""

    def test_cache_only_reads_a_cached_answer_and_misses_an_uncached_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            _resolve(_client(Answering(_PAGES), cache), overrides_of(_WITH_ISBN))
            for url in (_WORK_URL, _FIRST_URL, _SECOND_URL):
                self.assertTrue((cache / resolve_http.cache_key(url)).is_file())
            again = Answering()
            out = _resolve(
                _client(again, cache, cache_only=True), overrides_of(_WITH_ISBN)
            )
            self.assertEqual(out[0].cells["creator"], _BOTH)
            other = overrides_of(_WITH_ISBN.replace("OL1W", "OL3W"))
            missed = _resolve(_client(again, cache, cache_only=True), other)
            self.assertEqual(missed[0].cells["creator"], "")
            self.assertEqual(missed[0].cells["resolve_status"], "override")
            self.assertEqual(again.calls, [])


def _resolve(
    client: resolve_http.Client, overrides: tuple[ResolveOverride, ...]
) -> tuple[resolve_rows.OutputRow, ...]:
    """The rows of one overridden book row resolved through ``client``."""
    out, _ = resolve_rows.resolve_all(
        [_book()],
        overrides,
        client=client,
        open_library=resolve_http.open_library(None),
        tmdb=resolve_http.tmdb(None),
        directors="none",
        vote_ratio=10.0,
        vote_floor=1,
        research=True,
    )
    return out


def _author_keys(count: int) -> list[str]:
    """``count`` invented author keys, numbered from 1."""
    return [f"/authors/OL{n}A" for n in range(1, count + 1)]


class AuthorCapTest(unittest.TestCase):
    """A work body listing more than ``MAX_WORK_AUTHORS`` authors is unusable.

    Regression: each listed author was one paced request, and a 4 MiB body lists about
    100,000 of them.
    """

    def test_a_work_at_the_author_cap_is_read_whole(self) -> None:
        keys = _author_keys(resolve_books.MAX_WORK_AUTHORS)
        work = resolve_books.parse_work(ol_work_body(_WORK, *keys), _WORK)
        assert work is not None
        self.assertEqual(work.author_keys, tuple(keys))

    def test_a_work_over_the_author_cap_is_unusable(self) -> None:
        keys = _author_keys(resolve_books.MAX_WORK_AUTHORS + 1)
        self.assertIsNone(resolve_books.parse_work(ol_work_body(_WORK, *keys), _WORK))

    def test_a_work_over_the_author_cap_asks_no_author(self) -> None:
        keys = _author_keys(resolve_books.MAX_WORK_AUTHORS + 1)
        fetch = Answering({**_PAGES, _WORK_URL: ol_work_body(_WORK, *keys)})
        self.assertIsNone(override_book_details(_WORK, _services(fetch)))
        self.assertEqual(fetch.calls, [_WORK_URL])


if __name__ == "__main__":
    unittest.main()
