"""Tests for review_read: checking review.json and building the page's data block."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
_TOOLS = _TOOL.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOLS / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOLS / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOLS / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import resolve_isbn  # noqa: E402 - after the sys.path inserts above
import resolve_rows  # noqa: E402 - after the sys.path inserts above
import review_read  # noqa: E402 - after the sys.path inserts above
from review_fixtures import (  # noqa: E402 - after the sys.path inserts above
    FILM_TITLE,
    as_bytes,
    book_entry,
    candidate,
    entry,
    review,
)

_SAMPLE = _TOOLS / "retrolog_resolve" / "samples" / "expected" / "review.json"


def _row_line_document(line: str) -> bytes:
    """A one-entry review.json whose only row has ``line`` after its colon."""
    return as_bytes(review([entry(rows=[f"2010/seen.txt:{line}"])]))


# Row lines from one digit to past int()'s 4,300-digit limit, which a random byte
# string never reaches.
_ROW_LINE_DOCUMENTS = st.builds(
    lambda digit, count: _row_line_document(digit * count),
    st.sampled_from("019"),
    st.integers(min_value=1, max_value=6000),
)


def _outcome(data: bytes) -> review_read.Review | review_read.ReviewError:
    """What ``read_review`` makes of ``data``: a Review, or its ReviewError.

    Any other exception escapes and fails the calling test, which is the property.
    """
    try:
        return review_read.read_review(data)
    except review_read.ReviewError as error:
        return error


class ReadReviewTest(unittest.TestCase):
    def _refusal(self, document: object) -> str:
        """The ReviewError message ``read_review`` gives for ``document``."""
        data = document if isinstance(document, bytes) else as_bytes(document)
        with self.assertRaises(review_read.ReviewError) as caught:
            review_read.read_review(data)
        return str(caught.exception)

    def test_digest_is_the_sha256_of_the_bytes(self) -> None:
        data = as_bytes(review([entry()]))
        read = review_read.read_review(data)
        self.assertEqual(read.sha256, hashlib.sha256(data).hexdigest())

    def test_medium_is_the_reviews_own(self) -> None:
        read = review_read.read_review(as_bytes(review([], medium="book")))
        self.assertEqual(read.medium, "book")

    def test_decidable_kinds_are_kept_in_order(self) -> None:
        kinds = [
            "no-isbn",
            "ambiguous",
            "re-searched",
            "variant",
            "not-found",
            "most-voted",
        ]
        entries = [
            entry(kind, title=f"Placeholder Film {n}") for n, kind in enumerate(kinds)
        ]
        read = review_read.read_review(as_bytes(review(entries)))
        self.assertEqual([e["kind"] for e in read.entries], kinds)

    def test_a_re_searched_entry_is_kept_with_its_candidate(self) -> None:
        match = candidate(id="1000015", title="Placeholder Hour", votes=None)
        found = entry(
            "re-searched",
            title="The Placeholder Hour",
            detail="found by searching without a leading article",
            candidates=[match],
        )
        (only,) = review_read.read_review(as_bytes(review([found]))).entries
        self.assertEqual(only["kind"], "re-searched")
        self.assertEqual(only["candidates"], [match])

    def test_a_suspect_exact_entry_is_kept_with_its_one_candidate(self) -> None:
        match = candidate(id="1000016", title=FILM_TITLE, year=None, votes=0)
        held = entry(
            "suspect-exact",
            detail="one exact title, held: no release date",
            candidates=[match],
        )
        (only,) = review_read.read_review(as_bytes(review([held]))).entries
        self.assertEqual(only["kind"], "suspect-exact")
        self.assertEqual(only["candidates"], [match])

    def test_suspect_exact_is_decidable_right_after_ambiguous(self) -> None:
        kinds = review_read.DECIDABLE_KINDS
        self.assertEqual(kinds.index("suspect-exact"), kinds.index("ambiguous") + 1)

    def test_re_searched_is_decidable_right_after_variant(self) -> None:
        kinds = review_read.DECIDABLE_KINDS
        self.assertEqual(kinds.index("re-searched"), kinds.index("variant") + 1)

    def test_error_and_applied_entries_are_counted_not_kept(self) -> None:
        entries = [
            entry("failed"),
            entry("override-unused"),
            entry("override-mismatch"),
            entry("override-applied", candidates=[]),
            entry("override-applied", candidates=[]),
            entry("ambiguous"),
        ]
        read = review_read.read_review(as_bytes(review(entries)))
        self.assertEqual((read.errors, read.applied), (3, 2))
        self.assertEqual([e["kind"] for e in read.entries], ["ambiguous"])

    def test_kept_entry_drops_error_and_keeps_key_order(self) -> None:
        read = review_read.read_review(as_bytes(review([entry()])))
        (only,) = read.entries
        self.assertNotIn("error", only)
        self.assertEqual(
            list(only), ["candidates", "detail", "kind", "medium", "rows", "title"]
        )
        self.assertEqual(only["title"], FILM_TITLE)

    def test_candidate_keeps_its_fields(self) -> None:
        read = review_read.read_review(as_bytes(review([book_entry()])))
        (only,) = read.entries
        self.assertEqual(only["candidates"], book_entry()["candidates"])

    def test_resolves_sample_review_loads(self) -> None:
        read = review_read.read_review(_SAMPLE.read_bytes())
        self.assertGreater(len(read.entries), 0)
        self.assertGreater(read.errors, 0)
        self.assertGreater(read.applied, 0)

    def test_kinds_partition_resolves_review_kinds(self) -> None:
        shown = set(review_read.DECIDABLE_KINDS)
        errors = set(review_read.ERROR_KINDS)
        self.assertEqual(errors, set(resolve_rows.ERROR_KINDS))
        self.assertEqual(
            shown | errors | {review_read.APPLIED_KIND},
            set(resolve_rows.REVIEW_KINDS),
        )
        self.assertFalse(shown & errors)

    def test_not_json_is_refused(self) -> None:
        self.assertEqual(self._refusal(b"{"), "review file: not valid UTF-8 JSON")
        self.assertEqual(self._refusal(b"\xff"), "review file: not valid UTF-8 JSON")
        # Deep enough to exhaust the parser's recursion on every supported Python.
        deep = b"[" * 1_000_000 + b"]" * 1_000_000
        self.assertEqual(self._refusal(deep), "review file: not valid UTF-8 JSON")

    def test_top_level_shape_is_refused(self) -> None:
        self.assertEqual(self._refusal([]), "review file: must be an object")
        document = review()
        del document["schema_version"]
        self.assertEqual(self._refusal(document), "schema_version: missing")
        for wrong in (2, True, "1"):
            document = review()
            document["schema_version"] = wrong
            self.assertEqual(
                self._refusal(document),
                "schema_version: this version reads schema_version 1 only",
            )
        self.assertEqual(
            self._refusal(review(medium="music")), "medium: must be book, film, or all"
        )
        document = review()
        document["entries"] = {}
        self.assertEqual(self._refusal(document), "entries: must be a list")
        document = review()
        del document["entries"]
        self.assertEqual(self._refusal(document), "entries: missing")

    def test_entry_shape_is_refused(self) -> None:
        document = review()
        document["entries"] = [[]]
        self.assertEqual(self._refusal(document), "entries[0]: must be an object")
        broken = entry()
        del broken["detail"]
        self.assertEqual(
            self._refusal(review([entry(), broken])), "entries[1].detail: missing"
        )
        self.assertEqual(
            self._refusal(review([entry(extra=1)])), "entries[0]: unknown key 'extra'"
        )

    def test_entry_fields_are_refused(self) -> None:
        cases = {
            "kind": ("nonsense", "entries[0].kind: must be a review kind"),
            "medium": ("all", "entries[0].medium: must be book or film"),
            "title": (3, "entries[0].title: must be a string"),
            "detail": (None, "entries[0].detail: must be a string"),
            "rows": ("2010/seen.txt:4", "entries[0].rows: must be a list of strings"),
            "candidates": ({}, "entries[0].candidates: must be a list"),
            "error": ("no", "entries[0].error: must be true or false"),
        }
        for key, (value, message) in cases.items():
            with self.subTest(key=key):
                self.assertEqual(
                    self._refusal(review([entry(**{key: value})])), message
                )
        self.assertEqual(
            self._refusal(review([entry(rows=["ok", 4])])),
            "entries[0].rows: must be a list of strings",
        )

    def test_entry_row_must_split_into_a_file_and_a_line(self) -> None:
        rule = "must be <file>:<line>, the line a whole number of 1 or more"
        bad = (
            "2010/seen.txt",
            ":4",
            "2010/seen.txt:",
            "2010/seen.txt:0",
            "2010/seen.txt:00",
            "2010/seen.txt:-1",
            "2010/seen.txt:+4",
            "2010/seen.txt: 4",
            "2010/seen.txt:4x",
            "2010/seen.txt:\N{FULLWIDTH DIGIT FOUR}",
        )
        for row in bad:
            with self.subTest(row=row):
                document = review([entry(rows=["2010/seen.txt:3", row])])
                self.assertEqual(self._refusal(document), f"entries[0].rows[1]: {rule}")
        # An error entry's rows are checked the same.
        self.assertEqual(
            self._refusal(review([entry("failed", rows=["x"])])),
            f"entries[0].rows[0]: {rule}",
        )

    def test_an_overlong_row_line_is_refused_not_raised(self) -> None:
        # Regression: int() raises ValueError past 4,300 digits, which escaped
        # --mode page as a traceback instead of review.json's refusal.
        rule = "must be <file>:<line>, the line a whole number of 1 or more"
        for digits in ("1" * 5000, "1" * 10):
            with self.subTest(digits=len(digits)):
                document = review([entry(rows=[f"2010/seen.txt:{digits}"])])
                self.assertEqual(self._refusal(document), f"entries[0].rows[0]: {rule}")
        self.assertEqual(
            review_read.split_row("2010/seen.txt:" + "9" * 9),
            ("2010/seen.txt", 999_999_999),
        )

    def test_row_splits_at_its_last_colon(self) -> None:
        self.assertEqual(review_read.split_row("a:b.txt:12"), ("a:b.txt", 12))
        self.assertEqual(
            review_read.split_row("2010/seen.txt:007"), ("2010/seen.txt", 7)
        )
        for row in ("a:b.txt:", "nothing"):
            with self.subTest(row=row):
                self.assertIsNone(review_read.split_row(row))

    def test_decidable_entry_needs_a_row(self) -> None:
        self.assertEqual(
            self._refusal(review([entry(rows=[])])),
            "entries[0].rows: a decidable entry must list at least one row",
        )
        # override-unused names no row, as resolve writes it.
        read = review_read.read_review(
            as_bytes(review([entry("override-unused", rows=[])]))
        )
        self.assertEqual(read.errors, 1)

    def test_two_decidable_entries_for_one_scope_are_refused(self) -> None:
        self.assertEqual(
            self._refusal(review([entry(), book_entry(), entry("not-found")])),
            "entries[2]: the same medium, title, and rows as entries[0]",
        )

    def test_one_title_with_other_rows_is_two_entries(self) -> None:
        later = ["2012/seen.txt:3"]
        entries = [entry(), entry("not-found", rows=later), entry("failed")]
        read = review_read.read_review(as_bytes(review(entries)))
        self.assertEqual([e["rows"] for e in read.entries], [entry()["rows"], later])

    def test_error_flag_must_match_kind(self) -> None:
        self.assertEqual(
            self._refusal(review([entry("failed", error=False)])),
            "entries[0].error: must be true for an error kind, else false",
        )
        self.assertEqual(
            self._refusal(review([entry("ambiguous", error=True)])),
            "entries[0].error: must be true for an error kind, else false",
        )

    def test_entry_medium_must_be_the_reviews(self) -> None:
        self.assertEqual(
            self._refusal(review([entry()], medium="book")),
            "entries[0].medium: outside the review's medium",
        )

    def test_candidate_shape_is_refused(self) -> None:
        self.assertEqual(
            self._refusal(review([entry(candidates=["x"])])),
            "entries[0].candidates[0]: must be an object",
        )
        broken = candidate()
        del broken["votes"]
        self.assertEqual(
            self._refusal(review([entry(candidates=[candidate(), broken])])),
            "entries[0].candidates[1].votes: missing",
        )
        self.assertEqual(
            self._refusal(review([entry(candidates=[candidate(extra="")])])),
            "entries[0].candidates[0]: unknown key 'extra'",
        )

    def test_candidate_fields_are_refused(self) -> None:
        cases: dict[str, tuple[object, str]] = {
            "creator": (None, "must be a string"),
            "id": (1000003, "must be a string"),
            "isbn13": (False, "must be a string"),
            "original_title": ([], "must be a string"),
            "title": ({}, "must be a string"),
            "votes": ("120", "must be an integer or null"),
            "year": (1927.0, "must be an integer or null"),
        }
        for key, (value, rule) in cases.items():
            with self.subTest(key=key):
                bad = review([entry(candidates=[candidate(**{key: value})])])
                self.assertEqual(
                    self._refusal(bad), f"entries[0].candidates[0].{key}: {rule}"
                )
        for key in ("votes", "year"):
            with self.subTest(boolean=key):
                bad = review([entry(candidates=[candidate(**{key: True})])])
                self.assertEqual(
                    self._refusal(bad),
                    f"entries[0].candidates[0].{key}: must be an integer or null",
                )

    def test_a_tampered_candidate_id_is_refused(self) -> None:
        # The page links a candidate to its catalog page by its id, so an id that is
        # not a catalog id (a path to another host, a traversal) must not reach it.
        film_rule = (
            "entries[0].candidates[0].id: must be a string of 1 to 10 digits with no "
            "leading zero"
        )
        # A leading zero or an eleventh digit is no TMDb id, by resolve's own rule.
        film_ids = ("", "/works/OL1000013W", "1000003x", " 1000003", "\u0661", "0603")
        for value in (*film_ids, "12345678901"):
            with self.subTest(film=value):
                bad = review([entry(candidates=[candidate(id=value)])])
                self.assertEqual(self._refusal(bad), film_rule)
        book_rule = "entries[0].candidates[0].id: must be a string /works/OL<digits>W"
        for value in ("", "1000013", "//203.0.113.7/x", "/works/OL1W/../../x", "/a"):
            with self.subTest(book=value):
                bad = review([book_entry(candidates=[candidate(id=value)])])
                self.assertEqual(self._refusal(bad), book_rule)

    def test_a_tampered_candidate_isbn_is_refused(self) -> None:
        film = review([entry(candidates=[candidate(isbn13="9780306406157")])])
        self.assertEqual(
            self._refusal(film),
            "entries[0].candidates[0].isbn13: must be empty for a film",
        )
        book_rule = (
            "entries[0].candidates[0].isbn13: must be empty or an ISBN-13 with a valid "
            "check digit"
        )
        for value in ("9780306406158", "0306406152", "978-0306406157", "x"):
            with self.subTest(book=value):
                tampered = candidate(id="/works/OL1000013W", isbn13=value)
                bad = review([book_entry(candidates=[tampered])])
                self.assertEqual(self._refusal(bad), book_rule)

    def test_refusal_never_quotes_a_value(self) -> None:
        secret = "Placeholder Secret Title"
        message = self._refusal(review([entry(kind=secret)]))
        self.assertNotIn(secret, message)

    @given(st.binary(max_size=400) | _ROW_LINE_DOCUMENTS)
    @example(_row_line_document("1" * 5000))
    def test_fuzz_read_review(self, data: bytes) -> None:
        outcome = _outcome(data)
        self.assertIsInstance(outcome, (review_read.Review, review_read.ReviewError))

    @given(
        st.recursive(
            st.none() | st.booleans() | st.integers() | st.text(max_size=5),
            lambda inner: (
                st.lists(inner, max_size=3)
                | st.dictionaries(st.text(max_size=8), inner, max_size=4)
            ),
            max_leaves=12,
        )
    )
    def test_any_json_value_gives_a_review_or_its_error(self, value: object) -> None:
        outcome = _outcome(json.dumps(value).encode("utf-8"))
        self.assertIsInstance(outcome, (review_read.Review, review_read.ReviewError))

    @given(st.text(max_size=12), st.sampled_from(("book", "film")))
    @example("1000003", "film")
    @example("/works/OL1000013W", "book")
    def test_candidate_id_is_kept_only_in_its_catalogs_shape(
        self, value: str, medium: str
    ) -> None:
        make = book_entry if medium == "book" else entry
        shape = review_read.OL_WORK if medium == "book" else review_read.TMDB_ID
        outcome = _outcome(as_bytes(review([make(candidates=[candidate(id=value)])])))
        self.assertEqual(
            isinstance(outcome, review_read.Review), bool(shape.fullmatch(value))
        )


class PageDataTest(unittest.TestCase):
    def test_data_block_has_the_plans_shape(self) -> None:
        data = as_bytes(review([entry("failed"), entry(), book_entry("no-isbn")]))
        block = review_read.page_data(review_read.read_review(data))
        self.assertEqual(
            list(block), ["schema", "review_sha256", "medium", "not_shown", "entries"]
        )
        self.assertEqual(block["schema"], 1)
        self.assertEqual(block["review_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(block["medium"], "all")
        self.assertEqual(block["not_shown"], {"errors": 1, "applied": 0})
        entries = block["entries"]
        assert isinstance(entries, list)
        self.assertEqual([e["kind"] for e in entries], ["ambiguous", "no-isbn"])

    def test_data_block_is_json_serializable(self) -> None:
        block = review_read.page_data(
            review_read.read_review(as_bytes(review([entry()])))
        )
        self.assertEqual(json.loads(json.dumps(block)), block)

    def test_the_page_reads_the_schema_this_module_writes(self) -> None:
        # The page shows its unreadable state for any other schema, so the two
        # constants must move together.
        script = (_TOOL / "page" / "review.js").read_text(encoding="utf-8")
        found = re.search(r"const PAGE_SCHEMA = (\d+);", script)
        assert found is not None
        self.assertEqual(int(found.group(1)), review_read.PAGE_SCHEMA)

    def test_the_page_holds_a_film_id_to_resolves_rule(self) -> None:
        # A film id the page accepts or links must be one --mode overrides and
        # resolve accept, so the page's pattern is resolve_columns.TMDB_ID's.
        script = (_TOOL / "page" / "review.js").read_text(encoding="utf-8")
        found = re.search(r"const TMDB_ID = /\^(.*)\$/;", script)
        assert found is not None
        self.assertEqual(found.group(1), review_read.TMDB_ID.pattern)

    def test_the_page_checks_an_isbn_by_resolves_rule(self) -> None:
        # A typed ISBN the page accepts must be one isbn13_or_none accepts, or the
        # saved picks are refused: the page's patterns and check-digit arithmetic
        # are resolve_isbn's (its private constants, read here to pin the two).
        script = (_TOOL / "page" / "review.js").read_text(encoding="utf-8")

        def constant(name: str) -> str:
            found = re.search(rf"const {name} = (.*);", script)
            assert found is not None, name
            return found.group(1)

        self.assertEqual(constant("ISBN13"), f"/^{resolve_isbn._ISBN13.pattern}$/")
        self.assertEqual(constant("ISBN10"), f"/^{resolve_isbn._ISBN10.pattern}$/")
        weights = json.loads(constant("ISBN13_WEIGHTS"))
        self.assertEqual(tuple(weights), resolve_isbn._ISBN13_WEIGHTS)
        moduli = (int(constant("ISBN13_MODULUS")), int(constant("ISBN10_MODULUS")))
        python = (resolve_isbn._ISBN13_MODULUS, resolve_isbn._ISBN10_MODULUS)
        self.assertEqual(moduli, python)
        self.assertEqual(int(constant("ISBN10_X")), resolve_isbn._ISBN10_X_VALUE)


if __name__ == "__main__":
    unittest.main()
