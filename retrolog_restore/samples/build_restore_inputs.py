"""Write the two synthetic account exports retrolog_restore's sample compares.

Every title is a placeholder (``Placeholder Film One``), every URI is made up, and
every review, tag, and rating is invented, so the sample holds no real account.
``--out DIR`` receives:

- ``DIR/account/backup.zip``: the export "taken before the deletion", as Letterboxd's
  ``.zip``, with ``diary.csv``, ``reviews.csv``, and ``likes/films.csv``;
- ``DIR/account/export-1/``: the export "taken after the deletion", unpacked, whose
  ``diary.csv`` keeps one of the backup's entries and adds one logged since, and whose
  ``likes/films.csv`` still likes one of the restored films.

README.md gives the commands. The rows are chosen so the expected output shows a
review with a comma, quotes, and a line break, several tags in one cell, a rewatch,
an entry with no URI matched by its film and date, a review joined by its URI, a
review of the same film on another date left unjoined, and two films liked in the
backup among four restored, one of them still liked and so not marked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOL = _HERE.parent
# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")
# Then the test fixture builders, which use the standard library only.
sys.path.insert(0, str(_TOOL / "tests" / "fixtures"))

import help_formatter  # noqa: E402 - after the bootstrap above
import restore_fixtures as fx  # noqa: E402 - after the sys.path insert above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_EXIT_OK, _EXIT_USAGE = 0, 2
_URI = "Letterboxd URI"
_WATCHED = "Watched Date"


def _diary(
    name: str, year: str, uri: str, watched: str, **cells: str
) -> dict[str, str]:
    """One diary.csv row of ``name`` (``year``), logged by its ``uri`` on ``watched``."""
    return fx.entry(Name=name, Year=year, **{_URI: uri, _WATCHED: watched}, **cells)


# The backup's diary, oldest logged first. The comment on each says what it shows.
_BACKUP_DIARY = [
    # Kept: the later export still holds this URI, so it is not restored.
    _diary("Placeholder Film One", "1999", "https://boxd.it/sample-1", "2026-01-02"),
    # Restored: a rewatch, rated 4.5, with two tags in one cell; liked.
    _diary(
        "Placeholder Film Two",
        "2001",
        "https://boxd.it/sample-2",
        "2026-01-05",
        Rating="4.5",
        Rewatch="Yes",
        Tags="cinema, late show",
    ),
    # Restored: no rating, no tag, no review; not liked.
    _diary(
        "Placeholder Film Three",
        "1985",
        "https://boxd.it/sample-3",
        "2026-01-06",
        Rating="",
    ),
    # Restored: no URI, so matched by film and date, which the later export lacks.
    _diary("Placeholder Film Four", "2010", "", "2026-01-07", Rating="2"),
    # Restored: liked, and still liked, with a one-word tag and a review.
    _diary(
        "Placeholder Film Five",
        "1974",
        "https://boxd.it/sample-5",
        "2026-01-08",
        Rating="5",
        Tags="festival",
    ),
]
# The backup's reviews: two restored entries', the kept entry's, and one of Film
# Two on an earlier date, which is a different watch and is not joined. Film Five's
# carries its diary entry's URI, so it joins by that.
_BACKUP_REVIEWS = [
    fx.entry(Name="Placeholder Film One", Year="1999", Review="Kept, not restored."),
    fx.entry(
        Name="Placeholder Film Two",
        Year="2001",
        Review='Better the second time, "much" better.\nA second paragraph.',
        **{_WATCHED: "2026-01-05"},
    ),
    fx.entry(
        Name="Placeholder Film Two",
        Year="2001",
        Review="The first watch's review.",
        **{_WATCHED: "2020-06-01"},
    ),
    fx.entry(
        Name="Placeholder Film Five",
        Year="1974",
        Review="Short and sharp.",
        **{_URI: "https://boxd.it/sample-5", _WATCHED: "2026-01-08"},
    ),
]
# The backup's liked films: two restored and the kept one, which restore.md omits.
_BACKUP_LIKES = [
    fx.entry(Name="Placeholder Film One", Year="1999"),
    fx.entry(Name="Placeholder Film Two", Year="2001"),
    fx.entry(Name="Placeholder Film Five", Year="1974"),
]
# The later export: the kept entry, and one logged after the deletion, which the
# backup lacks and the restore ignores. Deleting Film Five's entry left its heart,
# so it is still liked and restore.md does not mark it.
_CURRENT_DIARY = [
    _BACKUP_DIARY[0],
    _diary("Placeholder Film Six", "2020", "https://boxd.it/sample-6", "2026-02-10"),
]
_CURRENT_LIKES = [_BACKUP_LIKES[0], _BACKUP_LIKES[2]]


def write_inputs(out: Path) -> None:
    """Write ``account/backup.zip`` and ``account/export-1/`` under ``out``."""
    account = out / "account"
    fx.write_zip(
        account / "backup.zip",
        fx.export_files(_BACKUP_DIARY, reviews=_BACKUP_REVIEWS, likes=_BACKUP_LIKES),
    )
    fx.write_folder(
        account / "export-1",
        fx.export_files(_CURRENT_DIARY, reviews=[], likes=_CURRENT_LIKES),
    )


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Write retrolog_restore's synthetic sample exports.",
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Directory to write the exports into; it must be missing or empty.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the exports and return the exit code: 0 written, 2 ``--out`` not usable."""
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
