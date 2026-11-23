# `bounded_run`: a captured subprocess bound that actually holds

Shared library for running a child process with a timeout that survives the child spawning
children of its own. Imported by the tools beside it; it has no CLI.

The bound is on time: it is measured on `time.monotonic()`, or on `clock=` when given. With
`memory_limit=` the child's process tree is bounded in memory as well (*Memory bound* below),
but that bounds the child, not this process. Nothing bounds the size of the output. Everything
the child writes to stdout and stderr is read into this process's memory, and no option caps it.
It is handed back whole in the
`CompletedProcess`, or in the `TimeoutExpired` an expiry raises; only a failed tree kill drops it
(property 3), and by then it has already been held. A child that writes a gigabyte well inside its
time costs this process more than a gigabyte, and nothing here stops it. *Measured behavior* below
gives the multiple.

## Why it exists

`subprocess.run(..., capture_output=True, timeout=N)` reads like a bound on the whole call. It is
not. On `TimeoutExpired` CPython on Windows kills only the direct child, then goes back to reading
the captured pipes to assemble the exception's `output`, and a surviving *grandchild* that
inherited those handles keeps them open, so that read blocks indefinitely. On POSIX the same call
returns near the bound but leaves the grandchild running.

The failure mode is inverted from the intuition: the bound is reliable for a leaf child and
unreliable for exactly the nested case you added it for.

This module inverts the order: on expiry it kills the whole process tree first, and only then
touches the streams.

## Importing it

A tool resolves it from its own file's location, so nothing depends on where the tree lives or
on the working directory. In this repository `retrolog_bootstrap` appends it for the tools that
need it, so it can shadow neither a tool's own modules nor the standard library:

```python
retrolog_bootstrap.add_shared("bounded_run")

import bounded_run
```

`bounded_run.py` puts `stdio_utf8` on the path itself, for its own import, from the folder
beside its own (`<parent>/stdio_utf8`), and appends it as `retrolog_bootstrap` does. That is
the folder the bootstrap names too, so keep the two libraries side by side.

## API

### `run_bounded(argv, *, timeout, cwd=None, env=None, stdin_text=None, kill_tree_fn=kill_tree, clock=None, hide_window=False, inherit_stderr=False, memory_limit=None, tree_observer=None)`

Runs `argv` capturing text output; returns `subprocess.CompletedProcess[str]`. `argv` is a
sequence of arguments: a `str` or `bytes` raises `TypeError` before anything is spawned, because
listing it would make each character an argument, so `"ls -l"` would run a program named `l`.

Bakes in the four kwargs a capturing call would otherwise retype at every call site
(`capture_output=True`, `encoding=stdio_utf8.ENCODING`, `errors=stdio_utf8.CAPTURE_ERRORS`,
`check=False`), so the stream policy `stdio_utf8` names has one implementation instead of one
convention per site.

`stdin_text` feeds the child on stdin; a pipe is opened only when it is given, and a caller that
passes nothing gives the child `DEVNULL`, an immediate EOF. That is deliberately not inheritance:
the run captures both output streams, so the child cannot be interactive anyway, and a child that
reads stdin would otherwise eat this process's own. Nor is it a pipe nobody writes to, which would
block where an EOF does not.

`clock`, when given, measures `timeout` on that clock instead of `time.monotonic()`. See
*Measuring the bound on a clock that counts sleep* below.

`memory_limit`, in bytes, also bounds the memory of the child's whole process tree: past it the
tree is killed and `MemoryLimitExceeded` is raised; should the tree become unreadable, it is
killed too and `MemoryWatchFailed` is raised. `tree_observer`, a `TreeObserver`, is filled in
with how many processes that watchdog saw in the tree, so it needs `memory_limit`. See *Memory
bound* below.

`hide_window=True` starts the child with no console window on Windows (`CREATE_NO_WINDOW`, added
to the `CREATE_SUSPENDED` every child gets there), and is ignored elsewhere. It exists for a
caller running under `pythonw.exe`, which has no console: Windows gives a console-subsystem child
of such a parent a new, visible console of its own. It is off by default, because a child of a
console parent shares that console and should keep doing so. The flags are computed by
`src/boundedrun_spawn_flags.py`.

`inherit_stderr=True` gives the child this process's own stderr instead of a pipe, and the
result's `stderr` is then `""`. It exists for a long child run for its stdout alone, such as a
sweep whose report the caller parses: its progress lines reach the operator as they are written,
instead of waiting in the pipe until it ends. Off by default, because most callers read the
child's stderr, and a child's stray output on the caller's own stream is noise to the rest.
The bytes sibling has no such option, because no caller needs one yet.

Two consequences a caller owns. The child's stderr reaches the terminal raw, escape
sequences included, with none of the sanitizing a caller applies to captured output, so
pass it only for a child whose output you trust. And a parent with no stderr of its own,
such as one run by `pythonw.exe`, has none to hand on, so the child's writes there go
nowhere.

### `run_bounded_bytes(argv, *, timeout, cwd=None, env=None, stdin_bytes=None, kill_tree_fn=kill_tree, clock=None, hide_window=False, memory_limit=None, tree_observer=None)`

Binary-mode sibling; returns `subprocess.CompletedProcess[bytes]`. Pass `stdin_bytes` to feed the
child's stdin; without it the child gets `DEVNULL`, as above, and `argv`, `clock`,
`hide_window`, `memory_limit` and `tree_observer` mean what they do there.
Kept a separate named function rather than a `text=` switch so each has one reason to exist and
mypy needs no `@overload`.

