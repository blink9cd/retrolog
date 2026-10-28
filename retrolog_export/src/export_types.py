"""The shapes every retrolog_export module passes between them.

Each is a frozen dataclass, so a row read from resolve's output, the operator's export
config and a review entry cannot change on their way to the import files.
``ReviewEntry.detail`` is written for the operator and is path-free.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

# The services export writes import files for (--service). Each imports one medium.
ServiceName = Literal["letterboxd"]
# Which rows the review note is added to: only those with a comment, or every row.
ReviewNoteRows = Literal["commented", "all"]
# The resolve statuses whose rows are sent to the service by their tmdbID.
SENT_BY_ID = frozenset({"resolved", "override"})


@dataclass(frozen=True)
class ResolvedRow:
    """One row of one of resolve's film CSV files, with the fields export reads."""

    # The CSV's own name, "film-2010.csv".
    source_csv: str
    title: str
    title_key: str  # parse_rows.title_key(title)
    year: int | None  # resolve's "year" column: the work's year, not the watch's
    date: datetime.date | None  # None for a row of an undated file
    rating_raw: str  # a rating key; "" when unrated
    comment: str
    rewatch: bool
    creator: str  # resolve's, names joined by " & "
    status: str  # one of resolve_types.Status
    tmdb_id: str  # "" when the row has none
    source_file: str
    source_line: int
    # The match keys (export_titles.match_keys: the title_key and the folded key)
    # of resolve's tmdb_title and tmdb_original_title, each once and never "": the
    # titles the account may know a resolved film by besides the list's. Empty
    # for every row resolve did not resolve to a TMDb candidate.
    tmdb_title_keys: tuple[str, ...] = ()

    @property
    def where(self) -> str:
        """The row's source position, ``file:line``, as review entries name it."""
        return f"{self.source_file}:{self.source_line}"


@dataclass(frozen=True)
class FilmKey:
    """How the account export names a film."""

    title_key: str
    year: int | None


@dataclass(frozen=True)
class Cutoff:
    """The source position from which no row is exported."""

    file: str
    line: int
    title_key: str  # the named row's title, as parse_rows.title_key gives it


@dataclass(frozen=True)
class LetterboxdConfig:
    """The export config's ``[letterboxd]`` table, checked."""

    half_stars: Mapping[str, int]  # rating key -> stars times 2, 1 to 10
    rating_tags: Mapping[str, str]  # rating key -> tag; a key may have none
    tags: tuple[str, ...]  # added to every row, such as "retrolog"
    watched_date: bool  # write WatchedDate or leave it empty
    review_note: str  # the review note's template; "" turns the note off
    review_note_rows: ReviewNoteRows
    cutoff: Cutoff | None  # None exports every row
    # rating key -> the word the rating sentence prints for it; a config may name none.
    rating_names: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    review_note_rating: str = ""  # the rating sentence, after the note; "" turns it off


@dataclass(frozen=True)
class ExportConfig:
    """The whole export config: one table per service it configures."""

    letterboxd: LetterboxdConfig | None  # None when the file has no [letterboxd]


@dataclass(frozen=True)
class ReviewEntry:
    """One item of export's review list, covering every row of one film."""

    kind: str  # see export_select.REVIEW_KINDS
    title: str
    rows: tuple[str, ...]  # ResolvedRow.where of every row covered, sorted
    detail: str  # path-free
    error: bool
