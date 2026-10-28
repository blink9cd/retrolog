"""Decide every row: apply the overrides, look each distinct title up once, and review.

``resolve_all`` is the one entry point. Its order is the contract:

1. An override naming a file and line settles the row there whose medium and
   ``title_key`` it names, with no request. When rows sit on that line but none has
   its title, it is an ``override-mismatch`` error and is not applied. (Two rows can
   share a line: a Word paragraph can hold two entries.)
2. The other rows are grouped by (medium, title_key, work year, date year): one lookup
   per group, so a rewatch in another year is looked up again with its own year bound
   (the Client's cache answers a URL that repeats).
3. A title-only override for the group's medium and title decides it, with no request.
4. Otherwise a book runs the Open Library query chain, then ``resolve_match.verdict``,
   and a film ``resolve_film_lookup.film_lookup``: the TMDb search, judged with
   ``vote_ratio``, one more search without a leading article when
   ``research`` is on and no title was exact, then the credits ``directors``
   names. A film resolved by the most-votes rule, a variant or the re-search is
   listed under a review kind of its own, and its listed candidates get credits just
   as an ambiguous film's do. A ``LookupFailed`` in a group's search makes that group
   ``failed`` and the run goes on (a failed re-search or credits request only notes
   it in the detail); ``TokenRefused`` propagates to the caller.
5. Every override that settled no row, and was not a mismatch, is ``override-unused``.

Every input row comes out once, in input order, its parse cells unchanged apart from
``creator`` and an empty ``year``, which the chosen candidate fills. The search URLs
are built by each service module from the title alone; one bounded_fetch would refuse
(not https, not printable ASCII) raises its ``ValueError`` inside the Client, which
turns it into ``LookupFailed``, so such a title fails its group rather than the run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

import resolve_books as books
from resolve_columns import RESOLVE_COLUMNS as RESOLVE_COLUMNS
from resolve_film_lookup import film_lookup
from resolve_http import Client, LookupFailed, Service
from resolve_match import exact, filter_years, verdict
from resolve_types import (
    Candidate,
    Directors,
    InputRow,
    Lookup,
    Medium,
    ResolveOverride,
    ReviewEntry,
    Services,
    Status,
)

# The review kinds, the errors first so they lead the review page.
REVIEW_KINDS = (
    "override-unused",
    "override-mismatch",
    "failed",
    "ambiguous",
    "not-found",
    "most-voted",
    "variant",
    "re-searched",
    "no-isbn",
    "override-applied",
)
ERROR_KINDS = frozenset({"override-unused", "override-mismatch", "failed"})
# RESOLVE_COLUMNS, the columns resolve adds after parse's, lives in the leaf module
# resolve_columns so a reader of resolve's CSVs need not import this one; it is
# re-exported above (the "as" form) for the callers that read it from here.
# The id columns an override can set, named as its fields are; the TMDb title columns
# after them come only from a chosen candidate.
_ID_COLUMNS = ("isbn13", "ol_work", "tmdb_id")
_FILLED_COLUMNS = RESOLVE_COLUMNS[1:]
_FIRST_STEP: books.Step = "edition-by-year"
# What each lookup status adds to the review; "resolved" adds an entry only for a film
# a loosened rule resolved (kind = its basis) or a book with no ISBN, and
# "override" adds its entry per override instead.
_LOOKUP_KINDS: Mapping[Status, str] = {
    "failed": "failed",
    "ambiguous": "ambiguous",
    "not-found": "not-found",
}
_NO_ISBN = "resolved with no English edition carrying an ISBN"

# (medium, title_key, work_year, date_year): the rows one lookup answers.
_GroupKey = tuple[Medium, str, int | None, int | None]


@dataclass(frozen=True)
class OutputRow:
    """One row as written back: parse's columns, then ``RESOLVE_COLUMNS``."""

    source_csv: str  # the parse CSV it came from, and is written back to
    cells: Mapping[str, str]  # COLUMNS + RESOLVE_COLUMNS


@dataclass(frozen=True)
class _Outcome:
    """What one row came to: its status and the candidate or override behind it."""

    status: Status
    chosen: Candidate | None = None
    override: ResolveOverride | None = None


