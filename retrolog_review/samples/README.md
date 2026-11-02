# retrolog_review samples

An invented review, two rounds of picks made against it, and the page and overrides
files `retrolog_review` writes for them, runnable by hand with no network and nothing
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
cp "$work/overrides.toml" "$work/overrides-round-2.toml"
python3 retrolog_review.py --mode overrides --review samples/review.json --picks samples/picks.round-2.json --append-to "$work/overrides-round-2.toml"
diff "$work/overrides-round-2.toml" samples/expected/overrides-round-2.toml
```

Any Python 3.12 or later works in place of the `python3` shown, with nothing installed
into it. `mktemp -d` gives a fresh folder outside the repository, which matters: the
tool refuses an `--out` inside a git work tree, as `retrolog_resolve` does, because a
real page and a real overrides file both name the operator's titles. For the same
reason the second command refuses an `overrides.toml` that already exists, so run it
into a fresh folder each time.

The three runs print, and each exits `0`:

```text
9 entries on the page; 3 errors and 4 applied overrides not shown.
4 overrides written for 4 picks, 4 kept, 1 undecided.
1 override appended for 1 pick, 0 kept, 8 undecided; the file now holds 5 overrides.
```

and no `diff` prints anything. The third run is a second round: it adds its one
override to a copy of the first round's file, whose text it keeps as it was. Run it
again on the same file and it is refused with exit `1`, because its entry is then
already there. Open `"$work/review.html"` in a browser to see the page: it loads from
`file://`, sends nothing, and its "Save picks file" button saves a `picks.json` in the
shape of `picks.example.json`.

## What the inputs hold

`review.json` has sixteen entries. The page shows the nine decidable ones; the three
error entries and the four `override-applied` ones are only counted, since an error is
fixed by editing the overrides file by hand and an applied override needs nothing.
`picks.example.json` answers eight of the nine:

| Entry                    | Kind            | Pick                 | Writes                                 |
| ------------------------ | --------------- | -------------------- | -------------------------------------- |
| `Placeholder Book Five`  | `not-found`     | None of these        | `unresolvable = true` for its row      |
| `Placeholder Book Four`  | `ambiguous`     | (none)               | nothing: it is the one undecided       |
| `Placeholder Book Three` | `no-isbn`       | Keep                 | nothing                                |
| `Placeholder Film Seven` | `suspect-exact` | Its one candidate    | that candidate's `tmdb_id` for its row |
| `Placeholder Film Six`   | `most-voted`    | Keep                 | nothing                                |
| `Placeholder Film Three` | `not-found`     | A typed TMDb id      | `tmdb_id` for its row                  |
| `Placeholder Film Two`   | `ambiguous`     | Its second candidate | that candidate's `tmdb_id` for its row |
| `Placeholder Story II`   | `variant`       | Keep                 | nothing                                |
| `The Placeholder Hour`   | `re-searched`   | Keep                 | nothing                                |

Each pick names its entry by medium, title, and rows, and writes one line override per
row (`file` and `line`), so `expected/overrides.toml` holds four entries.
`picks.round-2.json` is the second round: it picks the second candidate for
`Placeholder Book Four`, the entry the first round left undecided, so
`expected/overrides-round-2.toml` is `expected/overrides.toml` with one more entry, its
`ol_work`. Both picks files' `review_sha256` is the SHA-256 of `review.json`'s bytes:
the tool refuses picks made against any other review.

## Regenerate the expected files

After a change to the tool or to its `page/` files alters either output:

```bash
python3 samples/build_review_samples.py --out samples/expected
```

The builder runs both modes and the second round's append into a temporary folder
outside the repository and copies the three results in.
`../tests/test_review_samples.py` runs the same builder and compares the result with
`expected/` byte for byte, so the samples cannot drift from the tool; it also checks
that `review.json` is still resolve's sample and that both picks files' digest is its
own.
