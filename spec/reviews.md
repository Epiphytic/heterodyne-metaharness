# Independent evidence reviews

Authority: [entrypoint](README.md), [transitions](transitions.md),
[operator asks](operator-asks.md), [handoffs](handoffs.md), [changes](changes.md).

All observation, activity classification, native boundary correlation, evidence
collection and scheduling are deterministic scripts. Only a scheduled progress
or completion reviewer invokes a model. The reviewer is a separate Hermes
invocation, never the implementation agent. It receives independently collected
terminal/scrollback, Git status/diff, retained test logs and artifact hashes.
Evidence is untrusted data, not instructions. Missing or bounded evidence must be
identified in the appraisal; the coder's account is not proof of delivery.

Progress deadlines are absolute active elapsed minutes 10, 30, 90, 150, 210 and
then every 60. Persist elapsed time, last observation and suspension state across
native identity changes, compaction and restart. A late scheduler emits at most
one overdue review and skips missed deadlines, never a catch-up burst. Clock
rollback adds no elapsed time. While awaiting operator input with no concurrent
coding/testing activity, suspend elapsed time and hold reviewer delivery. Native
working activity in the same channel permits progress. PID liveness, pane timer
changes and a claimed Bead alone do not establish active work. Existing ask and
admin-tag policy owns reminders; reviews do not implement another ask mechanism.

Completion requires an explicit artifact-bound handoff followed by the confirmed
native turn boundary of that handoff. Pane inactivity alone never triggers it.
Review results bind task scope and exact artifacts. Closure requires the full
summary delivered through the durable outbox and no unresolved blocking findings.
A changed scope or artifact invalidates the result for closure. Findings can be
resolved by a fresh review or explicitly accepted by an operator with retained
evidence; the coder cannot label their own findings accepted. Transport retries
reuse immutable identities and never launch another model. Progress reviews use
their own schedule, not the escalation damper or transition batching window.

Rollout is explicit: harness configuration `reviews.enabled` must be true before
jobs are scheduled or completion review gates apply. Completion handoffs require
a clean committed checkout and exact current native turn identity. Review evidence
covers the recorded task worktree base through HEAD, plus working-tree state;
legacy worktrees lacking a base expose only HEAD's commit and report that limit.
Evidence captures are bounded, with full-output hashes and truncation indicators.
An uncertain running process blocks new reviewer launches in that workstream,
even after task scope changes. Reconciliation requires retained operator evidence
and confirmation the prior process exited; retries create distinct attempts.
Reviewer identity is separate from stable worker identity. Zero model tools and
an isolated Hermes home prevent worker plugins/memory from participating; this is
not an OS sandbox. Normal provider authentication may refresh native credentials.
