"""Tests for export_account: reading the films already on a Letterboxd account."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The tool's own src/ first, then resolve's, parse's and the survey's: the order
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
    account_csv,
    write_account_folder,
    write_account_zip,
)
from export_types import FilmKey  # noqa: E402 - after the sys.path inserts above

_ONE = ("Placeholder Film One", "1999")
_TWO = ("Metropolis", "1927")
_UNDATED = ("Placeholder Film Two", "")
# The signatures that open a zip's records (APPNOTE 4.3.7, 4.3.12 and 4.3.16).
_LOCAL_HEADER = b"PK\x03\x04"
_CENTRAL_RECORD = b"PK\x01\x02"
_END_RECORD = b"PK\x05\x06"


class ParseAccountCsvTest(unittest.TestCase):
    def test_rows_become_title_key_and_year(self) -> None:
        keys = export_account.parse_account_csv(account_csv([_ONE, _TWO]))
        self.assertEqual(
            keys,
            {FilmKey("placeholder film one", 1999), FilmKey("metropolis", 1927)},
        )

    def test_a_name_is_also_keyed_without_accents_and_with_and(self) -> None:
        # Regression: an accent or an "&" the TMDb title lacked kept the film off
        # the diff, so it was sent and overwrote the live rating.
        keys = export_account.parse_account_csv(
            "Name,Year\nPlaceholder Café & Co,1999\n"
        )
        self.assertEqual(
            keys,
            {
                FilmKey("placeholder café co", 1999),
                FilmKey("placeholder cafe and co", 1999),
            },
        )

    def test_a_plain_name_gives_one_key(self) -> None:
        keys = export_account.parse_account_csv("Name,Year\nMetropolis,1927\n")
        self.assertEqual(keys, {FilmKey("metropolis", 1927)})

    def test_empty_or_non_numeric_year_is_none(self) -> None:
        keys = export_account.parse_account_csv(
            account_csv([_UNDATED, ("Placeholder Film Three", "19x9")])
        )
        self.assertEqual(
            keys,
            {
                FilmKey("placeholder film two", None),
                FilmKey("placeholder film three", None),
            },
        )

    def test_non_ascii_digits_and_overlong_years_are_none(self) -> None:
        keys = export_account.parse_account_csv(
            account_csv([("Placeholder Film One", "١٩٩٩"), ("Metropolis", "1" * 5000)])
        )
        self.assertEqual(
            keys,
            {FilmKey("placeholder film one", None), FilmKey("metropolis", None)},
        )

    def test_row_with_an_empty_name_is_skipped(self) -> None:
        keys = export_account.parse_account_csv(
            account_csv([("", "2001"), ("?!", "2002"), _TWO])
        )
        self.assertEqual(keys, {FilmKey("metropolis", 1927)})

    def test_short_row_is_read_with_what_it_has(self) -> None:
        text = "Name,Year\nMetropolis\n,\n"
        keys = export_account.parse_account_csv(text)
        self.assertEqual(keys, {FilmKey("metropolis", None)})

    def test_other_columns_and_their_order_are_ignored(self) -> None:
        text = account_csv([_TWO], header=("Rating", "Year", "Extra", "Name"))
        keys = export_account.parse_account_csv(text)
        self.assertEqual(keys, {FilmKey("metropolis", 1927)})

    def test_quoted_name_with_a_comma_parses(self) -> None:
        text = account_csv([("Placeholder, the Film", "2003")])
        self.assertIn('"Placeholder, the Film"', text)
        keys = export_account.parse_account_csv(text)
        self.assertEqual(keys, {FilmKey("placeholder the film", 2003)})

    def test_leading_bom_is_accepted(self) -> None:
        keys = export_account.parse_account_csv("\ufeff" + account_csv([_TWO]))
        self.assertEqual(keys, {FilmKey("metropolis", 1927)})

    def test_header_without_name_or_year_is_refused(self) -> None:
        for header in (("Date", "Year"), ("Date", "Name"), ()):
            with self.subTest(header=header):
                text = ",".join(header) + "\n"
                with self.assertRaises(export_account.AccountError):
                    export_account.parse_account_csv(text)

    def test_empty_text_is_refused(self) -> None:
        with self.assertRaises(export_account.AccountError):
            export_account.parse_account_csv("")

    def test_invalid_csv_is_refused_without_quoting_it(self) -> None:
        text = 'Name,Year\n"Placeholder Secret Title" trailing,1999\n'
        with self.assertRaises(export_account.AccountError) as caught:
            export_account.parse_account_csv(text)
        self.assertNotIn("Placeholder Secret Title", str(caught.exception))

    @given(st.text(max_size=300))
    def test_fuzz_parse_account_csv(self, text: str) -> None:
        try:
            keys = export_account.parse_account_csv(text)
        except export_account.AccountError:
            return
        for key in keys:
            self.assertTrue(key.title_key)


class ReadAccountTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.files: dict[str, str | bytes] = {
            "watched.csv": account_csv([_ONE, _TWO]),
            "diary.csv": account_csv([_UNDATED]),
            "ratings.csv": account_csv([_TWO]),
            "reviews.csv": account_csv([("Placeholder Film Four", "2004")]),
        }

    def _zip(self, members: dict[str, str | bytes]) -> Path:
        return write_account_zip(self.root / "letterboxd-export.zip", members)

    def _folder(self, files: dict[str, str | bytes]) -> Path:
        return write_account_folder(self.root / "letterboxd-export", files)

    def _refusal(self, path: Path) -> str:
        """The AccountError message ``read_account`` gives for ``path``."""
        with self.assertRaises(export_account.AccountError) as caught:
            export_account.read_account(path)
        message = str(caught.exception)
        self.assertNotIn(str(self.root), message)
        self.assertIsNone(caught.exception.__cause__)
        return message

    def test_account_files_are_letterboxds_four(self) -> None:
        self.assertEqual(
            export_account.ACCOUNT_FILES,
            ("watched.csv", "diary.csv", "ratings.csv", "reviews.csv"),
        )
        self.assertEqual(export_account.MEMBER_LIMIT, 32 * 1024 * 1024)

    def test_zip_and_folder_give_the_union_of_every_file(self) -> None:
        expected = {
            FilmKey("placeholder film one", 1999),
            FilmKey("metropolis", 1927),
            FilmKey("placeholder film two", None),
            FilmKey("placeholder film four", 2004),
        }
        from_zip = export_account.read_account(self._zip(self.files))
        from_folder = export_account.read_account(self._folder(self.files))
        self.assertEqual(from_zip, expected)
        self.assertEqual(from_folder, expected)

    def test_only_the_files_present_are_read(self) -> None:
        files: dict[str, str | bytes] = {"ratings.csv": account_csv([_TWO])}
        expected = {FilmKey("metropolis", 1927)}
        self.assertEqual(export_account.read_account(self._zip(files)), expected)
        self.assertEqual(export_account.read_account(self._folder(files)), expected)

    def test_subfolders_and_other_files_are_ignored(self) -> None:
        files: dict[str, str | bytes] = {
            "watched.csv": account_csv([_TWO]),
            "deleted/diary.csv": account_csv([_ONE]),
            "likes/films.csv": account_csv([_ONE]),
            "profile.csv": "not, an, account file\n",
            "lists/diary.csv": b"\xff\xfe not utf-8",
        }
        expected = {FilmKey("metropolis", 1927)}
        self.assertEqual(export_account.read_account(self._zip(files)), expected)
        self.assertEqual(export_account.read_account(self._folder(files)), expected)

    def test_a_folder_named_like_an_account_file_is_ignored(self) -> None:
        folder = self._folder({"watched.csv": account_csv([_TWO])})
        (folder / "diary.csv").mkdir()
        self.assertEqual(
            export_account.read_account(folder), {FilmKey("metropolis", 1927)}
        )

    def test_a_zip_directory_entry_named_like_an_account_file_is_ignored(
        self,
    ) -> None:
        path = self._zip({"watched.csv": account_csv([_TWO])})
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr(zipfile.ZipInfo("diary.csv/"), b"")
        self.assertEqual(
            export_account.read_account(path), {FilmKey("metropolis", 1927)}
        )

    def test_export_holding_no_account_file_is_refused(self) -> None:
        files: dict[str, str | bytes] = {"deleted/watched.csv": account_csv([_TWO])}
        self.assertIn("none of", self._refusal(self._zip(files)))
        self.assertIn("none of", self._refusal(self._folder(files)))

    def test_bom_prefixed_watched_csv_reads(self) -> None:
        files: dict[str, str | bytes] = {
            "watched.csv": b"\xef\xbb\xbf" + account_csv([_TWO]).encode()
        }
        expected = {FilmKey("metropolis", 1927)}
        self.assertEqual(export_account.read_account(self._zip(files)), expected)
        self.assertEqual(export_account.read_account(self._folder(files)), expected)

    def test_non_utf8_file_is_refused(self) -> None:
        files: dict[str, str | bytes] = {"diary.csv": b"Name,Year\n\xff\xfe,1999\n"}
        self.assertIn("diary.csv: not UTF-8", self._refusal(self._zip(files)))
        self.assertIn("diary.csv: not UTF-8", self._refusal(self._folder(files)))

    def test_bad_header_names_the_file(self) -> None:
        files: dict[str, str | bytes] = {"reviews.csv": "Title,Released\n"}
        self.assertIn("reviews.csv", self._refusal(self._zip(files)))

    def test_member_declared_over_the_limit_is_refused_before_reading(self) -> None:
        path = self._zip(self.files)
        info = zipfile.ZipInfo("watched.csv")
        info.file_size = export_account.MEMBER_LIMIT + 1
        with (
            mock.patch.object(zipfile.ZipFile, "infolist", return_value=[info]),
            mock.patch.object(zipfile.ZipFile, "open") as opened,
        ):
            message = self._refusal(path)
        self.assertIn("watched.csv: larger than", message)
        opened.assert_not_called()

    def test_member_declared_at_the_limit_is_read(self) -> None:
        path = self._zip(self.files)
        info = zipfile.ZipInfo("watched.csv")
        info.file_size = export_account.MEMBER_LIMIT
        body = io.BytesIO(account_csv([_TWO]).encode())
        with (
            mock.patch.object(zipfile.ZipFile, "infolist", return_value=[info]),
            mock.patch.object(zipfile.ZipFile, "open", return_value=body),
        ):
            keys = export_account.read_account(path)
        self.assertEqual(keys, {FilmKey("metropolis", 1927)})

    def test_member_whose_header_lies_is_capped_while_reading(self) -> None:
        path = self._zip({"watched.csv": account_csv([_TWO])})
        with mock.patch.object(export_account, "MEMBER_LIMIT", 8):
            info = zipfile.ZipInfo("watched.csv")
            info.file_size = 1
            reader = mock.MagicMock()
            reader.__enter__.return_value.read.return_value = b"x" * 9
            with (
                mock.patch.object(zipfile.ZipFile, "infolist", return_value=[info]),
                mock.patch.object(zipfile.ZipFile, "open", return_value=reader),
            ):
                message = self._refusal(path)
        reader.__enter__.return_value.read.assert_called_once_with(9)
        self.assertIn("watched.csv: larger than", message)

    def test_folder_file_over_the_limit_is_refused(self) -> None:
        folder = self._folder({"watched.csv": account_csv([_TWO, _ONE])})
        with mock.patch.object(export_account, "MEMBER_LIMIT", 8):
            self.assertIn("watched.csv: larger than", self._refusal(folder))

    def test_file_at_the_limit_is_read(self) -> None:
        text = account_csv([_TWO])
        folder = self._folder({"watched.csv": text})
        with mock.patch.object(export_account, "MEMBER_LIMIT", len(text.encode())):
            keys = export_account.read_account(folder)
        self.assertEqual(keys, {FilmKey("metropolis", 1927)})

    def test_unreadable_zip_is_refused(self) -> None:
        path = self.root / "letterboxd-export.zip"
        path.write_bytes(b"not a zip archive")
        self.assertIn("--account", self._refusal(path))

    def _patched_zip(self, *patches: tuple[bytes, int, bytes]) -> Path:
        """A one-member ``watched.csv`` zip, each ``(signature, offset, bytes)`` written
        over the bytes ``offset`` past the first ``signature`` in it."""
        path = self._zip({"watched.csv": account_csv([_TWO])})
        data = bytearray(path.read_bytes())
        for signature, offset, replacement in patches:
            start = data.index(signature) + offset
            data[start : start + len(replacement)] = replacement
        path.write_bytes(bytes(data))
        return path

    def test_zip_with_a_corrupt_central_directory_offset_is_refused(self) -> None:
        # The offset's high byte, 19 bytes into the end record: zipfile seeks before
        # the start of the file. On a file that raises OSError (EINVAL); on an
        # in-memory stream it is ValueError, which test_damaged_member_is_refused holds.
        path = self._patched_zip((_END_RECORD, 19, b"\xff"))
        self.assertIn("--account", self._refusal(path))

    def test_zip_member_name_flagged_utf8_but_not_is_refused(self) -> None:
        # General-purpose flag bit 11 (UTF-8 names) at offset 8 of the central record,
        # and an invalid UTF-8 byte as the name's first, at offset 46: ZipFile() itself
        # raises UnicodeDecodeError.
        path = self._patched_zip(
            (_CENTRAL_RECORD, 8, (0x800).to_bytes(2, "little")),
            (_CENTRAL_RECORD, 46, b"\xff"),
        )
        self.assertIn("--account", self._refusal(path))

    def test_member_neither_stored_nor_deflated_is_refused_naming_it(self) -> None:
        # The method field, offset 8 of the local header and 10 of the central record,
        # set to Zstandard (93), whose decoder raises its own error class when it exists.
        method = (93).to_bytes(2, "little")
        path = self._patched_zip(
            (_LOCAL_HEADER, 8, method), (_CENTRAL_RECORD, 10, method)
        )
        self.assertIn(
            "watched.csv: compressed with an unsupported method", self._refusal(path)
        )

    def test_stored_member_is_read(self) -> None:
        path = self.root / "stored.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("watched.csv", account_csv([_TWO]))
        self.assertEqual(
            export_account.read_account(path), {FilmKey("metropolis", 1927)}
        )

    def test_damaged_member_is_refused(self) -> None:
        path = self._zip(self.files)
        for error in (
            zipfile.BadZipFile("bad CRC"),
            zlib.error("bad data"),
            RuntimeError("encrypted"),
            NotImplementedError("compression"),
            EOFError(),
            ValueError("negative seek value"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(zipfile.ZipFile, "open", side_effect=error),
            ):
                self.assertIn("--account", self._refusal(path))

    def test_missing_account_is_refused(self) -> None:
        self.assertIn("--account", self._refusal(self.root / "absent.zip"))

    def test_unreadable_folder_file_is_refused(self) -> None:
        folder = self._folder({"watched.csv": account_csv([_TWO])})
        with mock.patch.object(Path, "open", side_effect=PermissionError(str(folder))):
            message = self._refusal(folder)
        self.assertIn("watched.csv: cannot be read", message)

    def test_unlistable_folder_is_refused(self) -> None:
        folder = self._folder({"watched.csv": account_csv([_TWO])})
        with mock.patch.object(Path, "is_file", side_effect=OSError(str(folder))):
            self.assertIn("--account", self._refusal(folder))

    def test_refusal_never_names_a_title(self) -> None:
        files: dict[str, str | bytes] = {
            "watched.csv": 'Name,Year\n"Placeholder Secret Title" x,1\n'
        }
        message = self._refusal(self._zip(files))
        self.assertNotIn("Placeholder Secret Title", message)


class OnServiceTest(unittest.TestCase):
    _ACCOUNT = frozenset(
        {FilmKey("metropolis", 1927), FilmKey("placeholder film two", None)}
    )

    def test_exact_title_and_year_match(self) -> None:
        self.assertTrue(
            export_account.on_service(FilmKey("metropolis", 1927), self._ACCOUNT)
        )

    def test_other_year_of_the_same_title_does_not_match(self) -> None:
        self.assertFalse(
            export_account.on_service(FilmKey("metropolis", 2001), self._ACCOUNT)
        )

    def test_key_without_a_year_matches_any_year_of_its_title(self) -> None:
        self.assertTrue(
            export_account.on_service(FilmKey("metropolis", None), self._ACCOUNT)
        )
        self.assertFalse(
            export_account.on_service(
                FilmKey("placeholder film one", None), self._ACCOUNT
            )
        )

    def test_account_entry_without_a_year_matches_any_year_of_its_title(
        self,
    ) -> None:
        self.assertTrue(
            export_account.on_service(
                FilmKey("placeholder film two", 2002), self._ACCOUNT
            )
        )

    def test_other_title_does_not_match(self) -> None:
        self.assertFalse(
            export_account.on_service(
                FilmKey("placeholder film one", 1927), self._ACCOUNT
            )
        )


if __name__ == "__main__":
    unittest.main()
