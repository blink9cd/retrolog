"""Tests for review_picks: checking picks.json and turning picks into overrides."""

from __future__ import annotations

import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

from hypothesis import assume, given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
_TOOLS = _TOOL.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOLS / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOLS / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_rows  # noqa: E402 - after the sys.path inserts above
import resolve_overrides  # noqa: E402 - after the sys.path inserts above
import review_picks  # noqa: E402 - after the sys.path inserts above
import review_read  # noqa: E402 - after the sys.path inserts above
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

_ISBN = "9780306406157"
_OTHER_FILM = "Placeholder Film Three"
# The other film's entry lists two rows, so a pick for it writes two overrides.
_OTHER_ROWS = ["2010/seen.txt:6", "2011/seen.txt:2"]
_REVIEW_BYTES = as_bytes(
    review(
        [
            entry("failed", title="Placeholder Film Nine"),
            entry(),
            entry("most-voted", title=_OTHER_FILM, rows=_OTHER_ROWS),
            book_entry("no-isbn"),
        ]
    )
)
_REVIEW = review_read.read_review(_REVIEW_BYTES)


def _other(answer: object) -> dict[str, object]:
    """A pick for the other film's two-row entry."""
    return pick(answer, title=_OTHER_FILM, rows=_OTHER_ROWS)


def _build(items: list[object]) -> review_picks.Overrides:
    """The overrides built from ``items`` against the shared review."""
    return review_picks.build_overrides(as_bytes(picks(_REVIEW_BYTES, items)), _REVIEW)


def _outcome(data: bytes) -> review_picks.Overrides | review_picks.PicksError:
    """What ``build_overrides`` makes of ``data``: its Overrides, or its PicksError.

    Any other exception escapes and fails the calling test, which is the property.
    """
    try:
        return review_picks.build_overrides(data, _REVIEW)
    except review_picks.PicksError as error:
        return error


