# Fulfilled questions: disabled core

This implements the additive fulfilled-question lifecycle from the approved design
`e8b5db3fb3e506ff909540dfb298b2c873446448`,
`docs/designs/2026-10-16-fulfilled-question-lifecycle.md`.
The daemon capability is **disabled** (the omitted/default-False host setting `[admind] fulfillment_enabled`). This change
does not install producer adapters, enable a service, migrate a live database, or
authorize deployment.

The implementation starts from the assigned pre-guard product base. Integration
with the current guard implementation and review of that integration are required
before activation. Existing inbound authorization gates are preserved here; this
base alone does not provide the current guard's fresh-policy guarantees.

## State and trust

A fulfillment receipt overlays an answered question or merge question. It never
changes the original wire status, answer history, or approval authority. Approval
asks are ineligible. A valid receipt releases capacity; a conflicting receipt
retains capacity and requires investigation. Original ask/list/get DTOs remain
unchanged. Human lists and details identify the separate fulfilled lifecycle.

The local API authenticates Linux socket peer UID against the service UID. These
callers are trusted administrators. A controller completion receipt is their
attestation, not an independent check that an external operation succeeded. The
daemon does not read evidence paths or execute the referenced operation. Evidence
contains bounded opaque identifiers and SHA256 digests.

An operator declaration is a distinct route. It queues complete, untruncated
request and consumed-answer context, evidence references, and a notice explaining
the absence of machine proof. Only an authenticated `!fulfill` reply to that exact
fully delivered notice can consume the current unexpired intent. Existing inbound
authorization, membership and latch gates still apply. Partial delivery, stale
answers, replaced intents and unrelated replies cannot confirm it.

## Explicit metadata operations

After a separately reviewed activation, the CLI surface is:

```sh
admind ask fulfillment get ASK --json
admind ask fulfillment apply --receipt receipt.json --json
admind ask fulfillment propose --ask ASK --declaration declaration.json --json
admind ask fulfillment dispose --ask ASK --expect-intent INTENT --reason operator-withdrawal --json
admind ask fulfillment reconcile --receipts receipts.json --plan
admind ask fulfillment reconcile --receipts receipts.json --apply
```

The frozen structs in `heterodyne.admind.fulfillment` define the closed v1 input
schema. `get` provides the store/ask/answer binding; producers must independently
supply the requested-operation and original-authority digests and exact consumed
answer IDs. They must not derive successful completion from an ask's answer alone.
Files must be owned regular files, opened without following symlinks. Receipts and
declarations are bounded to 24 KiB; manifests to 256 KiB and 20 distinct asks.
Reconciliation defaults to planning. It queries before each metadata application,
returns per-entry results, and stops on an unknown response with an exact
manifest-bound continuation. It never replays the external operation.

If a response is lost, query the exact ask and receipt identity before retrying
metadata. Identical valid retries are idempotent. Conflicting content is refused.
Never clear an uncertain result by changing database rows or replacing identities.

## Durability and limits

Store initialization performs the additive schema migration transactionally before
serving. One stable random store identity survives SQLite backup and restore.
Receipt, authority audit, capacity change and notice enqueue commit atomically.
Answer and status writers check receipt protection within their transactions.
Pending intents are limited to 40, one per ask, and expire after 24 hours; expired,
replaced and disposed identities remain tombstones. Receipt identities and shared
operation agreement are retained without pruning.

Canonical digest input is sorted-key compact JSON, UTF8, without a final newline,
with nonfinite numbers rejected. The domain prefix is
`admind-fulfillment-v1:<domain>\n`. Full ordered answer rows, immutable ask fields
and the persistent store identity bind the receipt. Duplicate JSON keys, unknown
fields, oversized messages and redaction-changing declarations are refused.

Rollback to an older writer must retain the additive tables. On restart the new
core revalidates retained receipts against actual original data. An old writer's
genuine changes become a visible conflict rather than silently releasing capacity.
No historical cancelled ask is migrated into a fulfillment receipt. Deployment,
consumer compatibility inventory and live recovery acceptance remain separate.

Confirmation notices retain the complete original request and consumed answer context. If that context
exceeds the notice bounds or would be redacted, the operator route refuses it; shortening only the
completion declaration cannot repair immutable context. This is an availability limit, not authority
to summarize, cancel the question, or claim completion. A genuine supported controller receipt may
still apply. Quoted answers carry durable inbound requester ownership; absent provenance quarantines
the notice and revoked ownership suppresses it.

Routine Store opens validate the current overlay schema without rebuilding receipt history. Daemon
startup/roll-forward rebuilds the validity index before serving requests; backup/restore callers must
complete that startup step before treating capacity projections as current.

## Per-daemon capability configuration

Keep `fulfillment_enabled` omitted in host `config.toml` while disabled. A boolean
`true` enables the existing protocol only for the daemon constructed from that
configuration; a restart is required. Workstream and environment overrides are
unsupported. The setting grants no approval or migration authority. Upgrade every
shared-config reader before writing the key; remove it before restarting an older
reader during an approved rollback. Preserve the newest database and all history.

Migration remains an explicit quiesced administrative operation independent of
this switch. Enabling on an unmigrated store refuses all question and merge asks;
complete the explicit quiesced migration before enabling and restarting, or ordinary
question/merge posting stops. Approval asks do not require this projection.
Before a planned disable, resolve unknown outcomes with Get/Inspect,
dispose pending intents and drain or durably hold notices while enabled. Disabled
Get/Inspect are unavailable and replies to already-issued notices use legacy
routing. Unresolved notices or outcomes require an explicit deployment decision;
the switch neither retracts them nor invents their result. No runtime activation
is included in this source change.
