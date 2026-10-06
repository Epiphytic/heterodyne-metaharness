# wsd: the workstream daemon (queue and state)

`wsd` picks up beads for each configured workstream, one coder bead at a time, and keeps a journal so that a crash never loses or repeats a claim, a launch, a park or a resume (ADR 0001 §3.3, §4.3, §5.2, §9). This page covers plan 3: the queue, the journal, pickup, parking and recovery. Agent launching (plan 4), approvals (plan 5) and Marmot (plan 6) plug into the seams listed at the end.

**Until plan 4 wires in a runtime, `wsd run` holds every workstream (`runtime_unavailable`) and claims nothing.**

## 1. Configuration

All of it is host config (`config.toml`); a workstream file can't set `[wsd]` or `[integrations]`.

| Key | Meaning |
|---|---|
| `wsd.backstop_seconds` | How often pickup runs without a trigger. Default 60. |
| `wsd.reconcile_seconds` | How often state is rebuilt from beads and the runtime, then pickup runs. Default 300. |
| `wsd.launch_failures_before_human` | Failed launches of one bead before it is `stuck` with `needs-human`. 1 to 100, default 2. |
| `wsd.park_attempts_before_human` | Failed park attempts (session not confirmed stopped, git failure) before `stuck`. Default 3. Beads being unreachable never counts. |
| `wsd.inbox_attempts_before_human` | Failed attempts to apply one inbound event before it needs a human. Default 3. |
| `wsd.coder_role` | The role (from a workstream's `[roles]`) that runs beads. Default `coder`. Renaming it never frees the role: every session the runtime lists for the workstream holds it, whatever role it was launched as, and a bead already picked up keeps the role recorded for it. |
| `integrations.beads.btq` | Required. The btq checkout; wsd loads its `bin/btq` and uses its `Queue` as agent `wsd`. In code this is `WsdSettings.btq_checkout`. |
| `integrations.beads.config_dir`, `repo`, `dolt_host`, `dolt_port`, `dolt_database`, `tls_cert` | Optional btq locations, passed to `Queue` unchanged. Unset ones fall back to btq's own `BTQ_*` environment and defaults. |
| `integrations.beads.credentials` | Optional. Must be `{ file = "<path>" }`: btq's credentials file. Inline secrets are refused. |

**`BTQ_REPO` is not the btq checkout.** btq itself reads `BTQ_REPO` as the beads database repository (the same thing as `integrations.beads.repo`). The plan and btq's contract test use `$BTQ_REPO` for the btq checkout, which here is `integrations.beads.btq`. Don't conflate them: if `repo` is unset and wsd's environment has a `BTQ_REPO` pointing at the btq checkout, btq will take that checkout as its beads repository. Set `repo` explicitly, or keep `BTQ_REPO` out of wsd's unit environment.

A workstream is `workstreams/<name>.toml`, where `<name>` is a slug (lowercase letters, digits, `.`, `_`, `-`). wsd needs:

- `[repos]`: repository name to absolute path (or `~/...`). One must be called `default`. A bead's `metadata.repo` picks another one; an unknown name makes that bead `stuck` with `config_invalid`.
- `[roles]`: `coder` (or your `wsd.coder_role`) must name a profile from `[profiles]`.

The journal, locks and control socket live in `<state>/wsd/` (0700): `wsd.db`, `wsd.lock`, `claims/<ws>.claim` and `ctl.sock`.

## 2. Commands

| Command | Does |
|---|---|
| `wsd run` | Runs the daemon. Exit 78 means a configuration problem or a journal that failed its check; the unit should not restart on it. Exit 1 means another wsd holds the state directory, or another process has the journal locked; retrying is safe and nothing was changed. A timer (backstop or reconcile) that dies ends `wsd run` with an error rather than leaving a daemon that never picks up again; the unit restarts it. On SIGTERM (even mid-startup) wsd stops taking requests, cancels every job that has not started, waits up to 120 s for jobs already running, closes the journal and only then releases its instance lock; the control socket and timers never start after a stop. If jobs are still running after the 120 s, it exits 1 at once (`os._exit`): to the journal that is a crash, which the next start's recovery replays. |
| `wsd tick pickup [--ws WS]` | Asks the running wsd to run pickup now. Timers may call it. Each workstream runs its jobs one at a time on its own worker, with at most one pickup and one reconcile waiting: a tick that finds one waiting joins it and gets its outcome. A busy workstream never delays another, or status. A pickup or reconcile that raises replies `failed`, is recorded as `tick_failed`, and the workstream is recovered again before its next pickup. |
| `wsd tick reconcile [--ws WS]` | Asks for a reconcile (recovery, then pickup). |
| `wsctl pause WS` | Asks wsd to stop new claims for `WS`. Returns only once no claim can start. Needs a running wsd. |
| `wsctl resume WS` | Asks wsd to allow claims again; wsd then runs a pickup. |
| `wsctl status [WS] [--all]` | Each workstream's state, holds, beads with their state and reason, open journal steps and the last event number. Closed and dropped beads are only counted (`finished=`) unless `--all` lists them too. A `stuck` workstream also shows `attention=not idle; a human must act`. A reply is at most 1 MiB; one that would be longer fails with `reply too large` (ask about one workstream, or leave out `--all`) and is never cut short. |

Pause is btq's own pause flag for the workstream's session worker, which wsd sets under the workstream's claim lock. A direct `btq pause` on that worker sets the same flag without the lock: wsd honours it from its next claim check, so at most one claim already under way can still complete. With wsd stopped nothing claims, so `wsctl` refuses rather than acknowledge anything. Pause stops **new claims only**. A running bead carries on, and a parked bead whose blockers closed still resumes. wsd never unclaims a bead.

## 3. States

Each bead wsd holds has one state and, when it is waiting or stuck, a reason:

- `claiming`, `starting`, `running`, `resuming`: being started, working, or coming back from a park.
- `parking`, then `parked` (`blocked_on_bead`), `waiting_input` (`waiting_on_operator`: it waits on an approval, question or confirm bead) or `held` (`held_by_operator`: `/stop`).
- `stuck`: needs a human. The reason says why (`launch_failed`, `launch_unrecorded`, `park_failed`, `config_invalid`, `claim_lost`, `routing_changed`, `worktree_failed`, `journal_lost`, `stop_unconfirmed`, `needs_human`, `unexpected_state`) and the bead gets the `needs-human` label.
- `stuck` with `unclaimable` is different: a ready bead wsd has **not** claimed, because btq's claim can never take it (a `session:` pin on the workstream session, or wsd's park labels left on an unclaimed bead). It gets no `needs-human` label and no release applies to it. A human repairs the bead (removes the pin or the labels) and the next pickup claims it; a bead that stops being ready is forgotten.
- Owned `held` and `stuck` beads move on only through the operator's release (plan 6). Removing `v2:held` or `needs-human` by hand changes nothing in wsd.
- `closed`, `dropped` (no longer ours).

