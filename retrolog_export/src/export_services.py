"""The services export writes import files for, and what makes each one different.

A ``ServiceSpec`` gathers everything service-specific: the medium it imports, the
import files' name and columns, how its items are keyed, how its account export is
read and each item's fate on it decided, how a sent item becomes its import rows,
which table of the export config it reads and what it checks there, and its file
caps. It is generic in the types of that config table (``C``) and of the account
export as read (``A``), so a book service fills it with its own config and account
types. Selection, packing, and output take a spec and know nothing of any one
service; a caller holding ``AnySpec``, either service's, runs one through
``ServiceSpec.apply``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import export_account
import export_goodreads
import export_goodreads_account
import export_letterboxd
from export_config import check_undated
from export_goodreads_account import GoodreadsAccount
from export_letterboxd_select import LetterboxdRules
from export_select import Fate, Hold, Item, film_key, film_splits
from export_types import (
    Account,
    ExportConfig,
    GoodreadsConfig,
    LetterboxdConfig,
    Medium,
    ResolvedRow,
    ServiceName,
)

# Letterboxd's importer takes up to 1,900 titles a file, and files of at most 1 MB;
# 1,000,000 bytes is under 1 MB however the megabyte is counted.
_LETTERBOXD_ROWS, _LETTERBOXD_BYTES = 1_900, 1_000_000
# Goodreads documents no cap on an import's rows or size, so its defaults make one
# file of any list; --rows-per-file and --max-bytes still split it.
_GOODREADS_ROWS, _GOODREADS_BYTES = 10_000, 10_000_000


@dataclass(frozen=True)
class ServiceSpec[C, A]:
    """One service's import format and account export.

    ``C`` is the service's checked table of the export config, and ``A`` its account
    export as ``read_account`` returns it.
    """

    name: ServiceName
    medium: Medium  # the one medium the service imports
    file_stem: str  # import files are "<file_stem>-001.csv" onward
    columns: tuple[str, ...]  # every import file's header, in order
    # The columns of the operator's own text, checked for a formula start.
    formula_columns: tuple[str, ...]
    # The columns one of which a row must fill for the import to tag it: a row with
    # neither a date nor review text makes no diary entry or review to hold a tag.
    tag_columns: tuple[str, ...]
    # The item a row belongs to; rows of one key are sent, held, or left out together.
    item_key: Callable[[ResolvedRow], str]
    read_account: Callable[[Path], A]
    # A free item's fate on the account: the item to send, if any, and its entries.
    account_fate: Callable[[Item, A, C], Fate]
    # Grows the holds in place to a fixed point: each item that sending would hide a
    # held one behind on the next run's diff is held too.
    grow_holds: Callable[[Sequence[Item], dict[str, Hold], C], None]
    # Each item split from another item that is the same film or book, which sending
    # both would put on the account twice or overwrite: its key, to its held-split
    # entry's detail. Given the account and the table, as account_fate is.
    split_holds: Callable[[Sequence[Item], A, C], Mapping[str, str]]
    # A sent item's import rows: one per row of the item, each one cell per column.
    item_cells: Callable[[Item, C], tuple[tuple[str, ...], ...]]
    # The service's table of the export config; None when the file has none.
    config: Callable[[ExportConfig], C | None]
    # The service's own checks of the rows read against its table, beyond the rating
    # keys and the omit list every service has: export_config.ConfigError, raised
    # before anything is written.
    check_rows: Callable[[C, Sequence[ResolvedRow]], None]
    # The most data rows, and bytes, one import file holds: each is the default, and
    # the most --rows-per-file and --max-bytes may give.
    default_rows: int
    default_bytes: int
    test_run: bool  # whether --test-film's one-item test run applies

    def apply[R](self, run: Callable[[ServiceSpec[C, A]], R]) -> R:
        """``run(self)``: how a caller holding ``AnySpec`` runs a generic function.

        A type checker cannot infer ``C`` and ``A`` from a union of specs, but it
        checks this call on each member of the union, so ``run`` stays generic.
        """
        return run(self)


def _letterboxd_config(config: ExportConfig) -> LetterboxdConfig | None:
    """The export config's ``[letterboxd]`` table."""
    return config.letterboxd


