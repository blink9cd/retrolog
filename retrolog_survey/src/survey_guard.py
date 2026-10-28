"""Decide whether a survey may write its reports into a given output directory.

A survey reads private source files and writes reports that quote their real text, so
the output directory must never be somewhere a report could be committed or published
by accident. ``check_out_dir`` refuses these places, checked in this order:

1. The source tree itself, or anywhere under it. This is checked twice: by the resolved
   path, and by file identity, because a case-insensitive filesystem (the macOS
   default) lets ``Inputs/report`` and ``inputs`` name the same directory while their
   strings differ.
2. Anywhere inside a git work tree. Two checks run, and either one refuses. git itself
   is asked from the nearest directory that already exists, with every environment
   variable that could redirect or stop its search removed. A ``.git`` scan also looks
   for a ``.git`` entry (a directory, a file, or even a broken link) in that directory
   and in every directory above it, and refuses any path with a ``.git`` component. The
   scan is there because git fails in ways that look the same as "not a repository":
   it is missing, it times out, or it exits non-zero on a repository it distrusts or
   cannot read. A failure of git alone therefore never accepts a path.
3. An existing path that is not a directory, or a directory that holds files but no
   marker of the calling tool. Each tool that shares this guard names itself with the
   ``tool`` keyword and leaves a marker file named ``"." + tool`` behind (``MARKER``
   for the survey, the default), so a re-run may overwrite its own earlier output and
   never anything else -- not even another tool's. A marker that is a symbolic link
   does not count.

``check_outside_work_tree`` applies rule 2 alone, for a tool that writes one file into
a folder of the operator's own choosing (which may already hold other files): the
folder is refused inside a git work tree, by the same two checks, and nothing else.

Every refusal message names the rule broken, never a path, so it can be printed or
logged as is. A location that cannot be examined (permission denied, a symbolic-link
loop) is refused as "cannot inspect the output location".

Limits: some repositories are not detected. These are bare repositories, and work trees
whose git directory is kept somewhere else (set up with ``--git-dir`` and
``--work-tree``). Nor is a folder that a cloud drive syncs: such a folder is published
off the machine, yet nothing in it marks it as published.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# The shared libraries go on sys.path through the project's one bootstrap, in the
# retrolog_bootstrap folder beside this tool's own (retrolog_bootstrap.md says why).
# bounded_run bounds the git call: on a timeout it kills the whole process tree before
# reading the captured pipes, where subprocess.run would block on pipes a descendant
# still holds. It puts its own stdio_utf8 on the path.
sys.path.append(str(Path(__file__).resolve().parents[2] / "retrolog_bootstrap"))
import retrolog_bootstrap  # noqa: E402 - after the sys.path append above

retrolog_bootstrap.add_shared("bounded_run")

import bounded_run  # noqa: E402 - after the bootstrap above

# A git call: argv (without the leading "git"), the directory to run in and a timeout in
# seconds in, the process exit code out.
GitRunner = Callable[[Sequence[str], Path, float], int]

# The tool name a caller gets when it passes no ``tool``: the survey's own.
_DEFAULT_TOOL = "retrolog_survey"
# The file a survey writes into its output directory, so a later run can tell its own
# earlier output from a directory that holds something else. Another tool's marker is
# ``_marker_name(tool)``; this is the default's spelling, kept for existing callers.
MARKER = "." + _DEFAULT_TOOL
# The marker's permissions: the user can read and write it, and others can only read it.
_MARKER_MODE = 0o644
# The name of git's own directory, or of the file a linked work tree or a submodule
# keeps in its place.
_GIT_ENTRY = ".git"
# The git question behind rule 2: it exits 0 inside a work tree (and inside a .git
# directory, which is refused too).
_INSIDE_WORK_TREE = ("rev-parse", "--is-inside-work-tree")
# Environment variables that point git at another repository, or stop its upward
# search, so an inherited value could hide the work tree that holds the output.
GIT_DISCOVERY_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_COMMON_DIR",
)
# What a git call can raise instead of returning: it ran past its timeout, or git could
# not be started at all.
_GIT_ERRORS = (subprocess.TimeoutExpired, OSError)
# What examining a location can raise: a permission error from stat or listing, or
# (before Python 3.13) a RuntimeError from resolving a symbolic-link loop. Both carry
# the path, so neither may reach the caller.
_INSPECT_ERRORS = (OSError, RuntimeError)
_UNINSPECTABLE = "cannot inspect the output location"


class OutRefused(Exception):
    """The output directory breaks a rule; the message names the rule, never a path."""


def scrubbed_env(environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` without the variables that redirect or stop git's repository search."""
    return {
        name: value
        for name, value in environ.items()
        if name not in GIT_DISCOVERY_VARIABLES
    }


