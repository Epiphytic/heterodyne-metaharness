# Marmot watchdog deployment

Source: scripts/marmot-wedge-watch.py. Contract: spec/marmot-wedge-watch.md.
Manager explicitly replaced the direct CLI recipe with socket liveness plus log
freshness and heartbeat age. Never open the live Marmot home with wn or SQLite.
No package dependency or gateway/connector unit change is required.

Read-only live verification: documented account_list returned successfully.
`python3 scripts/marmot-wedge-watch.py --check` returned inbound_inactivity with
last inbound 1789906044.293 and gateway start 1789886556.6039681. No timer or
watchdog state was changed by that check. This shows the important limitation:
quiet live service is NOT silently healthy under the selected inactivity rule.
Do not report the original healthy-path live acceptance as passed.

Manager deploys the reviewed revision only after accepting the operational
inactivity threshold. Back up any existing script/state/cron privately with
verified hashes; install at ~/.hermes/scripts/marmot-wedge-watch.py. Repeat
installation should compare hashes and leave identical files unchanged. Run
--check first. A plain invocation can arm the timer: use a quiet maintenance
window, capture its output, and disarm hermes-gw-deploy.timer if testing only.

Native cron job specification (confirm native schedule syntax on creation):

```json
{"name":"marmot-wedge-watch","schedule":"every 5m","script":"marmot-wedge-watch.py","no_agent":true,"deliver":"marmot","failure_deliver":"marmot"}
```

Script path is relative to ~/.hermes/scripts. Reuse a matching existing cron ID;
never blindly create another. Verify first actual tick and failure delivery.
A healthy fixture must return 0 with empty stdout. Live health requires fresh
inbound traffic within threshold; socket success alone is not sufficient.

Fault injection: use --gw-log/--heartbeat copies, preserving the real socket
probe. Stale log should produce inbound_inactivity, stale heartbeat should
produce stale_heartbeat. Both arm only hermes-gw-deploy.timer; immediately stop
the timer after the test. If it fires, verify gateway restart, Marmot reconnect
AND an actual fresh inbound line. Outbound success is not inbound proof.
Retain intent/result JSONL, cooldown state, commands and service evidence.

Uncertain activation: match pending activation ID with its log records, timer
and gateway history. Back up state; clear pending only after external effects
are reconciled, retaining last_trigger. Rollback disables cron before restoring
backed-up files. Do not automatically restart the connector on probe failure.

Quality tool on the initial core reported dead-code/test-discovery findings and
standalone durability-helper duplication. It was not a clean quality gate.
