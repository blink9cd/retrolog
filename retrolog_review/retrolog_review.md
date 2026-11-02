# retrolog_review

Settle the titles `retrolog_resolve` could not settle on its own, on one local page
instead of one question at a time. Mode one turns resolve's `review.json` into a page
listing each title's candidates, with year, creator, votes, and a catalog link, the
first one marked as the suggestion. The operator picks as they go, and each pick is
kept in the browser as it is made. Mode two turns the picks saved from the page into an
overrides file in the format `retrolog_resolve.md` documents ("The overrides file"),
ready for resolve's next run.

It sits between two runs of `retrolog_resolve`: resolve writes `review.json`, this
tool turns the operator's picks into overrides, and resolve, given those overrides,
settles the rows the picks name.

**The page and the overrides file hold the operator's own titles.** Treat both as
being as private as the lists they come from: do not post them or commit them to a
repository. The tool refuses an `--out` or an `--append-to` inside a git work tree for that
reason, as resolve refuses its own `--out`.

## Usage

```bash
python retrolog_review.py --mode page --review path/to/resolved/review.json --out path/to/notes/review.html
python retrolog_review.py --mode overrides --review path/to/resolved/review.json --picks path/to/picks.json --out path/to/notes/new-overrides.toml
python retrolog_review.py --mode overrides --review path/to/resolved/review.json --picks path/to/picks.json --append-to path/to/notes/overrides.toml
```

| Option        | Required                                     | Default | Meaning                                                                                                                                                                                                                |
| ------------- | -------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--mode`      | yes                                          |         | `page` writes the review page; `overrides` writes the overrides file from the picks saved on it.                                                                                                                       |
| `--review`    | yes                                          |         | `retrolog_resolve`'s `review.json` (read only). Both modes take the same one.                                                                                                                                          |
| `--picks`     | overrides only                               | none    | The `picks.json` the page saved. Refused in page mode.                                                                                                                                                                 |
| `--out`       | page, or overrides in place of `--append-to` | none    | The page (refused when it exists, unless it is a page this tool wrote, which is replaced atomically), or the overrides file (refused when it exists, so an earlier one is never lost). Refused inside a git work tree. |
| `--append-to` | overrides, in place of `--out`               | none    | An overrides file that already exists, to add the picks' overrides to (see "Adding a round to an existing file"). Refused in page mode, and inside a git work tree.                                                    |

Prerequisites: Python 3.12 or later, with nothing installed into it: the tool imports
the standard library only. It reads the page's template, stylesheet, and scripts from
the `page/` folder beside it, imports modules from its sibling `retrolog_resolve`,
`retrolog_parse`, and `retrolog_survey` folders, and finds the shared libraries it
needs through the project's one bootstrap, the `retrolog_bootstrap` folder beside its
own, so it runs from the tree it ships in. Any current browser opens the page; nothing
else is needed.

## A review, start to finish

1. Run `retrolog_resolve`. Its output folder holds `review.json`.
2. Run `--mode page` and open the page it wrote, straight from disk.
3. Pick, a title at a time, as long as it takes. Each pick is saved in the browser at
   once, so closing the tab and reopening the page later loses nothing (see "Where the
   picks are kept").
4. Press **Save picks file**. The browser saves `picks.json` the way it saves any
   download.
5. Run `--mode overrides` on that `picks.json`, against the same `review.json`: with
   `--out` the first time, to write the overrides file, and with `--append-to` on that
   same file every round after, to add the new picks to it.
6. Run resolve again with that file as its `--overrides`. Resolve checks every entry
   before it looks anything up.

## What the page shows

Every review entry with a decision in it, in `review.json`'s order:

| Kind            | Starts on | The choices                                        |
| --------------- | --------- | -------------------------------------------------- |
| `ambiguous`     | undecided | each candidate, a typed id, or None of these       |
| `suspect-exact` | undecided | each candidate, a typed id, or None of these       |
| `not-found`     | undecided | a typed id, or None of these                       |
| `most-voted`    | Keep      | Keep, each candidate, a typed id, or None of these |
| `variant`       | Keep      | Keep, each candidate, a typed id, or None of these |
| `re-searched`   | Keep      | Keep, each candidate, a typed id, or None of these |
| `no-isbn`       | Keep      | Keep, each candidate, a typed id, or None of these |

Keep leaves resolve's own result alone and writes nothing. The error kinds
(`override-unused`, `override-mismatch`, and `failed`) and `override-applied` are only
counted in a line at the top. An error is settled by editing the overrides file by
hand, as resolve's `review.md` describes; an `override-applied` entry is already
settled by its override.

Each entry names its title, kind, medium, and rows (`<source_file>:<source_line>`), and
each candidate its title (and original title when that differs), year, creator, vote
count when TMDb gave one, and a link: `View on TMDb` for a film, `View on Open Library`
for a book. The first candidate is marked Suggested: it is the service's top result
that survived the year bound: a starting point, not a verdict, so check its link
before taking it. "Show only undecided" hides every entry already decided.

Both modes refuse a `review.json` whose candidate id is not its catalog's id (a film's
TMDb id, one to ten digits with no leading zero, by `retrolog_resolve`'s own rule; a
book's `/works/OL<digits>W`) or whose ISBN is not the ISBN-13 resolve writes, so an
edited file can never turn a link toward another site. The page checks the id shape
again before it builds a link.

A typed id is checked when it is entered. For a film it is the TMDb id, by the same
rule: one to ten digits with no leading zero. For a book it is an Open Library work
key, `/works/OL<digits>W`, or an ISBN, hyphens and spaces dropped: an ISBN-13 starting
`978` or `979`, or an ISBN-10, whose check character alone may be `X`. Either must
carry a correct check digit, by the same rule `--mode overrides` applies to the picks
file. Anything else is marked invalid beside the field and is not saved.

The page reads its review only from the data block this tool writes, with its schema
number; a block that is not valid JSON, or names another schema, shows "The review
data on this page could not be read." and nothing else.

## What a pick writes

A review entry covers the rows of one lookup, and resolve looks a title up once per
pair of years (the work's year, and the year of the row's date). So one title can
appear in more than one entry, for example a film seen in two different years. A pick
therefore writes one line override per row its entry lists, naming that row's `file`
and `line`, and never a title-only one: a title-only override would also move the
title's other rows, including ones resolve settled correctly on its own, which the
review never shows.

| The pick       | Each row's override holds                                                             |
| -------------- | ------------------------------------------------------------------------------------- |
| Keep           | nothing: no entry is written                                                          |
| A film         | `tmdb_id`                                                                             |
| A book         | `ol_work`, and the candidate's `isbn13` when it has one                               |
| A typed id     | `tmdb_id`, `ol_work`, or `isbn13`, as the id was classified                           |
| None of these  | `unresolvable = true`: the export sends it by title (and year when known), with no id |
| Nothing picked | nothing: the entry is counted as undecided                                            |

For example, a film whose entry lists two rows, picked as TMDb id `1000003`:

```toml
schema = 1

