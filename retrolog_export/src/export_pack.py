"""Pack films' import rows into CSV files under a row cap and a byte cap.

Letterboxd's importer takes a file of limited size and row count, so the rows are split
across numbered files. A film's rows stay together and in order in one file, so
its logs import oldest first and its newest rating is the one kept. Every file carries
the header, which counts toward the byte cap; the row cap counts data rows only.

The rows are plain strings, so this module knows nothing of films beyond "a run of rows
kept whole", and the caps are the caller's (the CLI's ``--rows-per-file`` and
``--max-bytes``).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

# Unix line ends: the importer reads either, and "\n" keeps a byte count equal on every
# platform.
_LINE_END = "\n"


class PackError(Exception):
    """One film alone exceeds a cap; the message names it by position, never a cell."""


def _rows_text(rows: Sequence[Sequence[str]]) -> str:
    """``rows`` as CSV lines, quoted only where a cell needs it."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator=_LINE_END)
    writer.writerows(rows)
    return buffer.getvalue()


def csv_text(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """One whole CSV file: ``header``, then each of ``rows``."""
    return _rows_text([header, *rows])


def _size(text: str) -> int:
    """``text``'s length in UTF-8 bytes, the unit the byte cap counts."""
    return len(text.encode("utf-8"))


def _check_fits(
    film: Sequence[Sequence[str]],
    film_size: int,
    where: str,
    *,
    max_rows: int,
    max_bytes: int,
) -> None:
    """Refuse a film that cannot fit an empty file (``max_bytes`` counts the header)."""
    if len(film) > max_rows or film_size > max_bytes:
        raise PackError(
            f"{where} has {len(film)} rows and needs {film_size} bytes with the "
            f"header, more than one file holds ({max_rows} rows, {max_bytes} bytes)"
        )


# fuzz-target
def pack(
    header: Sequence[str],
    films: Sequence[Sequence[Sequence[str]]],
    *,
    max_rows: int,
    max_bytes: int,
) -> tuple[str, ...]:
    """Each file's CSV text, films packed whole and in order; ``()`` for no films.

    A new file starts when the next film would take the current one past ``max_rows``
    data rows or ``max_bytes`` bytes, header included. Raises ``PackError`` when a film
    cannot fit even an empty file.
    """
    header_text = _rows_text([header])
    files: list[str] = []
    chunks: list[str] = []
    # The open file's data rows, and its bytes counting the header.
    rows, size = 0, _size(header_text)
    for index, film in enumerate(films):
        text = _rows_text(film)
        _check_fits(
            film,
            _size(header_text + text),
            f"film {index + 1} of {len(films)}",
            max_rows=max_rows,
            max_bytes=max_bytes,
        )
        if chunks and (rows + len(film) > max_rows or size + _size(text) > max_bytes):
            files.append(header_text + "".join(chunks))
            chunks, rows, size = [], 0, _size(header_text)
        chunks.append(text)
        rows += len(film)
        size += _size(text)
    if chunks:
        files.append(header_text + "".join(chunks))
    return tuple(files)
