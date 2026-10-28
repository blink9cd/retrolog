# retrolog

Turn a folder of old, hand-kept book and film lists into files a service can import.
Lists kept for years drift between formats: plain text from different machines and
encodings, Word 97 `.doc` files, newer `.docx` files, and ratings marked by a symbol
font, a color, a highlight, a small picture, an emoji, or a single letter. These tools
read such a folder without changing it, turn its lines into dated, rated rows by rules
you write, match each title to a catalog entry, and write import files for Letterboxd.

## The pipeline

| Step | Tool | Reads | Writes |
| --- | --- | --- | --- |
| 1. survey | `retrolog_survey` | the folder of lists | a report of which formats and rating conventions each part of the folder uses, with sample lines |
| 2. parse | `retrolog_parse` | the folder, and a rules file you write from the survey | one CSV per medium and year, and a review list of every line it could not read cleanly |
| 3. resolve | `retrolog_resolve` | parse's CSVs, and an optional overrides file | the same CSVs with a catalog id added to each row (TMDb for films, Open Library for books), and a review list of titles it could not settle alone |
| 3a. review | `retrolog_review` | resolve's review list | a local web page to pick each unsettled title's match, then an overrides file from those picks for resolve's next run |
| 4. export | `retrolog_export` | resolve's CSVs, an export config, and a fresh export of your Letterboxd account | numbered Letterboxd import files holding only the films the account does not hold yet, and a review list of what was held back |

Nothing is dropped silently along the way: every line ends up in a row, in a review
list, or is a layout line such as a heading. Each tool folder has a companion
`<tool>.md` with its options, exit codes, outputs, and limits, and a `samples/` folder
with an invented input and the output the tool writes for it. Every CLI also states its
options, defaults, and prerequisites in `--help`. The rules, overrides, and export config
are yours to write; each `samples/` folder holds an invented example of its own.

Run each tool, and each test suite, as a script in Python's isolated mode:
`python -I path/to/tool.py`, such as
`python -I retrolog_survey/retrolog_survey.py`. Isolated mode keeps a stray
module in the current folder or beside a tool from being imported in place of a
standard one.

`retrolog_bootstrap` puts four shared libraries on each tool's import path, each
appended after the standard library, so none of them can shadow a standard-library
module. Each tool puts its own `src/` folder, and those of the sibling tools it imports
from, first on the path instead, so a module inside the checkout is trusted as part of
it. The shared libraries sit beside the tools: `stdio_utf8` (UTF-8 on
each tool's own output streams), `help_formatter` (live defaults in `--help`),
`bounded_run` (a subprocess timeout that holds when a converter leaves a grandchild
running), and `bounded_fetch` (resolve's HTTPS fetch, bounded in size and time).
`bounded_run` appends `stdio_utf8`'s folder for its own import as well. A fifth folder,
`live_pid_guard`, is used only by `bounded_run`'s own tests, to clean up the processes
they start; no tool imports it.

`retrolog_survey/src` and `retrolog_parse/src` double as the project's shared modules:
the later tools import the output guard and the atomic writer from the survey's, and
the CSV escape, parse's columns, the title key, and the config file checks from parse's.

## Your lists stay private

**Every output these tools write holds your own list text:**

- the survey report quotes sample lines;
- parse's CSVs and review list hold your titles, ratings, and comments;
- resolve's CSVs, review list, and cache quote your titles;
- the review page and its overrides file hold them too, and the page keeps your picks in
  the browser's local storage, readable by any other local file that browser opens, until
  you press **Forget stored picks**;
- export's import files are your lists in Letterboxd's format.

Treat all of them as being as private as the lists themselves: do not commit them to a
repository, attach them to an issue, or share them. Each tool refuses to
write its output inside a git work tree for that reason.

**Never open the CSVs or the import files in a spreadsheet.** A cell that starts with
`=`, `+`, `-`, or `@` is read by a spreadsheet as a formula. The tools write each cell
exactly as the list has it, since the importer reads it as text, and export's review
list names every row holding such a cell. Read the review list instead, and upload the
import files as they were written.

**A Letterboxd import cannot be undone once it is confirmed.** The import screen
previews each file's matches before you confirm: read that preview, and export's review
list, before confirming. Removing an imported film by hand takes three steps (delete
its diary entry or review, un-mark it as watched, and clear its rating).
`retrolog_export/retrolog_export.md` describes the import screen's switches and what
each file holds.

## Requirements

Python 3.12 or newer. Two runtime packages: `python-docx`, which the survey and parse
read `.docx` files with, and `lxml`, which it is built on and which the survey also uses
directly, each at the exact version pinned under `[project].dependencies` in
`pyproject.toml`; everything else, the network lookups included, uses the standard
library only. The tests also need `hypothesis`, at the exact version pinned in the
`test` extra under `[project.optional-dependencies]`. Install those versions with pip,
since the tree is not an installable package, into a virtual environment or the
interpreter's own packages rather than with `--user`: isolated mode leaves the user
packages folder off the path.

Reading a `.doc` file needs a converter: macOS's `textutil`, or LibreOffice's
`soffice` on any system. `.txt` and `.docx` files need neither.

`retrolog_resolve` looks film titles up on TMDb, which needs a free TMDb account and its
API read access token; `retrolog_resolve/retrolog_resolve.md` ("The TMDb token") says
where to put it so it is never typed on a command line. Book titles are looked up on
Open Library, which needs no account; set `RETROLOG_CONTACT` to an email address or URL
to send it inside the tool's `User-Agent`, so Open Library can reach you. Every answer is
cached under resolve's output folder, so a re-run after editing the overrides sends
nothing for a lookup that succeeded. A failed lookup is never cached, so the next run
tries it again.

The review page is a single local HTML file that any current browser opens. It sends
nothing itself; its catalog links open TMDb or Open Library only when you follow one.
Its own scripts have a separate test suite under `retrolog_review/page/`, run with
Node.js through `retrolog_review/package.json`: in that folder, run
`npm ci --ignore-scripts`, then `npm test` for the suite and `npm run check` for its
formatting and lint checks.

## Running the tests

Each folder has one test file at its top that runs its whole suite:

```bash
for suite in */test_*.py; do python -I "$suite"; done
```

`retrolog_resolve`'s live tests send real requests to TMDb and Open Library; the TMDb
one also needs `RETROLOG_TMDB_TOKEN` set. To run everything else without touching the
network, set:

```bash
export RETROLOG_RESOLVE_OFFLINE=1
```

A function marked `# fuzz-target` takes untrusted input and has a property-based test,
named `test_fuzz_<function>`, that runs it on generated input.

A few survey, parse, and review tests run macOS's `textutil` and `git` (the review tests
run only `git`) on files they create in a temporary folder; on a system without them,
those tests skip themselves.

## Credits

This product uses the TMDB API but is not endorsed or certified by TMDB. Film data comes
from [The Movie Database (TMDB)](https://www.themoviedb.org/), and book data from
[Open Library](https://openlibrary.org/).

## License

GNU Affero General Public License v3.0 or later; see `LICENSE`.
