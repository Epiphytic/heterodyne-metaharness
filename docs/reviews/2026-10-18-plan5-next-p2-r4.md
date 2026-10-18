**Verdict: APPROVE as an offline PoC.** I found no critical contract bug in dd38be7a..00ff9fc2. The permission path never reaches Allow, a provider, or an adapter, so no finding below can cause a real effect. Repairs 1–6 do what the R3 dispositions say. Repair 7 (the repeated prepare work) stays deferred and unwaived, which is acceptable for a PoC.

Findings 1 and 2 are the ones to fix before this goes past PoC. Both are about outputs being right, not about authority:
- **Concern labels (#1):** the `ambiguous-target` label can never be produced, and several gap kinds still fall back to `unsupported-effect`. This is the same class of bug as R3 #5.
- **Repeated denial (#2):** a repeated denied intent becomes a deny only after an approval id exists, not before.

Findings 3 and 4 are activation limitations: they matter once a real journal or Allow route exists. Findings 5–9 are low-severity or cleanup.

This was a static read only: no tests, probes, edits or subagents. No CLAUDE.md rule clearly applies to this Python diff.

10 findings, most severe first:

1. `src/heterodyne/actions/judgement.py:721`: _concern_reason can never return 'ambiguous-target', and the 'why'/'risks'/'excludes'/'refs' gaps still fall back to 'unsupported-effect'. SemanticGap.field is a closed Literal (what, effect, excludes, why, context, risks, alternatives, refs), so project._sufficiency only produces /context, /candidate/title, /candidate/description or /candidate/ask/<one of those>, and nothing ever starts with /candidate/ask/target.

   Failure scenario: A revision-2 Groom reply with gaps [refs] (unresolved reference/target) or [why] gives Concern(reason='unsupported-effect'). The operator sees 'unsupported effect' for missing rationale or ambiguous refs, which is the same mislabel R3 #5 says was fixed. The D4 'ambiguous-target' code is dead, and its only test (test_actions_p2_r2.py:118) is a negative assertion.

2. `src/heterodyne/actions/judgement.py:388`: The permission branch returns EscalateProposal('invalid_host') when approval_id is None, before it checks prior denials. A repeated same-session denied intent before allocation (allocation-intent/allocation-unknown, approval_id None) therefore escalates instead of staying denied, even though policy.request_denial_key exists to compute this key without an approval id.

   Failure scenario: action.phase='allocation-unknown', approval_id=None, prior_denials=(request_denial_key(intent, origin, resolved, config),). current_projection(..., {'kind':'permission',...}) returns EscalateProposal('invalid_host'), not DenyProposal. This contradicts the doc ('retained same-session canonical-intent denial remains an inert DenyProposal, even without a judgement or usable host policy') and D4 §5.

3. `src/heterodyne/actions/judgement.py:408`: The deterministic hard-deny floor uses the caller's PolicyFacts/host_overrides before policy.revision is bound to the snapshot or to action.policy_revision. A stale or unrelated policy can therefore produce a DenyProposal that downstream callers may treat as a sticky same-session denial.

   Failure scenario: The current policy (rev B) escalates push_branch, but the caller passes stale PolicyFacts(rev A, widens_permissions=True) or a stale hard_deny override. The result is DenyProposal, while the same call with a non-denying stale policy returns stale_digest. Once a future journal appends this to prior_denials, the intent stays denied for the whole session on the basis of a stale policy (activation limitation).

4. `src/heterodyne/actions/judgement.py:415`: The hard deny is returned for any input tagged 'kind':'permission' before subject, session or workstream binding, so a deny for action Y is emitted in reply to a permission DTO for an unrelated task or session. The R3 #4 disposition keeps this ordering; it is safe only while denials stay inert.

   Failure scenario: {'kind':'permission', subject.task_id='X', session_ref='other'} with an action for bead Y and a hard_deny override returns DenyProposal. A future native adapter that records it against the permission subject would deny X's request in another session (activation limitation; needs an explicit adapter-side guard).

5. `src/heterodyne/actions/judgement.py:382`: Only phase 'denied' gets an early answer. Other terminal phases (completed, cancelled, reconciled-abandoned) and blocker-pending still run evaluate_policy and return DenyProposal on a hard floor, which contradicts the doc statement 'Terminal requests refuse'.

   Failure scenario: action.phase='completed' with a hard_deny host override returns DenyProposal instead of a refusal or stale escalation. A downstream consumer would get a fresh denial for an already-settled request.

6. `src/heterodyne/actions/judgement.py:420`: The permission branch reimplements part of _bind's binding (subject/session/workstream/revision/policy) but leaves out _bind's other checks: action.key vs origin, validate_request_inputs, cached envelope equality, and the deny-before-bind ordering on a separate path. The two binding authorities can drift.

   Failure scenario: A Request with key.bead != origin.bead, or a cached envelope that differs from the reconstructed one, passes the permission branch to EscalateProposal('input_profile'), while the same action is refused by _bind on the sufficiency path. Future edits to _bind will not reach permission. A shared _bind_identity helper called by both paths would avoid this.

7. `src/heterodyne/actions/judgement.py:432`: The permission branch returns EscalateProposal('input_profile') directly and skips gatekeeper _precheck, so an expired permission budget is never reported as 'deadline'.

   Failure scenario: A PermissionRequest with elapsed_ms >= effective_budget_ms and an otherwise valid binding returns EscalateProposal('input_profile'). disposition() would return EscalateProposal('deadline'), so the operator sees the wrong escalation reason.

8. `src/heterodyne/actions/judgement.py:579`: With the type(action) guard removed, record_groom/groom_request/concern now rely on _valid_groom_state(state, action), which skips all action validation when action is None. A None action passes the first gate and fails later inside _bind with a different refusal code.

   Failure scenario: record_groom(valid_state, receipt, None, ...) raises Refused('judgement-binding-refused') instead of 'groom-origin-refused'. It still refuses, but the stable diagnostic contract changes, and the safety now depends on _bind ordering rather than on the stated authority.

9. `src/heterodyne/actions/judgement.py:571`: The unattested default boot label string 'fake-provider-unattested-boot' is now duplicated in 7 signatures (assess, current_projection, _sufficiency_projection, record_groom, groom_request, repin_enrichment, concern) instead of one module constant.

   Failure scenario: Renaming or attesting the fixture label requires 7 synchronized edits. One missed site silently restores the R3 #2 boot mismatch, where records under one label refuse under another.

10. `src/heterodyne/actions/judgement.py:390`: The permission branch decodes the same envelope at least four times: assemble_envelope (encode/decode), denial_key (action_data decode plus validate_request_inputs), evaluate_policy (action_data decode again), then _prepared. This adds to the unwaived R3 #7 cost.

    Failure scenario: Each permission projection does repeated JSON encode/decode/validate passes of the same envelope. Computing action_data once and passing the checked envelope to denial_key and evaluate_policy would remove the redundant passes.
