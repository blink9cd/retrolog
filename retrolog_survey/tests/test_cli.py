"""Tests for the retrolog_survey command line: what it reads, writes and returns."""

import contextlib
import errno
import functools
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TESTS.parent / "samples"))
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS.parent))
import build_sample_tree  # noqa: E402 - after the sys.path inserts above
import retrolog_survey  # noqa: E402 - after the sys.path inserts above
import survey_convert  # noqa: E402 - after the sys.path inserts above
import survey_guard  # noqa: E402 - after the sys.path inserts above
from cli_case import (  # noqa: E402 - after the sys.path inserts above
    EXIT_FILE_ERRORS,
    EXIT_OK,
    EXIT_USAGE,
    PNG,
    PNG_SHA,
    TIMEOUT,
    CliCase,
    fake_git,
    snapshot,
)
from report_shapes import as_dict, as_list  # noqa: E402 - after the sys.path inserts above

_TOOL = _TESTS.parent
# Words a document written for a stranger must not use.
# prose: ignore[AGENTS] the list of words this test refuses in --help
_PRIVATE_WORDS = re.compile(r"\b(owner|specs?|AGENTS)\b", re.IGNORECASE)


class ReadOnlyTest(CliCase):
    def test_source_tree_is_unchanged(self) -> None:
        self.add_broken_docx()
        before = snapshot(self.tree)
        self.assertEqual(self.main(), EXIT_FILE_ERRORS)
        self.assertEqual(snapshot(self.tree), before)
        self.assertGreater(self.runner.calls, 0)

    def test_snapshot_sees_a_new_directory(self) -> None:
        # Regression: the snapshot recorded files only, so a directory created under
        # --path would have passed the read-only test.
        before = snapshot(self.tree)
        (self.tree / "films" / "new").mkdir()
        self.assertNotEqual(snapshot(self.tree), before)

    def test_out_holds_only_the_outputs(self) -> None:
        self.assertEqual(self.main(), EXIT_OK)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            [survey_guard.MARKER, "images", "report.json", "report.md"],
        )


class ExitCodeTest(CliCase):
    def test_broken_docx_is_listed_and_exits_1(self) -> None:
        self.add_broken_docx()
        self.assertEqual(self.main(), EXIT_FILE_ERRORS)
        self.assertEqual(
            self.groups()["films"]["errors"],
            [{"file": "films/broken.docx", "error": "BadDocx: BadZipFile"}],
        )
        self.assertIn("1 file(s) could not be surveyed", self.stderr)

    def test_clean_tree_exits_0(self) -> None:
        self.assertEqual(self.main(), EXIT_OK)
        self.assertEqual(self.stderr, "")
        self.assertEqual(self.groups()["films"]["errors"], [])

    def test_summary_line_per_group(self) -> None:
        self.main()
        self.assertEqual(
            self.stdout.splitlines(),
            ["books: 1 file, 1 line, 0 errors", "films: 3 files, 18 lines, 0 errors"],
        )

    def test_summary_escapes_a_non_utf8_group_name(self) -> None:
        # Regression: a group named by a folder whose name is not valid UTF-8 holds
        # surrogate escapes, which the strict UTF-8 standard output cannot print.
        group = {"group": "caf\udce9", "files": {}, "line_kinds": {}, "errors": []}
        lines = retrolog_survey._summary_lines({"groups": [group]})
        self.assertEqual(lines, ["caf\\udce9: 0 files, 0 lines, 0 errors"])
        lines[0].encode("utf-8")

    @unittest.skipIf(
        sys.platform == "win32" or os.geteuid() == 0, "needs a non-root POSIX user"
    )
    def test_unreadable_entries_and_errors_name_no_path(self) -> None:
        locked_dir = self.tree / "locked"
        locked_dir.mkdir()
        locked_file = self.tree / "books" / "secret.txt"
        locked_file.write_bytes(b"Book B\n")
        locked_dir.chmod(0)
        locked_file.chmod(0)
        try:
            self.assertEqual(self.main(), EXIT_FILE_ERRORS)
        finally:
            locked_dir.chmod(0o700)
            locked_file.chmod(0o600)
        groups = self.groups()
        self.assertEqual(
            groups["books"]["errors"],
            [
                {
                    "file": "books/secret.txt",
                    "error": "PermissionError: Permission denied",
                }
            ],
        )
        self.assertEqual(
            groups["locked"]["errors"], [{"file": "locked", "error": "PermissionError"}]
        )
        self.assertNotIn(str(self.base), (self.out / "report.json").read_text("utf-8"))
        self.assertNotIn(str(self.base), (self.out / "report.md").read_text("utf-8"))
        self.assertNotIn(str(self.base), self.stderr + self.stdout)


