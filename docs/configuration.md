# Configuration reference

This page describes the configuration loader as it exists today (`src/heterodyne/config/`). The design is ADR 0001 §15. Later plans add settings; the rules below stay.

## Locations

| What | Chosen by (first match wins) |
|---|---|
| Host config directory | `HETERODYNE_CONFIG_DIR`; else `$XDG_CONFIG_HOME/heterodyne`; else `~/.config/heterodyne` |
| State directory | `HETERODYNE_STATE_DIR`; else `$XDG_STATE_HOME/heterodyne`; else `~/.local/state/heterodyne` |

- A leading `~` in `HETERODYNE_CONFIG_DIR` or `HETERODYNE_STATE_DIR` expands against `HOME`.
- A relative `XDG_CONFIG_HOME` or `XDG_STATE_HOME` is ignored, as the XDG Base Directory spec requires.
- An empty variable counts as unset.
- `admind` writes `<state>/admind/` (its database, audit log, hook socket and Marmot home) and reads `<state>/alerts/`.

The host config directory holds:

- `config.toml`: host settings (profiles, default roles, platform backends, sandbox allowlists, integrations);
- `policy.toml`: approvers, identities and tier overrides;
- `workstreams/<ws>.toml`: one file per workstream.

A missing file is an empty layer. A file that isn't valid TOML is a config error naming the file. A missing `policy.toml` gives an empty policy: no approvers and the default tiers.

## Layers

The loader merges these layers, lowest first. A higher layer replaces a lower one key by key. Tables merge recursively; arrays and scalars are replaced whole.

| # | Layer | Location | In git? | Source label in `config check` |
|---|---|---|---|---|
| 1 | Built-in defaults | `defaults/defaults.toml` inside the `heterodyne` package | Yes | `defaults` |
| 2 | Host config | `config.toml` in the host config directory | **No** | `host:config.toml` |
| 3 | Workstream config | `workstreams/<ws>.toml`, loaded only with `--workstream <ws>` | **No** | `workstream:<ws>.toml` |
| 4 | Bead override | a `role:<role>=<profile>` label (roles only) | n/a | not implemented yet |
| 5 | Environment | the three `HETERODYNE_*` variables below | No | `env:<VARIABLE>` |