### `stream_bounded(argv, *, timeout, cwd=None, env=None, kill_tree_fn=kill_tree, hide_window=False, memory_limit=None, tree_observer=None)`

Returns a `BoundedStream` over `argv`'s stdout, as bytes, for a `with` statement. It exists for
a child whose output is too large to hold until it ends, a decoder writing raw frames for one:
the caller reads stdout as it comes, while the child's whole tree stays under the same
`timeout` and `memory_limit` as `run_bounded`'s, killed by the same `kill_tree_fn`.

```python
with bounded_run.stream_bounded(argv, timeout=600, memory_limit=2 << 30) as stream:
    while frame := stream.read(frame_bytes):
        consume(frame)
if stream.returncode != 0:
    report(stream.stderr)
```

- **Validated at the call, spawned at the `with`.** Every refusal `run_bounded` makes is made
  when `stream_bounded` is called, before anything starts: a `str` argv, a `memory_limit` that
  is not a positive `int`, a `tree_observer` without one, and any `memory_limit` on Windows,
  with the same `ValueError`. Nothing is spawned until the stream is entered, and a stream is
  entered once.
- **Reading.** `stream.read(size)` blocks until `size` bytes or the end, and fewer than `size`
  means the end. stdin is `DEVNULL`, for the reason `run_bounded`'s is. stderr is drained by a
  thread as the child writes it, so a child writing a lot there never blocks, and only its last
  `boundedrun_stream.STDERR_TAIL_BYTES` are kept, as `stream.stderr`.
- **The bound, while the caller reads.** A pipe read takes no timeout, so a deadline thread
  kills the tree when `timeout` passes, on `time.monotonic()` (there is no `clock=`), and the
  memory watchdog kills it on a breach. Either kill ends the read with the end of the stream.
- **How it ends.** Leaving the `with` after reading to the end waits, in what is left of
  `timeout`, for the child's exit and for its stderr to close, as `run_bounded` waits for
  both pipes: a descendant the child left behind holding stderr keeps the stream running,
  and past the deadline the tree is killed and the run times out. Leaving it any other way,
  stopping early or raising, kills the tree at once, a descendant whose leader already
  exited included. Either way the leader is reaped (within `KILL_GRACE_SECONDS`), the
  watchdog and drain are stopped, and the pid and job are released before the statement
  ends; then `returncode` holds the exit status. An interrupt that lands while the ending
  waits kills the tree, reaps the leader and releases it before it propagates (property 7).
- **What it raises.** Only after a body that raised nothing: `MemoryLimitExceeded` or
  `MemoryWatchFailed` for the memory bound, else `subprocess.TimeoutExpired` (carrying the
  stderr tail) for a run past its deadline, or for one whose kill left it unreaped, when
  `returncode` is `None`. A nonzero exit is not raised: read `returncode`. A caller's own
  exception always wins over the outcome. A deadline kill that itself raised is that
  `TimeoutExpired`'s cause, after the child alone was killed in its place.

On Windows, without `memory_limit`, the child joins a job before it runs, as every other entry
point's does, so the deadline kill reaches its whole tree there too.

### `sleep_inclusive_clock()`

Seconds on a clock that keeps counting while the machine sleeps, for `clock=`. See *Measuring the
bound on a clock that counts sleep* below for which clock each platform reads.

### `kill_tree(pid)`

Kills `pid` and every descendant. A child this module started is killed, while its run lasts,
through its job object on Windows: `TerminateJobObject`, which reaches every descendant whether
or not `pid` itself is still alive (property 5). Any other pid gets the platform's own tree
kill: `taskkill /F /T /PID` on Windows, which walks parent-pid links and so reaches only
descendants still linked to a live `pid`. Elsewhere `pid` must lead its own process group, which
every child this module starts does (property 4 says why), and the kill freezes that group and
every group reachable from it before killing them all (property 6). A `pid` below 2 raises
`ValueError` on every platform with nothing signaled: `killpg(0)` would address the caller's own
group, and 1 is init's. On POSIX so does the caller's own group, and a `pid` that the first
process-table snapshot, read before any signal, lists in a group it does not lead: the walk would
collect that whole group, its leader included. Exposed and injectable via
`kill_tree_fn` so tests can drive the expiry branches without spawning a real process or waiting
out a real timeout.

### `kill_live_trees()`

Kills the tree of every child a `run_bounded`, `run_bounded_bytes` or `stream_bounded` call has
started and not yet finished, through `kill_tree`, and returns how many kills completed. It is
for a caller's own signal handler: property 8 says why such a caller needs it. A kill that raises `OSError`,
or the `ValueError` of a root the tree kill refuses with nothing sent, is skipped and not
counted, so one tree that cannot be killed does not spare the rest. With nothing running it
kills nothing and returns 0. The registry behind it is `src/boundedrun_live.py`.

### `live_pids()`

The pids `kill_live_trees` would kill right now, as a tuple in no particular order; empty with
nothing running. Read-only. It is how a caller learns that a child it started has been
registered, which happens on the line after `Popen` returns (property 8): a test that signals
its own process to prove the stop reaches a child polls this for the child's pid first, since
anything the child itself writes can be seen before then.

