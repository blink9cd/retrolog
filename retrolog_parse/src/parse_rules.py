"""Load and check the operator's rules file: everything parse must not guess.

The rules file is TOML (``samples/rules.example.toml`` explains every key). It says
which file names hold which medium, what each rating letter, color and glyph means,
which lines are headings rather than entries, how folder names and words date an
entry, and which single lines need a hand-made correction.

``load_rules`` checks the whole file before any source is read, so a typo stops the
run up front instead of silently changing the output: unknown keys are refused by name,
every pattern is compiled and its groups counted, and every rating key used anywhere
must be defined by a letter or a color. A ``RulesError`` message names the table and
key (``ratings.colors: ...``) and never quotes a value or a path, so it can be printed
as is.
"""

from __future__ import annotations

import datetime
import fnmatch
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

import parse_checks as checks
from parse_checks import RulesError as RulesError
from parse_types import Medium, RatingKey

# The only schema this module reads; a later incompatible layout bumps it.
_SCHEMA = 1
# The largest Euclidean distance between two RGB colors: sqrt(3 * 255**2) is 441.7.
_MAX_COLOR_DISTANCE = 442
# One rating letter, and one "RRGGBB" color.
_LETTER = re.compile(r"[a-z]")
_HEX_COLOR = re.compile(r"[0-9A-Fa-f]{6}")
# A glyph as the Word reader spells it: "<Font>/<HEX4>".
_GLYPH = re.compile(r"(?P<font>[^/]+)/(?P<code>[0-9A-Fa-f]{4})")
_MONTHS = range(1, 13)
_CENTURY_STEP = 100
# A split code or rating names exactly two parts.
_PAIR = checks.PAIR
_MEDIA: Mapping[str, Medium] = MappingProxyType({"book": "book", "film": "film"})
Action = Literal["skip", "rating", "title", "unrated"]
_ACTIONS: Mapping[str, Action] = MappingProxyType(
    {"skip": "skip", "rating": "rating", "title": "title", "unrated": "unrated"}
)
# The actions whose override carries a value; the others must not.
_VALUED_ACTIONS = frozenset({"rating", "title"})

# Every table's (required keys, optional keys). "" is the top level. Free-form tables
# (letters, colors, shapes, seasons) are absent: their keys are data, checked by shape.
_TABLE_KEYS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = MappingProxyType(
    {
        "": (
            frozenset({"schema", "media", "ratings", "lines", "dates"}),
            frozenset({"shapes", "terms", "overrides"}),
        ),
        "media": (frozenset(), frozenset(_MEDIA)),
        "ratings": (
            frozenset({"letters", "split_separator", "colors", "color_tolerance"}),
            frozenset({"legend_order"}),
        ),
        "lines": (frozenset({"headings", "sections", "progress"}), frozenset()),
        "dates": (
            frozenset(
                {
                    "two_year_folder",
                    "one_year_folder",
                    "century",
                    "earlier_words",
                    "later_words",
                    "explicit_year",
                    "seasons",
                }
            ),
            frozenset(),
        ),
        "terms": (frozenset({"file", "lines", "date", "expect"}), frozenset()),
        "overrides": (
            frozenset({"file", "line", "expect", "action"}),
            frozenset({"value"}),
        ),
    }
)


@dataclass(frozen=True)
class TermBlock:
    """Lines of one file that all take one date the folder name cannot give."""

    file: str
    first_line: int
    last_line: int
    date: datetime.date
    expect: str


@dataclass(frozen=True)
class Override:
    """A hand-made correction for one source line, checked against its text."""

    file: str
    line: int
    expect: str
    action: Action
    value: str | None  # rating key for "rating", the title for "title"; None otherwise


@dataclass(frozen=True)
class DateRules:
    """How folder names and words in a file name date an entry."""

    two_year_folder: re.Pattern[str]
    one_year_folder: re.Pattern[str]
    century: int
    earlier_words: tuple[str, ...]
    later_words: tuple[str, ...]
    explicit_year: tuple[re.Pattern[str], ...]
    seasons: Mapping[str, int]


