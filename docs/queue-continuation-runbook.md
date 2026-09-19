# Queue continuation review and deployment

Normative behavior: [spec/continuation.md](../spec/continuation.md).
Implementation: [harness/continuation.py](../harness/continuation.py), native notify
and lifecycle integration, existing Beads facade/worktree handoff and durable inbox.
No additional package, plugin, hook trust, native permission or database migration.
Per-run checkpoint fields carry the new state; retained native/inbox history is intact.

Operator deployment, after signed commit/full suite/private patch review:

1. Back up the harness SQLite database coherently and retain checkpoint snapshots in
   private quarantine. Preserve original worker/manager native identities and Beads.
2. Merge the reviewed branch into canonical main. Verify canonical index AND worktree
   are clean at the reviewed SHA before restarting `hermes-workstreams.service`.
   A ref-only fast-forward with a stale worktree is insufficient.
3. Confirm daemon cwd is canonical, MainPID changed, service is active and facade reads
   respond. No gateway restart, package installation or model replay is required.
4. Allow the next natural worker completion with pickup enabled. Check exactly one
   `queue_boundary` event, and one `queue_continuation_sent` event if eligible. Confirm
   the matching inbox row is submitted, the bound Bead has the same verified owner,
   and original native identity remains unchanged. Do not manufacture a task close or
   a receipt, or replay a workload to make this check pass.
5. An empty queue or paused/prompt/recovery condition must not send input. Ordinary
   ticks must not repeat the queue check. Observe a later authorized natural boundary
   for subsequent work. Actual next-task handoff should retain an isolated Bead checkout
   and exact native resume. Keep lifecycle completion pending until live evidence exists.

Unit fixtures cover queue failures, wrong owner, native BTQ pause, empty queue,
restart/dedup, pending prompts, direct steering, cooldown, unchanged-work loop hold,
natural turn reset, native abort/stale completion and real isolated Git worktree
handoff with fresh-pane gating. No live Claude model turn or physical reboot is claimed.

Quality review is not a clean gate: the measured supervisor complexity regression was
removed by extracting readiness/boundary operations; remaining tool findings include
unittest fixture dead-code detection, fixture similarity/size, superclass size and
short-horizon churn. The original sandbox suite timed out in socket-dependent fixtures;
the external isolated suite is authoritative. Test counts and immutable log hashes are
recorded in the Bead lifecycle evidence, not inferred from this runbook.

Rollback: pause pickup through the facade, retain any uncertain inbox/claim state for
inspection, restore the reviewed prior source and restart the supervisor. Do not delete
claims, rewind receipts, or automatically replay a continuation. New run JSON fields
are additive; the prior implementation ignores them.
