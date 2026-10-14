# Spike S9: content, updaters and the backend's bind on the pinned versions

Status: **done.** S9-1 to S9-4 and S9-9 are confirmed; S9-5, S9-6 and S9-7 changed; S9-8 refuted. S9-10 (Amendment 1 §4.5, the launcher's own Codex launch shape) Part 1 passed with E25's binds on 2026-10-14; Part 2 is the team lead's run. This is plan 4c Task 1. Each finding below is marked **confirmed**, **refuted** or **changed**, gives the exact flag, key or field and the command that showed it, and names the consumer's stop rule when the result isn't "confirmed".

Versions: codex-cli 0.160.0 (`rust-v0.160.0`, target `x86_64-unknown-linux-musl`) and Claude Code 2.1.286 (npm platform package `@anthropic-ai/claude-code-linux-x64`), OpenShell 0.1.2 with the Podman driver on rootless Podman 5.8.8. The installed CLIs were neither upgraded nor replaced.

## Method

- `$T` is a scratch directory under `/tmp`. Pinned copies live only in `$T/store`: the Codex release archive extracted to `$T/store/codex/cli` (`bin/`, `codex-path/`, `codex-resources/`, `codex-package.json`) and the Claude platform tarball's `package/` (`claude`, `package.json`) at `$T/store/claude/package`. The downloads were checked before extraction: the Codex archive with `sha256sum -c --ignore-missing` against the release's `codex-package_SHA256SUMS` (OK), and the npm tarball against its registry `dist.integrity` (sha512, matched; the registry entry also carries 2 `signatures`, not verified here).
- Every CLI run used a fresh throwaway home: `env -i HOME=$T/<run> CODEX_HOME=$T/<run>/.codex CLAUDE_CONFIG_DIR=$T/<run>/.claude XDG_CONFIG_HOME=$T/<run>/.config PATH=<store bin>:/usr/bin:/bin`, plus `TERM` for TUI runs. No login file was opened, made or bound; every run ends at "Not logged in" or a model-call failure, which is enough to show what the CLI loads.
- Network: runs that only show what is loaded ran under `unshare -rn` or `bwrap --unshare-net` (loopback only). The updater comparisons ran both ways: with network, to see each DNS name (decoded from the `sendto`/`sendmmsg` buffers under `strace -f -s 300 -xx`), and under `unshare -rn`, where the same names are attempted and fail. No run had a credential, so nothing could authenticate.
- Read-only binds for S9-4 used `bwrap --dev-bind / / --unshare-net --ro-bind <src> <dst>` (not Podman). The TUI runs used `script -qfec <cmd> /dev/null` under `timeout`; any process left over (the Codex daemon survives its parent) was killed by PID.
- Claude's `--debug` output goes to `$CLAUDE_CONFIG_DIR/debug/*.txt`, not stderr; the debug lines below come from there.

## Summary

| # | Verdict | The switch, field or layout | Consumer |
|---|---|---|---|
| S9-1 | **confirmed** | archive tree = release dir; `strip = ""`; files and dirs only | T3, T6 |
| S9-2 | **confirmed** | `--plugin-dir` loads under `--setting-sources=` | T6 |
| S9-3 | **confirmed** | `--mcp-config` + `--strict-mcp-config` under `--setting-sources=`; `ENABLE_CLAUDEAI_MCP_SERVERS=false` holds | T6 |
| S9-4 | **confirmed** | a read-only `$CODEX_HOME/skills/<name>` loads; `.system` is still written beside it | T6 |
| S9-5 | **changed** | `check_for_update_on_startup = false` is the update switch; `[features] daemon_auto_start = false` is also required; `features.in_app_updates` changes nothing observable | amends E9, T6, T13 |
| S9-6 | **changed** | `DISABLE_AUTOUPDATER=1` as an env var only; `"autoUpdates": false` migrates once and is then ignored under `--setting-sources=`, and does nothing in `--settings` | amends E9, T6, T13 |
| S9-7 | **changed** | aliases in `$CODEX_HOME/tmp/arg0/codex-arg0<random>/`, prepended to `PATH`; the daemon copy in `$CODEX_HOME/packages/` is executed unless `daemon_auto_start = false` | amends T6, T8, T13 |
| S9-8 | **refuted** | bind fields are only `type`, `source`, `target`, `read_only`, `selinux_label`; Podman always gets `ro,rbind,rprivate`: private, but recursive, and a carried submount stays `rw` | T8 stops (E24) |
| S9-9 | **confirmed** | `st_dev`, `st_ino`, `st_mode`, `st_nlink`, `st_size`, `st_ctime_ns` and the link target are identical inside; only the mount ID differs | T8 probe |
| S9-10 | Part 1 **confirmed** (with E25); Part 2 pending | the adapter's own `server_argv` / `tui_argv` and home, production's socket binds, no `-c`: with `.codex/tmp` and `.codex/packages` empty and read-only, no exec under the home, `packages/` empty, no `api.github.com` | Task 6 merge gate |

## S9-1: the Codex archive's layout (confirmed)

Commands:

```sh
cd $T/dl
gh release download rust-v0.160.0 -R openai/codex \
  -p 'codex-package-x86_64-unknown-linux-musl.tar.gz' -p 'codex-package_SHA256SUMS'
sha256sum -c --ignore-missing codex-package_SHA256SUMS        # OK
tar -tzvf codex-package-x86_64-unknown-linux-musl.tar.gz > $T/tarlist.txt
# installed release dir, compared in Python by name, type and mode:
find <installed release dir> -printf '%y %m %P\n'
```

What it showed:
- 54 members: 10 directories and 44 regular files. No symlink, hard link, device or other member type.
- **No top-level prefix**: the members are `bin/…`, `codex-package.json`, `codex-path/…` and `codex-resources/…` directly. The `strip` value is `""`.
- Names, types and modes are identical to the installed release directory, with one exception: the installed directory has a top-level symlink `codex -> bin/codex` that the archive lacks. The installer creates it; T6's layout should treat it as the installer's, not the archive's.
- Some files are mode `0555` in the archive; the modes are carried as-is.
- `codex-package.json` is `{"layoutVersion":1,"version":"0.160.0","target":"x86_64-unknown-linux-musl","variant":"codex","entrypoint":"bin/codex","resourcesDir":"codex-resources","pathDir":"codex-path"}`. The entry point is `bin/codex`.
- The release also publishes `<archive>.sigstore` bundles and `.tar.zst` variants (signature available, not verified in v1, as E5 expects).

## S9-2: Claude plugins under `--setting-sources=` (confirmed)

A plugin at `$T/content/plugins/s9demo` with `.claude-plugin/plugin.json` and `commands/s9hello.md`, made read-only with `chmod -R a-w`:

```sh
claude --setting-sources= --plugin-dir $T/content/plugins/s9demo --debug -p "/s9hello" </dev/null
```

Debug log lines: `--plugin-dir … is one plugin: .claude-plugin at its top marks it`, `Loaded inline plugin from path: s9demo`, `Loaded 1 commands from plugin s9demo default directory`. The run then ends with `Not logged in` (rc 1).

Observation for T6: the CLI's builtin plugins also load under `--setting-sources=` (`cc-plugin-agents-md`, `cc-plugin-telemetry`, `cc-plugin-plugin-authoring`), and `cc-plugin-telemetry` registers hooks. They ship inside the binary, so they're covered by the pinned binary's digest, but `--setting-sources=` doesn't remove them.

## S9-3: Claude MCP config (confirmed, with a different check)

`claude mcp list` is not a valid check: it ignores the top-level `--mcp-config`. The check used a `-p --debug` run and the debug log's MCP lines instead. Decoys: a server `decoy-user` in `$CLAUDE_CONFIG_DIR/.claude.json` and `decoy-project` in `./.mcp.json`; the real config `$T/content/s9echo.json` names `s9echo`.

```sh
ENABLE_CLAUDEAI_MCP_SERVERS=false claude --setting-sources= \
  --mcp-config $T/content/s9echo.json --strict-mcp-config --debug -p hi </dev/null
```

| Run | Servers started |
|---|---|
| `--setting-sources=` + `--mcp-config` + `--strict-mcp-config` + `ENABLE_CLAUDEAI_MCP_SERVERS=false` | `s9echo` only; log: `[claudeai-mcp] Disabled via env var` |
| `--strict-mcp-config` only (default sources) | `s9echo` only |
| neither flag (default sources) | `decoy-project`, `decoy-user`, `s9echo` |
| without the env var | log: `[claudeai-mcp] Disabled: API-key auth precedence active` (a different reason, not the switch) |

Note: in `-p` mode the unapproved project `.mcp.json` server started without `--strict-mcp-config`. The strict flag is what keeps it out, so T6 must always pass it.

## S9-4: Codex read-only skill binds (confirmed)

The skill `$T/content/skills/s9skill/SKILL.md` was first `chmod -R a-w`, then bound for real:

```sh
bwrap --dev-bind / / --unshare-net --ro-bind $T/content/skills/s9skill $CODEX_HOME/skills/s9skill \
  codex exec --skip-git-repo-check 'list your skills' </dev/null
ls -A $CODEX_HOME/skills $CODEX_HOME/skills/.system
```

- The session rollout in `$CODEX_HOME/sessions/…jsonl` lists `- s9skill: S9 spike skill, says S9-SKILL. (file: …/s9skill/SKILL.md)`.
- Codex still wrote `skills/.system/` beside the bound skill: `imagegen`, `openai-docs`, `review-agent`, `skill-creator`, `skill-installer` and `.codex-system-skills.marker`. So `$CODEX_HOME/skills` itself must stay writable, with only the per-skill entries bound read-only.
- Other writes into `CODEX_HOME` on an `exec` run: the `*.sqlite` files, `installation_id`, `sessions/`, `shell_snapshots/`, `thread-writer-locks/`, `.tmp/` (with `plugins.sync.lock`), `.sandbox_migration` and `tmp/arg0/` (S9-7).

## S9-5: Codex's updater switches (changed)

The plan's pair, `-c check_for_update_on_startup=false -c features.in_app_updates=false`, is not the right test, for two reasons found here:
1. **Any `-c` override suppresses the app-server daemon**, even an unknown key (`-c x_unused_key=1`). Comparing a run with `-c` keys to one without measures the daemon, not the keys. All the comparisons below set the keys in `$CODEX_HOME/config.toml` and pass no `-c`.
2. The TUI starts more than an update check: it self-installs and runs a daemon, and syncs curated plugins.

Command (one per config; 40 s each, then the leftovers killed by PID):

```sh
printf '<config>' > $CODEX_HOME/config.toml
env -i HOME=$T/<run> CODEX_HOME=$CODEX_HOME PATH=$T/store/codex/cli/bin:/usr/bin:/bin TERM=xterm-256color \
  timeout -k 3 40 strace -f -qq -s 300 -xx -o $T/<run>.strace -e trace=sendto,sendmmsg,connect,execve \
  script -qfec "$T/store/codex/cli/bin/codex" /dev/null </dev/null
```

| `config.toml` | DNS names attempted | `$CODEX_HOME/packages` | exec from `$CODEX_HOME/packages` |
|---|---|---|---|
| (empty) | `ab.chatgpt.com`, `api.github.com`, `chatgpt.com`, `github.com`, `raw.githubusercontent.com` | `app-server-daemon/` | yes |
| `check_for_update_on_startup = false` + `[features] in_app_updates = false` | `ab.chatgpt.com`, `chatgpt.com`, `github.com`, `raw.githubusercontent.com` | `app-server-daemon/` | yes |
| `[features] in_app_updates = false` only | (daemon: as the empty config) | `app-server-daemon/` | yes |
| `check_for_update_on_startup = false` only | (daemon: as the empty config) | `app-server-daemon/` | yes |
| `[features] daemon_auto_start = false` only | | none | no |
| `check_for_update_on_startup = false` + `[features] daemon_auto_start = false`, `plugins = false` | `raw.githubusercontent.com` | none | no |
| `[features] in_app_updates = false`, `daemon_auto_start = false`, `plugins = false` | `api.github.com`, `raw.githubusercontent.com` | none | no |
| `[features] daemon_auto_start = false`, `plugins = false` | `api.github.com`, `raw.githubusercontent.com` | none | no |

The same names appear with `unshare -rn` (empty config vs the locked-down one), where every attempt fails.

What each name is, from the binary's strings and the traces:
- `api.github.com`: the startup update check, `https://api.github.com/repos/openai/codex/releases/latest` (`tui/src/updates.rs`). **`check_for_update_on_startup = false` alone removes it.** `features.in_app_updates = false` doesn't change any lookup; it was not shown to do anything observable here.
- `github.com`, `chatgpt.com`, `ab.chatgpt.com`: the curated plugin sync (`codex_core_plugins::startup_sync`: `git ls-remote https://github.com/openai/plugins.git`, then the GitHub API and an export archive) and its backend. `[features] plugins = false` removes them. In `codex exec` the same sync is what produced `api.github.com` and `github.com`, with or without the update keys.
- `raw.githubusercontent.com`: `https://raw.githubusercontent.com/openai/codex/main/announcement_tip.toml`, the TUI's announcement tip. It is not an updater and no key here removes it; managed egress denies it.

**The app-server daemon** (default `features.daemon_auto_start = true`). The TUI:
- creates `$CODEX_HOME/packages/app-server-daemon/` with `auto-update-version` (mode 0600, content `0.160.0-x86_64-unknown-linux-musl`), `install.lock`, `current -> …/releases/0.160.0-x86_64-unknown-linux-musl` and that release directory (`bin/`, `codex-path/`, `codex-resources/`, `codex-package.json`, `codex -> bin/codex`). The copied binary has the same bytes as the store's but is a separate file (different inode, link count 1), so it is a copy, not a link;
- then **executes that copy**: `…/packages/app-server-daemon/current/bin/codex --version`, `…/releases/0.160.0-…/bin/codex app-server --listen unix:// --managed-daemon` and `… app-server daemon pid-update-loop`;
- talks to it over `$CODEX_HOME/app-server-control/app-server-control.sock` and a rendezvous socket `/tmp/codex-daemon-<uid>/<hash>`, which is outside `CODEX_HOME` and shared by every Codex of the same uid on the host's `/tmp`;
- leaves the daemon running after the TUI exits.

With `[features] daemon_auto_start = false` none of this happens: no `packages/`, no exec from it, one failed probe of `app-server-control.sock`, and the TUI runs in-process. The TUI flag `--no-daemon` alone (no `-c`) had the same effect: no `packages/`, no exec from it. It is a per-launch flag, so the config key is the one to own.

**Verdict: changed.** The updater can be turned off, so Task 6 continues for Codex, with this `owned_keys` set in the generated `config.toml`:

```toml
check_for_update_on_startup = false
[features]
daemon_auto_start = false
plugins = false
```

`in_app_updates = false` is harmless to keep but isn't the switch. The daemon key is required by S9-7's stop rule, not only by §7's updater rule: without it the CLI runs a binary it wrote into its home. A Codex launch must not rely on `-c` for any of these; the daemon suppression by `-c` is a side effect, not a switch.

**Amends** (S9-5 stop rule):
- **E9** (Codex row): the switch is `check_for_update_on_startup = false`; add `[features] daemon_auto_start = false` and `[features] plugins = false` to the launcher's keys.
- **Task 6**: Codex's `prepare_home` / `prepare_install_home` writes the three keys above (keeping `in_app_updates = false` is optional), and `owned_keys` lists them, so a `defaults/` that sets any of them loses.
- **Task 13**: the updater test asserts no `$CODEX_HOME/packages/` after a TUI run, and no lookup of `api.github.com` from the update check.

## S9-6: Claude Code's updater switches (changed)

Two checks. First the network and debug comparison (`-p` and the TUI, 30 s each, with network):

```sh
env -i HOME=$T/<run> CLAUDE_CONFIG_DIR=$T/<run>/.claude XDG_CONFIG_HOME=$T/<run>/.config PATH=/usr/bin:/bin \
  TERM=xterm-256color [DISABLE_AUTOUPDATER=1] timeout -k 3 30 strace -f -qq -s 300 -xx -o $T/<run>.strace \
  -e trace=sendto,sendmmsg,execve script -qfec "$T/store/claude/package/claude --setting-sources= --debug [-p hi]" /dev/null
```

- `-p`: only `api.anthropic.com` in all three variants (none, `DISABLE_AUTOUPDATER=1`, `{"autoUpdates":false}` in `.claude.json`). The switched variants log `Plugin autoupdate: skipped (auto-updater disabled)`; the baseline logs no plugin-autoupdate line.
- TUI: `api.anthropic.com`, `platform.claude.com` and `raw.githubusercontent.com` (the changelog, written to `$CLAUDE_CONFIG_DIR/cache/changelog.md`) in all three. With no login the CLI never reaches its update download, so the network comparison can't separate the variants. Nothing was written under `$HOME/.local` in any run.

Then the CLI's own readout of its updater state, which is the same gate the updater uses (the binary's `hbe()`: `DISABLE_UPDATES`, then `DISABLE_AUTOUPDATER`, then the "nonessential traffic" variables, then `autoUpdates === false` unless `installMethod` is `native` with `autoUpdatesProtectedForNative`):

