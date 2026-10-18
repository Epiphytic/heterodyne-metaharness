**Verdict: APPROVE for PoC scope.** I found no critical safety defect in the four areas you named. Every finding below fails closed, meaning it refuses, wedges or mislabels rather than replaying an effect or granting authority. Finding 1 is the only one I would fix before scanner or cutover work, because it leaves a dispatch stuck with no recovery path. These come from one read-only pass with no verification step, as the recipe specifies.

- **Canonical dispatch reconstruction:** `add_dispatch` rebuilds the expected record from the sampled clock through `prepare` or `prepare_reconciliation`, so caller-supplied clock and deadline values no longer reach storage.
- **Typed denial:** the `denial_domain` gate holds on every request read path.
- **Finite Unicode:** the grammar refuses tag characters outside the allowed flags, bidi and other format characters, lone CR or LF, variation selectors not after an emoji, and filler or blank characters.
- **Own-parent-success versus absence:** blocking absence is correct when this dispatch's own worker reported success. It is wrong when the claim was never used (finding 1).

10 findings, most severe first:

1. `src/heterodyne/actions/dispatch.py:441`: A success seen by a reconciliation lookup is treated as this dispatch's own effect even when its claim was never used. Absence is then refused and attested success also requires a used claim, so an unclaimed dispatch can never settle. This contradicts the new reconcile_lookup comment ('without attributing another actor's effect to this claim').

   Failure scenario: Dispatch goes ready -> uncertain with claim_consumed=False. A reconciliation child finishes with effect='succeeded' (another actor's write), and the lookup records it without changing state. On settle, effect_definitively_absent is refused here and again in journal._recovery_clock (line ~752), and effect_attested_succeeded is refused because claim_consumed is False. No settle succeeds, so the resource, effect:<target> and approval: rows are never released and every later dispatch on that target gets 'resource-still-held'.

2. `src/heterodyne/actions/journal.py:710`: _worker_succeeded treats any recorded dispatch-observation containing dispatch_readback.effect=='succeeded' as a worker success. dispatch_transition only checks dispatch_readback for the 'report' operation, and the observation is stored for every operation.

   Failure scenario: A caller sends operation='uncertain' (or 'ready' or 'lookup') with a stray DispatchReadback(effect='succeeded', mismatched attempt or claim). The transition succeeds and the observation is saved. From then on every settle with effect_definitively_absent fails with 'reconciliation-success-conflicts-with-absence', which wedges the dispatch on an unverified readback.

3. `src/heterodyne/actions/judgement.py:828`: In the permission projection, the expired-deadline check and the stale-policy escalation now run before the hard_deny floor. A hard-denied action can therefore come back as EscalateProposal instead of DenyProposal.

   Failure scenario: Permission request whose deadline has passed, current policy with tier hard_deny: disposition(...) returns EscalateProposal('deadline'), so the function returns before evaluate_policy runs and a human is asked about an action policy hard-denies. Before this change the floor was evaluated first ('Fresh deterministic hard denial/floor precedes every receipt failure').

4. `src/heterodyne/actions/judgement.py:757`: _concern_reason now matches the gap field '/candidate/ask/refs' exactly and no longer matches the '/candidate/ask/target' prefix. The validator emits '/candidate/ask/refs/{i}', '/candidate/ask/targets' and '/candidate/ask/targets/{i}', so those gaps no longer map to ambiguous-target.

   Failure scenario: A groom result with Gap(field='/candidate/ask/targets/0') or '/candidate/ask/refs/2' (gatekeeper/validate.py:314-323) produces concern 'unsupported-effect' instead of 'ambiguous-target', so the operator sees the wrong fixed label.

5. `src/heterodyne/actions/journal.py:274`: get('request') and _requests_for_approvals now refuse any request without denial_domain, while SCHEMA_VERSION stays 2. Existing journals with in-flight older requests become impossible to transition, cancel or settle, and every dispatch or decision transition that looks up its request fails.

   Failure scenario: A v2 journal created before this change has an outstanding request, or a dispatch holding resources. Every transition, including cleanup, settle and cancellation, raises 'legacy-denial-domain-needs-provenance'. Those rows count toward MAX_OUTSTANDING and keep their resources until manual recovery, and no recovery path exists.

6. `src/heterodyne/actions/journal.py:888`: _denial_provenance runs on every denied() call and fails the whole session if any one denial row cannot be re-verified. A single inconsistent row blocks every request in that session, not just the related one.

   Failure scenario: Session S has one denial row whose request or decision no longer matches exactly (for example an older-domain request, or a request_revision mismatch). Every add_request and filed or revised transition for any intent in S raises Refused, so the session is frozen instead of only the bad row being refused.

7. `src/heterodyne/actions/decisions.py:65`: U+FE0F is accepted only directly after a symbol in the small emoji range, so ordinary keycap emoji and text-default emoji outside that range are refused as unsafe denial reasons.

   Failure scenario: denial_reason '1\ufe0f\u20e3 too risky' or '\u00a9\ufe0f' raises Refused('decision-reason-unsafe'), so a legitimate visible deny reason is rejected (fails closed).

8. `src/heterodyne/actions/journal.py:335`: Efficiency: each _requests_for_approvals call (used by _request_for_approval and _denial_provenance) decodes every row in the requests table. denied() now runs this full scan, plus one decision lookup per denial row, on every add_request and filed transition.

   Failure scenario: With MAX_RECORDS requests and many denials in a session, each filed transition decodes the whole table at least twice inside the write transaction. An indexed approval column, or one decoded scan reused per transaction, would avoid this.

9. `src/heterodyne/actions/journal.py:801`: Simplification: now that every transition writes a <kind>-clock event, _dispatch_lookup_clock and _request_proposal still read 'now' from the observation event and then cross-check it against the clock event.

   Failure scenario: Two sources of truth for the same timestamp, kept alive by the extra 'allocation_clock' write rule on line ~667. Reading the '-clock' event directly, as _recovery_clock now does for requests, would remove the special case.

10. `src/heterodyne/actions/decisions.py:52`: Efficiency: the allowed flag-tag tuple is rebuilt every time 🏴 appears in the input, instead of once as a module constant.

    Failure scenario: A 1024-character reason containing many 🏴 characters rebuilds the four tag strings each time. A module-level frozenset or tuple would avoid the repeated work.
