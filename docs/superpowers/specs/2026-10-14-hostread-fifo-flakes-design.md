# The hostread and session-git `[fifo]` test flakes

Base: origin/main 0ac8c92.

Files involved:
- `src/heterodyne/sandbox/hostread.py` (`_holders`, `_ruled_out`, `_end_holders`)
- Reviews: `docs/reviews/flakes2-design-r1.md`, `docs/reviews/flakes2-design-r2.md` (both REVISE)
- `tests/test_sandbox_hostread.py`
- `tests/fakes/fake_agent_cli.py` (the fake app-server)
- `tests/test_session_git.py` (`test_snapshot_refuses_links_special_files_and_hard_links`)

**Verdict:**
- **Hostread's main flake is a production bug.** The holder scan reads *any* exiting process of this user as one it can't rule out. It then gives up on the whole scan. On a busy host this discards reads and leaves the copied login on disk until the next start. The fix changes production.
- **A second, smaller hostread flake is in the test.** Its "orphaned" fake app-server exits as soon as its reader dies.
- **The `[fifo]` flake is in the test.** A plant collides with the commit's random fan-out directory.
- **A related production gap was found on the way, and is in scope:** a process whose main thread has exited is cleared as a zombie while its other threads still run. The fix requires proof that the whole thread group is dead.
- No assertion is removed or weakened.

## The flakes

