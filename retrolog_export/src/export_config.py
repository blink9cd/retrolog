"""Load and check the operator's export config, and check it covers the rows' ratings.

The file is ``schema = 1`` plus one table per service, ``[letterboxd]``,
``[goodreads]``, or both. ``[letterboxd]`` holds ``watched_date``, ``tags``,
``review_note``, ``review_note_rows``, and a ``ratings`` table mapping each rating
key to stars. It may also hold ``rating_tags``, ``rating_names``,
``review_note_rating``, ``cutoff`` (whose ``title`` is kept as parse's
``title_key``, so spacing, case, and punctuation do not matter), and ``omit``, a
list of films never sent, each a ``title`` kept the same way and an optional
``year``. Every tag, in ``tags`` and ``rating_tags`` alike, must start with
``export_types.IMPORT_TAG_PREFIX``, and ``tags`` must hold at least one, so every
row carries the tag a rerun finds an earlier import by. ``[goodreads]`` holds
``ratings`` and ``shelves``, and may hold ``undated_date_added``, the note keys
``private_note``, ``private_note_rating``, ``rating_names``, and
``private_note_earlier``, and ``cutoff`` and ``omit`` as above; its own checks are
``export_goodreads_config``'s.

The value checks are parse's ``parse_checks`` helpers where one fits, so a refusal
reads the way a rules-file refusal does: it names the TOML key
(``letterboxd.ratings.b/c``) and never quotes the value, which can be the operator's
own list text. Every helper takes the key it checks as ``where``, and each check of
the rows takes its service's table name, so no message names a table of its own
accord and a second service's table reads the same way.
"""

from __future__ import annotations

import string as string_module
import unicodedata
from collections.abc import Mapping, Sequence
from types import MappingProxyType

import export_goodreads_config as goodreads_checks
import parse_checks as checks
from export_types import (
    EARLIER_FIELDS,
    IMPORT_TAG_PREFIX,
    MAX_HALF_STARS,
    MIN_HALF_STARS,
    NOTE_FIELDS,
    SENTENCE_FIELDS,
    Cutoff,
    ExportConfig,
    GoodreadsConfig,
    LetterboxdConfig,
    OmitEntry,
    ResolvedRow,
    ServiceTable,
    template_names_any,
)
from parse_checks import RulesError
from parse_rows import title_key

_SCHEMA = 1
_TOP_KEYS = frozenset({"schema", "letterboxd", "goodreads"})
_LETTERBOXD_REQUIRED = frozenset(
    {"watched_date", "tags", "review_note", "review_note_rows", "ratings"}
)
_LETTERBOXD_KEYS = _LETTERBOXD_REQUIRED | {
    "rating_tags",
    "rating_names",
    "review_note_rating",
    "cutoff",
    "omit",
}
_CUTOFF_KEYS = frozenset({"file", "line", "title"})
_OMIT_KEYS = frozenset({"title", "year"})
_STARS_MESSAGE = "must be stars from 0.5 to 5 in halves"
# A Letterboxd tag: the importer splits Tags on commas, so a tag holds none, nor a
# quote or a control character that could change how the cell reads back.
_MAX_TAG_LENGTH = 50
_TAG_FORBIDDEN = frozenset(",\"'")
_TAG_MESSAGE = (
    f"a tag must be 1 to {_MAX_TAG_LENGTH} characters with no comma, quote, "
    "or control character"
)
# Every tag starts with IMPORT_TAG_PREFIX, so none collides with a tag the account
# already uses, the import's tagged entries can be found from its tag pages, and the
# next run's account read knows them. Case counts: Letterboxd keeps a tag as written.
_PREFIX_MESSAGE = f"a tag must start with {IMPORT_TAG_PREFIX}"
# A rerun finds an earlier import only by its tag, so every row must carry one.
_NO_TAGS_MESSAGE = "must hold at least one tag"
# The review note: only the watch's year and date (NOTE_FIELDS) may be filled in.
_MAX_NOTE_LENGTH = 500
_NOTE_MESSAGE = (
    "may hold only the placeholders {year} and {date}, and at most "
    f"{_MAX_NOTE_LENGTH} characters"
)
# The rating sentence: only the rating key's word (SENTENCE_FIELDS) may be filled in.
_SENTENCE_MESSAGE = (
    f"may hold only the placeholder {{rating}}, and at most {_MAX_NOTE_LENGTH} "
    "characters"
)
# The earlier-reads sentence: only the earlier years (EARLIER_FIELDS).
_EARLIER_MESSAGE = (
    f"may hold only the placeholder {{years}}, and at most {_MAX_NOTE_LENGTH} "
    "characters"
)
# A rating key's word: plain text the note prints, so no control character.
_MAX_NAME_LENGTH = 50
_NAME_MESSAGE = f"must be 1 to {_MAX_NAME_LENGTH} characters with no control character"


