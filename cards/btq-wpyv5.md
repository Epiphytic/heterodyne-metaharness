Decidable. Class: stalled claim. Bead btq-wpyv5.
What it is (title, type and priority from the bead):
Decision summary (written by the controller at 2026-10-16T20:56:19Z):
| ` Planning amendment: preserve generation end evidence across session resolution so healthy stopped sessions are not counted as crash-loop deaths. `
| ` The description proposes copying end_cause/reached_running and accounting the launch exactly once; it is a proposal, not current-code verification. `
| ` The saved record is in_progress under a historical Claude holder, marked needs-human, with an older ADR revision but no progress comments or recorded worktree/commit/PR evidence. `
| ` Current implementation progress, effects elsewhere and process state remain unknown; no claim of death or completion follows. `
| ` Recommend leave it held and groom the proposal against current resolution/death-accounting code, then use supported recovery and genuine design admission for any remaining amendment. `
| ` Do not delete sessions, alter accounting, clear ownership or infer new authority from this summary. `
|
| ` [g08sd follow-up] Keep end evidence across wsctl resolve so resolved healthy sessions are not counted as deaths (task, 3) `
Description (from the bead; whole field in More):
(sanitized)
| ` Follow-up from xb56t x g08sd merge (2026-10-13). Under the approved g08sd spec, a generation whose session directory is removed by wsctl resolve (land/keep/discard) or by manual docs/wsd.md section 5 step 4 has no end... `
Holder: claude:belthanior:g08sd-author.
Claim: 2026-10-14T22:47:16Z (last update); activity: 2026-10-14T22:47:16Z (before btq-supervise wrote).
Idle: 46.2h; threshold: 2.0h. 
What has happened. Process: unknown. Progress: unknown. Comments: 1.
Worktree: not inspected (none recorded); work may exist elsewhere.
Newest comment (from the bead):
(sanitized)
| ` decision-summary: btq-wpyv5 at 2026-10-16T20:56:19Z `
| ` Planning amendment: preserve generation end e... `
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
request j8y9urzsjk23; facts f18df93b6441
More
Full description, design and acceptance criteria (from the bead):
| ` Follow-up from xb56t x g08sd merge (2026-10-13). Under the approved g08sd spec, a generation whose session directory is removed by wsctl resolve (land/keep/discard) or by manual docs/wsd.md section 5 step 4 has no end evidence, so the next launch guard (Parker._account) counts it as one death. A healthy session that was parked or stopped with unlanded commits, then resolved, therefore counts toward crash_loop; two in a row escalate. Proposal (option B from xb56t-impl): at the resolve confirm step copy the record's end_cause and reached_running into the resolution intent, and in the same journal transaction as emit_once account that generation's LAUNCHED entry with the class _account would have used (stopped: no count, lifetime: reset, died: count), exactly once via INSERT OR IGNORE. This amends the g08sd design, so it needs a design doc, cross-model review and a new digest-pinned design approval before implementation. `
|
|
Labels, notes and holder (from the bead):
| ` agent:claude kind:brainstorm repo:heterodyne-metaharness ws:heterodyne `
| ` parked by claude:belthanior:g08sd-author at 2026-10-14T22:47:15Z: kind:design is not a routable kind; use kind:brainstorm `
| ` unparked by claude:belthanior:g08sd-author at 2026-10-14T22:47:15Z: routed kind:brainstorm `
| ` claude:belthanior:g08sd-author `
Dependencies (from the bead):
Branch, path and commits (from git; local evidence, not completion):
|
|
Claimed: 2026-10-14T22:47:16Z. Last activity: 2026-10-14T22:47:16Z.
Process evidence (recorded, not proof of completion):
| ` process not recorded `
Holder comments: 0. Review line recorded: no; evidence, never completion.
Local containment refs: 0; last fetched: unknown; never proof of merge completion.
Comments (from the bead; newest first):
(sanitized)
| ` decision-summary: btq-wpyv5 at 2026-10-16T20:56:19Z `
| ` Planning amendment: preserve generation end evidence across session resolution so healthy stopped sessions are not counted as crash-loop deaths. `
| ` The description proposes copying end_cause/reached_running and accounting the launch exactly once; it is a proposal, not current-code verification. `
| ` The saved record is in_progress under a historical C... `
URLs (recorded, never fetched):
request j8y9urzsjk23; facts f18df93b6441
