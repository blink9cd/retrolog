# retrolog_export

Turn a resolved film list into Letterboxd import files, or a resolved book list into a
Goodreads import file. For films, the tool reads the CSV files
`retrolog_resolve` wrote, the operator's export config, and a fresh export of the
operator's Letterboxd account, and writes numbered import files holding every film
an earlier import has not sent, one row per logged watch, plus a review list of what
it held back, skipped, or sent with less than a full match. A film the account's owner
already logged there by hand is sent without stars, so the rating given there stands.
For books, `--service goodreads` does the same with the book CSVs and the account's
Goodreads library export: every book once, leaving out a book the account has read
or rated and holding back a book whose reads resolve keyed apart (see "Goodreads"). Every section before "Goodreads" describes the Letterboxd
run; the Goodreads section says what a book run shares with it and what differs.
This doc calls whoever runs the tools the operator, and the account holder the owner;
usually they are the same person.

It runs after three steps: survey the folder (`retrolog_survey`), parse it
(`retrolog_parse`), and resolve each title (`retrolog_resolve`), whose review list
`retrolog_review` helps settle. Two optional tools follow it for films only:
`retrolog_probe` finds the films Letterboxd lacks in the import files, and
`retrolog_restore` rebuilds diary entries deleted before an import.
Nothing leaves the machine: the tool sends no request, and the operator uploads the
files on the service's import page.

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
python retrolog_export.py --service letterboxd --resolved path/to/resolved --account path/to/letterboxd-export.zip --config path/to/export.toml --out path/to/letterboxd
python retrolog_export.py --service goodreads --resolved path/to/resolved --account path/to/goodreads_library_export.csv --config path/to/export.toml --out path/to/goodreads
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--service` | yes | | The service to write import files for: `letterboxd` (films) or `goodreads` (books). |
| `--resolved` | yes | | retrolog_resolve's output folder (read only). See "The resolve folder". |
| `--account` | yes | | A fresh export of the account (read only): Letterboxd's `.zip`, or the folder it unpacks to (see "The account export"); Goodreads' `goodreads_library_export.csv` (see "The library export"). |
| `--config` | yes | | The export config file (see "The export config"). |
| `--out` | yes | | Output directory; refused inside `--resolved`, inside an `--account` folder, or inside a git work tree, and refused when it holds another service's import files. Give each service its own: a run replaces only its own service's files and the one review, so a shared folder would lose the other run's review and keep its files. |
| `--rows-per-file` | no | the service's | The most data rows one import file holds, from 1 to the service's default: `1900` for `letterboxd`, `10000` for `goodreads`. |
| `--max-bytes` | no | the service's | The most bytes one import file holds, header included, from 1024 to the service's default: `1000000` for `letterboxd`, `10000000` for `goodreads`. |
| `--timeout` | no | `15.0` | Seconds per git call of the `--out` check: greater than 0 and at most 3600. |
| `--dry-run` | no | off | Check everything a run checks, `--out` included, and print the summary a run would, but write nothing, not even `--out` (see "The dry run"). |
| `--test-film` | no | off | Letterboxd only: write only `letterboxd-test.csv`, every row of one `logged-no-stars` film, to import before the rest (see "The test run"). Not with `--dry-run`. |

Prerequisites: Python 3.12 or later, and nothing installed into it: the tool imports
the standard library only. It also imports modules from its sibling
`retrolog_resolve`, `retrolog_parse`, and `retrolog_survey` folders (none of the
survey's Word reader, and none of resolve's network code), and shared libraries it
finds through the project's one bootstrap, the `retrolog_bootstrap` folder beside its
own, so it runs from the tree it ships in.

Whenever it writes the output (exit `0` or `1`), and on a dry run, the tool prints a
summary to standard output, counts only and no title: `films sent` (`books sent` for
Goodreads), `rows sent`, `rows cut`, and `files`, then `review <kind>: <n>` for each
review kind that occurred,
in the order of the table under "Review kinds". With exit `1`, standard error adds
`<program>: <n> error item(s); see review.md`, or, on a dry run, which writes no
`review.md`, `<program>: <n> error item(s); rerun without --dry-run to write review.md`.
A test run prints `test film rows: <n>` and `file: letterboxd-test.csv` instead.

### The dry run

`--dry-run` reads and checks the same inputs a run does, in the same order, and stops
on the same refusals with exit `2`: the arguments, the config (an `omit` entry naming
no row among them), the resolve folder, the account export, `--out`, the cutoff, and a
film too large for one file. It then prints the summary and exits `0` or `1` as a run
would, but writes nothing: `--out` is checked and never created, and an earlier
output in it is left as it was. Use it to check a new or edited config, and to see the
counts, before writing anything.

### The test run

A `logged-no-stars` film is sent without stars so the account keeps its rating. To
see that the import does keep it before sending every such film, `--test-film` writes
`letterboxd-test.csv` holding every row of one of them, cells exactly as a run writes
them, and nothing else: no numbered file and no review. Every row goes, because the
import tags the film, and the next run sends no row of a tagged film.

- **Which film:** the first that `review.md`'s `logged-no-stars` table lists, by the
  review's own order (title, then source rows), of those the import will tag, so it
  is easy to find there, and the same inputs always choose the same film. A tag
  lands only on a row that becomes a diary entry or a review (see "Limits"), so a
  film none of whose rows is sent with a `WatchedDate` or review text is passed
  over: it would keep no tag, read on the next run as logged, and be sent again
  without stars. Any one tagged row is enough, since the next run skips a tagged
  film whole. When no such film has a tagged row (every row undated, or
  `watched_date = false`, and no review text), the table's first film is chosen all
  the same, and the next run sends it again.
- **No such film:** with no `logged-no-stars` film to test, the run stops with exit
  `2`, writing and removing nothing.
- **`--out`:** checked as for a run; the earlier run's numbered files and review are
  removed first, so the folder never holds the test file beside import files it
  should not be uploaded with. A later run's cleanup removes `letterboxd-test.csv` in
  turn.
- **Formula cells:** with no review to list them, a test-film row whose cell a
  spreadsheet would read as a formula adds `review formula-cell: <n>` to the
  printed lines; never open the test file in a spreadsheet either.
- **Exit code:** `0` once written. A held film does not stop the test file, since it
  is not in it, but settle every held film before importing anything all the same
  ("Which films are sent").

Then import the test file (all four switches on, as for a run), check the film's
rating on Letterboxd, download a fresh account export, and run normally into a new
`--out`: the test film is now tagged, so it reads as `on-service` and is not sent
again, unless it was chosen with no row the import could tag ("Which film" above).

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Output written (a dry run: checked), and no review entry is an error; a test run: its file written. |
| `1` | Output written (a dry run: checked), but the review lists a held film or book (`held-ambiguous`, `held-failed`, `held-split`, or `held-no-title`). Every other item was written, but settle each held one before importing any file (see "Which films are sent", and for books "Goodreads books and their rows"). Read `review.md`. |
| `2` | Bad arguments, a `--rows-per-file` or `--max-bytes` above the service's default, or `--test-film` with `--service goodreads`; `--resolved` missing, unreadable, holding no CSV of the service's medium, or holding a CSV that is not resolve's (an older resolve's among them); the config unreadable, invalid, missing a rating key a row carries, holding an `omit` entry that names no row, holding no table for `--service` (`[letterboxd]` or `[goodreads]`), or, for Goodreads, naming no `undated_date_added` while a row is undated; the account export unreadable, too large, holding none of the files it should, or holding a damaged one (for Goodreads: a folder or a zip, a missing required column, or no book); a cutoff naming no row; an item too large for one file; `--test-film` with no `logged-no-stars` film; `--test-film` with `--dry-run`; `--out` refused, or holding another service's import files; or the output could not be written. Apart from a failed write, nothing is written, and an earlier output is left as it was. Each message names the problem and the option, the config key, or the file inside the account export, never a path or a value. |

## Run order

1. Run `retrolog_resolve` and settle its `review.md`: an `ambiguous` or `failed` film
   is held back here.
2. On Letterboxd, download a fresh account export ("Export your data", in the
   account's settings).
3. Run this tool, with `--dry-run` first after any config change, and read
   `review.md`. When it lists a `logged-no-stars` film, import a `--test-film` file
   first, then download a fresh export and run again (see "The test run").
4. On Letterboxd's import page, upload `letterboxd-001.csv`, check the preview, and
   confirm; then `letterboxd-002.csv` and so on, **in number order**.
5. Before the next run, download a fresh account export again.

## The resolve folder

The tool reads every `film-<year>.csv` and `film-undated.csv` directly in
`--resolved`, the names parse writes (`parse_output.CSV_NAME`, a year of any number of
digits), in year order with the undated file last, and nothing else there: book CSVs,
the review, and the cache are never opened. Each must carry resolve's columns in
resolve's order, which end with `tmdb_id`, `tmdb_title`, and `tmdb_original_title`. A
CSV written by an older `retrolog_resolve`, before the two TMDb title columns, is
refused with a hint to rerun `retrolog_resolve`, which rewrites it.

Every cell the tool uses is checked, and a cell resolve would not write stops the run:
a year of more than four digits, a `source_line` of more than nine, a date outside the
file name's year, an unknown status, or a TMDb title on a row with no `tmdb_id`.

## The account export

Letterboxd has no import that skips what an account already holds, and an old rating
sent for a film already there would overwrite one given since. So every run compares
the resolved films with the account's own export, and what a film's rows become
depends on which of its files list the film:

| The account's files list the film | What is sent | Listed as |
| --- | --- | --- |
| In none of them | Every row, as it is. | nothing for the account |
| In `watched.csv` alone: marked watched, maybe liked, never rated, logged, or reviewed | Every row, with its stars. | `watched-only` |
| In `diary.csv`, `ratings.csv`, or `reviews.csv`, and no matching `diary.csv` or `reviews.csv` row carries a tag starting `retrolog`: logged by the owner | Every row with its `Rating` empty, and nothing else changed: its date, rewatch flag, tags (the rating tag among them), and review note with its rating sentence all stay, so the lists' verdict still shows. A row on the `Watched Date` of a matching `diary.csv` entry is not sent at all; when that is every row, the film is not sent. An earlier import's entry a year apart that the tagged-date rule cannot see (dates off, or undated rows) lands here too: the logged check matches it, so the film is sent again without stars. | `logged-no-stars`; `same-date` for each row not sent |
| A matching `diary.csv` or `reviews.csv` row carries a tag starting `retrolog`, or a `diary.csv` entry of one of the film's titles, in any year, carries one on the `Watched Date` of one of its rows: an earlier import of this tool's | Nothing. | `on-service` |

A tag counts when, stripped of spaces, it starts with `retrolog` in any case: every
tag this tool writes does (see "The export config"), so the tag marks a film the
import sent. The same-date rule is there because Letterboxd's importer updates a diary
entry with the same film and date in place rather than adding a second one, so such a
row would overwrite the owner's own entry; it applies only with
`letterboxd.watched_date = true`, since a row with no `WatchedDate` meets no entry.
`watched.csv` has no dates, so a film watched only loses no row.

Letterboxd dates a film by its first release, and TMDb can give a later year, so an
exact title and year can miss a film the account holds. Two rules cover it. A
tagged `diary.csv` entry of one of the film's titles on the date of one of its rows
is the import's own entry for that film, whatever year either side gives it, so the
film is skipped as imported; like the same-date rule, this applies only with
`letterboxd.watched_date = true`, since otherwise an imported entry's date is the
import's day, not the row's. And the logged, watched-only, and same-date checks accept
an entry of the film's title whose year is one either side of a row's: a logged film
missed by a year would be sent with stars and overwrite the rating on the account,
while a false match only sends it without stars. A logged match one year apart still
outranks an exact `watched.csv` one. The imported check itself, and the held films
(see "Which films are sent"), keep the exact title and year, with no year either side;
a held film's title and date, with `watched_date` on, also hold any film that shares
them, by the tagged-date rule above.

That comparison is only as current as the export it reads. **Download a fresh
account export before every run**, and in particular after every import: run again on
the export from before an import, and every film that import sent is sent a second
time. With a fresh export, a run after an import sends nothing already sent, as long
as each sent film arrived tagged (see "Limits"), so a film held back earlier and
settled since goes out alone.

`--account` names the `.zip` Letterboxd's "Export your data" gives, or the folder it
unpacks to. The tool reads only the top-level `watched.csv`, `diary.csv`,
`ratings.csv`, and `reviews.csv` that exist, never the `deleted` folder or any other
subfolder, whose films are no longer on the account. It reads each file's `Name` and
`Year`, and the `Tags` and `Watched Date` of `diary.csv` and `reviews.csv`; a file
without either of those two columns reads as having no tags or no dates, and a cell
that is not a `2010-05-06` date reads as no date. Each of those files is capped at
32 MiB, and one cell, such as a long review, may be as long as its whole file; an
export holding none of the four is refused. A `.zip` is checked before
it is opened: one larger than 256 MiB, holding more than 10,000 files, or with a zip
directory larger than 5,120,000 bytes is refused, and so is a zip64 archive, with a
message to unpack it and pass the folder instead.

The export carries no TMDb id, so a film there is its `Name` and `Year`. A resolved
film matches when one of its titles and its year agree with an account entry. Its
titles are each row's list title and the TMDb title and original title resolve
wrote (`tmdb_title` and `tmdb_original_title`): for a film resolve settled from a
candidate, that candidate's, and for a film an override settled by its `tmdb_id`,
those TMDb's details for that id give. Resolve can settle a film under a title other
than the list's, and an override names only an id. Titles are compared ignoring
case, punctuation, and spacing, which drops a hyphen. Each is also compared folded,
with accents dropped and "&" read as "and", and that folded title is compared once
more with each hyphen (`-`, or Unicode's hyphen or non-breaking hyphen) read as a
space. Each fold adds a key and drops none, so `Placeholder Café` meets `Placeholder
Cafe`, `Placeholder & Film` meets `Placeholder and Film`, and `Placeholder Café-Bar`
meets both `Placeholder Cafebar` and `Placeholder Cafe Bar`. An account entry with no
year matches its title in any year, and a row with no year matches its title in any
year too; the `on-service` entry's detail says which of the three matches it was. The
same match decides every account class and which `diary.csv` entries share a row's
date. The year either side widens only the logged, the watched-only, and the
same-date checks; the imported check is widened only by the tagged date, as set out
above.

An override row carries no TMDb title only when resolve could not read TMDb's
details for its `tmdb_id` (a failed request, or one not in the cache under
`--cache-only`). The account is then checked for it by its list title alone, and in
any year when resolve could not fill its year either; it is sent and listed as
`override-unchecked` for a check by hand. Rerunning resolve asks for the details
again.

## The export config

The export config is TOML; start from `samples/export.example.toml`, and keep it
beside the operator's notes, never in a repository: a real one names rating codes and
a title from the operator's lists.

| Key | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | `1`, the only layout. |
| `letterboxd.watched_date` | yes | `true` writes each dated row's date as its `WatchedDate`; `false` leaves the column empty. |
| `letterboxd.tags` | yes | Tags added to every row, at least one: the next run knows a film an import sent by its tag, so `[]` stops the run. Each is 1 to 50 characters with no comma, quote, or control character, and must start with the tool's name, `retrolog`, so it cannot collide with a tag the account already uses and the import's tagged entries can be found from its tag pages. |
| `letterboxd.review_note` | yes | A note added to the `Review` column (see "The review note"), at most 500 characters; `""` adds none. |
| `letterboxd.review_note_rating` | no | A sentence added after the note on a rated row (see "The review note"), at most 500 characters; `""` or leaving it out adds none. |
| `letterboxd.review_note_rows` | yes | `commented` puts the note and its sentence on rows with a comment, `all` on every row. |
| `letterboxd.ratings` | yes | Each rating key a row can carry, mapped to stars from 0.5 to 5 in halves. A split code's key is both keys joined by `/`. |
| `letterboxd.rating_tags` | no | A tag added to the rows of a rating key, which must also be in `ratings`, such as `retrolog-high`; it must start with `retrolog`, like every tag. It keeps the lists' own verdict where the stars cannot: a split code that shares its stars with another key, or a film rerated later. |
| `letterboxd.rating_names` | no | The word `review_note_rating` prints for a rating key, 1 to 50 characters with no control character, each key also in `ratings`. A sentence naming `{rating}` needs a word for every key in `ratings`. |
| `letterboxd.cutoff` | no | The source row from which nothing is exported: `file`, `line`, and `title` (see "The cutoff"). |
| `letterboxd.omit` | no | Films never sent, one `[[letterboxd.omit]]` table each: a `title`, compared ignoring case, spacing, and punctuation, and a `year`, left out for a film with no year (see "The omit list"). |

An unknown key, a missing one, a value of the wrong shape, an empty `tags` list, a tag
not starting with `retrolog`, a row rated with a key `ratings` lacks, or an `omit`
entry with no letter or digit in its title, naming the same film as an earlier one, or
naming no row stops the run before anything is written, with a message naming the key
and never its value; an `omit` entry is named by its number in the file,
`letterboxd.omit[1]` onward. An unrated row needs no key and is exported with no
rating.

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

## The omit list

`letterboxd.omit` names films never to send: a by-title film the import preview
showed Letterboxd lacks, say, or a film whose TMDb id Letterboxd has no page for. Each
entry is a title and a year, and names a film when one of its rows has that title
(compared as the cutoff's is) on its list line and that year in resolve's `year`
column; an entry with no `year` names only a film with no year. Every row of a film an
entry names is left out, before any hold, and the film is listed `omitted`, so the
final check and every later round stop reporting it. A film left out holds nothing
back: it can neither hide a held film nor split a title.

An entry is checked against every row read, the cut ones included, so moving the
cutoff never makes one stale; an entry that names no row (a typo, or a film gone from
the lists) stops the run with exit `2`, since it would otherwise leave out nothing
while the operator believes it does. Remove the entry to have the film considered
again: the next run sends it, holds it, or skips it as for any other film.

## Which films are sent

The rows are grouped into films: a film is its TMDb id, else its title and year. Each
film's fate follows from its rows' resolve statuses, its titles, and the account, in
the order of this table:

| Resolve status | What is sent | Listed as |
| --- | --- | --- |
| any, when a `letterboxd.omit` entry names one of the film's rows | Nothing (see "The omit list"). | `omitted` |
| `ambiguous`, `failed` | Nothing: the whole film is held back, an error, until resolve settles it. | `held-ambiguous`, `held-failed` |
| any, when no title of the film has a letter or digit | Nothing: no account entry could match it, so every run would send it again. An error. | `held-no-title` |
| any, when one of the film's titles is also a title of a film on the other side of a split: one sent by `tmdbID`, the other with none | Nothing from either film: each is held, an error, never merged, until a resolve override settles the title. | `held-split` |
| any other status, an earlier import of the film on the account, under any of its rows' titles and years, or a tagged diary entry of one of its titles on one of its rows' dates | Nothing. | `on-service` |
| `resolved`, `override` with an id | The film by `tmdbID`, `Title`, and `Year`. | nothing, unless unrated or undated; `override-unchecked` for a row with no TMDb title |
| `not-found`, `override` with `unresolvable` | The film by `Title`, and `Year` when known. Letterboxd's import preview matches it by title; check it there. | `by-title` |

A film sent is then sent as its account class sets out (see "The account export"):
in full, in full and listed `watched-only`, or without stars and listed
`logged-no-stars`, less any `same-date` row.

A held film makes the exit code `1`, but every other film is still written. Even so,
**settle every held film before importing any file**, except a `held-no-title` one,
which is added by hand: a held film that resolve later settles to the TMDb id of a film
already sent under another title would read as already imported on the next run, and
never be sent. Once resolve settles it, the next run, after a fresh account export,
sends that film and nothing already imported. A film that shares a held film's title
and year (an override row beside an ambiguous one, or a rewatch resolve settled in one
year and not another) is held with it, under the same kind and as an error, since
sending it would put that title on the account and the next run would then skip the
held film as already imported. With `watched_date` on, a film that shares a held
film's title and one of its rows' dates, in any year, is held with it the same way:
its tagged diary entry on that date would meet the next run's tagged-date rule (see
"The account export"), which would skip the held film whatever its year. A film held
this way passes the hold on in turn, to any film sharing one of its own rows' titles
and years, or, with `watched_date` on, a title and a row date. Here, as in the split,
a title is any list or TMDb title of the film, compared plain and folded as under "The
account export".

Sharing a hold on a title and date, not only on an exact title and year, is
deliberate: the tagged-date rule matches a date in any year, so an exact title and
year alone would let the next run skip the held film.

## Columns

Every import file has these columns, in this order, in its header; a cell with no
value is empty.

| Column | Comes from |
| --- | --- |
| `tmdbID` | resolve's `tmdb_id`, for a `resolved` or `override` row; empty otherwise. |
| `Title` | parse's `title`. |
| `Year` | resolve's `year`, the film's release year. |
| `Directors` | resolve's `creator`, its names joined by a comma and a space. |
| `Rating` | `letterboxd.ratings` for the row's `rating_raw`, written like `3.5` or `5`; empty for an unrated row, and for every row of a film the owner logged on the account (`logged-no-stars`), so the rating given there stands. |
| `WatchedDate` | The row's date, `2010-05-06`, when `letterboxd.watched_date` is `true`; empty otherwise and for an undated row. |
| `Rewatch` | `true` for a rewatch (an entry in parentheses or brackets in the source list); empty otherwise. |
| `Tags` | The rating key's tag from `letterboxd.rating_tags`, then `letterboxd.tags`, joined by a comma and a space, each tag once. |
| `Review` | The row's comment, with the review note and its rating sentence above it when there are any. |

A film logged more than once is one row per log, oldest first, all in one file, less
any `same-date` row.

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

Films are ordered by their first row sent (undated rows first, then by date, file,
and line), and packed whole into `letterboxd-001.csv`, `letterboxd-002.csv`, and so
on: a new file starts when the next film would take the current one past
`--rows-per-file` data rows or `--max-bytes` bytes, header included. A film never spans two files, so
its logs import oldest first and its newest rating is the one the film keeps. The
defaults, 1900 rows and 1000000 bytes, sit at Letterboxd's limits of 1,900 titles and
1 MB a file. A single film too large for one file stops the run, naming it by
position.

## The import screen

Letterboxd's importer takes files of watched films (films, diary, and ratings): the
watchlist and lists have importers of their own. It is normally for paid (Pro)
members.
It previews each file's matches before the import is confirmed, and an import cannot
be undone once confirmed, so read the preview, the `by-title` films above all.
Cleaning up an imported film by hand takes three steps: delete its diary entry or
review, unmark Watched, and clear its rating. Deleting the entry or review alone
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
| `held-ambiguous` | yes | resolve did not settle on one match; nothing was sent. | Settle it with a resolve override, rerun resolve, then this tool. |
| `held-failed` | yes | resolve could not look it up; nothing was sent. | Rerun resolve, then this tool. |
| `held-split` | yes | One title is split between a film sent by `tmdbID` and a film with none, comparing list and TMDb titles, plain and folded; both halves are held, never merged, each with a detail saying which half it is. | Settle the title with a resolve override, rerun resolve, then this tool. |
| `held-no-title` | yes | No title of the film has a letter or digit, so the account cannot be checked for it; nothing was sent. | Add it on Letterboxd by hand. |
| `omitted` | no | A `letterboxd.omit` entry names the film, so it was not sent (see "The omit list"). | Nothing; remove the entry to have it considered again. |
| `on-service` | no | An earlier import sent the film: a matching `diary.csv` or `reviews.csv` row carries a `retrolog` tag, so it was not sent. The detail gives one of four answers: an exact match; an account entry with no year, matched by title alone; a row with no year, matched by title alone; or a `retrolog` diary entry of its title on a row's date, in any year, which the detail names. | Nothing; check the match when it was by title alone. |
| `same-date` | no | The owner logged the film, and a matching `diary.csv` entry has this row's date: Letterboxd would update that entry rather than add one, so the row was not sent. Any other rows of the film are sent, listed `logged-no-stars`. The detail says so when the entry's year was one apart from the row's. | Nothing; the diary entry already records that watch. |
| `logged-no-stars` | no | The owner logged the film (a diary entry, a rating, or a review, none tagged `retrolog`), so it was sent with an empty `Rating` to keep the rating on the account; its tags and review note still carry the lists' rating. The detail says so when the account's year was one apart from the row's. | Nothing; rerate it by hand if the lists' rating should win. |
| `watched-only` | no | The account holds the film in `watched.csv` alone, with no rating or diary entry, so it was sent in full, stars included. The detail says so when the account's year was one apart from the row's. | Nothing. |
| `override-unchecked` | no | Sent by `tmdbID` from a row with no TMDb title to check the account by: an override's row whose details resolve could not read (any other such row, which only a hand-edited CSV holds, has a detail of its own). | Rerun resolve to ask for the details again, or confirm by hand that the film is not already on the account before importing. |
| `by-title` | no | Sent with no `tmdbID`, by title (and year when known). | Check the match in the import preview. |
| `unrated` | no | Sent with no rating. | Rate it by hand on Letterboxd. |
| `undated` | no | Sent with no watched date, so it makes no diary entry. | Nothing, or log the date by hand. |
| `formula-cell` | no | A sent row's `Title`, `Directors`, or `Review` starts with `=`, `+`, `-`, `@`, a tab, or a carriage return, which a spreadsheet reads as a formula; the cell is written unchanged, and the detail names the columns. | Inspect `review.md`, and never open the import CSVs in a spreadsheet: upload them as written. |

## Outputs

| File | Holds |
| --- | --- |
| `letterboxd-001.csv` onward | The import files, to upload in number order. None when no film is to be sent. |
| `goodreads-001.csv` onward | A Goodreads run's import files: one, by default (see "Goodreads"). None when no book is to be sent. |
| `letterboxd-test.csv` | A test run's only file, one `logged-no-stars` film (see "The test run"); never beside the files above. |
| `review.json` | `schema_version` (`1`), `counts` (every review kind the service's medium can earn, zero included, and `films-sent` or `books-sent`, `rows-sent`, `rows-cut`, and `files`) and `entries`. |
| `review.md` | The same entries as tables, one per kind, for reading. |
| `.retrolog_export` | A marker that lets a later run reuse this directory. |

Each file is UTF-8 with Unix line ends and is replaced atomically. A run that gets as
far as writing first removes the earlier run's `review.json`, `review.md`, and every
import file of its service (`letterboxd-*.csv`, the test file included, or
`goodreads-*.csv`), then writes the import files, then the
review last. So a file from an earlier, larger run is never uploaded by mistake, and a
folder holding numbered import files but no review is a run that did not finish: run
it again before uploading anything. A test run removes the same files, then writes its
one file and no review. A link of one of those names is removed, not followed, and no
folder or other file is touched. Every row is named relative to the folder parse read, never by an absolute
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
  preview is the place to catch it. A year more than one apart is found only through
  the tagged date of an earlier import, so a film the owner logged two years from
  TMDb's year is still sent with stars. And an earlier import's entry a year apart
  that the tagged date cannot reach, because `letterboxd.watched_date` is off or the
  rows are undated, meets the logged check instead: the film is sent again, without
  stars, as `logged-no-stars`.
- **An import is known by its tag.** A film counts as imported only when a matching
  `diary.csv` or `reviews.csv` row carries a `retrolog` tag. The config guarantees a
  tag on every row, since `letterboxd.tags` may not be empty, but the tag lands only
  with the import screen's "Import tags" switch on, and only on a row that becomes a
  diary entry or a review: an undated row with no `Review` text keeps no tag. Without
  it, the next run finds the film only watched or logged, and sends it again: in full
  when watched only, without stars when rated.
- **A by-title film is Letterboxd's match, not this tool's.** Read the preview.
- **One medium a service.** `--service letterboxd` reads only the `film-*.csv` files
  and `--service goodreads` only the `book-*.csv` files; the other medium's files in
  the same folder are never opened. Goodreads' own limits are under "The Goodreads
  import".

## Goodreads

`--service goodreads` writes the books resolve settled into `goodreads-001.csv`, the
file Goodreads' importer takes, plus the same `review.json` and `review.md`. It reads
every `book-<year>.csv` and `book-undated.csv` in `--resolved`, as a film run reads
the film CSVs, and the `[goodreads]` table of the same export config. These work as
they do for films: the checks of every cell, the cutoff, the omit list (naming a book
by its title and resolve's `year`), the holds of an `ambiguous` or `failed` row and
of a title with no letter or digit, the formula-cell check, the dry run, `--out`, and
the output files. What differs is below. `--test-film` is Letterboxd's alone and is
refused for books.

### Goodreads run order

1. Run `retrolog_resolve` on the book CSVs, with an `--out` of its own, since each
   resolve run rewrites its folder's `review.md` with that run's medium only:

   ```bash
   python retrolog_resolve.py --parsed path/to/parsed --out path/to/resolved-books --medium book --overrides path/to/overrides.toml
   ```

   Settle its `review.md`. Resolve looks up each override book's work and authors and
   picks each work's ISBN-13 from its edition list, so rerun it after adding an
   override or updating the tools.
2. Before the first run, write the export config's `[goodreads]` table (see "The
   `[goodreads]` table" below).
3. On Goodreads, export the library (My Books, "Import and export", "Export
   Library") and download `goodreads_library_export.csv`. Do this before **every**
   run: the file is what keeps a book the account already has from being sent again.
4. Run this tool with `--dry-run` first, then for real, and read `review.md`. Settle
   each held book it lists (see "Goodreads books and their rows" and "Which films are
   sent") and run again, until it exits `0` or holds only `held-no-title` books,
   which are added by hand.
5. Remove from Goodreads every book `review.md` lists `remove-first` (see "Books
   already on a shelf" below).
6. On Goodreads' import page ("Import and export", "Import"), upload
   `goodreads-001.csv`, in one go (see "The feed" below).
7. After the import: check the `by-title` and `title-only` books (see "The Goodreads
   import").
8. Export the library again and run this tool once more, into a new `--out`, to
   confirm. Expect no import file: every imported book is listed `on-service`, and
   every book the omit list names `omitted`. A book still sent is one the import did
   not land as read, such as a `by-title` or `title-only` book Goodreads matched to
   another work or did not find: look it up before importing anything. Export the
   library again before any later run, too.

### The library export

`--account` names the one CSV that "Export Library" gives. A folder or a zip is
refused, since the download is never either. The file is held to the same bounds as
a Letterboxd export file (32 MiB, and one cell as long as the whole file), and must
carry the columns `Title`, `Author`, `ISBN13`, `My Rating`, `Exclusive Shelf`, and
`Read Count`; others are ignored. Goodreads writes an ISBN cell as `="9780000000002"`
so a spreadsheet keeps its digits; the wrapper is dropped. A file missing a column,
or holding no book, stops the run with exit `2`.

Each book the export lists is either read or shelved, and that decides whether a
book from the lists is sent:

| The library export lists the book | What is sent | Listed as |
| --- | --- | --- |
| Not at all | The book. | nothing for the account |
| On the `read` shelf, or with a rating above 0 or a read count above 0, on any shelf | Nothing: sending it would overwrite the rating or date given there, or add a second read. | `on-service` |
| On any other shelf with no rating: to-read, currently reading, or an exclusive custom shelf | The book, in full, with the tag `goodreads.shelf_tags` gives that shelf, if any. | `remove-first` |

A book from the lists matches an account book by ISBN-13, else by title and author.
A title is compared ignoring case, spacing, and punctuation, with a trailing series
part such as `(Name, #3)` dropped, and once more with any subtitle after the first
`:` dropped too; either form matching is enough. An author is the last word of the
first author's name, with accents dropped, so `Le Guin` against `Guin`, or a drift in
initials, does not split a match. A book from the lists with no author matches its
title alone, and the `on-service` detail says so.

The holds reach one step further for books, as they do for films: a free book that
shares a held book's title and author would put that title on the account, and the
next run would then skip the held book as read. So it is held with it, under the
same kind and as an error. A book with no author shares a title with a held book of
any author.

### Goodreads books and their rows

The rows are grouped into books: a book is its ISBN-13, else its Open Library work
key, else its title and its first author's last name. Goodreads keeps one record a
book, and a second import overwrites the first, so **a book read more than once is
sent as one row**: its newest read, by date, then file, then line, with that read's
date and rating. The earlier reads' years can go into Private Notes
(`private_note_earlier`), and the book is listed `reread-merged`. Reads merge only
when they share that key, so **reads of one book keyed apart are held**, every one of
them, as `held-split` errors: two books that share an Open Library work key, that
share a title and first author's last name, or where one has no author and its title
is the other's. A title is compared as the account match compares one: a trailing
series part dropped, and a match of either the whole title or the part before its
subtitle (before the first `:`) is enough. A shared title alone does not hold two
books whose full titles (subtitle kept) differ, when each carries an Open Library
work and the two works differ: they are treated as different books, as two volumes of
one collection are (`Collected Stories: Volume One` and `Volume Two`), and both are
sent. One book whose two subtitled forms resolved to two duplicate work records is
sent twice the same way, unless an override gives both reads the same work. Two
reads with the same full title are held even when their works differ, since Open
Library often keeps two work records for one book, and so are two subtitled forms of
one title when either has no work. So two reads of one title and author resolved to
two ISBN-13s are such a book, and so is a read resolve found by search beside an
override that names no work (an `isbn13` alone, or nothing), even when the override's
title adds a subtitle. Sending both would put a second row in the file that
overwrites the first. Settle it with title-only resolve overrides that give every read
the same answer (the same `isbn13` and `ol_work`): an override applies to the rows of
its own title only, so reads whose list titles differ (a work shared under two
titles, say) need one override for each title. Rerun resolve, then this tool. When
the held books are in fact different books, an override would merge them: name one
in `[[goodreads.omit]]` instead, and the other is sent. The omitted book is never
exported, so add it on Goodreads by hand. An omit entry names every book with a read
of its title (compared ignoring case, spacing, and punctuation, subtitle included) in
its `year`, or with no year when it names none, so it cannot tell apart two
different books listed under the same title with the same year, or both with no
year: it leaves out both, and both must then be added by hand. A held group the
account has already read whole, every book of it skipped as `on-service`, is not
held, since nothing would be sent.
So no book is sent twice in one file, and the order inside the file does not matter.
Books are ordered by their sent row, undated rows first, then by date, file, and line.

How Goodreads finds the book depends on what resolve gave it:

| The book's sent row has | Goodreads matches it by | Listed as |
| --- | --- | --- |
| An ISBN-13 (a `resolved` book, an override naming one, or an override naming a work resolve found one for: the pick from the work's edition list, or another row's ISBN-13 for that work, an override's own or a search's) | `ISBN13` | nothing, unless unrated or undated |
| An ISBN-13 and a work key but no author: an override whose work lookup failed | `ISBN13` | `author-missing`: rerun resolve to fill the author |
| No ISBN-13, and an author: an override naming a work for which resolve found no ISBN-13 (no edition list pick, and no other row of the work carrying one), or a `not-found` book whose list gave an author | `Title` and `Author` | `by-title` |
| No ISBN-13 and no author: an `unresolvable` override, an override whose work lookup failed, or a `not-found` book whose list gave none | `Title` alone | `title-only` |

An override whose work lookup failed and that has no ISBN-13 either is listed
`title-only`, not `author-missing`: Goodreads will match it by its title alone, which
is the more pressing check. To leave a book out of the import for good (an
`unresolvable` book Goodreads would not find, say), name it in `[[goodreads.omit]]`.

### Goodreads columns

Every Goodreads import file has these columns, in this order:

| Column | Comes from |
| --- | --- |
| `Title` | parse's `title`. |
| `Author` | The first name in resolve's `creator`; empty for a book with none. |
| `Additional Authors` | The other names in `creator`, joined by a comma and a space. |
| `ISBN` | Always empty: resolve keeps one ISBN-13 a book, and the import matches on it. |
| `ISBN13` | resolve's `isbn13`; empty when it has none. |
| `My Rating` | `goodreads.ratings` for the row's `rating_raw`, written like `2.5` or `5` (Goodreads keeps a half star); empty for an unrated row. |
| `Date Read` | The row's date, written `2010/05/06` as the library export writes one; empty for an undated row. |
| `Date Added` | The same date, so the import sorts the book into the past (see "Backdating" below); for an undated row, `goodreads.undated_date_added`. |
| `Bookshelves` | `goodreads.shelves`, then for a `remove-first` book the tag `goodreads.shelf_tags` gives its shelf, joined by a comma and a space. |
| `Exclusive Shelf` | Always `read`. |
| `My Review` | The row's comment, and nothing else. |
| `Private Notes` | The note, its rating sentence, and the earlier-reads sentence, joined by spaces (see below). |

As for films, a `Title`, `Author`, `Additional Authors`, `My Review`, or
`Private Notes` cell that starts with a formula character is written as it is and
listed `formula-cell`.

### The `[goodreads]` table

| Key | Required | Meaning |
| --- | --- | --- |
| `goodreads.shelves` | yes | Shelves written to every row, at least one. Each is 1 to 50 lowercase letters, digits, hyphens, or underscores (Goodreads' own rule: no comma, space, or capital), and starts with `retrolog`, as every Letterboxd tag does. |
| `goodreads.ratings` | yes | Each rating key a row can carry, mapped to stars from 0.5 to 5 in halves. |
| `goodreads.undated_date_added` | when a row is undated | The `Date Added` an undated row gets, a TOML date such as `1900-01-01`. An undated row with none stops the run before anything is written, even when that row would be cut, omitted, or merged away: the check reads the rows before selection, so a run never fails or passes on what the account holds. A sent undated row with no `Date Added` would be dated today by the import and sorted above current reading. |
| `goodreads.private_note` | no | The note written to Private Notes on every row, at most 500 characters, with `{year}` and `{date}` as for `letterboxd.review_note`; an undated row leaves out a note naming either. |
| `goodreads.private_note_rating` | no | A sentence after the note on a rated row only, with `{rating}`, the word `goodreads.rating_names` gives the key. |
| `goodreads.rating_names` | no | The word for each rating key, as `letterboxd.rating_names`. A sentence naming `{rating}` needs a word for every key. |
| `goodreads.private_note_earlier` | no | A sentence after the others on a merged reread only, with `{years}`, the earlier reads' years, oldest first, joined by a comma and a space. |
| `goodreads.cutoff` | no | As `letterboxd.cutoff` (see "The cutoff"). |
| `goodreads.omit` | no | Books never sent, one `[[goodreads.omit]]` table each, by `title` and `year` (see "The omit list"). |
| `goodreads.shelf_tags` | no | An account shelf mapped to the tag shelf a `remove-first` book on it adds to its row, such as `"donation-shelf" = "donation-shelf-read"`; a shelf with no entry adds no tag. Both names obey the shelf rule above, but a tag needs no `retrolog` prefix: it keeps the account's own grouping. Every row still carries `goodreads.shelves`. |

A config may hold `[letterboxd]`, `[goodreads]`, or both; `--service` names the one
read, and a run whose table is missing stops with exit `2`.

### Private Notes and the shelf

Goodreads keeps My Review and Private Notes as separate fields, so the note this tool
adds goes to Private Notes, which only the owner sees, and My Review holds only the
lists' own comment. With the example config, a rated book read twice reads:

```text
Example import. Read 2019. Lists said: good. Earlier reads: 2010.
```

Every row is shelved with `goodreads.shelves`, normally one shelf, `retrolog`, so
that shelf lists everything the import added and is where to undo it. There is no
shelf per rating: Goodreads shelves are public, and Private Notes already keeps the
lists' verdict.

### The Goodreads import

Goodreads shows no preview before an import and has no undo, so read `review.md`
first, remove the `remove-first` books before the upload, and do the other checks
below after it.

- **The feed.** Every rated row posts a "rated a book" update to the owner's
  followers, once per book, backdated or not, and a CSV cannot turn it off. Import
  the whole file in one go, so it is one burst rather than several.
- **Backdating.** The importer honors `Date Added`, and the account's read-shelf
  views and widgets order books by it, not by the date read. So each row's
  `Date Added` is its read date, and an imported book sorts into the past instead of
  above the owner's current reading; an undated book gets the config's early date and
  sorts to the bottom. Should an import ignore the column, the fallback is to delete
  and re-add the three newest real books afterward, which puts them back on top but
  loses their started dates, any likes and comments on their reviews, and their
  original date added.
- **Books already on a shelf.** A book the library export has on a shelf other
  than `read`, with no rating and no read count (to-read, currently reading, or an
  exclusive custom shelf such as a giveaway pile), is sent and listed `remove-first`,
  its detail naming the shelf. Before the import, remove each of those books from
  Goodreads: on My Books, Batch Edit, select them by the review's list, and remove
  them from all shelves. Each then arrives as a new book: on `read`, with its
  Private Notes, its backdated `Date Added`, and the tag `goodreads.shelf_tags`
  gives its old shelf, which keeps that grouping without a pass afterward. It loses
  only the date it was first shelved. A book left in place is updated instead: it
  takes the rating, read date, and review, but stays on its custom shelf, uncounted
  as read, with no Private Notes and its own `Date Added`; and moving it to `read`
  afterward posts "finished reading" to the feed.
- **Title matches.** A book sent by Title and Author, or by Title alone, can land on
  another edition, or on another work altogether: a study guide to the book, by
  another author, has taken a book's rating and note and posted to the feed. Open
  each `by-title` and `title-only` book after the import and check it.
- **Undoing a book's read by hand.** Returning a book to the shelf it was on takes
  three controls: its status, each tag shelf (Batch Edit, remove from that shelf),
  and the Remove on its "Dates read" row. Clearing the date's fields alone leaves an
  empty reading, the library export still gives a read count of 1, and the next run
  lists the book `on-service`.
- **The files hold the owner's titles.** As for films, the import file and the review
  quote the operator's own titles and notes: keep them as private as the lists.

### Goodreads review kinds

A book run's review lists only the kinds a book can earn: never `same-date`,
`logged-no-stars`, `watched-only`, or `override-unchecked`, which are Letterboxd's.
`held-ambiguous`, `held-failed`, `held-no-title`, `omitted`, and `formula-cell` mean
what they do for films; the others mean:

| Kind | Error | Meaning | What the operator does |
| --- | --- | --- | --- |
| `held-split` | yes | One book's reads are keyed apart: they share a work key, or a title (its subtitle dropped or kept) and author, but not the ISBN-13 or work that keys a book (see "Goodreads books and their rows"). A shared title alone does not hold books whose full titles differ and that each carry a different work, such as two volumes of one collection. Every such book is held, never merged, since a second row would overwrite the first. | Give every read the same answer with title-only resolve overrides, one for each list title the reads carry, rerun resolve, then this tool. If the books are in fact different, name one in `[[goodreads.omit]]` instead and add it on Goodreads by hand; an entry cannot tell apart two books listed under one title and year. |
| `on-service` | no | The library export lists the book as read, or rated or read on any shelf, so it was not sent. The detail says when the match was by title alone. | Nothing; check the match when it was by title alone. |
| `remove-first` | no | Sent, though the library export has the book on another shelf, with no rating or read count; the detail names the shelf. Its row adds the tag `goodreads.shelf_tags` gives that shelf. | Remove it from Goodreads before the import (see "Books already on a shelf"). |
| `by-title` | no | Sent by Title and Author, with no ISBN-13. | After the import, check that Goodreads matched the right edition. |
| `title-only` | no | Sent by Title alone, with no ISBN-13 and no author. | After the import, check that Goodreads found it; or omit it. |
| `author-missing` | no | An override whose Open Library work gave no author: sent by its ISBN-13, but the author would sharpen the next run's match. | Rerun resolve to fill the author. |
| `reread-merged` | no | Read more than once in the lists, and sent once, as its newest read; the detail says how many reads were merged. | Nothing. |
| `unrated` | no | Sent with no rating. | Rate it by hand on Goodreads. |
| `undated` | no | Sent with no read date; its `Date Added` is the config's. | Nothing, or add the date by hand. |

## Samples

`samples/` holds a builder for a synthetic resolve folder, account export, and config,
and the output this tool writes for them with `--rows-per-file 4`
(`samples/expected/`), which shows a film's two rows kept together, a split into two
files, every review kind, an omitted film, and a film in each account class, and the
test run's file for them (`samples/expected-test/`). With `--service goodreads`, the
builder writes book CSVs, a library export, and the same config instead, and
`samples/expected-goodreads/` holds the one import file and review the tool writes
for them, which show every book kind: a reread merged, a book on the account's custom
shelf sent and listed `remove-first` with its tag, one on `to-read` listed with no
tag, one rated on the custom shelf left out, and a book matched each way.
`samples/README.md` gives the commands to build the inputs, run the tool, dry run
it, and compare.