def _is_same_or_under(path: Path, root: Path) -> bool:
    """Whether ``path`` is ``root`` or lies under it, compared as resolved paths."""
    return path == root or root in path.parents


def _names_source_by_identity(out: Path, source: Path) -> bool:
    """Whether ``out`` or any existing ancestor of it is the same file as ``source``."""
    if not source.exists():
        return False
    return any(
        os.path.samefile(candidate, source)
        for candidate in (out, *out.parents)
        if candidate.exists()
    )


def _refuse_unresolvable(resolved: Path) -> None:
    """Refuse a path that still runs through a link nothing can follow (a loop).

    After resolving, an entry that exists as a link but cannot be followed can only be
    a loop. Python 3.13 and later return such a path from ``resolve`` instead of
    raising, so this check gives every version the same answer.
    """
    if any(
        os.path.lexists(candidate) and not candidate.exists()
        for candidate in (resolved, *resolved.parents)
    ):
        raise OutRefused(_UNINSPECTABLE)


def _nearest_existing(path: Path) -> Path:
    """``path`` itself if it exists, else its closest existing ancestor."""
    return next(candidate for candidate in (path, *path.parents) if candidate.exists())


def _has_git_entry(resolved: Path, start: Path) -> bool:
    """Whether a ``.git`` lies on the way to ``resolved``, by name or on disk.

    True when ``resolved`` has a ``.git`` component, or when ``start`` or any directory
    above it holds a ``.git`` entry of any kind, even a broken link. This ignores ceilings and filesystem boundaries on purpose: it errs toward
    refusing.
    """
    return _GIT_ENTRY in resolved.parts or any(
        os.path.lexists(folder / _GIT_ENTRY) for folder in (start, *start.parents)
    )


def _git_says_work_tree(start: Path, run_git: GitRunner, timeout: float) -> bool:
    """Ask git whether ``start`` is inside a work tree; a git that fails says no."""
    try:
        return run_git(_INSIDE_WORK_TREE, start, timeout) == 0
    except _GIT_ERRORS:
        return False


def _marker_name(tool: str) -> str:
    """The name of the marker file ``tool`` leaves in its output directory."""
    return "." + tool


def _marker_text(tool: str) -> bytes:
    """What ``tool``'s marker file says, for a person who finds it."""
    return f"This directory holds {tool} output.\n".encode()


def _output_noun(tool: str) -> str:
    """How a refusal names ``tool``'s output: "survey" for the default, else the name.

    The survey's own wording predates the ``tool`` keyword and is documented, so it is
    kept; any other tool is named as itself.
    """
    return "survey" if tool == _DEFAULT_TOOL else tool


def _is_reusable(out: Path, tool: str) -> bool:
    """Whether an existing ``out`` is empty or ``tool``'s own previous output."""
    marker = out / _marker_name(tool)
    return (not marker.is_symlink() and marker.is_file()) or not any(out.iterdir())


def _refuse_work_tree(resolved: Path, run_git: GitRunner, timeout: float) -> None:
    """Rule 2: refuse a resolved path inside a git work tree, by git or by the scan."""
    start = _nearest_existing(resolved)
    git_says = _git_says_work_tree(start, run_git, timeout)
    if git_says or _has_git_entry(resolved, start):
        raise OutRefused("inside a git work tree")


def _check_resolved(
    resolved: Path, source: Path, run_git: GitRunner, timeout: float, tool: str
) -> None:
    """Apply the rules, in order, to an already resolved ``out``."""
    _refuse_unresolvable(resolved)
    if _is_same_or_under(resolved, source.resolve()) or _names_source_by_identity(
        resolved, source
    ):
        raise OutRefused("inside the source tree")
    _refuse_work_tree(resolved, run_git, timeout)
    if resolved.exists() and not resolved.is_dir():
        raise OutRefused("not a directory")
    if resolved.exists() and not _is_reusable(resolved, tool):
        raise OutRefused(f"not empty and not a previous {_output_noun(tool)} output")


