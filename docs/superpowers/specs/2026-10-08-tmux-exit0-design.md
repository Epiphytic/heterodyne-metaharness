# Design r2: a tmux start that fails must not report success (btq-n27uo)

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

The proposed check is `new-session -P -F 'hz-started <nonce> #{session_id} #{session_name}'`. It prints one marker line for the session it created, carrying the session's server-unique id. The probe uses the nonce `0123abcd`:

```
F1 ok                                         0  [hz-started 0123abcd $0 admin]
F2 duplicate                                  1  []         duplicate session: admin
F3 name "a:b"                                 0  [hz-started 0123abcd $1 a_b]
F4 argv exits at once                         0  [hz-started 0123abcd $2 quick]
F5 -S dir missing                             0  []         error creating $B/nodir/sock (No such file or directory)
F6 -S dir not writable                        0  []         error creating $B/ro/sock (Permission denied)
F7 behind a launcher prefix (env), ok         0  [hz-started 0123abcd $0 admin]
F8 behind a launcher prefix, dir missing      0  []         error creating $B/nodir/l.sock (No such file or directory)
F9 kill-session -t <id of a_b>                0  []
F10 sessions left                             0  [admin|quick]   (only a_b went)
F11 the same id again                         1  []         can't find session: $1
```

I also ran a one-off check of the revised `GateLauncher` script (section 4) with a private `-S` socket, `$D` being the scratch directory:
- **Success:** the start exits 0. The caller's stdout gets the marker line, and `launch` gets the same line.
- **Missing socket directory:** the start exits 0 with an empty stdout, and `launch` holds `error creating $D/nodir/s.sock (No such file or directory)`.
- **No held pipe:** the caller's capture returned while the server kept running, so the server doesn't hold the caller's stdout pipe.

Earlier ad hoc runs, folded into the script's cases or confirmed by them, also showed:
- a TMUX_TMPDIR that is a regular file, and a `-S` path that is too long, both exit 1;
- a `-S` path that is a regular file is replaced by the socket and succeeds;
- `has-session` after the server is killed exits 1 with `no server running on <path>`.

## 2. Approach: the start reports what it created, in the same invocation

`new_session` appends `-P -F 'hz-started <nonce> #{session_id} #{session_name}'` to `new-session`, with a fresh `uuid4().hex` nonce per call. It succeeds only if the exit status is 0 **and** stdout contains the marker line for this nonce with `#{session_name} == name`. Everything else raises `TmuxError`.

Any other stdout line is ignored: a launcher's own output, or a line that merely looks like a session name. Only the marker comes from this invocation's `new-session`, so only the marker can confirm the start or name a session to clean up. Nothing else in the wrapper changes, apart from the two small items in section 3.

Why this approach and not the alternatives:
- **Stdout, not stderr.** stderr is not a reliable failure signal:
  - On the launcher path it also carries systemd-run's own output.
  - Its wording is tmux's and changes between versions.
  - A successful start writes nothing there today, but nothing guarantees that.

  Positive confirmation can't be faked by noise. Stray output never carries this call's nonce, and a missing marker is a failure whatever else is printed. Existing callers that see stderr noise are unaffected, because stderr is still only read for the message.
- **No second call** (for example, `has-session` afterwards):
  - The same process both does the work and reports the result, so there is no window in which the server dies or another client acts between the two steps.
  - It adds no extra tmux process per start.
  - A follow-up call would also run outside the launcher. In the launch-lock test, any second guarded call after the barrier raises `WatchdogError`, which is not a `TmuxError`.
- **Only `new-session` needs it.** Every other subcommand exits 1 on every failure probed (section 1). Treating their stderr as an error would add nothing and would risk false failures.
- **Name mangling is caught for free.** tmux rewrites `:` and `.` in names (F3). Today that "succeeds" with a session the wrapper can never find again.
- **The session id is the cleanup target, never a name.** `#{session_id}` (`$N`) is unique for the server's lifetime and is never reused: F11 refuses the same id once it is gone. Killing by that id can only remove the session this call created (F9, F10). An unrelated session can't be hit, whatever its name and whatever else stdout contains.
- **Trust boundary.** The launcher prefix is trusted code in admind's own configuration. The nonce separates tmux's answer from ordinary output; it is not a defence against a hostile prefix that copies its argv.

