"""Read the films already on a Letterboxd account, from its "Export your data" download.

``read_account`` takes the ``.zip`` Letterboxd gives, or the folder it unpacks to, and
reads only the top-level ``watched.csv``, ``diary.csv``, ``ratings.csv``, and
``reviews.csv`` that exist: never ``deleted/`` or any other subfolder, whose films are
no longer on the account. Each file is capped at ``MEMBER_LIMIT`` bytes, so a damaged
or hostile export cannot fill memory; a zip is first held to ``ARCHIVE_LIMIT`` bytes,
``MAX_MEMBERS`` members, and a ``DIRECTORY_LIMIT``-byte directory, read from its end
record before ``zipfile`` builds an entry for every member. A film there is a
``FilmKey`` for each of its ``Name``'s ``export_titles.match_keys`` (its ``title_key``,
its folded key, and its hyphen key), each with its ``Year``. A cell may be as long as
its whole file's cap: ``csv_field_limit`` raises csv's process-wide field limit for
each parse, and other tools reading an export's CSVs use it too.

The files are kept apart in the ``Account`` it returns: ``watched.csv``'s films,
the films the owner logged (``diary.csv``, ``ratings.csv``, ``reviews.csv``), the
diary and review rows an earlier import tagged (a tag starting ``IMPORT_TAG_PREFIX``),
each diary entry's ``Watched Date``, and each tagged diary entry's ``Watched Date``.
The ``Tags`` and ``Watched Date`` columns are optional reads: a file without one
reads as no tag and no date.

``read_export_texts`` is the same reader opened to other tools: it returns the text of
whichever named files an export holds, a subfolder's file among them, under the same
bounds, and names the caller's own option in its errors.

Every ``AccountError`` names the option or a file's name, never a path
(an ``OSError``'s own message carries the absolute one) and never a title, which is the
operator's private list content.
"""

from __future__ import annotations

import csv
import datetime
import io
import os
import re
import struct
import zipfile
import zlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO

from export_types import IMPORT_TAG_PREFIX, Account, FilmKey
from export_titles import match_keys

# The account export's files that list a film the account holds, in the order read.
_WATCHED, _DIARY, _RATINGS, _REVIEWS = (
    "watched.csv",
    "diary.csv",
    "ratings.csv",
    "reviews.csv",
)
ACCOUNT_FILES = (_WATCHED, _DIARY, _RATINGS, _REVIEWS)
# The files whose rows carry the owner's own tags, so an earlier import's tag there
# marks the film imported; watched.csv and ratings.csv have no Tags column.
_TAGGED_FILES = frozenset({_DIARY, _REVIEWS})
# The two optional columns, and how the Tags cell separates its tags.
_TAGS_COLUMN, _DATE_COLUMN = "Tags", "Watched Date"
_TAG_SEPARATOR = ","
# A Watched Date as Letterboxd writes it, "2010-05-06"; anything else reads as no date.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
# The most uncompressed bytes read from any one file. Letterboxd's own files are far
# smaller even for a large account; the cap only stops a damaged or hostile export.
MEMBER_LIMIT = 32 * 1024 * 1024
# The largest account zip opened. The four files read are capped at MEMBER_LIMIT each
# and Letterboxd's other files (likes, lists, the deleted folder) are small, so eight
# times the cap leaves room for any real export while refusing a file no export is.
ARCHIVE_LIMIT = 8 * MEMBER_LIMIT
# The most members an account zip may hold. A real export holds a few dozen files,
# one per list among them; ten thousand leaves room for an account with thousands of
# lists, and stops an archive from making zipfile build an entry for millions.
MAX_MEMBERS = 10_000
# The largest central directory opened, at 512 bytes a member: a member's record is
# 46 bytes, plus a short name and a timestamp extra field. zipfile reads records until
# the directory's bytes run out, whatever count the end record gives, so this, not
# the count, bounds what opening the zip builds.
DIRECTORY_LIMIT = MAX_MEMBERS * 512
# The end-of-central-directory record (APPNOTE 4.3.16): signature, this disk, the
# directory's disk, entries here, entries in all, directory size, its offset, and the
# comment's length; then the comment, at most 0xFFFF bytes. A zip64 archive puts a
# 20-byte locator directly before it (4.3.15), and zipfile then reads its sizes there.
_END_RECORD = struct.Struct("<4s4H2LH")
_END_SIGNATURE = b"PK\x05\x06"
_MAX_COMMENT = 0xFFFF
_ZIP64_LOCATOR = b"PK\x06\x07"
_LOCATOR_SIZE = 20
# A year Letterboxd writes: ASCII digits, at most four. Longer runs read as no year
# rather than reaching int(), which refuses a string past its digit limit.
_YEAR = re.compile(r"[0-9]{1,4}", re.ASCII)
_BOM = "\ufeff"
# What zipfile raises for a damaged archive or member: a bad CRC or structure, a
# corrupt deflate stream, an encrypted member (RuntimeError, which also covers
# NotImplementedError), a truncated file, an unreadable one, and ValueError, raised for
# a central-directory offset that seeks before the start of a stream and, as its
# subclass UnicodeDecodeError, for a member name flagged UTF-8 that is not.
_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    RuntimeError,
    EOFError,
    OSError,
    ValueError,
)
# The only compression methods read. Letterboxd writes deflated members; every other
# method's decoder (bz2, lzma, zstd) raises an error class of its own on bad data, so a
# member using one is refused before it is opened rather than each class being listed.
_READ_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class AccountError(Exception):
    """The account export is unusable; the message never names a path or a title."""


