"""Live boundary test: two real requests to Letterboxd, through the real HEAD fetch.

The end-to-end smoke test at the network boundary that the unit tests' fakes cannot
give: the real ``bounded_fetch.head_url``, the real site, and its real answers. It sends
two public TMDb ids and nothing else (no cookie, no account), two seconds apart:
Metropolis (1927), which Letterboxd has, and an id far past any TMDb has issued, which
it cannot have.

Skipped when ``RETROLOG_RESOLVE_OFFLINE`` is ``1``, the switch that skips every test
of these tools that reaches a network service.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
import probe_client  # noqa: E402 - after the sys.path insert above
from probe_verdict import Outcome  # noqa: E402 - after the sys.path insert above

_OFFLINE = os.environ.get("RETROLOG_RESOLVE_OFFLINE") == "1"
# Seconds per request: generous, since a public site can be slow.
_TIMEOUT = 30.0
# Seconds between the two requests, the tool's own default pace.
_INTERVAL = 2.0
_PRESENT_ID = "19"
_MISSING_ID = "999999999"


@unittest.skipIf(_OFFLINE, "RETROLOG_RESOLVE_OFFLINE=1 skips the live tests")
class LiveTest(unittest.TestCase):
    def test_a_film_letterboxd_has_is_present_and_one_it_lacks_is_missing(self) -> None:
        prober = probe_client.Prober(timeout=_TIMEOUT, interval=_INTERVAL)
        self.assertEqual(prober.probe(_PRESENT_ID), Outcome("present"))
        self.assertEqual(prober.probe(_MISSING_ID), Outcome("missing"))


if __name__ == "__main__":
    unittest.main()
