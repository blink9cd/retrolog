# retrolog_resolve

Give each row of a parsed book or film list a catalog identifier. The tool reads the
CSV files `retrolog_parse` wrote, looks each distinct book title up on Open Library and
each film title on TMDb, and writes every CSV back with six columns added: the
outcome, an ISBN-13 and Open Library work for a book, and a TMDb id, title, and
original title for a film. A review list names every title it could not settle on its
own, with the candidates the search found, and an overrides file the operator writes
settles them on the next run.

It is the third of four steps: survey the folder (`retrolog_survey`), parse it
(`retrolog_parse`), resolve each title (this tool), then export to a service's import
format (`retrolog_export`). Nothing is dropped: every row parse wrote comes back, in
its own file and its own place, with a status.

**The output holds the operator's own titles.** The CSVs are the operator's lists, and
the review and the cache quote their titles. Treat all three as being as private as
the lists: do not post them or commit them to a repository. The tool refuses to write
inside a git work tree for that reason (see "Where the output may go").

This product uses the TMDb API but is not endorsed or certified by TMDb. Film data
comes from [The Movie Database (TMDb)](https://www.themoviedb.org/); book data from
[Open Library](https://openlibrary.org/).

## Usage

```bash
python retrolog_resolve.py --parsed path/to/parsed --out path/to/resolved --overrides path/to/overrides.toml
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--parsed` | yes | | retrolog_parse's output folder (read only). |
| `--out` | yes | | Output directory; refused inside `--parsed` or a git work tree. |
| `--medium` | no | `all` | Which media to read and resolve: `book` or `film` reads only that medium's CSVs, sends no request for the other, and leaves the other's earlier output in `--out` as it was (see "One medium at a time"). |
| `--overrides` | no | none | The overrides file. |
| `--token-file` | no | `~/.config/retrolog/tmdb.env` | The TMDb token file, read only when `RETROLOG_TMDB_TOKEN` is unset or empty and a film row needs a request (see "The TMDb token"). |
| `--directors` | no | `ambiguous` | Which films get their directors from TMDb credits: `none`, the candidates an ambiguous film lists, or `all` (those and every resolved film). |
| `--vote-ratio` | no | `10.0` | The most-votes rule's ratio: a film whose year-kept candidates hold two or more exact titles resolves to the most-voted when its TMDb votes are at least this many times every other's (see "How a title is matched"). `0` turns the rule off, and anything else must be a finite number above 1. Books never take it. |
| `--vote-floor` | no | `1` | A film's single exact title with fewer TMDb votes than this, or an unknown count, goes to review instead of settling (see "How a title is matched"). `0` turns this one signal off; the others stay on. Must be a whole number of 0 or more. Books never take it. |
| `--research`, `--no-research` | no | on | Send at most one extra TMDb search per film with no exact title, for its title without a leading `The`, `A`, or `An` (see "How a title is matched"). Books never take it. |
| `--timeout` | no | `15.0` | Seconds per request or git call: greater than 0 and at most 3600. |
| `--refresh` | no | off | Ask every service again, ignoring what earlier runs cached and rewriting it. |
| `--cache-only` | no | off | Send no request: every lookup the cache cannot answer is `failed`. No token is needed. |

`--refresh` and `--cache-only` cannot be given together.

Prerequisites: Python 3.12 or later, and nothing installed into it: the tool imports
the standard library only. It also imports modules from its sibling `retrolog_parse`
and `retrolog_survey` folders (none of the survey's Word reader), and shared
libraries it finds through the project's one bootstrap, the `retrolog_bootstrap`
folder beside its own, so it runs from the tree it ships in. Its input is
`retrolog_parse`'s output folder. Film rows need a TMDb API read access token (free
with a TMDb account), in a token file or the environment (see "The TMDb token"):

| Variable | Needed | Meaning |
| --- | --- | --- |
| `RETROLOG_TMDB_TOKEN` | when a film row exists and the token file holds no token, unless `--cache-only` | TMDb's "API Read Access Token". An empty value counts as unset. |
| `RETROLOG_CONTACT` | no | An email address or URL Open Library can reach the operator at; printable ASCII, no parentheses. |

### The TMDb token

The token is looked up only when a film row needs a request: a folder with no film
row, a run with `--medium book`, or a run with `--cache-only`, reads neither the
environment variable nor the file. Otherwise a non-empty `RETROLOG_TMDB_TOKEN` wins and
the file is never opened; when it is unset or empty, the token comes from the
`--token-file` file, read once.

Create the file once, readable by its owner only. The token is typed at a hidden
prompt, so it never reaches the command line or the shell history:

```bash
mkdir -p ~/.config/retrolog
( umask 077 && printf 'TMDb read token: ' && read -rs t && printf 'RETROLOG_TMDB_TOKEN=%s\n' "$t" > ~/.config/retrolog/tmdb.env ); echo
```

`umask` sets the mode only of a file it creates: when the file already exists, or
when an editor wrote it, run `chmod 600 ~/.config/retrolog/tmdb.env`.

The file is parsed, never run as a script: no command substitution, no variable
expansion, no escape processing. Blank lines and lines starting with `#` (after
leading whitespace) are skipped. The one other line is `RETROLOG_TMDB_TOKEN=<token>`,
optionally prefixed by `export` and a space; everything after the first `=` is the
token, with one pair of matching surrounding quotes (`"` or `'`) stripped, and a `#`
later in the line is part of it. An empty value is no token. Refused, naming at most the line number and
never the value or the name found there: a line with no `=`, any other variable name,
the name set twice, a NUL character anywhere, an unterminated quote, a file over
64 KiB, and one that is not UTF-8. The token then passes the same check as the
environment value: one that would break the `Authorization` header is refused,
naming "the token file".

On macOS and Linux the open file must be a regular file owned by the current user
with no group or other permission bits (mode 600, or stricter); otherwise the run is
refused with the mode found. The check is made on the open file itself, so the file
checked is the file read. A symbolic link is followed, and the check applies to
the file it points at. Windows has no such bits, so the check is skipped there and
the file is protected by its folder's access control list.

A missing file at the default path is no token, and the run stops with the
needs-a-token refusal. A file named with `--token-file` that is missing or cannot be
read is refused as unreadable.

## Exit codes

Whenever it writes the output (exit `0` or `1`), the tool prints a summary to standard
output, counts only and no title: first `rows: <n>`, then `status <status>: <n>` for
each status that occurred, then `review <kind>: <n>` for each review kind that
occurred, both in the order of the "Statuses" and "Review kinds" tables. With exit
`1`, standard error adds `<program>: <n> error item(s); see review.md`.

| Code | Meaning |
| --- | --- |
| `0` | Output written, and no review entry is an error. |
| `1` | Output written, but the review lists at least one error (`override-unused`, `override-mismatch`, or `failed`). Read `review.md`. |
| `2` | Bad arguments; `--parsed` missing, unreadable, holding no parse CSV (no CSV of the chosen medium, under `--medium book` or `film`), or holding a CSV that is not parse's; the overrides file unreadable or invalid; a film row and no token; a token file that is unreadable, malformed, too large, not UTF-8, or readable by others; a token or contact that would break its header; TMDb refusing the token; `--out` refused; or the output could not be written. Each message names the problem and the variable or "the token file", never a path or a value. |

A refused token stops the run at the first TMDb request, rather than failing every
film one by one. No request checks the token beforehand, so the books are looked up
first and their answers cached. Nothing is written but the output folder's marker and
the answers already cached, so the next run, with a working token, is accepted and
sends only what is missing.

## What is sent, and where

Only the title, the year bound a book search carries, and a film's TMDb id ever
leave the machine: never a rating, a date, a comment, a file name, or a line.

- **Open Library**, for each distinct book title: `search.json` with the title as a
  quoted phrase, `first_publish_year` up to the year the row was read in, and on the
  first two steps English editions with an ISBN (see "How a book is looked up"). No
  key is needed. When `RETROLOG_CONTACT` is set it goes inside the `User-Agent`
  header, in parentheses after the tool's name and version, as Open Library asks of
  scripts.
- **TMDb**, for each distinct film title: `/3/search/movie` with the title alone,
  since TMDb has no "released before" filter; the year bound is applied to each
  result's release date on the machine. With `--directors`, also
  `/3/movie/<id>/credits` for the films it names, and with `--research` (the default)
  one more `/3/search/movie` for a film with no exact title, with its leading article
  dropped. For each distinct `tmdb_id` an override settled a film row by,
  `/3/movie/<id>` with the id alone, for that film's details: its title, original
  title, and release date (see "Statuses"). The token, from the environment
  or the token file, travels only in the `Authorization: Bearer` header; the token
  file's path is never sent or printed.

Neither the token nor the contact appears in a URL, a cache file name, a message, the
review or a CSV. Every request is bounded by `--timeout`, goes over HTTPS only, and
never carries its headers to another host on a redirect.

Open Library's usage policy asks scripts to go easy on it: the tool sends at most
three searches per distinct book title (see "How a book is looked up"), waits a second
between Open Library requests, and identifies itself in the `User-Agent`, with the operator's
contact when `RETROLOG_CONTACT` is set. When a service answers HTTP 429 the tool
waits as long as it asks (at most a minute) and tries once more; a second 429 stops
every further request to that service for the run, and its remaining titles are
`failed`.

## How a title is matched

Rows are grouped by medium, title, and the two years a row carries: the work year parse
read from the title, and the year of the row's date. One lookup answers a whole group,
so a book read twice in one year is looked up once, and a film seen again in another
year is looked up again, bounded by its own year.

A film search result whose id fails the TMDb id rule (a positive whole number of at most
ten digits; the `tmdb_id` column below) is skipped before anything else, as if TMDb had
not listed it.

The candidates left are filtered first: one first published or released after the
row's date year is dropped (the same year is kept), and a row whose title carried a
work year keeps only candidates of exactly that year. An undated row skips the date
filter. Then the rules below run in turn on the candidates left, and the first that
settles the title decides. Every title is compared ignoring case, punctuation, and
spacing, and a film's original title is tried too.

1. **Exact title.** Exactly one remaining candidate's title equals the row's: `resolved`.
   A film is held instead when that candidate has no release date, matches on its
   original title alone, or has fewer TMDb votes than `--vote-floor` (default 1; an
   unknown count is under any floor above 0). A held film is `ambiguous` with no ids,
   under a `suspect-exact` review entry listing the candidate, with a detail naming
   every reason, such as
   `one exact title, held: no release date; 0 votes, under the floor of 1`.
   A year after the row's date is no signal, since the year filter has already
   dropped that candidate. A title in the row's own work year is held all the same.
2. **Most votes (films only).** Two or more remaining candidates have the row's exact
   title, and the most-voted has a positive TMDb vote count at least `--vote-ratio`
   (default 10) times every other exact title's: `resolved`, with a `most-voted` review
   entry listing the exact titles, most-voted first. A vote count TMDb does not give,
   on any exact title, means the rule does not fire, and neither does a tie.
3. **Variants (films only).** No remaining candidate has the exact title, so the row's
   title and every candidate's title and original title are compared again under each
   of six variants, one at a time and each alone, in this order:
   1. an ampersand: `&` read as `and`;
   2. a trailing note: one closing `(...)` or `[...]` dropped;
   3. a sequel numeral: a closing Roman `II` to `X`, in either case, read as its
      digits, so `Placeholder II` meets `Placeholder 2`;
   4. a leading article: an opening `The`, `A`, or `An` dropped;
   5. a possessive: `'s`, or the apostrophe after a plural's `s`, dropped;
   6. number words: each whole word `zero` to `twenty` read as its digits.

   The first variant under which any candidate matches decides: one match is
   `resolved`, with a `variant` review entry naming the variant, and two or more are
   `ambiguous`, listing them, even when a later variant would have found one.
4. **Search again (films only, `--research`).** The rules above left the film
   unresolved with no exact title among the remaining candidates (`not-found`, an
   empty search included, or `ambiguous` with no exact title, a variant's included),
   and its title opens with `The`, `A`, or `An`: TMDb is searched once more for the
   title without that article (a title that is nothing but punctuation without it is
   not searched). Its results are judged together with the first search's, each film
   once, filtered by the same years, and a candidate matches when its title or
   original title, without a leading article, equals the row's title without one. So
   a second search that returns only one of the first's several matches cannot pick
   it. One match is `resolved`, with a `re-searched`
   review entry listing it; two or more are `ambiguous`, listing the first three; none
   leaves the first answer as it was. A failed second search also leaves the first
   answer, with `the search without a leading article failed` added to its detail. Two or
   more exact titles (a collision) are never searched again.

No candidate left is `not-found`; anything the rules leave is `ambiguous`, listing the
first three exact titles, or the first three candidates when none is exact. Holding a
film sends no search, since an exact title is never searched again; `--directors` looks
up its one listed candidate's credits, as for any ambiguous film. Rules 2 and 3 send
no request: both read the answer of the one search already sent. Rule 4's search is
cached like every other, so `--cache-only` reproduces all four from a cache written
with the re-search on; a cache written with `--no-research` lacks those searches, so
rerun such a cache with `--no-research` to reproduce it. Books take the exact rule
only, and are never held.

### How a book is looked up

Each book title is searched in up to three steps, each asking less of the editions:

1. English editions with an ISBN published in or before the read year, the latest
   first.
2. English editions with an ISBN, the earliest first.
3. The work alone, with no edition clause.

The chain stops at the first step whose answer holds an exact title with an ISBN; a
step with no exact title, or with an exact title whose first edition gave no valid
ISBN, moves on. A title the last step resolves has no ISBN and is listed as `no-isbn`.
A resolved book records its work key, its authors, and the first edition's first
ISBN whose check digit is valid (an ISBN-10 is converted to an ISBN-13).

## The overrides file

The overrides file settles the titles the search could not, or settled wrongly. It is
TOML; start from `samples/overrides.example.toml`, and keep it beside the operator's
notes, never in a repository: a real one names titles from the operator's lists.

```toml
schema = 1

[[overrides]]
medium = "book"
title = "Moby Dick"
ol_work = "/works/OL1000001W"
isbn13 = "978-0-306-40615-7"

[[overrides]]
medium = "film"
title = "Placeholder Film One"
file = "2010/seen.txt"
line = 7
tmdb_id = "1000002"
```

Each `[[overrides]]` entry names a `medium` (`book` or `film`) and a `title`, matched
the way search results are: ignoring case, punctuation, and spacing. It applies to
every row with that title. An entry that also names a source `file` (relative to the
folder parse read, with `/` separators, as the CSV's `source_file` column spells it)
and `line` applies to that row only, and beats a title-only entry for it. Each entry
gives exactly one answer:

| Key | Medium | Meaning |
| --- | --- | --- |
| `isbn13` | book | An ISBN-13, or an ISBN-10 (converted); hyphens and spaces allowed, check digit verified. |
| `ol_work` | book | An Open Library work key, `/works/OL<digits>W`. A book may give `isbn13` and `ol_work` together. |
| `tmdb_id` | film | A TMDb id: a string of 1 to 10 ASCII digits with no leading zero, so `"603"` but not `"0603"`, `"0"` or an 11-digit string. |
| `unresolvable` | either | `true`: the title has no id, and the export sends it by title (and year when known), with no id. |

A title an override settles is not searched; a film override's `tmdb_id` is asked
only for that film's details, which fill its row's TMDb titles. An unknown key, a
missing one, an id the medium cannot take, two answers, or two entries for the same
medium, title, file, and line stop the run before anything is looked up, with a message naming the key, never
its value. An entry that settles no row is an `override-unused` error, so a stale one
is noticed. That includes a title-only entry every row of which a line entry settled:
a line entry always beats it, so as the file stands it can never apply, and leaving it
in would hide a mistake (a `line` set on the wrong entry, say) as quietly as a typo.
Under `--medium book` or `film` an entry for the other medium is still checked, but it
is out of scope for the run rather than unused, so it is neither applied nor reported.

## Statuses

Each row's `resolve_status`:

| Status | Meaning |
| --- | --- |
| `resolved` | Exactly one candidate's title matched and, for a film, nothing held it, or a film's most-voted exact title, single variant match, or single match from the second search was chosen (each of those three with its `most-voted`, `variant`, or `re-searched` review entry). The ids, `creator`, and an empty `year` are filled from it. |
| `override` | An override settled the row; its ids are the override's (none for `unresolvable`). A film's TMDb titles, and an empty `year`, come from TMDb's details for its `tmdb_id`, one request per distinct id, as a resolved film's come from its candidate. A failed details request (a timeout, an HTTP error, an answer that is not JSON, or, with `--cache-only`, an answer not in the cache), or an answer that names no usable film, fails nothing: the row stays `override` with its id, its TMDb titles stay empty, and so does an empty `year`. |
| `ambiguous` | Two or more candidates, none with an exact title, or a film's single exact title held as suspect; listed in the review with up to three candidates. |
| `not-found` | No candidate within the year bounds. |
| `failed` | A search request failed (a timeout, an HTTP error, an answer that is not JSON, or, with `--cache-only`, an answer not in the cache). Failures are never cached, so a re-run tries again. A failed credits request (`--directors`) fails nothing: it only leaves that film's director blank. Nor does a failed details request for an override's id. |

## Review kinds

`review.md` opens with a count line, `<n> review items, <e> of them errors.` (`1 review
item` and `1 of them an error` for a count of one), then has
one table per kind that occurred, in the order of the table below, so errors come
first; each heading is the kind and its count, with `, error` added for an error kind.
An entry covers the rows of one lookup, which is one title in one pair of years (the
work's and the date's), so a title read or seen in two years can have two entries, and
an override for one of them is best written per row (`file` and `line`): a title-only
override settles every row of the title, including ones resolve settled on its own.
`retrolog_review` writes them that way. An entry names the medium, the title, each row as
`<source_file>:<source_line>`, a short detail, and the candidates, each as its title
(with its original title when that differs), year, creator, id, ISBN, and, for a film
whose vote count TMDb gave, `<n> votes`. `review.json` holds the same entries, ordered
by medium, title, and kind, with each candidate's fields spelled out.

| Kind | Error | Meaning | What the operator does |
| --- | --- | --- | --- |
| `override-unused` | yes | An override settled no row. It is titled by its title as matched (lower case, no punctuation). | Fix its title, `file`, or `line`, or delete it. |
| `override-mismatch` | yes | An override's `file` and `line` hold rows, but none with its medium and title. It was not applied, and the row was looked up. | Fix its `line` or `title`. |
| `failed` | yes | A lookup failed; the detail names the service and how. | Re-run; settle it by an override if it keeps failing. |
| `ambiguous` | no | No single exact title; the candidates are listed. | Add an override with the right id, or `unresolvable`. |
| `suspect-exact` | no | A film's single exact title held rather than settled; the detail names every reason, and the candidate is listed. The row stays `ambiguous`, with no ids, so export holds it as it does any `ambiguous` row. | Confirm by adding an override with the listed id, give the right id, or `unresolvable`. |
| `not-found` | no | Nothing within the year bounds. | Add an override, or accept it: export sends it by title (and year when known), with no id. |
| `most-voted` | no | A film resolved by most votes; the detail gives the top two counts, and the exact titles are listed, most-voted first. | Check the choice; if it is the wrong film, add an override with the right id. |
| `variant` | no | A film resolved under a title variant; the detail names it, and the match is listed. | Check the match; if it is the wrong film, add an override with the right id. |
| `re-searched` | no | A film resolved by a second search, for its title without a leading article; the match is listed. | Check the match; if it is the wrong film, add an override. |
| `no-isbn` | no | A book resolved with no English edition carrying an ISBN. | Add an `isbn13` override, or accept it: export sends title and author. |
| `override-applied` | no | An override settled these rows; the detail names its keys. | Nothing; it records the change. |

## Outputs

| File | Holds |
| --- | --- |
| `<medium>-<year>.csv` | Each of parse's CSVs, under its own name, every row in its own place. |
| `review.json` | `schema_version` (`1`), `medium` (the run's `--medium`), `counts` (every kind, zero included), and `entries`. Each candidate carries `votes`: TMDb's vote count, or `null` when TMDb gave none and for every book. |
| `review.md` | The same entries as tables, one per kind, for reading, each known vote count as `<n> votes`; after a single-medium run, a line saying which medium it covers. |
| `cache` | A folder: every answer a service gave, one JSON file per request URL, named by the URL's SHA-256. |
| `.retrolog_resolve` | A marker that lets a later run reuse this directory. |

Each CSV keeps parse's columns and every cell in them exactly as parse wrote it, apart
from `creator` and an empty `year`, which a resolved row fills from its candidate,
and an override row with a `tmdb_id` fills its empty `year` from that film's details.
Six columns follow:

| Column | Meaning |
| --- | --- |
| `resolve_status` | One of the statuses above. |
| `isbn13` | A book's ISBN-13, else empty. |
| `ol_work` | A book's Open Library work key, such as `/works/OL1000011W`, else empty. |
| `tmdb_id` | A film's TMDb id, else empty. |
| `tmdb_title` | A film's title on TMDb: a resolved film's from the candidate resolve chose, an override film's from TMDb's details for its `tmdb_id`. Empty for a book, an unresolved row, an `unresolvable` override, and an override whose details could not be read. |
| `tmdb_original_title` | That film's original title on TMDb, empty in the same cases. `retrolog_export` checks the account by both TMDb titles as well as the list's, and refuses a resolved CSV that lacks these two columns. |

Each file is replaced atomically. A re-run removes every `book-*.csv` and `film-*.csv`
it did not write this time, so a CSV parse no longer writes does not linger; a link of
that name is removed, not followed, and no folder or other file is touched. A
`--medium book` or `film` run removes only its own medium's CSVs.

### One medium at a time

`--medium book` or `--medium film` resolves one medium of a parse folder that holds
both, for an operator importing one service's list before the other's. The run reads
only that medium's CSVs (the other's are not opened, so a damaged one cannot stop it),
sends no request to the other medium's service, and leaves the other medium's CSVs in
`--out` from an earlier run exactly as they were. A book run needs no TMDb token.

`review.json` and `review.md` are rewritten with this run's entries only. They are not
merged with an earlier run's review: the other medium's entries were checked against
an overrides file and a cache that may since have changed, and the exit code counts
only what this run checked, so the review must not list errors the run never looked
at. `review.md` says so in a line under its count, and `review.json`'s `medium` names
the run's choice; re-run the other medium, or `all`, to review it again.

## The cache

Every answer is cached under `<out>/cache/` before it is used, so a re-run after
editing the overrides sends only what no earlier run had answered: a new override's
`tmdb_id` details, the search for a title whose override was removed, and any request
that failed, since a failed request is never cached. A run that stopped partway
resumes where it left off. `--refresh` asks again for every lookup and rewrites its entry, for
when a service's catalog has changed; it skips only what earlier runs cached, so a request
that repeats within the run (the search for a film logged in several years, and its
credits) is still sent once;
`--cache-only` sends no request at all, so a run can be repeated with no network and
no token. The cache quotes the operator's titles inside the search answers, so it is
as private as the CSVs; deleting it costs only the requests to rebuild it.

## Where the output may go

`--out` is checked, and created with its `.retrolog_resolve` marker, before any lookup:
it is refused inside `--parsed` (by path and by file identity), anywhere inside a git
work tree, when it is a file, and when it is a folder that holds files but not this
tool's marker. `--parsed` itself is only read.

## Limits

- **A held film needs an override even when it was right.** A real film with no release
  date, no votes, or only its original title on the list stays `ambiguous` until the
  operator confirms it; `--vote-floor 0` lifts the votes signal alone. The signals only
  catch what looks wrong: a dated, voted film of the same title as the wrong one still
  settles, and only reading the list can tell.
- **No spelling is guessed at.** There is no fuzzy matching: a title spelled
  differently from the catalog's (`Colour` for `Color`, a subtitle left off) is
  `ambiguous` or `not-found`, and an override settles it. A film's six variants and
  its second search are the only loosening, and each is listed for review.
- **Only a leading article is searched again.** The other five variants only compare
  the one search's answers; a title TMDb's search misses under an ampersand, a note, a
  numeral, a possessive, or a number word is not searched again under it.
- **Hyphenated number words are not read as one number.** The number-words variant
  reads each word alone, so `twenty-one` becomes `20-1` and never meets `21`.
- **A remake can still collide.** A remake released before the row's year survives
  the year filter beside the original; when its votes do not settle it, or settle it
  on the wrong film, only the operator can tell which was seen, by an override.
- **One edition per book.** The ISBN recorded is one English edition's, chosen by the
  read year; it is not necessarily the copy the operator read.
- **Directors help choose, nothing more.** They are looked up only for the films
  `--directors` names, and a resolved film is matched by its TMDb id, not its director.
  So a credits request that fails leaves the director blank and the film's status as
  it was; an ambiguous film's detail then ends `directors unavailable`, and a re-run
  asks again.
- **An override's details help match, nothing more.** An override row is matched by
  its `tmdb_id`, and its TMDb titles only let `retrolog_export` check the account by
  the titles TMDb knows it under. So a details request that fails, or an answer that
  names no usable film, leaves the row `override` with its id and its titles empty
  (export then lists it `override-unchecked`). A failed request, an answer that is
  not JSON included, is never cached, so a re-run asks again; an answer that decodes
  as JSON but names no usable film is cached like any other, so only `--refresh`
  asks again.
- **A token is needed for any film row** unless `--cache-only` is given, even when
  every film title is settled by an override, whose id is still asked for its
  details.
- **The token file's mode is checked on POSIX only.** On Windows the tool cannot tell
  whether others can read the file; keep it in a folder only the operator can open.
- **The token file holds one variable.** It is not a general env file: any other
  line is refused rather than ignored, so a pasted contact or a typo is noticed.

## Samples

`samples/` holds a builder for a synthetic parse folder, overrides file, and canned
cache, and the output this tool writes for them (`samples/expected/`), which shows
every status and every review kind, and the output of a `--medium film` run on the same
inputs (`samples/expected-film/`). `samples/README.md` gives the commands to build
the inputs, run the tool with `--cache-only` (no network, no token), and compare.
