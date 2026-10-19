**Verdict: REVISE, but the fixes are small.** Nothing here can cause a real effect, since all adapters stay unavailable. Two findings still break the offline PoC's own D4 contract:
- **#1:** an intent the user already denied gets escalated again instead of staying denied.
- **#2:** the groom and enrichment paths become unreachable as soon as a real `boot_id` is used.

Findings 3–4 come from the a138c99 rework of the permission path, which dropped checks that `_bind` used to do. Findings 5–7 are lower-severity correctness and cleanup items.

This was a static read only: I ran no tests or probes, did no verify pass, and made no edits. No CLAUDE.md rule applies to this Python diff.

7 findings, most severe first:

1. `src/heterodyne/actions/judgement.py:405`: The permission branch of current_projection never checks action.prior_denials or a terminal 'denied' phase. A same-session repeat of an intent that was already denied returns EscalateProposal('input_profile') rather than DenyProposal, which contradicts D4 §5 ('Repeat denied requests with the same canonical intent in the same session remain denied') and the doc's 'Terminal/denied requests refuse'.

   Failure scenario: action = replace(action, prior_denials=(denial_key(action.envelope, action.config),)) or phase='denied', with valid PolicyFacts(rev, False) and no hard-deny overrides. current_projection(receipt, action, permission_request, ...) returns EscalateProposal('input_profile'), so an operator escalation card is produced again for an intent the user already denied. The only test that covers prior_denials (test_existing_same_intent_denial_cannot_be_judged_into_permission) goes through assess, not current_projection.

2. `src/heterodyne/actions/judgement.py:678`: _groom_projection (used by record_groom, groom_request and concern) and repin_enrichment (line 625, via current_projection) never forward boot_id, so _sufficiency_projection always compares the receipt against the default 'fake-provider-unattested-boot' label.

   Failure scenario: assess(..., boot_id='boot-abc') returns a lint-passed Groom or Enrich reply. Then: (a) record_groom gets HoldUnavailable('invalid_host'), not NeedsGrooming, and silently returns the unchanged state; (b) groom_request and concern raise groom-advice-required / concern-refused; (c) repin_enrichment raises enrichment-proposal-required. Model-based negative advice and enrichment can never be recorded with any non-default boot id. Only the lint-failed path works, because it returns before the attempt check.

3. `src/heterodyne/actions/judgement.py:384`: The rewritten permission branch no longer binds PolicyFacts.revision to anything. Before a138c99, _bind required policy.revision == selected.policy_revision.sha256 and matched action.policy_revision. Now any syntactically valid revision is accepted, yet the doc still claims permission 'require[s] explicit host PolicyFacts bound to the current policy fingerprint'.

   Failure scenario: The current host policy (rev B) hard-denies push_branch, but the caller passes stale PolicyFacts(rev A, False) with no overrides. evaluate_policy returns 'escalate' and current_projection returns EscalateProposal('input_profile') instead of DenyProposal. Nothing flags the revision mismatch, so a stale policy decides between the deny and escalate tiers.

4. `src/heterodyne/actions/judgement.py:380`: The permission branch never looks at the permission request beyond its 'kind' tag. The removed _bind checks (subject.task_id == origin.bead, subject.session_ref == origin.session_key, workstream_id, revision) are not re-established, so the action's policy result is applied to an unrelated permission request.

   Failure scenario: Pass {'kind': 'permission', subject for bead X / another session, ...} with an action for bead Y whose host override is hard_deny. current_projection returns DenyProposal for X's request, or EscalateProposal('input_profile') rather than a binding refusal. A downstream journal would record a denial or escalation against the wrong subject. Fully malformed permission dicts also get 'input_profile' instead of a stale/invalid reason.

5. `src/heterodyne/actions/judgement.py:687`: _concern_reason maps every gap that is not /context and not /candidate/ask/target* to 'unsupported-effect'. This includes Groom 'what' (/candidate/title), 'alternatives' (/candidate/description), the oversize gap and the Used-enrichment invariant gap.

   Failure scenario: A revision-2 Groom result with gaps [what] (missing title) or an oversize enrichment produces Concern(reason='unsupported-effect'). The fixed public label tells the operator that the effect is unsupported when the actual defect is missing or oversize request content, and their recommended-deny decision rests on the wrong reason code.

6. `src/heterodyne/actions/judgement.py:557`: Redundant checks: record_groom re-validates type(action.groom_revisions) and its 0..2 range after _valid_groom_state(state, action) has already enforced both. The 'type(action) is not Request' guard is also repeated before _valid_groom_state in record_groom, groom_request and concern, although _valid_groom_state performs the same check.

   Failure scenario: The same invariant is spread across 4 sites. A future change to the allowed revision range has to be made in each place or the checks drift apart (for example, one path allows a third revision and another refuses it). This is dead code that hides which check is authoritative.

7. `src/heterodyne/actions/judgement.py:471`: Every use of a sufficiency receipt prepares the same request repeatedly: _prepared (full prepare), then _bind (data/convert round-trips of action and model, then digest), then disposition, which calls prepare(request, privacy) again internally. groom_request and concern run this whole chain on each call.

   Failure scenario: For requests of up to 128 KiB, each current_projection, record_groom or groom_request call runs two full privacy/schema prepare passes plus canonicalisation. Passing the already-prepared Ready value into a disposition variant that skips re-preparation would halve this work.
