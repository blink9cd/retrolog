# retrolog_probe report

Read 2 import files: 6 rows, 1 of them by title, which carries no tmdbID and is not checked. 4 distinct tmdbIDs: 2 present, 2 missing, 0 unknown; 4 answered from the cache, 0 requests sent.

Row counts each file's data rows from 1, the header not counted. It is not a line number: a review can span lines.

## Missing (3 rows)

| File | Row | tmdbID | Title | Year |
| --- | --- | --- | --- | --- |
| letterboxd-001.csv | 2 | 100002 | Placeholder Film Two | 1948 |
| letterboxd-001.csv | 4 | 100004 | Placeholder \| Film Four | 1960 |
| letterboxd-002.csv | 2 | 100002 | Placeholder Film Two | 1948 |

## Omit entries (2 films)

To leave the missing films out of this and every later export, add these entries to the export config's `[letterboxd]` settings and run export again into a new folder.

```toml
[[letterboxd.omit]]
title = "Placeholder Film Two"
year = 1948
[[letterboxd.omit]]
title = "Placeholder | Film Four"
year = 1960
```

## Unknown (0 rows)

None.

Leave the missing films out of the import: add the omit entries and run export again, or delete their rows from the import file before uploading it. Run the check again later for the unknown ones.
