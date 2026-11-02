"""Write the synthetic parse folder, overrides file and canned cache resolve's sample uses.

Every title is a placeholder (``Placeholder Book Two``) or a public-domain work
(``Moby Dick``, ``Metropolis``), and every id and ISBN is made up, so the sample holds
no real list and no real service answer. ``--out DIR`` receives:

- ``DIR/parsed/``: two CSV files in parse's columns, one per medium;
- ``DIR/overrides.toml``: ``overrides.example.toml`` beside this file, then two
  entries of the sample's own: one no row matches, and one naming another title for
  its line;
- ``DIR/out/``: the tool's marker and ``cache/``, a canned answer for every request
  the sample run makes, so ``--cache-only`` reproduces ``expected/`` with no network
  and no token. One title is left out of the cache on purpose: it shows "failed".

README.md gives the commands. The rows are chosen so the expected output shows every
resolve status and every review kind at least once.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOL = _HERE.parent
# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter", "bounded_fetch")
# Then the tool's own modules, parse's and the survey's, and the test fixture
# builders.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TOOL / "tests" / "fixtures"),
]

import help_formatter  # noqa: E402 - after the bootstrap above
import resolve_books  # noqa: E402 - after the sys.path inserts above
import resolve_films  # noqa: E402 - after the sys.path inserts above
import resolve_fixtures as fx  # noqa: E402 - after the sys.path inserts above
import resolve_http  # noqa: E402 - after the sys.path inserts above
import stdio_utf8  # noqa: E402 - after the bootstrap above
import survey_guard  # noqa: E402 - after the sys.path inserts above

_EXIT_OK, _EXIT_USAGE = 0, 2
_TOOL_NAME = "retrolog_resolve"
_YEAR = 2010
_READ, _SEEN = f"{_YEAR}/read.txt", f"{_YEAR}/seen.txt"
# Made-up ISBN-13s with valid check digits: the 978 prefix and then zeros.
_ISBN_A, _ISBN_B = "9780000000002", "9780000000019"
_STEPS: tuple[resolve_books.Step, ...] = (
    "edition-by-year",
    "edition-after",
    "work-only",
)

# (title, line) per row, in file order.
_BOOKS = (
    ("Moby Dick", 1),  # override-applied: a title-only override in the example file
    ("Placeholder Book One", 2),  # override-applied: unresolvable
    ("Placeholder Book Two", 3),  # resolved at the first step, with an ISBN
    ("Placeholder Book Three", 4),  # resolved at the last step, no ISBN: no-isbn
    ("Placeholder Book Four", 5),  # two exact titles: ambiguous
    ("Placeholder Book Five", 6),  # nothing at any step: not-found
    ("Placeholder Book Six", 7),  # left out of the cache: failed
)
_FILMS = (
    ("Metropolis", 3),  # override-applied: a title-only override in the example file
    ("Placeholder Film One", 7),  # override-applied: the example's line override
    ("Placeholder Film One", 9),  # the same title on another line: still searched
    ("Placeholder Film Two", 10),  # two exact titles: ambiguous, directors shown
    ("Placeholder Film Three", 11),  # an empty search: not-found
    ("Placeholder Film Five", 12),  # its line's override names another title
    ("Placeholder Film Six", 13),  # two exact titles, 30x the votes: most-voted
    ("Placeholder Story II", 14),  # only "Placeholder Story 2" matches: variant
    ("The Placeholder Hour", 15),  # found by searching again unarticled: re-searched
    (
        "Placeholder Film Seven",
        16,
    ),  # one exact title, undated and unvoted: suspect-exact
)
# The sample's own overrides, after the example file's four.
_SAMPLE_OVERRIDES = """
# Sample only: no row has this title, so it is override-unused.
[[overrides]]
medium = "book"
title = "Placeholder Book Nine"
unresolvable = true

