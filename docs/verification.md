# Verification — 2026-09-16

`python3 -m unittest discover -s tests -q`: **92 tests passed**. The printed SQLite locked exception is an injected routing-outage case, not a test failure.

Coverage includes isolated real tmux process start/exit/recovery, exact native resume without original prompt replay, pending-input hold across reboot, fixed reporting cadence while unknown or blocked, durable inbox and outbox, acknowledgement validation, remote idempotency keys, uncertain creation, safe stop, preserved unrelated sessions/worktrees, native-ID predecessor validation, compaction continuation, hook/settings preservation and repeated installation.

Native Codex hook installation was verified by its actual app-server `hooks/list` protocol: the exact installed command is trusted, existing hooks remain present, and installing twice makes no additional entry. No model session is started by this check. Real Marmot read-only `group_info` succeeded against the installed connector. Transport delivery/create tests use local mock sockets.

Deployment checks: `hermes-workstreams.service` active and enabled; user lingering enabled; `hermes-gateway.service` restarted and active with the new routing module. `workstream doctor`: zero pending outbound deliveries and zero inbound-spool records. Seven stale legacy manifests were imported as archived records; no historical task was reexecuted.

SOUL.md and coding-delegation skill were updated through the idempotent installer; original files and replaced scripts have `.pre-durable-harness` backups. The Marmot adapter has an `.before-workstream-routing` backup. Source and tests are tracked in this local repository.

Limits: reboot behavior was tested by changing the boot identity while killing/restoring real isolated tmux processes; the host itself was not rebooted. Tests do not ask real coding models to perform a task or create/send to a real Marmot group. No remote repository was configured for this newly created local project, so publishing is not part of this verification.
