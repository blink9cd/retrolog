"""Tests for survey_walk: what the survey sees in a source tree, and what it skips."""

import errno
import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import survey_walk  # noqa: E402


class WalkSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _touch(self, rel: str, data: bytes = b"x") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_kinds_groups_and_order(self) -> None:
        self._touch("era_b/2009.docx")
        self._touch("era_a/films.txt")
        self._touch("era_a/old.doc")
        self._touch("notes.rtf")
        entries = survey_walk.walk_source(self.root, group_depth=1)
        self.assertEqual(
            [(e.rel_path, e.group, e.kind) for e in entries],
            [
                ("era_a/films.txt", "era_a", "txt"),
                ("era_a/old.doc", "era_a", "doc"),
                ("era_b/2009.docx", "era_b", "docx"),
                ("notes.rtf", ".", "other"),
            ],
        )

    def test_skips_finder_and_appledouble_files_and_hidden_dirs(self) -> None:
        self._touch(".DS_Store")
        self._touch("era/._films.txt")
        self._touch(".hidden/x.txt")
        self._touch("era/films.txt")
        self.assertEqual(
            [e.rel_path for e in survey_walk.walk_source(self.root, group_depth=1)],
            ["era/films.txt"],
        )

    def test_extension_match_is_case_insensitive(self) -> None:
        self._touch("A.DOCX")
        self.assertEqual(
            survey_walk.walk_source(self.root, group_depth=1)[0].kind, "docx"
        )

    def test_decomposed_names_are_reported_nfc(self) -> None:
        decomposed = unicodedata.normalize("NFD", "café.txt")
        self._touch(decomposed)
        rel = survey_walk.walk_source(self.root, group_depth=1)[0].rel_path
        self.assertEqual(rel, unicodedata.normalize("NFC", "café.txt"))

    def test_symlink_is_reported_not_followed(self) -> None:
        self._touch("real/a.txt")
        (self.root / "link").symlink_to(self.root / "real", target_is_directory=True)
        kinds = {
            e.rel_path: e.kind
            for e in survey_walk.walk_source(self.root, group_depth=1)
        }
        self.assertEqual(kinds, {"link": "symlink", "real/a.txt": "txt"})

    def test_exclude_patterns_match_path_case_insensitively(self) -> None:
        self._touch("01-02/Year One CDs.doc")
        self._touch("03-04/music sample.docx")
        self._touch("03-04/films.txt")
        kinds = {
            e.rel_path: e.kind
            for e in survey_walk.walk_source(
                self.root, group_depth=1, exclude=("*music*", "*cds*")
            )
        }
        self.assertEqual(
            kinds,
            {
                "01-02/Year One CDs.doc": "excluded",
                "03-04/music sample.docx": "excluded",
                "03-04/films.txt": "txt",
            },
        )

    def test_lstat_failure_is_unreadable_not_a_crash(self) -> None:
        # Regression: on Python 3.12, Path.is_symlink re-raises an lstat failure such
        # as EACCES, which a directory readable but not searchable gives for each of
        # its children, so the walk crashed. Later releases return False instead,
        # which hid it; failing lstat itself stands in for 3.12 on any release.
        self._touch("era/locked/a.txt")
        self._touch("era/locked/sub/b.txt")
        self._touch("ok/c.txt")
        real_lstat = os.lstat

        def failing_lstat(path: object, *args: object, **kwargs: object) -> object:
            if Path(os.fsdecode(path)).parent.name == "locked":  # type: ignore[arg-type]
                raise PermissionError(13, "Permission denied", str(path))
            return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(os, "lstat", failing_lstat):
            entries = survey_walk.walk_source(self.root, group_depth=1)
        self.assertEqual(
            [(e.rel_path, e.kind, e.size, e.error) for e in entries],
            [
                ("era/locked/a.txt", "unreadable", 0, "PermissionError"),
                ("era/locked/sub", "unreadable", 0, "PermissionError"),
                ("ok/c.txt", "txt", 1, None),
            ],
        )

    def test_group_key_depths(self) -> None:
        self.assertEqual(survey_walk.group_key(("a", "b"), 0), ".")
        self.assertEqual(survey_walk.group_key(("a", "b"), 1), "a")
        self.assertEqual(survey_walk.group_key(("a", "b", "c"), 2), "a/b")
        self.assertEqual(survey_walk.group_key(("a",), 3), "a")
        self.assertEqual(survey_walk.group_key((), 1), ".")