# Sample only: line 12 holds another title, so it is override-mismatch.
[[overrides]]
medium = "film"
title = "Placeholder Film Four"
file = "2010/seen.txt"
line = 12
tmdb_id = "1000009"
"""


def _parsed_csvs() -> dict[str, str]:
    """The two parse CSV files, by name."""
    books = [
        fx.parse_row(
            title=title, date=f"{_YEAR}-03-04", source_file=_READ, source_line=str(line)
        )
        for title, line in _BOOKS
    ]
    films = [
        fx.parse_row(
            medium="film",
            title=title,
            date=f"{_YEAR}-05-06",
            source_file=_SEEN,
            source_line=str(line),
            rating_raw="y",
        )
        for title, line in _FILMS
    ]
    return {
        f"book-{_YEAR}.csv": fx.csv_text(books),
        f"film-{_YEAR}.csv": fx.csv_text(films),
    }


def _work(title: str, key: str, author: str, *isbns: str) -> dict[str, object]:
    """One Open Library work, its first edition English with ``isbns``."""
    editions = (fx.ol_edition(*isbns),) if isbns else ()
    return fx.ol_work(
        key=key, title=title, authors=(author,), year=1901, editions=editions
    )


def _book_answers() -> dict[str, object]:
    """Every Open Library URL the sample asks, with its canned answer."""

    def url(title: str, step: int) -> str:
        return resolve_books.search_url(title, _YEAR, _STEPS[step])

    empty = fx.ol_payload()
    four = "Placeholder Book Four"
    return {
        url("Placeholder Book Two", 0): fx.ol_payload(
            _work("Placeholder Book Two", "/works/OL1000011W", "Author Two", _ISBN_A)
        ),
        url("Placeholder Book Three", 0): empty,
        url("Placeholder Book Three", 1): empty,
        url("Placeholder Book Three", 2): fx.ol_payload(
            _work("Placeholder Book Three", "/works/OL1000012W", "Author Three")
        ),
        url(four, 0): empty,
        url(four, 1): empty,
        url(four, 2): fx.ol_payload(
            _work(four, "/works/OL1000013W", "Author Four"),
            _work(four, "/works/OL1000014W", "Author Five", _ISBN_B),
        ),
        **{url("Placeholder Book Five", step): empty for step in range(len(_STEPS))},
    }


def _film_answers() -> dict[str, object]:
    """Every TMDb URL the sample asks, with its canned answer."""

    def search(*results: object) -> object:
        return fx.tmdb_search_payload(*results)

    def credits(name: str) -> object:
        return fx.tmdb_credits_payload(fx.tmdb_crew(name))

    return {
        # The details of the two override ids the sample applies: each row takes its
        # TMDb titles from them, and its empty year from the release date.
        resolve_films.details_url("1000001"): fx.tmdb_result(
            1000001, "Metropolis", release_date="1927-01-10"
        ),
        resolve_films.details_url("1000002"): fx.tmdb_result(
            1000002,
            "Placeholder Film One",
            original_title="Film Un Factice",
            release_date="1988-04-05",
        ),
        resolve_films.search_url("Placeholder Film One"): search(
            fx.tmdb_result(
                1000003,
                "Placeholder Film One",
                release_date="2001-02-03",
                vote_count=320,
            )
        ),
        resolve_films.search_url("Placeholder Film Two"): search(
            fx.tmdb_result(1000004, "Placeholder Film Two", release_date="1999-01-01"),
            fx.tmdb_result(1000005, "Placeholder Film Two", release_date="2008-01-01"),
            fx.tmdb_result(1000006, "Placeholder Film Two", release_date="2015-01-01"),
        ),
        resolve_films.credits_url("1000004"): credits("Director Four"),
        resolve_films.credits_url("1000005"): credits("Director Five"),
        resolve_films.search_url("Placeholder Film Three"): search(),
        resolve_films.search_url("Placeholder Film Five"): search(
            fx.tmdb_result(
                1000007,
                "Placeholder Film Five",
                original_title="Film Cinq",
                release_date="2005-06-07",
                vote_count=75,
            )
        ),
        # Two exact titles, the first with thirty times the second's votes, so the
        # most-votes rule resolves it, listing both with their directors.
        resolve_films.search_url("Placeholder Film Six"): search(
            fx.tmdb_result(
                1000010,
                "Placeholder Film Six",
                release_date="1994-03-04",
                vote_count=1200,
            ),
            fx.tmdb_result(
                1000011,
                "Placeholder Film Six",
                release_date="2003-08-09",
                vote_count=40,
            ),
        ),
        resolve_films.credits_url("1000010"): credits("Director Six"),
        resolve_films.credits_url("1000011"): credits("Director Seven"),
        # No exact title; under the sequel-numeral variant the row's "II" and
        # the second candidate's "2" meet, and the first candidate still differs.
        resolve_films.search_url("Placeholder Story II"): search(
            fx.tmdb_result(
                1000012,
                "Placeholder Story",
                release_date="2001-05-06",
                vote_count=1,
            ),
            fx.tmdb_result(
                1000013,
                "Placeholder Story 2",
                release_date="2006-07-08",
                vote_count=85,
            ),
        ),
        resolve_films.credits_url("1000013"): credits("Director Eight"),
        # No exact title, and no variant meets "Placeholder Hourglass"; the second
        # search, without the leading article, finds one match.
        resolve_films.search_url("The Placeholder Hour"): search(
            fx.tmdb_result(1000014, "Placeholder Hourglass", release_date="1998-02-03")
        ),
        resolve_films.search_url("Placeholder Hour"): search(
            fx.tmdb_result(1000014, "Placeholder Hourglass", release_date="1998-02-03"),
            fx.tmdb_result(1000015, "Placeholder Hour", release_date="2002-09-10"),
        ),
        resolve_films.credits_url("1000015"): credits("Director Nine"),
        # One exact title, but no release date and no votes: held for review, with its
        # director listed like an ambiguous film's.
        resolve_films.search_url("Placeholder Film Seven"): search(
            fx.tmdb_result(1000016, "Placeholder Film Seven", vote_count=0)
        ),
        resolve_films.credits_url("1000016"): credits("Director Ten"),
    }


def _write_cache(cache: Path, answers: Mapping[str, object]) -> None:
    """Each answer as the file the tool's cache keeps for its URL."""
    cache.mkdir(parents=True, exist_ok=True)
    for url, answer in answers.items():
        text = json.dumps(answer, sort_keys=True, indent=1) + "\n"
        (cache / resolve_http.cache_key(url)).write_bytes(text.encode("utf-8"))


def write_inputs(out: Path) -> None:
    """Write ``parsed/``, ``overrides.toml`` and ``out/`` (marker and cache) under ``out``."""
    fx.write_parsed(out / "parsed", _parsed_csvs())
    example = (_HERE / "overrides.example.toml").read_bytes()
    (out / "overrides.toml").write_bytes(example + _SAMPLE_OVERRIDES.encode("utf-8"))
    survey_guard.prepare_out_dir(out / "out", tool=_TOOL_NAME)
    _write_cache(out / "out" / "cache", {**_book_answers(), **_film_answers()})


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Write retrolog_resolve's synthetic sample inputs.",
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Directory to write the inputs into; it must be missing or empty.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the inputs and return the exit code: 0 written, 2 ``--out`` not usable."""
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        print(f"{parser.prog}: --out must be missing or empty", file=sys.stderr)
        return _EXIT_USAGE
    write_inputs(args.out)
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
