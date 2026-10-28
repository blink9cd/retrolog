"""Tests for the retrolog_parse command line: bad rules, refused outputs, --help."""

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL))
import parse_fixtures  # noqa: E402 - after the sys.path inserts above
import retrolog_parse  # noqa: E402 - after the sys.path inserts above
import survey_guard  # noqa: E402 - retrolog_parse puts its folder on sys.path
from parse_cli_case import EXIT_USAGE, ParseCliCase  # noqa: E402 - after the inserts

# Words a document written for a stranger must not use.
# prose: ignore[AGENTS] the list of words this test refuses in --help
_PRIVATE_WORDS = re.compile(r"\b(owner|specs?|AGENTS)\b", re.IGNORECASE)


class RulesRefusalTest(ParseCliCase):
    def assert_refused(self, message: str) -> None:
        self.assertEqual(self.main(), EXIT_USAGE)
        prog = retrolog_parse.build_parser().prog
        self.assertEqual(self.stderr, f"{prog}: {message}\n")
        self.assertNotIn(str(self.base), self.stderr)
        self.assertFalse(self.out.exists())

    def test_invalid_rules_name_the_key(self) -> None:
        self.rules.write_bytes(
            parse_fixtures.rules_bytes(
                {"color_tolerance = 40": "color_tolerance = 999"}
            )
        )
        self.assert_refused(
            "invalid --rules: ratings.color_tolerance: must be an integer from 0 to 442"
        )

    def test_missing_rules_file(self) -> None:
        self.rules.unlink()
        self.assert_refused("could not read --rules: FileNotFoundError")


class OutRefusalTest(ParseCliCase):
    def assert_refused(self, message: str, out: Path) -> None:
        self.assertEqual(self.main(out=out), EXIT_USAGE)
        prog = retrolog_parse.build_parser().prog
        self.assertEqual(self.stderr, f"{prog}: {message}\n")
        self.assertEqual(self.stdout, "")

    def test_out_inside_the_source_tree(self) -> None:
        before = sorted(p.name for p in (self.tree / "33").iterdir())
        self.assert_refused(
            "refused --out: inside the source tree", self.tree / "33" / "out"
        )
        self.assertEqual(sorted(p.name for p in (self.tree / "33").iterdir()), before)

    def test_out_holding_another_tools_output(self) -> None:
        self.out.mkdir()
        (self.out / survey_guard.MARKER).write_bytes(b"survey\n")
        self.assert_refused(
            "refused --out: not empty and not a previous retrolog_parse output",
            self.out,
        )

    def test_temporary_directory_inside_path_is_refused(self) -> None:
        with mock.patch("tempfile.gettempdir", return_value=str(self.tree)):
            self.assert_refused(
                "refused the temporary directory: it lies inside --path or --out",
                self.out,
            )

    def test_refused_at_prepare(self) -> None:
        with mock.patch.object(
            survey_guard,
            "prepare_out_dir",
            side_effect=survey_guard.OutRefused("cannot inspect the output location"),
        ):
            self.assert_refused(
                "refused --out: cannot inspect the output location", self.out
            )

    def test_write_failure_exits_2_without_a_path(self) -> None:
        error = PermissionError(13, "Permission denied", str(self.out / "x"))
        with mock.patch.object(retrolog_parse, "write_atomic", side_effect=error):
            self.assertEqual(self.main(), EXIT_USAGE)
        self.assertIn("could not write the output: PermissionError", self.stderr)
        self.assertNotIn(str(self.base), self.stderr)


class ArgumentTest(ParseCliCase):
    def test_path_that_is_not_a_directory_exits_2(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as caught:
                retrolog_parse.main(
                    ["--path", str(self.rules), "--rules", str(self.rules),
                     "--out", str(self.out)]
                )  # fmt: skip
        self.assertEqual(caught.exception.code, EXIT_USAGE)
        self.assertIn("--path must be an existing directory", stderr.getvalue())

    def test_bad_numbers_exit_2(self) -> None:
        for option, value in (("--max-bytes", "0"), ("--timeout", "0")):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    retrolog_parse.main(
                        ["--path", str(self.tree), "--rules", str(self.rules),
                         "--out", str(self.out), option, value]
                    )  # fmt: skip
            self.assertEqual(caught.exception.code, EXIT_USAGE, option)

    def test_help_shows_defaults_and_prerequisites(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit) as caught:
                retrolog_parse.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        text = " ".join(stdout.getvalue().split())
        for expected in (
            "--path",
            "--rules",
            "--out",
            "--exclude",
            f"(default: {50 * 1024 * 1024})",
            "(default: 120",
            "Prerequisites:",
            "textutil",
            "Run order:",
            "Exit codes:",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("(default: None)", text)

    def test_help_and_doc_name_every_error_kind(self) -> None:
        epilog = " ".join((retrolog_parse.build_parser().epilog or "").split())
        doc = (_TOOL / "retrolog_parse.md").read_text(encoding="utf-8")
        exit_one = next(line for line in doc.splitlines() if line.startswith("| `1` |"))
        for kind in retrolog_parse.ERROR_KINDS:
            self.assertIn(kind, epilog)
            self.assertIn(f"`{kind}`", exit_one)


class CompanionDocTest(unittest.TestCase):
    def test_written_for_a_stranger(self) -> None:
        for name in ("retrolog_parse.md", "samples/README.md"):
            text = (_TOOL / name).read_text(encoding="utf-8")
            self.assertIsNone(_PRIVATE_WORDS.search(text), name)

    def test_sample_commands_use_a_plain_interpreter(self) -> None:
        text = (_TOOL / "samples" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("venv", text)
        self.assertIn("python3 samples/build_parse_tree.py", text)
        self.assertIn("python3 retrolog_parse.py", text)
        self.assertIn("diff -r", text)

    def test_doc_names_every_review_kind(self) -> None:
        text = (_TOOL / "retrolog_parse.md").read_text(encoding="utf-8")
        for kind in retrolog_parse.REVIEW_KINDS:
            self.assertIn(f"`{kind}`", text, kind)

    def test_doc_names_every_column(self) -> None:
        text = (_TOOL / "retrolog_parse.md").read_text(encoding="utf-8")
        for column in retrolog_parse.COLUMNS:
            self.assertIn(f"`{column}`", text, column)


if __name__ == "__main__":
    unittest.main()
