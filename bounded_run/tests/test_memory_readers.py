"""The memory bound's readers: the tree sum, both platform readers and the reader choice.

The libproc reader is driven through C function pointers built from the module's own signature
table, so its calls cross real ctypes marshaling on any host; ``LiveLibprocTests`` then reads the
real library on macOS. The ``/proc`` reader reads a directory laid out the way Linux lays out
``/proc``, so its parsing and its fallback run on every host too. The watchdog's own tests are
in ``test_memory_watchdog.py``.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import boundedrun_memory
import boundedrun_memread
from _libproc_fake import fake_libproc, reader_over
from boundedrun_memread import RusageInfoV0

# A deterministic Hypothesis run, registered at import as test_posix_tree.py registers it:
# deadline=None and too_slow suppressed, because a wall-clock deadline measures machine load.
settings.register_profile(
    "deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("deterministic")

_PAGE = 4096


class _TableReader:
    """A reader over fixed tables: footprint, children and process group, each by pid."""

    def __init__(
        self,
        sizes: Mapping[int, int],
        children: Mapping[int, Sequence[int]],
        groups: Mapping[int, int] | None = None,
    ) -> None:
        self._sizes = sizes
        self._children = children
        self._groups = groups or {}

    def footprint(self, pid: int) -> int:
        return self._sizes.get(pid, 0)

    def children(self, pid: int) -> list[int]:
        return list(self._children.get(pid, ()))

    def group(self, pid: int) -> int | None:
        return self._groups.get(pid)

    def group_members(self, pgid: int) -> list[int]:
        return [pid for pid, group in self._groups.items() if group == pgid]


class TreeFootprintTests(unittest.TestCase):
    def test_sums_every_descendant_breadth_first(self) -> None:
        reader = _TableReader({10: 1, 11: 2, 12: 4, 13: 8}, {10: [11, 12], 12: [13]})
        self.assertEqual(boundedrun_memread.tree_footprint(10, reader), 15)

    def test_a_pid_listed_twice_is_counted_once(self) -> None:
        reader = _TableReader({10: 1, 11: 2}, {10: [11, 10], 11: [10]})
        self.assertEqual(boundedrun_memread.tree_footprint(10, reader), 3)

    def test_counts_every_member_of_every_group_the_tree_reaches(self) -> None:
        # 20 was orphaned in the root's group, 30 in child 11's own group: neither has a
        # parent link left, so only the group walk the tree kill makes reaches them. 31 is
        # 30's child, reached through both links and counted once. 12 reports group 1,
        # init's, which the kill never signals, so its member 999 is not the tree's.
        reader = _TableReader(
            {10: 1, 11: 2, 12: 4, 20: 8, 30: 16, 31: 32, 999: 1 << 20},
            {10: [11, 12], 30: [31]},
            {10: 10, 11: 11, 12: 1, 20: 10, 30: 11, 31: 11, 999: 1},
        )
        self.assertEqual(boundedrun_memread.tree_footprint(10, reader), 63)

    def test_the_root_group_is_walked_even_when_the_root_is_unreadable(self) -> None:
        # A leader that exited unreaped reports no group, as libproc reports a zombie.
        reader = _TableReader({20: 8}, {}, {20: 10})
        self.assertEqual(boundedrun_memread.tree_footprint(10, reader), 8)


class LibprocReaderTests(unittest.TestCase):
    def test_reads_phys_footprint(self) -> None:
        lib = fake_libproc({7: 5 * _PAGE}, {})
        self.assertEqual(reader_over(lib).footprint(7), 5 * _PAGE)

    def test_a_dead_pid_reads_as_zero(self) -> None:
        self.assertEqual(reader_over(fake_libproc({}, {})).footprint(8), 0)

    def test_lists_children_and_grows_a_full_buffer(self) -> None:
        lib = fake_libproc({}, {7: [21, 22, 23, 24, 25]})
        reader = reader_over(lib, capacity=2)
        self.assertEqual(reader.children(7), [21, 22, 23, 24, 25])

    def test_a_pid_with_no_children_lists_none(self) -> None:
        self.assertEqual(reader_over(fake_libproc({}, {})).children(7), [])

    def test_the_struct_has_the_documented_v0_size(self) -> None:
        # 16 bytes of uuid, then ten uint64 fields.
        self.assertEqual(ctypes.sizeof(RusageInfoV0), 16 + 10 * 8)

    def test_reads_a_process_group_and_none_for_a_dead_pid(self) -> None:
        reader = reader_over(fake_libproc({}, {}, {7: 70}))
        self.assertEqual(reader.group(7), 70)
        self.assertIsNone(reader.group(8))

    def test_lists_a_groups_members_and_grows_a_full_buffer(self) -> None:
        lib = fake_libproc({}, {}, {pid: 70 for pid in (21, 22, 23, 24, 25)})
        reader = reader_over(lib, capacity=2)
        self.assertEqual(reader.group_members(70), [21, 22, 23, 24, 25])

    def test_a_group_with_no_members_lists_none(self) -> None:
        self.assertEqual(reader_over(fake_libproc({}, {})).group_members(70), [])

    def test_the_bsd_short_info_struct_has_its_documented_size(self) -> None:
        # Four uint32 fields, a 16-byte name, then eight more uint32 fields.
        self.assertEqual(ctypes.sizeof(boundedrun_memread.ProcBsdShortInfo), 64)


@unittest.skipUnless(sys.platform == "darwin", "libproc is macOS's")
class LiveLibprocTests(unittest.TestCase):
    def test_reads_a_real_child_and_zero_once_it_is_reaped(self) -> None:
        bound = boundedrun_memory.memory_bound(1, "darwin")
        assert bound is not None
        reader = bound.reader
        child = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        try:
            self.assertGreater(reader.footprint(child.pid), 0)
            self.assertIn(child.pid, reader.children(os.getpid()))
            self.assertEqual(reader.group(child.pid), child.pid)
            self.assertEqual(reader.group_members(child.pid), [child.pid])
        finally:
            child.kill()
            child.wait(timeout=10)
        self.assertEqual(reader.footprint(child.pid), 0)
        self.assertEqual(reader.children(child.pid), [])
        self.assertIsNone(reader.group(child.pid))


def _write_proc(
    root: Path, pid: int, *, statm: str, stat: str, children: str | None
) -> None:
    """Lay out ``<root>/<pid>`` as Linux does; *children* None omits the children file."""
    (root / str(pid) / "task" / str(pid)).mkdir(parents=True)
    (root / str(pid) / "statm").write_text(statm, encoding="utf-8", newline="\n")
    (root / str(pid) / "stat").write_text(stat, encoding="utf-8", newline="\n")
    if children is not None:
        (root / str(pid) / "task" / str(pid) / "children").write_text(
            children, encoding="utf-8", newline="\n"
        )


class ProcfsReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_reads_resident_pages_times_the_page_size(self) -> None:
        _write_proc(
            self.root, 7, statm="900 25 3 1 0 40 0\n", stat="7 (x) S 1", children=""
        )
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertEqual(reader.footprint(7), 25 * _PAGE)

    def test_a_missing_or_malformed_statm_reads_as_zero(self) -> None:
        _write_proc(self.root, 7, statm="900\n", stat="7 (x) S 1", children="")
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertEqual(reader.footprint(7), 0)
        self.assertEqual(reader.footprint(8), 0)

    def test_children_come_from_every_threads_children_file(self) -> None:
        _write_proc(self.root, 7, statm="1 1", stat="7 (x) S 1", children="21 22 ")
        (self.root / "7" / "task" / "70").mkdir()
        (self.root / "7" / "task" / "70" / "children").write_text(
            "23", encoding="utf-8", newline="\n"
        )
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertEqual(sorted(reader.children(7)), [21, 22, 23])

    def test_without_children_files_it_scans_every_stat_for_the_parent(self) -> None:
        _write_proc(self.root, 7, statm="1 1", stat="7 (x) S 1", children=None)
        _write_proc(
            self.root, 21, statm="1 1", stat="21 (a) b) c) S 7 21", children=None
        )
        _write_proc(self.root, 22, statm="1 1", stat="22 (y) S 1 22", children=None)
        _write_proc(self.root, 23, statm="1 1", stat="garbled", children=None)
        (self.root / "self").mkdir()
        (self.root / "24").mkdir()  # a process that went before its stat could be read
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertEqual(reader.children(7), [21])

    def test_a_gone_process_has_no_children(self) -> None:
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertEqual(reader.children(7), [])

    def test_reads_the_group_from_stat_and_none_once_gone(self) -> None:
        _write_proc(self.root, 21, statm="1 1", stat="21 (a) b) S 7 20 1", children="")
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertEqual(reader.group(21), 20)
        self.assertIsNone(reader.group(22))

    def test_lists_a_groups_members_by_scanning_every_stat(self) -> None:
        _write_proc(self.root, 20, statm="1 1", stat="20 (x) S 1 20 20", children="")
        _write_proc(self.root, 21, statm="1 1", stat="21 (y) S 1 20 20", children="")
        _write_proc(self.root, 22, statm="1 1", stat="22 (z) S 1 22 22", children="")
        (self.root / "self").mkdir()
        reader = boundedrun_memread.ProcfsReader(self.root, _PAGE)
        self.assertEqual(sorted(reader.group_members(20)), [20, 21])

    def test_a_stat_field_that_is_missing_garbled_or_negative_reads_as_none(
        self,
    ) -> None:
        cases = {
            "7 (x) S": (None, None),
            "7 (x) S y z": (None, None),
            "7 (x) S -1 -2": (None, None),
            "7 (x S 1 2": (None, None),
            "7 (a) b) S 1 2": (1, 2),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                got = (
                    boundedrun_memread.parse_stat_ppid(text),
                    boundedrun_memread.parse_stat_pgrp(text),
                )
                self.assertEqual(got, expected)

    def test_the_parsers_hold_their_properties(self) -> None:
        test_fuzz_parse_statm_resident()
        test_fuzz_parse_stat_ppid()
        test_fuzz_parse_stat_pgrp()


@given(st.text())
def test_fuzz_parse_statm_resident(text: str) -> None:
    """Never raises, and never reads a negative size: the poll runs on whatever the kernel gave."""
    assert boundedrun_memread.parse_statm_resident(text, _PAGE) >= 0


@given(st.text())
def test_fuzz_parse_stat_ppid(text: str) -> None:
    """Never raises; a parent pid it finds is never negative."""
    ppid = boundedrun_memread.parse_stat_ppid(text)
    assert ppid is None or ppid >= 0


@given(st.text())
def test_fuzz_parse_stat_pgrp(text: str) -> None:
    """Never raises; a group it finds is never negative."""
    pgrp = boundedrun_memread.parse_stat_pgrp(text)
    assert pgrp is None or pgrp >= 0


class MemoryReaderTests(unittest.TestCase):
    def test_no_limit_needs_no_reader(self) -> None:
        self.assertIsNone(boundedrun_memory.memory_bound(None, "win32"))

    def test_linux_reads_proc(self) -> None:
        bound = boundedrun_memory.memory_bound(1, "linux")
        assert bound is not None
        self.assertIsInstance(bound.reader, boundedrun_memread.ProcfsReader)

    def test_darwin_binds_libproc_where_it_exists(self) -> None:
        try:
            bound = boundedrun_memory.memory_bound(1, "darwin")
        except OSError:
            self.assertNotEqual(
                sys.platform, "darwin", "libproc failed to load on macOS"
            )
        else:
            assert bound is not None
            self.assertIsInstance(bound.reader, boundedrun_memread.LibprocReader)


if __name__ == "__main__":
    unittest.main()