```sh
env -i HOME=$T/<run> CLAUDE_CONFIG_DIR=$T/<run>/.claude XDG_CONFIG_HOME=$T/<run>/.config PATH=/usr/bin:/bin \
  TERM=xterm-256color [VAR=1] timeout -k 3 25 unshare -rn \
  script -qfec "$T/store/claude/package/claude --setting-sources= [--settings $T/<run>/flag.json] doctor" /dev/null
```

| Variant | `doctor` says |
|---|---|
| none | `Auto-updates: enabled` |
| `DISABLE_AUTOUPDATER=1` | `Auto-updates: disabled (set by env: DISABLE_AUTOUPDATER)` |
| `DISABLE_UPDATES=1` | `Auto-updates: disabled (set by env: DISABLE_UPDATES)` |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | `Auto-updates: disabled (set by env: CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC)` |
| `--settings` file `{"env":{"DISABLE_AUTOUPDATER":"1"}}` | `Auto-updates: disabled (set by env: DISABLE_AUTOUPDATER)` |
| `--settings` file `{"autoUpdates":false}` | `Auto-updates: enabled` |
| `{"autoUpdates":false}` in `.claude.json`, first run | `Auto-updates: disabled (set by env: DISABLE_AUTOUPDATER)` |
| the same home, second run | `Auto-updates: enabled` |

