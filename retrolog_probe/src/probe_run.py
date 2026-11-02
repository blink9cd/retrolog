"""Settle every id: from the cache where it can, by asking Letterboxd where it must.

Before the first request of a run, a control id that Letterboxd is known to have is
asked about. Unless it comes back ``present``, the run stops with ``ControlFailed`` and
nothing is called missing: an answer the check does not expect means the check itself
is not working (a changed site, a block page), and every ``missing`` it gave would be
wrong. A run that sends no request, everything being cached or ``cache_only`` set, asks
about no control either.

After each settled verdict the whole cache is handed to ``record``, so an interrupted
run keeps every verdict it reached. A ``RateLimitedOut`` stops the run: that id and
every id not yet asked about are ``unknown``, to be asked about next run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from probe_cache import Settled
from probe_client import Prober, RateLimitedOut
from probe_verdict import Outcome

RATE_LIMITED = Outcome("unknown", "rate limited; run again later")
NOT_CACHED = Outcome("unknown", "not in the cache")


class ControlFailed(Exception):
    """The control id was not ``present``; the message names how it was answered."""


@dataclass(frozen=True)
class RunResult:
    """Each id's outcome, how many came from the cache, and how many requests went out."""

    outcomes: Mapping[str, Outcome]
    from_cache: int
    requests: int


def _check_control(prober: Prober, control_id: str) -> Outcome:
    """The control's outcome when it is ``present``; ``ControlFailed`` otherwise."""
    try:
        outcome = prober.probe(control_id)
    except RateLimitedOut:
        raise ControlFailed("the control was rate limited") from None
    if outcome.verdict != "present":
        detail = outcome.reason or outcome.verdict
        raise ControlFailed(f"the control came back {detail}")
    return outcome


def _settle(
    outcomes: dict[str, Outcome],
    store: dict[str, Settled],
    tmdb_id: str,
    outcome: Outcome,
    record: Callable[[Mapping[str, Settled]], None],
) -> None:
    """Keep ``outcome`` for ``tmdb_id``, and record the cache when it is settled."""
    outcomes[tmdb_id] = outcome
    if outcome.verdict != "unknown":
        store[tmdb_id] = outcome.verdict
        record(store)


def _ask(
    pending: Sequence[str],
    outcomes: dict[str, Outcome],
    store: dict[str, Settled],
    prober: Prober,
    record: Callable[[Mapping[str, Settled]], None],
) -> None:
    """Ask about each pending id in turn, until the run is rate limited out."""
    for index, tmdb_id in enumerate(pending):
        if tmdb_id in outcomes:
            continue
        try:
            outcome = prober.probe(tmdb_id)
        except RateLimitedOut:
            outcomes.update(
                (rest, RATE_LIMITED) for rest in pending[index:] if rest not in outcomes
            )
            return
        _settle(outcomes, store, tmdb_id, outcome, record)


def probe_all(
    ids: Sequence[str],
    *,
    cached: Mapping[str, Settled],
    prober: Prober,
    control_id: str,
    refresh: bool,
    cache_only: bool,
    record: Callable[[Mapping[str, Settled]], None],
) -> RunResult:
    """Each of ``ids`` settled; ``ControlFailed`` before anything else when it fails.

    ``refresh`` asks about every id whatever is cached; ``cache_only`` asks about
    none, and an uncached id is ``unknown``. The cache handed to ``record`` keeps what
    ``cached`` held for ids this run did not settle.
    """
    store: dict[str, Settled] = dict(cached)
    known = {} if refresh else dict(cached)
    outcomes = {tmdb_id: Outcome(known[tmdb_id]) for tmdb_id in ids if tmdb_id in known}
    from_cache = len(outcomes)
    pending = [tmdb_id for tmdb_id in ids if tmdb_id not in known]
    if cache_only:
        outcomes.update((tmdb_id, NOT_CACHED) for tmdb_id in pending)
    elif pending:
        control = _check_control(prober, control_id)
        if control_id in pending:
            _settle(outcomes, store, control_id, control, record)
        _ask(pending, outcomes, store, prober, record)
    return RunResult(outcomes, from_cache, prober.requests)
