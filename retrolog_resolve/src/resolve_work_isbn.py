"""One ISBN-13 per Open Library work per run: every row resolved to it carries it.

Export sends a book read more than once as one Goodreads row only when its reads
share an ISBN-13 (a reread is sent once, as its newest read), so resolve must not
give one work two. ``work_read`` says which work a row's ISBN-13 is picked for, and
``work_isbn13s`` makes each work's one pick: an override's own ISBN-13 when one of
the work's overrides names it (no request), else the edition rule
(``resolve_editions.pick_isbn13``, through ``resolve_book_lookup.edition_isbn13``) by
the newest read year among the work's rows, else, when the list gives nothing, the
newest read's search ISBN-13. ``resolve_rows`` is the only caller. Each work's list
is read at most once per run, since the reads are grouped by work before any is asked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from resolve_book_lookup import edition_isbn13
from resolve_types import Candidate, InputRow, ResolveOverride, Services, Status


@dataclass(frozen=True)
class WorkRead:
    """One row whose ISBN-13 is its work's pick: the work, the read year, a fallback."""

    ol_work: str  # the work key the row resolved to
    date_year: int | None  # the row's read year, None when undated
    # The ISBN-13 the search's first edition gave, kept when the work's list gives
    # none; "" for an override row, which has no search.
    search_isbn13: str = ""
    # An override's own ISBN-13, which becomes its work's pick; "" for any other row.
    own_isbn13: str = ""


def work_read(
    row: InputRow,
    status: Status,
    chosen: Candidate | None,
    override: ResolveOverride | None,
) -> WorkRead | None:
    """The work whose pick ``row`` takes, or None when its ISBN-13 is not a pick.

    A book the search resolved takes its work's pick, its search ISBN-13 the
    fallback; so does an override naming a work key and no ISBN-13. An override
    naming a work key and its own ISBN-13 keeps it, and supplies it as the work's
    pick; one naming no work key joins no work.
    """
    if override is not None:
        if override.ol_work is None:
            return None
        own = override.isbn13 or ""
        return WorkRead(override.ol_work, row.date_year, own_isbn13=own)
    if status != "resolved" or chosen is None or row.medium != "book":
        return None
    return WorkRead(chosen.id, row.date_year, chosen.isbn13)


def _newest_year(reads: Sequence[WorkRead]) -> int | None:
    """The newest read year among ``reads``, or None when none is dated."""
    return max((r.date_year for r in reads if r.date_year is not None), default=None)


def _read_order(read: WorkRead) -> tuple[bool, int]:
    """A sort key putting a dated read after an undated one, then by year."""
    return (read.date_year is not None, read.date_year or 0)


def _newest_own(reads: Sequence[WorkRead]) -> str:
    """The own ISBN-13 of the newest read whose override names one; "" when none does.

    Two overrides of one work can disagree; the newest read's wins, a tie keeping
    the first in input order, as the search fallback does.
    """
    found = [read for read in reads if read.own_isbn13]
    return max(found, key=_read_order).own_isbn13 if found else ""


def _work_isbn13(work: str, reads: Sequence[WorkRead], services: Services) -> str:
    """One work's pick: an override's own ISBN-13, else the list's, else the search's.

    An override's own ISBN-13 asks nothing: the operator has named the edition.
    """
    return (
        _newest_own(reads)
        or edition_isbn13(work, _newest_year(reads), services)
        or _search_fallback(reads)
    )


def _search_fallback(reads: Sequence[WorkRead]) -> str:
    """The search's ISBN-13 of the newest read that has one; "" when none does.

    Each search group is bounded by its own read year, so two groups of one work
    can give two first editions; taking the newest read's keeps the work's rows on
    one ISBN-13. A tie keeps the first in input order (``max`` keeps the first).
    """
    found = [read for read in reads if read.search_isbn13]
    return max(found, key=_read_order).search_isbn13 if found else ""


def work_isbn13s(reads: Sequence[WorkRead], services: Services) -> dict[str, str]:
    """Each work's one ISBN-13 for this run, keyed by work key; "" when it has none.

    Every row resolved to a work carries the same ISBN-13, because export sends a
    book's reads as one row only when they share it. An override's own ISBN-13 is
    the pick when one names it (``_newest_own``); else the edition list's, by the
    newest read year among the work's ``reads`` (``edition_isbn13``); else the
    newest read's search ISBN-13 (``_search_fallback``). Works are asked in the
    order of their first read, each work's list read once at most.
    """
    by_work: dict[str, list[WorkRead]] = {}
    for read in reads:
        by_work.setdefault(read.ol_work, []).append(read)
    return {work: _work_isbn13(work, rows, services) for work, rows in by_work.items()}