class BuildOverridesTest(unittest.TestCase):
    def _refusal(self, document: object) -> str:
        """The PicksError message ``build_overrides`` gives for ``document``."""
        data = document if isinstance(document, bytes) else as_bytes(document)
        with self.assertRaises(review_picks.PicksError) as caught:
            review_picks.build_overrides(data, _REVIEW)
        return str(caught.exception)

    def _pick_refusal(self, *items: object) -> str:
        """The PicksError message for a picks file holding ``items``."""
        return self._refusal(picks(_REVIEW_BYTES, list(items)))

    def test_each_answer_writes_its_override(self) -> None:
        built = _build(
            [
                pick({"tmdb_id": "1000003"}),
                _other({"unresolvable": True}),
                pick(
                    {"ol_work": "/works/OL1000013W", "isbn13": _ISBN},
                    title=BOOK_TITLE,
                    medium="book",
                ),
            ]
        )
        self.assertEqual(
            built.toml,
            "schema = 1\n"
            "\n[[overrides]]\n"
            'medium = "film"\n'
            f'title = "{FILM_TITLE}"\n'
            'file = "2010/seen.txt"\n'
            "line = 4\n"
            'tmdb_id = "1000003"\n'
            "\n[[overrides]]\n"
            'medium = "film"\n'
            f'title = "{_OTHER_FILM}"\n'
            'file = "2010/seen.txt"\n'
            "line = 6\n"
            "unresolvable = true\n"
            "\n[[overrides]]\n"
            'medium = "film"\n'
            f'title = "{_OTHER_FILM}"\n'
            'file = "2011/seen.txt"\n'
            "line = 2\n"
            "unresolvable = true\n"
            "\n[[overrides]]\n"
            'medium = "book"\n'
            f'title = "{BOOK_TITLE}"\n'
            'file = "2010/read.txt"\n'
            "line = 5\n"
            'ol_work = "/works/OL1000013W"\n'
            f'isbn13 = "{_ISBN}"\n',
        )
        self.assertEqual(
            (built.written, built.picked, built.kept, built.undecided), (4, 3, 0, 0)
        )

    def test_written_file_is_one_resolve_accepts(self) -> None:
        built = _build([pick({"isbn13": _ISBN}, title=BOOK_TITLE, medium="book")])
        (only,) = resolve_overrides.load_overrides(built.toml.encode("utf-8"))
        self.assertEqual(
            (only.medium, only.isbn13, only.ol_work), ("book", _ISBN, None)
        )
        self.assertEqual(only.title_key, parse_rows.title_key(BOOK_TITLE))
        self.assertEqual((only.file, only.line), ("2010/read.txt", 5))

    def test_one_title_in_two_entries_writes_only_the_picked_rows(self) -> None:
        """Regression: resolve groups by years too, so one title makes two entries.

        A pick names its entry by its rows as well, so picking one writes that
        entry's rows alone, and the other entry stays undecided.
        """
        later = ["2012/seen.txt:3", "2012/seen.txt:8"]
        data = as_bytes(review([entry(), entry("not-found", rows=later)]))
        read = review_read.read_review(data)
        document = picks(data, [pick({"tmdb_id": "1000004"}, rows=later)])
        built = review_picks.build_overrides(as_bytes(document), read)
        scopes = [
            (each.title_key, each.file, each.line, each.tmdb_id)
            for each in resolve_overrides.load_overrides(built.toml.encode("utf-8"))
        ]
        key = parse_rows.title_key(FILM_TITLE)
        self.assertEqual(
            scopes,
            [
                (key, "2012/seen.txt", 3, "1000004"),
                (key, "2012/seen.txt", 8, "1000004"),
            ],
        )
        self.assertEqual(
            (built.written, built.picked, built.kept, built.undecided), (2, 1, 0, 1)
        )

    def test_row_splits_at_its_last_colon(self) -> None:
        rows = ["notes/a:b.txt:12"]
        data = as_bytes(review([entry(rows=rows)]))
        document = picks(data, [pick({"tmdb_id": "1"}, rows=rows)])
        built = review_picks.build_overrides(
            as_bytes(document), review_read.read_review(data)
        )
        self.assertIn('file = "notes/a:b.txt"\nline = 12\n', built.toml)

    def test_keep_writes_nothing_and_untouched_titles_are_undecided(self) -> None:
        built = _build([_other("keep")])
        self.assertEqual(built.toml, "schema = 1\n")
        self.assertEqual(
            (built.written, built.picked, built.kept, built.undecided), (0, 0, 1, 2)
        )

    def test_no_picks_leaves_every_title_undecided(self) -> None:
        built = _build([])
        self.assertEqual(
            (built.written, built.picked, built.kept, built.undecided), (0, 0, 0, 3)
        )

    def test_typed_ol_work_alone_is_written(self) -> None:
        built = _build(
            [pick({"ol_work": "/works/OL9W"}, title=BOOK_TITLE, medium="book")]
        )
        self.assertIn('ol_work = "/works/OL9W"\n', built.toml)
        self.assertNotIn("isbn13", built.toml)

    def test_not_json_or_not_an_object_is_refused(self) -> None:
        self.assertEqual(self._refusal(b"\xff"), "picks file: not valid UTF-8 JSON")
        self.assertEqual(self._refusal([]), "picks file: must be an object")

    def test_top_level_keys_are_refused(self) -> None:
        document = picks(_REVIEW_BYTES)
        document["extra"] = 1
        self.assertEqual(self._refusal(document), "picks file: unknown key 'extra'")
        for key in ("schema", "review_sha256", "picks"):
            with self.subTest(missing=key):
                document = picks(_REVIEW_BYTES)
                del document[key]
                self.assertEqual(self._refusal(document), f"{key}: missing")

    def test_schema_and_list_are_refused(self) -> None:
        for wrong in (2, True):
            document = picks(_REVIEW_BYTES)
            document["schema"] = wrong
            self.assertEqual(
                self._refusal(document), "schema: this version reads schema 1 only"
            )
        document = picks(_REVIEW_BYTES)
        document["picks"] = {}
        self.assertEqual(self._refusal(document), "picks: must be a list")

    def test_digest_mismatch_is_refused(self) -> None:
        other = picks(b"another review")
        self.assertEqual(
            self._refusal(other),
            "review_sha256: the picks were made against another review file",
        )
        document = picks(_REVIEW_BYTES)
        document["review_sha256"] = 7
        self.assertEqual(self._refusal(document), "review_sha256: must be a string")

    def test_pick_shape_is_refused(self) -> None:
        self.assertEqual(self._pick_refusal("x"), "picks[0]: must be an object")
        extra = pick("keep") | {"note": ""}
        self.assertEqual(
            self._pick_refusal(_other("keep"), extra),
            "picks[1]: unknown key 'note'",
        )
        missing = pick("keep")
        del missing["answer"]
        self.assertEqual(self._pick_refusal(missing), "picks[0].answer: missing")
        self.assertEqual(
            self._pick_refusal(pick("keep", medium="all")),
            "picks[0].medium: must be book or film",
        )
        self.assertEqual(
            self._pick_refusal(pick("keep", medium=["film"])),
            "picks[0].medium: must be book or film",
        )
        self.assertEqual(
            self._pick_refusal(pick("keep", title=5)),
            "picks[0].title: must be a string",
        )
        for rows in ("2010/seen.txt:4", ["2010/seen.txt:4", 4], None):
            with self.subTest(rows=rows):
                document = pick("keep")
                document["rows"] = rows
                self.assertEqual(
                    self._pick_refusal(document),
                    "picks[0].rows: must be a list of strings",
                )

    def test_title_unicode_cannot_encode_is_refused(self) -> None:
        data = as_bytes(picks(_REVIEW_BYTES, [pick("keep")])).replace(
            FILM_TITLE.encode("ascii"), b"\\ud800"
        )
        self.assertEqual(
            self._refusal(data), "picks[0].title: must be valid Unicode text"
        )

    def test_pick_naming_no_decidable_entry_is_refused(self) -> None:
        cases = (
            pick("keep", title="Placeholder Film Nine"),  # an error entry
            pick("keep", title="Placeholder Film Eight"),  # in no entry at all
            pick("keep", medium="book", rows=["2010/seen.txt:4"]),  # wrong medium
            pick("keep", rows=["2010/seen.txt:5"]),  # the right title, other rows
            pick("keep", rows=[]),  # the right title, no rows
            _other("keep") | {"rows": list(reversed(_OTHER_ROWS))},  # rows reordered
            _other("keep") | {"rows": _OTHER_ROWS[:1]},  # one row of two
        )
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                self.assertEqual(
                    self._pick_refusal(case),
                    "picks[0]: names no decidable entry in the review",
                )

    def test_two_picks_for_one_entry_are_refused(self) -> None:
        self.assertEqual(
            self._pick_refusal(pick("keep"), _other("keep"), pick("keep")),
            "picks[2]: a second pick for the entry that picks[0] names",
        )

    def test_answer_shape_is_refused(self) -> None:
        shape = (
            'picks[0].answer: must be "keep" or an object with tmdb_id, ol_work '
            "and/or isbn13, or unresolvable"
        )
        answers: tuple[object, ...] = (
            "maybe",
            3,
            None,
            [],
            {},
            {"tmdb_id": "1", "unresolvable": True},
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertEqual(self._pick_refusal(pick(answer)), shape)
        self.assertEqual(
            self._pick_refusal(pick({"imdb": "tt1"})),
            "picks[0].answer: unknown key 'imdb'",
        )

    def test_answer_for_the_other_medium_is_refused(self) -> None:
        self.assertEqual(
            self._pick_refusal(pick({"ol_work": "/works/OL1W"})),
            "picks[0].answer.ol_work: only for a book",
        )
        self.assertEqual(
            self._pick_refusal(pick({"tmdb_id": "1"}, title=BOOK_TITLE, medium="book")),
            "picks[0].answer.tmdb_id: only for a film",
        )

    def test_answer_values_are_refused(self) -> None:
        film = {
            "tmdb_id": (
                [["1"], 1, "", "1a", "\N{FULLWIDTH DIGIT ONE}", "0603", "1" * 11],
                "must be a string of 1 to 10 digits with no leading zero",
            ),
            "unresolvable": ([False, "true", 1], "must be true"),
        }
        book = {
            "ol_work": (
                ["/works/OL1M", 1, "/works/OLW"],
                "must be a string /works/OL<digits>W",
            ),
            "isbn13": (
                ["9780306406158", 9780306406157, ""],
                "must be a string holding an ISBN-13 or ISBN-10 with a valid check "
                "digit",
            ),
        }
        for medium, title, table in (
            ("film", FILM_TITLE, film),
            ("book", BOOK_TITLE, book),
        ):
            for key, (values, rule) in table.items():
                for value in values:
                    with self.subTest(key=key, value=value):
                        answer = pick({key: value}, title=title, medium=medium)
                        self.assertEqual(
                            self._pick_refusal(answer), f"picks[0].answer.{key}: {rule}"
                        )

    def test_refusal_never_quotes_a_value(self) -> None:
        secret = "Placeholder Secret Answer"
        message = self._pick_refusal(pick({"tmdb_id": secret}))
        self.assertNotIn(secret, message)
        message = self._pick_refusal(pick("keep", title=secret))
        self.assertNotIn(secret, message)

    def test_output_resolve_refuses_is_refused(self) -> None:
        """Two titles with one title key would be two overrides for one title."""
        twin = "placeholder film two!"
        # The two entries list the same row, as no real review would, so the two
        # overrides share a medium, title key, file and line.
        data = as_bytes(review([entry(), entry(title=twin)]))
        read = review_read.read_review(data)
        document = picks(
            data, [pick({"tmdb_id": "1"}), pick({"tmdb_id": "2"}, title=twin)]
        )
        with self.assertRaises(review_picks.PicksError) as caught:
            review_picks.build_overrides(as_bytes(document), read)
        self.assertEqual(
            str(caught.exception),
            "the overrides file it would write fails resolve's check: overrides[1]: "
            "the same medium, title, file and line as overrides[0]",
        )

    @given(st.binary(max_size=400))
    def test_fuzz_read_picks(self, data: bytes) -> None:
        outcome = _outcome(data)
        self.assertIsInstance(
            outcome, (review_picks.Overrides, review_picks.PicksError)
        )

    @given(
        st.lists(
            st.fixed_dictionaries(
                {
                    "medium": st.sampled_from(["film", "book", "all"]),
                    "title": st.sampled_from([FILM_TITLE, BOOK_TITLE, _OTHER_FILM]),
                    "rows": st.sampled_from(
                        [["2010/seen.txt:4"], ["2010/read.txt:5"], _OTHER_ROWS, []]
                    ),
                    "answer": st.one_of(
                        st.just("keep"),
                        st.dictionaries(
                            st.sampled_from(
                                ["tmdb_id", "ol_work", "isbn13", "unresolvable"]
                            ),
                            st.one_of(
                                st.booleans(),
                                st.sampled_from(["1000003", "/works/OL1W", _ISBN]),
                                st.text(max_size=4),
                            ),
                            max_size=3,
                        ),
                    ),
                }
            ),
            max_size=4,
        )
    )
    def test_any_picks_give_overrides_or_their_error(self, items: list[object]) -> None:
        outcome = _outcome(as_bytes(picks(_REVIEW_BYTES, items)))
        self.assertIsInstance(
            outcome, (review_picks.Overrides, review_picks.PicksError)
        )
        if isinstance(outcome, review_picks.Overrides):
            resolve_overrides.load_overrides(outcome.toml.encode("utf-8"))


class TomlStringTest(unittest.TestCase):
    def test_quotes_backslashes_and_controls_are_escaped(self) -> None:
        self.assertEqual(
            review_picks.toml_string('a"b\\c\td\ne\x00f\x7fg\x1b\b\f\r'),
            '"a\\"b\\\\c\\td\\ne\\u0000f\\u007Fg\\u001B\\b\\f\\r"',
        )

    def test_unicode_is_kept(self) -> None:
        self.assertEqual(review_picks.toml_string("Ça été"), '"Ça été"')

    @given(st.text(max_size=40))
    def test_any_title_round_trips_through_tomllib(self, title: str) -> None:
        parsed = tomllib.loads(f"title = {review_picks.toml_string(title)}\n")
        self.assertEqual(parsed["title"], title)

    @given(st.text(min_size=1, max_size=40))
    def test_any_title_round_trips_through_load_overrides(self, title: str) -> None:
        assume(parse_rows.title_key(title))
        data = as_bytes(review([entry(title=title)]))
        document = picks(data, [pick({"unresolvable": True}, title=title)])
        built = review_picks.build_overrides(
            json.dumps(document).encode("utf-8"), review_read.read_review(data)
        )
        self.assertEqual(tomllib.loads(built.toml)["overrides"][0]["title"], title)
        (only,) = resolve_overrides.load_overrides(built.toml.encode("utf-8"))
        self.assertEqual(only.title_key, parse_rows.title_key(title))


class PageShapesTest(unittest.TestCase):
    def test_the_page_stores_only_the_answer_shapes_picks_accept(self) -> None:
        # A stored answer the page keeps must be one --mode overrides takes, so its
        # ID_SHAPES (sorted keys, comma-joined) are _ANSWER_SHAPES' id shapes; the
        # page checks unresolvable on its own.
        script = (_TOOL / "page" / "review_storage.js").read_text(encoding="utf-8")
        found = re.search(r"const ID_SHAPES = \[(.*)\];", script)
        assert found is not None
        page = set(re.findall(r'"([^"]*)"', found.group(1)))
        python = {",".join(sorted(shape)) for shape in review_picks._ANSWER_SHAPES}
        self.assertEqual(page, python - {"unresolvable"})


if __name__ == "__main__":
    unittest.main()
