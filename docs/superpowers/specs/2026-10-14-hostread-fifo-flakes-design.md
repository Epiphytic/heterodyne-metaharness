# The hostread and session-git `[fifo]` test flakes

Base: origin/main 0ac8c92.

Files involved:
- `src/heterodyne/sandbox/hostread.py` (`_holders`, `_ruled_out`, `_end_holders`)
- `tests/test_sandbox_hostread.py`
- `tests/fakes/fake_agent_cli.py` (the fake app-server)
- `tests/test_session_git.py` (`test_snapshot_refuses_links_special_files_and_hard_links`)

**Verdict:**
- **Hostread's main flake is a production bug.** The holder scan reads *any* exiting process of this user as one it can't rule out. It then gives up on the whole scan. On a busy host this discards reads and leaves the copied login on disk until the next start. The fix changes production.
- **A second, smaller hostread flake is in the test.** Its "orphaned" fake app-server exits as soon as its reader dies.
- **The `[fifo]` flake is in the test.** A plant collides with the commit's random fan-out directory.
- **A related production gap was found on the way:** a process whose main thread has exited is ruled out as a zombie while its other threads still run. The design proposes a fix; whether it goes in this bead is the lead's call.
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

## Finding (production, pre-existing): a process whose main thread exited is ruled out as a zombie

- `_ruled_out` treats `Z` as "holds nothing" (`:173`).
- When a process's main thread exits (`pthread_exit`) while other threads run, `/proc/<pid>/stat` shows `Z` and `PF_EXITING`, and `/proc/<pid>/environ` is EACCES. But the process is alive.
- Probe: a Python child whose main thread called `pthread_exit` with one other thread sleeping showed `Z`, flags & 4, environ EACCES, with 2 tasks. `/proc/<pid>/task/<tid>/environ` of the live thread reads normally.
- Such a holder is ruled out and never killed, and its home is removed under it. That breaks the "removed only once a complete scan finds none" rule.
- It is unlikely for the codex app-server, but a descendant can do it.

## The fix

1. **Production: an exiting process makes the scan settle, not fail** (`hostread.py`).
   - On `PermissionError` for a process `_ruled_out` doesn't clear, read its `stat` flags.
     - If `PF_EXITING` is set, the scan is *settling*. Close that pidfd, send it nothing, and go on to the next process.
     - Otherwise raise `_Unknown`, as now. A live non-dumpable process is still an unknown.
   - `_holders` returns `(holders, settling)`.
   - `_end_holders` loops while either is non-empty: it SIGKILLs only `holders`, sleeps 50 ms and scans again. At the deadline it raises `TimeoutError`, as now.
   - A home is still removed only after a scan that found no holder *and* no settling process.
   - So an exiting process can delay removal by its exit time, a few ms, but it can never cause it.
   - Nothing new is ruled out. An exiting process is waited for, not cleared.
   - The docstrings of `_holders` and `_ruled_out` drop "a zombie's environment is empty" and say what the kernel does.