### Constants

| Constant | Value | Why |
| --- | --- | --- |
| `KILL_GRACE_SECONDS` | 5.0 | Time allowed for the pipes to drain *after* the tree kill. Generous against the 0.94s measured `taskkill` + immediate drain: a safety net, not a tuning knob. Exceeding it means the kill did not work, which is reported rather than waited out. |
| `KILL_TIMEOUT_SECONDS` | 10.0 | Bound on the tree kill itself (the `taskkill` fallback on Windows, the freeze-and-walk on POSIX), so a kill that cannot complete does not become the new hang. |

## The contract

The properties callers depend on:

1. **It raises `subprocess.TimeoutExpired`, carrying partial output**, the same shape
   `subprocess.run` raises. Every existing `except subprocess.TimeoutExpired` handler keeps
   working unchanged, including the ones that bind it and read `exc.output` / `exc.stderr`.
2. **The kill precedes the stream read.** That inversion *is* the fix, and it is asserted directly:
   the fake records the call sequence, so an implementation that re-read first would fail.
3. **A failed tree kill fails honestly rather than blocking.** If the post-kill read also expires,
   the partial output is abandoned and the exception raised without it. A bound that always returns
   beats one that sometimes hangs.
4. **The POSIX kill reaches the tree even after the direct child has exited.** Every child starts
   with `start_new_session=True`, so its process-group id *is* its pid, and `kill_tree` signals
   `killpg(pid)` with no lookup. It must never go through `os.getpgid`: macOS refuses that lookup
   (`ESRCH`) for a child that has exited but is not yet reaped, which is exactly when a grandchild
   can still hold the pipes. Through the lookup, that case raised `ProcessLookupError` in place of
   `TimeoutExpired`, killed nothing, and left the grandchild running. `ESRCH` and `EPERM` are
   tolerated: macOS reports `ESRCH` once the group is gone and `EPERM` while the unreaped leader is
   its only member. `EPERM` also means some member could not be signaled (one running under
   another uid, say); those members are left running, and the caller gets property 3's output-less
   `TimeoutExpired` once the bounded re-read expires. Linux answers `getpgid` for an unreaped
   child, which is why a run under Linux never showed the defect.
5. **The Windows kill reaches the tree after the direct child has exited, measured on Windows
   CI** (*Proving the Windows branch*). `taskkill /T` rebuilds the tree from parent-pid links at the moment
   of the kill, so it cannot start from a leader that has exited, and it misses the orphans of any
   intermediate process that exited. So every child is created with `CREATE_SUSPENDED`, assigned
   to a job object of its own, and only then resumed; every descendant is born inside the job, and
   `kill_tree` terminates the job. A failure anywhere in that sequence kills the still-suspended
   child and raises the `OSError` Windows reported, because a suspended child would otherwise sit
   out the whole timeout and be reported as a hang. A member the job kill may not terminate
   (`ERROR_ACCESS_DENIED`, surfacing as `PermissionError`) is tolerated exactly as property 4
   tolerates `EPERM`. *Proving the Windows branch* says what has and has not run.
