# hostread: another user's zombie leader is cleared by its uid (btq-19fh0)

An amendment to the flakes2 design (`docs/superpowers/specs/2026-10-14-hostread-fifo-flakes-design.md`, approved at 6d4fab6, implemented in PR #88). It changes one branch of `_judge` in `src/heterodyne/sandbox/hostread.py`. Line numbers are at origin/main 7c68abb (the #88 merge).

## The rule order today

`_judge` reads the leader's environ. If that read is empty, refused (EACCES) or ESRCH, one `status` read decides, in this order (`hostread.py:246–263`):

```python
    if environ:                                              # :246
        return _matched(fd, environ, marks)
    facts = _facts(_status(pid))                             # :248  one read: State, Threads, Uid
    state, threads = facts.get("State", "")[:1], facts.get("Threads", "")
    if not state or not threads.isdigit():                   # :250
        return _SETTLE
    if state in ("Z", "X"):                                  # :252  rules 1-3
        if threads == "1":
            return _CLEAR
        if threads == "0":
            return _SETTLE
        group = _through_workers(pid)                        # :257
        return _SETTLE if group is None else _matched(fd, group, marks)
    if why == "empty" or int(_stat(pid)[6]) & PF_EXITING:   # :259  rule 4, rule 5's first clause
        return _SETTLE
    if why == "refused" and _ruled_out(pid, facts, since):   # :261  rule 5: other user, or started early
        return _CLEAR
    raise _Unknown(...)                                      # :263
```

`_ruled_out` (`:186`) clears another user's process (`_ours`, `:177`, which reads the `Uid:` line: neither the real nor the effective uid is this user's), or one started before the earliest home.

## The case that waits until the deadline

Take another user's process whose main thread has exited (`pthread_exit`) while other threads run. Its leader environ is EACCES, and its status shows `Z` with `Threads:` of 2 or more. That takes it into the branch at `:257`, which never consults the uid:
- Its workers' `task/<tid>/environ` are refused, because they are another user's.
- So `_through_workers` returns None, and the process **settles** on every scan.
- `recover` and the read's cleanup then time out at `RECOVER_SECONDS` (10 s) and keep every home (`DIAG_RECOVERY_KEPT` / `DIAG_SCRATCH_KEPT`).

This fails closed, but it lasts as long as that process lives. While another user's process is in this state, every start-up recovery and every trusted read on the host waits 10 s and keeps its home. Before flakes2 the same process was cleared as a zombie. A live (non-zombie) leader of another user is cleared today by rule 5 at `:261`.

## The change

In the branch at `:257`, replace `_SETTLE` when no worker gives an environment with rule 5's uid clause. The uid comes from the facts the branch already holds, the same `status` read:

```python
        group = _through_workers(pid)
        if group is not None:
            return _matched(fd, group, marks)
        return _SETTLE if _ours(facts) else _CLEAR          # another user's group: rule 5's uid clause
```

- The workers are still read **first**. The uid clears only when no thread gives an environment. A worker readable by this user (ours and dumpable) decides as it does today, whatever the leader's uid. So the change only turns some of today's settles into clears. It never turns a match into a clear.
- Only the uid clause applies, not the whole of rule 5:
  - Rule 5's `PF_EXITING` clause would always settle here, because a zombie leader always has the flag.
  - The start-time clause is left out: see Out of scope.
- `_ours` is unchanged:
  - A missing `Uid:` line counts as ours, so the process settles as today.
  - A line that can't be parsed raises `ValueError`, which fails the scan (unknown-like, homes kept), as it does on the live path today.

## Why this stays fail-closed

- **One read, one task.** State, Threads and Uid all come from one `status` read. `proc_single_show` pins one task for the whole show. `task_state` prints both `State:` and the `Uid:` line from that task, the uids from `get_task_cred(p)` (`fs/proc/array.c`, v6.17). The branch is reached only when that read showed `Z`/`X` with `Threads:` of 2 or more. So the uid is the zombie leader's.
  - A zombie runs no code, and credentials change only through `commit_creds` on the current task. So that uid stays fixed while the task is a zombie.
