# retrolog_restore samples

Two synthetic account exports and the restore file and list `retrolog_restore` writes
for them, runnable by hand with no network and no account. Nothing here comes from a
real account: `build_restore_inputs.py` writes both exports, every title is a
placeholder such as `Placeholder Film One`, every `Letterboxd URI` is made up, and
every rating, tag, and review is invented.

## Regenerate and compare

From the tool's folder (the one holding `retrolog_restore.py`):

```bash
work="$(mktemp -d)"
python3 samples/build_restore_inputs.py --out "$work"
python3 retrolog_restore.py --backup "$work/account/backup.zip" --current "$work/account/export-1" --out "$work/restore"
diff -r -x .retrolog_restore "$work/restore" samples/expected
```

Any Python 3.12 or later works in place of the `python3` shown, with nothing
installed into it: the builder and the tool import the standard library only.
`mktemp -d` gives a fresh, empty folder each time; it must not lie inside a git work
tree, or the tool refuses `--out`. The `-x` option leaves the tool's marker out of the
comparison.

The tool exits `0` and prints:

```text
entries restored: 4
to like again: 1
see restore.md for every restored entry and the films to like again
```

`diff` prints nothing: `letterboxd-restore.csv` and `restore.md` match byte for byte.

## What the inputs hold

`account/backup.zip` is the export taken before the deletion, as Letterboxd's `.zip`,
with `diary.csv`, `reviews.csv`, and `likes/films.csv`. `account/export-1/` is the
export taken after it, unpacked: its `diary.csv` keeps the backup's first entry and
adds one logged since, its `likes/films.csv` still likes Film One and Film Five, as
deleting a diary entry leaves the heart, and its `reviews.csv` is empty, as it is
never read.

| Backup diary entry | Shows |
| --- | --- |
| `Placeholder Film One` | Still in the later export by its URI: not restored, and left out of `restore.md` though it is liked and reviewed. |
| `Placeholder Film Two` | A rewatch (`Yes`, written `true`), two tags in one cell, and a review with a comma, double quotes, and a line break, all quoted in the import file. The backup also holds a review of this film on an earlier date: another watch, not joined. Liked in the backup only, so marked to like again. |
| `Placeholder Film Three` | No rating, tag, or review: empty cells. |
| `Placeholder Film Four` | No `Letterboxd URI`, so matched by its name, year, and watched date, which the later export lacks. |
| `Placeholder Film Five` | One tag and a short review, which carries the entry's `Letterboxd URI` and so joins by it. Liked, and still liked in the later export, so not marked to like again. |

The later export's `Placeholder Film Six` was logged after the backup, so it is not in
the backup and is never restored.

The two refusals the sample cannot show, a `Rewatch` cell other than `Yes` or empty
and an entry matching two reviews, stop the run with exit `2` before anything is
written; `../tests/test_restore_cli.py` covers both. That test file also runs the
three steps above and compares the result with `expected/`, so the sample cannot
drift from the tool.
