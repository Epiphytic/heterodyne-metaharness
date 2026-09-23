# Signed blocker Beads

Authority: [gates](gates.md), [operator asks](operator-asks.md),
[native permissions](permission-relay.md), [worktrees](worktrees.md).

Opt-in `beads.blockers.enabled` extends native gate Beads, not the ordinary task
lifecycle. `task RUN blocker create --file PLAN` uses the gate plan fields plus
`category`, `assignee: {type,id}`, and a configured `policy` name; optional
`supersedes` replaces an earlier same-kind gate explicitly. Categories are approval,
question, access-validation, external-result and pr-review. Assignee types are human,
agent, worker and external. Assignment never grants approval authority. The typed
assignee is retained in `harness_gate.blocker`; gate Beads are not worker claims.

The blocker is embedded in the existing immutable parent/gate binding, with run,
repository, nonce and policy snapshot. Parent admission, steering, stage recording
and closure validate native blocking edges, retained artifacts and signatures.
Known gate metadata accepts either an object or one JSON encoding. Double encoding,
malformed values and duplicate keys fail closed. Other metadata is not normalized.
Legacy tasks and workflow formula versions remain unchanged.

Policies specify a revision, allowed lowercase Nostr public keys, optional revoked
keys, event kind, gate kinds and authority_basis. Merge and deployment require an
operator policy. Policies and routes come from operator-controlled config, never
from task text. Policy changes require explicit new scoped gates. Superseded bindings
retain history but no longer govern admission.

`blocker request GATE` returns the exact decision envelope. `blocker receive GATE
--evidence-file FILE` retains `signed_receipt` and `decision_evidence` in
`harness_blocker_decision`, including phase approved, resolved (answer), or denied.
It does not close the native gate. A conflicting receipt requires a replacement gate.
`blocker resolve GATE --evidence-file FILE` additionally validates existing gate
evidence, writes its resolution and closes it. It accepts receipt+evidence together
or an exact previously received decision. Denial and raw native closure never grant
eligibility. Resolving a question requires a signed answer.

Receipts are complete NIP-01 events with real BIP340 verification using the optional
pinned requirements-blockers.txt dependency; missing verification support fails
closed. Content binds schema/domain, run/repository, gate/parent, scope, revision,
kind, nonce, policy revision, decision, npub, evidence_sha256, issued_at/expires_at.
The evidence digest is SHA256 of sorted compact UTF-8 JSON, with string keys,
integer/string/bool/null values and arrays/objects; floats are excluded. Input is
bounded to 64KiB and canonical nesting to 20. Event serialization follows NIP-01.
Expiry and revocation are rechecked on admission, not only ingestion; an expired
resolution requires an explicitly superseding fresh decision.

Decision evidence and native effect evidence are separate. Access-validation
resolution requires native_effect_ref to name a retained artifact verified by the
existing digest contract. This is a trusted manager attestation of inspection and
effect, not authentication of native execution. This extension never types approval
keys, changes permission policy or turns approval_policy=never into an exemption.
The existing native inspection/live-test prerequisite continues to apply.

Human asks and routes marked via=marmot use operator_asks and its existing bound
channel/reminder machinery. Agent/worker/external routes dispatch one deduplicated
inbox item to a registered run and manager/worker destination. No new workstream is
created. Original signed receipt ingestion is explicit: connector events lacking
required original fields, and ordinary reactions, cannot resolve these blockers.
Ask closure follows verified gate closure; partial closure can retry exactly.

See [PR watchers](pr-watch.md) and [secondary workers](secondary-workers.md).
