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
inbound timestamp across `--gw-log` (gateway.log) and `--agent-log` (agent.log).
Use the newest valid record from either source; a silent/missing gateway file
must not hide fresh agent.log ingress. Unzoned timestamps use local timezone.
No usable timestamp in either source is an error. Inbound age strictly greater
than --inbound-max-age (default 300 seconds, minimum 300) triggers inactivity.

**Inactivity is not proof of a wedge.** A quiet chat and a wedged subscription
have identical signals here. This manager-selected policy may restart a healthy
quiet gateway at cooldown intervals. No message-arrival comparison or complete
chat inventory is claimed. `--check` prints the assessment without writing state
or activating timers; run it before deployment and assess the inactivity policy.

## Recovery and audit

Hold an exclusive file lock through assessment and activation. Cooldown is 3600
seconds since a trigger; healthy ticks and startup grace do not reset it.
Read `hermes-gw-deploy.service` ExecMainStartTimestampMonotonic before writing
the durable activation intent. Re-arm only the sanctioned timer with explicit
`systemctl --user stop hermes-gw-deploy.timer`, then `start` of the same timer.
No direct gateway restart, wn-agent restart, unit edits or .env writes.

Append/fsync intent and atomically persist pending state before either command.
A command failure/timeout or interruption retains uncertain intent for operator
reconciliation, never automatic repetition. Successful stop/start records
`armed`, **not successful recovery**. On subsequent cron ticks, verify a strictly
new service execution marker, completed service state, Result=success and
ExecMainStatus=0. Record actual success/failure in the audit log before clearing
pending state. An old marker or still-running service is pending; after 300
seconds it is an error requiring reconciliation. Polling here is deterministic
cron observation, with no model and no synchronous 90-second wait.

Healthy ticks print nothing. Repeated error notices are globally limited to one
per hour in a separate durable error checkpoint; suppressed ticks return zero
without clearing uncertain activation state. Read-only --check is not suppressed.
Error output contains no secret or message content.
Manager owns script installation, cron and live verification. Native logging
regression exercises CLI initialization followed by gateway mode across fresh
processes using an isolated home; it does not assert a production restart.
Deployment checks must retain actual execution and log-freshness evidence.