@dataclass(frozen=True)
class Rules:
    """The whole checked rules file."""

    media: tuple[tuple[Medium, tuple[str, ...]], ...]  # (medium, file-name globs)
    letters: Mapping[str, RatingKey]  # lower-case letter -> key
    split_separator: str
    colors: Mapping[str, RatingKey]  # "RRGGBB" -> key
    color_tolerance: int  # max Euclidean RGB distance
    legend_order: tuple[RatingKey, ...]  # () when not checked
    shapes: Mapping[str, frozenset[RatingKey]]  # glyph -> keys it may carry
    headings: tuple[re.Pattern[str], ...]  # non-entry title lines
    sections: tuple[str, ...]  # casefolded section names
    progress: re.Pattern[str]  # must define group "title"
    dates: DateRules
    terms: tuple[TermBlock, ...]
    overrides: tuple[Override, ...]


# ---------------------------------------------------------------------------
# Shape checks shared by every table
# ---------------------------------------------------------------------------


def _keyed(where: str, kind: str, value: object) -> dict[str, object]:
    """A table whose key set ``_TABLE_KEYS[kind]`` fixes: unknown and missing refused."""
    table = checks.table(where, value)
    required, optional = _TABLE_KEYS[kind]
    unknown = sorted(set(table) - required - optional)
    if unknown:
        raise RulesError(f"{where}: unknown key {unknown[0]!r}")
    missing = sorted(required - set(table))
    if missing:
        prefix = f"{where}." if where else ""
        raise RulesError(f"{prefix}{missing[0]}: missing")
    return table


# ---------------------------------------------------------------------------
# One validator per table
# ---------------------------------------------------------------------------


def _media(value: object) -> tuple[tuple[Medium, tuple[str, ...]], ...]:
    """[media]: each medium's file-name globs, in file order."""
    table = _keyed("media", "media", value)
    if not table:
        raise RulesError("media: names no medium")
    return tuple(
        (_MEDIA[name], checks.strings(f"media.{name}", globs, allow_empty=False))
        for name, globs in table.items()
    )


def _separator(value: object) -> str:
    """ratings.split_separator: non-empty, and no letter, so a code splits unambiguously."""
    separator = checks.string("ratings.split_separator", value)
    if any(char.isalpha() for char in separator):
        raise RulesError("ratings.split_separator: must not contain a letter")
    return separator


def _rating_key(where: str, value: object, separator: str) -> RatingKey:
    """A rating key: a non-empty string that does not hold the split separator."""
    key = checks.string(where, value)
    if separator in key:
        raise RulesError(f"{where}: a rating key must not hold the split separator")
    return key


def _letters(value: object, separator: str) -> dict[str, RatingKey]:
    """ratings.letters: single lower-case letter -> rating key."""
    where = "ratings.letters"
    table = checks.table(where, value)
    if not all(_LETTER.fullmatch(letter) for letter in table):
        raise RulesError(f"{where}: each key must be one lower-case letter a-z")
    return {letter: _rating_key(where, key, separator) for letter, key in table.items()}


def _colors(value: object, separator: str) -> dict[str, RatingKey]:
    """ratings.colors: six hex digits (stored upper-case) -> rating key."""
    where = "ratings.colors"
    table = checks.table(where, value)
    if not all(_HEX_COLOR.fullmatch(color) for color in table):
        raise RulesError(f"{where}: each key must be six hex digits RRGGBB")
    checks.unique(where, [color.upper() for color in table])
    return {
        color.upper(): _rating_key(where, key, separator)
        for color, key in table.items()
    }


def _known_keys(where: str, values: tuple[str, ...], known: frozenset[str]) -> None:
    """Refuse any of ``values`` that no letter or color defines."""
    if not set(values) <= known:
        raise RulesError(f"{where}: names a rating key no letter or color defines")


def _ratings(
    value: object,
) -> tuple[dict[str, RatingKey], str, dict[str, RatingKey], int, tuple[RatingKey, ...]]:
    """[ratings]: letters, separator, colors, tolerance and the legend order."""
    table = _keyed("ratings", "ratings", value)
    separator = _separator(table["split_separator"])
    letters = _letters(table["letters"], separator)
    colors = _colors(table["colors"], separator)
    tolerance = checks.integer(
        "ratings.color_tolerance", table["color_tolerance"], 0, _MAX_COLOR_DISTANCE
    )
    legend = checks.strings("ratings.legend_order", table.get("legend_order", []))
    _known_keys("ratings.legend_order", legend, _defined(letters, colors))
    return letters, separator, colors, tolerance, legend


