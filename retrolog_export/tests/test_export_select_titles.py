"""Tests for export_select's title matching: TMDb titles, split titles, empty titles.

Each class is one rule: the account is matched on resolve's TMDb titles as well as the
list's, a title split between a tmdbID film and a title-only film is held, the
on-service detail says when a row had no year, and a film with no title to match is
held.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict, Unpack

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
import export_account  # noqa: E402 - after the sys.path inserts above
import export_input  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
from export_fixtures import (  # noqa: E402 - after the sys.path inserts above
    csv_text,
    resolved_row,
    write_resolved,
)
from export_types import FilmKey, ResolvedRow  # noqa: E402 - after the inserts
from parse_rows import title_key  # noqa: E402 - after the sys.path inserts above
from select_case import (  # noqa: E402 - after the sys.path inserts above
    FilmIdentity,
    imported,
    kinds,
    make_row,
    select,
    sent,
)

# A sequel the list writes with a Roman numeral and TMDb with a digit (a title variant resolve tries).
_SEQUEL: FilmIdentity = {
    "title": "Placeholder Story II",
    "title_key": "placeholder story ii",
    "year": 2006,
    "tmdb_id": "1000013",
}
_SEQUEL_TMDB = "placeholder story 2"


def _real(
    rows: list[ResolvedRow], account: frozenset[FilmKey]
) -> export_select.Selection:
    """``select`` with export_account's own ``on_service``, as the CLI runs it."""
    return export_select.select(
        rows,
        cutoff=None,
        account=imported(account),
        on_service=export_account.on_service,
        watched_dates=True,
    )


class TmdbTitleTest(unittest.TestCase):
    """A film resolve settled under another title is matched by that title too."""

    def test_a_film_on_the_account_by_its_tmdb_title_is_not_sent(self) -> None:
        # Regression: only the list's title was matched, so the sequel was sent
        # and its old rating overwrote the live one.
        row = make_row(**_SEQUEL, tmdb_title_keys=(_SEQUEL_TMDB,))
        chosen = select([row], account=frozenset({FilmKey(_SEQUEL_TMDB, 2006)}))
        self.assertEqual(chosen.films, ())
        self.assertEqual(kinds(chosen), [("on-service", ("10/seen.txt:1",))])

    def test_a_film_on_the_account_by_its_original_title_is_not_sent(self) -> None:
        original = "histoire factice 2"
        row = make_row(**_SEQUEL, tmdb_title_keys=(_SEQUEL_TMDB, original))
        chosen = select([row], account=frozenset({FilmKey(original, 2006)}))
        self.assertEqual(chosen.films, ())

    def test_a_tmdb_title_in_another_year_is_not_a_match(self) -> None:
        row = make_row(**_SEQUEL, tmdb_title_keys=(_SEQUEL_TMDB,))
        chosen = select([row], account=frozenset({FilmKey(_SEQUEL_TMDB, 1990)}))
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])

    def test_a_film_sent_under_a_held_films_tmdb_title_is_held(self) -> None:
        # Sending the sequel would put its TMDb title on the account, and the next run
        # would skip the held film of that title as already there.
        rows = [
            make_row(**_SEQUEL, tmdb_title_keys=(_SEQUEL_TMDB,), source_line=1),
            make_row(
                title="Placeholder Story 2",
                title_key=_SEQUEL_TMDB,
                year=2006,
                tmdb_id="",
                status="ambiguous",
                source_line=2,
            ),
        ]
        chosen = select(rows)
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            [entry.kind for entry in chosen.review], ["held-ambiguous"] * 2
        )

    def test_an_override_with_no_tmdb_title_is_sent_and_listed(self) -> None:
        chosen = select([make_row(status="override")])
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])
        [entry] = chosen.review
        self.assertEqual(entry.kind, "override-unchecked")
        self.assertFalse(entry.error)
        self.assertIn("by hand", entry.detail)

    def test_a_resolved_row_with_no_tmdb_title_is_sent_and_listed(self) -> None:
        # Regression: a hand-edited CSV's resolved row with no TMDb title was matched
        # by its list title alone, and nothing said so.
        chosen = select([make_row(tmdb_title_keys=())])
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])
        [entry] = chosen.review
        self.assertEqual(entry.kind, "override-unchecked")
        self.assertFalse(entry.error)
        self.assertIn("by hand", entry.detail)
        self.assertNotIn("override", entry.detail)

    def test_an_override_with_a_tmdb_title_is_not_listed(self) -> None:
        row = make_row(status="override", tmdb_title_keys=("placeholder film one",))
        self.assertEqual(select([row]).review, ())

    def test_an_override_with_no_id_is_not_listed_as_unchecked(self) -> None:
        chosen = select([make_row(status="override", tmdb_id="")])
        self.assertEqual([entry.kind for entry in chosen.review], ["by-title"])


