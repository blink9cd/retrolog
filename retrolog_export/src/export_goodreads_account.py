"""Read the books already on a Goodreads account, and decide each list book's fate there.

``read_account`` takes the one CSV Goodreads' "Export Library" gives
(``goodreads_library_export.csv``); a folder or a zip is refused, since the download
is never either. The file is held to export_account's ``MEMBER_LIMIT`` and read under
its ``csv_field_limit``, the bounds the Letterboxd reader uses, and must carry the
``Title``, ``Author``, ``ISBN13``, ``My Rating``, ``Exclusive Shelf`` and
``Read Count`` columns; any others are ignored. Goodreads writes an ISBN cell as
``="9780000000002"`` (or ``=""`` for none), so a spreadsheet keeps its digits; the
wrapper is dropped, and a plain cell is read as it is.

A book on the account is *read* when its exclusive shelf is ``read``, its rating is
above 0, or its read count is above 0, and *shelved* otherwise (to-read, or a custom
shelf such as a donation pile). A list book whose ISBN-13, or whose title key and
author key, the account has read is ``on-service`` and not sent: sending it again
would add a second read of a book the owner already logged. A shelved book is sent,
and ``shelved_on`` names its shelf, so export can list it to remove from the account
before the import: the import would otherwise update it in place.

A title's keys are parse's ``title_key`` of it with a trailing series parenthetical
(``(Name, #3)``, ``(Name #3)``) dropped, and of that with any subtitle after the
first ``:`` dropped too; two titles match on either. An author's key is the last word
of the first author's name, folded (accents dropped, case folded), so ``Le Guin``
against ``Guin`` or a drift in initials does not split a match. A list book with no
author key matches the account by its title alone, and its entry says so.

Every ``AccountError`` names the problem and the option, never a path or a title.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

# The module, not its names: a test patching export_account.MEMBER_LIMIT bounds this
# reader too, which is how the shared bound is proven shared rather than copied.
import export_account
from export_account import AccountError, csv_field_limit
from export_goodreads_config import SHELF_NAME
from export_select import Fate, Hold, Item, review_entry
from export_types import ResolvedRow
from parse_rows import title_key
from resolve_columns import CREATOR_JOINER

# The option every refusal names: the file is the operator's, its path is not ours.
_OPTION = "--account"
_TITLE, _AUTHOR, _ISBN13 = "Title", "Author", "ISBN13"
_RATING, _SHELF, _COUNT = "My Rating", "Exclusive Shelf", "Read Count"
REQUIRED_COLUMNS = (_TITLE, _AUTHOR, _ISBN13, _RATING, _SHELF, _COUNT)
# The exclusive shelf that alone marks a book read, compared case-folded.
_READ_SHELF = "read"
# How Goodreads wraps an ISBN cell so a spreadsheet keeps its digits.
_WRAP_START, _WRAP_END = '="', '"'
_ISBN13_DIGITS = re.compile(r"[0-9]{13}", re.ASCII)
# A rating or read count: a few ASCII digits, a decimal part allowed. Anything else,
# a longer run among them, reads as 0 rather than reaching float().
_COUNT_CELL = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6})?", re.ASCII)
# The local-file and end-of-directory signatures a zip starts with.
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
# A series parenthetical holds "#" and a number: "(Name, #3)", "(Name #2.5)".
_SERIES_NUMBER = re.compile(r"#[0-9]", re.ASCII)
_SUBTITLE = ":"
# The on-service entry's detail, and its title-alone form (worded as a film's is).
_ON_SERVICE = "already read or rated on the account, so not sent"
_ON_SERVICE_BY_TITLE = (
    f"{_ON_SERVICE}; the book has no author, so matched by title alone"
)
# A book sent now would put a held book's title and author on the account, and the
# next run would skip the held one as on-service: so it waits too, under that kind.
HELD_WITH_ANOTHER = (
    "held with another book of this title and author until resolve settles it"
)
# The held-split detail of a book whose reads book_key keys apart. Goodreads keeps one
# record per book and a re-import overwrites it, so sending both rows would let the
# second clobber the first.
_SPLIT_DETAIL = (
    "another read of this title and author, or of its Open Library work, is keyed "
    "by a different ISBN-13 or work; if they are one book, give every read the "
    "same answer in resolve, with one override for each list title they carry, "
    "then rerun; if they are different books, name one in [[goodreads.omit]]"
)
# The kinds of key book_splits groups books by: a title and author pair, a title key
# alone, and an Open Library work key.
_BY_PAIR, _BY_TITLE, _BY_WORK = "pair", "title", "work"
_Facts = tuple[frozenset[str], frozenset[str]]
_Groups = dict[tuple[str, ...], set[str]]


@dataclass(frozen=True)
class GoodreadsAccount:
    """The books a Goodreads library export lists, read apart from shelved.

    Each ``*_titles`` collection holds a ``(title key, author key)`` pair for each
    of a book's title keys (``title_keys``). A shelved book's ISBN-13 and pairs map
    to its exclusive shelf (``shelf_name``); a key on two shelves keeps the first
    row's.
    """

    read_isbns: frozenset[str]
    read_titles: frozenset[tuple[str, str]]
    shelved_isbns: Mapping[str, str]
    shelved_titles: Mapping[tuple[str, str], str]


@dataclass(frozen=True)
class _Columns:
    """Where the header puts each required column."""

    title: int
    author: int
    isbn13: int
    rating: int
    shelf: int
    count: int


def _without_series(title: str) -> str:
    """``title`` less a trailing series parenthetical, if it ends in one.

    Found by the last "(" rather than a regex, so a long cell costs one scan. A
    parenthetical with no "#<digit>", or holding a ")" of its own, is kept.
    """
    stripped = title.rstrip()
    start = stripped.rfind("(")
    inner = stripped[start + 1 : -1]
    if start < 0 or not stripped.endswith(")") or ")" in inner:
        return title
    return stripped[:start] if _SERIES_NUMBER.search(inner) else title


def title_keys(title: str) -> tuple[str, ...]:
    """The keys ``title`` matches by: the series-free title's, then its subtitle-free.

    A title that is only a series parenthetical keeps its own key; an empty one is cut.
    """
    base = _without_series(title)
    if not title_key(base):
        base = title
    main = base.split(_SUBTITLE, 1)[0]
    return tuple(dict.fromkeys(key for key in map(title_key, (base, main)) if key))


def author_key(name: str) -> str:
    """The last word of ``name``, accents dropped, case folded and punctuation dropped."""
    decomposed = unicodedata.normalize("NFKD", name)
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    words = title_key(folded).split()
    return words[-1] if words else ""


def first_author_key(creator: str) -> str:
    """The ``author_key`` of the first of resolve's ``creator`` names."""
    return author_key(creator.split(CREATOR_JOINER, 1)[0])


