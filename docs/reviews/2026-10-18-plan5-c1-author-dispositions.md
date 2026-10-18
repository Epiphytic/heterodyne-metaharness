# Plan5 C1 author dispositions

Author: Codex. This is implementation evidence for approved next-stage design `092c10221660605598bb41151b496bda0f0bfa38`, §§3–4. It is not a new design review or a current code-review verdict.

The two historical reports below are copied byte for byte from the admitted source inventory. P1 actual R11 and P2 actual R4 remain separate ordinals and sources. Their historical offline APPROVE verdicts do not approve C1. Current C1 requires its own actual code review.

## P1 finding 1

Verbatim retained report excerpt:

```text
1. `src/heterodyne/actions/journal.py:144`: FixtureJournal's clock defaults to None, and then every 'journal-sampled' time silently falls back to caller-supplied Clock values: add_dispatch keeps the caller's started/deadlines, and the lookup/ready/consume-claim/allocation transitions use observation.now as given.

   Failure scenario: FixtureJournal(path) with no clock; the caller passes TransitionObservation(now=Clock(same boot, mono+31s, utc+31s)) right after issuance. reconcile_lookup/observe_allocation backoff (1,5,30) and the 60s/30s/5s deadlines are satisfied by a forged clock. This contradicts the 'never caller time' invariant that _request_proposal and the dispatch docs state. Only the discontinuity path refuses (journal-clock-sampler-required).
```

Author disposition: FIXED. Issuance/add_dispatch and each timed transition require the journal injected sampler. Allocation, dispatch preparation and lookup anchors are retained as clock events; subsequent/recovery paths require that exact provenance. Production FixtureJournal with clock=None refuses, even if the proposal supplies now. Retained synthetic tests explicitly enroll their fixture clock; the new C1 adversarial tests use the production class directly.

## P1 finding 2

Verbatim retained report excerpt:

```text
2. `src/heterodyne/actions/dispatch.py:387`: The conflict/exhaustion checks in settle_dispatch never compare the operator's attestation with retained child evidence, so a consumed, uncertain parent can be settled as 'absent' even when a retained reconciliation child reported effect='succeeded'.

   Failure scenario: Parent consumed, then uncertain. A reader finishes with effect='succeeded' but effect_cleanup_proven=False, and the lookup leaves the parent uncertain. After 3 lookups (or a discontinuity), the operator submits DispatchRecovery(effect_definitively_absent=True), which is accepted. The request then completes with completion_effect='absent' even though the journal holds machine evidence that the effect happened.
```

Author disposition: FIXED. The journal computes every retained reconciliation child disposition before settlement, including facts omitted by the proposal. Any retained succeeded effect contradicts an absent attestation and refuses atomically. The pure reducer also rejects contradictory supplied facts. Existing child evidence, resources and parent remain intact.

## P1 finding 3

Verbatim retained report excerpt:

```text
3. `src/heterodyne/actions/dispatch.py:289`: reconcile_lookup refuses a 'succeeded' reading for an unconsumed parent (state reached via uncertain() from 'ready') instead of recording it. The child stays unread forever, and the only way out is an operator attesting 'absent', because effect_attested_succeeded requires claim_consumed.

   Failure scenario: Parent ready, then uncertain (claim not consumed). A reader finishes with effect='succeeded'. The lookup raises reconciliation-needs-authenticated-recovery, and _add_reconciliation then refuses every later reader (reconcile-parent-observation-required). Settlement via 'conflict' must attest absent, so a real observed effect can never be recorded as succeeded.
```

Author disposition: FIXED. An actual succeeded lookup against an unconsumed parent increments the lookup count and records/applies its child audit without manufacturing claim consumption or settling succeeded. The parent remains uncertain. A later absent attestation cannot contradict the retained success. No automatic whole-request withdrawal is introduced.

## P1 finding 4

Verbatim retained report excerpt:

```text
4. `src/heterodyne/actions/dispatch.py:77`: clock_elapsed uses a fixed 1s absolute tolerance between monotonic and UTC deltas, whatever the interval length, so normal NTP slew over long gaps counts as a discontinuity.

   Failure scenario: A lookup or reconcile attempt runs hours after the last anchor. 50ppm slew over about 6h exceeds 1s, so it raises dispatch-clock-or-deadline, and every later timed transition needs an operator clock-discontinuity recovery even though no clock jump happened.
```

