# Module registry

| Module | Responsibility |
|---|---|
| `harness/store.py` | Atomic run, alias/lineage, event, inbox and outbox persistence; readable checkpoints |
| `harness/supervisor.py` | Shared start, observe, recover, report, steer and stop state machine |
| `harness/agents.py` | Provider registry and native CLI/session adapters |
| `harness/tmux.py` | Owned interactive panes, literal input, logs and process exit observation |
| `harness/workspace.py` | Idempotent creation of isolated, owned worktrees |
| `harness/marmot.py` | Validated, bounded agent-control protocol facade |
| `harness/delivery.py` | Acknowledged message retries, ordering and escalation |
| `harness/routing.py` | Authorized channel admission, deduplication and crash-durable input spool |
| `harness/lifecycle.py` | Bounded exact-session transcript observation and compression continuation |
| `harness/native_hook.py` | Native SessionStart identity registration and checkpoint context restoration |
| `harness/hook_config.py` | Preserve existing hooks/settings and trust only the installed Codex hook |
| `harness/capabilities.py` | Native context thresholds and web/search configuration shared by launches |
| `harness/memory.py` | Bounded repository-scoped session recall and Codex read-only JSONL fallback |
| `harness/beads.py` | Stable Beads queue identity, idempotent tasks, atomic claims and evidence-backed recovery |
| `harness/cli.py` | Operator/manager commands, migration, daemon and independent delivery thread |
| `install.py` | Idempotent scripts, service, policy and native-hook deployment with backups |
| `install_routing.py` | Idempotent local Marmot adapter integration with backup |
| `install_memory.py` | Pinned isolated auto-memory installation, recall wrappers and owned instruction blocks |
| `install_capabilities.py` | Native global compaction/search defaults and Semble MCP registration |
| `policy/` | Versioned Hermes SOUL and coding-delegation contract |
| `tests/` | Lifecycle, crash, identity, transport, installer and isolated real-tmux verification |
