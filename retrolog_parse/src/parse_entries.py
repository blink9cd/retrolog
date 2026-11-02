"""Parse one source line, through the rules alone, into what it holds.

A line is blank, a divider, a heading, a section name, a legend of colored marks, one
or more entries, or something this module cannot read ("unparsed"). Nothing here knows
a real rating scale: letters, colors, glyph shapes, headings, sections and the progress
pattern all come from ``Rules``.

A plain-text entry goes through a fixed chain of small steps: strip a leading ``*``,
unwrap a rewatch's brackets, split off a trailing comment, recognize a progress note,
split off the letter code, and clean the title. A Word line's marks are read in order:
untitled marks before any title are a legend, a titled mark is an entry rated by its
color and cross-checked against its glyph's shape.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from parse_rules import Rules, rating_for_code
from parse_types import Mark, RatingKey, SourceLine

LineKind = Literal[
    "blank", "divider", "heading", "section", "legend", "entries", "unparsed"
]

# The closer each rewatch opener pairs with.
_CLOSERS = {"(": ")", "[": "]"}
# A divider: dashes and spaces only, at least one dash.
_DIVIDER = re.compile(r"[-\s]*-[-\s]*")
# A trailing code-shaped token: a letter, or two joined by the separator ({sep}).
_CODE_SHAPE = r"[a-z](?:{sep}[a-z])?"
# A trailing "(...)" comment, as one regex fragment.
_COMMENT = r"\([^()]*\)"
# A trailing "(YYYY)" in a title, and the years it may name.
_WORK_YEAR = re.compile(r"\((?P<year>[0-9]{4})\)$")
_YEARS = range(1870, 2101)
# One "RRGGBB" color, and where each channel sits in it.
_HEX_COLOR = re.compile(r"[0-9A-Fa-f]{6}")
_CHANNELS = (0, 2, 4)
_HEX = 16
# Two or more untitled marks before any title make a legend.
_LEGEND_MIN = 2
# The trailing characters a title never keeps.
_TITLE_TAIL = " -"


@dataclass(frozen=True)
class ParsedEntry:
    """One rated (or unrated) title read from a line."""

    title: str  # cleaned, as written otherwise; never empty
    rating: RatingKey | None
    code: str | None  # the letter code as written, lower-case ("k", "k/o")
    color: str | None  # "RRGGBB" that decided the rating, or None
    rewatch: bool  # the entry was wrapped in () or []
    comment: str  # text in a trailing "(...)" after the code, else ""
    work_year: int | None  # from a trailing "(YYYY)" in the title, removed from it
    progress: bool  # matched rules.progress: resolved by a later stage
    notes: tuple[str, ...]  # review details: "color-outlier", "shape-conflict", ...
    # (note, detail) for each note whose detail names what to fix, such as the
    # mark's glyph and color; a note not listed takes its kind's fixed detail.
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ParsedLine:
    """What one source line holds."""

    kind: LineKind
    # kind "entries" (and "legend" with a trailing entry)
    entries: tuple[ParsedEntry, ...]
    section: str | None  # kind "section": the casefolded name
    legend: tuple[RatingKey | None, ...]  # kind "legend": the marks' ratings, in order
    legend_colors: tuple[str | None, ...] = ()  # kind "legend": the marks' colors
    # kind "heading" or "section": ("heading-with-rating",) when the line also
    # carries a mark or ends in a known code, so a rated entry is not lost silently.
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------


def _channels(color: str) -> tuple[int, int, int]:
    """The red, green and blue values of a well-formed "RRGGBB" color."""
    red, green, blue = (int(color[at : at + 2], _HEX) for at in _CHANNELS)
    return red, green, blue


def rating_for_color(rules: Rules, color: str) -> RatingKey | None:
    """The key of the rules color nearest ``color``, within the tolerance.

    Distance is Euclidean in RGB. None when no rules color is near enough, when two
    colors meaning different keys are equally near, or when ``color`` is not "RRGGBB".
    """
    if not _HEX_COLOR.fullmatch(color):
        return None
    rgb = _channels(color)
    distances = (
        (math.dist(rgb, _channels(known)), key) for known, key in rules.colors.items()
    )
    near = [(dist, key) for dist, key in distances if dist <= rules.color_tolerance]
    if not near:
        return None
    best = min(distance for distance, _ in near)
    keys = {key for distance, key in near if distance == best}
    return keys.pop() if len(keys) == 1 else None


def _mark_rating(rules: Rules, mark: Mark) -> RatingKey | None:
    """The key a mark's color means, or None for an uncolored or unknown one."""
    return None if mark.color is None else rating_for_color(rules, mark.color)


