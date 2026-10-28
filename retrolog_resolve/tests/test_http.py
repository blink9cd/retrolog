"""Tests for resolve_http: the services' headers, the URL-keyed cache and pacing.

Every test injects a recording fake in place of ``bounded_fetch.fetch_url`` and a fake
clock whose ``sleep`` advances it, so nothing waits and nothing reaches the network.
The fake replaces the fetch wholesale, so that the Authorization header is never
carried to another host on a redirect is proven by bounded_fetch's own redirect suite
(``test_bounded_fetch_redirect``), not here. The 429/401 handling and the secrecy
checks are in ``test_http_status``.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_http  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    FakeClock,
    FakeFetch,
    http_error,
)
from resolve_fixtures import HTTP_BODY as _BODY  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import HTTP_CONTACT as _CONTACT  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import HTTP_DECODED as _DECODED  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import HTTP_TOKEN as _TOKEN  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import OL_URL as _OL_URL  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import OL_URL_2 as _OL_URL_2  # noqa: E402 - after the sys.path inserts above
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


class ServiceTest(unittest.TestCase):
    """The two services' headers, and what they refuse or hide."""

    def test_open_library_user_agent_without_a_contact(self) -> None:
        service = resolve_http.open_library(None)
        self.assertEqual(service.name, "openlibrary")
        self.assertEqual(service.interval, resolve_http.OPEN_LIBRARY_INTERVAL)
        self.assertEqual(dict(service.headers), {"User-Agent": "retrolog-resolve/1.0"})

    def test_open_library_user_agent_carries_the_contact(self) -> None:
        service = resolve_http.open_library(_CONTACT)
        self.assertEqual(
            dict(service.headers), {"User-Agent": f"retrolog-resolve/1.0 ({_CONTACT})"}
        )

    def test_an_empty_contact_is_no_contact(self) -> None:
        self.assertEqual(
            resolve_http.open_library("").headers,
            resolve_http.open_library(None).headers,
        )

    def test_contact_with_a_header_break_is_refused(self) -> None:
        for bad in ("a\rb", "a\nb", "a\x00b", "a(b", "a)b", "café", "a\tb"):
            contact = f"{_CONTACT}{bad}"
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as raised:
                    resolve_http.open_library(contact)
                self.assertNotIn(_CONTACT, str(raised.exception))
                self.assertNotIn(bad, str(raised.exception))

    def test_tmdb_headers(self) -> None:
        service = resolve_http.tmdb(_TOKEN)
        self.assertEqual(service.name, "tmdb")
        self.assertEqual(service.interval, 0.0)
        self.assertEqual(
            dict(service.headers),
            {
                "Authorization": f"Bearer {_TOKEN}",
                "Accept": "application/json",
                "User-Agent": "retrolog-resolve/1.0",
            },
        )

    def test_tmdb_without_a_token_sends_no_authorization(self) -> None:
        headers = resolve_http.tmdb(None).headers
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Accept"], "application/json")

    def test_token_with_a_header_break_is_refused(self) -> None:
        """A pasted token with a newline fails once here, not on every film."""
        for bad in ("a\rb", "a\nb", "a\x00b", "café", "a\tb", "a\x7fb"):
            token = f"{_TOKEN}{bad}"
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as raised:
                    resolve_http.tmdb(token)
                self.assertNotIn(_TOKEN, str(raised.exception))
                self.assertNotIn(bad, str(raised.exception))

    def test_a_printable_token_with_punctuation_is_kept(self) -> None:
        token = f"{_TOKEN}.(x)_-~"
        headers = resolve_http.tmdb(token).headers
        self.assertEqual(headers["Authorization"], f"Bearer {token}")

    def test_service_repr_and_str_hide_the_token_and_contact(self) -> None:
        for service, secret in (
            (resolve_http.tmdb(_TOKEN), _TOKEN),
            (resolve_http.open_library(_CONTACT), _CONTACT),
        ):
            with self.subTest(service=service.name):
                self.assertNotIn(secret, repr(service))
                self.assertNotIn(secret, str(service))

    def test_service_headers_are_read_only(self) -> None:
        with self.assertRaises(TypeError):
            # The assignment is the refused operation under test; mypy rightly sees a
            # read-only Mapping.
            resolve_http.tmdb(_TOKEN).headers["Authorization"] = "x"  # type: ignore[index] # deliberate


class CacheKeyTest(unittest.TestCase):
    """The cache key is the URL's digest and nothing else."""

    def test_cache_key_is_the_sha256_of_the_url(self) -> None:
        digest = hashlib.sha256(_OL_URL.encode("utf-8")).hexdigest()
        self.assertEqual(resolve_http.cache_key(_OL_URL), f"{digest}.json")

    def test_different_urls_have_different_keys(self) -> None:
        self.assertNotEqual(
            resolve_http.cache_key(_OL_URL), resolve_http.cache_key(_OL_URL_2)
        )


