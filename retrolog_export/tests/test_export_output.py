"""Tests for export_services and export_output: the service table, the import file
names and the review's JSON and Markdown text."""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# Inserted last first, so sys.path reads in retrolog_export.SOURCE_DIRS' order with the
# tool's own src/ ahead of resolve's, parse's, and the survey's.
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_account  # noqa: E402 - after the sys.path inserts above
import export_goodreads  # noqa: E402 - after the sys.path inserts above
import export_letterboxd  # noqa: E402 - after the sys.path inserts above
import export_output  # noqa: E402 - after the sys.path inserts above
import export_pack  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
import export_services  # noqa: E402 - after the sys.path inserts above
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    account_csv,
    write_account_folder,
)
from export_types import (  # noqa: E402 - after the sys.path inserts above
    FilmKey,
    LetterboxdConfig,
    ReviewEntry,
)
from select_case import make_row  # noqa: E402 - after the sys.path inserts above

# The Letterboxd entry by its own name, typed to its own config and account.
_SPEC = export_services.LETTERBOXD
# A [letterboxd] table rating select_case's rows, whose key is "2".
_CONFIG = LetterboxdConfig(
    half_stars=MappingProxyType({"2": 5}),
    rating_tags=MappingProxyType({}),
    tags=("retrolog",),
    watched_date=True,
    review_note="",
    review_note_rows="commented",
    cutoff=None,
)
_COUNTS = {"films-sent": 3, "rows-sent": 4, "rows-cut": 2, "files": 2}


def entry(
    kind: str, title: str = "Placeholder Film One", rows: tuple[str, ...] = ("a:1",)
) -> ReviewEntry:
    """A review entry of ``kind``, its error flag the kind's own."""
    return ReviewEntry(
        kind=kind,
        title=title,
        rows=rows,
        detail="a detail",
        error=kind in export_select.ERROR_KINDS,
    )


def counts(entries: list[ReviewEntry]) -> dict[str, int]:
    """``review_counts`` for ``entries`` and the module's sample totals."""
    return export_output.review_counts(
        entries,
        medium="film",
        items_sent=_COUNTS["films-sent"],
        rows_sent=_COUNTS["rows-sent"],
        rows_cut=_COUNTS["rows-cut"],
        files=_COUNTS["files"],
    )


class ServicesTest(unittest.TestCase):
    def test_the_services_are_letterboxd_and_goodreads(self) -> None:
        self.assertEqual(list(export_services.SERVICES), ["letterboxd", "goodreads"])

    def test_the_letterboxd_spec_names_its_parts(self) -> None:
        self.assertEqual(_SPEC.name, "letterboxd")
        self.assertEqual(_SPEC.medium, "film")
        self.assertEqual(_SPEC.file_stem, "letterboxd")
        self.assertEqual(_SPEC.columns, export_letterboxd.COLUMNS)
        rules = export_services.LETTERBOXD_RULES
        self.assertIs(rules.on_service, export_account.on_service)
        self.assertEqual(_SPEC.account_fate, rules.account_fate)
        self.assertEqual(_SPEC.grow_holds, rules.grow_holds)

    def test_the_spec_reads_an_account_export(self) -> None:
        with tempfile.TemporaryDirectory() as holder:
            folder = write_account_folder(
                Path(holder), {"watched.csv": account_csv([("Metropolis", "1927")])}
            )
            account = _SPEC.read_account(folder)
        self.assertEqual(account.watched, frozenset({FilmKey("metropolis", 1927)}))

    def test_letterboxd_item_cells_are_each_rows_row_cells(self) -> None:
        # Every row of a film, each with the film's withheld rating.
        rows = (make_row(source_line=1), make_row(source_line=2, rewatch=True))
        film = export_select.Item("tmdb:1", rows, withhold_rating=True)
        expected = tuple(
            export_letterboxd.row_cells(row, _CONFIG, True) for row in rows
        )
        self.assertEqual(_SPEC.item_cells(film, _CONFIG), expected)

    def test_the_goodreads_spec_names_its_parts(self) -> None:
        spec = export_services.SERVICES["goodreads"]
        self.assertEqual(
            (spec.name, spec.medium, spec.file_stem), ("goodreads", "book", "goodreads")
        )
        self.assertEqual(spec.columns, export_goodreads.COLUMNS)
        self.assertIs(spec.account_fate, export_goodreads.book_fate)
        self.assertIs(spec.item_cells, export_goodreads.item_cells)
        self.assertFalse(spec.test_run)
        self.assertEqual((spec.default_rows, spec.default_bytes), (10_000, 10_000_000))

    def test_apply_runs_a_function_on_the_spec(self) -> None:
        for name, spec in export_services.SERVICES.items():
            with self.subTest(service=name):
                self.assertIs(spec.apply(lambda chosen: chosen), spec)

    def test_the_table_is_read_only(self) -> None:
        with self.assertRaises(TypeError):
            export_services.SERVICES["other"] = _SPEC  # type: ignore[index] # the test


class ImportFilesTest(unittest.TestCase):
    def test_files_are_numbered_from_one_in_order(self) -> None:
        self.assertEqual(
            export_output.import_files(_SPEC, ["a\n", "b\n", "c\n"]),
            {
                "letterboxd-001.csv": "a\n",
                "letterboxd-002.csv": "b\n",
                "letterboxd-003.csv": "c\n",
            },
        )

    def test_no_texts_is_no_files(self) -> None:
        self.assertEqual(export_output.import_files(_SPEC, []), {})

    def test_every_packed_file_starts_with_the_header_row(self) -> None:
        film = [tuple(f"cell {n}" for n in range(len(_SPEC.columns)))]
        texts = export_pack.pack(
            _SPEC.columns, [film] * 5, max_rows=2, max_bytes=1_000_000, noun="film"
        )
        files = export_output.import_files(_SPEC, texts)
        self.assertEqual(len(files), 3)
        for name, text in files.items():
            with self.subTest(name=name):
                header = next(csv.reader(io.StringIO(text)))
                self.assertEqual(tuple(header), export_letterboxd.COLUMNS)


