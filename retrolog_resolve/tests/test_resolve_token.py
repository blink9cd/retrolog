"""Tests for resolve_token: the token file's format, its size and its permissions.

Every file is written under a temporary folder with an invented token, so no test can
read the operator's own token file. The permission decision is a pure function, so
both its POSIX and its non-POSIX branch run on any machine; the end-to-end mode checks
chmod a real temporary file and run only where POSIX permission bits exist.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
import resolve_token  # noqa: E402 - after the sys.path insert above

_TOKEN = "tok-FILE-SECRET"
_NAME = "RETROLOG_TMDB_TOKEN"
_POSIX = os.name == "posix"
_REGULAR = stat.S_IFREG
_ME, _OTHER = 1000, 1001


class ParseTest(unittest.TestCase):
    """The text's format: comments, quotes, export, and every malformed line."""

    def parse(self, text: str) -> str | None:
        return resolve_token.parse_token_file(text)

    def assert_refused(self, text: str, *phrases: str) -> str:
        """``TokenFileError`` holding each phrase, and never the value or the name."""
        with self.assertRaises(resolve_token.TokenFileError) as raised:
            self.parse(text)
        message = str(raised.exception)
        for phrase in phrases:
            self.assertIn(phrase, message)
        self.assertNotIn(_TOKEN, message)
        return message

    def test_a_plain_assignment(self) -> None:
        self.assertEqual(self.parse(f"{_NAME}={_TOKEN}\n"), _TOKEN)

    def test_blank_and_comment_lines_are_skipped(self) -> None:
        text = f"\n# a comment\n   # indented = comment\n\n{_NAME}={_TOKEN}\n\n"
        self.assertEqual(self.parse(text), _TOKEN)

    def test_an_export_prefix_is_dropped(self) -> None:
        self.assertEqual(self.parse(f"export {_NAME}={_TOKEN}"), _TOKEN)
        self.assertEqual(self.parse(f"export   {_NAME}={_TOKEN}"), _TOKEN)

    def test_one_pair_of_matching_quotes_is_stripped(self) -> None:
        for quote in ('"', "'"):
            with self.subTest(quote=quote):
                text = f"{_NAME}={quote}{_TOKEN}{quote}"
                self.assertEqual(self.parse(text), _TOKEN)
        self.assertEqual(self.parse(f"{_NAME}=\"'{_TOKEN}'\""), f"'{_TOKEN}'")

    def test_a_later_hash_is_part_of_the_value(self) -> None:
        self.assertEqual(self.parse(f"{_NAME}={_TOKEN} # x"), f"{_TOKEN} # x")

    def test_surrounding_whitespace_and_crlf_are_dropped(self) -> None:
        self.assertEqual(self.parse(f"  {_NAME}={_TOKEN}  \r\n"), _TOKEN)

    def test_an_empty_or_absent_value_is_no_token(self) -> None:
        for text in (
            "",
            "# only a comment\n",
            f"{_NAME}=",
            f"{_NAME}=''",
            f'{_NAME}=""',
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.parse(text))

    def test_a_line_with_no_equals_is_refused_by_number(self) -> None:
        self.assert_refused(f"# c\n{_TOKEN}\n", "line 2", "no '='")

    def test_another_variable_is_refused_without_its_name(self) -> None:
        message = self.assert_refused(f"OTHER_SECRET_NAME={_TOKEN}", "line 1")
        self.assertNotIn("OTHER_SECRET_NAME", message)

    def test_a_name_with_spaces_is_another_name(self) -> None:
        self.assert_refused(f"{_NAME} = {_TOKEN}", "line 1")

    def test_the_name_twice_is_refused(self) -> None:
        self.assert_refused(f"{_NAME}=\n{_NAME}={_TOKEN}", "line 2", "twice")

    def test_a_nul_anywhere_is_refused(self) -> None:
        for text in (f"{_NAME}={_TOKEN}\x00", f"{_NAME}={_TOKEN}\n# \x00"):
            with self.subTest(text=text):
                self.assert_refused(text, "NUL")
        self.assert_refused(f"\n\n# \x00{_TOKEN}", "line 3", "NUL")

    def test_an_unterminated_quote_is_refused(self) -> None:
        for value in (f'"{_TOKEN}', f"{_TOKEN}'", '"', f"'{_TOKEN}\""):
            with self.subTest(value=value):
                self.assert_refused(f"{_NAME}={value}", "line 1", "quote")

    # Plain text, and text behind the one name the parser accepts, so the property
    # reaches the value handling and not only the "another variable" refusal.
    @given(st.one_of(st.text(), st.text().map(lambda value: f"{_NAME}={value}")))
    def test_fuzz_parse_token_file(self, text: str) -> None:
        try:
            token = resolve_token.parse_token_file(text)
        except resolve_token.TokenFileError:
            return
        if token is not None:
            self.assertTrue(token)
            for forbidden in ("\n", "\r", "\x00"):
                self.assertNotIn(forbidden, token)


