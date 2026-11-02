"""The samples/ folder: its inputs are what they claim, and its outputs are current.

``samples/expected/`` holds what the tool writes for the sample inputs today, so a
change to the tool or to the page that alters either output fails here until the
expected files are regenerated (samples/README.md gives the command).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
_SAMPLES = _TOOL / "samples"
sys.path.insert(0, str(_SAMPLES))
import build_review_samples  # noqa: E402 - after the sys.path insert above

_EXPECTED = _SAMPLES / "expected"
_RESOLVE_REVIEW = (
    _TOOL.parent / "retrolog_resolve" / "samples" / "expected" / "review.json"
)
_EXIT_OK, _EXIT_REFUSED = 0, 1


def _quietly(argv: list[str]) -> tuple[int, str]:
    """``build_review_samples.main(argv)``'s exit code and standard output."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = build_review_samples.main(argv)
    return code, out.getvalue()


class SampleInputsTest(unittest.TestCase):
    def test_review_is_resolves_sample_byte_for_byte(self) -> None:
        self.assertEqual(
            build_review_samples.REVIEW.read_bytes(), _RESOLVE_REVIEW.read_bytes()
        )

    def test_picks_are_keyed_to_the_sample_review(self) -> None:
        digest = hashlib.sha256(build_review_samples.REVIEW.read_bytes()).hexdigest()
        for path in (build_review_samples.PICKS, build_review_samples.PICKS_ROUND_2):
            with self.subTest(path=path.name):
                picks = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(picks["review_sha256"], digest)

    def test_picks_show_each_kind_of_answer(self) -> None:
        picks = json.loads(build_review_samples.PICKS.read_text(encoding="utf-8"))
        answers = [pick["answer"] for pick in picks["picks"]]
        self.assertIn("keep", answers)
        self.assertIn({"unresolvable": True}, answers)
        self.assertGreaterEqual(
            sum(
                1
                for answer in answers
                if isinstance(answer, dict) and "tmdb_id" in answer
            ),
            2,  # one a candidate's id, one typed
        )


class SampleOutputsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "expected"

    def test_expected_files_are_what_the_tool_writes_now(self) -> None:
        code, printed = _quietly(["--out", str(self.out)])
        self.assertEqual(code, _EXIT_OK)
        self.assertEqual(
            printed,
            "9 entries on the page; 3 errors and 4 applied overrides not shown.\n"
            "4 overrides written for 4 picks, 4 kept, 1 undecided.\n"
            "1 override appended for 1 pick, 0 kept, 8 undecided; "
            "the file now holds 5 overrides.\n",
        )
        names = sorted(path.name for path in _EXPECTED.iterdir())
        self.assertEqual(names, sorted(path.name for path in self.out.iterdir()))
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(
                    (self.out / name).read_bytes(), (_EXPECTED / name).read_bytes()
                )

    def test_the_second_round_keeps_the_first_rounds_file_whole(self) -> None:
        first = (_EXPECTED / build_review_samples.OVERRIDES_NAME).read_bytes()
        second = (_EXPECTED / build_review_samples.ROUND_2_NAME).read_bytes()
        self.assertTrue(second.startswith(first))
        self.assertGreater(len(second), len(first))

    def test_a_refused_append_writes_nothing(self) -> None:
        absent = _SAMPLES / "absent"
        with mock.patch.object(build_review_samples, "PICKS_ROUND_2", absent):
            code, _ = _quietly(["--out", str(self.out)])
        self.assertNotEqual(code, _EXIT_OK)
        self.assertFalse(self.out.exists())

    def test_a_refused_mode_writes_nothing(self) -> None:
        with mock.patch.object(build_review_samples, "PICKS", _SAMPLES / "absent"):
            code, _ = _quietly(["--out", str(self.out)])
        self.assertNotEqual(code, _EXIT_OK)
        self.assertFalse(self.out.exists())

    def test_a_refused_page_stops_before_the_overrides(self) -> None:
        with mock.patch.object(build_review_samples, "REVIEW", _SAMPLES / "absent"):
            code, _ = _quietly(["--out", str(self.out)])
        self.assertNotEqual(code, _EXIT_OK)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
