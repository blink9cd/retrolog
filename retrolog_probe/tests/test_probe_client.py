"""Tests for probe_client: what is sent, the pace, and the 429 back-off.

The fetch, the sleep, and the clock are fakes, so every assertion is on what was sent
and which waits were asked for; nothing reaches the network and no test waits.
"""

from __future__ import annotations

import http.client
import math
import sys
import unittest
import urllib.error
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_export" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import probe_client  # noqa: E402 - after the sys.path inserts above
from probe_fixtures import (  # noqa: E402 - after the sys.path inserts above
    MISSING,
    PRESENT,
    ClosingHTTPError,
    FakeClock,
    FakeHead,
    http_error,
)
from probe_verdict import Outcome  # noqa: E402 - after the sys.path inserts above

_TIMEOUT = 12.5
_INTERVAL = 2.0
_RATE_LIMITED = 429


class _ProberCase(unittest.TestCase):
    def setUp(self) -> None:
        self.time = FakeClock()
        ClosingHTTPError.closes = 0

    def prober(
        self, head: FakeHead, interval: float = _INTERVAL
    ) -> probe_client.Prober:
        return probe_client.Prober(
            head=head,
            timeout=_TIMEOUT,
            interval=interval,
            sleep=self.time.sleep,
            clock=self.time.clock,
        )


class WhatIsSentTest(_ProberCase):
    def test_the_url_holds_only_the_id_and_the_headers_only_the_user_agent(
        self,
    ) -> None:
        head = FakeHead(PRESENT)
        self.assertEqual(self.prober(head).probe("19"), Outcome("present"))
        self.assertEqual(
            head.calls,
            [
                (
                    "https://letterboxd.com/tmdb/19/",
                    _TIMEOUT,
                    {"User-Agent": "retrolog-probe/1.0"},
                )
            ],
        )

    def test_the_headers_cannot_be_changed_through_the_module(self) -> None:
        with self.assertRaises(TypeError):
            probe_client.HEADERS["Cookie"] = "a=b"  # type: ignore[index]

    def test_the_count_of_requests_sent(self) -> None:
        prober = self.prober(FakeHead(http_error(_RATE_LIMITED, "1"), MISSING))
        self.assertEqual(prober.requests, 0)
        prober.probe("7")
        self.assertEqual(prober.requests, 2)


class PaceTest(_ProberCase):
    def test_the_interval_separates_every_two_requests(self) -> None:
        prober = self.prober(FakeHead(PRESENT, MISSING, PRESENT))
        for tmdb_id in ("1", "2", "3"):
            prober.probe(tmdb_id)
        self.assertEqual(self.time.sleeps, [_INTERVAL, _INTERVAL])

    def test_time_already_passed_counts_toward_the_interval(self) -> None:
        prober = self.prober(FakeHead(PRESENT, MISSING))
        prober.probe("1")
        self.time.now += 0.5
        prober.probe("2")
        self.assertEqual(self.time.sleeps, [1.5])

    def test_after_a_long_back_off_no_further_pacing_wait(self) -> None:
        prober = self.prober(FakeHead(http_error(_RATE_LIMITED, "0"), PRESENT))
        self.assertEqual(prober.probe("1"), Outcome("present"))
        self.assertEqual(self.time.sleeps, [probe_client.RETRY_SCHEDULE[0]])


