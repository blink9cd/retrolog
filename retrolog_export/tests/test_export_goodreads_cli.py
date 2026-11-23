"""Tests for ``retrolog_export --service goodreads`` end to end, through ``main``.

The run reads resolve's book CSVs and a Goodreads library export, and writes
``goodreads-001.csv`` and the review: every book once, a reread merged into its
newest read, books the account has read or rated left out (``on-service``), a book
only shelved there sent. Letterboxd's one-film test run is refused for books, as is a
config with no ``[goodreads]`` table, and the file caps are the service's own. The
sample's Goodreads run is compared with ``samples/expected-goodreads`` byte for byte.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL / "samples"))
sys.path.insert(0, str(_TOOL))
import build_export_inputs  # noqa: E402 - after the sys.path inserts above
import retrolog_export  # noqa: E402 - after the sys.path inserts above
from export_cli_case import (  # noqa: E402 - after the sys.path inserts above
    EXIT_ERRORS,
    EXIT_OK,
    EXIT_USAGE,
    MARKER,
    fake_git,
)
from export_fixtures import config_toml  # noqa: E402 - after the sys.path inserts
from export_select import FILM_KINDS  # noqa: E402 - after the sys.path inserts
from export_write import other_imports  # noqa: E402 - after the sys.path inserts
from goodreads_cli_case import (  # noqa: E402 - after the sys.path inserts above
    LIBRARY,
    SENT,
    GoodreadsCliCase,
    book_row,
    read_book,
)
from goodreads_account_fixtures import wrapped  # noqa: E402 - after the inserts
from goodreads_fixtures import goodreads_toml  # noqa: E402 - after the inserts

_FIRST_FILE = "goodreads-001.csv"
_EXPECTED = _TOOL / "samples" / "expected-goodreads"


def _review(case: GoodreadsCliCase) -> list[dict[str, object]]:
    """The run's review.json entries."""
    text = (case.out / "review.json").read_text(encoding="utf-8")
    entries: list[dict[str, object]] = json.loads(text)["entries"]
    return entries


def _titles(case: GoodreadsCliCase, kind: str) -> set[object]:
    """The titles of the review entries of ``kind``."""
    return {entry["title"] for entry in _review(case) if entry["kind"] == kind}