`policy.toml` is not a layer. It is read on its own and never merged with anything (see [Policy](#policy-host-only)).

`examples/` is not a layer either. Nothing loads it: `heterodyne setup` copies from it. Host config and policy never go in git; `.gitignore` excludes `config.toml`, `policy.toml`, `workstreams/` and `state/` at the repository root, and `*.local.toml` anywhere.

### Built-in defaults

The shipped defaults define the known adapters (`claude-code`, `codex`), the review mode when both roles use the same model (`adversarial`), timeouts, and the action-class tiers. They name no models. It also sets each adapter's executable (`[adapters.<adapter>] binary`) and admind's defaults (see below).

| Tier | Default classes |
|---|---|
| `auto_approve` | `worktree_edit`, `run_tests`, `local_git`, `dependency_install` |
| `escalate` | `push_branch`, `open_pr`, `merge_pr`, `deploy`, `notify`, `new_egress_host` |
| `hard_deny` | `modify_harness_config`, `modify_policy`, `modify_sandbox` |
| `locked` (never lowered) | `modify_harness_config`, `modify_policy`, `modify_sandbox` |

`locked` is not a tier. It lists the classes whose tier host policy can never lower.

### Host config (`config.toml`)

See `examples/config.toml` for a commented sample.

- **No policy keys.** A top-level key that belongs in `policy.toml` (`approvers`, `identities`, `operators`, `tiers`, `hard_deny_rules`, `action_registry`, `tier_floor`, `policy`) is an error: "belong in policy.toml".
- **`[platform]`:** `os`, `service_manager` and `sandbox`. `heterodyne setup` writes them from the detected platform: `systemd` and `bubblewrap` on Linux, `launchd` and `seatbelt` on macOS (ADR 0001 §3.2). They are recorded once, at setup, and not re-probed. `wsd run` builds its agent runtime from `sandbox` (ADR 0001 §7):
  - `openshell`: the OpenShell runtime (plan 4; see [packaging/sandbox/README.md](../packaging/sandbox/README.md));
  - missing or `none`: no runtime, so wsd holds every workstream;
  - `bubblewrap` or `seatbelt`: no runtime either, with a one-line notice when wsd starts. Setup records `bubblewrap` on Linux until persistent crash-loop accounting lands (plan 3's P1, btq-g08sd), so a Linux host runs no agents until the operator changes `sandbox` to `openshell` (see [wsd.md](wsd.md) §5);
  - anything else: a configuration error, and `wsd run` exits with 78.
- **`[profiles.<name>]`:** a profile is an `adapter` plus an optional `model`. The `adapter` must be one of `adapters.known`. `model`, if present, must be a string.
- **`[roles]`:** role name to profile name.
- **`[sandbox]`** must be a table. It holds two host allowlists:
  - `egress_approved`: a list of hosts a workstream may add as extra egress.
  - `ro_mounts_approved`: a list of **absolute** directory paths a workstream may add as extra read-only mounts. A missing or empty list allows none.

  It also holds the OpenShell runtime's settings, read once when wsd starts. Any other key is an error. Errors name the key, never its value.
  - `image` (default `localhost/heterodyne-agent:1`): the workload image, built from `packaging/sandbox/Containerfile`.
  - `openshell` and `podman` (defaults `openshell`, `podman`): the commands wsd runs.
  - `tool_env`: a table of extra environment variables for those two commands only, for example the `PATH` and `CONTAINERS_CONF` of a side-by-side podman 5. It is never passed into a sandbox. `HOME`, `USER`, `LANG` and `TERM` can't be set.
  - `max_lifetime_minutes` (10 to 1440, default 120) and `stop_margin_minutes` (1 to 120, default 15, less than the lifetime): a session is stopped at a turn boundary inside the margin before its deadline, or at the deadline, and then resumed (D13). The deadline is the earlier of the lifetime and the login's expiry less the margin.
  - `probe_allowed_host` and `probe_denied_host` (defaults `api.openai.com`, `example.org`): the self-test's egress controls.
  - `agent_probe_seconds` (30 to 900, default 240): how long the agent-path self-test may take.
- **`[integrations]`:** external tools (btq, the `wn-agent` socket and its token) are configured by location here. `wsd` reads `[integrations.beads]` (see [wsd.md](wsd.md#1-configuration)); nothing reads `[integrations.marmot]` yet.
- **`[wsd]`:** the workstream daemon's timers and limits; see [wsd.md](wsd.md#1-configuration).
- **`[accounts.<name>]`** (ADR 0001 §4.4 D1): a named login for one adapter, with exactly two keys:
  - `adapter`: one of `adapters.known`;
  - `login_dir`: the directory that holds the login, an absolute path or `~/...` (expanded against `HOME`). It is a path, not a secret, so a `{ file }` or `{ command }` reference is refused. The key is `login_dir`, not `auth_dir`, because the secret-name check flags any key with an `auth` segment.
  - Names are plain identifiers (`[A-Za-z][A-Za-z0-9_-]{0,63}`), and `default` is reserved for the adapter's own login. A name with a secret-like segment, such as `codex-auth`, is refused by the secret scan; pick another.
  - Every adapter also has an implicit `default` account: `~/.claude` for `claude-code` and `~/.codex` for `codex`. `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are not read, because the environment can't select a login.
  - No two accounts, the implicit defaults included, may share a login: their login directories can't be equal or nested, and no login file (`.credentials.json`, `auth.json`) may resolve, through a symlink or a hard link, to another account's file.
  - **Currently refused on every adapter.** Accounts are accepted only on an adapter for which spike S7 demonstrated login binding (§4.4 D9), and it hasn't for either yet.
- **`profiles.<p>.accounts`**: an optional, non-empty list of account names, in order of preference, each for the profile's adapter. Without it the profile uses the adapter's default login. `default` can't be listed.
- **`profiles.<p>.failover`**: `"none"` (the default) uses only the first account, and usage only defers work. `"next"` moves to the next eligible account; it is refused unless S7 demonstrated a trusted usage read for the adapter (§4.4 D6), which it hasn't for either yet. Whether a provider's terms allow switching accounts is the operator's call.
- **`[usage]`** (§4.4 D3–D5; defaults shown): `reserve_percent = 5` (an integer from 0 to 50), `stale_minutes = 30`, `unknown_backoff_minutes = 30`, `untrusted_max_defer_minutes = 60`, `min_recheck_seconds = 60`, `max_window_hours = 192`. The others are positive integers, with `min_recheck_seconds` at most 60 × the smallest of `stale_minutes`, `unknown_backoff_minutes` and `untrusted_max_defer_minutes`, and `untrusted_max_defer_minutes` and `unknown_backoff_minutes` each at most 60 × `max_window_hours`. Unknown keys are refused.
- **No login path in errors.** No error names a login directory or file; a login that can't be resolved (a symlink loop, for example) is reported by account name and reason only.

### Workstream config (`workstreams/<ws>.toml`)

See `examples/workstreams/example.toml` for a commented sample. The file is loaded only by `config check --workstream <ws>`; if it doesn't exist, the workstream layer is empty.

- **Allowed top-level tables:** `roles`, `repos`, `sandbox`, `cron`, `render`, `timeouts` and `restrict`. Any other key is an error, so no policy key can be set here. `[accounts]` and `[usage]` get their own error: they are host-only, and workstreams choose profiles, never accounts.
- **`[roles]`** must be a table of strings, and each value must name a profile that exists in the defaults or host config.
- **`[repos]`** names the workstream's repositories for `wsd`: absolute or `~/` paths, one of them `default` (see [wsd.md](wsd.md#1-configuration)).
- **`[sandbox]`** may contain only `extra_ro_mounts` and `extra_egress`, both lists of strings:
  - every `extra_egress` host must be listed in the host's `sandbox.egress_approved`;
  - every `extra_ro_mounts` entry must be an absolute path inside one of the host's `sandbox.ro_mounts_approved` entries.
- **Read-only mount containment.** Both the workstream path and the host entries are resolved first (`..` removed, existing symlinks followed), so a path cannot climb or link out of an approved tree. Containment compares whole path components: an approved `/srv/data` covers `/srv/data/x`, but not `/srv/database`. A relative path is rejected.
- **`[restrict]`** is policy tightening. It is validated, removed from the merged values, and applied to the policy (see [`[restrict]`](#restrict-workstream-tightening)).

### Environment

Only these three variables are accepted:

| Variable | Sets | Source label |
|---|---|---|
| `HETERODYNE_CONFIG_DIR` | `paths.config_dir` (and the host config directory) | `env:HETERODYNE_CONFIG_DIR` |
| `HETERODYNE_STATE_DIR` | `paths.state_dir` (and the state directory) | `env:HETERODYNE_STATE_DIR` |
| `HETERODYNE_LOG_LEVEL` | `debug.log_level` | `env:HETERODYNE_LOG_LEVEL` |

Any other variable starting with `HETERODYNE_` is an error: "environment overrides are limited to [...]". Variables without the prefix are ignored. The environment covers locations and debugging only; it can't change policy or roles. The only command-line flag today is `config check --workstream`.

### Admin channel (`[admind]`)

| Key | Meaning |
|---|---|
| `profile` | Required. The admin agent's profile from `[profiles]`; claude-code only for now. |
| `workdir` | Working directory of the admin agent. Default `~`, but set a dedicated directory: Claude Code does not persist workspace trust for the home directory (see [admind.md](admind.md#8-troubleshooting)). |
| `restart_units` | The only units `!restart` accepts. Default none. |
| `chunk_chars` | Reply chunk size, 200 to 60000. Default 4000. |
| `alert_poll_seconds` | How often the alerts directory is polled. Default 5. |
| `group_check_seconds` | How often the admin group is checked. Default 60. |
| `start_timeout_seconds` | How long to wait for the agent to start. Default 60. |
| `turn_notice_seconds` | How long a turn may run before the operator is told later messages are held. Default 1800. |
| `group_name` | Name of the admin group. Default `heterodyne admin`. |
| `summarizer` | Optional. The name of a profile from `[profiles]` used to summarize long replies. Default none: without it, a reply longer than the verbatim limits goes to the batched backstop. claude-code only for now, like `profile` (any other adapter is a configuration error). It runs headless, without tools, hooks, MCP servers or user settings, and the profile's `args` are ignored. See [admind.md](admind.md#4-using-it). |
| `reply_verbatim_lines` | A reply of at most this many lines (and `reply_verbatim_chars`) is sent as it is; a longer one is summarized or batched. Integer, 1 to 200. Default 8. |
| `reply_verbatim_chars` | The character limit for a verbatim reply. Integer, 50 to 60000. Default 800. |
| `ask_bump_hours` | How long an open ask may go without activity before admind bumps it automatically: a reminder threaded to its card, as `!asks bump` posts. Activity is the card being posted, bumped or repeated, or an operator's reply, reaction, note, answer, decision or `!details` on it. Integer hours, 0 to 720; 0 turns automatic bumps off. Default 12. Checked every 5 minutes. See [admind.md](admind.md#10-asks-and-approvals-interim). |
| `approve_bead` | Optional. The absolute path of beads-task-queue's `bin/approve-bead` (for example `<BTQ-LIVE>/bin/approve-bead`), which admind runs to read and decide btq approval asks from Marmot. Default none: approval asks are then refused ("Approval asks are not configured on this host"), and question and merge asks still work. The path must be absolute (`~` is not expanded) and name an executable file; anything else is a configuration error, `[admind] approve_bead must be an absolute path to an executable`, which never repeats the value. See [admind.md](admind.md#10-asks-and-approvals-interim). |
| `marmot.wn_agent` | The wn-agent executable. Default `wn-agent`. |
| `marmot.home` | Marmot home directory. Default `<state>/admind/marmot`. |
| `marmot.relays` | Required. A list of ws:// or wss:// relay URLs. |

`[admind]` is host-only: a workstream file can't set it.

**`approve_bead` and rollback.** `heterodyne config check` does not read `[admind]`. admind checks it when it starts, and so does every `admind` command that reads the configuration (`admind ask list` is a quick check; [admind.md](admind.md#setting-it-up) says how to read its exit status before a restart). A bad value exits 78 (`EX_CONFIG`), so the unit does not restart-loop. An admind older than the relay does not know the key and also exits 78 on it. To roll back the code, remove `approve_bead` from `config.toml` **first**, then check out the older commit and restart. Before removing it, check that `admind ask list` shows no ask `deciding` or `uncertain`: such an ask cannot be read back without the key. Its operator gets one "could not check whether this decision … was recorded" notice, and the ask waits until the key is set again ([admind.md](admind.md#outcomes-and-recovery)). The relay's database tables are left in place, and the older code ignores them.

## Policy (host-only)

`policy.toml` is read **only** from the host config directory. No other layer can set any of its keys: the loader rejects them as a startup error, not a silent ignore. See `examples/policy.toml`.

- **Allowed keys:** `approvers`, `identities`, `operators`, `tiers`, `hard_deny_rules`, `action_registry`, `tier_floor` and `policy`. Any other top-level key is an error ("unknown keys").
- Four are interpreted today:
  - `approvers`: a list of names.
  - `identities`: a table of tables of strings, one table per approver, for example `marmot_npub`, `github` and `radicle_did`.
  - `operators`: a list of approver names allowed to use the admin channel. Each must be in approvers. Every entry with an identities.<name>.marmot_npub is an admind operator; admind needs at least one. A name must be 1 to 128 characters with no control characters, and two operators can't share a key. Operators are added to and removed from the running group with `admind operators add|remove NAME` (see [admind.md](admind.md#operators)); an entry in the file alone authorises no one.
  - `[tiers]`: re-tiering, as `class = "tier"`. The class must be a default action class and the tier one of `auto_approve`, `escalate` or `hard_deny`. A host may raise or lower any class **except a locked one, which can never be lowered**.
- The others are reserved for later plans: accepted, but not used.

The effective tiers are computed as the default tiers, then the host `[tiers]` overrides, then the workstream `[restrict]` table.

## `[restrict]` (workstream tightening)

A workstream can only tighten policy, and only through `[restrict]`. It has two lists of action-class names:

- `escalate`: move classes to the `escalate` tier;
- `hard_deny`: move classes to the `hard_deny` tier.

Rules:

- No other key is allowed in `[restrict]`.
- Each entry must be a known action class.
- Tightening only. An entry that would lower a class below its current tier (after the host overrides) is rejected at startup: for example `escalate = ["modify_policy"]`, because `modify_policy` is already `hard_deny`. Naming a class already at that tier is allowed and changes nothing.

Example `workstreams/<ws>.toml`:

```toml
[restrict]
escalate = ["dependency_install"]   # auto_approve -> escalate for this workstream
hard_deny = ["deploy"]              # escalate -> hard_deny for this workstream
```

`config check` validates `[restrict]` and computes the effective tiers, but prints only the approvers from the policy, not the tiers.

## Secret references

Secrets never appear inline in any layer (§15). A setting that holds a secret refers to it with a **reference**: a table with exactly one key, `file` or `command`, whose value is a non-empty string.

```toml
[integrations.marmot]
auth_token = { command = "<command-that-prints-the-token>" }
# or
auth_token = { file = "<path-to-token-file>" }
```

An empty value, a non-string value, both keys, an extra key or an empty table is not a reference. The loader validates references; nothing resolves them yet.

### The secret scan (best effort)

Every layer is scanned before any other validation of that layer: defaults, the raw `HETERODYNE_*` environment, `config.toml`, `policy.toml` and the workstream file. The scan walks every key and value, including arrays and arrays of tables. It has two checks:

- **Name check.** A key whose name looks secret must hold a reference. The name is split into segments on `_`, `-`, `.` and camelCase, then lowercased. It is secret-named if any segment is one of `credential(s)`, `cred(s)`, `auth`, `authorization`, `bearer`, `cookie(s)`, `passphrase`, `pass`, `password`, `passwd`, `pwd`, `secret(s)`, `token(s)`, `nsec`, `apikey`, `privkey` or `session(s)`, or if its last segment is `key` (`key`, `api_key`, `private_key`). Matching is per segment, so `keyboard`, `author` and `key_file` are not flagged. `public_key` is exempt.
- **Value check.** Any string (a value, a key, or a reference's target) that looks like secret material is rejected whatever its key is called. The patterns are an `nsec1` key, a PEM private key block, `sk-`, GitHub (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`) and Slack (`xoxb-`, `xoxa-`, `xoxp-`, `xoxr-`, `xoxs-`) token prefixes, AWS access key IDs, and JWTs, each with a minimum length.

Both checks are heuristics and can't be complete. The second line of defence is the gitleaks gate in pre-commit and CI (see [install.md](install.md#repository-checks)). A per-table key allowlist, the complete fix, comes when later plans define those keys.

### Redaction

Error messages never echo a secret. A scan error names the layer, the indexed path (for example `policy.toml: identities.op.credentials[0].api_key`) and the kind of secret, but not the value. Every other config error that quotes a key or value renders it through one helper, which replaces any string the value check flags with `<redacted <kind>>`.

## `heterodyne config check`

```sh
heterodyne config check [--workstream <ws>]
```

It loads and validates every layer and the policy. On success it prints each merged value as `key = value    (source)`, then the approvers, then a note if fewer than two distinct models are configured, then any account warnings, and exits 0. Each `accounts.<name>.login_dir` prints as `<hidden>`, with its source. The warnings, which don't change the exit code, are for a profile listing more than one account with `failover = "none"` (only the first is used) and for `failover = "next"` without accounts. On any error it prints `config error: <message>` to stderr and exits 1.

Output after `heterodyne setup`, shortened:

```text
adapters.known = ['claude-code', 'codex']    (defaults)
timeouts.gatekeeper_seconds = 60    (defaults)
tiers.escalate = ['push_branch', 'open_pr', 'merge_pr', 'deploy', 'notify', 'new_egress_host']    (defaults)
platform.os = 'linux'    (host:config.toml)
platform.service_manager = 'systemd'    (host:config.toml)
platform.sandbox = 'bubblewrap'    (host:config.toml)
profiles.coder.adapter = 'codex'    (host:config.toml)
sandbox.ro_mounts_approved = []    (host:config.toml)
integrations.marmot.auth_token = {'command': '<command-that-prints-the-token>'}    (host:config.toml)
paths.config_dir = '<config-dir>'    (env:HETERODYNE_CONFIG_DIR)
policy: approvers=['<approver-name>']  (policy.toml)
note: only one model configured; reviews will be adversarial (two LLMs recommended, §11.1)
```

- A reference is printed as the reference, never resolved.
- The `tiers.*` lines are the built-in defaults as merged values, not the effective tiers.
- The note appears when the profiles have fewer than two distinct `model` values. The examples use the same `<model-name>` placeholder for both, so it appears until you fill them in.
