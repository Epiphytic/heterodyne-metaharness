# Artifact review gate

Design for btq-ejp. Status: implementation design; manager decision on ask 14f487f9 accepted.

This extends spec/reviews.md, spec/gates.md and spec/blockers.md. The existing
completion-review boundary, native permission checks, recovery checks and task
ownership remain authoritative. Review approval authorizes the exact artifact;
it does not execute a merge, deployment, service operation or native prompt.

## Policy and configuration

An operator-controlled `review_gate` section configures `enabled`, `model`,
`auto_approve`, `max_attempts`, the provider endpoint/profile, and the signed
blocker policy and receipt signer. The intended model is
`fuelix/claude-opus-5-5`; the transport is Anthropic messages with Bearer auth.
Neither the provider nor model is hardcoded in the gate state machine. Credentials
remain in the provider profile, outside task metadata, prompts and audit logs.
The Hermes adapter is optional; the harness contract is provider-independent.

`auto_approve=true` encodes Liam's temporary policy until portable v1. It applies
to design, PR and escalation artifacts across workstreams with this policy
installed. `false` routes a passing appraisal to an operator approval Bead.
Missing/invalid configuration fails closed. Each engagement snapshots the policy;
a policy or model change requires explicit revalidation, not reinterpretation of
an old verdict. No worker-supplied request can override the policy.

This is a new delegated artifact-review authority, not an `approved_by=Liam`
record. Existing human-only approvals, signed blocker policies and native
permissions cannot be impersonated. In particular, the mandatory separate human
approval and two-model ADR for Claude implementation remain independent guards.
A review pass must not fabricate those records.

## Bead and engagement lifecycle

Represent review Beads as native gate issues with a review category, following
the existing approval/blocker structure. They are prerequisites rather than
ordinary worker claims. Reserve a deterministic identity before creation; verify
the native blocking edge and immutable parent binding on every replay. Store
run, parent issue, semantic scope, artifact kind, artifact revision, retained
artifact paths and digests, policy snapshot, model, and attempt number.

Supervisor admission creates a pending engagement from an explicit artifact
submission. A review of a PR includes the complete committed diff and tests;
a design includes the complete document; an escalation includes its exact
question, proposed action and supporting evidence. Acceptance criteria are copied
from the verified parent snapshot. Missing or truncated evidence cannot produce
an automatic pass. Task text and artifacts are untrusted reviewer input.

Reuse the isolated, tool-free review process and durable process markers from
review_dispatch/review_runner. Model configuration must be written separately
from the untrusted evidence. One active model invocation per workstream remains
the concurrency rule. Unknown process state blocks another invocation until
reconciliation establishes that it exited. Review process and artifact identities
are independent of the coding session.

States are pending, running, reviewed, changes-requested, receipt-pending,
approved, escalated, stale and superseded. A running job without a terminal
result remains uncertain until inspected; it cannot be retried automatically. A result
has a validated verdict, summary, concrete findings, model identity, reviewer
session and evidence digest. A malformed result, model failure or missing evidence
never means pass. Persist the result before any outward effects.

Findings produce one durable worker inbox item for the exact engagement attempt.
The existing native-boundary routing owns submission safety. A worker revision
submits a new artifact digest with the previous engagement identity; it cannot
edit a retained verdict or accept its own findings. The replacement gate/edge is
installed before the old edge is superseded, so the worker can revise without
losing the review prerequisite. Every attempt retains its artifacts and findings.

A pass with auto-approval enabled enters receipt-pending. Only a verified receipt
for the exact policy/scope/artifact can resolve the approval gate. A denied review,
exhausted attempt budget, explicit reviewer escalation or policy-disabled pass
creates a deduplicated operator approval Bead and discrete operator ask. Recoverable
transport errors retry the same delivery; they do not consume new model attempts.
Uncertain reviewer processes require reconciliation rather than blind retries.

## Receipt authority and delivery

