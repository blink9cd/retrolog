"""Tests for resolve_output: the CSV files and the review's JSON and Markdown text."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_input  # noqa: E402 - after the sys.path inserts above
import resolve_output  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
from parse_output import COLUMNS  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import (  # noqa: E402 - after the sys.path inserts above
    candidate,
    csv_text,
    parse_row,
    write_parsed,
)
from resolve_types import Candidate, Medium, ReviewEntry  # noqa: E402 - after the sys.path inserts above

_HEADER = ",".join(COLUMNS + resolve_rows.RESOLVE_COLUMNS)


def _row(
    csv_name: str, title: str, status: str = "not-found"
) -> resolve_rows.OutputRow:
    """An output row of ``title`` in ``csv_name``, its id columns empty."""
    cells = {**parse_row(title=title), "resolve_status": status}
    cells.update(dict.fromkeys(resolve_rows.RESOLVE_COLUMNS[1:], ""))
    return resolve_rows.OutputRow(csv_name, MappingProxyType(cells))


def _entry(
    kind: str,
    title: str = "Moby Dick",
    medium: Medium = "book",
    *,
    rows: tuple[str, ...] = ("10/read.txt:1",),
    detail: str = "a detail",
    candidates: tuple[Candidate, ...] = (),
) -> ReviewEntry:
    """A review entry of ``kind``, an error exactly when its kind is one."""
    return ReviewEntry(
        kind, medium, title, rows, detail, candidates, kind in resolve_rows.ERROR_KINDS
    )


class CsvFilesTest(unittest.TestCase):
    def test_one_file_per_source_csv_rows_in_input_order(self) -> None:
        rows = [
            _row("film-2010.csv", "B"),
            _row("book-2010.csv", "A"),
            _row("film-2010.csv", "A"),
        ]
        files = resolve_output.csv_files(rows)
        self.assertEqual(list(files), ["book-2010.csv", "film-2010.csv"])
        lines = files["film-2010.csv"].split("\n")
        self.assertEqual(lines[0], _HEADER)
        self.assertEqual([line.split(",")[1] for line in lines[1:-1]], ["B", "A"])
        self.assertTrue(files["film-2010.csv"].endswith("\n"))
        self.assertNotIn("\r", files["film-2010.csv"])

    def test_parse_cells_are_written_back_byte_for_byte(self) -> None:
        awkward = parse_row(title='A "quoted", title', comment="two\nlines, é")
        text = csv_text([parse_row(), awkward])
        with tempfile.TemporaryDirectory() as folder:
            parsed = write_parsed(Path(folder), {"book-2010.csv": text})
            rows = resolve_input.read_parsed(parsed)
        cells = [
            {
                **row.cells,
                **dict.fromkeys(resolve_rows.RESOLVE_COLUMNS, ""),
                "resolve_status": "not-found",
            }
            for row in rows
        ]
        written = resolve_output.csv_files(
            [resolve_rows.OutputRow("book-2010.csv", c) for c in cells]
        )["book-2010.csv"]
        # Each parse record, with ",not-found" and five empty cells before its end.
        expected = text.replace(",".join(COLUMNS), _HEADER, 1)
        records = expected.split("\n")
        self.assertEqual(
            written,
            "\n".join(
                [
                    records[0],
                    records[1] + ",not-found,,,,,",
                    records[2],
                    records[3] + ",not-found,,,,,",
                    "",
                ]
            ),
        )


class ReviewJsonTest(unittest.TestCase):
    def test_shape_counts_every_kind_and_sorts_entries(self) -> None:
        entries = [
            _entry("not-found", "Zed"),
            _entry("failed", "Metropolis", "film"),
            _entry("ambiguous", "Ahab", candidates=(candidate("Ahab", 1999),)),
            _entry("no-isbn", "Ahab"),
        ]
        document = json.loads(resolve_output.review_json(entries))
        self.assertEqual(
            sorted(document), ["counts", "entries", "medium", "schema_version"]
        )
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            document["counts"],
            {kind: 0 for kind in resolve_rows.REVIEW_KINDS}
            | {"not-found": 1, "failed": 1, "ambiguous": 1, "no-isbn": 1},
        )
        self.assertEqual(
            [(e["medium"], e["title"], e["kind"]) for e in document["entries"]],
            [
                ("book", "Ahab", "ambiguous"),
                ("book", "Ahab", "no-isbn"),
                ("book", "Zed", "not-found"),
                ("film", "Metropolis", "failed"),
            ],
        )
        first = document["entries"][0]
        self.assertEqual(first["rows"], ["10/read.txt:1"])
        self.assertEqual(first["candidates"][0]["title"], "Ahab")
        self.assertEqual(first["candidates"][0]["year"], 1999)
        self.assertIs(document["entries"][3]["error"], True)

    def test_text_is_kept_unescaped_and_ends_in_a_newline(self) -> None:
        text = resolve_output.review_json([_entry("not-found", "Café")])
        self.assertIn('"Café"', text)
        self.assertTrue(text.endswith("}\n"))

    def test_entries_of_one_title_and_kind_are_ordered_by_rows(self) -> None:
        entries = [
            _entry("ambiguous", rows=("10/read.txt:9",)),
            _entry("ambiguous", rows=("10/read.txt:10",)),
        ]
        document = json.loads(resolve_output.review_json(entries))
        self.assertEqual(
            [e["rows"] for e in document["entries"]],
            [["10/read.txt:10"], ["10/read.txt:9"]],
        )


class ReviewMarkdownTest(unittest.TestCase):
    def test_an_empty_review_is_its_count_line(self) -> None:
        self.assertEqual(
            resolve_output.review_markdown([]),
            "# retrolog_resolve review\n\n0 review items, 0 of them errors.\n",
        )

    def test_one_table_per_kind_present_in_kind_order(self) -> None:
        text = resolve_output.review_markdown(
            [
                _entry("not-found"),
                _entry("failed", "Metropolis", "film"),
                _entry("not-found", "Ahab"),
            ]
        )
        lines = text.split("\n")
        self.assertEqual(lines[2], "3 review items, 1 of them errors.")
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(headings, ["## failed (1, error)", "## not-found (2)"])
        not_found = lines[lines.index("## not-found (2)") + 4 :]
        self.assertTrue(not_found[0].startswith("| book | Ahab |"))

    def test_cells_are_escaped_and_candidates_described(self) -> None:
        shown = (
            candidate("Moby | Dick", 1851, ident="/works/OL1W"),
            candidate("Other", None, original_title="Autre", ident="7"),
        )
        text = resolve_output.review_markdown(
            [
                _entry(
                    "ambiguous",
                    "Moby | Dick\nTwo",
                    candidates=shown,
                    detail="2 exact titles",
                )
            ]
        )
        row = text.split("\n")[-2]
        self.assertEqual(
            row,
            "| book | Moby \\| Dick↵Two | 10/read.txt:1 | 2 exact titles | "
            "Moby \\| Dick, 1851, /works/OL1W; Other (Autre), 7 |",
        )

    def test_an_empty_cell_is_one_space(self) -> None:
        text = resolve_output.review_markdown([_entry("override-unused", rows=())])
        self.assertIn("| book | Moby Dick | | a detail | |", text)

    def test_a_single_medium_review_states_its_scope_after_the_count(self) -> None:
        text = resolve_output.review_markdown([], "film")
        self.assertEqual(
            text,
            "# retrolog_resolve review\n\n0 review items, 0 of them errors.\n\n"
            "This run resolved film rows only; any book CSV files in this folder "
            "are from an earlier run, and this review does not cover them.\n",
        )

    def test_an_all_media_review_has_no_scope_line(self) -> None:
        self.assertEqual(
            resolve_output.review_markdown([], "all"),
            resolve_output.review_markdown([]),
        )


class ReviewScopeJsonTest(unittest.TestCase):
    def test_the_json_names_the_medium_the_run_covered(self) -> None:
        for medium in ("book", "film", "all"):
            with self.subTest(medium=medium):
                document = json.loads(resolve_output.review_json([], medium))
                self.assertEqual(document["medium"], medium)

    def test_the_json_defaults_to_all_media(self) -> None:
        self.assertEqual(json.loads(resolve_output.review_json([]))["medium"], "all")


if __name__ == "__main__":
    unittest.main()
