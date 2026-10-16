# Bounded controller management delegation d1

Design task `btq-so7ma`, Codex author, 2026-10-16. Proposed design only; no
current operational grant, implementation or deployment. Root conducts genuine
Claude cross-model review (maximum three actual rounds), publishes the exact
reviewed design on GitHub and obtains human approval through Marmot/admind.

## Existing contracts and intended result

The operator wants blocking approvals only for new/changed designs and
deployments. [Management d2](../reviews/approved-inputs/2026-10-15-operator-task-management.md)
requires an authenticated card for each management mutation. Add a distinct
finite delegated authority mode alongside that unchanged legacy mode. One human
choice can explicitly grant an exact finite operational descriptor attached to
the new design/deployment decision; generic approval of this mechanism grants
no operations, future tasks or implicit blanket controller trust.

[Provenance](../reviews/bounded-controller-delegation-inputs.json) pins exact d2
archive bytes and observed source. Future planning kinds require separately
reviewed, genuinely approved and deployed `btq-zcd1l`. Its observed e73e revision
is proposed, not its eventual approval pin. Resolve its actual approved revision
before implementation; changed classification/protection needs compatibility
review. Do not duplicate or silently approve Z here. Implementation tasks retain
the original valid native design pins and final matching approval blocker.

The approved controller-integration evidence contract (historical source
`3158263`) governs truthful lifecycle/review evidence, not delegated management.
Its scope acknowledgments, native gates and review counters remain intact.
Nothing here modifies its approval verifier or claims a new review of it or d2.

Implementation requires the queue Management adapter, pinned Hermes wrapper/
installer and a new admind delegation service. Methods below are proposed APIs,
not claims about installed capability. Until reviewed implementation and approved
deployment, use existing real per-card authority or hold. Verify actual current
admind policy-path/transport health; the historical setting fix grants no new
capability or operational authority.

## Immutable finite descriptor

Version-1 descriptor JSON rejects duplicate keys, unknown fields, ambiguous
values and unknown versions. Canonical UTF-8 JSON uses sorted keys, compact
separators, no NaN and bounded integers. SHA256 covers the complete payload;
only the outer hash/signature envelope is excluded. Publish the descriptor in a
signed Git commit with the design/deployment materials. Bind exact commit,
path, blob and SHA256 in the authenticated ask. A controller Git signature proves
artifact identity, never human consent or automatic signature-key enrollment.

Required payload:

- Unique `grant_id`; exact published design commit/path/blob/SHA256 and genuine
  native design approval. Exact reviewed queue/wrapper/admind release identities
  and actual deployment authority; activation waits for deployed hash proof.
- Exact enrolled controller agent, host, run and session, workstream, native
  queue/store identity, and affected code repository common-directory identities.
  Paths alone, aliases and another session's receiving lane are insufficient.
- Finite existing task IDs, each exact native kind, derived management class,
  repository binding, primary pins or explicitly permitted absent planning pins,
  and that task's exact own receiving lane. No wildcard, title predicate,
  dynamically discovered future task or caller-supplied class.
- Finite unique operation IDs, each one task and one exact typed delta with exact
  old state and endpoint facts. No alternative deltas. Each task appears in at most one operation per grant, avoiding ambiguous
  inter-operation snapshot progression. Each operation is one-shot;
  `max_operations` equals list length, at most 32 operations and 32 tasks. Each
  replacement has at most two native steps. Exceeding bounds refuses.
- UTC `not_before`/`expires_at`, interval at most 24 hours; exact current approver
  policy fingerprint. Actual human decision/ask binding belongs in the daemon's
  authenticated activation envelope, not trusted caller-authored provenance.

Only these operation classes are admitted:

| Class | Exact delta and protection |
| --- | --- |
| Status | blocked/unassigned to open/unassigned; closed-source reopen excluded in v1 |
| Dependency | Enumerated typed add/remove/replace with exact old/new endpoint IDs, types and endpoint snapshots; preserve every existing approval prerequisite |
| Reclaim | Named former stopped holder into named controller own lane, unchanged status, fresh locally verifiable ended identity and existing holder locks/fences/reservations/cap checks |

Reclaim binds holder identity, host/boot ID, PID/start identity and cessation
evidence. Existing checks must report ended at preparation and immediately before
transfer. Missing identity, inaccessible host, unknown or alive refuses. A stop
request or age is insufficient. D2's per-card unknown-liveness exception does
not extend to delegated mode. Ordinary management ownership/quiescence predicates
still apply; a descriptor cannot override them.

Always forbid approval-task targets, routing/kind/parent/metadata/design-pin
changes, removal of genuine approval gates, status-to-closed, direct in_progress
assignment, borrowed identity, direct DB writes, worktree deletion, history edits
or historical review-counter resets. An approval endpoint may only be named to
add/preserve its prerequisite, never remove/replace it or edit that endpoint.
Implementation pins/final blocker use ordinary native verification; planning uses
only deployed Z's exact protection. Missing/mixed/unknown native kinds refuse.
No merge, deployment, service/policy/member change, arbitrary command or message
send is included, except the bounded protocol audit notices specified below.

