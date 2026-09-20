# Queue order and explicit interruption

Authority: [tasks](tasks.md), [worktrees](worktrees.md), [continuation](continuation.md).
Beads owns task status, routes, dependencies, claims and queue-order metadata.

Default pending order is FIFO by native created_at, then exact ID. Native ready,
route, design and pause filters apply before sorting. All harness ready consumers,
including the single completion-boundary check, use this same order. Explicit
claim ID remains an intentional selection, not an automatic claim-next command.

`task RUN create --at-top` inserts ahead of pending tasks, never interrupts a claim.
Its placement option belongs to the immutable create fingerprint; replay does not
move an already-placed task again. `prioritize ID_OR_KEY ... --issuer RECORD`
moves distinct pending unassigned tasks to the front in argument order. Keys use
exact facade create-key derivation, never prefix or title matching. Per-task native
metadata records a serialized batch epoch/index and caller issuer. Unmentioned
pending tasks retain relative order; priorities and dependency edges are unchanged.

The shared harness lock serializes facade ordering changes. Native writes are not
retried inside the facade. A failed multi-task ordering write may leave a prefix
applied: inspect native metadata, then explicitly repeat the intended ordering.
This is not a distributed transaction against arbitrary external bd writers.
Issuer records and native worker actors provide audit context, not authentication.

`ready --all` returns routed unclaimed open tasks in the same order, with native
eligibility and transitive open blocking chains. Non-blocking relations are not
blockers. Views reject cycles, missing dependency records and oversized graphs.
`dep add DEPENDENT PREREQUISITE --issuer RECORD` and `dep remove ...` use the
installed BTQ transport and native cycle checks; both endpoints must match the
route. Removing a recorded design-approval edge is refused. The request record
precedes mutation and is not proof of success. Reconcile after uncertain outcomes.
The updated full task snapshot, including dependency records, is available through
read-only show; actual claim still rechecks current native gates. External bd edits
require explicit reconcile, as described in [tasks](tasks.md).

`drop-everything ID_OR_KEY --issuer RECORD --reason TEXT` requires enabled pickup,
a clean confirmed idle native boundary, no approval/recovery hold, verified current
ownership and an owned per-task checkout. It refuses busy or dirty workers without
stopping them; retry at a safe boundary after resolving those conditions. The target
must already pass native ready/design/dependency checks. Other retained claims must
be valid review handoffs or explicitly parked owned checkouts.

Interruption puts the target first, checkpoints the old claim's commit/path/reason,
records an interruption note in native Bead metadata and an audit event, then uses
the existing atomic claim and exact-session worktree switch. The old claim remains
in_progress under the same owner; parking does not close, release or authorize a
second writer. A partial handoff leaves the parked checkpoint and requires explicit
reconciliation; automatic continuation never resumes a currently parked task.

After the urgent task closes or reaches validated review handoff, `claim OLD_ID`
resumes the retained parked claim in its original verified clean checkout and marks
the park record resumed. Existing pause/resume controls still govern pickup; this
operation does not clear a pause. Changed parked Git state/ownership fails closed.
No tool cancellation, native prompt acceptance, timeout reclaim or task replay occurs.
