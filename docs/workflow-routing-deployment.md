# Workflow routing deployment

Normative contract: [workflow routing](../spec/workflow-routing.md).
No new packages, hooks, schema upgrades or second task database. Manager merges the
reviewed signed revision, retains the post-merge suite log/hash and restarts the
existing supervisor from the clean canonical checkout.

Verify `workstream task hermes-maintenance progress` works in the worker sandbox
and reports cached-projection. Run the same command with --live using authorized
operator access; it must perform reads only and preserve all task ownership/state.
Retain sanitized outputs and running revision. Native Beads 1.1.0 mol progress was
inspected read-only on the bound ungrouped task and returned schema_version=1,
total=0, completed=0, in_progress=0, current_step_id empty; no molecule creation claimed.

Production dogfood requires an explicit new formula plan, not migration of the active
legacy claim. Admit a small real research-v1 workflow to validate the live routing:
use a unique persisted key and a description naming the actual investigation, routes
for investigation/recommendation to existing authorized workstreams. Replay the exact
plan and verify identical member IDs. Attach an external gate only if there is an actual
external prerequisite; do not manufacture an approval fixture in production.
At an authorized boundary claim investigation using the normal facade. Record actual
findings in an immutable retained artifact, record investigated, and close that step.
Confirm parent stays open and recommendation becomes eligible; inspect progress from
its routed session. Recommendation records its real independent assessment and prior
evidence digest, then closes. Parent verifies retained evidence and closes. No test-only
assignees or forced worktree switching. Manager schedules these real work steps after
current closure; source fixtures alone do not establish production dogfooding.

Regression coverage exercises formula admission, independent completion, retained
predecessor validation, paused idempotent claims, read-only views and missing snapshots.
Existing installed-BTQ tests cover native atomic ownership and both provider design
contracts. No physical reboot or production formula execution is claimed by this file.
