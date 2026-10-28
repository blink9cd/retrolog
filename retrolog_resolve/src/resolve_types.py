"""The shapes every retrolog_resolve module passes between them.

Each is a frozen dataclass, so a row read from parse's output, a candidate a service
returned and the answer reached for a title cannot change on their way to the output.
``Lookup.detail`` and ``ReviewEntry.detail`` are written for the operator and are
path-free: they never hold a URL, a token or the operator's contact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # annotation only: importing the HTTP layer here would make every
    # module that reads these shapes load it, and bounded_fetch with it.
    from resolve_http import Client, Service

# The kinds of list a row can come from, as parse names them.
Medium = Literal["book", "film"]
# Which media one run reads and resolves (--medium): one of them, or both.
RunMedium = Literal["book", "film", "all"]
# What one distinct title came to; written to each row's resolve_status column.
Status = Literal["resolved", "override", "ambiguous", "not-found", "failed"]
# Which films get their directors looked up (--directors).
Directors = Literal["none", "ambiguous", "all"]
# Which rule resolved a title: the exact-title rule, the most-votes rule, a
# title variant or a second search without a leading article. A lookup
# that did not resolve keeps the default, "exact".
Basis = Literal["exact", "most-voted", "variant", "re-searched"]


@dataclass(frozen=True)
class InputRow:
    """One row of one of parse's CSV files, with the fields resolve reads parsed out."""

    # The CSV's own name, "book-2010.csv": the row is written back there.
    source_csv: str
    cells: Mapping[str, str]  # every parse column -> its text, exactly as read
    medium: Medium
    title: str
    title_key: str  # parse_rows.title_key(title)
    work_year: int | None  # parse's "year" column
    date_year: int | None  # the year of parse's "date" column; None when undated
    source_file: str
    source_line: int


@dataclass(frozen=True)
class Candidate:
    """One work a search returned, reduced to what matching and review need."""

    id: str  # book: "/works/OL…W"; film: TMDb id digits
    title: str
    original_title: str  # film only; "" for a book
    year: int | None  # first_publish_year, or release_date's year
    creator: str  # book: authors joined by " & "; film: directors, or ""
    isbn13: str  # book: the recorded edition's, "" when none; film: ""
    votes: int | None = None  # film: TMDb's vote_count; None when unknown or a book


@dataclass(frozen=True)
class Lookup:
    """What one distinct title came to."""

    status: Status
    chosen: Candidate | None  # set for "resolved" only
    shown: tuple[Candidate, ...]  # up to three, for "ambiguous"
    detail: str  # path-free; never a URL, a token or a contact
    basis: Basis = "exact"  # the rule that resolved it; "exact" when it did not


@dataclass(frozen=True)
class Services:
    """What a lookup sends requests through, and the film rules' settings.

    ``open_library`` is the book chain's; the rest are the film lookup's.
    """

    client: Client
    open_library: Service
    tmdb: Service | None
    directors: Directors
    vote_ratio: float  # the most-votes ratio; 0 turns the most-votes rule off
    research: bool  # search a film with no exact title again, unarticled


@dataclass(frozen=True)
class ResolveOverride:
    """One [[overrides]] entry of the operator's overrides file."""

    index: int  # its position in the file, for messages
    medium: Medium
    title_key: str
    file: str | None  # both set, or both None
    line: int | None
    isbn13: str | None
    ol_work: str | None
    tmdb_id: str | None
    unresolvable: bool


@dataclass(frozen=True)
class ReviewEntry:
    """One item of resolve's review list, covering every row of one title."""

    kind: str  # see resolve_rows.REVIEW_KINDS
    medium: Medium
    title: str
    rows: tuple[str, ...]  # "file:line" of every row the entry covers, sorted
    detail: str
    candidates: tuple[Candidate, ...]
    error: bool
