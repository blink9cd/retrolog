"""Write the synthetic resolve folder, account export, and config of export's sample.

Every title is a placeholder (``Placeholder Film One``, or ``???`` for a title with no
letter or digit), every director's name is a placeholder too, and every id is made up,
so the sample holds no real list and no real account, and no real film is paired
with an id that is not its own. ``--out DIR`` receives:

- ``DIR/resolved/``: three film CSV files in resolve's columns, one of them undated,
  holding every resolve status;
- ``DIR/account/``: an unpacked account export, as invented as the rest, whose
  ``watched.csv``, ``diary.csv``, and ``ratings.csv`` put one film in each of three
  account classes: one only watched, sent in full; one the owner logged, sent
  without stars, less its row on its diary entry's date; and one an earlier import
  tagged ``retrolog``, not sent;
- ``DIR/export.toml``: ``export.example.toml`` beside this file, unchanged. Its cutoff
  names one of the rows, so it and the row after it are cut.

README.md gives the commands. The rows are chosen so the expected output shows every
review kind at least once and, with ``--rows-per-file 4``, a film's two rows kept
together in the first of two files.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOL = _HERE.parent
# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")
# Then the tool's own modules, resolve's, parse's, and the survey's, then the test
# fixture builders.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TOOL / "tests" / "fixtures"),
]

import export_fixtures as fx  # noqa: E402 - after the sys.path inserts above
import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_EXIT_OK, _EXIT_USAGE = 0, 2
_SEEN, _LATER, _UNDATED = "2010/seen.txt", "2019/films.txt", "misc/films.txt"


def _row(title: str, source_file: str, line: int, **cells: str) -> dict[str, str]:
    """One resolve row of ``title``; every cell not given is empty or resolve's default.

    A resolved row with a tmdbID carries ``title`` as both its TMDb titles, as resolve
    writes a film its exact-title rule settled.
    """
    defaults = {
        "creator": "",
        "year": "",
        "rating_raw": "",
        "tmdb_id": "",
        "resolve_status": "resolved",
    }
    if cells.get("tmdb_id") and cells.get("resolve_status", "resolved") == "resolved":
        defaults |= {"tmdb_title": title, "tmdb_original_title": title}
    return fx.resolved_row(
        title=title,
        source_file=source_file,
        source_line=str(line),
        **(defaults | cells),
    )


# Each CSV's rows, in file order. The comment on each says what the row shows.
_FILES: Mapping[str, list[dict[str, str]]] = {
    "film-2010.csv": [
        # override, by tmdbID, with a comment: the review note goes above it.
        _row(
            "Placeholder Film Eleven",
            _SEEN,
            3,
            date="2010-05-06",
            year="1927",
            creator="Director Three",
            rating_raw="c",
            comment="Seen with a live score.",
            resolve_status="override",
            tmdb_id="1000001",
        ),
        # resolved, a split rating sharing another key's stars, and two directors
        # joined by a comma.
        _row(
            "Placeholder Film One",
            _SEEN,
            7,
            date="2010-05-06",
            year="2001",
            creator="Director One & Director Two",
            rating_raw="b/c",
            tmdb_id="1000002",
        ),
        # ambiguous: held back, an error.
        _row(
            "Placeholder Film Two",
            _SEEN,
            10,
            date="2010-06-07",
            rating_raw="b",
            resolve_status="ambiguous",
        ),
        # not-found and unrated: sent by title, with no rating.
        _row(
            "Placeholder Film Three",
            _SEEN,
            11,
            date="2010-06-07",
            resolve_status="not-found",
        ),
        # failed: held back, an error.
        _row(
            "Placeholder Film Four",
            _SEEN,
            12,
            date="2010-07-08",
            rating_raw="a",
            resolve_status="failed",
        ),
        # resolved, and on the account as watched only: sent in full, with its stars.
        _row(
            "Placeholder Film Five",
            _SEEN,
            13,
            date="2010-07-08",
            year="1995",
            rating_raw="b",
            tmdb_id="1000005",
        ),
        # an override marking it unresolvable: sent by title and year.
        _row(
            "Placeholder Film Seven",
            _SEEN,
            14,
            date="2010-08-09",
            year="1960",
            rating_raw="a",
            resolve_status="override",
        ),
        # One title split between a tmdbID film and a title-only film: both held, as
        # errors, until an override settles the second.
        _row(
            "Placeholder Film Eight",
            _SEEN,
            15,
            date="2010-09-10",
            year="1980",
            rating_raw="b",
            tmdb_id="1000008",
        ),
        _row(
            "Placeholder Film Eight",
            _SEEN,
            16,
            date="2010-09-10",
            rating_raw="b",
            resolve_status="not-found",
        ),
        # a title with no letter or digit: no account entry could match it, so it is
        # held, an error.
        _row("???", _SEEN, 17, date="2010-09-10", resolve_status="not-found"),
        # logged by the owner on the account, untagged, with a diary entry on this
        # date: this row is not sent (same-date), and the film's other row, in
        # film-2019.csv, is sent without stars.
        _row(
            "Placeholder Film Twelve",
            _SEEN,
            18,
            date="2010-10-11",
            year="1974",
            rating_raw="c",
            tmdb_id="1000012",
        ),
        # imported before: its diary entry carries the "retrolog" tag, so not sent.
        _row(
            "Placeholder Film Thirteen",
            _SEEN,
            19,
            date="2010-10-11",
            year="1988",
            rating_raw="b",
            tmdb_id="1000013",
        ),
    ],
    "film-2019.csv": [
        # Placeholder Film Eleven again, a rewatch before the cutoff: a second row of
        # its film.
        _row(
            "Placeholder Film Eleven",
            _LATER,
            3,
            date="2019-02-01",
            year="1927",
            creator="Director Three",
            rating_raw="c",
            rewatch="true",
            resolve_status="override",
            tmdb_id="1000001",
        ),
        # Placeholder Film Twelve again: sent, but with an empty Rating, since the
        # owner rated it on the account. Its rating tag and the review's rating
        # sentence still say what the lists did.
        _row(
            "Placeholder Film Twelve",
            _LATER,
            5,
            date="2019-03-04",
            year="1974",
            rating_raw="c",
            comment="Better the second time.",
            rewatch="true",
            tmdb_id="1000012",
        ),
        # The row the example config's cutoff names: cut, with every row after it.
        _row(
            "Placeholder Film Nine",
            _LATER,
            12,
            date="2019-06-01",
            year="2018",
            rating_raw="b",
            tmdb_id="1000009",
        ),
        _row(
            "Placeholder Film Ten",
            _LATER,
            15,
            date="2019-08-01",
            year="2019",
            rating_raw="c",
            tmdb_id="1000010",
        ),
    ],
    "film-undated.csv": [
        # undated: never cut, sent with no watched date. Its Directors cell starts
        # with "@", which a spreadsheet reads as a formula: listed, left as it is.
        _row(
            "Placeholder Film Six",
            _UNDATED,
            2,
            date="",
            year="2001",
            creator="@Placeholder Director",
            rating_raw="c",
            tmdb_id="1000006",
        ),
    ],
}
# The account export's files: watched.csv names every film the account holds,
# ratings.csv the two the owner rated, and diary.csv one entry the owner logged and
# one an earlier import tagged.
_WATCHED = [
    ("Placeholder Film Five", "1995"),
    ("Placeholder Film Twelve", "1974"),
    ("Placeholder Film Thirteen", "1988"),
]
_RATED = [("Placeholder Film Twelve", "1974"), ("Placeholder Film Thirteen", "1988")]
_DIARY = [
    ("Placeholder Film Twelve", "1974", "", "2010-10-11"),
    ("Placeholder Film Thirteen", "1988", "retrolog, retrolog-mid", "2010-10-11"),
]


def write_inputs(out: Path) -> None:
    """Write ``resolved/``, ``account/``, and ``export.toml`` under ``out``."""
    fx.write_resolved(
        out / "resolved", {name: fx.csv_text(rows) for name, rows in _FILES.items()}
    )
    fx.write_account_folder(
        out / "account",
        {
            "watched.csv": fx.account_csv(_WATCHED),
            "diary.csv": fx.logged_csv(_DIARY),
            "ratings.csv": fx.account_csv(_RATED),
        },
    )
    shutil.copyfile(_HERE / "export.example.toml", out / "export.toml")


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Write retrolog_export's synthetic sample inputs.",
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
