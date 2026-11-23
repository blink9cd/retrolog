"""The memory bound fails closed: a tree the readers may not read is a failed watch, never "gone".

A sandbox that denies ``process-info*`` once made every libproc call fail with ``EPERM``, which
the reader read as "every process is gone": footprint 0, no children, and a 200 MiB child
finished under a 50 MiB bound. These suites pin the line between gone (``ESRCH`` on macOS, a
``/proc/<pid>`` that no longer exists on Linux) and unreadable (anything else), on fakes that
run on every host, then through ``run_bounded``, then live under ``sandbox-exec`` on macOS.
"""

from __future__ import annotations

import errno
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bounded_run
import boundedrun_memory
import boundedrun_memread
from _libproc_fake import fake_libproc, reader_over

_PAGE = 4096

#: The sandbox profile that reproduces the fail-open: everything allowed but process info.
_DENY_PROCESS_INFO = "(version 1)(allow default)(deny process-info*)"

#: Bound on a live run here: far past what each takes, so only a hang reaches it.
_LIVE_TIMEOUT = 60.0


class _Everything:
    """A collection holding every pid, for a fake that denies whatever is asked."""

    def __contains__(self, item: object) -> bool:
        return True


class LibprocDeniedTests(unittest.TestCase):
    """``EPERM`` is a read failure on every call; only ``ESRCH`` reads as gone."""

    def setUp(self) -> None:
        self.reader = reader_over(fake_libproc({7: _PAGE}, {}, {7: 7}, denied={9}))

    def test_a_denied_footprint_raises_with_its_errno(self) -> None:
        with self.assertRaises(OSError) as caught:
            self.reader.footprint(9)
        self.assertEqual(caught.exception.errno, errno.EPERM)

    def test_denied_children_raise(self) -> None:
        with self.assertRaises(OSError):
            self.reader.children(9)

    def test_a_denied_group_raises(self) -> None:
        with self.assertRaises(OSError):
            self.reader.group(9)

    def test_a_denied_group_listing_raises(self) -> None:
        with self.assertRaises(OSError):
            self.reader.group_members(9)

    def test_a_gone_pid_still_reads_as_gone(self) -> None:
        self.assertEqual(self.reader.footprint(8), 0)
        self.assertEqual(self.reader.children(8), [])
        self.assertIsNone(self.reader.group(8))
        self.assertEqual(self.reader.group_members(8), [])

    def test_an_earlier_calls_errno_never_fails_a_later_call(self) -> None:
        with self.assertRaises(OSError):
            self.reader.footprint(9)
        self.assertEqual(self.reader.children(7), [])
        self.assertEqual(self.reader.group(7), 7)

    def test_a_denied_tree_fails_the_walk(self) -> None:
        reader = reader_over(fake_libproc({}, {}, denied=_Everything()))  # type: ignore[arg-type]
        with self.assertRaises(OSError):
            boundedrun_memread.tree_footprint(10, reader)


class ListingVerdictTests(unittest.TestCase):
    """A listing's count and errno: what is a list, what is gone, what is a failure."""

    def test_a_listing_with_entries_stands(self) -> None:
        self.assertFalse(boundedrun_memread.listing_failed(3, errno.EPERM))

    def test_an_empty_listing_fails_only_on_an_errno_other_than_gone(self) -> None:
        self.assertFalse(boundedrun_memread.listing_failed(0, 0))
        self.assertFalse(boundedrun_memread.listing_failed(0, errno.ESRCH))
        self.assertTrue(boundedrun_memread.listing_failed(0, errno.EPERM))

    def test_a_negative_listing_fails_unless_the_pid_is_gone(self) -> None:
        self.assertFalse(boundedrun_memread.listing_failed(-1, errno.ESRCH))
        self.assertTrue(boundedrun_memread.listing_failed(-1, 0))
        self.assertTrue(boundedrun_memread.listing_failed(-1, errno.EPERM))


def _lay_out(root: Path, pid: int) -> Path:
    """``<root>/<pid>`` with one thread, a readable stat and statm; return its folder."""
    folder = root / str(pid)
    (folder / "task" / str(pid)).mkdir(parents=True)
    (folder / "statm").write_text("9 2 0 0 0 0 0", encoding="utf-8", newline="\n")
    (folder / "stat").write_text(f"{pid} (x) S 1 {pid}", encoding="utf-8", newline="\n")
    (folder / "task" / str(pid) / "children").write_text(
        "", encoding="utf-8", newline="\n"
    )
    return folder