class RetryAfterTest(unittest.TestCase):
    """Reading a Retry-After header into a bounded wait."""

    def test_a_number_is_waited(self) -> None:
        self.assertEqual(resolve_http.retry_after_seconds(" 2.5 "), 2.5)
        self.assertEqual(resolve_http.retry_after_seconds("0"), 0.0)

    def test_retry_after_is_capped(self) -> None:
        self.assertEqual(
            resolve_http.retry_after_seconds("9999"), resolve_http.MAX_RETRY_AFTER
        )

    def test_absent_or_unreadable_waits_the_maximum(self) -> None:
        for value in (
            None,
            "",
            "soon",
            "-5",
            "nan",
            "inf",
            "Wed, 21 Oct 2026 07:28:00 GMT",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    resolve_http.retry_after_seconds(value),
                    resolve_http.MAX_RETRY_AFTER,
                )

    @given(st.one_of(st.none(), st.text()))
    def test_fuzz_retry_after_seconds(self, value: str | None) -> None:
        wait = resolve_http.retry_after_seconds(value)
        self.assertGreaterEqual(wait, 0.0)
        self.assertLessEqual(wait, resolve_http.MAX_RETRY_AFTER)


class CacheTest(_ClientCase):
    """Hits, misses, refresh, cache-only and what is written."""

    def test_second_call_is_served_from_the_cache(self) -> None:
        fetch = FakeFetch(_BODY)
        client = self.client(fetch)
        service = resolve_http.open_library(None)
        self.assertEqual(client.get_json(service, _OL_URL), _DECODED)
        self.assertEqual(client.get_json(service, _OL_URL), _DECODED)
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual(
            (self.cache / resolve_http.cache_key(_OL_URL)).read_bytes(), _BODY
        )

    def test_a_later_client_reads_the_cache(self) -> None:
        service = resolve_http.open_library(None)
        self.client(FakeFetch(_BODY)).get_json(service, _OL_URL)
        fetch = FakeFetch()
        self.assertEqual(self.client(fetch).get_json(service, _OL_URL), _DECODED)
        self.assertEqual(fetch.calls, [])

    def test_refresh_ignores_the_cache_and_rewrites_it(self) -> None:
        service = resolve_http.open_library(None)
        self.client(FakeFetch(_BODY)).get_json(service, _OL_URL)
        fetch = FakeFetch(b'{"docs": []}')
        client = self.client(fetch, refresh=True)
        self.assertEqual(client.get_json(service, _OL_URL), {"docs": []})
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual(
            (self.cache / resolve_http.cache_key(_OL_URL)).read_bytes(), b'{"docs": []}'
        )

    def test_cache_only_miss_fails_without_a_request(self) -> None:
        fetch = FakeFetch()
        client = self.client(fetch, cache_only=True)
        with self.assertRaises(resolve_http.LookupFailed) as raised:
            client.get_json(resolve_http.tmdb(None), _TMDB_URL)
        self.assertIn("not in the cache", str(raised.exception))
        self.assertEqual(fetch.calls, [])

    def test_cache_only_hit_is_served_without_a_token(self) -> None:
        self.client(FakeFetch(_BODY)).get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        fetch = FakeFetch()
        client = self.client(fetch, cache_only=True)
        self.assertEqual(client.get_json(resolve_http.tmdb(None), _TMDB_URL), _DECODED)
        self.assertEqual(fetch.calls, [])

    def test_refresh_and_cache_only_together_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.client(FakeFetch(), refresh=True, cache_only=True)

    def test_cache_write_creates_its_folder(self) -> None:
        self.cache = self.cache / "nested" / "deeper"
        self.client(FakeFetch(_BODY)).get_json(resolve_http.open_library(None), _OL_URL)
        self.assertTrue((self.cache / resolve_http.cache_key(_OL_URL)).is_file())

    def test_no_cache_folder_fetches_every_time(self) -> None:
        fetch = FakeFetch(_BODY, _BODY)
        client = resolve_http.Client(
            None,
            timeout=_TIMEOUT,
            fetch=fetch,
            sleep=self.time.sleep,
            clock=self.time.clock,
        )
        service = resolve_http.tmdb(_TOKEN)
        client.get_json(service, _TMDB_URL)
        client.get_json(service, _TMDB_URL)
        self.assertEqual(len(fetch.calls), 2)

    def test_no_cache_folder_and_cache_only_fails(self) -> None:
        client = resolve_http.Client(
            None, timeout=_TIMEOUT, cache_only=True, fetch=FakeFetch()
        )
        with self.assertRaises(resolve_http.LookupFailed):
            client.get_json(resolve_http.tmdb(None), _TMDB_URL)

    def test_a_corrupt_cache_entry_is_fetched_again(self) -> None:
        self.cache.mkdir()
        (self.cache / resolve_http.cache_key(_OL_URL)).write_bytes(b"{not json")
        fetch = FakeFetch(_BODY)
        self.assertEqual(
            self.client(fetch).get_json(resolve_http.open_library(None), _OL_URL),
            _DECODED,
        )
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual(
            (self.cache / resolve_http.cache_key(_OL_URL)).read_bytes(), _BODY
        )

    def test_a_corrupt_cache_entry_under_cache_only_is_lookup_failed(self) -> None:
        self.cache.mkdir()
        (self.cache / resolve_http.cache_key(_OL_URL)).write_bytes(b"\xff")
        with self.assertRaises(resolve_http.LookupFailed):
            self.client(FakeFetch(), cache_only=True).get_json(
                resolve_http.open_library(None), _OL_URL
            )

    def test_a_cache_write_failure_is_lookup_failed_without_the_path(self) -> None:
        self.cache.write_bytes(b"")  # a file where the folder should be
        with self.assertRaises(resolve_http.LookupFailed) as raised:
            self.client(FakeFetch(_BODY)).get_json(
                resolve_http.open_library(None), _OL_URL
            )
        self.assertNotIn(str(self.cache), str(raised.exception))
        self.assertNotIn(resolve_http.cache_key(_OL_URL), str(raised.exception))

    def test_failure_is_not_cached(self) -> None:
        service = resolve_http.open_library(None)
        fetch = FakeFetch(http_error(500), _BODY)
        client = self.client(fetch)
        with self.assertRaises(resolve_http.LookupFailed):
            client.get_json(service, _OL_URL)
        self.assertEqual(self.cache_files(), [])
        self.assertEqual(client.get_json(service, _OL_URL), _DECODED)
        self.assertEqual(len(fetch.calls), 2)

    def test_bad_json_is_lookup_failed(self) -> None:
        for body in (b"{not json", b"\xff\xfe"):
            with self.subTest(body=body):
                with self.assertRaises(resolve_http.LookupFailed):
                    self.client(FakeFetch(body)).get_json(
                        resolve_http.open_library(None), _OL_URL
                    )
                self.assertEqual(self.cache_files(), [])

    def test_json_nested_too_deeply_is_lookup_failed(self) -> None:
        # Patched rather than built: how deep a body json.loads survives depends on
        # the interpreter's stack limit, so no fixed depth raises on every version.
        with mock.patch.object(resolve_http.json, "loads", side_effect=RecursionError):
            with self.assertRaises(resolve_http.LookupFailed):
                self.client(FakeFetch(b"[[]]")).get_json(
                    resolve_http.open_library(None), _OL_URL
                )
        self.assertEqual(self.cache_files(), [])


