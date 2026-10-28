"""One resolve row as one row of a Letterboxd import file.

Every row holds every one of ``COLUMNS``, in that order, and a cell with no value is
empty. A row is sent by tmdbID only
when resolve settled its film, so a ``not-found`` or ``unresolvable`` row goes by
Title and Year and the import preview matches it. The ratings, tags, WatchedDate switch
and review note all come from the operator's checked ``LetterboxdConfig``; this module
holds no value of the operator's own.
"""

from __future__ import annotations

import string

from export_types import SENT_BY_ID, LetterboxdConfig, ResolvedRow

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
# Stars are kept doubled: 1 is half a star, 10 is five stars.
_MIN_HALF_STARS, _MAX_HALF_STARS = 1, 10
# resolve joins a film's directors with " & "; Letterboxd's Directors column takes
# commas.
_RESOLVE_JOINER, _DIRECTORS_JOINER = " & ", ", "
_TAGS_JOINER = ", "
# The note sits above the comment, a blank line between them; the rating sentence
# follows the note on its line.
_NOTE_JOINER = "\n\n"
_SENTENCE_JOINER = " "
# The note's fields that name the watch's date; an undated row has neither to give.
_DATE_FIELDS = frozenset({"year", "date"})
# The sentence's one field, the rating key's word.
_RATING_FIELDS = frozenset({"rating"})


def stars(half_stars: int) -> str:
    """Half stars as Letterboxd writes a rating: 7 is ``"3.5"``, 10 is ``"5"``."""
    if not _MIN_HALF_STARS <= half_stars <= _MAX_HALF_STARS:
        raise ValueError(f"half stars must be {_MIN_HALF_STARS} to {_MAX_HALF_STARS}")
    whole, half = divmod(half_stars, 2)
    return f"{whole}.5" if half else str(whole)


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
        part for part in (_note(row, config), _sentence(row, config)) if part
    )
    return _NOTE_JOINER.join(part for part in (lead, row.comment) if part)


def _note(row: ResolvedRow, config: LetterboxdConfig) -> str:
    """The review note for ``row``, or "" when there is none.

    Its ``{year}`` and ``{date}`` are the watch's year and ISO date, so an undated row
    leaves out a note naming either, rather than print it with a blank where the date
    belongs.
    """
    if row.date is None:
        if _names_any(config.review_note, _DATE_FIELDS):
            return ""
        return config.review_note.format()
    return config.review_note.format(year=str(row.date.year), date=row.date.isoformat())


def _sentence(row: ResolvedRow, config: LetterboxdConfig) -> str:
    """The rating sentence for a rated ``row``, its ``{rating}`` the key's word; else "".

    An unrated row has no word to print, so it keeps the note and drops the sentence.
    The word is looked up only for a sentence naming ``{rating}``: ``load_config``
    refuses such a sentence while any key lacks a word, and ``check_keys`` every row
    key ``ratings`` lacks, so the lookup cannot miss. It is indexed rather than given
    a default, so a broken guarantee stops the run instead of printing a blank word
    into every review.
    """
    sentence = config.review_note_rating
    if not sentence or not row.rating_raw:
        return ""
    if not _names_any(sentence, _RATING_FIELDS):
        return sentence.format()
    return sentence.format(rating=config.rating_names[row.rating_raw])


def _names_any(template: str, fields: frozenset[str]) -> bool:
    """Whether ``template`` holds a field named in ``fields``."""
    return any(field in fields for _, field, _, _ in string.Formatter().parse(template))


def _tags(row: ResolvedRow, config: LetterboxdConfig) -> str:
    """The rating key's tag, if it has one, then the config's tags, each once."""
    rating_tag = config.rating_tags.get(row.rating_raw) if row.rating_raw else None
    ordered = ((rating_tag,) if rating_tag else ()) + config.tags
    # dict.fromkeys keeps first sightings in order, dropping later repeats.
    return _TAGS_JOINER.join(dict.fromkeys(ordered))


def row_cells(row: ResolvedRow, config: LetterboxdConfig) -> tuple[str, ...]:
    """One cell per ``COLUMNS`` entry, in that order.

    A rated row's key must be in ``config.half_stars``: ``check_keys`` refuses the run
    before any row gets here otherwise.
    """
    sent_by_id = row.status in SENT_BY_ID and row.tmdb_id
    return (
        row.tmdb_id if sent_by_id else "",
        row.title,
        "" if row.year is None else str(row.year),
        row.creator.replace(_RESOLVE_JOINER, _DIRECTORS_JOINER),
        stars(config.half_stars[row.rating_raw]) if row.rating_raw else "",
        row.date.isoformat() if config.watched_date and row.date else "",
        "true" if row.rewatch else "",
        _tags(row, config),
        review_text(row, config),
    )
