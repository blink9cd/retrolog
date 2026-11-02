"""Tests for probe_report: the report's counts, its two tables, and their escaping."""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_export" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
import probe_report  # noqa: E402 - after the sys.path inserts above
from export_config import load_config  # noqa: E402 - after the sys.path inserts
from parse_rows import title_key  # noqa: E402 - after the sys.path inserts above
from probe_input import IdRow, ImportRows  # noqa: E402 - after the sys.path inserts
from probe_run import RunResult  # noqa: E402 - after the sys.path inserts above
from probe_verdict import Outcome  # noqa: E402 - after the sys.path inserts above

_A, _B = "letterboxd-001.csv", "letterboxd-002.csv"
# The smallest export config export's own loader accepts, for the omit block to join.
_CONFIG_HEAD = (
    "schema = 1\n"
    "[letterboxd]\n"
    "watched_date = true\n"
    'tags = ["retrolog"]\n'
    'review_note = ""\n'
    'review_note_rows = "all"\n'
    "[letterboxd.ratings]\n"
    '"a" = 1\n'
)


def _entry_key(row: IdRow) -> tuple[str, int | None]:
    """The key export matches an omit entry by: the title's key and the year."""
    return (title_key(row.title), int(row.year) if row.year else None)


def _rows(*id_rows: IdRow, by_title: int = 0) -> ImportRows:
    files = tuple(sorted({row.file for row in id_rows}))
    return ImportRows(files, len(id_rows) + by_title, by_title, id_rows)


class ReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = _rows(
            IdRow(_A, 1, "10", "Present Film", "1950"),
            IdRow(_A, 2, "20", "Missing | *Film*", "1960"),
            IdRow(_B, 1, "30", "Unknown\nFilm", ""),
            IdRow(_B, 2, "20", "Missing | *Film*", "1960"),
            by_title=1,
        )
        self.result = RunResult(
            outcomes={
                "10": Outcome("present"),
                "20": Outcome("missing"),
                "30": Outcome("unknown", "HTTP 503"),
            },
            from_cache=1,
            requests=3,
        )

    def test_the_whole_report(self) -> None:
        self.assertEqual(
            probe_report.report_markdown(self.rows, self.result),
            "# retrolog_probe report\n"
            "\n"
            "Read 2 import files: 5 rows, 1 of them by title, which carries no tmdbID "
            "and is not checked. 3 distinct tmdbIDs: 1 present, 1 missing, 1 unknown; "
            "1 answered from the cache, 3 requests sent.\n"
            "\n"
            "Row counts each file's data rows from 1, the header not counted. It is not "
            "a line number: a review can span lines.\n"
            "\n"
            "## Missing (2 rows)\n"
            "\n"
            "| File | Row | tmdbID | Title | Year |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| letterboxd-001.csv | 2 | 20 | Missing \\| *Film* | 1960 |\n"
            "| letterboxd-002.csv | 2 | 20 | Missing \\| *Film* | 1960 |\n"
            "\n"
            "## Omit entries (1 film)\n"
            "\n"
            "To leave the missing films out of this and every later export, add these "
            "entries to the export config's `[letterboxd]` settings and run export "
            "again into a new folder.\n"
            "\n"
            "```toml\n"
            "[[letterboxd.omit]]\n"
            'title = "Missing | *Film*"\n'
            "year = 1960\n"
            "```\n"
            "\n"
            "## Unknown (1 row)\n"
            "\n"
            "| File | Row | tmdbID | Title | Year | Reason |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| letterboxd-002.csv | 1 | 30 | Unknown\N{DOWNWARDS ARROW WITH CORNER LEFTWARDS}Film | | HTTP 503 |\n"
            "\n"
            "Leave the missing films out of the import: add the omit entries and run "
            "export again, or delete their rows from the import file before uploading "
            "it. Run the check again later for the unknown ones.\n",
        )

    def test_an_empty_section_says_none(self) -> None:
        result = RunResult(
            outcomes={"10": Outcome("present")}, from_cache=0, requests=2
        )
        text = probe_report.report_markdown(
            _rows(IdRow(_A, 1, "10", "Present Film", "1950")), result
        )
        self.assertIn(
            "## Missing (0 rows)\n\nNone.\n\n## Unknown (0 rows)\n\nNone.\n", text
        )
        self.assertNotIn("Omit entries", text)
        self.assertIn("Read 1 import file: 1 row, 0 of them by title", text)
        self.assertIn("1 distinct tmdbID: ", text)
        self.assertIn("0 answered from the cache, 2 requests sent.", text)

    def test_the_summary_line_holds_counts_only(self) -> None:
        line = probe_report.summary_line(self.result)
        self.assertEqual(
            line,
            "3 distinct tmdbIDs: 1 present, 1 missing, 1 unknown "
            "(1 from the cache, 3 requests sent)",
        )
        self.assertNotIn("Film", line)


