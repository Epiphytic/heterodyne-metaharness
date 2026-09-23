# Bead-forward workflow and signed decision receipts

Status: design proposal; no runtime behavior or permission changes in this document.
Task: btq-harness-67e876d04b02b3ba1cccf2d3, revision
8b42579e8e5d465cb0425a025efd35cf9f2f1369d40bf08e3076857fecb33ced.

## Decision and authority

Represent every approval, blocking question and operational blocker as a durable
Bead linked to the exact dependent outcome. Beads owns dependency and resolution
state. Existing operator asks own visible questions, reminders and delivery retries.
A signed decision establishes who approved which evidence; it does not establish
successful execution, native permission, or authority outside the signer's scope.

Extend [gates](../spec/gates.md), [operator asks](../spec/operator-asks.md),
[permission relay](../spec/permission-relay.md), [sessions](../spec/sessions.md),
[worktrees](../spec/worktrees.md), [formulas](../spec/formulas.md), and
[delivery ownership](../spec/delivery-tasks.md). Those contracts remain authoritative
until reviewed extensions and their implementation land. Preserve independent
completion review wherever enabled. No lifecycle waiver is inferred from a blocker.

The accepted btq-50u design at commit 22d7994 supplies preparation, least-privilege
manifests, occurrence classification and negotiate-then-restart. It is absent from
this checkout's baseline; its retained artifact was read in the btq-50u worktree.
Implementation of that preparation system belongs to dependent Bead
btq-harness-ac63e3b714f65cae632e79b2. This design defines the blocker and decision
interfaces it consumes, without making its unfinished functionality a prerequisite
for basic human resolution. Deployments remain manager-side.

## Existing seams and changes required

`task_gates.create/check/resolve_gate` already reserves stable gate identities,
installs blocking edges and validates revision-bound resolution. Extend this path
with a versioned contract; do not build an independent approval engine.
`operator_asks.enqueue/register/resolve` already binds durable asks to outbox parts
and delivers reminders. Add a blocker identity to that association and reconcile
resolution through the existing outbox. `permission_relay` retains consent and
observation evidence but explicitly cannot authenticate a native request.
`task_progress.category` is a projection, not an eligibility check.

Gate metadata readback currently compares raw objects. Shared known-key decoding
must accept an object or one JSON encoding, validate schema, and retain raw evidence;
scalar, malformed, duplicate-key and double-encoded values fail closed. Reuse the
adapter normalization planned by btq-50u/ac63e3b7; if needed earlier, land that bounded
prerequisite here and record reuse on ac63e3b7, not a second implementation.
Do not retroactively recalculate historical scope hashes.

## Bead contract and admission

Use native gate Beads for non-executable approvals, questions and access checks.
An executable fix, investigation or external work item is an ordinary separately
owned task that can satisfy such a gate with retained evidence. A human or external
assignee is never interpreted as a coding-worker claim.

Versioned `harness_blocker` metadata contains:

| Field | Meaning |
| --- | --- |
| version, key, request_digest | Schema and immutable caller identity |
| run_id, dependent_issue_id | Exact routing and task being blocked |
| category | approval, question, access-validation, external-result, pr-review |
| scope_digest, artifact_revision | Current requirements and exact commit/artifact |
| assignee | Tagged human/agent/worker/external identity and registered route |
| authority_policy_ref | Immutable allowed decision makers, action and delegation scope |
| resolution_contract | Required answer, artifacts, native execution or external result |
| ask_id, observation_ref | Existing delivery and permission evidence associations |
| phase | open, approved, resolved, denied, expired, superseded |
| receipt_refs, effect_ref | Retained decision and independently verified effect evidence |

The logical phases are metadata; native status stays open until verified resolution
allows native closure. `approved` is consent received, `resolved` means all required
effects or answers were verified, and `closed` is the final native status. Denied
or expired decisions do not release the dependent task. Operator cancellation or
replacement requires an explicit disposition; closing a denied Bead manually does
not satisfy managed gate validation. Questions that change scope require a new
scope receipt and revalidation before execution.

Resolve assignees against configured identities; assignment does not grant authority.
Keep the external decision owner distinct from the harness route used for reconciliation.
Unknown routes remain pending and visible. A human receives an existing Marmot ask;
an agent/worker receives a bounded task dispatch via its registered provider;
an external entity uses a configured ingress or an explicit human-mediated route.
No arbitrary URL, executable or new workstream is derived from Bead text.

