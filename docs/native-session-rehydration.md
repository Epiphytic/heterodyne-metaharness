# Restore retained native identity (btq-98q)

The supervisor can restart with a live pane and a missing native_session_id in
runs.data, even though native_sessions retains its registered identity. Task
switching then correctly refuses to resume an unidentified worker.

Before recovery intents or observation on each active supervisor tick, restore
only missing identities from native_sessions using the exact run_id and role.
For each existing worker/manager/secondary slot, follow the registered previous_id chain to its unique terminal successor.
Every record must belong to that complete, acyclic chain. Competing roots, missing
predecessors or cycles leave identity absent; timestamps never select a session.
Never borrow another run or role, create a missing slot, or replace an identity
already present. Empty registry identities cannot authorize restoration.

Persist a restored identity to runs.data and checkpoint before subsequent tick
operations. Registry restoration is identity repair only: it does not clear
recovery/pickup holds, manufacture an idle boundary, replay commands, or change
approval policy. All guards in spec/sessions.md and spec/worktrees.md remain.
A missing or ambiguous registration continues to fail the existing switch guard.

Regression coverage uses durable SQLite and fake panes across supervisor restart:
restore the registered successor worker identity and resume that exact identity on a safe task
switch; retain current identities and holds; isolate runs and roles; reject ambiguous lineage
and absent records. No live service operations are required.
