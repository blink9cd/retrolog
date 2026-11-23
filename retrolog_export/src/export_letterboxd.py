"""One resolve row as one row of a Letterboxd import file.

Every row holds every one of ``COLUMNS``, in that order, and a cell with no value is
empty. A row is sent by tmdbID only when resolve settled its film, so a
``not-found`` or ``unresolvable`` row goes by Title and Year and the import preview
matches it. The ratings, tags, WatchedDate switch, and review note all come from the
operator's checked ``LetterboxdConfig``; this module holds no value of the operator's
own.
"""

from __future__ import annotations

from export_cells import note_text, sentence_text, stars
from export_types import SENT_BY_ID, LetterboxdConfig, ResolvedRow
from resolve_columns import CREATOR_JOINER

COLUMNS = (
    "tmdbID",
    "Title",
    "Year",
    "Directors",
    "Rating",
    "WatchedDate",
    "Rewatch",
    "Tags",
    "Review",
)
# resolve joins a film's directors with its CREATOR_JOINER; Letterboxd's Directors
# column takes commas.
_DIRECTORS_JOINER = ", "
_TAGS_JOINER = ", "
# The note sits above the comment, a blank line between them; the rating sentence
# follows the note on its line.
_NOTE_JOINER = "\n\n"
_SENTENCE_JOINER = " "


def review_text(row: ResolvedRow, config: LetterboxdConfig) -> str:
    """The row's comment, with the review note and the rating sentence above it when asked for.

    Both go on a commented row, or on every row when ``review_note_rows`` is
    ``"all"``: the note first, then the sentence, on one line. ``load_config`` already
    checked each template holds no other field, so ``str.format`` cannot fail here.
    """
    if not (config.review_note or config.review_note_rating) or not (
        row.comment or config.review_note_rows == "all"
    ):
        return row.comment
    lead = _SENTENCE_JOINER.join(
        part
        for part in (
            note_text(config.review_note, row),
            sentence_text(config.review_note_rating, row, config.rating_names),
        )
        if part
    )
    return _NOTE_JOINER.join(part for part in (lead, row.comment) if part)


def _tags(row: ResolvedRow, config: LetterboxdConfig) -> str:
    """The rating key's tag, if it has one, then the config's tags, each once."""
    rating_tag = config.rating_tags.get(row.rating_raw) if row.rating_raw else None
    ordered = ((rating_tag,) if rating_tag else ()) + config.tags
    # dict.fromkeys keeps first sightings in order, dropping later repeats.
    return _TAGS_JOINER.join(dict.fromkeys(ordered))


def row_cells(
    row: ResolvedRow, config: LetterboxdConfig, withhold_rating: bool = False
) -> tuple[str, ...]:
    """One cell per ``COLUMNS`` entry, in that order.

    A rated row's key must be in ``config.half_stars``: ``check_keys`` refuses the run
    before any row gets here otherwise. ``withhold_rating`` leaves the Rating cell
    empty and nothing else, for a film the owner already logged: its rating tag
    and the review's rating sentence still carry what the lists said.
    """
    sent_by_id = row.status in SENT_BY_ID and row.tmdb_id
    rated = row.rating_raw and not withhold_rating
    return (
        row.tmdb_id if sent_by_id else "",
        row.title,
        "" if row.year is None else str(row.year),
        row.creator.replace(CREATOR_JOINER, _DIRECTORS_JOINER),
        stars(config.half_stars[row.rating_raw]) if rated else "",
        row.date.isoformat() if config.watched_date and row.date else "",
        "true" if row.rewatch else "",
        _tags(row, config),
        review_text(row, config),
    )
