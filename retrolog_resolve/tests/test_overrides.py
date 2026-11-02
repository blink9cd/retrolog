"""Tests for resolve_overrides: loading and checking the operator's overrides file."""

import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import parse_checks  # noqa: E402
import parse_rows  # noqa: E402
import resolve_overrides  # noqa: E402
from resolve_fixtures import (  # noqa: E402
    OVERRIDE_TOML_EACH_SHAPE,
    OVERRIDE_TOML_SOME_FILM,
    override_toml,
)
from resolve_types import ResolveOverride  # noqa: E402

_SAMPLE = _TOOL / "samples" / "overrides.example.toml"
_BOOK = 'medium = "book"\ntitle = "Placeholder Book One"\n'
_FILM = 'medium = "film"\ntitle = "Placeholder Film One"\n'


def _outcome(
    data: bytes,
) -> tuple[ResolveOverride, ...] | resolve_overrides.OverridesError:
    """What ``load_overrides`` makes of ``data``: its entries, or its OverridesError.

    Any other exception escapes and fails the calling test, which is the property.
    """
    try:
        return resolve_overrides.load_overrides(data)
    except resolve_overrides.OverridesError as error:
        return error


class LoadOverridesTest(unittest.TestCase):
    def _refusal(self, data: bytes) -> str:
        """The OverridesError message ``load_overrides`` gives for ``data``."""
        with self.assertRaises(resolve_overrides.OverridesError) as caught:
            resolve_overrides.load_overrides(data)
        return str(caught.exception)

    def test_override_title_is_matched_by_title_key(self) -> None:
        (only,) = resolve_overrides.load_overrides(OVERRIDE_TOML_SOME_FILM)
        self.assertEqual(only.title_key, parse_rows.title_key("Some  Film!"))

    def test_each_shape_loads(self) -> None:
        entries = resolve_overrides.load_overrides(OVERRIDE_TOML_EACH_SHAPE)
        self.assertEqual([e.index for e in entries], [0, 1, 2, 3])
        isbn_only, both, one_row, unresolvable = entries
        self.assertEqual(isbn_only.isbn13, "9780306406157")  # stored normalized
        self.assertIsNone(isbn_only.ol_work)
        self.assertEqual(
            (both.ol_work, both.isbn13), ("/works/OL123W", "9780306406157")
        )
        self.assertEqual(
            (one_row.medium, one_row.file, one_row.line, one_row.tmdb_id),
            ("film", "10/seen.txt", 7, "999"),
        )
        self.assertIsNone(isbn_only.file)
        self.assertIsNone(isbn_only.line)
        self.assertTrue(unresolvable.unresolvable)
        self.assertFalse(both.unresolvable)
        self.assertIsNone(unresolvable.tmdb_id)

    def test_sample_file_loads(self) -> None:
        entries = resolve_overrides.load_overrides(_SAMPLE.read_bytes())
        self.assertGreater(len(entries), 0)

    def test_file_with_no_overrides_is_empty(self) -> None:
        self.assertEqual(resolve_overrides.load_overrides(b"schema = 1\n"), ())

    def test_schema_other_than_one_is_refused(self) -> None:
        for schema in ("2", "true", '"1"'):
            with self.subTest(schema=schema):
                message = self._refusal(f"schema = {schema}\n".encode())
                self.assertIn("schema:", message)

    def test_missing_schema_is_refused(self) -> None:
        self.assertIn("schema: missing", self._refusal(b"[[overrides]]\n"))

    def test_unknown_keys_are_refused(self) -> None:
        self.assertIn("unknown key", self._refusal(b"schema = 1\nextra = 1\n"))
        message = self._refusal(override_toml(_FILM + 'tmdb_id = "1"\nnote = "x"'))
        self.assertIn("overrides[0]: unknown key 'note'", message)

    def test_overrides_must_be_an_array_of_tables(self) -> None:
        message = self._refusal(b"schema = 1\noverrides = 3\n")
        self.assertIn("overrides: must be an array of tables", message)
        message = self._refusal(b"schema = 1\noverrides = [3]\n")
        self.assertIn("overrides[0]: must be a table", message)

    def test_medium_and_title_are_required(self) -> None:
        message = self._refusal(override_toml('title = "Moby Dick"\ntmdb_id = "1"'))
        self.assertIn("overrides[0].medium: missing", message)
        message = self._refusal(override_toml('medium = "film"\ntmdb_id = "1"'))
        self.assertIn("overrides[0].title: missing", message)

    def test_medium_must_be_book_or_film(self) -> None:
        body = 'medium = "album"\ntitle = "Moby Dick"\ntmdb_id = "1"'
        self.assertIn(
            "overrides[0].medium: must be", self._refusal(override_toml(body))
        )

    def test_title_without_a_letter_or_digit_is_refused(self) -> None:
        body = 'medium = "film"\ntitle = "?!"\ntmdb_id = "1"'
        self.assertIn("overrides[0].title:", self._refusal(override_toml(body)))

    def test_override_needs_exactly_one_id(self) -> None:
        for body in (
            _FILM,
            _BOOK + 'isbn13 = "9780306406157"\nunresolvable = true',
            _FILM + 'tmdb_id = "1"\nunresolvable = true',
        ):
            with self.subTest(body=body[-20:]):
                message = self._refusal(override_toml(body))
                self.assertIn("overrides[0]: needs exactly one of", message)

    def test_unresolvable_must_be_true(self) -> None:
        for value in ("false", '"yes"'):
            with self.subTest(value=value):
                body = f"{_FILM}unresolvable = {value}"
                message = self._refusal(override_toml(body))
                self.assertIn("overrides[0].unresolvable: must be true", message)

    def test_override_tmdb_id_on_a_book_is_refused(self) -> None:
        message = self._refusal(override_toml(_BOOK + 'tmdb_id = "1"'))
        self.assertIn("overrides[0].tmdb_id: only for a film", message)

    def test_book_ids_on_a_film_are_refused(self) -> None:
        for key, value in (("isbn13", "9780306406157"), ("ol_work", "/works/OL1W")):
            with self.subTest(key=key):
                message = self._refusal(override_toml(f'{_FILM}{key} = "{value}"'))
                self.assertIn(f"overrides[0].{key}: only for a book", message)

    def test_bad_id_values_are_refused(self) -> None:
        cases = (
            (_BOOK, "isbn13", '"9780306406158"'),
            (_BOOK, "isbn13", "9780306406157"),
            (_BOOK, "ol_work", '"OL123W"'),
            (_BOOK, "ol_work", '"/works/OL123M"'),
            (_BOOK, "ol_work", '"/works/OL١W"'),
            (_FILM, "tmdb_id", '"12a"'),
            (_FILM, "tmdb_id", "12"),
            (_FILM, "tmdb_id", '"١٢"'),
            (_FILM, "tmdb_id", '"0603"'),
            (_FILM, "tmdb_id", '"0"'),
            (_FILM, "tmdb_id", '"12345678901"'),
        )
        for base, key, value in cases:
            with self.subTest(key=key, value=value):
                message = self._refusal(override_toml(f"{base}{key} = {value}"))
                self.assertIn(f"overrides[0].{key}: must be", message)

    def test_override_file_without_line_is_refused(self) -> None:
        body = _FILM + 'tmdb_id = "1"\nfile = "10/seen.txt"'
        self.assertIn(
            "overrides[0].line: required with file", self._refusal(override_toml(body))
        )

    def test_override_line_without_file_is_refused(self) -> None:
        body = _FILM + 'tmdb_id = "1"\nline = 3'
        self.assertIn(
            "overrides[0].file: required with line", self._refusal(override_toml(body))
        )

    def test_bad_file_and_line_values_are_refused(self) -> None:
        for extra, key in (
            ('file = "../seen.txt"\nline = 3', "file"),
            ('file = "/abs/seen.txt"\nline = 3', "file"),
            ('file = "10/seen.txt"\nline = 0', "line"),
            ('file = "10/seen.txt"\nline = true', "line"),
        ):
            with self.subTest(extra=extra):
                body = f'{_FILM}tmdb_id = "1"\n{extra}'
                self.assertIn(
                    f"overrides[0].{key}:", self._refusal(override_toml(body))
                )

    def test_same_title_key_file_and_line_twice_is_refused(self) -> None:
        second = 'medium = "film"\ntitle = "placeholder film ONE."\ntmdb_id = "2"'
        message = self._refusal(override_toml(_FILM + 'tmdb_id = "1"', second))
        self.assertIn("overrides[1]: the same medium, title, file and line", message)
        self.assertIn("overrides[0]", message)

    def test_same_title_with_a_different_scope_or_medium_loads(self) -> None:
        entries = resolve_overrides.load_overrides(
            override_toml(
                _FILM + 'tmdb_id = "1"',
                _FILM + 'tmdb_id = "2"\nfile = "10/seen.txt"\nline = 3',
                _FILM + 'tmdb_id = "3"\nfile = "10/seen.txt"\nline = 4',
                'medium = "book"\ntitle = "Placeholder Film One"\nol_work = "/works/OL9W"',
            )
        )
        self.assertEqual(len(entries), 4)

    def test_override_error_never_quotes_the_value(self) -> None:
        secretish = "Private Title Words"
        cases = (
            f'medium = "{secretish}"\ntitle = "x"\ntmdb_id = "1"',
            f'{_FILM}tmdb_id = "{secretish}"',
            f'{_BOOK}isbn13 = "{secretish}"',
            f'{_BOOK}ol_work = "{secretish}"',
            f'{_FILM}tmdb_id = "1"\nfile = "../{secretish}"\nline = 1',
        )
        for body in cases:
            with self.subTest(body=body[:12]):
                self.assertNotIn(secretish, self._refusal(override_toml(body)))

    def test_invalid_toml_is_refused_without_quoting_it(self) -> None:
        for data in (b"schema = ", b"\xff\xfe", b'title = "Private Title Words'):
            with self.subTest(data=data[:8]):
                message = self._refusal(data)
                self.assertEqual(message, "overrides file: not valid UTF-8 TOML")

    def test_deeply_nested_array_is_refused_not_a_recursion_error(self) -> None:
        # tomllib parses nested arrays recursively and raises RecursionError, which
        # neither UnicodeDecodeError nor TOMLDecodeError covers; the fuzz test's
        # 400-byte inputs are too short to nest this deep.
        depth = 2000
        data = b"schema = 1\nx = " + b"[" * depth + b"]" * depth + b"\n"
        self.assertEqual(self._refusal(data), "overrides file: not valid UTF-8 TOML")

    def test_parse_checks_failure_surfaces_as_overrides_error(self) -> None:
        # relative_file is parse_checks' helper, which raises parse's RulesError; the
        # loader must turn it into its own error, so a caller catches one class only.
        body = _FILM + 'tmdb_id = "1"\nfile = "../seen.txt"\nline = 3'
        with self.assertRaises(resolve_overrides.OverridesError) as caught:
            resolve_overrides.load_overrides(override_toml(body))
        self.assertIs(type(caught.exception), resolve_overrides.OverridesError)
        self.assertNotIsInstance(caught.exception, parse_checks.RulesError)
        self.assertTrue(str(caught.exception).startswith("overrides[0].file: "))
        message = self._refusal(
            override_toml('medium = "film"\ntitle = 3\ntmdb_id = "1"')
        )
        self.assertEqual(message, "overrides[0].title: must be a non-empty string")

    @given(st.binary(max_size=400))
    def test_fuzz_load_overrides(self, data: bytes) -> None:
        self.assertIsInstance(_outcome(data), (tuple, resolve_overrides.OverridesError))

    @given(st.integers(min_value=0), st.integers(min_value=0, max_value=40))
    def test_damaged_fixture_is_overrides_or_error(
        self, start: int, length: int
    ) -> None:
        """Deleting any slice of a valid file yields entries or OverridesError only."""
        text = OVERRIDE_TOML_EACH_SHAPE.decode()
        start %= len(text)
        damaged = (text[:start] + text[start + length :]).encode()
        self.assertIsInstance(
            _outcome(damaged), (tuple, resolve_overrides.OverridesError)
        )


if __name__ == "__main__":
    unittest.main()