Why the last two differ: on its first run the CLI migrates the legacy key. It writes `{"env": {"DISABLE_AUTOUPDATER": "1"}}` into `$CLAUDE_CONFIG_DIR/settings.json`, deletes `autoUpdates` from `.claude.json` and sets the variable for that run only. Under `--setting-sources=` the user `settings.json` is never loaded, so from the second run the updater is on again.

**Verdict: changed.** `"autoUpdates": false` is a one-shot under `--setting-sources=` (it migrates once, then is ignored), and in a `--settings` file it does nothing. The switch is the environment variable **`DISABLE_AUTOUPDATER=1`** in the CLI's process environment, set by the launcher (`extra_env`, passed as the sandbox's `--env`). `DISABLE_UPDATES=1` is stronger (it also blocks a manual `claude update`) and may be set beside it.

**Amends** (S9-6 stop rule):
- **E9** (Claude Code row): drop `"autoUpdates": false` in the launcher's settings file as a switch; `DISABLE_AUTOUPDATER=1` in `extra_env` is the switch.
- **Task 6**: Claude's `owned_keys` keeps `DISABLE_AUTOUPDATER` as an owned env key, so a `defaults/` can't unset it; `autoUpdates` isn't relied on (if it stays in the settings file, it's inert).
- **Task 13**: the updater test asserts `claude --setting-sources= doctor` reports `Auto-updates: disabled (set by env: DISABLE_AUTOUPDATER)` on a second launch of the same home, not the presence of the key.

