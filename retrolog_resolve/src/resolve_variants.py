"""The title variants: six ways a film title can differ from TMDb's and still be it.

Pure: each variant maps a title to a title and never raises. A variant is compare-only:
it rewrites the row's title and each candidate's title and original title alike, the
caller passes every result through parse's ``title_key``, and the rewritten text is never
sent or written. So a variant works on the raw title, before ``title_key`` drops the
ampersand and the apostrophe it needs to see, and it need not rewrite to the same
spelling on both sides: only the normalized keys have to meet.

``VARIANTS`` holds them in the order they are tried, each beside the short phrase a review detail
names it by ("matched ignoring <name>"). That order is the measured one: the first
variant under which any candidate matches decides.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_AMPERSAND = re.compile(r"&")
# One parenthetical or bracketed note closing the title, with the space before it.
_TRAILING_NOTE = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])\s*\Z")
# A sequel numeral as the title's last word, Roman in either case (a row may be typed
# in lower case) or ASCII digits, then any punctuation. A last word that only looks
# like one ("vi") can at most meet a candidate whose title ends in its digits, and every
# variant match is listed for review.
_ROMAN = ("II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")
_SEQUEL = re.compile(
    r"(?<!\w)(" + "|".join(_ROMAN) + r"|[2-9]|10)(\W*)\Z", re.IGNORECASE
)
_ROMAN_VALUE = {roman: str(value) for value, roman in enumerate(_ROMAN, start=2)}
# "The", "A" or "An" opening a title that has more after it.
_ARTICLE = re.compile(r"\A\s*(?:the|an|a)\s+(?=\S)", re.IGNORECASE)
# The "'s" closing a word, or the apostrophe after a plural's "s"; either apostrophe.
_POSSESSIVE = re.compile(r"(?<=\w)['’]s\b|(?<=s)['’](?!\w)", re.IGNORECASE)
_NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty"
).split()
# Whole words only: "often" holds "ten" and "someone" holds "one".
_NUMBER_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
_NUMBER_VALUE = {word: str(value) for value, word in enumerate(_NUMBER_WORDS)}


# fuzz-target
def ampersand(title: str) -> str:
    """``title`` with each "&" read as the word "and"."""
    return _AMPERSAND.sub(" and ", title)


# fuzz-target
def trailing_note(title: str) -> str:
    """``title`` without a closing "(...)" or "[...]" note; a title that is only one stays."""
    match = _TRAILING_NOTE.search(title)
    if match is None or not title[: match.start()].strip():
        return title
    return title[: match.start()]


# fuzz-target
def sequel_numeral(title: str) -> str:
    """``title`` with a closing Roman numeral, II to X in either case, read as its digits."""
    return _SEQUEL.sub(_sequel_value, title)


def _sequel_value(match: re.Match[str]) -> str:
    """A matched sequel numeral as its digits, then the punctuation after it.

    Looked up upper-cased, since the match ignores case. A non-ASCII letter that the
    case-insensitive match accepts but that does not upper-case to a Roman numeral
    ("İ" for "i") is left as it was, as the digits are.
    """
    numeral = match.group(1)
    return _ROMAN_VALUE.get(numeral.upper(), numeral) + match.group(2)


# fuzz-target
def leading_article(title: str) -> str:
    """``title`` without an opening "The", "A" or "An"; a title of only one stays."""
    return _ARTICLE.sub("", title, count=1)


# fuzz-target
def possessive(title: str) -> str:
    """``title`` with each possessive marker ("'s", or "'" after an "s") dropped."""
    return _POSSESSIVE.sub("", title)


# fuzz-target
def number_words(title: str) -> str:
    """``title`` with each whole number word, zero to twenty, read as its digits."""
    return _NUMBER_WORD.sub(_number_value, title)


def _number_value(match: re.Match[str]) -> str:
    """The digits of a matched number word, or the word itself when it has none.

    A case-insensitive match also accepts a few non-ASCII letters ("İ" for "i") whose
    casefold is not the ASCII word, so such a word is left as it was.
    """
    word = match.group(1)
    return _NUMBER_VALUE.get(word.casefold(), word)


# The order they are tried in, each with the phrase "matched ignoring <name>" names it by.
VARIANTS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("an ampersand", ampersand),
    ("a trailing note", trailing_note),
    ("a sequel numeral", sequel_numeral),
    ("a leading article", leading_article),
    ("a possessive", possessive),
    ("number words", number_words),
)
