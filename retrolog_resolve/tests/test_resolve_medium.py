"""Tests for retrolog_resolve's ``--medium``: a run that reads and resolves one medium.

A single-medium run reads only that medium's parse CSVs, so it sends no request for
the other medium, needs no TMDb token for books, leaves the other medium's earlier
output alone, and treats an override for the other medium as out of scope rather than
unused. Its review covers this run only and says so. The runs reuse the command-line
suite's case: a parse folder of one book row and one film row.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path[:0] = [str(_TOOL), str(_TOOL / "samples"), str(_TESTS / "fixtures")]
sys.path.insert(0, str(_TESTS))
import build_resolve_inputs  # noqa: E402 - after the sys.path inserts above
import retrolog_resolve  # noqa: E402 - after the sys.path inserts above
from resolve_fixtures import Answering  # noqa: E402 - after the sys.path inserts
from test_resolve_cli import (  # noqa: E402 - after the sys.path insert above
    _BOOK_URL,
    _CONTACT_ENV,
    _FILM_URL,
    _MARKER,
    _TOKEN_ENV,
    EXIT_ERRORS,
    EXIT_OK,
    EXIT_USAGE,
    _CliCase,
    fake_git,
    snapshot,
)

_FILM_ONLY = "This run resolved film rows only"
_BOOK_ONLY = "This run resolved book rows only"


class FilmOnlyTest(_CliCase):
    def test_a_film_run_asks_tmdb_only_and_writes_only_film_csvs(self) -> None:
        self.assertEqual(self.main("--medium", "film"), EXIT_OK, self.stderr)
        self.assertEqual(self.fetch.calls, [_FILM_URL])
        names = {path.name for path in self.out.iterdir()}
        self.assertIn("film-2010.csv", names)
        self.assertNotIn("book-2010.csv", names)
        self.assertEqual(self.stdout.splitlines(), ["rows: 1", "status resolved: 1"])

    def test_a_film_run_leaves_the_book_output_of_an_earlier_run(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        # An earlier run's book CSV that parse no longer writes stays too: a film run
        # has no say over book files. The film one it did not write is removed.
        for name in ("book-2009.csv", "film-2009.csv"):
            (self.out / name).write_bytes(b"old\n")
        books = {
            name: digest
            for name, digest in snapshot(self.out).items()
            if name.startswith("book-")
        }
        self.assertEqual(self.main("--medium", "film"), EXIT_OK, self.stderr)
        after = snapshot(self.out)
        for name, digest in books.items():
            self.assertEqual(after.get(name), digest, name)
        self.assertNotIn("film-2009.csv", after)

    def test_a_book_override_is_out_of_scope_not_unused(self) -> None:
        overrides = self.overrides(
            'medium = "book"\ntitle = "Gone Book"\nunresolvable = true'
        )
        code = self.main("--medium", "film", "--overrides", overrides)
        self.assertEqual(code, EXIT_OK, self.stderr)
        self.assertNotIn("Gone Book", self.read("review.md"))

    def test_a_film_override_is_still_checked(self) -> None:
        overrides = self.overrides(
            'medium = "film"\ntitle = "Gone Film"\nunresolvable = true'
        )
        code = self.main("--medium", "film", "--overrides", overrides)
        self.assertEqual(code, EXIT_ERRORS)
        self.assertIn("review override-unused: 1", self.stdout)

    def test_a_folder_with_no_film_csv_is_refused_and_out_kept(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        before = snapshot(self.out)
        (self.parsed / "film-2010.csv").unlink()
        self.assertEqual(self.main("--medium", "film"), EXIT_USAGE)
        self.assertIn("--parsed: holds no film CSV file", self.stderr)
        self.assertEqual(snapshot(self.out), before)


class BookOnlyTest(_CliCase):
    def test_a_book_run_asks_open_library_only(self) -> None:
        self.assertEqual(self.main("--medium", "book"), EXIT_OK, self.stderr)
        self.assertEqual(self.fetch.calls, [_BOOK_URL])
        self.assertNotIn("film-2010.csv", {p.name for p in self.out.iterdir()})

    def test_a_book_run_never_reads_a_token_or_the_token_file(self) -> None:
        # A named --token-file that does not exist is refused whenever it is read,
        # so exit 0 here proves the book run never looked for it.
        self.env = {}
        missing = str(self.base / "missing.env")
        code = self.main("--medium", "book", "--token-file", missing)
        self.assertEqual(code, EXIT_OK, self.stderr)

    def test_the_same_film_folder_needs_the_token_without_medium(self) -> None:
        # The control for the test above: the film row is what reads the file.
        self.env = {}
        missing = str(self.base / "missing.env")
        self.assertEqual(self.main("--token-file", missing), EXIT_USAGE)


class ReviewScopeTest(_CliCase):
    def test_a_single_medium_review_states_its_scope(self) -> None:
        for medium, sentence, other in (
            ("film", _FILM_ONLY, _BOOK_ONLY),
            ("book", _BOOK_ONLY, _FILM_ONLY),
        ):
            with self.subTest(medium=medium):
                self.assertEqual(self.main("--medium", medium), EXIT_OK, self.stderr)
                markdown = self.read("review.md")
                self.assertIn(sentence, markdown)
                self.assertNotIn(other, markdown)
                document = json.loads(self.read("review.json"))
                self.assertEqual(document["medium"], medium)
                mediums = {entry["medium"] for entry in document["entries"]}
                self.assertLessEqual(mediums, {medium})

    def test_an_all_media_review_names_no_scope(self) -> None:
        self.assertEqual(self.main(), EXIT_OK, self.stderr)
        self.assertNotIn("This run resolved", self.read("review.md"))
        self.assertEqual(json.loads(self.read("review.json"))["medium"], "all")

    def test_a_film_review_drops_the_last_runs_book_entries(self) -> None:
        overrides = self.overrides(
            'medium = "book"\ntitle = "Moby Dick"\nunresolvable = true'
        )
        self.assertEqual(self.main("--overrides", overrides), EXIT_OK, self.stderr)
        self.assertIn("Moby Dick", self.read("review.md"))
        code = self.main("--medium", "film", "--overrides", overrides)
        self.assertEqual(code, EXIT_OK, self.stderr)
        self.assertNotIn("Moby Dick", self.read("review.md"))

    def test_an_unknown_medium_is_a_usage_error(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.main("--medium", "music")
        self.assertEqual(raised.exception.code, EXIT_USAGE)


class FilmSampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name).resolve() / "work"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_film_only_sample_output_is_reproduced(self) -> None:
        self.assertEqual(build_resolve_inputs.main(["--out", str(self.work)]), EXIT_OK)
        argv = ["--parsed", str(self.work / "parsed"), "--out", str(self.work / "out")]
        argv += ["--overrides", str(self.work / "overrides.toml"), "--cache-only"]
        argv += ["--medium", "film"]
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ))
            for name in (_TOKEN_ENV, _CONTACT_ENV):
                os.environ.pop(name, None)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code = retrolog_resolve.main(argv, fetch=Answering(), run_git=fake_git())
        # The sample's one film error is its override-mismatch; the book override no
        # row has is out of scope for a film run, so it is no error here.
        self.assertEqual(code, EXIT_ERRORS)
        expected = _TOOL / "samples" / "expected-film"
        out = self.work / "out"
        written = sorted(
            p.name for p in out.iterdir() if p.name not in {_MARKER, "cache"}
        )
        self.assertEqual(written, sorted(p.name for p in expected.iterdir()))
        for name in written:
            self.assertEqual(
                (out / name).read_bytes(), (expected / name).read_bytes(), name
            )


if __name__ == "__main__":
    unittest.main()
