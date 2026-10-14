# The hostread and session-git `[fifo]` test flakes

Base: origin/main 0ac8c92.

Files involved:
- `src/heterodyne/sandbox/hostread.py` (`_holders`, `_ruled_out`, `_end_holders`)
- Review: `docs/reviews/flakes2-design-r1.md` (REVISE)
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

The scan reads, in this order and each after the last:
1. the leader's environ;
2. `stat` (state, flags);
3. when needed, `status` (`Uid:`, `Threads:`).

That order matters. The kernel sets `PF_EXITING` before it tears down the memory map, and never clears it. Each process then ends in one of four outcomes.

| Outcome | Effect on the scan |
|---|---|
| **match** | pidfd kept and SIGKILLed |
| **clear** | pidfd closed; not a holder |
| **settle** | pidfd closed, nothing sent; the scan is not complete and runs again |
| **unknown** | `_Unknown`: the scan fails at once, and its homes are kept |

The leader's environ read leads to one of these outcomes:

- **Non-empty environment.** As today: match if it holds a mark, clear if not. The pidfd signal-0 identity check is unchanged.
- **Empty, EACCES or ESRCH.** None of these alone proves anything. Read `stat`, then go through these rules in order:
  1. **Group dead:** state `Z`/`X` *and* `Threads: 1` in `status`, read after `stat` → clear.
     - `Threads:` is the kernel's count of the group's unreleased threads (`signal->nr_threads`). It does not depend on listing `task/`. A worker leaves the count only once it has finished exiting, so a worker still exiting keeps it above 1.
     - A zombie leader is released only after the group is empty, so `Z` with `Threads: 1` means no thread is left.
     - This is the only way a `Z` process is cleared. `PF_EXITING` belongs to one task and never by itself shows the group is dead.
  2. **Zombie leader with live threads:** state `Z`/`X` and `Threads:` above 1. Read the environment through the workers:
     - list `/proc/<pid>/task`;
     - for each tid other than the leader, read `/proc/<pid>/task/<tid>/environ`. The threads share one memory map, so one successful non-empty read is the group's environment.
     - The first such read decides match or clear, after the same pidfd signal-0 check.
     - A worker that is gone (`FileNotFoundError`, `ProcessLookupError`), refused (EACCES) or empty is skipped, and the next one is tried. These errors are handled per task and never reach the per-process handler, so they never skip the whole process.
     - If no worker gives a readable environment → **settle**. That includes a listing that shows no worker even though `Threads:` is above 1 (truncated enumeration: `next_tid` stops when its current task loses its PID).
  3. **Exiting:** `PF_EXITING` set → **settle**. A single-threaded process mid-exit lands here and is cleared by rule 1 once it is `Z`.
  4. **Otherwise**, the leader is running and not exiting:
     - *Empty:* a genuine empty environment, because the memory map was present when the environ was read. Otherwise `PF_EXITING` would have been set by the `stat` read. → **clear**, as today.
     - *EACCES:* `_ruled_out`'s other two rules as today: another user's, or started before the earliest home → clear. Otherwise → **unknown**, as today. A live non-dumpable process of this user is still an unknown.
     - *ESRCH:* there is no memory map, but the process is neither exiting nor a zombie. This should not happen for a user process. → **unknown**.
- **`/proc/<pid>` itself is gone** (`stat` or `status` raises `FileNotFoundError` or `ProcessLookupError`) → clear, as today. The PID directory outlives every thread of the group, so its absence means the group is gone.

`_holders` returns `(holders, settling)`. `_end_holders`:
- loops while either is non-empty;
- SIGKILLs only `holders`, through their pidfds. A pidfd SIGKILL ends the whole group.
- **closes every pidfd it collected in a `finally`, before it checks the deadline**;
- sleeps 50 ms and scans again.

The deadline rules:
- At the deadline it raises `TimeoutError`, whether the last scan found holders or was settling. **A timeout while settling keeps every home**, exactly as a timeout with holders does.
- An **unknown** raises at once. It never waits for the deadline.
- A home is removed only after one scan that found no holder, no settling process and no unknown.

Waiting and rescanning stay conservative across exec and PID reuse. A rescan sees whatever process holds the PID then, with a fresh pidfd. `_ruled_out` and `_holders` get docstrings that say all of this, and the false "a zombie's environment is empty" is removed.

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

Most of the new tests drive the scan through module seams. These are the existing `_environ` and `_stat`, plus new one-line readers for `status`'s `Threads:`, a task listing and a task's environ. They use a fake clock: `hostread.time.monotonic` and `hostread.time.sleep` are replaced together, and the sleep advances the clock and counts calls. So they are deterministic, need no load, and assert timing by the number of calls, not by wall time.

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
- **Live zombie-leader regression** (no seams):
  - A child of the test whose main thread calls `pthread_exit` while a worker sleeps, with `CODEX_HOME` set to a stamped home. `recover` kills it and removes the home.
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

  If the flag ever read clear, the process would fall to rule 4 and become unknown or be cleared by today's rules. If `Threads:` were missing, the group-dead rule couldn't fire, and a zombie would settle until the deadline. Both fail closed.
- **PID reuse.** `stat` and `status` are read by PID after the pidfd is open, as the zombie check already does. A worker match is confirmed by pidfd signal 0, as a leader match is. A reused PID can only make the scan wait or match through a fresh pidfd. It can never make it clear a process it had no proof for.
- **Cost.** It adds one `status` read per unreadable or empty process, and a `task/` listing only for a zombie leader with live threads. Both are rare.

## Out of scope

- Per-PR CI load testing. The churn harness is an acceptance check, as in y3e88.
