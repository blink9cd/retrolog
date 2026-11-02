"""Tests for restore_build: which diary entries were deleted, and what each restores."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The tool's own src/ first, then export's, resolve's, parse's, and the survey's: the
# order retrolog_restore.SOURCE_DIRS puts them on sys.path.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_export" / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
]
import restore_build  # noqa: E402 - after the sys.path inserts above
from restore_build import Restored  # noqa: E402 - after the sys.path inserts above
from restore_read import DiaryEntry, ExportFiles, RestoreError  # noqa: E402

_ONE = DiaryEntry("Placeholder Film One", "1999", "u1", "2026-01-02")
_TWO = DiaryEntry("Placeholder Film Two", "2001", "u2", "2026-01-05", rewatch="Yes")
_THREE = DiaryEntry("Placeholder Film Three", "1985", "u3", "2026-01-06")
_UNLINKED = DiaryEntry("Placeholder Film Four", "2010", "", "2026-01-07")


def _export(
    diary: tuple[DiaryEntry, ...],
    reviews: tuple[DiaryEntry, ...] = (),
    likes: tuple[DiaryEntry, ...] = (),
) -> ExportFiles:
    """An export of these rows."""
    return ExportFiles(diary=diary, reviews=reviews, likes=likes)


def _review(of: DiaryEntry, text: str, **changes: str) -> DiaryEntry:
    """A reviews.csv row for ``of``'s film and date, ``changes`` applied."""
    cells = {
        "name": of.name,
        "year": of.year,
        "watched_date": of.watched_date,
        "uri": "r",
    }
    return DiaryEntry(**(cells | changes), review=text)


class DeletedEntriesTest(unittest.TestCase):
    def test_entries_whose_uri_is_gone_are_deleted_in_backup_order(self) -> None:
        deleted = restore_build.deleted_entries((_THREE, _ONE, _TWO), (_ONE,))
        self.assertEqual(deleted, (_THREE, _TWO))

    def test_an_entry_still_present_by_uri_is_kept_whatever_else_changed(
        self,
    ) -> None:
        edited = DiaryEntry("Placeholder Renamed", "2000", "u1", "2026-03-03")
        self.assertEqual(restore_build.deleted_entries((_ONE,), (edited,)), ())

    def test_an_entry_without_a_uri_falls_back_to_name_year_and_date(self) -> None:
        present = DiaryEntry(_UNLINKED.name, _UNLINKED.year, "u9", "2026-01-07")
        self.assertEqual(restore_build.deleted_entries((_UNLINKED,), (present,)), ())
        moved = DiaryEntry(_UNLINKED.name, _UNLINKED.year, "u9", "2026-01-08")
        self.assertEqual(
            restore_build.deleted_entries((_UNLINKED,), (moved,)), (_UNLINKED,)
        )

    def test_an_empty_current_uri_matches_no_backup_entry(self) -> None:
        self.assertEqual(restore_build.deleted_entries((_ONE,), (_UNLINKED,)), (_ONE,))


