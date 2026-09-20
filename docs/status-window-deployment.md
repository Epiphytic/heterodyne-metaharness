# Status window deployment

Contract: [status](../spec/status.md), [change lifecycle](../spec/changes.md).
No new package, model hook, permissions, native identity or Marmot protocol change.

Operator reviews/merges the signed private patch, retains a coherent harness SQLite
and checkpoint backup, updates the clean canonical checkout, then restarts
`hermes-workstreams.service`. Verify its cwd/revision and active status. Store startup
adds status_limits and a recipient lookup index idempotently; existing notices,
events, outbox attempts and delivery IDs remain intact. Old history retains old
hashes; each scope admits one baseline event after upgrade. No historical notices
are resent by the migration. Rollback may retain the additive table/index.

During ordinary activity, inspect status_notices/outbox admission for the affected
run and group and the service journal: alternating unchanged reports do not create
new outgoing rows; minor progress wording changes admit at most once per 300 seconds
of unchanged semantic state; repeated duplicate ERROR logs have the same bound.
Record actual observation interval/counts and a distinct real transition when one
occurs. Do not synthesize an approval or interrupt a production task for testing.
Pending transport retries must still use their existing IDs. Preserve original
worker/manager sessions and all native approval/recovery holds.

Fixtures cover alternating variants, pending/delivered history, restart, journal
throttle, rate boundary, changed task/state, independent recipients and kinds,
conservative normalization and duplicate event replay. These are isolated checks,
not live deployment evidence. Retain exact-commit and post-merge suite logs with
SHA256, then actual operator deployment/live verification before closure.

Limits: normalization recognizes harness display wrappers, not arbitrary semantic
paraphrases. The broad progress limiter bounds other wording churn. A materially new
progress summary in unchanged state may wait up to the rate boundary and a subsequent
heartbeat attempt; explicit blocked/completed/failed notices are not rate-limited.
True repeated state transitions remain visible. Suppressed event IDs are retained
for idempotency, while journal output is throttled. No retention purge is included.
