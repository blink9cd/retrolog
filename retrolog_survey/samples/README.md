# retrolog_survey samples

A synthetic source tree and the report `retrolog_survey` writes for it, runnable by
hand. Nothing here comes from a real list: `build_sample_tree.py` writes every file
with the same builders the tests use, and every title is a placeholder such as
`Film A` or `Book A`.

## Regenerate and compare

From the tool's folder (the one holding `retrolog_survey.py`):

```bash
work="$(mktemp -d)"
python3 samples/build_sample_tree.py --out "$work/sample_tree"
python3 retrolog_survey.py --path "$work/sample_tree" --out "$work/sample_out" --no-convert
diff "$work/sample_out/report.json" samples/expected_report.json
```

Any Python 3.12 or later with `python-docx` installed works in place of the
`python3` shown. `mktemp -d` gives a fresh, empty folder each time; it must not
lie inside a git work tree, or the survey refuses `--out`.

The survey prints, and exits `0`:

```text
books: 2 files, 2 lines, 0 errors
films: 3 files, 12 lines, 0 errors
```

`diff` prints nothing: the report matches byte for byte.

## What the tree holds

| File | Shows |
| --- | --- |
| films/notes.txt | Classic Mac OS text: `mac_roman`, carriage-return line endings, a blank line, and lone letters at line ends. |
| films/rated.docx | One paragraph per rating convention: a Wingdings `w:sym` thumb, a color, a highlight, a shading, an emoji, a picture, a table cell, and a Wingdings text run. |
| films/older.doc | A stand-in `.doc`, counted only, because `--no-convert` skips conversion. |
| films/.DS_Store | A hidden file, skipped: it appears nowhere in the report. |
| books/notes.txt | Windows text: `cp1252`, CRLF line endings, curly quotes. |
| books/cover.jpg | Any other suffix: counted as `other`, never opened. |

## What you should see

- `converters` reads `skipped` for both: `--no-convert` keeps the sample the same on
  every machine, whether or not textutil or LibreOffice is present.
- The films group's `colored` samples read `Film B [#00B050]`, `Film C [hl:yellow]`
  and `Film D [shd:FF0000]`, and its `symbol` samples include
  `Film A [Wingdings/F043 #0070C0]`.
- `images` lists the document's one picture once, by its `sha256_12`.
- The report holds no absolute path: every `file` is relative to the tree.

`../tests/test_cli.py` runs the same three steps and compares the result with
`expected_report.json`, so the sample cannot drift from the tool.
