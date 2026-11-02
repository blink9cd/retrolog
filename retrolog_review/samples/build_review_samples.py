"""Write retrolog_review's sample outputs: the review page and the overrides file.

Both inputs beside this file are invented. ``review.json`` is a byte copy of
retrolog_resolve's sample review (its ``samples/expected/review.json``), and
``picks.example.json`` holds picks made against it: two candidates, a typed id, "None
of these", and four keeps, with one entry left undecided. ``picks.round-2.json`` is a
second round's picks against the same review, settling that last entry. ``--out DIR``
receives:

- ``DIR/review.html``: what ``--mode page`` writes for ``review.json``;
- ``DIR/overrides.toml``: what ``--mode overrides`` writes for the first picks;
- ``DIR/overrides-round-2.toml``: a copy of that file with the second round's picks
  added by ``--mode overrides --append-to``.

The tool refuses an ``--out`` inside a git work tree, as retrolog_resolve does, so
each mode writes into a fresh temporary folder (the system's, outside the
repository) and the result is then copied into ``DIR``. ``--out samples/expected``
regenerates the committed expected files; README.md gives the commands.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOL = _HERE.parent
# The tool itself, which puts its own src/ folders and the shared libraries on
# sys.path as it loads.
sys.path.insert(0, str(_TOOL))

import retrolog_review  # noqa: E402 - after the sys.path insert above

REVIEW = _HERE / "review.json"
PICKS = _HERE / "picks.example.json"
PICKS_ROUND_2 = _HERE / "picks.round-2.json"
PAGE_NAME = "review.html"
OVERRIDES_NAME = "overrides.toml"
ROUND_2_NAME = "overrides-round-2.toml"
_EXIT_OK = 0


def _run_modes(work: Path) -> int:
    """Run both modes, then the append, into ``work``; the first non-zero exit code."""
    page = [
        "--mode", "page",
        "--review", str(REVIEW),
        "--out", str(work / PAGE_NAME),
    ]  # fmt: skip
    overrides = [
        "--mode", "overrides",
        "--review", str(REVIEW),
        "--picks", str(PICKS),
        "--out", str(work / OVERRIDES_NAME),
    ]  # fmt: skip
    for argv in (page, overrides):
        code = retrolog_review.main(argv)
        if code != _EXIT_OK:
            return code
    # The second round adds to a copy, so the first round's file stays a sample too.
    shutil.copyfile(work / OVERRIDES_NAME, work / ROUND_2_NAME)
    return retrolog_review.main(
        [
            "--mode", "overrides",
            "--review", str(REVIEW),
            "--picks", str(PICKS_ROUND_2),
            "--append-to", str(work / ROUND_2_NAME),
        ]
    )  # fmt: skip


def write_samples(out: Path) -> int:
    """Write the three sample outputs into ``out``; the tool's exit code (0 written)."""
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        code = _run_modes(work)
        if code != _EXIT_OK:
            return code
        out.mkdir(parents=True, exist_ok=True)
        for name in (PAGE_NAME, OVERRIDES_NAME, ROUND_2_NAME):
            shutil.copyfile(work / name, out / name)
    return _EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """The CLI: one named option."""
    parser = argparse.ArgumentParser(
        description="Write retrolog_review's sample page and overrides files."
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help=f"Directory to write {PAGE_NAME}, {OVERRIDES_NAME}, and {ROUND_2_NAME} "
        "into; created if missing, and the files replaced if present.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the samples and return the tool's exit code."""
    args = build_parser().parse_args(argv)
    return write_samples(args.out)


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
