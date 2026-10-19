# P4 actual first HIGH author dispositions

The actual reviewer reported ten findings and explicitly did not approve. These are author dispositions, not a review pass. Exact raw report and hash-bound public provenance are adjacent; private original command and stream remain retained.

## 1. REPAIRED OFFLINE

A second authenticated agreeing settle retains the first immutable decision and consent; receipt records agreement. Even capacity failure cannot turn agreement into revocation.

Evidence: `test_agreeing_new_settle_retains_first_consent; test_agreeing_capacity_failure_does_not_revoke_existing_consent`.

## 2. REPAIRED OFFLINE; PRODUCTION PORT OPEN

Renewal requires exact source-owned certificate pair observed at/after old initiation expiry. Before successor publication, publish/read back identical source certificate as product generations/<proposal>/<old-generation>.superseded.json. Original permit immutable. Interrupted successor repair must reread original queue certificate pair; local retained metadata cannot fabricate a tombstone. Source/enrollment authenticity still requires absent production adapters.

Evidence: `test_early_nonconsumption_cannot_renew; test_renewal_requires_expired_old_window_and_persists_exact_source_tombstone; test_renewal_repair_requires_original_source_tombstone`.

## 3. PARTIAL REPAIR; SAFE RECOVERY OPEN

Duplicate failed/missing notice registration explicitly refuses notice-failed/notice-missing, and status exposes exact notice_status; no misleading registered response. Exact retained proposal/outbox/slots remain unchanged. No automatic requeue, deletion or inferred non-delivery. Genuine independent native recovery/readback and separately consented export/epoch reclamation are not implemented and remain activation blockers; this repair does not claim failed notice delivery can recover today.

Evidence: `test_failed_notice_is_reported_as_recovery_hold_not_registered`.

## 4. REPAIRED OFFLINE; RESERVE OPEN

An authenticated decline/revoke rebases only onto the same immutable descriptor/thread under the writer transaction and retains command plus revocation. A settle that loses CAS remains refused. Conflicting retained native-ID payload preserves original receipt and retains bounded conflict diagnostics; at payload cap, unissued authority still revoked without fake receipt. Physical reserved receipt capacity remains absent.

Evidence: `test_concurrent_authenticated_decline_is_not_lost; existing store rollback/capacity tests`.

## 5. REPAIRED OFFLINE

Terminal proposal state and bundle are immutable in both replay-conflict and capacity paths. Revocation guards only awaiting-consent/consented. New authentic terminal command is held without rewriting; original exact duplicate still reads earlier receipt.

Evidence: `test_terminal_state_survives_conflict_and_capacity`.

## 6. REPAIRED OFFLINE

Undelivered counts every custody proposal lacking exact sent outbox with non-null native message ID, including failed, held, missing rows and sent-without-ID. Never borrows shared notice quota.

Evidence: `test_failed_notices_still_exhaust_undelivered_limit; existing independent quota tests`.

## 7. REPAIRED OFFLINE

Publication fsync/readback no longer holds shared Store RLock. Dedicated per-service publication mutex serializes permit publication and native consent writers; daemon independent policy views share that same mutex. Store critical sections contain only bounded DB work. Production single enrolled owner remains required.

Evidence: `test_publication_never_holds_shared_store_lock; test_independent_policy_view_shares_publication_mutex; existing concurrent revocation/rollback tests`.

## 8. REPAIRED OFFLINE

Direct ordinary-work fallback routes custody commands into independent custody_work instead of processing custody under model work lock. Primary on_event routing stays unchanged.

Evidence: `test_direct_message_fallback_routes_to_independent_worker; existing actual daemon/model-lock test`.

## 9. REPAIRED; QUALITY DEBT OPEN

Registration and retained source comparison share validate_observation; settle calls observation. MAX_TOTAL and transactional capacity() centralize per-bundle/aggregate byte accounting. Source checks are not replaced by supplied claims. Internal command state/transaction/conflict helpers keep message entrypoint readable; quality output retained without waiver.

Evidence: `existing source drift, capacity and rollback tests`.

## 10. REPAIRED OFFLINE

Gateway custody strict decoder receives frame payload excluding trailing newline, matching existing server boundary check. JSON payload <=256KiB still strict; no budget expansion.

Evidence: `test_socket_exact_maximum_excludes_frame_newline; existing socket duplicate-key/bounds tests`.

- Dormant offline source slice only; not task completion, live transport qualification or deployment clearance.
- Production descriptor/source/publication enrollment, native consumer ACK/queue settlement, physical 256KiB reserve and intake crash-redelivery remain OPEN/HOLD.
- Failed native notice has no proven recovery/replay port in this slice. Original obligations/slots remain retained.
- All predecessor and current quality debt remain OPEN UNWAIVED; no review verdict or approval manufactured.