@dataclass
class _Tally:
    """The run's accumulating state, filled by each ordering rule in turn."""

    rows: Sequence[InputRow]
    outcomes: dict[int, _Outcome] = field(default_factory=dict)
    entries: list[ReviewEntry] = field(default_factory=list)
    # Override index -> the row indexes it settled, in the order it settled them.
    applied: dict[int, list[int]] = field(default_factory=dict)
    mismatched: set[int] = field(default_factory=set)

    def settle(self, indexes: Sequence[int], outcome: _Outcome) -> None:
        """Record ``outcome`` for each row, and the override behind it, if any."""
        for index in indexes:
            self.outcomes[index] = outcome
        if outcome.override is not None:
            self.applied.setdefault(outcome.override.index, []).extend(indexes)

    def places(self, indexes: Sequence[int]) -> tuple[str, ...]:
        """Each row's "file:line", sorted by file then line, each once."""
        found = {(self.rows[i].source_file, self.rows[i].source_line) for i in indexes}
        return tuple(f"{file}:{line}" for file, line in sorted(found))

    def review(
        self,
        kind: str,
        indexes: Sequence[int],
        detail: str,
        candidates: tuple[Candidate, ...] = (),
    ) -> None:
        """Add one review entry about the rows at ``indexes``, titled by the first."""
        first = self.rows[indexes[0]]
        self.entries.append(
            ReviewEntry(
                kind=kind,
                medium=first.medium,
                title=first.title,
                rows=self.places(indexes),
                detail=detail,
                candidates=candidates,
                error=kind in ERROR_KINDS,
            )
        )


# ---------------------------------------------------------------------------
# Rule 1: line overrides
# ---------------------------------------------------------------------------


def _names(override: ResolveOverride, row: InputRow) -> bool:
    """Whether ``override`` names ``row``'s medium and title."""
    return row.medium == override.medium and row.title_key == override.title_key


def _apply_line_overrides(tally: _Tally, overrides: Sequence[ResolveOverride]) -> None:
    """Settle each row a line override names; a line of other titles is a mismatch."""
    at: dict[tuple[str, int], list[int]] = {}
    for index, row in enumerate(tally.rows):
        at.setdefault((row.source_file, row.source_line), []).append(index)
    for override in overrides:
        if override.file is None or override.line is None:
            continue
        here = at.get((override.file, override.line), [])
        named = [i for i in here if _names(override, tally.rows[i])]
        if named:
            tally.settle(named, _Outcome("override", override=override))
        elif here:
            tally.mismatched.add(override.index)
            detail = f"overrides[{override.index}] names another title for this line"
            tally.review("override-mismatch", here, detail)


# ---------------------------------------------------------------------------
# Rules 2-4: groups, title overrides and lookups
# ---------------------------------------------------------------------------


def _groups(tally: _Tally) -> dict[_GroupKey, list[int]]:
    """The unsettled rows by group key, groups in order of their first row."""
    grouped: dict[_GroupKey, list[int]] = {}
    for index, row in enumerate(tally.rows):
        if index not in tally.outcomes:
            key = (row.medium, row.title_key, row.work_year, row.date_year)
            grouped.setdefault(key, []).append(index)
    return grouped


def _stops_book_chain(found: Sequence[Candidate], row: InputRow) -> bool:
    """Whether an edition step's answer ends the chain: an exact title with an ISBN.

    An exact title whose first edition gave no ISBN moves on, so a later step can
    still find the edition resolve records.
    """
    kept = filter_years(found, work_year=row.work_year, date_year=row.date_year)
    return any(candidate.isbn13 for candidate in exact(kept, row.title_key))


def _book_lookup(row: InputRow, services: Services) -> Lookup:
    """The Open Library chain for ``row``'s title: up to three searches."""
    step: books.Step = _FIRST_STEP
    while True:
        url = books.search_url(row.title, row.date_year, step)
        payload = services.client.get_json(services.open_library, url)
        found = books.parse_search(payload, step)
        following = books.next_step(step)
        if following is None or _stops_book_chain(found, row):
            return verdict(
                found, row.title_key, work_year=row.work_year, date_year=row.date_year
            )
        step = following


def _lookup(row: InputRow, services: Services) -> Lookup:
    """What ``row``'s group comes to; a ``LookupFailed`` becomes a failed lookup."""
    try:
        if row.medium == "book":
            return _book_lookup(row, services)
        return film_lookup(row, services)
    except LookupFailed as failed:
        return Lookup("failed", None, (), str(failed))


def _review_lookup(tally: _Tally, indexes: Sequence[int], lookup: Lookup) -> None:
    """Add the review entry a lookup's status calls for, if any."""
    kind = _LOOKUP_KINDS.get(lookup.status)
    if kind is not None:
        tally.review(kind, indexes, lookup.detail, lookup.shown)
        return
    if lookup.basis != "exact":
        # A loosened match resolves its rows and is listed for a check, not an error.
        tally.review(lookup.basis, indexes, lookup.detail, lookup.shown)
        return
    chosen = lookup.chosen
    if chosen is not None and tally.rows[indexes[0]].medium == "book":
        if not chosen.isbn13:
            tally.review("no-isbn", indexes, _NO_ISBN, (chosen,))


