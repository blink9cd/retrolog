"""The shapes every retrolog_export module passes between them.

Each is a frozen dataclass, so a row read from resolve's output, the account export,
the operator's export config, and a review entry cannot change on their way to the
import files.
``ReviewEntry.detail`` is written for the operator and is path-free. The few values
and the template scan that two modules must agree on are defined here once too.
"""

from __future__ import annotations

import datetime
import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

# The services export may write import files for. Each imports one medium. A name is
# offered as a --service choice only once export_services.SERVICES holds its entry.
ServiceName = Literal["letterboxd", "goodreads"]
# The media resolve writes CSVs for: a service imports one, and export reads only its.
Medium = Literal["film", "book"]
# Which rows the review note is added to: only those with a comment, or every row.
ReviewNoteRows = Literal["commented", "all"]
# The resolve statuses whose rows are sent to the service by their tmdbID.
SENT_BY_ID = frozenset({"resolved", "override"})
# The start of every tag retrolog_export writes. Defined here, once, because two
# modules rely on it agreeing: export_config refuses a tag without it, and
# export_account reads a tag with it as an earlier import of this tool's.
IMPORT_TAG_PREFIX = "retrolog"
# Stars run 0.5 to 5 in halves, kept doubled as whole numbers 1 to 10. Defined here
# once: export_config refuses a rating outside them, and export_letterboxd writes one.
MIN_HALF_STARS, MAX_HALF_STARS = 1, 10
# The review note's fields, the watch's year and ISO date; and the rating sentence's
# one field, the rating key's word. export_config refuses a template naming any other
# field, and export_letterboxd fills these in.
NOTE_FIELDS = frozenset({"year", "date"})
SENTENCE_FIELDS = frozenset({"rating"})
# The earlier-reads sentence's one field, a merged reread's earlier years joined by
# ", ". export_config refuses a template naming any other, and export_goodreads fills
# it in.
EARLIER_FIELDS = frozenset({"years"})
# The note fields an undated row lacks, so export_letterboxd leaves out a note
# naming any of them. Today it is every note field; kept apart since it means less.
NOTE_DATE_FIELDS = frozenset({"year", "date"})


def template_names_any(template: str, fields: frozenset[str]) -> bool:
    """Whether ``template`` holds a field named in ``fields``.

    ``string.Formatter().parse`` is the parser ``str.format`` itself uses, so the
    answer is what filling the template in would meet.
    """
    parsed = string.Formatter().parse(template)
    return any(name in fields for _, name, _, _ in parsed)


