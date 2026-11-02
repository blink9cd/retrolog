"""End-to-end tests of the account classes: one run, every class of film.

A film the account holds only as watched is sent in full; one the owner logged is sent
with an empty Rating, less any row on a diary entry's date; one an earlier import
tagged ``retrolog`` is not sent; one the account lacks is sent as it always was.
"""

from __future__ import annotations

import csv
import io
import json
import sys
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
sys.path.insert(0, str(_TOOL))
from export_cli_case import EXIT_OK, CliCase  # noqa: E402 - after the inserts
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    account_csv,
    config_toml,
    logged_csv,
    resolved_row,
    write_account_folder,
)


def _film(title: str, tmdb_id: str, line: int, date: str) -> dict[str, str]:
    """A resolved row of ``title`` from 1999, rated "2", on ``date``."""
    return resolved_row(
        title=title, tmdb_id=tmdb_id, source_line=str(line), date=date, comment="Ok."
    )


_ROWS = [
    _film("Placeholder Fresh", "1000011", 1, "2010-03-01"),
    _film("Placeholder Watched", "1000012", 2, "2010-03-02"),
    _film("Placeholder Logged", "1000013", 3, "2010-03-03"),
    _film("Placeholder Logged", "1000013", 4, "2010-03-04"),
    _film("Placeholder Imported", "1000014", 5, "2010-03-05"),
]


class AccountClassesTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_rows(_ROWS)
        write_account_folder(
            self.account,
            {
                "watched.csv": account_csv(
                    [
                        ("Placeholder Watched", "1999"),
                        ("Placeholder Logged", "1999"),
                        ("Placeholder Imported", "1999"),
                    ]
                ),
                "diary.csv": logged_csv(
                    [
                        ("Placeholder Logged", "1999", "favorite", "2010-03-04"),
                        ("Placeholder Imported", "1999", "", "2010-03-05"),
                    ]
                ),
                "ratings.csv": account_csv([("Placeholder Imported", "1999")]),
                "reviews.csv": logged_csv(
                    [("Placeholder Imported", "1999", " Retrolog ", "")]
                ),
            },
        )

    def _sent(self) -> dict[str, dict[str, str]]:
        """Each sent row by its Title and WatchedDate."""
        reader = csv.DictReader(io.StringIO(self.read("letterboxd-001.csv")))
        return {f"{row['Title']} {row['WatchedDate']}": row for row in reader}

    def test_each_class_is_sent_as_r69_sets_out(self) -> None:
        self.config.write_bytes(
            config_toml(
                rating_tags={"2": "retrolog-mid"},
                rating_names={"1": "low", "2": "mid"},
                review_note_rating="Original rating: {rating}.",
            )
        )
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        sent = self._sent()
        self.assertEqual(
            sorted(sent),
            [
                "Placeholder Fresh 2010-03-01",
                "Placeholder Logged 2010-03-03",
                "Placeholder Watched 2010-03-02",
            ],
        )
        self.assertEqual(sent["Placeholder Fresh 2010-03-01"]["Rating"], "2.5")
        self.assertEqual(sent["Placeholder Watched 2010-03-02"]["Rating"], "2.5")
        logged = sent["Placeholder Logged 2010-03-03"]
        self.assertEqual(logged["Rating"], "")
        self.assertEqual(logged["Tags"], "retrolog-mid, retrolog")
        self.assertIn("Original rating: mid.", logged["Review"])

    def test_the_summary_and_review_list_each_class(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        for line in (
            "films sent: 3",
            "rows sent: 3",
            "review on-service: 1",
            "review same-date: 1",
            "review logged-no-stars: 1",
            "review watched-only: 1",
        ):
            self.assertIn(line, self.stdout.splitlines())
        counts = json.loads(self.read("review.json"))["counts"]
        self.assertEqual(
            [counts[kind] for kind in ("same-date", "logged-no-stars", "watched-only")],
            [1, 1, 1],
        )
        markdown = self.read("review.md")
        self.assertLess(markdown.index("## on-service"), markdown.index("## same-date"))
        self.assertLess(
            markdown.index("## logged-no-stars"), markdown.index("## watched-only")
        )

    def test_no_row_is_dropped_when_watched_dates_are_off(self) -> None:
        self.config.write_bytes(config_toml(watched_date=False))
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertIn("rows sent: 4", self.stdout.splitlines())
        self.assertNotIn("review same-date", self.stdout)


if __name__ == "__main__":
    unittest.main()
