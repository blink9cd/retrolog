"""Tests for the retrolog_parse command line: what it reads, writes and returns."""

import contextlib
import functools
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL / "samples"))
sys.path.insert(0, str(_TOOL))
import build_parse_tree  # noqa: E402 - after the sys.path inserts above
import parse_fixtures  # noqa: E402 - after the sys.path inserts above
import retrolog_parse  # noqa: E402 - after the sys.path inserts above
import survey_convert  # noqa: E402 - retrolog_parse puts its folder on sys.path
import survey_guard  # noqa: E402 - retrolog_parse puts its folder on sys.path
from parse_cli_case import (  # noqa: E402 - after the sys.path inserts above
    EXIT_ERRORS,
    EXIT_OK,
    OVERRIDE_LINE,
    READ_TEXT,
    ParseCliCase,
    fake_git,
    snapshot,
)

_MARKER = ".retrolog_parse"
_OUTPUTS = sorted(
    [_MARKER, "book-2033.csv", "film-2033.csv", "review.json", "review.md"]
)
# Seconds a live converter or git call may take.
_TIMEOUT = 60.0


class CleanRunTest(ParseCliCase):
    def test_clean_tree_exits_0_and_writes_every_output(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), _OUTPUTS)
        self.assertEqual(self.stderr, "")

    def test_rows_reach_their_csv(self) -> None:
        self.main()
        films = (self.out / "film-2033.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [line.split(",")[1] for line in films[1:]],
            ["docx film", "first film", "second film"],
        )
        books = (self.out / "book-2033.csv").read_text(encoding="utf-8")
        self.assertIn("odd book,,,2033-12-31,true,,3,", books)

    def test_summary_counts_rows_and_each_kind_present(self) -> None:
        self.main()
        self.assertEqual(self.stdout.splitlines(), ["rows: 7", "override-applied: 1"])

    def test_doc_is_read_through_a_converted_copy(self) -> None:
        self.write_bytes("33/old seen.doc", b"a stand-in the fake converter replaces")
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertEqual(self.runner.calls, 1)
        films = (self.out / "film-2033.csv").read_text(encoding="utf-8")
        self.assertIn("33/old seen.doc", films)

    def test_other_kinds_are_ignored(self) -> None:
        self.write_bytes("33/cover seen.jpg", b"never opened")
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertNotIn("cover", (self.out / "review.json").read_text("utf-8"))

    def test_exclude_skips_matching_files(self) -> None:
        self.write("33/skip seen.txt", "not a list at all\n")
        self.assertEqual(self.main("--exclude", "*/SKIP*"), EXIT_OK, self.stderr)
        for name in ("review.json", "film-2033.csv"):
            self.assertNotIn("skip seen", (self.out / name).read_text("utf-8"))

    def test_max_bytes_is_applied(self) -> None:
        self.assertEqual(self.main("--max-bytes", "8"), EXIT_ERRORS)
        self.assertIn(("read-error", "33/seen.txt", 0), self.kinds())

    def test_unset_soffice_is_located(self) -> None:
        self.write_bytes("33/old seen.doc", b"a stand-in")
        with mock.patch.object(
            survey_convert, "locate_soffice", return_value=None
        ) as locate:
            argv = ["--path", str(self.tree), "--rules", str(self.rules)]
            with contextlib.redirect_stdout(io.StringIO()):
                code = retrolog_parse.main(
                    [*argv, "--out", str(self.out)], run=self.runner, run_git=fake_git()
                )
        self.assertEqual(code, EXIT_OK)
        locate.assert_called_once_with()


class ErrorRunTest(ParseCliCase):
    def test_override_mismatch_exits_1_and_still_writes(self) -> None:
        self.write("33/read.txt", READ_TEXT.replace(OVERRIDE_LINE, "other book - q"))
        self.assertEqual(self.main(), EXIT_ERRORS)
        self.assertIn(("override-mismatch", "33/read.txt", 4), self.kinds())
        self.assertTrue((self.out / "book-2033.csv").is_file())
        self.assertIn("1 error item(s)", self.stderr)

    def test_unreadable_file_is_a_read_error_and_the_run_continues(self) -> None:
        self.write_bytes("33/broken seen.docx", b"not a zip archive")
        self.assertEqual(self.main(), EXIT_ERRORS)
        self.assertIn(("read-error", "33/broken seen.docx", 0), self.kinds())
        self.assertTrue((self.out / "film-2033.csv").is_file())
        self.assertNotIn(str(self.base), (self.out / "review.json").read_text("utf-8"))

    def test_read_error_detail_names_the_failure_class(self) -> None:
        self.write_bytes("33/broken seen.docx", b"not a zip archive")
        self.main()
        items = self.review()["items"]
        assert isinstance(items, list)
        self.assertEqual(items[0]["detail"], "the file could not be read (BadDocx)")

    def test_unreadable_walk_entry_is_a_read_error(self) -> None:
        entry = retrolog_parse.FileEntry(
            "33/gone seen.txt", "33", "unreadable", 0, "PermissionError"
        )
        with mock.patch.object(retrolog_parse, "walk_source", return_value=[entry]):
            self.assertEqual(self.main(), EXIT_ERRORS)
        self.assertEqual(self.kinds()[0], ("read-error", "33/gone seen.txt", 0))
        self.assertIn(("rule-file-missing", "33/read.txt", 4), self.kinds())

    def test_a_rule_naming_a_file_not_in_the_tree_is_an_error(self) -> None:
        self.rules.write_bytes(parse_fixtures.rules_bytes())  # with its term block
        self.assertEqual(self.main(), EXIT_ERRORS)
        self.assertIn(("rule-file-missing", "31-32/seen.txt", 1), self.kinds())
        self.assertIn("rule-file-missing: 1", self.stdout.splitlines())
        self.assertIn("1 error item(s)", self.stderr)

    def test_a_rule_for_an_excluded_file_is_not_flagged(self) -> None:
        self.assertEqual(self.main("--exclude", "*/read.txt"), EXIT_OK, self.stderr)
        self.assertNotIn("rule-file-missing", [kind for kind, _, _ in self.kinds()])

    def test_file_with_no_medium_is_not_read(self) -> None:
        self.write_bytes("33/songs.doc", b"never converted")
        self.assertEqual(self.main(), EXIT_ERRORS)
        self.assertEqual(self.runner.calls, 0)
        self.assertIn(("no-medium", "33/songs.doc", 0), self.kinds())

    def test_source_tree_is_unchanged(self) -> None:
        self.write_bytes("33/broken seen.docx", b"not a zip archive")
        self.write_bytes("33/old seen.doc", b"a stand-in")
        before = snapshot(self.tree)
        self.main()
        self.assertEqual(snapshot(self.tree), before)
        self.assertEqual(self.runner.calls, 1)


