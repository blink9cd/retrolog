"""Tests for parse_output: rows into CSV files, review items into JSON and Markdown."""

import csv
import dataclasses
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
import parse_output  # noqa: E402
import parse_rows  # noqa: E402
from parse_rows import Row  # noqa: E402
from parse_types import ReviewItem  # noqa: E402

_HEADER = ",".join(parse_output.COLUMNS)


def row(**changes: Any) -> Row:
    """A dated, rated film row, with any field replaced."""
    base = Row(
        medium="film",
        title="first film",
        creator="",
        year=None,
        date=date(2031, 12, 31),
        date_is_approximate=True,
        color="",
        rating_raw="3",
        comment="",
        rewatch=False,
        section="",
        source_file="31/seen.txt",
        source_line=1,
    )
    return dataclasses.replace(base, **changes)


def item(kind: str, rel_path: str = "31/seen.txt", line: int = 1) -> ReviewItem:
    """A review item of ``kind``, an error exactly when the kind is one."""
    error = kind in parse_rows.ERROR_KINDS
    return ReviewItem(kind, rel_path, line, "some text", "why", error)


def parsed(text: str) -> list[list[str]]:
    """A CSV text's records, as the standard reader reads them."""
    return list(csv.reader(io.StringIO(text)))


class ColumnsTest(unittest.TestCase):
    def test_columns_follow_the_row_fields(self) -> None:
        fields = tuple(field.name for field in dataclasses.fields(Row))
        self.assertEqual(parse_output.COLUMNS, fields)


class CsvFilesTest(unittest.TestCase):
    def test_grouped_by_medium_and_year(self) -> None:
        rows = [
            row(),
            row(medium="book", source_file="31/read.txt"),
            row(date=date(2032, 1, 31)),
            row(date=None, source_line=4),
        ]
        files = parse_output.csv_files(rows)
        self.assertEqual(
            sorted(files),
            ["book-2031.csv", "film-2031.csv", "film-2032.csv", "film-undated.csv"],
        )

    def test_header_then_one_record_per_row(self) -> None:
        text = parse_output.csv_files([row()])["film-2031.csv"]
        self.assertEqual(
            text,
            f"{_HEADER}\nfilm,first film,,,2031-12-31,true,,3,,false,,31/seen.txt,1\n",
        )

    def test_values_are_spelled_out(self) -> None:
        full = row(
            year=1999,
            date_is_approximate=False,
            color="112233",
            rewatch=True,
            section="work",
            comment="good",
        )
        record = parsed(parse_output.csv_files([full])["film-2031.csv"])[1]
        self.assertEqual(
            record,
            [
                "film",
                "first film",
                "",
                "1999",
                "2031-12-31",
                "false",
                "112233",
                "3",
                "good",
                "true",
                "work",
                "31/seen.txt",
                "1",
            ],
        )

    def test_undated_rows_leave_the_date_empty(self) -> None:
        text = parse_output.csv_files([row(date=None)])["film-undated.csv"]
        self.assertEqual(parsed(text)[1][4], "")

    def test_sorted_by_date_then_file_then_line(self) -> None:
        rows = [
            row(title="c", date=date(2031, 12, 31), source_file="31/b.txt"),
            row(
                title="b",
                date=date(2031, 12, 31),
                source_file="31/a.txt",
                source_line=9,
            ),
            row(
                title="a",
                date=date(2031, 12, 31),
                source_file="31/a.txt",
                source_line=2,
            ),
            row(title="z", date=date(2031, 4, 30), source_file="31/z.txt"),
        ]
        text = parse_output.csv_files(rows)["film-2031.csv"]
        self.assertEqual(
            [record[1] for record in parsed(text)[1:]], ["z", "a", "b", "c"]
        )

    def test_undated_rows_sort_by_file_then_line(self) -> None:
        rows = [
            row(title="b", date=None, source_file="b.txt"),
            row(title="a2", date=None, source_file="a.txt", source_line=2),
            row(title="a1", date=None, source_file="a.txt", source_line=1),
        ]
        text = parse_output.csv_files(rows)["film-undated.csv"]
        self.assertEqual([record[1] for record in parsed(text)[1:]], ["a1", "a2", "b"])

    def test_no_rows_no_files(self) -> None:
        self.assertEqual(parse_output.csv_files([]), {})

    def test_comma_and_quote_round_trip(self) -> None:
        title = 'a "quoted", comma title'
        text = parse_output.csv_files([row(title=title)])["film-2031.csv"]
        self.assertEqual(parsed(text)[1][1], title)

    @given(st.text(alphabet=st.characters(codec="utf-8")))
    def test_any_title_round_trips(self, title: str) -> None:
        text = parse_output.csv_files([row(title=title)])["film-2031.csv"]
        self.assertEqual(list(csv.reader(io.StringIO(text, newline="")))[1][1], title)


