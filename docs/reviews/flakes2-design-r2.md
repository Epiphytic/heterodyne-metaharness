Reviewer: gpt-6.1-sol. Author: claude-opus-5-5. Mode: cross-model. Range: 0ac8c92c54943cd6cfeb1971699787f9196d76dd..14c16b45baf06f5ffb92f57a7cc50191eccd41a3. Verdict: REVISE.

Note: cycle 2 of 3 of the flakes2 (hostread + session-git [fifo]) design review. Verbatim, except that the home directory in link targets is written as `~` (the install-agnostic hook).

- **[BLOCKING] Exec de-threading defeats the new group-death proof.** At [lines 153–155](<~/repos/heterodyne-metaharness-v2-design-flakes2/docs/superpowers/specs/2026-10-14-hostread-fifo-flakes-design.md:153>), `stat` can report the old zombie leader as `Z`; a worker can then exec, assume the leader’s PID and release the old leader; the subsequent `status` reports `Threads: 1` for the surviving process. The rule clears a live holder. Linux explicitly performs this PID transfer in [`de_thread`](https://github.com/torvalds/linux/blob/v6.17/fs/exec.c).

  I reproduced that ordering in a bounded probe: leader environ returned EACCES, saved state was `Z`, then worker exec produced `Threads: 1` with current state `S`. **Signal 0 through the original pidfd still succeeded.** The child was killed by PID and reaped.

  Require a race-safe proof that the state and thread count establish death of the same task/group, or conservatively settle when that proof is unavailable. Add a deterministic regression that forces exec between these reads.

- **[BLOCKING] The empty-environment ordering argument also needs task-identity protection.** At [line 165](<~/repos/heterodyne-metaharness-v2-design-flakes2/docs/superpowers/specs/2026-10-14-hostread-fifo-flakes-design.md:165>), an empty read from an exiting leader can precede worker exec; the later `stat` then describes the replacement leader with `PF_EXITING` clear. That does not prove the original empty read represented a genuinely empty environment. [`do_exit`](https://github.com/torvalds/linux/blob/v6.17/kernel/exit.c) sets `PF_EXITING` before `exit_mm`, but that ordering applies to one task. PID continuity and pidfd signal 0 do not establish task continuity across de-threading. Cover this interleaving and narrow the exec/PID-reuse safety claims accordingly.

- **[NON-BLOCKING] Specify readiness for the live zombie-leader regression.** At [line 217](<~/repos/heterodyne-metaharness-v2-design-flakes2/docs/superpowers/specs/2026-10-14-hostread-fifo-flakes-design.md:217>), require bounded confirmation of `Z` plus multiple threads before invoking recovery. Otherwise recovery can kill the child before leader exit, passing without exercising the regression.

The r1 requirements are otherwise addressed: EACCES, empty and ESRCH share one table; worker failures are handled per task; truncated enumeration and no readable worker settle. The deadline rules explicitly close pidfds and keep every home on timeout. The r1 lifecycle items—both EOF paths, PID cleanup, whole-group waiting and fake-clock assertions—are covered.

Ordinary thread creation cannot revive a genuinely dead single-thread group; the remaining problem is establishing that condition across separate reads and identity transitions.

No repository files were modified. Offline tests were not rerun because the revision specifies future implementation and tests.

Verdict: REVISE