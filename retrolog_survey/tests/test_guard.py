"""Tests for survey_guard: where a survey may write its reports, and where it may not."""

import errno
import functools
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
import survey_guard  # noqa: E402

# Seconds a fake or live git call may take.
_TIMEOUT = 30.0
# git's exit code outside any repository.
_NOT_A_REPO = 128
# The git question the guard asks.
_INSIDE_WORK_TREE = ["rev-parse", "--is-inside-work-tree"]


class FakeGit:
    """Stands in for git: records each call's argv and cwd and returns a fixed code."""

    def __init__(self, code: int = _NOT_A_REPO) -> None:
        self.code = code
        self.calls: list[tuple[list[str], Path, float]] = []

    def __call__(self, argv: Sequence[str], cwd: Path, timeout: float) -> int:
        self.calls.append((list(argv), cwd, timeout))
        return self.code


class RaisingGit:
    """A git call that raises instead of returning: git missing, or a timeout."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(self, argv: Sequence[str], cwd: Path, timeout: float) -> int:
        raise self.error


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        # A cleanup, not tearDown: tearDown runs before every cleanup, so a test's own
        # later-registered chdir back would run too late, and Windows refuses to delete
        # the directory a process is standing in. Cleanups run last-registered first.
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.source = self.root / "inputs"
        self.source.mkdir()

    def make_symlink(self, link: Path, target: Path) -> Path:
        """Create ``link`` pointing at ``target``, skipping where symlinks are denied."""
        try:
            link.symlink_to(target)
        except OSError:  # pragma: no cover - only where symlinks need a privilege
            self.skipTest("this platform does not allow creating symlinks")
        return link

    def check(self, out: Path, git: survey_guard.GitRunner | None = None) -> None:
        survey_guard.check_out_dir(
            out, self.source, run_git=git or FakeGit(), timeout=_TIMEOUT
        )

    def assert_refused(
        self, out: Path, rule: str, git: survey_guard.GitRunner | None = None
    ) -> None:
        with self.assertRaises(survey_guard.OutRefused) as caught:
            self.check(out, git)
        self.assertEqual(str(caught.exception), rule)
        self.assertNotIn(str(self.root), str(caught.exception))


class SourceTreeRuleTest(_TempDirCase):
    def test_out_equal_to_source_is_refused(self) -> None:
        self.assert_refused(self.source, "inside the source tree")

    def test_out_under_source_is_refused(self) -> None:
        self.assert_refused(self.source / "a" / "report", "inside the source tree")

    def test_out_through_a_symlink_into_source_is_refused(self) -> None:
        link = self.root / "shortcut"
        link.symlink_to(self.source, target_is_directory=True)
        self.assert_refused(link / "report", "inside the source tree")

    def test_source_rule_is_checked_before_git(self) -> None:
        git = FakeGit(code=0)
        self.assert_refused(self.source / "report", "inside the source tree", git)
        self.assertEqual(git.calls, [])

    def test_case_variant_of_source_is_refused_by_identity(self) -> None:
        probe = self.root / "CaseProbe"
        probe.mkdir()
        if not (self.root / "caseprobe").exists():
            self.skipTest("the temporary filesystem is case-sensitive")
        self.assert_refused(self.root / "Inputs" / "report", "inside the source tree")

    def test_missing_source_falls_back_to_the_path_compare(self) -> None:
        self.source.rmdir()
        self.check(self.root / "report")
        self.assert_refused(self.source / "report", "inside the source tree")


class GitRuleTest(_TempDirCase):
    def test_inside_a_work_tree_is_refused(self) -> None:
        self.assert_refused(self.root / "report", "inside a git work tree", FakeGit(0))

    def test_git_runs_from_the_nearest_existing_ancestor(self) -> None:
        git = FakeGit()
        (self.root / "reports").mkdir()
        self.check(self.root / "reports" / "new" / "deeper", git)
        self.assertEqual(
            git.calls,
            [(["rev-parse", "--is-inside-work-tree"], self.root / "reports", _TIMEOUT)],
        )

    def test_git_runs_in_out_itself_when_it_exists(self) -> None:
        git = FakeGit()
        out = self.root / "reports"
        out.mkdir()
        self.check(out, git)
        self.assertEqual(git.calls[0][1], out)

    def test_git_missing_counts_as_not_a_work_tree(self) -> None:
        self.check(self.root / "report", RaisingGit(FileNotFoundError("git")))

    def test_git_timeout_counts_as_not_a_work_tree(self) -> None:
        error = subprocess.TimeoutExpired(["git"], _TIMEOUT)
        self.check(self.root / "report", RaisingGit(error))


class NonEmptyRuleTest(_TempDirCase):
    def test_non_empty_dir_without_marker_is_refused(self) -> None:
        out = self.root / "reports"
        out.mkdir()
        (out / "notes.txt").write_text("kept", encoding="utf-8", newline="\n")
        self.assert_refused(out, "not empty and not a previous survey output")

    def test_existing_file_is_refused(self) -> None:
        out = self.root / "reports"
        out.write_text("a file", encoding="utf-8", newline="\n")
        self.assert_refused(out, "not a directory")

    def test_empty_existing_dir_is_accepted(self) -> None:
        out = self.root / "reports"
        out.mkdir()
        self.check(out)

    def test_previous_output_with_marker_is_accepted(self) -> None:
        out = self.root / "reports"
        survey_guard.prepare_out_dir(out)
        (out / "summary.md").write_text("old report", encoding="utf-8", newline="\n")
        self.check(out)


class GitScanTest(_TempDirCase):
    """The ``.git`` scan that runs beside git, so a git that fails cannot accept a repo."""

    def test_git_failing_with_a_git_dir_above_is_refused(self) -> None:
        (self.root / ".git").mkdir()
        self.assert_refused(
            self.root / "reports", "inside a git work tree", FakeGit(_NOT_A_REPO)
        )

    def test_git_file_above_is_refused(self) -> None:
        project = self.root / "project"
        project.mkdir()
        (project / ".git").write_text(
            "gitdir: elsewhere\n", encoding="utf-8", newline="\n"
        )
        self.assert_refused(project / "reports" / "deep", "inside a git work tree")

    def test_broken_git_symlink_above_is_refused(self) -> None:
        self.make_symlink(self.root / ".git", self.root / "missing")
        self.assert_refused(self.root / "reports", "inside a git work tree")

    def test_out_inside_a_git_directory_is_refused(self) -> None:
        git_dir = self.root / "store" / ".git"
        git_dir.mkdir(parents=True)
        self.assert_refused(git_dir / "objects", "inside a git work tree")

    def test_out_under_a_git_name_not_yet_created_is_refused(self) -> None:
        self.assert_refused(self.root / ".git" / "objects", "inside a git work tree")

    def test_git_missing_and_no_git_anywhere_is_accepted(self) -> None:
        self.check(self.root / "reports", RaisingGit(FileNotFoundError("git")))


class OutsideWorkTreeTest(_TempDirCase):
    """``check_outside_work_tree``: the git rule alone, for a folder a file goes into."""

    def check_folder(self, folder: Path, git: survey_guard.GitRunner) -> None:
        survey_guard.check_outside_work_tree(folder, run_git=git, timeout=_TIMEOUT)

    def assert_folder_refused(
        self, folder: Path, rule: str, git: survey_guard.GitRunner
    ) -> None:
        with self.assertRaises(survey_guard.OutRefused) as caught:
            self.check_folder(folder, git)
        self.assertEqual(str(caught.exception), rule)
        self.assertIsNone(caught.exception.__cause__)

    def test_a_folder_holding_other_files_is_accepted(self) -> None:
        """No marker and no emptiness rule: the folder is the operator's own."""
        (self.source / "notes.txt").write_text("kept", encoding="utf-8", newline="\n")
        git = FakeGit(_NOT_A_REPO)
        self.check_folder(self.source, git)
        self.assertEqual(git.calls, [(_INSIDE_WORK_TREE, self.source, _TIMEOUT)])
        self.check_folder(self.root / "not" / "yet", FakeGit(_NOT_A_REPO))

    def test_inside_a_work_tree_is_refused(self) -> None:
        self.assert_folder_refused(self.source, "inside a git work tree", FakeGit(0))

    def test_a_git_entry_above_is_refused_when_git_fails(self) -> None:
        (self.root / ".git").mkdir()
        self.assert_folder_refused(
            self.source, "inside a git work tree", RaisingGit(FileNotFoundError("git"))
        )

    def test_a_symlink_loop_is_uninspectable(self) -> None:
        self.make_symlink(self.root / "a", self.root / "b")
        self.make_symlink(self.root / "b", self.root / "a")
        self.assert_folder_refused(
            self.root / "a", "cannot inspect the output location", FakeGit()
        )

    def test_an_error_examining_it_names_no_path(self) -> None:
        denied = PermissionError(errno.EACCES, os.strerror(errno.EACCES), "x")
        with mock.patch.object(Path, "resolve", side_effect=denied):
            self.assert_folder_refused(
                self.source, "cannot inspect the output location", FakeGit()
            )