class CsvNameTest(unittest.TestCase):
    # The reader pattern resolve and export read parse's output by: it must take
    # every name csv_name writes, a year of any width included.
    def test_the_pattern_is_the_published_one(self) -> None:
        self.assertEqual(
            parse_output.CSV_NAME.pattern, r"(book|film)-(\d+|undated)\.csv"
        )

    def test_a_short_year_and_an_undated_row_match(self) -> None:
        # dates.century = 100 dates a row in year 100, a three-digit name.
        for dated, groups in (
            (date(100, 1, 1), ("film", "100")),
            (None, ("film", "undated")),
        ):
            match = parse_output.CSV_NAME.fullmatch(
                parse_output.csv_name(row(date=dated))
            )
            assert match is not None
            self.assertEqual(match.groups(), groups)

    def test_no_other_name_matches(self) -> None:
        for name in ("film-2031.txt", "song-2031.csv", "film-.csv", "film-٣.csv"):
            self.assertIsNone(parse_output.CSV_NAME.fullmatch(name))

    @given(
        st.sampled_from(["book", "film"]),
        st.one_of(st.none(), st.dates()),
    )
    def test_every_written_name_matches(self, medium: str, dated: date | None) -> None:
        name = parse_output.csv_name(row(medium=medium, date=dated))
        match = parse_output.CSV_NAME.fullmatch(name)
        assert match is not None
        year = parse_output.UNDATED if dated is None else str(dated.year)
        self.assertEqual(match.groups(), (medium, year))


class YearOrderTest(unittest.TestCase):
    # The one key resolve and export sort parse's file names by: the names' year
    # part is unpadded, so ordering it as text would put 150 after 1499.
    def test_a_shorter_year_is_the_earlier_and_undated_is_last(self) -> None:
        years = ["undated", "2010", "1499", "150", "999", "1000"]
        self.assertEqual(
            sorted(years, key=parse_output.year_order),
            ["150", "999", "1000", "1499", "2010", "undated"],
        )

    def test_the_key_is_undated_then_width_then_text(self) -> None:
        self.assertEqual(parse_output.year_order("150"), (False, 3, "150"))
        self.assertEqual(
            parse_output.year_order(parse_output.UNDATED), (True, 0, "undated")
        )

    @given(st.dates(), st.dates())
    def test_it_orders_written_names_as_their_years(self, a: date, b: date) -> None:
        def year_of(dated: date) -> str:
            match = parse_output.CSV_NAME.fullmatch(
                parse_output.csv_name(row(date=dated))
            )
            assert match is not None
            return match.group(2)

        first, second = (parse_output.year_order(year_of(d)) for d in (a, b))
        self.assertEqual(first < second, a.year < b.year)


class ReviewCountsTest(unittest.TestCase):
    def test_every_kind_counted_in_order(self) -> None:
        counts = parse_output.review_counts([item("unrated"), item("unrated")])
        self.assertEqual(tuple(counts), parse_rows.REVIEW_KINDS)
        self.assertEqual(counts["unrated"], 2)
        self.assertEqual(sum(counts.values()), 2)


