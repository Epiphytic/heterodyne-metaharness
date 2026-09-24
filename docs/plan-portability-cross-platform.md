# Plan: Harness portability & cross-platform (2026-09-23)

Synthesized from two independent brainstorm passes (Claude Opus 5.5 + Codex
GPT-6). Both converged on the same architecture; divergences resolved below.

## Stance

This is a seam-carving project, not a rewrite. ~90% of the work routes
already-divergent behavior (codex-vs-claude hooks, systemd-vs-launchd-vs-none,
beads-present-vs-absent) through three narrow seams plus the config file that
already exists (`harness-config.json`). Design now for the future remote mode
(beads-over-Marmot, remote hosts reporting back over Marmot); build almost none
of it.

## Architecture: five layers

    L4  install.py + workstream doctor     probe -> plan -> apply; degraded modes
    L3  harness/service.py (NEW)           systemd-user | launchd | foreground
    L2  harness/agents/ (NEW)              codex.py, claude.py - one adapter per CLI
    L1  harness-config.json (exists)       probed host values, persisted at install
    L0  harness/platform.py (NEW)          boot_id, file_lock, which(), service detect

Rules:
- `sys.platform` appears in exactly ONE file: `harness/platform.py`. It owns
  `boot_id()` (moved from supervisor.py:21), a `file_lock()` context manager
  centralizing the fcntl.flock pattern copy-pasted across store.py, delivery.py,
  review_runner.py, hook_config.py, usage/monitor.py, and `which_required()`.
- `harness-config.json` is the single persistence point for host-specific
  values (btq path, agent homes, marmot socket, service manager, interpreter).
  No new config formats.
- Precedence: explicit env (HERMES_HOME, CODEX_HOME, BEADS_EXECUTABLE) ->
  harness-config.json -> runtime probe -> safe default. Probe at INSTALL,
  persist; never silently re-probe at runtime (drift trap).
- Interpreter from `sys.executable`; tmux stays a hard requirement for running
  workstreams (no tmuxless mode - scope creep).

## Resolved decisions (were open in the brainstorms)

1. Foreground service (no systemd/launchd): print the cron/tick line + document
   manual path. NOT a managed nohup daemon (that is a daemon reimplementation).
2. btq discovery: install probes which('btq') AND the legacy sibling-repo hint,
   persists the result in config; runtime reads config only.
3. Review-gate default: no provider/model default at all - falls through to
   Hermes runtime provider resolution (review_runner.py already does this);
   explicit overrides (provider/base_url/api_key_env) stay as-is. Subtracting
   the fuelix default IS the pluggability work; no new registry.
4. flock: non-issue for macOS/WSL (both POSIX). platform.file_lock() raises
   "unsupported platform" on native Windows rather than faking msvcrt.
5. /usr/bin/env in tmux.py: red herring (stable on both targets) - fix in
   passing with the probe, not as its own work item.
6. Case-insensitive filesystems: lint/test asserting module filenames ==
   import names; reject workstream names differing only by case.
7. Remote mode: do NOT abstract tmux panes behind a terminal interface - pane
   locality gets redesigned in the remote-mode project itself.

## Work beads (ordered; each independently shippable)

| # | Bead | Type | Key acceptance |
|---|------|------|----------------|
| 1 | Platform seam + path purge | refactor | grep -r /home/operator harness/ bin/ install*.py = 0 hits; boot_id moved w/ macOS fallback (sysctl kern.boottime); no behavior change |
| 2 | Config-driven externals + graceful absence | behavior | harness runs with beads.enabled=false and marmot.enabled=false; missing tmux = one-line actionable error; btq path from config only |
| 3 | Service manager abstraction | behavior | Linux unit byte-identical to today (golden test); launchd plist w/ explicit PATH; foreground mode prints tick line; After=wn-agent dependency conditional |
| 4 | Agent adapter registry; Claude parity | behavior | no codex/claude literals outside harness/agents/; per-adapter: home(), available(), install hooks, resume_argv(), transcript_roots(), trust_args(); live start->stop both agents |
| 5 | Provider/model pluggability | behavior | grep fuelix = 0; review gate works with no model configured (Hermes default); usage adapters = config-supplied registry (+claude via ccusage) |
| 6 | Vanilla install pipeline + doctor + agent docs | behavior+docs | clean container w/ python3+tmux+1 agent CLI: install succeeds, doctor names degraded components, docs/install-agent.md walkthrough completes verbatim with fresh $HOME |
| 7 | macOS/WSL validation pass | test/bugfix | documented pass: Ubuntu, macOS, WSL2+systemd, WSL2-foreground; per-OS prerequisite lines in docs |
| 8 | Remote-mode seams | design+tiny code | all queue ops via Beads public methods; Transport protocol named around marmot.py envelope; host attribution column in store.py; docs/remote-mode.md |

B8 can be pulled forward (independent after B2). B6 depends on 1-5. B7 depends
on 1+3. Highest-risk gate: real fresh macOS launch + recovery, and WSL without
user systemd, preserving the rule that uncertain claims/approvals/identity are
never silently retried or accepted.

## macOS/WSL actual breakage list

- systemd --user: absent on macOS (launchd LaunchAgent, explicit PATH for GUI
  context); WSL has it only if enabled in wsl.conf -> foreground fallback.
- /proc boot_id: macOS uses sysctl kern.boottime; unverifiable = fail closed
  to recovery review (a process UUID would mislabel restarts as reboots).
- Process attestation (/proc/pid/cmdline+environ): hardest macOS port; return
  "unverified" when argv/env cannot be attested; provisioning waits for verified
  evidence.
- tmux socket path length: macOS sun_path 104 vs Linux 108 - keep default short
  socket name; honor TMUX_TMPDIR.
- Case-insensitive APFS: filename==import-name lint.
- WSL: use its Linux python/tmux/agents; warn against Windows-mounted checkouts
  (lock/worktree reliability).

## Vanilla install path (agent-focused)

Hard requirements: python3 >= 3.11, git, tmux, >=1 coding-agent CLI, repo
checked out. Optional w/ degraded modes: btq (beads disabled, queue hooks
skipped - fixes hook_config.py:194 raise), marmot socket (local-only event
logging), codex/claude CLI (per-agent skip), systemd/launchd (foreground),
npx/ccusage (usage adapters off).

docs/install-agent.md: numbered agent-executable steps - run X, expect Y, run
workstream doctor, finish with a hello-world workstream through
start/status/steer/stop/resume before declaring success. Commands with expected
outputs, not prose. Installer: --dry-run plan JSON; backups of modified hook
files; never invents Marmot recipients.

## Remote-mode hooks (now) / deferred (later)

Now: Transport protocol name for the marmot.py request/response envelope
(already transport-shaped; local unix-socket client = first impl); Beads class
public methods become the ONLY queue doorway; host (hostname + boot_id) column
in store.py runs while schema is young. Later (separate project): beads-over-
Marmot client/server (beads-marmot workstream), remote controller with stable
operation IDs, pane-locality redesign, cross-host migration (explicit
reconciliation, never automatic).
