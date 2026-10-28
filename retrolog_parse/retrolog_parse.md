# retrolog_parse

Turn a folder of old book and film lists into dated, rated rows. The tool reads every
`.txt`, `.doc` and `.docx` list under a folder, applies a rules file the operator
writes, and produces one CSV per medium and year in a normalized format, plus a review
list of every line it could not turn into a row cleanly. Nothing is dropped silently:
every line ends up in a row, in the review list, or is a layout line (a blank line, a
divider, a heading or a section name).

It is the second of four steps: survey the folder (`retrolog_survey`), parse it (this
tool), resolve each title to a catalog identifier, then export to a service's import
format. Run the survey first: its report shows which conventions the lists use, and
that is what the rules file describes.

**The output quotes real text from the source files.** The CSVs hold the titles and
comments, and the review list quotes whole lines. Treat both as being as private as
the lists: do not post them or commit them to a repository. The tool refuses to write
inside a git work tree for that reason (see "Where the output may go").

## Usage

```bash
python retrolog_parse.py --path path/to/lists --rules path/to/rules.toml --out path/to/parsed
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--path` | yes | | Source tree to read (read only). |
| `--rules` | yes | | The rules file. |
| `--out` | yes | | Output directory; refused inside `--path` or a git work tree. |
| `--exclude` | no | none | Glob over the relative path, case-insensitive, repeatable; matching files are not read. |
| `--max-bytes` | no | `52428800` | Per-file size limit (text bytes, .doc file size or Word uncompressed size). |
| `--timeout` | no | `120.0` | Seconds per converter or git call. |

Prerequisites: Python 3.12 or later with `python-docx`, and a rules file (start from
`samples/rules.example.toml`). Reading `.doc` files needs macOS, whose built-in
`textutil` converts a copy of each; LibreOffice is used instead when it is installed
and its conversion works. Text and `.docx` files need neither.

Whenever it writes the output (exit `0` or `1`), the tool prints a summary to standard
output, counts only and no text from the lists: first `rows: <n>`, then one
`<kind>: <n>` line for each review kind that occurred, in the order of the review
kinds table below. Kinds with no items are not printed. With exit `1`, standard
error adds `<program>: <n> error item(s); see review.md`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Output written, and no review item is an error. |
| `1` | Output written, but the review lists at least one error (`read-error`, `no-medium`, `rule-file-missing`, `override-mismatch` or `term-mismatch`). A count goes to standard error; read `review.md`. |
| `2` | Bad arguments, `--path` missing or not a directory, the rules file unreadable or invalid, `--out` or the temporary directory refused (nothing was written), or the output could not be written. Each message names the problem, never a path. |

A file that cannot be read is a `read-error` item; the other files are still parsed.

## What it reads, and what it never does

The source tree is only read. No file under `--path` is created, changed, renamed or
locked: a `.doc` is copied into a private temporary directory, and only the copy is
converted. Every output goes to `--out`.

- Only `.txt`, `.doc` and `.docx` files are read. Hidden files, links and every other
  suffix are skipped silently; the survey reports them.
- A file whose name matches no `[media]` glob is not read at all, not even converted:
  it is listed once as `no-medium`. Leave music lists and other non-list files out
  with `--exclude`.
- Text files are decoded the way the survey decodes them, and Word files are read
  paragraph by paragraph, with each symbol-font mark and its color.

What parse never does, by design, because each belongs to a later step:

- **No cutoff.** Every row is written, however recent; deciding which rows a service
  already holds is the export step's job.
- **No stars.** `rating_raw` is the rules file's opaque key, such as `3` or `2/3`;
  mapping a key to a service's rating scale is the export step's job.
- **No deduplication.** An entry logged twice gives two rows. The only rows dropped
  are progress notes a later rated entry finishes, and each is listed as
  `progress-skipped`.
- **No lookups.** `creator` is always empty, and `year` holds only a year written in
  the title as `(YYYY)`; the resolve step fills both.

## The rules file

A TOML file that holds everything specific to one person's lists, so the tool guesses
nothing. `samples/rules.example.toml` explains every key in place and reads the
sample tree as written; copy it next to the lists (never into a repository) and change
every value. The whole file is checked before any list is read: an unknown key, a
missing one, a pattern that does not compile or has the wrong number of groups, or a
rating key no letter or color defines stops the run with exit code `2` and a message
naming the key, such as `ratings.color_tolerance: must be an integer from 0 to 442`.

