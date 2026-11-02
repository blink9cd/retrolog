"""The one rule for a TMDb id this tool will put into a Letterboxd address.

An import file's ``tmdbID`` cell is text from a file the operator can edit, and it is
pasted into a URL path, so only a plain positive number may pass: one to ten ASCII
digits, the first not a zero. Nothing else can reach a request, a cache key, or the
report's id column.

The rule is retrolog_resolve's ``resolve_columns.is_tmdb_id``, re-exported here, so
the id resolve writes, export sends, and probe asks about is held to one pattern.
"""

from __future__ import annotations

from resolve_columns import is_tmdb_id as is_tmdb_id
