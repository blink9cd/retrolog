"""Tests for restore_output: the import file, restore.md, and the printed summary."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from pathlib import Path

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
import restore_output  # noqa: E402 - after the sys.path inserts above
from restore_build import Restored  # noqa: E402 - after the sys.path inserts above
from restore_read import DiaryEntry  # noqa: E402 - after the sys.path inserts above

_TWO = Restored(
    DiaryEntry(
        "Placeholder Film Two",
        "2001",
        "u2",
        "2026-01-05",
        rating="4.5",
        rewatch="Yes",
        tags='cinema, "late", night',
    ),
    review='Fine, "really".\nSecond line.\r\nThird.',
    rewatch="true",
    liked=True,
)
_THREE = Restored(
    DiaryEntry("Placeholder | Three", "1985", "u3", "2026-01-06"),
    review="",
    rewatch="",
    liked=False,
)


def _restored(*, name: str = "Plain", tags: str = "", review: str = "") -> Restored:
    """A restored entry of ``name`` with ``tags`` and ``review``, the free-text cells."""
    entry = DiaryEntry(name, "2001", "u", "2026-01-05", tags=tags)
    return Restored(entry, review=review, rewatch="", liked=False)


class RestoreCsvTest(unittest.TestCase):
    def test_the_header_is_the_importers(self) -> None:
        text = restore_output.restore_csv(())
        self.assertEqual(text, "Title,Year,WatchedDate,Rating,Rewatch,Tags,Review\n")

    def test_each_entry_is_one_row_of_its_cells(self) -> None:
        rows = list(csv.reader(io.StringIO(restore_output.restore_csv((_THREE,)))))
        self.assertEqual(
            rows[1], ["Placeholder | Three", "1985", "2026-01-06", "", "", "", ""]
        )

    def test_commas_quotes_and_line_breaks_round_trip(self) -> None:
        text = restore_output.restore_csv((_TWO, _THREE))
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        rows = list(reader)
        self.assertEqual(
            rows[1],
            [
                "Placeholder Film Two",
                "2001",
                "2026-01-05",
                "4.5",
                "true",
                'cinema, "late", night',
                'Fine, "really".\nSecond line.\r\nThird.',
            ],
        )
        self.assertEqual(len(rows), 3)

    def test_rows_end_in_unix_line_ends_and_plain_cells_stay_unquoted(self) -> None:
        text = restore_output.restore_csv((_THREE,))
        self.assertTrue(text.endswith("2026-01-06,,,,\n"))
        self.assertNotIn("\r\n", text)
        self.assertNotIn('"1985"', text)


class RestoreMarkdownTest(unittest.TestCase):
    def test_every_entry_is_listed_and_the_liked_are_marked(self) -> None:
        text = restore_output.restore_markdown((_TWO, _THREE))
        self.assertIn("| Placeholder Film Two | 2001 | 2026-01-05 | yes |", text)
        self.assertIn(r"| Placeholder \| Three | 1985 | 2026-01-06 | |", text)

    def test_the_counts_and_the_re_heart_step_are_stated(self) -> None:
        text = restore_output.restore_markdown((_TWO, _THREE))
        self.assertIn("2 entries", text)
        self.assertIn("1 of them to like again", text)
        self.assertIn("cannot restore a like", text)
        self.assertIn("Like again", text)
        self.assertIn("clicking a filled heart removes it", text)
        self.assertIn("letterboxd-restore.csv", text)

    def test_one_entry_none_liked_reads_in_the_singular(self) -> None:
        text = restore_output.restore_markdown((_THREE,))
        self.assertIn("1 entry", text)
        self.assertIn("none of them to like again", text)

    def test_the_text_ends_in_one_line_end(self) -> None:
        text = restore_output.restore_markdown((_THREE,))
        self.assertTrue(text.endswith("|\n"))


class SummaryTest(unittest.TestCase):
    def test_counts_only_never_a_title(self) -> None:
        lines = restore_output.summary_lines((_TWO, _THREE))
        self.assertEqual(lines[:2], ["entries restored: 2", "to like again: 1"])
        self.assertIn("restore.md", lines[2])
        self.assertNotIn("Placeholder", "\n".join(lines))

    def test_no_formula_cell_adds_no_line(self) -> None:
        lines = restore_output.summary_lines((_TWO, _THREE))
        self.assertFalse(any("formula-cell" in line for line in lines))

    def test_each_entry_with_a_formula_start_is_counted_once(self) -> None:
        restored = (
            _restored(name="=Placeholder"),
            _restored(tags="@tag"),
            _restored(review="-dash", tags="+both"),
            _restored(review="fine =later"),
            _THREE,
        )
        lines = restore_output.summary_lines(restored)
        self.assertIn("review formula-cell: 3", lines)
        self.assertNotIn("Placeholder", "\n".join(lines))

    def test_a_formula_cell_is_written_unchanged(self) -> None:
        item = _restored(name="=Placeholder", tags="\tx", review="-dash")
        rows = list(csv.reader(io.StringIO(restore_output.restore_csv((item,)))))
        self.assertEqual(
            rows[1], ["=Placeholder", "2001", "2026-01-05", "", "", "\tx", "-dash"]
        )


if __name__ == "__main__":
    unittest.main()
