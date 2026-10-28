"""Builders for retrolog_review's tests: review.json documents, picks and page files.

Every title, creator and id here is invented. A review is built as a plain dict in
the shape retrolog_resolve's ``review_json`` writes (keys sorted), so a test changes
one field and serializes it with ``as_bytes``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
# The stand-in page files, never the real page/ ones: a test of the page writer must
# not change when the front end does. Named ".in" so the web linters and the
# JavaScript type check, which own the real page files, do not take them for some.
TEMPLATE = FIXTURES / "template.html.in"
STYLE = FIXTURES / "style.css.in"
SCRIPT = FIXTURES / "script.js.in"
STORAGE_SCRIPT = FIXTURES / "storage_script.js.in"
# The names the CLI reads from its page folder.
PAGE_FILES = {
    "review.html": TEMPLATE,
    "review.css": STYLE,
    "review_storage.js": STORAGE_SCRIPT,
    "review.js": SCRIPT,
}

FILM_TITLE = "Placeholder Film Two"
BOOK_TITLE = "Placeholder Book Four"
# The row each medium's default entry lists, which a default pick names it by.
DEFAULT_ROWS: dict[str, list[str]] = {
    "film": ["2010/seen.txt:4"],
    "book": ["2010/read.txt:5"],
}


def candidate(**fields: object) -> dict[str, object]:
    """A candidate with every field resolve writes, ``fields`` replacing any."""
    base: dict[str, object] = {
        "creator": "Director Nine",
        "id": "1000003",
        "isbn13": "",
        "original_title": "",
        "title": FILM_TITLE,
        "votes": 120,
        "year": 1927,
    }
    base.update(fields)
    return base


def entry(kind: object = "ambiguous", **fields: object) -> dict[str, object]:
    """A review entry of ``kind``, ``fields`` replacing any of its others."""
    base: dict[str, object] = {
        "candidates": [candidate(), candidate(id="1000004", votes=None, year=None)],
        "detail": "2 exact titles",
        "error": kind in ("override-unused", "override-mismatch", "failed"),
        "kind": kind,
        "medium": "film",
        "rows": list(DEFAULT_ROWS["film"]),
        "title": FILM_TITLE,
    }
    base.update(fields)
    return base


def book_entry(kind: str = "ambiguous", **fields: object) -> dict[str, object]:
    """A book entry: two Open Library candidates, the first with an ISBN."""
    books = [
        candidate(
            creator="Author Four",
            id="/works/OL1000013W",
            isbn13="9780306406157",
            title=BOOK_TITLE,
            votes=None,
            year=1901,
        ),
        candidate(
            creator="Author Five",
            id="/works/OL1000014W",
            title=BOOK_TITLE,
            votes=None,
            year=1901,
        ),
    ]
    values: dict[str, object] = {
        "candidates": books,
        "medium": "book",
        "rows": list(DEFAULT_ROWS["book"]),
        "title": BOOK_TITLE,
    }
    values.update(fields)
    return entry(kind, **values)


def review(
    entries: Sequence[Mapping[str, object]] = (), medium: str = "all"
) -> dict[str, object]:
    """A review document holding ``entries``, its counts left as resolve fills them."""
    return {
        "counts": {},
        "entries": [dict(item) for item in entries],
        "medium": medium,
        "schema_version": 1,
    }


def as_bytes(document: object) -> bytes:
    """``document`` as resolve writes it: sorted, indented UTF-8 JSON."""
    text = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    """The hex SHA-256 of ``data``, as a picks file carries it."""
    return hashlib.sha256(data).hexdigest()


def pick(
    answer: object,
    title: object = FILM_TITLE,
    medium: object = "film",
    rows: object = None,
) -> dict[str, object]:
    """One pick of the picks file, naming its entry by medium, title and rows.

    ``rows`` defaults to the rows ``entry`` or ``book_entry`` gives that medium.
    """
    default = DEFAULT_ROWS.get(medium, []) if isinstance(medium, str) else []
    named = list(default) if rows is None else rows
    return {"medium": medium, "title": title, "rows": named, "answer": answer}


def picks(review_data: bytes, items: Sequence[object] = ()) -> dict[str, object]:
    """A picks document keyed to ``review_data``, holding ``items``."""
    return {"schema": 1, "review_sha256": sha256(review_data), "picks": list(items)}
