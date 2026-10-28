"""Check and normalize ISBN text to the ISBN-13 form resolve records.

Open Library editions and the operator's overrides file both give ISBNs in either
length, with or without hyphens. ``isbn13_or_none`` accepts either form, verifies its
check digit, and returns the 13 bare digits; anything it cannot verify is ``None``, so
a mistyped number is never recorded as though it named a book.
"""

from __future__ import annotations

import re

# Only ASCII digits: str.isdigit and \d without re.ASCII also accept other scripts'
# digits, which no ISBN is written in.
_ISBN13 = re.compile(r"97[89][0-9]{10}")
_ISBN10 = re.compile(r"[0-9]{9}[0-9X]")
# The separators an ISBN is printed with; nothing else is ignored.
_SEPARATORS = str.maketrans("", "", "- ")
# The prefix an ISBN-10 gains when it becomes an ISBN-13.
_BOOKLAND = "978"
# ISBN-10's check digit is modulo 11, written "X" for 10.
_ISBN10_MODULUS = 11
_ISBN10_X = "X"
_ISBN10_X_VALUE = 10
# ISBN-13's check digit is modulo 10 over weights alternating 1 and 3.
_ISBN13_MODULUS = 10
_ISBN13_WEIGHTS = (1, 3)


def _isbn13_check(first12: str) -> str:
    """The check digit that completes twelve ISBN-13 digits."""
    total = sum(
        int(digit) * _ISBN13_WEIGHTS[place % len(_ISBN13_WEIGHTS)]
        for place, digit in enumerate(first12)
    )
    return str(-total % _ISBN13_MODULUS)


def _isbn10_valid(text: str) -> bool:
    """Whether ten ISBN-10 characters carry a correct check digit."""
    check = _ISBN10_X_VALUE if text[-1] == _ISBN10_X else int(text[-1])
    # Weights run from ten for the first digit down to two for the ninth.
    weights = range(len(text), 1, -1)
    weighted = sum(w * int(digit) for w, digit in zip(weights, text[:-1], strict=True))
    return (weighted + check) % _ISBN10_MODULUS == 0


# fuzz-target
def isbn13_or_none(text: str) -> str | None:
    """``text`` as a verified ISBN-13, or ``None``.

    An ISBN-13 (978 or 979) must carry a valid check digit; an ISBN-10 (check digit
    0-9 or X) must too, and is converted to 978 + its first nine digits + a new check
    digit. Hyphens and spaces are ignored; any other character makes it ``None``.
    """
    bare = text.translate(_SEPARATORS)
    if _ISBN13.fullmatch(bare):
        return bare if _isbn13_check(bare[:-1]) == bare[-1] else None
    if _ISBN10.fullmatch(bare) and _isbn10_valid(bare):
        first12 = _BOOKLAND + bare[:-1]
        return first12 + _isbn13_check(first12)
    return None
