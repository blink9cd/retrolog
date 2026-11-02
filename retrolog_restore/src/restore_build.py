"""Find the diary entries deleted between two exports, and what each one restores.

Pure: it takes the two exports' rows and returns the entries to restore, in the
backup's order (oldest logged first), each with its review, its rewatch flag as the
importer reads it, and whether the film needs liking again. Only the backup's reviews
are read: the deleted entries' reviews went with them. Both exports' likes are read,
since deleting a diary entry leaves its film's heart: a film is to be liked again only
when the backup likes it and the later export does not, because clicking a filled
heart removes the like.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from restore_read import DIARY, REVIEWS, DiaryEntry, ExportFiles, RestoreError

# diary.csv's Rewatch cell for a rewatch, and the importer's value for it; an empty
# cell stays empty.
_REWATCH_YES, _IMPORT_TRUE = "Yes", "true"

# A watch: the film's name and year, and the date it was watched.
_Watch = tuple[str, str, str]
# A review's join key: its URI, or its watch.
_Key = str | _Watch


@dataclass(frozen=True)
class Restored:
    """One deleted diary entry and what the import file restores with it."""

    entry: DiaryEntry
    review: str  # the backup's review of this film on this date, or ""
    rewatch: str  # "true" for a rewatch, else ""
    liked: bool  # liked in the backup and not in the later export: like it again


def _watch(entry: DiaryEntry) -> _Watch:
    """The film and date of ``entry``, which join a review to its diary entry."""
    return (entry.name, entry.year, entry.watched_date)


def deleted_entries(
    backup: Sequence[DiaryEntry], current: Sequence[DiaryEntry]
) -> tuple[DiaryEntry, ...]:
    """The ``backup`` diary entries ``current`` lacks, in ``backup``'s order.

    An entry is matched by its ``Letterboxd URI``; one with no URI falls back to its
    name, year, and watched date.
    """
    uris = {entry.uri for entry in current if entry.uri}
    watches = {_watch(entry) for entry in current}
    return tuple(
        entry
        for entry in backup
        if (entry.uri not in uris if entry.uri else _watch(entry) not in watches)
    )


def _texts_by(
    reviews: Sequence[DiaryEntry], key: Callable[[DiaryEntry], _Key]
) -> dict[_Key, list[str]]:
    """Each ``key``'s review texts, in file order."""
    found: dict[_Key, list[str]] = {}
    for review in reviews:
        found.setdefault(key(review), []).append(review.review)
    return found


def _uri(entry: DiaryEntry) -> str:
    """The ``Letterboxd URI`` of ``entry``, a review's exact join key."""
    return entry.uri


def _joined_reviews(
    deleted: Sequence[DiaryEntry],
    diary: Sequence[DiaryEntry],
    reviews: Sequence[DiaryEntry],
) -> list[list[str]]:
    """Each deleted entry's matching review texts, in ``deleted``'s order.

    A review whose URI is a backup diary entry's URI belongs to that entry alone. Any
    other review joins by film and watched date, so two watches of one film on one
    date share it there.
    """
    diary_uris = {entry.uri for entry in diary if entry.uri}
    by_uri = _texts_by([r for r in reviews if r.uri in diary_uris], _uri)
    by_watch = _texts_by([r for r in reviews if r.uri not in diary_uris], _watch)
    return [
        by_uri[entry.uri] if entry.uri in by_uri else by_watch.get(_watch(entry), [])
        for entry in deleted
    ]


def _check_one_review_each(joined: Sequence[Sequence[str]]) -> None:
    """Refuse when a deleted entry matches more than one review; counts only."""
    count = sum(1 for texts in joined if len(texts) > 1)
    if count:
        noun = "entry matches" if count == 1 else "entries match"
        raise RestoreError(
            f"{REVIEWS}: {count} restored {noun} more than one review by URI, "
            "or by name, year, and watched date"
        )


def _rewatch(cell: str) -> str:
    """diary.csv's ``Rewatch`` cell as the importer reads it: "true" or ""."""
    if cell == _REWATCH_YES:
        return _IMPORT_TRUE
    if not cell:
        return ""
    raise RestoreError(f"{DIARY}: a restored entry's Rewatch is neither Yes nor empty")


# fuzz-target
def restore_entries(backup: ExportFiles, current: ExportFiles) -> tuple[Restored, ...]:
    """Each diary entry of ``backup`` that ``current`` lacks, ready to restore.

    A review joins its entry by ``Letterboxd URI``, else by name, year, and watched
    date. Raises ``RestoreError`` when a deleted entry's Rewatch is neither ``Yes``
    nor empty, or more than one of the backup's reviews matches a deleted entry.
    """
    deleted = deleted_entries(backup.diary, current.diary)
    joined = _joined_reviews(deleted, backup.diary, backup.reviews)
    _check_one_review_each(joined)
    still_liked = {(like.name, like.year) for like in current.likes}
    to_like = {(like.name, like.year) for like in backup.likes} - still_liked
    return tuple(
        Restored(
            entry=entry,
            review=texts[0] if texts else "",
            rewatch=_rewatch(entry.rewatch),
            liked=(entry.name, entry.year) in to_like,
        )
        for entry, texts in zip(deleted, joined, strict=True)
    )