class GoodreadsRunTest(GoodreadsCliCase):
    """One run on the case's books: what is sent, left out, and reviewed."""

    def test_sends_every_free_book_once_in_one_file(self) -> None:
        """Each sent book is one row of the one default file, the run exits 0."""
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(self.import_names(), [_FIRST_FILE])
        titles = [row["Title"] for row in self.import_rows()]
        self.assertCountEqual(titles, SENT)

    def test_read_and_rated_books_are_on_service_and_absent(self) -> None:
        """The read book and the rated custom-shelf book are left out and listed."""
        self.main()
        on_service = {"Placeholder Book Seven", "Placeholder Book Eight"}
        self.assertEqual(_titles(self, "on-service"), on_service)
        titles = {row["Title"] for row in self.import_rows()}
        self.assertFalse(titles & on_service)

    def test_unrated_shelved_book_is_sent(self) -> None:
        """A book only shelved on the account, unrated, is sent with its stars."""
        self.main()
        (row,) = [r for r in self.import_rows() if r["Title"].endswith("Nine")]
        self.assertEqual((row["My Rating"], row["Exclusive Shelf"]), ("1", "read"))

    def test_shelved_book_is_listed_remove_first_with_its_tag(self) -> None:
        """Nine, sent though shelved, is to be removed first and carries the tag."""
        self.main()
        self.assertEqual(_titles(self, "remove-first"), {"Placeholder Book Nine"})
        (row,) = [r for r in self.import_rows() if r["Title"].endswith("Nine")]
        self.assertEqual(row["Bookshelves"], "retrolog, custom-shelf-read")
        others = [r for r in self.import_rows() if not r["Title"].endswith("Nine")]
        self.assertEqual({r["Bookshelves"] for r in others}, {"retrolog"})

    def test_reread_is_one_row_of_its_newest_read(self) -> None:
        """A book read twice goes once: the newest read, the earlier year noted."""
        self.main()
        (row,) = [r for r in self.import_rows() if r["Title"].endswith("Five")]
        self.assertEqual(row["Date Read"], "2019/07/08")
        self.assertEqual(row["My Rating"], "2.5")
        self.assertIn("Earlier reads: 2010.", row["Private Notes"])
        self.assertEqual(_titles(self, "reread-merged"), {"Placeholder Book Five"})

    def test_book_review_kinds(self) -> None:
        """by-title, title-only, and unrated name their books; no film kind appears."""
        self.main()
        self.assertEqual(_titles(self, "by-title"), {"Placeholder Book Three"})
        self.assertEqual(_titles(self, "title-only"), {"Placeholder Book Four"})
        self.assertEqual(_titles(self, "unrated"), {"Placeholder Book Six"})
        kinds = {entry["kind"] for entry in _review(self)}
        self.assertFalse(kinds & FILM_KINDS)

    def test_rows_hold_the_book_columns(self) -> None:
        """The file's header is Goodreads' columns; an override's author is Author."""
        self.main()
        text = (self.out / _FIRST_FILE).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("Title,Author,Additional Authors,ISBN,"))
        (row,) = [r for r in self.import_rows() if r["Title"].endswith("Three")]
        self.assertEqual((row["Author"], row["ISBN13"]), ("Invented Writer", ""))

    def test_summary_counts_books(self) -> None:
        """The summary names books, and the book kinds, never a film-only kind."""
        self.main()
        self.assertIn(f"books sent: {len(SENT)}", self.stdout)
        self.assertIn("review on-service: 2", self.stdout)
        self.assertIn("review reread-merged: 1", self.stdout)
        self.assertNotIn("logged-no-stars", self.stdout)
        self.assertNotIn("films", self.stdout)

    def test_no_absolute_path_in_any_output(self) -> None:
        """No file written, nor stdout or stderr, names the temporary folder."""
        self.main()
        self.assert_no_absolute_path()

    def test_formula_cell_is_reviewed(self) -> None:
        """A title starting a formula is written as it is and listed formula-cell."""
        self.write_books({"book-2010.csv": [book_row("=Placeholder Sum", 11)]})
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(_titles(self, "formula-cell"), {"=Placeholder Sum"})

    def test_formula_in_additional_authors_is_reviewed(self) -> None:
        """A second author starting a formula is listed formula-cell.

        Regression: Additional Authors comes from Open Library's author names, which
        anyone can edit, yet it was left out of the checked columns.
        """
        creator = 'Invented Author & =HYPERLINK("https://example.invalid","x")'
        title = "Placeholder Book Linked"
        self.write_books({"book-2010.csv": [book_row(title, 11, creator=creator)]})
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        (row,) = [r for r in self.import_rows() if r["Title"] == title]
        self.assertTrue(row["Additional Authors"].startswith("=HYPERLINK("))
        self.assertEqual(_titles(self, "formula-cell"), {title})

    def test_undated_book_gets_the_configured_date_added(self) -> None:
        """An undated book's Date Read is blank and its Date Added the config's."""
        self.write_books(
            {"book-undated.csv": [book_row("Placeholder Book Ten", 10, date="")]}
        )
        self.main()
        (row,) = [r for r in self.import_rows() if r["Title"].endswith("Ten")]
        self.assertEqual((row["Date Read"], row["Date Added"]), ("", "1900/01/01"))


class OneWorkTwoYearsTest(GoodreadsCliCase):
    """Regression: a work read in two years once reached export with two ISBN-13s.

    Resolve picked each row's ISBN-13 by its own read year, so a reread straddling an
    edition's publication went to Goodreads as two rows, not one. Resolve now
    gives every row of one work the one ISBN-13 picked by the newest read year; the
    rows below are resolve's output for such a work, an override row and a search
    row, and export must send them as one book.
    """

    def test_one_work_read_in_two_years_is_one_row_and_a_merged_reread(self) -> None:
        title, work = "Placeholder Book Twelve", "/works/OL12W"
        first = book_row(
            title, 12, resolve_status="override", ol_work=work, date="2010-02-03"
        )
        again = book_row(
            title,
            12,
            ol_work=work,
            date="2019-04-05",
            rewatch="true",
            source_file="19/read.txt",
        )
        self.write_books({"book-2010.csv": [first], "book-2019.csv": [again]})
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        (row,) = self.import_rows()
        self.assertEqual(row["Date Read"], "2019/04/05")
        self.assertEqual(_titles(self, "reread-merged"), {title})


