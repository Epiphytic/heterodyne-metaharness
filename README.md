# heterodyne-metaharness

## What it is

heterodyne-metaharness is a control plane for coding agents. At its centre is `wsd`, a deterministic daemon that owns intake, pickup, approvals, delivery, commands, cron and reconcile. It calls an LLM for judgement, but never depends on one to make progress. The agents are the vanilla interactive CLIs (`claude`, `codex`), one session per bead and role, each running inside a platform sandbox that is the security boundary. Agent state comes from hooks, never from screen scraping. The operator works through Marmot: one group per workstream with a thread per bead, plus one control group. Beads hold task intent, decisions and the audit trail; a `wsd` journal holds operational state. An independent admin channel, `admind`, is the recovery path when anything else breaks. See ADR 0001 §2.

## Status

v1 in development. Implemented: platform seam, configuration layering, policy tiers. Next: admind, then the wsd core (see `docs/superpowers/plans/`).

What exists today is the `heterodyne` command with three subcommands, `platform`, `setup` and `config check`, plus the repository checks (the install-agnostic checker, pre-commit and CI). There is no daemon, sandbox, agent adapter or Marmot integration yet. The design for those is in the ADR.

## Architecture

This is the target design from ADR 0001 §3. None of these components exists yet.

```
                 Marmot (workstream groups + control group)     GitHub / Radicle
                              ▲  │                                    ▲ │
             render (pure)    │  │ cmd / reply / reaction / new msg   │ │ reviews (poll)
                              │  ▼                                    │ ▼
 ┌──────────────── wsd: control plane (deterministic, service unit) ─────────────────┐
 │ router ─ commands (/status /workstreams /approve …)              forge bridge     │
 │ scheduler (pickup, park/resume, cron, reconcile)   approvals (state machine)      │
 │ event bus ◄── hook socket (PreToolUse, PostToolUse, Stop, SessionStart …)       │
 │ beads adapter (btq)   session registry   policy engine   renderer   outbox        │
 └───────┬──────────────────────────────┬───────────────────────────┬───────────────┘
         │ judgement requests           │ launch/resume/steer       │ read/write
         ▼                              ▼                           ▼
  Hermes gatekeeper (LLM)      AgentRuntime adapters          Beads (Dolt):
  • rewrite + materiality      claude | codex, per role       source of truth
  • grey-zone permissions      in per-bead tmux session       + audit trail
  • answer/nudge agents        inside platform sandbox

 admind (independent service): own Marmot identity + 2-member group ─► superuser agent
```

## Quick start (development)

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). See [docs/install.md](docs/install.md) for details.

```sh
uv sync
uv run pytest
uv run heterodyne platform
export HETERODYNE_CONFIG_DIR=$(mktemp -d)   # a throwaway host config directory
uv run heterodyne setup && uv run heterodyne config check
```

- `heterodyne platform` prints the detected OS and the backends chosen for it, for example `{"os": "linux", "service_manager": "systemd", "sandbox": "bubblewrap"}`.
- `heterodyne setup` copies the example `config.toml` and `policy.toml` into the host config directory, fills in the platform backends, and never overwrites an existing file.
- `heterodyne config check` validates the merged configuration and prints every value with the layer it came from.

`HETERODYNE_CONFIG_DIR` is exported so that both commands use the same directory. Written as a prefix (`HETERODYNE_CONFIG_DIR=… uv run heterodyne setup && uv run heterodyne config check`), it would apply to `setup` only, and `config check` would read your real host config directory.

Before committing, run the repository checks: `uv run ruff check`, `uv run pyright`, `uv run pytest -q`, `python3 scripts/check_install_agnostic.py` and `uvx pre-commit run --all-files`.

## Configuration in one minute

Settings come from one precedence order (ADR 0001 §15). Lowest first:

| # | Layer | Location | In git? |
|---|---|---|---|
| 1 | Built-in defaults | `heterodyne/defaults/*.toml` in the package: tier rules, sandbox profile templates, timeouts, rendering. Adapters are defined, but no models are chosen. | Yes |
| 2 | Host config | `$HETERODYNE_CONFIG_DIR`, default `${XDG_CONFIG_HOME:-~/.config}/heterodyne/` on both OSes: `config.toml` (host settings, profiles, default roles, platform backends, integrations) and `policy.toml` (approvers and identities) | **No** |
| 3 | Workstream config | `$HETERODYNE_CONFIG_DIR/workstreams/<ws>.toml` | **No** |
| 4 | Bead override | a `role:<role>=<profile>` label (roles only) | n/a (in beads) |
| 5 | Environment and CLI | `HETERODYNE_*` variables and flags, limited to locations and debugging; they can't change policy or roles | No |

This table is the ADR's. Layers 1, 2, 3 and 5 are implemented; layer 4 (the bead override) arrives with the beads adapter in a later plan. Today the defaults hold the adapters, review mode, timeouts and tiers, and the only flag is `config check --workstream`.

**Host config and policy never go in git.** The repository is install-agnostic: it holds no host, user, home path, npub, group ID, database address, approver, credential or local repository. `examples/` holds placeholder-only samples that `heterodyne setup` copies from; nothing loads them as a layer. `policy.toml` is host-only, and a workstream can only tighten policy, through `[restrict]`.

The full reference is [docs/configuration.md](docs/configuration.md).

## Documentation

- [docs/install.md](docs/install.md): prerequisites, `heterodyne setup`, and the repository checks.
- [docs/configuration.md](docs/configuration.md): layers, policy, `[restrict]`, secret references and `config check`.
- [docs/security-model.md](docs/security-model.md): a summary of the security design.

## Design record

- [docs/adr/0001-workstreams-v2.md](docs/adr/0001-workstreams-v2.md): the architecture decision record.
- [docs/reviews/](docs/reviews/): the design review rounds and the responses to them.
- [docs/spikes/](docs/spikes/): the spike findings (S1 to S4) and the spike gate.

## License

Apache-2.0, see [LICENSE](LICENSE).
