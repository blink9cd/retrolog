"""The command line end to end, with Letterboxd, time, and git replaced by fakes.

Each run reads import files written at test time into a temporary folder and writes
its report beside them; the committed sample is checked here too, so it cannot drift
from the tool.
"""

from __future__ import annotations

import contextlib
import io
import errno
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL))
sys.path.insert(0, str(_TOOL / "samples"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import build_probe_inputs  # noqa: E402 - after the sys.path inserts above
import retrolog_probe  # noqa: E402 - after the sys.path inserts above
from probe_fixtures import (  # noqa: E402 - after the sys.path inserts above
    MISSING,
    PRESENT,
    FakeClock,
    FakeHead,
    http_error,
    import_row,
    write_import,
)

_EXIT_OK, _EXIT_FOUND, _EXIT_USAGE = 0, 1, 2
_NOT_A_WORK_TREE, _A_WORK_TREE = 128, 0
_DENIED = PermissionError(errno.EACCES, os.strerror(errno.EACCES))
#: A name component past every common filesystem's 255-character limit.
_OVERLONG = "x" * 300


def _mode_500_locks_a_folder() -> bool:
    """True when ``chmod(0o500)`` stops this process creating a file in a folder.

    Root can write into a mode-500 folder, and on Windows ``chmod`` sets only the
    read-only attribute, which never stops a folder taking a new file.
    Probed rather than inferred from the platform, so the real-permission
    tests run wherever they can.
    """
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "probe"
        folder.mkdir()
        folder.chmod(0o500)
        try:
            (folder / "file").write_bytes(b"")
        except PermissionError:
            return True
        finally:
            folder.chmod(0o700)
    return False


def _an_overlong_name_is_unexaminable() -> bool:
    """True when ``lstat`` on an overlong name fails as other than a missing file.

    POSIX refuses it as ENAMETOOLONG, a plain OSError; Windows maps its error for a
    name too long (WinError 206) to FileNotFoundError, so there the product finds the
    folder missing instead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.lstat(Path(tmp) / _OVERLONG / "report.md")
        except FileNotFoundError:
            return False
        except OSError:
            return True
    return False


_MODE_500_LOCKS = _mode_500_locks_a_folder()
_NO_LOCK = "a mode-500 folder can still be written here (root, or Windows)"
_OVERLONG_IS_UNEXAMINABLE = _an_overlong_name_is_unexaminable()


class _CliCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.base = Path(holder.name).resolve()
        self.files = self.base / "export-out"
        self.out = self.base / "report.md"
        self.cache = self.base / "cache.json"
        self.time = FakeClock()
        self.git_answer = _NOT_A_WORK_TREE
        self.git_calls = 0

    def run_git(self, argv: Sequence[str], cwd: Path, timeout: float) -> int:
        self.git_calls += 1
        return self.git_answer

    def run_cli(self, head: FakeHead, *extra: str) -> tuple[int, str, str]:
        argv = ["--files", str(self.files), "--out", str(self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = retrolog_probe.main(
                    argv,
                    run_git=self.run_git,
                    head=head,
                    sleep=self.time.sleep,
                    clock=self.time.clock,
                )
            except SystemExit as stopped:  # argparse's own refusals
                code = stopped.code if isinstance(stopped.code, int) else -1
        return code, stdout.getvalue(), stderr.getvalue()

    def write_files(self) -> None:
        write_import(
            self.files,
            "letterboxd-001.csv",
            [import_row("10", "Placeholder Film One"), import_row("", "By Title")],
        )
        write_import(
            self.files, "letterboxd-002.csv", [import_row("20", "Placeholder Film Two")]
        )


class ExitCodeTest(_CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_files()

    def test_all_present_is_0_with_a_count_line_and_the_report_path(self) -> None:
        code, out, err = self.run_cli(FakeHead(PRESENT, PRESENT, PRESENT))
        self.assertEqual(code, _EXIT_OK, err)
        self.assertEqual(
            out,
            "2 distinct tmdbIDs: 2 present, 0 missing, 0 unknown "
            f"(0 from the cache, 3 requests sent)\nreport: {self.out}\n",
        )
        self.assertNotIn("Placeholder", out + err)
        self.assertIn("## Missing (0 rows)", self.out.read_text(encoding="utf-8"))

    def test_a_missing_film_is_1(self) -> None:
        code, _, err = self.run_cli(FakeHead(PRESENT, PRESENT, MISSING))
        self.assertEqual(code, _EXIT_FOUND)
        self.assertNotIn("Placeholder", err)
        report = self.out.read_text(encoding="utf-8")
        self.assertIn("| letterboxd-002.csv | 1 | 20 | Placeholder Film Two |", report)

    def test_an_unknown_film_is_1(self) -> None:
        code, _, _ = self.run_cli(FakeHead(PRESENT, PRESENT, http_error(404)))
        self.assertEqual(code, _EXIT_FOUND)
        self.assertIn("| HTTP 404 |", self.out.read_text(encoding="utf-8"))

    def test_a_failed_control_is_2_and_settles_nothing(self) -> None:
        code, out, err = self.run_cli(FakeHead(MISSING), "--cache", str(self.cache))
        self.assertEqual(code, _EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("the check itself is not working", err)
        self.assertIn("nothing is called missing", err)
        self.assertFalse(self.out.exists())
        # Saved once before the first request, to prove it can be written, and never
        # again: the failed control settles nothing.
        cache = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(cache["verdicts"], {})

    def test_the_control_id_is_an_option(self) -> None:
        head = FakeHead(PRESENT, PRESENT, PRESENT)
        code, _, _ = self.run_cli(head, "--control-id", "27")
        self.assertEqual(code, _EXIT_OK)
        self.assertEqual(head.urls()[0], "https://letterboxd.com/tmdb/27/")

    def test_the_default_control_is_19_and_the_interval_2_seconds(self) -> None:
        head = FakeHead(PRESENT, PRESENT, PRESENT)
        self.run_cli(head)
        self.assertEqual(head.urls()[0], "https://letterboxd.com/tmdb/19/")
        self.assertEqual(self.time.sleeps, [2.0, 2.0])
        self.assertEqual({timeout for _, timeout, _ in head.calls}, {30.0})

    def test_interval_and_timeout_are_options(self) -> None:
        head = FakeHead(PRESENT, PRESENT, PRESENT)
        self.run_cli(head, "--interval", "5", "--timeout", "9")
        self.assertEqual(self.time.sleeps, [5.0, 5.0])
        self.assertEqual({timeout for _, timeout, _ in head.calls}, {9.0})


class CacheCliTest(_CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_files()

    def test_the_cache_is_written_and_a_rerun_sends_nothing(self) -> None:
        self.run_cli(FakeHead(PRESENT, PRESENT, MISSING), "--cache", str(self.cache))
        self.assertEqual(
            json.loads(self.cache.read_text(encoding="utf-8")),
            {"schema": 1, "verdicts": {"10": "present", "20": "missing"}},
        )
        self.out.unlink()
        head = FakeHead()
        code, out, _ = self.run_cli(head, "--cache", str(self.cache))
        self.assertEqual(code, _EXIT_FOUND)
        self.assertEqual(head.calls, [])
        self.assertIn("(2 from the cache, 0 requests sent)", out)

    def test_cache_only_sends_nothing_and_calls_the_uncached_unknown(self) -> None:
        self.cache.write_text(
            json.dumps({"schema": 1, "verdicts": {"10": "present"}}),
            encoding="utf-8",
            newline="\n",
        )
        head = FakeHead()
        code, _, _ = self.run_cli(head, "--cache", str(self.cache), "--cache-only")
        self.assertEqual(code, _EXIT_FOUND)
        self.assertEqual(head.calls, [])
        self.assertIn("| not in the cache |", self.out.read_text(encoding="utf-8"))

    def test_refresh_sends_again(self) -> None:
        self.cache.write_text(
            json.dumps({"schema": 1, "verdicts": {"10": "missing", "20": "missing"}}),
            encoding="utf-8",
            newline="\n",
        )
        head = FakeHead(PRESENT, PRESENT, PRESENT)
        code, _, _ = self.run_cli(head, "--cache", str(self.cache), "--refresh")
        self.assertEqual(code, _EXIT_OK)
        self.assertEqual(len(head.calls), 3)

    def test_a_corrupt_cache_is_2_and_is_left_as_it_was(self) -> None:
        self.cache.write_bytes(b"{")
        code, _, err = self.run_cli(FakeHead(), "--cache", str(self.cache))
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("--cache is not a verdict cache", err)
        self.assertEqual(self.cache.read_bytes(), b"{")


class UsageTest(_CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_files()

    def assert_usage(self, *extra: str, says: str = "") -> None:
        code, _, message = self.run_cli(FakeHead(), *extra)
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn(says, message)
        self.assertFalse(self.out.exists())

    def test_refresh_with_cache_only(self) -> None:
        self.assert_usage(
            "--cache", str(self.cache), "--refresh", "--cache-only", says="--refresh"
        )

    def test_refresh_or_cache_only_without_a_cache(self) -> None:
        for flag in ("--refresh", "--cache-only"):
            with self.subTest(flag=flag):
                self.assert_usage(flag, says="--cache")

    def test_an_interval_below_one_second(self) -> None:
        self.assert_usage("--interval", "0.5", says="at least 1")

    def test_a_timeout_of_zero(self) -> None:
        self.assert_usage("--timeout", "0", says="greater than 0")

    def test_a_control_id_the_rule_refuses(self) -> None:
        self.assert_usage("--control-id", "019", says="TMDb id")

    def test_a_missing_files_folder(self) -> None:
        self.files = self.base / "absent"
        self.assert_usage(says="--files: no such folder")

    def test_a_cache_in_a_missing_folder(self) -> None:
        self.assert_usage(
            "--cache", str(self.base / "absent" / "c.json"), says="--cache"
        )

    def test_the_cache_and_the_report_at_one_path(self) -> None:
        self.assert_usage("--cache", str(self.out), says="--cache")


class OutGuardTest(_CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_files()

    def test_an_existing_report_is_refused_before_any_request(self) -> None:
        self.out.write_text("earlier", encoding="utf-8", newline="\n")
        head = FakeHead()
        code, _, err = self.run_cli(head)
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("refused --out: it exists", err)
        self.assertEqual(head.calls, [])
        self.assertEqual(self.out.read_text(encoding="utf-8"), "earlier")

    def test_a_dangling_link_counts_as_existing(self) -> None:
        self.out.symlink_to(self.base / "nowhere")
        code, _, _ = self.run_cli(FakeHead())
        self.assertEqual(code, _EXIT_USAGE)

    def test_a_report_inside_a_git_work_tree_is_refused_before_any_request(
        self,
    ) -> None:
        self.git_answer = _A_WORK_TREE
        head = FakeHead()
        code, _, err = self.run_cli(head)
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("refused --out: inside a git work tree", err)
        self.assertEqual(head.calls, [])
        self.assertGreater(self.git_calls, 0)

    def test_a_report_inside_the_files_folder_is_refused_before_any_request(
        self,
    ) -> None:
        for out in (self.files / "report.md", self.files / "sub" / "report.md"):
            with self.subTest(out=out.name):
                self.out = out
                (self.files / "sub").mkdir(exist_ok=True)
                head = FakeHead()
                code, _, err = self.run_cli(head)
                self.assertEqual(code, _EXIT_USAGE)
                self.assertIn("refused --out: inside the source tree", err)
                self.assertNotIn(str(self.base), err)
                self.assertEqual(head.calls, [])
                self.assertFalse(out.exists())

    def test_a_report_under_a_dotted_files_path_is_refused(self) -> None:
        # --files given as a path with a ".." still resolves to the same folder.
        (self.base / "elsewhere").mkdir()
        self.files = self.base / "elsewhere" / ".." / "export-out"
        self.out = self.base / "export-out" / "report.md"
        code, _, err = self.run_cli(FakeHead())
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("refused --out: inside the source tree", err)

    def test_a_report_named_beside_the_files_folder_is_accepted(self) -> None:
        self.out = self.base / "export-out-report.md"
        code, _, err = self.run_cli(FakeHead(PRESENT, PRESENT, PRESENT))
        self.assertEqual(code, _EXIT_OK, err)

    def test_a_report_that_appears_during_the_run_is_not_overwritten(self) -> None:
        def head(url: str, timeout: float, headers: object) -> object:
            self.out.write_text("raced", encoding="utf-8", newline="\n")
            return PRESENT

        code, _, err = self.run_cli(head)  # type: ignore[arg-type]
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("refused --out: it exists", err)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "raced")

    def test_a_report_in_a_missing_folder_is_refused_before_any_request(self) -> None:
        self.out = self.base / "no-such-folder" / "report.md"
        head = FakeHead()
        code, _, err = self.run_cli(head)
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("refused --out: its folder does not exist", err)
        self.assertEqual(head.calls, [])

    @unittest.skipUnless(_MODE_500_LOCKS, _NO_LOCK)
    def test_a_report_that_cannot_be_written_is_2_naming_no_path(self) -> None:
        locked = self.base / "locked"
        locked.mkdir()
        self.out = locked / "report.md"
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        code, _, err = self.run_cli(FakeHead(PRESENT, PRESENT, PRESENT))
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("could not write --out: PermissionError", err)
        self.assertNotIn(str(self.base), err)

    def test_a_report_write_refused_by_permission_is_2_naming_no_path(self) -> None:
        # The portable twin of the mode-500 test: the refusal is raised directly, so
        # the write-error path is proven on a host where chmod cannot lock a folder.
        with mock.patch.object(retrolog_probe, "write_atomic", side_effect=_DENIED):
            code, _, err = self.run_cli(FakeHead(PRESENT, PRESENT, PRESENT))
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("could not write --out: PermissionError", err)
        self.assertNotIn(str(self.base), err)
        self.assertFalse(self.out.exists())

    def test_an_overlong_out_is_2_naming_no_path(self) -> None:
        # A name component past the filesystem's limit: stat itself fails. The reason
        # is the host's (see _an_overlong_name_is_unexaminable); the refusal is not.
        self.out = self.base / _OVERLONG / "report.md"
        head = FakeHead()
        code, _, err = self.run_cli(head)
        self.assertEqual(code, _EXIT_USAGE)
        if _OVERLONG_IS_UNEXAMINABLE:
            self.assertIn("could not write --out: OSError", err)
        else:
            self.assertIn("refused --out: its folder does not exist", err)
        self.assertNotIn("xxx", err)
        self.assertEqual(head.calls, [])

    def test_an_out_that_cannot_be_examined_is_2_naming_no_path(self) -> None:
        # The portable form of the overlong name: lstat raising other than a missing
        # file, which a Windows host never does for that name.
        real_lstat = os.lstat
        too_long = OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG))

        def refuse_out(path: object, *args: object, **kwargs: object) -> object:
            if path == self.out:
                raise too_long
            return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

        head = FakeHead()
        with mock.patch.object(retrolog_probe.os, "lstat", refuse_out):
            code, _, err = self.run_cli(head)
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("could not write --out: OSError", err)
        self.assertNotIn(str(self.base), err)
        self.assertEqual(head.calls, [])

    @unittest.skipUnless(_MODE_500_LOCKS, _NO_LOCK)
    def test_a_cache_that_cannot_be_written_is_2_before_any_request(self) -> None:
        folder = self.base / "cache-folder"
        folder.mkdir()
        folder.chmod(0o500)
        self.addCleanup(folder.chmod, 0o700)
        cache = str(folder / "cache.json")
        head = FakeHead(PRESENT, PRESENT)
        code, _, err = self.run_cli(head, "--cache", cache)
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("could not write --cache: PermissionError", err)
        self.assertFalse(self.out.exists())
        self.assertEqual(head.urls(), [])

    def test_a_cache_save_refused_by_permission_is_2_before_any_request(self) -> None:
        # The portable twin of the mode-500 cache test, as for the report above.
        head = FakeHead(PRESENT, PRESENT)
        with mock.patch.object(retrolog_probe, "save_cache", side_effect=_DENIED):
            code, _, err = self.run_cli(head, "--cache", str(self.cache))
        self.assertEqual(code, _EXIT_USAGE)
        self.assertIn("could not write --cache: PermissionError", err)
        self.assertNotIn(str(self.base), err)
        self.assertFalse(self.out.exists())
        self.assertEqual(head.urls(), [])


class HelpTest(unittest.TestCase):
    def help_text(self) -> str:
        return retrolog_probe.build_parser().format_help()

    def test_help_states_the_terms_and_what_is_sent(self) -> None:
        text = " ".join(self.help_text().split())
        for phrase in (
            "Letterboxd's terms of use forbid automated access",
            "one request per film",
            "your own decision",
            "no cookies",
            "never read as missing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_help_shows_live_defaults(self) -> None:
        text = " ".join(self.help_text().split())
        for default in ("(default: 19)", "(default: 2.0)", "(default: 30.0)"):
            with self.subTest(default=default):
                self.assertIn(default, text)


class SampleTest(unittest.TestCase):
    def test_the_committed_report_is_what_the_tool_writes_now(self) -> None:
        with tempfile.TemporaryDirectory() as holder:
            work = Path(holder).resolve()
            build_probe_inputs.write_inputs(work)
            argv = ["--files", str(work / "files"), "--cache", str(work / "cache.json")]
            argv += ["--cache-only", "--out", str(work / "report.md")]
            with contextlib.redirect_stdout(io.StringIO()):
                code = retrolog_probe.main(argv, run_git=lambda *_: _NOT_A_WORK_TREE)
            self.assertEqual(code, _EXIT_FOUND)
            expected = _TOOL / "samples" / "expected" / "report.md"
            self.assertEqual((work / "report.md").read_bytes(), expected.read_bytes())


if __name__ == "__main__":
    unittest.main()
