# Design r1: a tmux start that fails must not report success (btq-n27uo)

**Problem.** When tmux 3.4 cannot create its server socket, `new-session` prints `error creating <path> (<errno>)` on stderr and **exits 0**. This happens when the `-S` directory is missing or not writable. `Tmux.new_session` checks only the exit status, so it returns normally even though no server or session exists. `AdminAgent.ensure_running` then reports `launched`. The failure only shows up later, and misleadingly: no SessionStart arrives, and the agent is reported as not ready or stuck. react-impl found this during tmux-leak Amendment 1 (btq-q1r4p). `test_a_launcher_released_after_the_final_unlink_starts_nothing` already tolerates it.

**Scope.** `src/heterodyne/tmux.py` and its offline tests. The only product user is admind (`cli.py`: `Tmux(TMUX_SOCKET, launcher=tmux_launcher(s))`, session `admin`). wsd has no tmux use on main b136b30. Callers don't change.

## 1. Inventory and probe evidence (tmux 3.4)

Every subcommand the product runs, all from `Tmux`:

| Method | Subcommand | Can it exit 0 on failure? |
|---|---|---|
| `new_session` | `start-server ; set-option -g remain-on-exit on ; new-session -d -s N -x 200 -y 50 -c CWD -- argv` | **Yes**: socket not creatable (A1, A11, B1); cwd missing (E4) |
| `pane_dead` | `display-message -p -t =N: #{pane_dead}` | **Yes**: missing session prints nothing (E10) |
| `has_session` | `has-session -t =N` | No (A2, C) |
| `paste` | `load-buffer -b B -`, `paste-buffer -p -d -b B -t =N:`, `send-keys -t =N: Enter`, `delete-buffer -b B` | No (A4-A7, C, E13-E15, E18) |
| `send_key` | `send-keys -t =N: KEY` | No for a missing target (E15). An unknown key name is typed literally (E16), but the product only sends `Escape` and `Enter` |
| `capture` | `capture-pane -p -J -S -L -t =N:` | No (A8, C, E17) |
| `kill`, `kill_server` | `kill-session -t =N`, `kill-server` | No (A9, A10, C, E19), and both are `check=False` anyway |

**How the probe was run.** It is `spikes/tmux-exit0/probe.sh`, committed with this doc and self-cleaning:
- every server uses a private `TMUX_TMPDIR` and a `-S` or `-L hz-probe-*` socket;
- every server started is killed by PID;
- the run ends with `servers left: none`.

Each row shows the exit status, `[stdout]`, and stderr, with `$B` standing for the scratch directory. These are the rows that matter:

```
A1 new-session chain, -S dir missing          0  []         error creating $B/nodir/sock (No such file or directory)
A11 same, behind a launcher prefix (env)      0  []         error creating $B/nodir/l.sock (No such file or directory)
B1 new-session chain, -S dir not writable     0  []         error creating $B/ro/sock (Permission denied)
A2-A10 every other command, -S dir missing    1  []         error connecting to $B/nodir/sock (No such file or directory)
C  every other command, -L, no server         1  []         error connecting to $B/tmpdir/tmux-UID/hz-probe-none (...)
D1 -L with TMUX_TMPDIR missing                0  []         (none: the server silently starts in /tmp/tmux-UID instead)
E2 duplicate session                          1  []         duplicate session: admin
E3 chain with an invalid set-option           1  []         invalid option: no-such-option
E4 cwd missing                                0  []         (none)
E5   ... its start|current path               0  [$B/missing|~]   (the pane runs in the home directory)
E6/E7 argv not found                          0  ; pane_dead status [1 127]   (session exists, pane dead: alive() sees it)
E8/E9 name "a:b"                              0  ; list-sessions shows a_b    (tmux renames it)
E10 display-message, missing session          0  []         (none)
E11 display-message, live pane                0  [0]
E16 send-keys, unknown key name               0  []         (typed literally)
```

The proposed check is `new-session -P -F '#{session_name}'`, which prints the name of the session it created:

