"""The keys a title is matched by in the account diff and among the held films.

Letterboxd's ``Name`` for a film and TMDb's en-US title can differ by an accent, by
"&" against "and", or by a hyphen against a space ("40-Year-Old" against "40 Year
Old"), and ``parse_rows.title_key`` keeps an accent and drops "&" and "-". So a title
is matched by up to three keys, in this order: its ``title_key``; its folded key, the
title decomposed (NFKD), its combining marks dropped, and each "&" read as "and", then
``title_key``; and that folded title with each hyphen (ASCII "-", U+2010, or U+2011)
read as a space, then ``title_key``. Each fold only adds a key and drops none, and both
sides of every comparison are keyed alike, so folding can only add a match, never lose
one an earlier key made: "Café-Bar" still meets "Cafebar" by its folded key, and
meets "Cafe Bar" by its hyphen key.

Only export's account diff and held-film checks use this: ``title_key`` itself stays
as it is, since other tools and resolve's grouping depend on it.
"""

from __future__ import annotations

import unicodedata

from parse_rows import title_key

# What "&" is read as. Spaced, so "Cats&Dogs" folds like "Cats and Dogs"; title_key
# collapses the spaces again.
_AND = " and "
# The hyphens read as a space: ASCII's, and Unicode's hyphen and non-breaking hyphen.
# NFKD already turns U+2011 into U+2010; both are listed so the fold does not rest on
# that. title_key collapses the spaces again.
_HYPHENS = str.maketrans(dict.fromkeys("-‐‑", " "))


def fold_title(title: str) -> str:
    """``title`` decomposed, its combining marks dropped, and each "&" read as "and"."""
    decomposed = unicodedata.normalize("NFKD", title)
    bare = "".join(char for char in decomposed if not unicodedata.combining(char))
    return bare.replace("&", _AND)


def fold_hyphens(title: str) -> str:
    """``fold_title``'s ``title`` with each hyphen read as a space."""
    return fold_title(title).translate(_HYPHENS)


def match_keys(title: str) -> tuple[str, ...]:
    """``title``'s ``title_key``, folded key, and hyphen key, each once, never ""."""
    keys = (
        title_key(title),
        title_key(fold_title(title)),
        title_key(fold_hyphens(title)),
    )
    # dict.fromkeys keeps first sightings in order, dropping a repeat.
    return tuple(dict.fromkeys(key for key in keys if key))
