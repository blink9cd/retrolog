"""Tests for review_append: joining new override tables onto an existing file."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
_TOOLS = _TOOL.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOLS / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOLS / "retrolog_parse" / "src"))
from review_append import (  # noqa: E402 - after the sys.path inserts above
    AppendError,
    append_overrides,
)

_FIRST = (
    '[[overrides]]\nmedium = "film"\ntitle = "Placeholder Film Two"\n'
    'file = "2010/seen.txt"\nline = 4\ntmdb_id = "1000003"\n'
)
_SECOND = (
    '[[overrides]]\nmedium = "book"\ntitle = "Placeholder Book Four"\n'
    'file = "2010/read.txt"\nline = 5\nunresolvable = true\n'
)
_EXISTING = f"schema = 1\n\n{_FIRST}".encode()


class AppendOverridesTest(unittest.TestCase):
    def test_tables_follow_the_existing_bytes_a_blank_line_before_each(self) -> None:
        appended = append_overrides(_EXISTING, [_SECOND])
        self.assertEqual(appended.data, _EXISTING + f"\n{_SECOND}".encode())
        self.assertEqual(appended.held, 2)

    def test_existing_text_is_kept_byte_for_byte(self) -> None:
        existing = (
            b"# my overrides\r\nschema = 1  # kept\r\n\r\n"
            + _FIRST.replace("\n", "\r\n").encode()
        )
        appended = append_overrides(existing, [_SECOND])
        self.assertTrue(appended.data.startswith(existing))
        self.assertEqual(appended.held, 2)

    def test_a_missing_final_newline_is_added_before_the_tables(self) -> None:
        existing = _EXISTING.rstrip(b"\n")
        appended = append_overrides(existing, [_SECOND])
        self.assertEqual(appended.data, existing + f"\n\n{_SECOND}".encode())

    def test_no_tables_leaves_the_bytes_as_they_were(self) -> None:
        appended = append_overrides(_EXISTING, [])
        self.assertEqual((appended.data, appended.held), (_EXISTING, 1))

    def test_a_file_holding_only_its_schema_takes_tables(self) -> None:
        appended = append_overrides(b"schema = 1\n", [_FIRST, _SECOND])
        self.assertEqual(appended.held, 2)

    def test_an_existing_file_resolve_refuses_is_refused(self) -> None:
        with self.assertRaises(AppendError) as raised:
            append_overrides(_FIRST.encode(), [_SECOND])
        self.assertEqual(
            str(raised.exception),
            "the existing file fails resolve's check: schema: missing",
        )

    def test_a_duplicate_of_an_existing_entry_is_refused(self) -> None:
        with self.assertRaises(AppendError) as raised:
            append_overrides(_EXISTING, [_SECOND, _FIRST])
        self.assertEqual(
            str(raised.exception),
            "the appended file would fail resolve's check: overrides[2]: the same "
            "medium, title, file and line as overrides[0]",
        )

    def test_a_duplicate_by_title_key_alone_is_refused(self) -> None:
        recased = _FIRST.replace("Placeholder Film Two", "placeholder film two")
        with self.assertRaises(AppendError):
            append_overrides(_EXISTING, [recased.replace("1000003", "1000004")])

    def test_an_inline_overrides_array_is_named_as_the_reason(self) -> None:
        for inline in (
            b"schema = 1\noverrides = []\n",
            b"schema = 1\noverrides = [ ]",
            b'schema = 1\noverrides = [{medium = "film", title = "A", tmdb_id = "1"}]\n',
        ):
            with self.subTest(inline=inline), self.assertRaises(AppendError) as raised:
                append_overrides(inline, [_SECOND])
            self.assertEqual(
                str(raised.exception),
                "the existing file holds its overrides as an inline array "
                "(overrides = [...]), which [[overrides]] tables cannot be added to",
            )

    def test_an_inline_array_takes_no_tables_without_complaint(self) -> None:
        inline = b"schema = 1\noverrides = []\n"
        self.assertEqual(append_overrides(inline, []).data, inline)

    def test_messages_name_no_title(self) -> None:
        for existing, tables in ((_FIRST.encode(), [_SECOND]), (_EXISTING, [_FIRST])):
            with self.assertRaises(AppendError) as raised:
                append_overrides(existing, tables)
            self.assertNotIn("Placeholder", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
