"""Tests for the omit list: ``letterboxd.omit`` in the config, and the films it leaves out.

Each entry names a film by its title, matched as the cutoff's title is, and its year
(none for a film with no year). ``select`` leaves out every film one of whose rows an
entry names, before any hold, and lists it ``omitted``; ``check_omit`` refuses an entry
naming no row at all, so a stale or mistyped entry is noticed.
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
sys.path.insert(0, str(_TOOL.parent / "retrolog_survey" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_parse" / "src"))
sys.path.insert(0, str(_TOOL.parent / "retrolog_resolve" / "src"))
sys.path.insert(0, str(_TOOL / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import export_config  # noqa: E402 - after the sys.path inserts above
import export_select  # noqa: E402 - after the sys.path inserts above
from export_fixtures import TomlValue, config_toml  # noqa: E402 - after the inserts
from export_strategies import resolved_rows  # noqa: E402 - after the sys.path inserts
from export_types import (  # noqa: E402 - after the sys.path inserts above
    Account,
    Cutoff,
    LetterboxdConfig,
    OmitEntry,
    ResolvedRow,
)
from select_case import (  # noqa: E402 - after the sys.path inserts above
    METROPOLIS,
    day,
    in_account,
    kinds,
    make_row,
    sent,
)

_SAMPLE = _TOOL / "samples" / "export.example.toml"


def _letterboxd(*entries: dict[str, TomlValue]) -> LetterboxdConfig:
    """The ``[letterboxd]`` table of a config omitting ``entries``."""
    config = export_config.load_config(config_toml(omit_films=entries)).letterboxd
    assert config is not None
    return config


def _refusal(*entries: dict[str, TomlValue]) -> str:
    """The ConfigError message for a config omitting ``entries``."""
    try:
        export_config.load_config(config_toml(omit_films=entries))
    except export_config.ConfigError as error:
        return str(error)
    raise AssertionError("load_config accepted the config")


def _select(
    rows: list[ResolvedRow], omit: tuple[OmitEntry, ...]
) -> export_select.Selection:
    """``select`` on an empty account, no cutoff, leaving out ``omit``."""
    return export_select.select(
        rows,
        cutoff=None,
        account=Account(),
        on_service=in_account,
        watched_dates=True,
        omit=omit,
    )


class OmitConfigTest(unittest.TestCase):
    def test_no_omit_key_omits_nothing(self) -> None:
        self.assertEqual(_letterboxd().omit, ())

    def test_entries_keep_their_title_key_and_year_in_order(self) -> None:
        config = _letterboxd(
            {"title": "  Placeholder FILM, Two ", "year": 2001},
            {"title": "Metropolis"},
        )
        self.assertEqual(
            config.omit,
            (OmitEntry("placeholder film two", 2001), OmitEntry("metropolis", None)),
        )

    def test_a_title_without_a_letter_or_digit_is_refused_by_its_index(self) -> None:
        message = _refusal({"title": "Metropolis"}, {"title": "???"})
        self.assertIn("letterboxd.omit[2].title", message)
        self.assertNotIn("???", message)

    def test_a_missing_title_or_an_unknown_key_is_refused(self) -> None:
        self.assertIn("letterboxd.omit[1].title: missing", _refusal({"year": 2001}))
        self.assertIn(
            "unknown key 'tmdb'", _refusal({"title": "Metropolis", "tmdb": "1"})
        )

    def test_bad_values_are_refused_without_quoting_them(self) -> None:
        cases: list[dict[str, TomlValue]] = [
            {"title": 7},
            {"title": "Metropolis", "year": "1927"},
            {"title": "Metropolis", "year": 0},
            {"title": "Metropolis", "year": True},
        ]
        for entry in cases:
            with self.subTest(entry=entry):
                message = _refusal(entry)
                self.assertTrue(message.startswith("letterboxd.omit[1]."), message)
                self.assertNotIn("1927", message)

    def test_omit_not_a_list_of_tables_is_refused(self) -> None:
        for value in ('"Metropolis"', '["Metropolis"]'):
            row = b'"review_note_rows" = "commented"'
            data = config_toml().replace(row, row + b"\nomit = " + value.encode())
            with self.subTest(value=value):
                with self.assertRaises(export_config.ConfigError) as caught:
                    export_config.load_config(data)
                self.assertIn("letterboxd.omit", str(caught.exception))

    def test_a_second_entry_for_the_same_film_is_refused(self) -> None:
        message = _refusal(
            {"title": "Metropolis", "year": 1927}, {"title": "METROPOLIS", "year": 1927}
        )
        self.assertIn("letterboxd.omit[2]: names the same film as", message)
        self.assertIn("letterboxd.omit[1]", message)

    def test_the_same_title_in_another_year_is_another_film(self) -> None:
        config = _letterboxd(
            {"title": "Metropolis", "year": 1927}, {"title": "Metropolis"}
        )
        self.assertEqual(len(config.omit), 2)

    def test_the_sample_omits_one_film(self) -> None:
        config = export_config.load_config(_SAMPLE.read_bytes()).letterboxd
        assert config is not None
        self.assertEqual(len(config.omit), 1)


class CheckOmitTest(unittest.TestCase):
    def test_an_entry_naming_a_row_passes(self) -> None:
        config = _letterboxd({"title": "Metropolis", "year": 1927})
        export_config.check_omit(config, [make_row(**METROPOLIS)])

    def test_an_entry_naming_no_row_is_refused_by_its_index(self) -> None:
        config = _letterboxd(
            {"title": "Metropolis", "year": 1927},
            {"title": "Metropolis", "year": 1928},
        )
        with self.assertRaises(export_config.ConfigError) as caught:
            export_config.check_omit(config, [make_row(**METROPOLIS)])
        self.assertEqual(str(caught.exception), "letterboxd.omit[2]: matches no row")

    def test_a_yearless_entry_does_not_match_a_row_with_a_year(self) -> None:
        config = _letterboxd({"title": "Metropolis"})
        with self.assertRaises(export_config.ConfigError):
            export_config.check_omit(config, [make_row(**METROPOLIS)])
        export_config.check_omit(
            config,
            [
                make_row(
                    title="Metropolis",
                    title_key="metropolis",
                    year=None,
                    tmdb_id="1000002",
                )
            ],
        )


class SelectOmitTest(unittest.TestCase):
    def test_an_omitted_film_is_listed_and_not_sent(self) -> None:
        rows = [make_row(), make_row(**METROPOLIS, source_line=2)]
        chosen = _select(rows, (OmitEntry("metropolis", 1927),))
        self.assertEqual(sent(chosen), ["10/seen.txt:1"])
        self.assertEqual(kinds(chosen), [("omitted", ("10/seen.txt:2",))])
        self.assertFalse(chosen.review[0].error)
        self.assertIn("letterboxd.omit", chosen.review[0].detail)

    def test_one_matching_row_omits_every_row_of_its_film(self) -> None:
        rows = [
            make_row(**METROPOLIS),
            make_row(
                title="Metropolis (restored)",
                title_key="metropolis restored",
                year=1927,
                tmdb_id="1000002",
                source_line=2,
            ),
        ]
        chosen = _select(rows, (OmitEntry("metropolis", 1927),))
        self.assertEqual(chosen.films, ())
        self.assertEqual(
            kinds(chosen), [("omitted", ("10/seen.txt:1", "10/seen.txt:2"))]
        )

    def test_an_omitted_half_of_a_split_title_holds_nothing_back(self) -> None:
        by_id = make_row(**METROPOLIS)
        by_title = make_row(
            title="Metropolis",
            title_key="metropolis",
            year=1927,
            tmdb_id="",
            status="not-found",
            source_line=2,
        )
        held = _select([by_id, by_title], ())
        self.assertEqual(
            [kind for kind, _ in kinds(held)], ["held-split", "held-split"]
        )
        chosen = _select([by_id, by_title], (OmitEntry("metropolis", 1927),))
        # Both halves carry the title and year, so the entry leaves both out.
        self.assertEqual(chosen.films, ())
        self.assertEqual([kind for kind, _ in kinds(chosen)], ["omitted", "omitted"])

    def test_an_omitted_held_film_passes_no_hold_on(self) -> None:
        # The entry names the ambiguous film by its list title alone; the other film
        # shares that title only as its TMDb title, so only an omit taken out before
        # the holds lets it go.
        held = make_row(
            title="Metropolis",
            title_key="metropolis",
            year=1927,
            tmdb_id="",
            status="ambiguous",
        )
        restored = make_row(
            title="Metropolis Restored",
            title_key="metropolis restored",
            year=1927,
            tmdb_id="1000002",
            tmdb_title_keys=("metropolis",),
            source_line=2,
        )
        self.assertEqual(
            [kind for kind, _ in kinds(_select([held, restored], ()))],
            ["held-ambiguous", "held-ambiguous"],
        )
        chosen = _select([held, restored], (OmitEntry("metropolis", 1927),))
        self.assertEqual(sent(chosen), ["10/seen.txt:2"])
        self.assertEqual(kinds(chosen), [("omitted", ("10/seen.txt:1",))])

    def test_an_omitted_by_title_film_splits_no_title(self) -> None:
        by_title = make_row(
            title="Metropolis",
            title_key="metropolis",
            year=1927,
            tmdb_id="",
            status="not-found",
        )
        by_id = make_row(
            title="Metropolis Restored",
            title_key="metropolis restored",
            year=1927,
            tmdb_id="1000002",
            tmdb_title_keys=("metropolis",),
            source_line=2,
        )
        self.assertEqual(
            [kind for kind, _ in kinds(_select([by_title, by_id], ()))],
            ["held-split", "held-split"],
        )
        chosen = _select([by_title, by_id], (OmitEntry("metropolis", 1927),))
        self.assertEqual(sent(chosen), ["10/seen.txt:2"])
        self.assertEqual(kinds(chosen), [("omitted", ("10/seen.txt:1",))])

    def test_an_entry_naming_only_a_cut_row_omits_its_films_kept_rows(self) -> None:
        # The entry names the film's only row at the cutoff, which is cut; check_omit
        # accepts it, so the film's earlier row must still be left out, not sent.
        kept = make_row(
            title="Alpha Cut",
            title_key="alpha cut",
            year=2001,
            tmdb_id="9",
            date=day(4),
            source_file="list.txt",
        )
        named = make_row(
            title="Alpha",
            title_key="alpha",
            year=2001,
            tmdb_id="9",
            date=day(6),
            source_file="list.txt",
            source_line=5,
        )
        chosen = export_select.select(
            [kept, named],
            cutoff=Cutoff("list.txt", 5, "alpha"),
            account=Account(),
            on_service=in_account,
            watched_dates=True,
            omit=(OmitEntry("alpha", 2001),),
        )
        self.assertEqual(chosen.films, ())
        self.assertEqual(chosen.cut, 1)
        self.assertEqual(kinds(chosen), [("omitted", ("list.txt:1",))])

    def test_omitted_follows_the_error_kinds_and_is_not_one(self) -> None:
        position = export_select.REVIEW_KINDS.index("omitted")
        self.assertEqual(export_select.REVIEW_KINDS[position - 1], "held-no-title")
        self.assertEqual(export_select.REVIEW_KINDS[position + 1], "on-service")
        self.assertNotIn("omitted", export_select.ERROR_KINDS)

    @settings(deadline=None)
    @given(
        st.lists(resolved_rows(), max_size=30, unique_by=lambda row: row.where),
        st.data(),
    )
    def test_every_row_is_omitted_held_or_sent_once(
        self, rows: list[ResolvedRow], data: st.DataObject
    ) -> None:
        named = data.draw(st.lists(st.sampled_from(rows), max_size=3)) if rows else []
        omit = tuple(dict.fromkeys(OmitEntry(r.title_key, r.year) for r in named))
        chosen = _select(rows, omit)
        placed = Counter(sent(chosen)) + Counter(
            where
            for entry in chosen.review
            if entry.kind.startswith("held-") or entry.kind == "omitted"
            for where in entry.rows
        )
        self.assertEqual(sum(placed.values()), len(rows))
        self.assertTrue(all(count == 1 for count in placed.values()))
        omitted = {(entry.title_key, entry.year) for entry in omit}
        for film in chosen.films:
            for row in film.rows:
                self.assertNotIn((row.title_key, row.year), omitted)


if __name__ == "__main__":
    unittest.main()
