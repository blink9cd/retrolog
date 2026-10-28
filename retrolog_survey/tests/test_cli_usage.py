"""Tests for the retrolog_survey command line: refused outputs and bad arguments."""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TESTS.parent / "samples"))
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS.parent))
import retrolog_survey  # noqa: E402 - after the sys.path inserts above
import survey_guard  # noqa: E402 - after the sys.path inserts above
from cli_case import EXIT_USAGE, CliCase, fake_git  # noqa: E402 - after the inserts
from report_shapes import as_dict, as_list  # noqa: E402 - after the inserts


class RefusalTest(CliCase):
    def assert_refused(
        self, message: str, out: Path, run_git: survey_guard.GitRunner | None = None
    ) -> None:
        self.assertEqual(self.main(out=out, run_git=run_git), EXIT_USAGE)
        prog = retrolog_survey.build_parser().prog
        self.assertEqual(self.stderr, f"{prog}: refused --out: {message}\n")
        self.assertEqual(self.stdout, "")

    def test_inside_the_source_tree(self) -> None:
        self.assert_refused("inside the source tree", self.tree / "reports")

    def test_inside_a_git_work_tree(self) -> None:
        self.assert_refused("inside a git work tree", self.out, run_git=fake_git(0))

    def test_not_a_directory(self) -> None:
        self.out.write_bytes(b"")
        self.assert_refused("not a directory", self.out)

    def test_not_empty(self) -> None:
        self.out.mkdir()
        (self.out / "notes.txt").write_bytes(b"")
        self.assert_refused("not empty and not a previous survey output", self.out)

    def test_cannot_inspect(self) -> None:
        loop = self.base / "loop"
        loop.symlink_to(self.base / "loop2")
        (self.base / "loop2").symlink_to(loop)
        self.assert_refused("cannot inspect the output location", loop / "out")

    def test_refused_at_prepare(self) -> None:
        refusal = survey_guard.OutRefused("cannot inspect the output location")
        with mock.patch.object(survey_guard, "prepare_out_dir", side_effect=refusal):
            self.assert_refused("cannot inspect the output location", self.out)
        self.assertFalse(self.out.exists())


class UsageTest(CliCase):
    def test_missing_path_exits_2(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as caught:
                retrolog_survey.main(
                    ["--path", str(self.base / "absent"), "--out", str(self.out)],
                    run_git=fake_git(),
                )
        self.assertEqual(caught.exception.code, EXIT_USAGE)
        self.assertNotIn(str(self.base), stderr.getvalue())

    def test_path_that_is_a_file_exits_2(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                retrolog_survey.main(
                    ["--path", str(self.produce), "--out", str(self.out)],
                    run_git=fake_git(),
                )
        self.assertEqual(caught.exception.code, EXIT_USAGE)

    def test_bad_numbers_exit_2(self) -> None:
        for option, value in (
            ("--samples", "-1"),
            ("--samples", "0"),
            ("--group-depth", "x"),
            ("--max-file-mb", "0"),
            ("--timeout", "0"),
            ("--timeout", "soon"),
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    retrolog_survey.main(
                        [
                            "--path",
                            str(self.tree),
                            "--out",
                            str(self.out),
                            option,
                            value,
                        ]
                    )
            self.assertEqual(caught.exception.code, EXIT_USAGE, option)

    def test_a_positive_timeout_is_accepted(self) -> None:
        # The default never passes through the type, and the only other caller passing a
        # valid --timeout is the macOS-only live test, so without this the accepting branch
        # goes unrun everywhere else.
        args = retrolog_survey.build_parser().parse_args(
            ["--path", str(self.tree), "--out", str(self.out), "--timeout", "2.5"]
        )
        self.assertEqual(args.timeout, 2.5)

    def test_help_shows_defaults_and_prerequisites(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit) as caught:
                retrolog_survey.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        text = " ".join(stdout.getvalue().split())
        for expected in (
            "(default: 1)",
            "(default: 5)",
            "(default: 50)",
            "(default: 120)",
            "Prerequisites: macOS",
            "LibreOffice",
            "Run order:",
        ):
            self.assertIn(expected, text)

    def test_numeric_options_are_applied(self) -> None:
        self.main("--group-depth", "0", "--samples", "1", "--max-file-mb", "1")
        report = self.report()
        self.assertEqual(
            as_dict(report["options"]),
            {"group_depth": 0, "samples": 1, "converters": True},
        )
        group = self.groups()["."]
        samples = as_dict(group["samples"])
        self.assertTrue(samples)
        self.assertTrue(all(len(as_list(taken)) == 1 for taken in samples.values()))

    def test_exclude_is_case_insensitive_and_repeatable(self) -> None:
        self.main("--exclude", "BOOKS/*", "--exclude", "*.DOC")
        self.assertEqual(as_dict(self.groups()["books"]["files"])["excluded"], 1)
        self.assertEqual(as_dict(self.groups()["films"]["files"])["excluded"], 1)
        self.assertEqual(self.runner.calls, 0)


class ImageExtensionTest(unittest.TestCase):
    def test_raster_types_keep_their_extension(self) -> None:
        cases = {"image/png": "png", "image/JPEG": "jpeg", "image/x-bmp": "bmp"}
        for content_type, extension in cases.items():
            with self.subTest(content_type=content_type):
                self.assertEqual(retrolog_survey._extension(content_type), extension)

    def test_any_other_type_is_written_as_bin(self) -> None:
        # Regression: image/svg+xml became ".svgxml", and an SVG a browser opens runs
        # its script; a declared type is the surveyed file's word, not a fact.
        for content_type in ("image/svg+xml", "text/html", "image/x-emf", "", "png"):
            with self.subTest(content_type=content_type):
                self.assertEqual(retrolog_survey._extension(content_type), "bin")


if __name__ == "__main__":
    unittest.main()
