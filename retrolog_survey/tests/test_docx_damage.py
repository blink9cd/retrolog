"""Tests for survey_docx on damaged, hostile and arbitrary input.

Every case asserts the same contract: a damaged file is reported as BadDocx (or read,
when the damage is harmless), never as any other exception, and never by decompressing
more than the size limit allows.
"""

import struct
import sys
import tempfile
import tracemalloc
import unittest
import zipfile
from unittest import mock
import zlib
from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from lxml import etree

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import build_fixtures  # noqa: E402
import survey_docx  # noqa: E402

# Byte offsets inside a zip central-directory entry (APPNOTE section 4.3.12).
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_CENTRAL_FLAGS = 8
_CENTRAL_METHOD = 10
_CENTRAL_CRC = 16
_CENTRAL_UNCOMPRESSED = 24
_CENTRAL_NAME_LENGTH = 28
_CENTRAL_NAME = 46
# Local file header: fixed part, then name and extra field, then the member's data.
_LOCAL_FIXED = 30
_LOCAL_NAME_LENGTH = 26
_ENCRYPTED_FLAG = 0x1
_UNKNOWN_METHOD = 99
_MAIN_PART = "word/document.xml"
_IMAGE_PART = "word/media/image1.png"
_STYLES_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
)
# A member far larger than the guard's limit in the bounded-decompression test.
_HUGE_MEMBER = 20_000_000
_PEAK_LIMIT = 5_000_000
# The compression methods a package may use, so a mutation can damage each one's
# decoder; bzip2 and LZMA archives are refused outright (see the tests for them).
_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)


def _read_or_bad(path: Path) -> survey_docx.DocxInfo | None:
    """Read `path`, returning None for BadDocx: any other exception fails the test."""
    try:
        return survey_docx.read_docx(path, max_uncompressed=10_000_000)
    except survey_docx.BadDocx:
        return None


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# Element and attribute names the reader looks at, for building structured fuzz bodies.
_FUZZ_TAGS = (
    "w:p", "w:r", "w:t", "w:tab", "w:tbl", "w:tr", "w:tc", "w:pPr", "w:pStyle",
    "w:rPr", "w:rFonts", "w:rStyle", "w:color", "w:highlight", "w:shd", "w:sym",
    "a:blip",
)  # fmt: skip
_FUZZ_ATTRIBUTES = (
    "w:val", "w:fill", "w:font", "w:char", "w:ascii", "w:themeColor", "r:embed",
)  # fmt: skip


def _fuzz_element(tag: str, attributes: dict[str, str], children: list[str]) -> str:
    """Serialize one generated element, so nesting is decided by the strategy."""
    element = OxmlElement(tag)
    for name, value in attributes.items():
        element.set(qn(name), value)
    for child in children:
        element.append(etree.fromstring(child))
    return etree.tostring(element).decode()


_FUZZ_BODY = st.recursive(
    st.builds(
        _fuzz_element,
        st.sampled_from(_FUZZ_TAGS),
        st.dictionaries(
            st.sampled_from(_FUZZ_ATTRIBUTES),
            st.sampled_from(["", "auto", "F043", "rId1", "Wingdings", "Title", "zz"]),
        ),
        st.just([]),
    ),
    lambda inner: st.builds(
        _fuzz_element,
        st.sampled_from(_FUZZ_TAGS),
        st.just({}),
        st.lists(inner, max_size=3),
    ),
    max_leaves=12,
)


def _with_document_xml(source: Path, target: Path, payload: bytes) -> None:
    """Copy the .docx at `source` to `target` with its main part replaced."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for item in old.infolist():
            data = old.read(item.filename)
            new.writestr(
                item, payload if item.filename == "word/document.xml" else data
            )


def _rezip(source: Path, target: Path, method: int = zipfile.ZIP_DEFLATED) -> None:
    """Copy the archive at `source` to `target`, compressing every member with `method`."""
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for name in old.namelist():
            new.writestr(name, old.read(name), compress_type=method)


def _central_entry(data: bytearray, name: str) -> int:
    """Offset of the central-directory entry for member `name`."""
    start = data.index(_CENTRAL_SIGNATURE)
    while True:
        (length,) = struct.unpack_from("<H", data, start + _CENTRAL_NAME_LENGTH)
        found = data[start + _CENTRAL_NAME : start + _CENTRAL_NAME + length]
        if found == name.encode():
            return start
        start = data.index(_CENTRAL_SIGNATURE, start + 1)


def _patch(path: Path, name: str, field: int, fmt: str, value: int) -> None:
    """Overwrite one field of `name`'s central-directory entry in place."""
    data = bytearray(path.read_bytes())
    struct.pack_into(fmt, data, _central_entry(data, name) + field, value)
    path.write_bytes(bytes(data))


