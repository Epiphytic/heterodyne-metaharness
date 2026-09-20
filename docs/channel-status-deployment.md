# Channel status deployment

Contract: [current specification](../spec/channel-status.md).

The module `harness.channel_status` is a read-only CLI, for example:

```
python3 -B -m harness.channel_status --group GROUP_HEX
```

Gateway wiring must be generated from the merged canonical checkout so the shim
pins that package directory, never a disposable task worktree. Manager procedure:

```
cd /home/operator/repos/hermes-workstream-harness
python3 install_channel_status.py --adapter /home/operator/.hermes/plugins/marmot/adapter.py --output /tmp/channel-status-plan.json
python3 install_brain.py --apply /tmp/channel-status-plan.json --quarantine /home/operator/.local/state/hermes-quarantine/channel-status
```

Keep the
plan private (contains adapter source); do not paste it into chat. Repeat with a
new output/backup path to verify zero changes. Reload gateway using the established
operator deployment procedure. Supervisor restart is unnecessary for this hook.

Verify `/status` in a real managed Marmot group: queue order and counts agree with
the cached task view, pane is the exact owned worker, output has capture time and
bounded tail, latency under five seconds under normal load, and no gateway or
worker model turn starts. Test unknown channel usage with an authorized sender
only. No live message, installation, restart or deployment is claimed by source tests.
Retain the response and native activity evidence privately for close-stage evidence.
Rollback uses the install manifest's verified private adapter backup and removes
only the created shim; reload the gateway using the same operator procedure.

Native `/status` is reserved, so PluginContext.register_command cannot implement
this override. The reviewed adapter shim intercepts immediately before its existing
HERMES WORKSTREAM ROUTING block. Tests execute the generated hook against a fake
adapter whose following route raises, proving status returns before that route.
The direct connector RPC receives one stable idempotency key; no retry-create.

No dependency packages are added. Existing SQLite, tmux, gateway transport and
Python are reused. Cached projections may be incomplete/stale until ordinary
facade reconciliation; this command does not run `bd`, claim work, or update them.
