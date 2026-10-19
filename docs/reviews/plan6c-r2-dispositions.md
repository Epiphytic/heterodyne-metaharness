# Plan6C actual second HIGH dispositions

Actual second FULL HIGH found ten issues and explicitly no APPROVE token. Raw report and exact artifact hashes are adjacent. This author incorporation is not another review or clearance. First-round reports remain intact.

1. **Conflicting confirmed receipts.** Retain original accepted receipt/message ID and bounded correlated conflicting IDs on same attempt; contradicted flag holds part and aggregate. Exact duplicate cannot erase diagnosis. Regression: `test_conflicting_confirmed_ids_are_retained_and_aggregate_holds`.

2. **Late receipt contradicting positive proof.** Locate original ordinal using one six-field header comparison, retain proof and raw diagnosis there, hold entire copy even after later ordinal confirms. ReceiptClaim includes ordinal; foreign header never contributes IDs. Regression: `test_late_prior_proof_receipt_stays_on_original_attempt_and_blocks_replay`.

3. **Dispatch mark erased by reconstruction.** Retain explicit dispatch_marked provenance. Constructors require marked phases to retain it and require exact correlated write-not-invoked prewrite facts before a marked attempt becomes proven_unissued. Owner may not discard stored provenance; no pure type authenticates a fabricated database. Regression: `test_reconstruction_cannot_drop_dispatch_mark_with_bare_proof`.

4. **Foreign continuation root.** Gates requires exact confirmed same-copy/owner part0 for continuation; validates root native ID and absence of contradictory/lifecycle state. Aggregate independently checks root linkage. Shared disposable fixture supplies synthetic confirmed root. Regression: `test_wrong_root_never_becomes_complete`.

5. **Diagnostic-dependent error codes.** Every PolicyError call site supplies an explicit closed code. Exception never parses diagnostic text; ordinary no-reservation/inflight conditions hold contention, mismatched proof refuses binding-conflict. Regression: `test_ordinary_contention_has_explicit_code`.

6. **Current binding drift terminal refusal.** Gate source-token, epoch and reply-target drift hold source-changed, trust-denied or parent-incomplete respectively. Actual immutable copy digest disagreement remains binding-conflict. Regression: `test_current_gate_drift_holds_instead_of_terminal_refusal`.

7. **Unsupported recovery epoch.** Reject recovery CopyRequest with unsupported-policy until genuine independently sealed epoch owner contract exists. No reconcile-selected recovery identity admitted. Regression: `test_unsealed_recovery_is_unsupported`.

8. **Reminder lifetime budget bypass.** Reserve and mark_dispatch explicitly refuse reminder effects as unsupported-policy. Pure ReminderBudget helper remains illustrative; no durable budget context or alternate authority is invented. Future actual budget owner remains OPEN. Regression: `test_reminder_cannot_reserve_without_real_durable_owner_budget`.

9. **Stale default source token after replace.** Computed source_token is init=False and recalculated from current Source on every construction/replace. Optional source_token_override is explicit owner input; former init source_token keyword intentionally changes in this dormant pure API. Regression: `test_replace_recomputes_derived_source_token`.

10. **Repeated correlation tuple.** Single _correlated(state,attempt,receipt) definition used for match validity and original-ordinal lookup/diagnosis. Regression: `test_foreign_receipt_cannot_pollute_original_proof_diagnosis`.

Only partial pure policy is implemented. Unsupported recovery and reminder effects refuse until actual owner protocols exist. No native authority, transport, storage adapter or live integration is installed. The controller alone runs the third MEDIUM review with the exact operator PoC question. All prior quality and qualification debt remains OPEN/unwaived.
