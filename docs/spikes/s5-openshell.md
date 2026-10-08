# Spike S5: OpenShell runtime on the reference host (ADR 0001 r14 §7 items 1-5)

Status: **all five items PASS** on the reference host (Linux 6.17, rootless podman 5.8.8, OpenShell 0.1.2 with the podman compute driver). Caveats are listed per item and under ADR impact.
CLIs tested: Claude Code 2.1.286 and codex-cli 0.160.0, both using the operator's real OAuth subscription logins, bound read-only.
Bead: btq-le4mq. Code: `spikes/s5/` (`launch.py`, `probes.py`, `probes.sh`, `ws-request`, `image/Containerfile`, `gateway.toml`). The session socket stub and `hook.py` are reused unchanged from `spikes/s3/`. Raw evidence: `spikes/s5/evidence/`. The host home is written `$HOME`, and session tokens are masked as `<TOKEN>`. No OAuth token was printed or stored at any point: only expiry times and sha256 prefixes.

## Verdicts

| # | §7 item | Verdict |
|---|---|---|
| 1 | Both CLIs in the managed launch shape, each started from a host tmux session (send-keys, break-glass attach); Codex with its per-session `codex app-server` inside and `codex queue` from outside | **PASS** |
| 2 | Every launch self-test probe, including Other accounts, with its specific evidence; a failing probe refuses the launch | **PASS** |
| 3 | A working login with read-only login files and no refresh inside | **PASS** |
| 4 | Hook events and `ws-request` reach the per-session socket; the control op is refused | **PASS** (Claude and Codex) |
| 5 | A runtime driver that works on the reference host | **PASS** (podman driver; host config change below) |
| - | Does OpenShell's proxy check more than the CONNECT host? | **Yes.** It enforces the HTTP authority inside the tunnel and terminates TLS itself, so the client's SNI can't steer the upstream connection (below) |

## How the launch works

`launch.py up <adapter> <sid>` does the following:
1. **Prepares the session dir** `~/.cache/s5/<sid>/`. It holds:
   - the synthetic home and the git worktree stand-in;
   - `run/`, bound read-only at `/run/hz`, containing the probes, `hook.py` and `ws-request`;
   - for Codex, `bridge/`.
   It also starts the S3 session-socket stub on the host (`run/session.sock`, a fresh token per session).
2. **Creates the sandbox:**
   ```
   openshell sandbox create --name s5-<sid> --from localhost/s5-agent:0 --policy <policy> --driver-config-json <binds> --env <allowlist> -- sleep infinity
   ```
   The policy (example: `evidence/policy-c2.yaml`) has these parts:
   - **Filesystem:** a Landlock allowlist with `hard_requirement`.
   - **Process:** the workload runs as the host uid/gid.
   - **Network:** per-binary policies, where the resolved CLI binary may reach only its model host (`api.anthropic.com` or `chatgpt.com`), and `/usr/bin/curl` may reach only the probe control host `api.openai.com`.

   The OAuth refresh hosts are absent on purpose. The bind mounts are:
   - home and work, read-write;
   - `/run/hz`, read-only;
   - the CLI install roots, read-only at their host paths;
   - the chosen login file, read-only, at its synthetic-home path;
   - for Codex only, `bridge/` at `/run/hz-bridge` and `bridge/daemon` at `/tmp/codex-daemon-<uid>`.
3. **Runs the self-test** and deletes the sandbox on any failure ("launch refused").

`launch.py agent <adapter> <sid>` refuses unless `selftest.rc` is 0. It then starts the agent in `tmux -L s5spike new-session -d -s <sid>` as `openshell sandbox exec -n s5-<sid> --tty -- <cli> ...`:
- **Claude:** `--permission-mode bypassPermissions`.
- **Codex:** first a detached `codex app-server --listen unix:///run/hz-bridge/app.sock` inside the sandbox. The launcher waits for its socket (refusing if it does not appear), then runs the TUI as `codex --remote unix:///run/hz-bridge/app.sock --dangerously-bypass-approvals-and-sandbox`.

## Item 1: managed launch shape (PASS)

