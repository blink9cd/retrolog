"""Tests for the retrolog_resolve command line's most-voted and variant matches.

``--vote-ratio`` reaches the most-votes rule, the summary counts the most-voted and
variant review kinds in ``REVIEW_KINDS`` order, and ``review.json`` and ``review.md``
carry the candidates' votes; ``--research`` reaches the re-search. As in
``test_resolve_cli``, ``main`` runs end to end on a temporary parse folder with a fetch
answering only the URLs listed, and ``HOME`` points at a temporary folder. Titles are
invented.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL))
import resolve_books  # noqa: E402 - after the sys.path inserts above
import resolve_films  # noqa: E402 - after the sys.path inserts above
import retrolog_resolve  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    HTTP_TOKEN,
    Answering,
    csv_text,
    ol_edition,
    ol_payload,
    ol_work,
    parse_row,
    tmdb_result,
    tmdb_search_payload,
    write_parsed,
)

EXIT_OK, EXIT_USAGE = 0, 2
_NOT_A_REPO = 128
_TOKEN_ENV, _CONTACT_ENV = "RETROLOG_TMDB_TOKEN", "RETROLOG_CONTACT"
_COLLIDING, _SEQUEL, _MISSING = "Placeholder", "Placeholder II", "Placeholder Lost"
_BOOK = "Placeholder Book One"
_STEPS: Sequence[resolve_books.Step] = ("edition-by-year", "edition-after", "work-only")
_FILM_PAGES: dict[str, object] = {
    resolve_films.search_url(_COLLIDING): tmdb_search_payload(
        tmdb_result(1, _COLLIDING, vote_count=10),
        tmdb_result(2, _COLLIDING, vote_count=500),
    ),
    resolve_films.search_url(_SEQUEL): tmdb_search_payload(
        tmdb_result(7, "Placeholder 2", vote_count=3)
    ),
    resolve_films.search_url(_MISSING): tmdb_search_payload(),
}
# A book whose every step finds its exact title with no ISBN: a no-isbn entry.
_BOOK_PAGES: dict[str, object] = {
    resolve_books.search_url(_BOOK, 2010, step): ol_payload(
        ol_work(title=_BOOK, editions=(ol_edition("not an isbn"),))
    )
    for step in _STEPS
}


def _not_a_repo(argv: Sequence[str], cwd: Path, timeout: float) -> int:
    """A git stand-in answering every question as outside any repository."""
    return _NOT_A_REPO


class _VoteRatioCase(unittest.TestCase):
    """A parse folder of three invented film rows, and an --out beside it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        films = [
            parse_row(medium="film", title=title, source_file="10/seen.txt")
            for title in (_COLLIDING, _SEQUEL, _MISSING)
        ]
        self.files = {"film-2010.csv": csv_text(films)}
        self.pages: dict[str, object] = dict(_FILM_PAGES)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def main(self, *extra: str) -> int:
        """Run the CLI on ``self.files``, answering ``self.pages``, with a token set."""
        parsed = write_parsed(self.base / "parsed", self.files)
        self.out = self.base / "out"
        argv = ["--parsed", str(parsed), "--out", str(self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        home = str(self.base / "home")
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ))
            os.environ.pop(_CONTACT_ENV, None)
            os.environ.update(
                {_TOKEN_ENV: HTTP_TOKEN, "HOME": home, "USERPROFILE": home}
            )
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            code = retrolog_resolve.main(
                argv,
                fetch=Answering(self.pages),
                run_git=_not_a_repo,
                sleep=lambda seconds: None,
            )
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def read(self, name: str) -> str:
        """One written output file's text."""
        return (self.out / name).read_text(encoding="utf-8")