[[overrides]]
medium = "film"
title = "Placeholder Film Two"
file = "2010/seen.txt"
line = 4
tmdb_id = "1000003"

[[overrides]]
medium = "film"
title = "Placeholder Film Two"
file = "2011/seen.txt"
line = 9
tmdb_id = "1000003"
```

The file is passed through resolve's own overrides reader before it is written, so a
file this tool writes is one resolve accepts. A line override beats a title-only entry
for its row, so an earlier title-only override for the same title stays in force only
for the rows the picks do not name.

Mode two prints `<w> overrides written for <p> picks, <k> kept, <u> undecided.`

## Adding a round to an existing file

`--append-to` takes the overrides file an earlier round wrote, or one kept by hand,
and adds the picks' `[[overrides]]` tables after its last line, a blank line before
each, with no second `schema = 1`. Its existing text, comments and line endings
included, is kept byte for byte. Before anything is written, resolve's own overrides
reader checks the file as it is, and then the file with the new tables added:

- A file resolve already refuses is refused with exit `1`, naming resolve's reason. So
  is one resolve accepts that holds its entries as an inline array
  (`overrides = [...]`) rather than as `[[overrides]]` tables, since TOML lets no table
  be added to an inline array; rewrite it as tables to append to it.
- A new entry with the same medium, title, file, and line as one already there is
  refused with exit `1`, as resolve would refuse it. Titles compare as resolve compares
  them, so one differing only in case or punctuation is the same title. The message
  names both entries by their index in the joined file (`overrides[7]` is the
  eighth table), never by title, and nothing is written. Running the same picks twice
  is refused this way.
- A missing file, a link, or a folder is refused with exit `1` before anything is read:
  the option adds to an existing file and never starts one, so a mistyped path cannot
  quietly begin a second file. A link is refused because the replace below would swap
  the link itself for a file.

The file is then checked and read once more, and replaced atomically, through a
temporary file beside it, only if it is still a regular file holding what was first
read; a file edited or swapped for a link during the run is refused with exit `1` and
left as it is. That narrows the window rather than closing it: an edit landing
between the last read and the replace is still lost, so run one append at a time on a
file. The replacement is a new file, so it is readable by its owner only, as every
file this tool writes is, and a hard link to the old one keeps the old text. Picks
that are all Keep add nothing, and the file is not touched.

It prints `<w> overrides appended for <p> picks, <k> kept, <u> undecided; the file now
holds <n> overrides.`

## How the outputs are written

Mode two writes the file to a temporary name beside `--out` and then hard-links it into
place, which is what makes the refusal of an existing file race-free. So an `--out` on a
filesystem without hard links, such as exFAT or an SMB share, fails with exit `2`:
write it to a local disk and move it afterward.

Mode one replaces an existing `--out` only when that file is a page this tool wrote,
recognized by the review-data block every such page holds. Any other file there, a
folder, or a link, even one to such a page, is refused with exit `1` and left as it
is: the atomic replace would swap the link itself for a file. The check runs
after the work-tree refusal, so an `--out` inside a work tree is refused with exit `2`
before its bytes are read, and runs again just before the page is written. It guards
against a mistyped `--out`, not a race: unlike mode two's hard link, the atomic
replace cannot refuse a file another process creates between that last check and the
write.

## Where the picks are kept

In the browser's own storage (`localStorage`), under a key that carries the SHA-256 of
the `review.json` the page was written from, so two reviews never mix and a page
rewritten from the same review finds its picks again. Every read and write is guarded:
where the browser keeps nothing (a private window, blocked site data), the page still
works and says its picks will not survive a reload, so save the picks file before
closing it. The storage stays in that browser on that machine and is never sent anywhere.
A stored pick its entry's choices cannot show (Keep where the entry offers none, an id
of the other medium, an id the field would mark invalid, or a work and ISBN pair no
candidate gives) is dropped when the page loads, so an edited store can neither stop
the page drawing its entries nor reach the picks file.

The storage is not private to this page, though. A browser treats every file opened
from `file://` as one origin, so any other local HTML file opened in the same browser
can read the stored picks. Once the picks file is saved, press **Forget stored picks**
to remove this review's picks from the browser: the page keeps the picks already made
until it is closed or reloaded, stores no more, and says so. Clearing the browser's
site data for local files removes them as well.

