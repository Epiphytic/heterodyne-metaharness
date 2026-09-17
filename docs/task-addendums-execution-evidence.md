# Task addendum execution evidence

Contract: [active task specification](../spec/tasks.md).
Deployment procedure: [task handoff runbook](task-addendums.md).
Implementation: [task admission and projections](../harness/tasks.py),
[task receipt adapter](../harness/task_hooks.py), and
[shared provider hooks](../harness/brain_hooks.py).

This records operator-confirmed deployment and live verification on 2026-09-17,
plus the worker's direct read-only CLI observations. It does not confer new approval.

## Deployment and preservation

Source commit `3aa8e86` was deployed after a private coherent backup of harness
SQLite and checkpoints. The supervisor and gateway restarted, and the original
Hermes manager was exact-resumed. Manager native identity
`20260916_182541_e8cb77` and worker native identity
`01a0acf9-dfb2-7d43-93f6-e90472d7c123` were retained. The stable workstream remained
`b61d651b-8206-52d9-8965-d30d937dd103`.

Services were active after deployment. Operator doctor results reported outbox
pending 0 and inbound 0. No new packages or provider hooks were installed; the
existing shared hooks load the deployed modules. No private backup contents are
included in this document.

## Automated verification

The operator reported the full suite as 188 tests passed with one native-only
skip. A separate run of `tests.test_brain_hooks` using the repaired Hermes venv
passed all four tests, including native plugin invocation. Existing provider
fixtures cover Codex and Claude receipt handling. Task tests cover admission,
read-only projections, idempotent updates, uncertain writes, owner changes,
external scope changes, close retries, deferred delivery and direct-message
priority.

Governance validation and diff whitespace checks passed before source freeze.
Ripwire quality-delta reported complexity, verbosity, churn and duplication
findings, including fixture-related findings. It did not pass cleanly; this
record makes no clean quality-gate claim.

## Live task receipt and idempotency

The Bead remained `btq-harness-a40956ff84a9873066149391`, assigned to
`codex:belthanior:b61d651b-8206-52d9-8965-d30d937dd103` throughout verification.
No new task was claimed.

The worker received the initial native context notice and successfully read its
snapshot using `workstream task ... show ...` inside the existing sandbox. The
read-only projection reported revision
`290395c70b7f911e784ac56ae74a20b7276e7d29ad0fb194edf08f9d93f6623d`.
The operator confirmed its receipt at Unix timestamp `1789614615.658155`.
The prior initial inbox notice was retired as superseded after context receipt.

The operator invoked the actual facade update twice with the same key and body.
Both returned revision
`b4fc3e78645914b566ba9c8be178000ee00d1142ed220a4a85d26dbc9c279cfe`.
Native notes contained one addendum marker; the Bead and owner remained unchanged.
The durable notification
`task-update:e75fe8fe2952201c2e1df2da65d71f6cbb9199c7aa51bb2c312b03d8610c8b67`
was submitted naturally. The worker received both the inbox notice and native
context notice, read the updated snapshot through the read-only sandbox CLI,
and reported the matching revision and retained ownership. The operator confirmed
latest `task_receipts.ack_at` as `1789614653.3037102`.

The addendum body was not manually sent to the worker. Its contents were obtained
from the task projection through the CLI. The verification addendum recorded
existing authorization without expanding implementation scope.

## Coverage limits and completion boundary

Bare external `bd` changes require an explicit `workstream task RUN reconcile ID`
by their writer or operator to refresh projections and queue notices. There is no
remote push subscription or continuous model polling. Close additionally fetches
current native scope and checks it against the claim baseline or acknowledged
scope, retaining native ownership and recovery checks.

No live Claude model turn or physical reboot was performed for this deployment.
Provider fixtures and retained identities support only their stated test coverage;
they are not evidence of those unperformed live scenarios. Context receipt proves
delivery, not semantic acceptance or new authorization.

The historical decision remains approved pending an immutable evidence commit and
subsequent completion event. This document does not close the Bead or terminate the
persistent maintenance session.
