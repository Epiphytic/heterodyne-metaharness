# Spike S5: OpenShell runtime on the reference host (ADR 0001 r14 §7 items 1-5)

Status: items 1, 3, 4 and 5 **PASS**. Item 2 **PASS except one DEVIATION** that needs an ADR amendment, which is an operator decision: under OpenShell a literal-address connection fails with **EACCES** from OpenShell's seccomp network broker, never with the "unreachable" result §7 specifies (details under Item 2). Reference host: Linux 6.17, rootless podman 5.8.8, OpenShell 0.1.2 with the podman compute driver. Caveats are listed per item and under ADR impact.
Revised after review r1 (REVISE, cycle 1): the agent launch reruns the whole self-test, the probes also run through each agent's own tool path, the direct-network probe is strict on the broker's mechanism, the Other accounts classifier was fixed, and the maximum lifetime is enforced.
CLIs tested: Claude Code 2.1.286 and codex-cli 0.160.0, both using the operator's real OAuth subscription logins, bound read-only.
Bead: btq-le4mq. Code: `spikes/s5/` (`launch.py`, `probes.py`, `probes.sh`, `netbattery.py`, `ws-request`, `image/Containerfile`, `gateway.toml`). The session socket stub and `hook.py` are reused unchanged from `spikes/s3/`. Raw evidence: `spikes/s5/evidence/`. The host home is written `$HOME`, and session tokens are masked as `<TOKEN>`. No OAuth token was printed or stored at any point: only expiry times and sha256 prefixes.

## Verdicts

| # | §7 item | Verdict |
|---|---|---|
| 1 | Both CLIs in the managed launch shape, each started from a host tmux session (send-keys, break-glass attach); Codex with its per-session `codex app-server` inside and `codex queue` from outside | **PASS** |
| 2 | Every launch self-test probe, including Other accounts, with its specific evidence; a failing probe refuses the launch | **PASS, with one DEVIATION**: direct network gives EACCES (OpenShell broker), not unreachable. ADR amendment needed |
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

`launch.py agent <adapter> <sid>` launches the agent. It never trusts an earlier result:
1. **Fresh gate.** It deletes `selftest.rc` and reruns the complete self-test (freshness gate, canary, outer fence, every probe, the log checks) immediately before the launch. Any exception counts as a FAIL. On anything but PASS it prints `REFUSED`, deletes the sandbox and exits 2.
2. **Launch.** It starts the agent in `tmux -L s5spike new-session -d -s <sid>` as `openshell sandbox exec -n s5-<sid> --tty -- <cli> ...`:
   - **Claude:** `--permission-mode bypassPermissions`.
   - **Codex:** first a detached `codex app-server --listen unix:///run/hz-bridge/app.sock` inside the sandbox. The launcher waits for its socket (refusing if it does not appear), then runs the TUI as `codex --remote unix:///run/hz-bridge/app.sock --dangerously-bypass-approvals-and-sandbox`.
3. **Lifetime reaper.** It starts a detached `launch.py reap` process (below).
4. **Agent-path self-test.** The session's first input is a scripted prompt that makes the agent run the probes through its own tool (Claude's Bash tool, the Codex app-server's exec). The session is stopped unless they all pass (below). Only then does it print `session ready for work`.

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

## Item 2: launch self-test (PASS, with one DEVIATION)

The self-test runs twice per launch, through two different execution paths:
- **exec path:** before the agent starts, the probes run in a fresh `openshell sandbox exec` process. This is the gate in `up` and the first step of `agent`.
- **agent path:** the same probes then run as a tool call inside the live agent, so they execute in the process tree that will do the work (next subsection).

Every probe prints its own evidence, and only the specific enforcement passes. Current runs: `evidence/agent-path-claude.txt` (session a1) and `evidence/agent-path-codex.txt` (a2) hold both paths, and both end `AGENT-PATH SELFTEST PASS`. `evidence/selftest-pass.txt` is the pre-review run.

