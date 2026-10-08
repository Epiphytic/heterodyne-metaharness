# Code review r5: tmux leak fix, Amendment 1 implementation (btq-q1r4p)

Reviewed commit `3aa91da`, the branch against main. Reviewer: gpt-6.1-sol (Codex, read-only, reasoning high). Author: claude-opus-5-5. Mode: cross-model.

Findings 1 and 2 were fixed in the next commit.

---

1. [BLOCKING] **The required macOS wrong-device regression does not test the matcher.** [tests/test_tmux_launch_lock.py:589](tests/test_tmux_launch_lock.py:589). This branch asserts that `lsof_matches` parses a record with the wrong device; it never verifies that `held_fds` rejects it. Amendment 1 explicitly requires that rejection.

   I reproduced this in memory: both the correct matcher and an inode-only mutation pass the committed test on simulated macOS. Given wrong-device output, the correct matcher returns `[]`, while the mutant returns `[77]`. The archived combined mutant’s Linux failure masks this missing coverage.

   **Fix:** Force the `lsof` branch with mocked platform detection and subprocess output, assert rejection of the wrong-device record, and retain a matching-device positive control. Run this synthetic test on Linux and verify that a mutation confined to the `lsof` predicate is killed.

2. [NON-BLOCKING] **An empty failed closure loses its diagnostic reason.** [tests/tmux_watchdog.py:497](tests/tmux_watchdog.py:497). When `launch.lock` disappears and no entries remain, the summary contains `closed:false` with empty outcome lists. This fails closed safely, so it is acceptable for safety, but consumers must inspect `watchdog.log` to understand the failure.

   **Fix:** Add a top-level closure reason and a regression for the missing-lock, empty-directory case.

3. [NON-BLOCKING] **The r4 synchronization findings are resolved.** [tests/tmux_guard.py:131](tests/tmux_guard.py:131) and [tests/tmux_watchdog.py:395](tests/tmux_watchdog.py:395). SH inheritance, close without launcher `LOCK_UN`, nonblocking EX, identity/generation/connect revalidation, and final EX retention implement the amended protocol. No further removals follow failed final closure. Watchdog operations remain within the private root under the approved ownership assumptions.

   tmux 3.4’s source supports descriptor retention through [daemonization](https://raw.githubusercontent.com/tmux/tmux/3.4/proc.c); [panes](https://raw.githubusercontent.com/tmux/tmux/3.4/spawn.c), [jobs](https://raw.githubusercontent.com/tmux/tmux/3.4/job.c), and [pipe-pane children](https://raw.githubusercontent.com/tmux/tmux/3.4/cmd-pipe-pane.c) call `closefrom` before executing commands.

   **Fix:** None required.

4. [NON-BLOCKING] **The production extraction preserves behavior.** [src/heterodyne/tmux.py:48](src/heterodyne/tmux.py:48). Existing callers retain their argv, inputs, timeouts, and exception mapping. Explicit `input=None` in the launcher branch is equivalent to the previous omitted argument. All 19 pinning cases passed independently.

   **Fix:** None required. The documented regression 3(b) deviation does not change production behavior.

5. [NON-BLOCKING] **The tripwire passes its current-tree checks; integration evidence has limits.** [tests/test_tmux_scan.py:90](tests/test_tmux_scan.py:90). All 60 scan cases passed independently, and the current tree produced no findings or false positives. The archived logs support 26 killed mutants and the reported 2,374-pass suite, subject to finding 1’s coverage gap. I did not rerun integration or mutation suites in this read-only review.

   **Fix:** Complete the already-open self-hosted/manual macOS validation after adding the missing matcher test.

REVISE