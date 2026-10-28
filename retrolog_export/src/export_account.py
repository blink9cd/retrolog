"""Read the films already on a Letterboxd account, from its "Export your data" download.

``read_account`` takes the ``.zip`` Letterboxd gives, or the folder it unpacks to, and
reads only the top-level ``watched.csv``, ``diary.csv``, ``ratings.csv`` and
``reviews.csv`` that exist: never ``deleted/`` or any other subfolder, whose films are
no longer on the account. Each file is capped at ``MEMBER_LIMIT`` bytes, so a damaged
or hostile export cannot fill memory; a zip is first held to ``ARCHIVE_LIMIT`` bytes,
``MAX_MEMBERS`` members and a ``DIRECTORY_LIMIT``-byte directory, read from its end
record before ``zipfile`` builds an entry for every member. A film there is a
``FilmKey`` for each of its ``Name``'s ``export_titles.match_keys`` (its ``title_key``
and its folded key), each with its ``Year``.

Every ``AccountError`` names the option or one of the four file names, never a path
(an ``OSError``'s own message carries the absolute one) and never a title, which is the
operator's private list content.
"""

from __future__ import annotations

import csv
import io
import os
import re
import struct
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from export_types import FilmKey
from export_titles import match_keys

# The account export's files that list a film the account holds, in the order read.
ACCOUNT_FILES = ("watched.csv", "diary.csv", "ratings.csv", "reviews.csv")
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


def _year(text: str) -> int | None:
    """The ``Year`` cell: a year, or None when it is empty or not one."""
    return int(text) if _YEAR.fullmatch(text) else None


def _keys(reader: Iterator[list[str]], name_at: int, year_at: int) -> set[FilmKey]:
    """Every row's ``FilmKey`` by each match key of its Name; one with none is skipped.

    A short row is read with the cells it has: a missing Year is no year.
    """
    keys: set[FilmKey] = set()
    for cells in reader:
        names = match_keys(cells[name_at]) if name_at < len(cells) else ()
        year = _year(cells[year_at]) if year_at < len(cells) else None
        keys.update(FilmKey(key, year) for key in names)
    return keys


def parse_account_csv(text: str) -> frozenset[FilmKey]:
    """The films one account export CSV lists, by its ``Name`` and ``Year`` columns.

    Every other column is ignored, in any order. A leading byte order mark is accepted.
    Raises ``AccountError`` when the header lacks ``Name`` or ``Year``, or the text is
    not valid CSV.
    """
    reader = csv.reader(io.StringIO(text.removeprefix(_BOM), newline=""), strict=True)
    try:
        header = next(reader, [])
        if "Name" not in header or "Year" not in header:
            raise AccountError("header lacks a Name or Year column")
        return frozenset(_keys(reader, header.index("Name"), header.index("Year")))
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


def _parsed(name: str, data: bytes) -> frozenset[FilmKey]:
    """One file's films; an AccountError from parsing is given the file's name."""
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


def _check_end_record(handle: BinaryIO) -> None:
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
            "--account: a zip64 archive, which is not read; unpack it and pass the "
            "folder instead"
        )
    _, _, _, here, total, directory, _, _ = _END_RECORD.unpack_from(tail, at)
    if max(here, total) > MAX_MEMBERS:
        raise AccountError(f"--account: holds more than {MAX_MEMBERS} files")
    if directory > DIRECTORY_LIMIT:
        raise AccountError(
            f"--account: its zip directory is larger than {DIRECTORY_LIMIT} bytes"
        )


def _check_archive(handle: BinaryIO) -> None:
    """Refuse a zip over ARCHIVE_LIMIT bytes, then one its end record says is too big.

    The size is the open file's own, so the file checked is the file opened.
    """
    if os.fstat(handle.fileno()).st_size > ARCHIVE_LIMIT:
        raise AccountError(f"--account: larger than {ARCHIVE_LIMIT} bytes")
    _check_end_record(handle)


def _zip_texts(path: Path) -> dict[str, bytes]:
    """Each top-level ACCOUNT_FILES member's bytes, its declared size checked first.

    The archive's own bounds are checked before zipfile opens it, and its member count
    again after, since an end record can understate it. The read is capped at
    MEMBER_LIMIT + 1 bytes as well, because a member's header can lie about its size;
    ``_decoded`` refuses the one byte over.
    """
    with path.open("rb") as handle:
        _check_archive(handle)
        handle.seek(0)
        with zipfile.ZipFile(handle) as archive:
            return _member_texts(archive)


def _member_texts(archive: zipfile.ZipFile) -> dict[str, bytes]:
    """``_zip_texts``' reading of an opened archive, its member count checked first."""
    found: dict[str, bytes] = {}
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise AccountError(f"--account: holds more than {MAX_MEMBERS} files")
    for info in infos:
        if info.filename not in ACCOUNT_FILES:
            continue  # a subfolder's member, a directory entry or another file
        if info.file_size > MEMBER_LIMIT:
            raise AccountError(f"{info.filename}: larger than {MEMBER_LIMIT} bytes")
        if info.compress_type not in _READ_METHODS:
            raise AccountError(
                f"{info.filename}: compressed with an unsupported method"
            )
        with archive.open(info) as member:
            found[info.filename] = member.read(MEMBER_LIMIT + 1)
    return found


def _folder_texts(folder: Path) -> dict[str, bytes]:
    """Each ACCOUNT_FILES file directly in ``folder``, read up to MEMBER_LIMIT + 1."""
    found: dict[str, bytes] = {}
    for name in ACCOUNT_FILES:
        try:
            if not (folder / name).is_file():
                continue
        except OSError:
            raise AccountError("--account: cannot be read") from None
        try:
            with (folder / name).open("rb") as handle:
                found[name] = handle.read(MEMBER_LIMIT + 1)
        except OSError:
            raise AccountError(f"{name}: cannot be read") from None
    return found


def _account_files(path: Path) -> dict[str, bytes]:
    """The account files ``path`` holds, as a folder or else as a zip."""
    if path.is_dir():
        return _folder_texts(path)
    try:
        return _zip_texts(path)
    except _ZIP_ERRORS:
        raise AccountError("--account: not a readable folder or zip") from None


# fuzz-target
def read_account(path: Path) -> frozenset[FilmKey]:
    """Every film the account export at ``path`` lists, the union of its files.

    Raises ``AccountError`` when ``path`` is neither a folder nor a readable zip, is a
    zip past ARCHIVE_LIMIT, MAX_MEMBERS or DIRECTORY_LIMIT or a zip64 one, holds none
    of ACCOUNT_FILES at its top level, or one of them is over MEMBER_LIMIT bytes, not
    UTF-8 (a byte order mark is accepted) or not an account CSV.
    """
    files = _account_files(path)
    if not files:
        raise AccountError(f"--account: holds none of {', '.join(ACCOUNT_FILES)}")
    return frozenset().union(*(_parsed(name, data) for name, data in files.items()))


def on_service(key: FilmKey, account: frozenset[FilmKey]) -> bool:
    """Whether the film ``key`` names is already on the account.

    An exact ``(title_key, year)`` match counts, and so does an entry of that title
    with no year, which matches any year of its title. A ``key`` with no year matches
    any entry of its title.
    """
    if key in account or FilmKey(key.title_key, None) in account:
        return True
    return key.year is None and any(
        entry.title_key == key.title_key for entry in account
    )