## S9-7: where Codex puts its PATH aliases, and whether it runs them (changed)

```sh
H=$(mktemp -d -p "$XDG_RUNTIME_DIR"); mkdir -p $H/.codex
env -i HOME=$H CODEX_HOME=$H/.codex PATH=$T/store/codex/cli/bin:/usr/bin:/bin timeout -k 3 40 unshare -rn \
  strace -f -qq -s 200 -o $T/s97-exec.strace -e trace=execve,symlink,symlinkat,openat,rename,renameat2 \
  $T/store/codex/cli/bin/codex exec --skip-git-repo-check -c features.daemon_auto_start=false 'say hi' </dev/null
find $H; grep execve $T/s97-exec.strace | grep -F "$H"
```

The same was repeated for the TUI (`script -qfec codex`, with `daemon_auto_start = false` in `config.toml`).

- **Where:** `$CODEX_HOME/tmp/arg0/codex-arg0<random>/` (a fresh random name per run, plus a `.lock`), holding `apply_patch`, `applypatch`, `codex-linux-sandbox` and `codex-execve-wrapper` (the TUI made only `apply_patch`). Each is a **symlink to the running binary**, by its absolute store path. Under `/tmp` the CLI refuses instead: `WARNING: proceeding, even though we could not create PATH aliases: Refusing to create helper binaries under temporary dir "/tmp"`.
- **PATH:** that directory is prepended to the PATH of the commands Codex runs. execve shows lookups through it: `…/tmp/arg0/codex-arg0<random>/lsb_release` and `…/getconf`, both ENOENT, before `/usr/bin`. Codex also tries `$T/store/codex/cli/codex-path/` the same way. So a file an updater (or the agent) put in that directory under a common command name would be run.
- **Other execs under the home:** `/bin/bash -c 'set -e; . "$CODEX_HOME/shell_snapshots/<id>.tmp-<n>"'`. Bash sources a shell snapshot script the CLI wrote into its home. The interpreter is the system's; the script is data the CLI made.
- **The daemon:** with the default `daemon_auto_start`, the CLI executes the binary it copied to `$CODEX_HOME/packages/app-server-daemon/` (S9-5). That is exactly the stop condition ("the CLI runs a binary it wrote into its home").
- **Turning the aliases off:** with `$CODEX_HOME/tmp` an empty read-only bind (`bwrap --ro-bind $T/empty $H/.codex/tmp`), Codex prints `WARNING: proceeding, even though we could not create PATH aliases: Read-only file system (os error 30)` and runs normally. `chmod 555` is not a test of this under `unshare -r`, because the mapped root ignores the mode.

