# Changelog

## 0.2.2 - 2026-11-10

### Changed

- The license is now two files. `LICENSE` holds the GNU AGPL v3 text exactly as the Free
  Software Foundation publishes it, so GitHub and other tools recognize it. `NOTICE` holds
  the copyright line and the "or any later version" grant. The terms are unchanged:
  AGPL-3.0-or-later.

## 0.2.1 - 2026-11-09

### Added

- `bounded_run`'s `run_bounded` takes `inherit_stderr=True`, which gives the child the
  caller's own stderr instead of a pipe, so a long run's progress lines appear as they
  are written rather than when it ends. The result's `stderr` is then `""`.

### Fixed

- Two of `bounded_run`'s tests failed on Windows and now run only where their subject
  does. The check that an unkillable `ps` is abandoned runs on POSIX alone, as its only
  caller does, and the late-tracking live test waits for a grandchild process on POSIX
  alone, since on Windows the child is still suspended at that point.
- `retrolog_probe`'s permission and overlong-name tests hold on Windows, and
  `retrolog_review`'s owner-only file-mode test skips on NTFS, which has no such mode.

### Documentation

- The bundled `bounded_run`, `bounded_fetch`, and `help_formatter` libraries' docs and
  comments are reworded for a reader outside the project they came from, without its
  tracking numbers, internal tool names, or development history.

## 0.2.0 - 2026-11-02

### Added

- `retrolog_probe` checks, before you import, which of the export's TMDb ids Letterboxd
  knows. Its report lists the missing films and prints `[[letterboxd.omit]]` entries to
  leave them out of the next export.
- `retrolog_restore` rebuilds an import file for diary entries deleted from Letterboxd,
  from an earlier account export, and lists the films whose like was lost with them.
- `retrolog_export` reads your Letterboxd account export, so a film already on the
  service is not sent again, matched across small year and hyphen differences.
- `retrolog_export --dry-run` writes nothing, and `--test-film` writes a one-film file to
  try the import with first. The config's `letterboxd.omit` list leaves named films out.
- `retrolog_resolve` holds a single exact title match that looks wrong for review instead
  of taking it, and an override takes its titles and year from TMDb's details.
- `retrolog_review --append-to` adds new picks to an existing overrides file.

### Changed

- Every tool checks a TMDb id by one rule, 1 to 10 digits with no leading zero, and every
  `--timeout` takes a finite number of seconds up to 3600.
- The README's install steps use a virtual environment.

### Fixed

- An omit entry now leaves its film out even when the only row it names is one the
  cutoff dropped.
- A film sharing a held film's title and a watched date is held with it.
- Export and restore read account files whose cells run past 131,072 characters, and
  probe reads import files with reviews that long.
- Resolve and export read parse's files for a year of any length, in year order.
- Restore lists a film to like again only when its like is gone.
- Probe gives one omit entry per title and year, as export requires, and refuses an
  `--out` inside `--files`.
- Restore counts the cells a spreadsheet would read as a formula, as export does.
- The review page checks an ISBN's check digit, and a damaged saved answer no longer
  stops it showing the later entries. Its page mode refuses a link at `--out`.
- Under `--refresh`, resolve fetches a URL repeated within one run only once.
- The README's test loop exits 1 when a suite fails.

## 0.1.0 - 2026-10-28

First public release.
