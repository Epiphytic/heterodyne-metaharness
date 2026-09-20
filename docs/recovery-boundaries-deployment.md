# Recovery and boundary accounting deployment

Contract: [continuation](../spec/continuation.md), [native status](../spec/status.md).
Task: `btq-harness-53ac6f7ea00122996fbb6709`.

This change retains the manager-working and idle-hook fixes from `3a8a891`.
It adds same-boot dead-pane recovery using the existing exact-resume path and
accounts for consumed boundaries even when queue access fails. Bound-task
status/ownership transitions schedule one guarded check, including after closure;
revision changes alone do not. Bare external Beads writes require explicit task
reconciliation. No new package or provider hook is required. The babysitter addendum needs the
scoped installer below in addition to the supervisor deployment.

## Operator deployment

1. Review the signed patch and merge through the existing Bead lifecycle. Retain
   the full post-merge test command, log and SHA256.
2. Coherently back up harness SQLite and checkpoints in private quarantine. Restart
   only the supervisor from the reviewed clean canonical revision. Retain the
   existing native manager/worker sessions and task ownership.
3. Verify services and exact native bindings. For controlled recovery verification,
   use an isolated owned test run or a separately authorized idle worker exit; do
   not kill a live task merely to exercise this feature. A confirmed dead worker
   with a native ID schedules after ten seconds and exact-resumes into a recovery
   hold. No task input is replayed. Record interruption/recovery events and retained
   conversation identity; reconcile effects before authorizing further work.
4. At a natural boundary verify one queue_boundary event, including when guarded.
   Close/reconcile the currently bound task normally; verify one fresh eligible
   check after any outstanding cooldown, without repeated queue reads on ticks.
   Pending approvals/questions, paused pickup, uncertainty and recovery still hold.
5. Record actual deployment evidence before closing this Bead or completing its
   historical decision. Fixture success is not a live recovery or reboot claim.

## Review limitations

Automatic recovery deliberately excludes missing panes, arbitrary command workers,
unregistered native sessions and existing recovery/approval holds. Failed restores
remain blocked for inspection rather than retrying uncertain effects. Recovery
scheduling is persisted; repeated eligible exits retain exponential backoff capped
at 300 seconds. Queue errors are accounted but not automatically retried.

Ripwire quality-delta reports nonzero structural findings, chiefly dynamic test
callbacks flagged as dead code, class size and short-horizon churn. No clean quality
gate claim is made. Regression tests exercise durable SQLite/checkpoints with fake
providers, while the full suite also includes isolated native sockets/tmux.

## Babysitter addendum

Prepare from the reviewed canonical revision (the plan contains source; keep it private):

```sh
python3 install_babysitter_queue.py --babysitter /home/operator/.hermes/workstreams/babysitter.py --output /tmp/53ac6f7-babysitter-plan.json
```

Apply with the existing backup mechanism, retaining its verified rollback manifest:

```sh
python3 install_brain.py --apply /tmp/53ac6f7-babysitter-plan.json --quarantine /home/operator/.local/state/hermes-quarantine/53ac6f7-babysitter
```

Use a fresh quarantine path if that directory already exists. Re-plan and confirm no changes.
Reload the existing babysitter through its operator-owned launcher; do not create a
second watchdog. This installer replaces only `nudge_idle_worker` and adds episode
resets beside existing idle-counter resets in `poll_pane`; unrelated behavior stays
intact. Review that exact diff before applying. The existing babysitter lifetime and
pane classifier remain constraints, including its configured expiry.

Verify with an isolated owned idle fixture and eligible queued task: one durable nudge,
one tagged operator escalation, repeated observation quiet, no claim by the detector.
Verify pending approval/recovery/pickup pause holds delivery, a new native turn retires
the old nudge, and direct steering precedes automatic input. No live model turn or
operator message is claimed by fixture results. Ready reads and ask enqueue must run
outside the shared harness lock; only owned inbox insertion is locked.