class OverrideDetailsTest(unittest.TestCase):
    """An override row resolve filled from TMDb's details is checked by its titles.

    Read from a CSV as resolve writes one: the list's title, a year resolve took from
    the details, and both TMDb titles beside the override's id.
    """

    def _override_row(self) -> list[ResolvedRow]:
        """The override row, read back through ``export_input.read_resolved``."""
        row = resolved_row(
            title="Placeholder Night",
            year="1999",
            resolve_status="override",
            tmdb_id="1000021",
            tmdb_title="Placeholder Nights",
            tmdb_original_title="Nuits Factices",
        )
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_resolved(Path(tmp), {"film-2010.csv": csv_text([row])})
            return list(export_input.read_resolved(folder, "film"))

    def test_it_is_skipped_when_the_account_holds_its_tmdb_title(self) -> None:
        account = frozenset({FilmKey("placeholder nights", 1999)})
        chosen = _real(self._override_row(), account)
        self.assertEqual(chosen.films, ())
        self.assertEqual(kinds(chosen), [("on-service", ("10/seen.txt:1",))])

    def test_it_is_skipped_when_the_account_holds_its_original_title(self) -> None:
        account = frozenset({FilmKey("nuits factices", 1999)})
        self.assertEqual(_real(self._override_row(), account).films, ())

    def test_a_same_titled_film_of_another_year_does_not_skip_it(self) -> None:
        account = frozenset({FilmKey("placeholder night", 2015)})
        chosen = _real(self._override_row(), account)
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])
        self.assertNotIn("override-unchecked", [e.kind for e in chosen.review])


class OnServiceDetailTest(unittest.TestCase):
    """The detail says a match was by title alone whichever side lacked a year."""

    def test_a_row_with_no_year_says_it_matched_by_title_alone(self) -> None:
        account = frozenset({FilmKey("placeholder film one", 1999)})
        chosen = _real([make_row(year=None)], account)
        [entry] = chosen.review
        self.assertEqual(entry.kind, "on-service")
        self.assertEqual(
            entry.detail,
            "an earlier import sent it, so not sent again; the row has no year, so "
            "matched by title alone",
        )

    def test_an_exact_match_does_not_say_title_alone(self) -> None:
        account = frozenset({FilmKey("placeholder film one", 1999)})
        [entry] = select([make_row()], account=account).review
        self.assertNotIn("title alone", entry.detail)


