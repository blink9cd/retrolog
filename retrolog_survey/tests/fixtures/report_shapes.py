"""Narrow a decoded report's values for the type checker, checking each as it goes.

A report is JSON, so its values are typed as ``object``; these helpers assert the
shape a test expects and return it typed, so a wrong shape fails the test loudly.
"""

from __future__ import annotations


def as_dict(value: object) -> dict[str, object]:
    """``value``, checked to be a report dictionary."""
    assert isinstance(value, dict)
    return value


def as_list(value: object) -> list[object]:
    """``value``, checked to be a report list."""
    assert isinstance(value, list)
    return value
