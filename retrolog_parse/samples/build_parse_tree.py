"""Write the small synthetic source tree that retrolog_parse's sample output comes from.

Every file is invented, so the sample holds no real list. Run it, parse the tree with
``rules.example.toml`` beside this file, and compare the result with ``expected/``
(README.md gives the commands). The tree holds every line shape the parser reads and
every way a line is dated:

- ``21-22/``, a two-year folder: a file whose name holds an earlier word, a file a
  ``[[terms]]`` block dates, and a file with neither, whose rows stay undated;
- ``23/``, a one-year folder: sections, a split code, a rewatch, a comment, a progress
  note a later file finishes, an override, an unknown code, an unparsed line and a Word
  file whose first line holds the legend and an entry, and whose second holds two;
- a file at the top whose name states its year.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")
# Then the test fixture builders.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests" / "fixtures"))

import help_formatter  # noqa: E402 - after the bootstrap above
import parse_fixtures  # noqa: E402 - import follows the sys.path insert above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_EXIT_OK, _EXIT_USAGE = 0, 2

# Plain-text files: relative path -> lines.
_TEXT_FILES: dict[str, tuple[str, ...]] = {
    # "autumn" is an earlier word and a season: October of the folder's first year.
    "21-22/autumn books.txt": ("Book One - c", "Book Two - a"),
    # Dated by the [[terms]] block in rules.example.toml, whose expect is line 1.
    "21-22/movies.txt": ("Film One - b", "Film Two - d", "----", "Film Three - c"),
    # No earlier or later word and no term block: these rows stay undated.
    "21-22/more books.txt": ("Book Undated - b",),
    # A season word only: April of the folder's year.
    "23/spring books.txt": ("Long Book - 120/300",),
    "23/books.txt": (
        "My books",
        "",
        "work",
        "Book Three - c",
        "Book Four - b/c",
        "(Book Five) - d",
        "Book Six - a (a gift)",
        "home",
        "Long Book - d",
        "Odd Book - q",
        "Other Book - q",
        "Book Seven",
        "~~ ~~",
    ),
    # The year is in the file name, so no folder is needed.
    "movies (24).txt": ("Late Film (1999) - c",),
}
# Two arbitrary Wingdings glyphs, as a Word 97 file stores them: a private-use
# character in a symbol-font run. rules.example.toml's [shapes] names both.
_LOW_GLYPH, _HIGH_GLYPH = "\uf06c", "\uf06e"
_SYMBOL = "Wingdings"
# rules.example.toml's four arbitrary colors, keys "1" to "4" in this order.
_KEY_1, _KEY_2, _KEY_3, _KEY_4 = "33A02C", "6A3D9A", "FF7F00", "A6CEE3"
# Line 1: a legend of four marks, then an entry; line 2: two entries.
_WORD_LINES: tuple[tuple[tuple[str, str | None, str | None], ...], ...] = (
    (
        (_LOW_GLYPH, _SYMBOL, _KEY_1),
        (" ", None, None),
        (_LOW_GLYPH, _SYMBOL, _KEY_2),
        (" ", None, None),
        (_HIGH_GLYPH, _SYMBOL, _KEY_3),
        (" ", None, None),
        (_HIGH_GLYPH, _SYMBOL, _KEY_4),
        (" ", None, None),
        (_HIGH_GLYPH, _SYMBOL, _KEY_3),
        (" Film Four", None, None),
    ),
    (
        (_HIGH_GLYPH, _SYMBOL, _KEY_4),
        (" Film Five ", None, None),
        (_LOW_GLYPH, _SYMBOL, _KEY_2),
        (" Film Six", None, None),
    ),
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Write retrolog_parse's synthetic sample source tree.",
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Directory to write the tree into; it must be missing or empty.",
    )
    return parser


def write_tree(out: Path) -> None:
    """Write the sample files under ``out``."""
    for rel_path, lines in _TEXT_FILES.items():
        path = out / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, so every platform writes the same "\n" line endings.
        path.write_bytes("".join(f"{line}\n" for line in lines).encode("utf-8"))
    (out / "23" / "movies.docx").write_bytes(parse_fixtures.docx_bytes(_WORD_LINES))


def main(argv: list[str] | None = None) -> int:
    """Write the tree and return the exit code: 0 written, 2 ``--out`` not usable."""
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        print(f"{parser.prog}: --out must be missing or empty", file=sys.stderr)
        return _EXIT_USAGE
    write_tree(args.out)
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