6. **The POSIX kill reaches the tree of a nested run.** A descendant that itself calls
   `run_bounded` starts its child in a new session, which no `killpg` of this child's group
   reaches. Orchestrators nest exactly so: one scheduled job of nested tools was seen with five
   nested groups under the one its kill addressed, and after a deadline kill of its outer tool, a
   nested run that tool had started kept running for 35 minutes. So the kill repeats: snapshot the
   process table with `ps`, collect every group reachable from the child's through a parent pid or
   a shared group, and `SIGSTOP` them all, until a snapshot finds no new group and shows every
   collected process stopped or a zombie. Only then does it `SIGKILL` each group. The first
   snapshot is read before any signal, so a root that does not lead its own group is refused with
   nothing sent (the `kill_tree` entry above). It freezes before killing because parent-pid links
   are the only way down, and they break the moment a parent dies; a stopped process can neither
   die nor fork, so a fork racing the freeze costs one more round rather than an escape. The
   caller's own group is never stopped. When `ps` cannot be read (missing, exiting non-zero, as a
   BusyBox `ps` refusing `-A` does, or past `KILL_TIMEOUT_SECONDS`), the groups collected so far
   are killed as they stand, which is at least the child's own group. An overdue
   `ps` is killed and given one second to be reaped, then abandoned unreaped: under heavy load
   macOS `ps -A` was measured sitting for minutes in an uninterruptible kernel wait, where
   `SIGKILL` cannot end it, and an unbounded reap there hung the stop handler that called the
   kill. Still out of reach: a descendant whose own parent exited before the kill and which left
   that parent's group, since no link of either kind leads to it; a member under another uid
   (property 4's `EPERM`); and, should the killer itself die between the freeze and the kill, the
   tree is left stopped rather than running. Windows needs none of this: a nested run's job nests
   inside the outer one (Windows 8 and later), and `TerminateJobObject` ends every child job's
   processes too. That is Microsoft's documented behavior, not a measurement here.
7. **Ctrl-C kills the child's tree, then propagates.** Because every child leads a session of
   its own, the terminal's `SIGINT` reaches only the caller, never the child. So a
   `KeyboardInterrupt` that arrives while the run waits is caught (that exception only; a
   `SystemExit` is not an interruption of the child), the tree is killed exactly as on expiry,
   the child is reaped under `KILL_GRACE_SECONDS`, and the same `KeyboardInterrupt` is
   re-raised. Without this, an interactive tool that says "stopped" on Ctrl-C leaves its child
   running unattended, still able to finish what it was doing. A reap that expires means the
   kill did not work; the interrupt is still what the caller sees. The kill happens once: this
   path marks the interrupt handled once its kill and reap have run, and property 8's guard
   further out skips a handled one, so the bound stays one `KILL_GRACE_SECONDS` even when the
   reap expired. `tests/test_drain_interrupt.py` pins that a kill that does not work is called
   once, and that a working kill is called once, with the child's pid, by either entry point.
8. **A caller told to stop can reach every running child.** The new session that keeps a child
   out of the terminal's `SIGINT` keeps it out of every signal sent to the caller: a logout's
   `SIGTERM`, or a scheduler stopping a job, ends the caller and leaves its children running,
   with any lock the caller held now free. So each child's pid is registered on the line right
   after `Popen` returns, with nothing between, and removed when its drain ends, however it ends;
   `kill_live_trees` kills each registered tree. A caller's handler that then raises a
   `KeyboardInterrupt` subclass also gets property 7's kill and reap of the child it interrupted,
   wherever after `Popen` returns the interrupt lands (below). A handler can run after a drain
   has reaped its child but before the pid is removed; the kill then finds a group with no
   leader, kills any member left in it, and otherwise finds nothing. Threads running children
   concurrently share the registry safely: each update is a single set operation, and the kill
   walks a snapshot.

   The spawn-to-track window, and what remains of it: `Popen` and the registration run in
   one frame, on consecutive statements, so no frame hands the new object back to a caller first.
   A frame return is where a debugger's or a pure-Python coverage tracer's code runs, and with it
   any pending signal's handler. A handler that runs once `Popen` has handed the object back, at
   the registering call itself or at a tracer's first event after `Popen`, finds the child not yet
   tracked, so its `kill_live_trees` misses it. The `KeyboardInterrupt` (or subclass) it then
   raises is caught there: the new child's tree is killed through `kill_tree_fn`, the child is
   reaped under `KILL_GRACE_SECONDS` and forgotten, and the same interrupt is re-raised. Should
   `kill_tree_fn` itself raise there, its exception replaces the interrupt, which it keeps as
   `__context__`, exactly as on the drain's interrupt path. The default `kill_tree`'s root refusal
   cannot fire there: the child `Popen` just returned leads the new session it was started in, and
   a session leader cannot leave its group, so its pid is at least 2 and leads a group that is not
   the caller's; unreaped, it keeps that pid.

   No frame entry falls between `Popen` and the registration either: a frame entry is a point
   where stock CPython checks for a pending signal, tracer or no tracer, so a helper call between
   them would let a stop find the child untracked and leave it running. A
   `finally` in that same frame forgets the pid around both the registration and the drain, and the
   same frame's interrupt guard spans the drain too. An interrupt after the registration but before
   the drain's own interrupt path is in force (at the drain's entry, which CPython checks with no
   tracer, or in the job containment) kills the child's tree and reaps it before re-raising, so a
   caller that never calls `kill_live_trees` orphans nothing. An interrupt the drain's own path
   already acted on, which it marks handled before re-raising, is not acted on again, so the kill
   happens once even when that path's reap expired (property 7); and a child already reaped is not
   signaled again, since its pid may have been reused. Blocking the stop signals around `Popen` was
   declined, because a child inherits a blocked signal mask and would be deaf to the very signals
   that stop it.

   What is still out of reach: an interrupt delivered inside `Popen`'s own call, before that
   call hands the object back: in `Popen`'s code after the child has started, at the
   pending-signal check CPython makes as the call instruction completes, or in a hook run on every
   instruction. No object reaches this module, so it has no child to name, and that child is left
   running in its own session; CPython at most reaps it after it exits on its own. And, as in
   property 7, a `SystemExit` is not caught, since it is the caller's own decision.
   `tests/test_spawn_window.py` provokes the window twice, by making the registering call raise
   and by raising the stop signal from a tracer at its first event after `Popen.__init__` returns,
   proves by pid that no child survives, and pins structurally that nothing is called between
   `Popen` and the registration. It also interrupts at the drain's entry and at the first trace
   event after the registration, and requires an empty registry. `tests/test_drain_entry_window.py`
   interrupts at the containment that opens the drain, with no handler calling `kill_live_trees`,
   and proves by pid that the child and its grandchild are gone (on Windows, where the child is
   still suspended there and starts no grandchild, that the child is gone).

## Implementation decisions that look wrong and are not

`start_new_session=True` is passed unconditionally. The stdlib documents it as POSIX-only, but
it is *accepted* on `win32` (verified). On POSIX it makes the child a process-group leader whose
group id is its own pid, which is what lets `killpg` reach that group with no lookup. Passing it
unconditionally (like `creationflags`, whose value a pure helper derives from the platform) keeps
the spawn itself free of a platform branch. A nested run passes it too, and so leaves the outer
group: joining the parent's group instead was considered and declined, because the inner kill
could then no longer signal its own child's group without signaling itself, and would need the
walk anyway (property 6).

