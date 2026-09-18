# Native activity and status notices

Authority: [sessions](sessions.md), [entrypoint](README.md).

Native turn completion is not Bead completion. Exact native Stop/post-turn hooks
record idle for their matching turn; Codex task_complete and turn_aborted records
also record idle. Newer turn identities reject older completion/start evidence.
Compaction retains stable ownership. A missing or empty pane never proves idle.
Supervisor observation runs every ten seconds; backlog parsing remains bounded.
Hooks update the checkpoint at the next native lifecycle boundary without polling
models. Approval and reboot recovery constraints take precedence over idle state.

When the worker is idle, no manager turn is running, and no input is pending,
unchanged idle heartbeat content is not produced. Changed content or transitions
produce a notice promptly. A bound unfinished Bead may wait idle; idle does not
close it or authorize pickup. Changed observations deliver immediately. Unchanged active observations attempted
at the 240-second interval are duplicate errors and suppressed. The interval is
not permission to repeat unchanged content. Observation timestamp suffixes are
removed; annotations and display labels must not manufacture content changes.

Status production hashes observable run/turn state and exact content. The previous
queued/delivered status per run and group is retained durably. Producing an identical
new notice records duplicate_status_error in the event store and ERROR on stderr,
and suppresses the extra outbox row. Replaying the same event ID is idempotent and
is not a new duplicate fault. Intentional idle suppression happens before production
and is not an error. Pending notices remain retryable with their original identities;
failed delivery never advances a delivered checkpoint. Transition sequences are
preserved (A, B, A is valid). Brain/task notices and reaction intents have their own
idempotency contracts. No pane parsing approves commands or declares task completion.

## Pane reporting fallback

Codex user-level `notify` uses `bin/workstream-notify` for native
`agent-turn-complete` receipts. The exact current worker native ID, owned cwd
and active turn key must match before idle is applied. Old/forked identities
cannot clear a newer turn. The shared supervisor lock serializes receipts and
observation; replayed lifecycle evidence older than the receipt cannot reopen
that turn. Receipts retain turn, timestamp and assistant-message SHA256, never
message content. Duplicate receipt IDs have no second effect. Matching receipt
sets native activity and stale working task state idle, without closing a Bead
or clearing recovery/approval requirements. Unmatched turns are retained as
unapplied evidence; existing lifecycle and pane detection remain the fallback.

Installation changes only user config `notify`, preserving an existing command
as a bounded chained invocation with the original payload, including when the
harness handler fails. No trust/approval setting changes. Failures log only their
class and do not fail the native turn; a busy supervisor lock defers to fallback.
The callback does not acknowledge unseen task/brain notices or submit terminal
input. A new native process must load the reviewed config through exact resume.

Each observation reuses the owned tmux capture (visible pane plus 200 history
lines). SHA256 of its text is persisted as pane_digest, with pane_unchanged_ticks
and pane_stopped. Two consecutive identical nonempty observations (one equal
comparison) classify reporting as stopped despite stale task/native working state.
Only elapsed digits/units in the Working (... • esc to interrupt) chrome are
normalized; arbitrary output numbers and clocks are preserved. New content resets
the counter and delivers promptly. Empty/dead/missing captures do not prove idle.

A repeated progress notice for the same stopped pane and recipient is suppressed
and error-logged with its pane digest even if parsed state changed. Subsequent
unchanged idle ticks produce no notice. Persisted fields survive supervisor restart
and are included in the full run returned by status. Pane quietness is a reporting
heuristic: silent tools may still run. It never updates native turn keys, authorizes
input/worktree switching, dismisses approvals, or closes Beads. Pending approvals,
recovery, manager activity and queued input retain their guards.
