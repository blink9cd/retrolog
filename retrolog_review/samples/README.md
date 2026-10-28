# retrolog_review samples

An invented review, picks made against it, and the page and overrides file
`retrolog_review` writes for them, runnable by hand with no network and nothing
installed. Nothing here comes from a real list: `review.json` is a byte copy of
`retrolog_resolve`'s own sample review (`../../retrolog_resolve/samples/expected/review.json`),
whose titles are placeholders such as `Placeholder Film Two` and whose ids are made up,
and `picks.example.json` picks among those same invented entries.

## Run both modes and compare

From the tool's folder (the one holding `retrolog_review.py`):

```bash
work="$(mktemp -d)"
python3 retrolog_review.py --mode page --review samples/review.json --out "$work/review.html"
python3 retrolog_review.py --mode overrides --review samples/review.json --picks samples/picks.example.json --out "$work/overrides.toml"
diff "$work/review.html" samples/expected/review.html
diff "$work/overrides.toml" samples/expected/overrides.toml
```

Any Python 3.12 or later works in place of the `python3` shown, with nothing installed
into it. `mktemp -d` gives a fresh folder outside the repository, which matters: the
tool refuses an `--out` inside a git work tree, as `retrolog_resolve` does, because a
real page and a real overrides file both name the operator's titles. For the same
reason the second command refuses an `overrides.toml` that already exists, so run it
into a fresh folder each time.

The two commands print, and each exits `0`:

```text
8 entries on the page; 3 errors and 4 applied overrides not shown.
3 overrides written for 3 picks, 4 kept, 1 undecided.
```

and neither `diff` prints anything. Open `"$work/review.html"` in a browser to see the
page: it loads from `file://`, sends nothing, and its "Save picks file" button saves a
`picks.json` in the shape of `picks.example.json`.

## What the inputs hold

`review.json` has fifteen entries. The page shows the eight decidable ones; the three
error entries and the four `override-applied` ones are only counted, since they are
fixed by editing the overrides file by hand. `picks.example.json` answers seven of the
eight:

| Entry                    | Kind          | Pick                 | Writes                                 |
| ------------------------ | ------------- | -------------------- | -------------------------------------- |
| `Placeholder Book Five`  | `not-found`   | None of these        | `unresolvable = true` for its row      |
| `Placeholder Book Four`  | `ambiguous`   | (none)               | nothing: it is the one undecided       |
| `Placeholder Book Three` | `no-isbn`     | Keep                 | nothing                                |
| `Placeholder Film Six`   | `most-voted`  | Keep                 | nothing                                |
| `Placeholder Film Three` | `not-found`   | A typed TMDb id      | `tmdb_id` for its row                  |
| `Placeholder Film Two`   | `ambiguous`   | Its second candidate | that candidate's `tmdb_id` for its row |
| `Placeholder Story II`   | `variant`     | Keep                 | nothing                                |
| `The Placeholder Hour`   | `re-searched` | Keep                 | nothing                                |

Each pick names its entry by medium, title and rows, and writes one line override per
row (`file` and `line`), so `expected/overrides.toml` holds three entries.
`picks.example.json`'s `review_sha256` is the SHA-256 of `review.json`'s bytes: the tool
refuses picks made against any other review.

## Regenerate the expected files

After a change to the tool or to its `page/` files alters either output:

```bash
python3 samples/build_review_samples.py --out samples/expected
```

The builder runs both modes into a temporary folder outside the repository and copies
the two results in. `../tests/test_review_samples.py` runs the same builder and compares
the result with `expected/` byte for byte, so the samples cannot drift from the tool; it
also checks that `review.json` is still resolve's sample and that the picks' digest is
its own.
