Decidable. Class: stalled claim. Bead btq-5dh3q.
What it is (title, type and priority from the bead):
Decision summary (written by the controller at 2026-10-16T20:56:19Z):
| ` Earlier planning scope: detect stalled or unroutable tasks, send one Marmot decision card, execute authorized recovery, and audit approval overrides. `
| ` The saved native record is in_progress under a historical Claude holder and marked needs-human; it has no progress comments or recorded worktree/commit/PR evidence. `
| ` Later supervisor and recovery designs cover related scope, but this record does not prove this task finished or that all its proposed execution functions shipped. `
| ` Process state and work elsewhere are unknown from these records; elapsed time does not establish worker death. `
| ` Recommend leave it held and groom its remaining scope against the delivered supervisor and recovery designs before supported ownership recovery. `
| ` Preserve source/history and genuine approvals. This summary authorizes no release, closure, reassignment or deployment. `
|
| ` Design btq Step 4: stall/needs-human escalation via Marmot ask, then execute the decision (task, 1) `
Description (from the bead; whole field in More):
(sanitized)
| ` Design btq Step 4's needs-human escalation through Marmot (ORIGINAL-SPEC Step 4; OPS.md "Migrations and reclaim"; PILOT.md rows for reclaim and stall detection). `
| ` Liam, 2026-10-14: OPS.md is right (never unclaim on ela... `
Holder: claude:belthanior:btq-bd-impl.
Claim: 2026-10-14T22:47:15Z (last update); activity: 2026-10-14T22:47:15Z (before btq-supervise wrote).
Idle: 46.2h; threshold: 2.0h. 
What has happened. Process: unknown. Progress: unknown. Comments: 1.
Worktree: not inspected (none recorded); work may exist elsewhere.
Newest comment (from the bead):
(sanitized)
| ` decision-summary: btq-5dh3q at 2026-10-16T20:56:19Z `
| ` Earlier planning scope: detect stalled or unroutable tasks, send one Marmot decision card, execute authorized recovery, and audit approval overrides. `
| ` The saved native record is in_progress under a historical Claude holder and marked needs-human;... `
Options
A leave it. Changes: nothing.
Still required: status in_progress; current holder; needs-human (supervisor).
B the worker is gone: release it, blocked. Changes: after confirmed process exit, holder cleared, status blocked; release note with ask ID; worktree and branch stay, named by note.
Still required: status blocked; needs-human (supervisor).
C the worker is gone: release it, open. Changes: after confirmed process exit, holder cleared, status open; release note with ask ID; worktree and branch stay, named by note; only supervisor-added needs-human removed.
Still required: none known to btq; pickup also needs a free worker on its route.
D close it, not completed. Changes: closed, reason not completed (ask ID); stops blocking 0 dependents; each keeps its own gates. Work stays; nothing merged or deleted..
Still required: dependent gates not assessed by btq.
Recommended: A. btq cannot tell whether the worker is alive; unreachable is not dead.
request 44svx92ysyuc; facts 32103fb33009
More
Full description, design and acceptance criteria (from the bead):
| ` Design btq Step 4's needs-human escalation through Marmot (ORIGINAL-SPEC Step 4; OPS.md "Migrations and reclaim"; PILOT.md rows for reclaim and stall detection). `
| ` Liam, 2026-10-14: OPS.md is right (never unclaim on elapsed time). needs-human means sending a Marmot ask about what to do, then carrying out the answer. `
| ` Scope: `
| ` (1) Stall detection: in_progress or blocked claims with no bead activity past the threshold (2h by default, overridable per task) get the needs-human flag. Detection runs on a schedule or at between-tasks checkpoints, never by continuous polling. Unroutable beads, such as ones missing a kind: label (like btq-9yiku was), are flagged the same way. `
| ` (2) Relay: post one deduplicated admind question ask per flagged bead. It carries the facts OPS.md requires: host, worker/session/process, whether that process is alive, worktree state and external effects. It offers explicit options: leave it, release it to the queue, reassign it, or close it, with a marked recommendation. `
| ` (3) Execution: a btq supervisor recovery op that changes assignee or routing only with a recorded Marmot decision. That decision is Bel's explicit reassignment authorization under OPS.md. The op records the ask ID and its outcome on the bead, and it refuses to act if the worker isn't confirmed dead, unless the answer says otherwise. `
| ` (4) Admin override for approved beads (Liam's request): define whether and how an operator can supersede or amend after approval, and how that's audited. `
| ` Needs a design, a cross-model review and Liam's approval before implementation. beads-task-queue changes also need Liam's confirmation. `
|
|
Labels, notes and holder (from the bead):
| ` agent:claude kind:brainstorm repo:beads-task-queue ws:heterodyne `
|
| ` claude:belthanior:btq-bd-impl `
Dependencies (from the bead):
Branch, path and commits (from git; local evidence, not completion):
|
|
Claimed: 2026-10-14T22:47:15Z. Last activity: 2026-10-14T22:47:15Z.
Process evidence (recorded, not proof of completion):
| ` process not recorded `
Holder comments: 0. Review line recorded: no; evidence, never completion.
Local containment refs: 0; last fetched: unknown; never proof of merge completion.
Comments (from the bead; newest first):
(sanitized)
| ` decision-summary: btq-5dh3q at 2026-10-16T20:56:19Z `
| ` Earlier planning scope: detect stalled or unroutable tasks, send one Marmot decision card, execute authorized recovery, and audit approval overrides. `
| ` The saved native record is in_progress under a historical Claude holder and marked needs-human; it has no progress comments or recorded worktree/commit/PR evidence. `
| ` Later supervisor and recovery ... `
URLs (recorded, never fetched):
request 44svx92ysyuc; facts 32103fb33009
