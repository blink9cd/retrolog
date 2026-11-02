# retrolog_resolve review

9 review items, 1 of them an error.

This run resolved film rows only; any book CSV files in this folder are from an earlier run, and this review does not cover them.

## override-mismatch (1, error)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | Placeholder Film Five | 2010/seen.txt:12 | overrides[5] names another title for this line | |

## ambiguous (1)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | Placeholder Film Two | 2010/seen.txt:10 | 2 exact titles | Placeholder Film Two, 1999, Director Four, 1000004; Placeholder Film Two, 2008, Director Five, 1000005 |

## suspect-exact (1)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | Placeholder Film Seven | 2010/seen.txt:16 | one exact title, held: no release date; 0 votes, under the floor of 1 | Placeholder Film Seven, Director Ten, 1000016, 0 votes |

## not-found (1)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | Placeholder Film Three | 2010/seen.txt:11 | no candidate within the year bounds | |

## most-voted (1)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | Placeholder Film Six | 2010/seen.txt:13 | most votes: 1200 against 40 | Placeholder Film Six, 1994, Director Six, 1000010, 1200 votes; Placeholder Film Six, 2003, Director Seven, 1000011, 40 votes |

## variant (1)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | Placeholder Story II | 2010/seen.txt:14 | matched ignoring a sequel numeral | Placeholder Story 2, 2006, Director Eight, 1000013, 85 votes |

## re-searched (1)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | The Placeholder Hour | 2010/seen.txt:15 | found by searching without a leading article | Placeholder Hour, 2002, Director Nine, 1000015 |

## override-applied (2)

| Medium | Title | Rows | Detail | Candidates |
| --- | --- | --- | --- | --- |
| film | Metropolis | 2010/seen.txt:3 | overrides[1]: tmdb_id | |
| film | Placeholder Film One | 2010/seen.txt:7 | overrides[2]: tmdb_id | |