def _text_color(rules: Rules, colors: Iterable[str]) -> tuple[RatingKey, str] | None:
    """The first text color the rules know, with its key."""
    for color in colors:
        rating = rating_for_color(rules, color)
        if rating is not None:
            return rating, color
    return None


# ---------------------------------------------------------------------------
# One plain-text entry, step by step
# ---------------------------------------------------------------------------


def _strip_star(text: str) -> str:
    """Strip whitespace, one leading ``*``, then whitespace again."""
    return text.strip().removeprefix("*").strip()


def _paired_closer(text: str) -> int | None:
    """Where the closer pairing with ``text``'s opening bracket sits, or None.

    Depth counting over the opener's own bracket kind, so "(a) - k (b)" pairs the
    first ")" rather than the last.
    """
    opener = text[:1]
    closer = _CLOSERS.get(opener)
    depth = 0
    for at, char in enumerate(text if closer else ""):
        depth += (char == opener) - (char == closer)
        if depth == 0:
            return at
    return None


def _code_shape(rules: Rules) -> str:
    """A code-shaped token's regex: one letter or two joined by the split separator."""
    return _CODE_SHAPE.format(sep=re.escape(rules.split_separator))


def _unwrap(text: str, rules: Rules) -> tuple[str, bool]:
    """Drop a rewatch's brackets; report whether it did.

    Only a bracket pair whose closer ends the text, or is followed by nothing but an
    optional code and an optional comment, wraps the entry: "(500) days - k" keeps
    its parentheses, since "days" follows them.
    """
    end = _paired_closer(text)
    if end is None:
        return text, False
    tail = rf"(?:[\s-]+{_code_shape(rules)})?\s*(?:{_COMMENT})?\s*"
    if not re.fullmatch(tail, text[end + 1 :]):
        return text, False
    return _strip_star(text[1:end] + text[end + 1 :]), True


def _code_pattern(rules: Rules) -> re.Pattern[str]:
    """A trailing letter code, one letter or two joined by the split separator."""
    return re.compile(rf"(?:^|[\s-])(?P<code>{_code_shape(rules)})\s*$")


def _unknown_code_tail(body: str, rules: Rules) -> re.Match[str] | None:
    """A dash, then a code-shaped token of either case, then an optional comment, at the end."""
    pattern = rf"-\s*{_code_shape(rules)}\s*(?:{_COMMENT})?\s*$"
    return re.search(pattern, body, re.IGNORECASE)


def _unknown_code(body: str, rules: Rules) -> bool:
    """Whether a dash ends ``body`` with a code-shaped token, of either case.

    Called only when no known code was split off, so such a token is one the rules
    do not know; a comment after it may follow, unsplit.
    """
    return _unknown_code_tail(body, rules) is not None


def strip_unknown_code(title: str, rules: Rules) -> str:
    """``title`` without the trailing unknown code an "unknown-code" note left in it.

    Every form ``parse_text_entry`` notes is removed, comment included. A title with
    no such tail, or one the cut would leave without a letter or digit, is returned
    unchanged, so a row never loses its title.
    """
    tail = _unknown_code_tail(title, rules)
    stripped = title if tail is None else title[: tail.start()].rstrip(_TITLE_TAIL)
    return stripped if _has_word(stripped) else title


def _split_code(text: str, rules: Rules) -> tuple[str, str | None]:
    """Split a known trailing code off ``text``; an unknown one stays in the title."""
    match = _code_pattern(rules).search(text)
    if match is None or rating_for_code(rules, match["code"]) is None:
        return text, None
    return text[: match.start("code")], match["code"]