## One activation; no new blocking routine questions

All new authority questions and answers go through Marmot/admind. The activation
card shows exact controller/run/workstream; all task IDs/kinds and operations;
expiry, grant hash, executor/policy identity, direct GitHub design/descriptor
links, exclusions and stopped-holder evidence. It explicitly grants the finite
operational scope and says covered routine effects need no fresh blocking card.
It also discloses the in-flight revocation limit below. Generic design acceptance
without that explicit operational choice cannot activate a descriptor.

Apply quoting, secret and budget checks. Complete safety facts must fit supported
multipart posting or the request refuses; explicitly reduce scope and ask afresh.
Do not truncate or hide unexplained permissions behind a digest. Unanswered asks
remain durable. Terminal diagnostics/repair use only already granted scope;
terminal/chat text is never consent or an approval substitute.

Proposed typed ask attachment `controller-management-grant-v1` binds descriptor
hash and action `activate`. It does not replace implementation primary pins via
ask.targets. Existing native design approvals/dependencies remain separate and
mandatory. This new delegation protocol needs its own strict attachment verifier;
its absence holds delegated execution, not ordinary manually verified design
amendments under the existing approval flow. Never loosen native approval_valid.

Activation verifies the actual stored authenticated ask decision, current operator
in confirmed membership intersected with fresh policy, exact attachment/artifact
binding, no unresolved membership mutation and the real selected activation
choice. Persist that provenance. A decline, self-authored JSON, model verdict or
copied historical decision cannot activate.

Keep current `Management.apply` per-request ask behavior unchanged. Add explicit
closed delegated plan/apply grammar and a management-specific durable schema
with discriminant `authority_mode=per_request|delegated`. Exactly one complete
structure is required. Never put a grant ID in an ask field, fake an answer or
reinterpret a saved old request. Nonterminal legacy requests complete under their
original authority or hold for reconciliation. Shared Handoff formats remain
compatible. Wrappers retain exact actor enrollment and pinned source closure.

## Proposed admind authority and revocation service

Implement a daemon-owned durable delegation journal through supported Store
migration/transaction APIs, never operator-written DB rows. Persist activation,
monotonic per-grant revision, policy fingerprint, operation/step reservations,
revocation and artifact hashes. New local control RPC methods use strict bounded
payloads/responses and existing authenticated endpoint trust, not caller-created
receipt files. No credentials or unnecessary task payload appears in receipts.

| Proposed method | Transactional contract |
| --- | --- |
| `delegation.status(grant_id, descriptor_hash)` | Exact descriptor/decision binding, current state/revision, fresh policy/authority validity, ledger and daemon boot identity. Unknown/corrupt/missing history refuses. |
| `delegation.reserve(grant_id, operation_id, request_id, facts_digest)` | Verify active authority, exact subject/deployed executor, time and policy; atomically consume one operation slot. Same binding retry returns same reservation; different binding refuses. No refund/reset API. |
| `delegation.permit(reservation, step_id, before_digest)` | Fresh authority/revocation/allowed-step check, prior settled steps required; atomically record one permit, with revision and remaining lifetime at most five seconds, clipped to grant expiry. Retry returns original permit with remaining lifetime, never extends it. |
| `delegation.settle(reservation, step_id, receipt, outcome_hash)` | Idempotent verified readback/uncertain-result record; no fresh effect authority, no erased consumption. Held step prevents subsequent steps. |
| `delegation.revoke(grant_id, descriptor_hash, decision_ref)` | Accept only a stored authenticated answer to the typed revoke ask for this exact grant/hash, with fresh operator authority; monotonic/idempotent revoke returns revision and outstanding permits. |

The supported future operator command posts a durable typed
`controller-management-revoke-v1` ask; invocation alone does not revoke, and
arbitrary old decision references refuse. Revocation asks are authority withdrawal,
not routine task approval questions. The original activator or any current
operator authorized by the same policy may request revocation; consumption checks
also hold when the activator loses current authority. No unilateral local journal
edit masquerades as revocation. A deliberate new grant requires human authority.

Reserve/permit/settle never ask a human synchronously. They check the one actual
grant. Missing transport, stale/removed approver, changed policy, daemon latch,
uncertain membership, wrong runtime or journal inconsistency holds with no new
permit. A cached earlier status never authorizes another effect. Reconciliation
and audit may proceed after revocation/expiry without further task mutation.

Revocation linearizes at journal commit: no permit is issued afterward. An
already issued permit may start its one step before its original deadline.
This is an explicitly disclosed maximum five-second initiation window, not a
claim of instantaneous cross-service cancellation. Controller starts elapsed
monotonic timing BEFORE the permit RPC, subtracts it from returned remaining
lifetime and checks expiry immediately before entering the bounded native call.
The call may finish later; the window bounds initiation, not completion.
Outstanding steps remain visible until readback; expired permits do not prove
quiescence. Local locks cannot atomically transact remote revocation with native
Beads. If zero in-flight effects after revoke acknowledgement are required, v1
must hold for a separately designed native atomic-authorization mechanism.

