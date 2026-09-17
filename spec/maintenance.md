# Maintenance ownership

Significant Hermes changes (code, plugins, harnesses, architecture, dependencies) MUST
execute in the existing Codex worker of `hermes-maintenance`, through owned tmux.
Simple configuration and ordinary memory preference edits may remain with Hermes.

Stable run: `b61d651b-8206-52d9-8965-d30d937dd103`.
Marmot group: `1cead9a9921044b3236ddb271e9a4cac`.
Worker/manager names: `workstream-hermes-maintenance-worker` and
`workstream-hermes-maintenance-manager`. Native IDs are replaceable lineage metadata.

Use `workstream maintenance --title TITLE --file TASK --key REQUEST_ID` for new work.
Admission verifies the installed binding, creates an idempotent Beads task and queues
one manager notification. Reusing a key with changed content fails. Admission never
claims work, interrupts the current claim, launches a replacement or answers approvals.
The manager hands tasks to the existing worker at an authorized task boundary.

Enforcement hooks can block direct source edits through known Hermes tools. They are
workflow guards, not an OS security boundary: arbitrary shell interpretation cannot
prove whether a command will mutate source. Unknown mechanisms still obey this policy.
See [session recovery](sessions.md) and [change completion](changes.md).
