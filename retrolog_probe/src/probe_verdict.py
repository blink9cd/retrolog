"""What Letterboxd's answer to one ``/tmdb/<id>/`` address says about that film.

Letterboxd sends ``https://letterboxd.com/tmdb/<id>/`` on to the film's own page
(``/film/<slug>/``) when it has the film, and answers it with a plain 200 at that
address when it does not. So a redirect to a film page is ``present`` and a 200 is
``missing``; any other answer is ``unknown``, with a short reason naming the status,
never a URL or a header. A 429 never reaches this module: the client backs off and asks
again, because a 429 says "slow down", not "missing".
"""

from __future__ import annotations

import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_fetch")

import bounded_fetch  # noqa: E402 - after the bootstrap above

Verdict = Literal["present", "missing", "unknown"]
# The site and the two paths the check knows: where an id is asked about, and where a
# film Letterboxd has lives.
_SITE = "https://letterboxd.com"
_HOST = "letterboxd.com"
_FILM_PATH = "/film/"
_OK = 200
_REDIRECTS = range(300, 400)
# Path segments that would move a path out of /film/ once a server or browser
# normalized it; a film page's address never holds one.
_DOT_SEGMENTS = frozenset({".", ".."})


@dataclass(frozen=True)
class Outcome:
    """One id's verdict, and for ``unknown`` the short reason (no URL, no header)."""

    verdict: Verdict
    reason: str = ""


def probe_url(tmdb_id: str) -> str:
    """The address asked about ``tmdb_id``: the id, and nothing else, in the path."""
    return f"{_SITE}/tmdb/{tmdb_id}/"


def _is_clean(location: str) -> bool:
    """Whether ``location`` holds no whitespace and no control or format character.

    Checked on the raw text, before any parse: ``urljoin`` silently strips tab,
    carriage return, and line feed, so a header break would otherwise parse clean.
    """
    return bool(location) and all(
        char.isprintable() and not char.isspace() for char in location
    )


# fuzz-target
def is_film_location(location: str, request_url: str) -> bool:
    """Whether ``location``, resolved against ``request_url``, is a Letterboxd film page.

    That is: an https URL whose whole authority is ``letterboxd.com`` (no port, no
    userinfo, no other host) and whose path starts ``/film/`` and holds no segment
    that is literally ``.`` or ``..``. A percent-encoded one (``%2e%2e``) is not
    decoded, so it passes; that is enough, since the location is only classified,
    never requested or followed, and a redirect into ``/film/`` already says the id
    names a film. Anything a parser refuses is not a film page.
    """
    if not _is_clean(location):
        return False
    try:
        resolved = urllib.parse.urljoin(request_url, location)
    except ValueError:
        return False
    if bounded_fetch.https_url_or_none(resolved) is None:
        return False
    parts = urllib.parse.urlsplit(resolved)
    return (
        parts.netloc.lower() == _HOST
        and parts.path.startswith(_FILM_PATH)
        and _DOT_SEGMENTS.isdisjoint(parts.path.split("/"))
    )


def classify(answer: bounded_fetch.HeadAnswer, request_url: str) -> Outcome:
    """The verdict ``answer`` gives for the id asked about at ``request_url``."""
    status = answer.status
    if status in _REDIRECTS:
        if answer.location is None:
            return Outcome("unknown", f"HTTP {status} with no Location")
        if is_film_location(answer.location, request_url):
            return Outcome("present")
        return Outcome("unknown", f"HTTP {status} to a page that is not a film")
    if status == _OK:
        return Outcome("missing")
    return Outcome("unknown", f"HTTP {status}")
