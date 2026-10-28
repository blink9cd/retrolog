"""Tests for export_pack: CSV text, and films packed whole into capped files."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_pack  # noqa: E402 - after the sys.path inserts above

_HEADER = ("A", "B")
# csv normalizes a lone carriage return, so the fuzz cells hold none: the property is
# the round trip, and a cell csv itself rewrites would fail it for csv's reasons. The
# alphabet is limited to what UTF-8 encodes, as every cell read from a file is: a lone
# surrogate cannot be written to one.
_CELL = st.text(
    alphabet=st.characters(codec="utf-8", exclude_characters="\r"), max_size=12
)


def _data_rows(text: str) -> list[list[str]]:
    """Every row of one file's CSV text but its header."""
    return list(csv.reader(io.StringIO(text)))[1:]


def _film(rows: int, cell: str = "x") -> list[list[str]]:
    """A film of ``rows`` rows, each ``[cell, cell]``."""
    return [[cell, cell] for _ in range(rows)]


class CsvTextTest(unittest.TestCase):
    def test_the_header_then_each_row(self) -> None:
        self.assertEqual(
            export_pack.csv_text(_HEADER, [["1", "2"], ["3", ""]]), "A,B\n1,2\n3,\n"
        )

    def test_a_cell_needing_quotes_is_quoted(self) -> None:
        text = export_pack.csv_text(_HEADER, [['a, "b"', "c\nd"]])
        self.assertEqual(text, 'A,B\n"a, ""b""","c\nd"\n')
        self.assertEqual(_data_rows(text), [['a, "b"', "c\nd"]])

    def test_no_rows_is_the_header_alone(self) -> None:
        self.assertEqual(export_pack.csv_text(_HEADER, []), "A,B\n")


class PackRowsTest(unittest.TestCase):
    def test_no_films_is_no_files(self) -> None:
        self.assertEqual(
            export_pack.pack(_HEADER, [], max_rows=1_900, max_bytes=1_000_000), ()
        )

    def test_exactly_the_row_cap_fills_one_file(self) -> None:
        films = [_film(1)] * 1_900
        files = export_pack.pack(_HEADER, films, max_rows=1_900, max_bytes=1_000_000)
        self.assertEqual(len(files), 1)
        self.assertEqual(len(_data_rows(files[0])), 1_900)

    def test_one_row_past_the_cap_starts_a_second_file(self) -> None:
        films = [_film(1)] * 1_901
        files = export_pack.pack(_HEADER, films, max_rows=1_900, max_bytes=1_000_000)
        self.assertEqual([len(_data_rows(text)) for text in files], [1_900, 1])

    def test_a_film_that_would_pass_the_row_cap_moves_on_whole(self) -> None:
        files = export_pack.pack(
            _HEADER, [_film(2), _film(2)], max_rows=3, max_bytes=1_000_000
        )
        self.assertEqual([len(_data_rows(text)) for text in files], [2, 2])

    def test_every_file_starts_with_the_header(self) -> None:
        files = export_pack.pack(_HEADER, [_film(1)] * 3, max_rows=1, max_bytes=100)
        self.assertEqual(len(files), 3)
        for text in files:
            self.assertTrue(text.startswith("A,B\n"))


class PackBytesTest(unittest.TestCase):
    # "A,B\n" is 4 bytes and each "x,x\n" row 4, so a file of n rows is 4 + 4n bytes.
    def test_exactly_the_byte_cap_fills_one_file(self) -> None:
        files = export_pack.pack(_HEADER, [_film(3)], max_rows=10, max_bytes=16)
        self.assertEqual(files, ("A,B\nx,x\nx,x\nx,x\n",))

    def test_a_byte_cap_landing_mid_film_moves_the_whole_film_on(self) -> None:
        # The first film ends at 12 bytes; the second's first row would fit (16) but
        # its second would not (20), so the second film opens a new file.
        files = export_pack.pack(
            _HEADER, [_film(2, "a"), _film(2, "b")], max_rows=10, max_bytes=16
        )
        self.assertEqual(files, ("A,B\na,a\na,a\n", "A,B\nb,b\nb,b\n"))

    def test_bytes_are_counted_in_utf8(self) -> None:
        # "é" is one character but two bytes, so each "é,é\n" row is 6 bytes.
        files = export_pack.pack(
            _HEADER, [_film(1, "é"), _film(1, "é")], max_rows=10, max_bytes=15
        )
        self.assertEqual(len(files), 2)

    def test_a_film_too_large_alone_is_refused(self) -> None:
        with self.assertRaises(export_pack.PackError) as caught:
            export_pack.pack(_HEADER, [_film(1), _film(5)], max_rows=10, max_bytes=16)
        self.assertIn("film 2 of 2", str(caught.exception))

    def test_a_film_with_more_rows_than_the_cap_is_refused(self) -> None:
        with self.assertRaises(export_pack.PackError) as caught:
            export_pack.pack(_HEADER, [_film(3)], max_rows=2, max_bytes=1_000)
        self.assertIn("film 1 of 1", str(caught.exception))

    def test_the_refusal_quotes_no_cell(self) -> None:
        with self.assertRaises(export_pack.PackError) as caught:
            export_pack.pack(
                _HEADER, [_film(5, "Metropolis")], max_rows=10, max_bytes=8
            )
        self.assertNotIn("Metropolis", str(caught.exception))


class PackPropertyTest(unittest.TestCase):
    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(
            st.lists(st.lists(_CELL, min_size=2, max_size=2), min_size=1, max_size=5),
            max_size=20,
        ),
        st.integers(1, 8),
        st.integers(40, 400),
    )
    def test_fuzz_pack(
        self, films: list[list[list[str]]], max_rows: int, max_bytes: int
    ) -> None:
        """Every row comes back once, in order, and no file passes either cap.

        A refusal must be warranted: some film alone has more rows than ``max_rows``,
        or needs more than ``max_bytes`` with the header.
        """
        try:
            files = export_pack.pack(
                _HEADER, films, max_rows=max_rows, max_bytes=max_bytes
            )
        except export_pack.PackError:
            self.assertTrue(
                any(
                    len(film) > max_rows
                    or len(export_pack.csv_text(_HEADER, film).encode("utf-8"))
                    > max_bytes
                    for film in films
                ),
                "pack refused films that each fit an empty file",
            )
            return
        read_back = [row for text in files for row in _data_rows(text)]
        self.assertEqual(read_back, [row for film in films for row in film])
        for text in files:
            self.assertLessEqual(len(text.encode("utf-8")), max_bytes)
            self.assertLessEqual(len(_data_rows(text)), max_rows)


if __name__ == "__main__":
    unittest.main()
