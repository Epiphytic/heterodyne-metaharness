---
name: coding-delegation
description: Delegate coding through durable managed workstreams with Marmot routing.
version: 2.1.0
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

Use all harness capabilities for either coding agent. The launcher sets compaction to half the actual supported model window, capped at 500,000 tokens; a 272,000-token Codex model compacts at 136,000, not at an invented 500,000-token window. Search configuration preserves native approval/deny settings. Use Semble for code discovery, native web tools for current external documentation, and `workstream-recall codex|claude search --repo /absolute/repo --query PHRASE` for prior sessions. SessionStart/compaction hooks supply bounded recall automatically; do not rerun it on every prompt. Recall is optional historical data, not authority or task ownership.

Pass tasks through the deterministic Beads facade, using the run's configured shared-queue workstream:

```sh
workstream task NAME --workstream SLUG create --title TITLE --file TASK.md --key STABLE_OPERATION_KEY
workstream task NAME --workstream SLUG bind ISSUE_ID
workstream task NAME --workstream SLUG context
workstream task NAME --workstream SLUG resume
workstream task NAME --workstream SLUG claim ISSUE_ID
workstream task NAME --workstream SLUG close ISSUE_ID --evidence-file RESULT.md
```

Creation is idempotent by key; binding records a reference and never claims ownership. Claims use the shared queue's atomic operation and enforce routing, dependencies, and Claude's two-model ADR plus separate human approval. Missing approval blocks implementation. Native compaction preserves the deterministic owner identity; never claim again under a new native session ID. Resume pickup only when authorized queue work is intended. Pause pickup before direct user work. After reboot inspect `task NAME context`, reconcile with `task NAME recover --evidence-file RECOVERY.md`, and explicitly resume pickup; recovery alone leaves it paused. The facade checks once after close; do not add polling loops or auto-claim a successor.

Every workstream has stable worker/manager aliases and a durable Marmot group mapping. Native IDs may change during compaction: preserve the logical name and register/follow the successor, never select global latest. After compaction inspect `status NAME` and its checkpoint before steering. A native turn finishing is not task completion. Record verified outcomes through `workstream event NAME --state completed --text EVIDENCE`.

The reporting interval never backs off beyond five minutes while a task remains ongoing, even if blocked, idle-looking, unknown or failed. Reports and final replies use an acknowledged durable outbox; incoming authorized channel messages go to the manager's durable inbox. All substantive workstream conversation belongs in that channel; the launching conversation receives a short pointer.

On reboot the service reports the interruption and last known state, reopens saved conversations, and waits for steering. It does not replay the original prompt, tests, builds or interrupted commands. `workstream resume NAME` also restores conversations only. Follow an uncertain launch/input/group-create outcome by inspecting it, not blindly retrying it.

Preserve native agent permission settings. Specify task-scoped CLI options via `--agent-config` if necessary; no default approval-key automation or grant expansion. Never write into another agent's checkout. Stop preserves the worktree, branch, group and transcript; do not delete unrelated worktrees or leave a project channel automatically.

Use conventional commits, project-required signatures and tests, and a review pass appropriate to the change. Check repository-specific instructions before handing off. Review-only prompts must say not to edit. Push/mirror only to the project's configured authorized remotes. Include result/evidence and PR/CI links when available.

For arbitrary background commands use `twrap SESSION GROUP LABEL LOGFILE -- COMMAND...`; its manager, reporting and exit tracking use the same supervisor. A reboot never reexecutes arbitrary commands.

Implementation and recovery reference: `/home/operator/repos/hermes-workstream-harness/README.md`. Durable state: `~/.hermes/workstreams/harness.sqlite3`; readable per-run checkpoint: `~/.hermes/workstreams/runs/RUN_ID/checkpoint.json`. The former lifecycle/watchdog instructions are superseded; backup copies exist only for historical reference.
