"""Tests for where the command line takes its TMDb token from: the environment or a file.

Each test runs ``main`` end to end through ``test_resolve_cli``'s case, which clears the
tool's variables and points ``HOME`` at a temporary folder, so the operator's own token
file is never read. Every token here is invented.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TESTS))
sys.path.insert(0, str(_TESTS.parent))
import retrolog_resolve  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import HTTP_TOKEN, Answering, http_error  # noqa: E402
from test_resolve_cli import (  # noqa: E402 - after the sys.path insert above
    _FILM_URL,
    _PAGES,
    _TOKEN_ENV,
    EXIT_OK,
    EXIT_USAGE,
    _CliCase,
)

_FILE_TOKEN = "tok-FILE-SECRET"
_DEFAULT = ("~", ".config", "retrolog", "tmdb.env")
_POSIX = os.name == "posix"


class _TokenCase(_CliCase):
    """The CLI case with no token in the environment and a token file to hand."""

    def setUp(self) -> None:
        super().setUp()
        self.env = {}
        self.token_file = self.base / "tmdb.env"

    def write_token_file(self, text: str, path: Path | None = None) -> str:
        """``text`` written owner-only to ``path`` (the case's file); its path."""
        target = path or self.token_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        target.chmod(0o600)
        return str(target)

    def assert_refused(self, code: int, *phrases: str) -> None:
        """Exit 2 and each phrase, with no path and neither token in the message."""
        self.assertEqual(code, EXIT_USAGE, self.stdout)
        for phrase in phrases:
            self.assertIn(phrase, self.stderr)
        self.assertNotIn(str(self.base), self.stderr)
        self.assertNotIn(self.token_file.name, self.stderr)
        self.assertNotIn(HTTP_TOKEN, self.stderr)
        self.assertNotIn(_FILE_TOKEN, self.stderr)

    def sent_token(self) -> str:
        """The Authorization header the film request carried."""
        sent = dict(zip(self.fetch.calls, self.fetch.headers, strict=True))
        return sent[_FILM_URL]["Authorization"]


class TokenFileTest(_TokenCase):
    def test_the_default_path_is_expanded_under_home(self) -> None:
        self.write_token_file(
            f"{_TOKEN_ENV}={_FILE_TOKEN}\n", self.home.joinpath(*_DEFAULT[1:])
        )
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(self.sent_token(), f"Bearer {_FILE_TOKEN}")

    def test_the_default_is_shown_unexpanded_in_help(self) -> None:
        text = retrolog_resolve.build_parser().format_help()
        self.assertIn("/".join(_DEFAULT), text)

    def test_an_explicit_token_file_is_read(self) -> None:
        path = self.write_token_file(f'export {_TOKEN_ENV}="{_FILE_TOKEN}"\n')
        self.assertEqual(self.main("--token-file", path), EXIT_OK, self.stderr)
        self.assertEqual(self.sent_token(), f"Bearer {_FILE_TOKEN}")
        self.assertNotIn(_FILE_TOKEN, self.stdout + self.stderr)

    def test_the_environment_wins_and_the_file_is_never_opened(self) -> None:
        self.env = {_TOKEN_ENV: HTTP_TOKEN}
        # A file that would be refused, to prove it is not read at all.
        path = self.write_token_file("not an assignment\n")
        with mock.patch.object(
            retrolog_resolve, "read_token_file", side_effect=AssertionError
        ) as reader:
            self.assertEqual(self.main("--token-file", path), EXIT_OK, self.stderr)
        reader.assert_not_called()
        self.assertEqual(self.sent_token(), f"Bearer {HTTP_TOKEN}")

    def test_an_empty_environment_value_falls_back_to_the_file(self) -> None:
        self.env = {_TOKEN_ENV: ""}
        path = self.write_token_file(f"{_TOKEN_ENV}={_FILE_TOKEN}\n")
        self.assertEqual(self.main("--token-file", path), EXIT_OK, self.stderr)
        self.assertEqual(self.sent_token(), f"Bearer {_FILE_TOKEN}")

    def test_the_file_is_read_once(self) -> None:
        path = self.write_token_file(f"{_TOKEN_ENV}={_FILE_TOKEN}\n")
        with mock.patch.object(
            retrolog_resolve, "read_token_file", return_value=_FILE_TOKEN
        ) as reader:
            self.assertEqual(self.main("--token-file", path), EXIT_OK, self.stderr)
        reader.assert_called_once()

    def test_cache_only_and_book_only_runs_read_no_file(self) -> None:
        path = self.write_token_file("not an assignment\n")
        with mock.patch.object(
            retrolog_resolve, "read_token_file", side_effect=AssertionError
        ) as reader:
            self.main("--token-file", path, "--cache-only")
            (self.parsed / "film-2010.csv").unlink()
            self.assertEqual(self.main("--token-file", path), EXIT_OK, self.stderr)
        reader.assert_not_called()


class TokenFileRefusalTest(_TokenCase):
    def test_a_missing_default_file_is_the_needs_token_refusal(self) -> None:
        self.assert_refused(self.main(), "--token-file", _TOKEN_ENV, "--cache-only")
        self.assertNotIn("could not read", self.stderr)
        self.assertEqual(self.fetch.calls, [])

    def test_an_empty_file_token_is_the_needs_token_refusal(self) -> None:
        path = self.write_token_file(f"# none yet\n{_TOKEN_ENV}=\n")
        self.assert_refused(self.main("--token-file", path), "needs a TMDb token")

    def test_a_missing_explicit_file_is_unreadable(self) -> None:
        missing = str(self.base / "missing.env")
        self.assert_refused(
            self.main("--token-file", missing), "could not read the token file"
        )
        self.assertNotIn("missing.env", self.stderr)

    def test_a_malformed_file_is_refused_by_line_number(self) -> None:
        path = self.write_token_file(f"# c\nOTHER_NAME={_FILE_TOKEN}\n")
        self.assert_refused(self.main("--token-file", path), "line 2")
        self.assertNotIn("OTHER_NAME", self.stderr)

    @unittest.skipUnless(_POSIX, "POSIX permission bits only")
    def test_a_file_others_can_read_is_refused(self) -> None:
        path = self.write_token_file(f"{_TOKEN_ENV}={_FILE_TOKEN}\n")
        self.token_file.chmod(0o644)
        self.assert_refused(self.main("--token-file", path), "mode 600", "644")

    def test_a_file_token_that_would_break_the_header_names_the_file(self) -> None:
        path = self.write_token_file(f"{_TOKEN_ENV}={_FILE_TOKEN}\t\x01x\n")
        self.assert_refused(self.main("--token-file", path), "the token file")
        self.assertNotIn(f"{_TOKEN_ENV}:", self.stderr)

    def test_a_refused_file_token_names_both_sources(self) -> None:
        self.fetch = Answering({**_PAGES, _FILM_URL: http_error(401)})
        path = self.write_token_file(f"{_TOKEN_ENV}={_FILE_TOKEN}\n")
        self.assert_refused(
            self.main("--token-file", path),
            "HTTP 401",
            f"check the token file or {_TOKEN_ENV}",
        )


if __name__ == "__main__":
    unittest.main()
