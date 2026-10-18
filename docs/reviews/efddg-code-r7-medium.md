**Verdict: REVISE.** This isn't good enough to implement as a PoC yet. The R6 repair does what it set out to do: a decoy second recovery is refused, and a live reconciliation child now blocks the parent from releasing its resources. But one flaw still lets a caller skip the required reads, which is exactly what the README says can't happen.

I reviewed the full range `35176c85..fb9a30c5` read-only and statically. I ran no tests or probes, and as a high-effort recall pass these findings are deduplicated but not verified.

- **The critical flaw (finding 1):** every anchor the journal uses to prove a clock discontinuity is a timestamp the caller supplied. The sampler only provides "now". So a caller who picks a fake boot ID once, at issuance or prepare, gets a "verified" discontinuity and skips all three lookups.
- **A new liveness gap from the R6 fence (finding 2):** a parent can wedge permanently with its resources held. This happens once one of its reconciliation children fails to launch and an operator has to settle that child.
- **Still present from R6:** findings 3, 6, 7 and 9 (unvalidated recovery metadata, the two different anchors, request routing by proposal shape, the extra queries per event).
- **No CLAUDE.md findings:** none of the CLAUDE.md rules apply to this Python code.

10 findings, most severe first:

1. `src/heterodyne/actions/journal.py:612`: Clock-discontinuity verification compares the journal's sampled clock against an anchor the caller supplied (the request issuance/lookup observation.now, Dispatch.started from prepare(now), or the dispatch lookup observation.now), so a caller-selected boot ID still bypasses the three lookups, contradicting README line 271.

   Failure scenario: Journal created with clock=real sampler on boot 'B'. Caller issues allocation (allocation-intent -> allocation-unknown, transitions.py:154) with observation.now=Clock('fake',1,1); _observed_clock accepts any well-formed clock and it is retained as the request-observation. Immediately (0 lookups) the caller transitions to reconciled-abandoned (or recover_allocation) with a single AbandonmentRecovery(clock_discontinuity=True, observed_now=<sampler value>). _recovery_clock samples 'B', reads started='fake', clock_elapsed raises on the boot_id mismatch, and the transition is accepted as discontinuous; recover_allocation/settle_request then skip allocation_lookups==3. The same works for dispatch: prepare/ready/consume all run on boot 'fake' (_within only checks against value.started), the dispatch goes uncertain, and settle_dispatch with clock_discontinuity skips the three reconciliation lookups. The R6 tests themselves drive caller clocks on boot 'continuous'.

2. `src/heterodyne/actions/journal.py:431`: A reconciliation child that leaves the preparing state through preparation-failed (not rejected for dispatch_kind='reconcile') can only be settled by an operator, which leaves it without reconciliation_effect. After that the parent can never add another child (reconciliations != lookups) or advance lookups, so its resources stay held permanently unless a clock discontinuity is proven.

   Failure scenario: Parent uncertain with lookups=0. A reconcile child is added, its launcher fails, and preparation-failed moves it to held. restart-preparation is refused after 3 attempts or past its 60s deadline (or under reserve, because restart is not counted as recovery progress), so the child stays held. With the new R6 fence the parent cannot settle while the child is unsettled. The operator settles the child with settle_dispatch, giving state failed, settled, reconciliation_effect=None. The lookup operation now refuses this child (reconciliation_effect is None). _add_reconciliation counts 1 child against parent.reconciliation_lookups=0 and refuses every new child. settle_dispatch on the parent needs lookups==3. The parent is wedged and its resource, effect and approval locks are never released.

