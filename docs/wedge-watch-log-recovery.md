# Watchdog log-source and timer repair

Contract: [watchdog specification](../spec/marmot-wedge-watch.md).
Task: btq-6h8. Implementation owner: persistent maintenance worker.

The incident reported a gateway.log silence after v0.21.1 while agent.log kept
receiving gateway records. The current installed native source already adds the
gateway handler before its logging initialization early return. No native core
patch or package upgrade is needed for this repair: the watchdog also reads
agent.log, and the opt-in native regression tests repeated fresh-process startup
with CLI logging initialized first. This verifies logging setup, not real network
ingress or live service restart. Quiet-chat inactivity remains an acknowledged
policy limitation; neither a fresh socket nor an old inbound record proves a wedge.

Timer commands previously treated an already-active timer's successful start as
recovery. Recovery now persists a baseline service execution marker, stops then
starts the timer, and checks a new completed successful service execution on a
later tick. A timeout or uncertain command leaves pending evidence for an operator.
Cooldown is one hour and survives healthy ticks. Missing log sources are errors,
not evidence that the gateway restarted.

## Manager deployment

Keep cron d92d58a3f045 disabled during review. Back up the installed watchdog and
its state/log privately. Install scripts/marmot-wedge-watch.py mode 0755 at
~/.hermes/scripts/marmot-wedge-watch.py from the reviewed commit. No dependencies.
Run --check with the current socket, allowlist, heartbeat and both logs; retain
sanitized assessment. Reconcile any old pending intent against systemd evidence
before enabling cron; do not simply delete uncertain state.

Run the optional native test using HERMES_RUNTIME_PYTHON pointing to the gateway
venv Python and HERMES_RUNTIME_SOURCE pointing to its source checkout:
`python3 -m unittest tests.test_gateway_logging_contract -v`.
It uses a temporary home and requires no service restart.

In an authorized quiet window, retain the service execution marker, re-arm the
sanctioned timer using stop then start, and verify its service actually executed
successfully. Confirm the gateway PID/start changed and heartbeat refreshed.
Within five minutes confirm new gateway.run records in gateway.log (retain only
timestamp/category evidence), plus actual inbound evidence from an authorized
message if supplied. If gateway.log remains silent but agent.log is fresh, the
fallback protects monitoring, but disclose the gateway.log check as failed.
Re-enable the existing cron only after the checks; preserve its identity and
no-agent mode. No live restart, cron change, or traffic test was done by the worker.