**Claude Code** (session `t1`, `evidence/claude-tui-t1.txt`):
- Claude Code came up interactive with the real login ("Opus 5.5 · Claude Enterprise") and bypass permissions on.
- Steering with `tmux send-keys` made it run `/run/hz/ws-request <TOKEN> hello-from-claude`, which returned `{"ok": true}`.
- Egress seen in the supervisor log:
  - **Allowed:** only `api.anthropic.com`.
  - **Denied, with no visible effect on the session:** `http-intake.logs.us5.datadoghq.com`, `raw.githubusercontent.com`, `github.com` and `downloads.claude.ai` (telemetry, updates and plugin fetches).

**Codex** (session `c2`, `evidence/codex-tui-c2.txt`, `evidence/codex-queue.txt`):
- The app-server runs inside the sandbox, and the TUI attaches to it with `--remote`. Model GPT-6.1-Sol, YOLO mode.
- `send-keys` steering made it run `ws-request`, which returned `{"ok": true}`.
- **Steering from outside:** this ran on the host, outside the sandbox:
  ```
  CODEX_HOME=/tmp/s5-cxq codex queue --remote unix://$HOME/.cache/s5/c2/bridge/daemon/<hash> --thread <uuid> --message "...reply with exactly QUEUE-OK-S5..."
  ```
  It printed `Queued message ... for thread ...`, and the live TUI then showed the queued prompt and the reply `QUEUE-OK-S5`.
- **Finding:** codex 0.160 does not bind the `--listen` path itself.
  - It binds `/tmp/codex-daemon-<uid>/<hash>` and leaves only a symlink at the `--listen` path. That symlink is dangling from the host's view.
  - It also refuses a socket dir that is not mode 0700 ("app-server socket directory must be a user-owned directory with mode 0700").
  - `TMPDIR` does not move the socket. The fix is a 0700 host dir bound read-write at `/tmp/codex-daemon-<uid>`. `/tmp` is already in the Landlock read-write set.
  - The host then dials the real socket file in that dir.

**Break-glass attach** (`evidence/break-glass-attach.txt`): `tmux -L s5spike attach -r -t <sid>` attaches to both sessions. While the attach ran, `list-clients` showed a client on each session (`readonly=1`).

## Item 2: launch self-test (PASS)

`evidence/selftest-pass.txt` holds both self-tests, Claude `t1` and Codex `c2`. Both were run against the live sandboxes with the agents running, and both ended `SELFTEST PASS`. Each probe prints its own evidence, and only the specific enforcement passes.

| Probe | What must hold under OpenShell | Evidence (Codex run; the Claude run is the same apart from paths) |
|---|---|---|
| freshness-gate (outside) | Access token outlives the session maximum (2 h) plus the stop margin (15 min); only the hours left are printed | `access token 170.66 h left, need > 2.25 h` (Claude: 5.53 h) |
| canary-precondition (outside) | `~/.s5-canary` is written and readable on the host just before the probes; else ABORT rc 3 | PASS |
| real-home-canary-unreadable | Inside: ENOENT/EACCES/EPERM | `-> ENOENT` |
| non-allowlisted-host-blocked | `example.org` resolves through OpenShell's policy DNS to a synthetic 198.18.0.0/15 address (the marker), `connect()` gives **EACCES**, and curl gives rc 7 "Permission denied" | `example.org -> 198.18.0.2 (policy-DNS synthetic=True) connect -> EACCES; curl rc=7` |
| direct-network-blocked | Only `lo`, no routes; TCP to a literal IPv4 address gives EACCES (or a no-route errno); UDP and IPv6 are refused | `non-lo interfaces=none routes=0 tcp -> EACCES udp -> EDESTADDRREQ tcp6 -> EACCES` |
| control-op-rejected | `approve` with a valid token gets the forbidden reply | `{"ok": false, "error": "forbidden"}` |
| wsd-socket-absent | `/run/hz/wsd.sock` absent | `exists=False` |
| allowlisted-host-reachable | curl rc 0 **and** the issuer is the OpenShell sandbox CA, which proves it went through the proxy | `curl rc=0 http=421 issuer='CN=OpenShell Sandbox CA; O=OpenShell'` |
| hook-event-accepted | `hook_event` with a valid token gets `{"ok": true}` | PASS |
| host-env-not-inherited | No variable outside the launcher allowlist plus OpenShell's fixed, secret-free set; `OPENSHELL_USER_ENVIRONMENT` keys checked too | `unexpected vars: none` |
| openshell-control-material-unreadable (**added**) | The supervisor's channel, TLS key and JWT are not readable by the workload | `/.openshell/channel/... -> ENOENT`, `/run/secrets -> EACCES`, `/etc/openshell/... -> ENOENT` |
| other-accounts | Each other account's login file, at both its configured path and its canonical path: present → ENOENT/EACCES; unknown → ENOENT/EACCES; absent → ENOENT. The outside canary gives ENOENT. The chosen file's sha256 matches the host's, and writing to it fails | b (present, configured path is a symlink): both paths → ENOENT; c (absent) → ENOENT; d (unknown: dir mode 000) → ENOENT; canary → ENOENT; chosen `sha256-match=True write->EROFS` |
| proxy-logged-refusal (outside) | The supervisor log has this run's `DENIED /usr/bin/curl(0) -> example.org:443 [reason:transparent_tcp_policy_denied]` | PASS |
| proxy-logged-allow (outside) | The supervisor log has this run's `ALLOWED /usr/bin/curl(0) -> api.openai.com:443 [policy:probe_control engine:opa]` | PASS |
| proxy-logged-direct-deny (outside) | The supervisor log has this run's literal-IP denial | `DENIED /usr/bin/python3.12(0) -> 1.1.1.1:443` <!-- install-agnostic: allow=ip-port (public probe target) --> |