def _corrupt_first_data_byte(path: Path, name: str) -> None:
    """Make member `name`'s compressed stream start with a reserved deflate block type."""
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as archive:
        local = archive.getinfo(name).header_offset
    name_length, extra_length = struct.unpack_from(
        "<HH", data, local + _LOCAL_NAME_LENGTH
    )
    # 0xFF sets BFINAL and the reserved block type 11, which zlib refuses.
    data[local + _LOCAL_FIXED + name_length + extra_length] = 0xFF
    path.write_bytes(bytes(data))


class DamagedPartTest(unittest.TestCase):
    """A real package whose main document part is damaged or unusual."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.source = Path(self._tmp.name) / "source.docx"
        build_fixtures.make_rated_docx(self.source)
        self.path = Path(self._tmp.name) / "damaged.docx"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read_with(self, payload: bytes) -> survey_docx.DocxInfo | None:
        _with_document_xml(self.source, self.path, payload)
        return _read_or_bad(self.path)

    def test_malformed_xml_is_bad(self) -> None:
        self.assertIsNone(self._read_with(b"<w:document"))

    def test_wrong_root_element_is_bad(self) -> None:
        # Regression: a well-formed part whose root is not w:document raised
        # AttributeError instead of BadDocx.
        self.assertIsNone(self._read_with(b"<x/>"))

    def test_document_without_a_body_has_no_paragraphs(self) -> None:
        # Regression: a w:document with no w:body raised AttributeError.
        info = self._read_with(f'<w:document xmlns:w="{_W_NS}"/>'.encode())
        assert info is not None
        self.assertEqual(info.paragraphs, ())

    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(st.lists(_FUZZ_BODY, max_size=4))
    def test_any_body_reads_or_is_bad(self, blocks: list[str]) -> None:
        body = _fuzz_element("w:body", {}, blocks)
        document = _fuzz_element("w:document", {}, [body])
        self._read_with(document.encode())

    def test_corrupt_deflate_stream_is_bad(self) -> None:
        # Regression: a damaged deflate stream escaped as zlib.error.
        _rezip(self.source, self.path)
        _corrupt_first_data_byte(self.path, _MAIN_PART)
        self.assertIsNone(_read_or_bad(self.path))

    def _with_one_member_compressed(self, method: int) -> None:
        """Write the fixture to self.path with only its main part compressed by `method`."""
        with (
            zipfile.ZipFile(self.source) as old,
            zipfile.ZipFile(self.path, "w") as new,
        ):
            for name in old.namelist():
                chosen = method if name == _MAIN_PART else zipfile.ZIP_DEFLATED
                new.writestr(name, old.read(name), compress_type=chosen)

    def test_bzip2_member_is_bad(self) -> None:
        # Regression: a bzip2 member was decompressed, and older zipfile releases do
        # not bound a bzip2 read, so one chunk could expand without limit.
        self._with_one_member_compressed(zipfile.ZIP_BZIP2)
        self.assertIsNone(_read_or_bad(self.path))

    def test_lzma_member_is_bad(self) -> None:
        # Regression: as for bzip2, an LZMA member is outside the package format.
        self._with_one_member_compressed(zipfile.ZIP_LZMA)
        self.assertIsNone(_read_or_bad(self.path))

    def test_unknown_compression_method_is_bad(self) -> None:
        # Regression: an unsupported method escaped as NotImplementedError; it is now
        # refused before zipfile is asked to open the member.
        _rezip(self.source, self.path)
        _patch(self.path, _MAIN_PART, _CENTRAL_METHOD, "<H", _UNKNOWN_METHOD)
        self.assertIsNone(_read_or_bad(self.path))

    def test_encrypted_member_is_bad(self) -> None:
        # Regression: an encrypted member escaped as RuntimeError.
        _rezip(self.source, self.path)
        _patch(self.path, _MAIN_PART, _CENTRAL_FLAGS, "<H", _ENCRYPTED_FLAG)
        self.assertIsNone(_read_or_bad(self.path))

    def test_member_data_ending_early_is_bad(self) -> None:
        # zipfile raises EOFError when a member's data stops short, which happens when
        # the file is shortened while it is being read. No static file reaches that
        # path (zipfile's overlap check refuses a size that runs past the next entry
        # first), so the member stream is replaced with one that ends early.
        def short_read(*_args: object, **_kwargs: object) -> object:
            raise EOFError

        with mock.patch.object(zipfile.ZipExtFile, "read", short_read):
            self.assertIsNone(_read_or_bad(self.source))

    def test_members_beyond_the_limit_in_total_are_bad(self) -> None:
        # Every member of the fixture is under the limit on its own, but together they
        # pass it: the limit is on the running total, not on each member.
        with zipfile.ZipFile(self.source) as archive:
            sizes = [info.file_size for info in archive.infolist()]
        limit = max(sizes) + 1
        self.assertGreater(sum(sizes), limit)
        with self.assertRaises(survey_docx.BadDocx) as caught:
            survey_docx.read_docx(self.source, max_uncompressed=limit)
        self.assertEqual(str(caught.exception), "expands past limit")

    def test_unreadable_styles_part_gives_no_style(self) -> None:
        # Regression: a styles part of the wrong content type let the document open,
        # then the style lookup escaped as AttributeError.
        with (
            zipfile.ZipFile(self.source) as old,
            zipfile.ZipFile(self.path, "w") as new,
        ):
            for name in old.namelist():
                data = old.read(name)
                if name == "[Content_Types].xml":
                    data = data.replace(_STYLES_TYPE.encode(), b"application/xml")
                new.writestr(name, data)
        info = _read_or_bad(self.path)
        assert info is not None
        self.assertEqual({p.style for p in info.paragraphs}, {None})

    def test_understated_member_size_does_not_decompress_it_all(self) -> None:
        # Regression: a member declaring a few bytes, CRC forged to match that prefix,
        # passed the declared-size guard and was then decompressed in full.
        with (
            zipfile.ZipFile(self.source) as old,
            zipfile.ZipFile(self.path, "w") as new,
        ):
            for name in old.namelist():
                data = bytes(_HUGE_MEMBER) if name == _IMAGE_PART else old.read(name)
                new.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        _patch(self.path, _IMAGE_PART, _CENTRAL_UNCOMPRESSED, "<I", 16)
        _patch(self.path, _IMAGE_PART, _CENTRAL_CRC, "<I", zlib.crc32(bytes(16)))
        tracemalloc.start()
        try:
            _read_or_bad(self.path)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, _PEAK_LIMIT)

    def test_real_expansion_past_the_limit_is_bad(self) -> None:
        with self.assertRaises(survey_docx.BadDocx) as caught:
            survey_docx.read_docx(self.source, max_uncompressed=1_000)
        self.assertEqual(str(caught.exception), "expands past limit")

    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        st.sampled_from(_METHODS),
        st.lists(
            st.tuples(st.floats(0, 1, exclude_max=True), st.integers(0, 255)),
            min_size=1,
            max_size=8,
        ),
    )
    def test_mutated_bytes_read_or_are_bad(
        self, method: int, edits: list[tuple[float, int]]
    ) -> None:
        _rezip(self.source, self.path, method)
        data = bytearray(self.path.read_bytes())
        for where, value in edits:
            data[int(where * len(data))] = value
        self.path.write_bytes(bytes(data))
        _read_or_bad(self.path)


# One damaged attribute in one part, each of which once escaped python-docx as an
# exception other than BadDocx: (part, text replaced, replacement).
_STYLE_WITHOUT_VALUE = (
    _MAIN_PART,
    "<w:body><w:p>",
    "<w:body><w:p><w:pPr><w:pStyle/></w:pPr>",
)
_NAME_WITHOUT_VALUE = ("word/styles.xml", '<w:name w:val="Normal"/>', "<w:name/>")
_BOGUS_STYLE_TYPE = (
    "word/styles.xml",
    '<w:style w:type="paragraph" w:default="1"',
    '<w:style w:type="bogus" w:default="1"',
)
_DEFAULT_WITHOUT_EXTENSION = (
    "[Content_Types].xml",
    '<Default Extension="xml" ',
    "<Default ",
)
_RELATIONSHIP_WITHOUT_TARGET = (
    "word/_rels/document.xml.rels",
    ' Target="styles.xml"',
    "",
)
# The parts the structural fuzz test damages: every one python-docx parses on opening.
_FUZZ_PARTS = (
    _MAIN_PART,
    "word/styles.xml",
    "[Content_Types].xml",
    "word/_rels/document.xml.rels",
    "_rels/.rels",
)
_FUZZ_VALUES = ("", "bogus", "rId999", "auto", "0", "-1", "/x")
# The three ways _damage_element damages an element.
_DROP, _SET, _REMOVE = 0, 1, 2


def _with_member(source: Path, target: Path, name: str, old: str, new: str) -> None:
    """Copy the .docx at `source` to `target`, replacing `old` with `new` in `name`."""
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, "w") as out:
        for member in archive.namelist():
            data = archive.read(member)
            if member == name:
                text = data.decode("utf-8")
                assert old in text, (name, old)
                data = text.replace(old, new, 1).encode("utf-8")
            out.writestr(member, data)


def _damage_element(data: bytes, pick: int, action: int, value: str) -> bytes:
    """One element of the part `data`, chosen by `pick`, damaged as `action` says.

    The actions are: drop one of its attributes, set one to `value`, or remove the
    element. An element with no attributes, or the root, is left as it is.
    """
    root = etree.fromstring(data)
    elements = list(root.iter())
    element = elements[pick % len(elements)]
    names = list(element.attrib)
    if action == _DROP and names:
        del element.attrib[names[pick % len(names)]]
    elif action == _SET and names:
        element.set(names[pick % len(names)], value)
    elif action == _REMOVE and element is not root:
        element.getparent().remove(element)
    return etree.tostring(root)


class PartDamageTest(unittest.TestCase):
    """Damaged attributes in the parts python-docx parses: BadDocx, never an escape."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = Path(self._tmp.name) / "source.docx"
        build_fixtures.make_rated_docx(self.source)
        self.path = Path(self._tmp.name) / "damaged.docx"

    def _read(self, damage: tuple[str, str, str]) -> survey_docx.DocxInfo | None:
        _with_member(self.source, self.path, *damage)
        return _read_or_bad(self.path)

    def test_paragraph_style_without_value_gives_no_style(self) -> None:
        # Regression: escaped as InvalidXmlError from the paragraph style lookup.
        info = self._read(_STYLE_WITHOUT_VALUE)
        assert info is not None
        self.assertIsNone(info.paragraphs[0].style)
        self.assertEqual(info.paragraphs[1].style, "Normal")

    def test_style_name_without_value_reads_or_is_bad(self) -> None:
        # Regression: escaped as InvalidXmlError.
        self._read(_NAME_WITHOUT_VALUE)

    def test_bogus_style_type_reads_or_is_bad(self) -> None:
        # Regression: escaped as ValueError from style resolution.
        self._read(_BOGUS_STYLE_TYPE)

    def test_content_type_default_without_extension_is_bad(self) -> None:
        # Regression: escaped as AttributeError from python-docx's package loader.
        self.assertIsNone(self._read(_DEFAULT_WITHOUT_EXTENSION))

    def test_relationship_without_target_is_bad(self) -> None:
        # Regression: escaped as TypeError from python-docx's package loader.
        self.assertIsNone(self._read(_RELATIONSHIP_WITHOUT_TARGET))

    def test_unexpected_failure_is_bad_by_class_name(self) -> None:
        # Whatever python-docx raises on input nobody foresaw becomes BadDocx naming
        # only the exception's class, never its message (which may hold text).
        failure = LookupError("document text")
        with mock.patch.object(survey_docx, "_block_paragraphs", side_effect=failure):
            with self.assertRaises(survey_docx.BadDocx) as caught:
                survey_docx.read_docx(self.source, max_uncompressed=10_000_000)
        self.assertEqual(str(caught.exception), "LookupError")

    @settings(
        max_examples=150,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        st.sampled_from(_FUZZ_PARTS),
        st.lists(
            st.tuples(
                st.integers(0, 10_000),
                st.sampled_from((_DROP, _SET, _REMOVE)),
                st.sampled_from(_FUZZ_VALUES),
            ),
            min_size=1,
            max_size=4,
        ),
    )
    def test_damaged_package_parts_read_or_are_bad(
        self, name: str, edits: list[tuple[int, int, str]]
    ) -> None:
        with zipfile.ZipFile(self.source) as archive:
            data = archive.read(name)
        for pick, action, value in edits:
            data = _damage_element(data, pick, action, value)
        with (
            zipfile.ZipFile(self.source) as old,
            zipfile.ZipFile(self.path, "w") as new,
        ):
            for member in old.namelist():
                new.writestr(member, data if member == name else old.read(member))
        _read_or_bad(self.path)


class ArbitraryBytesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(st.binary(max_size=256))
    def test_fuzz_read_docx(self, data: bytes) -> None:
        path = Path(self._tmp.name) / "fuzz.docx"
        path.write_bytes(data)
        _read_or_bad(path)


if __name__ == "__main__":
    unittest.main()