def check_out_dir(
    out: Path,
    source: Path,
    *,
    run_git: GitRunner,
    timeout: float,
    tool: str = _DEFAULT_TOOL,
) -> None:
    """Raise ``OutRefused`` unless ``out`` is a safe place for ``tool``'s output.

    ``out`` need not exist yet. Nothing is created or written; see ``prepare_out_dir``.
    A non-empty ``out`` is accepted only when it holds ``tool``'s own marker.
    """
    try:
        _check_resolved(out.resolve(), source, run_git, timeout, tool)
    except _INSPECT_ERRORS:
        # "from None": the original error names the path, and a chained traceback
        # would print it.
        raise OutRefused(_UNINSPECTABLE) from None


def check_outside_work_tree(
    folder: Path, *, run_git: GitRunner, timeout: float
) -> None:
    """Raise ``OutRefused`` if ``folder`` lies inside a git work tree.

    Rule 2 alone, by the same two checks: for a tool writing one file into ``folder``,
    which need not exist and may hold other files. Nothing is created or written.
    """
    try:
        resolved = folder.resolve()
        _refuse_unresolvable(resolved)
        _refuse_work_tree(resolved, run_git, timeout)
    except _INSPECT_ERRORS:
        # "from None", as in check_out_dir: keep the path out of the chain.
        raise OutRefused(_UNINSPECTABLE) from None


def prepare_out_dir(out: Path, *, tool: str = _DEFAULT_TOOL) -> None:
    """Create ``out`` (and its parents) if missing, and write ``tool``'s marker file.

    Call it after ``check_out_dir`` has accepted ``out``. Nothing locks the location
    between the two calls, so another process could change it in that window. That is
    acceptable for a command one person runs on their own files, and the marker is
    still never written through a symbolic link: a marker that is a link raises
    ``OutRefused`` and leaves the link's target untouched.
    """
    try:
        resolved = out.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        _write_marker(resolved / _marker_name(tool), _marker_text(tool))
    except _INSPECT_ERRORS:
        # "from None", as in check_out_dir: keep the path out of the chain.
        raise OutRefused(_UNINSPECTABLE) from None


def _write_marker(marker: Path, text: bytes) -> None:
    """Write ``text`` to ``marker``, never following a symbolic link in its place."""
    descriptor = os.open(marker, _marker_open_flags(marker), _MARKER_MODE)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(text)


def _marker_open_flags(marker: Path) -> int:
    """Flags that create or truncate ``marker`` and raise rather than follow a link.

    On POSIX ``O_NOFOLLOW`` makes the open itself refuse a link. Windows has no such
    flag, so there the link is refused just before the open. That leaves a short race,
    but only a privileged or developer-mode account can create a link on Windows.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if sys.platform == "win32":
        if marker.is_symlink():  # pragma: only-on win32
            raise OSError("the marker is a symbolic link")  # pragma: only-on win32
        return flags  # pragma: only-on win32
    return flags | os.O_NOFOLLOW  # pragma: only-on posix - Windows returned above


def run_git_with(
    argv: Sequence[str],
    cwd: Path,
    timeout: float,
    *,
    extra_env: Mapping[str, str] | None = None,
) -> int:  # pragma: no cover - live seam
    """Run ``git`` with ``argv`` in ``cwd``, discarding output; return its exit code.

    git gets the caller's environment without ``GIT_DISCOVERY_VARIABLES``, then
    ``extra_env`` on top. That parameter exists so a test can bound git's search, and
    production code leaves it unset. Raises ``subprocess.TimeoutExpired`` once
    ``timeout`` passes, after killing git's whole process tree, and ``OSError`` when
    git cannot be started. The live tests exercise it.
    """
    env = scrubbed_env(os.environ) | dict(extra_env or {})
    return bounded_run.run_bounded_bytes(
        ["git", *argv], timeout=timeout, cwd=cwd, env=env
    ).returncode


def real_git(
    argv: Sequence[str], cwd: Path, timeout: float
) -> int:  # pragma: no cover - live seam
    """The production ``GitRunner``: ``run_git_with`` and no extra environment."""
    return run_git_with(argv, cwd, timeout)
