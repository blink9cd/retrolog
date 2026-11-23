"""The cell text both import formats share: a rating's stars, the note, the rating sentence.

``export_letterboxd`` and ``export_goodreads`` each build their rows from these, so the
two services write a rating, a note, and a rating sentence the same way, and neither row
module imports the other. Each template is the operator's, already checked by its
service's ``load_config``; this module holds no value of the operator's own.
"""

from __future__ import annotations

from collections.abc import Mapping

from export_types import (
    MAX_HALF_STARS,
    MIN_HALF_STARS,
    NOTE_DATE_FIELDS,
    SENTENCE_FIELDS,
    ResolvedRow,
    template_names_any,
)


def stars(half_stars: int) -> str:
    """Half stars as both services write a rating: 7 is ``"3.5"``, 10 is ``"5"``."""
    if not MIN_HALF_STARS <= half_stars <= MAX_HALF_STARS:
        raise ValueError(f"half stars must be {MIN_HALF_STARS} to {MAX_HALF_STARS}")
    whole, half = divmod(half_stars, 2)
    return f"{whole}.5" if half else str(whole)


def note_text(template: str, row: ResolvedRow) -> str:
    """``template`` filled for ``row``, or "" when there is no note.

    Letterboxd's review note and Goodreads' Private Notes note both come through here.
    Its ``{year}`` and ``{date}`` are the row's year and ISO date, so an undated row
    leaves out a note naming either, rather than print it with a blank where the date
    belongs. The caller's ``load_config`` already checked the template holds no other
    field.
    """
    if row.date is None:
        if template_names_any(template, NOTE_DATE_FIELDS):
            return ""
        return template.format()
    return template.format(year=str(row.date.year), date=row.date.isoformat())


def sentence_text(template: str, row: ResolvedRow, names: Mapping[str, str]) -> str:
    """The rating sentence ``template`` for a rated ``row``, its ``{rating}`` the word.

    ``names`` maps a rating key to its word. Empty when ``template`` is empty or the
    row is unrated: an unrated row has no word to print, so it keeps the note and
    drops the sentence. The word is looked up only for a sentence naming ``{rating}``:
    each service's ``load_config`` refuses such a sentence while any key lacks a word,
    and ``check_keys`` every row key ``ratings`` lacks, so the lookup cannot miss. It
    is indexed rather than given a default, so a broken guarantee stops the run
    instead of printing a blank word into every review or note.
    """
    if not template or not row.rating_raw:
        return ""
    if not template_names_any(template, SENTENCE_FIELDS):
        return template.format()
    return template.format(rating=names[row.rating_raw])
