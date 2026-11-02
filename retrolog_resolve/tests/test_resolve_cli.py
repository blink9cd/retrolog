"""Tests for the retrolog_resolve command line: what it reads, writes and returns.

Each test runs ``main`` end to end on a temporary parse folder, with a fetch that
answers only the URLs the test lists, a git stand-in and a sleep that does not wait.
The environment variables the tool reads are cleared first and set per test, and
``HOME`` points at a temporary folder, so neither a token in the developer's own shell
nor their own token file can ever reach a test.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL / "samples"))
sys.path.insert(0, str(_TOOL))
import build_resolve_inputs  # noqa: E402 - after the sys.path inserts above
import resolve_books  # noqa: E402 - retrolog_resolve puts its folder on sys.path
import resolve_films  # noqa: E402 - retrolog_resolve puts its folder on sys.path
import retrolog_resolve  # noqa: E402 - after the sys.path inserts above
import survey_guard  # noqa: E402 - retrolog_resolve puts its folder on sys.path
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    HTTP_CONTACT,
    HTTP_TOKEN,
    Answering,
    csv_text,
    http_error,
    ol_edition,
    ol_payload,
    ol_work,
    override_toml,
    parse_row,
    tmdb_result,
    tmdb_search_payload,
    write_parsed,
)

EXIT_OK, EXIT_ERRORS, EXIT_USAGE = 0, 1, 2
# git's exit code outside any repository, and inside one.
_NOT_A_REPO, _IN_A_REPO = 128, 0
_MARKER = ".retrolog_resolve"
_ISBN = "9780306406157"
_TOKEN_ENV, _CONTACT_ENV = "RETROLOG_TMDB_TOKEN", "RETROLOG_CONTACT"
_BOOK_URL = resolve_books.search_url("Moby Dick", 2010, "edition-by-year")
_FILM_URL = resolve_films.search_url("Metropolis")
_PAGES: Mapping[str, object] = {
    _BOOK_URL: ol_payload(
        ol_work(key="/works/OL7W", title="Moby Dick", editions=(ol_edition(_ISBN),))
    ),
    _FILM_URL: tmdb_search_payload(
        tmdb_result(19, "Metropolis", release_date="1927-01-10", vote_count=2500)
    ),
}
_BOOK_CSV = csv_text([parse_row(title="Moby Dick")])
_FILM_CSV = csv_text(
    [parse_row(medium="film", title="Metropolis", source_file="10/seen.txt")]
)


def fake_git(code: int = _NOT_A_REPO) -> survey_guard.GitRunner:
    """A git stand-in answering every question with ``code``."""

    def run(argv: Sequence[str], cwd: Path, timeout: float) -> int:
        return code

    return run


def snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` by relative path, as a hash of its bytes."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class _CliCase(unittest.TestCase):
    """A parse folder of one book row and one film row, and an --out beside it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.parsed = write_parsed(
            self.base / "parsed",
            {"book-2010.csv": _BOOK_CSV, "film-2010.csv": _FILM_CSV},
        )
        self.out = self.base / "out"
        # The default --token-file is under HOME, so a run that reaches the token lookup
        # with no token in its environment reads this empty folder, never the real one.
        self.home = self.base / "home"
        self.env: dict[str, str] = {_TOKEN_ENV: HTTP_TOKEN}
        self.fetch = Answering(_PAGES)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def main(
        self,
        *extra: str,
        out: Path | None = None,
        run_git: survey_guard.GitRunner | None = None,
    ) -> int:
        """Run the CLI with only ``self.env`` of the tool's variables set."""
        argv = ["--parsed", str(self.parsed), "--out", str(out or self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ))
            for name in (_TOKEN_ENV, _CONTACT_ENV):
                os.environ.pop(name, None)
            os.environ.update(self.env)
            os.environ.update(HOME=str(self.home), USERPROFILE=str(self.home))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            code = retrolog_resolve.main(
                argv,
                fetch=self.fetch,
                run_git=run_git or fake_git(),
                sleep=lambda seconds: None,
            )
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def overrides(self, *entries: str) -> str:
        """An overrides file of ``entries`` beside the parse folder, by path."""
        path = self.base / "overrides.toml"
        path.write_bytes(override_toml(*entries))
        return str(path)

    def read(self, name: str) -> str:
        """One written output file's text."""
        return (self.out / name).read_text(encoding="utf-8")