def _isbn(cell: str) -> str:
    """The cell's ISBN-13, its ``="..."`` wrapper dropped; "" when it holds none."""
    text = cell.strip()
    if text.startswith(_WRAP_START) and text.endswith(_WRAP_END) and len(text) > 2:
        text = text[len(_WRAP_START) : -len(_WRAP_END)]
    return text if _ISBN13_DIGITS.fullmatch(text) else ""


def _positive(cell: str) -> bool:
    """Whether a rating or read count cell holds a number above 0."""
    text = cell.strip()
    return bool(_COUNT_CELL.fullmatch(text)) and float(text) > 0


def shelf_name(cell: str) -> str:
    """An exclusive shelf cell, stripped and case folded; "" unless a shelf name.

    The name goes into review.md, so a cell breaking Goodreads' own shelf rule
    (``SHELF_NAME``), which no export of Goodreads' writes, is never shown.
    """
    name = cell.strip().casefold()
    return name if SHELF_NAME.fullmatch(name) else ""


def _cell(cells: Sequence[str], at: int) -> str:
    """The cell at ``at``; "" when the row is short of it."""
    return cells[at] if at < len(cells) else ""


def _columns(header: Sequence[str]) -> _Columns:
    """Where ``header`` puts each required column; ``AccountError`` naming any absent."""
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        noun = "column" if len(missing) == 1 else "columns"
        raise AccountError(f"{_OPTION}: header lacks the {', '.join(missing)} {noun}")
    return _Columns(*(header.index(column) for column in REQUIRED_COLUMNS))


