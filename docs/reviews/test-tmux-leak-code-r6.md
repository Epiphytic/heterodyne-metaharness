# Code review r6: tmux leak fix, Amendment 1 implementation (btq-q1r4p)

Reviewed commit `0fe4f86`, the branch against main. Reviewer: gpt-6.1-sol (Codex, read-only, reasoning high). Author: claude-opus-5-5. Mode: cross-model.

Finding 3 was folded in in the next commit.

---

1. [NON-BLOCKING] **R5 finding 1: yes, resolved.** [tests/test_tmux_launch_lock.py:642](tests/test_tmux_launch_lock.py:642). Both committed lsof cases passed in memory. A mutation confined to the lsof device predicate failed the wrong-device case on Linux. `has_proc_fds()` preserves the previous availability check: `/proc` on Linux, lsof on macOS. **Fix:** None required.

2. [NON-BLOCKING] **R5 finding 2: yes, resolved.** [tests/tmux_watchdog.py:527](tests/tmux_watchdog.py:527). Every returned summary includes `reason` exactly when `closed` is false. Fault → appeared entry → held lock → deadline precedence is deterministic; 24 state combinations passed. The new regressions cover the requested cases, and both equality updates are appropriate. **Fix:** None required for the r5 finding.

3. [NON-BLOCKING] **The deadline fallback can misdiagnose a final filesystem error.** [tests/tmux_watchdog.py:528](tests/tmux_watchdog.py:528). I simulated final `rmdir` raising `EACCES` with an empty inventory. Closure stopped immediately, but the summary reported `"deadline passed"` with nearly 30 seconds remaining. Cleanup still fails closed safely. **Fix:** Record the terminal `rmdir` failure independently of inventory contents, preserve its diagnostic reason, and add an empty-inventory regression.

4. [NON-BLOCKING] **R5 finding 3: yes, remains resolved.** [tests/tmux_guard.py:131](tests/tmux_guard.py:131). This round leaves SH inheritance, launcher close without unlock, EX serialization, identity/generation/connect checks, and final descriptor retention unchanged. **Fix:** None required.

5. [NON-BLOCKING] **R5 finding 4: yes, remains resolved.** [src/heterodyne/tmux.py:48](src/heterodyne/tmux.py:48). Production extraction is unchanged; all 19 committed pinning cases passed by direct invocation. **Fix:** None required.

6. [NON-BLOCKING] **R5 finding 5: partially resolved; macOS runtime evidence remains open.** [tests/test_tmux_scan.py:90](tests/test_tmux_scan.py:90). All 60 scan cases passed by direct invocation, including the current tree. Archived evidence records 30/30 killed mutants at `0fe4f86` and 2,393 passed, 36 skipped, rc=0. Integration suites were not rerun in this sandbox. **Fix:** Complete the already-open self-hosted/manual macOS validation.

APPROVE