class CleanRunTest(_CliCase):
    def test_a_clean_run_exits_0_and_writes_every_output(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            sorted(
                [_MARKER, "book-2010.csv", "cache", "film-2010.csv"]
                + ["review.json", "review.md"]
            ),
        )
        self.assertEqual(self.stderr, "")
        self.assertIn(
            ",resolved,9780306406157,/works/OL7W,,,\n", self.read("book-2010.csv")
        )
        self.assertIn(
            ",resolved,,,19,Metropolis,Metropolis\n", self.read("film-2010.csv")
        )

    def test_the_summary_counts_rows_statuses_and_kinds(self) -> None:
        self.main()
        self.assertEqual(self.stdout.splitlines(), ["rows: 2", "status resolved: 2"])

    def test_the_summary_lists_each_review_kind_present(self) -> None:
        self.fetch = Answering({**_PAGES, _FILM_URL: tmdb_search_payload()})
        self.main()
        self.assertEqual(
            self.stdout.splitlines(),
            [
                "rows: 2",
                "status resolved: 1",
                "status not-found: 1",
                "review not-found: 1",
            ],
        )

    def test_the_token_and_contact_travel_only_in_their_headers(self) -> None:
        self.env[_CONTACT_ENV] = HTTP_CONTACT
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        sent = dict(zip(self.fetch.calls, self.fetch.headers, strict=True))
        self.assertEqual(sent[_FILM_URL]["Authorization"], f"Bearer {HTTP_TOKEN}")
        self.assertIn(HTTP_CONTACT, sent[_BOOK_URL]["User-Agent"])
        self.assertNotIn("Authorization", sent[_BOOK_URL])
        for url in self.fetch.calls:
            self.assertNotIn(HTTP_TOKEN, url)
            self.assertNotIn(HTTP_CONTACT, url)
        for path in self.out.rglob("*"):
            if path.is_file():
                text = path.read_bytes()
                self.assertNotIn(HTTP_TOKEN.encode(), text, path.name)
                self.assertNotIn(HTTP_CONTACT.encode(), text, path.name)
        self.assertNotIn(HTTP_TOKEN, self.stdout + self.stderr)
        self.assertNotIn(HTTP_CONTACT, self.stdout + self.stderr)

    def test_the_timeout_reaches_every_request(self) -> None:
        seen: list[float] = []
        answering = self.fetch

        def fetch(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
            seen.append(timeout)
            return answering(url, timeout, headers)

        self.fetch = fetch  # type: ignore[assignment] # a RawFetch wrapping Answering
        self.main("--timeout", "2.5")
        self.assertEqual(seen, [2.5, 2.5])

    def test_the_parse_folder_is_never_written(self) -> None:
        before = snapshot(self.parsed)
        self.main()
        self.assertEqual(snapshot(self.parsed), before)

    def test_a_book_only_folder_needs_no_token(self) -> None:
        (self.parsed / "film-2010.csv").unlink()
        self.env = {}
        self.assertEqual(self.main(), EXIT_OK, self.stderr)

    def test_an_override_is_applied(self) -> None:
        overrides = self.overrides(
            'medium = "film"\ntitle = "Metropolis"\ntmdb_id = "8"'
        )
        details = resolve_films.details_url("8")
        self.fetch = Answering(
            {**_PAGES, details: tmdb_result(8, "Metropolis", original_title="M")}
        )
        self.assertEqual(self.main("--overrides", overrides), EXIT_OK, self.stderr)
        self.assertIn(",override,,,8,Metropolis,M\n", self.read("film-2010.csv"))
        self.assertEqual(self.fetch.calls, [_BOOK_URL, details])

    def test_directors_default_to_ambiguous(self) -> None:
        both = (tmdb_result(1, "Metropolis"), tmdb_result(2, "Metropolis"))
        credits = {"crew": [{"job": "Director", "name": "Director One"}]}
        self.fetch = Answering(
            {
                **_PAGES,
                _FILM_URL: tmdb_search_payload(*both),
                resolve_films.credits_url("1"): credits,
                resolve_films.credits_url("2"): credits,
            }
        )
        self.main()
        self.assertIn("Director One", self.read("review.md"))
        self.main("--directors", "none", "--refresh")
        self.assertNotIn("Director One", self.read("review.md"))


class CacheTest(_CliCase):
    def test_cache_only_serves_films_without_a_token(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        first = snapshot(self.out)
        self.env, self.fetch = {}, Answering()
        self.assertEqual(self.main("--cache-only"), EXIT_OK, self.stderr)
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(snapshot(self.out), first)

    def test_a_rerun_sends_nothing(self) -> None:
        self.main()
        self.fetch = Answering()
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(self.fetch.calls, [])

    def test_cache_only_with_nothing_cached_fails_each_title(self) -> None:
        self.env, self.fetch = {}, Answering()
        self.assertEqual(self.main("--cache-only"), EXIT_ERRORS)
        self.assertEqual(self.fetch.calls, [])
        self.assertIn("status failed: 2", self.stdout)
        self.assertIn("2 error item(s); see review.md", self.stderr)

    def test_refresh_asks_again(self) -> None:
        self.main()
        self.fetch = Answering(_PAGES)
        self.assertEqual(self.main("--refresh"), EXIT_OK, self.stderr)
        self.assertEqual(sorted(self.fetch.calls), sorted([_BOOK_URL, _FILM_URL]))

    def test_refresh_and_cache_only_are_refused_together(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.main("--refresh", "--cache-only")
        self.assertEqual(raised.exception.code, EXIT_USAGE)


class StaleOutputTest(_CliCase):
    def test_a_stale_csv_is_removed_and_other_files_kept(self) -> None:
        self.main()
        for name in (
            "film-2009.csv",
            "book-undated.csv",
            "notes.csv",
            "film-notes.txt",
        ):
            (self.out / name).write_bytes(b"old\n")
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        names = {path.name for path in self.out.iterdir()}
        self.assertFalse({"film-2009.csv", "book-undated.csv"} & names)
        self.assertTrue({"notes.csv", "film-notes.txt", "book-2010.csv"} <= names)

    def test_a_folder_named_like_a_csv_is_left(self) -> None:
        self.main()
        (self.out / "film-2009.csv").mkdir()
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertTrue((self.out / "film-2009.csv").is_dir())


class ErrorExitTest(_CliCase):
    def test_an_unused_override_exits_1(self) -> None:
        overrides = self.overrides(
            'medium = "book"\ntitle = "Gone Book"\nunresolvable = true'
        )
        self.assertEqual(self.main("--overrides", overrides), EXIT_ERRORS)
        self.assertIn("review override-unused: 1", self.stdout)
        self.assertIn("1 error item(s); see review.md", self.stderr)

    def test_a_failed_lookup_exits_1_and_writes_every_row(self) -> None:
        self.fetch = Answering({**_PAGES, _FILM_URL: http_error(500)})
        self.assertEqual(self.main(), EXIT_ERRORS)
        self.assertIn(",failed,,,,,\n", self.read("film-2010.csv"))
        self.assertIn("tmdb: HTTP 500", self.read("review.md"))


class UsageExitTest(_CliCase):
    def assert_refused(self, code: int, *phrases: str) -> None:
        """Exit 2, a message holding each phrase, and no path or secret in it."""
        self.assertEqual(code, EXIT_USAGE, self.stdout)
        for phrase in phrases:
            self.assertIn(phrase, self.stderr)
        self.assertNotIn(str(self.base), self.stderr)
        self.assertNotIn(HTTP_TOKEN, self.stderr)

    def test_a_missing_parse_folder(self) -> None:
        self.parsed = self.base / "missing"
        self.assert_refused(self.main(), "--parsed: no such folder")

    def test_a_parse_folder_with_no_csv_leaves_an_earlier_out(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        before = snapshot(self.out)
        for path in self.parsed.iterdir():
            path.unlink()
        self.assert_refused(self.main(), "--parsed: holds no parse CSV file")
        self.assertEqual(snapshot(self.out), before)

    def test_a_damaged_parse_csv(self) -> None:
        (self.parsed / "book-2010.csv").write_bytes(b"not,parse\n")
        self.assert_refused(self.main(), "book-2010.csv: header")

    def test_an_invalid_overrides_file(self) -> None:
        overrides = self.overrides('medium = "book"\ntitle = "Moby Dick"')
        self.assert_refused(self.main("--overrides", overrides), "invalid --overrides")

    def test_an_unreadable_overrides_file(self) -> None:
        missing = str(self.base / "missing.toml")
        self.assert_refused(
            self.main("--overrides", missing), "could not read --overrides"
        )

    def test_a_missing_token_is_named_never_valued(self) -> None:
        for env in ({}, {_TOKEN_ENV: ""}):
            with self.subTest(env=env):
                self.env = env
                self.assert_refused(self.main(), _TOKEN_ENV)
                self.assertEqual(self.fetch.calls, [])

    def test_a_token_with_a_line_break(self) -> None:
        self.env = {_TOKEN_ENV: HTTP_TOKEN + "\n"}
        self.assert_refused(self.main(), _TOKEN_ENV)

    def test_a_contact_that_would_break_the_header(self) -> None:
        self.env[_CONTACT_ENV] = "a(b"
        self.assert_refused(self.main(), _CONTACT_ENV)
        self.assertNotIn("a(b", self.stderr)

    def test_a_refused_token_stops_the_run_and_a_rerun_is_accepted(self) -> None:
        self.fetch = Answering({**_PAGES, _FILM_URL: http_error(401)})
        self.assert_refused(self.main(), "HTTP 401", _TOKEN_ENV)
        # The out folder and its marker were made before any lookup.
        self.assertTrue((self.out / _MARKER).is_file())
        self.fetch = Answering(_PAGES)
        self.assertEqual(self.main(), EXIT_OK, self.stderr)

    def test_an_out_inside_a_git_work_tree(self) -> None:
        self.assert_refused(self.main(run_git=fake_git(_IN_A_REPO)), "refused --out")
        self.assertFalse(self.out.exists())
        self.assertEqual(self.fetch.calls, [])

    def test_an_out_inside_the_parse_folder(self) -> None:
        self.assert_refused(self.main(out=self.parsed / "out"), "refused --out")
        self.assertEqual(self.fetch.calls, [])

    def test_a_marker_that_is_a_link_is_refused(self) -> None:
        self.out.mkdir()
        (self.out / _MARKER).symlink_to(self.base / "elsewhere")
        self.assert_refused(self.main(), "refused --out")

    def test_a_write_failure(self) -> None:
        with mock.patch.object(
            retrolog_resolve, "write_atomic", side_effect=PermissionError(13, "denied")
        ):
            self.assert_refused(self.main(), "could not write the output")

    def test_bad_option_values_are_usage_errors(self) -> None:
        for extra in (["--timeout", "0"], ["--directors", "some"], ["--timeout", "x"]):
            with self.subTest(extra=extra):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        self.main(*extra)
                self.assertEqual(raised.exception.code, EXIT_USAGE)


class HelpTest(unittest.TestCase):
    def test_help_names_the_prerequisites_run_order_and_exit_codes(self) -> None:
        text = retrolog_resolve.build_parser().format_help()
        for phrase in ("Prerequisites", "Run order", "Exit codes", _TOKEN_ENV):
            self.assertIn(phrase, text)


class SampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sample_output_is_reproduced(self) -> None:
        work = self.base / "work"
        self.assertEqual(build_resolve_inputs.main(["--out", str(work)]), EXIT_OK)
        argv = ["--parsed", str(work / "parsed"), "--out", str(work / "out")]
        argv += ["--overrides", str(work / "overrides.toml"), "--cache-only"]
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ))
            for name in (_TOKEN_ENV, _CONTACT_ENV):
                os.environ.pop(name, None)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code = retrolog_resolve.main(argv, fetch=Answering(), run_git=fake_git())
        self.assertEqual(code, EXIT_ERRORS)
        expected = _TOOL / "samples" / "expected"
        out = work / "out"
        written = sorted(
            p.name for p in out.iterdir() if p.name not in {_MARKER, "cache"}
        )
        self.assertEqual(written, sorted(p.name for p in expected.iterdir()))
        for name in written:
            self.assertEqual(
                (out / name).read_bytes(), (expected / name).read_bytes(), name
            )

    def test_the_sample_shows_every_status_and_review_kind(self) -> None:
        expected = _TOOL / "samples" / "expected"
        csvs = "".join(p.read_text("utf-8") for p in sorted(expected.glob("*.csv")))
        for status in ("resolved", "override", "ambiguous", "not-found", "failed"):
            self.assertIn(f",{status},", csvs)
        review = (expected / "review.md").read_text("utf-8")
        for kind in retrolog_resolve.REVIEW_KINDS:
            self.assertIn(f"## {kind} (", review)

    def test_sample_builder_refuses_a_non_empty_out(self) -> None:
        (self.base / "notes.txt").write_bytes(b"")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = build_resolve_inputs.main(["--out", str(self.base)])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("must be missing or empty", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
