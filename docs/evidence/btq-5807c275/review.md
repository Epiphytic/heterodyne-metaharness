# Review evidence

Task: btq-harness-5807c2754db50c5a918d085d.
Provisioned base: 492464ad8; manager integrates with newer canonical changes.

Design was written before implementation. Deterministic observations drive durable
repair episodes; only existing manager inbox delivery performs agent engagement.
The adapter plan is prepared but not applied; no live sends, service changes,
permission modifications, task closures or deployment were performed.

All four categories have tested dispatch/verification/circuit-breaker behavior.
Only the existing EROFS detector is wired by this patch. Invalid-pause,
missing-notification and unverified-job audit producers must call the documented
observation interface when their separate detector implementations land. This is
not evidence that those audit engines already run in production.

EROFS verification proves the visible signature cleared at a later idle native
turn, not that Git permission or lifecycle operations succeeded. Existing lifecycle
and permission evidence contracts remain responsible for those stronger claims.

Ripwire quality-delta is not clean. Its name-based comparison conflates new
`advance`/`observe` symbols with same-name functions in other modules, reports
existing dynamically invoked fixture methods as dead, and flags supervisor churn.
New observer/episode functions also have substantial branch complexity (16 each);
these branches encode explicit unknown/delivery/verification/circuit states and
are covered with state-transition fixtures. The duplicated test setup deliberately
keeps delayed-delivery and stale-verification scenarios independent. No structural
metric is offered as proof of correctness. Test-gate reports broad supervisor
reachability; the full harness suite is the validation boundary.

The default python3 resolved to a Hermes venv without pytest. Tests use the system
Python explicitly with PYTHONPATH=. to avoid the previously observed package shadow.

Commit blocker: final staging failed with
`fatal: Unable to create '/home/operator/repos/harness-improvements/.git/worktrees/checkout8/index.lock': Read-only file system`.
An earlier staging operation reported exit 0, so the index contains only an earlier
partial staging snapshot; commit must re-stage the final files after correcting the
sandbox grant. No attempt to relocate the Git directory, bypass the sandbox, remove
an unverified lock, or modify another checkout was made. Commit/tested/PR lifecycle
stages are not recorded without the required signed committed revision.

2026-09-23 recovery: manager restored the checkout8 sandbox grant and explicitly
authorized commit/push/PR. All retained artifact digests and the completed suite log
matched the submission manifest after restart; no implementation changes occurred.
Recovery was reconciled through the facade before resuming the lifecycle.
