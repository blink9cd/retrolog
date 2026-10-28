"""Hypothesis strategies shared by retrolog_export's fuzz tests.

Kept apart from ``export_fixtures`` because a sample builder may import that module
and must run on the standard library alone; only the tests import this one.
"""

from __future__ import annotations

import datetime

from export_types import ResolvedRow
from hypothesis import strategies as st
from parse_rows import title_key

# Any value a star rating could be written as in TOML, the valid half-star grid
# included, so a property meets both the values it must take and those it must refuse.
star_values = st.one_of(
    st.sampled_from([0.5 * step for step in range(-2, 13)]),
    st.floats(),
    st.integers(),
    st.booleans(),
    st.text(max_size=4),
)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

# Small pools, so a list of rows meets shared ids, shared titles and shared positions:
# the films selection groups, holds and orders are made of such collisions. "!!!" has
# no letter or digit, so its title_key is empty.
_TITLES = ("Metropolis", "Placeholder Film One", "Placeholder Film Two", "!!!")
_TMDB_IDS = ("1000001", "1000002", "1000003")
_SOURCE_FILES = ("10/seen.txt", "11/seen.txt", "undated/seen.txt")
# The statuses resolve gives a row with no tmdbID; resolved always carries one.
_STATUSES_WITHOUT_ID = ("override", "ambiguous", "not-found", "failed")


@st.composite
def resolved_rows(draw: st.DrawFn) -> ResolvedRow:
    """One row as export_input reads it, shaped the way resolve writes its statuses.

    A ``resolved`` row always has a tmdbID and an ``override`` row may; the others
    never do. A resolved row may carry TMDb title keys, from the same pool, as
    resolve writes a film settled under another title. Positions are drawn from small pools, so a list may repeat one: a test
    that needs every ``where`` distinct draws its list with ``unique_by``.
    """
    title = draw(st.sampled_from(_TITLES))
    tmdb_id = draw(st.sampled_from(("", *_TMDB_IDS)))
    if tmdb_id:
        status = draw(st.sampled_from(("resolved", "override")))
    else:
        status = draw(st.sampled_from(_STATUSES_WITHOUT_ID))
    return ResolvedRow(
        source_csv="film-2010.csv",
        title=title,
        title_key=title_key(title),
        year=draw(st.sampled_from((None, 1927, 1999))),
        date=draw(
            st.none() | st.dates(datetime.date(2010, 1, 1), datetime.date(2010, 1, 9))
        ),
        rating_raw=draw(st.sampled_from(("", "1", "2"))),
        comment="",
        rewatch=draw(st.booleans()),
        creator="Placeholder Director",
        status=status,
        tmdb_id=tmdb_id,
        source_file=draw(st.sampled_from(_SOURCE_FILES)),
        source_line=draw(st.integers(min_value=1, max_value=40)),
        tmdb_title_keys=draw(_tmdb_title_keys(status)),
    )


def _tmdb_title_keys(status: str) -> st.SearchStrategy[tuple[str, ...]]:
    """A resolved row's TMDb title keys, each once and never empty; none otherwise."""
    if status != "resolved":
        return st.just(())
    keys = [key for key in map(title_key, _TITLES) if key]
    return st.lists(st.sampled_from(keys), max_size=2, unique=True).map(tuple)
