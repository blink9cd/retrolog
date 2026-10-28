"""The columns retrolog_resolve adds to parse's, and the cell shapes its readers check.

A leaf module: it imports only the standard library's ``re``, so a reader of resolve's
output (retrolog_export) takes the columns and cell shapes from here without loading
resolve's HTTP layer, and the hardened fetch library behind it, just to learn a header.
resolve reads parse's CSVs and export reads resolve's with these same shapes, so a
bound changed here changes in both.
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

# The columns resolve adds after parse's, in order. The last two are the TMDb title and
# original title of a resolved film's chosen candidate, empty for every other row: the
# Letterboxd account export that retrolog_export diffs against names a film by its
# title, and resolve can
# settle a film under a title other than the list's (a variant, a re-search, an
# original title, the most-voted rule), so export matches on these too.
RESOLVE_COLUMNS = (
    "resolve_status",
    "isbn13",
    "ol_work",
    "tmdb_id",
    "tmdb_title",
    "tmdb_original_title",
)
