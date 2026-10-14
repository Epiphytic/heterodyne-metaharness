Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 95b5a28a4a0d7abfd7a221f3d2f3aa0aec69abb8 (range b055f38..95b5a28). Verdict: REVISE.

Note: review cycle cap (3) reached. The one blocker (stale census fd) is fixed by 25e06cbf829fb11dba4363bd2b264fd4dcbfa6b8 (st_dev/st_ino compared after LOCK_SH, --take validates, test 9a); the controller (claude-opus-5-5) verified the fix against this finding. Test 2 determinism is carried into implementation.

[BLOCKING] **A stale census fd permits spawning after successful teardown — Design, lines 92–107 and 118–120.** A spawner opens `census`, then pauses before `flock`. Teardown acquires `LOCK_EX`, removes the directory, closes its fd and returns. The spawner resumes and successfully acquires `LOCK_SH` on the unlinked inode, then spawns. I reproduced this lock sequence on Linux. A child stopped before its interpreter runs can now survive unreported after successful cleanup. The sleeper’s `fstat` check is too late. **After acquiring the shared lock, each spawner must compare its fd’s device/inode with the current census pathname before spawning.** The shell’s `--take` must perform this validation too. Add a deterministic regression paused between open and lock.

[NON-BLOCKING] **The other two r2 blockers are resolved — “The fakes and scripts,” line 145; Tests 5–6.** `ESCAPED` now reaches an admitted sleeper before PID publication fails. Fork paths specify parent `os.fork` and child `os.exec` events; shell paths use a separate trace boundary. Those are executable positive-control strategies.

[NON-BLOCKING] **The fd handoff itself is sound under the stated ownership invariant — Design, lines 67, 83–114.** Inherited references share the lock; closing the parent’s copy preserves the child’s reference. This matches the [Linux](https://man7.org/linux/man-pages/man2/flock.2.html) and [Apple](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/flock.2.html) documentation. My Linux shell experiment confirmed retention across `--take` exit, shell fd closure and child `setsid`, followed by release on child exit. macOS execution remains deferred.

[NON-BLOCKING] **Test 2 is not deterministic as written — lines 169–171.** A child may execute before the parent sends SIGSTOP; conversely, stopping it before reporting leaves no `pids/<pid>` to name. Use a child-side gate and acknowledge the stopped state before assertions. Pipe readiness and the normal cleanup budget otherwise improve CI robustness, but design prose alone cannot establish flake-free execution.

**Is this good enough for a PoC to implement? No—not until the stale-fd admission race is closed.** The remaining test adjustments are non-blocking implementation details.

Verdict: REVISE