```
F1 ok                                         0  [admin]
F2 duplicate                                  1  []         duplicate session: admin
F3 name "a:b"                                 0  [a_b]
F4 argv exits at once                         0  [quick]
F5 -S dir missing                             0  []         error creating $B/nodir/sock (No such file or directory)
F6 -S dir not writable                        0  []         error creating $B/ro/sock (Permission denied)
F7 behind a launcher prefix (env), ok         0  [admin]
F8 behind a launcher prefix, dir missing      0  []         error creating $B/nodir/l.sock (No such file or directory)
```

Earlier ad hoc runs, folded into the script's cases or confirmed by them, also showed:
- a TMUX_TMPDIR that is a regular file, and a `-S` path that is too long, both exit 1;
- a `-S` path that is a regular file is replaced by the socket and succeeds;
- `has-session` after the server is killed exits 1 with `no server running on <path>`.

## 2. Approach: the start reports what it created, in the same invocation

`new_session` appends `-P -F '#{session_name}'` to `new-session`. It succeeds only if the exit status is 0 **and** the last non-empty stdout line equals `name`. Everything else raises `TmuxError`. Nothing else in the wrapper changes, apart from the two small items in section 3.

Why this approach and not the alternatives:
- **Stdout, not stderr.** stderr is not a reliable failure signal:
  - On the launcher path it also carries systemd-run's own output.
  - Its wording is tmux's and changes between versions.
  - A successful start writes nothing there today, but nothing guarantees that.

  Positive confirmation can't be faked by noise: an empty or wrong stdout is a failure, whatever stderr says. Existing callers that see stderr noise are unaffected, because stderr is still only read for the message.
- **No second call** (for example, `has-session` afterwards):
  - The same process both does the work and reports the result, so there is no window in which the server dies or another client acts between the two steps.
  - It adds no extra tmux process per start.
  - A follow-up call would also run outside the launcher. In the launch-lock test, any second guarded call after the barrier raises `WatchdogError`, which is not a `TmuxError`.
- **Only `new-session` needs it.** Every other subcommand exits 1 on every failure probed (section 1). Treating their stderr as an error would add nothing and would risk false failures.
- **Name mangling is caught for free.** tmux rewrites `:` and `.` in names (F3). Today that "succeeds" with a session the wrapper can never find again.

## 3. Exact behaviour

### `Tmux.new_session(name, cwd, argv)`
1. **cwd check.** If `not cwd.is_dir()`, raise `TmuxError(f"tmux session directory is missing: {cwd}")` before running anything. Without this check, tmux exits 0 and runs the pane in the home directory (E4, E5), which is not admind's configured `workdir`. This is a check-then-use race, which is acceptable: it catches a misconfigured or missing directory, not a concurrent rename.
2. **The chain.** Same as today, with `-P -F '#{session_name}'` added after `new-session -d`.
3. **Exit status not 0.** No change:
   - on the direct path, `_run` raises `TmuxError(f"tmux start-server failed: {stderr}")` (the first argument is still `start-server`);
   - on the launcher path, the error is `TmuxError(LAUNCHER_FAILED)`.
4. **Exit status 0.** Let `reported` be the last non-empty line of `stdout.decode("utf-8", "replace")`, stripped, or `""` if there is none.
   - If `reported == name`, return.
   - If `reported` is non-empty but different (mangled), first run `kill-session -t =<reported>` with `check=False`. That session was created by this call: a pre-existing one would have made it exit 1 (F2). Then raise.
   - The error raised:
     - direct path: `TmuxError(f"tmux new-session did not create {name!r}: {detail}")`, where `detail` is the stripped stderr, or `reported {reported!r}`, or `no session reported` when both are empty. This matches `_run`'s style;
     - launcher path: a new constant, `TmuxError(NO_SESSION)`, with `NO_SESSION = "tmux reported a start through the launcher, but no session exists"`. It carries no detail, like `LAUNCHER_FAILED`, for the same reason: the combined output belongs to the launcher.
5. The type stays plain `TmuxError`, with no subclass. Every caller already handles it:
   - `Daemon.start_agent` catches it, sets `stuck = reason(exc)` ("a tmux command failed"), and audits `start-failed` with `error=TmuxError`;
   - `ensure_running` has already incremented `launches_without_start` and keeps `replace_pending`, so repeated failures reach the existing `AgentStuck` limit. `!tail` and `!new` work as before.

   The visible change is that a broken start now fails at once, in the right place, instead of looking like a launch that never reported in.

