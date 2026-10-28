"""Check the rules' term blocks and overrides against the files they name.

Every term block and override carries the expected text of the line it names, so a
line-numbering difference (a Word paragraph holding a soft break, say) is caught as an
error instead of silently dating or rewriting the wrong line. These checks run before
any row is built; ``parse_rows`` applies only the overrides they pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from parse_review import review_item
from parse_rules import Override, Rules
from parse_types import ReviewItem, SourceLine

# The rule-file-missing detail for each kind of rule.
_MISSING_TERM = "a term block names a file the run did not find, so it dates nothing"
_MISSING_OVERRIDE = (
    "an override names a file the run did not find, so it was not applied"
)


def _line_at(lines: Iterable[SourceLine], number: int) -> SourceLine | None:
    """The line numbered ``number``, if the file has one."""
    return next((line for line in lines if line.number == number), None)


def _matches(line: SourceLine | None, expect: str) -> bool:
    """Whether a line exists and its stripped text is ``expect`` stripped."""
    return line is not None and line.text.strip() == expect.strip()


def check_missing(walked: frozenset[str] | None, rules: Rules) -> list[ReviewItem]:
    """A rule-file-missing error for each term block or override naming no walked file.

    ``walked`` None means the caller knows of no files beyond those it passed, so no
    rule is checked here: an absent file is then simply not checked.
    """
    if walked is None:
        return []
    terms = [
        review_item("rule-file-missing", term.file, term.first_line, "", _MISSING_TERM)
        for term in rules.terms
        if term.file not in walked
    ]
    overrides = [
        review_item("rule-file-missing", over.file, over.line, "", _MISSING_OVERRIDE)
        for over in rules.overrides
        if over.file not in walked
    ]
    return [*terms, *overrides]


def check_terms(
    files: Mapping[str, Sequence[SourceLine]], rules: Rules
) -> list[ReviewItem]:
    """A term-mismatch for each present file's term block whose first line is off."""
    items = []
    for term in rules.terms:
        if term.file not in files:
            continue  # absent: check_missing reports it, unless walked but unread
        line = _line_at(files[term.file], term.first_line)
        if not _matches(line, term.expect):
            text = "" if line is None else line.text
            items.append(review_item("term-mismatch", term.file, term.first_line, text))
    return items


def check_overrides(
    files: Mapping[str, Sequence[SourceLine]], rules: Rules
) -> tuple[dict[tuple[str, int], Override], list[ReviewItem]]:
    """The overrides whose line matches, by (file, line); a mismatch item for the rest.

    A second override naming a line already claimed is a mismatch too: the first wins.
    """
    usable: dict[tuple[str, int], Override] = {}
    items = []
    for override in rules.overrides:
        if override.file not in files:
            continue  # absent: check_missing reports it, unless walked but unread
        line = _line_at(files[override.file], override.line)
        key = (override.file, override.line)
        if _matches(line, override.expect) and key not in usable:
            usable[key] = override
            continue
        text = "" if line is None else line.text
        items.append(
            review_item("override-mismatch", override.file, override.line, text)
        )
    return usable, items
