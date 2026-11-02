"""The services export writes import files for, and what makes each one different.

A ``ServiceSpec`` gathers everything service-specific: the medium it imports, the
import files' name and columns, how its account export is read and matched, and
how one resolve row becomes one import row. Selection, packing, and output take a spec
and know nothing of any one service, so a later service (Goodreads) is a new entry
here, with its own medium, config, and account key, and no change to them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import export_account
import export_letterboxd
from export_types import Account, FilmKey, LetterboxdConfig, ResolvedRow, ServiceName


@dataclass(frozen=True)
class ServiceSpec:
    """One service's import format and account export."""

    name: ServiceName
    # The one medium the service imports; a book service adds "book".
    medium: Literal["film"]
    file_stem: str  # import files are "<file_stem>-001.csv" onward
    columns: tuple[str, ...]  # every import file's header, in order
    # The columns of the operator's own text, checked for a formula start.
    formula_columns: tuple[str, ...]
    # The columns one of which a row must fill for the import to tag it: a row with
    # neither a date nor review text makes no diary entry or review to hold a tag.
    tag_columns: tuple[str, ...]
    read_account: Callable[[Path], Account]
    on_service: Callable[[FilmKey, frozenset[FilmKey]], bool]
    # A row, the config, and whether to withhold its rating (a film already logged).
    row_cells: Callable[[ResolvedRow, LetterboxdConfig, bool], tuple[str, ...]]


# Read-only, so no caller can add or swap a service at run time.
SERVICES: Mapping[ServiceName, ServiceSpec] = MappingProxyType(
    {
        "letterboxd": ServiceSpec(
            name="letterboxd",
            medium="film",
            file_stem="letterboxd",
            columns=export_letterboxd.COLUMNS,
            formula_columns=("Title", "Directors", "Review"),
            tag_columns=("WatchedDate", "Review"),
            read_account=export_account.read_account,
            on_service=export_account.on_service,
            row_cells=export_letterboxd.row_cells,
        ),
    }
)