class ConfigError(Exception):
    """The export config is unusable; the message names the TOML key, never a value."""


def _keyed(
    where: str, value: object, allowed: frozenset[str], required: frozenset[str]
) -> dict[str, object]:
    """A table holding only ``allowed`` keys, every ``required`` one among them."""
    table = checks.table(where, value)
    prefix = f"{where}: " if where else ""
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{prefix}unknown key {unknown[0]!r}")
    missing = sorted(required - set(table))
    if missing:
        dotted = f"{where}." if where else ""
        raise ConfigError(f"{dotted}{missing[0]}: missing")
    return table


def _half_stars(where: str, value: object) -> int:
    """A star value from 0.5 to 5 in halves, as a whole number of half stars."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{where}: {_STARS_MESSAGE}")
    doubled = value * 2
    # NaN fails the range test, so int() below never meets it or an infinity.
    if not MIN_HALF_STARS <= doubled <= MAX_HALF_STARS or doubled != int(doubled):
        raise ConfigError(f"{where}: {_STARS_MESSAGE}")
    return int(doubled)


def _ratings(where: str, value: object) -> Mapping[str, int]:
    """The ``ratings`` table: each non-empty rating key mapped to its half stars."""
    table = checks.table(where, value)
    if "" in table:
        raise ConfigError(f"{where}: a rating key must be non-empty")
    return MappingProxyType(
        {key: _half_stars(f"{where}.{key}", stars) for key, stars in table.items()}
    )


def _tag(where: str, value: object) -> str:
    """One tag: a short string starting with ``IMPORT_TAG_PREFIX``.

    It holds no comma, quote, or control character.
    """
    if not isinstance(value, str):
        raise ConfigError(f"{where}: must be a non-empty string")
    if (
        not 1 <= len(value) <= _MAX_TAG_LENGTH
        or any(char in _TAG_FORBIDDEN for char in value)
        or any(unicodedata.category(char) == "Cc" for char in value)
    ):
        raise ConfigError(f"{where}: {_TAG_MESSAGE}")
    if not value.startswith(IMPORT_TAG_PREFIX):
        raise ConfigError(f"{where}: {_PREFIX_MESSAGE}")
    return value


def _tags(where: str, value: object) -> tuple[str, ...]:
    """The ``tags`` list: at least one item, every item a tag."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: must be a list of strings")
    if not value:
        raise ConfigError(f"{where}: {_NO_TAGS_MESSAGE}")
    return tuple(_tag(where, item) for item in value)


def _rating_tags(
    where: str, value: object, ratings: Mapping[str, int], ratings_where: str
) -> Mapping[str, str]:
    """The optional ``rating_tags`` table, each key one ``ratings`` already maps.

    ``ratings_where`` is the key ``ratings`` was read from, which a refusal names.
    """
    table = checks.table(where, value)
    for key in table:
        if key not in ratings:
            raise ConfigError(f"{where}.{key}: not a key of {ratings_where}")
    return MappingProxyType(
        {key: _tag(f"{where}.{key}", tag) for key, tag in table.items()}
    )


def _template(where: str, value: object, allowed: frozenset[str], message: str) -> str:
    """A note template: "" or text whose only fields are bare names in ``allowed``.

    ``string.Formatter().parse`` is the parser ``str.format`` itself uses, so a
    template it accepts here fills in later exactly as checked. A conversion or format
    spec is refused too: the note is text, not a format language.
    """
    if not isinstance(value, str):
        raise ConfigError(f"{where}: must be a string")
    try:
        fields = list(string_module.Formatter().parse(value))
    except ValueError:
        raise ConfigError(f"{where}: {message}") from None
    if len(value) > _MAX_NOTE_LENGTH or any(
        name is not None and (name not in allowed or spec or conversion)
        for _, name, spec, conversion in fields
    ):
        raise ConfigError(f"{where}: {message}")
    return value


def _rating_names(
    where: str, value: object, ratings: Mapping[str, int], ratings_where: str
) -> Mapping[str, str]:
    """The optional ``rating_names`` table, each key one ``ratings`` already maps.

    ``ratings_where`` is the key ``ratings`` was read from, which a refusal names.
    """
    table = checks.table(where, value)
    names: dict[str, str] = {}
    for key, name in table.items():
        if key not in ratings:
            raise ConfigError(f"{where}.{key}: not a key of {ratings_where}")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= _MAX_NAME_LENGTH
            or any(unicodedata.category(char) == "Cc" for char in name)
        ):
            raise ConfigError(f"{where}.{key}: {_NAME_MESSAGE}")
        names[key] = name
    return MappingProxyType(names)