| Probe | What must hold under OpenShell | Evidence (Codex a2; the Claude run is the same apart from paths and hosts) |
|---|---|---|
| freshness-gate (outside) | Access token outlives the session maximum (2 h) plus the stop margin (15 min); only the hours left are printed | `access token 170.44 h left, need > 2.25 h` (Claude: 5.32 h) |
| canary-precondition (outside) | `~/.s5-canary` is written and readable on the host just before the probes; else ABORT rc 3 | PASS |
| outer-fence-network-none (outside, **added**) | `podman inspect` of the workload container shows `NetworkMode=none`: the kernel-level fence under the broker | `workload container NetworkMode=none` |
| real-home-canary-unreadable | Inside: ENOENT/EACCES/EPERM | `-> ENOENT` |
| non-allowlisted-host-blocked | `example.org` resolves through OpenShell's policy DNS to a synthetic 198.18.0.0/15 address (the marker), `connect()` gives **EACCES**, and curl gives rc 7 "Permission denied" | `example.org -> 198.18.0.2 (policy-DNS synthetic=True) connect -> EACCES; curl rc=7` |
| direct-network-blocked (**DEVIATION**) | All of: only `lo`, no IPv4 routes, IPv6 routes on `lo` only; `Seccomp: 2`, `NoNewPrivs: 1`, `CapEff` 0; TCP to literal IPv4 and IPv6 addresses and a UDP connect give EACCES; a UDP `sendto` to a non-DNS address gives EDESTADDRREQ; raw and ICMP sockets give EPROTONOSUPPORT. These are the OpenShell broker's own answers (below), so any other errno fails | `DEVIATION (EACCES by the OpenShell broker, not unreachable; ADR amendment needed): non-lo interfaces=none ipv4 routes=0 ipv6 non-lo routes=0 Seccomp=2 NoNewPrivs=1 CapEff=0000000000000000 tcp4 connect -> EACCES tcp6 connect -> EACCES udp connect -> EACCES udp sendto -> EDESTADDRREQ raw socket -> EPROTONOSUPPORT icmp socket -> EPROTONOSUPPORT` |
| control-op-rejected | `approve` with a valid token gets the forbidden reply | `{"ok": false, "error": "forbidden"}` |
| wsd-socket-absent | `/run/hz/wsd.sock` absent | `exists=False` |
| allowlisted-host-reachable | curl rc 0 **and** the issuer is the OpenShell sandbox CA, which proves it went through the proxy | `curl rc=0 http=421 issuer='CN=OpenShell Sandbox CA; O=OpenShell'` |
| model-host-exec-path / model-host-agent-path (**added**) | curl to the CLI's model host. exec path: refused (rc 7), because curl has no CLI ancestor. agent path: reachable through the proxy (rc 0, OpenShell CA), because the CLI is curl's ancestor | exec: `chatgpt.com: curl rc=7`; agent: `chatgpt.com: curl rc=0 http=403 issuer='CN=OpenShell Sandbox CA; O=OpenShell'` |
| hook-event-accepted | `hook_event` with a valid token gets `{"ok": true}` | PASS |
| host-env-not-inherited | exec path: no variable outside the launcher allowlist plus OpenShell's fixed, secret-free set; `OPENSHELL_USER_ENVIRONMENT` keys checked too. agent path: names only, as INFO, because each CLI adds its own variables to its tools | exec: `unexpected vars: none`; agent: `INFO agent-tool-env [CODEX_CI CODEX_SESSION_ID CODEX_THREAD_ID ...]` |
| openshell-control-material-unreadable (**added**) | The supervisor's channel, TLS key and JWT are not readable by the workload | `/.openshell/channel/... -> ENOENT`, `/run/secrets -> EACCES`, `/etc/openshell/... -> ENOENT` |
| other-accounts | Each other account's login file, at both its configured path and its canonical path: present → ENOENT/EACCES; unknown → ENOENT/EACCES; absent → ENOENT. The outside canary gives ENOENT. The chosen file's sha256 matches the host's, and writing to it fails | b (present, configured path is a symlink): both paths → ENOENT; c (absent) → ENOENT; d (unknown: dir mode 000) → ENOENT; e (unknown: dangling symlink) and its target → ENOENT; canary → ENOENT; chosen `sha256-match=True write->EROFS` |
| proxy-logged-refusal (outside) | The supervisor log has this run's `DENIED /usr/bin/curl(0) -> example.org:443 [reason:transparent_tcp_policy_denied]` | PASS |
| proxy-logged-allow (outside) | The supervisor log has this run's `ALLOWED /usr/bin/curl(0) -> api.openai.com:443 [policy:probe_control engine:opa]` | PASS |
| proxy-logged-direct-deny (outside) | The supervisor log has this run's literal-IP denial | `DENIED /usr/bin/python3.12(0) -> 1.1.1.1:443` <!-- install-agnostic: allow=ip-port (public probe target) --> |
| exec-path-not-agent / agent-ancestry-allowed (outside, **added**) | The supervisor log shows which path ran the probes. exec: `DENIED /usr/bin/curl(0) -> <model host>:443 [reason:transparent_tcp_policy_denied]`. agent: `ALLOWED /usr/bin/curl(0) -> <model host>:443 [policy:<adapter>_model ...]` | exec: `DENIED ... -> chatgpt.com:443`; agent: `ALLOWED /usr/bin/curl(0) -> chatgpt.com:443 [policy:codex_model engine:opa]` |