class GoodreadsSecondRunTest(GoodreadsCliCase):
    """A rerun after the import: every sent book is now read on the account."""

    def test_second_run_writes_no_import_file(self) -> None:
        """With every sent book read on the account, nothing is sent; exit 0."""
        self.main()
        # As the import would list them: its own author for the title-only book.
        imported = [
            read_book(
                row["Title"],
                None,
                Author=row["Author"] or "Someone",
                ISBN13=wrapped(row["ISBN13"]),
            )
            for row in self.import_rows()
        ]
        self.write_library([*LIBRARY, *imported])
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(self.import_names(), [])
        self.assertIn("books sent: 0", self.stdout)


class GoodreadsRefusalTest(GoodreadsCliCase):
    """What stops a Goodreads run with exit 2 before anything is written."""

    def assert_refused(self, *extra: str, needle: str) -> None:
        """The run exits 2, names ``needle``, and writes nothing, not even --out."""
        self.assertEqual(self.main(*extra), EXIT_USAGE)
        self.assertIn(needle, self.stderr)
        self.assertFalse(self.out.exists())

    def test_test_film_is_refused(self) -> None:
        """--test-film is Letterboxd's one-film test run: refused for books."""
        # The refusal's own words: a later failure also names --test-film.
        self.assert_refused("--test-film", needle="goodreads has no test run")

    def test_config_without_goodreads_table(self) -> None:
        """A config holding only [letterboxd] has no table for the service."""
        self.config.write_bytes(config_toml())
        self.assert_refused(needle="[goodreads]")

    def test_undated_row_without_date_added(self) -> None:
        """An undated book with no undated_date_added stops the run unwritten."""
        self.config.write_bytes(goodreads_toml(ratings={"a": 1.0, "b": 2.5}))
        self.write_books(
            {"book-undated.csv": [book_row("Placeholder Book Ten", 10, date="")]}
        )
        self.assert_refused(needle="goodreads.undated_date_added")

    def test_a_folder_account_is_refused(self) -> None:
        """The account is the one library CSV: its folder is refused."""
        self.assertEqual(self.main(account=self.account_dir), EXIT_USAGE)
        self.assertIn("--account", self.stderr)

    def test_rows_above_the_service_maximum(self) -> None:
        """--rows-per-file above Goodreads' default, its maximum, is a usage error.

        No service takes more, so argparse refuses it while parsing, exit 2; its
        message goes to the standard error ``main`` captures.
        """
        with self.assertRaises(SystemExit) as raised:
            self.main("--rows-per-file", "10001")
        self.assertEqual(raised.exception.code, EXIT_USAGE)
        self.assertFalse(self.out.exists())


class GoodreadsCapsTest(GoodreadsCliCase):
    """The file caps are each service's: Goodreads' default is one file."""

    def test_rows_per_file_splits_the_books(self) -> None:
        """A lower --rows-per-file splits the books across numbered files."""
        self.assertEqual(self.main("--rows-per-file", "3"), EXIT_OK, self.stderr)
        self.assertEqual(
            self.import_names(),
            ["goodreads-001.csv", "goodreads-002.csv", "goodreads-003.csv"],
        )

    def test_goodreads_takes_rows_above_letterboxd_maximum(self) -> None:
        """5,000 rows a file is Goodreads' to take, though Letterboxd's maximum is less."""
        self.assertEqual(self.main("--rows-per-file", "5000"), EXIT_OK, self.stderr)
        self.assertEqual(self.import_names(), [_FIRST_FILE])

    def test_letterboxd_refuses_rows_above_its_maximum(self) -> None:
        """The same 5,000 is above Letterboxd's 1,900 and refused, exit 2."""
        argv = ["--service", "letterboxd", "--resolved", str(self.resolved)]
        argv += ["--account", str(self.account_dir), "--config", str(self.config)]
        argv += ["--out", str(self.out), "--rows-per-file", "5000"]
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = retrolog_export.main(argv, run_git=fake_git())
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--rows-per-file", stderr.getvalue())
        self.assertFalse(self.out.exists())

    def test_help_names_each_service_maximum(self) -> None:
        """--help gives each service's file caps, read from the service table."""
        help_text = retrolog_export.build_parser().format_help()
        self.assertIn("goodreads 10000", help_text)
        self.assertIn("letterboxd 1900", help_text)


