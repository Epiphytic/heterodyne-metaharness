# Agent resolution for babysitter findings

Design for btq-harness-5807c2754db50c5a918d085d, 2026-09-23.

Deterministic observers detect and verify; an existing workstream manager agent
investigates and fixes. The babysitter must not repair permissions, resume paused
workers, claim tasks, accept native prompts, restart services or create replacement
sessions. Existing approval, recovery, blocker and secondary-worker contracts apply.
Dispatch is information, not additional execution authority.

## Boundary and integration

Add a durable findings ledger in the harness database. Observers call `observe`
under the supervisor lock with registered run ID, category, stable subject, current
problem evidence and a healthy/unhealthy result. Categories are `invalid-pause`,
`missing-operator-notification`, `unverified-agent-job`, and
`blocked-handoff-commit`. Evidence contains the observed condition and required
verification; each category supplies a focused repair instruction. An explicit
healthy observation is required to resolve. No observation, a failed probe, or a
missing pane is unknown, never success. Observers own their evidence predicates;
this extension does not treat every intentional pause or approval gate as a bug.

The currently installed babysitter has an EROFS detector but no structured detector
for the other three categories. Expose the same `observe` entry point for those
separate detector workstreams; test the resolution path for every category. Replace
only the installed EROFS handler and run a resolution poll from its normal tick
using the existing guarded installer/backup mechanism. No service deployment from
this worker. The built-in observer ignores manager panes and requires an anchored
native git/Python read-only error signature in worker output, not a prose keyword.
Unchanged captured output is not a fresh verification of failure. An empty capture
or missing pane cannot clear a finding. A changed healthy capture clears it only
when the same owned worker is natively idle in a later native turn; this avoids treating scrolling output
or an in-progress retry as success. Native-turn identity is recorded in evidence.

## Durable attempts and verification

Identity binds run, category and subject; an explicit clear followed by recurrence
starts a new numbered episode. Persist observation sequence, evidence, phase,
attempt count, next deadline, inbox ID and last attempted observation sequence.
Queue one focused message to the run's existing manager in the same SQLite
transaction as the attempt. Include full evidence, success predicate and the
instruction to preserve current ownership, approvals and recovery holds. No tmux
input from the detector. A missing manager or unsafe native state defers dispatch;
the supervisor checks the repair inbox item again before delivery. Pauses on the
worker do not prevent manager investigation, but manager/native approval and run
recovery/stop guards do. Pending, sending or uncertain input is never blindly
resent. Direct steering retains priority.

At most one attempt per problem per five minutes. After the verification deadline,
a fresh unhealthy observation counts one failure. Dispatch the next attempt only
once the previous inbox item has reached submitted; pending delivery is not a repair
failure. A delivery that remains pending/uncertain for fifteen minutes escalates
without replacing it. Three failed attempts open one tagged operator ask via the
existing transactional event/outbox API; no fourth attempt is queued. The ask names
the condition and evidence, with IDs in a trailing reference line. An outage of the
notifier leaves the ask durably pending. Circuit state survives restarts and is not
reset by evidence wording changes or loss of local babysitter state.

A later positive observation marks the episode resolved and supersedes any pending
repair input. An escalated operator ask remains open for explicit evidence-backed
resolution through the existing ask contract. Verified resolution is journaled; no
agent's claim of success alone closes the finding. The ledger is not a Beads queue
and does not replace task or blocker ownership.

## Verification and rollout

Fixtures exercise detection -> inbox -> simulated manager repair -> later positive
observation, persisted restart dedup, all four categories, three-failure escalation,
unknown/stale probes, uncertain sends, delayed delivery, repeated recurrence,
manager/recovery guards and false-positive EROFS prose. Installer tests validate
idempotence, strict function signatures and preservation of unrelated live code.
Run the full harness suite and retain logs under docs/evidence. The manager applies
the reviewed source-digest-bound plan and records live verification. Integration
must retain newer canonical operator-ask and signed-blocker behavior; this worktree
was provisioned at 492464a and does not supersede those later changes.

## Producer and installation interface

Structured detector producers use `harness.babysitter_resolution.observe(store,
run, category, subject, evidence, healthy=bool)` under `Store.lock()` and a database
transaction. Supply a stable subject (pause record, outbox item, job or handoff ID),
not a changing error string. Call with `healthy=True` only after checking that
subject's success predicate. Call `poll(store)` under the same lock at the normal
babysitter cadence. Polling does not query or claim the Beads queue. The adapter
runs this for all categories even when no worker panes are present.

The EROFS observer verifies clearance of the visible error at a later idle native
turn; it does not prove a commit, permission grant or lifecycle stage succeeded.
Those remain governed by their own evidence checks. The other three detector
producers are separate integrations: this patch supplies their repair actions and
observation interface, not a new pause-legitimacy or agent-job audit engine.

Manager rollout from the reviewed checkout:

```sh
python3 install_babysitter_resolution.py --babysitter /home/operator/.hermes/workstreams/babysitter.py --output /tmp/babysitter-resolution-plan.json
python3 install_brain.py --apply /tmp/babysitter-resolution-plan.json --quarantine /home/operator/.local/state/hermes-quarantine/babysitter-resolution
```

Review the source-bound plan and preserve the backup; integrate the supervisor
change before enabling the external adapter. Rollback restores that exact backup
and prior harness revision; retain the findings tables for audit and reconcile
pending repair input before rolling back supervisor delivery guards. No rollout
or live repair is implied by fixture evidence.