A workstream is `running`, `idle`, `all_blocked`, `paused`, `held` or `stuck`. `stuck` is never idle: status, tick and resume replies all carry `attention: not idle; a human must act` for it. What the human does depends on the reason: release an owned stuck bead, or repair an `unclaimable` one. `held` lists its holds: `beads_unreachable`, `runtime_unavailable`, `claim_uncertain`, `launch_uncertain` (a launch whose outcome the runtime could not report), `actions_unreconciled` (a plan 5 action in any state but `pending`, `succeeded` or `failed`, closed beads included). A hold is retried on every pickup and cleared once its cause is gone. Holds never escalate a bead: an outage is not the bead's fault.

Every state change is also a progress event in the journal, carrying the reason, its detail (for a parked bead, the blocker IDs) and the message or event that caused it when there is one.

## 4. Recovery

On start, before the control socket opens, wsd runs for each workstream:

1. the journal integrity check (a failed check stops wsd with exit 78 and leaves the file in place; move it aside to start from beads alone);
2. read every bead the workstream's per-bead workers hold, found by assignee whatever its labels say, and every session the runtime may still be running;
3. hold the workstream while any action is unsettled, closed beads included;
4. replay every open park, release and escalation, and read back any claim a pickup left uncertain;
5. the sweep: stop the sessions of beads no longer ours, give a running bead whose session is gone a resume operation, and hold as `stuck` anything beads and the journal disagree on;
6. then a startup pickup, and only then events.