The POSIX walk reads `ps`, not `psutil`, `sysctl` or `/proc`. `ps -A -o pid=,ppid=,pgid=,stat=`
is one command that BSD/macOS `ps` and procps both understand, and like `taskkill` it spawns
nothing, so its own capturing read is safe. It reads through `Popen` and a bounded
`communicate`, not `subprocess.run`, whose timeout path kills the child and then waits for it
with no bound: a wait that never ends while `ps` cannot yet die (property 6 above). `psutil` was
rejected: it would put a compiled dependency under a library that otherwise needs only the
standard library, and its recursive listing is the same parent-pid walk with no freeze, so it
would not close the race. `sysctl` through `ctypes` is a macOS-only structure layout, and `/proc`
is Linux-only.

A SIGTERM cascade was declined. Asking the tree to stop itself (each nesting level trapping
`SIGTERM` and killing its own in-flight run) needs every level to opt in, and the chain runs
through programs that never will, test suites and shell scripts among them; one silent level
breaks it. It would also add a grace period to every expiry. The walk needs nothing from the
processes it kills.

`Popen` is deliberately not used as a context manager. `Popen.__exit__` calls an unbounded
`wait()`, which would reintroduce this module's own bug on the failed-kill path. `with
subprocess.Popen(...)` reads like the more careful choice and is the less safe one here.

The Windows child is resumed through a thread snapshot. Assigning a *running* child to a job
races it: anything it starts before the assignment is born outside the job and survives the kill.
`CREATE_SUSPENDED` closes that window, but `Popen` closes the thread handle `CreateProcess`
returned, so the child is resumed by finding its one thread in a `CreateToolhelp32Snapshot` walk.
`NtResumeProcess` would be shorter and is undocumented, so it was not used.

The job sets `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, and not `KILL_ON_JOB_CLOSE`. Without
breakaway-ok, a descendant that explicitly asks for `CREATE_BREAKAWAY_FROM_JOB` would have its
`CreateProcess` *fail* inside the job, where it succeeded before containment existed; with it, that
process leaves the job and escapes the kill, which is what it asked for. Kill-on-close stays off so
that closing the job when a run ends leaves a process the run deliberately detached still running,
just as the POSIX path never signals a finished run's group.

The job is found by pid, through a registry. `kill_tree(pid)` keeps its signature, so
`kill_tree_fn` stays `pid -> None` and no caller or fake changes. Each run registers its child's
job at spawn and removes it when the run ends, however it ends; a pid cannot be reused meanwhile,
because the child's `Popen` holds its process handle.

The kernel32 calls sit behind a seam that runs on every host. `_KernelJobApi` takes the loaded
library and the last-error reader as arguments, and its test fakes are C function pointers built
from the same `_KERNEL32_SIGNATURES` table the Windows binding applies. The call order, the
argument marshaling and the structure sizes are therefore checked on macOS and Linux too; only the
binding itself is Windows-only.

The Windows half stays in `bounded_run.py`. The drain's module in `src/` is a seam between
waiting and spawning, while the Windows half is part of the spawning. Only its transcription of
the Win32 headers lives apart, in `src/boundedrun_kernel32.py`: the structure layouts and the
kernel32 function table, which have no behavior of their own. `bounded_run` re-exports them.

## Memory bound

`memory_limit=` (bytes, default `None`) bounds the memory of the child and every descendant
reachable from it, summed. Past the limit the tree is killed with the same `kill_tree_fn` the
timeout uses, and the call raises `MemoryLimitExceeded`: a `subprocess.SubprocessError` carrying
`cmd` (the argv), `limit`, `peak` (the largest sum observed, in bytes) and whatever output was
captured before the kill, as `output`/`stdout` and `stderr`, the way `TimeoutExpired` carries
it. A breach never comes back as an ordinary `CompletedProcess` with a signal return code: a
caller that classifies a signal exit as a crash would otherwise misread its own bound. A breach
whose kill failed, so that the timeout fired afterward, still raises `MemoryLimitExceeded`, with
the kill's error as its cause. With `None`, no watchdog starts and the run is exactly as before.

**It fails closed.** A read of the tree that raises (a reader error, not a process that went
away, which reads as 0) kills the tree at once, as a breach would, and the call raises
`MemoryWatchFailed`: also a `subprocess.SubprocessError`, a sibling of `MemoryLimitExceeded`
rather than a kind of it, since no breach was observed. Its cause is the read error, and it
carries `cmd`, `limit`, the captured `output`/`stdout` and `stderr`, and `kill_error` (the
kill's own error, when that kill raised too; the timeout then fires and its partial output is
carried). A watchdog that could not read would otherwise let the child run on with no bound at
all until the drain ended, the outcome the caller asked the bound to prevent. Should the
watchdog thread fail to start, the child is killed and reaped before that error propagates,
since nothing else would end it. And no kill begins once the run's end has stopped the watchdog,
nor once the child has been reaped: by then its pid may be reused.

