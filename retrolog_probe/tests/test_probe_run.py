"""Tests for probe_run: the control, the cache, and the stop after repeated 429s."""

from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_export" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import probe_client  # noqa: E402 - after the sys.path inserts above
import probe_run  # noqa: E402 - after the sys.path inserts above
from probe_fixtures import (  # noqa: E402 - after the sys.path inserts above
    MISSING,
    PRESENT,
    FakeClock,
    FakeHead,
    bounded_fetch,
    http_error,
)
from probe_cache import Settled  # noqa: E402 - after the sys.path inserts above
from probe_verdict import Outcome, probe_url  # noqa: E402 - after the inserts

_CONTROL = "19"
_LIMITS = len(probe_client.RETRY_SCHEDULE) + 1


def _limited() -> list[BaseException]:
    """Enough 429s to stop a run: one more than the schedule waits out."""
    return [http_error(429, "0") for _ in range(_LIMITS)]


class _RunCase(unittest.TestCase):
    def setUp(self) -> None:
        self.time = FakeClock()
        self.saved: list[dict[str, str]] = []

    def run_probe(
        self,
        ids: tuple[str, ...],
        head: FakeHead,
        *,
        cached: Mapping[str, Settled] | None = None,
        refresh: bool = False,
        cache_only: bool = False,
    ) -> probe_run.RunResult:
        prober = probe_client.Prober(
            head=head,
            timeout=5.0,
            interval=2.0,
            sleep=self.time.sleep,
            clock=self.time.clock,
        )
        return probe_run.probe_all(
            ids,
            cached=dict(cached or {}),
            prober=prober,
            control_id=_CONTROL,
            refresh=refresh,
            cache_only=cache_only,
            record=lambda verdicts: self.saved.append(dict(verdicts)),
        )


class ControlTest(_RunCase):
    def test_the_control_is_probed_first(self) -> None:
        head = FakeHead(PRESENT, MISSING)
        result = self.run_probe(("7",), head)
        self.assertEqual(head.urls(), [probe_url(_CONTROL), probe_url("7")])
        self.assertEqual(result.outcomes, {"7": Outcome("missing")})
        self.assertEqual(result.requests, 2)
        self.assertEqual(self.time.sleeps, [2.0])

    def test_a_control_that_is_not_present_stops_with_nothing_missing(self) -> None:
        for answer, reason in (
            (MISSING, "missing"),
            (
                bounded_fetch.HeadAnswer(302, "/"),
                "HTTP 302 to a page that is not a film",
            ),
            (http_error(403), "HTTP 403"),
        ):
            with self.subTest(reason=reason):
                self.saved.clear()
                head = FakeHead(answer)
                with self.assertRaises(probe_run.ControlFailed) as caught:
                    self.run_probe(("7", "8"), head)
                self.assertIn(reason, str(caught.exception))
                self.assertEqual(len(head.calls), 1)
                self.assertEqual(self.saved, [])

    def test_a_rate_limited_control_stops_too(self) -> None:
        with self.assertRaises(probe_run.ControlFailed) as caught:
            self.run_probe(("7",), FakeHead(*_limited()))
        self.assertIn("rate limited", str(caught.exception))

    def test_the_control_answers_for_its_own_id_when_it_is_in_the_files(self) -> None:
        head = FakeHead(PRESENT, MISSING)
        result = self.run_probe(("7", _CONTROL), head)
        self.assertEqual(head.urls(), [probe_url(_CONTROL), probe_url("7")])
        self.assertEqual(result.outcomes[_CONTROL], Outcome("present"))
        self.assertEqual(self.saved[-1], {_CONTROL: "present", "7": "missing"})

    def test_no_request_no_control(self) -> None:
        head = FakeHead()
        result = self.run_probe(("7",), head, cached={"7": "missing"})
        self.assertEqual(head.calls, [])
        self.assertEqual(result.requests, 0)
        self.assertEqual(result.from_cache, 1)

    def test_no_ids_no_request(self) -> None:
        head = FakeHead()
        result = self.run_probe((), head)
        self.assertEqual((head.calls, result.outcomes), ([], {}))


class CacheTest(_RunCase):
    def test_a_cached_id_is_not_sent_and_each_settled_verdict_is_saved(self) -> None:
        head = FakeHead(PRESENT, PRESENT, MISSING)
        result = self.run_probe(
            ("1", "2", "3"), head, cached={"2": "missing", "99": "present"}
        )
        self.assertEqual(
            head.urls(), [probe_url(_CONTROL), probe_url("1"), probe_url("3")]
        )
        self.assertEqual(
            result.outcomes,
            {"1": Outcome("present"), "2": Outcome("missing"), "3": Outcome("missing")},
        )
        self.assertEqual(result.from_cache, 1)
        # Saved after each settled verdict, entries for other runs' ids kept.
        self.assertEqual(
            self.saved,
            [
                {"2": "missing", "99": "present", "1": "present"},
                {"2": "missing", "99": "present", "1": "present", "3": "missing"},
            ],
        )

    def test_an_unknown_verdict_is_never_saved(self) -> None:
        result = self.run_probe(("1",), FakeHead(PRESENT, http_error(500)))
        self.assertEqual(result.outcomes, {"1": Outcome("unknown", "HTTP 500")})
        self.assertEqual(self.saved, [])

    def test_refresh_sends_every_id_and_still_saves(self) -> None:
        head = FakeHead(PRESENT, PRESENT)
        result = self.run_probe(("2",), head, cached={"2": "missing"}, refresh=True)
        self.assertEqual(result.outcomes, {"2": Outcome("present")})
        self.assertEqual(result.from_cache, 0)
        self.assertEqual(self.saved, [{"2": "present"}])

    def test_cache_only_sends_nothing(self) -> None:
        head = FakeHead()
        result = self.run_probe(
            ("1", "2"), head, cached={"2": "present"}, cache_only=True
        )
        self.assertEqual(head.calls, [])
        self.assertEqual(
            result.outcomes,
            {"1": Outcome("unknown", "not in the cache"), "2": Outcome("present")},
        )
        self.assertEqual(self.saved, [])


class RateLimitTest(_RunCase):
    def test_the_stop_marks_that_id_and_every_later_one_unknown(self) -> None:
        head = FakeHead(PRESENT, MISSING, *_limited())
        result = self.run_probe(("1", "2", "3", "4"), head)
        unknown = Outcome("unknown", "rate limited; run again later")
        self.assertEqual(
            result.outcomes,
            {"1": Outcome("missing"), "2": unknown, "3": unknown, "4": unknown},
        )
        self.assertEqual(len(head.calls), 2 + _LIMITS)
        self.assertEqual(self.saved, [{"1": "missing"}])


if __name__ == "__main__":
    unittest.main()