**Verdict: changed.** The plan expected aliases somewhere under the home; they're at `$CODEX_HOME/tmp/arg0/codex-arg0<random>/`, put first on the child PATH, and the CLI resolves commands through them. With the daemon on (the default), the CLI runs a binary it wrote into its home.

**Amends** (S9-7 stop rule: Task 6 stops for Codex until these land):
- **Task 6**: Codex's generated config sets `[features] daemon_auto_start = false` (S9-5), and `prepare_install_home` creates no `$CODEX_HOME/packages`.
- **Task 8**: the Codex sandbox presents `$CODEX_HOME/tmp` as an empty read-only directory (a read-only bind of an empty directory), so no aliases are made, and `$CODEX_HOME/packages` as an empty read-only directory, so no daemon copy can be written. With that bind, startup succeeds (the run above). Editing compatibility (whether `apply_patch` still works without its shell alias) wasn't exercised, since no run invoked it, and is pending Task 13 validation.
- **Task 13**: `test_an_updater_write_into_the_synthetic_home_is_never_run` covers the `packages/` copy and the `tmp/arg0` directory, and shows that `shell_snapshots/` holds only what the CLI wrote in that session (it's a script the system bash sources).

## S9-8: the bind's options on OpenShell 0.1.2 and Podman (refuted)

From the OpenShell v0.1.2 source (`crates/openshell-driver-podman/src/container.rs`):
- The Podman driver config is `{"podman": {"cdi_devices": [...], "mounts": [...]}}`, both structs with `#[serde(deny_unknown_fields)]`.
- A mount is tagged by `type`: `bind`, `volume`, `tmpfs` or `image`. The `bind` variant's fields are exactly `source`, `target`, `read_only` (default `true`) and `selinux_label` (`shared` gives `z`, `private` gives `Z`).
- `podman_user_mounts` always hands Podman a bind with options `["ro" | "rw", "rbind"]` plus the SELinux flag.

The run (`$T/s98/run.sh`, run with the team lead's go; every `podman` and `openshell` call went through a transient `systemd-run --user --scope --collect` with the side-by-side Podman 5 environment, against the running gateway, image `localhost/heterodyne-agent:1`, no network policy, no login). The test tree `$T/s98/src` holds a file `a`, a link `l -> a`, a directory `d` (0555) with `d/f`, and two empty directories `sub` and `late`. A `tmpfs` was mounted on `sub` inside Podman's own user and mount namespace (`podman unshare mount -t tmpfs -o size=1m,mode=0755 s9sub $T/s98/src/sub`), because a mount in any other namespace is invisible to the Podman service that resolves the bind.

```sh
create() { $P openshell sandbox create --name "$1" --detach --from localhost/heterodyne-agent:1 \
  --policy $T/s98/policy.yaml --no-credential-warnings --driver-config-json "$2" -- sleep infinity; }
create s9-f1 '{"podman":{"mounts":[{"type":"bind","source":"$T/s98/src","target":"/s9/tree","read_only":true,"propagation":"rprivate"}]}}'
# ... s9-f2 to s9-f6 the same, with the field below; s9-b1 with no extra field
$P podman inspect <s9-b1 container> --format '{{json .HostConfig.Binds}}'
$P openshell sandbox exec -n s9-b1 --no-tty -- python3 - /s9/tree w < $T/s98/stat.py
```

Candidate fields:

| Sandbox | Extra field | Result |
|---|---|---|
| s9-f1 | `"propagation": "rprivate"` | refused: `invalid podman driver_config: unknown field `propagation`, expected one of `source`, `target`, `read_only`, `selinux_label`` |
| s9-f2 | `"bind_propagation": "rprivate"` | refused, same message for `bind_propagation` |
| s9-f3 | `"nonrecursive": true` | refused, same message for `nonrecursive` |
| s9-f4 | `"bind_nonrecursive": true` | refused, same message for `bind_nonrecursive` |
| s9-f5 | `"options": ["norbind"]` | refused, same message for `options` |
| s9-f6 | `"selinux_label": "private"` (positive control) | accepted, created, deleted |

What Podman got for the base bind (s9-b1, fields `type`, `source`, `target`, `read_only: true`), from `HostConfig.Binds`: `$T/s98/src:/s9/tree:ro,rbind,rprivate`. Propagation is `rprivate` by Podman's default; the bind is recursive (`rbind`), and nothing in the config can change either.

Inside (`/proc/self/mountinfo` lines for the tree, paths shortened to `$T`):

```
9097 9108 259:2 $T/s98/src /s9/tree ro,relatime - ext4 /dev/nvme0n1p2 rw
9098 9097 0:118 / /s9/tree/sub rw,relatime - tmpfs s9sub rw,size=1024k,mode=755,uid=1001,gid=1004,inode64
```

- The top mount is `ro`, with no `shared:` or `master:` tag (no optional fields): **private, as required**.
- **The submount is carried in** (`rbind`), and it is **`rw`**: `ro` applies to the top mount only, not recursively. A write to `/s9/tree/a` gave `Read-only file system`; a write to `/s9/tree/sub/x` gave `Permission denied`, which is Landlock (the policy lists `/s9/tree` read-only), not the mount. The mount alone would have allowed it.
- A `tmpfs` mounted on the host's `late` directory after create (again in Podman's namespace) **did not appear inside**: the same two lines, as `rprivate` predicts.

**Verdict: refuted.** OpenShell 0.1.2's Podman driver accepts no field for a non-recursive bind, and refuses every candidate field by name. The bind it makes is `ro,rbind,rprivate`: private (later host mounts stay out), but recursive, and a submount present at create time is carried in without `ro`. Stop rule (S9-8): **Task 8 stops** at `driver_config` / E24. A backend change, or another way to get a non-recursive bind, goes through its own design review first. Facts for that review:
- what does hold today: `rprivate` propagation; `ro` on the top mount; Landlock's read-only rule covering the carried submount;
- what doesn't: non-recursion, and `ro` on submounts;
- which checks see a submount depends on the namespace. A mount made in Podman's namespace (as here) is invisible to a host walk: `host.json`, from the host's own namespace, shows `sub` as the underlying directory, and only `host-podmanns.json` shows the carried tmpfs. So T2's `check_fast` (`mnt_id` vs the root's, `CROSSES_MOUNT`), run on the host, can't detect it. Inside the sandbox the submount has its own mount ID (9098 vs 9097) and its own `st_dev` (S9-9), so **Task 8's inside probe is still required**: it must refuse any mountinfo line below the bind's target. Task 13's controls must keep that split, with one submount visible to the host walk and one made only in Podman's namespace.

