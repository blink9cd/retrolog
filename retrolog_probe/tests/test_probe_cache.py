"""Tests for probe_cache: the verdict cache's one layout, read strictly and written whole."""

from __future__ import annotations

import json
import sys
import typing
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
import probe_cache  # noqa: E402 - after the sys.path inserts above


def _reading_a_directory_error() -> str:
    """The error type this host raises reading a directory's bytes.

    ``load_cache`` names the type it caught: IsADirectoryError on POSIX, and
    PermissionError on Windows, which refuses to open a directory at all.
    Asked of the host the same way, so the case pins the type the product
    passes through without hardcoding one host's.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            Path(tmp).read_bytes()
        except OSError as error:
            return type(error).__name__
    raise AssertionError("this host read a directory's bytes")


def _document(verdicts: object, schema: object = 1) -> bytes:
    return json.dumps({"schema": schema, "verdicts": verdicts}).encode("utf-8")


class ParseTest(unittest.TestCase):
    def test_a_well_formed_cache(self) -> None:
        data = _document({"19": "present", "7": "missing"})
        self.assertEqual(
            probe_cache.parse_cache(data), {"19": "present", "7": "missing"}
        )

    def test_an_empty_cache(self) -> None:
        self.assertEqual(probe_cache.parse_cache(_document({})), {})

    def test_every_other_shape_is_refused(self) -> None:
        for data in (
            b"",
            b"\xff",
            b"not json",
            b"[]",
            b'{"verdicts": {}}',
            b'{"schema": 1}',
            _document({}, schema=2),
            _document({}, schema=True),
            _document({}, schema="1"),
            _document([]),
            _document({"19": "unknown"}),
            _document({"19": True}),
            _document({"019": "present"}),
            _document({"title": "present"}),
            json.dumps({"schema": 1, "verdicts": {}, "extra": 1}).encode(),
            b"[" * 100_000,
        ):
            with self.subTest(data=data[:40]):
                with self.assertRaises(probe_cache.CacheError):
                    probe_cache.parse_cache(data)

    @given(st.binary())
    def test_fuzz_parse_cache(self, data: bytes) -> None:
        try:
            verdicts = probe_cache.parse_cache(data)
        except probe_cache.CacheError:
            return
        self.assertTrue(set(verdicts.values()) <= {"present", "missing"})

    @given(
        st.dictionaries(
            st.integers(1, 9_999_999_999).map(str),
            st.sampled_from(typing.get_args(probe_cache.Settled)),
        )
    )
    def test_fuzz_cache_bytes_round_trips(
        self, verdicts: dict[str, probe_cache.Settled]
    ) -> None:
        self.assertEqual(
            probe_cache.parse_cache(probe_cache.cache_bytes(verdicts)), verdicts
        )


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.path = Path(holder.name) / "cache.json"

    def test_a_missing_file_is_an_empty_cache(self) -> None:
        self.assertEqual(probe_cache.load_cache(self.path), {})

    def test_save_then_load(self) -> None:
        probe_cache.save_cache(self.path, {"7": "missing", "19": "present"})
        self.assertEqual(
            probe_cache.load_cache(self.path), {"7": "missing", "19": "present"}
        )
        # Ids and verdicts only, in a stable order.
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")),
            {"schema": 1, "verdicts": {"19": "present", "7": "missing"}},
        )

    def test_save_leaves_no_temporary_file(self) -> None:
        probe_cache.save_cache(self.path, {"7": "missing"})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["cache.json"])

    def test_a_corrupt_file_is_refused(self) -> None:
        self.path.write_bytes(b"{")
        with self.assertRaises(probe_cache.CacheError) as caught:
            probe_cache.load_cache(self.path)
        self.assertEqual(str(caught.exception), "--cache is not a verdict cache")

    def test_an_unreadable_file_is_refused_naming_no_path(self) -> None:
        self.path.mkdir()
        with self.assertRaises(probe_cache.CacheError) as caught:
            probe_cache.load_cache(self.path)
        self.assertEqual(
            str(caught.exception),
            f"could not read --cache: {_reading_a_directory_error()}",
        )


if __name__ == "__main__":
    unittest.main()
