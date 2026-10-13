Reviewer: gpt-6.1-sol. Reviewed commit: b2c37326c5106125cf38eb4b66b6664240908a18. Verdict: REVISE.

Note: cycle 2 of 3. Fixed in f9e5d6e.

Cycle-1 findings are addressed in the revised **design** at `b2c3732`:

- ENDED records with `unlanded` or `wip_mark` now retain the branch hold.
- Cross-workstream recovery explicitly requires `sessions(other.ws)` or `stop(other.key)`.
- The test plan covers every non-ENDED phase, stale same-key cleanup, and repository aliases.

Only the design changed; these fixes are not yet implemented.

1. **[BLOCKING] WIP settlement runs before all conflicting records are checked.**  
   `docs/superpowers/specs/2026-10-13-jjnqm-one-session-per-branch-design.md:23`

   Suppose an existing branch has record A, ENDED with owed WIP, and record B, RUNNING or ENDED with `unlanded`. If `_records()` returns A first, the guard commits A’s WIP before discovering B and refusing launch. The refusal has already advanced the host branch, potentially making B’s private commits diverge. This requires no concurrency: it can arise from the multiple-session state this change was prompted by. Record-directory sorting determines whether the mutation happens (`src/heterodyne/sandbox/runtime.py:653`).

   **Fix:** collect all matching other-key records first. Refuse immediately if any is non-ENDED or unlanded; only then settle the remaining ENDED records’ owed WIP. Add tests with an owed-WIP record and a RUNNING/unlanded record in both iteration orders, asserting refusal leaves HEAD, worktree, and records unchanged.

2. **[NON-BLOCKING] The new settlement path lacks record-write/crash replay tests.**  
   `docs/superpowers/specs/2026-10-13-jjnqm-one-session-per-branch-design.md:55`

   The proposed tests cover settlement success and Git failure, but not a successful commit followed by failure to clear the record’s mark. Static inspection supports the intended behavior: `_settle_wip()` commits before clearing the mark (`src/heterodyne/sandbox/runtime.py:644`), and `_phase()` returns only after writing the record (`:435`). If that write raises `OSError`, the guard retains its original marked record and refuses. A later serialized retry should find the existing marked commit and clear the record without duplicating it.

   **Fix:** extend the different-key test to inject failure after the commit but before mark clearance. Assert no replacement record/sandbox exists, the mark remains, and retry launches successfully with exactly one WIP commit. Also cover failure before the commit.

3. **[NON-BLOCKING] Existing multi-session tests need migration beyond T14.**  
   `docs/superpowers/specs/2026-10-13-jjnqm-one-session-per-branch-design.md:69`; `tests/test_wsd_lifetime.py:232`

   `test_one_stop_that_fails_does_not_keep_the_next_from_stopping` launches both profiles using the default bead and repository. The second launch will now fail before that test reaches its intended cleanup behavior. `tests/sandbox_env.py:137` confirms both specs default to `btq-1`.

   **Fix:** include existing simultaneous-session fixtures in the migration scope. Give independent sessions distinct beads with correctly pinned worktrees, or distinct repositories; retain shared-branch cases only where refusal or legacy-state recovery is the test’s purpose.

The remaining checks look sound within the stated serialized-launch scope:

- Placement after `prev` handling preserves live same-key idempotency, stale same-key cleanup, and existing unlanded/WIP checks; placement before `_resume` prevents replacement creation.
- `realpath` matches the current main-repository contract. `gitwip.pin()` resolves `spec.repo` and derives its `.git` directory; supplying a linked worktree as the repository does not satisfy that contract.
- A dead different-key record deliberately remains blocking until its owner is reconciled. Same-workstream `sessions(ws)` handles any role; another workstream’s call does not. The revised recovery instructions cover that distinction.
- Scheduler pickup holds the same Parker’s `RLock` through launch. Separate Parkers and concurrent direct callers remain outside that guarantee, as acknowledged.

Read-only static review; no files modified or tests executed.

Verdict: REVISE