@dataclass(frozen=True)
class AccountRow:
    """One row of an account export CSV: its film, its import tag, and its date."""

    keys: frozenset[FilmKey]  # one per match key of its Name, each with its Year
    retrolog_tagged: bool  # a tag of its Tags cell starts with IMPORT_TAG_PREFIX
    watched_date: datetime.date | None  # its Watched Date, when it has a valid one


@dataclass(frozen=True)
class _Columns:
    """Where a file's header puts each column read; None for an absent optional one."""

    name: int
    year: int
    tags: int | None
    date: int | None


def _year(text: str) -> int | None:
    """The ``Year`` cell: a year, or None when it is empty or not one."""
    return int(text) if _YEAR.fullmatch(text) else None


def _watched_date(text: str) -> datetime.date | None:
    """The ``Watched Date`` cell: a date, or None when it is empty or not one."""
    if not _ISO_DATE.fullmatch(text):
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:  # the right shape, but no such day, such as 2010-13-01
        return None


def _retrolog_tagged(text: str) -> bool:
    """Whether a tag of the ``Tags`` cell, stripped, starts with the import's prefix."""
    return any(
        # Matched ignoring case, so a tag found here is this tool's own import.
        tag.strip().casefold().startswith(IMPORT_TAG_PREFIX)
        for tag in text.split(_TAG_SEPARATOR)
    )


def _cell(cells: Sequence[str], at: int | None) -> str:
    """The cell at ``at``; "" when the column is absent or the row is short of it."""
    return cells[at] if at is not None and at < len(cells) else ""


def _row(cells: Sequence[str], at: _Columns) -> AccountRow:
    """One CSV row as an ``AccountRow``; a short row is read with the cells it has."""
    year = _year(_cell(cells, at.year))
    return AccountRow(
        keys=frozenset(FilmKey(key, year) for key in match_keys(_cell(cells, at.name))),
        retrolog_tagged=_retrolog_tagged(_cell(cells, at.tags)),
        watched_date=_watched_date(_cell(cells, at.date)),
    )


def _columns(header: list[str]) -> _Columns:
    """Where ``header`` puts each column; ``AccountError`` without Name or Year."""
    if "Name" not in header or "Year" not in header:
        raise AccountError("header lacks a Name or Year column")
    optional = {
        name: header.index(name) if name in header else None
        for name in (_TAGS_COLUMN, _DATE_COLUMN)
    }
    return _Columns(
        header.index("Name"),
        header.index("Year"),
        optional[_TAGS_COLUMN],
        optional[_DATE_COLUMN],
    )


def _rows(reader: Iterator[list[str]], at: _Columns) -> tuple[AccountRow, ...]:
    """Every row of ``reader`` with a film key.

    A row whose Name gives none is skipped.
    """
    return tuple(row for cells in reader if (row := _row(cells, at)).keys)


@contextmanager
def csv_field_limit() -> Iterator[None]:
    """Raise csv's field limit to ``MEMBER_LIMIT`` for the block, then restore it.

    csv's limit is process-wide and defaults to 131072 characters, short of a long
    review, while a cell can be as long as its whole file's cap. The previous limit
    is put back on exit, the block raising or not.
    """
    previous = csv.field_size_limit(MEMBER_LIMIT)
    try:
        yield
    finally:
        csv.field_size_limit(previous)


# fuzz-target
def parse_account_csv(text: str) -> tuple[AccountRow, ...]:
    """The rows one account export CSV lists, by ``Name``, ``Year``, ``Tags``, and date.

    ``Tags`` and ``Watched Date`` are optional; every other column is ignored, in any
    order. A leading byte order mark is accepted. Raises ``AccountError`` when the
    header lacks ``Name`` or ``Year``, or the text is not valid CSV.
    """
    reader = csv.reader(io.StringIO(text.removeprefix(_BOM), newline=""), strict=True)
    try:
        with csv_field_limit():
            return _rows(reader, _columns(next(reader, [])))
    except csv.Error:
        raise AccountError("not valid CSV") from None