3. `src/heterodyne/actions/requests.py:395`: settle_request still durably copies recovery_operator_event and recovery_evidence from any AbandonmentRecovery or CancellationRecovery it is given, including on 'completed', 'denied' and allocated..landed cancellation paths that never validate it (R6#6 is still present).

   Failure scenario: A 'completed' transition whose observation carries abandonment_recovery=AbandonmentRecovery(operator_event='forged', evidence=<digest>, authenticated_current=False, clock_discontinuity=False) passes _recovery_clock (single recovery, no discontinuity). The terminal request is written with recovery_operator_event='forged' as if an authenticated operator recovery happened.

4. `src/heterodyne/actions/journal.py:584`: The multiple-recovery check excludes cancellation_recovery, so an observation can still carry two recovery attestations (abandonment plus cancellation). Which one applies is decided implicitly by value.phase in request_transition.

   Failure scenario: A 'cancelled' transition carrying both an AbandonmentRecovery(clock_discontinuity=True) and a CancellationRecovery makes the journal sample and verify the abandonment clock (possibly replacing observation.now), while settle_request uses the cancellation recovery. Unused, conflicting attestations are retained in the observation event and the 'exactly one recovery' invariant the R6 fix claims does not hold.

5. `src/heterodyne/actions/dispatch.py:348`: settle_dispatch re-derives discontinuity against 'clock_anchor or value.started', while the journal verified it against _dispatch_lookup_clock (the last lookup's clock) and only checks clock_anchor when it is non-None, so the two disagree when lookups>0 and the anchor is None (R6#7 is still present).

   Failure scenario: Lookups=2, clock_anchor=None, last lookup L on boot B2, started on boot B1, sampled now on boot B2 and continuous with L. The journal raises clock-recovery-not-discontinuous, which is correct. With now on B3 the journal accepts. A reducer-only caller is checked against started instead and accepts the B2 case. Two validators exist for one fact, and only the journal's is safe.

6. `src/heterodyne/actions/journal.py:364`: add_dispatch and allocation issuance (allocation-intent treated as recovery progress in _recovery_progress) can spend the owner's terminal reserve on new effect work, contrary to 'historical missing headroom HOLD new work' (R6#4 is still present).

   Failure scenario: The journal is at MAX_EVENTS minus reserved. add_dispatch calls _terminal_budget and inserts an initial dispatch plus its locks, using reserve events. preparing->ready is not progress, so ready is refused under reserve and the dispatch can only go held, consuming more reserve. The finite 96-event recovery headroom is burned on work that should have been held.

7. `src/heterodyne/actions/dispatch.py:298`: reconcile_lookup's succeeded branch runs before the failed-state preservation, so a parent already proven definitively absent is flipped to settled 'succeeded' by a later contradictory child read (R6#5 is still present).

   Failure scenario: report(effect=absent, evidence=E1, cleanup=True) gives state failed. The next finished child reports reconciliation_effect=succeeded with cleanup and evidence E2. The lookup sets state=succeeded, settled=True, evidence=E2, the R6 fence passes, resources are released, and the request completes with completion_effect='succeeded' and no operator review.

8. `src/heterodyne/actions/journal.py:541`: The new child-liveness fence duplicates the reconciliation-children scan in _add_reconciliation (same LIKE query and decode loop) instead of sharing one helper.

   Failure scenario: Two copies of the child enumeration (journal.py:410-413 and :541-547) must stay in sync. A future change to the child key format or settled semantics updated in one place silently weakens either the admission check or the release fence.

9. `src/heterodyne/actions/journal.py:603`: Dead checks remain in _recovery_clock: the inner 'not isinstance(recovery, DispatchRecovery)' and the final 'else: raise clock-recovery-record-mismatch' for Decision are now unreachable after the new up-front mismatch check.

   Failure scenario: Redundant branches make the validation order harder to audit and suggest record kinds can reach those points when they cannot. Removing them keeps a single mismatch guard.

10. `src/heterodyne/actions/transitions.py:157`: Request transitions are still routed by proposal shape (recovery_operator_event presence, value.phase) instead of an explicit operation tag that requires exactly one matching observation type (R6#8 is still present).

    Failure scenario: Findings 1, 3 and 4 above all come from observation fields being selected implicitly. Each new request recovery path must re-audit which recovery field is consulted, which is how decoy and unvalidated-recovery routes keep reappearing.

The fix for finding 1 is to have the journal take every clock it later uses as an anchor from its own sampler: issuance, prepare/started and each lookup. Otherwise discontinuity recovery should be refused unless the anchor was sampled by the journal.
