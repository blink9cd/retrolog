"""Build the Goodreads tests' inputs: invented book rows and ``[goodreads]`` configs.

Every title is invented (``Placeholder Book One``), every author too
(``Invented Author``), and every work key made up (``/works/OL1W``). An ISBN-13 is
built from an invented twelve-digit stem with its check digit computed here, never
copied from a list. Kept apart from ``export_fixtures``, the Letterboxd tests', so each
service's fixtures can grow on their own; it uses the standard library only, like that
module.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from export_fixtures import TomlValue, toml_string, toml_value
from export_types import GoodreadsConfig, ResolvedRow

# ISBN-13's check digit weighs the twelve digits before it 1, 3, 1, 3, and so on, and
# makes the weighted sum a multiple of ten.
_ISBN_STEM_LENGTH = 12
_ISBN_WEIGHTS = (1, 3)
_ISBN_MODULUS = 10


def isbn13(stem: str) -> str:
    """``stem``, twelve digits, followed by its ISBN-13 check digit."""
    if len(stem) != _ISBN_STEM_LENGTH or not stem.isdigit():
        raise ValueError("an ISBN-13 stem is twelve digits")
    total = sum(
        int(digit) * _ISBN_WEIGHTS[index % len(_ISBN_WEIGHTS)]
        for index, digit in enumerate(stem)
    )
    return stem + str(-total % _ISBN_MODULUS)


# One resolved book every row test starts from: dated, rated "2", with no comment.
BOOK = ResolvedRow(
    source_csv="book-2010.csv",
    title="Placeholder Book One",
    title_key="placeholder book one",
    year=1999,
    date=datetime.date(2010, 3, 4),
    rating_raw="2",
    comment="",
    rewatch=False,
    creator="Invented Author",
    status="resolved",
    tmdb_id="",
    source_file="10/read.txt",
    source_line=1,
    isbn13=isbn13("979000000001"),
    ol_work="/works/OL1W",
)


def book_row(**changes: object) -> ResolvedRow:
    """``BOOK`` with ``changes`` applied."""
    # Each test's keywords are real ResolvedRow fields, checked when the row is built.
    return dataclasses.replace(BOOK, **changes)  # type: ignore[arg-type] # see above


# The config every row test starts from: four keys, one of them a half star.
GOODREADS = GoodreadsConfig(
    half_stars=MappingProxyType({"1": 2, "2": 5, "3": 7, "4": 10}),
    shelves=("retrolog",),
    undated_date_added=datetime.date(1900, 1, 1),
)


def goodreads_config(**changes: object) -> GoodreadsConfig:
    """``GOODREADS`` with ``changes`` applied."""
    # Each test's keywords are real GoodreadsConfig fields, checked when it is built.
    return dataclasses.replace(GOODREADS, **changes)  # type: ignore[arg-type] # see above


# ---------------------------------------------------------------------------
# The [goodreads] table as TOML
# ---------------------------------------------------------------------------

# A TOML value a Goodreads test may write: export_fixtures' values, or a local date.
GoodreadsValue = TomlValue | datetime.date

DEFAULT_GOODREADS_RATINGS: Mapping[str, TomlValue] = {"1": 1.0, "2": 2.5}


def _value(value: GoodreadsValue) -> str:
    """``value`` as TOML source text; a date is a bare TOML local date."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    return toml_value(value)


def _table(name: str, entries: Mapping[str, GoodreadsValue], array: bool) -> str:
    """One ``[name]`` table, or ``[[name]]`` item, each key quoted."""
    header = f"[[{name}]]" if array else f"[{name}]"
    lines = [f"\n{header}"]
    lines += [f"{toml_string(key)} = {_value(value)}" for key, value in entries.items()]
    return "\n".join(lines) + "\n"


def goodreads_toml(
    *,
    ratings: Mapping[str, TomlValue] | None = None,
    rating_names: Mapping[str, TomlValue] | None = None,
    cutoff: Mapping[str, TomlValue] | None = None,
    shelf_tags: Mapping[str, TomlValue] | None = None,
    omit_books: Sequence[Mapping[str, TomlValue]] = (),
    omit: Sequence[str] = (),
    head: str = "schema = 1\n",
    **goodreads: GoodreadsValue,
) -> bytes:
    """An export config of ``head`` and a ``[goodreads]`` table.

    The table holds ``shelves = ["retrolog"]`` and the ``ratings`` given (else
    ``DEFAULT_GOODREADS_RATINGS``); ``goodreads`` replaces or adds plain keys, and
    ``omit`` drops any key, ``ratings`` included. ``rating_names``, ``cutoff`` and
    ``shelf_tags`` are written only when given, and ``omit_books`` as one
    ``[[goodreads.omit]]`` each.
    """
    plain: dict[str, GoodreadsValue] = {"shelves": ["retrolog"], **goodreads}
    text = head + _table(
        "goodreads", {k: v for k, v in plain.items() if k not in omit}, array=False
    )
    if "ratings" not in omit:
        chosen = DEFAULT_GOODREADS_RATINGS if ratings is None else ratings
        text += _table("goodreads.ratings", chosen, array=False)
    if rating_names is not None:
        text += _table("goodreads.rating_names", rating_names, array=False)
    if cutoff is not None:
        text += _table("goodreads.cutoff", cutoff, array=False)
    if shelf_tags is not None:
        text += _table("goodreads.shelf_tags", shelf_tags, array=False)
    for entry in omit_books:
        text += _table("goodreads.omit", entry, array=True)
    return text.encode()
