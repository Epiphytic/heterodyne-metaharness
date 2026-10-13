Reviewer: gpt-6.1-sol. Reviewed commit: d17c3fa933fa04f00c31e4d81c6c25f12782be07. Verdict: REVISE.

Note: code-review cycle 1 of the cross-model review (author claude-opus-5-5), range 850d531..d17c3fa.

- **BLOCKING — required dead-RUNNING same-key test is missing.** [test_sandbox_runtime.py:945](~/repos/heterodyne-metaharness-v2-jjnqm-impl/tests/test_sandbox_runtime.py:945) seeds `Phase.TESTING`, whereas design test 6 requires a dead `RUNNING` record. This bypasses the RUNNING liveness check and recovery of a session that actually ran. Launch a session, kill its pane without calling `sessions()` or `stop()`, then relaunch the same key and verify cleanup and successful replacement.

Otherwise, the guard matches the approved design, including preflight before settlement. The remaining required scenarios are covered; the migrations use separate pinned bead worktrees, and I found no missed conflicting multi-session tests. The live two-CLI test already uses distinct beads.

No files modified or tests run.

Verdict: REVISE