class RestoreEntriesTest(unittest.TestCase):
    def test_each_deleted_entry_gets_its_review_rewatch_and_like(self) -> None:
        backup = _export(
            (_ONE, _TWO, _THREE),
            reviews=(_review(_TWO, 'Fine, "really".\nYes.'), _review(_ONE, "x")),
            likes=(DiaryEntry(_TWO.name, _TWO.year),),
        )
        restored = restore_build.restore_entries(backup, _export((_ONE,)))
        self.assertEqual(
            restored,
            (
                Restored(
                    _TWO, review='Fine, "really".\nYes.', rewatch="true", liked=True
                ),
                Restored(_THREE, review="", rewatch="", liked=False),
            ),
        )

    def test_a_review_of_the_same_film_on_another_date_is_not_joined(self) -> None:
        backup = _export((_TWO,), reviews=(_review(_TWO, "x", watched_date="2020"),))
        restored = restore_build.restore_entries(backup, _export(()))
        self.assertEqual(restored[0].review, "")

    def test_a_like_of_the_same_title_in_another_year_does_not_count(self) -> None:
        backup = _export((_TWO,), likes=(DiaryEntry(_TWO.name, "2002"),))
        self.assertFalse(restore_build.restore_entries(backup, _export(()))[0].liked)

    def test_the_current_exports_reviews_are_not_read(self) -> None:
        current = _export((), reviews=(_review(_TWO, "x"),))
        restored = restore_build.restore_entries(_export((_TWO,)), current)
        self.assertEqual(restored[0].review, "")

    def test_a_film_still_liked_on_the_account_is_not_marked_to_like_again(
        self,
    ) -> None:
        # Deleting a diary entry leaves the heart, and clicking a filled heart
        # removes the like: only a like the account lost is marked.
        likes = (DiaryEntry(_TWO.name, _TWO.year), DiaryEntry(_THREE.name, "1985"))
        backup = _export((_TWO, _THREE), likes=likes)
        current = _export((), likes=(DiaryEntry(_TWO.name, _TWO.year),))
        restored = restore_build.restore_entries(backup, current)
        self.assertEqual([item.liked for item in restored], [False, True])

    def test_a_like_only_on_the_current_account_does_not_count(self) -> None:
        current = _export((), likes=(DiaryEntry(_TWO.name, _TWO.year),))
        restored = restore_build.restore_entries(_export((_TWO,)), current)
        self.assertFalse(restored[0].liked)

    def test_two_watches_of_one_film_on_one_date_each_get_their_own_review(
        self,
    ) -> None:
        first = DiaryEntry(_TWO.name, _TWO.year, "u2a", _TWO.watched_date)
        second = DiaryEntry(_TWO.name, _TWO.year, "u2b", _TWO.watched_date)
        reviews = (_review(_TWO, "b", uri="u2b"), _review(_TWO, "a", uri="u2a"))
        backup = _export((first, second), reviews=reviews)
        restored = restore_build.restore_entries(backup, _export(()))
        self.assertEqual([item.review for item in restored], ["a", "b"])

    def test_a_review_joined_by_uri_is_not_shared_by_the_date_fallback(
        self,
    ) -> None:
        first = DiaryEntry(_TWO.name, _TWO.year, "u2a", _TWO.watched_date)
        second = DiaryEntry(_TWO.name, _TWO.year, "u2b", _TWO.watched_date)
        backup = _export((first, second), reviews=(_review(_TWO, "a", uri="u2a"),))
        restored = restore_build.restore_entries(backup, _export(()))
        self.assertEqual([item.review for item in restored], ["a", ""])

    def test_a_review_naming_a_kept_entrys_uri_is_not_joined_by_date(self) -> None:
        kept = DiaryEntry(_TWO.name, _TWO.year, "u2k", _TWO.watched_date)
        backup = _export((kept, _TWO), reviews=(_review(_TWO, "k", uri="u2k"),))
        restored = restore_build.restore_entries(backup, _export((kept,)))
        self.assertEqual([item.review for item in restored], [""])

    def test_a_review_whose_uri_names_no_entry_falls_back_to_film_and_date(
        self,
    ) -> None:
        backup = _export((_TWO,), reviews=(_review(_TWO, "x", uri="elsewhere"),))
        restored = restore_build.restore_entries(backup, _export(()))
        self.assertEqual(restored[0].review, "x")

    def test_two_reviews_with_one_entrys_uri_are_refused(self) -> None:
        twice = (_review(_TWO, "a", uri="u2"), _review(_TWO, "b", uri="u2"))
        with self.assertRaises(RestoreError) as raised:
            restore_build.restore_entries(_export((_TWO,), reviews=twice), _export(()))
        self.assertIn("1 restored entry matches more than one", str(raised.exception))

    def test_entries_matching_more_than_one_review_are_refused_by_count(
        self,
    ) -> None:
        twice = (_review(_TWO, "a"), _review(_TWO, "b"))
        thrice = (_review(_THREE, "a"),) * 3
        for reviews, count in (
            (twice, "1 restored entry matches"),
            (twice + thrice, "2 restored entries match"),
        ):
            with self.subTest(count=count), self.assertRaises(RestoreError) as raised:
                restore_build.restore_entries(
                    _export((_TWO, _THREE), reviews=reviews), _export(())
                )
            message = str(raised.exception)
            self.assertIn(f"reviews.csv: {count} more than one review", message)
            self.assertNotIn("Placeholder", message)

    def test_a_kept_entry_with_two_reviews_is_not_refused(self) -> None:
        backup = _export((_ONE,), reviews=(_review(_ONE, "a"), _review(_ONE, "b")))
        self.assertEqual(restore_build.restore_entries(backup, _export((_ONE,))), ())

    def test_a_rewatch_cell_neither_yes_nor_empty_is_refused(self) -> None:
        for cell in ("No", "yes", "true", " Yes"):
            odd = DiaryEntry("Placeholder Secret", "1", "u", rewatch=cell)
            with self.subTest(cell=cell), self.assertRaises(RestoreError) as raised:
                restore_build.restore_entries(_export((odd,)), _export(()))
            self.assertEqual(
                str(raised.exception),
                "diary.csv: a restored entry's Rewatch is neither Yes nor empty",
            )

    def test_nothing_deleted_restores_nothing(self) -> None:
        self.assertEqual(
            restore_build.restore_entries(_export((_ONE,)), _export((_ONE, _TWO))), ()
        )


_CELLS = st.sampled_from(["", "a", "b"])
_ENTRIES = st.builds(
    DiaryEntry,
    name=_CELLS,
    year=_CELLS,
    uri=_CELLS,
    watched_date=_CELLS,
    rewatch=st.sampled_from(["", "Yes", "No"]),
    review=_CELLS,
)
_EXPORTS = st.builds(
    ExportFiles,
    diary=st.tuples(_ENTRIES, _ENTRIES, _ENTRIES),
    reviews=st.lists(_ENTRIES, max_size=4).map(tuple),
    likes=st.lists(_ENTRIES, max_size=4).map(tuple),
)


class RestorePropertiesTest(unittest.TestCase):
    @given(_EXPORTS, _EXPORTS)
    def test_fuzz_restore_entries(
        self, backup: ExportFiles, current: ExportFiles
    ) -> None:
        # Only RestoreError escapes, and what is restored is the deleted entries, in
        # the backup's order, none of them still on the account by its URI.
        try:
            restored = restore_build.restore_entries(backup, current)
        except RestoreError:
            return
        entries = tuple(item.entry for item in restored)
        self.assertEqual(
            entries, restore_build.deleted_entries(backup.diary, current.diary)
        )
        uris = {row.uri for row in current.diary}
        for item in restored:
            self.assertFalse(item.entry.uri and item.entry.uri in uris)
            self.assertIn(item.rewatch, ("", "true"))


if __name__ == "__main__":
    unittest.main()
