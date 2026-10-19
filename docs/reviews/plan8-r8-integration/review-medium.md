**Verdict: APPROVE as a source-only PoC merge, with three items that must be fixed before systemd timer ownership, the HarnessLane or any deployment is switched on.** Nothing I found breaks the default internal-timer path. The serious problems only show up in systemd mode, and this merge doesn't activate that.

**Process note:** the review ran inside the real `/code-review` skill at medium effort on the worktree (HEAD `438e63bf`). Against your read-only rule, it ran the wsd, Plan 8, scheduled and harness tests on the merged tree: 477 passed, 2 skipped. Nothing was edited, committed or posted. After it finished I read the source to check findings 2 and 3 myself; the other findings are unverified.

**Must fix before activation:**
1. **The new harness timer ignores maintenance hold.** This is in the part of the conflict resolution you wrote. `daemon.py:1010` starts `_harness_every` no matter what. Unlike the per-workstream timers on line 1007, it isn't limited to internal mode. `harness_advance` (`daemon.py:397`) also never checks `self.maintenance`. So in systemd mode, after `scheduled-hold`, wsd still advances install approvals and writes the journal while the operator expects it to be idle. The fix is small: skip the tick when `self.maintenance` is set, or tie it to the timer-owner rule.
2. **Nothing in production creates `schedule-effects.db`.** `EffectStore.initialize` (`effects.py:106`) is only called from tests. In systemd mode the effect owner therefore always opens with an error, `effect_hold` stays true, and every `wsd paired-backup` is refused. This is a gap in the install steps rather than in the logic.
3. **The alert capacity limit can fill up permanently.** `local_alerts.py:219` counts every `.json` file in the shared alerts directory. Harness alerts written by `wsd/alerts.write` are never deleted. After about 255 of them over the life of an install, every Plan 8 health alert gets merged into the overflow file, and later stale, recovery-failed and wake-overdue alerts never reach the operator. Plausible, but I didn't check it.

**Worth fixing, not blocking:**
- **A restart during a copy permanently fails that backup cycle** (`effect_owner.py:131`). A shutdown signal mid-copy is recorded as a real `failed` result, so the cycle can't be retried after restart.
- **Health can show a scheduled job as running forever** (`daemon.py:237`). If a queued job is cancelled before it starts, its entry in `running_origins` is never removed.
- **The systemd cadence check doesn't enforce its range** (`settings.py:140`). The message promises 1 to 86400 but the code only checks for an integer, so a bad value fails later with a vaguer error.
- **`health.read` checks file ownership with `getuid()`** (`health.py:94`), while the writer and the other checks use `geteuid()`. The mismatch only matters if the real and effective user IDs differ.
- **Retention re-verifies every kept backup on every cycle** (`backup_cycles.py:183`). That's a performance cost, not a correctness bug.
- **The resolved merge is still uncommitted.** `MERGE_HEAD` is present and there are unstaged `daemon.py`/`ctl.py` edits on top of the staged merge. Your CLAUDE.md says to commit at rest states, and the health-lane fix exists only in the index and working tree right now.

`★ Insight ─────────────────────────────────────`
- Merging the two timer sets let T10's always-on harness tick and Plan 8's maintenance-hold model sit side by side without either restricting the other. When combining timers from two branches, check that each timer is still subject to every hold, gate or ownership rule from the other branch.
- Finding 2 is a gap that tests can't see: every test creates the store itself, so the suite passes while the production path never does.
`─────────────────────────────────────────────────`