class ModeRefusalTest(unittest.TestCase):
    """The pure permission decision, on both sides of the POSIX split."""

    def refusal(self, mode: int, uid: int = _ME, posix: bool = True) -> str | None:
        return resolve_token.mode_refusal(_REGULAR | mode, uid, _ME, posix)

    def test_owner_only_is_accepted(self) -> None:
        for mode in (0o600, 0o400, 0o700):
            with self.subTest(mode=oct(mode)):
                self.assertIsNone(self.refusal(mode))

    def test_any_group_or_other_bit_is_refused_naming_the_mode(self) -> None:
        for mode, shown in ((0o640, "640"), (0o604, "604"), (0o666, "666")):
            with self.subTest(mode=shown):
                message = self.refusal(mode)
                assert message is not None
                self.assertIn("mode 600", message)
                self.assertIn(f"its mode is {shown}", message)
                self.assertIn("owner only", message)

    def test_a_foreign_owner_is_refused(self) -> None:
        message = self.refusal(0o600, uid=_OTHER)
        assert message is not None
        self.assertIn("owned by the current user", message)

    def test_a_non_regular_file_is_refused(self) -> None:
        message = resolve_token.mode_refusal(stat.S_IFDIR | 0o700, _ME, _ME, True)
        assert message is not None
        self.assertIn("regular file", message)

    def test_off_posix_the_check_is_skipped(self) -> None:
        self.assertIsNone(self.refusal(0o666, uid=_OTHER, posix=False))
        self.assertIsNone(
            resolve_token.mode_refusal(stat.S_IFDIR | 0o777, _OTHER, _ME, False)
        )