"Went away" is narrow, so the bound cannot fail open by misreading a denial as an exit. On
macOS only `ESRCH` means gone; every libproc call clears `errno` first and reads it after,
and any other failure (`EPERM` above all) raises. On Linux a process is gone only once its
`/proc/<pid>` folder is: a `statm`, `stat` or thread `children` file that cannot be read while
its process (or thread) is still listed raises. The run's own child is held to more: it is gone
only once the run has reaped it, so a reader that cannot see its folder while it is unreaped (a
`/proc` that does not hold it, such as an empty directory or another pid namespace's mount,
where every read would look like an exit and the walk would sum 0 bytes) fails the watch, after
polling for about a second, on the monotonic clock rather than a count of polls a throttled host
stretches, for the reap `subprocess` may be recording at that moment
(`src/boundedrun_rootwatch.py`). A zombie child is not a failure: Linux keeps its folder until
the reap. macOS is not checked this way, since `ESRCH` there is the kernel's own answer, and a
zombie child returns it too. Measured with the parent under `sandbox-exec` and
`(deny process-info*)`: every libproc call fails with `EPERM`, and a reader that took that for
"gone" let a 200 MiB child finish under a 50 MiB bound with exit 0. That run raises
`MemoryWatchFailed` (`tests/test_memory_fail_closed.py` runs it live on macOS when
`sandbox-exec` exists). The cost is that a descendant the reader may not inspect, one running
under another uid (a setuid helper, say), fails the watch as well rather than being silently
left out of the sum.

**What the watchdog saw of the tree.** Pass a `TreeObserver()` as `tree_observer=` and, after
the run (returned or raised), its `max_processes` is the most processes one poll found in the
tree, the child included: 1 for a child that ran alone, more once a descendant was seen, 0
only if no poll ran. It exists for a caller that trusts a clean exit and must know whether some
descendant ran whose own end it never saw (a stress harness, say). A descendant that started and
ended between two polls is never seen, so a count of 1 is evidence, not proof. Measured on
macOS at the 20 ms poll, 80 runs each: a forked child that exits at once was seen in 3 runs
at one job and 13 at eight concurrent jobs, one that lives 20 ms in 72, and one that lives
40 ms or more in all 80. A descendant living under about 20 ms is usually missed, so the count
protects a campaign of many runs (one run that sees a descendant is enough), not each run. It
needs `memory_limit=`, the only thing that walks the tree; given alone it raises `ValueError`
before spawning, rather than report 0 as though nothing had run.

The value must be a positive `int`; anything else (`0`, a negative, a `float`, a `bool`) raises
`ValueError` before anything is spawned.

| Platform | Mechanism | Footprint read |
| --- | --- | --- |
| macOS | A watchdog thread polls the tree | `phys_footprint` from `libproc`'s `proc_pid_rusage`, through `ctypes`, which includes compressed memory; children from `proc_listchildpids`; each process's group from `proc_pidinfo`'s BSD short info, and a group's members from `proc_listpids` |
| Linux | The same watchdog | The resident set, `/proc/<pid>/statm`'s second field times the page size; children from each thread's `/proc/<pid>/task/*/children`, or a scan of every `/proc/*/stat` where the kernel lacks that file; each process's group from its `stat`'s fifth field, and a group's members from a scan of every `/proc/*/stat` |
| Windows | Refused: `ValueError` before spawning | None yet: it needs a job-object memory cap, which is not built |
| Anything else | Refused: `ValueError` before spawning | No reader |

**Why a watchdog, not a resource limit.** On macOS 27.2, `setrlimit` refuses any lowered
`RLIMIT_DATA` or `RLIMIT_AS` with `EINVAL` (256 MiB, 8 GiB and 64 GiB tried, from Python and
from C), so the kernel offers no bound to set. Linux honors `RLIMIT_AS`, but a breach there
makes `malloc` return NULL, and a tool that then exits cleanly non-zero is indistinguishable
from one that rejected its input. A parent-side watchdog that kills and raises gives one
unmistakable outcome on both.

**The poll, and what it cannot promise.** The watchdog starts once the child is registered and
contained, reads the tree at once and then every `POLL_SECONDS` (in
`src/boundedrun_memory.py`), and stops when the drain ends. Between two reads a child can
allocate freely, so the overshoot past the limit is about its allocation rate times the
interval, plus the kill's latency. Measured on macOS: polling every 10 ms killed a child capped
at 512 MiB at 535 MiB, and at a 20 ms poll the suite's allocator (64 MiB chunks, 20 ms
apart) capped at 256 MiB was killed at 267 or 268 MiB, about 0.3 s in. Each read walks the
same tree the POSIX kill reaches (property 6): every descendant through parent links, and every
member of every process group one of them is in, so a descendant whose own parent already
exited, re-parented to init or launchd, is still counted while it stays in its group, and
killed with it. Group ids below 2 are never followed. A process that exits mid-read counts as
0; one the reader may not read fails the watch (above). On Linux each group the tree
reaches costs a scan of every `/proc/*/stat` per poll, there being no per-group index.

**One read is bounded too.** The walk adds each footprint to a running total and stops as soon
as that total is over the limit, so a breach is caught without reading the rest of the tree;
the run's `peak` is then the sum when the walk stopped. A walk that reads more than
`MAX_WALK_PROCESSES` processes, or runs longer than `MAX_WALK_SECONDS` on the monotonic clock
(both in `src/boundedrun_memread.py`), raises `TreeWalkOverCap`, an `OSError`, and fails the
watch: a child forking faster than the walk could otherwise keep one read from ever ending, and
with it the comparison to the limit. Such a run raises `MemoryWatchFailed`, its tree killed.

**The two readers do not measure the same thing.** macOS's `phys_footprint` is what Activity
Monitor calls Memory: private memory, compressed pages included. Linux's resident set counts
shared file-backed pages (mapped libraries, a mapped file) once for every process mapping them,
so a tree of many processes over-counts what it costs, and it excludes swapped-out pages, so a
tree under memory pressure under-counts. A limit tuned on one platform may trip earlier or later
on the other.