@dataclass(frozen=True)
class _Book:
    """One library row: its ISBN-13, its title and author pairs, whether read, its shelf."""

    isbn13: str
    titles: frozenset[tuple[str, str]]
    read: bool
    shelf: str


def _book(cells: Sequence[str], at: _Columns) -> _Book:
    """One CSV row as a ``_Book``; a short row is read with the cells it has."""
    author = author_key(_cell(cells, at.author))
    shelf = shelf_name(_cell(cells, at.shelf))
    read = (
        shelf == _READ_SHELF
        or _positive(_cell(cells, at.rating))
        or _positive(_cell(cells, at.count))
    )
    return _Book(
        isbn13=_isbn(_cell(cells, at.isbn13)),
        titles=frozenset((key, author) for key in title_keys(_cell(cells, at.title))),
        read=read,
        shelf=shelf,
    )


def _books(reader: Iterator[list[str]], at: _Columns) -> list[_Book]:
    """Every row of ``reader`` with an ISBN-13 or a title key; others are skipped."""
    return [
        book for cells in reader if (book := _book(cells, at)).isbn13 or book.titles
    ]


def _account(books: Iterable[_Book]) -> GoodreadsAccount:
    """The account ``books`` make, each read or shelved; a shelved key's first shelf."""
    read_isbns: set[str] = set()
    read_titles: set[tuple[str, str]] = set()
    shelved_isbns: dict[str, str] = {}
    shelved_titles: dict[tuple[str, str], str] = {}
    for book in books:
        if book.read:
            read_isbns.update({book.isbn13} - {""})
            read_titles.update(book.titles)
            continue
        if book.isbn13:
            shelved_isbns.setdefault(book.isbn13, book.shelf)
        for pair in book.titles:
            shelved_titles.setdefault(pair, book.shelf)
    return GoodreadsAccount(
        read_isbns=frozenset(read_isbns),
        read_titles=frozenset(read_titles),
        shelved_isbns=MappingProxyType(shelved_isbns),
        shelved_titles=MappingProxyType(shelved_titles),
    )


# fuzz-target
def parse_library_csv(text: str) -> GoodreadsAccount:
    """The account one library export's text lists (see the module docstring).

    Raises ``AccountError`` when the header lacks a required column, the text is not
    valid CSV, or no row has an ISBN-13 or a title.
    """
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        with csv_field_limit():
            books = _books(reader, _columns(next(reader, [])))
    except csv.Error:
        raise AccountError(f"{_OPTION}: not valid CSV") from None
    if not books:
        raise AccountError(f"{_OPTION}: lists no books")
    return _account(books)


def _file_bytes(path: Path) -> bytes:
    """The file at ``path``, read up to one byte past export_account's MEMBER_LIMIT.

    The one byte over is left for ``export_account.decoded`` to refuse. A folder, a
    missing file and an unreadable one are refused; ``OSError``'s own message, which
    carries the absolute path, never reaches the operator.
    """
    try:
        if path.is_dir():
            raise AccountError(
                f"{_OPTION}: a folder; pass the library export's CSV file itself"
            )
        with path.open("rb") as handle:
            data = handle.read(export_account.MEMBER_LIMIT + 1)
    except FileNotFoundError:
        raise AccountError(f"{_OPTION}: no such file") from None
    except OSError:
        raise AccountError(f"{_OPTION}: cannot be read") from None
    return data


