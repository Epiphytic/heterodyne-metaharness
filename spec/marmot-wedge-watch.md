# Marmot inbound watchdog

Authority and lifecycle: [entrypoint](README.md), [changes](changes.md).

Standalone stdlib script `scripts/marmot-wedge-watch.py` uses no model and never
opens Marmot databases or invokes wn CLI. Manager's scope correction replaces
the obsolete chats/messages recipe with gateway log freshness, heartbeat age,
and a documented agent-control `account_list` Unix socket probe.

## Evidence and thresholds

Skip the first 300 seconds of gateway uptime. Validate heartbeat start time and
mtime. Probe the socket with a correlated v2 account_list request, five-second
overall deadline and 64KiB response bound. Require a valid nonempty account list.
Socket/transport/protocol failure exits 1 for manager escalation: restarting the
gateway alone does not repair a dead connector. Never restart wn-agent here.

With a healthy socket, heartbeat age greater than 180 seconds triggers recovery.
Otherwise validate MARMOT_ALLOWED_USERS as a nonempty list of 64-hex keys; native
Marmot inbound log lines are already sender-filtered. Read the newest matching
inbound timestamp; unzoned timestamps use local timezone. Missing/malformed log
or allowlist is an error. Inbound age strictly greater than --inbound-max-age
(default 300 seconds, minimum 300) triggers `inbound_inactivity`.

**Inactivity is not proof of a wedge.** A quiet chat and a wedged subscription
have identical signals here. This manager-selected policy may restart a healthy
quiet gateway at cooldown intervals. No message-arrival comparison or complete
chat inventory is claimed. `--check` prints the assessment without writing state
or activating timers; run it before deployment and assess the inactivity policy.

## Recovery and audit

Hold an exclusive file lock through assessment and activation. Cooldown is 900
seconds since a trigger; healthy assessment resets it to zero per original task.
Only `/usr/bin/systemctl --user start hermes-gw-deploy.timer` may arm recovery.
No direct gateway restart, retrip timer, unit edits, or .env writes.

Append and fsync intent before arming; persist pending intent atomically with
parent-directory fsync. Append result with actual exit code afterward. Each has
activation ID, epoch, reason and available evidence timestamps. Intent records
have armed_timer=false; only confirmed successful command return records true.
The log is append-only before/after evidence for connector fixes. Healthy ticks
append nothing and print nothing. A timeout/crash leaves an unresolved intent
and blocks another arm until manager reconciliation. Failed command returns are
logged and cooldown-limited. No uncertain automatic retry.

Success prints one cause line plus the fixed timer/replay notice. Errors exit 1
with marmot-wedge-watch ERROR on stderr, without secret values or message text.
Manager owns installation, cron, live fault injection and verified deployment.
