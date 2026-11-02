"""Tests for resolve_args.positive: the seconds a request or git call may take.

A value ``socket.settimeout`` cannot take (infinite, NaN, or past what it can hold)
must be refused at the command line with the rule, never reach a request and end the
run with a traceback.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL))
import resolve_args  # noqa: E402 - after the sys.path inserts above
import retrolog_resolve  # noqa: E402 - after the sys.path inserts above

_EXIT_USAGE = 2


class PositiveTest(unittest.TestCase):
    def test_a_finite_value_up_to_the_maximum_is_read(self) -> None:
        for text, value in (("0.5", 0.5), ("30", 30.0), ("3600", 3600.0)):
            with self.subTest(text=text):
                self.assertEqual(resolve_args.positive(text), value)

    def test_the_maximum_is_an_hour(self) -> None:
        self.assertEqual(resolve_args.MAX_SECONDS, 3600.0)

    def test_a_value_settimeout_cannot_take_is_refused_with_the_rule(self) -> None:
        for text in ("inf", "-inf", "nan", "1e10", "3600.5", "1e400"):
            with self.subTest(text=text):
                with self.assertRaises(argparse.ArgumentTypeError) as raised:
                    resolve_args.positive(text)
                message = str(raised.exception)
                self.assertIn("greater than 0", message)
                self.assertIn("at most 3600", message)

    def test_zero_negative_and_no_number_are_refused(self) -> None:
        for text in ("0", "-1", "x", ""):
            with self.subTest(text=text):
                with self.assertRaises(argparse.ArgumentTypeError):
                    resolve_args.positive(text)

    def test_the_command_line_refuses_an_infinite_timeout(self) -> None:
        argv = ["--parsed", "in", "--out", "out", "--timeout=inf"]
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as raised:
                retrolog_resolve.build_parser().parse_args(argv)
        self.assertEqual(raised.exception.code, _EXIT_USAGE)
        self.assertIn("--timeout", stderr.getvalue())
        self.assertIn("at most 3600", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
