"""Decide what one title's search results come to: resolved, ambiguous or not found.

Pure: nothing here sends a request or reads a file. A caller hands over the candidates a
service returned, in the service's own order, with the row's normalized title (parse's
``title_key``) and its two year bounds, and gets one ``Lookup`` back.

The year filters run first, then the exact-title rule: a row resolves only
when exactly one surviving candidate's normalized title equals the row's. ``verdict``
is that rule alone, and books use it. Films use ``film_verdict``, which adds two rules
for what the exact rule leaves ambiguous: two or more exact titles go to the most-voted
one when it far outvotes the rest, and no exact title is retried under each of
``resolve_variants.VARIANTS`` in turn. Neither sends a request: both read the
candidates the one search already returned.

``film_verdict`` also doubts the exact rule's own answer: a single exact title with no
release date, matched on its original title alone, or under the vote floor is held for
review rather than settled (``suspect_reasons``). The loosened rules are not held, since
each already lists its choice for a check.

One more rule does need a second search: a film still without an exact title
is searched again under its title without a leading article. Deciding it stays pure:
``research_query`` says whether to send that search and for what title, and
``research_verdict`` judges its candidates; the caller sends the request between them.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from parse_rows import title_key as normalize
from resolve_types import HELD_BASIS, Candidate, Lookup
from resolve_variants import VARIANTS, leading_article

SHOWN = 3  # candidates an ambiguous lookup lists


def _passes_years(
    candidate: Candidate, *, work_year: int | None, date_year: int | None
) -> bool:
    """Whether ``candidate`` survives the year bounds.

    A work year read from the title wants exactly that year, so an unknown year fails
    it; the date bound only drops a work known to be later, so an unknown year passes.
    """
    if work_year is not None and candidate.year != work_year:
        return False
    if date_year is None or candidate.year is None:
        return True
    # The same year is kept: an approximate December 31 date never excludes a work.
    return candidate.year <= date_year


def filter_years(
    candidates: Sequence[Candidate], *, work_year: int | None, date_year: int | None
) -> tuple[Candidate, ...]:
    """The candidates the year bounds keep, in their given order."""
    return tuple(
        candidate
        for candidate in candidates
        if _passes_years(candidate, work_year=work_year, date_year=date_year)
    )


def _unchanged(title: str) -> str:
    """``title`` as it is: the exact rule's variant."""
    return title


def _matches(
    candidate: Candidate, title_key: str, vary: Callable[[str], str] = _unchanged
) -> bool:
    """Whether ``candidate``'s title, under ``vary``, normalizes to ``title_key``.

    Its original title is tried too, when it has one.
    """
    if normalize(vary(candidate.title)) == title_key:
        return True
    # An empty original title is unset, not a title that normalizes to "".
    return (
        bool(candidate.original_title)
        and normalize(vary(candidate.original_title)) == title_key
    )


def exact(candidates: Sequence[Candidate], title_key: str) -> tuple[Candidate, ...]:
    """The candidates whose normalized title equals ``title_key``, in their given order."""
    return tuple(
        candidate for candidate in candidates if _matches(candidate, title_key)
    )


# fuzz-target
def verdict(
    candidates: Sequence[Candidate],
    title_key: str,
    *,
    work_year: int | None,
    date_year: int | None,
) -> Lookup:
    """The exact-title rule's answer for one title, after the year filters.

    One exact title resolves; no candidate left is not found; anything else is
    ambiguous, listing the first ``SHOWN`` exact titles, or the first ``SHOWN``
    candidates when none is exact, in the service's order.
    """
    kept = filter_years(candidates, work_year=work_year, date_year=date_year)
    if not kept:
        return Lookup("not-found", None, (), "no candidate within the year bounds")
    matched = exact(kept, title_key)
    if len(matched) == 1:
        return Lookup("resolved", matched[0], (), "one exact title")
    if matched:
        detail = f"{len(matched)} exact titles"
        return Lookup("ambiguous", None, matched[:SHOWN], detail)
    detail = f"no exact title among {len(kept)} candidates"
    return Lookup("ambiguous", None, kept[:SHOWN], detail)


def _most_voted(matched: Sequence[Candidate], vote_ratio: float) -> Lookup | None:
    """The most-votes rule's answer for two or more exact titles, or ``None`` when the rule does not fire.

    It fires when every exact title's votes are known, the most-voted has a positive
    count, and that count is strictly more than, and at least ``vote_ratio`` times, the
    second-most-voted's. A ratio that is not a positive finite number (0, NaN or an
    infinity) turns it off: an infinite ratio times a runner-up of 0 is NaN, which no
    comparison refuses. A tie is never broken, whatever the ratio, since nothing would
    say which to pick.
    """
    if not (math.isfinite(vote_ratio) and vote_ratio > 0):
        return None
    counted = [(c.votes, c) for c in matched if c.votes is not None]
    if len(counted) != len(matched):
        return None
    # A stable sort, so titles with equal votes keep the service's order.
    counted.sort(key=lambda pair: pair[0], reverse=True)
    top, runner_up = counted[0][0], counted[1][0]
    if top <= 0 or top <= runner_up or top < vote_ratio * runner_up:
        return None
    ranked = tuple(candidate for _, candidate in counted)
    detail = f"most votes: {top} against {runner_up}"
    return Lookup("resolved", ranked[0], ranked[:SHOWN], detail, "most-voted")


