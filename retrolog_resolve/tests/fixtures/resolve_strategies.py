"""Hypothesis strategies shared by retrolog_resolve's fuzz tests.

Kept apart from ``resolve_fixtures`` because the sample builder imports that module and
must run on the standard library alone; only the tests import this one.
"""

from __future__ import annotations

from hypothesis import strategies as st

# Any value json.loads can return, nested a few levels deep, with keys drawn mostly
# from the names the parsers read, so a fuzz property meets wrong types at every level
# rather than only at the top.

_JSON_KEYS = (
    # TMDb
    "id",
    "title",
    "original_title",
    "release_date",
    "results",
    "crew",
    "job",
    "name",
    # Open Library
    "docs",
    "key",
    "author_name",
    "first_publish_year",
    "editions",
    "isbn",
    "language",
)
_JSON_LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False),
    st.text(max_size=12),
)
json_values = st.recursive(
    _JSON_LEAVES,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(
            st.one_of(st.sampled_from(_JSON_KEYS), st.text()),
            children,
            max_size=6,
        ),
    ),
    max_leaves=20,
)