`tests/test_memory_limit.py` proves it live on macOS (the Linux reader is pinned only against a
directory laid out like `/proc`, not yet run against a real kernel): a real allocator past its
bound raises with `peak > limit` long before the timeout, through both entry points, and is gone
afterward; one well under its bound completes; two siblings that each stay under the bound but
together exceed it trip it and are both gone afterward; a grandchild orphaned in the child's
group is counted and killed; and a watchdog that cannot start leaves the child killed and
reaped. `tests/test_memory_readers.py` pins the tree sum, both readers (the
`libproc` one through C function pointers built from its own signature table, the `/proc` one
over a directory laid out like `/proc`, so both run on every host); `tests/test_memory_watchdog.py`
pins the watchdog's outcomes and the watched drain's; `tests/test_memory_fail_closed.py` pins
gone against unreadable on both readers, through `run_bounded`, and live under `sandbox-exec`;
`tests/test_tree_observer.py` pins the process count and the no-kill-once-reaped check;
`tests/test_root_unreadable.py` pins an unseen, unreaped child as a failed watch, a zombie child
as none, and the reap race's grace.

## Measuring the bound on a clock that counts sleep

`Popen.communicate(timeout=...)` measures on `time.monotonic()`. On macOS that clock stops while
the machine sleeps, so a bound on a laptop that sleeps mid-run is a bound on awake time, and a
child that sleeps with the machine is never killed. Pass `clock=` to measure the bound on another
clock instead:

```python
bounded_run.run_bounded(argv, timeout=7200, clock=bounded_run.sleep_inclusive_clock)
```

- **`sleep_inclusive_clock()`** reads `CLOCK_MONOTONIC` on macOS, which counts sleep there, and
  `CLOCK_BOOTTIME` on Linux, where `CLOCK_MONOTONIC` stops in suspend. Elsewhere it falls back to
  `time.time()`, which counts sleep but steps when the system clock is set.
- **With a clock,** the drain waits in slices of at most 30 s and re-reads the clock between them.
  The kill lands within one slice of *awake* time after the deadline, because nothing runs while
  the machine sleeps, the kill included.
- **Without a clock,** behavior is unchanged. It stays opt-in: an orchestrator's per-stage bound
  that counted sleep would kill stages on a laptop that dozed mid-run.
- **Not with stdin:** `clock=` with non-empty `stdin_text` or `stdin_bytes` raises `ValueError`
  before anything is spawned, because a sliced wait retries `communicate()`, and on CPython 3.12
  and earlier a retry arms the stdin pipe only for a non-empty `input` argument, which a retry may
  not pass, so input larger than the pipe buffer would stall until the deadline and be killed as
  a spurious timeout.

The wait lives in `src/boundedrun_drain.py`, a private module: how long to wait, on which clock,
and the kill-then-reread order. The POSIX tree kill lives in `src/boundedrun_posix_tree.py`, a
second one, and the registry of running children `kill_live_trees` reads in
`src/boundedrun_live.py`, a third. The `Popen` call itself, and the registration on the statement
after it (property 8), live in `src/boundedrun_spawn.py`, a fourth. Each was split out to keep
`bounded_run.py` to a reviewable length. `bounded_run.py` keeps the spawn's flags, Windows job
containment, and `kill_tree`, which picks between them. `tests/test_live_registry.py` pins the
registry's bookkeeping with fakes, and kills a real child and grandchild mid-run, probing that
both are gone. The memory watchdog (*Memory bound*) lives in `src/boundedrun_memory.py`, and the
tree walk and its two platform readers in `src/boundedrun_memread.py`, and the check that the
reader can see the unreaped child at all in `src/boundedrun_rootwatch.py`. The order a
capturing run's drain follows (contain, wait under the bounds, release) lives in
`src/boundedrun_contained.py`, with the containment passed in from `bounded_run.py`, and the
streaming entry point's child, its deadline thread and its ending in `src/boundedrun_stream.py`.

## Proving the POSIX branch

`kill_tree`'s POSIX arm, and `kill_tree_posix` behind it, carry `# pragma: only-on posix`, not
`# pragma: no cover`: they cannot execute on Windows, so a coverage run configured for that
pragma excludes them there and *measures* them on every POSIX host. On macOS or Linux the suite
covers them natively. The live grandchild test kills a leader that is still running, the
exited-leader test kills a group whose leader has already exited, and
`tests/test_nested_tree_kill.py` kills a real tree that crosses a nested run's session, polling
for survivors rather than sleeping. A stubbed test pins the tolerated `ESRCH` and `EPERM`
outcomes, which a live run cannot produce on demand. `tests/test_posix_tree.py` pins the
freeze-and-walk loop's order (the first snapshot before any signal, every stop before any kill,
another round for a member still running) and that each refused root sends nothing, against
scripted process tables, and runs on every host because the loop takes its signals as seams.

Run on Linux, where `sys.platform` is `linux`, the live alive-leader grandchild test takes the
`killpg` path for real, and `ps` afterward shows zero surviving `gc_child` processes; the
exited-leader test has not been run there, and neither has any of property 6's walk. That run
alone could not have caught property 4's defect, because Linux answers the lookup that macOS
refuses.

