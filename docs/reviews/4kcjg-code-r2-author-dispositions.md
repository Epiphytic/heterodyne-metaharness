# Actual second HIGH review: author dispositions

Raw report SHA256: `84225576cf7f91fe10d3b06b983eaff9faf36a549436205ff2c58b3396c87b9d`. Actual verdict **REVISE**; all ten findings retained verbatim below. This is author repair evidence, not another review or approval.

1. `src/heterodyne/admind/daemon.py:1467`: record_not_spawned commits in its own transaction inside the except handler, and the overlay restore (hold_maintenance -> defer_maintenance) runs later in a separate transaction, so 'atomically mark that generation not-spawned and restore the previous eligible chain' (ADR §23) is not atomic.

   Failure scenario: A maintenance retry gets a cold or drifted cached_identity, so BtqError('unavailable') is raised with launched=False. record_not_spawned commits the proof at invocations=n+1. Then the process dies, or hold_maintenance raises (for example RuntimeError('maintenance pending capacity') from defer_maintenance, or an enqueue/notice error), before the overlay transaction commits. The ask is left at outcome='maintenance-issuing' with no ask_maintenance row. eligible() needs pending-maintenance or maintenance-held, and reconcile_issuing settles only an exact native decision, so a fully proven not-issued attempt holds its slot permanently. A sqlite error inside record_not_spawned also escapes the except block and skips the hold.


Author disposition: **fixed**. Dedicated NotSpawned evidence and defer_maintenance now share one Store transaction. If overlay commit fails, the proof remains at its previous generation and issuance stays conservatively unknown. Notification publication is a later transaction.
Regression coverage: `test_not_spawned_proof_and_overlay_share_rollback`.

2. `src/heterodyne/admind/retirement.py:376`: publish_notice counts only 'pending' or 'sent with message_id' as usable, so transient outbox states ('delayed', 'quarantined', 'suppressed') are treated like 'failed'. A refreshing `!asks` then repairs a notice that will still be delivered, and moves the current mapping away from it.

   Failure scenario: A suspended hold's notice N1 is in a rate-limited group with status='delayed'. The operator runs `!asks`: usable=False, so a repair notice N2 (key ...:repair:<seq>) is posted and maintenance_recovery_notices is rebound to N2. release_delayed then sends N1 and the operator replies to it with the correct token. select() joins only the current mapping (N2), so they get 'Retirement refused: reply to the current eligible held notice'. Each further `!asks` while N2 is also delayed or held adds another repair notice and another history row.


Author disposition: **fixed**. Delayed, quarantined and suppressed rows remain existing transport intents, even across proof generations. Only failed or unusable sent rows, or delivered stale-generation notices, may be replaced by explicit inspection; no automatic replay.
Regression coverage: `test_held_delivery_is_not_failed`.

3. `src/heterodyne/admind/daemon.py:1479`: record_hold now shares one transaction with hold_maintenance, which also runs the new publish_notice. Any notice-side exception rolls back the proof row after mark_maintenance_issuing has already committed in a separate transaction.

   Failure scenario: An exact HOLD returns with invocations=n. mark_maintenance_issuing commits. Inside the joint transaction, publish_notice -> daemon.post/enqueue or bind_notice raises (an IntegrityError on maintenance_recovery_notices.outbox_key UNIQUE, a storage error, or a capacity RuntimeError in defer). The whole block rolls back, so the n-th HOLD proof is lost and the attempt stays maintenance-issuing with proof n-1 < generation n. Every later record_hold or eligible check fails, so the attempt can never be retired. Notice publication failure should not erase already-observed evidence; the ADR only requires the proof to be atomic with defer, not with notice I/O.


Author disposition: **fixed**. Closed HOLD proof and overlay commit before notice publication. Expected local notice failures are audited without erasing evidence; storage/overlay commit failure still preserves unknown issuance and grants no retry.
Regression coverage: `test_notice_error_cannot_erase_observed_hold`, `test_hold_persistence_failure_restart_never_consumes_or_replays`.

