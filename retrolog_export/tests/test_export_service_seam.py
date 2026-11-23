"""Tests for the service seam a book service fills: what no Letterboxd test reaches.

A book CSV read with medium ``book`` carries its ISBN-13 and Open Library work key,
each shape-checked; ``select`` runs its generic steps (cutoff, omit, hold, order) for
any ``ServiceSpec`` and leaves each free item's fate to the spec's ``account_fate``;
and the config checks, the packer and the review text take their table name and noun
from the caller rather than from Letterboxd. Every title, author, work key and ISBN is
invented, and each ISBN's check digit is computed here.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import typing
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Unpack

_TESTS = Path(__file__).resolve().parent
_TOOL = _TESTS.parent
# The same order as retrolog_export.SOURCE_DIRS: the tool's own src/ first.
sys.path[:0] = [
    str(_TOOL / "src"),
    str(_TOOL.parent / "retrolog_resolve" / "src"),
    str(_TOOL.parent / "retrolog_parse" / "src"),
    str(_TOOL.parent / "retrolog_survey" / "src"),
    str(_TESTS / "fixtures"),
    str(_TOOL),
]
import export_config  # noqa: E402 - after the sys.path inserts above
import export_input  # noqa: E402 - after the sys.path inserts above
import export_output  # noqa: E402 - after the sys.path inserts above
import export_pack  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
import export_services  # noqa: E402 - after the sys.path inserts above
import export_types  # noqa: E402 - after the sys.path inserts above
from export_cutoff import CutoffError  # noqa: E402 - after the sys.path inserts above
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    config_toml,
    csv_text,
    resolved_row,
    write_resolved,
)
from export_types import (  # noqa: E402 - after the sys.path inserts above
    Cutoff,
    ExportConfig,
    OmitEntry,
    ResolvedRow,
    ReviewEntry,
)
from select_case import day, kinds, make_row, sent  # noqa: E402 - after the inserts


class _BookChanges(typing.TypedDict, total=False):
    """The ResolvedRow fields a book test changes beyond its line."""

    status: str
    tmdb_id: str


# ISBN-13's check digit: modulo 10 over weights alternating 1 and 3.
_WEIGHTS = (1, 3)
_MODULUS = 10


def isbn13(first12: str) -> str:
    """``first12`` completed with its ISBN-13 check digit."""
    total = sum(int(d) * _WEIGHTS[i % 2] for i, d in enumerate(first12))
    return first12 + str(-total % _MODULUS)


_ISBN = isbn13("978000000001")
_WORK = "/works/OL1W"


def _wrong_check(isbn: str) -> str:
    """``isbn`` with its check digit changed, so it no longer verifies."""
    return isbn[:-1] + str((int(isbn[-1]) + 1) % _MODULUS)


def _book_row(**cells: str) -> dict[str, str]:
    """One resolve book row, the film defaults replaced by a book's."""
    book = {
        "medium": "book",
        "title": "Placeholder Book One",
        "creator": "Invented Author",
        "tmdb_id": "",
        "isbn13": _ISBN,
        "ol_work": _WORK,
    }
    return resolved_row(**{**book, **cells})


