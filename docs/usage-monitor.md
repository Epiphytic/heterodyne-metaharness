# Quota utilization monitor

Design for btq-586. This is a standalone harness component configured by the
hosting application. Existing approval, deployment, task and delivery ownership
remain authoritative; a timer never starts or changes worker tasks.

## Collection and intervals

An adapter callable accepts source name, source config, UTC poll time and storage
root, returning normalized samples. Config supplies a `module:callable` for new
providers without core edits. Built-ins are codex and zai. Each sample identifies
source, model (or explicit shared quota limit ID), interval, window start/end,
percent, measurement method and observation time. All numeric values are finite.

Codex reads token_count events in session JSONL files, retaining only quota fields
and timestamps, never transcript content. Newest valid snapshot per limit_id wins;
primary and secondary are classified by window_minutes, not their names. Reset
seconds are authoritative. Expired or stale reports do not imply zero usage.
Shared account limits are labeled `quota:<limit_id>` rather than falsely attributed
to the model that happened to emit them. ccusage codex supplies configured model
estimates for intervals lacking a live server quota.

ccusage runs as argv via npx with a pinned cache directory, UTC timezone and a
bounded cold-start timeout. Its daily model breakdowns supply daily, Monday-based
weekly and calendar monthly totals. For five-hour UTC epoch-aligned blocks, daily
buckets overlapping the block are counted in full: a deliberately conservative
upper-bound estimate, visibly labeled `daily-bucket upper bound`. This avoids
inventing intraday precision and includes both dates across midnight. It can alert
early. A future timestamped adapter can replace it without core changes.
Token ceilings must be positive and explicitly configured for every estimated
interval. No invented provider plan limits: examples stay disabled until the
operator configures ceilings. Missing ceilings are reported as monitor errors.

## Delivery and durable state

A dedicated SQLite database stores threshold identities and a frozen outbox batch
in one transaction, before any socket operation. Keys include source, model,
interval, window epoch and threshold. All newly crossed thresholds (including an
initial observation above several thresholds) are ascending in one message per
sample. Pending batches survive restart. Delivery retries reuse the original
idempotency key and original text. A lost socket acknowledgement therefore does
not create a new message identity; connector retention bounds exactly-once effects.
The process holds a local flock to prevent overlapping timer/manual polls.

Delivery awaits the installed MarmotAgentControlClient over the agent-control
socket and uses the `text` field. No wn CLI, gateway model dispatch or alternate
transport is used. Client module, socket, account and OPS group are configured;
no deployment-specific identifiers are compiled into the harness.

Failures are retained in JSONL and monitor health (waiting-on-agent), queued as
OPS error messages, and return nonzero. Each failed source is isolated so healthy
sources still report. Delivery failures preserve pending messages and local error
health; a broken socket cannot report its own failure through that same socket.
The next timer retries. No false successful send or silent exception suppression.
Manager confirmed there is no installed channel setter: persist health.json and
prefix OPS error text with `[waiting-on-agent]`. Remote channel integration belongs
to btq-2sa and is not a dependency of this monitor.

## Operations and verification

Host config.yaml contains usage_monitor settings. JSON config is also supported;
YAML uses the host's existing PyYAML installation. The core monitor uses stdlib.
A user systemd oneshot/timer runs every ten minutes; installation and activation
remain manager-side. State defaults to ~/.hermes/workstreams/usage-monitor/.
Daily JSONL history is retained for 90 days. Pending deliveries are never expired;
threshold state survives through window end plus 90 days to prevent repeats.

Tests use synthetic JSONL/ccusage fixtures, injected clocks and async fake clients.
They cover reset equality, month/week boundaries, conservative five-hour estimates,
shared-limit selection, stale fallback, malformed data, threshold jumps, restart
and uncertain-send retries, retention and source failures. No test sends to Marmot.
Deployment must verify configured ceilings, complete OPS identity, socket permission,
channel-status integration and timer operation before live-verification is recorded.

## Adapter and deployment contract

An extension `async def collect(source, config, now, root)` returns
`harness.usage.adapters.Collection(samples=[Sample(...)], errors=[...])`.
Samples must match the configured source name. Partial adapter errors preserve
valid measurements and still mark the poll unhealthy. Codex reads the last 8 MiB
of each recently modified JSONL; a missing fresh quota falls back to ccusage.
Adapter configuration is trusted operator configuration, never channel input.

Manager installation:

1. Merge the reviewed commit into the stable harness checkout.
2. Merge the `usage_monitor` object from deploy/usage-monitor.example.json into
   the host config.yaml. Set full account/OPS group identifiers, socket and plugin
   import path. Supply positive token ceilings in all four intervals for enabled
   sources; only then set enabled=true. Ceilings are estimates of the plan, not
   provider claims. No live configuration is modified by this change.
3. Verify the interpreter and WorkingDirectory in deploy/harness-usage-monitor.service
   match this host; its Python must import both the harness and installed Marmot
   adapter. The supplied path targets the current Hermes host runtime.
4. Install the service/timer into the user systemd unit directory, reload and
   enable the timer under normal manager deployment authority. This repository
   does not activate a service during tests or installation planning.
5. Run one authorized poll; inspect health.json, retained JSONL, service journal
   and OPS receipt. Verify `[waiting-on-agent]` errors using a controlled failure
   only if the operator authorizes live test alerts. Record live evidence in a
   retained path and keep quota configs/secrets out of public review bundles.

The worker verified real read-only ccusage output and the installed client import;
all automated tests use synthetic provider data and fake delivery. OPS permission,
quota ceilings and timer activation are manager-side deployment verification.