def read_account(path: Path) -> GoodreadsAccount:
    """What the Goodreads library export at ``path`` lists (``GoodreadsAccount``).

    Raises ``AccountError`` when ``path`` is missing, unreadable, a folder or a zip,
    is over export_account's ``MEMBER_LIMIT`` bytes, is not UTF-8 (a byte order mark
    is accepted), or ``parse_library_csv`` refuses its text.
    """
    data = _file_bytes(path)
    if data.startswith(_ZIP_SIGNATURES):
        raise AccountError(f"{_OPTION}: a zip; unzip it and pass the CSV file inside")
    return parse_library_csv(export_account.decoded(_OPTION, data))


@dataclass(frozen=True)
class _ListKeys:
    """A list book's match keys: its title and author pairs, and its authorless titles.

    ``authorless`` holds the title keys of the rows with no author key, which match
    an account title by the title alone.
    """

    pairs: frozenset[tuple[str, str]]
    authorless: frozenset[str]


def _list_keys(book: Item) -> _ListKeys:
    """Every title and author pair ``book``'s rows give, and its authorless titles."""
    pairs: set[tuple[str, str]] = set()
    authorless: set[str] = set()
    for row in book.rows:
        author = first_author_key(row.creator)
        keys = title_keys(row.title)
        pairs.update((key, author) for key in keys if author)
        authorless.update(keys if not author else ())
    return _ListKeys(frozenset(pairs), frozenset(authorless))


def _title_match(keys: _ListKeys, titles: frozenset[tuple[str, str]]) -> str | None:
    """How ``keys`` meet ``titles``: the on-service detail, or None for no match.

    An exact pair is asked first, so a book matched both ways reads as exact.
    """
    if keys.pairs & titles:
        return _ON_SERVICE
    if keys.authorless and any(key in keys.authorless for key, _ in titles):
        return _ON_SERVICE_BY_TITLE
    return None


def _joins_hold(held: _ListKeys, free: _ListKeys) -> bool:
    """Whether sending ``free`` would make ``account_fate`` skip ``held`` next run.

    Sent, ``free`` lands on the account under its pairs, so ``_title_match`` asks
    those. An authorless title of ``free`` lands under whatever author Goodreads
    gives it, so it stands for any author: it joins when it equals any of
    ``held``'s title keys, paired or authorless.
    """
    if _title_match(held, free.pairs) is not None:
        return True
    held_titles = held.authorless | {key for key, _ in held.pairs}
    return not free.authorless.isdisjoint(held_titles)


def account_fate(book: Item, account: GoodreadsAccount, config: object) -> Fate:
    """``book`` skipped as ``on-service`` when the account read it; else sent as it is.

    Read means any row's ISBN-13 is in ``read_isbns``, or a title and author pair is
    in ``read_titles`` (``_title_match``). ``config`` is the service's table, unused
    here: the type admits any table, so the function fits a ``ServiceSpec``. A sent
    book's own review entries are the row module's, never this one's.
    """
    del config  # the account alone decides a book's fate
    if any(row.isbn13 and row.isbn13 in account.read_isbns for row in book.rows):
        return None, [review_entry(book, "on-service", _ON_SERVICE)]
    detail = _title_match(_list_keys(book), account.read_titles)
    if detail is not None:
        return None, [review_entry(book, "on-service", detail)]
    return book, []


def shelved_on(book: Item, account: GoodreadsAccount) -> str | None:
    """The account shelf ``book`` is on, when the account has it shelved; else None.

    Matched as ``account_fate`` matches a read book: any row's ISBN-13 first, then a
    title and author pair, then an authorless title of ``book`` against any shelved
    title. Among several, the first in that order and then in sorted key order wins,
    so a run's answer never depends on set order. The shelf is ``shelf_name``'s, so
    "" when the cell could not be shown. Asked only of a book ``account_fate`` sends.
    """
    for row in book.rows:
        if row.isbn13 and row.isbn13 in account.shelved_isbns:
            return account.shelved_isbns[row.isbn13]
    keys = _list_keys(book)
    for pair in sorted(keys.pairs):
        if pair in account.shelved_titles:
            return account.shelved_titles[pair]
    for (title, _), shelf in sorted(account.shelved_titles.items()):
        if title in keys.authorless:
            return shelf
    return None