### `Tmux.pane_dead(name)`
Change it to `return stdout.strip() != "0"`, so only an explicit `0` means a live pane. Today, the empty output for a session that no longer exists (E10, exit 0) reads as "alive". The only caller is `AdminAgent.alive()`, which checks `has_session` first, so the empty case is a lost race: the session vanished between the two calls. "Not live" is the right answer there. It leads to `kill` (a no-op) and a relaunch under the crash-loop limit. tmux never printed anything other than `0` or `1` for a pane that exists (E7, E11).

### Recorded, not changed
- **D1, TMUX_TMPDIR missing on the `-L` path.** tmux silently uses `/tmp/tmux-<uid>`. Every call from one admind process shares the environment, so they all agree on the socket. This is not a failure.
- **E16, unknown key names typed literally.** `send_key` callers pass constants only.
- **systemd-run and stdout.** The launcher path assumes `systemd-run --scope --quiet` writes nothing of its own to stdout, which the last-line rule tolerates anyway; the child's stdout passes through. The probe used `env` as the prefix, because the standing rules forbid starting units. If the assumption were wrong, every launcher start would fail loudly with `NO_SESSION` on the first admind restart, so the post-merge restart check (it must come up and reach SessionStart) settles it.

## 4. Offline tests

These need no live units. Real tmux only goes through `tests/tmux_guard.py`.

**Fakes that need updating.** `Run` in `tests/test_tmux_exec.py` and `Recorder` in `tests/test_tmux_launcher.py` now answer a call containing `new-session` with stdout `b"<name>\n"`, taken from the argv after `-s`. Every existing test keeps its assertions, including:
- `test_new_session_prefixes_only_the_server_start`;
- `test_start_always_uses_the_launcher_and_never_probes` (no `list-sessions`, no extra call);
- the exact-argv pins, which gain `-P -F #{session_name}`.

**New fake-subprocess tests** (`tests/test_tmux_exec.py`):
1. Exit 0 with empty stdout and stderr `error creating ...`:
   - direct path: `TmuxError`, whose message names the session and contains the stderr detail;
   - launcher path: `str(exc) == NO_SESSION`, with no stderr in it ("secret detail" style);
   - in both cases, exactly one `subprocess.run` call.
2. Exit 0 with stdout `a_b\n` for name `a:b`: `TmuxError`, and the next call is `kill-session -t =a_b` with check off. Both paths.
3. Exit 0 with stdout `noise\nadmin\n`: success (the last-line rule).
4. A missing `cwd`: `TmuxError`, and **no** `subprocess.run` call.
5. `pane_dead` with stdout `b"0\n"` returns False; with `b"1\n"`, `b""` or `b"x"` it returns True.

**Real tmux: no new test.** A guarded server can't be given a missing socket directory:
- `GuardedTmux` opens the launch lock in the socket's parent directory;
- a missing parent therefore raises `WatchdogError` before tmux runs;
- `test_tmux_scan` forbids passing `socket_path=` directly.

The real exit-0 path is already exercised by the next test: after the watchdog's rename, tmux's socket directory is gone.

**Existing test, tightened.** In `test_a_launcher_released_after_the_final_unlink_starts_nothing` (`tests/test_tmux_launch_lock.py`):
- **Change:** a successful return now fails the test. `TmuxError` is the only accepted outcome.
- **Unchanged:** the recorded single `_exec` result must still exit 0 with ENOENT on stderr when the installed tmux behaves like 3.4, or exit non-zero otherwise.
- **Removed:** the comment that documents the tolerated exit 0.

**Gates.**
- `uv run pytest -q` must pass, including `test_tmux_scan`: the new test uses `new_test_tmux` / `GuardedTmux` only.
- `uvx pre-commit run --all-files` must pass.

## 5. Out of scope
- Retrying a failed start.
- Classifying the errno.
- Changing `reason()` wording.
- Validating names up front: admind only uses `admin`.
- Any wsd change: wsd has no tmux.