class SplitTitleTest(unittest.TestCase):
    """Rows of one title split into a tmdbID film and a title-only film are held."""

    def _split(self) -> list[ResolvedRow]:
        return [
            make_row(source_line=1),
            make_row(tmdb_id="", status="not-found", year=None, source_line=2),
        ]

    def test_both_halves_are_held_as_errors(self) -> None:
        # Regression: the two halves were sent as two films.
        chosen = select(self._split())
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            kinds(chosen),
            [
                ("held-split", ("10/seen.txt:1",)),
                ("held-split", ("10/seen.txt:2",)),
            ],
        )
        self.assertTrue(all(entry.error for entry in chosen.review))
        details = [entry.detail for entry in chosen.review]
        self.assertIn("no tmdbID", details[0])
        self.assertIn("have a tmdbID", details[1])
        self.assertTrue(all("override" in detail for detail in details))

    def test_a_split_in_another_year_is_still_held(self) -> None:
        rows = [
            make_row(source_line=1),
            make_row(tmdb_id="", status="not-found", year=2005, source_line=2),
        ]
        self.assertEqual(
            [entry.kind for entry in select(rows).review], ["held-split"] * 2
        )

    def test_a_held_ambiguous_half_keeps_its_own_kind(self) -> None:
        rows = [
            make_row(source_line=1),
            make_row(tmdb_id="", status="ambiguous", source_line=2),
        ]
        self.assertEqual(
            [entry.kind for entry in select(rows).review], ["held-ambiguous"] * 2
        )

    def test_two_tmdb_films_of_one_title_are_not_a_split(self) -> None:
        rows = [make_row(source_line=1), make_row(tmdb_id="7", source_line=2)]
        self.assertEqual(len(select(rows).films), 2)

    def test_a_title_only_film_named_as_a_tmdb_title_is_a_split(self) -> None:
        # Regression: only list titles were compared, so a title-only film whose list
        # title was a resolved film's TMDb title was sent beside it.
        rows = [
            make_row(**_SEQUEL, tmdb_title_keys=(_SEQUEL_TMDB,), source_line=1),
            make_row(
                title="Placeholder Story 2",
                title_key=_SEQUEL_TMDB,
                year=2006,
                tmdb_id="",
                status="not-found",
                source_line=2,
            ),
        ]
        self.assertEqual(
            [entry.kind for entry in select(rows).review], ["held-split"] * 2
        )

    def test_a_split_hold_passes_on_to_a_film_sharing_a_title(self) -> None:
        # The third film shares only the split film's original title, which no
        # title-only film carries, so it is held for hiding that film, not as a split.
        original = "histoire factice 1"
        rows = [
            make_row(tmdb_title_keys=("placeholder film one", original), source_line=1),
            self._split()[1],
            make_row(
                tmdb_id="7",
                title="Metropolis",
                title_key="metropolis",
                tmdb_title_keys=(original,),
                source_line=3,
            ),
        ]
        chosen = select(rows)
        self.assertEqual(chosen.films, ())
        self.assertEqual([entry.kind for entry in chosen.review], ["held-split"] * 3)
        self.assertIn("another film of this title", chosen.review[2].detail)


class _Untitled(TypedDict, total=False):
    """The row fields ``FoldedTitleTest._row`` takes besides the title."""

    status: str
    tmdb_id: str
    tmdb_title_keys: tuple[str, ...]
    source_line: int


