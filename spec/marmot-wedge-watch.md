# Marmot inbound watchdog

Authority and lifecycle: [entrypoint](README.md), [changes](changes.md).

The watchdog is deterministic, stdlib-only, and never uses a model. No direct
live-home database access is permitted. The older task's `wn chats/messages`
recipe requires replacement: those CLI handlers open MarmotApp directly and
later operational evidence links that access to live-daemon WAL failures.

The current implementation is a detector/recovery core, not a deployable complete
watchdog. Deployment is blocked until an operator-reviewed socket exporter
provides authoritative chat enumeration and local receipt timestamps. A file
claiming `source=agent-control` is a trusted local interface, not cryptographic
proof. Do not enable a cron with an absent or unverified exporter.

## Evidence and thresholds

Read gateway heartbeat start time/mtime; skip the first 300 seconds of uptime.
Heartbeat age greater than 180 seconds independently permits recovery. Otherwise
read the newest Marmot inbound log timestamp and the configured 64-hex sender
allowlist. Local timezone applies to unzoned gateway timestamps. Missing or
malformed evidence is an error, not healthy silence.

Inventory schema: `source=agent-control`, `complete=true`, `captured_at` epoch,
`chats` array with `activity_sort_at` and `messages` array. Messages carry
`direction`, `from` (64-hex sender), `received_at` (local receipt epoch). Export
must be atomic and no more than 60 seconds old. Scan chats active at or after
gateway inbound minus 60 seconds. Only received messages from allowed senders
count. Trigger when newest qualifying receipt exceeds last gateway inbound by
strictly more than 300 seconds. Sender-authored event time cannot substitute for
receipt time. A partial or stale inventory cannot establish health.

## Recovery and audit

Hold a local exclusive lock through assessment and activation. Cooldown is 900
seconds since a trigger; healthy assessment resets it to zero as requested.
Only `/usr/bin/systemctl --user start hermes-gw-deploy.timer` may arm recovery.
No direct gateway restart or retrip timer. Manager owns installation, cron and
live fault injection.

Append and fsync activation intent before arming; save durable pending intent.
Append result with actual exit code afterward. Each includes activation ID,
epoch, reason and available evidence timestamps. `armed_timer=false` on intent;
`armed_timer=true` only for confirmed successful command return. This avoids the
contradictory claim of successful arming before executing the command. The log
measures watchdog activity before/after connector fixes and remains append-only.
A crash/timeout with unresolved intent blocks another arm until the manager
reconciles timer state and records recovery. Failed command returns are logged
and cooldown-limited. Healthy ticks emit nothing and append no activation.

Success emits one cause line followed by the fixed timer/replay notice. Errors
exit 1 with `marmot-wedge-watch ERROR:` on stderr, without logging secret values
or message text. Large/malformed external evidence and clock anomalies require
explicit verification before live deployment.
