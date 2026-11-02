"""Read the TMDb token file: one ``RETROLOG_TMDB_TOKEN=value`` line, parsed, never run.

The operator may keep the TMDb token in a file under their home folder instead of the
environment. The file is parsed rather than ``source``d, so nothing in it runs: no
command substitution, no variable expansion, no escape processing. A line is one of:

* blank, or a comment starting with ``#`` (after leading whitespace): skipped;
* ``RETROLOG_TMDB_TOKEN=value``, optionally prefixed ``export ``: ``value`` is everything
  after the first ``=``, with one pair of matching surrounding quotes (``"`` or ``'``)
  stripped. A ``#`` later in the line is part of the value, not a comment.

Anything else is refused: a malformed line, any other variable, the variable twice, a
NUL anywhere, a file over ``MAX_TOKEN_FILE_BYTES`` or one that is not UTF-8. A refusal
names a line number at most, never the path, the value or the name found on the line,
because a mistyped line may hold the token in any position. An empty value is no token.

On POSIX the open file must be a regular file owned by the current user with no group
or other permission bits (mode 600 or stricter). Windows has no such bits, so the check
is skipped there; the file's protection is then its folder's access control list.

Entry point assumptions: imported by retrolog_resolve, with ``src/`` on ``sys.path``.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Final

#: The one variable the file may assign; the same name the CLI reads from the
#: environment.
TOKEN_VARIABLE: Final = "RETROLOG_TMDB_TOKEN"
#: The largest file read. A token is under a kilobyte; a bigger file is not one.
MAX_TOKEN_FILE_BYTES: Final = 64 * 1024
#: The optional shell prefix, dropped before the name.
_EXPORT_PREFIX: Final = "export "
#: Starts a comment line.
_COMMENT: Final = "#"
#: The quote characters one surrounding pair of which is stripped.
_QUOTES: Final = ("'", '"')
#: No environment value or header can hold one.
_NUL: Final = "\x00"
#: The permission bits a group or other user would read or write the file through.
_GROUP_OR_OTHER: Final = 0o077
#: ``os.open`` flags: read only, and never block on a FIFO planted at the path (the
#: regular-file check comes after the open). O_NONBLOCK does not exist on Windows.
_OPEN_FLAGS: Final = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)


class TokenFileError(ValueError):
    """The token file is unreadable, too open, or holds a malformed line."""


# fuzz-target
def parse_token_file(text: str) -> str | None:
    """The token ``text`` assigns, or None when it assigns none or an empty one.

    Lines split on every line boundary ``str.splitlines`` knows, so no value returned
    can hold a line break. Raises TokenFileError naming the first bad line's number.
    """
    token: str | None = None
    seen = False
    for number, raw in enumerate(text.splitlines(), start=1):
        if _NUL in raw:
            raise TokenFileError(f"the token file: line {number}: a NUL character")
        line = raw.strip()
        if not line or line.startswith(_COMMENT):
            continue
        if seen:
            raise TokenFileError(
                f"the token file: line {number}: {TOKEN_VARIABLE} is set twice"
            )
        token, seen = _parse_line(line, number) or None, True
    return token


def _parse_line(line: str, number: int) -> str:
    """One assignment line's unquoted value; TokenFileError when malformed."""
    if line.startswith(_EXPORT_PREFIX):
        line = line[len(_EXPORT_PREFIX) :].lstrip()
    name, equals, value = line.partition("=")
    if not equals:
        raise TokenFileError(f"the token file: line {number}: no '=' in the line")
    if name != TOKEN_VARIABLE:
        # The name found is not repeated: a mistyped line may hold the token there.
        raise TokenFileError(
            f"the token file: line {number}: not an assignment to {TOKEN_VARIABLE}"
        )
    return _unquoted(value, number)


def _unquoted(value: str, number: int) -> str:
    """``value`` without one matching pair of surrounding quotes; a lone one refused."""
    if not value.startswith(_QUOTES) and not value.endswith(_QUOTES):
        return value
    if len(value) >= 2 and value[0] == value[-1]:
        return value[1:-1]
    raise TokenFileError(f"the token file: line {number}: an unterminated quote")


def mode_refusal(st_mode: int, st_uid: int, uid: int, posix: bool) -> str | None:
    """Why an open file with this mode and owner may not hold the token, else None.

    Pure, so both sides of the POSIX split are tested on any machine. Off POSIX the
    check is skipped: Windows reports no owner and no group or other bits.
    """
    if not posix:
        return None
    if not stat.S_ISREG(st_mode):
        return "the token file must be a regular file"
    if st_uid != uid:
        return "the token file must be owned by the current user"
    if st_mode & _GROUP_OR_OTHER:
        # The mode is shown, never the path: it says what to fix without saying where.
        return (
            "the token file must be readable by its owner only (mode 600); "
            f"its mode is {stat.S_IMODE(st_mode):03o}"
        )
    return None


def read_token_file(path: Path, *, required: bool) -> str | None:
    """The token the file at ``path`` assigns, or None.

    A missing file is no token unless ``required`` (the operator named the file), when
    it is refused as unreadable like any other open failure. The descriptor itself is
    checked with ``fstat``, so the file checked is the file read.
    """
    try:
        descriptor = os.open(path, _OPEN_FLAGS)
    except FileNotFoundError as error:
        if not required:
            return None
        raise _unreadable(error) from None
    except OSError as error:
        raise _unreadable(error) from None
    return parse_token_file(_decoded(_read_descriptor(descriptor)))


def _read_descriptor(descriptor: int) -> bytes:
    """The open file's bytes once its mode passes; the descriptor is always closed.

    Until fdopen succeeds this function owns the descriptor, so every failure before
    then closes it here; once fdopen returns, its ``with`` block closes it.
    """
    try:
        # Checked before fdopen, which refuses a folder with its own error.
        _check_mode(os.fstat(descriptor))
        handle = os.fdopen(descriptor, "rb")
    except OSError as error:
        os.close(descriptor)
        raise _unreadable(error) from None
    except TokenFileError:
        os.close(descriptor)
        raise
    try:
        with handle:
            return handle.read(MAX_TOKEN_FILE_BYTES + 1)
    except OSError as error:
        raise _unreadable(error) from None


def _unreadable(error: OSError) -> TokenFileError:
    """The refusal for a file that could not be opened or read: its class only.

    The OSError's own message is dropped because it carries the path.
    """
    return TokenFileError(f"could not read the token file: {type(error).__name__}")


def _check_mode(status: os.stat_result) -> None:
    """TokenFileError when ``mode_refusal`` refuses the open file's status."""
    posix = os.name == "posix"
    # os.getuid exists on POSIX only; looked up rather than called by name so the
    # type checker, run for Windows too, accepts it. Off POSIX no uid is compared.
    getuid: Callable[[], int] | None = getattr(os, "getuid", None)
    uid = getuid() if getuid is not None else -1
    refusal = mode_refusal(status.st_mode, status.st_uid, uid, posix)
    if refusal is not None:
        raise TokenFileError(refusal)


def _decoded(data: bytes) -> str:
    """The file's bytes as text; TokenFileError when oversize or not UTF-8."""
    if len(data) > MAX_TOKEN_FILE_BYTES:
        raise TokenFileError(
            f"the token file is larger than {MAX_TOKEN_FILE_BYTES // 1024} KiB"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Not chained: the decode error holds the bytes it failed on.
        raise TokenFileError("the token file is not UTF-8 text") from None
