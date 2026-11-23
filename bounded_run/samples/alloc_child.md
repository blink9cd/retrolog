# `alloc_child.py`: a child that allocates past a memory bound

A test fixture for `bounded_run`'s `memory_limit=`. It allocates memory in chunks and writes
every byte of each, so the pages are resident rather than only reserved: an untouched
allocation costs no resident memory and would never trip the bound. It prints one line per
chunk (`allocated <n> MiB`) as it lands, then `done` once it has allocated and held everything,
so a caller can tell a run that was killed partway from one that finished.

`tests/test_memory_limit.py` runs it as the child of a bounded run, and `README.md` beside this
file shows a run by hand.

## Options

| Option | Meaning |
| --- | --- |
| `--chunk-mib` | Size of each chunk, in MiB. |
| `--total-mib` | Stop allocating once this much is held, in MiB. |
| `--pause-seconds` | Wait between two chunks, which sets the allocation rate a poll sees. |
| `--hold-seconds` | Keep everything allocated this long before printing `done` and exiting: how two siblings are made to hold memory at once. |
| `--pid-file` | Record this process's pid in the named file first, so a test can confirm it is gone after a kill and end it itself if not. |

Every option has a default, which `--help` prints; a bare run allocates a little and exits 0.