class LoosenedRunTest(_VoteRatioCase):
    def test_both_matches_resolve_and_the_run_exits_0(self) -> None:
        self.assertEqual(self.main("--directors", "none"), EXIT_OK, self.stderr)
        written = self.read("film-2010.csv")
        self.assertIn(f"{_COLLIDING},", written)
        self.assertIn(",resolved,,,2,Placeholder,Placeholder\n", written)
        self.assertIn(",resolved,,,7,Placeholder 2,Placeholder 2\n", written)
        self.assertEqual(self.stderr, "")

    def test_the_summary_lists_the_kinds_in_review_kinds_order(self) -> None:
        self.files["book-2010.csv"] = csv_text([parse_row(title=_BOOK)])
        self.pages.update(_BOOK_PAGES)
        self.assertEqual(self.main("--directors", "none"), EXIT_OK, self.stderr)
        self.assertEqual(
            [line for line in self.stdout.splitlines() if line.startswith("review ")],
            [
                "review not-found: 1",
                "review most-voted: 1",
                "review variant: 1",
                "review no-isbn: 1",
            ],
        )

    def test_review_json_and_review_md_carry_the_votes(self) -> None:
        self.main("--directors", "none")
        document = json.loads(self.read("review.json"))
        voted = [e for e in document["entries"] if e["kind"] == "most-voted"]
        self.assertEqual([c["votes"] for c in voted[0]["candidates"]], [500, 10])
        markdown = self.read("review.md")
        self.assertIn("## most-voted (1)", markdown)
        self.assertIn("## variant (1)", markdown)
        self.assertIn("2, 500 votes; ", markdown)

    def test_a_ratio_of_zero_keeps_the_collision_ambiguous(self) -> None:
        self.main("--directors", "none", "--vote-ratio", "0")
        self.assertIn(",ambiguous,,,,,\n", self.read("film-2010.csv"))
        self.assertIn("review ambiguous: 1", self.stdout.splitlines())

    def test_a_ratio_above_the_votes_keeps_it_ambiguous(self) -> None:
        self.main("--directors", "none", "--vote-ratio", "50.5")
        self.assertIn("review ambiguous: 1", self.stdout.splitlines())


class VoteRatioOptionTest(unittest.TestCase):
    def test_a_ratio_neither_zero_nor_finite_above_one_is_refused(self) -> None:
        for value in ("1", "0.5", "-1", "nan", "inf", "-inf", "x"):
            with self.subTest(value=value):
                # The "=" form: argparse would read a bare "-inf" as an option.
                argv = ["--parsed", "in", "--out", "out", f"--vote-ratio={value}"]
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as raised:
                        retrolog_resolve.build_parser().parse_args(argv)
                self.assertEqual(raised.exception.code, EXIT_USAGE)
                self.assertIn("--vote-ratio", stderr.getvalue())
                # Every refusal says why, and none shows the private type's name.
                self.assertIn("0 or a finite number above 1", stderr.getvalue())
                self.assertNotIn("_vote_ratio", stderr.getvalue())

    def test_help_shows_the_live_default(self) -> None:
        parser = retrolog_resolve.build_parser()
        action = next(a for a in parser._actions if "--vote-ratio" in a.option_strings)
        self.assertEqual(action.default, 10.0)
        self.assertIn(
            f"(default: {action.default})", " ".join(parser.format_help().split())
        )


_ARTICLED, _BARE = "The Placeholder Hour", "Placeholder Hour"


class ResearchRunTest(_VoteRatioCase):
    """The re-search end to end: on by default, and ``--no-research`` sends no second search."""

    def setUp(self) -> None:
        super().setUp()
        row = parse_row(medium="film", title=_ARTICLED, source_file="10/seen.txt")
        self.files = {"film-2010.csv": csv_text([row])}
        self.pages = {
            resolve_films.search_url(_ARTICLED): tmdb_search_payload(),
            resolve_films.search_url(_BARE): tmdb_search_payload(
                tmdb_result(6, _BARE, vote_count=3)
            ),
        }

    def test_the_re_search_is_on_by_default(self) -> None:
        self.assertEqual(self.main("--directors", "none"), EXIT_OK, self.stderr)
        self.assertIn(f",resolved,,,6,{_BARE},{_BARE}\n", self.read("film-2010.csv"))
        self.assertIn("review re-searched: 1", self.stdout.splitlines())
        self.assertIn("## re-searched (1)", self.read("review.md"))

    def test_no_research_sends_no_second_search(self) -> None:
        # An unlisted URL fails the test, so a second search could not pass unseen.
        del self.pages[resolve_films.search_url(_BARE)]
        self.assertEqual(
            self.main("--directors", "none", "--no-research"), EXIT_OK, self.stderr
        )
        self.assertIn(",not-found,,,,,\n", self.read("film-2010.csv"))
        self.assertIn("review not-found: 1", self.stdout.splitlines())

    def test_help_shows_the_live_default_and_what_it_sends(self) -> None:
        parser = retrolog_resolve.build_parser()
        action = next(a for a in parser._actions if "--research" in a.option_strings)
        self.assertIs(action.default, True)
        text = " ".join(parser.format_help().split())
        self.assertIn("--research, --no-research", text)
        self.assertIn(f"(default: {action.default})", text)
        self.assertIn("at most one extra TMDb search per film", text)


if __name__ == "__main__":
    unittest.main()
