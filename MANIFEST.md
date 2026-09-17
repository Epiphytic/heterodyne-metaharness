# Module registry

Authority: [active specification](spec/README.md). Operational checks and results:
[deployment runbook](docs/maintenance-deployment.md),
[execution evidence](docs/maintenance-execution-evidence.md).

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
| [harness/maintenance.py](harness/maintenance.py) | Admission of idempotent Beads tasks to the existing maintenance manager |
| [harness/governance.py](harness/governance.py) | Spec links/reference checks and historical lifecycle/evidence validation |
| [harness/cleanup.py](harness/cleanup.py) | Hash-verified file quarantine, replacements and repeat-safe execution |
| [harness/transcript_cleanup.py](harness/transcript_cleanup.py) | Structured identity guards, consistent backup and native detached-session deletion |
| [plugins/hermes-maintenance](plugins/hermes-maintenance/__init__.py) | Supported Hermes pre-tool workflow guard |
| [install_maintenance.py](install_maintenance.py) | Existing session binding, managed local AGENTS block and plugin installation |
| [prepare_context_cleanup.py](prepare_context_cleanup.py) | Exact reviewed file/memory cleanup manifest generation |
| [migrate_maintenance_cron.py](migrate_maintenance_cron.py) | Native retirement of duplicate supervision and failure-monitor update |
| [install_brain_pointer.py](install_brain_pointer.py) | Scoped SOUL mirror and canonical-spec pointer, with private external backup |
| [harness/brain.py](harness/brain.py) | Transactional per-session inventory offers and acknowledged checkpoints |
| [harness/brain_visible.py](harness/brain_visible.py) | Exact chat routing and durable visible receipts through the existing outbox |
| [harness/brain_hooks.py](harness/brain_hooks.py) | Shared native provider receipt adapters |
| [plugins/session-sync](plugins/session-sync/__init__.py) | Generic Hermes pre/post-turn notification hooks |
| [install_brain.py](install_brain.py) | Private reviewed hook/plugin installation plans and exact trust |
| [spec/](spec/README.md) | Small crosslinked active maintenance contracts |
| [harness/runtime_repair.py](harness/runtime_repair.py) | Native runtime staging, dependency gates, backups and guarded cutover |
| [prepare_runtime_packaging.py](prepare_runtime_packaging.py) | Exact native state-module packaging correction manifest |

Runtime operations: [runbook](docs/sqlite-runtime-repair.md),
[execution evidence](docs/sqlite-runtime-execution-evidence.md).

Brain notifications: [contract](spec/brain.md), [runbook](docs/brain-notifications.md),
[execution evidence](docs/brain-notifications-execution-evidence.md).