Author disposition: DEFERRED, UNWAIVED. The conservative UTC-vs-monotonic skew tolerance remains. Long-running samples can produce a false HOLD; no timeout or deadline relaxation is introduced in this offline stage.

## P1 finding 5

Verbatim retained report excerpt:

```text
5. `src/heterodyne/actions/journal.py:306`: _request_for_approval decodes every row in the requests table (up to 4096 records of up to 256KB each) to find one approval_id, and it is called several times per decision/dispatch transaction (add_decision, add_dispatch, _add_reconciliation, the decision branch of transition, _recovery_progress).

   Failure scenario: A near-capacity fixture journal: each dispatch transition decodes O(4096) Request blobs 2-3 times inside BEGIN IMMEDIATE, holding the write lock. An indexed approval column (or a decisions→request key) would make this a single lookup.
```

Author disposition: DEFERRED, UNWAIVED. Approval lookup still decodes retained requests. MAX_RECORDS=4096 is enforced; this remains bounded performance debt, not an indexed lookup claim. No production store or provider is enabled.

## P1 finding 6

Verbatim retained report excerpt:

```text
6. `src/heterodyne/actions/journal.py:198`: _event calls _reserved() on every insert, which runs one SUM query plus a Python decode loop per outstanding request (up to 128) over capacity-recovery events.

   Failure scenario: A transition emits 3-5 events (record, clock, observation, denial), so with 128 outstanding requests that is about 640 extra queries per transaction. The reserve could be computed once per transaction, or kept as a counter column.
```

Author disposition: DEFERRED, UNWAIVED. Reservation accounting still scans bounded active rows/events inside the transaction. Existing capacity/reserved-terminal-budget tests remain. This does not claim constant-time accounting or the future P3/P4 64-request/161-slot contract.

## P1 finding 7

Verbatim retained report excerpt:

```text
7. `src/heterodyne/actions/transitions.py:93`: TransitionObservation.allocation_elapsed_seconds is dead: nothing reads it, yet it is accepted and persisted in *-observation events.

   Failure scenario: A caller sets allocation_elapsed_seconds=999 believing it controls backoff. It is silently ignored, but it is written into the journal's observation history, which makes the audit record misleading for exactly the caller-supplied elapsed time the design forbids. Remove the field.
```

Author disposition: FIXED. allocation_elapsed_seconds is no longer an accepted TransitionObservation field. New construction rejects it; historical serialized event bytes are not rewritten. Fixture setup now supplies only actual sampled clocks; the original constructor assertion is archived.

## P1 finding 8

Verbatim retained report excerpt:

```text
8. `src/heterodyne/actions/policy.py:87`: The denial identity's content changed (presentation aliases repo/target/channel_ref/alias are now stripped), but the domain tag is still 'plan5-denial-v1'.

   Failure scenario: A denial row hashed under the P0 denial_key (aliases included) carries the same domain/version as the new hash but never matches it, so a previously denied intent is no longer recognised as same-session-denied after the change. Bumping the domain would make the break explicit.

On the last part of your brief: this review covers the code only. It is not proof that a reserved review profile ran or that a run was manually observed.
```

Author disposition: FIXED. Canonical denial identity uses explicit plan5-denial-v2 and new requests carry that domain. Missing/legacy domain holds without rewriting raw data. Retained denials must have the exact canonical request/session and committed matching decision/digest/revision/event binding before lookup/new admission. Missing association holds; this is not a production migration.

## P2 finding 1

Verbatim retained report excerpt:

```text
1. `src/heterodyne/actions/judgement.py:721`: _concern_reason can never return 'ambiguous-target', and the 'why'/'risks'/'excludes'/'refs' gaps still fall back to 'unsupported-effect'. SemanticGap.field is a closed Literal (what, effect, excludes, why, context, risks, alternatives, refs), so project._sufficiency only produces /context, /candidate/title, /candidate/description or /candidate/ask/<one of those>, and nothing ever starts with /candidate/ask/target.

   Failure scenario: A revision-2 Groom reply with gaps [refs] (unresolved reference/target) or [why] gives Concern(reason='unsupported-effect'). The operator sees 'unsupported effect' for missing rationale or ambiguous refs, which is the same mislabel R3 #5 says was fixed. The D4 'ambiguous-target' code is dead, and its only test (test_actions_p2_r2.py:118) is a negative assertion.
```