class MarkerSymlinkTest(_TempDirCase):
    def test_symlinked_marker_is_not_a_previous_output(self) -> None:
        out = self.root / "reports"
        out.mkdir()
        target = self.root / "elsewhere.txt"
        target.write_text("precious", encoding="utf-8", newline="\n")
        self.make_symlink(out / survey_guard.MARKER, target)
        self.assert_refused(out, "not empty and not a previous survey output")

    def test_prepare_refuses_to_follow_a_symlinked_marker(self) -> None:
        out = self.root / "reports"
        out.mkdir()
        target = self.root / "elsewhere.txt"
        target.write_text("precious", encoding="utf-8", newline="\n")
        self.make_symlink(out / survey_guard.MARKER, target)
        with self.assertRaises(survey_guard.OutRefused) as caught:
            survey_guard.prepare_out_dir(out)
        self.assertEqual(str(caught.exception), "cannot inspect the output location")
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(target.read_text(encoding="utf-8"), "precious")


class UninspectableTest(_TempDirCase):
    def test_symlink_loop_is_refused_without_a_path(self) -> None:
        self.make_symlink(self.root / "a", self.root / "b")
        self.make_symlink(self.root / "b", self.root / "a")
        self.assert_refused(
            self.root / "a" / "report", "cannot inspect the output location"
        )

    def test_a_listing_the_os_refuses_is_refused_without_a_path(self) -> None:
        """The OS's refusal raised directly, so this runs where chmod cannot cause it."""
        out = self.root / "reports"
        out.mkdir()
        (out / "kept.txt").write_text("kept", encoding="utf-8", newline="\n")
        real_iterdir = Path.iterdir

        def iterdir(path: Path) -> object:
            if path == out:
                raise PermissionError(
                    errno.EACCES, os.strerror(errno.EACCES), str(path)
                )
            return real_iterdir(path)

        with (
            mock.patch.object(Path, "iterdir", iterdir),
            self.assertRaises(survey_guard.OutRefused) as caught,
        ):
            self.check(out)
        self.assertEqual(str(caught.exception), "cannot inspect the output location")
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    @unittest.skipIf(
        sys.platform == "win32" or os.geteuid() == 0,
        "chmod cannot deny the superuser, and on Windows sets only a read-only attribute",
    )
    def test_unreadable_dir_is_refused_without_a_path(self) -> None:
        out = self.root / "reports"
        out.mkdir()
        (out / "kept.txt").write_text("kept", encoding="utf-8", newline="\n")
        out.chmod(0)
        try:
            with self.assertRaises(survey_guard.OutRefused) as caught:
                self.check(out)
        finally:
            # Restored before the cleanup, which must be able to remove the directory.
            out.chmod(0o700)
        self.assertEqual(str(caught.exception), "cannot inspect the output location")
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)