class BookInputTest(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.resolved = Path(holder.name) / "resolved"

    def _read(self, *rows: dict[str, str]) -> tuple[ResolvedRow, ...]:
        """``read_resolved`` of one book-2010.csv holding ``rows``, as books."""
        write_resolved(self.resolved, {"book-2010.csv": csv_text(list(rows))})
        return export_input.read_resolved(self.resolved, "book")

    def _refusal(self, **cells: str) -> str:
        """The InputError message for one book row with ``cells`` changed."""
        with self.assertRaises(export_input.InputError) as caught:
            self._read(_book_row(**cells))
        return str(caught.exception)

    def test_the_medium_type_names_both_media(self) -> None:
        self.assertEqual(typing.get_args(export_types.Medium), ("film", "book"))

    def test_a_book_row_carries_its_isbn13_and_work_key(self) -> None:
        (row,) = self._read(_book_row())
        self.assertEqual((row.isbn13, row.ol_work), (_ISBN, _WORK))
        self.assertEqual(row.title, "Placeholder Book One")
        self.assertEqual(row.creator, "Invented Author")

    def test_either_id_may_be_empty(self) -> None:
        rows = self._read(
            _book_row(isbn13="", source_line="1"),
            _book_row(ol_work="", source_line="2"),
            _book_row(isbn13="", ol_work="", source_line="3"),
        )
        self.assertEqual(
            [(row.isbn13, row.ol_work) for row in rows],
            [("", _WORK), (_ISBN, ""), ("", "")],
        )

    def test_a_film_row_reads_both_ids_empty(self) -> None:
        write_resolved(self.resolved, {"film-2010.csv": csv_text([resolved_row()])})
        (row,) = export_input.read_resolved(self.resolved, "film")
        self.assertEqual((row.isbn13, row.ol_work), ("", ""))

    def test_a_book_file_is_not_read_as_films(self) -> None:
        write_resolved(self.resolved, {"book-2010.csv": csv_text([_book_row()])})
        with self.assertRaises(export_input.InputError):
            export_input.read_resolved(self.resolved, "film")

    def test_a_malformed_isbn13_is_refused_by_its_column(self) -> None:
        for bad in (
            _wrong_check(_ISBN),
            _ISBN[:-1],
            _ISBN + "0",
            f"{_ISBN[:3]}-{_ISBN[3:]}",
            "0" + _ISBN[1:],
            "x" * 13,
        ):
            with self.subTest(bad=bad):
                message = self._refusal(isbn13=bad)
                self.assertIn("book-2010.csv: line 2: isbn13", message)
                self.assertNotIn(bad, message)

    def test_a_malformed_work_key_is_refused_by_its_column(self) -> None:
        for bad in ("/works/OL1", "/works/OLW", "/books/OL1M", "OL1W", "/works/OL1W/"):
            with self.subTest(bad=bad):
                message = self._refusal(ol_work=bad)
                self.assertIn("book-2010.csv: line 2: ol_work", message)
                self.assertNotIn(bad, message)

    def test_a_book_row_with_a_tmdb_id_is_refused(self) -> None:
        message = self._refusal(tmdb_id="1000001")
        self.assertIn("book-2010.csv: line 2: tmdb_id", message)


# ---------------------------------------------------------------------------
# select, with a stub service
# ---------------------------------------------------------------------------

_ACCOUNT = "the stub account"
_CONFIG = "the stub config"


def _book_key(row: ResolvedRow) -> str:
    """A stub book key: its ISBN-13, else its title key."""
    return f"book:{row.isbn13 or row.title_key}"


@dataclasses.dataclass
class _Stub:
    """A stub service's account calls, recorded, and the holds it adds."""

    fates: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)
    grows: list[str] = dataclasses.field(default_factory=list)
    splits: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    hold: str = ""  # an item key the stub's grow_holds holds, "" for none

    def account_fate(
        self, item: export_select.Item, account: str, config: str
    ) -> export_select.Fate:
        """Send ``item`` as it is, and record the call."""
        self.fates.append((item.key, account, config))
        return item, []

    def grow_holds(
        self,
        items: Sequence[export_select.Item],
        holds: dict[str, export_select.Hold],
        config: str,
    ) -> None:
        """Hold ``self.hold`` when it is an item's, and record the call."""
        self.grows.append(config)
        if any(item.key == self.hold for item in items):
            holds.setdefault(self.hold, ("held-failed", "a stub hold"))

    def split_holds(
        self, items: Sequence[export_select.Item], account: str, config: str
    ) -> dict[str, str]:
        """Find no split, and record the account and config it was asked with."""
        del items  # the stub splits nothing
        self.splits.append((account, config))
        return {}


def _spec(stub: _Stub) -> export_services.ServiceSpec[str, str]:
    """A book service whose account fate and holds are ``stub``'s."""
    return export_services.ServiceSpec(
        name="goodreads",
        medium="book",
        file_stem="goodreads",
        columns=("Title",),
        formula_columns=("Title",),
        tag_columns=("Title",),
        item_key=_book_key,
        read_account=lambda _path: _ACCOUNT,
        account_fate=stub.account_fate,
        grow_holds=stub.grow_holds,
        split_holds=stub.split_holds,
        item_cells=lambda item, _config: tuple((row.title,) for row in item.rows),
        config=lambda _export_config: _CONFIG,
        check_rows=lambda _config, _rows: None,
        default_rows=10_000,
        default_bytes=10_000_000,
        test_run=False,
    )


