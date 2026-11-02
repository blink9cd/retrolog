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
import export_letterboxd  # noqa: E402 - after the sys.path inserts above
import export_output  # noqa: E402 - after the sys.path inserts above
import export_pack  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
import export_services  # noqa: E402 - after the sys.path inserts above
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    account_csv,
    write_account_folder,
)
from export_types import FilmKey, ReviewEntry  # noqa: E402 - after the inserts

_SPEC = export_services.SERVICES["letterboxd"]
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
        films_sent=_COUNTS["films-sent"],
        rows_sent=_COUNTS["rows-sent"],
        rows_cut=_COUNTS["rows-cut"],
        files=_COUNTS["files"],
    )


class ServicesTest(unittest.TestCase):
    def test_letterboxd_is_the_only_service(self) -> None:
        self.assertEqual(list(export_services.SERVICES), ["letterboxd"])

    def test_the_letterboxd_spec_names_its_parts(self) -> None:
        self.assertEqual(_SPEC.name, "letterboxd")
        self.assertEqual(_SPEC.medium, "film")
        self.assertEqual(_SPEC.file_stem, "letterboxd")
        self.assertEqual(_SPEC.columns, export_letterboxd.COLUMNS)
        self.assertIs(_SPEC.row_cells, export_letterboxd.row_cells)
        self.assertIs(_SPEC.on_service, export_account.on_service)

    def test_the_spec_reads_an_account_export(self) -> None:
        with tempfile.TemporaryDirectory() as holder:
            folder = write_account_folder(
                Path(holder), {"watched.csv": account_csv([("Metropolis", "1927")])}
            )
            account = _SPEC.read_account(folder)
        self.assertEqual(account.watched, frozenset({FilmKey("metropolis", 1927)}))

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
            _SPEC.columns, [film] * 5, max_rows=2, max_bytes=1_000_000
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
        expected = dict.fromkeys(export_select.REVIEW_KINDS, 0)
        expected.update({"unrated": 2, "held-failed": 1, **_COUNTS})
        self.assertEqual(found, expected)


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
        text = export_output.review_markdown(entries, counts=counts(entries))
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
        text = export_output.review_markdown(entries, counts=ones)
        self.assertEqual(
            text.splitlines()[2],
            "1 row of 1 film sent in 1 file; 1 row cut. "
            "1 review item, 1 of them an error.",
        )

    def test_a_count_of_zero_reads_plural(self) -> None:
        zeros = {"films-sent": 0, "rows-sent": 0, "rows-cut": 0, "files": 0}
        text = export_output.review_markdown([], counts=zeros)
        self.assertEqual(
            text.splitlines()[2],
            "0 rows of 0 films sent in 0 files; 0 rows cut. "
            "0 review items, 0 of them errors.",
        )

    def test_a_kind_with_no_entry_has_no_table(self) -> None:
        text = export_output.review_markdown([], counts=counts([]))
        self.assertNotIn("##", text)
        self.assertIn("0 review items, 0 of them errors.", text)

    def test_a_title_cannot_end_a_cell_or_start_markup(self) -> None:
        entries = [entry("undated", "A | B <b>")]
        text = export_output.review_markdown(entries, counts=counts(entries))
        self.assertIn(r"| A \| B \<b\> |", text)

    def test_rows_are_joined_with_commas(self) -> None:
        entries = [entry("undated", rows=("a:1", "b:2"))]
        text = export_output.review_markdown(entries, counts=counts(entries))
        self.assertIn("| a:1, b:2 |", text)


if __name__ == "__main__":
    unittest.main()
