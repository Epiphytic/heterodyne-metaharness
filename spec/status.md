# Native activity and status notices

Authority: [sessions](sessions.md), [entrypoint](README.md).

Native turn completion is not Bead completion. Exact native Stop/post-turn hooks
record idle for their matching turn; Codex task_complete and turn_aborted records
also record idle. Newer turn identities reject older completion/start evidence.
Compaction retains stable ownership. An unknown or quiet pane never proves idle.
Supervisor observation runs every ten seconds; backlog parsing remains bounded.
Hooks update the checkpoint at the next native lifecycle boundary without polling
models. Approval and reboot recovery constraints take precedence over idle state.

When the worker is idle, no manager turn is running, and no input is pending,
unchanged idle heartbeat content is not produced. Changed content or transitions
produce a notice promptly. A bound unfinished Bead may wait idle; idle does not
close it or authorize pickup. During ongoing or unknown activity a fresh timestamped
observation is produced at most 240 seconds apart, even if no progress is visible.
The timestamp explicitly denotes observation, never invented task progress.

Status production hashes observable run/turn state and exact content. The previous
queued/delivered status per run and group is retained durably. Producing an identical
new notice records duplicate_status_error in the event store and ERROR on stderr,
and suppresses the extra outbox row. Replaying the same event ID is idempotent and
is not a new duplicate fault. Intentional idle suppression happens before production
and is not an error. Pending notices remain retryable with their original identities;
failed delivery never advances a delivered checkpoint. Transition sequences are
preserved (A, B, A is valid). Brain/task notices and reaction intents have their own
idempotency contracts. No pane parsing approves commands or declares task completion.