## 3. Exact behaviour

### `Tmux.new_session(name, cwd, argv)`
1. **cwd check.** If `not cwd.is_dir()`, raise `TmuxError(f"tmux session directory is missing: {cwd}")` before running anything. Without this check, tmux exits 0 and runs the pane in the home directory (E4, E5), which is not admind's configured `workdir`. This is a check-then-use race, which is acceptable: it catches a misconfigured or missing directory, not a concurrent rename.
2. **The chain.** Same as today, with `-P -F f"hz-started {nonce} #{{session_id}} #{{session_name}}"` added after `new-session -d`, where `nonce = uuid.uuid4().hex`.
3. **Exit status not 0.** No change:
   - on the direct path, `_run` raises `TmuxError(f"tmux start-server failed: {stderr}")` (the first argument is still `start-server`);
   - on the launcher path, the error is `TmuxError(LAUNCHER_FAILED)`.
4. **Exit status 0.** Decode stdout as `utf-8` with `replace`. Find the lines that start with `f"hz-started {nonce} "` and split each one at most twice into `marker, sid, reported`; `reported` may itself contain spaces.
   - Exactly one marker line with `sid` matching `\$[0-9]+` and `reported == name`: return.
   - Exactly one well-formed marker line but `reported != name` (tmux renamed it): first run `kill-session -t <sid>` with `check=False`. The session with that id was created by this invocation, so nothing else can be removed. Then raise.
   - No marker line, more than one, or a malformed `sid`: clean nothing up, and raise. An unverified line is never a cleanup target.
   - The error raised:
     - direct path: `TmuxError(f"tmux new-session did not create {name!r}: {detail}")`. `detail` is `renamed to {reported!r}` in the rename case. Otherwise it is the stripped stderr, or `no session reported` when stderr is empty. This matches `_run`'s style;
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
- **systemd-run and stdout.** The launcher path assumes that `systemd-run --scope` passes the child's stdout through: a scope runs the command in the foreground, with its stdio. Any output systemd-run adds itself is ignored, because only the marker line counts. The probe used `env` as the prefix, because the standing rules forbid starting units. If the assumption were wrong, every launcher start would fail loudly with `NO_SESSION` on the first admind restart, so the post-merge restart check (it must come up and reach SessionStart) settles it.

## 4. Offline tests

These need no live units. Real tmux only goes through `tests/tmux_guard.py`.

**Fakes that need updating.** `Run` in `tests/test_tmux_exec.py` and `Recorder` in `tests/test_tmux_launcher.py` must answer a call containing `new-session` with the marker line. They build it from the argv: the nonce is taken from the `-F` value, the name from the argument after `-s`, and the id is `$0`. Every existing test keeps its assertions, including:
- `test_new_session_prefixes_only_the_server_start`;
- `test_start_always_uses_the_launcher_and_never_probes` (no `list-sessions`, no extra call);
- the exact-argv pins, which gain `-P -F <marker format>`; the pins check the format with the nonce masked.

**`GateLauncher` (`tests/tmux_guard.py`) must forward stdout.** Today its script redirects all of tmux's output into `launch.tmp`, so the caller's stdout is always empty and every successful gated start would fail the new check. The new script:

```sh
... wait for the gate, as today ...
"$@" > "$0/out.tmp" 2> "$0/err.tmp"; status=$?
cat "$0/out.tmp"                                   # the caller sees tmux's stdout (the marker)
cat "$0/out.tmp" "$0/err.tmp" > "$0/launch.tmp"    # the diagnostic file keeps both
rm -f "$0/out.tmp" "$0/err.tmp"; mv "$0/launch.tmp" "$0/launch"; exit $status
```