class ReviewCountsTest(unittest.TestCase):
    def test_every_kind_is_counted_zero_included_with_the_totals(self) -> None:
        found = counts([entry("unrated"), entry("unrated"), entry("held-failed")])
        expected = dict.fromkeys(export_select.review_kinds("film"), 0)
        expected.update({"unrated": 2, "held-failed": 1, **_COUNTS})
        self.assertEqual(found, expected)

    def test_a_book_run_counts_the_book_kinds_and_no_film_only_kind(self) -> None:
        found = export_output.review_counts(
            [entry("reread-merged")],
            medium="book",
            items_sent=1,
            rows_sent=1,
            rows_cut=0,
            files=1,
        )
        book_kinds = export_select.review_kinds("book")
        self.assertEqual(list(found)[: len(book_kinds)], list(book_kinds))
        for kind in export_select.FILM_KINDS:
            self.assertNotIn(kind, found)
        self.assertEqual(found["reread-merged"], 1)
        self.assertEqual(found["title-only"], 0)
        self.assertEqual(found["books-sent"], 1)


class ReviewJsonTest(unittest.TestCase):
    def test_the_document_shape(self) -> None:
        entries = [entry("undated")]
        document = json.loads(
            export_output.review_json(entries, counts=counts(entries))
        )
        self.assertEqual(sorted(document), ["counts", "entries", "schema_version"])
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["counts"]["undated"], 1)
        self.assertEqual(document["counts"]["files"], 2)
        self.assertEqual(
            document["entries"],
            [
                {
                    "detail": "a detail",
                    "error": False,
                    "kind": "undated",
                    "rows": ["a:1"],
                    "title": "Placeholder Film One",
                }
            ],
        )

    def test_entries_go_by_title_then_kind_then_rows(self) -> None:
        entries = [
            entry("unrated", "B"),
            entry("by-title", "B"),
            entry("undated", "A", ("b:2",)),
            entry("undated", "A", ("a:1",)),
        ]
        document = json.loads(
            export_output.review_json(entries, counts=counts(entries))
        )
        self.assertEqual(
            [(e["title"], e["kind"], e["rows"]) for e in document["entries"]],
            [
                ("A", "undated", ["a:1"]),
                ("A", "undated", ["b:2"]),
                ("B", "by-title", ["a:1"]),
                ("B", "unrated", ["a:1"]),
            ],
        )

    def test_keys_are_sorted_and_text_is_not_escaped_to_ascii(self) -> None:
        entries = [entry("undated", "Fille Été")]
        text = export_output.review_json(entries, counts=counts(entries))
        self.assertIn("Fille Été", text)
        self.assertTrue(text.endswith("}\n"))
        self.assertLess(text.index('"counts"'), text.index('"entries"'))
        self.assertLess(text.index('"entries"'), text.index('"schema_version"'))


class ReviewMarkdownTest(unittest.TestCase):
    def test_the_count_line_and_one_table_per_kind_in_order(self) -> None:
        entries = [entry("undated"), entry("held-ambiguous", "Placeholder Film Two")]
        text = export_output.review_markdown(
            entries, counts=counts(entries), medium="film"
        )
        lines = text.splitlines()
        self.assertEqual(lines[0], "# retrolog_export review")
        self.assertEqual(
            lines[2],
            "4 rows of 3 films sent in 2 files; 2 rows cut. "
            "2 review items, 1 of them an error.",
        )
        self.assertLess(
            text.index("## held-ambiguous (1, error)"), text.index("## undated (1)")
        )
        self.assertIn("| Title | Rows | Detail |", text)
        self.assertIn("| Placeholder Film One | a:1 | a detail |", text)
        self.assertTrue(text.endswith("|\n"))

    def test_a_count_of_one_reads_singular(self) -> None:
        # Regression: the line once read "in 1 files".
        entries = [entry("held-ambiguous")]
        ones = {"films-sent": 1, "rows-sent": 1, "rows-cut": 1, "files": 1}
        text = export_output.review_markdown(entries, counts=ones, medium="film")
        self.assertEqual(
            text.splitlines()[2],
            "1 row of 1 film sent in 1 file; 1 row cut. "
            "1 review item, 1 of them an error.",
        )

    def test_a_count_of_zero_reads_plural(self) -> None:
        zeros = {"films-sent": 0, "rows-sent": 0, "rows-cut": 0, "files": 0}
        text = export_output.review_markdown([], counts=zeros, medium="film")
        self.assertEqual(
            text.splitlines()[2],
            "0 rows of 0 films sent in 0 files; 0 rows cut. "
            "0 review items, 0 of them errors.",
        )

    def test_a_kind_with_no_entry_has_no_table(self) -> None:
        text = export_output.review_markdown([], counts=counts([]), medium="film")
        self.assertNotIn("##", text)
        self.assertIn("0 review items, 0 of them errors.", text)

    def test_a_title_cannot_end_a_cell_or_start_markup(self) -> None:
        entries = [entry("undated", "A | B <b>")]
        text = export_output.review_markdown(
            entries, counts=counts(entries), medium="film"
        )
        self.assertIn(r"| A \| B \<b\> |", text)

    def test_rows_are_joined_with_commas(self) -> None:
        entries = [entry("undated", rows=("a:1", "b:2"))]
        text = export_output.review_markdown(
            entries, counts=counts(entries), medium="film"
        )
        self.assertIn("| a:1, b:2 |", text)


if __name__ == "__main__":
    unittest.main()
