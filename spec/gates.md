# Revision-bound prerequisite gates

Authority: [task admission](tasks.md), [formulas](formulas.md),
[delivery ownership](delivery-tasks.md), [native approvals](permission-relay.md).

`workstream task RUN gate create --file PLAN.json` attaches a native Beads gate
and blocking edge to one routed task. PLAN contains key, issue_id, kind (design,
merge, deployment, external), revision (exact ADR/artifact revision), reason and
issuer. Attach gates to the independently owned formula role that needs them.
No active task is silently converted to a formula or released from ownership.

IDs derive from stable run/task/request key. A native metadata reservation precedes
creation so partial admission fails facade eligibility closed. Exact replay repairs
missing gates/edges; changed requests under the same key fail. Gate issues are not
coding tasks. No uncertain non-idempotent create retry, DB migration or polling.

`gate resolve GATE --evidence-file JSON` requires issue_id, kind, revision, scope
(the gate's captured scope digest), issuer, authority_basis, result=passed,
evidence_ref and retained artifacts (absolute path/SHA256 pairs). Authority basis
is operator, operator-reaction or verified-external; merge and deployment require
operator authority. These are trusted evidence records, not identity authentication.
Keep actual review/merge/deployment or external deliverable evidence in artifacts.
Use explicit Radicle evidence, never GitHub watcher substitution. Gate resolution
neither merges nor deploys nor grants native tool approval.

Design additionally requires the existing closed design_approval Bead, native
blocking edge, matching adr_revision, separate Liam approval timestamp, and the
required two-model ADR evidence. Existing queue design guards remain in force.
An unrelated/stale approval or mere raw gate closure cannot authorize execution.

Scope includes requirements, notes, acceptance, design/spec, routing labels,
non-lifecycle metadata and dependency identities/types. Assignee, timestamps,
dependency status and execution evidence are excluded. Source changes to approved
scope invalidate resolution. Explicit revalidation uses a new key/revision and
optional supersedes=OLD_GATE_ID of the same kind. The replacement native edge is
installed before the old edge is removed; original gate and evidence remain intact.
The old gate may remain open as historical superseded evidence. No automatic acceptance.

Ready, claim, lifecycle-stage and closure facade boundaries recheck managed gates,
resolution digests, current scope and retained artifacts. `gate check ISSUE` performs
an explicit bounded check. Resolving an event updates the shared task projection;
no model invocation, auto-claim or continuous polling is introduced. Raw privileged
bd access can bypass facade policy; the facade is not a security boundary against
an operator editing metadata. Native dependencies still block ordinary ready work.

Beads 1.1.0 supports gate issue type and gate resolve. Its gate create lacks a stable
caller ID, so admission uses native create --id --type gate plus dep add. Await
conditions live in revision-bound harness metadata; native timer/GitHub watchers
are unused. The native cross-rig bead checker reports unsupported; external owners
supply retained revision-specific evidence through this explicit resolution path.
