"""Build retrolog_probe test inputs, and the fakes standing in for time and Letterboxd.

Import files are written into a temporary folder with retrolog_export's own header
and CSV writer. Every title is invented (``Placeholder Film One``) and every id is
made up: nothing here comes from a real list or a real answer.

The fakes record what the code under test asked for: ``FakeClock`` every wait, and
``FakeHead`` every request's URL, timeout, and headers, so a test asserts the waits and
what was sent rather than measuring wall time or reaching the network.
"""

from __future__ import annotations

import email.message
import io
import sys
import urllib.error
from collections.abc import Mapping, Sequence
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[3] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_fetch")

import bounded_fetch  # noqa: E402 - after the bootstrap above
from export_letterboxd import COLUMNS  # noqa: E402 - export's src/ is on the path
from export_pack import csv_text  # noqa: E402 - export's src/ is on the path

# What Letterboxd answers, as head_url returns it: a film it has redirects to the film's
# page, and a film it lacks is a plain 200 at the /tmdb/ address.
PRESENT = bounded_fetch.HeadAnswer(302, "/film/placeholder/")
MISSING = bounded_fetch.HeadAnswer(200, None)


def import_row(tmdb_id: str, title: str, year: str = "1999") -> tuple[str, ...]:
    """One import row: its id, title, and year, the other cells as export fills them."""
    cells = dict.fromkeys(COLUMNS, "")
    cells.update({"tmdbID": tmdb_id, "Title": title, "Year": year, "Rating": "4"})
    return tuple(cells[name] for name in COLUMNS)


def write_import(
    folder: Path,
    name: str,
    rows: Sequence[Sequence[str]],
    header: Sequence[str] = COLUMNS,
) -> Path:
    """Write ``rows`` under ``header`` to ``folder/name``, as export writes a file."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(csv_text(header, rows), encoding="utf-8", newline="\n")
    return path


class FakeClock:
    """A monotonic clock that only ``sleep`` moves."""

    def __init__(self) -> None:
        """Start at time zero, with no sleep recorded."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        """The current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record the wait and advance the clock by it."""
        self.sleeps.append(seconds)
        self.now += seconds


class FakeHead:
    """Answers each request with the next queued outcome and records what was sent."""

    def __init__(self, *outcomes: bounded_fetch.HeadAnswer | BaseException) -> None:
        """Queue ``outcomes``: an answer to return or an exception to raise, in order."""
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, float, dict[str, str]]] = []

    def __call__(
        self, url: str, timeout: float, headers: Mapping[str, str]
    ) -> bounded_fetch.HeadAnswer:
        """Record the request, then return or raise the next outcome."""
        self.calls.append((url, timeout, dict(headers)))
        if not self.outcomes:
            raise AssertionError(f"an unexpected request: {url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def urls(self) -> list[str]:
        """Every URL requested, in order."""
        return [url for url, _, _ in self.calls]


class ClosingHTTPError(urllib.error.HTTPError):
    """The real HTTPError type, counting every ``close`` across all instances."""

    closes = 0

    def close(self) -> None:
        """Count the close, then close the wrapped body as usual."""
        type(self).closes += 1
        super().close()


def http_error(code: int, retry_after: str | None = None) -> ClosingHTTPError:
    """An HTTP error status as head_url raises it, optionally with a Retry-After."""
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    url = "https://letterboxd.com/tmdb/1/"
    return ClosingHTTPError(url, code, "refused", headers, io.BytesIO(b""))