UTC rollback, lost monotonic reference or daemon reboot holds unissued work.
Server persists its last observed UTC, refuses rollback, and requires retained
journal/revision plus fresh policy on restart. New daemon boot invalidates old
unissued permits. Executor checks durable revision high-water marks; delayed
replies, rollback or restored/lost history refuse, not reset counters. Restart
of the same enrolled run may reconcile exact saved operations but never replay
an issued native call. An inactive pre-crash reservation is not fresh authority.

## Execution, locking and crash consistency

Keep affected pickup paused and workers quiescent under existing management
preconditions. Use established controller/lane/holder/native lock ordering,
fences, reservations and cap checks. Compute the complete native task/endpoint/
worktree/process snapshot and derived protection. Exact planned before state
must match before reservation and before each step; classification, pins and
native gates are freshly validated. No broad comments/content exclusion hides
drift. A changed source cannot borrow the old descriptor.

Persist request ID, descriptor/activation hashes, reserved operation, before
facts and complete expected after state. For each native step obtain a permit,
durably record issued, re-read facts/authority and check deadline, then invoke
existing native adapter at most once. Dependency replacement remains
add-before-remove. Whole-bead/endpoint readback accepts only intended delta,
then settles. Fresh status is required before effect; no stale receipt fallback.

After issued, unknown results are read back, never automatically reissued even
if apparently not committed. The operation remains consumed/held for diagnosis.
Crash after commit before settlement/audit resumes by readback and idempotent
settlement. Revocation/expiry/drift between replacement steps retains the added
edge and holds before remove; no rollback pretending the multi-step repair was
atomic. All missing-lock and renewed-alive verdicts refuse before writing.

Append the existing request-marked native audit with grant/hash, real activation
reference, operation/step IDs, policy/revision/permit receipts, exact delta and
before/after evidence hashes. Do not claim a per-operation human answer. Audit
retry retains issued/readback discipline and cannot repeat task mutation.
Completion requires fresh protection/gates and exact final readback; after
revocation it may truthfully record verified effects and authority revoked while
pickup remains paused. No automatic unpause, claim, close or worker resumption.

## Renewal, delivery and executable acceptance

Descriptor/list are immutable. New tasks/deltas, interval extension, changed
run/session, policy or executable release require a new exact signed descriptor
and authenticated Marmot grant. No counters/history reset, auto-renewal or
blanket mutable policy. Exhaustion/expiry returns structured HOLD; operator may
deliberately approve another finite design/deployment attachment or leave paused.
No extra blocking question is introduced per routine operation within scope.

Queue and admind code reviews plus explicit approved deployments are future
prerequisites, with design links and deviations in deployment cards. Preserve
all native approval/lifecycle gates and actual review budgets. Design approval
alone authorizes neither installation nor a live initial descriptor. This design
proposes the five-second revocation boundary openly for review/human choice;
implementation cannot silently strengthen claims or weaken checks.

Required offline acceptance assertions (proposed, not executed):

| Cases | Required result |
| --- | --- |
| Genuine activation, exact deployed identity, finite valid operation | Only named delta runs without fresh blocking routine card |
| Mutated descriptor, duplicate/unknown keys, wrong action/decision, design-only consent, missing attachment | Zero writes |
| Wrong subject/run/session/lane/repository/store/executor/policy | HOLD without aliases or inherited authority |
| Approval target, mixed kind, invalid pins/blocker, planning without Z, unknown/alive/remote holder, closed source/destination, approval edge removal | Refuse at each effect boundary; history/gates preserved |
| Concurrent reserve, crash, exhaustion, same/different retry | One consumed slot; no refund/reset or replay |
| Revoke before permit; after permit; during replacement | No post-revoke permit; only unexpired previously issued step may start; partial graph retained |
| RPC/latch/policy/membership failure, UTC rollback, boot/revision change, delayed reply | No new effect or cached-validity fallback |
| Crash before/after issued/native/readback/settle/audit | At most one native invocation, uncertain result held, truthful readback retained |
| Drift during permit await, holder returns, locks/cap unavailable | No native write |
| Hostile/oversized/secret card, unanswered ask, terminal answer | Safe durable refusal/retention, no partial card or fallback consent |
| Legacy per-request requests and existing safe Management tests | Existing authenticated behavior unchanged, no reinterpretation |
| Wrapper/installer closure and normal claim/close/review gates | Reviewed exact deployment, no DB bypass or review reset |

Use named disposable fake queue/admind/clock tests and failure injection at each
ledger/native boundary. No known live-DB queue tests, broad discovery, model
calls, live scans or sends. Completion here is only a reviewed, published and
human-approved design with honest provenance; implementation and deployment
remain separate work.
