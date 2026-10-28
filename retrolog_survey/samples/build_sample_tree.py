"""Write the small synthetic source tree that retrolog_survey's sample report comes from.

Every file is invented, through the same builders the tests use, so the sample holds
no real list. Run it, survey the tree with ``--no-convert``, and compare the result
with ``expected_report.json`` beside this file (README.md gives the commands).
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

import build_fixtures  # noqa: E402 - import follows the sys.path insert above
import help_formatter  # noqa: E402 - after the bootstrap above
import stdio_utf8  # noqa: E402 - after the bootstrap above

_EXIT_OK, _EXIT_USAGE = 0, 2

# Old film notes: classic Mac OS text, carriage returns only. The en dash is what
# tells mac_roman from cp1252 here.
_FILMS_TEXT = "Film A (1999) g\nFilm B \N{EN DASH} seen twice\n\nFilm C b\n"
# Book notes: Windows text, CRLF line endings, curly quotes.
_BOOKS_TEXT = (
    "Book A \N{LEFT SINGLE QUOTATION MARK}worth it\N{RIGHT SINGLE QUOTATION MARK}\n"
    "Book B \N{EN DASH} a\n"
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Write retrolog_survey's synthetic sample source tree.",
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
    films, books = out / "films", out / "books"
    films.mkdir(parents=True)
    books.mkdir()
    build_fixtures.write_text_fixture(
        films / "notes.txt", _FILMS_TEXT, encoding="mac_roman", newline="\r"
    )
    build_fixtures.make_rated_docx(films / "rated.docx")
    # Counted but not read under --no-convert, so its content does not matter.
    (films / "older.doc").write_bytes(b"A stand-in for an old Word file.\n")
    (films / ".DS_Store").write_bytes(b"\x00")
    build_fixtures.write_text_fixture(
        books / "notes.txt", _BOOKS_TEXT, encoding="cp1252", newline="\r\n"
    )
    (books / "cover.jpg").write_bytes(b"not read: any other suffix is only counted\n")


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
