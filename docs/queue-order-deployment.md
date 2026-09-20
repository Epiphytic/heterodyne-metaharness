# Queue controls deployment

Contract: [queue order](../spec/queue-order.md), [worktrees](../spec/worktrees.md),
[task lifecycle](../spec/changes.md). No new package or native model hook install.

Review and merge the signed task branch through the private Radicle destination.
Retain a coherent harness SQLite/checkpoint backup and existing Beads backup before
operator deployment. Integrate the reviewed commit into the canonical checkout;
verify a clean tree and restart hermes-workstreams.service through the operator.
Verify its executable/cwd and responsive list, without replacing manager/worker
native identities. Do not run drop-everything against this implementation task.

At a safe boundary, use explicitly authorized disposable routed tasks to verify:

1. Create two default tasks and one with --at-top; ready lists the latter first,
   then FIFO. Repeat its original create key/body/options: position is unchanged.
2. Prioritize the two exact IDs in reverse order with --issuer manager; ready and
   the next authorized boundary selection agree. No claim occurs on prioritize.
3. Add/remove a native blocking edge through dep with --issuer manager; ready --all
   reports chains, ordinary ready excludes blocked tasks, and show after mutation
   exposes full dependency records. No raw bd credential/config change is needed.
4. For separately authorized interruption testing, first commit the active test
   task and confirm native idle, no pending approval/recovery/pickup pause. Invoke
   drop-everything TARGET --issuer manager --reason ACTUAL_REASON. Confirm retained
   old claim, interruption metadata/event, checkpoint commit/path and target claim.
   The existing worker exact-resumes in the target checkout. Finish the urgent
   fixture through its applicable lifecycle, then claim OLD_ID and verify original
   checkout/native identity/context retained. Do not fabricate completion evidence
   for production tasks to create a test boundary.

Busy/dirty/pause/recovery rejection and uncertain-claim recovery are covered by
isolated fixtures. Live testing must not interrupt tools or approve prompts.
If any interruption write or switch is uncertain, pickup remains disabled with
recovery_required; inspect both native claims/checkpoints/worktrees and use the
existing recover workflow. A partially applied prioritize batch is visible on
native metadata: inspect then explicitly reissue intended ordering, never retry
an uncertain task creation with altered content.

Do not roll back to older code with parked claims active: older guards do not
understand this explicit handoff. Reconcile parked claims with the new code first.
Preserve all task branches, evidence and backups. No live deployment or physical
reboot is claimed by this document; execution evidence follows operator checks.