## S9-9: the file identity seen inside the sandbox (confirmed)

In the S9-8 sandbox (s9-b1), `$T/s98/stat.py` `lstat`ed the tree's root and every entry inside, and the same script ran on the host twice: in the host's own namespace (`python3 $T/s98/stat.py $T/s98/src`) and in Podman's (`podman unshare python3 …`). Compared field by field:

| Entry | `st_dev` | `st_ino` | `st_mode` | `st_nlink` | `st_size` | `st_ctime_ns` | link target |
|---|---|---|---|---|---|---|---|
| `.`, `a`, `d`, `d/f`, `l`, `late` | same | same | same | same | same | same | same (`l -> a`) |
| `sub` (the submount) | same as Podman's namespace; differs from the host's own view (tmpfs vs the underlying directory) | as `st_dev` | same | same | as `st_dev` | as `st_dev` | n/a |

All seven fields are identical inside for every entry on the bound file system, including `st_dev` (the ext4 device 259:2) and `st_ctime_ns` to the nanosecond. The only difference for `sub` is the expected one: the host's own namespace doesn't have the tmpfs, so it sees the directory underneath. The mount ID is not one of the fields; it differs inside by construction (each namespace numbers its mounts), so T8's probe can compare `mnt_id` only within one side (entry vs root), never across.

**Verdict: confirmed.** T8's probe may compare `st_dev`, `st_ino`, `st_mode`, `st_nlink`, `st_size`, `st_ctime_ns` and the link target against the host record; none is dropped.

## S9-10: the launcher's own Codex launch shape (Part 1 confirmed with E25; Part 2 pending)

Amendment 1 §4.5. Part 1 is the worker's run: no network, no login. It ran on 2026-10-14 from the Task 6 worktree (branch `p4c-t6`, PR #86) with the scripts in `docs/spikes/s9-10/`. Part 2 (network plus the live-test login, a new session's prompt, then a resume) is the team lead's run and isn't recorded yet.

**The CLI.** `gh release download rust-v0.160.0 -R openai/codex -p codex-package-x86_64-unknown-linux-musl.tar.gz -p codex-package_SHA256SUMS` into a fresh `mktemp -d -p "$XDG_RUNTIME_DIR"` directory, then `sha256sum -c --ignore-missing codex-package_SHA256SUMS`: `codex-package-x86_64-unknown-linux-musl.tar.gz: OK`. Unpacked with `tar -xzf` into `cli/`. `codex-package.json` gives version `0.160.0`, target `x86_64-unknown-linux-musl` and entrypoint `bin/codex`. That download was the only network access.

**Commands.**

```sh
sh docs/spikes/s9-10/part1.sh <dl>/cli <results>
# for V in plain e25, each with a fresh T=$(mktemp -d -p "$XDG_RUNTIME_DIR" s9-10.XXXXXX):
.venv/bin/python docs/spikes/s9-10/generate.py $T <dl>/cli [--e25]     # Codex.prepare_home (+ exec_dirs.prepare)
timeout -k 5 40 unshare -rmnpf --mount-proc --kill-child sh docs/spikes/s9-10/inner.sh $T <dl>/cli $V <python>
.venv/bin/python docs/spikes/s9-10/analyze.py $T $V --part 1
```

- **`generate.py`** calls the candidate adapter.
  - It runs `Codex.prepare_home(SessionLayout($T/s), $T/wt)`.
  - With `--e25` it also runs `exec_dirs.prepare(home, Codex.empty_read_only)`, which is what `prepare_install_home` adds.
  - It writes `server_argv(cli)` and `tui_argv(cli, {}, native_id=None, resume=False, …)` to `argv.json`.
  - It refuses any argv that has `-c`/`--config`.