def grow_holds(books: Sequence[Item], holds: dict[str, Hold], config: object) -> None:
    """Grow ``holds`` in place to a fixed point, as ``account_fate`` would match.

    A free book whose title and author pairs, put on the account by sending it,
    would make ``account_fate`` skip a held book on the next run is held too, under
    that book's kind, and passes the hold on in turn; an authorless title of it
    matches a held book's title under any author (``_joins_hold``). Stops when a pass
    holds nothing new. ``config`` is unused, as in ``account_fate``.
    """
    del config  # the keys alone decide a hold
    keys = {book.key: _list_keys(book) for book in books}
    grew = True
    while grew:
        held = [
            (keys[book.key], holds[book.key][0]) for book in books if book.key in holds
        ]
        grew = False
        for book in books:
            if book.key in holds:
                continue
            sent = keys[book.key]
            shared = next(
                (kind for other, kind in held if _joins_hold(other, sent)), None
            )
            if shared is not None:
                holds[book.key] = (shared, HELD_WITH_ANOTHER)
                grew = True


def _split_groups(books: Sequence[Item]) -> tuple[_Groups, set[str]]:
    """The items each split key groups, and the title keys some row has no author for.

    Each book adds its key under every title and author pair, every title key (paired
    or authorless) and every work key its rows give.
    """
    groups: _Groups = {}
    authorless: set[str] = set()
    for book in books:
        keys = _list_keys(book)
        split_keys = [
            *((_BY_PAIR, title, author) for title, author in keys.pairs),
            *((_BY_TITLE, title) for title, _ in keys.pairs),
            *((_BY_TITLE, title) for title in keys.authorless),
            *((_BY_WORK, row.ol_work) for row in book.rows if row.ol_work),
        ]
        for split_key in split_keys:
            groups.setdefault(split_key, set()).add(book.key)
        authorless |= keys.authorless
    return groups, authorless


def _facts(book: Item) -> _Facts:
    """``book``'s full titles (``title_keys``' first) and its Open Library works."""
    full = {key for row in book.rows for key in title_keys(row.title)[:1]}
    return frozenset(full), frozenset({row.ol_work for row in book.rows} - {""})


def _apart(one: _Facts, other: _Facts) -> bool:
    """Whether a shared title leaves two items apart: other full titles, other works."""
    (full, works), (other_full, other_works) = one, other
    distinct = full.isdisjoint(other_full) and works.isdisjoint(other_works)
    return distinct and bool(works and other_works)


def book_splits(
    books: Sequence[Item], account: GoodreadsAccount, config: object
) -> dict[str, str]:
    """Each book whose reads ``book_key`` split from another item's, to its detail.

    Two items are one book split apart when they share a work key, or a title and
    author pair (``_split_groups``), or an authorless title of one is any title of
    the other (as in ``_joins_hold``), unless a title alone joins two ``_apart``
    (volumes). Goodreads' ``split_holds``: each is ``held-split``, since the reads
    settle together, unless ``account_fate`` skips the whole group as on-service.
    """
    groups, authorless = _split_groups(books)
    facts = {book.key: _facts(book) for book in books}
    on_service = {
        book.key for book in books if account_fate(book, account, config)[0] is None
    }
    split: set[str] = set()
    for (kind, *value), owners in groups.items():
        if len(owners) < 2 or owners <= on_service:
            continue
        if kind == _BY_WORK:
            split |= owners
        elif kind == _BY_PAIR or value[0] in authorless:
            split |= {
                key
                for key in owners
                if not all(_apart(facts[key], facts[it]) for it in owners - {key})
            }
    return dict.fromkeys(sorted(split), _SPLIT_DETAIL)


def book_key(row: ResolvedRow) -> str:
    """The book a row belongs to: its ISBN-13, else its work key, else title and author."""
    if row.isbn13:
        return f"isbn:{row.isbn13}"
    if row.ol_work:
        return f"work:{row.ol_work}"
    return f"title:{row.title_key}:{first_author_key(row.creator)}"