### The launch never trusts a cached PASS (review r1 #1)

`agent` deletes `selftest.rc`, reruns the full gate, and writes the new result on every exit path, including the early ABORTs and exceptions. `evidence/cached-pass-control.txt` holds two controls where `up` had just ended `SELFTEST PASS` (stored rc 0), and a failure was then injected at `agent` time:
- `S5_SELFTEST_INJECT=leak-env agent claude r1`: `FAIL host-env-not-inherited [unexpected vars: LEAK]`, `REFUSED`, rc 2, stored rc now 1, no tmux session.
- `S5_SELFTEST_INJECT=canary-missing agent claude r2`: `ABORT canary-precondition`, `REFUSED`, rc 2, stored rc now 3, no tmux session, sandbox gone.

### Probes through the agent's own tool path (review r1 #2)

OpenShell authorizes a connection by the executable that owns the socket **or any of its executable ancestors** (OpenShell `docs/security/best-practices.mdx`). So a `sandbox exec` probe does not show what a tool started by the agent can reach. The agent path closes that gap:
1. The launcher writes the probe input to `run/agent-probe.json` (it appears read-only at `/run/hz`).
2. Once the CLI shows its prompt, it types one scripted prompt: run `python3 /run/hz/probes.py < /run/hz/agent-probe.json > "$HOME/.s5-agent-probe.out" 2>&1; echo "probes-rc=$?" >> ...`, then reply with only the exit status.
3. It waits for `probes-rc=` in the synthetic home, prints each line prefixed `agent:`, and runs the log checks for this path.
4. If any probe fails, or the run times out, it prints `AGENT-PATH SELFTEST FAIL: stopping the session` and deletes the sandbox.

Both CLIs passed: the Claude Bash tool (session a1) and the Codex app-server exec (session a2). Real-home canary, egress, direct network, control-op, socket and Other accounts probes all pass on the agent path.

**Proof that the probes ran inside the agent.** It comes from the host side: the supervisor logged `ALLOWED /usr/bin/curl(0) -> api.anthropic.com:443 [policy:claude_model engine:opa]` (Codex: `chatgpt.com` under `codex_model`). The policy admits that host only for the CLI binary, so curl got through only because the CLI was its ancestor. The same curl on the exec path is `DENIED`. For Claude, the session socket also logged the hook event for that very tool call: `hook_event PreToolUse Bash {"command": "python3 /run/hz/probes.py ..."} -> ok`.

