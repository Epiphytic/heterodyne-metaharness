**Verdict: REVISE.** This was a static read of `dd38be7a..67a80144` only. I ran no tests, probes or verify pass, so these are unverified candidates.

Most of the ten R1 repairs hold up in the code:
- **Hold up:**
  - No permission `AllowProposal` is emitted and the provider is never called for permission.
  - The timeout and clock reason codes are now honest.
  - The recovery fields are now in the digest.
  - Concern is now scoped to the creation key and approval.
  - The enrichment repin is distinct and charges no groom revision.
  - Raw permission dicts escalate.
- **Still broken, three correctness problems in the pure PoC:**
  - R1 #1 is only fixed for `widens_permissions`. A hard deny that comes from host overrides or restrictions still loses to bad input.
  - The lint-failed grooming path records a receipt digest it never checked.
  - The R1 #2 reconciliation only works if revision 0 was assessed.

Items 4 and 7 are partly production-design gaps (caller-forgeable state, no freshness check at use) rather than PoC bugs. No CLAUDE.md rule applies to this Python diff, so there are no conventions findings.

10 findings, most severe first:

1. `src/heterodyne/actions/judgement.py:376`: Hard-deny precedence covers only policy.widens_permissions: a hard deny from host_overrides or restrict is evaluated only after _prepared(request) succeeds and action.envelope exists, so malformed or stale permission input turns that deterministic hard deny into an escalation.

   Failure scenario: PolicyFacts(rev, False) with host_overrides={'push_branch': 'hard_deny'} (or restrict={'hard_deny': ['push_branch']}), plus a permission request dict that fails prepare(), or an action whose envelope is None. Line 379 raises Refused, or line 386 returns early, giving EscalateProposal('stale_digest'/'invalid_host') instead of DenyProposal. An escalation card can then reach a human for an action the current P0 policy hard-denies, which breaks D4's 'no route overrides hard deny'. The R1 test only covers overrides with a valid request. Fix: evaluate evaluate_policy(action.envelope, ...) before any request preparation whenever permission is True.

2. `src/heterodyne/actions/judgement.py:513`: For lint-failed sufficiency requests, current_projection returns NeedsGrooming at line 396-397 without checking the receipt, the binding or that the request belongs to the action. record_groom then stores receipt.input_digest unverified.

   Failure scenario: The request has lint=Failed and the caller passes a receipt from another request or revision (or one with an arbitrary digest-syntax input_digest). record_groom appends that foreign digest at revision N. groom_request and concern then always refuse because assessed[-1] != bind(action, ...). Re-recording with the right receipt raises groom-budget-refused because groom_revisions <= revisions[-1]. The request is stuck with no fixed exit. A non-Receipt object (for example None) raises an AttributeError here, not Refused. Fix: record bind(action, request, privacy, model) rather than receipt.input_digest, and type-check the receipt.

3. `src/heterodyne/actions/judgement.py:518`: Reconciling a consumed P1 revision only works if revision 0 was assessed. record_groom refuses any first record with groom_revisions != 0, and _valid_groom_state requires revisions[0] == 0.

   Failure scenario: The revision-0 judgement is an outage, or is Sufficient and the request is later groomed from 'sufficient'. A legacy P1.groom moves the request to groom_revisions=1 and then 2. Every NeedsGrooming at revisions 1 and 2 raises groom-budget-refused, so the state never reaches failures=2 and concern() and CannotApproveNotice can never be reached. That contradicts the doc's claim that the revision-two negative assessment reaches the fixed concern exit. The R1 test only covers an outage at revision 1.

4. `src/heterodyne/actions/judgement.py:611`: concern() takes the public reason code from the caller instead of deriving it from the recorded NeedsGrooming gaps. Unlike groom_request, it also needs no current receipt or projection, so a hand-built GroomState is enough.

   Failure scenario: The revision-2 gaps are native_lint, but the caller passes reason='ambiguous-target', and the fixed public label shown to the operator is wrong. Separately, GroomState(origin, 2, (d0, d1, bind(action)), key, approval, (0, 1, 2)) built without any receipts passes concern() and yields a deny-recommended Concern. Production gap: the state is caller-forgeable. PoC gap: this check is inconsistent with groom_request.

5. `src/heterodyne/actions/judgement.py:374`: permission = _permission_input(request) or policy is not None: any sufficiency request given a PolicyFacts value gets permission-style outcomes (DenyProposal or EscalateProposal) instead of a sufficiency hold.

   Failure scenario: A host passes a SufficiencyRequest with PolicyFacts(rev, True) and gets DenyProposal, which is not a valid sufficiency outcome. With PolicyFacts(rev, False), _bind raises judgement-policy-refused and the result is EscalateProposal('stale_digest') rather than HoldUnavailable. A consumer branching on the projection sees deny or escalate for a sufficiency judgement.

6. `src/heterodyne/actions/judgement.py:485`: _valid_groom_state treats action=None as 'no action to bind' and returns True, and it dereferences action fields without type guards. Malformed inputs to record_groom, groom_request and concern therefore escape as AttributeError or TypeError instead of the fixed Refused diagnostic.

   Failure scenario: record_groom(state, receipt, None, lint_failed_request, ...) passes the state check because action is None, and current_projection returns NeedsGrooming without touching the action. Line 516 then raises an AttributeError on None.groom_revisions. A dict action raises an AttributeError in same_session or on action.key. A non-int groom_revisions raises a TypeError at line 490.

7. `src/heterodyne/actions/judgement.py:423`: The receipt boot and deadline are not rechecked when the receipt is used. assess and current_projection default to the same constant boot_id, so the boot check passes trivially by default, and nothing compares the receipt age with the current clock.

   Failure scenario: A receipt produced before a reboot, or hours earlier, is replayed with the default boot_id and still returns SufficientProposal, as long as the binding is unchanged. The doc says the future owner must recheck boot/deadline on use. Production-design gap, acceptable for an offline PoC only if it is labelled as such.

8. `src/heterodyne/actions/judgement.py:617`: Validation work is still repeated after the R1 #10 repair: concern() calls bind() twice with identical arguments, groom_request computes bind() and then current_projection prepares and binds again, and the permission branch runs a full prepare() only to check the request type before deterministic policy.

   Failure scenario: Each concern or groom call prepares and canonicalises requests of up to 128 KiB two or three times. The simpler form is to compute the binding once, reuse it for the comparison and the digest_prefix, and evaluate policy before prepare (which also fixes finding 1).

9. `src/heterodyne/actions/judgement.py:546`: groom_request and repin_enrichment import groom and repin_enriched_ask inside the function, although lines 20-27 already import from heterodyne.actions.requests and there is no import cycle. requests.repin_enriched_ask also imports hashlib locally.

   Failure scenario: The hidden local imports are inconsistent with the module's top-level imports and hide these dependencies from static tooling. Move them to the existing top-level import block.

10. `docs/plan5-p2-pure-judgement.md:7`: The doc says the deadline is at most 60 seconds 'or the smaller remaining permission budget', but permission assessment never invokes a provider and assess always uses budget = DEADLINE_NS.

    Failure scenario: A reader or a future owner assumes the remaining permission budget (effective_budget_ms - elapsed_ms) is enforced in the receipt path, but no such code exists. Reword the doc to describe only the sufficiency 60-second budget.