Author disposition: FIXED. Concern mapping follows actual /context, /candidate/title, /candidate/description and /candidate/ask paths. Every SemanticGap field is exercised through the real gatekeeper sufficiency projection: refs maps to ambiguous-target; rationale/context/risks/excludes map to missing-context; effect maps to unsupported-effect.

## P2 finding 2

Verbatim retained report excerpt:

```text
2. `src/heterodyne/actions/judgement.py:388`: The permission branch returns EscalateProposal('invalid_host') when approval_id is None, before it checks prior denials. A repeated same-session denied intent before allocation (allocation-intent/allocation-unknown, approval_id None) therefore escalates instead of staying denied, even though policy.request_denial_key exists to compute this key without an approval id.

   Failure scenario: action.phase='allocation-unknown', approval_id=None, prior_denials=(request_denial_key(intent, origin, resolved, config),). current_projection(..., {'kind':'permission',...}) returns EscalateProposal('invalid_host'), not DenyProposal. This contradicts the doc ('retained same-session canonical-intent denial remains an inert DenyProposal, even without a judgement or usable host policy') and D4 §5.
```

Author disposition: FIXED. Canonical same-session denied identity is checked after common strict request/DTO identity validation but before requiring approval allocation or usable host policy. It remains an inert DenyProposal; no fake approval ID or authenticated decision is introduced.

## P2 finding 3

Verbatim retained report excerpt:

```text
3. `src/heterodyne/actions/judgement.py:408`: The deterministic hard-deny floor uses the caller's PolicyFacts/host_overrides before policy.revision is bound to the snapshot or to action.policy_revision. A stale or unrelated policy can therefore produce a DenyProposal that downstream callers may treat as a sticky same-session denial.

   Failure scenario: The current policy (rev B) escalates push_branch, but the caller passes stale PolicyFacts(rev A, widens_permissions=True) or a stale hard_deny override. The result is DenyProposal, while the same call with a non-denying stale policy returns stale_digest. Once a future journal appends this to prior_denials, the intent stays denied for the whole session on the basis of a stale policy (activation limitation).
```

Author disposition: FIXED. Permission policy must be an exact PolicyFacts with revision bound to the prepared snapshot and any retained action policy revision before a hard floor is evaluated. Stale hard-deny input escalates; it cannot create a current denial proposal.

## P2 finding 4

Verbatim retained report excerpt:

```text
4. `src/heterodyne/actions/judgement.py:415`: The hard deny is returned for any input tagged 'kind':'permission' before subject, session or workstream binding, so a deny for action Y is emitted in reply to a permission DTO for an unrelated task or session. The R3 #4 disposition keeps this ordering; it is safe only while denials stay inert.

   Failure scenario: {'kind':'permission', subject.task_id='X', session_ref='other'} with an action for bead Y and a hard_deny override returns DenyProposal. A future native adapter that records it against the permission subject would deny X's request in another session (activation limitation; needs an explicit adapter-side guard).
```

Author disposition: FIXED. Subject, logical session, workstream, revision, origin/key, canonical intent and envelope binding all precede deterministic permission projection. An unrelated or malformed DTO cannot yield a fresh denial for another action.

## P2 finding 5

Verbatim retained report excerpt:

```text
5. `src/heterodyne/actions/judgement.py:382`: Only phase 'denied' gets an early answer. Other terminal phases (completed, cancelled, reconciled-abandoned) and blocker-pending still run evaluate_policy and return DenyProposal on a hard floor, which contradicts the doc statement 'Terminal requests refuse'.

   Failure scenario: action.phase='completed' with a hard_deny host override returns DenyProposal instead of a refusal or stale escalation. A downstream consumer would get a fresh denial for an already-settled request.
```

Author disposition: FIXED. completed, cancelled, reconciled-abandoned and blocker-pending requests refuse fresh projection. A correctly bound retained denial stays inert. Parameterized regressions cover all four excluded phases.

## P2 finding 6

Verbatim retained report excerpt:

