# retrolog_export review

6 rows of 5 films sent in 2 files; 2 rows cut. 12 review items, 5 of them errors.

## held-ambiguous (1, error)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Two | 2010/seen.txt:10 | resolve found more than one match; settle it with an override |

## held-failed (1, error)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Four | 2010/seen.txt:12 | resolve could not look it up; run resolve again |

## held-split (2, error)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Eight | 2010/seen.txt:15 | other rows of this title have no tmdbID; settle them in resolve with an override, then rerun |
| Placeholder Film Eight | 2010/seen.txt:16 | other rows of this title have a tmdbID; settle these in resolve with an override, then rerun |

## held-no-title (1, error)

| Title | Rows | Detail |
| --- | --- | --- |
| ??? | 2010/seen.txt:17 | its title has no letter or digit, so the account cannot be checked for it; add it by hand |

## on-service (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Five | 2010/seen.txt:13 | already on the account, so not sent |

## override-unchecked (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Eleven | 2010/seen.txt:3, 2019/films.txt:3 | sent by an override's tmdbID, with no TMDb title to check the account by; confirm by hand it is not already there |

## by-title (2)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Seven | 2010/seen.txt:14 | sent by title with no tmdbID; check it in the import preview |
| Placeholder Film Three | 2010/seen.txt:11 | sent by title with no tmdbID; check it in the import preview |

## unrated (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Three | 2010/seen.txt:11 | sent with no rating; rate it by hand |

## undated (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Six | misc/films.txt:2 | sent with no watched date |

## formula-cell (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Film Six | misc/films.txt:2 | its Directors cell starts with a character a spreadsheet reads as a formula; upload the file without opening it in one |