class OmitEntriesTest(unittest.TestCase):
    def _block(self, *id_rows: IdRow) -> str:
        result = RunResult(
            outcomes={row.tmdb_id: Outcome("missing") for row in id_rows},
            from_cache=0,
            requests=0,
        )
        text = probe_report.report_markdown(_rows(*id_rows), result)
        return text.split("```toml\n", 1)[1].split("```", 1)[0]

    def _entries(self, *id_rows: IdRow) -> object:
        return tomllib.loads(self._block(*id_rows))["letterboxd"]["omit"]

    def test_titles_alike_in_one_year_get_one_entry_export_accepts(self) -> None:
        # Export keys an entry by title_key and year, so two missing films whose
        # titles normalize alike in one year must share one entry, or export stops.
        rows = (
            IdRow(_A, 1, "111", "Hamlet", "1990"),
            IdRow(_A, 2, "222", "HAMLET", "1990"),
            IdRow(_A, 3, "333", "Crash", "2004"),
            IdRow(_B, 1, "444", "Crash!", "2004"),
            IdRow(_B, 2, "555", "Crash", "1996"),
        )
        block = self._block(*rows)
        config = load_config((_CONFIG_HEAD + block).encode())
        assert config.letterboxd is not None
        self.assertEqual(len(config.letterboxd.omit), 3)
        self.assertEqual(
            tomllib.loads(block)["letterboxd"]["omit"],
            [
                {"title": "Hamlet", "year": 1990},
                {"title": "Crash", "year": 2004},
                {"title": "Crash", "year": 1996},
            ],
        )

    def test_a_skipped_row_reserves_no_key_export_accepts(self) -> None:
        # Film 111's second row adds no entry, so its key must not block film 222's
        # entry: otherwise no entry names 222 and export sends it.
        block = self._block(
            IdRow(_A, 1, "111", "Hamlet", "1990"),
            IdRow(_A, 2, "111", "Othello", "1990"),
            IdRow(_A, 3, "222", "Othello", "1990"),
        )
        config = load_config((_CONFIG_HEAD + block).encode())
        assert config.letterboxd is not None
        self.assertEqual(len(config.letterboxd.omit), 2)
        self.assertEqual(
            tomllib.loads(block)["letterboxd"]["omit"],
            [{"title": "Hamlet", "year": 1990}, {"title": "Othello", "year": 1990}],
        )

    def test_the_heading_counts_films_not_entries(self) -> None:
        result = RunResult(
            outcomes={key: Outcome("missing") for key in ("111", "222", "333")},
            from_cache=0,
            requests=0,
        )
        rows = _rows(
            IdRow(_A, 1, "111", "Hamlet", "1990"),
            IdRow(_A, 2, "222", "HAMLET", "1990"),
            IdRow(_A, 3, "333", "Crash", "2004"),
        )
        text = probe_report.report_markdown(rows, result)
        self.assertIn("## Omit entries (3 films)\n", text)
        self.assertEqual(text.count("[[letterboxd.omit]]"), 2)

    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["1", "2", "3"]),
                st.sampled_from(["Hamlet", "HAMLET", "Othello"]),
                st.sampled_from(["1990", ""]),
            ),
            max_size=6,
        )
    )
    def test_every_missing_film_is_named_by_one_entry(
        self, cells: list[tuple[str, str, str]]
    ) -> None:
        missing = [IdRow(_A, n, *cell) for n, cell in enumerate(cells, 1)]
        keys = [_entry_key(row) for row in probe_report._distinct_entries(missing)]
        self.assertEqual(len(keys), len(set(keys)))
        for film in {row.tmdb_id for row in missing}:
            named = [row for row in missing if row.tmdb_id == film]
            self.assertTrue(any(_entry_key(row) in keys for row in named))

    def test_titles_alike_with_no_year_get_one_entry(self) -> None:
        entries = self._entries(
            IdRow(_A, 1, "111", "Undated", ""), IdRow(_A, 2, "222", "undated", "")
        )
        self.assertEqual(entries, [{"title": "Undated"}])

    def test_one_entry_per_film_from_its_first_row(self) -> None:
        # A film listed under two titles still gets one entry: export stops on a
        # second entry naming the same film.
        entries = self._entries(
            IdRow(_A, 1, "20", "First Title", "1960"),
            IdRow(_A, 2, "30", "Other Film", "1970"),
            IdRow(_B, 1, "20", "Second Title", "1960"),
        )
        self.assertEqual(
            entries,
            [
                {"title": "First Title", "year": 1960},
                {"title": "Other Film", "year": 1970},
            ],
        )

    def test_a_film_with_no_year_gets_no_year_key(self) -> None:
        entries = self._entries(IdRow(_A, 1, "20", "Undated", ""))
        self.assertEqual(entries, [{"title": "Undated"}])

    def test_a_title_cannot_end_the_code_block(self) -> None:
        entries = self._entries(IdRow(_A, 1, "20", 'A "```" \\ Title', "1999"))
        self.assertEqual(entries, [{"title": 'A "```" \\ Title', "year": 1999}])

    @given(st.text(alphabet=st.characters(exclude_categories=["Cs"])))
    def test_fuzz_toml_string(self, text: str) -> None:
        quoted = probe_report.toml_string(text)
        self.assertNotIn("`", quoted)
        self.assertEqual(tomllib.loads(f"t = {quoted}")["t"], text)


if __name__ == "__main__":
    unittest.main()