class OutputTest(CliCase):
    def test_markdown_and_images(self) -> None:
        self.main()
        markdown = (self.out / "report.md").read_text(encoding="utf-8")
        self.assertIn("Film B [#00B050]", markdown)
        self.assertEqual(
            [p.name for p in (self.out / "images").iterdir()], [f"{PNG_SHA}.png"]
        )
        self.assertEqual((self.out / "images" / f"{PNG_SHA}.png").read_bytes(), PNG)

    def test_report_json_is_sorted_and_indented(self) -> None:
        self.main()
        text = (self.out / "report.json").read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(self.report(), sort_keys=True, indent=2,
                                          ensure_ascii=False) + "\n")  # fmt: skip

    def test_doc_is_surveyed_through_the_converter(self) -> None:
        self.main()
        films = self.groups()["films"]
        conversions = [as_dict(c) for c in as_list(films["conversions"])]
        self.assertEqual([c["file"] for c in conversions], ["films/old.doc"])
        self.assertEqual(as_dict(films["files"])["doc"], 1)
        self.assertEqual(as_dict(as_list(films["images"])[0])["count"], 2)

    def test_rerun_prunes_stale_images(self) -> None:
        self.main()
        stale = self.out / "images" / "000000000000.png"
        stale.write_bytes(b"old")
        (self.tree / "films" / "rated.docx").unlink()
        (self.tree / "films" / "old.doc").unlink()
        self.assertEqual(self.main(), EXIT_OK)
        self.assertEqual(list((self.out / "images").iterdir()), [])

    def test_rerun_keeps_current_images_and_folders(self) -> None:
        self.main()
        (self.out / "images" / "kept_folder").mkdir()
        self.assertEqual(self.main(), EXIT_OK)
        self.assertEqual(
            sorted(p.name for p in (self.out / "images").iterdir()),
            [f"{PNG_SHA}.png", "kept_folder"],
        )

    def test_no_images_makes_no_images_folder(self) -> None:
        (self.tree / "films" / "rated.docx").unlink()
        (self.tree / "films" / "old.doc").unlink()
        self.assertEqual(self.main(), EXIT_OK)
        self.assertFalse((self.out / "images").exists())

    def test_write_failure_exits_2_without_a_path(self) -> None:
        self.out.mkdir()
        (self.out / survey_guard.MARKER).write_bytes(b"")
        (self.out / "images").write_bytes(b"a file where the folder goes")
        self.assertEqual(self.main(), EXIT_USAGE)
        prog = retrolog_survey.build_parser().prog
        self.assertTrue(
            self.stderr.startswith(
                f"{prog}: could not write the report: FileExistsError"
            ),
            self.stderr,
        )
        self.assertNotIn(str(self.base), self.stderr)
        self.assertNotIn(tempfile.gettempdir(), self.stderr)
        self.assertNotIn("Traceback", self.stderr)

    def test_temporary_directory_cleanup_failure_is_not_fatal(self) -> None:
        # Regression: a failure removing the conversion directory escaped as an
        # OSError naming the temporary path. The stand-in fails every removal that
        # does not ask for errors to be ignored, as a busy or vanished folder would.
        left: list[str] = []

        def failing_rmtree(
            _cls: type, name: str, ignore_errors: bool = False, repeated: bool = False
        ) -> None:
            left.append(name)
            if not ignore_errors:
                raise OSError(errno.EBUSY, "Device or resource busy", name)

        self.addCleanup(lambda: [shutil.rmtree(n, ignore_errors=True) for n in left])
        # TemporaryDirectory removes itself through this private classmethod.
        with mock.patch.object(
            tempfile.TemporaryDirectory, "_rmtree", classmethod(failing_rmtree)
        ):
            code = self.main()
        self.assertEqual(code, EXIT_OK, self.stderr)
        self.assertTrue(left)

    def test_non_utf8_file_name_is_reported_escaped(self) -> None:
        # Regression: a name that is not valid UTF-8 reaches Python as surrogate
        # escapes, and writing it into the report raised UnicodeEncodeError.
        # Windows decodes file names as strict UTF-8, so it cannot even spell this
        # one: fsdecode raises there, and the skip below is the honest result.
        try:
            name = os.fsdecode(b"caf\xe9.txt")
            (self.tree / "films" / name).write_bytes(b"Film Z\n")
        except (OSError, UnicodeEncodeError, UnicodeDecodeError):
            self.skipTest("this file system refuses a name that is not valid UTF-8")
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        files = {
            sample["file"]
            for kind in as_dict(self.groups()["films"]["samples"]).values()
            for sample in map(as_dict, as_list(kind))
        }
        self.assertIn("films/" + name, files)
        self.assertIn("caf\\udce9", (self.out / "report.md").read_text("utf-8"))

    def test_temporary_directory_inside_path_is_refused(self) -> None:
        before = snapshot(self.tree)
        with mock.patch.object(tempfile, "tempdir", str(self.tree / "films")):
            self.assertEqual(self.main(), EXIT_USAGE)
        prog = retrolog_survey.build_parser().prog
        self.assertEqual(
            self.stderr,
            f"{prog}: refused the temporary directory: it lies inside --path or --out\n",
        )
        self.assertEqual(snapshot(self.tree), before)
        self.assertEqual(self.runner.calls, 0)

    def test_temporary_directory_inside_out_is_refused(self) -> None:
        self.out.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(self.out)):
            self.assertEqual(self.main(), EXIT_USAGE)
        self.assertIn("refused the temporary directory", self.stderr)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_images_link_is_replaced_not_followed(self) -> None:
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.png").write_bytes(b"keep")
        self.out.mkdir()
        (self.out / survey_guard.MARKER).write_bytes(b"")
        (self.out / "images").symlink_to(elsewhere)
        self.assertEqual(self.main(), EXIT_OK)
        self.assertFalse((self.out / "images").is_symlink())
        self.assertEqual([p.name for p in elsewhere.iterdir()], ["keep.png"])


