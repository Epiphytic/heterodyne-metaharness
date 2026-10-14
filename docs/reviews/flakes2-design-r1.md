Reviewer: gpt-6.1-sol. Author: claude-opus-5-5. Mode: cross-model. Range: 0ac8c92c54943cd6cfeb1971699787f9196d76dd..934655d69f94f190c0e2796ce35b5c4b84aaa4bf. Verdict: REVISE.

Note: cycle 1 of 3 of the flakes2 (hostread + session-git [fifo]) design review.

The three flake mechanisms match the code. Fix 1’s settling approach preserves fail-closed behavior **provided settling prevents removal and deadline expiry keeps every home**. Including fix 2 is justified, but its current design is incomplete.

- **[BLOCKING] Fix 2 must cover empty environments and `ESRCH`, not only `PermissionError`.** The proposed thread lookup is reached through `_ruled_out`, which currently runs only after `PermissionError`. A zombie leader can instead produce an empty environment or `ProcessLookupError`: Linux’s proc implementation permits empty reads in [v6.12](https://github.com/torvalds/linux/blob/v6.12/fs/proc/base.c), and `__mem_open` returns `ESRCH` for a missing memory map in [v6.17](https://github.com/torvalds/linux/blob/v6.17/fs/proc/base.c). Neither proves the thread group is dead. Today those paths respectively discard a nonmatching environment or skip the process. Require thread-group validation before clearing these cases, and test all three outcomes deterministically.

- **[BLOCKING] A task-directory listing containing only the leader is insufficient proof of group death.** Enumeration races with thread exit and creation. The kernel’s [`next_tid`](https://github.com/torvalds/linux/blob/v6.17/fs/proc/base.c) stops when its current task loses its PID, potentially leaving later live threads unvisited. Also, a selected worker can disappear before its environment is read; letting that `FileNotFoundError` reach the current outer handler would skip the entire process. Specify conservative handling: retry or settle on uncertain enumeration, task disappearance, or permission failure. Require independent confirmation before treating a zombie group as empty. Add deterministic tests for truncated enumeration and a disappearing worker while another holder survives.

- **[NON-BLOCKING] Specify the deadline and identity rules explicitly.** Close all collected pidfds before timeout; timeout while settling must keep homes. Preserve the existing pidfd identity check after a successful thread-environment read. `PF_EXITING` belongs to a task, so it must never itself establish thread-group death. Waiting and rescanning remains conservative across exec and PID reuse.

- **[NON-BLOCKING] Tighten the test lifecycle and determinism.** `outlive` must survive EOF both before any request and inside the unanswered-request path. Add PID-based `finally` cleanup to the crash test because the replacement fake intentionally survives its reader. The proposed `alive()` is suitable for these single-threaded fixtures, but cannot establish death in the zombie-leader regression; use whole-group completion there. Test “unknown fails immediately” with controlled calls or a fake clock rather than a wall-clock timing threshold.

The absent fan-out directory change preserves the existing rejection and secrecy assertions. Removing `once_a_zombie` is appropriate after settling is implemented.

Validation: a bounded local probe reproduced a `Z`/`PF_EXITING` leader with an unreadable environment and a live worker whose environment retained `CODEX_HOME`. I killed and reaped the child by PID. No repository files were modified; I did not rerun the reported load campaigns.

Verdict: REVISE