# Live worker provisioning

The supervisor renders each worker launch through `harness.provisioning.apply`.
The same renderer handles initial launch, exact-session resume, and re-apply.
Codex cwd flags follow the owned task checkout and writable roots are deduplicated.
Re-applying an unchanged configuration leaves the process and event log alone.

Each tick compares the owned pane's `/proc/<pid>/cmdline`, configured environment,
and working directory with the current rendered launch specification. Changes to
stored per-run configuration or to global launch policy become a pending update.
Changing `provisioning_revision` in `harness-config.json` also requests a fresh
worker launch when a policy change cannot be seen in the command line. The daemon
reloads that file before its next supervision pass. The event log records changed
argument status, environment key names, and writable roots; environment values
and full command lines are not logged.

The update waits for a matched native completion, an idle native turn, a live
observation without an approval or question, and no pending worker input or
secondary slot. After five minutes without that boundary, it emits one operator
ask and keeps waiting. It never uses a timeout as permission to stop a worker.
When safe, the supervisor records a stopping intent, stops only the owned worker
pane, re-applies configuration, resumes the exact native session with no prompt,
and checks the new process before clearing the intent. The manager pane is left
alone. A fresh pane observation precedes one ordinary continuation check, so a
bound Bead can continue without a manual steering message.

A supervisor crash during stop, launch, or verification turns the durable intent
into a recovery hold. The pane and native identity remain available for explicit
inspection; no uncertain launch or input is replayed automatically. A worker
whose process cannot be inspected is left running and reported for operator
inspection.
