"""One book as one row of a Goodreads import file, and a reread's rows merged into one.

Every row holds every one of ``COLUMNS``, in that order, and a cell with no value is
empty. Goodreads matches an imported row by its ISBN13 when it has one, else by Title
and Author, so ``ISBN`` is always empty: resolve keeps one ISBN-13 a book, and that is
what the import is matched on. The stars, shelves, undated rows' Date Added, and
Private Notes templates all come from the operator's checked ``GoodreadsConfig``; this
module holds no value of the operator's own.

Goodreads keeps one record per book and a re-import overwrites it, so a book read more
than once is sent as one row: ``merge_rereads`` picks the newest read and names the
earlier reads' years, which Private Notes' earlier-reads sentence prints.

``book_fate`` is the service's account check: a book the account has not read is
sent as ``merge_rereads``' one row, the merged reads kept beside it, and
``item_cells`` writes that row with their years. A sent book the account has on a
shelf other than read keeps that shelf: it is listed ``remove-first``, since
the import would update it in place, dropping Private Notes and keeping its own
Date Added, and its row adds the tag ``shelf_tags`` maps that shelf to.
``book_entries`` gives a sent book's review entries. It is the book counterpart of
``export_select.sent_entries``, which is film-shaped (its ``by-title`` means no
tmdbID), so a book's kinds are judged here, on the row ``merge_rereads`` sends.
Every detail is path-free and names no title, though ``remove-first``'s names the
account's shelf: the entry's own fields carry the title.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Sequence

from export_goodreads_account import GoodreadsAccount, account_fate, shelved_on
from export_cells import note_text, sentence_text, stars
from export_select import UNRATED_DETAIL, Fate, Item, review_entry
from export_types import (
    EARLIER_FIELDS,
    GoodreadsConfig,
    ResolvedRow,
    ReviewEntry,
    template_names_any,
)
from resolve_columns import CREATOR_JOINER

COLUMNS = (
    "Title",
    "Author",
    "Additional Authors",
    "ISBN",
    "ISBN13",
    "My Rating",
    "Date Read",
    "Date Added",
    "Bookshelves",
    "Exclusive Shelf",
    "My Review",
    "Private Notes",
)
# Goodreads' own library export joins Additional Authors and Bookshelves with ", ".
_AUTHORS_JOINER = ", "
_SHELVES_JOINER = ", "
_YEARS_JOINER = ", "
# The note, the rating sentence, and the earlier-reads sentence read as one line.
_SENTENCE_JOINER = " "
# Every imported book is one already read.
_EXCLUSIVE_SHELF = "read"
# Each book kind's detail. by-title and undated are shared with films, but their film
# details name a tmdbID and a watched date, so a book has its own; unrated's names
# neither, so it is the film's.
_BOOK_DETAILS = {
    # Goodreads shows no preview before an import, so each check comes after it.
    "by-title": "sent by Title and Author with no ISBN-13; check after the import "
    "that Goodreads matched the right edition",
    "title-only": "sent by Title alone, with no ISBN-13 or author; check after the "
    "import that Goodreads found it",
    "author-missing": "an override whose Open Library work gave no author; run "
    "resolve again to fill it in",
    "unrated": UNRATED_DETAIL,
    "undated": "sent with no read date",
}
# The remove-first detail, naming the account's shelf, and its form for a shelf
# whose name cannot be shown. The book is removed so the import adds it as new.
_REMOVE_FIRST = (
    "on the account's {shelf} shelf; remove it from Goodreads before importing"
)
_REMOVE_FIRST_UNNAMED = (
    "on a shelf of the account other than read; remove it from Goodreads before "
    "importing"
)
# The reread-merged detail, by how many earlier reads were merged: one, or more.
_REREAD_DETAIL = "merged with {count} earlier {noun}: sent once, as the newest read"
_STATUS_OVERRIDE = "override"


def goodreads_date(date: datetime.date) -> str:
    """``date`` as the library export writes one, ``YYYY/MM/DD``, the year in four digits."""
    return f"{date.year:04d}/{date.month:02d}/{date.day:02d}"


def _read_key(row: ResolvedRow) -> tuple[bool, datetime.date, str, int]:
    """How new a read is: dated over undated, then by date, file, and line."""
    dated = row.date is not None
    return (dated, row.date or datetime.date.min, row.source_file, row.source_line)


# fuzz-target
def merge_rereads(
    rows: Sequence[ResolvedRow],
) -> tuple[ResolvedRow, tuple[str, ...]]:
    """One book's reads as its newest row and the earlier reads' years.

    The newest read is the latest by date, then by source file, then by line; a dated
    read counts as newer than an undated one, whose date is unknown. The years are those
    of every other dated read, each once, oldest first; an undated read adds none. One
    row gives no years. Raises ``ValueError`` for no rows.
    """
    if not rows:
        raise ValueError("a book has at least one row")
    ordered = sorted(rows, key=_read_key, reverse=True)
    newest, earlier = ordered[0], ordered[1:]
    years = sorted({row.date.year for row in earlier if row.date is not None})
    return newest, tuple(str(year) for year in years)


def _earlier(years: tuple[str, ...], config: GoodreadsConfig) -> str:
    """The earlier-reads sentence for a merged reread's ``years``; "" when there are none."""
    sentence = config.private_note_earlier
    if not sentence or not years:
        return ""
    if not template_names_any(sentence, EARLIER_FIELDS):
        return sentence.format()
    return sentence.format(years=_YEARS_JOINER.join(years))


