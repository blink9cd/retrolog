"""Synthetic Letterboxd account exports for retrolog_restore's tests and sample.

Every title is a placeholder and every URI is made up, so no test holds a real
account. The sample builder imports this module, so it uses the standard library
only; the property-based tests define their strategies in their own files instead.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

# The columns of Letterboxd's diary.csv, reviews.csv, and likes/films.csv, in the order
# its "Export your data" writes them.
DIARY_HEADER = (
    "Date",
    "Name",
    "Year",
    "Letterboxd URI",
    "Rating",
    "Rewatch",
    "Tags",
    "Watched Date",
)
REVIEWS_HEADER = (
    "Date",
    "Name",
    "Year",
    "Letterboxd URI",
    "Rating",
    "Rewatch",
    "Review",
    "Tags",
    "Watched Date",
)
LIKES_HEADER = ("Date", "Name", "Year", "Letterboxd URI")
# Every cell a row leaves out reads as this.
_DEFAULTS: Mapping[str, str] = {
    "Date": "2026-02-01",
    "Name": "Placeholder Film One",
    "Year": "1999",
    "Letterboxd URI": "https://boxd.it/placeholder-1",
    "Rating": "3",
    "Rewatch": "",
    "Review": "",
    "Tags": "",
    "Watched Date": "2026-01-02",
}


def entry(**cells: str) -> dict[str, str]:
    """One export row: ``_DEFAULTS`` with ``cells`` (by column name) on top."""
    return {**_DEFAULTS, **cells}


def csv_text(rows: Sequence[Mapping[str, str]], header: Sequence[str]) -> str:
    """``rows`` under ``header`` as CSV text, each row's cells taken by column name."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([[row.get(name, "") for name in header] for row in rows])
    return buffer.getvalue()


def export_files(
    diary: Sequence[Mapping[str, str]],
    reviews: Sequence[Mapping[str, str]] | None = None,
    likes: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, str]:
    """An export's ``diary.csv`` and, when given, ``reviews.csv`` and likes file."""
    files = {"diary.csv": csv_text(diary, DIARY_HEADER)}
    if reviews is not None:
        files["reviews.csv"] = csv_text(reviews, REVIEWS_HEADER)
    if likes is not None:
        files["likes/films.csv"] = csv_text(likes, LIKES_HEADER)
    return files


def write_folder(folder: Path, files: Mapping[str, str | bytes]) -> Path:
    """An unpacked export: each ``relative name -> text or bytes`` in ``folder``."""
    for name, content in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)
    return folder


def write_zip(path: Path, files: Mapping[str, str | bytes]) -> Path:
    """An export ``.zip`` holding each ``member name -> text or bytes``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path
