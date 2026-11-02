"""Write retrolog_probe's sample inputs: two import files and a cache answering them.

Everything is invented: every title is a placeholder and every id is made up, so
nothing here comes from a real list, and the sample runs with ``--cache-only``, so it
sends nothing to Letterboxd. ``--out DIR`` receives:

- ``DIR/files/letterboxd-001.csv`` and ``DIR/files/letterboxd-002.csv``: import files
  as retrolog_export writes them, holding one by-title row (no tmdbID) and one film
  watched twice, its two rows in different files;
- ``DIR/cache.json``: a verdict cache answering every id, two of them ``missing``.

README.md beside this file gives the command that runs the tool on them and the
comparison with ``expected/report.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# The tool itself, which puts its own src/ folders and the shared libraries on
# sys.path as it loads.
sys.path.insert(0, str(_HERE.parent))

import retrolog_probe  # noqa: E402, F401 - puts the src/ folders on sys.path
from export_letterboxd import COLUMNS  # noqa: E402 - after the import above
from export_pack import csv_text  # noqa: E402 - after the import above
from probe_cache import Settled, cache_bytes  # noqa: E402 - after the import above

_EXIT_OK = 0
# Each file's rows as (tmdbID, Title, Year, WatchedDate, Rewatch); the other cells
# stay empty. "Placeholder Film Two" is watched twice, once in each file.
_FILES: dict[str, tuple[tuple[str, str, str, str, str], ...]] = {
    "letterboxd-001.csv": (
        ("100001", "Placeholder Film One", "1931", "2004-02-03", ""),
        ("100002", "Placeholder Film Two", "1948", "2004-05-06", ""),
        ("", "Placeholder Film Three", "1952", "2005-01-02", ""),
        ("100004", "Placeholder | Film Four", "1960", "2005-03-04", ""),
    ),
    "letterboxd-002.csv": (
        ("100005", "Placeholder Film Five", "1971", "2006-07-08", ""),
        ("100002", "Placeholder Film Two", "1948", "2007-09-10", "true"),
    ),
}
_CACHE: dict[str, Settled] = {
    "100001": "present",
    "100002": "missing",
    "100004": "missing",
    "100005": "present",
}


def _row(cells: tuple[str, str, str, str, str]) -> tuple[str, ...]:
    """One import row: the given cells in their columns, every other cell empty."""
    named = dict(
        zip(("tmdbID", "Title", "Year", "WatchedDate", "Rewatch"), cells, strict=True)
    )
    return tuple(named.get(column, "") for column in COLUMNS)


def write_inputs(out: Path) -> None:
    """Write the two import files under ``out/files`` and the cache at ``out``."""
    files = out / "files"
    files.mkdir(parents=True, exist_ok=True)
    for name, rows in _FILES.items():
        text = csv_text(COLUMNS, [_row(cells) for cells in rows])
        (files / name).write_text(text, encoding="utf-8", newline="\n")
    (out / "cache.json").write_bytes(cache_bytes(_CACHE))


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Write retrolog_probe's sample import files and verdict cache."
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="The folder to write them into."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the sample inputs; always exit 0."""
    args = build_parser().parse_args(argv)
    write_inputs(args.out)
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
