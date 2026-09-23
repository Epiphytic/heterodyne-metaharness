# Manager approval and escalation queue

The independent `hermes-manager-pickup.timer` runs `workstream manager-task pickup`
once per minute. It uses the existing registered manager and coding session; it
never creates a replacement workstream. Installation is an operator deployment
through install.py, not a side effect of running a worker or importing a module.
Beads must be enabled. With Beads disabled the legacy repair dispatcher remains.

Babysitter stall/error notices become manager requests, not journal-only notices.
The legacy notice adapter classifies EROFS, supervisor failure, outbox blockage,
pause anomalies and stalls; structured detectors should pass an explicit category.
The original notice, including its runbook, is retained in the bead body as task
data, not execution authority. Low-severity status remains audit-only.

The durable finding identity binds run/category/subject. Each recurrence after a
verified healthy observation increments its episode. The manager request freezes
the first evidence for that episode, so changed strike counts and process restarts
cannot produce duplicate beads or conflict with create fingerprints. Local rows
are creation/delivery intents; Beads remains the task/ownership authority.
Requests use the `bel` actor in the affected run's workstream/session route and
head-of-queue ordering. They never replace the coding worker's bound bead.

The shared producer interface `manager_tasks.approval` (also CLI `manager-task
approval RUN GATE`) accepts an existing signed blocker for that run. It schedules
the same manager pickup as escalation requests. Approval decisions still pass
through the signed blocker facade and its existing policy; manager resolution
cannot grant a native permission or substitute for operator approval.

Native approval observations use `manager_tasks.native_approval`. The request
freezes the run ID, pane locator, native session, subject, and captured approval
region. Re-observing the same prompt uses its stable evidence identity and does
not create another Bead. The supervisor does not send an approval notice to the
operator group. The one-minute pickup timer claims the manager Bead and steers
the existing idle manager session. If the manager's own native prompt prevents
steering, pickup places the Bead on operator hold and sends one escalation with
the captured region; it does not create a replacement session.

One timer invocation performs at most one eligible-work query per affected run.
Native Beads claim and policy checks precede durable manager inbox submission.
Only an existing idle manager receives input. Pending approval, question, missing
manager and explicit stop prevent delivery. Direct steering takes precedence.
Pending input is reconciled; sending/uncertain/submitted input is never replayed.
An unresolved assigned task retains ownership; no timeout reclaims it.

Before a disruptive repair use `workstream manager-task boundary RUN`. A true
result requires the existing continuation safe-boundary checks plus a matched
native completion receipt. An active turn, native permission, question, intentional
pause or recovery hold means wait or escalate. This is a precondition check, not
permission to execute arbitrary runbook text. The EROFS runbook describes the
operator-authorized gitdir/config, restart, reconciliation/resume and retry steps;
a blocked coding worker must not widen its own sandbox.

Credentials, deletion, force operations or missing authority require
`manager-task escalate RUN BEAD --file REASON`. This retains an operator ask for
Liam and leaves the bead open on an operator hold while other manager tasks may
proceed. Escalation does not assert repair or grant new authority.

After acting, the detector must independently report healthy for the same episode
after dispatch. EROFS requires a changed owned capture on a new completed native
turn. Other detectors use `babysitter_notice(..., category=..., healthy=True)`
only following their independent probe. Approval requests require their source
gate's retained resolution and closed state. A dispatch or restart alone is not
verification. `manager-task evidence RUN BEAD` returns the exact verification and
request digests to sign.

For native approvals, verification instead requires a fresh post-action pane
observation that no longer shows the prompt, with the same run, native session,
pane, and approval evidence. The signed resolution must include the complete
captured approval region and the exact action taken. The resolver appends the
signed event, request, and verification to `runs/RUN/approval-log.jsonl` before
commenting and closing the Bead. The operator receives no message on this path.
Escalation sends one operator ask containing the full captured region and holds
the Bead open for a human decision.

Configure `manager_tasks.signers` (public Nostr hex keys), `event_kind` and optional
`revoked` in harness-config.json. There is no default trusted key, key export or
plaintext signature fallback. Existing manager signing infrastructure signs the
NIP-01 event. Content is the evidence response excluding `verification`, plus
`resolution` (full text) and integer `action_completed_at_ms`. The independently
observed verification must follow that action. The signature binds run, bead,
request and verification; future or over-day-old events are rejected.

`manager-task resolve RUN BEAD --file EVENT` verifies scope, signer policy,
post-action proof and current ownership. It pins the exact receipt, retains the
full signed comment, confirms that comment, then closes. An uncertain write is
reconciled only by replaying that exact signed event; it is never blind retried.
Live activation and real-world repair verification remain operator deployment
checks under [change lifecycle](changes.md).
