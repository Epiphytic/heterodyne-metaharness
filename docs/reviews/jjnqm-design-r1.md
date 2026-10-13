Reviewer: gpt-6.1-sol. Reviewed commit: 96470d77c232845e5b5c76ec43d4c295b2ab15d6. Verdict: REVISE.

Note: cycle 1 of 3. Fixed in b2c3732.

The placement is correct for sequential launches: after all `prev` handling and before `_resume`, it preserves live same-key idempotency, stale same-key cleanup, and that key’s unlanded/WIP checks. Two different keys with matching repo and bead are refused while either record remains non-ENDED.

1. **[BLOCKING] ENDED does not mean the branch is settled.**  
   `docs/superpowers/specs/2026-10-13-jjnqm-one-session-per-branch-design.md:21,30`  
   `_end()` records ENDED before attempting lifetime WIP settlement (`src/heterodyne/sandbox/runtime.py:568–572`). It also records ENDED when `_land()` sets `unlanded=True`. The proposed guard ignores both conditions for another key. A direct caller can therefore launch a replacement over unresolved commits or owed WIP. Later WIP replay can advance the host branch while the replacement holds a private tip, recreating the divergence this change addresses. The workstream hold protects normal pickup, but does not protect direct runtime callers—the stated motivation.

   **Fix:** matching branch records must also block reuse while `unlanded` or `wip_mark` remains. Refuse unlanded records; settle owed WIP before replacement launch, and refuse if settlement remains incomplete. Add different-key tests for both conditions, including a crash after ENDED but before WIP settlement.

2. **[NON-BLOCKING] Automatic cleanup is scoped to the owning workstream.**  
   `docs/superpowers/specs/2026-10-13-jjnqm-one-session-per-branch-design.md:27`  
   `sessions(ws)` skips records belonging to another workstream (`src/heterodyne/sandbox/runtime.py:191`). Calling it for another role in the same workstream does clean the crashed record; calling it for a new workstream does not. Repeated replacement launches also never end the conflicting key. If the owning workstream stops receiving pickups, its dead record can hold the branch indefinitely.

   **Fix:** document recovery explicitly: run `sessions(other.ws)` or `stop(other.key)` before handing off. Alternatively, provide deliberate branch-scoped reconciliation. Add a cross-workstream recovery test; do not end a live conflicting session merely to permit replacement.

3. **[NON-BLOCKING] Tests omit transient phases and placement regressions.**  
   `docs/superpowers/specs/2026-10-13-jjnqm-one-session-per-branch-design.md:34`  
   The proposed tests exercise RUNNING and successfully ENDED records, but omit CREATING, TESTING, STARTING, and STOPPING. The prose also calls the last phase “ENDING,” whereas the actual enum uses STOPPING.

   **Fix:** parameterize refusal across every non-ENDED phase. Add a stale same-key relaunch test verifying cleanup and settlement happen before the new guard, plus a repository-path alias test.

Repository identity via `realpath` is sound under the current contract: `gitwip.pin()` requires `spec.repo` to identify the main repository with a `.git` directory; a linked worktree supplied as `repo` is rejected (`src/heterodyne/wsd/gitwip.py:104–115`). Different `spec.worktree` paths therefore do not bypass the comparison.

The concurrency claim holds for callers sharing one Parker: pickup holds its `RLock` through launch. It is not a runtime-wide or cross-process lock. The explicitly acknowledged concurrent direct-caller race remains, so the guarantee should remain qualified as serialized-launch enforcement.

Read-only static review; no files modified or tests executed.

Verdict: REVISE