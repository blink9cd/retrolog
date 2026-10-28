# retrolog_review

Settle the titles `retrolog_resolve` could not settle on its own, on one local page
instead of one question at a time. Mode one turns resolve's `review.json` into a page
listing each title's candidates, with year, creator, votes and a catalog link, the
first one marked as the suggestion. The operator picks as they go, and each pick is
kept in the browser as it is made. Mode two turns the picks saved from the page into an
overrides file in the format `retrolog_resolve.md` documents ("The overrides file"),
ready for resolve's next run.

It sits between two runs of `retrolog_resolve`: resolve writes `review.json`, this
tool turns the operator's picks into overrides, and resolve, given those overrides,
settles the rows the picks name.

**The page and the overrides file hold the operator's own titles.** Treat both as
being as private as the lists they come from: do not post them or commit them to a
repository. The tool refuses an `--out` inside a git work tree for that reason, as
resolve does.

## Usage

```bash
python retrolog_review.py --mode page --review path/to/resolved/review.json --out path/to/notes/review.html
python retrolog_review.py --mode overrides --review path/to/resolved/review.json --picks path/to/picks.json --out path/to/notes/new-overrides.toml
```

| Option     | Required       | Default | Meaning                                                                                                                                                                                                                |
| ---------- | -------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--mode`   | yes            |         | `page` writes the review page; `overrides` writes the overrides file from the picks saved on it.                                                                                                                       |
| `--review` | yes            |         | `retrolog_resolve`'s `review.json` (read only). Both modes take the same one.                                                                                                                                          |
| `--picks`  | overrides only | none    | The `picks.json` the page saved. Refused in page mode.                                                                                                                                                                 |
| `--out`    | yes            |         | The page (refused when it exists, unless it is a page this tool wrote, which is replaced atomically), or the overrides file (refused when it exists, so an earlier one is never lost). Refused inside a git work tree. |

Prerequisites: Python 3.12 or later, with nothing installed into it: the tool imports
the standard library only. It reads the page's template, stylesheet and script from
the `page/` folder beside it, imports modules from its sibling `retrolog_resolve` and
`retrolog_survey` folders, and finds the shared libraries it needs through the
project's one bootstrap, the `retrolog_bootstrap` folder beside its own, so it runs
from the tree it ships in. Any
current browser opens the page; nothing else is needed.

## A review, start to finish

1. Run `retrolog_resolve`. Its output folder holds `review.json`.
2. Run `--mode page` and open the page it wrote, straight from disk.
3. Pick, a title at a time, as long as it takes. Each pick is saved in the browser at
   once, so closing the tab and reopening the page later loses nothing (see "Where the
   picks are kept").
4. Press **Save picks file**. The browser saves `picks.json` the way it saves any
   download.
5. Run `--mode overrides` on that `picks.json`, against the same `review.json`.
6. Append the new file's `[[overrides]]` entries to the overrides file resolve already
   reads (leaving out its `schema = 1` line), or pass the new file to resolve on its
   own if there is none yet, and run resolve again. Resolve checks every entry before
   it looks anything up.

## What the page shows

Every review entry with a decision in it, in `review.json`'s order:

| Kind          | Starts on | The choices                                        |
| ------------- | --------- | -------------------------------------------------- |
| `ambiguous`   | undecided | each candidate, a typed id, or None of these       |
| `not-found`   | undecided | a typed id, or None of these                       |
| `most-voted`  | Keep      | Keep, each candidate, a typed id, or None of these |
| `variant`     | Keep      | Keep, each candidate, a typed id, or None of these |
| `re-searched` | Keep      | Keep, each candidate, a typed id, or None of these |
| `no-isbn`     | Keep      | Keep, each candidate, a typed id, or None of these |

Keep leaves resolve's own result alone and writes nothing. The error kinds
(`override-unused`, `override-mismatch` and `failed`) and `override-applied` are only
counted in a line at the top: they are settled by editing the overrides file by hand,
as `review.md` describes.

Each entry names its title, kind, medium and rows (`<source_file>:<source_line>`), and
each candidate its title (and original title when that differs), year, creator, vote
count when TMDb gave one, and a link: `View on TMDb` for a film, `View on Open Library`
for a book. The first candidate is marked Suggested: it is the service's top result
that survived the year bound, and it was the pick in 33 of 37 no-exact items on a
first real film review. "Show only undecided" hides every entry already decided.

Both modes refuse a `review.json` whose candidate id is not its catalog's id (a film's
TMDb digits, a book's `/works/OL<digits>W`) or whose ISBN is not the ISBN-13 resolve
writes, so an edited file can never turn a link toward another site. The page checks
the id shape again before it builds a link.

A typed id is checked when it is entered. For a film it is the TMDb id's digits. For a book
it is an Open Library work key, `/works/OL<digits>W`, or an ISBN of ten or thirteen
digits (a final `X` allowed, hyphens and spaces dropped). Anything else is marked
invalid beside the field and is not saved.

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

Mode two writes the file to a temporary name beside `--out` and then hard-links it into
place, which is what makes the refusal of an existing file race-free. So an `--out` on a
filesystem without hard links, such as exFAT or an SMB share, fails with exit `2`:
write it to a local disk and move it afterward.

Mode one replaces an existing `--out` only when that file is a page this tool wrote,
recognized by the review-data block every such page holds. Any other file there, a
folder or a dangling link is refused with exit `1` and left as it is. The check runs
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

Nothing. The page is one file with its stylesheet, scripts and data inlined (the two
scripts, `review_storage.js` then `review.js`, joined into one), and it opens from
`file://`. A Content-Security-Policy in it allows only that stylesheet and
that script, by their SHA-256 hashes, and forbids every request, image, font, form
submission and base URL; a `no-referrer` policy covers the catalog links. Those links
open only when the operator clicks one, and no search link is built, since a search
link would put a title into a web address.

## Exit codes

| Code | Meaning                                                                                                                                                                                                                   |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | Written: the page, or the overrides file.                                                                                                                                                                                 |
| `1`  | An input refused: a bad `review.json` or `picks.json`, picks made against another review, an `--out` that exists in overrides mode or is not an earlier page in page mode, or picks whose overrides resolve would refuse. |
| `2`  | Bad arguments, a missing or unreadable file, a page template missing a marker, an `--out` inside a git work tree, or the output could not be written.                                                                     |

Each message names the option, the file's role or the key at fault (for a pick, its
index and key, such as `picks[3].answer.tmdb_id`), never a path, a title or an id.

## Samples

`samples/` holds an invented review (a copy of resolve's own sample), invented picks
made against it, and the page and overrides file the tool writes for them, with the
commands to run both modes by hand in its `README.md`.
