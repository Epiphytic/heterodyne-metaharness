# Durable workstream supervisor

Accepted by user on 2026-09-16: implement the reviewed repair plan, add reboot and compaction recovery, modular agent adapters, idempotent scripts, and Hermes SOUL policy.

Use Python standard-library SQLite for transactional run state, inbox deduplication and an acknowledged outbox; systemd user service (with lingering) for boot/start/restart; tmux for interactive agent terminals. Do not implement another queue engine or model runtime. Keep Marmot and terminal/agent protocols behind adapters.

One project has one durable group mapping. One run owns a coding-agent session, separate Hermes manager session, and isolated worktree. The manager steers; the supervisor owns launching, timers, delivery and lifecycle. CLI and gateway routing use the same durable database. Model compaction cannot discard ownership or reporting deadlines.

A boot ID change creates a durable interruption report with the previous observation and exact native session IDs. Reopen native sessions without replaying the task or interrupted commands; if the native session was not yet recorded, reopen a blank terminal and retain the recovery checkpoint. Recovery requires explicit steering to resume work. Never infer completion from idle. Unknown state remains reportable.

Use stable request identities where the remote API supports idempotency. An uncertain group-create or terminal-submit outcome is retained for reconciliation rather than automatically replayed. Delivery is at-least-once with stable server idempotency keys; do not claim end-to-end exactly-once. Retry messages independently of model turns. Check active tasks every 10 seconds and queue a report every 240 seconds (headroom below five minutes); outages are exposed and retried, not hidden.

No approval keystroke automation; native agent permission settings are explicit adapter arguments. No automatic worktree deletion, group leave, or original command replay on reboot. Keep stopped/completed projects resumable and preserve work/history.

Legacy manifests are imported as interrupted records without resurrecting stale tasks automatically. Live new managed runs recover at boot. No host reboot is required for verification: simulate boot ID changes and use isolated real tmux sessions plus mock protocol servers.
