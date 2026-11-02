"""Tests for export_account.read_export_texts: named files of an export, as text.

The reader ``read_account`` uses, opened to other callers: any member name, a
subfolder's member among them, and the option an error message names.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The tool's own src/ first, then resolve's, parse's, and the survey's: the order
# retrolog_export.SOURCE_DIRS puts them on sys.path.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
    str(_TOOL),
]
import export_account  # noqa: E402 - after the sys.path inserts above
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    write_account_folder,
    write_account_zip,
)

_NAMES = ("diary.csv", "likes/films.csv")
_OPTION = "--backup"
_DIARY = "Name,Year\nPlaceholder Film One,1999\n"
_LIKES = "Name,Year\nPlaceholder Film Two,2001\n"


class ReadExportTextsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _refusal(self, path: Path) -> str:
        """The message of the ``AccountError`` reading ``path`` raises."""
        with self.assertRaises(export_account.AccountError) as raised:
            export_account.read_export_texts(path, _NAMES, _OPTION)
        return str(raised.exception)

    def test_a_zip_gives_each_named_member_a_subfolder_one_included(self) -> None:
        path = write_account_zip(
            self.root / "export.zip",
            {
                "diary.csv": _DIARY,
                "likes/films.csv": _LIKES,
                "films.csv": "Name,Year\n",
                "deleted/diary.csv": "Name,Year\n",
            },
        )
        texts = export_account.read_export_texts(path, _NAMES, _OPTION)
        self.assertEqual(texts, {"diary.csv": _DIARY, "likes/films.csv": _LIKES})

    def test_a_folder_gives_each_named_file_a_subfolder_one_included(self) -> None:
        folder = write_account_folder(
            self.root / "export",
            {"diary.csv": _DIARY, "likes/films.csv": _LIKES, "films.csv": "x\n"},
        )
        texts = export_account.read_export_texts(folder, _NAMES, _OPTION)
        self.assertEqual(texts, {"diary.csv": _DIARY, "likes/films.csv": _LIKES})

    def test_an_absent_member_is_left_out(self) -> None:
        folder = write_account_folder(self.root / "export", {"diary.csv": _DIARY})
        texts = export_account.read_export_texts(folder, _NAMES, _OPTION)
        self.assertEqual(texts, {"diary.csv": _DIARY})

    def test_a_byte_order_mark_is_dropped(self) -> None:
        folder = write_account_folder(
            self.root / "export", {"diary.csv": "\ufeff" + _DIARY}
        )
        texts = export_account.read_export_texts(folder, _NAMES, _OPTION)
        self.assertEqual(texts, {"diary.csv": _DIARY})

    def test_an_unreadable_export_names_the_option(self) -> None:
        path = self.root / "export.zip"
        path.write_bytes(b"not a zip archive")
        message = self._refusal(path)
        self.assertIn(_OPTION, message)
        self.assertNotIn("--account", message)

    def test_a_missing_export_names_the_option(self) -> None:
        self.assertIn(_OPTION, self._refusal(self.root / "absent.zip"))

    def test_the_archive_limits_name_the_option(self) -> None:
        path = write_account_zip(
            self.root / "export.zip", {"diary.csv": _DIARY, "a.csv": "", "b.csv": ""}
        )
        with mock.patch.object(export_account, "MAX_MEMBERS", 2):
            self.assertIn(f"{_OPTION}: holds more than 2 files", self._refusal(path))
        with mock.patch.object(export_account, "ARCHIVE_LIMIT", 10):
            self.assertIn(f"{_OPTION}: larger than 10 bytes", self._refusal(path))

    def test_the_member_count_after_opening_names_the_option(self) -> None:
        path = write_account_zip(self.root / "export.zip", {"diary.csv": _DIARY})
        with (
            mock.patch.object(export_account, "_check_archive", return_value=None),
            mock.patch.object(export_account, "MAX_MEMBERS", 0),
        ):
            self.assertIn(f"{_OPTION}: holds more than 0 files", self._refusal(path))

    def test_an_unlistable_folder_names_the_option(self) -> None:
        folder = write_account_folder(self.root / "export", {"diary.csv": _DIARY})
        with mock.patch.object(Path, "is_file", side_effect=OSError(str(folder))):
            message = self._refusal(folder)
        self.assertIn(f"{_OPTION}: cannot be read", message)
        self.assertNotIn(str(folder), message)

    def test_a_member_too_large_names_the_member(self) -> None:
        folder = write_account_folder(self.root / "export", {"diary.csv": _DIARY})
        with mock.patch.object(export_account, "MEMBER_LIMIT", 4):
            self.assertIn("diary.csv: larger than 4 bytes", self._refusal(folder))

    def test_a_member_not_utf8_names_the_member(self) -> None:
        folder = write_account_folder(self.root / "export", {"diary.csv": b"\xff\xfe"})
        self.assertIn("diary.csv: not UTF-8", self._refusal(folder))

    def test_read_account_still_names_account(self) -> None:
        path = self.root / "export.zip"
        path.write_bytes(b"not a zip archive")
        with self.assertRaises(export_account.AccountError) as raised:
            export_account.read_account(path)
        self.assertEqual(
            str(raised.exception), "--account: not a readable folder or zip"
        )


if __name__ == "__main__":
    unittest.main()
