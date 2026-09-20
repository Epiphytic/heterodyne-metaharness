# Marmot watchdog deployment — blocked pending inventory integration

Source: `scripts/marmot-wedge-watch.py`; contract: `spec/marmot-wedge-watch.md`.
Ten isolated tests currently pass. No live timer was armed, no service restarted,
and no live Marmot database opened. This is not completion evidence.

The task recipe predates the subsequent live-home WAL incident. Read-only source
inspection shows `crates/cli/src/commands/chats.rs:chats_command_with_runtime`
uses MarmotApp; the CLI is not a socket query. The operational incident report is
`~/.hermes/skills/platforms/marmot-platform/references/session-db-corruption.md`.
Agent-control has per-group timeline pagination and observed_at fields, but no
verified complete chat enumeration was found. Do not substitute sender timestamps
or a hand-maintained subset of groups and claim complete detection.

Manager/connector owner must supply a socket-only inventory exporter satisfying
the active contract, then integration tests must cover pagination, excluded
senders, missed inbound receipts, empty history, export failure and atomicity.
The inventory currently defaults to ~/.hermes/state/marmot-inbound-inventory.json.
This dependency must be resolved before installation or the cron is enabled.

After that review, manager deploys with a verified private backup of any existing
script, state and cron definition; installs the reviewed standalone script at
~/.hermes/scripts/marmot-wedge-watch.py; verifies a healthy invocation exits 0
with empty stdout; then creates the native cron using this job specification:

```json
{"name":"marmot-wedge-watch","schedule":"every 5m","script":"marmot-wedge-watch.py","no_agent":true,"deliver":"marmot","failure_deliver":"marmot"}
```

Confirm the native cron API's schedule representation before applying. The
script field is relative to ~/.hermes/scripts, not an absolute path. No model
belongs in execution. Verify the first real tick reports success.

Fault injection remains manager-side in a quiet window: override --gw-log or
--heartbeat to private fixtures, retain real verified socket receipt evidence,
arm only hermes-gw-deploy.timer, and immediately disarm it. Inspect the intent
and result JSONL plus cooldown state. If it fired, verify service health, Marmot
reconnect and actual fresh inbound receipt. Outbound success is not inbound proof.

Uncertain activation: inspect pending activation ID, its log records, timer and
gateway service history. Preserve the state backup; clear pending only after
actual external effects are reconciled, retaining last_trigger. Do not blindly
retry on timeout. Rollback disables the cron before restoring backed-up files.