class SharedOutTest(GoodreadsCliCase):
    """An --out holding another service's import files is refused, and kept whole."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        # As if a Letterboxd run had shared this --out: its file beside the review.
        self.film_file = self.out / "letterboxd-001.csv"
        self.film_file.write_bytes(b"a film\n")
        self.review = (self.out / "review.md").read_bytes()

    def assert_kept(self, *extra: str) -> None:
        """The run exits 2 naming the other service, and changes nothing in --out."""
        self.assertEqual(self.main(*extra), EXIT_USAGE)
        self.assertIn("letterboxd import files", self.stderr)
        self.assertEqual(self.film_file.read_bytes(), b"a film\n")
        self.assertEqual((self.out / "review.md").read_bytes(), self.review)
        self.assertEqual(self.import_names(), [_FIRST_FILE])

    def test_files_of_no_other_service_are_none(self) -> None:
        """Only another stem's import files count; an absent --out has none."""
        stems = ("letterboxd", "goodreads")
        self.assertEqual(other_imports(self.out, "goodreads", stems), ("letterboxd",))
        self.assertEqual(other_imports(self.out, "letterboxd", stems), ("goodreads",))
        self.film_file.unlink()
        self.assertEqual(other_imports(self.out, "goodreads", stems), ())
        self.assertEqual(other_imports(self.base / "none", "goodreads", stems), ())

    def test_a_run_is_refused(self) -> None:
        """Regression: the run once deleted the Letterboxd review and kept its files."""
        self.assert_kept()

    def test_a_dry_run_is_refused(self) -> None:
        """A dry run is refused too, so it answers as the real run would."""
        self.assert_kept("--dry-run")

    def test_help_names_the_refusal(self) -> None:
        """--out's help states this refusal, as it states the others."""
        help_text = " ".join(retrolog_export.build_parser().format_help().split())
        self.assertIn("holding another service's import files", help_text)


class DefaultCapsTest(unittest.TestCase):
    """With no cap option, a run takes its own service's defaults."""

    def test_each_service_defaults_to_its_own_caps(self) -> None:
        bare = argparse.Namespace(test_film=False)
        for name, spec in retrolog_export.SERVICES.items():
            with self.subTest(service=name):
                self.assertEqual(
                    spec.apply(lambda chosen: retrolog_export._caps(bare, chosen)),
                    (spec.default_rows, spec.default_bytes),
                )

    def test_the_defaults_differ(self) -> None:
        """So the test above tells one service's defaults from the other's."""
        letterboxd, goodreads = retrolog_export.SERVICES.values()
        self.assertNotEqual(letterboxd.default_rows, goodreads.default_rows)
        self.assertNotEqual(letterboxd.default_bytes, goodreads.default_bytes)


class GoodreadsSampleTest(unittest.TestCase):
    """The README's Goodreads run reproduces ``samples/expected-goodreads``."""

    def test_sample_run_matches_expected(self) -> None:
        """Build the inputs, run the tool, and compare every output file."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp).resolve()
            with contextlib.redirect_stdout(io.StringIO()):
                built = build_export_inputs.main(
                    ["--service", "goodreads", "--out", str(work)]
                )
            self.assertEqual(built, EXIT_OK)
            argv = ["--service", "goodreads", "--resolved", str(work / "resolved")]
            argv += [
                "--account",
                str(work / "account" / "goodreads_library_export.csv"),
            ]
            argv += ["--config", str(work / "export.toml"), "--out", str(work / "out")]
            with contextlib.redirect_stdout(io.StringIO()):
                code = retrolog_export.main(argv, run_git=fake_git())
            # One book is held, an error, so the run exits 1 as the README says.
            self.assertEqual(code, EXIT_ERRORS)
            written = sorted(p.name for p in (work / "out").iterdir())
            self.assertEqual(
                written, sorted([MARKER, *(p.name for p in _EXPECTED.iterdir())])
            )
            for expected in _EXPECTED.iterdir():
                with self.subTest(file=expected.name):
                    got = (work / "out" / expected.name).read_bytes()
                    self.assertEqual(got, expected.read_bytes())


if __name__ == "__main__":
    unittest.main()
