"""List the sent rows whose cell a spreadsheet would read as a formula.

The import files are written cell for cell, since Letterboxd's importer reads every
cell as text. But a spreadsheet opening the file first would run a cell that starts a
formula, so each such row is listed as ``formula-cell`` for the operator to see, and
the cell itself is never changed. ``export_select`` builds the films and review
entries this module reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from export_select import Film, review_entry
from export_types import ReviewEntry

# The characters a spreadsheet reads as the start of a formula when it opens a
# CSV. Export leaves such a cell as it is, since the importer reads it as text, and
# lists the row so the operator never opens the file in a spreadsheet first.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_FORMULA_DETAIL = (
    "cell starts with a character a spreadsheet reads as a formula; upload the file "
    "without opening it in one"
)


def _formula_columns(
    cells: Sequence[str], checked: Mapping[str, int]
) -> tuple[str, ...]:
    """The ``checked`` column names whose cell in ``cells`` starts a formula."""
    return tuple(
        name for name, at in checked.items() if cells[at].startswith(FORMULA_STARTS)
    )


def formula_entries(
    films: Sequence[Film],
    cells: Sequence[Sequence[Sequence[str]]],
    checked: Mapping[str, int],
) -> list[ReviewEntry]:
    """One ``formula-cell`` entry per film with a checked cell that starts a formula.

    ``cells`` holds each film's import rows, in the order of ``films`` and of their
    rows; ``checked`` maps each column to check to its position in a row. An entry
    lists only the rows that have such a cell, and names the columns, never the text.
    """
    entries = []
    for film, rows in zip(films, cells, strict=True):
        found = [
            (row, names)
            for row, row_cells in zip(film.rows, rows, strict=True)
            if (names := _formula_columns(row_cells, checked))
        ]
        if found:
            named = ", ".join(
                dict.fromkeys(name for _, names in found for name in names)
            )
            flagged = Film(film.key, tuple(row for row, _ in found))
            entries.append(
                review_entry(flagged, "formula-cell", f"its {named} {_FORMULA_DETAIL}")
            )
    return entries
