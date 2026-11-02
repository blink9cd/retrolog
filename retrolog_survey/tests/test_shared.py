"""Tests for the survey helpers the later retrolog tools import: the count noun and the
converter preference."""

import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
import survey_markdown  # noqa: E402 - after the sys.path insert above
import survey_report  # noqa: E402 - after the sys.path insert above
from survey_convert import (  # noqa: E402 - after the sys.path insert above
    ConversionResult,
    Converter,
    Status,
)

_NONE: frozenset[str] = frozenset()


def _result(converter: Converter, status: Status) -> ConversionResult:
    """One converter's outcome, with no features."""
    return ConversionResult(converter, status, "", _NONE)


class CountedTest(unittest.TestCase):
    def test_one_is_singular_and_every_other_count_plural(self) -> None:
        self.assertEqual(survey_markdown.counted(1, "row"), "1 row")
        self.assertEqual(survey_markdown.counted(0, "row"), "0 rows")
        self.assertEqual(survey_markdown.counted(2, "file"), "2 files")


class PreferredTest(unittest.TestCase):
    def test_libreoffice_wins_when_it_worked(self) -> None:
        results = [_result("textutil", "ok"), _result("libreoffice", "ok")]
        chosen = survey_report.preferred(results)
        assert chosen is not None
        self.assertEqual(chosen.converter, "libreoffice")

    def test_textutil_when_libreoffice_failed(self) -> None:
        results = [_result("textutil", "ok"), _result("libreoffice", "failed")]
        chosen = survey_report.preferred(results)
        assert chosen is not None
        self.assertEqual(chosen.converter, "textutil")

    def test_none_when_neither_worked(self) -> None:
        results = [_result("textutil", "failed"), _result("libreoffice", "failed")]
        self.assertIsNone(survey_report.preferred(results))


if __name__ == "__main__":
    unittest.main()