def _split_comment(text: str, rules: Rules) -> tuple[str, str]:
    """Split a trailing "(...)" off ``text`` when a known code comes just before it.

    The known-code check is ``_split_code``'s own, so a title's parentheses survive
    unless the words before them end in a code that step would also take.
    """
    separator = re.escape(rules.split_separator)
    match = re.match(
        rf"(?P<body>.*?[\s-][a-z](?:{separator}[a-z])?)\s*\((?P<comment>[^()]*)\)\s*$",
        text,
        re.IGNORECASE,
    )
    if match is None or _split_code(match["body"], rules)[1] is None:
        return text, ""
    return match["body"], match["comment"].strip()


def _progress(text: str, rules: Rules) -> str | None:
    """The title of a progress note ("halfway through"), or None for any other text."""
    match = rules.progress.search(text)
    return None if match is None else match["title"] or ""


def clean_title(title: str) -> tuple[str, int | None]:
    """Collapse whitespace, drop trailing dashes, and take off a trailing work year."""
    title = " ".join(title.split()).rstrip(_TITLE_TAIL)
    match = _WORK_YEAR.search(title)
    if match is None or int(match["year"]) not in _YEARS:
        return title, None
    return title[: match.start()].rstrip(_TITLE_TAIL), int(match["year"])


def _has_word(title: str) -> bool:
    """Whether a title holds a letter or digit, so it can name something."""
    return any(char.isalnum() for char in title)


def _title_and_code(body: str, rules: Rules) -> tuple[str, str | None, bool]:
    """The raw title, the code and whether the body is a progress note."""
    progress_title = _progress(body, rules)
    if progress_title is not None:
        return progress_title, None, True
    title, code = _split_code(body, rules)
    return title, code, False


# fuzz-target
def parse_text_entry(text: str, rules: Rules) -> ParsedEntry | None:
    """One entry from a line's text, or None when no title is left to name one."""
    body, rewatch = _unwrap(_strip_star(text), rules)
    body, comment = _split_comment(body, rules)
    raw_title, code, progress = _title_and_code(body, rules)
    title, work_year = clean_title(raw_title)
    if not _has_word(title):
        return None
    unknown = code is None and not progress and _unknown_code(body, rules)
    return ParsedEntry(
        title=title,
        rating=None if code is None else rating_for_code(rules, code),
        code=code,
        color=None,
        rewatch=rewatch,
        comment=comment,
        work_year=work_year,
        progress=progress,
        notes=("unknown-code",) if unknown else (),
    )


# ---------------------------------------------------------------------------
# Marked (Word) lines
# ---------------------------------------------------------------------------


def _mark_notes(
    rules: Rules, mark: Mark, rating: RatingKey | None
) -> tuple[tuple[str, str], ...]:
    """Why a marked entry needs a look, each with a detail naming the mark.

    No color, an unknown color, or a glyph whose ``[shapes]`` entry does not allow
    the rating its color gives. The detail names the glyph, the color and the rating,
    the values the operator adds to the rules or checks.
    """
    if mark.color is None:
        return (("no-color", f"mark {mark.glyph} has no color to rate it by"),)
    shown = f"mark {mark.glyph} in {mark.color}"
    if rating is None:
        detail = f"{shown} is near no rules color, or equally near two"
        return (("color-outlier", detail),)
    allowed = rules.shapes.get(mark.glyph)
    if allowed is None or rating in allowed:
        return ()
    detail = (
        f"{shown} gives rating {rating}, which [shapes] does not allow for that glyph"
    )
    return (("shape-conflict", detail),)


def _mark_entry(rules: Rules, mark: Mark, title: str, year: int | None) -> ParsedEntry:
    """A titled mark: rated by its color, its shape cross-checked."""
    rating = _mark_rating(rules, mark)
    details = _mark_notes(rules, mark, rating)
    return ParsedEntry(
        title=title,
        rating=rating,
        code=None,
        color=None if rating is None else mark.color,
        rewatch=False,
        comment="",
        work_year=year,
        progress=False,
        notes=tuple(note for note, _ in details),
        details=details,
    )