class ReviewJsonTest(unittest.TestCase):
    def test_layout_and_order(self) -> None:
        items = [
            item("unrated", "b.txt", 1),
            item("unparsed", "a.txt", 2),
            item("unrated", "a.txt", 2),
            item("read-error", "a.txt", 0),
        ]
        counts = parse_output.review_counts(items)
        data = json.loads(parse_output.review_json(items, counts))
        self.assertEqual(sorted(data), ["counts", "items", "schema_version"])
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["counts"], counts)
        self.assertEqual(
            [(i["rel_path"], i["line"], i["kind"]) for i in data["items"]],
            [("a.txt", 0, "read-error"), ("a.txt", 2, "unparsed"),
             ("a.txt", 2, "unrated"), ("b.txt", 1, "unrated")],
        )  # fmt: skip
        self.assertEqual(
            data["items"][0],
            {
                "kind": "read-error",
                "rel_path": "a.txt",
                "line": 0,
                "text": "some text",
                "detail": "why",
                "error": True,
            },
        )

    def test_keys_sorted_and_text_ends_in_a_newline(self) -> None:
        text = parse_output.review_json([item("unrated")], {"b": 1, "a": 0})
        self.assertTrue(text.endswith("}\n"))
        self.assertLess(text.index('"a"'), text.index('"b"'))
        self.assertLess(text.index('"counts"'), text.index('"items"'))

    def test_non_ascii_text_is_kept(self) -> None:
        caf = dataclasses.replace(
            item("unrated"), text="caf\N{LATIN SMALL LETTER E WITH ACUTE}"
        )
        self.assertIn(
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            parse_output.review_json([caf], {}),
        )


class ReviewMarkdownTest(unittest.TestCase):
    def render(self, items: list[ReviewItem]) -> str:
        return parse_output.review_markdown(items, parse_output.review_counts(items))

    def test_count_line_at_the_top(self) -> None:
        text = self.render([item("read-error"), item("unrated"), item("unrated")])
        lines = text.splitlines()
        self.assertEqual(lines[0], "# retrolog_parse review")
        self.assertEqual(lines[2], "3 review items, 1 of them an error.")

    def test_one_table_per_kind_in_kind_order(self) -> None:
        text = self.render(
            [item("unrated"), item("unparsed"), item("override-mismatch")]
        )
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(
            headings,
            ["## override-mismatch (1, error)", "## unparsed (1)", "## unrated (1)"],
        )

    def test_table_rows(self) -> None:
        text = self.render([item("unrated", "31/seen.txt", 4)])
        self.assertIn(
            "| File | Line | Text | Detail |\n| --- | --- | --- | --- |\n"
            "| 31/seen.txt | 4 | some text | why |\n",
            text,
        )

    def test_markup_in_source_text_is_escaped(self) -> None:
        odd = dataclasses.replace(item("unrated"), text="a | b `c` <d>\nnext")
        text = self.render([odd])
        self.assertIn(
            "| a \\| b \\`c\\` \\<d\\>\N{DOWNWARDS ARROW WITH CORNER LEFTWARDS}next |",
            text,
        )

    def test_cells_are_trimmed(self) -> None:
        # Regression: a Word line's text that opens with spaces padded its cell, which
        # Markdown's compact table style refuses.
        padded = dataclasses.replace(item("legend"), text="   Film Four ")
        self.assertIn("| 31/seen.txt | 1 | Film Four | why |", self.render([padded]))

    def test_an_empty_cell_is_one_space(self) -> None:
        whole_file = dataclasses.replace(item("read-error"), text="", line=0)
        self.assertIn("| 31/seen.txt | 0 | | why |", self.render([whole_file]))

    def test_a_pipe_in_a_file_name_is_escaped(self) -> None:
        text = self.render([item("unrated", "a|b.txt")])
        self.assertIn("| a\\|b.txt |", text)

    def test_no_items(self) -> None:
        text = self.render([])
        self.assertEqual(
            text, "# retrolog_parse review\n\n0 review items, 0 of them errors.\n"
        )

    def test_one_item_is_singular(self) -> None:
        text = self.render([item("unrated")])
        self.assertEqual(text.splitlines()[2], "1 review item, 0 of them errors.")

    def test_the_count_line_is_public(self) -> None:
        # resolve prints the same count line, so it imports this one.
        self.assertEqual(
            parse_output.count_line(1, 1), "1 review item, 1 of them an error."
        )
        self.assertEqual(
            parse_output.count_line(2, 0), "2 review items, 0 of them errors."
        )

    def test_ends_with_one_newline(self) -> None:
        text = self.render([item("unrated")])
        self.assertTrue(text.endswith("|\n"))
        self.assertFalse(text.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