| Table | Key | What it says |
| --- | --- | --- |
| | `schema` | The file's layout version; only `1` exists. |
| `[media]` | `book`, `film` | File-name globs for each medium, case-insensitive; the first match wins. |
| `[ratings]` | `letters` | One lower-case letter to a rating key, for lines ending in a letter code such as `- c`. |
| `[ratings]` | `split_separator` | Joins two letters into a split rating: `b/c` gives `2/3`, first letter first. |
| `[ratings]` | `colors` | `RRGGBB` to a rating key, for a Word mark's color or an unmarked entry's text color. |
| `[ratings]` | `color_tolerance` | How far, in RGB distance, a color may sit from a listed one and still match it. |
| `[ratings]` | `legend_order` | Optional: the keys in the order a legend line lists them. |
| `[shapes]` | `"<Font>/<HEX4>"` | Optional: the keys a symbol glyph may carry, to cross-check its color. |
| `[lines]` | `headings` | Patterns for list titles, which are not entries. |
| `[lines]` | `sections` | Words that, alone on a line, start a section; recorded in the `section` column. |
| `[lines]` | `progress` | The pattern of a progress note, with a group named `title`. |
| `[dates]` | `two_year_folder`, `one_year_folder` | Patterns for folder names holding two years or one. |
| `[dates]` | `century` | Added to every two-digit year, such as `2000`. |
| `[dates]` | `earlier_words`, `later_words` | File-name words that pick the earlier or later year of a two-year folder. |
| `[dates]` | `explicit_year` | Patterns for a year stated in a file name; it wins over the folder. |
| `[dates]` | `seasons` | File-name words that name a month. |
| `[[terms]]` | `file`, `lines`, `date`, `expect` | Optional: a block of lines dated by hand. Two blocks of one file may not share a line. |
| `[[overrides]]` | `file`, `line`, `expect`, `action`, `value` | Optional: a correction for one line: `skip`, `rating`, `title` or `unrated`. |

A rule's `file` is relative to `--path`, with `/` separators, and must name a file the
run finds: one that names no file is a `rule-file-missing` error. Copy a line's
`expect` text from the `text` field of its item in `review.json`, never from
`review.md`, which escapes markup characters such as `|` and `*` and trims the
line so its tables render.

### How an entry is dated

In this order, the first that applies wins:

1. A `[[terms]]` block holding the line gives its date.
2. A year comes from the file name (`explicit_year`), else from a one-year folder, else
   from a two-year folder: its earlier year when the file name holds words from
   `earlier_words` only, its later year when it holds words from `later_words` only.
   A name holding words from both lists, or from neither, gives no year.
3. The month is that of the first `seasons` word in the file name, else December, and
   the day is that month's last.

An entry none of these dates has an empty `date` and an `unplaced` item. Every date is
approximate today (`date_is_approximate` is `true`), since no list dates an entry to
the day.

## Review kinds

`review.md` opens with a count line, `<n> review items, <e> of them errors.`, then has
one table per kind that occurred, in the order of the table below, so errors come
first. Each table's heading is the kind and its item count, with `, error` added for
an error kind: `## unrated (2)`, `## read-error (1, error)`. `review.json` holds the
same items. Each names the file (relative to `--path`), the line (`0` for a whole
file), the line's text and a short detail.

| Kind | Error | Meaning | What the operator does |
| --- | --- | --- | --- |
| `read-error` | yes | The file could not be read (damaged, too large, or no converter produced output). | Repair or convert the file, raise `--max-bytes`, or exclude it. |
| `no-medium` | yes | The file name matches no `[media]` glob, so it was not read. | Add a glob, or `--exclude` the file. |
| `rule-file-missing` | yes | A `[[terms]]` block or `[[overrides]]` entry names a file the run did not find, so the rule did nothing. The item names the rule's file and first line. A file that was found but excluded or unreadable is not listed. | Fix the rule's `file` (relative to `--path`, `/` separators), or delete the rule. |
| `override-mismatch` | yes | An override's line does not read its `expect` text, a second override names the same line, or the line holds no single entry. The override was not applied. | Fix the override's `line` or `expect`. |
| `term-mismatch` | yes | A term block's first line does not read its `expect` text. The block's date was still applied. | Fix the block's `lines` or `expect`. |
| `unparsed` | no | No entry, heading or section could be read from the line. | Ignore it, or add a heading pattern or a `skip` override. |
| `unrated` | no | The row has no rating. It is still written, unrated. | Rate it by hand later, or add a `rating` override. |
| `unplaced` | no | No rule dates the entry; it is in `<medium>-undated.csv`. | Add a `[[terms]]` block, a break word or a folder pattern. |
| `color-outlier` | no | A mark's color is near no rules color, or equally near two. The detail names the glyph and the color, such as `mark Wingdings/F06C in 3A7FD0`. | Add that color to `colors`, or widen `color_tolerance`. |
| `shape-conflict` | no | A mark's glyph may not carry the rating its color gives. The color decided. The detail names the glyph, the color and that rating. | Check the line; correct that glyph's `[shapes]` entry or add an override. |
| `unknown-code` | no | The letter code after the dash is not one the rules define; it stays in the title. | Add a `rating`, `unrated` or `title` override. |
| `stray-mark` | no | An untitled mark stands beside this entry. | Check the line by eye. |
| `no-color` | no | The entry's mark has no color to rate it by. The detail names its glyph. | Add a `rating` override. |
| `heading-with-rating` | no | A line a heading pattern or a section word matched that also carries a mark or ends in a known code. It was read as a heading or section, and no row was written for it. | Narrow the heading pattern or the section word if the line is an entry; otherwise nothing. |
| `legend` | no | A legend line. The detail lists each mark as `<color> = <rating>`, in order (`?` for no rating). | Nothing; it confirms the colors. |
| `legend-order` | no | A legend whose ratings differ from `legend_order`. The detail lists each mark's color and rating, then the rules' order. | Check `colors` against the file's own legend. |
| `override-applied` | no | An override changed this line. | Nothing; it records the change. |
| `progress-skipped` | no | A progress note a later rated entry finished; no row was written for it. | Nothing. |
| `progress-unmatched` | no | A progress note no later rated entry finishes; it is written unrated. | Rate it, or `skip` it. |
| `skipped` | no | A `skip` override removed this line. | Nothing. |

