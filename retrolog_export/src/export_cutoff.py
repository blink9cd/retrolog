"""Leave out every row from the operator's cutoff onward.

When the account already holds every film from some point of the lists onward (the
operator has logged live since), ``letterboxd.cutoff`` names the first source row of
that point. ``apply_cutoff`` keeps every undated row and every dated row before it, in
parse's order (date, file, line). ``export_select`` runs it first.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

from export_types import Cutoff, ResolvedRow


class CutoffError(ValueError):
    """The cutoff names no dated row with its title, so nothing can be cut safely."""


def _cut_order(row: ResolvedRow) -> tuple[datetime.date, str, int]:
    """Parse's order for dated rows (date, file, line), which the cutoff follows.

    Only dated rows are ever compared to the cutoff; the ``date.min`` stand-in is for
    the type checker, not a position an undated row could take.
    """
    return (row.date or datetime.date.min, row.source_file, row.source_line)


def apply_cutoff(
    rows: Sequence[ResolvedRow], cutoff: Cutoff | None
) -> tuple[list[ResolvedRow], int]:
    """The rows the cutoff keeps, and how many it cut.

    Raises ``CutoffError`` when no dated row sits at the cutoff's file and line with
    its title, since a numbering slip must not silently cut the wrong rows. The
    message names the config key only, never the position or the title.
    """
    if cutoff is None:
        return list(rows), 0
    named = [
        row
        for row in rows
        if row.date is not None
        and (row.source_file, row.source_line, row.title_key)
        == (cutoff.file, cutoff.line, cutoff.title_key)
    ]
    if not named:
        raise CutoffError(
            "letterboxd.cutoff names no dated row with that title at that position"
        )
    start = min(_cut_order(row) for row in named)
    kept = [row for row in rows if row.date is None or _cut_order(row) < start]
    return kept, len(rows) - len(kept)