def _decoded(name: str, data: bytes) -> str:
    """One file's bytes as text, refused past MEMBER_LIMIT or when not UTF-8."""
    if len(data) > MEMBER_LIMIT:
        raise AccountError(f"{name}: larger than {MEMBER_LIMIT} bytes")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AccountError(f"{name}: not UTF-8") from None


def _parsed(name: str, data: bytes) -> tuple[AccountRow, ...]:
    """One file's rows; an AccountError from parsing is given the file's name."""
    text = _decoded(name, data)
    try:
        return parse_account_csv(text)
    except AccountError as error:
        raise AccountError(f"{name}: {error}") from None


def _end_offset(tail: bytes) -> int | None:
    """Where zipfile finds the end record in ``tail``, the file's last bytes, if at all.

    As zipfile does: a record with no comment at the very end, else the last signature
    with a whole record after it; None when there is neither, and zipfile then refuses
    the file itself.
    """
    exact = len(tail) - _END_RECORD.size
    if exact >= 0 and tail[exact:].startswith(_END_SIGNATURE) and tail[-2:] == b"\0\0":
        return exact
    found = tail.rfind(_END_SIGNATURE)
    return found if found >= 0 and len(tail) - found >= _END_RECORD.size else None


def _check_end_record(handle: BinaryIO, option: str) -> None:
    """Refuse a zip whose end record declares too many members or too large a directory.

    A zip64 archive is refused outright, since its sizes live in another record; the
    refusal tells the operator to pass the unpacked folder instead. A file with no end
    record is left to zipfile to refuse.
    """
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - _END_RECORD.size - _MAX_COMMENT - _LOCATOR_SIZE))
    tail = handle.read()
    at = _end_offset(tail)
    if at is None:
        return
    if tail[max(0, at - _LOCATOR_SIZE) : at].startswith(_ZIP64_LOCATOR):
        raise AccountError(
            f"{option}: a zip64 archive, which is not read; unpack it and pass the "
            "folder instead"
        )
    _, _, _, here, total, directory, _, _ = _END_RECORD.unpack_from(tail, at)
    if max(here, total) > MAX_MEMBERS:
        raise AccountError(f"{option}: holds more than {MAX_MEMBERS} files")
    if directory > DIRECTORY_LIMIT:
        raise AccountError(
            f"{option}: its zip directory is larger than {DIRECTORY_LIMIT} bytes"
        )


def _check_archive(handle: BinaryIO, option: str) -> None:
    """Refuse a zip over ARCHIVE_LIMIT bytes, then one its end record says is too big.

    The size is the open file's own, so the file checked is the file opened.
    """
    if os.fstat(handle.fileno()).st_size > ARCHIVE_LIMIT:
        raise AccountError(f"{option}: larger than {ARCHIVE_LIMIT} bytes")
    _check_end_record(handle, option)


def _zip_texts(path: Path, names: Sequence[str], option: str) -> dict[str, bytes]:
    """Each member named in ``names``, as bytes, its declared size checked first.

    The archive's own bounds are checked before zipfile opens it, and its member count
    again after, since an end record can understate it. The read is capped at
    MEMBER_LIMIT + 1 bytes as well, because a member's header can lie about its size;
    ``_decoded`` refuses the one byte over.
    """
    with path.open("rb") as handle:
        _check_archive(handle, option)
        handle.seek(0)
        with zipfile.ZipFile(handle) as archive:
            return _member_texts(archive, names, option)


def _member_texts(
    archive: zipfile.ZipFile, names: Sequence[str], option: str
) -> dict[str, bytes]:
    """``_zip_texts``' reading of an opened archive, its member count checked first."""
    found: dict[str, bytes] = {}
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise AccountError(f"{option}: holds more than {MAX_MEMBERS} files")
    for info in infos:
        if info.filename not in names:
            continue  # a member not asked for, a directory entry or another file
        if info.file_size > MEMBER_LIMIT:
            raise AccountError(f"{info.filename}: larger than {MEMBER_LIMIT} bytes")
        if info.compress_type not in _READ_METHODS:
            raise AccountError(
                f"{info.filename}: compressed with an unsupported method"
            )
        with archive.open(info) as member:
            found[info.filename] = member.read(MEMBER_LIMIT + 1)
    return found