@dataclass(frozen=True)
class ResolvedRow:
    """One row of one of resolve's film or book CSVs, with the fields export reads."""

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
    # The match keys (export_titles.match_keys: the title_key, the folded key, and
    # the hyphen key) of resolve's tmdb_title and tmdb_original_title, each once and
    # never "": the titles the account may know a film by besides the list's, from a
    # resolved film's candidate or an override film's TMDb details. Empty for every
    # other row, and for an override whose details resolve could not read.
    tmdb_title_keys: tuple[str, ...] = ()
    # A book's ISBN-13, 13 digits with a valid check digit, and its Open Library work
    # key, "/works/OL<digits>W"; resolve fills either, both, or neither. A film's are
    # always empty.
    isbn13: str = ""
    ol_work: str = ""

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
class Account:
    """What the account export says of its films, file by file.

    Each set holds the ``FilmKey`` of every row of its files. Which sets match a film
    decides its fate: imported is skipped whole, logged is sent without stars, and
    watched alone is sent in full.
    """

    watched: frozenset[FilmKey] = frozenset()  # watched.csv
    logged: frozenset[FilmKey] = frozenset()  # diary.csv, ratings.csv, reviews.csv
    # The diary.csv and reviews.csv rows tagged by an earlier import, "retrolog...".
    imported: frozenset[FilmKey] = frozenset()
    # Each Watched Date in diary.csv -> the keys of its entries on that date.
    diary_dates: Mapping[datetime.date, frozenset[FilmKey]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    # Each Watched Date in diary.csv -> the keys of its entries an earlier import
    # tagged on that date: how a film is known as imported whatever year either side
    # gives it, since Letterboxd dates a film by its first release and TMDb does not
    # always.
    imported_dates: Mapping[datetime.date, frozenset[FilmKey]] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class Cutoff:
    """The source position from which no row is exported."""

    file: str
    line: int
    title_key: str  # the named row's title, as parse_rows.title_key gives it


@dataclass(frozen=True)
class OmitEntry:
    """One film the config's omit list leaves out: a row's title and year name it."""

    title_key: str  # the entry's title, as parse_rows.title_key gives it
    year: int | None  # None names a film with no year

    def names(self, row: ResolvedRow) -> bool:
        """Whether ``row``'s list title and year are this entry's."""
        return row.title_key == self.title_key and row.year == self.year


@dataclass(frozen=True)
class LetterboxdConfig:
    """The export config's ``[letterboxd]`` table, checked."""

    half_stars: Mapping[str, int]  # rating key -> stars times 2, 1 to 10
    rating_tags: Mapping[str, str]  # rating key -> tag; a key may have none
    tags: tuple[str, ...]  # added to every row, at least one, such as "retrolog"
    watched_date: bool  # write WatchedDate or leave it empty
    review_note: str  # the review note's template; "" turns the note off
    review_note_rows: ReviewNoteRows
    cutoff: Cutoff | None  # None exports every row
    # rating key -> the word the rating sentence prints for it; a config may name none.
    rating_names: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    review_note_rating: str = ""  # the rating sentence, after the note; "" turns it off
    # The films never sent, each listed "omitted": ones Letterboxd lacks, say.
    omit: tuple[OmitEntry, ...] = ()


@dataclass(frozen=True)
class GoodreadsConfig:
    """The export config's ``[goodreads]`` table, checked."""

    half_stars: Mapping[str, int]  # rating key -> stars times 2, 1 to 10
    # Written to every row's Bookshelves, at least one, each once, such as "retrolog".
    shelves: tuple[str, ...]
    # The Date Added an undated row gets; None when the config names none, which
    # export_config.check_undated refuses once any row is undated.
    undated_date_added: datetime.date | None = None
    private_note: str = ""  # the note's template, {year} and {date}; "" turns it off
    private_note_rating: str = ""  # the rating sentence, {rating}; "" turns it off
    # rating key -> the word the rating sentence prints for it; a config may name none.
    rating_names: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    # The earlier-reads sentence, {years}, on a merged reread only; "" turns it off.
    private_note_earlier: str = ""
    cutoff: Cutoff | None = None  # None exports every row
    # The books never sent, each listed "omitted".
    omit: tuple[OmitEntry, ...] = ()
    # account shelf -> the tag shelf a book sent though shelved there adds.
    shelf_tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class ServiceTable(Protocol):
    """What every service's checked config table holds, which a run reads alike.

    ``LetterboxdConfig`` and ``GoodreadsConfig`` each hold these as fields, so the
    rating-key and omit checks and the selection take either table.
    """

    @property
    def half_stars(self) -> Mapping[str, int]:
        """Each rating key's stars, doubled: 1 to 10."""
        ...

    @property
    def cutoff(self) -> Cutoff | None:
        """The row from which none is exported; None exports every row."""
        ...

    @property
    def omit(self) -> tuple[OmitEntry, ...]:
        """The items never sent."""
        ...


@dataclass(frozen=True)
class ExportConfig:
    """The whole export config: one table per service it configures."""

    letterboxd: LetterboxdConfig | None  # None when the file has no [letterboxd]
    goodreads: GoodreadsConfig | None = None  # None when the file has no [goodreads]


@dataclass(frozen=True)
class ReviewEntry:
    """One item of export's review list, covering the rows of one film."""

    kind: str  # see export_select.REVIEW_KINDS
    title: str
    rows: tuple[str, ...]  # ResolvedRow.where of every row covered, sorted
    detail: str  # path-free
    error: bool
