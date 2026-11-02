# retrolog_probe

Find the films in `retrolog_export`'s Letterboxd import files that Letterboxd does not
have, before importing them. A Letterboxd import matches a row by its `tmdbID`, but
Letterboxd holds only part of TMDb's catalog, so a row whose film it lacks lands as
"no match" in the import preview. This tool asks Letterboxd about each id first and
writes a report of every row whose film it lacks, by file and row, with the
`letterboxd.omit` entries that leave those films out of the export, so they never reach
an import.

It is optional, and it is the one tool besides `retrolog_resolve` that reaches a
network service.

## Letterboxd's terms

**Letterboxd's terms of use forbid automated access to the site without its
permission. This tool sends one request per film to Letterboxd; whether to run it is
your own decision.** The same statement is in its `--help`.

What it does, so the decision is an informed one:

- It runs only when you start it. Nothing schedules it, and no other tool calls it.
- It sends only the ids: one HEAD request for `https://letterboxd.com/tmdb/<id>/` per
  distinct id, setting only a `User-Agent: retrolog-probe/1.0` header (Python's HTTP
  library adds only `Host`, `Connection: close`, and `Accept-Encoding: identity`). No
  cookie, no account, no login, and no referrer is sent, and no answer is kept but the
  verdict.
- It sends one request at a time, at least `--interval` seconds apart (two by default,
  never less than one), and asks about one control film before the others.
- A 429 ("too many requests") is never read as missing. The tool waits and asks about
  the same id again, longer each time; after the last wait it stops, and every id not
  yet settled is reported as unknown, to be asked about on a later run.

## Usage

```bash
python retrolog_probe.py --files path/to/export-out --out path/to/notes/probe-report.md --cache path/to/notes/probe-cache.json
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--files` | yes | | `retrolog_export`'s `--out` folder, holding its `letterboxd-NNN.csv` import files (read only). |
| `--out` | yes | | The report to write. Refused when it exists, when its folder does not, inside the `--files` folder, or inside a git work tree: it names your films. |
| `--cache` | no | none | A verdict cache: ids already settled are not asked about again, and each new settled verdict is added as it arrives. Left out, nothing is cached. |
| `--refresh` | no | off | Ask about every id again, whatever `--cache` holds, still writing it. Needs `--cache`. |
| `--cache-only` | no | off | Send nothing: an id not in `--cache` is reported as unknown. Needs `--cache`; not with `--refresh`. |
| `--control-id` | no | `19` | A TMDb id Letterboxd is known to have, asked about before any other. Unless it comes back present, the run stops and calls nothing missing. |
| `--interval` | no | `2.0` | Seconds between any two requests, the control and every retry included. At least `1.0`. |
| `--timeout` | no | `30.0` | Seconds each request may wait on the network. |

Prerequisites: Python 3.12 or later, with nothing installed into it: the tool imports
the standard library only. It imports modules from its sibling `retrolog_export`,
`retrolog_resolve` (the TMDb id rule only, none of its network code), `retrolog_parse`,
and `retrolog_survey` folders, and finds the shared libraries it needs
(`stdio_utf8`, `help_formatter`, and `bounded_fetch` for the request) through the
project's one bootstrap, the `retrolog_bootstrap` folder beside its own. A network
connection to `letterboxd.com` is needed unless every id is in `--cache`.

## What it reads

Every `letterboxd-NNN.csv` in `--files`, in number order: the import files
`retrolog_export` writes. Nothing else there is read, and nothing there is written.
The run is refused (exit `2`) when the folder is missing or holds no import file, when
a file's header is not export's exactly, when a row has more or fewer cells than the
header, and when a row's `tmdbID` is not one to ten ASCII digits with no leading zero:
an id is pasted into the request's address, so nothing else may reach it. The refusal
names the file and row, never the cell's value. A cell may be as long as export writes
one: the files are read under export's raised CSV field limit, so a long review is
read rather than refusing its whole file.

A row with an empty `tmdbID` goes to Letterboxd by title and year, so it has no id to
check: it is counted ("by title") and skipped. Each distinct id is asked about once,
however many rows carry it (a film watched twice is two rows), and every row carrying
it is reported.

**A row number counts the file's data rows from 1, the header not counted.** It is not
a line number: a review can span lines, and a row is one CSV record however many lines
it takes, so a text editor's line numbers drift from it after the first multi-line
review.

## What each answer means

Letterboxd sends `https://letterboxd.com/tmdb/<id>/` on to the film's page when it has
the film, and answers that address with a plain 200 when it does not (measured
2026-10-30: Metropolis's id `19` answered `302` to `/film/metropolis/`, and an unused id
answered `200`, with no redirect). So the request follows no redirect, and:

| Answer | Verdict |
| --- | --- |
| A redirect (3xx) whose `Location`, resolved against the request, is `https://letterboxd.com/film/...` | `present` |
| `200`, no redirect | `missing` |
| `429` | asked again after a wait (below); never a verdict |
| Anything else: a redirect elsewhere or with no `Location`, `404`, `403`, a 5xx, a network failure | `unknown`, with a short reason naming the status or the failure's type |

A film page's address must be https, on `letterboxd.com` itself (no port, no other
host, no user name), with a path starting `/film/` and no `..` in it. A reason never
quotes a URL or a header.

### The control

