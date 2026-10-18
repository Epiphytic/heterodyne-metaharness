# Fifth actual MEDIUM review and author dispositions

Actual verdict APPROVE as PoC at 7e1b9a7; report SHA256 88799dd68b3834dcd42c58bdb29a2820944dbc118982a4132c7c313ee2d19c0f. These later repairs require a sixth actual review, with every prior ordinal preserved.

## Full unchanged actual report

**Verdict: APPROVE as a PoC.** I found no critical flaw beyond the R4 limits you already retained. The review history stands as R1 HIGH REVISE, R2 HIGH REVISE, R3 MEDIUM REVISE, R4 MEDIUM APPROVE, and now R5 MEDIUM APPROVE.

Scope: range `35176c85..7e1b9a7b` (HEAD confirmed, working tree clean), read statically. No edits, tests, probes, queue or DB access, network, or child agents. I did not re-run Pyright or the tests, so the "0 errors" and "130 tests pass" results are your reported evidence, not mine.

- **Latest commit:** it only adds `assert isinstance(result, tuple)` before unpacking in three places in `tests/test_admind_approvals.py` (lines 197, 209, 214). It changes nothing at runtime and only narrows the `tuple | MaintenanceHold` type that `decide` now returns. I checked every other `ab.decide` caller. They compare the whole result with `==`, don't unpack it, or are `daemon.py:1362`, which handles the `isinstance` case explicitly. None is broken.
- **Full range:** I re-read every hunk in `approvals.py`, `daemon.py` and `store.py`, and the code around them: `recover`, `reconcile_one`, `run_attempt`'s tail, `transaction`, `ask_notice`. I found no new defect. Every finding below is an R4 limit that is still present at HEAD, carried forward for continuity. They remain prerequisites for the composed deployment, not PoC blockers.
- **Not closed by this review:** the quality debt is still unwaived. The manual observed stream is not proof for the reserved profile. The separate retirement amendment aa860fe7 is still waiting on human9567.

10 findings, most severe first:

1. `src/heterodyne/admind/store.py:1052`: (R4-1, retained) A latch is final only for overlays that are idle when it fires: issue_decision(maintenance_retry=True) consumes the overlay without checking outcome=='pending-maintenance', a retryable defer_maintenance turns 'maintenance-held:latched' back into 'pending-maintenance', and the HOLD path checks latched() only when the child returns.

   Failure scenario: retry_maintenance holds work_lock and is waiting on read_and_settle. latch() marks the row 'maintenance-held:latched', and the operator rearms before the read returns. run_attempt then passes the authority check and issue_decision deletes the suspended overlay, so consent given before the latch is issued with no new consent. The same happens through read-unavailable or a 'maintenance' HOLD if a rearm lands before the next 30 s tick.

2. `src/heterodyne/admind/daemon.py:1383`: (R4-2, retained) A proven HOLD is committed in two transactions (mark_maintenance_issuing, then hold_maintenance), so if the second fails, a decision that provably had no effect is stranded as 'maintenance-issuing'.

   Failure scenario: The child returns an exact HOLD and mark_maintenance_issuing commits. Then defer_maintenance or ask_notice raises (SQLite busy, a chunking error). The inbound message stays 'executing'. On restart reconcile_issuing reads 'untouched' and posts 'issuance remains unverified', and the ask stays 'deciding' for good, occupying the bead and a MAX_OPEN slot.

3. `src/heterodyne/admind/daemon.py:528`: (R4-3, retained) latch() sets 'latched' in the same transaction as the per-overlay defer_maintenance loop, so any exception in that loop rolls back the safety latch itself, and the loop overwrites an existing suspended reason with ':latched'.

   Failure scenario: A membership anomaly calls latch(). current_attempt or defer_maintenance raises on a corrupt row or a DB error. The ROLLBACK discards store.set('latched'), so admind keeps posting and acting although it should be latched.

4. `src/heterodyne/admind/daemon.py:1302`: (R4-4, retained) When hold_maintenance's compare-and-set fails, it calls conflict() and returns without finishing the inbound message, unlike the issue_decision path that R3 fixed.

   Failure scenario: On the retry or HOLD path, defer_maintenance returns False because the attempt changed. The operator's !approve message stays 'executing' with no reply until a later restart, and recover() skips it because it is an attempt message.