class PathFormsTest(_TempDirCase):
    def test_source_through_a_symlink_with_out_under_its_target(self) -> None:
        self.source = self.make_symlink(self.root / "shortcut", self.root / "inputs")
        self.assert_refused(self.root / "inputs" / "report", "inside the source tree")

    def test_relative_out_and_source(self) -> None:
        here = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, here)
        self.source = Path("inputs")
        self.assert_refused(Path("inputs") / "report", "inside the source tree")
        self.check(Path("reports"))

    def test_out_an_ancestor_of_source_is_refused(self) -> None:
        self.assert_refused(self.root, "not empty and not a previous survey output")


class PrepareOutDirTest(_TempDirCase):
    def test_new_dir_is_accepted_then_created_with_marker(self) -> None:
        out = self.root / "reports" / "first"
        self.check(out)
        survey_guard.prepare_out_dir(out)
        self.assertTrue((out / survey_guard.MARKER).is_file())

    def test_prepare_is_repeatable(self) -> None:
        out = self.root / "reports"
        survey_guard.prepare_out_dir(out)
        survey_guard.prepare_out_dir(out)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [survey_guard.MARKER])


class ToolKeywordTest(_TempDirCase):
    """``tool=`` lets a sibling tool share the guard with a marker of its own."""

    def test_tool_keyword_names_its_own_marker(self) -> None:
        out = self.root / "out"
        survey_guard.prepare_out_dir(out, tool="retrolog_parse")
        marker = out / ".retrolog_parse"
        self.assertEqual(
            marker.read_bytes(), b"This directory holds retrolog_parse output.\n"
        )
        self.assertFalse((out / survey_guard.MARKER).exists())

    def test_one_tools_output_is_refused_as_the_others(self) -> None:
        out = self.root / "out"
        survey_guard.prepare_out_dir(out)  # the survey's marker
        (out / "report.md").write_text("x", encoding="utf-8", newline="\n")
        with self.assertRaises(survey_guard.OutRefused) as caught:
            survey_guard.check_out_dir(
                out,
                self.source,
                run_git=FakeGit(),
                timeout=_TIMEOUT,
                tool="retrolog_parse",
            )
        self.assertEqual(
            str(caught.exception), "not empty and not a previous retrolog_parse output"
        )

    def test_tools_own_previous_output_is_accepted(self) -> None:
        out = self.root / "out"
        survey_guard.prepare_out_dir(out, tool="retrolog_parse")
        (out / "rows.csv").write_text("x", encoding="utf-8", newline="\n")
        survey_guard.check_out_dir(
            out, self.source, run_git=FakeGit(), timeout=_TIMEOUT, tool="retrolog_parse"
        )


