# Transition delivery

Authority: [tasks](tasks.md), [workflow routing](workflow-routing.md),
[status](status.md), [operator asks](operator-asks.md), [entrypoint](README.md).

Native Beads remains task authority. The harness journal is a delivery projection,
not another queue. Facade observations compare native status, execution-relevant
scope and retained lifecycle digests. Initial observation establishes a baseline;
a verified claim emits one complete Bead per stable worker/task. Reconcile after
external bare `bd` writes; changes never observed cannot be reconstructed from a
final snapshot. Retained lifecycle entries are replayed in their recorded order.
There is no native task polling or model in detection.

Each run/recipient uses fixed windows starting at the first transition and ending
120 seconds later. Later arrivals do not slide the deadline. Preserve every
transition, including returning to a previous status. After expiry, atomically
queue the ordered batch into the existing FIFO outbox. Restart retains both the
window and transport identity. A failed send retries exactly that identity.
No group binding means an inspectable unqueued batch, never a guessed recipient.

Each transition starts with exactly `<bead title>, <bead status>, <bead id>`.
Whitespace in titles is collapsed to keep one line. Native status strings are
retained; lifecycle stages retain their names except `pr-open` becomes
`review-requested` and `deployed` becomes `live-verification-passed` after its
existing evidence gate. Native process stops use distinct `agent-crashed`,
`agent-interrupted`, or explicitly evidenced `agent-usage-limited` notices; they
never mutate Bead status. Turn completion alone is not task completion.

First handoff includes the complete serialized Bead. Steering and gate resolution
use separately typed deterministic JSON immediately after their status line,
keeping question/steering and subject/actor/authority together. Observed consent
is not applied approval. Existing gate resolution records supply actual issuer
and authority; no actor is inferred from terminal prose. Native approval systems
without a confirmed resolution record must not claim applied approval.

Large messages use deterministic numbered chunks with no discarded content.
Transitions have immutable journal identities; legitimate return transitions
bypass the legacy identical-text delay. Existing ask rendering and permission
message bindings remain authoritative. Independent reviewer scheduling and scoped
reaction/ask policy belong to separate Beads; no new reviewer or approval executor
is introduced here.

Bound task heartbeat polls and model turn summaries remain local evidence, not
channel statuses. Only positively identified unsent, never-attempted and never-
rendered old harness heartbeat rows may be retired. Keep their original events
and explicit retirement records; retain uncertain, permission and arbitrary rows.
Tables `transitions`, `transition_batches`, `transition_heads`, and
`transition_retirements` expose pending delivery and retirement history.

`workstream notice NAME record --file PATH` accepts retained actual-applied
approval evidence or explicit crash/usage-limit evidence for the current routed
Bead. Require issue_id, kind, immutable key, subject, actor, authority_basis,
evidence_ref, and result=applied for approval-applied. This records attribution,
not authentication of arbitrary strings; it never executes native approval.
`notice NAME inspect` exposes bounded batch and retirement records. Native gates
emit directly; native approval paths without a resolution callback use this
explicit ingress after exact inspection. Consent alone fails the applied gate.
