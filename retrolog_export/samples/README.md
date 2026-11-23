# retrolog_export samples

A synthetic resolve folder, an unpacked account export, an export config, and the
Letterboxd import files and review `retrolog_export` writes for them, runnable by hand
with no network and no account. Nothing here comes from a real list or a real
account: `build_export_inputs.py` writes every input, every title is a placeholder
such as `Placeholder Film One` (or `???`, a title with no letter or digit), every
director's name is a placeholder, and every id is made up. The rating keys, their
words, and their tags (`a` to `c`, `low` to `high`) are invented too. A second
sample, of books for Goodreads, is under "The Goodreads sample"; its titles, authors,
work keys, and ISBN-13s are invented the same way.

## Regenerate and compare

From the tool's folder (the one holding `retrolog_export.py`):

```bash
work="$(mktemp -d "${TMPDIR:-/tmp}/retrolog_export.XXXXXX")"
python3 -I samples/build_export_inputs.py --out "$work"
python3 -I retrolog_export.py --service letterboxd --resolved "$work/resolved" --account "$work/account" --config "$work/export.toml" --out "$work/out" --rows-per-file 4
diff -r -x .retrolog_export "$work/out" samples/expected
```

Any Python 3.12 or later works in place of the `python3` shown, with nothing
installed into it: the builder and the tool import the standard library only (the
tests' property-test strategies, which need `hypothesis`, live in a module the builder
does not import). `mktemp -d` gives a fresh, empty folder each time; it must not lie
inside a git work tree, or the tool refuses `--out`. The `-x` option leaves the
tool's marker out of the comparison. `--rows-per-file 4` is far below the default so
that the seven sent rows split into two files.

The tool prints, and exits `1`, because the sample holds back five films:

```text
films sent: 6
rows sent: 7
rows cut: 2
files: 2
review held-ambiguous: 1
review held-failed: 1
review held-split: 2
review held-no-title: 1
review omitted: 1
review on-service: 1
review same-date: 1
review logged-no-stars: 1
review watched-only: 1
review override-unchecked: 1
review by-title: 1
review unrated: 1
review undated: 1
review formula-cell: 1
```

Standard error adds `retrolog_export.py: 5 error item(s); see review.md`, and `diff`
prints nothing: both import files, `review.json`, and `review.md` match byte for byte.

## The dry run and the test run

Add `--dry-run` to the run above, with a new `--out`: it prints the same summary and
the same exit code, but writes nothing, and `"$work/dry"` does not exist afterward:

```bash
# shellcheck disable=SC2154 # work is the folder the block above made
python3 -I retrolog_export.py --service letterboxd --resolved "$work/resolved" --account "$work/account" --config "$work/export.toml" --out "$work/dry" --rows-per-file 4 --dry-run
```

`--test-film` writes the one-film test file, and exits `0`:

```bash
# shellcheck disable=SC2154 # work is the folder the block above made
python3 -I retrolog_export.py --service letterboxd --resolved "$work/resolved" --account "$work/account" --config "$work/export.toml" --out "$work/test" --test-film
diff -r -x .retrolog_export "$work/test" samples/expected-test
```

It prints `test film rows: 1` and `file: letterboxd-test.csv`, and `diff` prints
nothing. The sample has one `logged-no-stars` film, `Placeholder Film Twelve`, so the
file holds its one sent row, the 2019 row, with an empty `Rating`.

## What the inputs hold

`export.toml` is `export.example.toml` from this folder, unchanged: its ratings,
rating tags, rating names, tags, review note and rating sentence (on commented rows),
cutoff, and omit list all apply. The account export is invented like the rest: its
`watched.csv` lists three films, its `ratings.csv` two of them, and its `diary.csv`
has one entry the owner logged and one an earlier import tagged
`retrolog, retrolog-mid`, which puts one film in each account class. Every other film
is on no account file, and is sent as it is.

| Row | Shows |
| --- | --- |
| `Placeholder Film Six`, undated | Sent first, with no `WatchedDate`, never cut: an `undated` entry. Its `Directors` cell starts with `@`: written as it is, and a `formula-cell` entry. |
| `Placeholder Film Eleven`, 2010 and 2019 | One film, two rows kept together in the first file, oldest first; the second is a rewatch. An override with a tmdbID and no TMDb title, as when resolve could not read its details: an `override-unchecked` entry. The 2010 row's comment gets the review note and its rating sentence (`Original rating: high.`) above it. |
| `Placeholder Film One` | A split rating key (`b/c`) sharing its 3 stars with `b`, so its rating tag, `retrolog-mid-high`, keeps the split; and two directors joined by a comma. |
| `Placeholder Film Two` | `ambiguous` in resolve: held back, a `held-ambiguous` error. |
| `Placeholder Film Three` | `not-found` and unrated: sent by title in the second file, a `by-title` and an `unrated` entry. |
| `Placeholder Film Four` | `failed` in resolve: held back, a `held-failed` error. |
| `Placeholder Film Five` | On the account only in `watched.csv`, with no rating or diary entry: sent in full, with its stars, and a `watched-only` entry. |
| `Placeholder Film Twelve`, 2010 and 2019 | Logged by the owner: in `ratings.csv`, and in `diary.csv` with no `retrolog` tag. Its 2010 row shares that diary entry's date, so it is not sent: a `same-date` entry. Its 2019 row is sent with an empty `Rating`, a `logged-no-stars` entry, still carrying its rating tag, `retrolog-high`, and the review's rating sentence; with its first row gone, it is the last film sent. |
| `Placeholder Film Thirteen` | Imported before: its `diary.csv` entry carries a `retrolog` tag, so it is not sent, even though `ratings.csv` lists it too: an `on-service` entry. |
| `Placeholder Film Seven` | An `unresolvable` override, which would be sent by title and year; the config's omit list names it, so it is not sent: an `omitted` entry. |
| `Placeholder Film Eight`, twice | One title split between a row with a tmdbID and a `not-found` row: both held, two `held-split` errors. |
| `???` | A title with no letter or digit: held, a `held-no-title` error. |
| `Placeholder Film Nine`, `Placeholder Film Ten` | The row the cutoff names and the one after it: both cut. |

