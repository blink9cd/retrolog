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
| 4a. probe | `retrolog_probe` | export's import files | a report of the films Letterboxd lacks, by file and row, to leave out before importing; optional, and the one tool besides resolve that reaches a network service |
| 4b. restore | `retrolog_restore` | the account export you backed up before deleting your latest diary entries, and one taken after | a Letterboxd import file restoring the deleted entries with their reviews, and `restore.md` listing them and marking the films to like again |

**`retrolog_probe` and Letterboxd's terms.** Letterboxd's terms of use forbid automated
access to the site without its permission. `retrolog_probe` sends one request per film
to Letterboxd; whether to run it is your own decision. It runs only when you start it,
sends only the ids (no cookies, no account), one at a time and a few seconds apart, and
never reads a 429 ("too many requests") as missing. Every other step works without it.

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

`retrolog_bootstrap` puts the shared libraries each tool asks for on its import path,
each appended after the standard library, so none of them can shadow a standard-library
module. Each tool puts its own `src/` folder, and those of the sibling tools it imports
from, first on the path instead, so a module inside the checkout is trusted as part of
it. The shared libraries sit beside the tools: `stdio_utf8` (UTF-8 on
each tool's own output streams), `help_formatter` (live defaults in `--help`),
`bounded_run` (a subprocess timeout that holds when a converter leaves a grandchild
running), and `bounded_fetch` (resolve's HTTPS fetch, bounded in size and time, and probe's
HEAD request, which reports a redirect instead of following it).
`bounded_run` appends `stdio_utf8`'s folder for its own import as well. A fifth folder,
`live_pid_guard`, is used only by `bounded_run`'s own tests, to clean up the processes
they start; no tool imports it.

Four tools' `src/` folders double as the project's shared modules. As the code
imports them:

- **parse** takes from the survey's its Word, text, and folder readers, its
  converters, the output guard, the atomic writer, and the Markdown escape rules;
- **resolve** takes the survey's output guard and atomic writer, and from parse's its
  columns, CSV file names and their year order, review count line, Markdown table-cell
  escape, title key, and config file checks;
- **review** takes from resolve's the TMDb id rule, the ISBN check, and the overrides
  check, and the survey's output guard and atomic writer (parse's `src/` goes on its
  path too, for resolve's own imports);
- **export** takes from resolve's its columns, statuses, cell shapes, TMDb id rule,
  creator joiner, and `--timeout` rule; from parse's the same as resolve does; and the
  survey's output guard, atomic writer, and count wording;
- **restore** takes export's account-export reader, its CSV field limit, and its CSV
  writer, resolve's `--timeout` rule, parse's Markdown table-cell escape, and the
  survey's output guard and atomic writer;
- **probe** takes export's import-file columns, resolve's TMDb id rule, parse's
  Markdown table-cell escape and title key, and the survey's output guard, atomic
  writer, and count wording (resolve's `src/` also serves export's columns module,
  which takes resolve's creator joiner).

## Your lists stay private

**Every output these tools write holds your own list text:**

- the survey report quotes sample lines;
- parse's CSVs and review list hold your titles, ratings, and comments;
- resolve's CSVs, review list, and cache quote your titles;
- the review page and its overrides file hold them too, and the page keeps your picks in
  the browser's local storage, readable by any other local file that browser opens, until
  you press **Forget stored picks**;
- export's import files are your lists in Letterboxd's format;
- restore's import file and `restore.md` hold your deleted entries' titles and reviews;
- probe's report names the films Letterboxd lacks, with their titles. Its cache holds
  only ids and verdicts, but running probe sends each `tmdbID` in the import files to
  Letterboxd.

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

## Install

Needs Python 3.12 or newer. Install into a virtual
environment, because Homebrew and Debian Python refuse a
system-wide `pip install`:

```bash
python3.12 -m venv .venv
# shellcheck source=/dev/null # the activate script exists only once the line above has run
source .venv/bin/activate
python -c "import tomllib; print(*tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies'])" | xargs python -m pip install
python -c "import tomllib; print(*tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']['test'])" | xargs python -m pip install
```

The commands below assume the venv is active, so `python`
is the venv's interpreter. The second install line is needed
only to run the tests.

The tree is not an installable package, so the two lines read the exact versions pinned in
`pyproject.toml` and install those: `python-docx` and `lxml`, which the survey and parse
read `.docx` files with, and for the tests `hypothesis`. Everything else, the network
lookups included, uses the standard library. The first line names `python3.12` because
macOS's own `python3` is older; any 3.12 or newer works. On Windows, activate with
`.venv\Scripts\activate` and install the same pinned versions with `python -m pip install`
by hand. Activate the venv again in each new shell.

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

Importing the files needs a Letterboxd account that can use its importer, which is
normally for paid (Pro) members (`retrolog_export/retrolog_export.md`, "The import
screen"). Check that your account can reach Letterboxd's import page before you start: nothing in the
steps below tells you whether it can until step 9.

The review page is a single local HTML file that any current browser opens. It sends
nothing itself; its catalog links open TMDb or Open Library only when you follow one.
Its own scripts have a separate test suite under `retrolog_review/page/`, run with
Node.js through `retrolog_review/package.json`: in that folder, run
`npm ci --ignore-scripts`, then `npm test` for the suite and `npm run check` for its
formatting and lint checks.

## A first run, step by step

The tool docs say what each tool does. This section says what you do between them,
including the parts that happen on Letterboxd's website, which no tool can do for you.
Its steps are numbered on their own, not by the pipeline table's.

### Before you start

Every command below runs from the top of this repository, in a shell where two
variables name folders of yours. Set them first, with your own paths:

```bash
export LISTS=../my-lists
export WORK=../retrolog-work
```

- `$LISTS` is the folder of your lists, which the tools only read.
- `$WORK` is a working folder you make for everything else. It must be outside every git
  work tree (this clone included) and outside `$LISTS`, because the tools refuse to
  write inside either. A folder beside the clone works, as long as the clone's parent is
  not itself a repository.

Lay it out like this, and create `review/` yourself: the review tool does not create its
output folder, while the others do.

```text
$WORK/
  rules.toml        your parse rules (step 2)
  overrides.toml    your resolve overrides (step 4)
  export.toml       your export config (step 5)
  account/          Letterboxd account exports, one .zip per download
  review/           the review page and its overrides files
  survey/  parsed/  resolved/  exported/  restore/   the other tools' outputs
```

The three `.toml` files are yours to write. `rules.toml` and `export.toml` start as
copies of the invented examples in parse's and export's `samples/` folders (steps 2
and 5). `overrides.toml` does not: it starts as the first round's file from the review
page (step 4), and `retrolog_resolve/samples/overrides.example.toml` is a reference for its
format only, since copying it would add the sample's titles to your run. Step 3 reads
it as `--overrides "$WORK/overrides.toml"`. All three name titles and rating codes from
your lists, so keep them as private as the lists. Put your TMDb token where
`retrolog_resolve/retrolog_resolve.md` ("The TMDb token") says, before step 3.

### Try it on the samples

You can run every tool first on invented inputs, with no lists of your own, no TMDb
token, and nothing sent over the network. Each tool's `samples/` folder holds its inputs
and a `README.md` saying what the output should hold: the survey, parse, resolve,
export, and restore samples have a builder script that writes their inputs, and the
review sample's inputs ship as files. The samples stand alone: each tool runs on its own
sample inputs, not on the previous tool's sample output, so they show what each tool
does rather than one run from start to end. Write everything into a fresh folder outside
every git work tree, such as one from `mktemp -d`:

```bash
TRY="$(mktemp -d)"
python -I retrolog_survey/samples/build_sample_tree.py --out "$TRY/survey-lists"
python -I retrolog_survey/retrolog_survey.py --path "$TRY/survey-lists" --out "$TRY/survey" --no-convert
python -I retrolog_parse/samples/build_parse_tree.py --out "$TRY/parse-lists"
python -I retrolog_parse/retrolog_parse.py --path "$TRY/parse-lists" --rules retrolog_parse/samples/rules.example.toml --out "$TRY/parsed"
python -I retrolog_resolve/samples/build_resolve_inputs.py --out "$TRY/resolve"
python -I retrolog_resolve/retrolog_resolve.py --parsed "$TRY/resolve/parsed" --out "$TRY/resolve/out" --overrides "$TRY/resolve/overrides.toml" --medium film --cache-only
python -I retrolog_review/retrolog_review.py --mode page --review retrolog_review/samples/review.json --out "$TRY/review.html"
python -I retrolog_review/retrolog_review.py --mode overrides --review retrolog_review/samples/review.json --picks retrolog_review/samples/picks.example.json --out "$TRY/overrides-round-1.toml"
```

`--cache-only` makes resolve answer from the cache its builder wrote into
`$TRY/resolve/out`, so it sends no request and needs no token; give it that same
`--out` folder, or every lookup comes back `failed`. Resolve exits `1` here, and export
below does too, because each sample shows its error kinds on purpose. The survey's
`--no-convert` skips `.doc` conversion, so its sample runs the same on every machine.

Export's builder writes the account export as an unpacked folder. `--account` takes
that folder as it is, but to try it the way step 7 runs, with a `.zip` like the one
Letterboxd gives you, zip the folder's files at the top level of the archive:

```bash
python -I retrolog_export/samples/build_export_inputs.py --out "$TRY/export"
(cd "$TRY/export/account" && zip -q -r ../account.zip .)
python -I retrolog_export/retrolog_export.py --service letterboxd --resolved "$TRY/export/resolved" --account "$TRY/export/account.zip" --config "$TRY/export/export.toml" --out "$TRY/export/exported" --rows-per-file 4
```

`--rows-per-file 4` splits the sample's seven sent rows into two files, as export's sample
README does, so the output matches it.

Restore's builder writes two account exports, one before a deletion as a `.zip` and one
after it as a folder:

```bash
python -I retrolog_restore/samples/build_restore_inputs.py --out "$TRY/restore"
python -I retrolog_restore/retrolog_restore.py --backup "$TRY/restore/account/backup.zip" --current "$TRY/restore/account/export-1" --out "$TRY/restore/out"
```

Read the survey's `$TRY/survey/report.md`, and the `review.md` in `$TRY/parsed`,
`$TRY/resolve/out`, and `$TRY/export/exported`, which also holds the import files,
`letterboxd-001.csv` and `letterboxd-002.csv`, and restore's `$TRY/restore/out/restore.md`
beside its `letterboxd-restore.csv`. Open `$TRY/review.html` in a browser to
see the review page. Each sample's `README.md` lists what its tool prints and what the
output shows.

### 1. Survey the lists

```bash
python -I retrolog_survey/retrolog_survey.py --path "$LISTS" --out "$WORK/survey"
```

Read the report, `$WORK/survey/report.md`: it shows which formats and rating marks each
part of the folder uses.

### 2. Write the rules, then parse

Copy `retrolog_parse/samples/rules.example.toml` to `$WORK/rules.toml` and describe
your lists' conventions in it, using the survey's sample lines.
`retrolog_parse/retrolog_parse.md` ("The rules file") explains each key. Then run:

```bash
python -I retrolog_parse/retrolog_parse.py --path "$LISTS" --rules "$WORK/rules.toml" --out "$WORK/parsed"
```

Read `$WORK/parsed/review.md` after every run, whatever the exit code: lines the rules
could not read are listed there even on exit `0`. Exit `1` means it also lists errors,
which you must fix. Adjust the rules and run parse again until everything it lists is
something you accept.

### 3. Match the titles

```bash
python -I retrolog_resolve/retrolog_resolve.py --parsed "$WORK/parsed" --out "$WORK/resolved" --medium film
```

Letterboxd takes films only, so this walkthrough goes on with the films alone. Parse
writes your books' CSVs too, but they stop there until a book service is supported; to
add their Open Library ids anyway, run the same command with `--medium book` and an
`--out` of its own, such as `"$WORK/resolved-books"`, since each run rewrites its
folder's `review.md` with that run's medium only.

Add `--overrides "$WORK/overrides.toml"` once that file exists. Every successful lookup
is cached, so running this again sends nothing for a title already found; a failed
lookup is not cached and is tried again. Exit `1` means `$WORK/resolved/review.md` lists an
error, such as a failed lookup or an override that matched nothing
(`retrolog_resolve/retrolog_resolve.md`, "Exit codes").

### 4. Settle the review, and repeat

`$WORK/resolved/review.md` lists every title the search could not settle alone, and
every single exact match it doubts. Settle them on the review page:

```bash
python -I retrolog_review/retrolog_review.py --mode page --review "$WORK/resolved/review.json" --out "$WORK/review/review.html"
```

Open the page, pick a match for each title, and press **Save picks file**. The browser
saves `picks.json` to its downloads folder: move it into `$WORK/review/`, since it
holds your titles. The first time, turn the picks into your overrides file:

```bash
python -I retrolog_review/retrolog_review.py --mode overrides --review "$WORK/resolved/review.json" --picks "$WORK/review/picks.json" --out "$WORK/overrides.toml"
```

Every round after that, add the new picks to the same file with `--append-to` in place
of `--out`. It keeps what the file already holds, and refuses a pick that repeats an
entry the file already has:

```bash
python -I retrolog_review/retrolog_review.py --mode overrides --review "$WORK/resolved/review.json" --picks "$WORK/review/picks.json" --append-to "$WORK/overrides.toml"
```

Run step 3 again with `--overrides`, and repeat this step until the review lists
nothing you have not decided. For a title TMDb lacks, pick **None of these**: it becomes
`unresolvable = true`, and export sends it by title. When you are done, press **Forget
stored picks** on the page.

**Check a sample of the settled films too.** A single exact title with enough TMDb votes
is accepted without review, and can still be a remake, a sequel, or another film of the
same name. Skim `$WORK/resolved/film-*.csv` for titles you know have more than one
film; to fix one, add a film override for it by hand
(`retrolog_resolve/retrolog_resolve.md`, "The overrides file") and run step 3 again.

### 5. Write the export config

Copy `retrolog_export/samples/export.example.toml` to `$WORK/export.toml`. The
decisions in it are yours, and `retrolog_export/retrolog_export.md` ("The export
config") explains each key:

- how each of your rating marks maps to stars;
- the tags every imported row carries, and any per-rating tags; every tag must start
  with `retrolog`;
- whether to send watched dates;
- the review note and its rating sentence, and which rows carry them;
- the cutoff: the first line of your lists from which you have logged on Letterboxd
  as you went, so no row dated from then on is sent; undated rows are never cut. It
  names that line's `file`, `line`, and `title`, and must name a dated row with that
  title there, or the run stops ("The cutoff").

### 6. Decide whether to protect your recent activity

Every import tops your diary's RSS feed and your profile's recent activity, above your
real recent entries ("The import screen" in the export doc). If you care about that
feed, do this before step 7; otherwise skip it and step 10.

1. Download an account export (**Export your data**, in your Letterboxd account's
   settings) and keep it as `$WORK/account/backup.zip`. It is the only copy of what you
   are about to delete.
2. On Letterboxd, delete your latest few diary entries, with their reviews. The
   restore in step 10 cannot bring a like back, so its `restore.md` marks each film to
   like again by hand.
3. Download a second export, and use only that one in step 7, so export reads the
   account as it now is. An export from before the deletion still lists the deleted
   diary entries, and export drops any row of yours that falls on the date of one of
   them (`same-date` in the export doc), so that watch would be left out.

### 7. Export

Download a fresh account export (skip this if you just did in step 6), save it in
`$WORK/account/` as `export-1.zip`, and run the command below. Number each later
download (`export-2.zip`, and so on), and pass the newest to `--account` every time:

```bash
python -I retrolog_export/retrolog_export.py --service letterboxd --resolved "$WORK/resolved" --account "$WORK/account/export-1.zip" --config "$WORK/export.toml" --out "$WORK/exported/round-1"
```

To check a new or edited config first, add `--dry-run`: it runs every check, prints
the counts, and exits as the real run would, but writes nothing.

Give each run its own `--out` folder, so you keep each run's `review.md` to compare
with the next. Exit `1` means a film is held back, and `$WORK/exported/round-1/review.md`
names it and its kind:

- `held-ambiguous` or `held-split`: settle the title with an override (step 4), rerun
  step 3, then this step.
- `held-failed`: rerun step 3, then this step.
- `held-no-title`: the title has no letter or digit, so no tool can match it. Add the
  film on Letterboxd by hand.

**Settle every held film before importing, except `held-no-title`.** A held film that
resolve later settles to a film already sent under another title would read as already
imported, and be skipped for good.

### 8. Check before importing

An import cannot be undone, so read `$WORK/exported/round-1/review.md` first. The export
doc ("Review kinds") says what to do for each kind. These need you:

- **`by-title`**: sent with no TMDb id, so Letterboxd matches it by its title, and year
  when known. Search Letterboxd for each one. To leave out a film Letterboxd lacks,
  add it to `$WORK/export.toml` as a `[[letterboxd.omit]]` table, with the `title` and
  `year` of its `Title` and `Year` cells in the import file (no `year` when that cell
  is empty), and run step 7 again into a new folder: the film is then listed
  `omitted`, in this round and every later one. The export doc ("The omit list") has
  the details.
- **`on-service`** matched by title alone: the detail says so. Check it is the same
  film; if not, fix it with an override.
- **`override-unchecked`**: confirm by hand that the film is not already on the account.
- **`logged-no-stars`**: a film you already logged on Letterboxd (a diary entry, a
  rating, or a review), sent without stars so your rating stays. Test one film before
  the real import: run step 7's command again with `--test-film` and a new `--out`
  (`$WORK/exported/test-1`). It writes only `letterboxd-test.csv`, every row of the
  `logged-no-stars` film `review.md` lists first; import it as in step 9. If the
  film's page still shows your rating, download a fresh export and run step 7 again
  into a new folder, so the test film is not sent twice. If the rating was cleared,
  re-rate the film, run step 7 again on a fresh export, and delete every
  `logged-no-stars` film's rows from the new import files before importing them.
- **`formula-cell`**: a cell a spreadsheet would read as a formula. Never open the import
  files in a spreadsheet; upload them as written.

`unrated` and `undated` rows are sent as they are; rate or date them by hand later if
you want.

### 9. Import

On Letterboxd's import page, upload `letterboxd-001.csv`,
then the next, **in number order**. Turn on all four switches: diary entries, reviews,
ratings, and **tags**. The export doc's "The import screen" gives each switch's label
as the page shows it, and what each one does with these files. The tags are how the
next run knows a film was already sent; without them it sends the film again. Read
each preview's unmatched and mismatched rows before confirming. If a match is wrong, do
not confirm: add an override for it (step 4), and rerun steps 3 and 7.

A preview's unmatched rows are often films Letterboxd lacks. `retrolog_probe` can find
them before you upload anything, by asking Letterboxd about each `tmdbID` in the import
files; it is optional, and Letterboxd's terms of use forbid automated access to the
site without its permission, so whether to run it is your own decision ("The
pipeline" above says what it sends):

```bash
python -I retrolog_probe/retrolog_probe.py --files "$WORK/exported/round-1" --out "$WORK/probe-round-1.md" --cache "$WORK/probe-cache.json"
```

For the films its report lists as missing, paste the report's omit entries into
`$WORK/export.toml` and run step 7 again into a new folder, or delete those rows from
their import files. Run it again later, with the same `--cache` and a new `--out` (it
never overwrites a report), for any it lists as unknown.

### 10. Restore the entries you deleted (only after step 6)

Build the restore file from the two exports of step 6: the backup, and the second
export, which step 7 saved as `export-1.zip`:

```bash
python -I retrolog_restore/retrolog_restore.py --backup "$WORK/account/backup.zip" --current "$WORK/account/export-1.zip" --out "$WORK/restore"
```

It writes `$WORK/restore/letterboxd-restore.csv`, holding every diary entry the backup
has and the second export lacks, with its rating, rewatch flag, tags, and review, and
`$WORK/restore/restore.md`, which lists them. Exit `1` means it found no deleted entry:
check that the two exports are not swapped. Import `letterboxd-restore.csv` last, with
all four switches on, so these entries top your feed again, and read its preview as in
step 9: the file has no `tmdbID`, so Letterboxd matches each row by title and year. An
import cannot restore a like, so then like again each film `restore.md` marks under
"Like again" (a film still liked is not marked): open its page and click the heart.

### 11. Confirm nothing is left

Download one more account export and run step 7 again into a new `--out` folder. Every
imported film now reads as `on-service`, and every film on your omit list as
`omitted`. Expect an import file only for rows you already know about:

- the `logged-no-stars` films you left out in step 8 because their test failed;
- rows sent with an empty `WatchedDate` and no review text: a tag lands only on a row
  that becomes a diary entry or a review, so these never carry one, and every run sends
  them again ("Limits" in the export doc). With `watched_date = false`, that is every
  row without review text. Leave them out of later imports by hand.

Any other film still sent is a mismatch between your lists and the account: look it up
before importing anything.

### A later round

When you add old lines to your lists, run steps 2 to 4 again with the same overrides
file, then steps 6 (optional) to 11 on a fresh account export. Films already imported
carry your `retrolog` tag, so export skips them, apart from the rows step 11 names;
only the new ones are sent.

## Running the tests

Each folder has one test file at its top that runs its whole suite:

```bash
(
  for suite in */test_*.py; do
    python -I "$suite" || { echo "FAILED: $suite" >&2; exit 1; }
  done
)
```

It stops at the first suite that fails, names it, and exits 1, so a script or CI step
running it fails too; the parentheses keep that `exit` from closing your own shell.

`retrolog_resolve`'s live tests send real requests to TMDb and Open Library, and the
TMDb one also needs `RETROLOG_TMDB_TOKEN` set; `retrolog_probe`'s live test sends two to
Letterboxd. To run everything else without touching the network, set:

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