4. `src/heterodyne/admind/daemon.py:1095`: Plain `!asks` now, as a side effect, publishes a fresh recovery notice for every retryable overlay whose generation advanced since its last notice. Each one also adds a row to the never-pruned maintenance_notice_history table, so inspection causes unbounded fan-out and growth.

   Failure scenario: With up to MAINTENANCE_CAPACITY=128 retryable overlays retrying every 30s, each `!asks` posts up to 128 new threaded notices and 128 new history rows (new token, new key retirement:<ask>:<token>). Repeated phone inspections flood the group and grow the outbox and history without limit. This contradicts the bounded one-active-proof/one-receipt storage the ADR (§33) and docs describe, and an inspection command should not have mass-post side effects.


Author disposition: **fixed**. An inspection snapshots pending overlays once, checks each eligibility once and publishes at most one new selector. Legacy maintenance_notice_history rows remain byte-preserved but no backfill or new history row is allocated. Current proof/mapping are replaceable; immutable existing outbox/attempt provenance and final receipts are retained. Older-token receipt reporting refuses when multiple historical attempts make association ambiguous; exact selected-token/same-command receipt remains usable.
Regression coverage: `test_inspection_publishes_at_most_one_and_scans_once`, `test_retry_notice_does_not_append_history`, `test_older_selector_never_reports_another_attempt_receipt`.

5. `src/heterodyne/admind/daemon.py:1466`: Positive not-spawned evidence is inferred in a broad `except Exception` from `issued and not launched and exc.word == "unavailable"`, instead of a dedicated exception type raised only at the two proven pre-spawn sites.

   Failure scenario: Any future or refactored code between `issued = True` and spawn that raises BtqError('unavailable') before on_launch would be recorded as proven not-spawned and extend the retirement chain. Examples: an extra read, an adapter pre-check, or reusing read() logic (`code != 0 -> BtqError('unavailable')`). The security evidence relies on a reused generic error word. A distinct NotSpawned raised only by decide's cache-miss guard and _run's Popen OSError branch would make the proof structural.


Author disposition: **fixed**. Only structural NotSpawned from the two actual pre-spawn sites can extend coverage. A generic unavailable exception never proves non-issuance. Offline real-adapter tests patch spawn failure rather than execute native tools.
Regression coverage: `test_generic_unavailable_never_proves_not_spawned`, `test_real_adapter_structural_not_spawned_sites`.

6. `src/heterodyne/admind/retirement.py:419`: The existing-receipt and same-command idempotency lookup runs only after the fresh authorised() check and the inbound-thread check. Redelivery of the SAME command whose sender has since lost authority gets a deny reply and audit, not the existing receipt with no effects.

   Failure scenario: Operator X retires an attempt (the receipt commits) and is then removed from the group. A recovery path, or redelivery that bypasses inbound dedup, replays X's command_id. retire() calls daemon.deny(command, 'retirement'), which emits a denial reply/audit for a command that already succeeded. ADR §33 requires that 'Repeated delivery of the SAME authenticated command returns its existing receipt, without new notice/native calls/effects'.


Author disposition: **fixed**. Exact same-command receipt lookup precedes fresh authority. It checks persisted sender/thread against the committed receipt as well as command/token/notice; changed sender or selector does not replay. Different commands retain fresh authorization requirements.
Regression coverage: `test_same_command_replay_precedes_revoked_authority`, `test_same_command_replay_rejects_rebound_sender`.

7. `src/heterodyne/admind/retirement.py:477`: The reconciled branch of retire, and every ordinary close path (settle_maintenance_recorded, reconcile, refresh, close_refused), leave the attempt's maintenance_proofs row and maintenance_recovery_notices mapping in place. Only the retirement commit deletes them.

   Failure scenario: An eligible attempt is reconciled as 'recorded' through `!asks retire`, or settled by a later retry or readback. Its proof row and current mapping stay forever. That contradicts 'remove only its exact maintenance overlay/proof' and the one-active-proof claim, and orphan rows accumulate per attempt. Correctness currently depends only on current_attempt() checks rather than on the state being cleaned up.