def _letterboxd_cells(
    film: Item, config: LetterboxdConfig
) -> tuple[tuple[str, ...], ...]:
    """Each of ``film``'s rows as an import row, its stars withheld when logged."""
    return tuple(
        export_letterboxd.row_cells(row, config, film.withhold_rating)
        for row in film.rows
    )


def _letterboxd_splits(
    films: Sequence[Item], account: Account, config: LetterboxdConfig
) -> dict[str, str]:
    """``film_splits``: a film's split is held whatever the account holds."""
    del account, config  # the films' own titles alone decide a film split
    return film_splits(films)


def _letterboxd_checks(config: LetterboxdConfig, rows: Sequence[ResolvedRow]) -> None:
    """Nothing: a ``[letterboxd]`` table needs only the checks every service shares."""
    del config, rows  # the shared checks cover the table


def _goodreads_config(config: ExportConfig) -> GoodreadsConfig | None:
    """The export config's ``[goodreads]`` table."""
    return config.goodreads


def _goodreads_checks(config: GoodreadsConfig, rows: Sequence[ResolvedRow]) -> None:
    """An undated row needs the table's ``undated_date_added`` (``check_undated``)."""
    check_undated(config, rows, where="goodreads")


# Letterboxd's account classes and hold growth, by the account reader's film match.
LETTERBOXD_RULES = LetterboxdRules(export_account.on_service)

LETTERBOXD: ServiceSpec[LetterboxdConfig, Account] = ServiceSpec(
    name="letterboxd",
    medium="film",
    file_stem="letterboxd",
    columns=export_letterboxd.COLUMNS,
    formula_columns=("Title", "Directors", "Review"),
    tag_columns=("WatchedDate", "Review"),
    item_key=film_key,
    read_account=export_account.read_account,
    account_fate=LETTERBOXD_RULES.account_fate,
    grow_holds=LETTERBOXD_RULES.grow_holds,
    split_holds=_letterboxd_splits,
    item_cells=_letterboxd_cells,
    config=_letterboxd_config,
    check_rows=_letterboxd_checks,
    default_rows=_LETTERBOXD_ROWS,
    default_bytes=_LETTERBOXD_BYTES,
    test_run=True,
)

GOODREADS: ServiceSpec[GoodreadsConfig, GoodreadsAccount] = ServiceSpec(
    name="goodreads",
    medium="book",
    file_stem="goodreads",
    columns=export_goodreads.COLUMNS,
    # Additional Authors is Open Library's author names, which anyone can edit.
    formula_columns=(
        "Title",
        "Author",
        "Additional Authors",
        "My Review",
        "Private Notes",
    ),
    # Every row carries the config's shelves, which are the import's tag.
    tag_columns=("Bookshelves",),
    item_key=export_goodreads_account.book_key,
    read_account=export_goodreads_account.read_account,
    account_fate=export_goodreads.book_fate,
    grow_holds=export_goodreads_account.grow_holds,
    split_holds=export_goodreads_account.book_splits,
    item_cells=export_goodreads.item_cells,
    config=_goodreads_config,
    check_rows=_goodreads_checks,
    default_rows=_GOODREADS_ROWS,
    default_bytes=_GOODREADS_BYTES,
    test_run=False,
)

# Either service's spec: each is of its own config and account types.
type AnySpec = (
    ServiceSpec[LetterboxdConfig, Account]
    | ServiceSpec[GoodreadsConfig, GoodreadsAccount]
)

# Read-only, so no caller can add or swap a service at run time.
SERVICES: Mapping[ServiceName, AnySpec] = MappingProxyType(
    {"letterboxd": LETTERBOXD, "goodreads": GOODREADS}
)
