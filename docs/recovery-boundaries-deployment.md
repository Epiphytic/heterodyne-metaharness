# Recovery and boundary accounting deployment

Contract: [continuation](../spec/continuation.md), [native status](../spec/status.md).
Task: `btq-harness-53ac6f7ea00122996fbb6709`.

This change retains the manager-working and idle-hook fixes from `3a8a891`.
It adds same-boot dead-pane recovery using the existing exact-resume path and
accounts for consumed boundaries even when queue access fails. Bound-task
status/ownership transitions schedule one guarded check, including after closure;
revision changes alone do not. Bare external Beads writes require explicit task
reconciliation. No new package, provider hook, or runtime configuration is required.

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