def private_notes(
    row: ResolvedRow, config: GoodreadsConfig, earlier_years: tuple[str, ...] = ()
) -> str:
    """Private Notes for ``row``: the note, the rating sentence, the earlier reads.

    Each part goes on every row that has it, joined by a space: the rating sentence on
    a rated row only, the earlier-reads sentence on a merged reread only. The comment is
    never here; it is My Review alone.
    """
    parts = (
        note_text(config.private_note, row),
        sentence_text(config.private_note_rating, row, config.rating_names),
        _earlier(earlier_years, config),
    )
    return _SENTENCE_JOINER.join(part for part in parts if part)


def _date_added(row: ResolvedRow, config: GoodreadsConfig) -> str:
    """The read's date, or for an undated row the config's ``undated_date_added``.

    ``check_undated`` refuses the run before any row gets here without that date, so
    meeting one is a broken guarantee and raises ``ValueError`` rather than leave the
    cell empty, which the import would fill with today.
    """
    if row.date is not None:
        return goodreads_date(row.date)
    if config.undated_date_added is None:
        raise ValueError("an undated row needs undated_date_added")
    return goodreads_date(config.undated_date_added)


def row_cells(
    book: ResolvedRow,
    config: GoodreadsConfig,
    withhold: bool = False,
    *,
    earlier_years: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """One cell per ``COLUMNS`` entry, in that order, for one book's one row.

    ``book`` is the row ``merge_rereads`` chose, and ``earlier_years`` the years it
    gave, and ``tags`` the shelves Bookshelves adds after the config's, each written
    once. A rated row's key must be in ``config.half_stars``: ``check_keys`` refuses
    the run before any row gets here otherwise. ``withhold`` is the service seam's
    withheld rating, which only a Letterboxd film can need; a book never does, so a
    True raises ``ValueError``.
    """
    if withhold:
        raise ValueError("a Goodreads row never withholds its rating")
    first, *rest = book.creator.split(CREATOR_JOINER)
    return (
        book.title,
        first,
        _AUTHORS_JOINER.join(rest),
        "",
        book.isbn13,
        stars(config.half_stars[book.rating_raw]) if book.rating_raw else "",
        goodreads_date(book.date) if book.date is not None else "",
        _date_added(book, config),
        _SHELVES_JOINER.join(dict.fromkeys((*config.shelves, *tags))),
        _EXCLUSIVE_SHELF,
        book.comment,
        private_notes(book, config, earlier_years),
    )


def _identity_kind(row: ResolvedRow) -> str | None:
    """How Goodreads will know the sent ``row``, when that needs a check.

    No ISBN-13 and no author is ``title-only``, even for an override whose work
    lookup failed: Goodreads can match it by its title alone, which is the more
    pressing check. No ISBN-13 with an author is ``by-title``. An override with a
    work key and an ISBN-13 but no author is ``author-missing``: resolve's work
    lookup failed or was skipped, and running it again fills the author in.
    """
    if not row.isbn13:
        return "by-title" if row.creator else "title-only"
    if row.status == _STATUS_OVERRIDE and row.ol_work and not row.creator:
        return "author-missing"
    return None


def _reread_detail(earlier: int) -> str:
    """The reread-merged detail for ``earlier`` merged reads."""
    noun = "read" if earlier == 1 else "reads"
    return _REREAD_DETAIL.format(count=earlier, noun=noun)


def _sent_entry(book: Item, sent: ResolvedRow, kind: str, detail: str) -> ReviewEntry:
    """``review_entry`` for ``book``, titled as its ``sent`` row is.

    ``review_entry`` names an item's first row in upload order, a reread's oldest
    read; but one ISBN-13 or work can key reads the lists titled differently, and
    the operator checks the title Goodreads is sent.
    """
    return dataclasses.replace(review_entry(book, kind, detail), title=sent.title)


def _remove_first_detail(shelf: str) -> str:
    """The remove-first detail for a book on the account's ``shelf``."""
    return _REMOVE_FIRST.format(shelf=shelf) if shelf else _REMOVE_FIRST_UNNAMED


def book_entries(book: Item) -> list[ReviewEntry]:
    """The review entries a sent book earns, in ``REVIEW_KINDS`` order.

    ``remove-first`` first, for a book the account has shelved (its
    ``account_shelf``). Then, judged on the row ``merge_rereads`` sends: its
    identity kind (``_identity_kind``),
    ``reread-merged`` when the book has more than one row (an undated earlier read
    included, which adds no year but is still merged away), then ``unrated`` and
    ``undated``. Each entry covers every row of the book and names the sent row's
    title. Raises ``ValueError`` for a book of no rows.
    """
    sent, _ = merge_rereads(book.rows)
    entries = []
    if book.account_shelf is not None:
        detail = _remove_first_detail(book.account_shelf)
        entries.append(_sent_entry(book, sent, "remove-first", detail))
    identity = _identity_kind(sent)
    if identity is not None:
        entries.append(_sent_entry(book, sent, identity, _BOOK_DETAILS[identity]))
    if len(book.rows) > 1:
        detail = _reread_detail(len(book.rows) - 1)
        entries.append(_sent_entry(book, sent, "reread-merged", detail))
    if not sent.rating_raw:
        entries.append(_sent_entry(book, sent, "unrated", _BOOK_DETAILS["unrated"]))
    if sent.date is None:
        entries.append(_sent_entry(book, sent, "undated", _BOOK_DETAILS["undated"]))
    return entries


def book_fate(book: Item, account: GoodreadsAccount, config: object) -> Fate:
    """``book``'s fate on the account; sent, it goes as its one newest read.

    ``account_fate`` decides whether the account has read it. A book it sends is
    ``merge_rereads``' newest row alone, its other reads kept in ``merged`` for
    ``item_cells`` to name their years, and the account shelf ``shelved_on`` finds
    it on, with ``book_entries``' review entries, which cover every row. ``config``
    is unused, as in ``account_fate``.
    """
    sent, entries = account_fate(book, account, config)
    if sent is None:
        return None, entries
    shelf = shelved_on(book, account)
    newest, _ = merge_rereads(book.rows)
    merged = tuple(row for row in book.rows if row is not newest)
    item = Item(book.key, (newest,), merged=merged, account_shelf=shelf)
    shelved = dataclasses.replace(book, account_shelf=shelf)
    return item, [*entries, *book_entries(shelved)]


def item_cells(book: Item, config: GoodreadsConfig) -> tuple[tuple[str, ...], ...]:
    """The import rows of a book ``book_fate`` sent: one, of its one row.

    The earlier reads' years come from merging that row with its ``merged`` reads
    again, which picks the same row, so Private Notes names them. A book on an
    account shelf ``config.shelf_tags`` maps adds that shelf's tag to Bookshelves.
    """
    newest, years = merge_rereads((*book.rows, *book.merged))
    tag = config.shelf_tags.get(book.account_shelf or "")
    tags = (tag,) if tag else ()
    return (row_cells(newest, config, earlier_years=years, tags=tags),)