class RerunTest(ParseCliCase):
    def test_second_run_replaces_and_prunes_csvs(self) -> None:
        self.assertEqual(self.main(), EXIT_OK)
        (self.out / "notes.txt").write_bytes(b"the operator's own note\n")
        (self.out / "keep.csv").write_bytes(b"no dash: not a CSV this tool writes\n")
        (self.out / "film-2001.csv").mkdir()  # a folder is never removed
        # Excluded rather than deleted: the override names it, and a rule naming a
        # file the run never found is an error.
        self.write("33/seen.txt", "third film - x\n")
        self.assertEqual(self.main("--exclude", "*/read.txt"), EXIT_OK, self.stderr)
        names = sorted(p.name for p in self.out.iterdir())
        self.assertNotIn("book-2033.csv", names)
        for kept in ("notes.txt", "keep.csv", "film-2001.csv", "film-2033.csv"):
            self.assertIn(kept, names)
        films = (self.out / "film-2033.csv").read_text(encoding="utf-8")
        self.assertIn("third film", films)
        self.assertNotIn("first film", films)

    def test_only_a_medium_csv_is_pruned(self) -> None:
        self.assertEqual(self.main(), EXIT_OK)
        for name in ("keep-me.csv", "notes-2020.csv", "book-1999.csv"):
            (self.out / name).write_bytes(b"medium,title\n")
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertTrue((self.out / "keep-me.csv").is_file())
        self.assertTrue((self.out / "notes-2020.csv").is_file())
        self.assertFalse((self.out / "book-1999.csv").exists())

    def test_a_stale_csv_link_is_removed_not_followed(self) -> None:
        self.main()
        target = self.base / "elsewhere-1.csv"
        target.write_bytes(b"kept\n")
        (self.out / "film-1999.csv").symlink_to(target)
        self.main()
        self.assertFalse((self.out / "film-1999.csv").is_symlink())
        self.assertEqual(target.read_bytes(), b"kept\n")


class SampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sample_output_is_reproduced(self) -> None:
        tree, out = self.base / "tree", self.base / "out"
        self.assertEqual(build_parse_tree.main(["--out", str(tree)]), EXIT_OK)
        rules = _TOOL / "samples" / "rules.example.toml"
        argv = ["--path", str(tree), "--rules", str(rules), "--out", str(out)]
        with contextlib.redirect_stdout(io.StringIO()):
            code = retrolog_parse.main(argv, run_git=fake_git())
        self.assertEqual(code, EXIT_OK)
        expected = _TOOL / "samples" / "expected"
        written = sorted(p.name for p in out.iterdir() if p.name != _MARKER)
        self.assertEqual(written, sorted(p.name for p in expected.iterdir()))
        for name in written:
            self.assertEqual(
                (out / name).read_bytes(), (expected / name).read_bytes(), name
            )

    def test_sample_builder_refuses_a_non_empty_out(self) -> None:
        (self.base / "notes.txt").write_bytes(b"")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = build_parse_tree.main(["--out", str(self.base)])
        self.assertEqual(code, 2)
        self.assertIn("must be missing or empty", stderr.getvalue())


@unittest.skipUnless(
    sys.platform == "darwin" and shutil.which("textutil") and shutil.which("git"),
    "the live run needs macOS's textutil and git",
)
class LiveEndToEndTest(ParseCliCase):
    """The real converter and the real git, on a .doc that textutil wrote."""

    def test_live_parse(self) -> None:
        if any((folder / ".git").exists() for folder in self.base.parents):
            self.skipTest("the temporary directory lies inside a repository")
        doc = self.tree / "33" / "old seen.doc"
        code = survey_convert.real_run(
            ["textutil", "-convert", "doc", "-output", str(doc), str(self.produce)],
            _TIMEOUT,
        )
        self.assertEqual(code, 0)
        git = functools.partial(
            survey_guard.run_git_with,
            extra_env={"GIT_CEILING_DIRECTORIES": str(self.base)},
        )
        before = snapshot(self.tree)
        code = self.main(run=survey_convert.real_run, run_git=git)
        self.assertEqual(code, EXIT_OK, self.stderr)
        self.assertEqual(snapshot(self.tree), before)
        films = (self.out / "film-2033.csv").read_text(encoding="utf-8")
        self.assertIn("33/old seen.doc", films)


if __name__ == "__main__":
    unittest.main()
