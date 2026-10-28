"""Place one entry in time from the rules: term blocks, file-name words and folders.

Pure: nothing here reads a file. ``place`` takes a source file's path relative to the
input root (``/``-separated), a line number in it and the checked rules, and returns a
``Placement``. The order is fixed and each step is its own helper:

1. a hand-dated ``[[terms]]`` block holding the line wins outright;
2. else the year comes from an explicit year in the file name (``#NN``), a one-year
   folder, or a two-year folder whose file name holds exactly one break word;
3. the month is the first season word in the file name, else December, and the day
   is that month's last.

Every placement is approximate today: no source dates an entry to the day.
"""

from __future__ import annotations

import calendar
import datetime
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from parse_rules import DateRules, Rules, TermBlock

Basis = Literal["term", "explicit-year", "break-word", "one-year-folder", "unplaced"]
# The month an entry with no season word is placed in.
_DECEMBER = 12
# The most digits a year group may hold: a year has at most four, and the bound keeps
# a longer run from int(), which refuses a string past its 4,300-digit limit.
_YEAR_DIGITS = 4


@dataclass(frozen=True)
class Placement:
    """When an entry happened, and which rule said so."""

    date: datetime.date | None  # None exactly when basis is "unplaced"
    approximate: bool  # True for every basis today; kept for exact dates later
    basis: Basis


_UNPLACED = Placement(None, True, "unplaced")


def term_for(rules: Rules, rel_path: str, line: int) -> TermBlock | None:
    """The first term block for ``rel_path`` whose line range holds ``line``, if any."""
    for term in rules.terms:
        if term.file == rel_path and term.first_line <= line <= term.last_line:
            return term
    return None


def _folder_and_name(rel_path: str) -> tuple[str | None, str]:
    """The first path component (None for a bare file) and the casefolded file stem."""
    parts = PurePosixPath(rel_path).parts
    folder = parts[0] if len(parts) > 1 else None
    return folder, PurePosixPath(rel_path).stem.casefold()


def _year(dates: DateRules, digits: str) -> int | None:
    """``century + digits`` as a calendar year, or None when it cannot be one.

    The rules check each pattern's group count, not what the group captures, so a group
    that is not a number, a run of more than four digits, or a year outside the calendar,
    is treated as no match.
    """
    if not digits.isdecimal() or len(digits) > _YEAR_DIGITS:
        return None
    year = dates.century + int(digits)
    return year if datetime.MINYEAR <= year <= datetime.MAXYEAR else None


def _find_word(name: str, word: str) -> re.Match[str] | None:
    """Where ``word`` first stands as a whole word in ``name``, if it does."""
    return re.search(rf"\b{re.escape(word)}\b", name)


def _has_any(name: str, words: Iterable[str]) -> bool:
    """Whether any of ``words`` stands as a whole word in ``name``."""
    return any(_find_word(name, word) for word in words)


def _explicit_year(dates: DateRules, name: str) -> int | None:
    """The year the first matching explicit-year pattern names in the file name."""
    for pattern in dates.explicit_year:
        match = pattern.search(name)
        year = _year(dates, match.group(1)) if match else None
        if year is not None:
            return year
    return None


def _one_year_folder(dates: DateRules, folder: str) -> int | None:
    """The year a one-year folder name gives, if it is one."""
    match = dates.one_year_folder.search(folder)
    return _year(dates, match.group(1)) if match else None


def _break_word_year(dates: DateRules, folder: str, name: str) -> int | None:
    """Which year of a two-year folder the file name's single break word picks."""
    match = dates.two_year_folder.search(folder)
    if match is None:
        return None
    earlier = _has_any(name, dates.earlier_words)
    later = _has_any(name, dates.later_words)
    if earlier == later:  # neither word, or both: the name does not choose a year
        return None
    return _year(dates, match.group(1 if earlier else 2))


def _year_and_basis(
    dates: DateRules, folder: str | None, name: str
) -> tuple[int, Basis] | None:
    """The entry's year and the rule that gave it, in precedence order; else None."""
    explicit = _explicit_year(dates, name)
    if explicit is not None:
        return explicit, "explicit-year"
    if folder is None:
        return None
    one_year = _one_year_folder(dates, folder)
    if one_year is not None:
        return one_year, "one-year-folder"
    split = _break_word_year(dates, folder, name)
    return (split, "break-word") if split is not None else None


def _month(dates: DateRules, name: str) -> int:
    """The month of the season word earliest in the file name, else December."""
    found = [
        (match.start(), month)
        for word, month in dates.seasons.items()
        if (match := _find_word(name, word))
    ]
    return min(found)[1] if found else _DECEMBER


def _month_end(year: int, month: int) -> datetime.date:
    """The last day of ``month`` in ``year``."""
    return datetime.date(year, month, calendar.monthrange(year, month)[1])


# fuzz-target
def place(rules: Rules, rel_path: str, line: int) -> Placement:
    """Place ``rel_path``'s ``line`` in time: a term block, else the file's name and folder."""
    term = term_for(rules, rel_path, line)
    if term is not None:
        return Placement(term.date, True, "term")
    folder, name = _folder_and_name(rel_path)
    found = _year_and_basis(rules.dates, folder, name)
    if found is None:
        return _UNPLACED
    year, basis = found
    return Placement(_month_end(year, _month(rules.dates, name)), True, basis)