**Finding (ADR impact 9):** a tool the agent runs can reach the agent's model host. Per-binary policy does not separate the CLI from its child processes.

### Direct network: why EACCES, and the deviation (review r1 #3)

Evidence: `evidence/direct-network-mechanism.txt` (socket battery inside, plus podman inspect outside) and `evidence/plain-podman-control.txt`.

There are two layers:
- **Kernel fence:** the workload container runs with podman `NetworkMode=none`. Its netns has only `lo`, no IPv4 routes, and IPv6 routes on `lo` only. Its pid 1 is `openshell-sandbox launch-capability-free 1001 1004`. Egress exists only through the separate supervisor container (`NetworkMode=host`), which runs the policy proxy.
- **Seccomp broker:** OpenShell's seccomp user-notification broker (`crates/openshell-sandbox/src/network_broker.rs`) traps every INET `socket()`, `connect()` and `sendto()`. The workload shows `Seccomp: 2` with 6 filters, `NoNewPrivs: 1`, and `CapEff` 0. The broker:
  - admits only TCP and UDP sockets (others get EPROTONOSUPPORT; `AF_PACKET` gets EPERM);
  - holds each TCP `connect()` until the supervisor decides, then relays an allowed one to the proxy, or answers EACCES for a denied one (`tcp_denial_errno`);
  - answers EACCES to a UDP `connect()` to a non-DNS address;
  - answers EDESTADDRREQ to a UDP `sendto()` to a non-DNS address.

The broker answers before the kernel's routing runs, so ENETUNREACH is **never observable** inside OpenShell. The plain `--network=none` control without OpenShell shows the kernel's answer: ENETUNREACH for TCP, UDP and IPv6.

The probe is now strict on this mechanism. It passes only with the full set of broker answers above, plus the namespace, route and seccomp evidence. It fails under plain `--network=none` (ENETUNREACH, NoNewPrivs 0) and under pasta (connected). UDP EDESTADDRREQ is accepted only as the broker's answer for a destination-bearing `sendto()`, together with EACCES for the UDP `connect()`, never as a generic error.

**This does not meet §7 as written**, which requires the literal-address connection to fail as unreachable. The result is labelled `DEVIATION` in every run. Adopting OpenShell needs an ADR amendment that defines this alternative proof: broker EACCES plus `NetworkMode=none` checked from outside. That is the operator's decision.

### Other accounts classifier and a non-default chosen account (review r1 #4)

The host-side classifier, `classify()` in `launch.py`, now gives:
- **present:** the login file opens.
- **absent:** the open fails **and** the parent dir's listing really lacks the file name.
- **unknown:** anything else, including a listed but unopenable file (a dangling symlink, account e) and an unlistable dir (mode 000, account d).

An account whose canonical login file is the chosen one's is an alias of the chosen account, not "other". It is printed as INFO and skipped (account alias, when the default is chosen).

`evidence/non-default-account.txt` is a self-test with `S5_ACCOUNT=b`, on dummy logins only, with no model call:
- The chosen file is b's canonical `accounts/claude-b/.credentials.json`, reached through the configured symlink `claude-b-link`. It is the only login bind.
- The real default login is classified `present` and checked at its configured path `$HOME/.claude/.credentials.json`.
- It is checked again through a second configured path, `accounts/claude-default-alias/.credentials.json`, a symlinked dir, and through that path's canonical path `$HOME/.claude/.credentials.json`.
- Every path gives ENOENT inside, as do c (absent), d (unknown) and e (unknown, plus its target). The run ends `SELFTEST PASS`.

### Maximum lifetime is enforced (review r1 #5)