def _names_every_key(
    where: str,
    sentence: tuple[str, str],
    names: Mapping[str, str],
    ratings: Mapping[str, int],
) -> None:
    """Refuse a ``{rating}`` sentence while a rating key has no word to print.

    ``sentence`` is the sentence's key in the table and its template, so the refusal
    names the key that asked for the words.
    """
    key_name, template = sentence
    if not template_names_any(template, SENTENCE_FIELDS):
        return
    for key in ratings:
        if key not in names:
            raise ConfigError(
                f"{where}.rating_names.{key}: missing, and {key_name} names {{rating}}"
            )


def _cutoff(where: str, value: object) -> Cutoff:
    """The optional ``cutoff`` table: a source file, a line, and that line's title."""
    table = _keyed(where, value, _CUTOFF_KEYS, _CUTOFF_KEYS)
    key = title_key(checks.string(f"{where}.title", table["title"]))
    if not key:
        raise ConfigError(f"{where}.title: has no letter or digit to match a row by")
    return Cutoff(
        file=checks.relative_file(f"{where}.file", table["file"]),
        line=checks.integer(f"{where}.line", table["line"], 1, checks.NO_LIMIT),
        title_key=key,
    )


def _omit_entry(where: str, value: object) -> OmitEntry:
    """One ``omit`` entry: a ``title`` with a letter or digit, and an optional year."""
    table = _keyed(where, value, _OMIT_KEYS, frozenset({"title"}))
    key = title_key(checks.string(f"{where}.title", table["title"]))
    if not key:
        raise ConfigError(f"{where}.title: has no letter or digit to match a row by")
    year = table.get("year")
    return OmitEntry(
        title_key=key,
        year=None
        if year is None
        else checks.integer(f"{where}.year", year, 1, checks.NO_LIMIT),
    )


def _omit(where: str, value: object) -> tuple[OmitEntry, ...]:
    """The optional ``omit`` list: its entries in order, numbered from 1, no film twice.

    The number is the entry's place in the file, so a refusal names it without
    quoting the operator's title.
    """
    if not isinstance(value, list):
        raise ConfigError(f"{where}: must be a list of tables")
    entries: list[OmitEntry] = []
    for number, item in enumerate(value, start=1):
        entry = _omit_entry(f"{where}[{number}]", item)
        if entry in entries:
            first = entries.index(entry) + 1
            raise ConfigError(
                f"{where}[{number}]: names the same film as {where}[{first}]"
            )
        entries.append(entry)
    return tuple(entries)


def _letterboxd(value: object) -> LetterboxdConfig:
    """The ``[letterboxd]`` table as a ``LetterboxdConfig``."""
    where = "letterboxd"
    table = _keyed(where, value, _LETTERBOXD_KEYS, _LETTERBOXD_REQUIRED)
    if not isinstance(table["watched_date"], bool):
        raise ConfigError(f"{where}.watched_date: must be true or false")
    rows = table["review_note_rows"]
    if rows not in ("commented", "all"):
        raise ConfigError(f"{where}.review_note_rows: must be commented or all")
    ratings_where = f"{where}.ratings"
    ratings = _ratings(ratings_where, table["ratings"])
    names = _rating_names(
        f"{where}.rating_names", table.get("rating_names", {}), ratings, ratings_where
    )
    sentence = _template(
        f"{where}.review_note_rating",
        table.get("review_note_rating", ""),
        SENTENCE_FIELDS,
        _SENTENCE_MESSAGE,
    )
    _names_every_key(where, ("review_note_rating", sentence), names, ratings)
    return LetterboxdConfig(
        half_stars=ratings,
        rating_tags=_rating_tags(
            f"{where}.rating_tags", table.get("rating_tags", {}), ratings, ratings_where
        ),
        tags=_tags(f"{where}.tags", table["tags"]),
        watched_date=table["watched_date"],
        review_note=_template(
            f"{where}.review_note", table["review_note"], NOTE_FIELDS, _NOTE_MESSAGE
        ),
        review_note_rows="all" if rows == "all" else "commented",
        cutoff=_cutoff(f"{where}.cutoff", table["cutoff"])
        if "cutoff" in table
        else None,
        rating_names=names,
        review_note_rating=sentence,
        omit=_omit(f"{where}.omit", table.get("omit", [])),
    )


