**Verdict: REVISE.** One flaw directly undoes the R5#2/R5#3 clock-recovery repairs, so this isn't ready as a PoC yet. A caller can still skip the allocation reads by claiming a clock discontinuity, and the README says that cannot happen.

This was a read-only static review of the full range 35176c85..1d684561. I ran no tests or probes, so none of this is verified by running code. No CLAUDE.md rule applies to this Python code, so the conventions pass found nothing. As a high-effort recall review I deduplicated the findings but did not verify them.

The other R5 repairs hold up statically:
- explicit dispatch operation tags
- `DispatchReadback`
- `CleanupReadback`
- absence surviving later `uncertain` transitions
- per-owner reserve isolation

The worst flaw is finding 1. Findings 2–4 are real gaps but smaller. Findings 5–10 are hardening and cleanup.

10 findings, most severe first:

1. `src/heterodyne/actions/journal.py:571`: _recovery_clock checks only the first non-None recovery (dispatch_recovery or allocation_recovery or abandonment_recovery), and the pure reducers recover_allocation and settle_request trust the bare clock_discontinuity flag. A decoy recovery therefore skips the journal's own clock sample and discontinuity check for the recovery that is actually used.

   Failure scenario: Request in allocation-unknown with 0 lookups and a continuous clock. The caller transitions with an observation holding dispatch_recovery=<any DispatchRecovery, clock_discontinuity=False> (or allocation_recovery with clock_discontinuity=False on the abandonment path) plus allocation_recovery/abandonment_recovery with clock_discontinuity=True and an arbitrary or None observed_now. _recovery_clock picks the decoy, sees no discontinuity and returns. request_transition then calls recover_allocation or settle_request('reconciled-abandoned'), which accept because clock_discontinuity is True. The three lookups are skipped with no journal sampler, which contradicts the README's 'A caller-selected boot ID alone cannot bypass reads'. Requests also accept a DispatchRecovery with no type check.

2. `src/heterodyne/actions/transitions.py:376`: Dispatch settlement (the 'report' operation on an uncertain parent, or 'settle') does not require outstanding reconciliation children to be settled. The parent can settle, release its locks, and let the request complete while a read-only reconcile child is still 'preparing' and its cleanup is unproven.

   Failure scenario: The parent goes uncertain and a reconcile child is added through add_dispatch, but not finished. An exact DispatchReadback report(effect=succeeded) settles the parent, the resources are deleted, and settle_request('completed') sets outstanding=0. The child row stays unsettled, with a live read-only invocation and no cleanup proof. Once the request is terminal, _terminal_budget gives no owner, so in reserve mode the child's own settlement is refused for good.

3. `src/heterodyne/actions/journal.py:521`: Lookup backoff anchors and the lookup 'now' both come from the caller's observation.now. Even when a journal clock is configured, the journal never samples it for allocation or dispatch lookups, so the 1/5/30-second spacing can be satisfied with made-up timestamps.

   Failure scenario: The caller commits three persist_reconcile_observation-style lookups within milliseconds of real time, supplying now = started+1s, +6s and +36s on the same boot_id. Each check against the previous retained observation.now passes, and the dispatch reaches 3 lookups and operator settlement with no real backoff. Allocation lookups have the same hole. The R5#6 outcome is still reachable through ordinary synthetic inputs, even though a sampler exists.

4. `src/heterodyne/actions/journal.py:364`: add_dispatch (and _recovery_progress treating allocation-intent→allocation-unknown as progress) lets new effect work spend the owner's terminal reserve. That contradicts 'historical missing headroom HOLD new work'.

   Failure scenario: The journal is at the reserve boundary and the request is filed with a committed approval. add_dispatch creates a new initial dispatch, takes the resource/effect/approval locks and spends reserve events. preparing→ready is not progress, so the dispatch can only go held and then needs operator settle_dispatch. Reserve is burned and locks are held for work that should have been HOLD. Likewise, issue_allocation can trigger a native create under reserve.

5. `src/heterodyne/actions/dispatch.py:298`: reconcile_lookup's succeeded branch runs before the failed-state preservation. A dispatch already marked definitively absent ('failed' with absent evidence) is flipped to settled 'succeeded' with the child's evidence, and the absent proof is lost from the current row.

   Failure scenario: report(effect=absent, evidence=E1, cleanup=True) gives state failed. The next read-only child returns succeeded with evidence E2 and cleanup. The lookup sets state=succeeded, settled=True, evidence=E2, and the request completes as 'succeeded' with no operator review of the contradictory evidence. The README's 'Absent parent evidence survives later ... reads' is only partly true.

6. `src/heterodyne/actions/requests.py:384`: settle_request copies recovery_operator_event and recovery_evidence from any AbandonmentRecovery or CancellationRecovery it is given, even on paths that never validate that recovery. request_transition passes abandonment_recovery for 'completed' and 'denied'.

   Failure scenario: A 'completed' transition whose observation carries abandonment_recovery=AbandonmentRecovery(evidence=X, operator_event='fake', authenticated_current=False) is accepted, and the request durably records an operator recovery event and evidence that were never authenticated. The same happens for cancellation from allocated..landed with an unvalidated CancellationRecovery.

7. `src/heterodyne/actions/dispatch.py:348`: settle_dispatch recomputes discontinuity against 'clock_anchor or value.started'. The journal verifies it against the retained last-lookup anchor, and only checks clock_anchor when it is non-None, so there are two different anchors for one fact.

   Failure scenario: With lookups>0 and clock_anchor=None, the journal proves discontinuity against lookup anchor L while the reducer re-checks against started. A UTC step between started and L makes the two disagree, so a verified recovery can be refused, or a reducer-only caller can use the wrong anchor. The journal should pass the verified discontinuity or anchor into the reducer instead of letting it re-derive one.

8. `src/heterodyne/actions/transitions.py:157`: Request transitions are still routed by the shape of the proposal (for example, allocation-unknown goes to recover_allocation if and only if value.recovery_operator_event is set, and the terminal branch picks its recovery by value.phase). The explicit-operation fix from R5#8 was applied to dispatch only.

   Failure scenario: Each new request reducer or recovery path must re-audit the inference chain. The finding 1 and finding 6 routes come from observation fields being chosen implicitly rather than from an operation tag that requires exactly one matching observation type.

9. `src/heterodyne/actions/journal.py:178`: _reserved() runs on every _event call and issues one capacity-recovery query, with JSON decode, for each outstanding request. _dispatch_lookup_clock also rescans and decodes all of a key's dispatch events.

   Failure scenario: With 128 outstanding requests, each event insert costs about 129 extra queries, and a transition writes 2–3 events, all inside BEGIN IMMEDIATE. That lengthens write-lock hold time. A maintained per-owner used counter, or one aggregated GROUP BY query, would do the same job.

10. `src/heterodyne/actions/journal.py:191`: Once _capacity_spent is non-zero, every later event in the transaction is charged to the owner's reserve, even after headroom would have returned in normal mode. A single transaction can also cross the boundary partway through.

    Failure scenario: A terminal settlement that frees the owner's own reserve (outstanding goes to 0) still charges its observation event and a capacity-recovery marker, so it spends up to 3 events of a reserve that is just being released. This makes the 96-event sizing harder to reason about. Low impact but not obvious.
