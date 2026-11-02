"""What the retrolog tools share about resolve's CSVs: columns, cell shapes and ids.

A leaf module: it imports only the standard library's ``re``, so a reader of resolve's
output (retrolog_export) takes the columns and cell shapes from here without loading
resolve's HTTP layer, and the hardened fetch library behind it, just to learn a header.
resolve reads parse's CSVs and export reads resolve's with these same shapes, so a
bound changed here changes in both. The TMDb id rule and the creator joiner live here
for the same reason: a tool that checks an id or joins a creator cell takes the one
definition here rather than keeping a copy that can drift.
"""

from __future__ import annotations

import re

# The year, date and source_line cells parse writes and resolve copies through. ASCII
# only: \d would also accept other scripts' digits. A year and a line are bounded
# before int() reads them, which refuses a string past its 4,300-digit limit with a
# ValueError: a year has at most four digits, and no list file runs to a billion lines.
YEAR_CELL = re.compile(r"[0-9]{1,4}")
DATE_CELL = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
LINE_CELL = re.compile(r"[1-9][0-9]{0,8}")

# A TMDb id, for fullmatch: ASCII digits with no leading zero, at most ten of them.
# Shared, so an override, a URL and a CSV cell can be held to one rule. The id is
# spliced into a URL path, and TMDb's ids
# are positive integers far short of ten digits; the bound keeps int() well inside
# its digit limit too.
TMDB_ID = re.compile(r"[1-9][0-9]{0,9}", re.ASCII)

# What joins a work's creators (a film's directors, a book's authors) in its one cell.
CREATOR_JOINER = " & "


# fuzz-target
def is_tmdb_id(text: str) -> bool:
    """Whether ``text`` is a whole TMDb id by ``TMDB_ID``."""
    return TMDB_ID.fullmatch(text) is not None


# The columns resolve adds after parse's, in order. The last two are a film's TMDb title
# and original title: a resolved film's chosen candidate's, or those TMDb's details give
# for an override's tmdb_id; empty for every other row, and for an override whose
# details could not be read. The Letterboxd account export that retrolog_export diffs
# against names a film by its title, and resolve can settle a film under a title other
# than the list's (a variant, a re-search, an original title, the most-voted rule, an
# override), so export matches on these too.
RESOLVE_COLUMNS = (
    "resolve_status",
    "isbn13",
    "ol_work",
    "tmdb_id",
    "tmdb_title",
    "tmdb_original_title",
)
