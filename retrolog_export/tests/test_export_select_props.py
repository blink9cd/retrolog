"""Property tests for export_select: every row placed once, and no held watch lost.

Kept apart from test_export_select.py's examples so each file stays under the length
bar. The account check here is export_account's real ``on_service``, so the hold and
diff branches run under the same match the CLI uses.
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

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
import export_select  # noqa: E402 - after the sys.path inserts above
from export_strategies import accounts, resolved_rows  # noqa: E402 - after the inserts
from export_types import (  # noqa: E402 - after the sys.path inserts above
    Account,
    Cutoff,
    FilmKey,
    ResolvedRow,
)


def _keys(row: ResolvedRow) -> list[FilmKey]:
    """Every non-empty key a row's titles, list and TMDb, give it, with its year."""
    titles = (row.title_key, *row.tmdb_title_keys)
    return [FilmKey(key, row.year) for key in titles if key]


def _never(_key: FilmKey, _account: frozenset[FilmKey]) -> bool:
    """An ``on_service`` that finds nothing on the account."""
    return False


class SelectPropertyTest(unittest.TestCase):
    @settings(deadline=None)
    @given(st.lists(resolved_rows(), max_size=30, unique_by=lambda row: row.where))
    def test_fuzz_select(self, rows: list[ResolvedRow]) -> None:
        chosen = export_select.select(
            rows,
            cutoff=None,
            account=Account(),
            on_service=_never,
            watched_dates=True,
        )
        sent = [row.where for film in chosen.films for row in film.rows]
        held = [
            where
            for entry in chosen.review
            if entry.kind.startswith("held-")
            for where in entry.rows
        ]
        # no cutoff and an empty account: every input row is sent or held, exactly once
        self.assertEqual(Counter(sent) + Counter(held), Counter(r.where for r in rows))
        self.assertEqual(chosen.cut, 0)

    @settings(deadline=None)
    @given(
        st.lists(resolved_rows(), max_size=30, unique_by=lambda row: row.where),
        st.sets(st.builds(FilmKey, st.sampled_from(("metropolis",)), st.none())),
    )
    def test_no_sent_film_would_hide_a_held_one(
        self, rows: list[ResolvedRow], account: set[FilmKey]
    ) -> None:
        chosen = export_select.select(
            rows,
            cutoff=None,
            account=Account(logged=frozenset(account), imported=frozenset(account)),
            on_service=export_account.on_service,
            watched_dates=True,
        )
        sent = [row for film in chosen.films for row in film.rows]
        held = {
            where
            for entry in chosen.review
            if entry.kind.startswith("held-")
            for where in entry.rows
        }
        skipped = [
            where
            for entry in chosen.review
            if entry.kind == "on-service"
            for where in entry.rows
        ]
        # every row is sent, held, or skipped, exactly once
        placed = Counter(row.where for row in sent) + Counter(held) + Counter(skipped)
        self.assertEqual(placed, Counter(row.where for row in rows))
        # and no sent row, by any title it carries, would make a held row read as on
        # the account next run
        held_keys = [key for row in rows if row.where in held for key in _keys(row)]
        for row in sent:
            for sent_key in _keys(row):
                for key in held_keys:
                    self.assertFalse(
                        export_account.on_service(key, frozenset({sent_key}))
                    )

    @settings(deadline=None)
    @given(
        st.lists(resolved_rows(), max_size=30, unique_by=lambda row: row.where),
        st.data(),
    )
    def test_every_row_is_cut_held_skipped_or_sent_once(
        self, rows: list[ResolvedRow], data: st.DataObject
    ) -> None:
        dated = [row for row in rows if row.date is not None]
        cutoff = None
        if dated:
            named = data.draw(st.sampled_from(dated))
            cutoff = Cutoff(named.source_file, named.source_line, named.title_key)
        account = frozenset(
            data.draw(
                st.sets(
                    st.builds(FilmKey, st.just("metropolis"), st.none()), max_size=1
                )
            )
        )
        chosen = export_select.select(
            rows,
            cutoff=cutoff,
            account=Account(logged=account, imported=account),
            on_service=export_account.on_service,
            watched_dates=True,
        )
        placed = Counter(
            row.where for film in chosen.films for row in film.rows
        ) + Counter(
            where
            for entry in chosen.review
            if entry.kind.startswith("held-") or entry.kind == "on-service"
            for where in entry.rows
        )
        self.assertEqual(sum(placed.values()) + chosen.cut, len(rows))
        self.assertTrue(all(count == 1 for count in placed.values()))
        firsts = [export_select.row_order(film.rows[0]) for film in chosen.films]
        self.assertEqual(firsts, sorted(firsts))

    @settings(deadline=None)
    @given(
        st.lists(resolved_rows(), max_size=30, unique_by=lambda row: row.where),
        accounts(),
        st.booleans(),
    )
    def test_every_account_class_places_each_row_once(
        self, rows: list[ResolvedRow], account: Account, watched_dates: bool
    ) -> None:
        chosen = export_select.select(
            rows,
            cutoff=None,
            account=account,
            on_service=export_account.on_service,
            watched_dates=watched_dates,
        )
        # every row is sent, held, skipped, or dropped on a diary date, exactly once
        placed = Counter(
            row.where for film in chosen.films for row in film.rows
        ) + Counter(
            where
            for entry in chosen.review
            if entry.kind.startswith("held-")
            or entry.kind in ("on-service", "same-date")
            for where in entry.rows
        )
        self.assertEqual(placed, Counter(row.where for row in rows))
        # a film sent without stars is exactly one listed as logged
        logged = [
            set(entry.rows)
            for entry in chosen.review
            if entry.kind == "logged-no-stars"
        ]
        for film in chosen.films:
            sent_rows = {row.where for row in film.rows}
            self.assertEqual(film.withhold_rating, sent_rows in logged)
        if not watched_dates:
            self.assertNotIn("same-date", [entry.kind for entry in chosen.review])


if __name__ == "__main__":
    unittest.main()
