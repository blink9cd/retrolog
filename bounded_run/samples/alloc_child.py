#!/usr/bin/env python3
"""Test fixture: allocate and touch memory in chunks, reporting each, to cross a memory bound.

The memory-bound tests run it as the child of ``run_bounded(..., memory_limit=...)``. Every
chunk is a ``bytes`` filled by repetition, so its pages are written, not merely reserved: an
untouched allocation would cost no resident memory and never trip the bound. Each chunk is
reported on stdout as it lands, so a test can see the partial output a breach carries.

With ``--hold-seconds`` it keeps what it allocated for that long before exiting, which is how
two siblings are made to hold memory at the same time, so only their sum crosses the bound.
With ``--pid-file`` it records its own pid first, so a test can confirm it is gone after the
kill and end it itself if not.
"""

import argparse
import os
import sys
import time

#: One mebibyte, the unit every size option is given in.
_MIB = 1024 * 1024


def _parse_args() -> argparse.Namespace:
    """The named options; every one has a default, so a bare run allocates a little and exits."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--chunk-mib", type=int, default=64, help="size of each chunk")
    parser.add_argument(
        "--total-mib", type=int, default=256, help="stop after this much"
    )
    parser.add_argument(
        "--pause-seconds", type=float, default=0.02, help="wait between chunks"
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=0.0,
        help="keep the memory this long, then exit",
    )
    parser.add_argument("--pid-file", default="", help="record this process's pid here")
    return parser.parse_args()


def main() -> int:
    """Allocate ``--total-mib`` in ``--chunk-mib`` steps, hold it, and report ``done``."""
    args = _parse_args()
    if args.pid_file:
        with open(args.pid_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(str(os.getpid()))
    held: list[bytes] = []
    while len(held) * args.chunk_mib < args.total_mib:
        # Repetition writes every byte, so each page is resident, not only reserved.
        held.append(b"\x01" * (args.chunk_mib * _MIB))
        print(f"allocated {len(held) * args.chunk_mib} MiB", flush=True)
        time.sleep(args.pause_seconds)
    time.sleep(args.hold_seconds)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
