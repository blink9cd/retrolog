"""The keys a title is matched by in the account diff and among the held films.

Letterboxd's ``Name`` for a film and TMDb's en-US title can differ by an accent or by
"&" against "and" alone, and ``parse_rows.title_key`` keeps an accent and drops "&".
So a title is matched by its ``title_key`` and also by its folded key: the title
decomposed (NFKD), its combining marks dropped and each "&" read as "and", then
``title_key``. Both sides of every comparison are folded alike, and the plain key is
kept, so folding can only add a match, never lose one ``title_key`` made.

Only export's account diff and held-film checks use this: ``title_key`` itself stays
as it is, since other tools and resolve's grouping depend on it.
"""

from __future__ import annotations

import unicodedata

from parse_rows import title_key

# What "&" is read as. Spaced, so "Cats&Dogs" folds like "Cats and Dogs"; title_key
# collapses the spaces again.
_AND = " and "


def fold_title(title: str) -> str:
    """``title`` decomposed, its combining marks dropped and each "&" read as "and"."""
    decomposed = unicodedata.normalize("NFKD", title)
    bare = "".join(char for char in decomposed if not unicodedata.combining(char))
    return bare.replace("&", _AND)


def match_keys(title: str) -> tuple[str, ...]:
    """``title``'s ``title_key`` and folded key, each once and never ""."""
    keys = (title_key(title), title_key(fold_title(title)))
    # dict.fromkeys keeps first sightings in order, dropping a repeat.
    return tuple(dict.fromkeys(key for key in keys if key))
