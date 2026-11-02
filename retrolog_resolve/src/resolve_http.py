"""Resolve's HTTP layer: service headers, pacing, a URL-keyed cache and 429/401 handling.

``Client.get_json`` is the one way resolve reads Open Library or TMDb. It serves a
cached answer when it has one, so a re-run after editing the overrides sends only the
requests no earlier run had answered: a new override id's details, the search for a
title whose override was removed, and any request that failed. Otherwise it waits out
the service's pacing interval, fetches through
``bounded_fetch.fetch_url`` (https only, bounded in size and time, caller headers never
carried to another host on a redirect), decodes the body as JSON and caches it. A
failed lookup is never cached.

What is secret here: the TMDb token travels only as the ``Authorization`` header and the
operator's contact only inside Open Library's ``User-Agent``. Neither enters a
URL, a cache key or a cache file, and no message raised here names a header, a URL or a
path: each says only which service failed and how. ``Service`` hides its headers from
``repr`` so a stray debug print cannot leak them either.

Puts ``bounded_fetch`` on ``sys.path`` itself, through the project's bootstrap, and
imports retrolog_survey's ``survey_write``, which the CLI and the tests put there.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import sys
import time
import types
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_fetch")

import bounded_fetch  # noqa: E402 - after the bootstrap above
from survey_write import write_atomic_bytes  # noqa: E402 - after the bootstrap above

# bounded_fetch.fetch_url's shape: (url, timeout, headers) -> body.
RawFetch = Callable[[str, float, Mapping[str, str]], bytes]

# The longest a 429's Retry-After is honored; also the wait when it is absent or
# unreadable, since a server that says "slow down" without saying how long means it.
MAX_RETRY_AFTER = 60.0
# Seconds between requests: Open Library's documented limit for a request that does not
# identify itself (https://openlibrary.org/developers/api, "Rate Limits"). It is kept
# even with a contact in the User-Agent, which that page allows three times as fast.
OPEN_LIBRARY_INTERVAL = 1.0

_USER_AGENT = "retrolog-resolve/1.0"
# A contact must stay inside the User-Agent's comment: a parenthesis would close or
# nest it, and anything outside printable ASCII is a header break or is refused by
# bounded_fetch at every request.
_CONTACT_REFUSED = frozenset("()")
_PRINTABLE_ASCII = range(0x20, 0x7F)
_CONTACT_REFUSAL = "refusing an operator contact that would break the User-Agent header"
_TOKEN_REFUSAL = "refusing a TMDb token that would break the Authorization header"
_TOO_MANY_REQUESTS = 429
_UNAUTHORIZED = 401
_CACHE_SUFFIX = ".json"


@dataclass(frozen=True)
class Service:
    """One service resolve reads: its name, pacing interval and request headers.

    ``headers`` is left out of ``repr`` (and so of ``str``): it carries the token or
    the contact, which are sent and never echoed.
    """

    name: Literal["openlibrary", "tmdb"]
    interval: float
    headers: Mapping[str, str] = field(repr=False)


class LookupFailed(Exception):
    """One request failed; the message names no path, URL, header or token."""


class ServiceStopped(LookupFailed):
    """A second 429 in a row: no further request goes to that service this run."""


class TokenRefused(Exception):
    """TMDb answered 401: the token is wrong, so the CLI exits 2."""


def _read_only(headers: dict[str, str]) -> Mapping[str, str]:
    """A read-only view, so a frozen ``Service`` cannot have its headers swapped."""
    return types.MappingProxyType(headers)


def _contact_is_safe(contact: str) -> bool:
    """Whether ``contact`` is printable ASCII holding no parenthesis."""
    return all(
        ord(char) in _PRINTABLE_ASCII and char not in _CONTACT_REFUSED
        for char in contact
    )


def open_library(contact: str | None) -> Service:
    """The Open Library service, its User-Agent naming ``contact`` when one is given.

    An empty contact is no contact. ``ValueError`` (naming neither the contact nor the
    offending character) when it holds a parenthesis or anything but printable ASCII.
    """
    if contact and not _contact_is_safe(contact):
        raise ValueError(_CONTACT_REFUSAL)
    agent = f"{_USER_AGENT} ({contact})" if contact else _USER_AGENT
    return Service(
        "openlibrary", OPEN_LIBRARY_INTERVAL, _read_only({"User-Agent": agent})
    )


def tmdb(token: str | None) -> Service:
    """The TMDb service; ``None`` sends no Authorization, for cache-only use.

    ``ValueError`` (naming neither the token nor the offending character) when the
    token holds anything but printable ASCII: a pasted token carrying a line break
    would otherwise fail every film's request one by one.
    """
    if token is not None and not all(ord(char) in _PRINTABLE_ASCII for char in token):
        raise ValueError(_TOKEN_REFUSAL)
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if token is not None:
        headers = {"Authorization": f"Bearer {token}", **headers}
    return Service("tmdb", 0.0, _read_only(headers))


def cache_key(url: str) -> str:
    """The cache file name for ``url``: its SHA-256 and nothing else, never a header."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest() + _CACHE_SUFFIX


# fuzz-target
def retry_after_seconds(value: str | None) -> float:
    """The wait a 429's Retry-After asks for, within ``[0, MAX_RETRY_AFTER]``.

    Only a non-negative finite number of seconds is read; an absent header, an HTTP
    date or anything else waits the maximum.
    """
    try:
        seconds = float(value) if value is not None else math.nan
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        return MAX_RETRY_AFTER
    return min(seconds, MAX_RETRY_AFTER)


