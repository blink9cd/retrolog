"""Tests for ``--mode overrides --append-to``: adding a round's picks to an existing file.

Each test runs ``main`` through ``review_cli_case.CliCase`` (tests/fixtures), with an
overrides file already holding one book override for the shared review's book entry.
"""

from __future__ import annotations

import errno
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TESTS / "fixtures"))
sys.path.insert(0, str(_TOOL))
import retrolog_review  # noqa: E402 - after the sys.path inserts above
from review_cli_case import (  # noqa: E402 - after the sys.path inserts above
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_USAGE,
    IN_A_REPO,
    PROG,
    REVIEW_BYTES,
    CliCase,
    FakeGit,
)
from review_fixtures import (  # noqa: E402 - after the sys.path inserts above
    BOOK_TITLE,
    FILM_TITLE,
    as_bytes,
    pick,
    picks,
)

_BOOK_TABLE = (
    f'[[overrides]]\nmedium = "book"\ntitle = "{BOOK_TITLE}"\n'
    'file = "2010/read.txt"\nline = 5\nunresolvable = true\n'
)
_FILM_TABLE = (
    f'[[overrides]]\nmedium = "film"\ntitle = "{FILM_TITLE}"\n'
    'file = "2010/seen.txt"\nline = 4\ntmdb_id = "1000003"\n'
)
_EXISTING = f"# round one\nschema = 1\n\n{_BOOK_TABLE}".encode()
_REFUSED = f"{PROG}: refused --append-to:"


class AppendModeTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.picks = self.base / "picks.json"
        self.target = self.base / "overrides.toml"
        self.target.write_bytes(_EXISTING)
        self.write_picks([pick({"tmdb_id": "1000003"})])

    def write_picks(self, items: list[object]) -> None:
        """Write a picks file holding ``items``, keyed to the shared review."""
        self.picks.write_bytes(as_bytes(picks(REVIEW_BYTES, items)))

    def append(self, *extra: str) -> tuple[int, str, str]:
        """Run overrides mode on the shared review and picks onto overrides.toml."""
        return self.run_main(
            "--mode", "overrides",
            "--review", str(self.review),
            "--picks", str(self.picks),
            "--append-to", str(self.target),
            *extra,
        )  # fmt: skip

    def assert_untouched(self) -> None:
        """The target still holds what it held, and no temporary file is left."""
        self.assertEqual(self.target.read_bytes(), _EXISTING)
        self.assertEqual(list(self.base.glob(".*")), [])

    def test_picks_are_appended_after_the_existing_text(self) -> None:
        code, out, err = self.append()
        self.assertEqual((code, err), (EXIT_OK, ""))
        self.assertEqual(
            out,
            "1 override appended for 1 pick, 0 kept, 1 undecided; "
            "the file now holds 2 overrides.\n",
        )
        self.assertEqual(
            self.target.read_bytes(), _EXISTING + f"\n{_FILM_TABLE}".encode()
        )
        self.assertEqual(list(self.base.glob(".*")), [])

    def test_all_keeps_leave_the_file_unwritten(self) -> None:
        self.write_picks([pick("keep")])
        with mock.patch.object(
            retrolog_review, "write_atomic_bytes", side_effect=AssertionError
        ):
            code, out, err = self.append()
        self.assertEqual((code, err), (EXIT_OK, ""))
        self.assertEqual(
            out,
            "0 overrides appended for 0 picks, 1 kept, 1 undecided; "
            "the file now holds 1 override.\n",
        )
        self.assert_untouched()

    def test_a_duplicate_of_an_existing_entry_is_refused(self) -> None:
        self.write_picks(
            [pick({"unresolvable": True}, title=BOOK_TITLE, medium="book")]
        )
        code, out, err = self.append()
        self.assertEqual((code, out), (EXIT_REFUSED, ""))
        self.assertEqual(
            err,
            f"{_REFUSED} the appended file would fail resolve's check: overrides[1]: "
            "the same medium, title, file and line as overrides[0]\n",
        )
        self.assert_untouched()

    def test_an_existing_file_resolve_refuses_is_refused(self) -> None:
        self.target.write_bytes(_BOOK_TABLE.encode())
        code, _, err = self.append()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            err,
            f"{_REFUSED} the existing file fails resolve's check: schema: missing\n",
        )
        self.assertEqual(self.target.read_bytes(), _BOOK_TABLE.encode())

    def test_a_missing_target_is_refused_before_anything_is_read(self) -> None:
        self.target.unlink()
        self.review.unlink()
        code, _, err = self.append()
        self.assertEqual((code, err), (EXIT_REFUSED, f"{_REFUSED} it does not exist\n"))
        self.assertFalse(self.target.exists())

    def test_a_link_or_folder_is_refused(self) -> None:
        real = self.base / "real.toml"
        real.write_bytes(_EXISTING)
        self.target.unlink()
        self.target.symlink_to(real)
        code, _, err = self.append()
        self.assertEqual(
            (code, err), (EXIT_REFUSED, f"{_REFUSED} it is not a regular file\n")
        )
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(real.read_bytes(), _EXISTING)
        self.target.unlink()
        self.target.mkdir()
        self.assertEqual(
            self.append()[::2], (EXIT_REFUSED, f"{_REFUSED} it is not a regular file\n")
        )

    def test_a_target_that_cannot_be_examined_is_a_usage_error(self) -> None:
        denied = PermissionError(errno.EACCES, os.strerror(errno.EACCES), "x")
        with mock.patch.object(Path, "lstat", side_effect=denied):
            code, out, err = self.append()
        self.assertEqual((code, out), (EXIT_USAGE, ""))
        self.assertEqual(
            err,
            f"{PROG}: could not write --append-to: PermissionError: "
            f"{os.strerror(errno.EACCES)}\n",
        )

    def test_a_target_inside_a_git_work_tree_is_refused(self) -> None:
        self.git = FakeGit(IN_A_REPO)
        code, out, err = self.append()
        self.assertEqual(
            (code, out, err), (EXIT_USAGE, "", f"{_REFUSED} inside a git work tree\n")
        )
        self.assert_untouched()

    def test_an_unreadable_target_is_a_usage_error(self) -> None:
        real = Path.read_bytes

        def failing(path: Path) -> bytes:
            if path == self.target:
                raise PermissionError(errno.EACCES, "denied")
            return real(path)

        with mock.patch.object(Path, "read_bytes", failing):
            code, _, err = self.append()
        self.assertEqual(
            (code, err),
            (EXIT_USAGE, f"{PROG}: could not read --append-to: PermissionError\n"),
        )

    def test_a_file_changed_during_the_run_is_kept(self) -> None:
        real = retrolog_review.append_overrides

        def edit_then_append(existing: bytes, tables: object) -> object:
            self.target.write_bytes(existing + b"# edited meanwhile\n")
            return real(existing, tables)  # type: ignore[arg-type]

        with mock.patch.object(retrolog_review, "append_overrides", edit_then_append):
            code, _, err = self.append()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            err, f"{_REFUSED} it changed during the run, so nothing was written\n"
        )
        self.assertEqual(self.target.read_bytes(), _EXISTING + b"# edited meanwhile\n")

    def test_a_file_swapped_for_a_link_during_the_run_is_kept(self) -> None:
        real_file = self.base / "real.toml"
        real = retrolog_review.append_overrides

        def swap_then_append(existing: bytes, tables: object) -> object:
            real_file.write_bytes(existing)
            self.target.unlink()
            self.target.symlink_to(real_file)
            return real(existing, tables)  # type: ignore[arg-type]

        with mock.patch.object(retrolog_review, "append_overrides", swap_then_append):
            code, _, err = self.append()
        self.assertEqual(
            (code, err), (EXIT_REFUSED, f"{_REFUSED} it is not a regular file\n")
        )
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(real_file.read_bytes(), _EXISTING)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not meaningful on NTFS")
    def test_the_replaced_file_is_readable_by_its_owner_only(self) -> None:
        self.target.chmod(0o644)
        self.assertEqual(self.append()[0], EXIT_OK)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_a_failed_write_is_a_usage_error(self) -> None:
        full = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        with mock.patch.object(retrolog_review, "write_atomic_bytes", side_effect=full):
            code, _, err = self.append()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(
            err,
            f"{PROG}: could not write --append-to: OSError: {os.strerror(errno.ENOSPC)}\n",
        )
        self.assert_untouched()

    def test_bad_picks_leave_the_file_untouched(self) -> None:
        self.write_picks([pick({"tmdb_id": "x"})])
        code, _, err = self.append()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("invalid --picks: picks[0].answer.tmdb_id", err)
        self.assert_untouched()

    def test_messages_name_no_title(self) -> None:
        _, out, err = self.append()
        self.assert_private(out, err)
        _, out, err = self.append()  # now a duplicate of the pick just appended
        self.assertIn("would fail resolve's check", err)
        self.assert_private(out, err)


class TargetOptionTest(CliCase):
    def run_mode(self, mode: str, *targets: str) -> tuple[int, str, str]:
        """Run ``mode`` with only the review, the picks where needed, and ``targets``."""
        picks_file = (
            ("--picks", str(self.base / "picks.json")) if mode == "overrides" else ()
        )
        return self.run_main(
            "--mode", mode, "--review", str(self.review), *picks_file, *targets
        )

    def test_overrides_mode_needs_one_target(self) -> None:
        code, _, err = self.run_mode("overrides")
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--mode overrides needs --out or --append-to", err)

    def test_overrides_mode_takes_only_one_target(self) -> None:
        both = (
            "--out",
            str(self.base / "a.toml"),
            "--append-to",
            str(self.base / "b.toml"),
        )
        code, _, err = self.run_mode("overrides", *both)
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("give --out or --append-to, not both", err)

    def test_page_mode_needs_out(self) -> None:
        code, _, err = self.run_mode("page")
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--mode page needs --out", err)

    def test_page_mode_refuses_append_to(self) -> None:
        target = (
            "--out",
            str(self.base / "p.html"),
            "--append-to",
            str(self.base / "o.toml"),
        )
        code, _, err = self.run_mode("page", *target)
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--append-to is only for --mode overrides", err)


if __name__ == "__main__":
    unittest.main()
