"""Live boundary tests: one real lookup on each service, through the real fetch.

They are the end-to-end smoke test at the network boundary that unit-test fakes cannot
give: the real ``bounded_fetch``, the real service and its real answer shape. Each
sends only a public-domain title and a year bound, and nothing is cached.

Skipped when ``RETROLOG_RESOLVE_OFFLINE`` is ``1``; the TMDb test is skipped too when
``RETROLOG_TMDB_TOKEN`` is unset, and reads the token only to send it.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_http  # noqa: E402 - after the sys.path inserts above
import resolve_isbn  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from resolve_columns import is_tmdb_id  # noqa: E402 - after the sys.path inserts
from resolve_fixtures import input_row  # noqa: E402 - after the sys.path inserts above
from resolve_types import InputRow  # noqa: E402 - after the sys.path inserts above

_OFFLINE = os.environ.get("RETROLOG_RESOLVE_OFFLINE") == "1"
# Seconds per request: generous, since a public service can be slow.
_TIMEOUT = 30.0


def _resolve(row: InputRow, token: str | None) -> resolve_rows.OutputRow:
    """``row`` resolved live, with no cache and no directors."""
    rows, _ = resolve_rows.resolve_all(
        [row],
        (),
        client=resolve_http.Client(None, timeout=_TIMEOUT),
        open_library=resolve_http.open_library(None),
        tmdb=resolve_http.tmdb(token) if token else None,
        directors="none",
        vote_ratio=10.0,
        vote_floor=1,
        research=True,
    )
    return rows[0]


@unittest.skipIf(_OFFLINE, "RETROLOG_RESOLVE_OFFLINE=1 skips the live tests")
class LiveTest(unittest.TestCase):
    def test_moby_dick_resolves_on_open_library_with_an_isbn13(self) -> None:
        row = _resolve(input_row("Moby Dick", work_year=1851, date="2020-01-01"), None)
        self.assertEqual(row.cells["resolve_status"], "resolved")
        self.assertTrue(row.cells["ol_work"].startswith("/works/OL"))
        self.assertEqual(
            resolve_isbn.isbn13_or_none(row.cells["isbn13"]), row.cells["isbn13"]
        )

    def test_metropolis_resolves_on_tmdb(self) -> None:
        token = os.environ.get("RETROLOG_TMDB_TOKEN")
        if not token:
            self.skipTest("RETROLOG_TMDB_TOKEN is unset")
        film = input_row("Metropolis", medium="film", work_year=1927, date="2020-01-01")
        row = _resolve(film, token)
        self.assertEqual(row.cells["resolve_status"], "resolved")
        self.assertTrue(is_tmdb_id(row.cells["tmdb_id"]))


if __name__ == "__main__":
    unittest.main()
