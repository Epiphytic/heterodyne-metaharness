# Delegated artifact reviews

Authority: [reviews](reviews.md), [gates](gates.md), [blockers](blockers.md).
Design: [artifact review gate](../docs/review-gate.md).

Opt-in `review_gate.enabled` admits artifact reviews through `workstream review
RUN submit --file PLAN`. PLAN contains key, issue_id, artifact_kind
(design/pr/escalation), artifacts (absolute path/SHA256 pairs), and optional
previous (an engagement with findings). Admission requires the current owner,
retained complete evidence and configured blocker policy/assignee routes.
Native external gate Beads carry category review and immutable artifact revision;
they cannot be claimed as coding work. They hold lifecycle advancement while
allowing revision input to the already-owned worker. Other blockers still apply.

Configuration selects model (default fuelix/claude-opus-5-5), auto_approve
(default true per operator policy until portable v1), max_attempts (default 3),
signer and policy (both default manager). Optional provider/base_url/api_mode/api_key_env configure
the reviewer adapter. Credentials are resolved through its provider profile.
The configured signer must be an agent route under beads.blockers.assignees;
its policy supplies the manager's authorized Nostr public key. Operator fallback
uses a human route named operator by default, configurable by operator and
operator_policy. No private key is supplied to the reviewer.

The independent tool-free runner returns pass/changes/escalate, a summary and
findings. Pass with findings is invalid. Attempts bind scope, complete evidence,
model and policy. Findings are durable worker inbox items; resubmission retains
old attempts and installs a replacement gate before removing its old edge.
Repeated findings at the attempt limit, explicit escalation and known runner
failure create an operator approval blocker. An uncertain running process
requires inspection, never automatic replay. Review gate model jobs and legacy
progress/completion jobs share the one-running-process-per-workstream guard.

On passing review with auto_approve=true, a durable signing request goes to the
configured manager. This executes delegated policy; it is not a fresh human
approval ask. `workstream review RUN receipt REVIEW --evidence-file JSON` receives
signed_receipt and decision_evidence. Existing BIP340 checks enforce the exact
run, scope, gate, revision, nonce, current signer policy and appraisal digest.
Verified receipts resolve the gate automatically. The full verdict and signed
approval receipt are posted through the existing durable group outbox. Delivery
retries do not invoke the model again. With auto_approve=false, passing review
routes to the operator approval blocker instead.

Artifact approval is distinct from execution and does not resolve separate
merge/deploy/native permission gates. Existing mandatory Claude human approval
and two-model ADR checks remain independent. Completion reviews retain their
exact native-boundary and delivery requirements. Raw gate closure, worker claims
and model text alone never establish approval.

For an explicit Fuel iX provider, configure `provider: custom`,
`base_url: https://api.fuelix.ai`, `api_mode: anthropic_messages` and
`api_key_env: FUELIX_API_KEY`. Provision that variable in the supervisor's secret
profile; values are never stored in review model.json or Bead metadata. Named
providers may instead resolve credentials and wire mode from their profile.
