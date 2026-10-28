# retrolog_bootstrap

The one place the retrolog tools learn where their shared libraries live. Each tool,
sample builder, source module and test that imports `stdio_utf8`, `help_formatter`,
`bounded_run` or `bounded_fetch` reaches this folder by one fixed step from its own
location (it sits beside every tool's folder), imports `retrolog_bootstrap`, and asks
for the libraries by name:

```python
sys.path.append(str(Path(__file__).resolve().parents[1] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("stdio_utf8", "help_formatter")
```

A file one folder deeper (`src/`, `tests/`, `samples/`) takes `parents[2]` instead.

Those four are the libraries it provides. The fifth library folder at the root,
`live_pid_guard`, is not one of them: only `bounded_run`'s own tests import it, by a
fixed path of their own, and no tool asks for it.

## Why one module

Every file that needs a shared library would otherwise carry its own copy of the
lookup, each making the same assumption about where the libraries live. With one
module, that assumption is stated once and changed once.

## Where it looks

`shared_folder` is the one function that decides where a library lives; `add_shared`
asks it for each name. In this repository each library is the folder of its name at
the root, beside the tool folders, so the lookup is one fixed candidate per name: the
folder one level above this file's own, when it holds `<name>.py`.

That is deliberately not a search. A walk up the ancestors would also take a
same-named folder planted above the checkout, and a module planted there would be
imported in place of the real one.

Each folder is **appended** to `sys.path`, never inserted first, so no shared
library can shadow a standard-library module, and a folder already on the path is not
added twice. That covers these libraries only. Each tool puts its own `src/` folder,
and those of the sibling tools it imports from, first on the path, so a module inside
the checkout is trusted as part of it.
`bounded_run` also puts `stdio_utf8` on the path itself, for its own import, from the
folder beside its own: the same folder, appended the same way.

## What it refuses

- **A name that is not a shared library** raises `ValueError` before anything is
  added, so a typo cannot leave half the folders on the path.
- **A library it cannot find** raises `ModuleNotFoundError` naming the library and
  the folder the lookup started from. Without it, the caller's own `import` would fail
  with no word on where the lookup looked.

## Tests

`test_retrolog_bootstrap.py` checks the lookup and `add_shared` against the real
libraries, restoring `sys.path` after each case, and its `load_tests` hook runs the
grouped suite under `tests/` with them. That no tool imports a library from above the
repository's root is proven black-box there, by `tests/test_bootstrap_black_box.py`: it
copies the repository under a parent folder holding planted look-alikes of
`stdio_utf8`, both as this repository lays its libraries out and inside a `utilities/`
folder, the layout of a larger tree the tools could be placed in. It runs each tool's
`--help` in a real subprocess and asserts no planted module ran, with a control for each
look-alike proving it does run under a search, and a structural check reads every
shipped module for a walk of the ancestors that feeds `sys.path`.