**Negative controls** (`evidence/negative-controls.txt`). Each one ended in launch refused, and the sandbox was deleted, which `openshell sandbox list` confirmed. One control printed "sandbox after: 1" because the deletion was still in progress; the sandbox was gone a few seconds later.

| `S5_SELFTEST_INJECT=` | Outcome |
|---|---|
| `canary-missing` | `ABORT canary-precondition ... not readable outside`, rc 3, no probes run |
| `oa-canary-inside` (the canary path points into the bound home) | `FAIL other-accounts [... canary:...oa-canary-copy->READABLE ...]`, rc 1 |
| `chosen-mismatch` (pinned hash wrong) | `FAIL other-accounts [... sha256-match=False ...]`, rc 1 |
| `leak-env` (an extra variable in the probe env) | `FAIL host-env-not-inherited [unexpected vars: LEAK]`, rc 1 |
| `socket-dead` (stub killed before create) | `FAIL control-op-rejected` and `FAIL hook-event-accepted` (`ECONNREFUSED`), rc 1 |

**Plain-podman control** (`evidence/plain-podman-control.txt`). The same `probes.py` was run in the same image without OpenShell, and it fails on both networks:
- `--network=pasta`: non-allowlisted-host, direct-network and allowlisted-host all FAIL (real DNS, real interface, a Google Trust Services issuer), and so do the socket probes.
- `--network=none`: the egress probes FAIL (no policy DNS) and so do the socket probes. direct-network-blocked passes here as expected, with `ENETUNREACH`.

This shows that the egress probes recognize OpenShell's enforcement specifically, not just "some failure". Writing this control exposed two probe bugs, both fixed:
- An empty address list counted as a synthetic DNS answer. It would not have produced a false PASS, because the connect check still failed.
- The wsd probe crashed when `/run/hz` was absent.

An earlier, incidental fail-closed case also happened. A hung `sandbox exec`, caused by an inherited stdin, was killed by PID. The launcher then found the supervisor log checks missing, printed SELFTEST FAIL, and deleted the sandbox. Since that fix, the probe config, which holds the session token, travels on stdin rather than on argv.

## Item 3: login read-only, no refresh inside (PASS)

Before launch, the remaining access-token time was checked on the host (expiry only): Claude about 5.7 h, Codex about 170.8 h. The freshness gate repeats this at every self-test.