def _defined(letters: Mapping[str, str], colors: Mapping[str, str]) -> frozenset[str]:
    """Every rating key a letter or a color defines."""
    return frozenset(letters.values()) | frozenset(colors.values())


def _shapes(value: object, known: frozenset[str]) -> dict[str, frozenset[RatingKey]]:
    """[shapes]: glyph ("<Font>/<HEX4>", hex stored upper-case) -> keys it may carry."""
    entries: list[tuple[str, frozenset[RatingKey]]] = []
    for glyph, keys in checks.table("shapes", value).items():
        match = _GLYPH.fullmatch(glyph)
        if match is None:
            raise RulesError("shapes: each key must be a glyph <Font>/<HEX4>")
        allowed = checks.strings("shapes", keys, allow_empty=False)
        _known_keys("shapes", allowed, known)
        entries.append((f"{match['font']}/{match['code'].upper()}", frozenset(allowed)))
    checks.unique("shapes", [glyph for glyph, _ in entries])
    return dict(entries)


def _lines(
    value: object,
) -> tuple[tuple[re.Pattern[str], ...], tuple[str, ...], re.Pattern[str]]:
    """[lines]: heading patterns, section names (casefolded) and the progress pattern."""
    table = _keyed("lines", "lines", value)
    headings = checks.patterns("lines.headings", table["headings"])
    sections = tuple(
        name.casefold() for name in checks.strings("lines.sections", table["sections"])
    )
    progress = checks.pattern(
        "lines.progress", checks.string("lines.progress", table["progress"])
    )
    if "title" not in progress.groupindex:
        raise RulesError("lines.progress: must define a group named 'title'")
    return headings, sections, progress