Recovery never launches: every launch goes through pickup's launch guard, which checks the runtime, the holds, the coder role, `needs-human`, btq's own post-claim checks, the launched-session record on the bead, whether the bead is still runnable, and its worktree, in that order. A workstream whose recovery failed stays `held` and is recovered again before its next pickup; so does one whose pickup, tick or recovery raised. A job that fails on a timer is recorded as a `tick_failed` event (skipped if the journal itself is locked) and the timer keeps running. Nothing is inferred from missing evidence. An unreadable claim, session list, session record or pause flag holds rather than proceeds. A bead the journal has no row for (a lost journal) is held as `journal_lost` until the operator releases it.

The journal is backed up with `Journal.backup(dest)`, a consistent online copy (SQLite's backup API). Scheduling it next to the beads backups is plan 8's job.

## 5. Seams for later plans

- **Plan 4, `AgentRuntime`** (`heterodyne.wsd.runtime`): `available()`, `sessions(ws) -> [Session]` (every session that may be running, until its end is confirmed; never a partial list), `launch(LaunchSpec)` (raises `LaunchFailed` when nothing started, `RuntimeUnavailable` when nothing was attempted, anything else is treated as uncertain) and `stop(session_key)` (returns only once the session has ended). `unknown` liveness is never treated as dead. The launched-session record (`metadata.wsd_session`: role, profile, session key, repository, worktree) is written to the bead before every first launch; plan 4 may add fields. wsd never rewrites an existing record: the same five fields leave it exactly as it is, extra fields included, and different ones are escalated (`unexpected_state`). Plan 4 must add persistent crash-loop accounting before a real runtime is enabled: each relaunch after a dead session opens a new resume with a fresh launch-failure budget, so a bead whose session dies on every launch is relaunched without limit. `LaunchSpec.resume` is prepared identity, not launch evidence: a first pickup shelved after writing its record never launched, so the runtime must create the session when it can establish none ever existed, and hold when it can't tell. wsd counts every listed session as the coder's; before a second runtime role (a reviewer, an auxiliary session) is enabled, occupancy must be classified by role, keeping the coder's reservation across a `wsd.coder_role` rename. Pass the runtime to `heterodyne.wsd.cli.run`.
- **Plan 5, `ActionReconciler`**: `unresolved(ws) -> [approval bead IDs]`. The default `HoldingReconciler` reports every action not `pending`, `succeeded` or `failed`, closed beads included, so the workstream stays held until plan 5 settles them. Approval beads must carry the `ws:<ws>` label and `metadata.action_state`.
- **Plans 5 and 6, parking**: `Parker.park(bead, blockers, why, hold, ref)` parks a running bead on blocking beads, or for the operator with `hold=True`. It takes the workstream's operation lock, raises `OpConflict` while another operation is open on the bead, and raises `BeadsUnavailable` when beads can't be reached; the caller keeps the request and retries after the next pickup.
- **Plan 6, release**: `Parker.release(bead, ref)` is the only way out of `held` or `stuck`. It raises `NotReleasable` for any other bead.
- **Plan 6, events**: `Journal.inbox_add` (deduplicated by surface and event ID), `inbox_pending`, `inbox_failed`, `inbox_finish`; `Journal.events_since(seq)` for progress; `Journal.snapshot(ws)` for status. Triggers enter as `Wsd.pickup_one(ws, Trigger(kind, ref))` (or a `tick` through `Wsd.handle`), never `Scheduler.pickup` directly: `pickup_one` holds the workstream's operation lock while it decides whether to recover first, recovers, picks up and records the result, and leaves the workstream to be recovered again if anything raises. Once shutdown begins, `Wsd.handle` refuses everything.
- **Plan 8, backups**: `Journal.backup(dest)`.
