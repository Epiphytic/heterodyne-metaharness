# Workflow routing and progress

Authority: [formulas](formulas.md), [gates](gates.md), [handoffs](handoffs.md),
[continuation](continuation.md), [delivery ownership](delivery-tasks.md).

Formula roles inherit the chosen existing workstream's agent, workstream and stable
session route through ordinary idempotent task admission. Parent ownership does not
reserve every step for its owner. No new writer identity or native session is created.
The facade continues to use installed BTQ routing/design policy and atomic native
claims. Before first or resumed step execution, all predecessor steps must be closed
with valid retained artifacts. Partial groups, missing edges and invalid predecessor
evidence fail closed even when a native task is already assigned. Both persisted
pickup pauses and recovery holds take precedence over claim acknowledgment.

Step closure releases only that step and retains its artifacts. Existing closure
performs exactly one ready-work check; continuation runs only at its existing
[authorized boundaries](continuation.md). This change adds no polling, timeout reclaim,
blind claim-next, automatic merge or extra model turns. Independent implementation
may proceed while another group's review/deployment remains open. Existing worktree,
concurrency and Claude design guards remain in force. Raw bd does not enforce all
harness evidence rules; use the workstream facade for workflow execution.

`workstream task RUN progress` is a bounded read-only cached projection: no Store
initialization, checkpoint writes, queue mutation or native database access. It labels
its source and reports eligible=null; apparent readiness is never claim authority.
Snapshots may be stale or missing after bare bd writes. Task reconciliation updates
individual snapshots; `progress --live` reads current Beads without updating snapshots.
Live eligibility reuses facade policy, native ready, route, approval and recovery checks.
It does not reserve work; a concurrent claim may change eligibility immediately.

Rows include ID/title/native status, assignee, workflow role/parent, pinned artifact,
blocking IDs, and one category: ready, executing, awaiting-review, awaiting-approval,
awaiting-deployment or blocked. Verification/integration awaiting execution share the
awaiting-deployment category and retain their exact role. Unresolved gate/design edges
are awaiting-approval; closed approval records in cached views are not proof of valid
approval evidence. Missing dependency snapshots and invalid contracts are explicitly
blocked. Closed tasks are omitted; other-session routes are excluded.

Limits: 200 displayed tasks, 5000 cached records scanned, 200 native list/ready rows
and 800 native dependency reads. Overflow fails explicitly, never silently truncates.
Native reads use installed BTQ credentials and fixed database selection. The installed
Beads 1.1.0 mol progress API returns schema_version, molecule_id/title, total, completed,
in_progress and current_step_id; ungrouped tasks have zero children. Those aggregate
counts are insufficient for approval/role/evidence eligibility, so the facade derives
its categories from native task dependencies and versioned group contracts instead.