`agent` starts a detached reaper (`launch.py reap`, with its pid in `reaper.pid` and its log in `lifetime.log`). The lifetime is 2 h, with a 15 min stop margin; the spike knobs `S5_MAX_LIFETIME_S` and `S5_STOP_MARGIN_S` override them, and the freshness gate uses the same values. The reaper:
1. logs when the stop window opens, at the maximum minus the margin;
2. at the maximum, sends Escape to interrupt the agent's turn;
3. then deletes the sandbox and the tmux session.

`down` kills the reaper by PID.

`evidence/lifetime-reaper.txt` shows session a1 with a 240 s maximum and 60 s margin:
- `stop window open` at 13:35:34;
- `maximum lifetime reached: interrupting the agent` at 13:36:34;
- `session stopped` at 13:36:37.

Afterwards a1's tmux session and sandbox were gone, while a2 kept running.

This is the spike's simple, hard stop. It does not wait for a turn boundary within the margin; that finer stop, and the relaunch through the gate, are plan 4 launcher duties.

### Negative controls

`evidence/negative-controls.txt` was rerun after the review, with sids n1-n6. Each run ended in launch refused, and `openshell sandbox list` confirmed the sandbox was deleted; `down` now waits until the asynchronous delete finishes.

| `S5_SELFTEST_INJECT=` | Outcome |
|---|---|
| `canary-missing` | `ABORT canary-precondition ... not readable outside`, rc 3, no probes run |
| `oa-canary-inside` (the canary path points into the bound home) | `FAIL other-accounts [... canary:...oa-canary-copy->READABLE ...]`, rc 1 |
| `chosen-mismatch` (pinned hash wrong) | `FAIL other-accounts [... sha256-match=False ...]`, rc 1 |
| `leak-env` (an extra variable in the probe env) | `FAIL host-env-not-inherited [unexpected vars: LEAK]`, rc 1 |
| `socket-dead` (stub killed before create) | `FAIL control-op-rejected` and `FAIL hook-event-accepted` (`ECONNREFUSED`), rc 1 |

In n4 (`leak-env`), the host-side `podman inspect` of the outer fence hung while it raced my concurrent plain-podman runs. I killed it by PID. `outer-fence-network-none` then failed too, and the launch was refused, so n4 doubles as a fence-failure control. The outer fence now has a 30 s timeout. n6 is the clean `leak-env` rerun.

**Plain-podman control** (`evidence/plain-podman-control.txt`). The same `probes.py` was run in the same image without OpenShell, and it fails on both networks:
- `--network=pasta`: non-allowlisted-host, direct-network, allowlisted-host and model-host all FAIL (real DNS, a real interface, a Google Trust Services issuer), and so do the socket probes.
- `--network=none`: the egress probes FAIL (no policy DNS), and so do the socket probes. direct-network-blocked now also FAILS: the kernel gives ENETUNREACH, not the broker's answers.