def _book(line: int, **changes: Unpack[_BookChanges]) -> ResolvedRow:
    """A dated book row on ``line`` of ``list.txt``, its ISBN-13 numbered by it."""
    row = make_row(
        source_file="list.txt",
        source_line=line,
        title=f"Placeholder Book {line}",
        title_key=f"placeholder book {line}",
        date=day(line),
        **({"tmdb_id": ""} | changes),
    )
    return dataclasses.replace(row, isbn13=isbn13(f"978{line:09d}"))


def _select(
    spec: export_services.ServiceSpec[str, str],
    rows: Sequence[ResolvedRow],
    *,
    cutoff: Cutoff | None = None,
    omit: tuple[OmitEntry, ...] = (),
) -> export_select.Selection:
    """``select`` on the stub account and config."""
    return export_select.select(
        rows, spec=spec, config=_CONFIG, account=_ACCOUNT, cutoff=cutoff, omit=omit
    )


class StubServiceSelectTest(unittest.TestCase):
    def test_the_generic_steps_run_and_the_spec_decides_each_fate(self) -> None:
        stub = _Stub()
        rows = [
            _book(5),
            _book(2),
            _book(3, status="ambiguous"),
            _book(4),
            _book(8),
            _book(9),
        ]
        omit = (OmitEntry("placeholder book 4", rows[3].year),)
        cutoff = Cutoff("list.txt", 8, "placeholder book 8")
        chosen = _select(_spec(stub), rows, cutoff=cutoff, omit=omit)
        self.assertEqual(chosen.cut, 2)
        self.assertEqual(sent(chosen), ["list.txt:2", "list.txt:5"])
        self.assertEqual(
            kinds(chosen),
            [("held-ambiguous", ("list.txt:3",)), ("omitted", ("list.txt:4",))],
        )
        keys = [_book_key(rows[1]), _book_key(rows[0])]
        self.assertEqual(stub.fates, [(key, _ACCOUNT, _CONFIG) for key in keys])
        self.assertEqual(stub.grows, [_CONFIG, _CONFIG])
        self.assertEqual(stub.splits, [(_ACCOUNT, _CONFIG)])

    def test_items_are_keyed_by_the_specs_item_key(self) -> None:
        stub = _Stub()
        # One title twice, as two books: two ISBNs make two items.
        first = _book(1)
        second = dataclasses.replace(
            _book(2), title=first.title, title_key=first.title_key
        )
        chosen = _select(_spec(stub), [first, second])
        self.assertEqual(
            [item.key for item in chosen.films], [_book_key(first), _book_key(second)]
        )

    def test_a_hold_the_spec_grows_is_kept(self) -> None:
        held = _book(2)
        stub = _Stub(hold=_book_key(held))
        chosen = _select(_spec(stub), [_book(1), held])
        self.assertEqual(sent(chosen), ["list.txt:1"])
        [entry] = chosen.review
        self.assertEqual((entry.kind, entry.detail), ("held-failed", "a stub hold"))
        self.assertEqual(len(stub.fates), 1)

    def test_the_split_rule_is_the_specs(self) -> None:
        # One title on a row with a tmdbID and a row without: a split for a film.
        rows = [_book(1, tmdb_id="1000001"), _book(2)]
        rows[1] = dataclasses.replace(
            rows[1], title=rows[0].title, title_key=rows[0].title_key
        )
        book = _select(_spec(_Stub()), rows)
        self.assertEqual(sent(book), ["list.txt:1", "list.txt:2"])
        film_rule = dataclasses.replace(
            _spec(_Stub()),
            split_holds=lambda items, _account, _config: export_select.film_splits(
                items
            ),
        )
        film = _select(film_rule, rows)
        self.assertEqual([kind for kind, _ in kinds(film)], ["held-split"] * 2)

    def test_the_cutoff_and_omit_messages_name_the_specs_table(self) -> None:
        with self.assertRaises(CutoffError) as caught:
            _select(_spec(_Stub()), [_book(1)], cutoff=Cutoff("list.txt", 9, "none"))
        self.assertIn("goodreads.cutoff", str(caught.exception))
        self.assertNotIn("letterboxd", str(caught.exception))
        omit = (OmitEntry("placeholder book 1", _book(1).year),)
        [entry] = _select(_spec(_Stub()), [_book(1)], omit=omit).review
        self.assertIn("goodreads.omit", entry.detail)
        self.assertNotIn("letterboxd", entry.detail)