class ReadTest(unittest.TestCase):
    """Reading a real file: missing, oversize, bad UTF-8 and, on POSIX, its mode."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.path = self.base / "tmdb.env"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, data: bytes, mode: int = 0o600) -> None:
        self.path.write_bytes(data)
        self.path.chmod(mode)

    def read(self, required: bool = True) -> str | None:
        return resolve_token.read_token_file(self.path, required=required)

    def assert_refused(self, *phrases: str, required: bool = True) -> None:
        """``TokenFileError`` holding each phrase, never the path or the token."""
        with self.assertRaises(resolve_token.TokenFileError) as raised:
            self.read(required)
        message = str(raised.exception)
        for phrase in phrases:
            self.assertIn(phrase, message)
        self.assertNotIn(str(self.base), message)
        self.assertNotIn(self.path.name, message)
        self.assertNotIn(_TOKEN, message)

    def test_a_good_file_gives_its_token(self) -> None:
        self.write(f"{_NAME}={_TOKEN}\n".encode())
        self.assertEqual(self.read(), _TOKEN)

    def test_a_missing_optional_file_is_no_token(self) -> None:
        self.assertIsNone(self.read(required=False))

    def test_a_missing_required_file_is_unreadable(self) -> None:
        self.assert_refused("could not read the token file", "FileNotFoundError")

    def test_an_open_failure_is_unreadable_even_when_optional(self) -> None:
        self.write(f"{_NAME}={_TOKEN}\n".encode())
        denied = PermissionError(13, "denied", str(self.path))
        with mock.patch.object(resolve_token.os, "open", side_effect=denied):
            self.assert_refused("could not read", "PermissionError", required=False)

    def assert_refused_and_closed(self, seam: str, *phrases: str) -> None:
        """``seam`` failing after the open is refused, and the descriptor is closed."""
        self.write(f"{_NAME}={_TOKEN}\n".encode())
        opened: list[int] = []
        real_open = os.open

        def recording_open(path: Path, flags: int) -> int:
            opened.append(real_open(path, flags))
            return opened[-1]

        failed = OSError(5, "I/O error", str(self.path))
        with (
            mock.patch.object(resolve_token.os, "open", side_effect=recording_open),
            mock.patch.object(resolve_token.os, seam, side_effect=failed),
            mock.patch.object(resolve_token.os, "close", wraps=os.close) as closing,
        ):
            self.assert_refused(*phrases)
        self.assertEqual(len(opened), 1)
        closing.assert_called_once_with(opened[0])

    def test_a_read_failure_is_unreadable_and_closes_the_file(self) -> None:
        self.assert_refused_and_closed("fdopen", "could not read", "OSError")

    def test_a_failure_while_reading_is_unreadable(self) -> None:
        self.write(f"{_NAME}={_TOKEN}\n".encode())
        real_fdopen = os.fdopen

        def failing_reader(descriptor: int, mode: str) -> mock.MagicMock:
            # The real file object, so its descriptor is still closed on exit.
            handle = mock.MagicMock(wraps=real_fdopen(descriptor, mode))
            handle.__enter__.return_value = handle
            handle.__exit__.side_effect = lambda *_: handle._mock_wraps.close()
            handle.read.side_effect = OSError(5, "I/O error", str(self.path))
            return handle

        with mock.patch.object(resolve_token.os, "fdopen", side_effect=failing_reader):
            self.assert_refused("could not read", "OSError")

    def test_an_fstat_failure_is_unreadable_and_closes_the_file(self) -> None:
        self.assert_refused_and_closed("fstat", "could not read", "OSError")

    def test_a_refused_mode_is_refused_and_closes_the_file(self) -> None:
        """The refusal raised on any host: off POSIX ``mode_refusal`` never refuses.

        The pure function's own cases are above; this one drives its refusal through
        the open file, so the raise and the close that follow it run on Windows too.
        """
        self.write(f"{_NAME}={_TOKEN}\n".encode())
        refusal = "the token file must be a regular file"
        with (
            mock.patch.object(resolve_token, "mode_refusal", return_value=refusal),
            mock.patch.object(resolve_token.os, "close", wraps=os.close) as closing,
        ):
            with self.assertRaises(resolve_token.TokenFileError) as raised:
                self.read()
        self.assertEqual(str(raised.exception), refusal)
        closing.assert_called_once()
        (descriptor,) = closing.call_args.args
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_a_folder_is_refused_even_when_optional(self) -> None:
        self.path.mkdir(mode=0o700)
        self.assert_refused("the token file", required=False)

    def test_a_file_at_the_size_limit_is_read(self) -> None:
        line = f"{_NAME}={_TOKEN}\n".encode()
        padding = b"#" * (resolve_token.MAX_TOKEN_FILE_BYTES - len(line) - 1) + b"\n"
        self.write(padding + line)
        self.assertEqual(self.read(), _TOKEN)

    def test_an_oversize_file_is_refused(self) -> None:
        self.write(b"#" * (resolve_token.MAX_TOKEN_FILE_BYTES + 1))
        self.assert_refused("larger than 64 KiB")

    def test_a_file_that_is_not_utf8_is_refused(self) -> None:
        self.write(f"{_NAME}={_TOKEN}\xff".encode("latin-1"))
        self.assert_refused("not UTF-8")

    def test_a_malformed_line_is_refused_by_number(self) -> None:
        self.write(f"{_TOKEN}\n".encode())
        self.assert_refused("line 1")

    @unittest.skipUnless(_POSIX, "POSIX permission bits only")
    def test_a_group_or_other_readable_file_is_refused(self) -> None:
        for mode, shown in ((0o640, "640"), (0o604, "604")):
            with self.subTest(mode=shown):
                self.write(f"{_NAME}={_TOKEN}\n".encode(), mode)
                self.assert_refused("mode 600", f"its mode is {shown}")
                self.assert_refused("mode 600", required=False)

    @unittest.skipUnless(_POSIX, "POSIX permission bits only")
    def test_a_link_to_an_owner_only_file_is_read(self) -> None:
        target = self.base / "real.env"
        target.write_bytes(f"{_NAME}={_TOKEN}\n".encode())
        target.chmod(0o600)
        self.path.symlink_to(target)
        self.assertEqual(self.read(), _TOKEN)


class NameTest(unittest.TestCase):
    def test_the_variable_name_matches_the_cli(self) -> None:
        self.assertEqual(resolve_token.TOKEN_VARIABLE, _NAME)


if __name__ == "__main__":
    unittest.main()
