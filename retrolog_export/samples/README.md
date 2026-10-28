# retrolog_export samples

A synthetic resolve folder, an unpacked account export, an export config, and the
Letterboxd import files and review `retrolog_export` writes for them, runnable by hand
with no network and no account. Nothing here comes from a real list or a real
account: `build_export_inputs.py` writes every input, every title is a placeholder
such as `Placeholder Film One` (or `???`, a title with no letter or digit), every
director's name is a placeholder, and every id is made up. The rating keys, their
words, and their tags (`a` to `c`, `low` to `high`) are invented too.

## Regenerate and compare

From the tool's folder (the one holding `retrolog_export.py`):

```bash
work="$(mktemp -d)"
python3 samples/build_export_inputs.py --out "$work"
python3 retrolog_export.py --service letterboxd --resolved "$work/resolved" --account "$work/account" --config "$work/export.toml" --out "$work/out" --rows-per-file 4
diff -r -x .retrolog_export "$work/out" samples/expected
```

Any Python 3.12 or later works in place of the `python3` shown, with nothing
installed into it: the builder and the tool import the standard library only (the
tests' property-test strategies, which need `hypothesis`, live in a module the builder
does not import). `mktemp -d` gives a fresh, empty folder each time; it must not lie
inside a git work tree, or the tool refuses `--out`. The `-x` option leaves the
tool's marker out of the comparison. `--rows-per-file 4` is far below the default so
that six rows split into two files.

The tool prints, and exits `1`, because the sample holds back five films:

```text
films sent: 5
rows sent: 6
rows cut: 2
files: 2
review held-ambiguous: 1
review held-failed: 1
review held-split: 2
review held-no-title: 1
review on-service: 1
review override-unchecked: 1
review by-title: 2
review unrated: 1
review undated: 1
review formula-cell: 1
```

Standard error adds `retrolog_export.py: 5 error item(s); see review.md`, and `diff`
prints nothing: both import files, `review.json`, and `review.md` match byte for byte.

## What the inputs hold

`export.toml` is `export.example.toml` from this folder, unchanged: its ratings,
rating tags, rating names, tags, review note and rating sentence (on commented rows),
and cutoff all apply. The account export's `watched.csv` lists one film.

| Row | Shows |
| --- | --- |
| `Placeholder Film Six`, undated | Sent first, with no `WatchedDate`, never cut: an `undated` entry. Its `Directors` cell starts with `@`: written as it is, and a `formula-cell` entry. |
| `Placeholder Film Eleven`, 2010 and 2019 | One film, two rows kept together in the first file, oldest first; the second is a rewatch. An override with a tmdbID and so no TMDb title: an `override-unchecked` entry. The 2010 row's comment gets the review note and its rating sentence (`Original rating: high.`) above it. |
| `Placeholder Film One` | A split rating key (`b/c`) sharing its 3 stars with `b`, so its rating tag, `retrolog-mid-high`, keeps the split; and two directors joined by a comma. |
| `Placeholder Film Two` | `ambiguous` in resolve: held back, a `held-ambiguous` error. |
| `Placeholder Film Three` | `not-found` and unrated: sent by title in the second file, a `by-title` and an `unrated` entry. |
| `Placeholder Film Four` | `failed` in resolve: held back, a `held-failed` error. |
| `Placeholder Film Five` | On the account export, so not sent: an `on-service` entry. |
| `Placeholder Film Seven` | An `unresolvable` override: sent by title and year, a `by-title` entry. |
| `Placeholder Film Eight`, twice | One title split between a row with a tmdbID and a `not-found` row: both held, two `held-split` errors. |
| `???` | A title with no letter or digit: held, a `held-no-title` error. |
| `Placeholder Film Nine`, `Placeholder Film Ten` | The row the cutoff names and the one after it: both cut. |

The one review state the sample cannot show is a cutoff naming no row: that stops the
run with exit `2` before anything is written, and `../tests/test_export_cli.py` covers
it. That test file also runs the three steps above and compares the result with
`expected/`, so the sample cannot drift from the tool.