# ---------------------------------------------------------------------------
# The service table, the config checks, the packer, and the review text
# ---------------------------------------------------------------------------


class LetterboxdSpecTest(unittest.TestCase):
    _SPEC = export_services.SERVICES["letterboxd"]

    def test_the_service_names_admit_goodreads(self) -> None:
        self.assertEqual(
            typing.get_args(export_types.ServiceName), ("letterboxd", "goodreads")
        )

    def test_the_letterboxd_spec_fills_the_new_fields(self) -> None:
        self.assertIs(self._SPEC.item_key, export_select.film_key)
        self.assertEqual(self._SPEC.default_rows, 1_900)
        self.assertEqual(self._SPEC.default_bytes, 1_000_000)
        self.assertTrue(self._SPEC.test_run)

    def test_the_letterboxd_spec_reads_its_own_table(self) -> None:
        loaded = export_config.load_config(config_toml())
        self.assertIs(self._SPEC.config(loaded), loaded.letterboxd)
        self.assertIsNone(self._SPEC.config(ExportConfig(letterboxd=None)))


class TableNamedChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        config = export_config.load_config(config_toml()).letterboxd
        assert config is not None
        self.config = config

    def test_check_keys_names_the_callers_table(self) -> None:
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.check_keys(
                self.config, [make_row(rating_raw="9")], where="goodreads"
            )
        self.assertEqual(
            str(caught.exception),
            "goodreads.ratings.9: missing, and a row is rated with it",
        )

    def test_check_omit_names_the_callers_table(self) -> None:
        config = dataclasses.replace(self.config, omit=(OmitEntry("none", None),))
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.check_omit(config, [make_row()], where="goodreads")
        self.assertEqual(str(caught.exception), "goodreads.omit[1]: matches no row")

    def test_a_rating_key_table_names_its_own_ratings_table(self) -> None:
        for helper, column in (
            (export_config._rating_tags, "rating_tags"),
            (export_config._rating_names, "rating_names"),
        ):
            with self.subTest(column=column):
                with self.assertRaises(export_config.ConfigError) as caught:
                    helper(
                        f"goodreads.{column}",
                        {"9": "retrolog-x"},
                        {"1": 2},
                        "goodreads.ratings",
                    )
                self.assertEqual(
                    str(caught.exception),
                    f"goodreads.{column}.9: not a key of goodreads.ratings",
                )


class BookNounTest(unittest.TestCase):
    _ENTRY = ReviewEntry("unrated", "Placeholder Book One", ("a:1",), "d", False)

    def _counts(self, medium: export_types.Medium) -> dict[str, int]:
        """``review_counts`` for one entry, two items, three rows, and one file."""
        return export_output.review_counts(
            [self._ENTRY],
            medium=medium,
            items_sent=2,
            rows_sent=3,
            rows_cut=0,
            files=1,
        )

    def test_the_counts_key_takes_the_medium(self) -> None:
        self.assertEqual(self._counts("book")["books-sent"], 2)
        self.assertEqual(self._counts("film")["films-sent"], 2)
        self.assertNotIn("films-sent", self._counts("book"))

    def test_the_summary_and_review_name_books(self) -> None:
        counts = self._counts("book")
        lines = export_output.summary_lines(counts, medium="book")
        self.assertEqual(lines[0], "books sent: 2")
        page = export_output.review_markdown(
            [self._ENTRY], counts=counts, medium="book"
        )
        self.assertIn("3 rows of 2 books sent in 1 file;", page)

    def test_a_book_too_large_for_a_file_is_named_a_book(self) -> None:
        with self.assertRaises(export_pack.PackError) as caught:
            export_pack.pack(
                ("Title",), [[("x",)] * 3], max_rows=2, max_bytes=1_000, noun="book"
            )
        self.assertIn("book 1 of 1 has 3 rows", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
