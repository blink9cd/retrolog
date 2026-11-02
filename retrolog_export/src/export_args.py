"""The value types of retrolog_export's command-line options.

Each is an argparse ``type``: it turns the option's text into its value, or raises
``argparse.ArgumentTypeError``, which argparse reports as a usage error naming the
option. They are kept here so the entry point holds the run itself.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable


def whole_number(low: int, high: int) -> Callable[[str], int]:
    """A whole number from ``low`` to ``high``, both included."""

    def parse(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be {low} to {high}")
        return value

    return parse
