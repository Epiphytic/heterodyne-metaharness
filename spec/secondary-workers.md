# One retained secondary worker

Authority: [signed blockers](blockers.md), [sessions](sessions.md),
[worktrees](worktrees.md), [task admission](tasks.md).

`beads.blockers.secondary_enabled` explicitly enables one additional owned coding
slot within the existing run. `task RUN blocker secondary-start ISSUE` requires a
confirmed-idle primary native session, clean primary checkout, unresolved signed
blocker, unchanged primary ownership, independently eligible task and active pickup.
Busy, dirty, native-approval, stopped or recovery states fail closed. This first
profile does not suspend a running command or continue around an unresolved prompt.

The existing per-task worktree preparation and atomic claim checks apply. Persist
claim/launch intent before effects. Keep primary claim, queue current pointer,
worktree and native session unchanged. Secondary uses its own tmux name, checkout,
registered native identity and worker-role hooks; Claude's native UUID is distinct.
Provider must match the enrolled coding agent. Operator-configured secondary_provider
supplies launch arguments/environment; existing capability policy applies. This is
not the permission-manifest implementation assigned to the follow-up Bead.

A blank secondary session requires explicit authorized `send --target secondary`
steering after its native identity is registered. It receives its own task snapshot.
Stage and review commands address its owned task checkout explicitly. Primary
steering/worktree switching stays blocked while the secondary slot is retained.
Manager inbox items can still be delivered while worker input is blocked.

`blocker secondary-finish` requires the secondary's ordinary lifecycle to be closed
by its owner, a confirmed idle native turn and clean checkout, plus satisfied primary
gates under current policy. It stops only the exact owned secondary pane, retains
its record, and makes the unchanged primary available for explicit steering. Receipt
arrival does not automatically type commands or resume an interrupted action.

Missing panes or uncertain claim/launch results retain the slot and error for
manager reconciliation. Repeated start refuses to adopt them. Stop covers both
workers; recovery holds both and never automatically replays secondary work.
No timeout reclaims, new workstream, broad permission fallback, deployment bypass,
or change to ordinary one-worker admission is introduced.