def _by_variant(kept: Sequence[Candidate], title: str) -> Lookup | None:
    """The title variants' answer for a title with no exact match, or ``None`` when no variant matches.

    The first variant under which any candidate matches decides, even when a later one
    would have given a single match: one match resolves, more is ambiguous.
    """
    for name, vary in VARIANTS:
        key = normalize(vary(title))
        found = tuple(c for c in kept if _matches(c, key, vary))
        if len(found) == 1:
            detail = f"matched ignoring {name}"
            return Lookup("resolved", found[0], found, detail, "variant")
        if found:
            detail = f"{len(found)} titles match ignoring {name}"
            return Lookup("ambiguous", None, found[:SHOWN], detail)
    return None


def suspect_reasons(
    candidate: Candidate, title_key: str, *, vote_floor: int
) -> tuple[str, ...]:
    """Why a film's single exact title should not settle: every reason that holds.

    ``candidate`` is the one exact title for the row's ``title_key``. A ``vote_floor``
    of 0 turns off the votes reason alone, an unknown count included. The reasons name
    no title, so a review detail built from them stays free of one.
    """
    reasons: list[str] = []
    if candidate.year is None:
        reasons.append("no release date")
    if normalize(candidate.title) != title_key:
        reasons.append("matched on its original title only")
    if vote_floor > 0 and candidate.votes is None:
        reasons.append(f"votes unknown, under the floor of {vote_floor}")
    elif candidate.votes is not None and candidate.votes < vote_floor:
        reasons.append(f"{candidate.votes} votes, under the floor of {vote_floor}")
    return tuple(reasons)


def _held_if_suspect(lookup: Lookup, title_key: str, vote_floor: int) -> Lookup:
    """``lookup``, or an ambiguous one listing its chosen film when that is suspect."""
    chosen = lookup.chosen
    if chosen is None:
        return lookup
    reasons = suspect_reasons(chosen, title_key, vote_floor=vote_floor)
    if not reasons:
        return lookup
    detail = f"{lookup.detail}, held: {'; '.join(reasons)}"
    return Lookup("ambiguous", None, (chosen,), detail, HELD_BASIS)


# fuzz-target
def film_verdict(
    candidates: Sequence[Candidate],
    title: str,
    title_key: str,
    *,
    work_year: int | None,
    date_year: int | None,
    vote_ratio: float,
    vote_floor: int,
) -> Lookup:
    """A film's answer: ``verdict``, with the most-votes and variant rules where it falls short.

    ``title`` is the row's title as written and ``title_key`` its normalized form: the
    variants rewrite the written title, since normalizing drops what some of them read.
    The year filters run before every rule. Two or more exact titles try the most-votes
    rule; no exact title tries the variants. Whatever those leave is ``verdict``'s
    answer, and ``Lookup.basis`` names the rule that resolved the title. A single exact
    title ``suspect_reasons`` doubts under ``vote_floor`` comes back ambiguous instead,
    listing it, with the basis ``suspect-exact``.
    """
    kept = filter_years(candidates, work_year=work_year, date_year=date_year)
    matched = exact(kept, title_key)
    loosened: Lookup | None = None
    if len(matched) > 1:
        loosened = _most_voted(matched, vote_ratio)
    elif not matched and kept:
        loosened = _by_variant(kept, title)
    if loosened is not None:
        return loosened
    plain = verdict(candidates, title_key, work_year=work_year, date_year=date_year)
    return _held_if_suspect(plain, title_key, vote_floor)


def research_query(
    lookup: Lookup,
    candidates: Sequence[Candidate],
    title: str,
    title_key: str,
    *,
    work_year: int | None,
    date_year: int | None,
) -> str | None:
    """The title the re-search sends, or ``None`` when no second search is sent.

    ``lookup`` is ``film_verdict``'s answer for ``candidates``. A second search is
    sent only when that did not resolve and no year-kept candidate's title is exact
    (so never for a collision), and only when dropping a leading article changes the
    normalized title.
    """
    if lookup.status == "resolved":
        return None
    kept = filter_years(candidates, work_year=work_year, date_year=date_year)
    if exact(kept, title_key):
        return None
    query = leading_article(title)
    key = normalize(query)
    # An empty key ("The !") would meet any candidate whose title normalizes to "".
    return query if key and key != title_key else None


# fuzz-target
def research_verdict(
    first: Sequence[Candidate],
    second: Sequence[Candidate],
    title: str,
    *,
    work_year: int | None,
    date_year: int | None,
) -> Lookup | None:
    """The re-search's answer from both searches' candidates, or ``None`` when none match.

    ``first`` and ``second`` are the two searches' answers. Both are judged together,
    the first's candidates then the second's, each id once, so a second search that
    returns only one of the first's several matches cannot pick it. ``title`` is the
    row's title as written. After the year filters, a candidate matches when its
    title or original title, without a leading article, normalizes to the row's title
    without one: one match resolves, more is ambiguous.
    """
    union: dict[str, Candidate] = {}
    for candidate in (*first, *second):
        union.setdefault(candidate.id, candidate)  # each id's first, in order
    kept = filter_years(tuple(union.values()), work_year=work_year, date_year=date_year)
    key = normalize(leading_article(title))
    found = tuple(c for c in kept if _matches(c, key, leading_article))
    if len(found) == 1:
        detail = "found by searching without a leading article"
        return Lookup("resolved", found[0], found, detail, "re-searched")
    if found:
        detail = f"{len(found)} titles match a search without a leading article"
        return Lookup("ambiguous", None, found[:SHOWN], detail)
    return None