Author disposition: **fixed**. Store.close_attempt removes only the exact active proof/mapping on known terminal settlement in the same transaction, covering ordinary close and reconciliation. Uncertain settlement retains evidence and holds; immutable final receipts/attempt/outbox records are preserved.
Regression coverage: `test_terminal_close_removes_only_active_proof_mapping`, `test_unknown_postlaunch_failure_never_becomes_not_spawned`.

8. `src/heterodyne/admind/daemon.py:1104`: list_asks walks maintenance_pending() twice and calls current_attempt plus eligible (proof JSON parse, sha256 and 8-way validation) twice per overlay, once inside publish_notice and again for the listing line.

   Failure scenario: With 128 overlays, each `!asks` does about 256 proof decodes and validations and two full overlay scans under the store lock on the event loop. Computing (attempt, proof) once in the first loop and reusing it for both publication and the listing line would halve the work and avoid the two passes disagreeing.


Author disposition: **fixed**. The single transaction-scoped inspection pass reuses each computed (attempt,proof) for publication and listing. No second pending scan or repeated eligibility parse.
Regression coverage: `test_inspection_publishes_at_most_one_and_scans_once`.

9. `src/heterodyne/admind/retirement.py:369`: publish_notice runs raw `daemon.store.db.execute` SQL from a daemon-level module function instead of a locked Store or Retirement accessor, repeating the R1 finding-10 pattern that Store.inbound_thread was added to fix.

   Failure scenario: Both current callers happen to wrap publish_notice in store.transaction(), so the lock is held. Any new caller, such as a future reconcile or startup notice pass, would query outbox and mapping state without the store lock and could interleave with an enqueue/mark_sent in another thread. Moving the query into a @_locked Retirement method keeps lock discipline local.


Author disposition: **fixed**. Notice reads now use Retirement.notice under Store.lock. Mapping writes and publication use Store transactions even when called without an external transaction.
Regression coverage: `test_held_delivery_is_not_failed`, `test_notice_error_cannot_erase_observed_hold`.

10. `src/heterodyne/admind/retirement.py:133`: The proof body stores `consecutive`, which is always invocations - not_spawned and is validated only as that sum, so it is derivable state that carries no independent evidence.

    Failure scenario: _proof() rejects any row where consecutive + not_spawned != invocations, so the field can never say anything the other two fields do not. It adds a hashed field, a validation clause and a misleading suggestion of separate chain tracking (the R1 #4/#10 concern) that future readers must keep in sync. Dropping it, or deriving it at read time, shrinks the canonical body and the checks.

Author disposition: **fixed-with-legacy-compatibility**. New proof bodies derive holds as invocations-not_spawned and omit redundant consecutive. Existing bodies with consecutive remain readable only if its exact integer agrees; stored bytes/hashes/tokens and legacy rows are not migrated or rewritten. A newly observed generation uses the smaller canonical body.
Regression coverage: `test_legacy_proof_and_history_remain_readable_without_backfill`.

## Oracle compatibility and retained limits

The original `tests/test_admind_retirement.py` remains byte-identical. The maintenance-R2 notice-failure fixture alone supersedes its old expectation that notification failure destroys a positively proven HOLD: that branch now uses the existing ProvenPort (closed HOLD bytes and captured adapter identity), verifies durable eligibility, and recovers after restart/fresh retry. Its original storage-failure branch remains no-replay. The original file bytes and exact old-to-new diff are retained in private controller evidence. R1 coverage now uses structural NotSpawned only at the positive pre-spawn fake and derives redundant hold count; its known terminal settlement assertion now expects exact active-proof cleanup. No unknown native outcome is relabeled not-issued.

The current-generation 30-second reply race and upgraded-adapter compatibility HOLD remain unwaived. No new per-retry history is allocated, no historical row is erased, and no activation or deployment is performed. Quality regressions remain **UNWAIVED**.
