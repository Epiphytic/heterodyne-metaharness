Reviewer: gpt-6.1-sol. Reviewed commit: 80ae09010c79928696dd65fa64226b565ed69dbc. Verdict: REVISE.

Note: cycle 2 of 3 of the cross-model design review (author claude-opus-5-5).

Reviewed commit `80ae09010c79928696dd65fa64226b565ed69dbc`, including `f40feb1..80ae090`. Read-only; no files changed or tests run.

R1 findings:

1. **Resolved.** `end_cause` is immutable, delayed absence becomes `DIED`, and lifetime WIP obligations remain separate. Cleanup crossing the deadline no longer upgrades a death into a healthy end.
2. **Resolved.** Accounting before replacement launches covers deaths consumed by park/defer and their replay. `_own_dispatch` preserves receipt settlement before accounting.
3. **Resolved.** `reached_running` separates execution evidence from optional duration. Legacy lifetime ends neither increment nor reset the counter.
4. **Resolved.** Retention is explicitly deferred, with requirements to preserve recoverable accounting evidence.
5. **Resolved.** Reset and reboot behavior are now identified as policy decisions requiring design approval.

Revision findings:

1. **BLOCKING — `backstop_seconds < hard_lead` does not guarantee a stop before the reaper.**  
   [Design:60](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:60) claims that a pickup always runs inside the window. However, [_every](~/repos/heterodyne-metaharness-v2-g08sd/src/heterodyne/wsd/daemon.py:530) sleeps **after awaiting completion of the preceding job**. Pickup also takes the workstream lock and performs runtime listing and reconciliation before calling `expire`, using a timestamp captured before those operations.

   With the defaults, a preceding job or lane delay exceeding 120 seconds can miss the entire stop window despite a 60-second backstop. Slow work before `expire` can also make its `now` stale. The reaper then kills a healthy generation; two such cycles incorrectly escalate it to `crash_loop`.

   **Required change:** Replace the claimed guarantee with an enforceable deadline mechanism independent of pickup delays, or explicitly make these scheduling misses an accepted false-escalation policy and remove the “healthy session never reaches the reaper” promise. Add design cases for a long preceding job, lane contention, and stale `now`; checking the configuration inequality alone is insufficient.

2. **NON-BLOCKING — Clarify the legacy exception to the crash definition.**  
   [Policy 1](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:174) counts launched generations lacking trusted stop evidence, whereas legacy lifetime and overwritten generations are exempt. State that this is an upgrade exception.

The `_account` transaction and escalation replay are otherwise sound: nested `_stuck` joins the accounting transaction, atomically finishing the launch operation and opening ESCALATE. After commit, recovery replays that escalation instead of permitting another launch.

Verdict: REVISE