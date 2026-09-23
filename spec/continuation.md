# Queue continuation at native boundaries

The supervisor performs one eligible-work check for a matched worker turn completion.
Native aborts, compaction, startup history, static panes and timer ticks are not pickup
triggers. Exact native/turn identity deduplicates notifier and transcript observations.
The boundary and consumed flag survive supervisor restart in the shared checkpoint.
This implements the boundary-only routing contract; it is not queue polling.
An exact current-turn completion remains a boundary when an earlier hook already
recorded that turn idle. Duplicate observations reuse the same boundary. Historical
or mismatched turns cannot establish it merely because the worker is idle.

Pickup must be enabled in both the harness and native BTQ state. Recovery holds,
paused pickup, uncertain/held worker input, pending direct steering, missing panes,
approval/question observations and conservative question detection hold continuation.
Manager reporting state `working` does not itself mean the native worker is busy:
the native turn must independently be idle. Blocked/failed/recovery run states
remain ineligible; reporting must not override those holds.
A fresh live observation is required after worktree switching. No native permission
is accepted, no approval dependency is removed and no claim is reclaimed by timeout.
Direct messages take precedence. Providers retain their existing native hooks.

At the boundary, inspect current native task ownership and approval/routing evidence.
Resume unfinished implementation before another task. A task awaiting review may
handoff only through the existing validated concurrent-review guard. If another
eligible task is selected, use the same `task_workspace.assign` path as the facade,
including atomic Beads claim, dependency/design gates, clean worktree boundary,
isolated task checkout and exact native resume. Claim while idle, before sending a
prompt: waking the worker first would make its own worktree-switch guard reject it.
Ready ordering is inherited from the shared queue; this feature adds no priority or
dependency-graph policy. Never automatically merge, deploy or close a task.

The existing durable inbox carries a bounded continuation naming the assigned task
and read-only facade. Context hooks supply its current projection. The claim's
ordinary revision notice remains pending until the started turn/context receipt can
retire it; do not paste both notices while waiting for that turn to start. A distinct
natural turn clears the loop guard. Every submitted continuation has a durable event.
Uncertain tmux delivery is retained for explicit reconciliation, never resent blindly.

Consume each boundary before external queue reads or writes. Empty queues and errors
are not retried on subsequent supervisor ticks; a new authorized native completion
is a new boundary. A reconciled status or assignee change of the bound task also
schedules one fresh check, distinct from its native completion. Revision-only changes
and duplicate projections do not schedule checks. The task check retains question,
pause, recovery, pending-input and unchanged-work guards; when necessary it waits
until the existing cooldown expires without querying Beads. Direct bare Beads writes
require explicit task reconciliation; there is no continuous task-status polling.
Each consumed boundary emits exactly one `queue_boundary` event with its outcome,
including guard, disabled and error outcomes. After a crash between consumption and
accounting, recovery records `interrupted-check` without repeating queue effects.
An earlier completion superseded before checking is accounted as such. Duplicate
native receipts keep their original completion identity after a task transition.
At most one continuation is attempted within 60 seconds. Consecutive
automatic turns with unchanged task scope and Git HEAD are suppressed even after that
cooldown; a natural turn, task change or committed progress permits continuation.
This intentionally favors a visible hold over an endless model/status loop. Operator
inspection is required after queue/claim/launch uncertainty. No timeout reclaim.

Question recognition is conservative, not semantic certainty: current native prompt
state, terminal selection hints and a question/waiting-for-operator pattern in the
last completion suppress input. Ordinary agents must still pause pickup before
asking for operator direction. Unknown native UI contracts are not an approval API.
The babysitter remains a separate safety net, not the authoritative pickup mechanism.
At an eligible idle observation it may inspect ready work at most once per minute.
Ready work stranded idle queues one standard resume nudge through the durable inbox,
then creates an operator escalation through the existing tagged-ask outbox. It never
claims a task. Nudge identity binds the owned pane and native turn; repeated detector
runs or lost local state cannot resend it. A changed turn retires stale queued input.
Delivery rechecks native idle, questions, approvals, recovery and both pickup pauses;
direct steering takes precedence. The detector's per-pane state deduplicates an
unchanged ready-set episode; failed escalation remains retryable. The existing
babysitter classifier resets the episode on observed activity or a prompt.
The externally installed babysitter is updated by a guarded backup/apply plan;
confirmed dead panes are restored by the supervisor, not by sending keys to them.

See [task contracts](tasks.md), [worktrees](worktrees.md), [native status](status.md),
[permissions](permission-relay.md) and [change lifecycle](changes.md).

Focused agent repair for structured faults: [babysitter resolution](babysitter-resolution.md).