class BackOffTest(_ProberCase):
    def test_each_429_waits_the_schedule_or_retry_after_whichever_is_longer(
        self,
    ) -> None:
        head = FakeHead(
            http_error(_RATE_LIMITED, "0"),
            http_error(_RATE_LIMITED, "90"),
            http_error(_RATE_LIMITED, "100000"),
            http_error(_RATE_LIMITED, "5"),
            PRESENT,
        )
        self.assertEqual(self.prober(head).probe("1"), Outcome("present"))
        self.assertEqual(self.time.sleeps, [30.0, 90.0, 600.0, 240.0])
        self.assertEqual(ClosingHTTPError.closes, 4)
        self.assertEqual(len(set(head.urls())), 1)

    def test_a_429_with_no_retry_after_waits_the_schedule(self) -> None:
        # The schedule already escalates, so a server that says only "slow down"
        # gets its waits; a missing header is not read as the cap.
        prober = self.prober(FakeHead(http_error(_RATE_LIMITED), PRESENT))
        prober.probe("1")
        self.assertEqual(self.time.sleeps, [probe_client.RETRY_SCHEDULE[0]])

    def test_a_429_after_the_last_wait_stops(self) -> None:
        schedule = probe_client.RETRY_SCHEDULE
        head = FakeHead(
            *(http_error(_RATE_LIMITED, "0") for _ in range(len(schedule) + 1))
        )
        with self.assertRaises(probe_client.RateLimitedOut):
            self.prober(head).probe("1")
        self.assertEqual(self.time.sleeps, list(schedule))
        self.assertEqual(len(head.calls), len(schedule) + 1)
        self.assertEqual(ClosingHTTPError.closes, len(schedule) + 1)

    def test_the_schedule_restarts_for_each_id(self) -> None:
        prober = self.prober(
            FakeHead(
                http_error(_RATE_LIMITED, "0"),
                PRESENT,
                http_error(_RATE_LIMITED, "0"),
                PRESENT,
            )
        )
        prober.probe("1")
        prober.probe("2")
        first = probe_client.RETRY_SCHEDULE[0]
        self.assertEqual(self.time.sleeps, [first, _INTERVAL, first])


class FailureTest(_ProberCase):
    def test_an_error_status_is_unknown_naming_the_status(self) -> None:
        for code in (403, 404, 500, 503):
            with self.subTest(code=code):
                outcome = self.prober(FakeHead(http_error(code))).probe("1")
                self.assertEqual(outcome, Outcome("unknown", f"HTTP {code}"))
        self.assertEqual(ClosingHTTPError.closes, 4)

    def test_a_transport_failure_is_unknown_naming_the_exception_type(self) -> None:
        for failure in (
            urllib.error.URLError("https://letterboxd.com/tmdb/1/ unreachable"),
            TimeoutError("timed out"),
            http.client.RemoteDisconnected("gone"),
            http.client.IncompleteRead(b""),
            ValueError("refusing a redirect"),
        ):
            with self.subTest(failure=type(failure).__name__):
                outcome = self.prober(FakeHead(failure)).probe("1")
                self.assertEqual(
                    outcome,
                    Outcome("unknown", f"request failed ({type(failure).__name__})"),
                )
                self.assertNotIn("letterboxd", outcome.reason)


class RetryAfterTest(unittest.TestCase):
    def test_a_number_of_seconds_is_read_and_capped(self) -> None:
        for value, expected in (("0", 0.0), ("45", 45.0), ("2.5", 2.5), ("601", 600.0)):
            with self.subTest(value=value):
                self.assertEqual(probe_client.retry_after_seconds(value), expected)

    def test_anything_else_is_no_wait_of_its_own(self) -> None:
        for value in (
            None,
            "",
            "soon",
            "-1",
            "nan",
            "inf",
            "Wed, 21 Oct 2015 07:28:00 GMT",
        ):
            with self.subTest(value=value):
                self.assertEqual(probe_client.retry_after_seconds(value), 0.0)

    @given(st.one_of(st.none(), st.text()))
    def test_fuzz_retry_after_seconds(self, value: str | None) -> None:
        seconds = probe_client.retry_after_seconds(value)
        self.assertTrue(math.isfinite(seconds))
        self.assertTrue(0 <= seconds <= probe_client.MAX_RETRY_AFTER)


class ConstantsTest(unittest.TestCase):
    def test_the_schedule_escalates(self) -> None:
        self.assertEqual(probe_client.RETRY_SCHEDULE, (30.0, 60.0, 120.0, 240.0))
        self.assertEqual(probe_client.MAX_RETRY_AFTER, 600.0)


if __name__ == "__main__":
    unittest.main()