This shows that the probes recognize OpenShell's enforcement specifically, not just "some failure". Writing this control exposed two probe bugs, both fixed:
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

   "Direct network blocked" gives **EACCES** from OpenShell's seccomp broker. ENETUNREACH is never observable, so §7's "fails as unreachable" cannot be met as written. **Amendment needed (operator decision):** accept the broker's specific answers (EACCES for TCP and UDP connects, EDESTADDRREQ for a non-DNS UDP `sendto`, EPROTONOSUPPORT for raw and ICMP sockets) together with `Seccomp: 2`/`NoNewPrivs: 1` inside and `NetworkMode=none` checked from outside, as the alternative proof. Until then item 2 stays a DEVIATION.
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
8. **Lifetime stop:** the spike enforces a hard stop at the maximum (Escape, then delete). The ADR's turn-boundary stop within the margin, and the relaunch through the gate, are left to the plan 4 launcher.
9. **Per-binary egress covers the CLI's whole process tree.** OpenShell matches the socket owner or any executable ancestor, so every tool the agent runs (curl, git, python) can reach the model host. It also means the launch self-test must run through the agent's tool path, not only through `sandbox exec`, because the two paths get different policy. ADR §7's per-binary allowlist should state this. If tools must not reach the model host, OpenShell's per-binary policy alone is not enough.
10. **Not covered here:**
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
python3 spikes/s5/launch.py up claude t1 && python3 spikes/s5/launch.py agent claude t1   # agent reruns the gate + agent-path probes
tmux -L s5spike send-keys -t t1 '...' Enter      # steering; attach: tmux -L s5spike attach -r -t t1
# Codex
python3 spikes/s5/launch.py up codex c2 && python3 spikes/s5/launch.py agent codex c2
CODEX_HOME=$(mktemp -d) codex queue --remote unix://$HOME/.cache/s5/c2/bridge/daemon/<hash> --thread <uuid> --message '...' </dev/null
# self-test against a live sandbox, and the negative controls (each must end "launch refused")
python3 spikes/s5/launch.py selftest codex c2
for m in canary-missing oa-canary-inside chosen-mismatch leak-env socket-dead; do S5_SELFTEST_INJECT=$m python3 spikes/s5/launch.py up claude n$((i+=1)); done   # sids: <= 16 chars
# cached PASS must not launch: a PASS from up, then a failure injected at agent time
python3 spikes/s5/launch.py up claude r1 && S5_SELFTEST_INJECT=leak-env python3 spikes/s5/launch.py agent claude r1   # REFUSED, rc 2
# non-default chosen account (dummy login, self-test only)
S5_ACCOUNT=b python3 spikes/s5/launch.py up claude nb
# enforced lifetime: a short maximum
S5_MAX_LIFETIME_S=240 S5_STOP_MARGIN_S=60 python3 spikes/s5/launch.py agent claude a1; cat ~/.cache/s5/a1/lifetime.log
# direct-network mechanism battery inside a live sandbox
openshell sandbox exec -n s5-c2 --no-tty -- python3 - < spikes/s5/netbattery.py
# teardown
python3 spikes/s5/launch.py down t1; python3 spikes/s5/launch.py down c2; tmux -L s5spike kill-server
```

The real logins are used only through the read-only binds, and only after checking on the host that the token outlives the session. The dummy accounts under `~/.cache/s5/accounts` are spike-made fixtures. Their dummy login files carry only a far expiry, never a token:
- b is a symlink to a dir holding a dummy file;
- c is an empty dir;
- d is a dir with mode 000;
- e is a dir whose login file is a dangling symlink;
- alias is a symlink to the real default login dir: a second configured path for the default account.

## Host changes (all user-level; no sudo, no system units)

| Change | Revert |
|---|---|
| `~/.config/openshell/gateway.env`: `OPENSHELL_COMPUTE_DRIVER=vm` → `podman`, plus `OPENSHELL_GATEWAY_CONFIG=...gateway.toml` (original saved as `gateway.env.s5-orig`) | `cp ~/.config/openshell/gateway.env.s5-orig ~/.config/openshell/gateway.env` |
| New `~/.config/openshell/gateway.toml` | `rm ~/.config/openshell/gateway.toml` |
| User unit `openshell-gateway.service` restarted to pick these up | `systemctl --user restart openshell-gateway.service` after reverting the two files |
| Image `localhost/s5-agent:0` in the user's podman 5 storage | `podman rmi localhost/s5-agent:0` (podman 5 env as above) |
| `~/.cache/s5/` (sessions, dummy accounts, canaries), `~/.s5-canary`, `/tmp/os-src` (OpenShell v0.1.2 source, for reading), `/tmp/s5-cxq`, `/tmp/s5-*.before` | `chmod -R u+rwx ~/.cache/s5 && rm -rf ~/.cache/s5 ~/.s5-canary /tmp/os-src /tmp/s5-cxq /tmp/s5-*.before` |
