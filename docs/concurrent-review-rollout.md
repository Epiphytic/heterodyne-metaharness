# Concurrent review and deployment-stage rollout

Current contracts: [worktree spec](../spec/worktrees.md) and
[change lifecycle](../spec/changes.md). This is an operator rollout checklist,
not evidence that deployment has happened.

Before integration, retain a coherent private harness SQLite/checkpoint backup
and inspect open Beads' `harness_lifecycle` metadata. Existing closed Beads remain
closed. Open histories that already contain the former `close-ready` stage need
explicit evidence reconciliation before using the new stage order; never insert
invented deployment evidence or erase prior history to pass a gate.

Review and merge the signed branch through its existing private Radicle patch.
Run the complete suite from the merged repository with `PYTHONPATH` pinned to
that repository and managed identity environment variables removed. Retain the
command, result, exact commit and SHA256 of its log. Restart the supervisor only
under operator deployment authorization; retain the original worker and manager
identities. No dependency or native approval configuration change is required.

Verify the loaded revision and service health. At a real idle task boundary,
verify a retained task with tested/pushed review evidence remains claimed while
a second eligible task is assigned its own worktree. Confirm exact ownership,
branch/cwd, unchanged native session identity and recovery guards. Do not create
a synthetic model workload for this check. The next authorized FIFO task can
provide the live boundary evidence after the current implementation is ready.

Record actual merged, final-tested and deployed evidence through the task stage
facade. Deployment evidence must include the tested revision, target and retained
live-verification reference. Neither this document nor a passing fixture suite
constitutes deployment evidence. The permissions transport design remains a
separate unresolved part of Bead 259f; this slice does not complete that Bead.