| # | Test | Seen | Failure |
|---|---|---|---|
| 1 | Most of `test_sandbox_hostread.py`. CI names `test_an_unconfirmed_recovery_keeps_the_homes_and_never_raises[signal]` (`:331`), and others. | 4–6 tests per full suite at load 7.5–10; once 1 in 5 runs with the file alone (lead's report) | Various. See below. |
| 2 | `test_sandbox_hostread.py::test_a_crashed_read_is_recovered_at_the_next_start[started]` | 5 of 10 local runs under CPU contention | `PermissionError: /proc/<pid>/environ` in `alive()` at `:223`, or `assert alive(orphan)` false, at `:246` |
| 3 | `test_session_git.py::test_snapshot_refuses_links_special_files_and_hard_links[fifo]` | PR #67, job 115520595610 | `FileExistsError: …/private/objects/cd` |

How flake 1 shows up in a run of the file under process churn (8 runs; 5 failed, 9 failures):
- `unconfirmed[signal]` (×3), `[pidfd]` and `[deadline]`:
  - `assert not os.path.lexists(home)` at `:331`;
  - or `orphan.wait(10)` raising `TimeoutExpired` at `:330`.
- `failed_read[ClosedOutput]` at `:346`, and `descendant_that_left_the_group` at `:488`. Each logged `DIAG_SCRATCH_KEPT` before its expected diagnostic.
- `descendant_that_cant_be_ended` at `:519`, where the home is still there.
- `short_writes` at `:171`, where `read()` is None.

No run showed the `[signal]` fd count (`:325`) failing. Every `[signal]` failure seen was at `:330` or `:331`, from the cause below.

## Root cause 1 (production): an exiting process makes the whole scan "incomplete"

**Mechanism.**
- `_holders` (`hostread.py:178`) lists every process in `/proc` and reads its environment.
- On `PermissionError` (`:197`) it asks `_ruled_out` (`:165`). That function only clears:
  - another user's process;
  - a zombie (`Z`/`X`);
  - one that started before the earliest home.
- Anything else raises `_Unknown`. `_end_holders` (`:220`) does not retry that: the exception ends the scan. Then:
  - `recover` logs `DIAG_RECOVERY_KEPT` and keeps every home (`:255`);
  - a read logs `DIAG_SCRATCH_KEPT` and is discarded (`:326–332`).
- On this kernel (6.17), a process's `/proc/<pid>/environ` returns **EACCES from the moment it starts exiting**. State is still `R`, and it stays EACCES in `Z` as well. It never reads empty, contrary to `_holders`' docstring ("A zombie's environment is empty").
- So any process of this user that started after the home, and is exiting at the moment the scan reaches it, aborts the scan. The process can be wholly unrelated.
- The same thing hits a scan's *own* victim. `_end_holders` sends SIGKILL, sleeps 50 ms and scans again. If the killed process hasn't reached `Z` by then, the rescan aborts.
- When the abort comes before the scan reaches the real holder, nothing is killed. That is the `:330` `TimeoutExpired`.

**Evidence.**
- *The kernel behaviour.* A probe SIGKILLs a child and samples `stat` and `environ` until `Z`:
  - over 5 trials of a 1 GiB child it saw only `(R, EACCES)` (31,172 samples) and `(Z, EACCES)`;
  - a live child reads normally.
- *Exit can be slow.* Over 200 small Python children, the `R` window from SIGKILL to `Z`:
  - idle: p50 0.32 ms, max 4.8 ms;
  - with 8 busy loops on the same 4 CPUs: p50 6.6 ms, max 12.6 ms;
  - it grows with the size of the address space.
- *`PF_EXITING` marks the window.* `/proc/<pid>/stat` field 9 (`flags`) has `PF_EXITING` (0x4) set in every EACCES sample. The only exceptions are samples where `stat` was read before `environ`, a read-order artifact. A live non-dumpable process (`prctl(PR_SET_DUMPABLE, 0)`) reads EACCES with the flag clear.
  - `_holders` already reads `environ` first and `stat` after. That is the order in which the flag is reliable, because the kernel sets it before the address space is torn down and never clears it.
- *Reproduced on demand.* The file runs in a `systemd-run --user --scope` alongside 4 loops that each keep starting and exiting a Python process (`python3 -c "b=bytearray(1<<26)"`).
  - No CPU contention is needed: 5 of 8 runs fail, as tabled above.
  - With CPU contention added (8 busy loops and the test, all on CPUs 0–3), the base fails 7 of 10 runs.
- *The fix holds.* With the fix below prototyped in `hostread.py`:
  - 8 of 8 churn runs pass;
  - 10 of 10 runs pass with contention and churn together.
  - With only the test fix (root cause 2) applied, 6 of 10 still fail. One of them is `crashed[started]` at `:248`, where `recover` kept the home.

**Production impact.**
- Under load, `wsd` would discard trusted usage reads (`DIAG_SCRATCH_KEPT`).
- Start-up recovery would fail (`DIAG_RECOVERY_KEPT`). That leaves each crashed read's scratch home, a copy of the login, on disk until a later start whose scan happens to meet no exiting process.
- It fails safe, in that nothing that holds a home is ever missed. But it fails often, on exactly the busy hosts that run agents.

## Root cause 2 (test): the "orphaned" app-server exits with its reader

**Mechanism.**
- `CRASHING_READER` runs a read with `HZ_FAKE_RATE_FAULT=hang`. The test SIGKILLs the reader once the fake app-server has written its PID (`:240`).
- The client starts the app-server with `stdin=PIPE` (`codex_usage.py:87`), and the reader holds the only write end. Its death is EOF to the fake.
- The fake's `hang` path is `sys.stdin.read()` followed by `return`, so the fake exits. Before the request even arrives, the `for line in sys.stdin` loop ends the same way.
- So the "orphan" the test means to recover is a process that is already exiting:
  - `alive(orphan)` (`:246`) passes only when it runs before the fake has finished dying;
  - while the fake is dying, `alive()`'s environ read raises EACCES (root cause 1's kernel behaviour);
  - once it has died, `alive()` is False.
- When the test does pass, `recover` has "recovered" a process that would have ended anyway. The case the test is named for, an app-server that outlives its reader, goes untested in those runs.
- `alive()`'s comment ("a zombie, not yet reaped, has none") has the same wrong belief as root cause 1. The `wait_for(lambda: not alive(...))` calls at `:250` and `:518` can raise `PermissionError` the same way.