def _century(value: object) -> int:
    """dates.century: a positive whole century, such as 1900 or 2000."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value % _CENTURY_STEP
    ):
        raise RulesError("dates.century: must be a positive multiple of 100")
    return value


def _seasons(value: object) -> dict[str, int]:
    """dates.seasons: word (casefolded) -> month 1-12."""
    where = "dates.seasons"
    table = checks.table(where, value)
    checks.unique(where, [word.casefold() for word in table])
    return {
        word.casefold(): checks.integer(where, month, _MONTHS.start, _MONTHS.stop - 1)
        for word, month in table.items()
    }


def _words(where: str, value: object) -> tuple[str, ...]:
    """A list of words, casefolded so later matching ignores case."""
    return tuple(word.casefold() for word in checks.strings(where, value))


def _dates(value: object) -> DateRules:
    """[dates]: folder-name patterns, century, words, year patterns and seasons."""
    table = _keyed("dates", "dates", value)
    return DateRules(
        two_year_folder=checks.pattern(
            "dates.two_year_folder",
            checks.string("dates.two_year_folder", table["two_year_folder"]),
            2,
        ),
        one_year_folder=checks.pattern(
            "dates.one_year_folder",
            checks.string("dates.one_year_folder", table["one_year_folder"]),
            1,
        ),
        century=_century(table["century"]),
        earlier_words=_words("dates.earlier_words", table["earlier_words"]),
        later_words=_words("dates.later_words", table["later_words"]),
        explicit_year=checks.patterns("dates.explicit_year", table["explicit_year"], 1),
        seasons=_seasons(table["seasons"]),
    )


def _entries(name: str, value: object) -> list[tuple[str, dict[str, object]]]:
    """An array of tables, each checked against ``_TABLE_KEYS[name]``, with its label."""
    if not isinstance(value, list):
        raise RulesError(f"{name}: must be an array of tables [[{name}]]")
    return [
        (f"{name}[{index}]", _keyed(f"{name}[{index}]", name, entry))
        for index, entry in enumerate(value)
    ]


def _refuse_overlap(where: str, block: TermBlock, earlier: list[TermBlock]) -> None:
    """Refuse a block sharing a line with an earlier block of the same file.

    Two blocks that both date one line would leave which date wins to the order of
    the file, so the operator is asked to settle it instead.
    """
    for other in earlier:
        if other.file == block.file and (
            block.first_line <= other.last_line and other.first_line <= block.last_line
        ):
            raise RulesError(
                f"{where}.lines: overlaps an earlier block of the same file"
            )


def _terms(value: object) -> tuple[TermBlock, ...]:
    """[[terms]]: line blocks dated by hand, no two of one file sharing a line."""
    blocks: list[TermBlock] = []
    for where, entry in _entries("terms", value):
        first, last = checks.line_span(f"{where}.lines", entry["lines"])
        block = TermBlock(
            file=checks.relative_file(f"{where}.file", entry["file"]),
            first_line=first,
            last_line=last,
            date=checks.date(f"{where}.date", entry["date"]),
            expect=checks.string(f"{where}.expect", entry["expect"]),
        )
        _refuse_overlap(where, block, blocks)
        blocks.append(block)
    return tuple(blocks)


def _rating_value(where: str, value: str, separator: str, known: frozenset[str]) -> str:
    """A known rating key, or a split of two different known keys."""
    parts = value.split(separator)
    if not set(parts) <= known or len(parts) > _PAIR or len(set(parts)) != len(parts):
        raise RulesError(f"{where}: must be a known rating key or a split of two")
    return value


def _override_value(
    where: str,
    action: Action,
    entry: Mapping[str, object],
    separator: str,
    known: frozenset[str],
) -> str | None:
    """The value an action needs, or None for an action that takes none."""
    if action not in _VALUED_ACTIONS:
        if "value" in entry:
            raise RulesError(f"{where}: not allowed for action {action!r}")
        return None
    if "value" not in entry:
        raise RulesError(f"{where}: required for action {action!r}")
    text = checks.string(where, entry["value"])
    return _rating_value(where, text, separator, known) if action == "rating" else text


def _overrides(
    value: object, separator: str, known: frozenset[str]
) -> tuple[Override, ...]:
    """[[overrides]]: single-line corrections."""
    result = []
    for where, entry in _entries("overrides", value):
        action_name = checks.string(f"{where}.action", entry["action"])
        if action_name not in _ACTIONS:
            raise RulesError(f"{where}.action: must be one of {', '.join(_ACTIONS)}")
        action = _ACTIONS[action_name]
        result.append(
            Override(
                file=checks.relative_file(f"{where}.file", entry["file"]),
                line=checks.integer(f"{where}.line", entry["line"], 1, checks.NO_LIMIT),
                expect=checks.string(f"{where}.expect", entry["expect"]),
                action=action,
                value=_override_value(
                    f"{where}.value", action, entry, separator, known
                ),
            )
        )
    return tuple(result)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


# fuzz-target
def load_rules(data: bytes) -> Rules:
    """Check the rules file's bytes and return them as ``Rules``; else raise RulesError."""
    document = _keyed("", "", checks.decode(data))
    schema = document["schema"]
    if isinstance(schema, bool) or schema != _SCHEMA:
        raise RulesError(f"schema: this version reads schema {_SCHEMA} only")
    letters, separator, colors, tolerance, legend = _ratings(document["ratings"])
    known = _defined(letters, colors)
    headings, sections, progress = _lines(document["lines"])
    return Rules(
        media=_media(document["media"]),
        letters=MappingProxyType(letters),
        split_separator=separator,
        colors=MappingProxyType(colors),
        color_tolerance=tolerance,
        legend_order=legend,
        shapes=MappingProxyType(_shapes(document.get("shapes", {}), known)),
        headings=headings,
        sections=sections,
        progress=progress,
        dates=_dates(document["dates"]),
        terms=_terms(document.get("terms", [])),
        overrides=_overrides(document.get("overrides", []), separator, known),
    )


def rating_for_code(rules: Rules, code: str) -> RatingKey | None:
    """The rating key a letter code means, or None when the code is not a known one.

    A code is one letter (``"k"``), or two different letters joined by the split
    separator (``"k/o"`` gives ``"3/2"``); case is ignored.
    """
    parts = code.lower().split(rules.split_separator)
    keys = [rules.letters.get(part) for part in parts]
    if None in keys or len(keys) > _PAIR or len(set(keys)) != len(keys):
        return None
    return rules.split_separator.join(cast(list[str], keys))


def medium_for(rules: Rules, file_name: str) -> Medium | None:
    """The medium of the first [media] glob ``file_name`` matches, ignoring case."""
    name = file_name.casefold()
    for medium, globs in rules.media:
        if any(fnmatch.fnmatchcase(name, glob.casefold()) for glob in globs):
            return medium
    return None