```text
6. `src/heterodyne/actions/judgement.py:420`: The permission branch reimplements part of _bind's binding (subject/session/workstream/revision/policy) but leaves out _bind's other checks: action.key vs origin, validate_request_inputs, cached envelope equality, and the deny-before-bind ordering on a separate path. The two binding authorities can drift.

   Failure scenario: A Request with key.bead != origin.bead, or a cached envelope that differs from the reconstructed one, passes the permission branch to EscalateProposal('input_profile'), while the same action is refused by _bind on the sufficiency path. Future edits to _bind will not reach permission. A shared _bind_identity helper called by both paths would avoid this.
```

Author disposition: FIXED. Shared _identity is used by _bind and the permission branch. It strictly converts the typed snapshot, validates inputs/key/origin, exact canonical domain/identity and cached envelope equality. There is one common identity boundary; neither fixture booleans nor this validation authenticate an origin.

## P2 finding 7

Verbatim retained report excerpt:

```text
7. `src/heterodyne/actions/judgement.py:432`: The permission branch returns EscalateProposal('input_profile') directly and skips gatekeeper _precheck, so an expired permission budget is never reported as 'deadline'.

   Failure scenario: A PermissionRequest with elapsed_ms >= effective_budget_ms and an otherwise valid binding returns EscalateProposal('input_profile'). disposition() would return EscalateProposal('deadline'), so the operator sees the wrong escalation reason.
```

Author disposition: FIXED. The real public gatekeeper disposition precheck runs for the prepared permission DTO. An expired deadline yields deadline escalation before a semantic hard floor. A sealed expired fixture tests the actual path.

## P2 finding 8

Verbatim retained report excerpt:

```text
8. `src/heterodyne/actions/judgement.py:579`: With the type(action) guard removed, record_groom/groom_request/concern now rely on _valid_groom_state(state, action), which skips all action validation when action is None. A None action passes the first gate and fails later inside _bind with a different refusal code.

   Failure scenario: record_groom(valid_state, receipt, None, ...) raises Refused('judgement-binding-refused') instead of 'groom-origin-refused'. It still refuses, but the stable diagnostic contract changes, and the safety now depends on _bind ordering rather than on the stated authority.
```

Author disposition: FIXED. record_groom, groom_request and concern explicitly require Request before field access. Existing malformed-host tests retain their expected fixed refusal/no effects.

## P2 finding 9

Verbatim retained report excerpt:

```text
9. `src/heterodyne/actions/judgement.py:571`: The unattested default boot label string 'fake-provider-unattested-boot' is now duplicated in 7 signatures (assess, current_projection, _sufficiency_projection, record_groom, groom_request, repin_enrichment, concern) instead of one module constant.

   Failure scenario: Renaming or attesting the fixture label requires 7 synchronized edits. One missed site silently restores the R3 #2 boot mismatch, where records under one label refuse under another.
```

Author disposition: FIXED. Seven default boot arguments now use the named FAKE_BOOT_ID constant. This is explicitly an unattested offline default, never a real host boot identity or a provider receipt.

## P2 finding 10

Verbatim retained report excerpt:

```text
10. `src/heterodyne/actions/judgement.py:390`: The permission branch decodes the same envelope at least four times: assemble_envelope (encode/decode), denial_key (action_data decode plus validate_request_inputs), evaluate_policy (action_data decode again), then _prepared. This adds to the unwaived R3 #7 cost.

    Failure scenario: Each permission projection does repeated JSON encode/decode/validate passes of the same envelope. Computing action_data once and passing the checked envelope to denial_key and evaluate_policy would remove the redundant passes.
```

Author disposition: DEFERRED, UNWAIVED. Repeated strict prepare/decode remains bounded by existing payload/codec limits and has no provider/native effect. This stage does not introduce caching that could reuse stale context, nor claim measured production latency.

## Metadata compatibility

C1 validates denial metadata separately from identifier labels. CR/LF line breaks, visible joined emoji and emoji tag sequences are preserved byte for byte; bidi/control-only/invisible-only reasons refuse. Approval must carry an empty denial reason. This does not relax identifier/source/purpose validation, grant consent or normalize an authenticated decision. The new tests retain explicit unsafe ASCII-joiner and scalar-limit historical probes.

## Historical fixture changes and explicit oracle supersessions

