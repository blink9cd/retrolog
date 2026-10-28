# retrolog_survey

Survey a folder of old book and film lists before importing them. Lists kept for
years drift between formats and conventions: plain text from different machines and
encodings, Word 97 `.doc` files, newer `.docx` files, and ratings marked by a symbol
font thumb, a color, a highlight, a small picture, an emoji or a single letter. This
tool reads the whole folder without changing it and reports which of those
conventions each part of the folder uses, with a few sample lines of each kind. A
later parse step reads the report to decide how to read each group of files.

**The report quotes real text from the source files.** Sample lines, and the
annotations around them, are copied from the lists themselves. Treat the report as
being as private as the lists: do not post it, attach it to a public issue, or commit
it to a repository. The tool refuses to write it inside a git work tree for that
reason (see "Where the report may go").

## Usage

```bash
python retrolog_survey.py --path path/to/lists --out path/to/reports
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--path` | yes | | Source tree to survey (read only). |
| `--out` | yes | | Report directory; refused inside `--path` or a git work tree. |
| `--group-depth` | no | `1` | Directory levels that form a report group. |
| `--exclude` | no | none | Glob over the relative path, case-insensitive, repeatable; matching files are counted as excluded and not read. |
| `--samples` | no | `5` | Sample lines per group per line kind (at least 1). |
| `--no-convert` | no | off | Skip `.doc` conversion (`.doc` files are then counted only). |
| `--max-file-mb` | no | `50` | Per-file size limit (text bytes, .doc file size or Word uncompressed size). |
| `--timeout` | no | `120` | Seconds per converter or git call. |

Prerequisites: Python 3.12 or later with `python-docx`. Converting `.doc` files needs
macOS, whose built-in `textutil` does it; LibreOffice is optional. Text and `.docx`
files need neither. Run order: this survey first, then the parse step that reads
`report.json`.

On success the tool prints one line per group to standard output, with counts only:
`<group>: <n> files, <n> lines, <n> errors`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Report written, and every file was surveyed. |
| `1` | Report written, but one or more files could not be surveyed. Each is listed under its group's `errors`, and a count goes to standard error. |
| `2` | Bad arguments, `--path` missing or not a directory, `--out` or the temporary directory refused (nothing was written), or the report could not be written: `<program>: could not write the report: <class>: <reason>`, without a path. |

Each file is surveyed on its own, so one damaged file never stops the run.

## What it reads, and what it never does