Evidence is in `evidence/login-readonly.txt`:
- **Working login:** both CLIs answered model prompts with the real ro-bound login (item 1).
- **Read-only inside:**
  - `echo x >> <synthetic home>/.claude/.credentials.json` gives "Read-only file system", and the same holds for Codex `auth.json`.
  - A `mv` (an atomic-rename refresh) gives "Device or resource busy".
  - The mountinfo line shows `ro`.
- **No refresh is possible inside:** `platform.claude.com` and `auth.openai.com` are in no policy.
  - From inside, curl to either gives rc 7.
  - The supervisor logs `Denied staged transparent connection ... reason=endpoint platform.claude.com:443 is not allowed by any policy`, and the same for `auth.openai.com`.
  - Over the sessions' lifetime, the only log lines naming a refresh host are those deliberate probes. Caveat: the OpenShell log buffer keeps only the last few hundred lines, so the earlier part of the lifetime may be missing.
- **Host files untouched:** the sha256 prefix, inode and mtime of `~/.claude/.credentials.json` and `~/.codex/auth.json` are identical before and after all sessions.

## Item 4: hooks, ws-request, control-op refusal (PASS)

Evidence is in `evidence/session-socket-events.txt`, a summary of the host stub's log for each session.

- **Claude:** the `PreToolUse` hook (`settings.json`, matcher `*`, running `python3 /run/hz/hook.py <token>`) delivered `hook_event PreToolUse Bash {"command": "/run/hz/ws-request ..."} -> ok`. The `ws_request hello-from-claude -> ok` that followed came from the agent's own tool call.
- **Codex (interactive, managed shape):**
  - `ws_request hello-from-codex -> ok` came from the agent's tool call.
  - **Codex hooks fire in the interactive app-server session** with the real `hooks.json` schema (nested `hooks` arrays, `matcher`).
  - Delivered: `hook_event UserPromptSubmit -> ok` and `hook_event PreToolUse Bash {"command": "echo hook-probe-codex-2"} -> ok`.
  - **A `PreToolUse` deny works:** a second hook exits 2 with a reason when the command contains `DENYME`. The TUI showed `Blocked by hook └ s5: operator-only intent, ask as plain text instead`, and Codex reported that the command did not run (`evidence/codex-tui-c2-hooks.txt`).
  - Caveat: Codex runs only **trusted** hooks. A new `hooks.json` shows "2 hooks need review before they can run", and its hooks are skipped until they are trusted. Here they were trusted with `/hooks` → `t`, which writes `[hooks.state."<path>:pre_tool_use:0:0"] trusted_hash = "sha256:..."` into `config.toml`. The launcher would have to pre-seed this. The hash derivation was not reverse-engineered: it does not match the obvious JSON serialisations of the entry.
- **Control-op refusal:** `approve` with the valid token, sent from inside, gets `{"ok": false, "error": "forbidden"}` in every self-test run. It is also visible in the stub logs.

## Item 5: runtime driver (PASS)

The gateway was switched from the `vm` compute driver to `podman` (rootless podman 5.8.8, user socket, pasta, cgroup v2). It uses `userns = "keep-id"`, so the workload's uid is the host uid and bind-mounted files keep their ownership. The config is `spikes/s5/gateway.toml`, installed at `~/.config/openshell/gateway.toml`.

**uid mapping** (`evidence/uid-mapping.txt`). The workload runs as uid 1001, gid 1004: the host uid and gid. `id` and `/proc/self/status` both show 1001 for every uid field, real through filesystem. `/proc/self/uid_map` shows the mapping in two hops:
- keep-id maps inside 1001 to parent-namespace 0;
- rootless podman's parent namespace maps 0 to host uid 1001, and the other ids to subuids. Inside 0 maps to parent 1, which is a subuid.

So a host file owned 1001 with mode 0600 is readable inside by uid, and **only the mount table keeps it out**. This is what the two kinds of file showed:
- **Bound files are readable:** the ro-bound login file and a 0600 file in the rw-bound synthetic home were both readable.
- **Unbound files are absent:** a 0600 file in the real home gave ENOENT, because nothing from the real home is mounted.

Ownership and permission bits give no protection here. The real-home and Other accounts probes, which accept ENOENT/EACCES, are what check this.

