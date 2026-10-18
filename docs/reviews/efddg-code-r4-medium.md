**Verdict: REVISE.** I found two critical correctness flaws that should be fixed before this is a PoC, plus four other correctness problems:

1. **Denial:** a committed deny can be bypassed by grooming, and the request then deadlocks.
2. **Uncertain dispatch:** after a reboot or suspend, a consumed dispatch has no exit at all.

I reviewed the full range 35176c85..5d3acfd6 against the third-round report and its dispositions. I made no edits and ran no tests, probes or child agents, so none of this was verified by running code. Most of the third-round fixes look correct where they apply, and none of these findings repeats one of them exactly. Findings 4 and 5 are the third-round #8 and #5 patterns still present on paths those fixes didn't cover (decisions and allocation).

Findings 3 and 4 also bear on the evidence you asked about: the reconciliation outcome that settles a dispatch is still taken from the caller's own proposal, not independently observed.

10 findings, most severe first:

1. `src/heterodyne/actions/transitions.py:73`: A request can be groomed from ask-pinned/sufficient while a deny decision is pending or already committed (the groom branch ignores the loaded decision). The deny never reaches the session denials table, and the request then has no terminal exit.

   Failure scenario: Request at ask-pinned (rev 1). Operator deny is recorded and committed (begin_decision allows deny at ask-pinned; decision_transition never re-checks the request). Caller grooms the SAME intent: groom only checks prior_denials, which is refreshed from the empty denials table, so it passes, and park/land/pin/sufficient/filed proceed to rev 2. The operator is shown the rejected card again. A new approve goes to add_decision -> 'superseded' (the committed deny holds the key). 'denied' refuses because decision.request_revision 1 != 2, 'cancelled' refuses (committed deny, and filed is not cancellable), and 'completed' has no dispatch. The request permanently holds a per-bead slot and a global slot.

2. `src/heterodyne/actions/dispatch.py:73`: A consumed-claim dispatch whose clock observations cross a reboot, or whose UTC/monotonic gap exceeds 1s (suspend, NTP step), can never leave 'uncertain'. Every lookup fails elapsed_seconds, and settle_dispatch requires reconciliation_lookups == 3 for consumed claims.

   Failure scenario: Claim consumed, then the host reboots (the classic uncertain-dispatch case). Each reconcile_lookup transition fails in elapsed_seconds because now.boot_id != started.boot_id ('dispatch-clock-or-deadline'). Operator settle_dispatch fails because claim_consumed and reconciliation_lookups != 3. The resource/effect/approval locks are never deleted, and the request can never complete. There is no authenticated-recovery path for this case.

3. `src/heterodyne/actions/transitions.py:154`: The read-only reconcile child's outcome is taken directly from the caller's proposal: finish_reconciliation receives value.reconciliation_effect and value.evidence with no TransitionObservation. The child also never launches or claims (preparing -> failed directly). So the 'provenance' that lets the parent settle 'succeeded' is the caller's own assertion.

   Failure scenario: Parent uncertain with claim consumed. Caller adds a reconcile child, then transitions it with a proposal reconciliation_effect='succeeded', evidence=<any digest>, cleanup_proven=True, which is accepted. The parent reconcile_lookup with that child's key then settles 'succeeded', deletes the locks, and the request completes. No read-only invocation ran and no independent observation exists, which is the same 'validator manufactures authority' pattern as third-round #8.

4. `src/heterodyne/actions/transitions.py:113`: decision_transition still asserts authority itself (issue_decision fresh_authority=True; invalidate current_check=True; finalize native_readback=True; read_ok=True). No recovery or authority observation is required, and issue checks old.digest instead of the durable request's current digest.

   Failure scenario: Any caller can invalidate an authentic pending operator approve or deny with reason 'authority-revoked' and no evidence. Re-adding the same event then returns 'superseded' (history check), so the operator's decision is silently discarded. Separately, a decision for revision 1 can still be issued and committed after the request has been groomed to revision 2, which produces the deadlock in finding 1.

5. `src/heterodyne/actions/transitions.py:106`: Allocation backoff uses an unanchored caller integer (allocation_elapsed_seconds) with no durable start time, so the 1/5/30s schedule is not enforced. Dispatch anchors to started/Clock; allocation does not.

   Failure scenario: Request in allocation-unknown. Three observe_allocation transitions are committed back-to-back in milliseconds, each with observation allocation_elapsed_seconds=30. All pass, lookups reach 3, and the request is pushed into abandonment/recovery without any real backoff. The third-round #5 problem persists on the allocation path.

6. `src/heterodyne/actions/requests.py:331`: A 'filed' request with no decision, or whose decision was invalidated, has no exit: cancellation excludes 'filed', abandonment needs allocation-unknown, and denied/completed need a committed decision or dispatch.

   Failure scenario: Request filed. The operator never answers, or the ask is withdrawn natively. Nothing can settle the request, so it holds an outstanding slot indefinitely. Two such requests lock the bead out of new requests ('two-outstanding-per-bead').

7. `src/heterodyne/actions/requests.py:317`: The third-round #3 repair records a failed/absent-effect initial dispatch as request phase 'completed', the same terminal state as a successful effect, so request-level consumers cannot tell success from failure.

   Failure scenario: Dispatch settled 'failed' by operator recovery, then settle_request('completed'). Any consumer reading the request row or the outstanding counts sees 'completed' and reports the action as done. The failure is only visible by also loading the dispatch row.

8. `src/heterodyne/actions/dispatch.py:302`: reconcile_lookup from an unsettled 'failed' (absent-effect) parent with a non-'succeeded' child result rewrites state to 'uncertain' and overwrites evidence. An 'absent' child effect is treated as 'unknown', discarding definitive absence observations.

   Failure scenario: report(effect='absent') gives failed with evidence E1. The next lookup with a child effect of 'absent' gives state='uncertain' with evidence E2, so E1 is lost from the current row. The operator must still burn all 3 lookups, and the settled record no longer shows the original absent observation.

9. `src/heterodyne/actions/journal.py:337`: Reconcile children may be created while the parent is 'held' (unconsumed, nothing to reconcile). Parent lookups refuse from 'held', so the child count desynchronizes from parent.reconciliation_lookups and every further child refuses.

   Failure scenario: Parent held after preparation failure. Caller adds reconcile child #1 and settles it. Parent lookups refuse (state must be uncertain/failed), and adding child #2 refuses with 'reconcile-parent-observation-required' (1 != 0). The orphan read-only row stays and counts toward MAX_RECORDS.

10. `src/heterodyne/actions/journal.py:224`: _request_for_approval decodes every request row (up to 4096) on each add_decision and add_dispatch inside BEGIN IMMEDIATE, because the requests table has no approval_id column or index.

    Failure scenario: As the journal fills toward MAX_RECORDS, each decision or dispatch insertion does a full table scan plus msgspec decode while holding the write lock, which lengthens lock hold time and increases contention and busy-timeout refusals. A stored approval_id column with a unique index would avoid this.
