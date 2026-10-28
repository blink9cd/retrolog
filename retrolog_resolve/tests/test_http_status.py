"""Tests for resolve_http: 429/401 handling, transport failures and secrecy.

The fakes are the HTTP section of ``resolve_fixtures``; the error fake is the real
``urllib.error.HTTPError`` type, so an unclosed error surfaces as a ``ResourceWarning``:
an ``HTTPError`` holds the response's open file until it is closed. The headers, cache
and pacing are in ``test_http``.
"""

from __future__ import annotations

import http.client
import urllib.error
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_http  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    ClosingHTTPError,
    FakeClock,
    FakeFetch,
    http_error,
)
from resolve_fixtures import HTTP_BODY as _BODY  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import HTTP_CONTACT as _CONTACT  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import HTTP_DECODED as _DECODED  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import HTTP_TOKEN as _TOKEN  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import OL_URL as _OL_URL  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import TMDB_URL as _TMDB_URL  # noqa: E402 - after the sys.path inserts above

_TIMEOUT = 7.5


class _ClientCase(unittest.TestCase):
    """A temporary cache folder, a fake clock and a client factory."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.cache = Path(holder.name) / "cache"
        self.time = FakeClock()

    def client(
        self, fetch: Callable[[str, float, Mapping[str, str]], bytes], **options: bool
    ) -> resolve_http.Client:
        return resolve_http.Client(
            self.cache,
            timeout=_TIMEOUT,
            fetch=fetch,
            sleep=self.time.sleep,
            clock=self.time.clock,
            **options,
        )

    def cache_files(self) -> list[Path]:
        return sorted(self.cache.iterdir()) if self.cache.is_dir() else []


class StatusTest(_ClientCase):
    """429, 401 and the other failures a request can end in."""

    def setUp(self) -> None:
        super().setUp()
        ClosingHTTPError.closes = 0

    def test_429_waits_retry_after_once_then_succeeds(self) -> None:
        fetch = FakeFetch(http_error(429, "3"), _BODY)
        client = self.client(fetch)
        self.assertEqual(
            client.get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL), _DECODED
        )
        self.assertEqual(self.time.sleeps, [3.0])
        self.assertEqual(len(fetch.calls), 2)
        self.assertEqual(ClosingHTTPError.closes, 1)

    def test_the_retry_after_a_short_wait_is_still_paced(self) -> None:
        fetch = FakeFetch(http_error(429, "0.25"), _BODY)
        self.client(fetch).get_json(resolve_http.open_library(None), _OL_URL)
        self.assertEqual(self.time.sleeps, [0.25, 0.75])

    def test_retry_after_is_capped(self) -> None:
        fetch = FakeFetch(http_error(429, "9999"), _BODY)
        self.client(fetch).get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        self.assertEqual(self.time.sleeps, [60.0])

    def test_a_missing_retry_after_waits_the_maximum(self) -> None:
        # urllib passes headers=None and fp=None for an error with neither, though its
        # stubs type both as required; the fake must reproduce that real shape.
        error = ClosingHTTPError(_TMDB_URL, 429, "slow", None, None)  # type: ignore[arg-type] # see above
        fetch = FakeFetch(error, _BODY)
        self.client(fetch).get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        self.assertEqual(self.time.sleeps, [resolve_http.MAX_RETRY_AFTER])

    def test_second_429_stops_the_service(self) -> None:
        fetch = FakeFetch(http_error(429, "1"), http_error(429, "1"))
        client = self.client(fetch)
        service = resolve_http.tmdb(_TOKEN)
        with self.assertRaises(resolve_http.ServiceStopped):
            client.get_json(service, _TMDB_URL)
        with self.assertRaises(resolve_http.ServiceStopped):
            client.get_json(service, _TMDB_URL + "&page=2")
        self.assertEqual(len(fetch.calls), 2)
        self.assertEqual(ClosingHTTPError.closes, 2)
        self.assertTrue(
            issubclass(resolve_http.ServiceStopped, resolve_http.LookupFailed)
        )

    def test_a_stopped_service_leaves_the_other_running(self) -> None:
        fetch = FakeFetch(http_error(429), http_error(429), _BODY)
        client = self.client(fetch)
        with self.assertRaises(resolve_http.ServiceStopped):
            client.get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        self.assertEqual(
            client.get_json(resolve_http.open_library(None), _OL_URL), _DECODED
        )

    def test_a_stopped_service_still_serves_the_cache(self) -> None:
        fetch = FakeFetch(_BODY, http_error(429), http_error(429))
        client = self.client(fetch)
        service = resolve_http.tmdb(_TOKEN)
        client.get_json(service, _TMDB_URL)
        with self.assertRaises(resolve_http.ServiceStopped):
            client.get_json(service, _TMDB_URL + "&page=2")
        self.assertEqual(client.get_json(service, _TMDB_URL), _DECODED)

    def test_401_from_tmdb_is_token_refused(self) -> None:
        client = self.client(FakeFetch(http_error(401)))
        with self.assertRaises(resolve_http.TokenRefused):
            client.get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        self.assertEqual(ClosingHTTPError.closes, 1)
        self.assertFalse(
            issubclass(resolve_http.TokenRefused, resolve_http.LookupFailed)
        )

    def test_401_after_a_429_is_token_refused(self) -> None:
        client = self.client(FakeFetch(http_error(429, "1"), http_error(401)))
        with self.assertRaises(resolve_http.TokenRefused):
            client.get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        self.assertEqual(ClosingHTTPError.closes, 2)

    def test_401_from_open_library_is_lookup_failed(self) -> None:
        client = self.client(FakeFetch(http_error(401)))
        with self.assertRaises(resolve_http.LookupFailed) as raised:
            client.get_json(resolve_http.open_library(None), _OL_URL)
        self.assertNotIsInstance(raised.exception, resolve_http.ServiceStopped)

    def test_other_http_errors_are_lookup_failed_and_closed(self) -> None:
        for code in (404, 500, 503):
            with self.subTest(code=code):
                with self.assertRaises(resolve_http.LookupFailed) as raised:
                    self.client(FakeFetch(http_error(code))).get_json(
                        resolve_http.tmdb(_TOKEN), _TMDB_URL
                    )
                self.assertIn(str(code), str(raised.exception))
        self.assertEqual(ClosingHTTPError.closes, 3)

    def test_each_transport_failure_is_lookup_failed(self) -> None:
        failures: tuple[BaseException, ...] = (
            urllib.error.URLError(f"no route to {_TMDB_URL}"),
            TimeoutError(f"timed out on {_TMDB_URL}"),
            ConnectionResetError("reset"),
            http.client.IncompleteRead(b"partial"),
            http.client.RemoteDisconnected("closed"),
            ValueError(f"refusing a redirect from {_TMDB_URL}"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(resolve_http.LookupFailed) as raised:
                    self.client(FakeFetch(failure)).get_json(
                        resolve_http.tmdb(_TOKEN), _TMDB_URL
                    )
                self.assertNotIn(_TMDB_URL, str(raised.exception))
                self.assertNotIn("api.themoviedb.org", str(raised.exception))


class SecrecyTest(_ClientCase):
    """The token and the contact travel only in their own headers."""

    def _messages(self, service: resolve_http.Service, url: str) -> list[str]:
        """Run every failure once against ``service`` and collect each message."""
        failures: tuple[BaseException, ...] = (
            http_error(401),
            http_error(404),
            urllib.error.URLError("down"),
            ValueError("refused"),
        )
        messages: list[str] = []
        for index, failure in enumerate(failures):
            try:
                self.client(FakeFetch(failure)).get_json(service, f"{url}&n={index}")
            except (resolve_http.LookupFailed, resolve_http.TokenRefused) as error:
                messages += [str(error), repr(error)]
        for outcomes in ((http_error(429), http_error(429)), (b"{bad",)):
            try:
                self.client(FakeFetch(*outcomes)).get_json(service, url + "&x=1")
            except resolve_http.LookupFailed as error:
                messages += [str(error), repr(error)]
        try:
            self.client(FakeFetch(), cache_only=True).get_json(service, url + "&y=2")
        except resolve_http.LookupFailed as error:
            messages += [str(error), repr(error)]
        return messages

    def _assert_secret_kept(
        self, service: resolve_http.Service, url: str, secret: str
    ) -> None:
        fetch = FakeFetch(_BODY)
        with self.assertNoLogs(level="DEBUG"):
            self.client(fetch).get_json(service, url)
            messages = self._messages(service, url)
        self.assertEqual(len(messages), 14)
        for message in messages:
            self.assertNotIn(secret, message)
            self.assertNotIn(url, message)
        self.assertNotIn(secret, fetch.calls[0][0])
        for path in self.cache_files():
            self.assertNotIn(secret, path.name)
            self.assertNotIn(secret.encode("utf-8"), path.read_bytes())
        self.assertNotIn(secret, resolve_http.cache_key(url))

    def test_token_never_reaches_cache_files_or_messages(self) -> None:
        self._assert_secret_kept(resolve_http.tmdb(_TOKEN), _TMDB_URL, _TOKEN)

    def test_contact_never_reaches_cache_files_or_messages(self) -> None:
        self._assert_secret_kept(resolve_http.open_library(_CONTACT), _OL_URL, _CONTACT)

    def test_the_cache_key_ignores_the_headers(self) -> None:
        self.client(FakeFetch(_BODY)).get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        self.client(FakeFetch(_BODY)).get_json(
            resolve_http.tmdb("tok-OTHER"), _TMDB_URL
        )
        self.assertEqual(
            [path.name for path in self.cache_files()],
            [resolve_http.cache_key(_TMDB_URL)],
        )


if __name__ == "__main__":
    unittest.main()
