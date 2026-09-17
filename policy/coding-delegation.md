---
name: coding-delegation
description: Delegate coding through durable managed workstreams with Marmot routing.
version: 2.0.0
---

# Coding delegation

Use the executable harness; do not reconstruct its lifecycle in prompts or tmux commands.

```sh
~/.hermes/scripts/workstream start NAME /absolute/repo codex --file TASK_FILE
~/.hermes/scripts/workstream start NAME /absolute/repo claude --file TASK_FILE --group EXISTING_GROUP
~/.hermes/scripts/workstream status NAME
~/.hermes/scripts/workstream send NAME --file STEERING_FILE
~/.hermes/scripts/workstream stop NAME
```

The start command creates/reuses the project Marmot group, creates an isolated worktree, starts a dedicated Hermes manager and coding terminal, and durably registers them. If available, pass `--parent-session HERMES_SESSION_ID` to link the originating conversation. The old workstream-start NAME REPO AGENT [PROMPT] [GROUP] interface delegates to the same implementation. Never spawn a separate watchdog, progress relay, or per-session cron.

For complex tasks and Marmot/Heterodyne/Epiphytic projects use Codex. For longer, less complex tasks use Claude Code. Simple tasks can use ordinary Hermes delegation. The managed Hermes child reasons and steers; the supervisor owns launch, routing, timers, retries, native IDs and recovery. The main conversation stays responsive.

Every workstream has stable worker/manager aliases and a durable Marmot group mapping. Native IDs may change during compaction: preserve the logical name and register/follow the successor, never select global latest. After compaction inspect `status NAME` and its checkpoint before steering. A native turn finishing is not task completion. Record verified outcomes through `workstream event NAME --state completed --text EVIDENCE`.

The reporting interval never backs off beyond five minutes while a task remains ongoing, even if blocked, idle-looking, unknown or failed. Reports and final replies use an acknowledged durable outbox; incoming authorized channel messages go to the manager's durable inbox. All substantive workstream conversation belongs in that channel; the launching conversation receives a short pointer.

On reboot the service reports the interruption and last known state, reopens saved conversations, and waits for steering. It does not replay the original prompt, tests, builds or interrupted commands. `workstream resume NAME` also restores conversations only. Follow an uncertain launch/input/group-create outcome by inspecting it, not blindly retrying it.

Preserve native agent permission settings. Specify task-scoped CLI options via `--agent-config` if necessary; no default approval-key automation or grant expansion. Never write into another agent's checkout. Stop preserves the worktree, branch, group and transcript; do not delete unrelated worktrees or leave a project channel automatically.

Use conventional commits, project-required signatures and tests, and a review pass appropriate to the change. Check repository-specific instructions before handing off. Review-only prompts must say not to edit. Push/mirror only to the project's configured authorized remotes. Include result/evidence and PR/CI links when available.

For arbitrary background commands use `twrap SESSION GROUP LABEL LOGFILE -- COMMAND...`; its manager, reporting and exit tracking use the same supervisor. A reboot never reexecutes arbitrary commands.

Implementation and recovery reference: `/home/operator/repos/hermes-workstream-harness/README.md`. Durable state: `~/.hermes/workstreams/harness.sqlite3`; readable per-run checkpoint: `~/.hermes/workstreams/runs/RUN_ID/checkpoint.json`. The former lifecycle/watchdog instructions are superseded; backup copies exist only for historical reference.