The source tree is only read. No file under `--path` is created, changed, renamed or
locked: a `.doc` is copied out before any converter sees it (see "Converting `.doc`
files"), and every output goes to `--out`.

- **Groups.** A file's group is its first `--group-depth` directory names, so with
  the default of `1` each top-level folder is one group. A file directly in `--path`,
  or every file when the depth is `0`, belongs to group `.`.
- **Hidden names** (any name starting with `.`, such as `.DS_Store` and the `._*`
  files a non-Apple volume collects) are skipped, and a hidden folder is not entered.
- **Symbolic links** are counted as `symlink` and never followed.
- **Excluded paths** (`--exclude`) are counted as `excluded` and never opened.
- **Other suffixes** are counted as `other` and never opened. Only `.txt`, `.doc` and
  `.docx` are read.
- **Unreadable entries** (a folder that cannot be listed, a file whose size cannot be
  read) are listed under `errors` with the error's class name.
- Paths in the report are relative to `--path`, use `/`, and are Unicode NFC.
- No error message carries an absolute path: each is an exception class name and
  its short reason, such as `PermissionError: Permission denied`.

## Where the report may go

`--out` is checked before anything is read, and refused when it is:

| Message | Rule |
| --- | --- |
| `inside the source tree` | `--out` is `--path` or lies under it, compared both by resolved path and by file identity (a case-insensitive volume can name one folder two ways). |
| `inside a git work tree` | git says the location is in a work tree, or a `.git` entry sits in it or any folder above it. A git that is missing, times out or fails never counts as "not a repository". |
| `not a directory` | `--out` exists and is a file. |
| `not empty and not a previous survey output` | `--out` holds files but no `.retrolog_survey` marker. |
| `cannot inspect the output location` | The location cannot be examined, for example a permission error or a symbolic-link loop. |

A refusal prints `<program>: refused --out: <message>` to standard error, without the
path, and exits `2`.

**What the check cannot see.** A bare repository, and a work tree whose git directory
is kept elsewhere (set up with `--git-dir` and `--work-tree`), are not detected. Nor
is a folder that a cloud drive synchronizes, or one a dotfile manager tracks: such a
folder is published off the machine, but nothing in it says so. Choose an `--out`
outside every cloud-synchronized and dotfile-managed folder.

**Names stored decomposed.** Paths are opened by their NFC form. On a filesystem that
stores decomposed (NFD) names and compares them byte for byte (not macOS APFS or
HFS+, which ignore the difference), such a file is reported under `errors` as
`FileNotFoundError`. A name that is not valid UTF-8, which some non-Apple volumes
allow, is reported with each undecodable byte written as a `\udcXX` escape.

**The temporary directory.** `.doc` conversions run in a folder under the system's
temporary directory (`TMPDIR`). When that lies inside `--path` or `--out` the run is
refused before anything is created, with
`<program>: refused the temporary directory: it lies inside --path or --out`, and
exits `2`.

A re-run may reuse its own earlier `--out`, which holds the `.retrolog_survey`
marker. It replaces `report.json` and `report.md` and removes images the new report
no longer names.

## Outputs

| File | Holds |
| --- | --- |
| `.retrolog_survey` | The marker that lets a later run reuse this folder. |
| `report.json` | The report, for the parse step: sorted keys, two-space indent, UTF-8. |
| `report.md` | The same report laid out for reading. |
| `images/<sha256_12>.<ext>` | Each distinct image found in a Word document, once. The extension is the declared type's when that is a raster image type; any other type, SVG and EMF included, is written as `.bin`, so no image written is one a browser would run. |

Every file is written to a temporary name in the same folder and then renamed, so a
reader never sees half a file.

## Text files: the encoding rule

Old text files carry no record of their encoding, so it is guessed from the bytes:

1. A byte-order mark decides (`utf-8-sig` or `utf-16`), if the whole file decodes so.
2. Every byte below `0x80`: `ascii`.
3. Decodes as strict UTF-8: `utf-8`.
4. Otherwise `cp1252` (Windows) or `mac_roman` (classic Mac OS). A byte cp1252 leaves
   undefined means `mac_roman`. Otherwise each decoding is scored by how many curly
   quotes, dashes, ellipses and bullets it produces, and the higher score wins. A tie
   goes to `mac_roman` when the line endings are carriage returns only, and is
   otherwise `cp1252`, counted under `ambiguous_encodings` for a person to check.

Line endings are counted as `crlf`, `cr` and `lf`. Lines are split on those three
only, so a form feed inside a line stays in the line.

## Word documents

A `.docx` is read run by run: font, character style, color (`RRGGBB`, or
`theme:<name>` for a theme color), highlight, shading, symbol characters (`w:sym`,
reported as `<font>/<code>`) and pictures. Paragraphs are numbered in document order
with table cells read where the table stands, and that number is the `line` of a
sample. Content controls are read in place, and where a document offers two
renderings of the same content only the preferred one is read.

**Text boxes.** The runs inside a text box are reported as runs of their own, in the
paragraph that anchors the text box, after the run that holds it. A picture inside a
text box is counted once, in the text box's run, not again in the anchoring run.

The archive is decompressed under `--max-file-mb`, counting the bytes actually
produced rather than the sizes the archive declares, so a file that expands without
bound is reported as `BadDocx: expands past limit` rather than read. An archive
declaring more members than any Word document holds (the cap is `MAX_MEMBERS` in
`src/survey_docx.py`; a Word document has tens) is reported as
`BadDocx: too many members` before its member list is read. A package with a part
declaring a document type, which no part python-docx parses as XML carries and which is
how an entity-expansion bomb or an external-file read is built, is reported as
`BadDocx: document type declaration` before any of it is parsed as a document. Only a
part the package's `[Content_Types].xml` declares an image is exempt: Word stores an
inserted SVG that way, often with a doctype, and python-docx never parses an image as
XML. The exemption follows the declared type, not the part's name, because python-docx
parses a main document part as XML whatever its name ends with.

### How a sample line is described

A sample from a Word document is its text, with each formatted run followed by its
annotations in brackets:

| Annotation | Meaning |
| --- | --- |
| `Wingdings/F043` | A `w:sym` symbol character: its font and code. |
| `Wingdings 'C' U+0043` | Text set in a symbol font, where the letter stands for a glyph. |
| `#0070C0` or `theme:accent1` | A font color. |
| `hl:yellow` | A highlight. |
| `shd:FF0000` | A shading fill. |
| `style:<name>` | A character style (a color may come from it). |
| `img:<sha256_12>` | A picture, named by its file in `images/`; plain `img` for a linked or unresolvable one. |

So a green title reads `Film B [#00B050]`, and a title followed by a blue thumb from
Wingdings reads `Film A [Wingdings/F043 #0070C0]`.

### Line kinds

Each line or paragraph is given one kind, the first that applies, in this order:
`blank`, `symbol` (a symbol character or symbol-font run), `image`, `colored` (a
color, highlight or shading on a run that shows something), `emoji`, `letter_code`
(a single letter alone at the start or end, which also catches a title starting with
the word "A"), and `unclassified`.

## Converting `.doc` files

A Word 97 `.doc` cannot be read directly, so it is converted to `.docx` first.

- **Copy before converting.** The `.doc` is copied into a private temporary folder,
  and the converters read only the copy and write only there, so no lock file,
  temporary file or output can appear beside the source. That folder is the
  system's temporary folder, never under `--path` or `--out`, and it is removed
  before the tool exits, on success or on error.
- **Both converters.** macOS's `textutil` always runs. LibreOffice's `soffice` runs
  headless too when it is installed (found on `PATH` or in an `Applications` folder),
  with a private profile in the temporary folder: a headless call that shares the
  normal profile with an open LibreOffice window is handed to that window and never
  runs.
- **Which output is surveyed.** LibreOffice's when it succeeded, otherwise
  textutil's. When neither produced a readable `.docx`, the file is listed under
  `errors` as `ConversionFailed`.
- **What each kept.** Each converter's output is reduced to a set of formatting
  features (`font:`, `style:`, `color:`, `hl:`, `shd:`, `sym:`, `image:`). Under
  `conversions`, the report lists the features only textutil kept, only LibreOffice
  kept, and both kept, with each converter's status (`ok`, `failed`, `absent`) and
  detail (an exit code or an exception class name).
- **No sandbox.** Both converters parse the untrusted `.doc` natively, as the user
  running the survey and with no sandbox around them; `bounded_run` bounds how long
  each may run (`--timeout`), not what it may do.
- `--no-convert` skips all of this: a `.doc` is counted and nothing else, and the
  report's `converters` reads `skipped` for both.

### What textutil keeps

Measured on macOS with a test document carrying each marking, converted
`.docx` to `.doc` with textutil and back:

- **Kept:** direct run colors, and a run whose font is Wingdings.
- **Added:** `font:Times`, which textutil writes on every run.
- **Lost:** the `w:sym` symbol character (it becomes an empty run that keeps only
  its color), highlights, shading and embedded pictures. All text survived, emoji
  included, and table cell text came out as plain paragraphs.

That measures textutil's round trip, not how it reads a `.doc` saved by Word: part
of the loss happens when textutil reads the `.docx` on the way in. A `.doc` made by
Word may keep more or less. The `conversions` section of a real survey shows what
each converter kept on the real files; when textutil loses markings that matter and
LibreOffice is not installed, install it and run the survey again.

## The report

`report.json` holds `schema_version` (now `1`; any change to the layout raises it),
the `options` it was run with (`group_depth`, `samples`, `converters`), converter
availability (`present`, `absent` or `skipped`), and one entry per group, sorted by
name:

| Key | Holds |
| --- | --- |
| `files` | Counts by kind: `txt`, `doc`, `docx`, `other`, `symlink`, `excluded`, `unreadable`. |
| `encodings`, `ambiguous_encodings` | Text files by guessed encoding, and how many guesses were a tie. |
| `line_endings` | `crlf`, `cr` and `lf` counts over the text files. |
| `line_kinds` | Lines and paragraphs by kind. |
| `fonts`, `symbols`, `colors`, `highlights`, `shadings`, `styles` | Word runs by each signal. `symbols` keys a symbol-font character as `<font>/<code>`; `styles` keys `paragraph:<name>` and `run:<name>`. |
| `emoji`, `letter_tokens`, `non_ascii` | Emoji codes, lone start and end letters (lowercased), and every non-ASCII character by code and name. |
| `images` | Each distinct picture: type, size, `sha256_12`, and how many runs show it. |
| `conversions` | One entry per converted `.doc` (see above). |
| `samples` | Per line kind, the first `--samples` lines, taken in path order and then line order. Every kind is sampled except `blank`, so free-form notes show up under `unclassified`. |
| `errors` | `{"file", "error"}` for each file that could not be surveyed. |

Samples from text files are the lines as decoded; samples from Word documents are
described as above. `report.md` shows the same content as tables and lists. Text taken
from the files is escaped there so it cannot change the layout: backslashes,
backticks, angle brackets, pipes and the `(` of a `](` link are backslash-escaped,
each line break becomes `↵`, and paths sit in code spans fenced past any backtick
they contain. `report.json` holds the text unchanged.

## Samples

`samples/` holds a synthetic source tree builder and the report the tool writes for
it; `samples/README.md` gives the commands to regenerate and compare.
