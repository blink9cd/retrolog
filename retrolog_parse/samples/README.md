# retrolog_parse samples

A synthetic source tree, the rules file that reads it, and the output `retrolog_parse`
writes for it, runnable by hand. Nothing here comes from a real list:
`build_parse_tree.py` writes every file, and every title is a placeholder such as
`Book One` or `Film Four`.

## Regenerate and compare

From the tool's folder (the one holding `retrolog_parse.py`):

```bash
work="$(mktemp -d)"
python3 samples/build_parse_tree.py --out "$work/tree"
python3 retrolog_parse.py --path "$work/tree" --rules samples/rules.example.toml --out "$work/out"
diff -r -x .retrolog_parse "$work/out" samples/expected
```

Any Python 3.12 or later with `python-docx` installed works in place of the `python3`
shown. `mktemp -d` gives a fresh, empty folder each time; it must not lie inside a git
work tree, or the tool refuses `--out`. The `-x` leaves out the marker file the tool
writes into `--out`.

The tool prints, and exits `0`:

```text
rows: 18
unparsed: 1
unrated: 2
unplaced: 1
unknown-code: 1
legend: 1
override-applied: 1
progress-skipped: 1
```

`diff` prints nothing: every CSV, `review.json` and `review.md` match byte for byte.

## What the tree holds

| File | Shows |
| --- | --- |
| 21-22/autumn books.txt | A two-year folder's file whose name holds an earlier word that is also a season: October of the first year. |
| 21-22/movies.txt | A file the `[[terms]]` block dates; a divider line. |
| 21-22/more books.txt | No earlier or later word and no term block: its row is undated, in `book-undated.csv`. |
| 23/spring books.txt | A progress note, finished by the rated `Long Book` in 23/books.txt, which is dated later. |
| 23/books.txt | A heading, a blank line, two sections, a split code, a rewatch, a comment, an override, an unknown code, an entry with no code and an unparsed line. |
| 23/movies.docx | A Word file: a legend of four colored symbol marks sharing its line with an entry, then two entries on one line. |
| movies (24).txt | A year stated in the file name, and a work year `(1999)` taken out of the title. |

There is no `.doc` sample: one cannot be written without a converter, and the tool
reads a `.doc` through the same Word reader as a `.docx`, after converting a copy.

## What you should see

- `book-2023.csv` holds `Book Four` rated `2/3`, `Book Five` with `rewatch` `true`,
  `Book Six` with the comment `a gift`, and the `section` column reading `work`, then
  `home`.
- `Odd Book` is rated `2` by the override, and `Other Book - q` keeps its unknown code
  in the title, unrated.
- `film-2023.csv` rates the Word entries by their marks' colors, recorded in `color`.
- `review.md` lists no error, so the run exits `0`.

`../tests/test_parse_cli.py` runs the same three steps and compares the result with
`expected/`, so the sample cannot drift from the tool.