`picks.json` carries the same SHA-256. Mode two refuses picks made against any other
`review.json`, so a review rerun after the picks were made has to be picked again
rather than half-applied.

## What the page sends

Nothing. The page is one file with its stylesheet, scripts, and data inlined (the two
scripts, `review_storage.js` then `review.js`, joined into one), and it opens from
`file://`. A Content-Security-Policy in it allows only that stylesheet and that
script, by their SHA-256 hashes, and forbids every request, image, font, form
submission, and base URL; a `no-referrer` policy covers the catalog links. Those links
open only when the operator clicks one, and no search link is built, since a search
link would put a title into a web address.

## Exit codes

| Code | Meaning                                                                                                                                                                                                                                                                                                                                                      |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `0`  | Written: the page, or the overrides file (or, with `--append-to`, nothing to add).                                                                                                                                                                                                                                                                           |
| `1`  | An input refused: a bad `review.json` or `picks.json`, picks made against another review, an `--out` that exists in overrides mode or is not an earlier page in page mode, picks whose overrides resolve would refuse, or an `--append-to` that is missing, not a regular file, refused by resolve alone or with the picks added, or changed during the run. |
| `2`  | Bad arguments, a missing or unreadable file, a page template missing a marker, an `--out` or `--append-to` inside a git work tree, or the output could not be written.                                                                                                                                                                                       |

Each message names the option, the file's role, or the key at fault (for a pick, its
index and key, such as `picks[3].answer.tmdb_id`), never a path, a title, or an id.

## Samples

`samples/` holds an invented review (a copy of resolve's own sample), invented picks
made against it, a second round's picks, and the page and overrides files the tool
writes for them (the second round appended with `--append-to`). Its `README.md` gives
the commands to run each by hand.
