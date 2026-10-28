"""Read an old plain-text list: guess its encoding, count its line endings, split it.

Text files written over many years by different machines carry no record of their
encoding, so `detect_encoding` guesses one from the bytes alone. The rule, in order:

1. A byte-order mark decides: the UTF-8 BOM gives ``utf-8-sig`` and either UTF-16 BOM
   gives ``utf-16`` -- provided the whole file decodes that way. Bytes that merely start
   like a BOM (a truncated or damaged file) fall through to the steps below.
2. If every byte is below 0x80, the file is ``ascii``.
3. If the bytes decode as strict UTF-8, the file is ``utf-8``. Random 8-bit text almost
   never forms valid multi-byte UTF-8 sequences, so a clean decode is strong evidence.
4. Otherwise it is one of the two common 8-bit encodings of older desktop text:
   ``cp1252`` (Windows) or ``mac_roman`` (classic Mac OS).

   - cp1252 leaves five bytes undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D). Any of them
     means the file cannot be cp1252, so it is ``mac_roman``.
   - Otherwise both decodings succeed, and each is scored by how many of its characters
     fall in the typographic set of curly quotes, en and em dashes, the ellipsis and the
     bullet. Those are the commonest non-ASCII characters in ordinary prose, and they sit
     at different byte values in the two encodings, so the right decoding produces many
     of them and the wrong one produces accented letters instead. The higher score wins.
   - On a tie, line endings that are carriage returns only (no line feed anywhere) were
     the classic Mac OS convention, so they favor ``mac_roman``. With no such hint the
     guess is ``cp1252``, marked ambiguous for a person to check.

Whatever the guess, ``data.decode(guess.encoding)`` succeeds: every branch returns an
encoding that the bytes have already been shown to decode under, or one (mac_roman)
that defines all 256 byte values. The guess's ``evidence`` explains the choice without
quoting any of the file's text, so it is safe to print in a report.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from pathlib import Path

# The bytes cp1252 leaves undefined: Python's cp1252 codec refuses each of them.
_CP1252_UNDEFINED = frozenset(b"\x81\x8d\x8f\x90\x9d")
# Curly quotes, en/em dash, ellipsis, bullet: the scoring set for the 8-bit tie-break.
_TYPOGRAPHIC = frozenset("’‘“”–—…•")
_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_ASCII_LIMIT = 0x80
# Any one of the three line endings; CRLF first so it is never read as CR then LF.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class EncodingGuess:
    """An encoding name Python's codecs accept, why it was chosen, and how sure."""

    encoding: str
    evidence: str
    ambiguous: bool


@dataclass(frozen=True)
class TextInfo:
    """A decoded text file: its encoding guess, line-ending counts, and its lines."""

    guess: EncodingGuess
    line_endings: dict[str, int]
    lines: tuple[str, ...]


class FileTooLarge(Exception):
    """Raised by `read_text` when a file is larger than the caller's byte limit."""


# fuzz-target
def detect_encoding(data: bytes) -> EncodingGuess:
    """Guess the encoding of `data` by the rule in the module docstring."""
    if data.startswith(codecs.BOM_UTF8) and _decodes(data, "utf-8-sig"):
        return EncodingGuess("utf-8-sig", "UTF-8 byte-order mark", ambiguous=False)
    if data.startswith(_UTF16_BOMS) and _decodes(data, "utf-16"):
        return EncodingGuess("utf-16", "UTF-16 byte-order mark", ambiguous=False)
    if all(byte < _ASCII_LIMIT for byte in data):
        return EncodingGuess("ascii", "every byte is 7-bit", ambiguous=False)
    if _decodes(data, "utf-8"):
        return EncodingGuess("utf-8", "decodes as strict UTF-8", ambiguous=False)
    return _guess_eight_bit(data)


def _decodes(data: bytes, encoding: str) -> bool:
    """Whether `data` decodes under `encoding` without error."""
    try:
        data.decode(encoding)
    except UnicodeDecodeError:
        return False
    return True


def _guess_eight_bit(data: bytes) -> EncodingGuess:
    """Choose cp1252 or mac_roman for bytes that are neither ASCII nor UTF-8."""
    if any(byte in _CP1252_UNDEFINED for byte in data):
        return EncodingGuess(
            "mac_roman", "contains a byte cp1252 leaves undefined", ambiguous=False
        )
    cp_score = _typographic_score(data.decode("cp1252"))
    mac_score = _typographic_score(data.decode("mac_roman"))
    scores = f"typographic characters: cp1252 {cp_score}, mac_roman {mac_score}"
    if cp_score != mac_score:
        winner = "cp1252" if cp_score > mac_score else "mac_roman"
        return EncodingGuess(winner, scores, ambiguous=False)
    endings = count_line_endings(data)
    if endings["cr"] > 0 and endings["lf"] == 0:
        return EncodingGuess(
            "mac_roman",
            f"{scores}; tie broken by CR-only line endings",
            ambiguous=False,
        )
    return EncodingGuess(
        "cp1252", f"{scores}; tie, no line-ending hint", ambiguous=True
    )


def _typographic_score(text: str) -> int:
    """How many characters of `text` are in the typographic scoring set."""
    return sum(1 for ch in text if ch in _TYPOGRAPHIC)


def count_line_endings(data: bytes) -> dict[str, int]:
    """Count CRLF, lone CR and lone LF line endings in raw bytes.

    Counted on the bytes rather than decoded text so the count is available before an
    encoding is chosen (the 8-bit tie-break uses it). Exact only for single-byte and
    UTF-8 data; `read_text` therefore counts on the decoded text instead.
    """
    crlf = data.count(b"\r\n")
    return {
        "crlf": crlf,
        "cr": data.count(b"\r") - crlf,
        "lf": data.count(b"\n") - crlf,
    }


# fuzz-target
def split_lines(text: str) -> list[str]:
    """Split `text` on CRLF, CR or LF, dropping the empty piece after a final ending.

    Only the three ASCII line endings count: `str.splitlines` would also break on form
    feeds and Unicode separators, which an old list may contain inside a line.
    """
    lines = _LINE_BREAK.split(text)
    if lines[-1] == "":
        lines.pop()
    return lines


def read_text(path: Path, *, max_bytes: int) -> TextInfo:
    """Read `path`, refusing it with `FileTooLarge` if it exceeds `max_bytes`.

    The size is checked before anything is read, so an oversized file costs nothing.
    """
    size = path.stat().st_size
    if size > max_bytes:
        raise FileTooLarge(f"{size} bytes exceeds the {max_bytes}-byte limit")
    data = path.read_bytes()
    guess = detect_encoding(data)
    text = data.decode(guess.encoding)
    # Counted on the decoded text re-encoded as UTF-8, not on the raw bytes: in UTF-16 a
    # CRLF is four bytes with zeros between, and other characters contain 0x0A or 0x0D
    # bytes, so a raw count would be wrong. In UTF-8 those bytes only ever mean CR or LF.
    endings = count_line_endings(text.encode("utf-8"))
    return TextInfo(guess, endings, tuple(split_lines(text)))