**Evidence.** The test was looped 10 times with 8 busy loops on CPUs 0–3:
- 5 failed: 4 with `PermissionError: [Errno 13] … /proc/<pid>/environ` at `:223` and 1 with `alive()` False, both at `:246`.
- 0 failures with this fix and root cause 1's fix applied (10 of 10, under contention and churn).

`hang` exiting at EOF is relied on elsewhere: `test_sandbox_runtime.py:2311` waits for "its server reads EOF and exits". So `hang` itself must not change.

## Root cause 3 (test): a plant collides with the commit's fan-out directory

**Mechanism.**
- `session_commits` leaves three loose objects in `private/objects`: the blob and tree, fixed (`9b/`, `fc/`), and the commit.
- The commit's hash depends on its timestamps, so its fan-out directory is random.
- The `fifo` plant runs `(private / "objects" / "cd").mkdir()` without `exist_ok`, and `object-dir-link` symlinks a fixed `ab`. Each fails with `FileExistsError` when the commit lands in that directory: 1 in 256 runs each, about 0.8% of suite runs for the two together.

**Evidence.**
- 12 runs of `session_commits` gave fan-out directories `9b`, `fc` and one random directory (`0b`, `9f`, `a2`, `bd`, `59`, `12`, `d9`, `7b`, `36`, `d4`, `6a`, `fc`).
- The CI failure is the `cd` case. It is not load-related.

## Finding (production, pre-existing): a zombie leader with live threads is cleared as a zombie

In scope (lead, 2026-10-14). It is a fail-closed gap in the same function.

- `_ruled_out` treats `Z`/`X` as "holds nothing" (`:173`).
- A process whose main thread has exited (`pthread_exit`) while other threads run is still alive. Its `/proc/<pid>/stat` shows `Z` with `PF_EXITING`.
- Its leader-level `/proc/<pid>/environ` doesn't give its environment:
  - on 6.17 it is EACCES, seen by probe;
  - the kernel also allows an empty read (`environ_read` with no `env_end`, v6.12);
  - or `ESRCH` when the leader's memory map is gone (`__mem_open`, v6.17).
- Today each of those outcomes clears the process without checking its thread group:
  - EACCES goes to `_ruled_out`, where `Z` clears it;
  - an empty environ doesn't match, so the pidfd is closed;
  - `ProcessLookupError` is taken as "gone" (`:203`).
- So a live holder is never killed, and its home is removed under it. That breaks "removed only once a complete scan finds none".

**Evidence (probes, Python 3.12, 6.17).**
- A child whose main thread called `pthread_exit` with one other thread sleeping showed:
  - `stat` `Z` with flags & 4, leader environ EACCES and 2 entries in `task/`;
  - `/proc/<pid>/status` `Threads: 2`;
  - a working `pidfd_open` and signal 0;
  - a readable `/proc/<pid>/task/<tid>/environ` for the worker.
- A pidfd SIGKILL ended the whole group: `wait()` returned -9.
- A true zombie showed `Z`, flags & 4 and `Threads: 1`.

## The fix

### Production (`hostread.py`): what a scan learns about each process

Each process ends in one of four outcomes.

| Outcome | Effect on the scan |
|---|---|
| **match** | pidfd kept and SIGKILLed |
| **clear** | pidfd closed; not a holder |
| **settle** | pidfd closed, nothing sent; the scan is not complete and runs again |
| **unknown** | `_Unknown`: the scan fails at once, and its homes are kept |

**PID and pidfd continuity do not prove task continuity.**
- A worker that execs takes over the leader's PID in `de_thread` (`fs/exec.c`), and the old leader is released.
- Signal 0 through the pidfd still succeeds afterwards.
- So two separate reads of `/proc/<pid>/…` can describe two different tasks. The rules below never clear a process on facts taken from more than one read. Anything they can't prove from one read settles or is unknown.

