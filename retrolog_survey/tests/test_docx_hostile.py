"""Tests for survey_docx on hostile packages: too many members, and entity tricks.

Every package here is generated at test time from the invented fixture document, so
nothing hostile is committed. A hostile package must be refused as BadDocx, quickly,
and must never make the reader expand an entity or read a file the package names.
"""

import struct
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Callable
from unittest import mock

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import build_fixtures  # noqa: E402
import survey_docx  # noqa: E402
import survey_unpack  # noqa: E402

_LIMIT = 10_000_000
_MAIN_PART = "word/document.xml"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# The end-of-central-directory record (APPNOTE section 4.3.16): its signature, and
# the offsets of its entries-on-this-disk, entries-in-total and directory-size fields.
_END_SIGNATURE = b"PK\x05\x06"
_END_DISK_ENTRIES = 8
_END_TOTAL_ENTRIES = 10
_END_DIRECTORY_SIZE = 12
# Seconds a refusal may take: a billion-laughs expansion would take far longer.
_QUICK = 5.0
# What the external-entity target holds, so its absence from every run is checkable.
_SECRET = "invented-secret-marker"
_CONTENT_TYPES = "[Content_Types].xml"
_SVG_NAME = "word/media/image9.svg"
_SVG_DEFAULT = b'<Default Extension="svg" ContentType="image/svg+xml"/>'
# An SVG as Word stores an inserted one: XML whose doctype names the SVG 1.1 DTD.
_SVG = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
    b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
)
_EMPTY_DOCTYPE = b'<?xml version="1.0"?>\n<!DOCTYPE x []>\n'


def _many_members(source: Path, target: Path, extra: int) -> None:
    """Copy the fixture to `target` with `extra` tiny members added after its own."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for item in old.infolist():
            new.writestr(item, old.read(item.filename))
        for number in range(extra):
            new.writestr(f"x/{number}", b"")


def _patch_end_record(path: Path, offset: int, fmt: str, value: int) -> None:
    """Overwrite one field of the archive's end-of-central-directory record."""
    data = bytearray(path.read_bytes())
    struct.pack_into(fmt, data, data.rindex(_END_SIGNATURE) + offset, value)
    path.write_bytes(bytes(data))


def _with_main_part(source: Path, target: Path, payload: bytes) -> None:
    """Copy the fixture to `target` with its main document part replaced."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for item in old.infolist():
            data = payload if item.filename == _MAIN_PART else old.read(item.filename)
            new.writestr(item, data)


def _rewrite(
    source: Path, target: Path, edit: Callable[[str, bytes], tuple[str, bytes]]
) -> None:
    """Copy the fixture to `target`, passing each member's name and bytes through `edit`."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for item in old.infolist():
            new.writestr(*edit(item.filename, old.read(item.filename)))


def _add_member(source: Path, target: Path, name: str, data: bytes) -> None:
    """Copy the fixture to `target` with one member added after its own."""
    _rewrite(source, target, lambda old_name, old_data: (old_name, old_data))
    with zipfile.ZipFile(target, "a") as archive:
        archive.writestr(name, data)


def _declare_svg(name: str, data: bytes) -> tuple[str, bytes]:
    """Declare the svg extension an image type, as Word does when it stores one."""
    if name == _CONTENT_TYPES:
        data = data.replace(b"<Default ", _SVG_DEFAULT + b"<Default ", 1)
    return name, data


def _rename_main_part(name: str, data: bytes) -> tuple[str, bytes]:
    """Move the main part to a name ending .bin, every reference to it following."""
    renamed = {"word/document.xml": "word/document.bin"}
    renamed["word/_rels/document.xml.rels"] = "word/_rels/document.bin.rels"
    if name in ("_rels/.rels", _CONTENT_TYPES):
        data = data.replace(b"word/document.xml", b"word/document.bin")
    if name == "word/document.xml":
        data = _document(_billion_laughs(), "&lol9;")
    return renamed.get(name, name), data


def _document(doctype: str, text: str) -> bytes:
    """A main part declaring `doctype`, with one run holding `text`."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype}\n'
        f'<w:document xmlns:w="{_W_NS}"><w:body><w:p><w:r><w:t>{text}</w:t>'
        "</w:r></w:p></w:body></w:document>"
    ).encode()


def _billion_laughs() -> str:
    """A DTD whose entity ``lol9`` expands to a billion copies of ``lol``."""
    entities = ['<!ENTITY lol0 "lol">']
    for level in range(1, 10):
        refs = f"&lol{level - 1};" * 10
        entities.append(f'<!ENTITY lol{level} "{refs}">')
    return f"<!DOCTYPE w:document [{''.join(entities)}]>"


class _PackageCase(unittest.TestCase):
    """A temporary folder holding the invented fixture document."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.source = self.base / "source.docx"
        build_fixtures.make_rated_docx(self.source)
        self.path = self.base / "hostile.docx"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def refusal(self) -> str:
        """The BadDocx message reading self.path gives; fails if it reads."""
        with self.assertRaises(survey_docx.BadDocx) as caught:
            survey_docx.read_docx(self.path, max_uncompressed=_LIMIT)
        return str(caught.exception)