class ConverterOptionTest(CliCase):
    def test_no_convert_skips_both_converters(self) -> None:
        with mock.patch.object(
            survey_convert, "locate_soffice", side_effect=AssertionError
        ):
            code = self.main("--no-convert", soffice=retrolog_survey._UNSET)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.runner.calls, 0)
        report = self.report()
        self.assertEqual(
            report["converters"], {"textutil": "skipped", "libreoffice": "skipped"}
        )
        self.assertEqual(as_dict(report["options"])["converters"], False)
        self.assertEqual(self.groups()["films"]["conversions"], [])

    def test_unset_soffice_is_located(self) -> None:
        with mock.patch.object(
            survey_convert, "locate_soffice", return_value=Path("soffice")
        ) as locate:
            self.main(soffice=retrolog_survey._UNSET, which=lambda name: None)
        locate.assert_called_once_with()
        self.assertEqual(
            self.report()["converters"],
            {"textutil": "absent", "libreoffice": "present"},
        )

    def test_present_textutil_and_absent_libreoffice(self) -> None:
        self.main()
        self.assertEqual(
            self.report()["converters"],
            {"textutil": "present", "libreoffice": "absent"},
        )


class SampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sample_report_is_reproduced(self) -> None:
        tree, out = self.base / "sample_tree", self.base / "sample_out"
        self.assertEqual(build_sample_tree.main(["--out", str(tree)]), EXIT_OK)
        with contextlib.redirect_stdout(io.StringIO()):
            code = retrolog_survey.main(
                ["--path", str(tree), "--out", str(out), "--no-convert"],
                run_git=fake_git(),
            )
        self.assertEqual(code, EXIT_OK)
        expected = _TOOL / "samples" / "expected_report.json"
        self.assertEqual((out / "report.json").read_bytes(), expected.read_bytes())

    def test_sample_builder_refuses_a_non_empty_out(self) -> None:
        (self.base / "notes.txt").write_bytes(b"")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = build_sample_tree.main(["--out", str(self.base)])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("must be missing or empty", stderr.getvalue())


class CompanionDocTest(unittest.TestCase):
    def test_written_for_a_stranger(self) -> None:
        for name in ("retrolog_survey.md", "samples/README.md"):
            text = (_TOOL / name).read_text(encoding="utf-8")
            self.assertIsNone(_PRIVATE_WORDS.search(text), name)

    def test_sample_commands_use_a_plain_interpreter(self) -> None:
        # Regression: the sample's commands named one machine's virtual environment.
        text = (_TOOL / "samples" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("venv", text)
        self.assertIn("python3 samples/build_sample_tree.py", text)
        self.assertIn("python3 retrolog_survey.py", text)


@unittest.skipUnless(
    sys.platform == "darwin" and shutil.which("textutil") and shutil.which("git"),
    "the live run needs macOS's textutil and git",
)
class LiveEndToEndTest(CliCase):
    """The real converter and the real git, on a tree whose .doc textutil wrote."""

    def build_tree(self) -> None:
        super().build_tree()
        (self.tree / "films" / "old.doc").unlink()
        code = survey_convert.real_run(
            ["textutil", "-convert", "doc", "-output",
             str(self.tree / "films" / "old.doc"), str(self.produce)],
            TIMEOUT,
        )  # fmt: skip
        self.assertEqual(code, 0)

    def test_live_survey(self) -> None:
        if any((folder / ".git").exists() for folder in self.base.parents):
            self.skipTest("the temporary directory lies inside a repository")
        git = functools.partial(
            survey_guard.run_git_with,
            extra_env={"GIT_CEILING_DIRECTORIES": str(self.base)},
        )
        before = snapshot(self.tree)
        code = self.main(
            "--timeout", str(int(TIMEOUT)),
            run=survey_convert.real_run, run_git=git, soffice=None,
            which=shutil.which,
        )  # fmt: skip
        self.assertEqual(code, EXIT_OK, self.stderr)
        self.assertEqual(snapshot(self.tree), before)
        conversion = as_dict(as_list(self.groups()["films"]["conversions"])[0])
        self.assertEqual(as_dict(conversion["textutil"])["status"], "ok")


if __name__ == "__main__":
    unittest.main()