**The group-dead proof: one read of `/proc/<pid>/status`.** The group is dead if, and only if, a *single* read shows `State:` `Z` or `X` *and* `Threads: 1`. Why one read is enough:
- `proc_single_show` (`fs/proc/base.c`) takes the task for the PID once and runs `proc_pid_status` (`fs/proc/array.c`) on that one task for the whole read. A read too large for the buffer reruns the whole show, so the text returned is always one show's.
  - `State:` is that task's state, printed first (`task_state`).
  - `Threads:` comes later, from `task_sig`. It reads `signal->nr_threads` while holding that same task's `sighand` lock, and prints 0 if the lock can't be taken because the task has already been released (`__exit_signal`, `kernel/exit.c`, clears `sighand`).
- So `Z`/`X` with `Threads: 1` means:
  - the pinned task was a zombie when `State` was printed. A zombie never runs again.
  - at the later locked read, it was still unreleased, and it was the group's only unreleased member, since `nr_threads` counts every member until its `__exit_signal`.
  - No other thread exists to clone or exec, and a zombie can't, so the group is dead from then on.
- The exec race can't produce this pair. Before the switch in `de_thread`, the exec'ing worker and the zombie leader are both counted (`Threads:` ≥ 2). After it, the old leader has been released, so a read pinned to it prints `Threads: 0`, and a read pinned to the new leader prints its live state.
- Checked by probe: a zombie leader's worker execs while the scan samples single `status` reads continuously. Over 30 execs and about 880,000 reads, the pairs were:
  - `Z 2` (400);
  - `R 1`, `S 1` and `D 1`, the new leader;
  - `R 2` (1);
  - `Z 0` and `X 0` (1 each): the released old leader.
  - Never `Z 1` or `X 1`.
- The stat, status, stat "sandwich" is **not** enough, and is not used. Here is how it fails:
  1. The first `stat` shows the old leader `Z`.
  2. A worker execs.
  3. `status` shows `Threads: 1` for the new, live leader.
  4. That leader clones a thread and calls `pthread_exit`.
  5. The second `stat` shows `Z` while the clone runs.

**The rules.** The leader's environ is read first, as today.

- **Non-empty environment.** As today: match if it holds a mark, clear if not, after the pidfd signal-0 check. Only a live memory map can give a non-empty read, so this is the group's environment at that moment. A zombie leader has none. (See "What the rules do and don't claim" under Risks.)
- **Empty, EACCES or ESRCH.** None of these alone proves anything. Read `status` once (`State:`, `Threads:`, `Uid:`), then apply these rules in order. A `status` read missing `State:` or `Threads:`, or one that can't be parsed, → **settle**.
  1. **`Z`/`X` with `Threads: 1`** → **clear**: the group is dead, by the proof above.
  2. **`Z`/`X` with `Threads:` above 1** (a zombie leader with live threads). Read the environment through the workers:
     - list `/proc/<pid>/task`;
     - for each tid other than the PID, read `/proc/<pid>/task/<tid>/environ`. Threads share one memory map, so a non-empty read is the group's environment.
     - The first such read decides **match** or **clear**, after the pidfd signal-0 check.
     - A worker that is gone (`FileNotFoundError`, `ProcessLookupError`), refused or empty is skipped, and the next one is tried. These errors are handled per task and never reach the per-process handler, so they never skip the whole process.
     - If no worker gives a non-empty environment → **settle**. That includes a truncated listing (`next_tid` stops when its current task loses its PID), and a worker that has exec'd, whose old tid is then gone.
  3. **`Z`/`X` with `Threads: 0`** → **settle**. The read was pinned to a task already released, possibly the old leader of an exec. The next scan reads whatever task holds the PID then.
  4. **Empty, in any other state** → **settle**. An empty read doesn't show a genuinely empty environment: it may be an exiting leader's, read just before a worker exec'd. The next scan reads again.
     - A live process of this user with a genuinely empty environment, started after the home, therefore settles on every scan. The deadline then keeps the homes (`TimeoutError`). That is fail-closed, and rare: a process run under `env -i` that is still alive at the scan.
     - No cheaper sound proof was found. Any later read can describe a task that replaced the one read empty.
  5. **EACCES or ESRCH, in any other state.** Read `stat` for `PF_EXITING` and the start time:
     - **`PF_EXITING` set** → **settle**. A single-threaded process mid-exit lands here, and is cleared by rule 1 once it is a zombie. The flag here only chooses settle over unknown, and never clears anything: `PF_EXITING` belongs to one task and never shows the group is dead.
     - **EACCES**: today's two remaining rules. Another user's process (`status` `Uid:`), or one started before the earliest home (`stat` field 22) → **clear**. Otherwise → **unknown**, as today. A live non-dumpable process of this user is still an unknown.
       - These two facts also come from reads other than the environ read. They hold across an exec: `de_thread` copies the leader's start time to the new leader, and a uid change makes the clear "another user's", as today.
     - **ESRCH** → **unknown**.