class FoldedTitleTest(unittest.TestCase):
    """A title differing only by accents or "&" and "and" is the same title.

    Folded alike on both sides, for the account diff and the held films only.
    """

    def _account(self, name: str) -> frozenset[FilmKey]:
        """The account export's keys for one film of ``name`` in 1999."""
        rows = export_account.parse_account_csv(f"Name,Year\n{name},1999\n")
        return frozenset(key for row in rows for key in row.keys)

    def _row(self, title: str, **changes: Unpack[_Untitled]) -> ResolvedRow:
        """A row of ``title`` in 1999, its title key as parse gives it."""
        return make_row(title=title, title_key=title_key(title), **changes)

    def test_an_accent_on_either_side_still_matches(self) -> None:
        # Regression: the film was sent, and its old rating overwrote the live one.
        for listed, named in (
            ("Placeholder Café", "Placeholder Cafe"),
            ("Placeholder Cafe", "Placeholder Café"),
        ):
            with self.subTest(listed=listed, named=named):
                chosen = _real([self._row(listed)], self._account(named))
                self.assertEqual(chosen.films, ())
                self.assertEqual([e.kind for e in chosen.review], ["on-service"])

    def test_an_ampersand_matches_and_on_either_side(self) -> None:
        for listed, named in (
            ("Placeholder Cats & Dogs", "Placeholder Cats and Dogs"),
            ("Placeholder Cats and Dogs", "Placeholder Cats&Dogs"),
        ):
            with self.subTest(listed=listed, named=named):
                chosen = _real([self._row(listed)], self._account(named))
                self.assertEqual(chosen.films, ())

    def test_a_folded_tmdb_title_matches(self) -> None:
        row = self._row(
            "Placeholder Story II",
            tmdb_title_keys=("placeholder cafe and co", "placeholder café co"),
        )
        chosen = _real([row], self._account("Placeholder Cafe and Co"))
        self.assertEqual(chosen.films, ())

    def test_a_title_key_match_still_matches(self) -> None:
        # Folding only adds matches: "&" dropped by title_key still meets its absence.
        chosen = _real([self._row("Placeholder A&B")], self._account("Placeholder AB"))
        self.assertEqual(chosen.films, ())

    def test_a_folded_title_shares_a_hold(self) -> None:
        rows = [
            self._row("Placeholder Café", source_line=1),
            self._row(
                "Placeholder Cafe", tmdb_id="", status="ambiguous", source_line=2
            ),
        ]
        chosen = select(rows)
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            [entry.kind for entry in chosen.review], ["held-ambiguous"] * 2
        )

    def test_a_folded_title_splits_a_tmdb_film_and_a_title_only_film(self) -> None:
        rows = [
            self._row("Placeholder Café", source_line=1),
            self._row(
                "Placeholder Cafe", tmdb_id="", status="not-found", source_line=2
            ),
        ]
        self.assertEqual(
            [entry.kind for entry in select(rows).review], ["held-split"] * 2
        )

    def test_a_different_title_is_still_sent(self) -> None:
        chosen = _real([self._row("Placeholder Café")], self._account("Metropolis"))
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])


class NoTitleTest(unittest.TestCase):
    """A film with no title the account could know it by is held."""

    # A title with no letter or digit, and the empty title_key parse gives it.
    _TITLE, _KEY = "!!!", ""

    def test_a_film_with_no_title_key_is_held_as_an_error(self) -> None:
        # Regression: it could never match the account, so it was sent every run.
        chosen = select(
            [
                make_row(
                    title=self._TITLE,
                    title_key=self._KEY,
                    tmdb_id="",
                    status="not-found",
                )
            ]
        )
        self.assertEqual(chosen.films, ())
        self.assertEqual(kinds(chosen), [("held-no-title", ("10/seen.txt:1",))])
        self.assertTrue(chosen.review[0].error)

    def test_a_held_blank_title_holds_no_other_blank_title(self) -> None:
        # Regression: a held "" key held every other film whose title had no letter.
        rows = [
            make_row(
                title=self._TITLE,
                title_key=self._KEY,
                tmdb_id="",
                status="ambiguous",
                source_line=1,
            ),
            make_row(
                title=self._TITLE,
                title_key=self._KEY,
                tmdb_title_keys=("x",),
                source_line=2,
            ),
        ]
        chosen = select(rows)
        self.assertEqual(sent(chosen), ["10/seen.txt:2"])
        self.assertEqual([entry.kind for entry in chosen.review], ["held-ambiguous"])

    def test_a_blank_list_title_with_a_tmdb_title_is_sent(self) -> None:
        row = make_row(
            title=self._TITLE,
            title_key=self._KEY,
            tmdb_title_keys=("placeholder film one",),
        )
        chosen = select([row])
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])
        account = frozenset({FilmKey("placeholder film one", 1999)})
        self.assertEqual(select([row], account=account).films, ())


if __name__ == "__main__":
    unittest.main()