def _noted(entry: ParsedEntry, note: str) -> ParsedEntry:
    """``entry`` with one more review note."""
    return replace(entry, notes=(*entry.notes, note))


def _with_text_color(
    rules: Rules, entry: ParsedEntry, colors: Iterable[str]
) -> ParsedEntry:
    """Rate an entry no code rated, and that is no progress note, by its text color."""
    found = (
        None
        if entry.rating is not None or entry.progress
        else _text_color(rules, colors)
    )
    if found is None:
        return entry
    return replace(entry, rating=found[0], color=found[1])


def _walk_marks(line: SourceLine, rules: Rules) -> tuple[list[ParsedEntry], list[Mark]]:
    """A marked line's entries, and the untitled marks seen before any title."""
    entries: list[ParsedEntry] = []
    legend: list[Mark] = []
    for segment in line.segments:
        if not segment.marks:
            plain = parse_text_entry(segment.text, rules)
            if plain is not None:
                entries.append(_with_text_color(rules, plain, segment.text_colors))
            continue
        mark = segment.marks[0]
        title, year = clean_title(segment.text)
        if _has_word(title):
            entries.append(_mark_entry(rules, mark, title, year))
        elif entries:
            # An untitled mark after a title belongs to no legend: flag its neighbor.
            entries[-1] = _noted(entries[-1], "stray-mark")
        else:
            legend.append(mark)
    return entries, legend


def _marked_line(line: SourceLine, rules: Rules) -> ParsedLine:
    """A line holding symbol marks: a legend, entries, or unparsed."""
    entries, legend = _walk_marks(line, rules)
    if len(legend) >= _LEGEND_MIN:
        ratings = tuple(_mark_rating(rules, mark) for mark in legend)
        colors = tuple(mark.color for mark in legend)
        return ParsedLine("legend", tuple(entries), None, ratings, colors)
    if entries:
        if legend:
            entries[0] = _noted(entries[0], "stray-mark")
        return ParsedLine("entries", tuple(entries), None, ())
    return _bare("unparsed")


# ---------------------------------------------------------------------------
# Whole lines
# ---------------------------------------------------------------------------


def _bare(
    kind: LineKind, section: str | None = None, notes: tuple[str, ...] = ()
) -> ParsedLine:
    """A line of a kind that carries no entry and no legend."""
    return ParsedLine(kind, (), section, (), (), notes)


def _section_name(text: str, rules: Rules) -> str | None:
    """The casefolded section name ``text`` is, without one trailing ":", or None."""
    name = text.casefold().removesuffix(":").strip()
    return name if name in rules.sections else None


def _rating_notes(text: str, marked: bool, rules: Rules) -> tuple[str, ...]:
    """("heading-with-rating",) for a line with a mark or a known code, else ().

    The code is found the way an entry's is, after any trailing comment, so a
    heading pattern that also matches a rated entry is reported, not obeyed silently.
    """
    body, _ = _split_comment(text, rules)
    rated = marked or _split_code(body, rules)[1] is not None
    return ("heading-with-rating",) if rated else ()


def _layout_line(text: str, marked: bool, rules: Rules) -> ParsedLine | None:
    """A blank, divider, section or heading line, or None for anything else."""
    if not text:
        return None if marked else _bare("blank")
    if not marked and _DIVIDER.fullmatch(text):
        return _bare("divider")
    section = _section_name(text, rules)
    if section is not None:
        return _bare("section", section, _rating_notes(text, marked, rules))
    if any(pattern.search(text) for pattern in rules.headings):
        return _bare("heading", None, _rating_notes(text, marked, rules))
    return None


def parse_line(line: SourceLine, rules: Rules) -> ParsedLine:
    """What one source line holds, by the rules."""
    marked = any(segment.marks for segment in line.segments)
    layout = _layout_line(line.text.strip(), marked, rules)
    if layout is not None:
        return layout
    if marked:
        return _marked_line(line, rules)
    entry = parse_text_entry(line.text, rules)
    if entry is None:
        return _bare("unparsed")
    colors = [color for segment in line.segments for color in segment.text_colors]
    return ParsedLine("entries", (_with_text_color(rules, entry, colors),), None, ())