- **`inner.sh`** runs in a fresh user, mount, network and PID namespace (uid 0 inside). In order:
  1. `mount --make-rprivate /`, then a `tmpfs` on `/tmp`.
  2. `BRIDGE_INSIDE`'s parent `/run` can't be written from a user namespace, so a `tmpfs` goes on `/run` and each host `/run` entry is bound back (`--rbind`).
  3. `layout.bridge` is bound at `/run/hz-bridge` and `layout.daemon` at `/tmp/codex-daemon-0`, the two binds `Codex.facts` declares.
  4. For `e25`, one fresh empty directory is bound read-only at `.codex/tmp` and at `.codex/packages`.
  5. The server starts under `strace -f -qq -s 300 -xx -e trace=execve,connect,sendto,sendmmsg`. Readiness is the adapter's own `Codex.server_ready(layout)`, polled every 0.5 s for up to 10 s.
  6. The TUI runs in a private tmux server: `tmux -L s9-10 -f /dev/null new-session -d -x 160 -y 48`, with its socket on the run's private `/tmp`. Its screen is captured after 14 s, and the tmux server is killed.
- **`analyze.py`** decodes the `-xx` buffers. Since r2 it fails closed: it needs `--part 1|2`, every file a check reads must be there (and hold something, except `inside.packages`), each trace must show the exec of the argv it traced, and the run must have ended by itself (`rc` 0, not the cap's 124). With any of that missing, no later check passes. It exits 1 on any FAIL. Part 1's screen check is the login screen, which is what the TUI shows without a login; Part 2 checks the composer. `part1.sh` and `part2.sh` keep its status (no pipe, since `sh` has no `pipefail`), also fail on a leftover process or an undeleted home, and exit non-zero unless both variants pass.

**The argv**, unchanged from the adapter (both variants; `<dl>` is the download directory):

```
server:  <dl>/cli/bin/codex app-server --listen unix:///run/hz-bridge/app.sock
tui_new: <dl>/cli/bin/codex --remote unix:///run/hz-bridge/app.sock --dangerously-bypass-approvals-and-sandbox
```

No argv has `-c`. The home's `config.toml` is the adapter's: `approval_policy`, `sandbox_mode`, `check_for_update_on_startup = false`, the worktree's `projects` entry, and `[features]` with `apps`, `daemon_auto_start`, `plugins` and `in_app_updates` all false.

**The environment** is `env -i HOME=<home> CODEX_HOME=<home>/.codex PATH=<dl>/cli/bin:/usr/bin:/bin TERM=xterm-256color`, for both the server and the TUI pane. `Codex.versions["0.160.0"].tool_env` names the variables 0.160.0 sets for its own children: `CODEX_CI`, `CODEX_SESSION_ID`, `CODEX_THREAD_ID`, `CODEX_VERSION`, `COLORTERM`, `GH_PAGER`, `GIT_PAGER`, `LC_ALL`, `LC_CTYPE`, `NO_COLOR` and `PAGER`. It gives no values, and the launcher passes none of them, so the run added none.

**Mounts inside** (from `/proc/self/mountinfo`): `/run/hz-bridge rw` and `/tmp/codex-daemon-0 rw` in both runs. `e25` adds `<home>/.codex/tmp ro` and `<home>/.codex/packages ro`.

**Results**

| | `plain` (no E25 binds) | `e25` (E25's binds) |
|---|---|---|
| `server_ready` (the adapter's) | yes | yes |
| TUI's Unix connects | `/run/hz-bridge/app.sock` (and `/run/user/0/bus`, which fails: no session bus) | the same |
| TUI screen | the login screen ("Welcome to Codex … Sign in with ChatGPT … Press enter to continue") | the same |
| `.codex/packages` | never created | empty (inside and on the host) |
| exec of a path under the home | 6 attempts, all ENOENT: `.codex/tmp/arg0/codex-arg0<random>/lsb_release` and `…/getconf`, from the server and the TUI | none |
| `.codex/tmp` after the run | `arg0/codex-arg0<random>/` with `apply_patch`, `applypatch`, `codex-linux-sandbox`, `codex-execve-wrapper` and `.lock`, per process | empty |
| DNS names attempted (`sendto`) | `raw.githubusercontent.com` only (from the TUI) | the same |
| `api.github.com` in `connect` / `sendto` | none | none |
| CLI warning | none | `WARNING: proceeding, even though we could not create PATH aliases: Read-only file system (os error 30)` (server and TUI) |

What this shows:
- **The production arrangement works.** The server reaches the adapter's `server_ready` through the bridge and daemon binds, and the TUI connects to `APP_SOCKET` over `--remote`. With no login, the TUI shows its login screen instead of the composer. That is expected, and Part 2 covers the logged-in path.
- **E9 reaches the CLI with no `-c`.** No `packages/` appears and nothing is executed from it (`daemon_auto_start = false`). There is no `api.github.com` (`check_for_update_on_startup = false`). The plugin sync's `github.com`, `chatgpt.com` and `ab.chatgpt.com` are gone too (`plugins = false`). The one name left, `raw.githubusercontent.com`, is the announcement tip (S9-5). It isn't an updater, and managed egress denies it.
- **E25 is what makes the exec condition hold.** Without it, the CLI makes its PATH aliases under `.codex/tmp/arg0/` and resolves commands through them first (S9-7). This run only shows failed lookups (ENOENT), but a file planted there under a common command name would run. With E25's read-only empty directory, no aliases are made and there's no exec attempt under the home.

**Verdict: Part 1 confirmed for the shape Task 6 ships (with E25's binds).** All three pass conditions hold in `e25`: `.codex/packages` stays empty, there's no exec of a path under the home, and there's no `api.github.com` lookup. The `plain` run is the baseline without E25. It meets two of the three, and its exec attempts under `.codex/tmp/arg0/` are the reason E25 exists. Reading `plain` as a control rather than a variant that must also pass is pending Liam's decision. Still open for Part 2 / Task 13: whether `apply_patch` works without its shell alias (S9-7), because no Part 1 run invokes a tool.

**r2 re-check (2026-10-14).** Review r2 found the first analyzer could pass on missing evidence: a missing trace read as no execs and no lookups, Part 2's checks vanished without `answered`, and neither the analyzer nor the wrapper's `tee` pipeline passed a failure on. So the record above was checked again:
- **The first run's evidence was complete.** Nothing was missing. Both variants had non-empty `server.strace` (70 and 66 lines) and `tui.strace` (48 and 45 lines), each showing the exec of its own argv, `rc` 0, `server_ready` `yes`, the screen and the `inside` listings. Re-analyzed with the fixed analyzer, the kept files give the same results: `e25` PASS, and `plain` FAIL on `no_exec_under_home` alone.
- **Part 1 was run again** at `f5a590b` with the fixed scripts, in the same scope: the same checksum-verified CLI (`sha256sum -c` OK again, no download), `unshare -rmnpf`, no login, `env -i`, 40 s caps. The results match the table above. `e25`: every check passes (exit 0). `plain`: every check passes except `no_exec_under_home`, with the same 6 ENOENT attempts under `.codex/tmp/arg0/` (exit 1). `packages/` was never created in `plain` and stayed empty in `e25`. The DNS names were `raw.githubusercontent.com` only, with no `api.github.com`. `part1.sh` now exits 1 because `plain` fails. Whether that run is a control is the decision pending with Liam. No process was left, both homes were deleted, and the traces are kept under `$XDG_RUNTIME_DIR`.

**Harness notes.** Two first attempts were discarded because of harness bugs, not CLI behaviour:
- `server_ready` got a `str` instead of a `Path`, so readiness was never seen.
- `script -qfec … </dev/null` gave the TUI no terminal to answer its cursor and colour queries (`CSI 6n`, OSC 10/11), so it drew nothing. The tmux pane fixes that.

The CLI results of those attempts (DNS names, execs, `packages/`) matched the run above.

**Cleanup.**
- After each run `ps` showed no process whose arguments contain the run directory, and none was left to kill. The PID namespace with `--kill-child` takes the server, the TUI and tmux down with it.
- Each `$T` (home, worktree, session layout) was deleted, and `ls -d "$XDG_RUNTIME_DIR"/s9-10.*` finds nothing.
- `/tmp/codex-daemon-*` on the host wasn't touched: every socket was on the run's private `/tmp`.
- `~/.codex` and `~/.claude` weren't read or written.
- The download directory and the results directory (traces, screens, `result.json`) are kept under `$XDG_RUNTIME_DIR` for Part 2 and the review.

**Part 2: the script, for the lead's run.** `S9_10_LOGIN=<live-test codex auth.json> sh docs/spikes/s9-10/part2.sh <dl>/cli <results>`. Supporting files: `inner2.sh`, `part2_steps.py`, and `generate.py`'s `--install` and `--resume` flags. Part 1's path through `generate.py` is unchanged.
- **Namespaces:** `unshare -rmpf`, with no `-n`. The binds are Part 1's.
- **Login:** bound read-only at `<home>/.codex/auth.json`, inside the namespace only. The script never reads it.
- **Model:** `HZ_LIVE_SANDBOX_CODEX_MODEL` (default `gpt-6-luna`) through `tui_argv`'s `--model`.
- **Effort:** `model_reasoning_effort = "low"` comes from an install's `defaults/config.toml` through `prepare_install_home`. Q4 refuses profile args, and no argv may carry `-c`, so that's the only route.
- **Steps for each variant (plain, then E25):**
  1. The hook trust from `runtime._trust`: the adapter's `run_files`, `trust_argv` and `apply_trust`.
  2. The server.
  3. A new session, which gets the prompt "Reply with only the word pong.". The answer is read from the rollout.
  4. The thread ID, taken from the rollout and checked with `has_state`.
  5. `tui_argv(resume=True)`. It passes when the resumed screen shows the first prompt.
- **Cap:** 240 s per variant.

`S9_10_DRY=1` adds `-n` for a harness check. It was run once that way, with a stand-in `{}` login in a temp directory.
- Trust listed 4 hooks, seeded them, then listed 0.
- `server_ready` held, and the TUI connected to `APP_SOCKET`.
- The TUI then exited, as it should on that stand-in: `account/read failed: plan type is required for chatgpt authentication`.
- With a login file present, the DNS names attempted were `ab.chatgpt.com`, `chatgpt.com` and `raw.githubusercontent.com`. There was no `api.github.com`.
- Resume wasn't reached, because that needs a real thread.

## Cleanup

Everything is under `$T` and the `mktemp -d -p "$XDG_RUNTIME_DIR"` homes. No process from the spike is left running. The S9-8 run ended with zero `s9-*` sandboxes or containers, both `tmpfs` mounts unmounted in Podman's namespace, and no `conmon` left.

The Codex TUI runs also left rendezvous sockets in `/tmp`, outside the throwaway homes. They were left alone, because the host's own Codex uses `/tmp/codex-daemon-<uid>/` too. Each name is a 64-hex digest (the socket's lock file is `<name>.lock`); they're listed here by their first 12 characters, with the time made (2026-10-13, Pacific time):
- `/tmp/codex-daemon-<uid>/`: sockets and locks `5af58a0262a0…` and `9e10ca84e872…` (21:16); `190eaab267cb…`, `2a7eb2f4286e…`, `948756bdc611…` and `f6a3c4ca6a17…` (21:18); `6c6b992d391e…` and `efa47b76cb2d…` (21:19).
- `/tmp/codex-daemon-0/` (made by the `unshare -r` runs, where the uid is 0; the directory is new): lock `50db70744d46….lock` (21:10), socket and lock `ea39582e5d92…` (21:21).
