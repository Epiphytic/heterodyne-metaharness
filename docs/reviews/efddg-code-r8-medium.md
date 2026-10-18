**Verdict: REVISE.** This isn't good enough to implement as a PoC yet. Most of the R7 repairs look correct, but the R7#7 fix creates a new way for a parent dispatch to get stuck permanently. When that happens, its resource, effect and approval locks are never released. That is the same class of liveness flaw R7#2 was treated as critical for.

I reviewed the full range `35176c85..39459ca7` read-only and statically. I ran no tests or probes, and as a high-effort recall pass these findings are deduplicated but not verified.

- **Repairs that look correct:** the exclusive-recovery guard now covers all four recovery types (R7#4), and unused terminal recovery is now refused (R7#3). The reserve fix holds: an initial dispatch without an owner is refused by `_event` under reserve, and issuance no longer counts as progress (R7#6). The shared `_reconciliation_children` helper removes the duplicated scan (R7#8).
- **The critical flaw (finding 1):** a child read that the parent refuses to apply blocks every exit. No further child can be added, the lookup count cannot advance, and the operator settlement path needs three children, which can never exist. The new R7 test only calls the pure reducer and never checks that an exit still exists.
- **Clock provenance (findings 2, 3, 6):** the guarantee is only as strong as `db.clock`, a public attribute that the tests themselves reassign. Callers must also predict the sampler's exact output, and recovery transitions sample the clock twice.
- **No CLAUDE.md findings:** the only code rule (cargo clippy) doesn't apply to this Python code.

10 findings, most severe first:

1. `src/heterodyne/actions/dispatch.py:300`: The new R7 refusal of a later success read on a failed parent says 'needs-authenticated-recovery', but no recovery path exists: the unapplied child read blocks all further children (journal.py:448 `if unread`), the lookup count cannot advance, and settle_dispatch needs lookups==3, a proven clock discontinuity, or three journal-proven children, which can no longer exist.

   Failure scenario: Parent report(effect=absent, evidence, cleanup) leaves it failed and unsettled with lookups=0. Child #1 is added and finish_reconciliation records effect=succeeded. The parent 'lookup' raises reconciliation-outcome-conflict. _add_reconciliation counts child #1 as unread and refuses child #2 with reconcile-parent-observation-required. Operator settle_dispatch: claim_consumed, lookups=0!=3, no discontinuity, and the exhaustion check needs len(children)==3, so it raises dispatch-recovery-not-bound. The parent is wedged forever and its resource/effect/approval locks are never deleted. The same wedge happens on an uncertain parent whose claim was never consumed when a child reads 'succeeded' (dispatch.py:289).

2. `src/heterodyne/actions/journal.py:589`: Clock provenance only proves that observation.now equals whatever `self.clock` returned, and `clock` is a mutable public attribute supplied by the caller (the R4/R5/R7 tests reassign `db.clock = lambda: now` mid-journal). The README claim 'A caller-selected boot ID alone cannot bypass reads' still does not hold against the caller.

   Failure scenario: The caller sets db.clock = lambda: Clock('fake',1,1) and issues allocation with observation.now=Clock('fake',1,1). A request-clock event is retained as 'proven'. The caller then sets db.clock = lambda: Clock('B',1,1) and transitions to reconciled-abandoned with AbandonmentRecovery(clock_discontinuity=True, observed_now=Clock('B',1,1)). _proven_clock passes, clock_elapsed raises on the boot mismatch, and allocation_lookups==3 is skipped with zero reads, which is the exact R7#1 bypass, now with one extra assignment.

3. `src/heterodyne/actions/journal.py:502`: A transition that carries observation.now and a discontinuity recovery samples the clock twice: once in _sample_clock and again in _recovery_clock (line 651). Both samples must equal observation.now and recovery.observed_now, so this only works with a sampler that returns the same value twice.

   Failure scenario: With a sampler that returns successive monotonic values, as the disposition says the evolving tests model, a settle or abandon transition that also supplies observation.now gets sample1==observation.now and sample2!=observation.now. It raises recovery-clock-sample-mismatch, so the genuine reboot recovery is refused unless the caller knows to omit `now`.

4. `src/heterodyne/actions/journal.py:631`: The exhaustion settlement check confirms only that three settled, cleaned children exist. It does not require every child read with a reconciliation_effect to be applied to the parent (the `unread` invariant the README states), so an operator disposition can be accepted while a machine read is still unapplied.

   Failure scenario: Children 1 and 2 fail preparation and are operator-settled. Child 3 finishes with effect=succeeded or unknown, but the parent lookup is not yet due, or is refused. The operator sends DispatchRecovery(completed_reconciliation_attempts=[c1,c2,c3], effect_definitively_absent=True). The journal accepts it, the parent settles 'failed', and the request completes with completion_effect='absent' while child 3's success evidence was never bound to or shown in the settlement.

5. `src/heterodyne/actions/dispatch.py:349`: settle_dispatch treats any three distinct strings in completed_reconciliation_attempts as exhaustion and does not check children, state or read history. The pure reducer has a second, weaker validator for the same fact, which is the R7#5 two-validator pattern again.

   Failure scenario: A reducer-only caller holding a claim-consumed parent with lookups=0 passes completed_reconciliation_attempts=('a','b','c') plus the exact operator fields. The reducer settles the parent with no reconciliation child ever having existed. Only the journal (journal.py:631) catches this.

6. `src/heterodyne/actions/journal.py:594`: The fix is at the wrong depth. The journal requires the caller to supply an observation.now exactly equal to a sample taken inside the transaction ('journal-clock-sample-mismatch'), instead of injecting its own sampled clock into the observation, as the R7 reviewer recommended.

   Failure scenario: With any real or advancing sampler, the caller cannot know the in-transaction sample beforehand. Every issuance, ready, consume-claim, restart and lookup transition is then refused. The tests only pass because they script exact sample sequences (e.g. `[Clock('boot',1,1)] * 4`), so the provenance mechanism cannot carry over to a real clock port.

7. `src/heterodyne/actions/transitions.py:371`: The 'preparation-failed' operation hard-codes launcher_exited=True and no_children=True without any readback observation. In R7 a failed preparation now uses up one of the three bounded child slots and feeds the exhaustion settlement path, so an unproved assertion consumes those slots.

   Failure scenario: A caller proposes preparation-failed on a live reconciliation child whose launcher is still running. The child goes to held with cleanup_proven=True with no exit evidence. Repeating this three times produces the 'three failed attempts' needed for operator exhaustion settlement while the read-only processes may still be running.

8. `src/heterodyne/actions/journal.py:448`: Launching a child is no longer tied to the parent's lookup count. The 1/5/30-second backoff only gates applying a read to the parent, so three read-only invocations, including failed preparations, can be launched back-to-back, unlike D4's 'at most three attempts at 1/5/30 seconds'.

   Failure scenario: Parent uncertain at t=0. Children 1, 2 and 3 each fail preparation within milliseconds, are operator-settled, and exhaustion is reached at about t=0. The bounded read schedule never actually spaces the target reads or attempts.

9. `src/heterodyne/actions/journal.py:651`: _recovery_clock re-implements sampling (`self.clock()` plus `clock_elapsed(now, now)`) instead of calling the new `_sample_clock` helper. That leaves two sampling sites with different mismatch codes and is the direct cause of the double sample.

   Failure scenario: Validation and error semantics drift between 'journal-clock-sample-mismatch' and 'recovery-clock-sample-mismatch'. One shared sample per transaction, reused for provenance and recovery, would remove both the duplication and finding 3.

10. `src/heterodyne/actions/journal.py:568`: Each clock-bearing transition now writes an additional '<kind>-clock' event, which raises the per-transition event cost during recovery. The fixed 96-event TERMINAL_RESERVE was not re-derived and the README still states the old coverage.

    Failure scenario: A recovery transition now costs up to 4 events instead of 3 (record, clock, observation, capacity marker), so the three-child reconciliation plus authenticated settlement sequence spends more reserve than before. The README's 'covered by actual-capacity regressions' claim depends on fixtures that may not exercise the worst case of failed child attempts plus operator child settlements.