One side effect: podman creates the mount-point placeholder for a file bound inside the rw home, such as `.codex/auth.json`. On the host, that placeholder is owned by a subuid (165536), from container root.

The image's base, ubuntu:24.04, ships a `ubuntu` user with uid 1000. The Containerfile adds the `agent` user (1001:1004) and `USER 1001:1004`, and the policy also sets `run_as_user`/`run_as_group` to the host ids. uid 1000 was never the workload's uid in this spike.

Bind mounts need three settings:
- `allow_driver_config = true`;
- `enable_bind_mounts = true`;
- `resource_admission.enabled = false`. With admission enabled, the gateway rejects bind mounts ("cannot be attached while resource admission is enabled: no trusted label resolver").

The sandbox shape is a supervisor container that launches the workload capability-free, with Landlock and 6 seccomp filters. The workload's network is only `lo`. DNS goes to OpenShell's policy DNS, and every `connect()` is transparently intercepted, then either refused with EACCES or proxied.

## Does the proxy check more than the CONNECT host? (Yes)

Evidence is in `evidence/proxy-authority-sni.txt`, run from inside with `/usr/bin/curl`, whose only allowed endpoint is `api.openai.com:443`. The proxy terminates TLS with a per-sandbox CA, which it injects through `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS` and similar variables.

1. **Control** (matching host): `http=421`, from the real upstream.
2. **Domain fronting through the Host header** (`-H "Host: example.org"` to `https://api.openai.com/`): `403 {"error":"request_authority_mismatch"}`, logged as `HTTP:GET [HIGH] DENIED ... engine:request-authority` and `FINDING:BLOCKED [HIGH]`.
3. **SNI and URL for another site, TCP to the allowed endpoint** (`--connect-to example.org:443:api.openai.com:443`): the certificate check fails. The proxy presents a leaf for `CN=api.openai.com`, the authorized endpoint, not for the client's SNI.
4. **The same with `-k`**: still `403 request_authority_mismatch`.
5. **SNI `example.org` with `Host: api.openai.com`, `-k`**: `http=421` from `api.openai.com`. The proxy ignores the client's SNI and originates its own TLS to the authorized endpoint, so a client SNI can't steer the upstream.

So under OpenShell, the §7 residual risk "a client can present a different SNI behind an allowed host" does not hold for HTTPS through the proxy. Authority and SNI are bound to the authorized endpoint. What remains is the path: the allowlist is still per host, not per path.

## ADR impact (for a later revision)

1. **The egress refusal marker differs from S3.** There is no CONNECT 403 with a marker header. The specific evidence is:
   - the policy-DNS synthetic address (198.18.0.0/15);
   - `connect()` giving EACCES;
   - the supervisor's OCSF `DENIED ... transparent_tcp_policy_denied` line, which the launcher reads with `openshell logs <name> --source sandbox`.

   "Direct network blocked" gives **EACCES**, not ENETUNREACH. The probe accepts both.
2. **The env allowlist must include OpenShell's fixed injected set:** `OPENSHELL_SANDBOX`, `OPENSHELL_USER_ENVIRONMENT`, the CA variables, `container`, `HOSTNAME`, `DEBIAN_FRONTEND` and `SHELL`. None of them holds a secret. Host env is not forwarded.
3. **Narrow the SNI/authority residual risk** (above). In exchange, **the supervisor sees plaintext, including bearer tokens**, so OpenShell's supervisor is inside the trusted computing base for credentials. This matters if OpenShell's proxy-side credential injection is adopted later. S5 bound the login files read-only instead and did not use injection.
4. **Codex managed shape:** bind a 0700 host dir at `/tmp/codex-daemon-<uid>`. The host-side `codex queue --remote` dials the real socket in that dir, not the `--listen` path.
5. **Codex hooks** (§4.2 "unsettled"): interactive Codex under a remote app-server runs `PreToolUse` and `UserPromptSubmit` with the real `hooks.json` schema, and a `PreToolUse` deny with a reason works.

   Two changes are needed before relying on this:
   - The launcher must pre-seed the trust state, or plan 4 must find out how Codex derives `trusted_hash`.
   - The hook command should not embed the per-session token, because that makes the hash per-session. Read the token from a file under `/run/hz` instead.

   `hooks.json` and `config.toml` live in the read-write synthetic home, so the agent can edit them. This is consistent with the ADR's "the hook is a UX layer, not a security control".
