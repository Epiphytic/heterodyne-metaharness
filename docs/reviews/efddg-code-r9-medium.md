**Verdict: REVISE.** This is not good enough to implement as a PoC yet. The R8 repairs fix the double clock sample (R8#3, R8#9), caller time prediction (R8#6), unproven launcher cleanup (R8#7) and launch spacing (R8#8). But the R8#1/#4 fix broadens the child-disposition check in `_recovery_clock`, and that breaks something that worked before: an operator can no longer settle a reconciliation child whose preparation failed once any sibling has finished its read. That child stays held forever, so the parent can never settle and its resource, effect and approval locks are never released. This is the same liveness class R7#2 and R8#1 were treated as critical for.

I reviewed `35176c85..d7a52336` read-only and statically, focusing on the R8 delta `39459ca..d7a5233`. I ran no tests or probes. These are deduplicated but unverified candidates, and the static trace for finding 1 is fairly concrete. No CLAUDE.md findings: the only code rule (cargo clippy) does not apply to this Python code.

10 findings, most severe first:

1. `src/heterodyne/actions/journal.py:683`: The broadened disposition check now runs for any DispatchRecovery on a reconcile child, works out 'applied' against the child itself (whose reconciliation_attempts is always empty), and then refuses because old.dispatch_kind != 'initial'. A held child whose preparation failed can never be operator-settled once any sibling has a reconciliation_effect.

   Failure scenario: Parent goes uncertain. Child #1 finishes with effect=unknown and the parent lookup applies it (child #1 is now in parent.reconciliation_attempts). Five seconds later child #2 is inserted and fails preparation (state held, settled=False). The operator settles child #2: _recovery_clock gets children=[c1,c2], computes c1.applied = c1 in c2.reconciliation_attempts = False, so unread=True; dispatch_kind=='reconcile' then raises reconciliation-exhaustion-provenance-required no matter what observed_reconciliations contains. Child #2 is wedged. _add_reconciliation refuses child #3 (reconcile-invocation-still-held). Parent settlement refuses (child not settled), and so does the final release loop. Resource, effect and approval locks are never deleted. The R8 capacity test misses this because its preparation-failed children all come before the only child that has an effect.

2. `src/heterodyne/actions/journal.py:478`: The child launch gate clock_elapsed(previous_start, value.started) raises on any boot change, so after a reboot (the main reason a parent is uncertain) no reconciliation read can ever launch. Separately, when db.clock is None the gate uses the caller-supplied value.started, so the 1/5/30 spacing is caller-controlled.

   Failure scenario: The host reboots while the parent is executing, and the parent becomes uncertain on the new boot. Every add_dispatch for a reconcile child raises dispatch-clock-or-deadline, which is a different code from reconcile-attempt-not-due, so the only exit is operator discontinuity settlement and no machine read is possible. In a journal built without a clock, a caller passes prepare_reconciliation(..., now=parent.started + 31s) three times back-to-back and passes every gate. 'All three read launches gated 1/5/30' holds only under an injected clock.

3. `src/heterodyne/actions/dispatch.py:387`: The pure settle_dispatch 'conflict' bypass relies entirely on caller-built ReconciliationDisposition rows, so one fabricated row with effect=succeeded and applied=False lifts the lookups==3 requirement in the reducer. That brings back the R8#5 two-validator gap through a new field.

   Failure scenario: A reducer-only caller with a failed, claim-consumed parent at lookups=0 passes observed_reconciliations=(ReconciliationDisposition('x',1,'succeeded',None,True,True,False),). bound and conflict are both True, so the parent settles without any child having existed. Only the journal's equality check against stored children catches this.

4. `src/heterodyne/actions/journal.py:550`: The journal silently overwrites the operator-supplied recovery.observed_now (and observation.now) with its own sample, so the retained '-observation' event no longer records what the operator attested. The operator's value is never compared with the sample.

   Failure scenario: The operator attests observed_now=Clock('B',5,5), but the sampler returns Clock('C',9,9). The journal swaps in C, finds it discontinuous from the anchor and settles. The retained observation shows C as the attested value, and nothing detects that the operator's attestation disagreed with the trusted sample. 'Exact-scope attestation' is not exact on this field.

5. `src/heterodyne/actions/journal.py:381`: add_dispatch rebases started, deadline_ns and preparation_deadline_ns onto the sampled clock but returns None, so the caller's in-memory Dispatch differs from the stored record.

   Failure scenario: A caller prepares at caller-time T, add_dispatch stores started=S (the sample), and the caller then builds a legacy reducer or prepare_reconciliation from its own copy started at T. Comparisons such as `candidate != value` or later deadline arithmetic diverge, giving dispatch-transition-not-closed or the wrong deadline, unless every caller re-reads with db.get.

6. `src/heterodyne/actions/dispatch.py:374`: The first child's 1-second gate (and the parent's first lookup gate) is measured from the parent's dispatch start, not from when the parent became uncertain or failed, so the first read is effectively ungated.

   Failure scenario: The parent starts at t=0 and goes uncertain at t=50s. Child #1 launches immediately at t=50s because 50 >= 1. Child spacing therefore starts at whenever uncertainty happened, not at the D4 '1/5/30 seconds' after the uncertain result.

7. `src/heterodyne/actions/dispatch.py:369`: completed_reconciliation_attempts can be derived from observed_reconciliations, but both are kept and cross-checked in two places (settle_dispatch and _recovery_clock). Each validator has slightly different rules: the reducer allows 1-3 facts, while the journal requires exactly 3 children when the IDs are given.

   Failure scenario: The two exhaustion encodings can drift, and an operator payload has to state the same IDs twice. Dropping completed_reconciliation_attempts and deriving exhaustion as len(observed)==3 removes a whole class of mismatch refusals and halves the validation surface.

8. `src/heterodyne/actions/journal.py:530`: needs_clock is an ad-hoc list of operation names plus recovery flags placed in the shared transition path, so a new clock-bearing operation silently skips sampling and provenance unless someone remembers to add it here.

   Failure scenario: A future operation added to dispatch_transition that calls _observed_clock without being listed here gets the caller's observation.now when it supplies one, or no clock otherwise, and writes no '-clock' event. Later _proven_clock recovery then holds for good. Sampling once on every clock-configured transition, or having dispatch_transition declare which operations need a clock, removes this special-case list.

9. `src/heterodyne/actions/journal.py:555`: cast(ObservedReducer[Any], reducer) right after an isinstance check is redundant.

   Failure scenario: The cast adds noise and hides type errors. `reducer.reduce(old, observation)` inside the isinstance branch narrows the type the same way.

10. `src/heterodyne/actions/journal.py:684`: Every DispatchRecovery transition, plus the settled-initial release loop, re-queries and JSON-decodes all reconciliation children with a LIKE scan, sometimes twice per transaction.

    Failure scenario: A parent settle runs _reconciliation_children in _recovery_clock and again in the release check, which is two LIKE scans and up to six decodes per transaction. It is small but redundant: fetch once and pass the tuple through.

The fix for finding 1 is to compute dispositions only when `old.dispatch_kind == "initial"`. Settling a held reconcile child should skip the sibling-disposition check entirely. A regression test should cover the order: finish child #1, apply it to the parent, fail child #2's preparation, operator-settle child #2, then settle the parent.