All original `tests/test_actions*.py` bytes and their SHA256s are archived privately with the complete pre-adaptation failures and final diff. No original test function is deleted or skipped. All old-to-new assert/raises AST differences are separately inventoried for the reviewer.

| Original test | Old expected result | Approved replacement | Authority |
|---|---|---|---|
| p1_r3 `test_unissued_claim_cannot_become_uncertain_or_successful` second probe | unconsumed succeeded lookup raises | actual read increments count; state stays uncertain, unsettled and unconsumed; first unissued-transition refusal remains | §4 P1 #3; §3 clock/audit invariant |
| p1_r8 `test_conflicting_child_has_exact_attested_exit_without_three_fake_reads` | absence attestation settles failed and frees resources despite succeeded child | contradictory absence refuses; exact parent, succeeded child and three resources stay retained; original no-extra-reader assertion retained | §4 P1 #2 |
| p2_r2 `test_hard_deny_precedes_bad_permission_input` all four parameter combinations | malformed permission yields DenyProposal | stale_digest escalation before hard deny | §3 current identity contract; §4 P2 #3–6 |
| p1_r3 missing-clock probe | missing caller observation raises clock-observation-required | explicit missing journal sampler raises journal-clock-sampler-required; the no-claim-consumption assertion remains | §3.3 and §4 P1 #1 |
| p1_r6 decoy recovery probe | selected missing-clock validation raises recovery text | same refusal/unchanged state with either selected recovery or earlier missing journal sampler diagnostic | §4 P1 #1 |

Other setup changes explicitly enroll existing synthetic times through `actions_journal_fixture.FixtureJournal`. Its sampler uses only the synthetic fixture and never changes the production default. Tests that intend a missing sampler explicitly set clock=None after a valid sampled issuance. The legacy provenance test removes the retained clock evidence after valid issuance, preserving its original provenance-refusal assertion. Removing obsolete elapsed-counter constructor arguments follows §4 P1 #7; a new test verifies that field is rejected. Literal casts describe already closed fixture operation tuples; expected outcomes are unchanged.

## Scope and residuals

All five effect adapters remain UNAVAILABLE. No native origin authentication, live queue/CAS, provider owner, production journal migration, runtime command, service, deployment or receipt is implemented or exercised. Q1 owns the pinned queue bridge, actual lookup wiring and lost-post-ack malformed lookup classification. Whole-undispatched settlement stays HOLD until the newly admitted §3.1 bridge proves the complete request. Confirm/recovery native purposes remain unsupported.

P1 #4–6 and P2 #10 remain unwaived as described above. Existing strict limits and terminal reserve tests remain. The fixture request/event limits are not a claim that future coordinator-specific reserved budgets exist. The complete retained actions suite is run, including all original P0/P1/P2 modules and new C1 regressions. Broader product runtime tests are outside this pure change.

Ripwire quality output is retained with its nonzero status; source journal complexity/size, test duplication, inferred dead code and historical churn are not waived. Test/overload/dynamic-dispatch dead-code reports are structural limitations, not permission to erase coverage. The decision-metadata validator is separated from consent binding for clarity. The recovery predicate remains explicit beside exact child provenance because removing conditions to reduce a metric would weaken the approved invariant. The reviewer should assess these actual debt items.

## Recorded offline measurement and composed limits

A serial 1,000-iteration synthetic permission projection used an 806-byte prepared request and 2,573-byte action snapshot. All outcomes were EscalateProposal. Median was 3.527 ms, p95 3.679 ms, maximum 10.575 ms, total 3.570 s. This measures a representative bounded fixture, not maximum payloads, production latency or activation readiness. P2 #10 remains unwaived.

The isolated P1 fixture still permits 128 outstanding requests and reserves 96 terminal slots. The approved composed §3.4 policy of 64 outstanding and 161 reserved slots remains a P3/P4 acceptance prerequisite; C1 does not claim that policy is implemented. Existing bounded fixture caps and reserve regressions support offline assessment only.

Final precommit quality delta reports 93 regressions: 24 minor, 73 preexisting-worse, 20 new-symbol and 49 gating findings. These categories overlap. All remain unwaived, including journal complexity 28→31 and verbosity 694→728, test duplication and historical churn. Configured Pyright checks 345 files with zero errors and warnings.
