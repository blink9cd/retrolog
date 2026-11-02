# retrolog_resolve samples

A synthetic parse folder, an overrides file, a canned cache of service answers, and the
output `retrolog_resolve` writes for them, runnable by hand with no network and no
token. Nothing here comes from a real list or a real service answer:
`build_resolve_inputs.py` writes every file, every title is a placeholder such as
`Placeholder Book Two` or a public-domain work (`Moby Dick`, `Metropolis`), and every
id and ISBN is made up.

## Regenerate and compare

From the tool's folder (the one holding `retrolog_resolve.py`):

```bash
work="$(mktemp -d)"
python3 samples/build_resolve_inputs.py --out "$work"
python3 retrolog_resolve.py --parsed "$work/parsed" --out "$work/out" --overrides "$work/overrides.toml" --cache-only
diff -r -x .retrolog_resolve -x cache "$work/out" samples/expected
```

Any Python 3.12 or later works in place of the `python3` shown, with nothing
installed into it: the builder and the tool import the standard library only (the
tests' property-test strategies, which need `hypothesis`, live in a module the builder
does not import). `mktemp -d` gives a fresh, empty folder each time; it must not lie
inside a git work tree, or the tool refuses `--out`. The builder writes `--out`'s
marker and `cache` folder itself, so the run is answered entirely from the cache; the
`-x` options leave both out of the comparison.

The tool prints, and exits `1`, because the sample shows each error kind once:

```text
rows: 17
status resolved: 7
status override: 4
status ambiguous: 3
status not-found: 2
status failed: 1
review override-unused: 1
review override-mismatch: 1
review failed: 1
review ambiguous: 2
review suspect-exact: 1
review not-found: 2
review most-voted: 1
review variant: 1
review re-searched: 1
review no-isbn: 1
review override-applied: 4
```

Standard error adds `retrolog_resolve.py: 3 error item(s); see review.md`, and `diff`
prints nothing: both CSVs, `review.json`, and `review.md` match byte for byte.

## One medium only

The same inputs, resolved with `--medium film`, from a second fresh folder so its `out`
holds only the builder's marker and cache:

```bash
film="$(mktemp -d)"
python3 samples/build_resolve_inputs.py --out "$film"
python3 retrolog_resolve.py --parsed "$film/parsed" --out "$film/out" --overrides "$film/overrides.toml" --cache-only --medium film
diff -r -x .retrolog_resolve -x cache "$film/out" samples/expected-film
```

The tool prints, and exits `1`:

```text
rows: 10
status resolved: 5
status override: 2
status ambiguous: 2
status not-found: 1
review override-mismatch: 1
review ambiguous: 1
review suspect-exact: 1
review not-found: 1
review most-voted: 1
review variant: 1
review re-searched: 1
review override-applied: 2
```

Only the film CSV is written, no book CSV is read, and the `override-unused` entry for
`Placeholder Book Nine` is gone: a book override is out of scope for a film run, not
unused. `review.md` says, under its count, that the run covered films only, and
`review.json`'s `medium` is `film`.

## What the inputs hold

`overrides.toml` is `overrides.example.toml` from this folder, then two entries of the
sample's own.

| Row | Shows |
| --- | --- |
| `Moby Dick`, `Metropolis` | `override`: the example file's title-only entries settle them, unsearched. `Metropolis` takes its TMDb titles and its empty year from the details of its override's id. |
| `Placeholder Book One` | `override` with no id: the example's `unresolvable` entry. |
| `Placeholder Film One`, line 7 | `override`: the example's entry for that line only. Its id's details give it a TMDb original title, `Film Un Factice`, and its empty year. |
| `Placeholder Film One`, line 9 | The same title on another line is still searched, and `resolved`. |
| `Placeholder Book Two` | `resolved` at the first search step, with an ISBN-13. |
| `Placeholder Book Three` | `resolved` only by the last step, with no ISBN: a `no-isbn` entry. |
| `Placeholder Book Four`, `Placeholder Film Two` | `ambiguous`: two exact titles each, listed; the films with their directors, the default `--directors ambiguous`. A third film released after the row's year is dropped. |
| `Placeholder Film Six` | `resolved` by most votes: two exact titles, the first with thirty times the second's votes, so a `most-voted` entry lists both, with their votes and directors. |
| `Placeholder Story II` | `resolved` by a variant: no candidate's title is exact, but under the sequel-numeral variant the row's `II` meets `Placeholder Story 2`'s `2`, so a `variant` entry names it. The other candidate, `Placeholder Story`, matches under no variant. |
| `The Placeholder Hour` | `resolved` by a second search: the first finds only `Placeholder Hourglass`, which no variant meets, so TMDb is searched again for `Placeholder Hour`, whose one match a `re-searched` entry lists with its director. |
| `Placeholder Film Seven` | `ambiguous` under a `suspect-exact` entry: one exact title, but no release date and 0 votes, so it is held, with no ids, its reasons in the detail, and its one candidate listed with its director. |
| `Placeholder Book Five`, `Placeholder Film Three` | `not-found`: every search is empty. |
| `Placeholder Book Six` | `failed`: its answer is left out of the cache, and `--cache-only` sends no request. |
| `Placeholder Film Five`, line 12 | An `override-mismatch`: the sample's entry for line 12 names another title, so it is not applied and the row is searched. |
| (none) | An `override-unused`: the sample's entry for `Placeholder Book Nine`, a title no row has. |

`../tests/test_resolve_cli.py` runs the same three steps and compares the result with
`expected/`, and `../tests/test_resolve_medium.py` does the same for `expected-film/`,
so neither sample can drift from the tool.
