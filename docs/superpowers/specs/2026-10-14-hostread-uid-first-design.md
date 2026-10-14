# hostread: the uid rule for a zombie leader with threads (btq-19fh0): recommend won't-fix

An amendment to the flakes2 design (`docs/superpowers/specs/2026-10-14-hostread-fifo-flakes-design.md`, approved at 6d4fab6, implemented in PR #88). It concerns one branch of `_judge` in `src/heterodyne/sandbox/hostread.py`.

Review: `docs/reviews/19fh0-design-r1.md` (REVISE).

**Recommendation (r2): don't change production. Keep settling, and close btq-19fh0 as won't-fix.** The only change is three test pins of today's behaviour (below). The r1 proposal is withdrawn, and the reasons are set out here.

Line numbers are at origin/main 7c68abb (the #88 merge).

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

## What r1 proposed, and why it is withdrawn

r1 proposed this: in the branch at `:257`, when no worker gives an environment, clear another user's group by the leader's uid (`_SETTLE if _ours(facts) else _CLEAR`).

Review r1 showed that this is unsound, and I agree. The leader's uid says nothing about who the *other* threads are, and holding a home depends on the environment, not the uid.
- **Credentials are per task, and need no privilege to diverge.** A thread can switch to any of its own real, effective or saved uids with a raw per-thread `setresuid` (`kernel/sys.c`). So:
  - A group with real and effective uid B and saved uid A (one of ours) can leave a worker that has gone back to A, while the leader reads as B's.
  - `_ours` reads only the real and effective uids, so it would call the group another user's.
  - In a user namespace with a subordinate-uid mapping, `CAP_SETUID` needs no host privilege, so threads can hold different host uids.
- **An unreadable thread is not an absent holder.** That worker can be non-dumpable, so its `task/<tid>/environ` is refused, while the shared environment still holds `CODEX_HOME`.
  - Today that group settles, and the homes are kept.
  - The r1 rule would clear it and remove the home under a live holder.
  - r1 argued that the scan couldn't kill such a holder anyway. That argument is wrong:
    - `kill` permission can match the target's saved uid.
    - In any case, a holder that can't be killed calls for keeping the homes, not for treating it as absent.
- **Exec makes it worse.** Suppose a worker of ours execs after the `status` read. It takes the leader's PID and drops out of the worker listing. The r1 rule would then clear the group on the old leader's uid, and no later scan may follow.

## Whether a sound proof is worth building

A clear here would need a proof that **no** thread of the group can hold a home. The candidate is:
1. read every task's `/proc/<pid>/task/<tid>/status`, with all four uids (real, effective, saved, fs) foreign;
2. prove the enumeration complete;
3. re-read the leader to show the same zombie.

It doesn't hold:
- **Completeness can't be proven from `/proc`.**
  - The listing can end early (`next_tid` stops when its task loses its PID, as flakes2 found).
  - `Threads:` comes from a different read than the listing. A clone and an exit between the two leave the count unchanged, with different members.
- **Each thread's uids are only true at its own read.** Unprivileged saved-uid restoration lets a thread judged foreign become ours a moment later. Its environment, the shared one, may still hold the mark.
- **A leader re-read doesn't show task continuity.** It is the flakes2 lesson: an exec moves a worker into the leader's PID, so a later read can describe a different task.
- **And it doesn't fix the premise.** Even with all four uids foreign at once, a foreign uid doesn't show the environment lacks `CODEX_HOME`. The environment is what holding means.

So the cost would be a per-thread read loop and a completeness check that can't be made sound, all to turn a fail-closed wait into a clear.

**The cost of keeping today's behaviour is small and bounded.**
- While another user's process is in this state, each start-up recovery and each trusted read waits `RECOVER_SECONDS` (10 s) and keeps its home. They log `DIAG_RECOVERY_KEPT` or `DIAG_SCRATCH_KEPT` (the read is discarded).
- Nothing that holds a home is ever missed. The next start or read, once that process has exited, cleans up.
- The state is rare. It needs another user's program to `pthread_exit` its main thread and keep running. No process on this host was in it at the 2026-10-14 check.

## Tests (a test-only change, if won't-fix is approved)

All existing hostread tests stay. `status_text` in `tests/test_sandbox_hostread.py` gains a `uids` argument: the four `Uid:` fields, defaulting to this user's.

Three seam tests pin today's settling against a future reintroduction of the r1 rule. They use the flakes2 `Scripted` and `Seam` helpers and the fake clock. Each asserts the home is kept at the deadline (`DIAG_RECOVERY_KEPT`), no signal is sent, and no pidfd is leaked.
1. **An unreadable mixed-uid holder:**
   - `Z`, `Threads: 2`, with the leader's `Uid:` another user's on all four fields.
   - The one worker's environ is refused (EACCES), standing in for a non-dumpable worker of ours that holds the mark.
2. **Saved-uid restoration:** the same, with the leader's `Uid:` showing another user's real and effective uids and *our* uid as saved.
3. **A worker exec between `status` and the listing:** `Z`, `Threads: 2`, another user's `Uid:`, and a listing whose only worker raises `FileNotFoundError` (it exec'd and took the leader's PID).

All three pass today. They would fail under the r1 rule, so they are pins, not failing-first tests.

## Out of scope (noted, not proposed)

- **Rule 5 on the live path (`:261`) relies on the same premise:** "another user's process is not a holder", from the leader's real and effective uids.
  - The saved-uid point above applies there too. For example, a descendant of ours that execs a setuid binary owned by user B can later drop to B entirely, without privilege. Its environment, which still holds `CODEX_HOME`, is then refused, and rule 5 clears it.
  - That behaviour predates flakes2 (T8) and is unchanged by this doc. Whether to tighten it is a separate question for the lead.
- **The start-time clause in the zombie branch,** as in r1.