Reserve immutable intent and a fail-closed parent gate binding before creating the
Bead or dependency. Use deterministic IDs from run/dependent/key. Native edge direction
is dependent blocks-on blocker; avoid parent-child propagation cycles. Admission
validates bounded graphs, existing outcomes and routes, rejects self/cyclic links,
and preserves other prerequisites. Partial writes suppress eligibility. Exact replay
repairs missing records/edges after readback; a changed body under a key conflicts.
Operational receipt/phase fields must be excluded from the new gate's scope digest;
requirements, decision policy and dependency identities remain included. Version the
scope algorithm rather than changing historical hashes in place.

## Signed receipt format and verification

Retain an original signed Nostr event plus the exact evidence bytes it binds.
Use NIP-01 event serialization and signature verification, with a tested BIP-340
implementation; never invent signatures for connector events missing original fields.
The event content contains the versioned decision envelope below. Its digest covers
retained evidence; the signature covers the event containing that digest. Public keys
are canonical hex internally, with validated npub display references.
Protocol reference: [NIP-01 events and signatures](https://github.com/nostr-protocol/nips/blob/master/01.md).

The private application receipt envelope contains schema, domain, repository identity,
run ID, blocker ID, dependent ID, request/scope digests, artifact revision, decision
(approve/deny/answer), evidence SHA256, action/resource scope, signer policy revision,
nonce, issued-at and expires-at. Evidence includes the precise question/command and
answer plus immutable artifact hashes. Encode evidence using one versioned canonical
JSON profile: UTF-8, sorted keys, compact separators, no floats or duplicate keys;
hash the retained bytes, not reconstructed display text. Publish test vectors.

Use a namespaced application payload carried by the existing private signed message
transport; pin its supported event kind in the adapter contract before activation.
Do not allocate a supposedly standard Nostr kind or publish private evidence to a
public relay. An adapter lacking raw signed events cannot claim cryptographic receipt
support. A human may supply a dedicated signed receipt if the reaction transport
cannot retain a verifiable event and its exact target binding.

Verification checks event ID/signature and derived npub, envelope schema, retained
hashes, exact question/action/target, authority and delegation, nonce, expiry and
current scope. Key ownership is not authority: merge/deploy still require an operator.
A worker cannot self-approve an operator gate. Key revocation before effect blocks
execution; retain the historical valid signature without treating it as current grant.

For a signed reaction, verify its original event and exact delivered target, then
validate the retained signed request binding containing the evidence digest. Store
both events as a receipt chain; a reaction to vague text is insufficient. Connector
authentication alone remains consent evidence under the existing relay contract.
Private keys stay in the configured signer, never in Beads, prompts or worker files.

Receipt event ID plus request identity is the replay key. Identical retries are
no-ops; conflicting bytes or conflicting decisions require explicit reconciliation.
New scope or amended evidence requires a new request/nonce. Do not implement
last-arrival-wins or assume timestamp ordering proves authority.

## Block, resolve and resume

1. Persist blocker intent and parent execution hold before dispatching a question.
   Refuse subsequent executable turns and lifecycle progression on that parent.
   A running command cannot be undone by a dependency edge: record the outstanding
   effect and use the provider's supported safe-stop boundary. If it cannot stop
   safely, expose quiescence-pending and do not claim the parent is suspended.
2. Admit the blocker and existing operator ask using stable linked identities.
   Delivery failure leaves a visible durable pending ask, not an unblocked parent.
3. Receive and verify the decision. Approval transitions to approved only.
   For pure review approval, retained artifact validation may satisfy resolution;
   for access or deployment, independently confirm the required native/effect result.
4. Persist resolution before native closure. Read back native closure and matching
   receipt/effect digests before resolving the visible ask. Repair partial progress
   idempotently without repeating native effects.
5. Revalidate all parent dependencies, scope, ownership, permissions, artifacts and
   recovery/pause state. Schedule continuation at an authorized native boundary;
   closure creates eligibility, never an automatic approval or immediate keystroke.

The terminal-approval example is conditional on an independently verified provider
request/response adapter. It must bind native session/turn, request ID, complete
command, cwd, requested capability and digest; support once-only response and query
of uncertain outcomes. Terminal text alone cannot meet that contract. Until the
adapter is implemented and live-tested, the manager inspects and decides through
the native interface, then retains actual result evidence. No reaction types keys.
`approval_policy=never` is never relaxed: use supervisor execution within existing
authority or negotiate-then-restart with a reviewed manifest. The blocker remains
open until effective permissions and pending effects are reconciled.

Every mid-task permission request also records a btq-50u occurrence, class a (scope)
or b (spawn permissions), and one idempotent improvement Bead. This feedback task
is distinct from the access blocker; diagnostic uncertainty cannot approve access.
It does not recursively create incidents when the feedback transport itself fails.

## Secondary session while blocked

Current worktree rules allow one current native worker and only specific retained
handoffs. Introduce an explicit, disabled-by-default secondary-slot extension; do
not call the ordinary task-switch command on a worker waiting at an approval prompt.
The supervisor remains the same manager and run. It registers a separate native
session and tmux pane for independent work, never a replacement for the blocked one.

Each slot records slot ID, exact native lineage, claim actor, task, checkout/branch,
provider configuration, manifest reference and active/idle/suspended state. Queue
ownership remains tied to the stable workstream; slot assignment is an additional
exclusive execution lease. Existing primary hooks keep their contract; slot-aware
hooks reject mismatched cwd/native/task tuples. Claim validators must explicitly
permit this blocked-primary mode; no bypass of current single-worker restrictions.

Require a persisted primary hold, a safe provider boundary, no uncertain primary
effect, separate checkout, eligible unrelated task, and explicit resource/conflict
checks. Dirty primary files may remain in their retained checkout without committing
fictional completion. They are never copied into the secondary checkout. A primary
approval stays in its exact pane. Run-wide recovery or operator stop prevents both
slots; only a proven task-local hold permits secondary work. Bound the initial
extension to one secondary slot. No timeout reclaims another worker's claim.

On blocker resolution, finish or park secondary work at its own safe boundary;
resume the primary's registered identity and retained worktree after revalidation.
Never choose latest session, replay an old prompt, force-stop active tools, or infer
idle from a vanished pane. Persist intent before each launch/resume. Crash recovery
requires reconciling both identities, processes, claims and effects before dispatch.
Both slots report through the same existing run delivery mechanism with task IDs.

## PR watch and delivery integration

Reuse formula roles and retained handoffs: implementation owns code/build/test and
PR-open; review owns actual review/merge evidence; deployment and verification retain
their existing owners. Splitting testing into a separately claimable outcome needs
an explicit new formula version, not silent changes to published profiles. A PR-watch
is a bounded observation task associated with the review gate, not merge authority.

Pin repository/forge, PR or Radicle patch identity and exact head revision. Prefer
configured event ingestion, with a bounded watcher dispatch when needed. An hourly
scheduler reconciles persisted watch records even after the watcher exits. Each
attempt acquires an exclusive generation lease, queries bounded history, retains a
cursor and schedules its next check; no continuously polling model. Expired lease
permits watcher reconciliation, not automatic Bead ownership reclamation.

Deduplicate review events by repository/review/event/revision. Only authorized review
comments yield fix Beads, with explicit acceptance, source and blocking dependency.
Ambiguous comments become questions. Fixes invalidate stale head approvals, feed
back through implementation/testing, and produce a fresh review request. A passed
PR-review receipt can close its approval blocker after validation; it does not prove
a merge occurred or close the review lifecycle task without merge/test evidence.
Unsupported forges stay pending; use Radicle evidence for Radicle reviews.

Human deployment approval uses the same blocker/ask/receipt path, bound to the
reviewed and tested merged commit. Actual deployment remains operator-owned.
The hourly backstop uses the configured existing maintenance route and operator-ask
contract. Preserve its required group/admin checks; do not embed deployment-specific
identities in the new generic blocker model. Coordinate notification and pause
legitimacy changes with the queued babysitter Beads; do not duplicate their engine.

## Persistence, rollout and specification changes

Beads retains contract, blocking edges and resolution references. Supervisor SQLite
retains local intent, receipt verification, effect state, slot registry and delivery
projections; immutable bounded artifacts retain signed bytes and sanitized evidence.
No transaction spans Beads, SQLite, native UI and Marmot. Persist intent, apply once,
read back, then advance. Every uncertain external effect stops for reconciliation.
Missing receipt artifacts fail eligibility closed. Do not log credentials or permit
unbounded event sizes, graph walks or artifact reads.

Extend gates with a versioned blocker/receipt contract; operator-asks with linked
Bead projection; permission-relay with verified receipt ingestion and conditional
native adapter requirements; sessions/worktrees with registered secondary slots;
formulas with watch association; task admission/stages with blocker revalidation.
Preserve v1 gates and historical consent as such; do not relabel them signed receipts.
Explicit adoption is required for existing open asks, with exact binding evidence.

Proposed implementation order: schemas and normalization; admission/resolution and
real signature validation; ask/relay integration; bounded PR watch reconciliation;
secondary-slot admission/recovery; full end-to-end fixtures. Land the documentation
before code. Activation requires configured signing authority, verified connector
capabilities and provider support. Unsupported paths remain visibly blocked.
Rollback disables new dispatch and receipt application, preserving claims, blockers,
asks and artifacts for reconciliation. It never strips edges or grants broader access.

## Acceptance evidence

Use temporary state, test keys and local fake transports/providers; no live message,
service restart or real signing credential is required for repository fixtures.

| Fixture | Required result |
| --- | --- |
| Parent block → signed agent approval → verified effect → close → resume | Exact original task/session resumes once; dependency enforced throughout |
| Human Marmot approval | Real test-key signature verified; correct account/group/target, one ask and retained receipt chain |
| Tampering and authority | Bad signatures/npubs/digests, stale head, revoked or unauthorized signers and wrong scopes cannot resolve |
| Partial writes/restarts | Crash at reservation, create, edge, receipt, native result, close and ask resolution repairs without duplicate effects |
| Native limitations | Clipped prompt, unknown request, stale pane and policy never cannot cause keystrokes or permission expansion |
| Secondary execution | Blocked parent executes nothing further; isolated eligible task runs; no shared writer or identity substitution |
| Recovery and pause | Run-wide hold stops both slots; dirty/uncertain primary or secondary cannot be force-switched |
| PR-watch backstop | Dead watcher reconciled by hourly attempt; duplicate events create one fix; changed head invalidates approval |
| Lifecycle preservation | Raw closure, answer receipt or PR approval cannot masquerade as tests, merge, deployment or live verification |
| Compatibility | Legacy gates/asks retain behavior; object and JSON-string metadata normalize identically without rewriting history |

Run the repository full suite after implementation, retaining command, clean commit,
log and hash. Live connector/native-adapter verification and deployment remain
separate manager-owned evidence. This design document alone satisfies none of those
runtime acceptance tests.

## Implemented contract and review boundaries

The first implementation follows spec/blockers.md, spec/pr-watch.md and
spec/secondary-workers.md. The conceptual `harness_blocker` contract above is stored
inside the existing `harness_gate.blocker` binding rather than a parallel engine.
Decision phase lives in `harness_blocker_decision`; native gate resolution remains
`harness_gate_resolution`. Consent hashes `decision_evidence`; resolution retains
separate actual effect artifacts, so a signer need not predict future native output.

Native gate Beads represent non-executable watch/control work; registered agent
inbox dispatch supplies execution. The deterministic supervisor tick supplies the
persisted hourly backstop, not a second OS cron owner. Review adapter executables
are operator-configured; no hosted provider is silently selected or installed.

Secondary admission initially requires clean, confirmed-idle primary state and no
native approval prompt. It supports independent work while a signed gate is open;
it does not freeze an executing command. Startup and parent resumption require
explicit steering. Unknown outcomes remain retained for manager reconciliation.
The broader preparation/permission manifest work remains ac63e3b7, and the missing
native request adapter/live-test gate remains unchanged. No automated terminal
approval is claimed by the offline fixtures.

Expiry is rechecked when using a resolved gate. Replace expired/revoked/scope-changed
gates explicitly using supersedes; do not rewrite receipts. Tests use real Schnorr
signatures and temporary fixture artifacts, while this task's suite logs are retained
under docs/evidence/btq-harness-67e876d0. Deployment verification remains manager-side.