## Proving the Windows branch

The job-object path was written on a macOS host with no Windows machine available. Its
verification is this folder's suite run under coverage on a Windows CI runner
(`windows-latest`): a run on 2026-11-03 gated the folder green at 100% branch coverage with
the `only-on win32` lines counted, and a run on 2026-11-13 re-gated it green at 100% on that
day's code. The gate log shows the unit's verdict,
not each test's, so the live rows below rest on one more fact: none of the three live classes has
a skip path (no decorator, no `setUp` skip, no environment switch), so a passing unit means each of
them ran and passed. What is established, and where:

| Check | Runs on | Establishes |
| --- | --- | --- |
| `mypy --platform win32` | any host | the Windows-only binding type-checks |
| `tests/test_job_containment.py` | every host, fake job API | create → assign → resume order; a failed step kills the child; `kill_tree` and every entry point go through the registered job |
| `tests/test_kernel_job_api.py` | every host, ctypes-marshaled fakes | kernel32 call order and arguments, handle closing, error read before close, the end of a thread walk told apart from a failed one, structure sizes against the documented x64 size (the x86 size is only computed under a 32-bit interpreter, which no run here has used, so it is asserted, not measured) |
| The exited-leader live test (`LiveExitedLeaderTests`) | Windows CI: passed, 2026-11-03 | the grandchild is gone after the timeout; the regression test for property 5 |
| The unregistered-tree live test (`LiveUnregisteredTreeTests`) | Windows CI: passed, 2026-11-03 | the `taskkill` fallback, reached only for a pid this module did not start |
| The nested-session live test (`LiveNestedSessionTreeTests`) | Windows CI: passed, 2026-11-03 | the `taskkill` fallback across a nested run's job, since the test starts the outer child itself; a registered job holding a nested one is still documented behavior, not a run |
| Coverage of the `only-on win32` lines | Windows CI: 100%, 2026-11-03 | the real binding, resume and last-error reader execute |

The pre-job-object Windows exited-leader behavior was never measured: *Measured behavior* keeps
its `taskkill` explanation labeled as analysis.

## Measured behavior

| Path | Result against a 3.0s bound |
| --- | --- |
| Stdlib `subprocess.run(capture_output=True, timeout=3.0)`, Windows | Never returned within 5 minutes |
| `run_bounded`, Windows (`taskkill /F /T` alone, no job object) | Raised `TimeoutExpired` after ~3.4s |
| `run_bounded`, Linux under WSL2 (`killpg`) | Full suite (then 7 tests) in 3.056s, no orphans |

The stdlib hang is the Windows shape: CPython's POSIX `subprocess.run` only `wait()`s the direct
child after its timeout kill instead of re-reading the pipes, so on macOS the same call returns
near the bound but leaves any grandchild running. That leak is what `kill_tree` exists to close.

On macOS, with the direct child exiting at once and its grandchild holding the pipes, a 1.0s bound
through the `getpgid` lookup raised `ProcessLookupError` at the bound and left the grandchild alive.
Signaling the group by pid raises `TimeoutExpired` and leaves no survivor.

On macOS, killing a live tree that crosses a nested run's session: with the single `killpg` the
nested run's child and grandchild both outlived the kill, as they did in the scheduled job that
found the defect. With the freeze-and-walk, neither survives, and the whole live test took 0.55s.

On Windows, the exited-leader case with the job object passed on the CI runner (*Proving the
Windows branch*): the grandchild was gone after the timeout. With `taskkill`, before the job, it
was never measured. The analysis that motivated the job, stated as analysis: an exited process is absent from the
process list even while `Popen` holds its handle, so `taskkill /T /PID <leader>` finds no root
(its exit code is ignored) and kills nothing; the grandchild then keeps the pipes until the 5s
post-kill read expires, giving property 3's output-less `TimeoutExpired` with the grandchild still
running.

`samples/README.md` reproduces both the fixed and unfixed paths by hand, so the difference can be
confirmed in one command rather than taken on trust.

The output is held in memory at a multiple of its size, not at its size. On macOS under CPython
3.14, a child writing 100 MiB to stdout and exiting well inside a 60s bound raised this process's
peak RSS by about 250 MiB through `run_bounded_bytes` and about 350 MiB through `run_bounded`, the
same with `clock=` as without. The multiple is the standard library's: `communicate()` keeps the
chunks it reads in a list, joins them into one `bytes` while that list is still alive, and in text
mode decodes the result into a `str` as well, so a bytes capture holds two full copies at its peak
and a text capture three.

## Adopting it at a call site

The substitution keeps every read and every handler:

```python
# before
proc = subprocess.run(
    argv, cwd=cwd, capture_output=True,
    encoding=stdio_utf8.ENCODING, errors=stdio_utf8.CAPTURE_ERRORS, timeout=timeout,
)
return proc.returncode, proc.stdout + proc.stderr

# after
proc = bounded_run.run_bounded(argv, cwd=cwd, timeout=timeout)
return proc.returncode, proc.stdout + proc.stderr
```

The legitimate exceptions, where a bare capturing `subprocess.run` or `Popen` with a timeout
stays, are the tree kill's own two commands, `taskkill` and the `ps` snapshot: neither spawns
anything, so the grandchild precondition cannot arise. Each carries a marker comment that
silences a reliability linter's rule against a capturing run with a timeout, with that reason.