def _decide_groups(
    tally: _Tally, overrides: Sequence[ResolveOverride], services: Services
) -> None:
    """Settle every group: by a title-only override, else by one lookup."""
    by_title = {(o.medium, o.title_key): o for o in overrides if o.file is None}
    for (medium, title_key, _, _), indexes in _groups(tally).items():
        override = by_title.get((medium, title_key))
        if override is not None:
            tally.settle(indexes, _Outcome("override", override=override))
            continue
        lookup = _lookup(tally.rows[indexes[0]], services)
        tally.settle(indexes, _Outcome(lookup.status, chosen=lookup.chosen))
        _review_lookup(tally, indexes, lookup)


# ---------------------------------------------------------------------------
# Rule 5 and the overrides' own review entries
# ---------------------------------------------------------------------------


def _override_detail(override: ResolveOverride) -> str:
    """What an applied override set, by key: never the operator's title."""
    if override.unresolvable:
        return f"overrides[{override.index}]: unresolvable"
    keys = [key for key in _ID_COLUMNS if getattr(override, key) is not None]
    return f"overrides[{override.index}]: {', '.join(keys)}"


def _unused_entry(override: ResolveOverride, rows_named: bool) -> ReviewEntry:
    """The error for an override that settled no row.

    It has no row to take a title from, so it is titled by its ``title_key``.
    ``rows_named`` says whether any row has its medium and title: for a title-only
    override that means line overrides settled every one of them first.
    """
    if override.file is None and rows_named:
        detail = "every row with this title is settled by a line override"
        rows: tuple[str, ...] = ()
    elif override.file is None:
        detail = "no row has this medium and title"
        rows = ()
    else:
        detail = "no row with this medium and title at this file and line"
        rows = (f"{override.file}:{override.line}",)
    return ReviewEntry(
        kind="override-unused",
        medium=override.medium,
        title=override.title_key,
        rows=rows,
        detail=f"overrides[{override.index}]: {detail}",
        candidates=(),
        error=True,
    )


def _review_overrides(tally: _Tally, overrides: Sequence[ResolveOverride]) -> None:
    """One entry per override: applied, or unused when it settled nothing."""
    for override in overrides:
        indexes = tally.applied.get(override.index)
        if indexes:
            tally.review("override-applied", indexes, _override_detail(override))
        elif override.index not in tally.mismatched:
            rows_named = any(_names(override, row) for row in tally.rows)
            tally.entries.append(_unused_entry(override, rows_named))


# ---------------------------------------------------------------------------
# Output rows
# ---------------------------------------------------------------------------


def _ids(row: InputRow, outcome: _Outcome) -> dict[str, str]:
    """The id and TMDb title columns: from the override, else the chosen candidate.

    An override names no candidate, so its row's TMDb titles stay empty; so do a
    book's, and an unresolved row's.
    """
    ids = dict.fromkeys(_FILLED_COLUMNS, "")
    override, chosen = outcome.override, outcome.chosen
    if override is not None:
        ids.update(
            (key, value)
            for key in _ID_COLUMNS
            if (value := getattr(override, key)) is not None
        )
    elif chosen is not None and row.medium == "book":
        ids.update(isbn13=chosen.isbn13, ol_work=chosen.id)
    elif chosen is not None:
        ids.update(
            tmdb_id=chosen.id,
            tmdb_title=chosen.title,
            tmdb_original_title=chosen.original_title,
        )
    return ids


def _output_row(row: InputRow, outcome: _Outcome) -> OutputRow:
    """``row`` written back with its outcome: creator, an empty year, and the ids."""
    cells = dict(row.cells)
    chosen = outcome.chosen
    if chosen is not None:
        cells["creator"] = chosen.creator or cells["creator"]
        if not cells["year"] and chosen.year is not None:
            cells["year"] = str(chosen.year)
    cells["resolve_status"] = outcome.status
    cells.update(_ids(row, outcome))
    return OutputRow(row.source_csv, MappingProxyType(cells))


# fuzz-target
def resolve_all(
    rows: Sequence[InputRow],
    overrides: Sequence[ResolveOverride],
    *,
    client: Client,
    open_library: Service,
    tmdb: Service | None,
    directors: Directors,
    vote_ratio: float,
    research: bool,
) -> tuple[tuple[OutputRow, ...], tuple[ReviewEntry, ...]]:
    """Every row with its outcome, in input order, and the review entries.

    ``tmdb`` None fails every film lookup (no token was given). ``vote_ratio`` is the
    most-votes rule's ratio, 0 turning it off, and ``research`` turns the
    second search on; books never take either. Raises
    ``TokenRefused`` when TMDb refuses the token; every other failure is a row's.
    """
    tally = _Tally(rows)
    services = Services(client, open_library, tmdb, directors, vote_ratio, research)
    _apply_line_overrides(tally, overrides)
    _decide_groups(tally, overrides, services)
    _review_overrides(tally, overrides)
    written = tuple(
        _output_row(row, tally.outcomes[index]) for index, row in enumerate(rows)
    )
    return written, tuple(tally.entries)
