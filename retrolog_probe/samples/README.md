# retrolog_probe samples

Two import files, a verdict cache answering every id in them, and the report
`retrolog_probe` writes for them, runnable by hand with no network: the run uses
`--cache-only`, so it sends nothing to Letterboxd. Nothing here comes from a real list
or a real answer: `build_probe_inputs.py` writes every file, every title is a
placeholder such as `Placeholder Film Two`, and every id is made up.

## Regenerate and compare

From the tool's folder (the one holding `retrolog_probe.py`):

```bash
work="$(mktemp -d)"
python3 samples/build_probe_inputs.py --out "$work"
python3 retrolog_probe.py --files "$work/files" --cache "$work/cache.json" --cache-only --out "$work/report.md"
diff "$work/report.md" samples/expected/report.md
```

Any Python 3.12 or later works in place of the `python3` shown, with nothing installed
into it: the builder and the tool import the standard library only. `mktemp -d` gives a
fresh, empty folder each time; it must not lie inside a git work tree, or the tool
refuses `--out`.

The tool prints these two lines, the second naming the report it wrote, and exits `1`,
because the sample has missing films:

```text
4 distinct tmdbIDs: 2 present, 2 missing, 0 unknown (4 from the cache, 0 requests sent)
report: <work>/report.md
```

`diff` prints nothing: the report matches byte for byte.

## What the inputs hold

| Row | Shows |
| --- | --- |
| `Placeholder Film One`, `Placeholder Film Five` | `present` in the cache: not in the report. |
| `Placeholder Film Two`, row 2 of the first file and row 2 of the second | One film watched twice: its id is settled once, and both rows are listed as missing, since both must be deleted. |
| `Placeholder Film Three` | A by-title row, with no `tmdbID`: counted and not checked. |
| `Placeholder \| Film Four` | `missing`; its title's pipe is escaped in the report's table. |

`../tests/test_probe_cli.py` runs the same steps and compares the result with
`expected/report.md`, so the sample cannot drift from the tool.