2. **Production: a zombie leader with live threads is not ruled out** (the finding; scope is the lead's call).
   - In `_ruled_out`, `Z`/`X` clears a process only if `/proc/<pid>/task` lists no task but the leader.
   - Otherwise the scan reads the environment through a live thread (`/proc/<pid>/task/<tid>/environ`) and treats that process like any other, as a holder if it matches. If no thread's environment can be read, it is not ruled out, so fix 1's `PF_EXITING` rule makes the scan settle until the deadline. That is fail-closed.
3. **Test: a fake app-server that outlives its reader.**
   - Add a fault value, `HZ_FAKE_RATE_FAULT=outlive`, to `fake_agent_cli.py`. It never answers, like `hang`. After stdin's EOF it keeps sleeping until killed.
   - `CRASHING_READER` uses it.
   - `hang` keeps exiting at EOF for `test_sandbox_runtime.py:2311`.
   - Document it in the fake's header with the other faults.
4. **Test: `alive()` reads state, not the environment.** A process is alive if its `/proc/<pid>/stat` exists, its state isn't `Z`/`X` and `PF_EXITING` is clear. It no longer raises for a process that is dying, and it no longer depends on what a dying process's environ reads.
5. **Test: plant into a fan-out directory that isn't there.**
   - A helper `absent_fanout(private) -> str` returns the first of `00`…`ff` with no entry under `private/objects`.
   - The `fifo` and `object-dir-link` plants use it in place of `cd` and `ab`.
   - The plant is still a real fan-out directory holding a 38-character name, so the snapshot's checks see the same shape.
6. **Test: drop the zombie wait in `test_an_app_server_that_cannot_be_reaped_is_no_reading`.**
   - `once_a_zombie` (`:370`) was added in T8 r3 to step around root cause 1: the read's own scan meeting its just-killed server.
   - With fix 1 the real `_end_holders` settles through that window itself. The wrapper goes, so the test covers the production path.

## Tests

All new tests use the module's existing seams (`_environ`, `_stat`, `_holders`). They are deterministic and need no load.

- **Settling, then removed.** An unreadable process of this user, started after the home, whose `stat` reports `PF_EXITING` for two scans and then `Z`:
  - `recover` removes the home and logs `DIAG_RECOVERED`;
  - it sends that process no signal (a recording `pidfd_send_signal`).
  - Today this logs `DIAG_RECOVERY_KEPT`.
- **Settling past the deadline.** The same process stays exiting: `recover(seconds=0.3)` keeps the home and logs `DIAG_RECOVERY_KEPT`, with no pidfd leaked (the existing `open_fds` check).
- **A live non-dumpable process is still an unknown.** `PF_EXITING` clear: kept at once, as today. The existing `unreadable` tests cover this; one assertion is added that it raised without waiting for the deadline.
- **The read path settles too.** `read()` with an unrelated exiting process present for one scan returns `WANTED` and logs nothing. Today it logs `DIAG_SCRATCH_KEPT` and returns None.
- **Zombie leader** (if fix 2 is in scope):
  - a live child whose main thread has exited, with `CODEX_HOME` set to the home: `recover` kills it and then removes the home;
  - with its thread's environ also refused: kept.
- **`crashed[started]`** asserts `alive(orphan)` before `recover`. With `outlive` that now holds on every run.
- **`[fifo]` / `[object-dir-link]`:** a parametrized check that `absent_fanout` is absent. Plus one run with the commit's fan-out directory forced to `cd`, by planting `cd/` first. Today that is the CI failure.
- **Acceptance under load.** The implementation PR records the hostread file passing 10 of 10 runs under the churn and contention harness. The harness is a documented command, not per-PR CI:
  - `systemd-run --user --scope`;
  - 8 busy loops and 4 exit loops on CPUs 0–3;
  - the test pinned to the same CPUs.
  - Today it fails 7 of 10.

## Risks

- **A host that is always exiting something.** The scan settles only while it meets an exiting process, and each one is gone within ms. A host spawning processes non-stop could keep the scan settling until the deadline (`RECOVER_SECONDS`, 10 s). The result is `TimeoutError` and a kept home: today's outcome, reached more slowly. Never a wrong removal.
- **`PF_EXITING` is a kernel flag value** (0x4 in `include/linux/sched.h`, long-standing). `/proc/<pid>/stat` field 9 is ABI. If either ever changed, the flag would read clear and the scan would fall back to today's behaviour (`_Unknown`), which is fail-closed.
- **PID reuse.** The settling check reads `stat` by PID after the pidfd is open, as the zombie check already does. A reused PID could only make a settling process look like a new one, and every outcome there is conservative.
- **Fix 2 widens the scan** by one `task/` listing per unreadable zombie. That is rare and cheap.

## Out of scope

- Per-PR CI load testing. The churn harness is an acceptance check, as in y3e88.