A progress note is finished by a later rated entry of the same medium and title
(compared ignoring case, punctuation and spacing). "Later" means a later date when both
are dated, a later line in the same file, or a later file name when either is undated.

## Outputs

| File | Holds |
| --- | --- |
| `<medium>-<year>.csv` | The rows of one medium dated in one year, such as `film-2023.csv`. |
| `<medium>-undated.csv` | The rows no rule could date. |
| `review.json` | `schema_version` (`1`), `counts` (every kind, zero included) and `items`, ordered by file, line and kind. |
| `review.md` | The same items as tables, one per kind, for reading. |
| `.retrolog_parse` | A marker that lets a later run reuse this directory. |

Each file is replaced atomically. A re-run removes every file named
`<medium>-*.csv`, for each medium the rules file's `[media]` table names (`book`,
`film`), that it did not write this time, so a CSV whose rows have moved does not
linger. A link of that name is removed, not followed. It never removes a folder, a
CSV named for anything else (`notes-2020.csv`, `keep-me.csv`) or any other file.

Each CSV is UTF-8 with a header row, rows sorted by date, then file, then line. Dates
are ISO (`2023-12-31`), booleans `true` or `false`, and an empty cell means no value.

| Column | Meaning |
| --- | --- |
| `medium` | `book` or `film`, from the file name. |
| `title` | The title as written, cleaned of codes, brackets, comments and a trailing `(YYYY)`. |
| `creator` | Always empty; the resolve step fills it. |
| `year` | The work's year, from a trailing `(YYYY)` in the title, else empty. |
| `date` | When the entry was logged, as dated above; empty when unplaced. |
| `date_is_approximate` | Whether `date` is an estimate. |
| `color` | The `RRGGBB` color that decided the rating, else empty. |
| `rating_raw` | The rules' rating key, such as `3`, or `2/3` for a split; empty when unrated. |
| `comment` | The text of a `(...)` after the rating code, else empty. |
| `rewatch` | Whether the entry was wrapped in `()` or `[]`, marking a rewatch or reread. |
| `section` | The section the entry sits under, lower-case, else empty. |
| `source_file` | The file, relative to `--path`, with `/` separators. |
| `source_line` | The line in that file, counted from 1. |

## Where the output may go

`--out` is checked before anything is read, by the same guard as the survey's: it is
refused inside `--path` (by path and by file identity), anywhere inside a git work
tree, when it is a file, and when it is a folder that holds files but not this tool's
`.retrolog_parse` marker. The temporary directory where `.doc` copies are converted is
refused too when it lies inside `--path` or `--out`.

## Limits

- **A title ending in a lone letter is read as rated.** The dash before a code is
  optional, so `Vitamin c`, with a letter `c` defined, is the title `Vitamin` rated by
  `c`. Write codes as `- x`, and add a `title` override for such a line.
- **A lower-case letter after a hyphen inside a word is read as a code.** `Spider-x`,
  with a letter `x` defined, is the title `Spider` rated by `x`. Write codes as `- x`
  with a space before the dash too (`Spider - x`), and add a `title` override for a
  title that holds such a hyphen.
- **Codes are lower-case.** An upper-case letter after a dash, such as `- C`, is not
  read as a code: it stays in the title and is listed as `unknown-code`.
- **Two-digit years lie in one century.** Every two-digit year in a folder or file
  name is read as `century + YY`, with one `century` for the whole tree, so a folder or
  file-name year range that crosses a century is not supported: all of one tree's
  years must lie in one century.
- **`.doc` files need a converter.** macOS's `textutil`, or LibreOffice; with neither,
  each `.doc` is a `read-error`.
- **A Word file's line numbers are paragraph indexes,** counted from 1 in document
  order, so a term block or override for a Word file counts paragraphs, not lines on
  the page. The `expect` text catches a numbering slip.

## Samples

`samples/` holds a synthetic tree builder, the example rules file that reads it, and
the output this tool writes for it (`samples/expected/`). `samples/README.md` gives the
commands to rebuild the tree, run the tool and compare.
