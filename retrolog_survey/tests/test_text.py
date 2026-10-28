"""Tests for survey_text: which encoding a text file is in, and how its lines end."""

import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import survey_text  # noqa: E402
from build_fixtures import write_text_fixture  # noqa: E402


class DetectEncodingTest(unittest.TestCase):
    def test_utf8_bom(self) -> None:
        self.assertEqual(
            survey_text.detect_encoding(b"\xef\xbb\xbfhi").encoding, "utf-8-sig"
        )

    def test_utf16_bom(self) -> None:
        data = "hi".encode("utf-16")
        self.assertEqual(survey_text.detect_encoding(data).encoding, "utf-16")

    def test_utf16_bom_with_truncated_body_is_not_utf16(self) -> None:
        # Regression: a UTF-16 BOM followed by an odd byte count was reported as
        # utf-16, which then failed to decode. A BOM decides only if the rest decodes.
        data = b"\xff\xfe\x00"
        data.decode(survey_text.detect_encoding(data).encoding)

    def test_utf8_bom_with_invalid_body_is_not_utf8_sig(self) -> None:
        # Regression: as above, for a UTF-8 BOM followed by an invalid UTF-8 byte.
        data = b"\xef\xbb\xbf\xff"
        data.decode(survey_text.detect_encoding(data).encoding)

    def test_ascii(self) -> None:
        self.assertEqual(survey_text.detect_encoding(b"plain").encoding, "ascii")

    def test_utf8(self) -> None:
        self.assertEqual(survey_text.detect_encoding("café".encode()).encoding, "utf-8")

    def test_cp1252_smart_quote(self) -> None:
        guess = survey_text.detect_encoding("it’s “ok”".encode("cp1252"))
        self.assertEqual((guess.encoding, guess.ambiguous), ("cp1252", False))

    def test_mac_roman_smart_quote(self) -> None:
        guess = survey_text.detect_encoding("it’s “ok”".encode("mac_roman"))
        self.assertEqual((guess.encoding, guess.ambiguous), ("mac_roman", False))

    def test_cp1252_undefined_byte_forces_mac_roman(self) -> None:
        self.assertEqual(survey_text.detect_encoding(b"a\x8db").encoding, "mac_roman")

    def test_tie_with_cr_only_endings_prefers_mac_roman(self) -> None:
        # ü and ö: defined in both encodings, neither typographic, so only the CRs decide.
        data = "ü\rö\r".encode("mac_roman")
        self.assertEqual(survey_text.detect_encoding(data).encoding, "mac_roman")

    def test_tie_without_cr_is_ambiguous_cp1252(self) -> None:
        guess = survey_text.detect_encoding(b"\xe9\n")
        self.assertEqual((guess.encoding, guess.ambiguous), ("cp1252", True))

    @given(st.binary(max_size=512))
    def test_fuzz_detect_encoding(self, data: bytes) -> None:
        data.decode(survey_text.detect_encoding(data).encoding)  # never raises


class LineTest(unittest.TestCase):
    def test_count_line_endings(self) -> None:
        self.assertEqual(
            survey_text.count_line_endings(b"a\r\nb\rc\nd"),
            {"crlf": 1, "cr": 1, "lf": 1},
        )

    def test_split_lines_all_three_endings(self) -> None:
        self.assertEqual(survey_text.split_lines("a\r\nb\rc\nd"), ["a", "b", "c", "d"])

    def test_split_lines_drops_one_trailing_empty_line(self) -> None:
        self.assertEqual(survey_text.split_lines("a\n\n"), ["a", ""])

    @given(st.text(max_size=256))
    def test_fuzz_split_lines(self, text: str) -> None:
        lines = survey_text.split_lines(text)
        self.assertFalse(any(ch in line for line in lines for ch in "\r\n"))


class ReadTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_mac_roman_cr_file(self) -> None:
        # The ü matters: an all-ASCII file is reported as "ascii" whatever wrote it. ü
        # is defined in both candidate encodings and is not typographic, so the scores
        # tie and the file's CR-only line endings are what pick mac_roman.
        path = self.root / "list.txt"
        write_text_fixture(
            path, "Film one ü\rFilm two", encoding="mac_roman", newline="\r"
        )
        info = survey_text.read_text(path, max_bytes=1024)
        self.assertEqual(info.guess.encoding, "mac_roman")
        self.assertEqual(info.line_endings, {"crlf": 0, "cr": 1, "lf": 0})
        self.assertEqual(info.lines, ("Film one ü", "Film two"))

    def test_utf16_crlf_counts_as_crlf(self) -> None:
        # Regression: counting on raw bytes saw UTF-16's 0D 00 0A 00 as a lone CR and a
        # lone LF. Counts must come from the decoded text.
        path = self.root / "wide.txt"
        write_text_fixture(
            path, "Film one\nFilm two", encoding="utf-16", newline="\r\n"
        )
        info = survey_text.read_text(path, max_bytes=1024)
        self.assertEqual(info.guess.encoding, "utf-16")
        self.assertEqual(info.line_endings, {"crlf": 1, "cr": 0, "lf": 0})

    def test_too_large_is_refused(self) -> None:
        path = self.root / "big.txt"
        path.write_bytes(b"x" * 11)
        with self.assertRaises(survey_text.FileTooLarge):
            survey_text.read_text(path, max_bytes=10)


if __name__ == "__main__":
    unittest.main()