Before the run's first request, the tool asks about `--control-id`, a film Letterboxd
is known to have (Metropolis, 1927, by default). If it does not come back `present`,
the run stops with exit `2`: the check itself is not working, because Letterboxd
answered something the check does not expect (a changed site, a block page), and every
`missing` it gave would be wrong. No report is written then, and the cache keeps only
what it held before the run. (A `--cache` is saved once before the first request, so
one that cannot be written is refused before any id is sent.) A run that sends no request, every id being
cached or `--cache-only` set, asks about no control either. When the control id is also in the import files, its answer
serves for those rows too.

### Pace and back-off

One request at a time, at least `--interval` seconds after the previous one. A 429 is
answered by waiting `30`, `60`, `120`, and then `240` seconds before each next try of the
same id, or as long as the 429's `Retry-After` asks if that is longer, up to `600`
seconds; a missing or unreadable `Retry-After` leaves the schedule's wait, which
already escalates. A 429 after the last of those waits stops the run: that id and every id not yet asked about are `unknown` ("rate limited;
run again later"), the report is written, and the exit code is `1`. The schedule starts
over for each id.

## The cache

`--cache FILE` keeps what a run settled, so a rerun asks only about the rest. It is one
JSON object and nothing else:

```json
{
  "schema": 1,
  "verdicts": {
    "19": "present",
    "100002": "missing"
  }
}
```

Only `present` and `missing` are stored, never `unknown`, and only ids and verdicts:
no title, row, or date. The file is rewritten whole and atomically after each settled
verdict, so a run that is interrupted, or stopped by a 429, keeps every verdict it
reached. A missing file is an empty cache; its folder must exist. Anything but the
layout above is refused (exit `2`) rather than discarded, so a damaged cache never
silently costs the operator its progress. Entries for ids the current files do not
hold are kept.

`--refresh` asks about every id again and rewrites what it learns; `--cache-only`
sends nothing, and an id the cache does not hold is `unknown` ("not in the cache").

## The report

`--out` is one Markdown file, written atomically, never over an existing file. It
holds:

- a count paragraph: the import files read, their rows, and how many went by title;
  the distinct ids and how many are `present`, `missing`, and `unknown`; how many came
  from the cache; and how many requests were sent;
- the note on row numbers above;
- **Missing**: one table row per import row whose film Letterboxd lacks, with its
  file, row, `tmdbID`, `Title`, and `Year`;
- **Omit entries**, when a film is missing, under a heading that counts the missing
  films: one `[[letterboxd.omit]]` table per missing film, or per title and year that
  missing films share, ready to paste into the export config (`retrolog_export.md`,
  "The omit list"), each naming its film by the `Title` and `Year` of the film's first
  row. Only a row that gives an entry claims that title and year, so a film's later
  row, under another title, never takes another film's entry. A film with no year
  gets no `year` key. Export matches an entry by its title, ignoring case, spacing, and
  punctuation, and its year, and stops on two entries that match alike, so there is
  never a second entry for one `tmdbID` nor for one such title and year: two missing
  films whose titles differ only that way in one year share the first one's entry. An
  entry names every film of its title and year, so omitting a missing film also leaves
  out a present film of the same title and year;
- **Unknown**: the same table as Missing, with the reason, for every row not settled;
- a closing line: leave the missing films out of the import, by adding the omit entries
  and running export again, or by deleting their rows from the import file, and run the
  check again later for the unknown ones.

A table section with no rows says "None.", and with no missing film there is no Omit
entries section. Each entry's title is a TOML basic string with the backtick escaped
too, so no title can end the string or the code block. Every cell is escaped by the
rule the other tools' review lists use (parse's table-cell escape), so a title cannot end a cell or start
markup. Standard output gets the count line and the report's path, never a title:

```text
4 distinct tmdbIDs: 2 present, 2 missing, 0 unknown (4 from the cache, 0 requests sent)
report: path/to/notes/probe-report.md
```

**The report holds your titles.** Treat it as being as private as the lists it comes
from; the tool refuses an `--out` inside a git work tree for that reason, as the other
tools do. It refuses one inside the `--files` folder as well, as every tool refuses its
output inside its input: a report there could be uploaded with the import files, and
the next export run would delete it. The cache holds no title.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Every film is present; the report is written. |
| `1` | The report is written and lists a missing or unknown film. |
| `2` | Bad arguments; a missing `--files` folder or one with no import file; an import file whose header is not export's, a row of the wrong width, or a `tmdbID` that is not an id; an unusable `--cache`; an `--out` that exists, sits in a missing folder, or lies inside the `--files` folder or a git work tree; the control not answered as present; or the report or cache could not be written. |

## Testing

`test_retrolog_probe.py` runs the suites under `tests/`. The request, the sleep, and the
clock are replaced by fakes in every unit test, so the tests assert what would be sent
(the URL holding only the id, the one header) and which waits were asked for, and none
reaches the network or waits. `tests/test_probe_live.py` is the exception: it asks
Letterboxd about `19` (present) and `999999999` (missing) through the real request, two
seconds apart, and is skipped when `RETROLOG_RESOLVE_OFFLINE` is `1`, the switch that
skips every test of these tools that reaches a network service. The id rule, the
`Location` check, the `Retry-After` reading, and the cache parser are each marked
`# fuzz-target` and have a property-based test.

## Samples

`samples/` holds an invented pair of import files, a cache answering every id in them,
and the report the tool writes for them under `--cache-only`, which sends nothing.
`samples/README.md` gives the commands to regenerate and compare it.