Reuse the NIP-01/BIP340 envelope and verification in blocker_receipts, including
run/repository, parent/gate, scope, artifact revision, nonce, policy revision,
issued/expiry times and evidence digest. Decision evidence adds the verdict,
configured and resolved model, reviewer session and retained appraisal digest.
The reviewer has no signing keys or mutation tools. A trusted supervisor-side
signer receives only an independently validated passing result bound to the
current policy and artifact. Verification remains mandatory after signing.

The durable outbox publishes the full appraisal and approval receipt to the
bound workstream group. Retain the receipt and delivery message identifiers;
retry the same outbox identity after transport failure. Announcing an approval
is distinct from retaining a verified approval. Group delivery through Marmot
alone does not supply a complete signed blocker-decision event: the current
`send_final` API returns message IDs, while blocker verification requires the
original signed NIP-01 event. Do not replace signature verification with an
outbox delivered timestamp or assert the model is the operator.

Resolved decision (manager, ask 14f487f9): Belthanior signs delegated review
receipts for each workstream, following approval-receipt conventions. The signer
is configurable and defaults to manager. Dispatch a durable signing request to
the configured manager inbox; ingest the original signed event through the
facade. Signing is delegated execution, not another human approval question.
Private keys remain manager-side. The configured blocker policy supplies the
authorized public key; the worker cannot choose or invent it.

## Implementation seams

Add a review-gate module for policy validation, immutable engagement transitions,
revision admission and signed-result application. Extend native gate/blocker
category validation with the delegated review contract, without loosening legacy
operator-only gates. Add explicit submit/receipt facade commands; submit with previous records a revision.
The existing inspect command lists both review kinds.

Extend review dispatch to share its process-concurrency guard with gate reviews,
and extend the runner with a separately retained provider/model configuration and
strict gate-result schema. Keep scheduled progress/completion reviews compatible.
Use existing task observations, inbox routing, operator asks and outbox delivery.
Retain real model evidence in docs/evidence/btq-ejp/ with secrets excluded.

## Validation

Cover admission/replay, findings/revise/pass, repeated failures and escalation,
auto-approval disabled, stale task/artifact/policy, forged/expired/revoked signatures,
missing signing configuration, outbox failure/retry, restart in each state,
uncertain process handling, cross-run isolation and legacy gate behavior. Fixtures
must not send live Marmot messages. The full harness suite is the regression gate.

Run one real artifact review using Fuel iX and retain model identity and evidence
hashes. Verify receipt and outbox conventions in signature-backed lifecycle tests.
Manager rollout supplies the production signing policy and validates live receipt
and group delivery, which remains distinct from the live model acceptance test. btq-c2p is closed with manager-recorded live Bearer-auth evidence;
that enables the provider test but does not itself configure receipt signing.

Artifact gates use native external prerequisites with review-category metadata;
design/PR/escalation denotes the reviewed artifact, not permission to execute
merge/deploy. Legacy design and execution gates remain independently enforced.
Review prerequisites do not block revision input to the already-owned worker,
but continue to block lifecycle advancement until verified resolution.

## Rollout configuration

For the currently deployed Hermes provider adapter, a configuration without a
named Fuel iX profile can use:

```yaml
review_gate:
  enabled: true
  model: fuelix/claude-opus-5-5
  provider: custom
  base_url: https://api.fuelix.ai
  api_mode: anthropic_messages
  api_key_env: FUELIX_API_KEY
  auto_approve: true
  max_attempts: 3
  signer: manager
  policy: manager
  operator: operator
  operator_policy: operator
```

The manager provisions the existing `beads.blockers` agent/human routes and
public-key policies for these names, and the secret variable in the supervisor
profile. A signing route without an explicit destination run uses the requesting
run's manager. Production enablement and deployment remain manager-owned. The
adapter records the configured model plus resolved provider, wire model and API
mode in the exact signed appraisal; it does not persist the credential value.