def _goodreads(value: object) -> GoodreadsConfig:
    """The ``[goodreads]`` table as a ``GoodreadsConfig``."""
    where = "goodreads"
    table = _keyed(
        where,
        value,
        goodreads_checks.GOODREADS_KEYS,
        goodreads_checks.GOODREADS_REQUIRED,
    )
    ratings_where = f"{where}.ratings"
    ratings = _ratings(ratings_where, table["ratings"])
    names = _rating_names(
        f"{where}.rating_names", table.get("rating_names", {}), ratings, ratings_where
    )
    sentence = _template(
        f"{where}.private_note_rating",
        table.get("private_note_rating", ""),
        SENTENCE_FIELDS,
        _SENTENCE_MESSAGE,
    )
    _names_every_key(where, ("private_note_rating", sentence), names, ratings)
    return GoodreadsConfig(
        half_stars=ratings,
        shelves=goodreads_checks.shelves(f"{where}.shelves", table["shelves"]),
        undated_date_added=goodreads_checks.undated_date_added(
            f"{where}.undated_date_added", table.get("undated_date_added")
        ),
        private_note=_template(
            f"{where}.private_note",
            table.get("private_note", ""),
            NOTE_FIELDS,
            _NOTE_MESSAGE,
        ),
        private_note_rating=sentence,
        rating_names=names,
        private_note_earlier=_template(
            f"{where}.private_note_earlier",
            table.get("private_note_earlier", ""),
            EARLIER_FIELDS,
            _EARLIER_MESSAGE,
        ),
        cutoff=_cutoff(f"{where}.cutoff", table["cutoff"])
        if "cutoff" in table
        else None,
        omit=_omit(f"{where}.omit", table.get("omit", [])),
        shelf_tags=goodreads_checks.shelf_tags(
            f"{where}.shelf_tags", table.get("shelf_tags", {})
        ),
    )


def _document(data: bytes) -> dict[str, object]:
    """The file's top-level table, its schema checked."""
    try:
        decoded = checks.decode(data)
    except RulesError:
        raise ConfigError("export config: not valid UTF-8 TOML") from None
    document = _keyed("", decoded, _TOP_KEYS, frozenset({"schema"}))
    schema = document["schema"]
    if isinstance(schema, bool) or schema != _SCHEMA:
        raise ConfigError(f"schema: this version reads schema {_SCHEMA} only")
    return document


# fuzz-target
def load_config(data: bytes) -> ExportConfig:
    """Check the export config's bytes and return it.

    Raises ``ConfigError`` naming the first key at fault.
    """
    document = _document(data)
    try:
        letterboxd = (
            _letterboxd(document["letterboxd"]) if "letterboxd" in document else None
        )
        goodreads = (
            _goodreads(document["goodreads"]) if "goodreads" in document else None
        )
    except RulesError as error:
        # parse_checks' helpers, and export_goodreads_config's, name the key the same
        # way; only the class differs.
        raise ConfigError(str(error)) from None
    return ExportConfig(letterboxd=letterboxd, goodreads=goodreads)


def check_keys(
    config: ServiceTable,
    rows: Sequence[ResolvedRow],
    *,
    where: str,
) -> None:
    """Refuse, naming it, the first rating key a row carries that ``ratings`` lacks.

    ``where`` is the config's table, ``letterboxd`` or ``goodreads``. An unrated row
    (``rating_raw`` empty) needs no key. Checked before anything is written, so a code
    the config forgot stops the run rather than dropping a rating.
    """
    for row in rows:
        if row.rating_raw and row.rating_raw not in config.half_stars:
            raise ConfigError(
                f"{where}.ratings.{row.rating_raw}: missing, and a row is rated with it"
            )


def check_undated(
    config: GoodreadsConfig, rows: Sequence[ResolvedRow], *, where: str
) -> None:
    """Refuse an undated row while ``undated_date_added`` names no date.

    ``where`` is the config's table, as ``check_keys`` takes it. Checked before
    anything is written: an undated book's Date Added is that date, and without one
    the import would date it today and sort it above current reading.
    """
    if config.undated_date_added is None and any(row.date is None for row in rows):
        raise ConfigError(f"{where}.undated_date_added: missing, and a row is undated")


def check_omit(
    config: ServiceTable,
    rows: Sequence[ResolvedRow],
    *,
    where: str,
) -> None:
    """Refuse, by its number, the first ``omit`` entry that names none of ``rows``.

    ``where`` is the config's table, as ``check_keys`` takes it. ``rows`` is every
    row read, the cutoff's included, so a moved cutoff never makes an entry stale; an
    entry naming nothing is a typo or a film gone from the lists, and would otherwise
    leave out nothing while the operator thinks it does.
    """
    for number, entry in enumerate(config.omit, start=1):
        if not any(entry.names(row) for row in rows):
            raise ConfigError(f"{where}.omit[{number}]: matches no row")
