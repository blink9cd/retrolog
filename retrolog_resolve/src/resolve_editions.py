"""Pick a book's ISBN-13 from its work's edition list: one rule, in one place.

Pure: nothing here touches the network. ``resolve_book_lookup`` fetches each page of
``/works/<id>/editions.json`` (``resolve_books.editions_url``), hands it to
``parse_editions_page``, and gives the editions it gathered to ``pick_isbn13``, which
picks every book's ISBN-13, a search-matched book's and an override book's alike.

The rule: an English edition carrying a verified ISBN-13 (an ISBN-10 is converted);
of those, the latest published in or before the read year, else the earliest after
it, else, when no edition is dated, the first listed. Editions from a print-on-demand
imprint are passed over unless they are the only ones carrying an ISBN-13.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import resolve_books
import resolve_isbn

# At most this many pages of a work's editions are read (resolve_books.EDITIONS_PAGE_SIZE
# each): enough that a widely read classic's several hundred editions are read whole,
# while each page is one more paced request. A larger work is picked from those pages.
PAGE_CAP: Final = 5
# The print-on-demand imprints. Goodreads often lacks their ISBNs: in a live import
# test, a print-on-demand edition's ISBN-13 was not on Goodreads at all, so a row sent
# by it would not match. Each is matched as whole words anywhere in a publisher name,
# case ignored, which covers the spellings Open Library holds: "CreateSpace",
# "CreateSpace Independent Publishing Platform", "Createspace Independent Pub",
# "[CreateSpace]", "Independently Published", "independently published", "Lulu.com",
# "Lulu Press, Inc.", "LULU".
_PRINT_ON_DEMAND: Final = re.compile(
    r"\b(?:createspace|independently\s+published|lulu)\b", re.IGNORECASE
)
# An edition's language entry for English, as editions.json writes it.
_ENGLISH_KEY: Final = "/languages/eng"
# The year of a (year, edition) pair, the key the pick orders by.
_YEAR: Final = operator.itemgetter(0)
# The ISBN fields an edition holds, the ISBN-13 one read first.
_ISBN_FIELDS: Final = ("isbn_13", "isbn_10")


@dataclass(frozen=True)
class Edition:
    """What the pick needs of one edition."""

    isbn13: str  # its first verified ISBN-13 (an ISBN-10 converted), "" when none
    year: int | None  # the four-digit year of its publish_date, when it holds one
    english: bool  # whether its languages name English
    print_on_demand: bool  # whether a print-on-demand imprint published it


@dataclass(frozen=True)
class EditionsPage:
    """One /works/{id}/editions.json page: its editions, and whether more follow."""

    editions: tuple[Edition, ...]
    more: bool  # the page carries a links.next


def _texts(value: object) -> list[str]:
    """The strings of ``value`` when it is a list, in order; anything else gives []."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def is_print_on_demand(publishers: object) -> bool:
    """Whether any of ``publishers`` (a list of names) is a print-on-demand imprint."""
    return any(_PRINT_ON_DEMAND.search(name) for name in _texts(publishers))


def _isbn13(entry: dict[object, object]) -> str:
    """The edition's first verified ISBN, as an ISBN-13, its ISBN-13 list first."""
    for field in _ISBN_FIELDS:
        for text in _texts(entry.get(field)):
            isbn13 = resolve_isbn.isbn13_or_none(text)
            if isbn13 is not None:
                return isbn13
    return ""


def _english(value: object) -> bool:
    """Whether a ``languages`` list holds the English entry."""
    entries = value if isinstance(value, list) else []
    return any(
        isinstance(entry, dict) and entry.get("key") == _ENGLISH_KEY
        for entry in entries
    )


def _edition(entry: object) -> Edition | None:
    """One editions.json entry as an ``Edition``, or None when it is not an object."""
    if not isinstance(entry, dict):
        return None
    return Edition(
        isbn13=_isbn13(entry),
        year=resolve_books.publish_year(entry.get("publish_date")),
        english=_english(entry.get("languages")),
        print_on_demand=is_print_on_demand(entry.get("publishers")),
    )


def _more(links: object) -> bool:
    """Whether a page's ``links`` names a next page."""
    following = links.get("next") if isinstance(links, dict) else None
    return isinstance(following, str) and bool(following)


# fuzz-target
def parse_editions_page(payload: object) -> EditionsPage | None:
    """A decoded editions.json page, or None when it is not one; never raises.

    None unless ``payload`` is an object with an ``entries`` list. An entry that is
    not an object is skipped; a field of another shape reads as absent.
    """
    if not isinstance(payload, dict):
        return None
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    found = (_edition(entry) for entry in entries)
    editions = tuple(edition for edition in found if edition is not None)
    return EditionsPage(editions, _more(payload.get("links")))


def _read_year_choice(editions: Sequence[Edition], date_year: int | None) -> Edition:
    """The read year's edition of a non-empty ``editions``; a tie keeps list order.

    The latest dated in or before ``date_year`` (any dated one when it is None), else
    the earliest after it, else the first listed.
    """
    dated = [(e.year, e) for e in editions if e.year is not None]
    before = [pair for pair in dated if date_year is None or pair[0] <= date_year]
    if before:
        return max(before, key=_YEAR)[1]
    if dated:
        # Every dated edition left is after date_year.
        return min(dated, key=_YEAR)[1]
    return editions[0]


def pick_isbn13(editions: Sequence[Edition], date_year: int | None) -> str:
    """The ISBN-13 the rule picks from ``editions`` for a row read in ``date_year``.

    "" when no English edition carries a verified ISBN-13.
    """
    usable = [e for e in editions if e.english and e.isbn13]
    preferred = [e for e in usable if not e.print_on_demand] or usable
    return _read_year_choice(preferred, date_year).isbn13 if preferred else ""