class MemberCountTest(_PackageCase):
    def test_package_at_the_cap_reads(self) -> None:
        with zipfile.ZipFile(self.source) as archive:
            own = len(archive.infolist())
        _many_members(self.source, self.path, survey_docx.MAX_MEMBERS - own)
        survey_docx.read_docx(self.path, max_uncompressed=_LIMIT)

    def test_package_past_the_cap_is_refused_before_it_is_opened(self) -> None:
        _many_members(self.source, self.path, survey_docx.MAX_MEMBERS)
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError):
            self.assertEqual(self.refusal(), "too many members")

    def test_understated_member_count_is_still_refused(self) -> None:
        # zipfile reads the directory by its byte size, not its declared count, so a
        # count lowered to 1 must not let every entry through.
        _many_members(self.source, self.path, survey_docx.MAX_MEMBERS)
        _patch_end_record(self.path, _END_DISK_ENTRIES, "<H", 1)
        _patch_end_record(self.path, _END_TOTAL_ENTRIES, "<H", 1)
        self.assertEqual(self.refusal(), "too many members")

    def test_oversized_directory_is_refused_before_it_is_opened(self) -> None:
        _many_members(self.source, self.path, 0)
        _patch_end_record(self.path, _END_DIRECTORY_SIZE, "<I", 0xFFFF_FFF0)
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError):
            self.assertEqual(self.refusal(), "too many members")

    def test_file_without_an_end_record_is_bad(self) -> None:
        self.path.write_bytes(b"not a zip archive at all")
        self.assertEqual(self.refusal(), "BadZipFile")


class EntityTest(_PackageCase):
    def test_billion_laughs_is_refused_quickly(self) -> None:
        _with_main_part(self.source, self.path, _document(_billion_laughs(), "&lol9;"))
        started = time.monotonic()
        self.assertEqual(self.refusal(), "document type declaration")
        self.assertLess(time.monotonic() - started, _QUICK)

    def test_external_entity_reads_nothing_from_its_file(self) -> None:
        secret = self.base / "secret.txt"
        secret.write_bytes(_SECRET.encode("utf-8"))
        doctype = f'<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]>'
        _with_main_part(self.source, self.path, _document(doctype, "&xxe;"))
        self.assertEqual(self.refusal(), "document type declaration")
        # Belt and braces: even with the refusal removed, nothing is read from it.
        with mock.patch.object(survey_unpack, "_declares_doctype", return_value=False):
            info = survey_docx.read_docx(self.path, max_uncompressed=_LIMIT)
        texts = [run.text for p in info.paragraphs for run in p.runs]
        self.assertNotIn(_SECRET, "".join(texts))


class PartKindTest(_PackageCase):
    """Only a member declared an image may carry a doctype; every other is refused."""

    def test_svg_image_with_a_doctype_reads(self) -> None:
        _rewrite(self.source, self.base / "declared.docx", _declare_svg)
        _add_member(self.base / "declared.docx", self.path, _SVG_NAME, _SVG)
        survey_docx.read_docx(self.path, max_uncompressed=_LIMIT)

    def test_undeclared_member_with_a_doctype_is_refused(self) -> None:
        _add_member(self.source, self.path, _SVG_NAME, _SVG)
        self.assertEqual(self.refusal(), "document type declaration")

    def test_main_part_renamed_off_xml_is_still_checked(self) -> None:
        # python-docx finds the main part through the relationships and parses it as
        # XML whatever its name ends with, so a name rule would let this one through.
        _rewrite(self.source, self.path, _rename_main_part)
        self.assertEqual(self.refusal(), "document type declaration")

    def test_doctype_in_the_content_types_part_is_refused(self) -> None:
        def edit(name: str, data: bytes) -> tuple[str, bytes]:
            if name == _CONTENT_TYPES:
                data = data.replace(b"?>", b"?><!DOCTYPE Types []>", 1)
            return name, data

        _rewrite(self.source, self.path, edit)
        self.assertEqual(self.refusal(), "document type declaration")

    def test_doctype_in_a_relationships_part_is_refused(self) -> None:
        def edit(name: str, data: bytes) -> tuple[str, bytes]:
            if name == "_rels/.rels":
                data = data.replace(b"?>", b"?><!DOCTYPE Relationships []>", 1)
            return name, data

        _rewrite(self.source, self.path, edit)
        self.assertEqual(self.refusal(), "document type declaration")


class ImageMembersTest(unittest.TestCase):
    """The content-type reading behind the image exemption, at its edges."""

    def test_no_content_types_part_exempts_nothing(self) -> None:
        self.assertEqual(survey_unpack._image_members({_SVG_NAME: _SVG}), frozenset())

    def test_unparseable_content_types_part_exempts_nothing(self) -> None:
        members = {_CONTENT_TYPES: b"<Types", _SVG_NAME: _SVG}
        self.assertEqual(survey_unpack._image_members(members), frozenset())

    def test_content_types_part_with_a_doctype_exempts_nothing(self) -> None:
        members = {_CONTENT_TYPES: _EMPTY_DOCTYPE + b"<Types/>", _SVG_NAME: _SVG}
        self.assertEqual(survey_unpack._image_members(members), frozenset())

    def test_override_and_default_are_read_without_regard_to_case(self) -> None:
        listing = (
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            b'content-types"><Default Extension="SVG" ContentType="image/svg+xml"/>'
            b'<Override PartName="/WORD/media/Pic.XML" ContentType="image/png"/>'
            b'<Override PartName="/word/media/b.svg" ContentType="text/xml"/></Types>'
        )
        members = {_CONTENT_TYPES: listing, "word/media/a.svg": b"", "README": b""}
        members.update({"word/media/pic.xml": b"", "word/media/b.svg": b""})
        found = survey_unpack._image_members(members)
        self.assertEqual(found, frozenset({"word/media/a.svg", "word/media/pic.xml"}))


if __name__ == "__main__":  # pragma: no cover - entry point
    unittest.main()
