"""Tests for export_goodreads_account's reader of a Goodreads library export.

The export is one CSV of invented books (``goodreads_account_fixtures``): each test
writes it into a temporary folder and reads it back. A property test holds that any
CSV of the library's columns reads, or is refused with an ``AccountError``, and that
nothing else escapes.
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

from hypothesis import given
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
sys.path.insert(0, str(_TESTS))
import _retrolog_export_fuzz_profile  # noqa: E402, F401 - loads the deterministic profile before any @given
import export_account  # noqa: E402 - after the sys.path inserts above
import export_goodreads_account as gr  # noqa: E402 - after the sys.path inserts above
from goodreads_account_fixtures import (  # noqa: E402 - after the sys.path inserts above
    LIBRARY_HEADER,
    REQUIRED_COLUMNS,
    isbn13,
    library_book,
    library_csv,
    wrapped,
    write_library,
)

# A title the tests check no error message ever names.
_SECRET_TITLE = "Placeholder Book Never Named"


class _LibraryCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)

    def read(self, *books: dict[str, str]) -> gr.GoodreadsAccount:
        """The account a library export of ``books`` reads as."""
        return gr.read_account(write_library(self.folder, library_csv(books)))

    def refusal(self, path: Path) -> str:
        """The AccountError message reading ``path`` gives; it names no path."""
        with self.assertRaises(export_account.AccountError) as raised:
            gr.read_account(path)
        message = str(raised.exception)
        self.assertNotIn(str(self.folder), message)
        self.assertNotIn(_SECRET_TITLE, message)
        self.assertIn("--account", message)
        return message


class IsbnTest(_LibraryCase):
    def test_a_wrapped_isbn_is_unwrapped(self) -> None:
        account = self.read(
            library_book(ISBN13=wrapped(isbn13(5)), **{"My Rating": "4"})
        )
        self.assertEqual(account.read_isbns, frozenset({isbn13(5)}))

    def test_an_empty_wrapped_isbn_is_no_isbn(self) -> None:
        account = self.read(library_book(ISBN13='=""', **{"My Rating": "4"}))
        self.assertEqual(account.read_isbns, frozenset())
        self.assertIn(("placeholder book one", "author"), account.read_titles)

    def test_a_plain_isbn_is_read_as_it_is(self) -> None:
        account = self.read(library_book(ISBN13=isbn13(6), **{"Read Count": "1"}))
        self.assertEqual(account.read_isbns, frozenset({isbn13(6)}))

    def test_a_cell_not_thirteen_digits_is_no_isbn(self) -> None:
        for cell in ("=", '="12345"', "978-0-00", wrapped("x" * 13), '="' + "1" * 14):
            with self.subTest(cell=cell):
                account = self.read(library_book(ISBN13=cell, **{"My Rating": "3"}))
                self.assertEqual(account.read_isbns, frozenset())


class ReadSignalTest(_LibraryCase):
    def test_each_read_signal_alone_makes_a_book_read(self) -> None:
        signals = (
            {"Exclusive Shelf": "read"},
            {"Exclusive Shelf": "Read"},
            {"My Rating": "1"},
            {"Read Count": "2"},
        )
        for signal in signals:
            with self.subTest(signal=signal):
                account = self.read(library_book(**signal))
                self.assertEqual(account.read_isbns, frozenset({isbn13(1)}))
                self.assertEqual(dict(account.shelved_isbns), {})
                self.assertIn(("placeholder book one", "author"), account.read_titles)

    def test_a_custom_shelf_with_no_rating_is_shelved(self) -> None:
        account = self.read(library_book(**{"Exclusive Shelf": "custom-shelf"}))
        self.assertEqual(account.read_isbns, frozenset())
        self.assertEqual(dict(account.shelved_isbns), {isbn13(1): "custom-shelf"})
        self.assertEqual(account.read_titles, frozenset())
        self.assertIn(("placeholder book one", "author"), account.shelved_titles)

    def test_a_zero_or_unreadable_count_is_no_signal(self) -> None:
        for cell in ("0", "", "none", "-1", "1" * 40, "0.0"):
            with self.subTest(cell=cell):
                account = self.read(
                    library_book(**{"My Rating": cell, "Read Count": cell})
                )
                self.assertEqual(account.read_isbns, frozenset())


class KeysTest(_LibraryCase):
    def test_a_series_parenthetical_is_dropped(self) -> None:
        for title in (
            "Placeholder Book One (Invented Series, #3)",
            "Placeholder Book One (Invented Series #3)",
            "Placeholder Book One (Invented Series, #2.5) ",
        ):
            with self.subTest(title=title):
                self.assertEqual(gr.title_keys(title), ("placeholder book one",))

    def test_a_parenthetical_without_a_number_is_kept(self) -> None:
        for title in ("Placeholder (Revised)", "Placeholder (a) b)", "Placeholder #3)"):
            with self.subTest(title=title):
                self.assertEqual(len(gr.title_keys(title)), 1)
                self.assertNotEqual(gr.title_keys(title), ("placeholder",))

    def test_a_subtitle_gives_a_second_key(self) -> None:
        self.assertEqual(
            gr.title_keys("Placeholder Book One: A Novel (Invented Series, #1)"),
            ("placeholder book one a novel", "placeholder book one"),
        )

    def test_a_title_that_is_only_a_series_keeps_its_own_key(self) -> None:
        self.assertEqual(gr.title_keys("(Invented Series, #1)"), ("invented series 1",))

    def test_a_title_with_no_letter_has_no_key(self) -> None:
        self.assertEqual(gr.title_keys(":"), ())
        self.assertEqual(gr.title_keys(""), ())

    def test_an_author_is_keyed_by_the_folded_last_word(self) -> None:
        for name, key in (
            ("Invented Author", "author"),
            ("Ínvented Authör", "author"),
            ("A. B. Placeholder", "placeholder"),
            ("Ann Placeholder", "placeholder"),
            ("Invented Le Guin", "guin"),
            ("Invented O'Name ", "oname"),
            ("", ""),
            ("...", ""),
        ):
            with self.subTest(name=name):
                self.assertEqual(gr.author_key(name), key)

    def test_the_first_author_of_a_creator_is_keyed(self) -> None:
        self.assertEqual(
            gr.first_author_key("Ann Placeholder & Invented Author"), "placeholder"
        )
        self.assertEqual(gr.first_author_key(""), "")

    def test_the_account_keeps_every_title_key(self) -> None:
        account = self.read(
            library_book(
                Title="Placeholder Book One: A Novel (Invented Series, #2)",
                Author="Ínvented Authör",
                **{"My Rating": "5"},
            )
        )
        self.assertEqual(
            account.read_titles,
            frozenset(
                {
                    ("placeholder book one a novel", "author"),
                    ("placeholder book one", "author"),
                }
            ),
        )


class ShapeTest(_LibraryCase):
    def test_columns_in_any_order_with_others_ignored(self) -> None:
        header = ("Extra", *reversed(REQUIRED_COLUMNS))
        text = library_csv([library_book(**{"Read Count": "1"})], header=header)
        account = gr.read_account(write_library(self.folder, text))
        self.assertEqual(account.read_isbns, frozenset({isbn13(1)}))

    def test_a_byte_order_mark_is_accepted(self) -> None:
        text = "\ufeff" + library_csv([library_book(**{"Read Count": "1"})])
        account = gr.read_account(write_library(self.folder, text))
        self.assertEqual(account.read_isbns, frozenset({isbn13(1)}))

    def test_a_short_row_reads_with_the_cells_it_has(self) -> None:
        text = ",".join(REQUIRED_COLUMNS) + "\nPlaceholder Book One,Invented Author\n"
        account = gr.read_account(write_library(self.folder, text))
        self.assertIn(("placeholder book one", "author"), account.shelved_titles)

    def test_a_row_with_no_title_or_isbn_is_skipped(self) -> None:
        account = self.read(
            library_book(Title="", ISBN13='=""'), library_book(**{"Read Count": "1"})
        )
        self.assertEqual(dict(account.shelved_titles), {})
        self.assertEqual(account.read_isbns, frozenset({isbn13(1)}))

    def test_a_cell_past_csv_default_limit_is_read(self) -> None:
        long_review = "x" * (csv.field_size_limit() + 10)
        account = self.read(
            library_book(**{"My Review": long_review, "Read Count": "1"})
        )
        self.assertEqual(account.read_isbns, frozenset({isbn13(1)}))

    def test_the_parse_leaves_the_field_limit_as_it_found_it(self) -> None:
        previous = csv.field_size_limit()
        self.read(library_book())
        self.assertEqual(csv.field_size_limit(), previous)


class RefusalTest(_LibraryCase):
    def test_a_missing_file(self) -> None:
        self.assertIn("no such file", self.refusal(self.folder / "absent.csv"))

    def test_a_folder(self) -> None:
        write_library(self.folder, library_csv([library_book()]))
        self.assertIn("a folder", self.refusal(self.folder))

    def test_a_zip_named_as_the_csv(self) -> None:
        # Named as the export's own CSV, so the bytes refuse it, never the name.
        path = self.folder / "goodreads_library_export.csv"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "goodreads_library_export.csv", library_csv([library_book()])
            )
        self.assertIn("a zip", self.refusal(path))

    def test_each_missing_required_column(self) -> None:
        for missing in REQUIRED_COLUMNS:
            with self.subTest(missing=missing):
                header = [column for column in LIBRARY_HEADER if column != missing]
                text = library_csv([library_book(Title=_SECRET_TITLE)], header=header)
                message = self.refusal(write_library(self.folder, text))
                self.assertIn(f"lacks the {missing} column", message)

    def test_an_empty_file_lacks_every_column(self) -> None:
        message = self.refusal(write_library(self.folder, ""))
        self.assertIn("lacks the Title, Author", message)

    def test_a_file_over_the_shared_member_limit(self) -> None:
        path = write_library(self.folder, library_csv([library_book()]))
        limit = path.stat().st_size - 1
        with mock.patch.object(export_account, "MEMBER_LIMIT", limit):
            self.assertIn(f"larger than {limit} bytes", self.refusal(path))

    def test_a_file_at_the_shared_member_limit_is_read(self) -> None:
        path = write_library(
            self.folder, library_csv([library_book(**{"Read Count": "1"})])
        )
        with mock.patch.object(export_account, "MEMBER_LIMIT", path.stat().st_size):
            self.assertEqual(gr.read_account(path).read_isbns, frozenset({isbn13(1)}))

    def test_not_utf8(self) -> None:
        path = self.folder / "library.csv"
        path.write_bytes(library_csv([library_book()]).encode("utf-8") + b"\xff\n")
        self.assertIn("not UTF-8", self.refusal(path))

    def test_not_valid_csv(self) -> None:
        text = library_csv([]) + f'"{_SECRET_TITLE}"x,a\n'
        self.assertIn("not valid CSV", self.refusal(write_library(self.folder, text)))

    def test_no_rows(self) -> None:
        for text in (library_csv([]), library_csv([library_book(Title="", ISBN13="")])):
            with self.subTest(text=text):
                message = self.refusal(write_library(self.folder, text))
                self.assertIn("lists no books", message)

    def test_an_unreadable_file(self) -> None:
        path = write_library(self.folder, library_csv([library_book()]))
        with mock.patch.object(Path, "open", side_effect=PermissionError(13, "denied")):
            self.assertIn("cannot be read", self.refusal(path))

    def test_an_unreadable_path(self) -> None:
        path = write_library(self.folder, library_csv([library_book()]))
        with mock.patch.object(
            Path, "is_dir", side_effect=PermissionError(13, "denied")
        ):
            self.assertIn("cannot be read", self.refusal(path))


# Cells a library row may hold: free text, and the shapes the reader looks for.
_CELLS = st.one_of(
    st.text(max_size=20),
    st.sampled_from(["read", "to-read", "0", "1", "5", '=""', "=", wrapped(isbn13(3))]),
    st.from_regex(r'="?[0-9]{0,14}"?', fullmatch=True),
)
_HEADERS = st.permutations(LIBRARY_HEADER).flatmap(
    lambda columns: st.integers(0, len(columns)).map(lambda cut: columns[cut:])
)


def _rendered(header: list[str], rows: list[list[str]]) -> str:
    """``header`` and ``rows`` as CSV text."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


class ParseLibraryPropertyTest(unittest.TestCase):
    @given(_HEADERS, st.lists(st.lists(_CELLS, max_size=26), max_size=6))
    def test_fuzz_parse_library_csv(
        self, header: list[str], rows: list[list[str]]
    ) -> None:
        """Any CSV of the library's columns reads, or raises AccountError alone."""
        try:
            account = gr.parse_library_csv(_rendered(header, rows))
        except export_account.AccountError:
            return
        self.assertIsInstance(account, gr.GoodreadsAccount)
        self.assertLessEqual(len(account.read_isbns), len(rows))

    @given(st.text(max_size=200))
    def test_any_text_reads_or_is_refused(self, text: str) -> None:
        """Arbitrary text reads, or raises AccountError alone."""
        try:
            gr.parse_library_csv(text)
        except export_account.AccountError:
            return


if __name__ == "__main__":
    unittest.main()