class InjectedUnreadableTest(unittest.TestCase):
    """A listing refused by the OS, raised where chmod cannot refuse one: any host.

    ``UnreadableTest`` below proves the same against real permission bits, which neither
    the superuser nor Windows honors; these keep the branch tested where it skips.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for rel in ("ok/a.txt", "era/locked/b.txt"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(b"x")

    def _walk_refusing(self, locked: Path) -> list[tuple[str, str, str, str | None]]:
        """Walk the tree with *locked*'s listing refused as EACCES, as the OS refuses it."""
        real_scandir = os.scandir

        def scandir(path: str) -> object:
            if Path(path) == locked:
                raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)
            return real_scandir(path)

        with mock.patch.object(os, "scandir", scandir):
            entries = survey_walk.walk_source(self.root, group_depth=1)
        return [(e.rel_path, e.group, e.kind, e.error) for e in entries]

    def test_a_refused_directory_is_reported_and_siblings_kept(self) -> None:
        self.assertEqual(
            self._walk_refusing(self.root / "era" / "locked"),
            [
                ("era/locked", "era", "unreadable", "PermissionError"),
                ("ok/a.txt", "ok", "txt", None),
            ],
        )

    def test_a_refused_root_is_one_dot_entry(self) -> None:
        self.assertEqual(
            self._walk_refusing(self.root),
            [(".", ".", "unreadable", "PermissionError")],
        )


@unittest.skipIf(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    "needs POSIX permission bits, which the superuser ignores and Windows does not use",
)
class UnreadableTest(unittest.TestCase):
    """A listing or stat that fails becomes an `unreadable` entry, never a lost subtree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _touch(self, rel: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def _lock(self, path: Path) -> None:
        path.chmod(0o000)
        # Registered after the cleanup of the temp dir, so it runs first (LIFO) and the
        # tree can be deleted.
        self.addCleanup(path.chmod, 0o700)

    def test_unreadable_directory_is_reported_and_siblings_kept(self) -> None:
        self._touch("ok/a.txt")
        self._touch("era/locked/b.txt")
        self._lock(self.root / "era" / "locked")
        entries = survey_walk.walk_source(self.root, group_depth=1)
        self.assertEqual(
            [(e.rel_path, e.group, e.kind, e.size, e.error) for e in entries],
            [
                ("era/locked", "era", "unreadable", 0, "PermissionError"),
                ("ok/a.txt", "ok", "txt", 1, None),
            ],
        )

    def test_unreadable_root_is_one_dot_entry(self) -> None:
        self._touch("a.txt")
        self._lock(self.root)
        entries = survey_walk.walk_source(self.root, group_depth=1)
        self.assertEqual(
            [(e.rel_path, e.group, e.kind, e.error) for e in entries],
            [(".", ".", "unreadable", "PermissionError")],
        )

    def test_excluded_unreadable_directory_stays_excluded(self) -> None:
        self._touch("locked/b.txt")
        self._lock(self.root / "locked")
        entries = survey_walk.walk_source(self.root, group_depth=1, exclude=("lock*",))
        self.assertEqual(
            [(e.rel_path, e.kind) for e in entries], [("locked", "excluded")]
        )

    def test_unsearchable_directory_marks_its_children_unreadable(self) -> None:
        # A directory that can be listed but not searched (read without execute).
        self._touch("era/locked/a.txt")
        self._touch("ok/b.txt")
        locked = self.root / "era" / "locked"
        locked.chmod(0o600)
        self.addCleanup(locked.chmod, 0o700)
        entries = survey_walk.walk_source(self.root, group_depth=1)
        self.assertEqual(
            [(e.rel_path, e.kind, e.error) for e in entries],
            [
                ("era/locked/a.txt", "unreadable", "PermissionError"),
                ("ok/b.txt", "txt", None),
            ],
        )

    def test_stat_failure_marks_one_file_unreadable(self) -> None:
        self._touch("a.txt")
        self._touch("b.txt")
        real_lstat = os.lstat

        def failing_lstat(path: object, *args: object, **kwargs: object) -> object:
            if Path(os.fsdecode(path)).name == "a.txt":  # type: ignore[arg-type]
                raise FileNotFoundError(path)
            return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

        # The walk examines each item with one lstat, so that is where it can fail.
        with mock.patch.object(os, "lstat", failing_lstat):
            entries = survey_walk.walk_source(self.root, group_depth=1)
        self.assertEqual(
            [(e.rel_path, e.kind, e.size, e.error) for e in entries],
            [
                ("a.txt", "unreadable", 0, "FileNotFoundError"),
                ("b.txt", "txt", 1, None),
            ],
        )