def _folder_texts(folder: Path, names: Sequence[str], option: str) -> dict[str, bytes]:
    """Each file of ``names`` at ``folder / name``, read up to MEMBER_LIMIT + 1."""
    found: dict[str, bytes] = {}
    for name in names:
        try:
            if not (folder / name).is_file():
                continue
        except OSError:
            raise AccountError(f"{option}: cannot be read") from None
        try:
            with (folder / name).open("rb") as handle:
                found[name] = handle.read(MEMBER_LIMIT + 1)
        except OSError:
            raise AccountError(f"{name}: cannot be read") from None
    return found


def _account_files(path: Path, names: Sequence[str], option: str) -> dict[str, bytes]:
    """The files of ``names`` that ``path`` holds, as a folder or else as a zip."""
    if path.is_dir():
        return _folder_texts(path, names, option)
    try:
        return _zip_texts(path, names, option)
    except _ZIP_ERRORS:
        raise AccountError(f"{option}: not a readable folder or zip") from None


def read_export_texts(path: Path, names: Sequence[str], option: str) -> dict[str, str]:
    """The text of each file of ``names`` the export at ``path`` holds.

    ``path`` is an account export's ``.zip`` or the folder it unpacks to, and a name
    is matched exactly: a zip member's whole name, or ``path / name`` in a folder, so
    ``likes/films.csv`` reads that subfolder's file. A name the export lacks is left
    out. Every bound and check ``read_account`` applies holds here, and an
    ``AccountError`` names ``option`` or the file, never a path or a title.
    """
    return {
        name: _decoded(name, data)
        for name, data in _account_files(path, names, option).items()
    }


def _diary_dates(
    rows: Sequence[AccountRow], *, tagged: bool = False
) -> Mapping[datetime.date, frozenset[FilmKey]]:
    """Each Watched Date of the diary's ``rows``, with the keys of its entries.

    With ``tagged``, only the entries an earlier import tagged count.
    """
    found: dict[datetime.date, set[FilmKey]] = {}
    for row in rows:
        if row.watched_date is not None and (row.retrolog_tagged or not tagged):
            found.setdefault(row.watched_date, set()).update(row.keys)
    return MappingProxyType({date: frozenset(keys) for date, keys in found.items()})


def _file_keys(
    parsed: Mapping[str, tuple[AccountRow, ...]],
    names: Iterable[str],
    *,
    tagged: bool = False,
) -> frozenset[FilmKey]:
    """The keys of every row of the files ``names``.

    With ``tagged``, only the rows an earlier import tagged count.
    """
    return frozenset(
        key
        for name in names
        for row in parsed.get(name, ())
        if row.retrolog_tagged or not tagged
        for key in row.keys
    )


def _account(parsed: Mapping[str, tuple[AccountRow, ...]]) -> Account:
    """The ``Account`` each file's rows make: see the module docstring."""
    return Account(
        watched=_file_keys(parsed, (_WATCHED,)),
        logged=_file_keys(parsed, (_DIARY, _RATINGS, _REVIEWS)),
        imported=_file_keys(parsed, _TAGGED_FILES, tagged=True),
        diary_dates=_diary_dates(parsed.get(_DIARY, ())),
        imported_dates=_diary_dates(parsed.get(_DIARY, ()), tagged=True),
    )


# fuzz-target
def read_account(path: Path) -> Account:
    """What the account export at ``path`` holds, each file kept apart (``Account``).

    Raises ``AccountError`` when ``path`` is neither a folder nor a readable zip, is a
    zip past ARCHIVE_LIMIT, MAX_MEMBERS, or DIRECTORY_LIMIT, or a zip64 one, holds
    none of ACCOUNT_FILES at its top level, or one of them is over MEMBER_LIMIT bytes,
    not UTF-8 (a byte order mark is accepted), or not an account CSV.
    """
    files = _account_files(path, ACCOUNT_FILES, "--account")
    if not files:
        raise AccountError(f"--account: holds none of {', '.join(ACCOUNT_FILES)}")
    return _account({name: _parsed(name, data) for name, data in files.items()})


def on_service(key: FilmKey, account: frozenset[FilmKey]) -> bool:
    """Whether the film ``key`` names is among ``account``, a set of an account's films.

    ``account`` is one of ``Account``'s sets (watched, logged, imported) or the keys
    of the diary entries of one date. An exact ``(title_key, year)`` match counts, and
    so does an entry of that title with no year, which matches any year of its title.
    A ``key`` with no year matches any entry of its title.
    """
    if key in account or FilmKey(key.title_key, None) in account:
        return True
    return key.year is None and any(
        entry.title_key == key.title_key for entry in account
    )
