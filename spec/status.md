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

Status production uses a durable twenty-notice window per run, recipient and kind,
including pending and delivered outbox events. Digests include semantic state
(run/native activity, recovery, manager presence, observed state and assigned Bead)
and content, excluding volatile pane hashes and native turn IDs. Real semantic
state transitions bypass prior-window matches, including returning to a previous
state. Progress strips only the harness Status/native-turn prefix, a trailing
ISO Observed-at annotation and whitespace; arbitrary command numbers remain intact.
Original delivered text is unchanged. This is deterministic normalization, not
natural-language equivalence detection.

Within unchanged semantic state, progress is admitted at most once per 300 seconds
regardless of wording or pane movement. Suppression does not slide the deadline;
at the boundary changed content can pass, but unchanged recent content remains a
duplicate. Other kinds (including distinct approval asks) are not progress-throttled.
Durable status_limits and status_notices update atomically with event/outbox admission
under the existing write transaction. Restart, pending transport failure and another
producer cannot reset those gates. Existing pre-upgrade history is retained; the
first post-upgrade event establishes its new semantic baseline.

Each suppressed event retains duplicate_status_error for identity replay protection,
but its ERROR journal message is emitted at most once per run/recipient/kind per
300 seconds, also persisted across restart. Replaying an event ID has no new effect.
Intentional idle suppression happens before production and is not an error. Existing
outbox retries retain their identities; no delivered checkpoint advances on failure.
Brain/task notices and reaction intents retain their own idempotency contracts.
No pane parsing approves commands or declares task completion.

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

## Dead worker recovery

A confirmed dead (not missing) owned worker pane in interrupted/failed state schedules
exact-session recovery after ten seconds, including on the same host boot or after a
supervisor restart. A recorded native ID is required; arbitrary command workers are
excluded. Consecutive recovery attempts retain an exponential delay capped at 300
seconds. The checkpoint retains the attempt count and due time before launching.
Existing resume/recovery holds and observed native approval/question prompts prevent
automatic recovery. Recovery restores only the conversation, preserves stable ownership,
holds pending input and disables pickup until effects are reconciled. A launch failure
remains visibly blocked under that hold, not an automatic retry of uncertain launch
or interrupted task effects. Missing panes still require explicit recovery.