- **Exec between the reads.** A worker that execs takes the leader's PID, and the old leader is released (flakes2, "PID and pidfd continuity do not prove task continuity").
  - A `status` read pinned to the old leader before the switch shows `Z`, its uid and `Threads:` of 2 or more. That is this branch, judged on the old group's uid.
  - After the switch, the old leader reads `Threads: 0` (settle), and the new leader shows a live state (rules 4–5, not this branch).
  - So this branch never judges the new image on the old image's facts. The new leader is judged by its own `status` read, on the live path, at the next scan or this one.
  - A worker that execs after the `status` read vanishes from the listing and is skipped. The group is then cleared on its leader's uid, which is the uid the exec'ing worker held too, unless the group is privileged (next point).
- **The uid changing between reads.** Credentials are per task.
  - A worker can hold a different uid from the leader only through `setresuid`-style calls, and changing to another user's uid needs `CAP_SETUID`.
  - So an "another user's" leader with one of *our* threads is a privileged process. Today's live rule at `:261` already judges such a process by its leader's uid.
  - The worker-first order narrows even that. A thread of ours that is dumpable is read, and matched or cleared on its environment, before the uid is consulted.
  - What is left is a privileged process with a non-dumpable thread of ours, whose leader is another user's zombie. The scan can't kill that process anyway:
    - `pidfd_send_signal` checks permission against the pidfd's task, the leader (`check_kill_permission`, `kernel/signal.c`).
    - A SIGKILL to another user's leader raises `PermissionError`, which `_end_holders` doesn't suppress, so the scan fails and the homes are kept.
    - Clearing it doesn't lose anything the scan could have done.
- **PID reuse.** As today, the pidfd is opened before any read, and `status` is read by PID.
  - If the PID was reused meanwhile, the original process is gone and holds nothing.
  - The reused PID's process is judged as any process met by the scan is. A match is still confirmed by signal 0 through the original pidfd, which fails for a process that is gone.
  - A clear here never sends a signal, so a reused PID can't misdirect a kill.

## Tests

All existing hostread tests stay as they are. Two of them pin that our own zombie leader still settles:
- `test_no_readable_worker_settles`;
- `test_a_truncated_task_listing_settles`.

`test_a_zombie_leader_with_live_threads_is_found_through_them` and `test_a_worker_that_cant_be_read_is_skipped_not_its_process` pin the worker-first order. They run with this user's uid and are unchanged.

The test helper `status_text` gains a `uid=(real, effective)` argument. It defaults to this user's uid, so the existing calls are unchanged.

New seam tests use the flakes2 `Scripted` and `Seam` helpers and the fake clock:
1. **Another user's zombie leader, no readable worker** (parametrized: workers refused, gone, empty, or an empty listing), with `Z`, `Threads: 3` and `Uid` of another uid → cleared at once:
   - zero sleeps;
   - no signal;
   - home removed, `DIAG_RECOVERED`.
   - Today it is kept at the deadline with `DIAG_RECOVERY_KEPT`, so the test fails before the change.
2. **Another user's zombie leader with a readable worker that holds the mark** → matched, and the SIGKILL is sent (recorded) before the home is removed. Pins worker-first. It passes today and must keep passing.
3. **Mixed uids count as ours** (parametrized: real ours with effective other, and real other with effective ours) → settles, kept at the deadline.
4. **No `Uid:` line** in a `Z`, `Threads: 2` read with no readable worker → settles, kept at the deadline.
5. **The read path:** `read()` with another user's zombie leader scripted for every scan, and `RECOVER_SECONDS` patched to 0.3 → returns `WANTED` and logs nothing. Today it settles to the deadline, logs `DIAG_SCRATCH_KEPT` and returns None.

No live test is added, since an unprivileged test can't start another user's process. The live zombie-leader regressions stay.

## Out of scope

- The start-time clause in this branch. Our own long-lived process with a zombie leader, started before the home, still settles until the deadline.
  - Applying that clause would need a separate argument: exec keeps the start time, and here a worker's later exec would be judged by the group's start.
  - It is left as a possible follow-up.
