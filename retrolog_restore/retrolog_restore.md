# retrolog_restore

Rebuild the Letterboxd diary entries deleted before an import, as one more import
file. Every import tops the account's activity feed, above the real recent entries,
so an operator who cares about that feed backs up the account export, deletes the
latest few diary entries, imports the backfill `retrolog_export` wrote, and then
restores the deleted entries with this tool's file, last, so they top the feed again.

The tool compares two account exports: the backup taken before the deletion, and an
export taken after it. Every diary entry the backup holds and the later export lacks
is a deleted one, so the operator never has to count or list them. It writes the
import file for those entries, each with its review, and `restore.md`, which lists
them and marks the films to like again: an import cannot restore a like.

Nothing leaves the machine: the tool sends no request, and the operator uploads the
file on Letterboxd's import page.

**The output holds the operator's own titles and reviews.** Treat it as being as
private as the account export itself: do not post it or commit it to a repository.
The tool refuses to write inside a git work tree for that reason (see "Where the
output may go").

## Usage

```bash
python retrolog_restore.py --backup path/to/backup.zip --current path/to/export-1.zip --out path/to/restore
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--backup` | yes | | The account export taken before the deletion: its `.zip`, or the folder it unpacks to (read only). |
| `--current` | yes | | An account export taken after the deletion: its `.zip`, or the folder it unpacks to (read only). |
| `--out` | yes | | Output directory; refused inside a `--backup` or `--current` folder, or inside a git work tree. |
| `--timeout` | no | `15.0` | Seconds per git call of the `--out` check: greater than 0 and at most 3600. |

Prerequisites: Python 3.12 or later, and nothing installed into it: the tool imports
the standard library only. It also imports modules from its sibling
`retrolog_export`, `retrolog_resolve`, `retrolog_parse`, and `retrolog_survey` folders
(export's account reader, CSV writer, and formula-cell characters, resolve's `--timeout` rule, parse's Markdown
cell escape, and the survey's output guard and atomic writes), and shared libraries it finds through the project's one bootstrap, the
`retrolog_bootstrap` folder beside its own, so it runs from the tree it ships in.

When it writes the output (exit `0`), the tool prints counts only, never a title:
`entries restored: <n>`, `to like again: <n>`, and a line pointing to `restore.md`.
When a restored entry's name, tags, or review starts with a character a spreadsheet
reads as the start of a formula (the characters of `retrolog_export`'s `formula-cell`
check, which this tool reuses), it adds
`review formula-cell: <n>`, the number of such entries, worded as `retrolog_export`
prints it; the cell is written unchanged, since Letterboxd's importer reads every cell
as text. Upload `letterboxd-restore.csv` without opening it in a spreadsheet first,
because a spreadsheet would run or rewrite such a cell, and saving it would change the
operator's own text.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Output written. |
| `1` | Nothing deleted: every diary entry of `--backup` is in `--current`, so nothing is written. Standard error suggests checking that the two exports are not swapped: `--backup` is the one from before the deletion. |
| `2` | Bad arguments; an export missing, unreadable, too large, holding no `diary.csv`, or holding a damaged or invalid `diary.csv`, `reviews.csv`, or `likes/films.csv`, or one lacking a required column; a restored entry whose `Rewatch` cell is neither `Yes` nor empty, or which matches more than one review; `--out` refused; or the output could not be written. Apart from a failed write, nothing is written, and an earlier output is left as it was. Each message names the problem, the option, and the file inside the export, never a path or a title. |

## Run order

1. Download an account export ("Export your data", in the account's settings) and
   keep it: it is `--backup`, the only copy of what is about to be deleted.
2. On Letterboxd, delete the latest few diary entries, with their reviews.
3. Download a second export. Pass it to `retrolog_export`'s `--account`, and import
   the files that run writes.
4. Run this tool with the first export as `--backup` and the second as `--current`.
5. Import `letterboxd-restore.csv` last, with all four switches on (diary entries,
   reviews, ratings, and tags), and read its preview: the file has no tmdbID, so
   Letterboxd matches each row by title and year.
6. Like again each film `restore.md` marks: open its page and click the heart.

The second export can be any export taken after the deletion: one taken after the
backfill import serves as well, since those imported entries are not in the backup
and so are never restored.

## The account export

Both options name the `.zip` Letterboxd's "Export your data" gives, or the folder it
unpacks to. The tool reads them through `retrolog_export`'s own account reader, so
every bound `retrolog_export.md` sets out under "The account export" holds here too:
each file read is capped at 32 MiB, and a `.zip` larger than 256 MiB, holding more
than 10,000 files, with a zip directory larger than 5,120,000 bytes, or in zip64 form
is refused. Each file must be UTF-8; a leading byte order mark is accepted.

These files are read, matched by their exact names in each export:

| File | Read from | Columns read | When absent |
| --- | --- | --- | --- |
| `diary.csv` | both exports | `Name`, `Year`, `Letterboxd URI`, and `Watched Date`, all required; `Rating`, `Rewatch`, and `Tags` | The export is refused. |
| `reviews.csv` | `--backup` only | `Name`, `Year`, `Watched Date`, and `Review`, all required; `Letterboxd URI` | No entry has a review. |
| `likes/films.csv` | both exports | `Name` and `Year`, required | From `--backup`: no film is to be liked again. From `--current`: no film is liked now, so every film `--backup` likes is marked. |

Columns are located by their header names, in any order. A file that is present must
name every required column in its header, even when no row follows it, so a header
row alone is read as no rows and an empty file is refused; a missing required column
stops the run with exit `2`, naming the option, the file, and the column. An optional
column that is absent reads as empty in every row, and so does a cell a short row
lacks. A cell may be as long as the file cap. Every cell is kept as the text
Letterboxd wrote. Blank lines are skipped. `--current`'s `reviews.csv` is never
read, so a damaged one there does not matter: a deleted entry's review went with it.

## Which entries are restored

A `--backup` diary entry is restored when its `Letterboxd URI` is not the URI of any
`--current` diary entry. An entry with an empty URI is matched instead by its `Name`,
`Year`, and `Watched Date` against every `--current` entry. So an entry still on the
account is kept out even when it was edited since, and a diary entry logged after the
backup is never restored. Entries are written in the backup's order, oldest logged
first, so the newest lands on top of the feed.

Each restored entry carries:

- **its review**: the `Review` of the backup's `reviews.csv` row whose
  `Letterboxd URI` is the entry's own. A review whose URI is no backup diary entry's,
  or which has none, joins instead by the same `Name`, `Year`, and `Watched Date`; a
  review whose URI is another diary entry's, kept or deleted, is never joined by date.
  An entry with neither gets no review. A review of the same film on another date
  belongs to another watch and is not joined. When more than one review matches an
  entry the tool stops with exit `2`, giving how many entries are affected and no
  title.
- **its rewatch flag**: `diary.csv` writes `Yes` for a rewatch and leaves the cell
  empty otherwise; the import file takes `true` and empty. Any other value stops the
  run with exit `2`.
- **whether to like it again**: when the backup's `likes/films.csv` has a row with
  the same `Name` and `Year` and `--current`'s has none. Deleting a diary entry leaves
  its film's heart, and clicking a filled heart removes the like, so a film still
  liked is not marked. A like belongs to the film, not the entry, so a film liked
  under another year is not matched.

## Outputs

| File | Holds |
| --- | --- |
| `letterboxd-restore.csv` | The import file: the header `Title,Year,WatchedDate,Rating,Rewatch,Tags,Review`, then one row per restored entry, from its `Name`, `Year`, `Watched Date`, `Rating`, rewatch flag, `Tags`, and review. |
| `restore.md` | The count of restored entries and of films to like again, the import and like steps, and a table of every restored entry: title, year, watched date, and `yes` when it is to be liked again. |
| `.retrolog_restore` | A marker that lets a later run reuse this directory. |

Each file is UTF-8 with Unix line ends and is replaced atomically. A cell holding a
comma, a double quote, or a line break is quoted in double quotes, with each quote
inside it doubled, as Letterboxd's importer reads it; several tags in one cell, and
most reviews, need it. A run that gets as far as writing first removes the earlier
run's two files, then writes the import file, then `restore.md` last, so a folder
holding the import file and no `restore.md` is a run that did not finish. A link of
either name is removed, not followed, and no other file is touched.

## Where the output may go

`--out` is checked before anything is written, and created with its
`.retrolog_restore` marker only once there is something to write: it is refused
inside a `--backup` or `--current` folder (by path and by file identity), anywhere
inside a git work tree, when it is a file, and when it is a folder that holds files
but not this tool's marker. Both exports are only read.

## Limits

- **The import matches by title and year.** The file has no tmdbID, because the
  account export carries none, so read the import preview for a film Letterboxd
  matched wrongly.
- **Likes come back by hand.** The importer has no column for a like, so
  `restore.md` lists the films to like again. A film unliked on purpose after the
  backup is marked too, since the two exports cannot tell that from a lost like.
- **Two watches of one film on one date can share a review.** A review joins its
  entry exactly only when its `Letterboxd URI` is that diary entry's. When it is not,
  two deleted entries of one film on one date both take the one review that matches
  by film and date (delete the extra copy after the import), or, with two such
  reviews, the run stops with exit `2`. Whether Letterboxd writes a review's diary
  entry URI into `reviews.csv` was not checked against a real export when this join
  was written, so the date join stays as the fallback.
- **Only the diary is restored.** A deleted rating with no diary entry, or a review
  with no diary entry, is not in `diary.csv` and is not restored.

## Samples

`samples/` holds a builder for two synthetic account exports and the output this tool
writes for them (`samples/expected/`): a review with a comma, quotes, and a line
break, several tags in one cell, a rewatch, an entry with no URI, a review joined by
its URI, a review of another watch left unjoined, and two films liked in the backup
among four restored, one of them still liked in the later export and so not marked.
`samples/README.md` gives the commands to build the inputs, run the tool, and compare.