class ScrubbedEnvTest(unittest.TestCase):
    def test_drops_the_discovery_variables_and_keeps_the_rest(self) -> None:
        environ = {name: "x" for name in survey_guard.GIT_DISCOVERY_VARIABLES}
        environ.update(PATH="/usr/bin", LANG="C")
        self.assertEqual(
            survey_guard.scrubbed_env(environ), {"PATH": "/usr/bin", "LANG": "C"}
        )

    def test_names_the_five_variables(self) -> None:
        self.assertEqual(
            set(survey_guard.GIT_DISCOVERY_VARIABLES),
            {
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_CEILING_DIRECTORIES",
                "GIT_DISCOVERY_ACROSS_FILESYSTEM",
                "GIT_COMMON_DIR",
            },
        )


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class RealGitLiveTest(_TempDirCase):
    """Runs the real git, stopped at the temporary directory by a ceiling, so the
    result does not depend on where the system keeps temporary files.
    """

    def setUp(self) -> None:
        super().setUp()
        self.git = functools.partial(
            survey_guard.run_git_with,
            extra_env={"GIT_CEILING_DIRECTORIES": str(self.root)},
        )

    def test_out_inside_a_fresh_repository_is_refused(self) -> None:
        repo = self.root / "project"
        repo.mkdir()
        self.assertEqual(self.git(["init", "--quiet"], repo, _TIMEOUT), 0)
        self.assert_refused(repo / "reports", "inside a git work tree", self.git)

    def test_plain_directory_is_accepted(self) -> None:
        if any((folder / ".git").exists() for folder in self.root.parents):
            self.skipTest("the temporary directory lies inside a repository")
        self.check(self.root / "reports", self.git)

    def test_real_git_answers_outside_a_repository(self) -> None:
        if any((folder / ".git").exists() for folder in self.root.parents):
            self.skipTest("the temporary directory lies inside a repository")
        code = survey_guard.real_git(_INSIDE_WORK_TREE, self.root, _TIMEOUT)
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