## The Goodreads sample

`--service goodreads` builds the book half instead: three book CSVs, a Goodreads
library export, and the same `export.toml`, whose `[goodreads]` table applies here.
`goodreads_inputs.py` beside the builder holds the rows, as invented as the films'.

```bash
gr="$(mktemp -d "${TMPDIR:-/tmp}/retrolog_export.XXXXXX")"
python3 -I samples/build_export_inputs.py --service goodreads --out "$gr"
python3 -I retrolog_export.py --service goodreads --resolved "$gr/resolved" --account "$gr/account/goodreads_library_export.csv" --config "$gr/export.toml" --out "$gr/out"
diff -r -x .retrolog_export "$gr/out" samples/expected-goodreads
```

The default caps make one file. The tool prints, and exits `1`, because the sample
holds back one book:

```text
books sent: 9
rows sent: 9
rows cut: 2
files: 1
review held-ambiguous: 1
review omitted: 1
review on-service: 2
review remove-first: 2
review by-title: 1
review title-only: 1
review author-missing: 1
review reread-merged: 1
review unrated: 1
review undated: 1
review formula-cell: 1
```

Standard error adds `retrolog_export.py: 1 error item(s); see review.md`, and `diff`
prints nothing. The library export lists four books: `Placeholder Book Seven` read,
`Placeholder Book Eleven` rated 5 on a custom shelf, `Placeholder Book Twelve` on
that shelf unrated, and `Placeholder Book Fifteen` on `to-read`. The config's
`[goodreads.shelf_tags]` maps the custom shelf to `custom-shelf-read`.

| Book | Shows |
| --- | --- |
| `Placeholder Book Fourteen`, undated | Sent first, with an empty `Date Read` and the config's `Date Added`, `1900/01/01`: an `undated` entry. |
| `Placeholder Book One` | Resolved, with a comment: `My Review` holds it, and `Private Notes` the note and rating sentence. |
| `Placeholder Book Two` | An override with an ISBN-13 and two authors: the second is `Additional Authors`. |
| `Placeholder Book Three` | An override with a work key and an author, no ISBN-13: a `by-title` entry. Its comment starts with `=`: written as it is, and a `formula-cell` entry. |
| `Placeholder Book Four` | An override with no ids and no author: sent by `Title` alone, a `title-only` entry. |
| `Placeholder Book Five` | An override with an ISBN-13 and a work key whose lookup gave no author: an `author-missing` entry. |
| `Placeholder Book Six`, 2010 and 2019 | Read twice: one row, the 2019 read's date and rating, with `Earlier reads: 2010.` in `Private Notes`, and a `reread-merged` entry covering both rows. |
| `Placeholder Book Seven` | Read on the account: not sent, an `on-service` entry. |
| `Placeholder Book Ten` | `ambiguous` in resolve: held back, a `held-ambiguous` error. |
| `Placeholder Book Nine` | Named in the config's `[[goodreads.omit]]`: an `omitted` entry. |
| `Placeholder Book Eleven` | Rated on the account's custom shelf, so read there: not sent, an `on-service` entry. |
| `Placeholder Book Twelve` | On the account's custom shelf with no rating: sent, a `remove-first` entry naming the shelf, and `custom-shelf-read` added to its `Bookshelves`. Unrated in the lists too: an `unrated` entry. |
| `Placeholder Book Fifteen` | On the account's `to-read` shelf: sent, a `remove-first` entry, and no tag, since the config maps none to that shelf. |
| `Placeholder Book Eight`, `Placeholder Book Thirteen` | The row the `[goodreads]` cutoff names and the one after it: both cut. |

`../tests/test_export_goodreads_cli.py` runs these steps and compares the result with
`expected-goodreads/`.

## What the sample cannot show

The one review state the sample cannot show is a cutoff naming no row: that stops the
run with exit `2` before anything is written, and `../tests/test_export_cli.py` covers
it. That test file also runs the Letterboxd steps above and compares the result with
`expected/`, so the sample cannot drift from the tool.