What stays the same:
- the `started` handshake;
- the exit status;
- `"$@"` keeps every inherited descriptor, so the launch lock still reaches the server;
- tmux still writes only to files, never to the caller's pipe, so a server can't hold that pipe open;
- `launch` still contains tmux's stderr, which `test_a_launch_released_after_the_rename_starts_nothing` reads for `No such file or directory`.

The one difference is ordering inside `launch`: stdout now comes before stderr. No test depends on that order.

**Successful gated starts are part of validation.** `test_a_gated_start_hands_the_lock_to_the_server` (`tests/test_tmux_launch_lock.py`) must pass unchanged. It is now also the real-tmux test that the marker survives a launcher prefix. Every other `new_test_tmux(GateLauncher(...))` start that is expected to succeed must also pass unchanged.

**New fake-subprocess tests** (`tests/test_tmux_exec.py`):
1. Exit 0 with empty stdout and stderr `error creating ...`:
   - direct path: `TmuxError`, whose message names the session and contains the stderr detail;
   - launcher path: `str(exc) == NO_SESSION`, with no stderr in it ("secret detail" style);
   - in both cases, exactly one `subprocess.run` call, so nothing is cleaned up.
2. The marker reports `$3 a_b` for name `a:b`: `TmuxError`, and the next call is exactly `kill-session -t $3` with check off. Both paths.
3. Trailing and leading noise around a valid marker (`noise\n<marker $0 admin>\nother\n`): success, with no further call.
4. **No unrelated cleanup.** Stdout `admin\nother\n`, with no marker, and stdout carrying a marker line with a different nonce, `hz-started <wrong> $5 other`: both raise `TmuxError` and make no further `subprocess.run` call. In particular, there is no `kill-session` for `other` or `$5`.
5. Two marker lines, or a marker with a malformed id (`hz-started <nonce> 5 admin`): `TmuxError`, no cleanup.
6. A missing `cwd`: `TmuxError`, and **no** `subprocess.run` call.
7. `pane_dead` with stdout `b"0\n"` returns False; with `b"1\n"`, `b""` or `b"x"` it returns True.

**Real tmux: no new test.** A guarded server can't be given a missing socket directory:
- `GuardedTmux` opens the launch lock in the socket's parent directory;
- a missing parent therefore raises `WatchdogError` before tmux runs;
- `test_tmux_scan` forbids passing `socket_path=` directly.

The real exit-0 path is already exercised by the next test: after the watchdog's rename, tmux's socket directory is gone. The real success path is exercised by every existing guarded start, gated or not.

**Existing test, tightened.** In `test_a_launcher_released_after_the_final_unlink_starts_nothing` (`tests/test_tmux_launch_lock.py`):
- **Change:** a successful return now fails the test. `TmuxError` is the only accepted outcome.
- **Unchanged:** the recorded single `_exec` result must still exit 0 with ENOENT on stderr when the installed tmux behaves like 3.4, or exit non-zero otherwise.
- **Removed:** the comment that documents the tolerated exit 0.

**Gates.**
- `uv run pytest -q` must pass, including `test_tmux_scan`, the gated-start tests and both launch-after-rename tests.
- `uvx pre-commit run --all-files` must pass.

## 5. Out of scope
- Retrying a failed start.
- Classifying the errno.
- Changing `reason()` wording.
- Validating names up front: admind only uses `admin`.
- Any wsd change: wsd has no tmux.

## Revision history
- **r1 (a3d8675)**
- **r2, from the r1 review:**
  - **Confirmation.** It comes from a nonce marker line carrying `#{session_id}` instead of the last stdout line.
  - **Cleanup.** A rename is cleaned up only by that id. Unverified lines are never cleanup targets. New tests cover noise, missing markers and wrong nonces.
  - **GateLauncher.** It forwards stdout and keeps its diagnostic file, exit status and descriptor inheritance. Successful gated starts are now part of validation.
  - **Probe.** Section F was rerun with the marker, adding F9 to F11.
