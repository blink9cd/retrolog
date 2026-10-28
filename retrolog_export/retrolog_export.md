# retrolog_export

Turn a resolved film list into Letterboxd import files. The tool reads the CSV files
`retrolog_resolve` wrote, the operator's export config, and a fresh export of the
operator's Letterboxd account, and writes numbered import files holding every film
the account does not hold yet, one row per logged watch, plus a review list of what
it held back, skipped, or sent with less than a full match.

It is the last of four steps: survey the folder (`retrolog_survey`), parse it
(`retrolog_parse`), resolve each title (`retrolog_resolve`), then export (this tool).
Nothing leaves the machine: the tool sends no request, and the operator uploads the
files on Letterboxd's import page.

**The output holds the operator's own titles.** The import files are the operator's
lists, ratings, and comments, and the review quotes their titles. Treat them as being
as private as the lists: do not post them or commit them to a repository. The tool
refuses to write inside a git work tree for that reason (see "Where the output may
go").

This product uses the TMDb API but is not endorsed or certified by TMDb. The film ids
and TMDb titles it reads come from [The Movie Database (TMDb)](https://www.themoviedb.org/),
through `retrolog_resolve`.

## Usage

```bash
python retrolog_export.py --service letterboxd --resolved path/to/resolved --account path/to/letterboxd-export.zip --config path/to/export.toml --out path/to/export
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--service` | yes | | The service to write import files for. Only `letterboxd` exists. |
| `--resolved` | yes | | retrolog_resolve's output folder (read only). See "The resolve folder". |
| `--account` | yes | | A fresh export of the account: its `.zip`, or the folder it unpacks to (read only). See "The account export". |
| `--config` | yes | | The export config file (see "The export config"). |
| `--out` | yes | | Output directory; refused inside `--resolved`, inside an `--account` folder, or inside a git work tree. |
| `--rows-per-file` | no | `1900` | The most data rows one import file holds, 1 to 1900. |
| `--max-bytes` | no | `1000000` | The most bytes one import file holds, header included, 1024 to 1000000. |
| `--timeout` | no | `15.0` | Seconds per git call of the `--out` check. |

Prerequisites: Python 3.12 or later, and nothing installed into it: the tool imports
the standard library only. It also imports modules from its sibling
`retrolog_resolve`, `retrolog_parse`, and `retrolog_survey` folders (none of the
survey's Word reader, and none of resolve's network code), and shared libraries it
finds through the project's one bootstrap, the `retrolog_bootstrap` folder beside its
own, so it runs from the tree it ships in. Its inputs are `retrolog_resolve`'s output folder, an
export config, and an account export downloaded after the last import.

Whenever it writes the output (exit `0` or `1`), the tool prints a summary to standard
output, counts only and no title: `films sent`, `rows sent`, `rows cut`, and `files`,
then `review <kind>: <n>` for each review kind that occurred, in the order of the
table under "Review kinds". With exit `1`, standard error adds
`<program>: <n> error item(s); see review.md`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Output written, and no review entry is an error. |
| `1` | Output written, but the review lists a held film (`held-ambiguous`, `held-failed`, `held-split`, or `held-no-title`). Every other film was written, so the import need not wait. Read `review.md`. |
| `2` | Bad arguments; `--resolved` missing, unreadable, holding no film CSV, or holding a CSV that is not resolve's (an older resolve's among them); the config unreadable, invalid, missing a rating key a row carries, or holding no `[letterboxd]` table; the account export unreadable, too large, holding none of the files it should, or holding a damaged one; a cutoff naming no row; a film too large for one file; `--out` refused; or the output could not be written. Apart from a failed write, nothing is written, and an earlier output is left as it was. Each message names the problem and the option, the config key, or the file inside the account export, never a path or a value. |

## Run order

1. Run `retrolog_resolve` and settle its `review.md`: an `ambiguous` or `failed` film
   is held back here.
2. On Letterboxd, download a fresh account export ("Export your data", in the
   account's settings).
3. Run this tool, and read `review.md`.
4. On Letterboxd's import page, upload `letterboxd-001.csv`, check the preview, and
   confirm; then `letterboxd-002.csv` and so on, **in number order**.
5. Before the next run, download a fresh account export again.

## The resolve folder

The tool reads every `film-<year>.csv` and `film-undated.csv` directly in
`--resolved`, in name order, and nothing else there: book CSVs, the review, and the
cache are never opened. Each must carry resolve's columns in resolve's order, which
end with `tmdb_id`, `tmdb_title`, and `tmdb_original_title`. A CSV written by an
older `retrolog_resolve`, before the two TMDb title columns, is refused with a hint
to rerun `retrolog_resolve`, which rewrites it.

Every cell the tool uses is checked, and a cell resolve would not write stops the run:
a year of more than four digits, a `source_line` of more than nine, a date outside the
file name's year, an unknown status, or a TMDb title on a row with no `tmdb_id`.

## The account export

Letterboxd has no import that skips what an account already holds, and a film already
there should not be sent again: an old rating would overwrite one given since. So
every run compares the resolved films with the account's own export, and a film
already on the account is not exported at all, but listed as `on-service`.

That comparison is only as current as the export it reads. **Download a fresh
account export before every run**, and in particular after every import: run again on
the export from before an import, and every film that import sent is sent a second
time. With a fresh export, a run after an import sends nothing already sent, so a
film held back earlier and settled since goes out alone.

`--account` names the `.zip` Letterboxd's "Export your data" gives, or the folder it
unpacks to. The tool reads only the top-level `watched.csv`, `diary.csv`,
`ratings.csv`, and `reviews.csv` that exist, never the `deleted` folder or any other
subfolder, whose films are no longer on the account. Each of those files is capped at
32 MiB, and an export holding none of the four is refused. A `.zip` is checked before
it is opened: one larger than 256 MiB, holding more than 10,000 files, or with a zip
directory larger than 5,120,000 bytes is refused, and so is a zip64 archive, with a
message to unpack it and pass the folder instead.

The export carries no TMDb id, so a film there is its `Name` and `Year`. A resolved
film matches when one of its titles and its year agree with an account entry. Its
titles are each row's list title and, for a film resolve settled from a candidate,
that candidate's TMDb title and original title (`tmdb_title` and
`tmdb_original_title`), since resolve can settle a film under a title other than the
list's. Titles are compared ignoring case, punctuation, and spacing, and each is also
compared folded: accents dropped and "&" read as "and", so `Placeholder Café` meets
`Placeholder Cafe` and `Placeholder & Film` meets `Placeholder and Film`. An account
entry with no year matches its title in any year, and a row with no year matches its
title in any year too; the `on-service` entry's detail says which of the three
matches it was.

An override row carries a `tmdb_id` but no TMDb title, since it names no candidate,
so the account is checked for it by its list title alone; it is sent and listed as
`override-unchecked` for a check by hand.

## The export config

The export config is TOML; start from `samples/export.example.toml`, and keep it
beside the operator's notes, never in a repository: a real one names rating codes and
a title from the operator's lists.

| Key | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | `1`, the only layout. |
| `letterboxd.watched_date` | yes | `true` writes each dated row's date as its `WatchedDate`; `false` leaves the column empty. |
| `letterboxd.tags` | yes | Tags added to every row, such as one marking the import; `[]` adds none. Each is 1 to 50 characters with no comma, quote, or control character, and must start with the tool's name, `retrolog`, so it cannot collide with a tag the account already uses and the import's tagged entries can be found from its tag pages. |
| `letterboxd.review_note` | yes | A note added to the `Review` column (see "The review note"), at most 500 characters; `""` adds none. |
| `letterboxd.review_note_rating` | no | A sentence added after the note on a rated row (see "The review note"), at most 500 characters; `""` or leaving it out adds none. |
| `letterboxd.review_note_rows` | yes | `commented` puts the note and its sentence on rows with a comment, `all` on every row. |
| `letterboxd.ratings` | yes | Each rating key a row can carry, mapped to stars from 0.5 to 5 in halves. A split code's key is both keys joined by `/`. |
| `letterboxd.rating_tags` | no | A tag added to the rows of a rating key, which must also be in `ratings`, such as `retrolog-high`; it must start with `retrolog`, like every tag. It keeps the lists' own verdict where the stars cannot: a split code that shares its stars with another key, or a film rerated later. |
| `letterboxd.rating_names` | no | The word `review_note_rating` prints for a rating key, 1 to 50 characters with no control character, each key also in `ratings`. A sentence naming `{rating}` needs a word for every key in `ratings`. |
| `letterboxd.cutoff` | no | The source row from which nothing is exported: `file`, `line`, and `title` (see "The cutoff"). |

An unknown key, a missing one, a value of the wrong shape, a tag not starting with
`retrolog`, or a row rated with a key `ratings` lacks stops the run before anything is
written, with a message naming the key and never its value. An unrated row needs no
key and is exported with no rating.

## The cutoff

When the account already holds every film from some point of the lists onward (the
operator has been logging live since), `letterboxd.cutoff` names the first source row
of that point: its `file` (as resolve's `source_file` column spells it), its `line`,
and the `title` on that line, compared ignoring case, spacing, and punctuation. Every
dated row at or after it, ordered by date, then file, then line, is left out and
counted as `rows cut`. An undated row is never cut, and is listed as `undated`.

A cutoff that names no dated row with its title stops the run with exit `2` and
nothing written, so a line number that slipped cannot silently cut the wrong rows.
Leave the table out to export every row.

## Which films are sent

The rows are grouped into films: a film is its TMDb id, else its title and year. Each
film's fate follows from its rows' resolve statuses, its titles, and the account, in
the order of this table:

| Resolve status | What is sent | Listed as |
| --- | --- | --- |
| `ambiguous`, `failed` | Nothing: the whole film is held back, an error, until resolve settles it. | `held-ambiguous`, `held-failed` |
| any, when no title of the film has a letter or digit | Nothing: no account entry could match it, so every run would send it again. An error. | `held-no-title` |
| any, when one of the film's titles is also a title of a film on the other side of a split: one sent by `tmdbID`, the other with none | Nothing from either film: each is held, an error, never merged, until a resolve override settles the title. | `held-split` |
| any other status, the film on the account under any of its rows' titles and years | Nothing. | `on-service` |
| `resolved`, `override` with an id | The film by `tmdbID`, `Title`, and `Year`. | nothing, unless unrated or undated; `override-unchecked` for a row with no TMDb title |
| `not-found`, `override` with `unresolvable` | The film by `Title`, and `Year` when known. Letterboxd's import preview matches it by title; check it there. | `by-title` |

A held film makes the exit code `1`, but every other film is still written. Once
resolve settles it, the next run, after a fresh account export, sends that film and
nothing else. A film that shares a held film's title and year (an override row beside
an ambiguous one, or a rewatch resolve settled in one year and not another) is held
with it, under the same kind and as an error, since sending it would put that title on
the account and the next run would then skip the held film as already there. A film
held this way passes the hold on in turn, to any film sharing one of its own rows'
titles and years. Here, as in the split, a title is any list or TMDb title of the
film, compared plain and folded as under "The account export".

## Columns

Every import file has these columns, in this order, in its header; a cell with no
value is empty.

| Column | Comes from |
| --- | --- |
| `tmdbID` | resolve's `tmdb_id`, for a `resolved` or `override` row; empty otherwise. |
| `Title` | parse's `title`. |
| `Year` | resolve's `year`, the film's release year. |
| `Directors` | resolve's `creator`, its names joined by a comma and a space. |
| `Rating` | `letterboxd.ratings` for the row's `rating_raw`, written like `3.5` or `5`; empty for an unrated row. |
| `WatchedDate` | The row's date, `2010-05-06`, when `letterboxd.watched_date` is `true`; empty otherwise and for an undated row. |
| `Rewatch` | `true` for a rewatch (an entry in parentheses or brackets in the source list); empty otherwise. |
| `Tags` | The rating key's tag from `letterboxd.rating_tags`, then `letterboxd.tags`, joined by a comma and a space, each tag once. |
| `Review` | The row's comment, with the review note and its rating sentence above it when there are any. |

A film logged more than once is one row per log, oldest first, all in one file.

No cell is rewritten. A `Title`, `Directors`, or `Review` cell that starts with a
character a spreadsheet reads as a formula is written as it is, since the importer
reads it as text, and its row is listed as `formula-cell`: inspect `review.md`, and
never open the import files in a spreadsheet before uploading them.

## The review note

`letterboxd.review_note` is an optional template, at most 500 characters, added to
the `Review` column, above the comment with a blank line between, on each commented
row (or every row, with `review_note_rows = "all"`). It may hold `{year}` and
`{date}`, the watch's year and ISO date; an undated row has neither, so a note naming
one is left off that row, and the rating sentence below, on a rated row, stands above
the comment alone. `{{` and `}}` are literal braces. It keeps a record of when the
film was first seen even when the import screen drops the dates, so the wording is
the operator's own, such as `Retrolog review. Original watch: {year}.`

`letterboxd.review_note_rating` is a second, optional template, also at most 500
characters, that follows the note on the same line, on a rated row only. Its one
field, `{rating}`, prints the word `letterboxd.rating_names` gives the row's rating
key, so a review shows at a glance what the lists said, which the stars cannot always
carry: a split code that shares its stars with another key, or a film rerated later.
An unrated row has no word, so it keeps the note and drops the sentence; an undated
rated row keeps the sentence and drops a note naming the date. With both set, a rated,
dated, commented row reads:

```text
Retrolog review. Original watch: 2009. Original rating: high.

The row's comment.
```

## Order and files

Films are ordered by their first row (undated rows first, then by date, file, and
line), and packed whole into `letterboxd-001.csv`, `letterboxd-002.csv`, and so on: a
new file starts when the next film would take the current one past `--rows-per-file`
data rows or `--max-bytes` bytes, header included. A film never spans two files, so
its logs import oldest first and its newest rating is the one the film keeps. The
defaults, 1900 rows and 1000000 bytes, sit at Letterboxd's limits of 1,900 titles and
1 MB a file. A single film too large for one file stops the run, naming it by
position.

## The import screen

Letterboxd's importer takes files of watched films (films, diary, and ratings): the
watchlist and lists have importers of their own. It is normally for paid (Pro)
members, though it was open to every account for a limited time when last checked.
It previews each file's matches before the import is confirmed, and an import cannot
be undone once confirmed, so read the preview, the `by-title` films above all.
Cleaning up an imported film by hand takes three steps: delete its diary entry or
review, un-mark Watched, and clear its rating. Deleting the entry or review alone
leaves the film watched and rated.

The screen has four switches, each dropping its own column from the import:

| Switch | With these files |
| --- | --- |
| Create diary entries from watched dates | On: each dated row becomes a diary entry on its `WatchedDate`, with its rating, so a film logged twice keeps both, and the film's current rating is its last row's, which is why a film's rows go oldest first. Off: no diary entry is made; every row arrives as its own undated review, with its rating, tag, and note, so a film logged twice makes two reviews, and every date and the `Rewatch` flag are lost. |
| Import reviews | The `Review` column. A review on a row with no `WatchedDate` becomes an undated, public review. |
| Import ratings | The `Rating` column. |
| Import tags | The `Tags` column. A tag lands only on a row that becomes a diary entry or a review, so an undated row with no `Review` text keeps no tag, and the tag pages do not list it. The `Rewatch` flag needs a `WatchedDate`. |

Re-importing a row whose `WatchedDate` matches an existing diary entry updates that
entry in place rather than adding a second one.

**The import tops the feed either way.** The diary's public feed (its RSS) and the
profile's recent activity are ordered by when an entry was logged, not by the watch
date, and the RSS holds a fixed number of the latest entries; the diary page itself
sorts by watch date. With diary entries on, every imported watch takes the top of that
feed, above real recent ones, and a bulk import pushes every recent entry out of it.
With diary entries off, each row arrives as a new review instead, which takes the top
of the RSS and the recent activity just the same, and loses the dates and rewatches
besides; the review note then keeps the watch year, but only on the rows it is added
to: the commented rows, or every row with `review_note_rows = "all"`. An operator who
shows recent watches from that feed somewhere else should plan to re-log their latest
real entries after the import. Whether followers' activity feeds show imported entries
has not been observed.

## Review kinds

`review.md` opens with a count line, what was sent and cut and how many review items
and errors there are, then has one table per kind that occurred, in the order of the
table below, so errors come first; each heading is the kind and its count, with
`, error` added for an error kind. An entry covers every row of one film (a
`formula-cell` entry, only the rows with such a cell): it names the title, each row
as `<source_file>:<source_line>`, and a short detail. `review.json` holds the same
entries, ordered by title and kind.

| Kind | Error | Meaning | What the operator does |
| --- | --- | --- | --- |
| `held-ambiguous` | yes | resolve found more than one match; nothing was sent. | Settle it with a resolve override, rerun resolve, then this tool. |
| `held-failed` | yes | resolve could not look it up; nothing was sent. | Rerun resolve, then this tool. |
| `held-split` | yes | One title is split between a film sent by `tmdbID` and a film with none, comparing list and TMDb titles, plain and folded; both halves are held, never merged, each with a detail saying which half it is. | Settle the title with a resolve override, rerun resolve, then this tool. |
| `held-no-title` | yes | No title of the film has a letter or digit, so the account cannot be checked for it; nothing was sent. | Add it on Letterboxd by hand. |
| `on-service` | no | The account export already holds the film, so it was not sent. The detail gives one of three answers: an exact match; an account entry with no year, matched by title alone; or a row with no year, matched by title alone. | Nothing; check the match when it was by title alone. |
| `override-unchecked` | no | Sent by `tmdbID` from a row with no TMDb title to check the account by: an override's row (any other such row, which only a hand-edited CSV holds, has a detail of its own). | Confirm by hand that the film is not already on the account before importing. |
| `by-title` | no | Sent with no `tmdbID`, by title (and year when known). | Check the match in the import preview. |
| `unrated` | no | Sent with no rating. | Rate it by hand on Letterboxd. |
| `undated` | no | Sent with no watched date, so it makes no diary entry. | Nothing, or log the date by hand. |
| `formula-cell` | no | A sent row's `Title`, `Directors`, or `Review` starts with `=`, `+`, `-`, `@`, a tab, or a carriage return, which a spreadsheet reads as a formula; the cell is written unchanged, and the detail names the columns. | Inspect `review.md`, and never open the import CSVs in a spreadsheet: upload them as written. |

## Outputs

| File | Holds |
| --- | --- |
| `letterboxd-001.csv` onward | The import files, to upload in number order. None when no film is to be sent. |
| `review.json` | `schema_version` (`1`), `counts` (every review kind, zero included, and `films-sent`, `rows-sent`, `rows-cut`, and `files`) and `entries`. |
| `review.md` | The same entries as tables, one per kind, for reading. |
| `.retrolog_export` | A marker that lets a later run reuse this directory. |

Each file is UTF-8 with Unix line ends and is replaced atomically. A run that gets as
far as writing first removes the earlier run's `review.json`, `review.md`, and every
`letterboxd-*.csv`, then writes the import files, then the review last. So a file from
an earlier, larger run is never uploaded by mistake, and a folder holding import files
but no review is a run that did not finish: run it again before uploading anything. A
link of one of those names is removed, not followed, and no folder or other file is
touched. Every row is named relative to the folder parse read, never by an absolute
path.

## Where the output may go

`--out` is checked before anything is written, and created with its
`.retrolog_export` marker only once the files to write are known: it is refused inside
`--resolved` or an `--account` folder (by path and by file identity), anywhere inside
a git work tree, when it is a file, and when it is a folder that holds files but not
this tool's marker. `--resolved` and `--account` are only read.

## Limits

- **The diff is by title and year.** Letterboxd's export carries no TMDb id, so a
  film the account holds under a title that is neither the list's nor one of TMDb's
  two (a translated one, say) is not found there and is sent again; the import
  preview is the place to catch it.
- **A by-title film is Letterboxd's match, not this tool's.** Read the preview.
- **One service.** `--service` takes `letterboxd` only, and reads only the
  `film-*.csv` files; book CSVs in the same folder are never opened.

## Samples

`samples/` holds a builder for a synthetic resolve folder, account export, and config,
and the output this tool writes for them with `--rows-per-file 4`
(`samples/expected/`), which shows a film's two rows kept together, a split into two
files, and every review kind. `samples/README.md` gives the commands to build the
inputs, run the tool, and compare.