class ProcfsDeniedTests(unittest.TestCase):
    """A file of a process whose ``/proc`` folder still exists must be read, or it fails."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.folder = _lay_out(self.root, 7)
        self.reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)

    @staticmethod
    def _make_unreadable(path: Path) -> None:
        """Replace the file at *path* with a folder, which no read of it can open."""
        path.unlink()
        path.mkdir()

    def test_an_unreadable_statm_of_a_live_process_raises(self) -> None:
        self._make_unreadable(self.folder / "statm")
        with self.assertRaises(OSError):
            self.reader.footprint(7)

    def test_an_unreadable_children_file_of_a_live_thread_raises(self) -> None:
        self._make_unreadable(self.folder / "task" / "7" / "children")
        with self.assertRaises(OSError):
            self.reader.children(7)

    def test_a_thread_that_ended_mid_read_is_skipped(self) -> None:
        gone = self.folder / "task" / "99" / "children"
        live = self.folder / "task" / "7" / "children"
        live.write_text("21", encoding="utf-8", newline="\n")
        with mock.patch.object(Path, "glob", return_value=[gone, live]):
            self.assertEqual(self.reader.children(7), [21])

    def test_an_unreadable_stat_of_a_live_process_raises(self) -> None:
        self._make_unreadable(self.folder / "stat")
        with self.assertRaises(OSError):
            self.reader.group(7)

    def test_a_gone_process_still_reads_as_gone(self) -> None:
        self.assertEqual(self.reader.footprint(8), 0)
        self.assertIsNone(self.reader.group(8))


#: A child that runs a few seconds and exits 0: under a fail-open bound it "passes".
_SHORT_SLEEP = "import time; time.sleep(3)"


@unittest.skipIf(sys.platform == "win32", "memory_limit= is refused on Windows")
class DeniedRunTests(unittest.TestCase):
    """Through ``run_bounded``: a reader denied everything kills the run at once."""

    def test_a_denied_reader_raises_watch_failed_instead_of_a_clean_exit(self) -> None:
        def denied() -> boundedrun_memread.LibprocReader:
            return reader_over(fake_libproc({}, {}, denied=_Everything()))  # type: ignore[arg-type]

        started = time.monotonic()
        with (
            mock.patch.dict(boundedrun_memory._READERS, {sys.platform: denied}),
            self.assertRaises(bounded_run.MemoryWatchFailed) as caught,
        ):
            bounded_run.run_bounded(
                [sys.executable, "-c", _SHORT_SLEEP],
                timeout=_LIVE_TIMEOUT,
                memory_limit=1 << 30,
            )
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertLess(time.monotonic() - started, 3.0, "the child ran on unwatched")


#: Run inside the sandbox: bound a 200 MiB child at 50 MiB, and print what the bound did.
_SANDBOXED_PARENT = """
import sys
sys.path.insert(0, sys.argv[1])
import bounded_run
child = "import time\\nheld = b'x' * (200 << 20)\\ntime.sleep(1)\\n"
try:
    done = bounded_run.run_bounded(
        [sys.executable, "-c", child], timeout=60, memory_limit=50 << 20
    )
except bounded_run.MemoryWatchFailed:
    print("WATCH_FAILED")
except bounded_run.MemoryLimitExceeded:
    print("MEMORY")
else:
    print("EXITED", done.returncode)
"""


@unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS's")
@unittest.skipIf(shutil.which("sandbox-exec") is None, "no sandbox-exec on PATH")
class LiveSandboxTests(unittest.TestCase):
    """The fail-open's reproduction, live: under a process-info denial the bound fails closed."""

    def test_a_sandbox_denying_process_info_fails_the_watch(self) -> None:
        tool_dir = str(Path(bounded_run.__file__).resolve().parent)
        done = bounded_run.run_bounded(
            [
                "sandbox-exec",
                "-p",
                _DENY_PROCESS_INFO,
                sys.executable,
                "-c",
                _SANDBOXED_PARENT,
                tool_dir,
            ],
            timeout=_LIVE_TIMEOUT,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "WATCH_FAILED", done.stderr)


@unittest.skipUnless(sys.platform == "darwin", "libproc is macOS's")
class LiveZombieTests(unittest.TestCase):
    """An exited, unreaped child is not an error: libproc reads it, and its group is gone."""

    def test_a_zombie_reads_zero_and_no_group(self) -> None:
        bound = boundedrun_memory.memory_bound(1, "darwin")
        assert bound is not None
        child = subprocess.Popen(["/usr/bin/true"], start_new_session=True)
        try:
            deadline = time.monotonic() + _LIVE_TIMEOUT
            while bound.reader.group(child.pid) is not None:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertEqual(bound.reader.footprint(child.pid), 0)
        finally:
            child.wait(timeout=_LIVE_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