5. `src/heterodyne/admind/daemon.py:1625`: (R4-5, retained) reconcile_issuing settles only an exact 'recorded' read-back; 'blocked' (a foreign native decision) and 'untouched' are held forever, and it starts a native read-back child on every !asks pass and every startup.

   Failure scenario: An interrupted maintenance retry leaves 'maintenance-issuing', and another approver then decides the bead natively. Every !asks starts an approve-bead read-back under work_lock, and the ask never reaches 'blocked'.

6. `src/heterodyne/admind/store.py:151`: (R4-6, retained) MAINTENANCE_CAPACITY=128 can never be reached because overlays need 'deciding' asks and MAX_OPEN=20 caps active asks, so the capacity refusal at daemon.py:1329 and the RuntimeError in defer_maintenance are dead paths.

   Failure scenario: About 20 permanently suspended holds lock out new asks with ASK_TOO_MANY rather than the 'maintenance-capacity' message, so the stated bound is not the real one.

7. `src/heterodyne/admind/daemon.py:1664`: (R4-8, retained) retry_maintenance ignores defer_maintenance's False return and goes on to the authority checks, a native read-back and run_attempt, which repeats the authority check and preflight, reloading policy two or three times.

   Failure scenario: Every due retry does extra policy-file I/O and starts a read-back child under work_lock even after the compare-and-set has shown the attempt is no longer current, which delays operator commands.

8. `src/heterodyne/admind/daemon.py:1325`: (R4-7, retained) Stale docstrings: run_attempt says every exit goes through the reap and the read-back, but the HOLD exit skips the read-back, and ApproveBead.decide still describes only the (exit status, line) tuple.

   Failure scenario: A maintainer trusts the docstring and assumes native state was read back after a HOLD, when the HOLD was accepted from the child's stdout alone.

9. `src/heterodyne/admind/store.py:1017`: (R4-9, retained) The internal states ('decision-issuing', 'maintenance-issuing', 'pending-maintenance', 'maintenance-held:*') are stored in the user-facing asks.outcome column.

   Failure scenario: The CLI 'outcome:' line (cli.py:163) and asks.summary show 'decision-issuing' for every ordinary decision in progress, and whether a latch suspension sticks depends on any code path that writes outcome.

10. `src/heterodyne/admind/approvals.py:116`: (R4-10, retained) maintenance_hold reimplements the strict duplicate-key JSON hook that already exists in actions/codec (_pairs/_reject_constant).

    Failure scenario: There are now two strict JSON decoders that can drift apart. It is harmless today only because of the exact three-key dict comparison.

The R4 list is complete at HEAD: all ten retained limits are still present, nine are listed above, and the "outcome state" item is folded into the asks.outcome finding. None blocks the PoC. All must be fixed before any composed deployment, and none allows the feature to be activated on its own.

## Author repair dispositions

1. Fixed suspended-consent CAS: neither issue nor retryable defer can consume/revive a maintenance-held row. Durable latch token changes in the committed safety latch transaction. A proven child HOLD compares the pre-await token even if rearm already cleared the latch. Real fail-first tests cover native readback and child completion races, plus direct Store CAS.

2. Retained explicit deployment prerequisite: two-stage proven-HOLD publication can leave maintenance-issuing if overlay/notice fails. No unsupported proof or untouched native settlement is manufactured. Retirement/proof design and unknown issuance recovery must compose before deployment.

3. Safety latch commits before overlay suspension; overlay failure cannot roll it back. Authenticated rearm finishes suspension before clearing the latch, in one transaction. Existing held reasons are preserved. Real injected-storage-failure regression and rearm regression pass.

4. Failed hold CAS now finishes the original inbound using the existing fixed conflict response. Regression passes.

5. Explicit remaining deployment prerequisite: interrupted maintenance issuance can require authenticated scoped recovery and incurs readback per explicit inspection/startup. No generic clear/replay added.

6. Existing MAX_OPEN=20 is the effective bound; 128 is not claimed as achievable capacity. No approval-slot redesign added.

7. Retry exits immediately on failed defer CAS. Authority is still deliberately refreshed after awaited readback; repeated policy reads remain noncritical unwaived debt.

8. Both docstrings now describe MaintenanceHold and its no-readback proof path.

9. Existing internal outcome storage remains unchanged and explicitly disclosed; no schema redesign.

10. Existing service-local closed JSON proof decoder retained. Deduplication remains noncritical unwaived debt.

No deployment, production DB migration, native decision, operator retirement or fabricated recovery authority. Quality debt unwaived; composed deployment needs separate approval and compatible fence/proof/recovery closure.
