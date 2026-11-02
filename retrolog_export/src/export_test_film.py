"""Choose the film of export's one-film test run (``--test-film``).

The test run writes every row of one ``logged-no-stars`` film, so the operator can
import it and see whether the account keeps the film's rating before importing the
rest. The film is the first, in ``review.md``'s ``logged-no-stars`` table (the
review's own order, ``export_output.entry_order``), of those the import will tag:
a film none of whose rows becomes a diary entry or a review keeps no tag, reads on
the next run as only logged, and is sent again without stars, while any one tagged
row has the next run skip the film whole. With no such film, it is the table's
first film.
So the operator finds it there at once, and the same inputs always choose the same
film.
"""

from __future__ import annotations

from collections.abc import Sequence

from export_output import entry_order
from export_select import Film, review_entry

# The review kind of a film the account already logged, sent without stars.
_LOGGED = "logged-no-stars"


def pick_test_film(films: Sequence[Film], tagged: Sequence[bool]) -> int | None:
    """The index in ``films`` of the test run's film; ``None`` when none is logged.

    ``tagged[index]`` is whether the import tags at least one row of
    ``films[index]``; a film it tags comes first, then the review's order decides.
    """
    logged = [index for index, film in enumerate(films) if film.withhold_rating]
    if not logged:
        return None
    return min(
        logged,
        key=lambda index: (
            not tagged[index],
            entry_order(review_entry(films[index], _LOGGED)),
        ),
    )
