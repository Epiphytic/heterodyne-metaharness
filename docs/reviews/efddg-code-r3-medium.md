**Verdict: REVISE.** I found 9 issues. The four state-machine and settlement issues (findings 1 to 4) need fixing before this can serve as the PoC. I read the full range 35176c85..9503f84f and the R1/R2 dispositions. I ran no tests and made no edits, so nothing below was verified by running code.

None of the findings repeats an R1 or R2 finding that was dispositioned as fixed. Finding 1 is the R1#2/R2#4 problem showing up again on a path those fixes didn't cover: they closed decision transitions, but not inserting a new decision.

9 findings, most severe first:

1. `src/heterodyne/actions/journal.py:213`: add_decision persists any caller-built pending Decision without re-running begin_decision or checking the request: it never checks the request is filed, that context_digest matches, that Decision.digest equals event.digest, or the current_principal/surface_bound/authority_current flags. decision_transition then supplies fresh_authority/native_readback=True itself, and add_dispatch only matches decision.digest against the caller-chosen dispatch digest.

   Failure scenario: Request R is still 'allocated' or 'ask-pinned' (never sufficient or filed). The caller builds Decision(approval_id=R.approval_id, purpose='execution', digest=D, event=<approve, all authority flags False>) and calls add_decision -> 'recorded'. It then steps the decision through issue/read/close/pins/finish to 'committed' using only transition(). prepare(..., digest=D) then passes add_dispatch, and ready/consume_claim take it to 'executing'. An effect claim is consumed for a request that was never filed or approved by any authenticated operator.

2. `src/heterodyne/actions/dispatch.py:286`: reconcile_lookup lets an initial dispatch settle as 'succeeded' without checking claim_consumed, and uncertain() can be applied from 'preparing' or 'held'. Together they settle a dispatch as succeeded when no claim was ever issued, which settle_dispatch explicitly forbids (effect_attested_succeeded and not claim_consumed).

   Failure scenario: add_dispatch(preparing) -> transition to uncertain(old) (dispatch_transition accepts `value == dis.uncertain(old)`) -> reconcile_lookup(effect='succeeded', cleanup_proven=True, evidence=<digest>). The result is state='succeeded', settled=True, claim_consumed=False. The resource/effect/approval locks are deleted, and settle_request('completed') accepts it, so the request completes with no effect claim ever issued or consumed.

3. `src/heterodyne/actions/requests.py:302`: A 'filed' request has no terminal exit once its initial dispatch settles 'failed'. 'completed' needs a succeeded dispatch, 'denied' needs a deny decision (the approval already holds the single committed approve), and 'cancelled' is only allowed before filing. The request stays outstanding=1 forever.

   Failure scenario: The request is filed and approved. The dispatch is consumed, reconciliation stays unknown 3 times, and the operator attests the effect is absent via settle_dispatch -> state='failed', settled=True. Every settle_request phase now refuses for this request. It permanently holds one of the bead's two outstanding slots and one of the 128 global slots, and repeated failures lock the bead out entirely.

4. `src/heterodyne/actions/requests.py:315`: Cancelling from 'ask-pinned'/'sufficient' does not check for a committed deny decision. A request whose deny is already committed (deny is now allowed at ask-pinned/sufficient) can be cancelled instead of denied, so no session denial row is written and the denial check is bypassed.

   Failure scenario: The request is at ask-pinned. The operator's authenticated deny is committed. The caller then transitions the request to 'cancelled' (request_transition passes decision=None, and the cancelled branch ignores decisions) -> outstanding=0, and the denials table is empty. The same session's add_request with the identical intent passes the `denied()` check, and the operator sees the rejected card again.

5. `src/heterodyne/actions/transitions.py:176`: The durable transition validators make up time instead of reading it. consume_claim is validated with now=old.started, ready/restart derive 'now' from the deadline in the proposal itself, and the lookup backoff gets elapsed_seconds=(1,5,30)[n] filled in. Claim expiry, the 60s dispatch deadline and the 1/5/30s readback backoff are therefore never enforced on the journal path.

   Failure scenario: A dispatch is set to 'ready' with claim_deadline=start+5s. Hours later transition(consume_claim) still validates, because _within/claim-deadline checks run against old.started, which is always inside the window, so the one-use claim is consumed long after expiry. Likewise, three reconcile_lookup or observe_allocation transitions can be committed back-to-back in milliseconds, using up the bounded lookups without any backoff.

6. `src/heterodyne/actions/journal.py:422`: record_denial takes a caller-supplied Request instead of loading the durable request row. Any intent/origin whose approval_id/context_digest/request_revision match a committed deny is written into denial history under that request's session.

   Failure scenario: A deny is committed for approval A (digest D, rev 1). The caller builds Request(origin.session_key=S2, intent=<unrelated push Y>, approval_id=A, context_digest=D, request_revision=1, denial_identity=request_denial_key(Y, S2...)) and calls record_denial -> a denials row is written for identity Y in session S2. Every later add_request or filing of Y in S2 is refused as same-session-denied, even though nobody denied Y.

7. `src/heterodyne/actions/dispatch.py:286`: The initial dispatch's reconciliation_lookups/succeeded settlement is not tied to any of the read-only reconcile invocation rows. Those rows are capped at 3 and serialized, but their evidence never feeds the parent, and parent lookups need no settled reconcile invocation.

   Failure scenario: The parent is uncertain. Three reconcile_lookup transitions on the parent run (and one can settle it 'succeeded') with zero reconcile invocation rows ever created. Conversely, three settled reconcile rows leave parent.reconciliation_lookups at 0. 'Bounded serial read-only reconciliation' is therefore two independent counters, and the attested parent outcome has no invocation provenance.

8. `src/heterodyne/actions/transitions.py:141`: Operator settlement through the journal sets authenticated_current/exact_repair_scope/native_readback/cleanup_proven=True itself, so any proposal with recovery_operator_event set to a printable label and an evidence digest settles the dispatch and releases its locks.

   Failure scenario: A dispatch is 'held' (unconsumed, after a preparation failure). The caller proposes changed(old, state='failed', settled=True, cleanup_proven=True, evidence=<any digest>, recovery_operator_event='x'). dispatch_transition builds an all-True DispatchRecovery, so settle_dispatch accepts it and the resource/effect locks are deleted with no operator observation behind it. The same pattern applies to allocation recovery/abandonment in request_transition.

9. `src/heterodyne/actions/dispatch.py:362`: settle_failed_dispatch is described as a 'compatibility entrypoint', but this is a brand-new module with no prior callers. The only references are one test and the README, so it is dead duplicate API around settle_dispatch.

   Failure scenario: There are two public settlement entrypoints with overlapping checks. A future change to settle_dispatch's preconditions has to be reasoned about twice, and adapters may bind to the redundant wrapper. Removing it and testing settle_dispatch with effect_definitively_absent covers the same case.
