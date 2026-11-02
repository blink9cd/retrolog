"""Ask Letterboxd about one TMDb id at a time: the request, its pace, and the 429 back-off.

``Prober.probe`` sends one HEAD for ``https://letterboxd.com/tmdb/<id>/`` through
``bounded_fetch.head_url``, which follows no redirect, and returns the verdict
``probe_verdict.classify`` reads from the answer. The only header it sets is a
``User-Agent`` naming this tool (urllib adds only ``Host`` and ``Connection: close``,
and ``http.client`` beneath it ``Accept-Encoding: identity``): no cookie, no account,
no referrer, and nothing but the id in the address.

Pace: one request at a time, at least ``interval`` seconds between any two requests the
same ``Prober`` sends, retries included. A 429 is never a verdict: the same id is asked
again after a wait of ``RETRY_SCHEDULE[n]`` or the 429's ``Retry-After``, whichever is
longer, and a 429 after the last scheduled wait raises ``RateLimitedOut``, which stops
the run. The schedule starts over for each id. The fetch, the sleep, and the clock are
injectable, so a test proves the waits by the ones asked for rather than by waiting.
"""

from __future__ import annotations

import http.client
import math
import time
import types
import urllib.error
from collections.abc import Callable, Mapping

from probe_verdict import Outcome, bounded_fetch, classify, probe_url

# bounded_fetch.head_url's shape: (url, timeout, headers) -> answer.
Head = Callable[[str, float, Mapping[str, str]], bounded_fetch.HeadAnswer]

# Exactly what every request carries, read-only so nothing can add a cookie to it.
HEADERS: Mapping[str, str] = types.MappingProxyType(
    {"User-Agent": "retrolog-probe/1.0"}
)
# Seconds to wait after each successive 429 for one id, longest last; a 429 after the
# last of them stops the run.
RETRY_SCHEDULE = (30.0, 60.0, 120.0, 240.0)
# The longest a 429's Retry-After is honored.
MAX_RETRY_AFTER = 600.0
_TOO_MANY_REQUESTS = 429


class RateLimitedOut(Exception):
    """A 429 after the last scheduled wait: no further request is sent this run."""


# fuzz-target
def retry_after_seconds(value: str | None) -> float:
    """The wait a 429's Retry-After asks for, within ``[0, MAX_RETRY_AFTER]``.

    Only a non-negative finite number of seconds is read; an absent header, an HTTP
    date or anything else is ``0``, a wait of its own that never outlasts the
    schedule's, which already escalates.
    """
    try:
        seconds = float(value) if value is not None else math.nan
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        return 0.0
    return min(seconds, MAX_RETRY_AFTER)


class _RateLimited(Exception):
    """One 429, carrying the wait its Retry-After asked for; never leaves the module."""

    def __init__(self, wait: float) -> None:
        super().__init__()
        self.wait = wait


def _status_outcome(error: urllib.error.HTTPError) -> Outcome:
    """An error status as an ``unknown`` outcome, or ``_RateLimited`` for a 429.

    Closes ``error`` either way.
    """
    try:
        if error.code == _TOO_MANY_REQUESTS:
            headers = error.headers
            raise _RateLimited(
                retry_after_seconds(
                    headers.get("Retry-After") if headers is not None else None
                )
            )
        return Outcome("unknown", f"HTTP {error.code}")
    finally:
        error.close()


class Prober:
    """Paced HEAD requests to Letterboxd, one instance per run."""

    def __init__(
        self,
        *,
        timeout: float,
        interval: float,
        head: Head = bounded_fetch.head_url,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``timeout`` bounds each socket wait; ``interval`` separates two requests."""
        self._timeout = timeout
        self._interval = interval
        self._head = head
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.requests = 0

    def probe(self, tmdb_id: str) -> Outcome:
        """``tmdb_id``'s verdict; ``RateLimitedOut`` after the last scheduled wait."""
        url = probe_url(tmdb_id)
        for wait in RETRY_SCHEDULE:
            try:
                return self._attempt(url)
            except _RateLimited as limited:
                self._sleep(max(wait, limited.wait))
        try:
            return self._attempt(url)
        except _RateLimited:
            raise RateLimitedOut("rate limited after every scheduled wait") from None

    def _attempt(self, url: str) -> Outcome:
        """One paced request; every failure becomes an ``unknown`` naming its kind.

        Only the exception's type is kept: urllib's own messages can quote the URL.
        """
        self._pace()
        self.requests += 1
        try:
            answer = self._head(url, self._timeout, HEADERS)
        except urllib.error.HTTPError as error:
            return _status_outcome(error)
        except (OSError, http.client.HTTPException, ValueError) as error:
            return Outcome("unknown", f"request failed ({type(error).__name__})")
        return classify(answer, url)

    def _pace(self) -> None:
        """Sleep until ``interval`` has passed since this prober's last request."""
        if self._last_request is not None:
            remaining = self._interval - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()
