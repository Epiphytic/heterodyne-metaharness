# Durable sessions

Reuse provider adapters, the supervisor SQLite checkpoint and exact native hooks.
A fork source is an exact UUID used only for initial Codex startup. Recovery resumes
the registered successor exactly, never forks again or chooses latest. Compaction
updates native lineage while stable run, task ownership and Marmot binding persist.

All runs remain routable and reboot-recoverable when a task completes: task state
becomes completed and run state idle, including legacy/nonpersistent runs. New runs
default persistent=True; start --no-persistent explicitly opts out of that flag but
never authorizes terminal completion. Status events cannot close or reopen a run.
Only explicit close/stop with operator consent may enter a terminal state. Both
require a retained run/action-bound operator evidence record. Nonclosed workstream
Beads block unless --force and consent cover the exact outstanding set. Unresolved
operator asks and pending deployments block even force. Queue inspection failures
fail closed. Consent is a trusted local attestation, not proof of authorship; task
completion and general worker permissions are never operator closure consent.
Consumed consent cannot authorize a later closure after resume. Explicit stop still
stops processes after these guards; durable terminal state precedes process stops.
See `docs/workstream-closure.md` for the record and guard contract.
On reboot report the interruption and last checkpoint, restore conversations, pause
pickup, and reconcile effects before execution. Never automatically replay jobs.
Ongoing tasks report every 240 seconds, with a maximum five-minute interval under
healthy transport. Delivery failures remain durably visible and retry independently.
Preserve native approvals and uncertain submission outcomes; no automatic keystrokes.
See [maintenance ownership](maintenance.md).