6. **Single-file read-only binds pin the inode.**
   - Writes give EROFS and renames give EBUSY inside, which is what "no refresh inside" needs.
   - A refresh on the host that replaces the file by rename is **not** seen by a running sandbox. This is expected bind-mount behaviour; it was not exercised here.
   - The freshness gate covers this for the session's maximum lifetime.
7. **Operational limits found:**
   - Sandbox names are limited to 19 characters, so `s5-<sid>` must stay short. Plan 4 needs a naming scheme.
   - `sandbox delete` is asynchronous.
   - The `openshell logs` buffer holds only a few hundred lines, so the launcher must read its checks right after the probes. It is not an audit log.
   - `sandbox exec` must get stdin `</dev/null`, or it hangs.
8. **Not covered here:**
   - OpenShell's proxy-side credential injection.
   - Package registry and git egress.
   - The reviewer's read-only worktree bind.
   - macOS.
   - The cause of Codex's non-fatal warning "couldn't save diagnostic logs to its local database".

## Reproduce

All commands run on the host as the agent user, with podman 5 on PATH:

```sh
export USER=$(id -un) LOGNAME=$(id -un) CONTAINERS_CONF=~/.config/podman5/containers.conf PATH=/opt/podman5/bin:$PATH
# one-time: gateway on the podman driver (see Host changes), then the image
podman build -t localhost/s5-agent:0 spikes/s5/image
# Claude
python3 spikes/s5/launch.py up claude t1 && python3 spikes/s5/launch.py agent claude t1
tmux -L s5spike send-keys -t t1 '...' Enter      # steering; attach: tmux -L s5spike attach -r -t t1
# Codex
python3 spikes/s5/launch.py up codex c2 && python3 spikes/s5/launch.py agent codex c2
CODEX_HOME=$(mktemp -d) codex queue --remote unix://$HOME/.cache/s5/c2/bridge/daemon/<hash> --thread <uuid> --message '...' </dev/null
# self-test against a live sandbox, and the negative controls (each must end "launch refused")
python3 spikes/s5/launch.py selftest codex c2
for m in canary-missing oa-canary-inside chosen-mismatch leak-env socket-dead; do S5_SELFTEST_INJECT=$m python3 spikes/s5/launch.py up claude n-$RANDOM; done
# teardown
python3 spikes/s5/launch.py down t1; python3 spikes/s5/launch.py down c2; tmux -L s5spike kill-server
```

The real logins are used only through the read-only binds, and only after checking on the host that the token outlives the session. The dummy accounts b, c and d under `~/.cache/s5/accounts` are spike-made fixtures:
- b is a symlink to a dir holding a dummy file;
- c is an empty dir;
- d is a dir with mode 000.

## Host changes (all user-level; no sudo, no system units)

| Change | Revert |
|---|---|
| `~/.config/openshell/gateway.env`: `OPENSHELL_COMPUTE_DRIVER=vm` → `podman`, plus `OPENSHELL_GATEWAY_CONFIG=...gateway.toml` (original saved as `gateway.env.s5-orig`) | `cp ~/.config/openshell/gateway.env.s5-orig ~/.config/openshell/gateway.env` |
| New `~/.config/openshell/gateway.toml` | `rm ~/.config/openshell/gateway.toml` |
| User unit `openshell-gateway.service` restarted to pick these up | `systemctl --user restart openshell-gateway.service` after reverting the two files |
| Image `localhost/s5-agent:0` in the user's podman 5 storage | `podman rmi localhost/s5-agent:0` (podman 5 env as above) |
| `~/.cache/s5/` (sessions, dummy accounts, canaries), `~/.s5-canary`, `/tmp/os-src` (OpenShell v0.1.2 source, for reading), `/tmp/s5-cxq`, `/tmp/s5-*.before` | `chmod -R u+rwx ~/.cache/s5 && rm -rf ~/.cache/s5 ~/.s5-canary /tmp/os-src /tmp/s5-cxq /tmp/s5-*.before` |
