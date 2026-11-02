"""Unpack a Word .docx archive under a size limit, refusing hostile packages.

The file is a zip archive, and its member headers may lie about their sizes, so it is
never handed to python-docx as it is. Each member is streamed in bounded chunks against
a running count of the bytes actually produced, and the result is repacked
uncompressed in memory for python-docx to parse. A package with more members than
``MAX_MEMBERS`` is refused before zipfile lists them, and one with a part declaring a
document type (the vehicle for an entity-expansion bomb or an external-file read) is
refused before python-docx parses it. Only a part the package declares as an image is
exempt: Word stores an inserted SVG as one, often with a doctype, and python-docx
never parses an image as XML. Every refusal is ``BadDocx``, whose message is the
underlying exception's class name or a fixed reason: never a path, never document text.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
import zlib
from pathlib import Path
from typing import IO

# zipfile's own end-of-central-directory reader and the indices of its result. Private,
# and used on purpose: ZipFile's constructor calls the same function and then reads the
# whole central directory into memory, building every member's ZipInfo, before anything
# can be counted, and zipfile offers no lazy listing. Asking that same reader first
# checks exactly the count and directory size ZipFile is about to act on, a zip64
# record's values included, where a hand-written parser could disagree with it.
from zipfile import (  # type: ignore[attr-defined]  # private, absent from typeshed
    _ECD_ENTRIES_TOTAL,
    _ECD_SIZE,
    _EndRecData,
)

from lxml import etree

# Bytes decompressed per read while unpacking, so no single call can expand a member
# past the limit by more than this.
_CHUNK = 64 * 1024
# The only compression methods an Open Packaging Conventions package may use (ECMA-376
# Part 2). Anything else is refused before it is opened, and not merely as a format
# rule: older zipfile releases decompress a bzip2 or LZMA chunk with no output limit, so
# one bounded read of such a member could expand without bound before it is counted.
_PACKAGE_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
# The most members a package may hold. A Word document has tens (parts, relationships,
# media); ten thousand is far past any real one, and refusing past it keeps a crafted
# archive from making zipfile build millions of ZipInfo objects before the size limit,
# which counts only decompressed bytes, ever applies.
MAX_MEMBERS = 10_000
# Central-directory bytes allowed per member when sizing the directory before it is
# read. An entry is a 46-byte header plus its name, extra field and comment, about a
# hundred bytes in a Word package. zipfile walks the directory by its byte size and
# ignores the declared count, so this bound, not the count, is what caps the entries
# it would build; the count is checked again once they are listed.
_DIRECTORY_BYTES_PER_MEMBER = 512
_MAX_DIRECTORY_BYTES = MAX_MEMBERS * _DIRECTORY_BYTES_PER_MEMBER
_TOO_MANY = "too many members"
_DOCTYPE = "document type declaration"
# The part listing each member's content type (ECMA-376 Part 2, section 10.1.2), and
# the namespace of its Default and Override elements.
_CONTENT_TYPES = "[Content_Types].xml"
_TYPES_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_IMAGE_TYPE = "image/"
# What streaming a damaged archive's members can raise: a damaged structure or a CRC
# mismatch (BadZipFile); a corrupt deflate stream (zlib.error); an unreadable file
# (OSError); data ending early, as when the file is shortened while it is read
# (EOFError); an encrypted member (RuntimeError); and an undecodable member name
# (UnicodeDecodeError, a ValueError). No unsupported-method error can arise: the
# method is checked against _PACKAGE_METHODS first.
_ARCHIVE_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    OSError,
    EOFError,
    RuntimeError,
    ValueError,
)


class BadDocx(Exception):
    """The file is not a readable .docx, or would expand past the size limit."""


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """Decompress one member chunk by chunk, failing once it passes `budget` bytes."""
    chunks: list[bytes] = []
    used = 0
    with archive.open(info) as member:
        while chunk := member.read(_CHUNK):
            used += len(chunk)
            if used > budget:
                raise BadDocx("expands past limit")
            chunks.append(chunk)
    return b"".join(chunks)


def _check_directory(handle: IO[bytes]) -> None:
    """BadDocx when the end record declares too many members or too large a directory.

    A file with no end record passes: ZipFile refuses it next, as BadZipFile.
    """
    end = _EndRecData(handle)
    if not end:
        return
    if end[_ECD_ENTRIES_TOTAL] > MAX_MEMBERS or end[_ECD_SIZE] > _MAX_DIRECTORY_BYTES:
        raise BadDocx(_TOO_MANY)


def _feed_to_root(
    parser: etree.XMLPullParser, data: bytes, roots: list[etree._Element]
) -> None:
    """Feed `data` a chunk at a time until the root's start tag, adding it to `roots`.

    A short document may yield its root only once the input is closed.
    """
    for offset in range(0, len(data), _CHUNK):
        parser.feed(data[offset : offset + _CHUNK])
        roots.extend(root for _event, root in parser.read_events())
        if roots:
            return
    parser.close()
    roots.extend(root for _event, root in parser.read_events())


def _declares_doctype(data: bytes) -> bool:
    """Whether `data`, read as XML up to its root's start tag only, declares a doctype.

    No part python-docx parses as XML has one, and a crafted one carries the entities
    behind an expansion bomb or an external-file read. The parser here resolves no
    entity, loads no DTD, touches no network, and stops at the root, so the
    declarations are seen but never acted on. Bytes that are not XML (an image, a damaged part)
    declare nothing; python-docx reports any part it cannot parse itself.
    """
    parser = etree.XMLPullParser(
        events=("start",), resolve_entities=False, load_dtd=False, no_network=True
    )
    roots: list[etree._Element] = []
    try:
        _feed_to_root(parser, data, roots)
    except etree.XMLSyntaxError:
        # Not XML, or XML failing at or past its root, as an entity bomb does at its
        # first reference when one chunk holds both: the events parsed before the
        # error still show whether a doctype came first.
        roots.extend(root for _event, root in parser.read_events())
    return bool(roots) and bool(roots[0].getroottree().docinfo.doctype)


def _read_members(archive: zipfile.ZipFile, max_uncompressed: int) -> dict[str, bytes]:
    """Every member's bytes by name, under the limit; BadDocx for a refused member.

    A repeated member name keeps its last copy, which is the one zipfile itself returns.
    """
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        # The end record understated the count; the directory's size bound held.
        raise BadDocx(_TOO_MANY)
    members: dict[str, bytes] = {}
    used = 0
    for info in infos:
        if info.compress_type not in _PACKAGE_METHODS:
            raise BadDocx("unsupported compression method")
        data = _read_member(archive, info, max_uncompressed - used)
        used += len(data)
        members[info.filename] = data
    _refuse_doctypes(members)
    return members


def _content_type_of(
    name: str, overrides: dict[str, str], defaults: dict[str, str]
) -> str:
    """`name`'s declared content type, an Override before its extension's Default.

    Both are matched without regard to case, as part names are (ECMA-376 Part 2).
    """
    extension = posixpath.splitext(name)[1][1:].lower()
    return overrides.get("/" + name.lower(), defaults.get(extension, ""))


def _image_members(members: dict[str, bytes]) -> frozenset[str]:
    """The members ``[Content_Types].xml`` declares an image type; none if unreadable.

    The exemption is keyed on content type, not name, because python-docx chooses how
    to load a part by its declared type: it parses a main part named ``document.bin``
    as XML, and loads anything typed ``image/`` as bytes it never parses. A listing
    that is missing, malformed or declares a doctype itself exempts nothing, so every
    member is then checked.
    """
    listing = members.get(_CONTENT_TYPES)
    if listing is None or _declares_doctype(listing):
        return frozenset()
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        root = etree.fromstring(listing, parser=parser)
    except etree.XMLSyntaxError:
        return frozenset()
    defaults = {
        item.get("Extension", "").lower(): item.get("ContentType", "")
        for item in root.iter(_TYPES_NS + "Default")
    }
    overrides = {
        item.get("PartName", "").lower(): item.get("ContentType", "")
        for item in root.iter(_TYPES_NS + "Override")
    }
    return frozenset(
        name
        for name in members
        if _content_type_of(name, overrides, defaults).startswith(_IMAGE_TYPE)
    )


def _refuse_doctypes(members: dict[str, bytes]) -> None:
    """BadDocx when any member not declared an image declares a document type."""
    images = _image_members(members)
    for name, data in members.items():
        if name not in images and _declares_doctype(data):
            raise BadDocx(_DOCTYPE)


def unpack(path: Path, max_uncompressed: int) -> io.BytesIO:
    """Decompress every member under the limit and repack them uncompressed in memory.

    The limit counts bytes actually produced, not the sizes the headers declare. The
    member count is checked from the end record before zipfile lists the members, on
    the same open file, so the file checked is the file read.
    """
    try:
        with path.open("rb") as handle:
            _check_directory(handle)
            with zipfile.ZipFile(handle) as archive:
                members = _read_members(archive, max_uncompressed)
    except _ARCHIVE_ERRORS as error:
        raise BadDocx(type(error).__name__) from None
    repacked = io.BytesIO()
    with zipfile.ZipFile(repacked, "w", zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    repacked.seek(0)
    return repacked
