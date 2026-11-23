# retrolog_export review

9 rows of 9 books sent in 1 file; 2 rows cut. 13 review items, 1 of them an error.

## held-ambiguous (1, error)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Ten | 2010/books.txt:10 | resolve did not settle on one match; settle it with an override |

## omitted (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Nine | 2010/books.txt:11 | named in goodreads.omit, so not sent; remove the entry to consider it again |

## on-service (2)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Eleven | 2010/books.txt:12 | already read or rated on the account, so not sent |
| Placeholder Book Seven | 2010/books.txt:9 | already read or rated on the account, so not sent |

## remove-first (2)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Fifteen | 2010/books.txt:14 | on the account's to-read shelf; remove it from Goodreads before importing |
| Placeholder Book Twelve | 2010/books.txt:13 | on the account's custom-shelf shelf; remove it from Goodreads before importing |

## by-title (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Three | 2010/books.txt:5 | sent by Title and Author with no ISBN-13; check after the import that Goodreads matched the right edition |

## title-only (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Four | 2010/books.txt:6 | sent by Title alone, with no ISBN-13 or author; check after the import that Goodreads found it |

## author-missing (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Five | 2010/books.txt:7 | an override whose Open Library work gave no author; run resolve again to fill it in |

## reread-merged (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Six | 2010/books.txt:8, 2019/books.txt:2 | merged with 1 earlier read: sent once, as the newest read |

## unrated (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Twelve | 2010/books.txt:13 | sent with no rating; rate it by hand |

## undated (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Fourteen | misc/books.txt:2 | sent with no read date |

## formula-cell (1)

| Title | Rows | Detail |
| --- | --- | --- |
| Placeholder Book Three | 2010/books.txt:5 | its My Review cell starts with a character a spreadsheet reads as a formula; upload the file without opening it in one |
