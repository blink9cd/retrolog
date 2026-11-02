"""Tests for export_account's bounds on a zip, and a fuzz of damaged zip bytes.

An account zip is untrusted input: its size is checked before it is opened, and its
member count and directory size before ``zipfile`` builds an entry for every member.
The property test mutates a real export's bytes and holds that only an
``AccountError`` ever escapes ``read_account``.
"""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

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
    LOGGED_HEADER,
    account_csv,
    csv_text,
    write_account_folder,
)
from export_types import Account, FilmKey  # noqa: E402 - after the sys.path inserts

# The end-of-central-directory record's signature, and its fields' offsets in it
# (APPNOTE 4.3.16): the entries on this disk, the entries in all, the directory size.
_END_RECORD = b"PK\x05\x06"
_ENTRIES_HERE, _ENTRIES_TOTAL, _DIRECTORY_SIZE = 8, 10, 12
_ZIP64_LOCATOR = b"PK\x06\x07"


def _zip_bytes(members: dict[str, str]) -> bytes:
    """A deflated zip holding each ``name -> text``, as bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buffer.getvalue()


# A small but real export: two of the four files, a subfolder file, and another file.
_EXPORT = _zip_bytes(
    {
        "watched.csv": account_csv([("Metropolis", "1927")]),
        "diary.csv": account_csv([("Placeholder Film One", "1999")]),
        "deleted/watched.csv": account_csv([("Placeholder Film Two", "2001")]),
        "profile.csv": "Username\nplaceholder\n",
    }
)


class _ZipCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.path = Path(holder.name) / "letterboxd-export.zip"

    def refusal(self, data: bytes) -> str:
        """The AccountError message for a zip of ``data``; zipfile must not open it."""
        self.path.write_bytes(data)
        with (
            mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError) as opened,
            self.assertRaises(export_account.AccountError) as caught,
        ):
            export_account.read_account(self.path)
        opened.assert_not_called()
        self.assertNotIn(str(self.path.parent), str(caught.exception))
        return str(caught.exception)


class ArchiveLimitTest(_ZipCase):
    def test_an_archive_over_the_size_limit_is_refused_unopened(self) -> None:
        with mock.patch.object(export_account, "ARCHIVE_LIMIT", len(_EXPORT) - 1):
            message = self.refusal(_EXPORT)
        self.assertEqual(message, f"--account: larger than {len(_EXPORT) - 1} bytes")

    def test_an_archive_at_the_size_limit_is_read(self) -> None:
        self.path.write_bytes(_EXPORT)
        with mock.patch.object(export_account, "ARCHIVE_LIMIT", len(_EXPORT)):
            account = export_account.read_account(self.path)
        self.assertIn(FilmKey("metropolis", 1927), account.watched)

    def test_the_limits_leave_room_for_a_real_export(self) -> None:
        self.assertGreaterEqual(export_account.ARCHIVE_LIMIT, 4 * 32 * 1024 * 1024)
        self.assertEqual(export_account.MAX_MEMBERS, 10_000)


class MemberLimitTest(_ZipCase):
    def test_too_many_members_are_refused_before_zipfile_lists_them(self) -> None:
        with mock.patch.object(export_account, "MAX_MEMBERS", 3):
            message = self.refusal(_EXPORT)
        self.assertEqual(message, "--account: holds more than 3 files")

    def test_the_member_limit_itself_is_read(self) -> None:
        self.path.write_bytes(_EXPORT)
        with mock.patch.object(export_account, "MAX_MEMBERS", 4):
            account = export_account.read_account(self.path)
        self.assertEqual(len(account.watched | account.logged), 2)

    def test_a_directory_over_its_limit_is_refused_unopened(self) -> None:
        # zipfile reads entries until the directory's bytes run out, not up to the
        # count the end record gives, so the directory's size is bounded too.
        with mock.patch.object(export_account, "DIRECTORY_LIMIT", 10):
            message = self.refusal(_EXPORT)
        self.assertEqual(
            message, "--account: its zip directory is larger than 10 bytes"
        )

    def test_an_end_record_understating_its_members_is_still_caught(self) -> None:
        data = bytearray(_EXPORT)
        end = data.rindex(_END_RECORD)
        for field in (_ENTRIES_HERE, _ENTRIES_TOTAL):
            data[end + field : end + field + 2] = (1).to_bytes(2, "little")
        self.path.write_bytes(bytes(data))
        with (
            mock.patch.object(export_account, "MAX_MEMBERS", 3),
            self.assertRaises(export_account.AccountError) as caught,
        ):
            export_account.read_account(self.path)
        self.assertEqual(str(caught.exception), "--account: holds more than 3 files")

    def test_a_zip64_archive_is_refused_unopened(self) -> None:
        end = _EXPORT.rindex(_END_RECORD)
        locator = _ZIP64_LOCATOR + bytes(16)
        message = self.refusal(_EXPORT[:end] + locator + _EXPORT[end:])
        self.assertEqual(
            message,
            "--account: a zip64 archive, which is not read; unpack it and pass "
            "the folder instead",
        )

    def test_a_file_with_no_end_record_is_left_to_zipfile(self) -> None:
        self.path.write_bytes(b"not a zip archive")
        with self.assertRaises(export_account.AccountError) as caught:
            export_account.read_account(self.path)
        self.assertEqual(
            str(caught.exception), "--account: not a readable folder or zip"
        )

    def test_an_end_record_cut_short_is_left_to_zipfile(self) -> None:
        self.path.write_bytes(_EXPORT[: _EXPORT.rindex(_END_RECORD) + 10])
        with self.assertRaises(export_account.AccountError):
            export_account.read_account(self.path)


# Up to eight byte overwrites anywhere in the export, then an optional cut.
# Longer than csv's default field limit of 131072 characters.
_LONG_REVIEW = "x" * 200_000


class CsvFieldLimitTest(unittest.TestCase):
    def test_a_review_past_the_default_field_limit_is_read(self) -> None:
        reviews = csv_text(
            [{"Name": "Metropolis", "Year": "1927", "Review": _LONG_REVIEW}],
            (*LOGGED_HEADER, "Review"),
        )
        with tempfile.TemporaryDirectory() as folder:
            path = write_account_folder(Path(folder), {"reviews.csv": reviews})
            account = export_account.read_account(path)
        self.assertIn(FilmKey("metropolis", 1927), account.logged)

    def test_the_limit_is_raised_inside_and_restored_after(self) -> None:
        previous = csv.field_size_limit()
        with export_account.csv_field_limit():
            self.assertEqual(csv.field_size_limit(), export_account.MEMBER_LIMIT)
        self.assertEqual(csv.field_size_limit(), previous)

    def test_the_limit_is_restored_when_the_body_raises(self) -> None:
        previous = csv.field_size_limit()
        with (
            self.assertRaises(KeyError),
            export_account.csv_field_limit(),
        ):
            raise KeyError("raised inside")
        self.assertEqual(csv.field_size_limit(), previous)

    def test_the_parse_leaves_the_limit_as_it_found_it(self) -> None:
        previous = csv.field_size_limit()
        export_account.parse_account_csv("Name,Year\nMetropolis,1927\n")
        self.assertEqual(csv.field_size_limit(), previous)


_MUTATIONS = st.lists(
    st.tuples(st.integers(0, len(_EXPORT) - 1), st.integers(0, 255)), max_size=8
)


class ReadAccountPropertyTest(unittest.TestCase):
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(_MUTATIONS, st.one_of(st.none(), st.integers(0, len(_EXPORT))))
    def test_fuzz_read_account(
        self, overwrites: list[tuple[int, int]], cut: int | None
    ) -> None:
        """A damaged export gives films or an AccountError; nothing else escapes."""
        data = bytearray(_EXPORT)
        for at, value in overwrites:
            data[at] = value
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "letterboxd-export.zip"
            path.write_bytes(bytes(data[:cut]))
            try:
                account = export_account.read_account(path)
            except export_account.AccountError as error:
                self.assertNotIn(folder, str(error))
                return
        self.assertIsInstance(account, Account)


if __name__ == "__main__":
    unittest.main()
