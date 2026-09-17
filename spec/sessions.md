# Durable sessions

Reuse provider adapters, the supervisor SQLite checkpoint and exact native hooks.
A fork source is an exact UUID used only for initial Codex startup. Recovery resumes
the registered successor exactly, never forks again or chooses latest. Compaction
updates native lineage while stable run, task ownership and Marmot binding persist.

A persistent run remains routable and reboot-recoverable when a task completes:
its task state becomes completed and its run state idle. Explicit stop still stops it.
On reboot report the interruption and last checkpoint, restore conversations, pause
pickup, and reconcile effects before execution. Never automatically replay jobs.
Ongoing tasks report every 240 seconds, with a maximum five-minute interval under
healthy transport. Delivery failures remain durably visible and retry independently.
Preserve native approvals and uncertain submission outcomes; no automatic keystrokes.
See [maintenance ownership](maintenance.md).
