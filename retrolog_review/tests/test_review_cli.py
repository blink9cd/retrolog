"""Tests for the retrolog_review command line: what each mode reads, writes and returns.

Each test runs ``main`` through ``review_cli_case.CliCase`` (tests/fixtures), which
works in a temporary folder with the stand-in page files patched in.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import unittest
from collections.abc import Mapping
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
    IN_GIT,
    PROG,
    REVIEW_BYTES,
    CliCase,
    FakeGit,
)
from review_fixtures import (  # noqa: E402 - after the sys.path inserts above
    BOOK_TITLE,
    FILM_TITLE,
    as_bytes,
    book_entry,
    entry,
    pick,
    picks,
    review,
)


class PageModeTest(CliCase):
    def page(self, *extra: str) -> tuple[int, str, str]:
        """Run page mode on the shared review into ``page.html``."""
        out = str(self.base / "page.html")
        return self.run_main(
            "--mode", "page", "--review", str(self.review), "--out", out, *extra
        )

    def test_page_is_written_with_its_data(self) -> None:
        code, out, err = self.page()
        self.assertEqual((code, err), (EXIT_OK, ""))
        self.assertEqual(
            out, "2 entries on the page; 1 error and 0 applied overrides not shown.\n"
        )
        page = (self.base / "page.html").read_text(encoding="utf-8")
        self.assertIn("<style>", page)
        self.assertIn("Content-Security-Policy", page)
        start = page.index('id="review-data">') + len('id="review-data">')
        data = json.loads(page[start : page.index("</script>", start)])
        self.assertEqual(data["not_shown"], {"errors": 1, "applied": 0})
        self.assertEqual(
            [e["title"] for e in data["entries"]], [FILM_TITLE, BOOK_TITLE]
        )

    def test_page_replaces_an_earlier_page(self) -> None:
        self.assertEqual(self.page()[0], EXIT_OK)
        written = self.base / "page.html"
        # An earlier page, its data changed, so a rewrite is visible.
        written.write_text(
            written.read_text(encoding="utf-8").replace('"entries"', '"stale"'),
            encoding="utf-8",
            newline="\n",
        )
        code, _, err = self.page()
        self.assertEqual((code, err), (EXIT_OK, ""))
        self.assertNotIn('"stale"', written.read_text(encoding="utf-8"))

    def test_page_recognized_past_a_read_boundary_is_replaced(self) -> None:
        """The fingerprint is found even when it straddles two reads."""
        (self.base / "page.html").write_bytes(
            b"x" * (retrolog_review._SCAN_CHUNK - 3) + retrolog_review.PAGE_FINGERPRINT
        )
        self.assertEqual(self.page()[0], EXIT_OK)

    def test_unrelated_existing_file_is_refused_and_left_intact(self) -> None:
        unrelated = self.base / "page.html"
        unrelated.write_text("someone's notes", encoding="utf-8", newline="\n")
        code, out, err = self.page()
        self.assertEqual(
            (code, out, err),
            (
                EXIT_REFUSED,
                "",
                f"{PROG}: refused --out: it exists and is not a page this tool wrote\n",
            ),
        )
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "someone's notes")

    def test_foreign_out_inside_a_work_tree_is_a_usage_error_unread(self) -> None:
        """The work-tree guard runs first, so the foreign file's bytes are never read."""
        (self.base / "page.html").write_text("notes", encoding="utf-8", newline="\n")
        self.git = FakeGit(IN_A_REPO)
        with mock.patch.object(
            retrolog_review, "_holds_fingerprint", side_effect=AssertionError
        ):
            code, out, err = self.page()
        self.assertEqual((code, out, err), (EXIT_USAGE, "", f"{PROG}: {IN_GIT}\n"))

    def test_foreign_file_appearing_while_the_page_is_built_is_kept(self) -> None:
        """The check is repeated just before the write, after the page is built."""
        out = self.base / "page.html"
        build = retrolog_review.build_page

        def build_then_appear(
            template: str, css: str, js: str, data: Mapping[str, object]
        ) -> str:
            out.write_text("notes", encoding="utf-8", newline="\n")
            return build(template, css, js, data)

        with mock.patch.object(retrolog_review, "build_page", build_then_appear):
            code, _, _ = self.page()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(out.read_text(encoding="utf-8"), "notes")

    def test_out_that_is_a_folder_or_dangling_link_is_refused(self) -> None:
        out = self.base / "page.html"
        out.mkdir()
        self.assertEqual(self.page()[0], EXIT_REFUSED)
        out.rmdir()
        out.symlink_to(self.base / "absent.html")
        self.assertEqual(self.page()[0], EXIT_REFUSED)
        self.assertTrue(out.is_symlink())

    def test_out_that_is_a_link_to_a_page_is_refused_and_kept(self) -> None:
        """The atomic replace would swap the link itself for a file."""
        self.assertEqual(self.page()[0], EXIT_OK)
        out, real = self.base / "page.html", self.base / "real.html"
        out.rename(real)
        before = real.read_bytes()
        out.symlink_to(real)
        code, _, err = self.page()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("refused --out", err)
        self.assertTrue(out.is_symlink())
        self.assertEqual(real.read_bytes(), before)

    def test_page_out_that_cannot_be_examined_is_a_usage_error(self) -> None:
        out, lstat = self.base / "page.html", Path.lstat

        def denied_for_out(path: Path) -> os.stat_result:
            # Only --out is unexaminable, so the work-tree guard before it still runs.
            if path == out:
                raise PermissionError(errno.EACCES, "denied")
            return lstat(path)

        with mock.patch.object(
            Path, "lstat", autospec=True, side_effect=denied_for_out
        ):
            code, _, err = self.page()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(
            err, f"{PROG}: could not write --out: PermissionError: denied\n"
        )

    def test_page_out_that_cannot_be_read_is_a_usage_error(self) -> None:
        (self.base / "page.html").write_text("notes", encoding="utf-8", newline="\n")
        denied = PermissionError(errno.EACCES, "denied")
        with mock.patch.object(
            retrolog_review, "_holds_fingerprint", side_effect=denied
        ):
            code, _, err = self.page()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(
            err, f"{PROG}: could not write --out: PermissionError: denied\n"
        )

    def test_bad_review_is_refused(self) -> None:
        self.review.write_bytes(as_bytes(review([entry(kind="nonsense")])))
        code, out, err = self.page()
        self.assertEqual((code, out), (EXIT_REFUSED, ""))
        self.assertEqual(
            err, f"{PROG}: invalid --review: entries[0].kind: must be a review kind\n"
        )
        self.assertFalse((self.base / "page.html").exists())

    def test_missing_review_is_a_usage_error(self) -> None:
        self.review.unlink()
        code, _, err = self.page()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(err, f"{PROG}: could not read --review: FileNotFoundError\n")

    def test_template_missing_a_marker_is_a_usage_error(self) -> None:
        template = self.page_dir / "review.html"
        template.write_text(
            template.read_text(encoding="utf-8").replace("review.js", "other.js"),
            encoding="utf-8",
            newline="\n",
        )
        code, _, err = self.page()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(
            err,
            f"{PROG}: the page template: the template must hold the "
            "script tag line exactly once\n",
        )

    def test_unreadable_page_file_is_a_usage_error(self) -> None:
        (self.page_dir / "review.css").unlink()
        code, _, err = self.page()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(
            err,
            f"{PROG}: could not read the page file review.css: FileNotFoundError\n",
        )
        (self.page_dir / "review.css").write_bytes(b"\xff")
        code, _, err = self.page()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(
            err,
            f"{PROG}: could not read the page file review.css: UnicodeDecodeError\n",
        )

    def test_unwritable_out_is_a_usage_error(self) -> None:
        out = str(self.base / "absent" / "page.html")
        code, _, err = self.run_main(
            "--mode", "page", "--review", str(self.review), "--out", out
        )
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err.startswith(f"{PROG}: could not write --out: "))

    def test_out_inside_a_git_work_tree_is_refused(self) -> None:
        self.git = FakeGit(IN_A_REPO)
        code, out, err = self.page()
        self.assertEqual((code, out, err), (EXIT_USAGE, "", f"{PROG}: {IN_GIT}\n"))
        self.assertFalse((self.base / "page.html").exists())
        # git is asked about the folder the page goes into.
        self.assertEqual(
            self.git.calls,
            [(["rev-parse", "--is-inside-work-tree"], self.base.resolve())],
        )

    def test_out_under_a_git_folder_is_refused_when_git_says_no(self) -> None:
        (self.base / ".git").mkdir()
        code, _, err = self.page()
        self.assertEqual((code, err), (EXIT_USAGE, f"{PROG}: {IN_GIT}\n"))

    def test_picks_with_page_mode_is_a_usage_error(self) -> None:
        code, _, err = self.page("--picks", str(self.review))
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--picks is only for --mode overrides", err)

    def test_page_messages_name_no_title(self) -> None:
        _, out, err = self.page()
        self.assert_private(out, err)


class OverridesModeTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.picks = self.base / "picks.json"
        self.out = self.base / "overrides.toml"
        self.write_picks([pick({"tmdb_id": "1000003"})])

    def write_picks(self, items: list[object], data: bytes = REVIEW_BYTES) -> None:
        """Write a picks file holding ``items``, keyed to ``data``."""
        self.picks.write_bytes(as_bytes(picks(data, items)))

    def overrides(self) -> tuple[int, str, str]:
        """Run overrides mode on the shared review and picks into overrides.toml."""
        return self.run_main(
            "--mode",
            "overrides",
            "--review",
            str(self.review),
            "--picks",
            str(self.picks),
            "--out",
            str(self.out),
        )

    def test_overrides_are_written_and_counted(self) -> None:
        code, out, err = self.overrides()
        self.assertEqual((code, err), (EXIT_OK, ""))
        self.assertEqual(out, "1 override written for 1 pick, 0 kept, 1 undecided.\n")
        self.assertEqual(
            self.out.read_text(encoding="utf-8"),
            f'schema = 1\n\n[[overrides]]\nmedium = "film"\ntitle = "{FILM_TITLE}"\n'
            'file = "2010/seen.txt"\nline = 4\ntmdb_id = "1000003"\n',
        )

    def test_summary_counts_tables_picks_keeps_and_the_undecided(self) -> None:
        rows = ["2010/seen.txt:4", "2011/seen.txt:9"]
        data = as_bytes(review([entry(rows=rows), book_entry()]))
        self.review.write_bytes(data)
        self.write_picks(
            [
                pick({"unresolvable": True}, rows=rows),
                pick("keep", title=BOOK_TITLE, medium="book"),
            ],
            data=data,
        )
        code, out, _ = self.overrides()
        self.assertEqual(
            (code, out),
            (EXIT_OK, "2 overrides written for 1 pick, 1 kept, 0 undecided.\n"),
        )

    def test_out_inside_a_git_work_tree_is_refused(self) -> None:
        self.git = FakeGit(IN_A_REPO)
        code, out, err = self.overrides()
        self.assertEqual((code, out, err), (EXIT_USAGE, "", f"{PROG}: {IN_GIT}\n"))
        self.assertFalse(self.out.exists())

    def test_out_that_cannot_be_examined_is_a_usage_error(self) -> None:
        denied = PermissionError(errno.EACCES, os.strerror(errno.EACCES), "x")
        with mock.patch.object(Path, "exists", side_effect=denied):
            code, out, err = self.overrides()
        self.assertEqual((code, out), (EXIT_USAGE, ""))
        self.assertEqual(
            err,
            f"{PROG}: could not write --out: PermissionError: "
            f"{os.strerror(errno.EACCES)}\n",
        )
        self.assertEqual(list(self.base.glob(".*")), [])  # no temporary file left

    def test_existing_out_is_refused_before_anything_is_read(self) -> None:
        self.out.write_text("kept", encoding="utf-8", newline="\n")
        self.review.unlink()
        code, _, err = self.overrides()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            err,
            f"{PROG}: refused --out: it exists, and an overrides file "
            "is never overwritten\n",
        )
        self.assertEqual(self.out.read_text(encoding="utf-8"), "kept")

    def test_out_that_is_a_dangling_link_is_refused(self) -> None:
        self.out.symlink_to(self.base / "nowhere")
        code, _, _ = self.overrides()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertFalse((self.base / "nowhere").exists())

    def test_out_created_before_the_write_is_not_overwritten(self) -> None:
        real = retrolog_review.write_new

        def racing(path: Path, data: bytes) -> None:
            path.write_text("raced", encoding="utf-8", newline="\n")
            real(path, data)

        with mock.patch.object(retrolog_review, "write_new", racing):
            code, _, err = self.overrides()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("refused --out: it exists", err)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "raced")
        self.assertEqual(list(self.base.glob(".*")), [])

    def test_unwritable_out_is_a_usage_error(self) -> None:
        self.out = self.base / "absent" / "overrides.toml"
        code, _, err = self.overrides()
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err.startswith(f"{PROG}: could not write --out: "))

    def test_digest_mismatch_is_refused(self) -> None:
        self.write_picks([], data=b"another review")
        code, _, err = self.overrides()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            err,
            f"{PROG}: invalid --picks: review_sha256: the picks were "
            "made against another review file\n",
        )
        self.assertFalse(self.out.exists())

    def test_bad_pick_is_refused(self) -> None:
        self.write_picks([pick({"tmdb_id": "x"})])
        code, _, err = self.overrides()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            err,
            f"{PROG}: invalid --picks: picks[0].answer.tmdb_id: must "
            "be a string of 1 to 10 digits with no leading zero\n",
        )

    def test_bad_review_is_refused(self) -> None:
        self.review.write_bytes(b"[]")
        code, _, err = self.overrides()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertIn("invalid --review: review file: must be an object", err)

    def test_missing_picks_file_is_a_usage_error(self) -> None:
        self.picks.unlink()
        code, _, err = self.overrides()
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(err, f"{PROG}: could not read --picks: FileNotFoundError\n")

    def test_overrides_mode_needs_picks(self) -> None:
        code, _, err = self.run_main(
            "--mode", "overrides", "--review", str(self.review), "--out", str(self.out)
        )
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--mode overrides needs --picks", err)

    def test_overrides_messages_name_no_title(self) -> None:
        _, out, err = self.overrides()
        self.assert_private(out, err)
        self.write_picks([pick("keep", title="Placeholder Film Nine")])
        self.out.unlink()
        _, out, err = self.overrides()
        self.assert_private(out, err)


class HelpTest(CliCase):
    def test_help_shows_defaults_and_prerequisites(self) -> None:
        code, out, _ = self.run_main("--help")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Prerequisites:", out)
        self.assertIn("--mode", out)
        self.assertNotIn("(default: None)", out)


if __name__ == "__main__":
    unittest.main()
