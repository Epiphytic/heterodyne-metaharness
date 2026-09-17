# Shared Beads handoff

The harness delegates credentials, its fixed `tasks` database, route filtering,
atomic claims and ownership validation to the existing trusted BTQ client. It
never initializes another database. Configure explicitly:

```json
{"beads":{"enabled":true,"workstream":"hermes-workstreams","executable":"/home/operator/repos/beads-task-queue/bin/btq"}}
```

Each coding run owns a stable harness UUID. Native Codex/Claude session IDs can
change during compaction without changing task ownership. `HERMES_WORKSTREAM_RUN`
and `HERMES_WORKSTREAM_ROLE=worker` associate installed queue hooks with that
record. The managed hook uses facade checks and managed CLI commands; unmanaged
sessions still execute the original installed BTQ hook unchanged. No hook claims
work automatically. Compaction only refreshes context, never starts new pickup.

Create a task using an operation key that the caller retains across retries:

```sh
workstream task SESSION create --title 'Implement feature' --file /absolute/task.txt --key message-123
workstream task SESSION bind ISSUE_ID
workstream task SESSION resume
workstream task SESSION claim ISSUE_ID
workstream task SESSION worktree ISSUE_ID /absolute/repository
workstream task SESSION close ISSUE_ID --evidence-file /absolute/evidence.txt
```

`create` passes work to the session's routed queue without binding or claiming.
The deterministic issue ID permits safe retries after lost acknowledgements;
reusing a key with different task content fails. `bind` records a task reference;
only a verified successful `claim` permits execution. Failed/uncertain claims
must not execute. `close` checks eligible work once after completion and never
claims it. Empty queues yield normally. `ready`, `show ISSUE_ID`, and `context`
are explicit inspection commands. `pause` persists pickup suspension for direct
user work, stop requests and approvals. A bound unfinished task cannot be replaced
by another task. Workstream enrollment can be supplied before an action with
`workstream task SESSION --workstream SLUG ...`; a bound task cannot be rerouted.

Claude implementation tasks require a current two-model ADR, separately recorded
human approval, matching ADR revision, and a native blocking dependency to that
approval bead. To reference existing legitimate evidence, creation accepts
`--approval-id ISSUE_ID --metadata '{"adr_revision":"REVISION"}'`. It creates the
blocking dependency. Missing evidence prevents pickup and claim. The facade does
not manufacture approvals. Research/review/brainstorm routes remain available.

On reboot, queue pickup pauses and retains the same ownership. Inspect the
checkpoint, bead, worktree and any surviving effects, then reconcile explicitly:

```sh
workstream task SESSION recover --evidence-file /absolute/recovery-evidence.txt
workstream task SESSION resume
```

Recovery records evidence and clears recovery guards but leaves pickup paused
until `resume`. It never replays the old prompt, starts work, takes another
worker's claim, or changes the conversation's awaiting-resume state. Sending a
new instruction remains explicit. Models must use the managed task CLI for
claims so all dependency checks remain enforced.

Validation includes both agents' create → bind → claim → close flows through the
real installed BTQ implementation against an isolated fake `bd` transport, plus
native-ID changes, recovery, routing rejection, duplicate requests, terminal-hook
behavior and CLI wiring. Read-only checks of the real shared DB succeeded for
both agents with an unused workstream; these checks created no issues or claims.