def _decode(body: bytes) -> object:
    """``body`` as JSON; ``ValueError`` for bad UTF-8 or JSON, including too deep."""
    try:
        return json.loads(body.decode("utf-8"))
    except RecursionError as error:
        raise ValueError("JSON nested too deeply") from error


class _RateLimited(Exception):
    """A 429, carrying the wait its Retry-After asked for; never leaves the module."""

    def __init__(self, wait: float) -> None:
        super().__init__()
        self.wait = wait


def _classify(service: Service, error: urllib.error.HTTPError) -> Exception:
    """The exception an HTTP error status becomes; closes ``error`` either way."""
    try:
        if error.code == _TOO_MANY_REQUESTS:
            headers = error.headers
            return _RateLimited(
                retry_after_seconds(
                    headers.get("Retry-After") if headers is not None else None
                )
            )
        if error.code == _UNAUTHORIZED and service.name == "tmdb":
            return TokenRefused("tmdb refused the token (HTTP 401)")
        return LookupFailed(f"{service.name}: HTTP {error.code}")
    finally:
        error.close()


class Client:
    """Paced, cached JSON GETs against the services, one instance per run."""

    def __init__(
        self,
        cache_dir: Path | None,
        *,
        timeout: float,
        refresh: bool = False,
        cache_only: bool = False,
        fetch: RawFetch = bounded_fetch.fetch_url,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``cache_dir`` None keeps no cache; ``refresh`` and ``cache_only`` conflict."""
        if refresh and cache_only:
            raise ValueError("refresh and cache_only cannot both be set")
        self._cache_dir = cache_dir
        self._timeout = timeout
        self._refresh = refresh
        self._cache_only = cache_only
        self._fetch = fetch
        self._sleep = sleep
        self._clock = clock
        # Keyed by service name: a Service holds a mapping, so it is not hashable.
        self._last_request: dict[str, float] = {}
        self._stopped: set[str] = set()
        # The URLs this run has cached. --refresh skips only what earlier runs wrote,
        # so a URL that repeats within a run (a film logged in several years) is
        # still asked once.
        self._written: set[str] = set()

    def get_json(self, service: Service, url: str) -> object:
        """The decoded JSON at ``url``, from the cache or one paced request.

        Raises ``LookupFailed`` (``ServiceStopped`` after a second 429) or, for a TMDb
        401, ``TokenRefused``. Nothing that failed is cached.
        """
        found, cached = self._read_cache(url)
        if found:
            return cached
        if self._cache_only:
            raise LookupFailed(f"{service.name}: not in the cache")
        if service.name in self._stopped:
            raise ServiceStopped(f"{service.name}: stopped after repeated 429s")
        body = self._fetch_with_one_retry(service, url)
        try:
            value = _decode(body)
        except ValueError:
            raise LookupFailed(f"{service.name}: the answer is not JSON") from None
        self._write_cache(service, url, body)
        return value

    def _cache_path(self, url: str) -> Path | None:
        """Where ``url``'s answer is cached, or None when no cache is kept."""
        return None if self._cache_dir is None else self._cache_dir / cache_key(url)

    def _read_cache(self, url: str) -> tuple[bool, object]:
        """(True, value) for a readable cached answer; (False, None) for a miss.

        An unreadable or corrupt entry is a miss, so it is fetched and rewritten. Under
        ``refresh`` every entry this run did not write is a miss.
        """
        path = self._cache_path(url)
        if path is None or (self._refresh and url not in self._written):
            return False, None
        try:
            return True, _decode(path.read_bytes())
        except (OSError, ValueError):
            return False, None

    def _write_cache(self, service: Service, url: str, body: bytes) -> None:
        """Cache ``body`` atomically, creating the folder; a failure names no path."""
        path = self._cache_path(url)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic_bytes(path, body)
        except OSError:
            raise LookupFailed(f"{service.name}: could not write the cache") from None
        self._written.add(url)

    def _fetch_with_one_retry(self, service: Service, url: str) -> bytes:
        """One attempt, and after a 429 one more once its wait is over."""
        try:
            return self._attempt(service, url)
        except _RateLimited as limited:
            self._sleep(limited.wait)
        try:
            return self._attempt(service, url)
        except _RateLimited:
            self._stopped.add(service.name)
            raise ServiceStopped(f"{service.name}: HTTP 429 twice; stopping") from None

    def _attempt(self, service: Service, url: str) -> bytes:
        """One paced request; every failure is translated to a message-safe one.

        ``from None`` keeps the original out of a printed traceback: urllib's own
        messages can quote the URL.
        """
        self._pace(service)
        try:
            return self._fetch(url, self._timeout, service.headers)
        except urllib.error.HTTPError as error:
            raise _classify(service, error) from None
        except (OSError, http.client.HTTPException, ValueError) as error:
            failure = type(error).__name__
            raise LookupFailed(f"{service.name}: request failed ({failure})") from None

    def _pace(self, service: Service) -> None:
        """Sleep until ``service.interval`` has passed since its last request."""
        last = self._last_request.get(service.name)
        if last is not None:
            remaining = service.interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[service.name] = self._clock()
