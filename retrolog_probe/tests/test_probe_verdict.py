"""Tests for probe_verdict: what Letterboxd's answer to one /tmdb/ address means."""

from __future__ import annotations

import sys
import unittest
import urllib.parse
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_export" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import probe_verdict  # noqa: E402 - after the sys.path inserts above
from probe_fixtures import bounded_fetch  # noqa: E402 - after the sys.path inserts above

_URL = probe_verdict.probe_url("19")
_FILM_PREFIX = "https://letterboxd.com/film/"


def _answer(status: int, location: str | None = None) -> bounded_fetch.HeadAnswer:
    return bounded_fetch.HeadAnswer(status, location)


class ProbeUrlTest(unittest.TestCase):
    def test_the_address_holds_the_id_and_nothing_else(self) -> None:
        self.assertEqual(
            probe_verdict.probe_url("19"), "https://letterboxd.com/tmdb/19/"
        )


class FilmLocationTest(unittest.TestCase):
    def test_a_film_page_relative_or_absolute(self) -> None:
        for location in (
            "/film/metropolis/",
            "https://letterboxd.com/film/metropolis/",
            "HTTPS://LETTERBOXD.COM/film/metropolis/",
            "//letterboxd.com/film/metropolis/",
        ):
            with self.subTest(location=location):
                self.assertTrue(probe_verdict.is_film_location(location, _URL))

    def test_anything_else_is_not_a_film_page(self) -> None:
        for location in (
            "",
            "film/x/",  # resolves under /tmdb/19/
            "/films/x/",
            "/film",
            "/tmdb/19/",
            "/",
            "https://letterboxd.com.example.net/film/x/",
            "https://example.org/film/x/",
            "https://letterboxd.com:8443/film/x/",
            "https://user:@letterboxd.com/film/x/",  # userinfo, its password empty
            "http://letterboxd.com/film/x/",
            "https://letterboxd.com/film/../tmdb/1/",
            "/film/x/\r\nSet-Cookie: a=b",
            "/film/x y/",
            "/film/x​/",
            "https://[::1/film/x/",
            "javascript:alert(1)",
        ):
            with self.subTest(location=location):
                self.assertFalse(probe_verdict.is_film_location(location, _URL))

    @given(st.text())
    def test_fuzz_is_film_location(self, location: str) -> None:
        if probe_verdict.is_film_location(location, _URL):
            resolved = urllib.parse.urljoin(_URL, location)
            self.assertTrue(resolved.lower().startswith(_FILM_PREFIX))

    @given(st.from_regex(r"\A[a-z0-9-]{1,30}\Z"))
    def test_fuzz_every_slug_is_a_film_page(self, slug: str) -> None:
        self.assertTrue(probe_verdict.is_film_location(f"/film/{slug}/", _URL))


class ClassifyTest(unittest.TestCase):
    def test_a_redirect_to_a_film_page_is_present(self) -> None:
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertEqual(
                    probe_verdict.classify(_answer(status, "/film/x/"), _URL),
                    probe_verdict.Outcome("present"),
                )

    def test_a_200_is_missing(self) -> None:
        self.assertEqual(
            probe_verdict.classify(_answer(200), _URL), probe_verdict.Outcome("missing")
        )

    def test_another_2xx_is_unknown(self) -> None:
        self.assertEqual(
            probe_verdict.classify(_answer(204), _URL),
            probe_verdict.Outcome("unknown", "HTTP 204"),
        )

    def test_a_redirect_elsewhere_is_unknown_naming_no_url(self) -> None:
        outcome = probe_verdict.classify(_answer(302, "https://example.org/x"), _URL)
        self.assertEqual(
            outcome,
            probe_verdict.Outcome("unknown", "HTTP 302 to a page that is not a film"),
        )

    def test_a_redirect_with_no_location_is_unknown(self) -> None:
        self.assertEqual(
            probe_verdict.classify(_answer(302), _URL),
            probe_verdict.Outcome("unknown", "HTTP 302 with no Location"),
        )


if __name__ == "__main__":
    unittest.main()