- **`/proc/<pid>` itself is gone** (`status`, or `stat` when it's needed, raises `FileNotFoundError` or `ProcessLookupError`) → **clear**, as today. The PID directory outlives every thread of the group.

`_holders` returns `(holders, settling)`. `_end_holders`:
- loops while either is non-empty;
- SIGKILLs only `holders`, through their pidfds. A pidfd SIGKILL ends the group that holds the PID at that moment.
- **closes every pidfd it collected in a `finally`, before it checks the deadline**;
- sleeps 50 ms and scans again.

The deadline rules:
- At the deadline it raises `TimeoutError`, whether the last scan found holders or was settling. **A timeout while settling keeps every home**, exactly as a timeout with holders does.
- An **unknown** raises at once. It never waits for the deadline.
- A home is removed only after one scan that found no holder, no settling process and no unknown.

`_ruled_out` and `_holders` get docstrings that say all of this, and the false "a zombie's environment is empty" is removed.

### Tests and fakes

1. **`HZ_FAKE_RATE_FAULT=outlive`** in `fake_agent_cli.py`: an app-server that outlives its reader.
   - It never answers, like `hang`. It survives EOF at both points where the fake can meet it: in the unanswered-request path, after `sys.stdin.read()`, and when the `for line in sys.stdin` loop ends before any request arrives. In both it sleeps until it is killed.
   - It is documented in the fake's header with the other faults.
   - `hang` keeps exiting at EOF, which `test_sandbox_runtime.py:2311` relies on.
2. **`CRASHING_READER` uses `outlive`.** `test_a_crashed_read_is_recovered_at_the_next_start` gets a `finally` that SIGKILLs the orphan by its PID, read from the PID file, with `ProcessLookupError` and a missing or empty file suppressed. The fake now outlives its reader on purpose, so a failing assertion must not leak it.
3. **`alive()` reads state, not the environment.** A process is alive if its `stat` exists, its state isn't `Z`/`X` and `PF_EXITING` is clear. That is enough for this file's fixtures, which are all single-threaded. It is not used to show that a group has died; the zombie-leader tests wait on the whole group (below).
4. **Plant into a fan-out directory that isn't there.**
   - A helper `absent_fanout(private) -> str` returns the first of `00`…`ff` with no entry under `private/objects`.
   - The `fifo` and `object-dir-link` plants use it in place of `cd` and `ab`.
   - The plant is still a real fan-out directory holding a 38-character name, so the rejection and secrecy assertions are unchanged.
5. **Drop `once_a_zombie`** in `test_an_app_server_that_cannot_be_reaped_is_no_reading` (`:370`). It was added in T8 r3 to step around root cause 1. With settling, the real `_end_holders` covers that window, so the test exercises the production path.

## Tests

Most of the new tests drive the scan through module seams. These are the existing `_environ` and `_stat`, plus new one-line readers for `status` (returning State, Threads and Uid from one read), a task listing and a task's environ. They use a fake clock: `hostread.time.monotonic` and `hostread.time.sleep` are replaced together, and the sleep advances the clock and counts calls. So they are deterministic, need no load, and assert timing by the number of calls, not by wall time.

- **The three leader outcomes for a zombie leader** (parametrized: EACCES, empty, ESRCH; `Z`, `Threads: 2`, a worker whose environ holds the mark):
  - `recover` SIGKILLs it, as recorded by `pidfd_send_signal`, and then removes the home.
  - Today all three clear it, and the home is removed without a kill.
- **A true zombie** for each of the three (`Z`, `Threads: 1`): cleared at once, no sleep, home removed, as today.
- **Truncated enumeration:** `Z`, `Threads: 2` and a listing with only the leader:
  - settles, sending no signal;
  - when the seam reports `Threads: 1` on the third scan, the home is removed after exactly two sleeps;
  - if the seam keeps reporting `Threads: 2`, the deadline passes, `DIAG_RECOVERY_KEPT` is logged, the home is kept and `open_fds()` is unchanged.
- **A worker that vanishes while another holder survives:** tasks are leader, w1 and w2. w1's environ raises `FileNotFoundError` and w2's holds the mark. The process is matched and SIGKILLed, not skipped. A second case has w1 raise EACCES, with the same result.
- **No readable worker:** every worker is gone, refused or empty while `Threads: 2` → settle, and kept at the deadline.
- **Settling, then removed** (fix 1): an unrelated unreadable process of this user that started after the home, `R` with `PF_EXITING` for two scans, then `Z` with `Threads: 1`:
  - `DIAG_RECOVERED` and the home removed, with no signal sent to it.
  - Today this logs `DIAG_RECOVERY_KEPT`.
- **Settling past the deadline:** the same process stays exiting → kept, `DIAG_RECOVERY_KEPT`, no pidfd leaked.
- **Unknown fails at once:** a live non-dumpable process of this user, EACCES with `PF_EXITING` clear → `DIAG_RECOVERY_KEPT` after **zero** sleeps and one scan. Today this is the same, and the test pins it against the new settle path.
- **The read path settles too:** `read()` with an unrelated exiting process present for one scan returns `WANTED` and logs nothing. Today it logs `DIAG_SCRATCH_KEPT` and returns None.
- **The group-dead proof needs both facts from one read** (seams):
  - `Z` with `Threads: 0` → settles, no signal, kept at the deadline.
  - A `status` text missing `Threads:` or `State:` → settles. It is never cleared.
  - EACCES, then `status` `S` with `Threads: 1` while the `stat` seam still reports `Z` with `PF_EXITING` (the replaced leader's stale facts) → not cleared. It settles, and the home is kept at the deadline.
- **Exec between the reads, B1** (seams, forced): the leader environ raises EACCES, `status` reports `Z` with `Threads: 2`, and the task listing's only worker raises `FileNotFoundError` because it has exec'd → settle. The next scan's leader environ holds the mark → matched and SIGKILLed, then removed. The home is never removed before the kill.
- **Exec between the reads, B2** (seams, forced): `_environ` returns `b""`, then `status` reports the new leader, `S` with `Threads: 1` → settle, no removal. The next scan's environ holds the mark → matched and killed.
- **A genuinely empty environment:** a live process of this user started after the home whose environ is always `b""` → settles on every scan, `DIAG_RECOVERY_KEPT` at the deadline, home kept, no pidfd leaked.
- **Live exec between the reads** (no fake `/proc`; one seam hook):
  - A child of the test with `CODEX_HOME` set to a stamped home. Its main thread calls `pthread_exit` while a worker waits on a pipe; on a byte, the worker execs a sleeper that inherits the environment.
  - The test wraps the `status` reader. Before its first call for the child's PID, the wrapper writes the byte, then waits (bounded, 10 s) until `/proc/<pid>/exe` names the sleeper. Then it calls the real reader.
  - So the leader environ was read from the zombie leader (EACCES), and `status` describes the new image (`S`, `Threads: 1`, `PF_EXITING` clear). Under rule 5 that is an unknown. The first `recover` therefore logs `DIAG_RECOVERY_KEPT` and keeps the home, with no signal sent.
  - A second `recover`, with the wrapper removed, reads the new image's environ, which holds the mark. It SIGKILLs the child and removes the home. The test asserts `Popen.wait(10) == -SIGKILL`.
  - The same harness with the hook placed before the environ read is the control: one scan matches.
- **Live zombie-leader regression** (no seams):
  - A child of the test whose main thread calls `pthread_exit` while a worker sleeps, with `CODEX_HOME` set to a stamped home. `recover` kills it and removes the home.
  - **Readiness:** before calling `recover`, the test waits (bounded, 10 s) for one `/proc/<pid>/status` read showing `Z` with `Threads:` of 2 or more, and fails if it never does. Otherwise `recover` could kill the child before its leader exits, and pass without exercising the regression.
  - Group death is shown by the child's `Popen.wait(10) == -SIGKILL`. Waiting on a direct child returns only once every thread of its group has exited, which `alive()` can't show.
  - A `finally` SIGKILLs and reaps it by its handle.
- **`crashed[started]`** asserts `alive(orphan)` before `recover`. With `outlive` that holds on every run. The `copied` case is unchanged.
- **`[fifo]` / `[object-dir-link]`:** a check that `absent_fanout` is absent, plus one run with `cd/` and `ab/` planted first. Today that collision is the CI failure.
- **Acceptance under load, bounded.** The implementation PR records the hostread file passing 10 of 10 runs under the churn and contention harness. Today it fails 7 of 10.
  - The harness is a documented command, not per-PR CI: `systemd-run --user --scope` with a runtime cap, 8 busy loops and 4 exit loops on CPUs 0–3, and the tests pinned to the same CPUs.
  - It runs in bursts of a few minutes, announced to the other workers, and the scope is stopped as each burst ends.

## Risks

- **A host that is always exiting something.** The scan settles only while it meets an exiting process or a zombie leader it can't read through, and an exit takes ms. A host spawning processes non-stop could keep the scan settling until the deadline (`RECOVER_SECONDS`, 10 s). The result is `TimeoutError` and kept homes: today's outcome, reached more slowly. Never a wrong removal.
- **Kernel interfaces.** The design relies on three of them:
  - `PF_EXITING` (0x4 in `include/linux/sched.h`, long-standing);
  - `stat` field 9;
  - `status`'s `Threads:`.

  If the flag ever read clear, an EACCES process would fall to today's rules under rule 5: cleared as another user's or as older than the home, otherwise unknown. If `Threads:` were missing, the group-dead rule couldn't fire, and a zombie would settle until the deadline. Both fail closed.
- **What the rules do and don't claim across exec and PID reuse.**
  - A process is judged by the environment of the image whose environ was read. A process that execs after a non-empty read is judged by the image read, as today; the scan doesn't claim to see an exec it raced.
  - Only two things are cleared without a non-empty environment:
    - the group-dead proof, which comes from one read of one task;
    - today's other-user and early-start rules, whose facts survive `de_thread`.
  - PID and pidfd continuity are used only to aim the SIGKILL and to confirm a match. They are never used to show that two reads saw the same task.
  - Every other case settles or is unknown, and keeps the home.
- **A genuinely empty environment.** A live process of this user, started after the home, whose environ is empty settles until the deadline and keeps the homes. This is accepted (lead, r2): it is fail-closed, and needs a process started with no environment that is still alive at the scan.
- **Cost.** It adds one `status` read per unreadable or empty process, and a `task/` listing only for a zombie leader with live threads. Both are rare.

## Out of scope

- Per-PR CI load testing. The churn harness is an acceptance check, as in y3e88.
