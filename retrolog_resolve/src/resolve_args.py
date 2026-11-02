"""The command line's own value checks: argparse types, each refusing with its rule.

Kept out of the entry point so that file stays within its length limit. Each raises
``argparse.ArgumentTypeError`` with the rule, since argparse would otherwise name the
function in its message for a value it cannot convert.
"""

from __future__ import annotations

import argparse
import math

# The longest a request or git call may be given, in seconds: an hour is far past any
# real wait, and well inside what socket.settimeout can hold (it raises OverflowError
# on an infinite or huge value, which would end the run with a traceback).
MAX_SECONDS = 3600.0
_POSITIVE_RULE = f"must be a number greater than 0 and at most {MAX_SECONDS:g}"
_VOTE_RATIO_RULE = "must be 0 or a finite number above 1"
_VOTE_FLOOR_RULE = "must be a whole number of 0 or more"


def positive(text: str) -> float:
    """A number of seconds greater than zero and at most ``MAX_SECONDS``.

    NaN fails every comparison, so the range is tested as a positive condition and a
    NaN, like an infinity, is refused.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(_POSITIVE_RULE) from None
    if not 0 < value <= MAX_SECONDS:
        raise argparse.ArgumentTypeError(_POSITIVE_RULE)
    return value


def vote_ratio(text: str) -> float:
    """0 (the most-votes rule off), or a finite number above 1.

    A ratio of 1 or below would let a film with barely more votes win a collision,
    and an infinite or NaN one would never compare as meant.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(_VOTE_RATIO_RULE) from None
    if value != 0 and not (math.isfinite(value) and value > 1):
        raise argparse.ArgumentTypeError(_VOTE_RATIO_RULE)
    return value


def vote_floor(text: str) -> int:
    """A whole number of votes, 0 or more; 0 turns the vote-floor signal off."""
    try:
        value = int(text)
    except ValueError:  # int() also refuses a string past its digit limit
        raise argparse.ArgumentTypeError(_VOTE_FLOOR_RULE) from None
    if value < 0:
        raise argparse.ArgumentTypeError(_VOTE_FLOOR_RULE)
    return value