class PacingTest(_ClientCase):
    """Open Library is paced one second apart; TMDb is not paced."""

    def test_open_library_is_paced_one_second_apart(self) -> None:
        client = self.client(FakeFetch(_BODY, _BODY))
        service = resolve_http.open_library(None)
        client.get_json(service, _OL_URL)
        self.time.now = 0.25
        client.get_json(service, _OL_URL_2)
        self.assertEqual(self.time.sleeps, [0.75])

    def test_a_long_gap_needs_no_pacing(self) -> None:
        client = self.client(FakeFetch(_BODY, _BODY))
        service = resolve_http.open_library(None)
        client.get_json(service, _OL_URL)
        self.time.now = 5.0
        client.get_json(service, _OL_URL_2)
        self.assertEqual(self.time.sleeps, [])

    def test_a_cache_hit_is_not_paced(self) -> None:
        client = self.client(FakeFetch(_BODY))
        service = resolve_http.open_library(None)
        client.get_json(service, _OL_URL)
        client.get_json(service, _OL_URL)
        self.assertEqual(self.time.sleeps, [])

    def test_tmdb_is_not_paced(self) -> None:
        client = self.client(FakeFetch(_BODY, _BODY))
        service = resolve_http.tmdb(_TOKEN)
        client.get_json(service, _TMDB_URL)
        client.get_json(service, _TMDB_URL + "&page=2")
        self.assertEqual(self.time.sleeps, [])

    def test_the_services_are_paced_apart(self) -> None:
        client = self.client(FakeFetch(_BODY, _BODY))
        client.get_json(resolve_http.tmdb(_TOKEN), _TMDB_URL)
        client.get_json(resolve_http.open_library(None), _OL_URL)
        self.assertEqual(self.time.sleeps, [])

    def test_every_request_carries_the_timeout_and_the_services_headers(self) -> None:
        fetch = FakeFetch(_BODY)
        service = resolve_http.open_library(_CONTACT)
        self.client(fetch).get_json(service, _OL_URL)
        self.assertEqual(fetch.calls, [(_OL_URL, _TIMEOUT, dict(service.headers))])


if __name__ == "__main__":
    unittest.main()
