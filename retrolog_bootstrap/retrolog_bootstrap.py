"""retrolog_bootstrap: put the shared libraries the retrolog tools import on sys.path.

Every retrolog file that imports a shared library (``stdio_utf8``, ``help_formatter``,
``bounded_run``, ``bounded_fetch``) reaches this module by one fixed step from its own
location, since this folder sits beside each tool's folder, and asks it for those
libraries by name with :func:`add_shared`. This module alone knows where they live, so
moving them is an edit to this one file.

Here they live beside the tool folders, at this repository's root, so each library is
one fixed candidate: the folder named after it, one level above this file's own. That
is deliberately not a search. A walk up the ancestors would also take a same-named
folder planted above the checkout, and a module planted in it would be imported in
place of the real one (``tests/test_bootstrap_black_box.py`` beside this file plants
exactly that). Each folder is appended, so none of these libraries can shadow a
standard-library module. That holds for the shared libraries only: each tool puts its
own ``src/`` folder, and those of the sibling tools it imports from, first on the path,
so a module inside the checkout is trusted as part of it. ``retrolog_bootstrap.md``
beside this file says why it is one module.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The shared libraries a retrolog file may ask for, each a folder holding ``<name>.py``.
SHARED = frozenset({"stdio_utf8", "help_formatter", "bounded_run", "bounded_fetch"})


def shared_folder(name: str) -> Path | None:
    """The folder holding the shared library ``name``, or ``None`` when none is found.

    The one place this module decides where the shared libraries live: here, the folder
    named ``name`` at this repository's root, when it holds ``<name>.py``.
    """
    folder = Path(__file__).resolve().parents[1] / name
    return folder if (folder / f"{name}.py").is_file() else None


def add_shared(*names: str) -> None:
    """Append each named shared library's folder to ``sys.path``, once.

    Every name is checked before anything changes, so a typo cannot leave half the
    folders added. A library that cannot be found raises ``ModuleNotFoundError``
    naming it, rather than leaving the caller's import to fail without saying where
    the search looked.

    Raises:
        ValueError: a name is not one of :data:`SHARED`.
        ModuleNotFoundError: a named library is not where :func:`shared_folder`
            looks.
    """
    unknown = sorted(set(names) - SHARED)
    if unknown:
        raise ValueError(f"not a shared library: {unknown[0]!r}")
    for name in names:
        folder = shared_folder(name)
        if folder is None:
            message = f"shared library {name!r} not found from {Path(__file__).parent}"
            raise ModuleNotFoundError(message, name=name)
        if str(folder) not in sys.path:
            sys.path.append(str(folder))
