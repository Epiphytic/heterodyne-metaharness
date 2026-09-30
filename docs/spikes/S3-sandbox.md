# Spike S3: bubblewrap sandbox and the §7 launch self-test

Status: **pass** on Linux/bubblewrap 0.9.0. Seatbelt (macOS) is not covered here.
CLIs tested: Claude Code 2.1.283 and codex-cli 0.157.0, both using OAuth subscription login.
Code: `spikes/s3/` (`proxy.py`, `bridge.py`, `session_stub.py`, `launch.py`, `probes.sh`, plus `hook.py` and `watch.py`, which the spike added).

## Result summary

The probes were strengthened in **fix round 1**, after a cross-model review found that probes 1–3 could pass vacuously: any `curl` failure, or a missing canary, counted as PASS. The self-test is now `launch.py --selftest <work> <home> <run> <token> <proxy.log>`. It fails closed, and it brackets the in-sandbox probes with checks outside the sandbox:
- **Before the probes:** the canary `~/.hz-canary` must exist and be readable outside the sandbox. If not, the self-test aborts (rc 3) and nothing is launched.
- **After the probes:** `proxy.log` must contain this run's `example.org:443 allowed=false` and `api.openai.com:443 allowed=true` entries.

Output from the self-test run after the fix. Each probe prints its own evidence in brackets:

| # | Probe | What must hold (anything else FAILs) | Result and evidence |
|---|---|---|---|
| 1 | real-home-canary-unreadable | `open(<real-home>/.hz-canary)` inside fails with ENOENT, EACCES or EPERM, and the canary was readable outside immediately before the launch | PASS: `-> ENOENT`. The canary is **absent**, not denied: bwrap mounts nothing from the real home except the two ro-bound CLI install dirs, so the path does not exist in the sandbox's mount namespace. |
| 2 | non-allowlisted-host-blocked | curl fails, the CONNECT response code is 403, **and** the response carries our proxy's `X-HZ-Egress: denied` marker header; proxy.log records the refusal | PASS: `curl rc=56 CONNECT=403 X-HZ-Egress-denied=1`; proxy.log has `{"target": "example.org:443", "allowed": false}` |
| 3 | direct-network-blocked | `/proc/net/dev` shows no interface except `lo`, **and** a raw TCP `connect()` to the literal `1.1.1.1:443` fails with ENETUNREACH or EHOSTUNREACH. A timeout, refusal, TLS error or success FAILs | PASS: `non-lo interfaces='none' connect(1.1.1.1:443) -> ENETUNREACH` |
| 4 | control-op-rejected | `{"type":"approve"}` with a valid token gets the forbidden reply | PASS: `reply={"ok": false, "error": "forbidden"}` |
| 5 | wsd-socket-absent | `/run/hz/wsd.sock` does not exist | PASS: `rc=1; /run/hz: bridge.py probes.sh proxy.sock session.sock` |
| 6 | allowlisted-host-reachable | curl rc 0 **and** the CONNECT response code is 200; proxy.log records the allow | PASS: `curl rc=0 connect=200 http=421`; proxy.log has `{"target": "api.openai.com:443", "allowed": true}` |
| 7 | hook-event-accepted | `{"type":"hook_event"}` with a valid token gets `{"ok": true}` | PASS: `reply={"ok": true}` |
| 8 | host-env-not-inherited (**added**) | no env var outside the launcher allowlist | PASS: `unexpected vars: none` |

The self-test was run twice after the fix, both times with `SELFTEST PASS`: the second run was on the final `launch.py`, which has the credential binds on by default and flushes output. The launcher's own checks also passed: `canary-precondition`, `proxy-logged-refusal` and `proxy-logged-allow`. Final line: `SELFTEST PASS`.

Negative controls, run to show that each strengthened probe can fail:

| Control | Outcome |
|---|---|
| Canary deleted before `--selftest` | `ABORT canary-precondition ... (No such file or directory)`, rc 3; the sandbox is not launched |
| Probes pointed at a path where the canary *is* readable | probe 1 `FAIL ... -> READABLE` |
| Proxy killed (a broken egress path) | probe 2 `FAIL [curl rc=56 CONNECT=000 X-HZ-Egress-denied=0]`. The old probe would have **passed** here, because curl still failed. Probe 6 FAILs too, both proxy.log checks FAIL, and the result is `SELFTEST FAIL (launch refused)`. |
| Probe 3's connect check run in the host network namespace | 3 non-lo interfaces, `connect -> CONNECTED`, so the check does distinguish namespace isolation |
| Probe 8 with an injected variable (`env LEAK=1`) | `FAIL host-env-not-inherited (got LEAK ...)` |

History: before fix round 1, the plan's original 7 probes all passed on the first run of the unmodified plan code.

Manual checks run in addition to the probes:
- A `hook_event` with the wrong token is rejected (`forbidden`).
- `/run/hz` is not writable.
- Under the real home, only the two ro-bound CLI install dirs are visible. The real `~/.claude/.credentials.json` and `~/.codex/auth.json` are absent.

| Login / hook run | Result |
|---|---|
| `codex exec --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=low "Reply only with OK"` | FAIL with the plan allowlist: `chatgpt.com` was blocked, and the run failed with "workspace routing discovery failed". **PASS** (`OK`) once `chatgpt.com` was allowed. |
| `claude -p --permission-mode bypassPermissions "Reply only with OK"` | FAIL with the plan launcher (`claude` not found). **PASS** (`OK`) once the binary path was resolved. |
| Claude `PreToolUse` hook, `claude -p ... "run: echo hi"` | **PASS**: `session.log` shows `hook_event` / `PreToolUse` / `Bash` / `echo hi`, answered with `{"ok": true}`. |
| Both CLIs with the credential files **ro-bound** (`HZ_S3_RO_CREDS=1`, now the default) | **PASS**: both returned `OK`. |
| Both CLIs and the hook re-run after `--clearenv` | **PASS** |

## bwrap argv as run

Paths are shown relative to `~` and the temporary spike dir `$S3`. The CLI install dirs come from `shutil.which(...)` followed by `resolve()`.

```
bwrap --unshare-all --die-with-parent --new-session --clearenv \
  --setenv LANG C.UTF-8 --setenv TERM "$TERM" --setenv USER "$USER" \
  --proc /proc --dev /dev --tmpfs /tmp \
  --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
  --ro-bind /usr /usr --ro-bind /etc/ssl /etc/ssl --ro-bind /etc/ca-certificates /etc/ca-certificates \
  --ro-bind /etc/alternatives /etc/alternatives --ro-bind /etc/passwd /etc/passwd \
  --ro-bind /etc/group /etc/group --ro-bind /etc/nsswitch.conf /etc/nsswitch.conf \
  --ro-bind /etc/localtime /etc/localtime \
  --ro-bind ~/.codex/packages/standalone/releases/<ver>-x86_64-unknown-linux-musl (same path) \
  --ro-bind ~/.nvm/versions/node/<ver>/lib/node_modules/@anthropic-ai/claude-code (same path) \
  --bind $S3/home $S3/home --setenv HOME $S3/home \
  --bind $S3/work $S3/work --chdir $S3/work \
  --ro-bind $S3/run /run/hz --setenv HZ_SESSION_SOCKET /run/hz/session.sock \
  --setenv PATH /usr/bin:<install>/bin:...:<install> \
  [--ro-bind $S3/home/.claude/.credentials.json (same) --ro-bind $S3/home/.codex/auth.json (same)]   # default since fix round 1; HZ_S3_RO_CREDS=0 opts out \
  -- python3 /run/hz/bridge.py /run/hz/proxy.sock -- <resolved agent binary> <args...>
```

The plan's mount set worked as written: no `/etc/resolv.conf` or `/etc/hosts` is needed, because DNS is resolved by the proxy outside the sandbox. A musl-static Codex binary and the glibc-linked `claude.exe` both ran with only `/usr` plus their install dir.

## Egress hosts per CLI (from `proxy.log`)

| CLI | Needed (allowed) | Attempted and denied (non-fatal) |
|---|---|---|
| Codex (ChatGPT login) | CONNECT to `chatgpt.com` observed (all model traffic). With `chatgpt.com` denied, Codex's own stderr named the URLs it could not reach, including `https://chatgpt.com/backend-api/ps/mcp` (its MCP client) and workspace routing. The proxy cannot see paths. | `ab.chatgpt.com` (experiments/telemetry), `sdmntpr*.oaiusercontent.com` (content downloads) |
| Claude Code (claude.ai login) | CONNECT to `api.anthropic.com` and `mcp-proxy.anthropic.com` observed. The second is inferred to be the claude.ai MCP connector channel, from the hostname and Claude writing `mcp-needs-auth-cache.json` at the same time. | `http-intake.logs.us5.datadoghq.com` (telemetry) |
| Probes | `api.openai.com` | `example.org` |

Adapter defaults for Plan 4:
- Codex: `chatgpt.com`, plus `api.openai.com` for API-key mode.
- Claude: `api.anthropic.com`.

**Proxy limit:** `proxy.py` checks only the host in the CONNECT request line. It then relays opaque bytes: there is no TLS SNI check and no check of the HTTP `Host`/authority inside the tunnel, and paths are invisible. A client can therefore CONNECT to an allowed host and present a different SNI, which matters for multi-tenant front ends and CDNs. Exact host entries narrow the destination set, but on their own they do not guarantee the TLS peer. Plan 4 should at least resolve and pin the upstream from the CONNECT host (already the case: the proxy dials that host itself) and consider peeking the ClientHello SNI and requiring it to match the CONNECT host.

Use exact hosts. The plan's `.anthropic.com`, `.openai.com` and `.claude.ai` suffix wildcards are broader than needed. Telemetry can stay denied: both CLIs completed without it.

## Credential-write finding (ADR-relevant)

Method:
- The spike recorded sha256, mtime and inode of the **copies** in `$S3/home` before and after every run.
- `inotifywait` is not installed. `spikes/s3/watch.py` (ctypes inotify) watched `$S3/home`, `$S3/home/.claude` and `$S3/home/.codex` for MODIFY, CLOSE_WRITE, CREATE, MOVED_* and DELETE events. These catch atomic tmp-plus-rename rewrites as well as in-place writes.

Observations:
- At launch, the Claude access token had about 5.5 h left (`expiresAt`). The Codex access JWT had about 140 h left, and `last_refresh` was 4 days old.
- Neither CLI wrote its credential file in any run: 5 Claude and 4 Codex sessions, including hook runs.
  - The hashes and inodes of `.credentials.json` and `auth.json` never changed.
  - There were zero inotify events on either file.
- With both files **ro-bound** over the writable synthetic home, both CLIs logged in and answered normally.
- Both CLIs write heavily to the **rest** of their config dirs, so those dirs must be writable.
  - Codex writes `~/.codex/*.sqlite*`, `models_cache.json`, `installation_id`, `sessions/` and `cache/`.
  - Claude writes `~/.claude/sessions`, `policy-limits.json` (via tmp-plus-rename), `remote-settings.json`, `mcp-needs-auth-cache.json`, `~/.claude.json` (with a `.claude.json.lock`) and `~/.cache`.
- **Not exercised:** the refresh path, because an expired access token was deliberately not tested (credential-rotation risk). The spike therefore shows that no write happens *while the access token is valid*. It does not show what happens at expiry.

Implication: the sandbox copy holds the **operator's own refresh token**, not a separate grant.
- If a sandboxed CLI refreshes, the provider may rotate the refresh token, and the operator's host login (and every other session copied from it) would break.
- With a read-only bind, the CLI cannot even persist the rotated token, so the next launch would fail too.

## Deviations from the plan code

1. **`launch.py`: agent binary resolution.** `claude` on the host is a symlink to `.../claude-code/bin/claude.exe`. `binary_roots()` puts `<root>/bin` on `PATH`, but no file named `claude` exists there, so the run failed with `FileNotFoundError: 'claude'`. The fix is `resolve_agent()`, which replaces `cmd[0]` with the resolved real path when it names a known agent.
2. **`launch.py`: `--clearenv` plus an explicit env.** bwrap passes the caller's whole environment through by default. Inside the sandbox the spike saw an operator bot token, the parent Claude session's messaging socket path and token, SSH/TMUX/DBus variables, and parent `CLAUDE_CODE_*` settings such as `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`. That breaks "only model credentials inside", and it silently changes CLI behaviour, including the egress it attempts. The launcher now clears the environment and sets only `LANG`, `TERM`, `USER`, `HOME`, `PATH` and `HZ_SESSION_SOCKET`. The bridge adds the proxy variables.
3. **`probes.sh`: probe 8 `host-env-not-inherited`**, which makes the leak above a fail-closed check. The 7 plan probes are unchanged.
4. **Allowlist: `chatgpt.com` added.** Codex with ChatGPT auth talks only to `chatgpt.com`, not `*.openai.com`. The allowlist used for the login runs was `api.openai.com,.openai.com,chatgpt.com,api.anthropic.com,.anthropic.com,.claude.ai`.
5. **`bridge.py`: `forward()` swallows `OSError`.** A peer closing the connection printed a `BrokenPipeError` traceback into the agent's stderr. This is cosmetic.
6. **`launch.py`: optional `HZ_S3_RO_CREDS=1`** ro-binds the two credential files over the writable home. It exists for the Step 7.5 experiment. The bind was opt-in during the login runs. Since fix round 1 it is **on by default** in `launch.py`, and `HZ_S3_RO_CREDS=0` opts out. The login runs that used the ro bind (see the login table) are the evidence that the default works. Plan 4 should keep it on by default.
7. **Added `hook.py`** (the Claude `PreToolUse` hook that forwards the event to the session socket, exiting 2 on refusal) and **`watch.py`** (an inotify logger, because `inotifywait` is not installed). The hook was configured in the synthetic `~/.claude/settings.json` as `python3 /run/hz/hook.py <token>`.
8. **Runs used `</dev/null`** after the first hook run, because `claude -p` otherwise waits 3 s for stdin.
9. **Fix round 1 (self-test strengthening):**
   - `probes.sh` probes 1–3 were rewritten as in the table above, and every probe now prints its evidence.
   - `proxy.py` adds the `X-HZ-Egress: denied` header to its 403.
   - `launch.py --selftest` was added: the canary precondition beforehand, the proxy.log checks afterwards, and fail-closed exit codes.
   - `bridge.py` defines the loopback proxy address once (`LISTEN`, `PROXY_URL`).
   - None of these changes affect the model-login runs. The only proxy change is on the deny path, and the bridge refactor keeps its behaviour, so the logins were not re-run.

## ADR impact

- **§7 "copied in read-only": holds for the auth files only, and only while the access token is valid.**
  - The rest of the synthetic home must be writable, because both CLIs write session state there. Suggested wording: "auth files ro-bound; the rest of the synthetic home is a writable per-session copy".
  - Refresh-token sharing is an open issue. Suggested amendment: a launch gate that refuses to start (or refreshes on the host first) when the access token expires before the session's maximum lifetime; or per-sandbox credentials, or credentials injected at the proxy.
- **§7 "only credential inside is model auth": needs two amendments.**
  - (a) The launcher must clear the environment, and a probe must enforce it (deviations 2 and 3).
  - (b) The model OAuth token probably also reaches the operator's account-level MCP connectors, which can include mail, chat, drive and similar services: messaging and secret material through the model credential. The evidence: Claude made CONNECTs to `mcp-proxy.anthropic.com` (inferred to be the claude.ai connector channel), and Codex's stderr named `chatgpt.com/backend-api/ps/mcp`. The proxy saw only a CONNECT to `chatgpt.com`. What each connector can actually do from the sandbox was not tested.
    - Recommendation: deny `mcp-proxy.anthropic.com` at the proxy and disable claude.ai MCP servers in the adapter config.
    - For Codex, disable connectors in config. The CONNECT proxy cannot filter paths on `chatgpt.com`.
- **§7 network allowlist:** use exact hosts per adapter (listed above), not suffix wildcards. Also state the enforcement limit: a CONNECT-host allowlist does not check SNI or the in-tunnel authority, so it is a host-level boundary, not a TLS-peer or path boundary.
- **§7 self-test:** the ADR's three probes are the right set, but **not as loosely written**. "Must fail" is not enough, because a generic failure (missing canary, dead proxy, a timeout) looks exactly like enforcement. §7 should require each probe to prove the *specific* enforcement:
  - The canary is readable outside immediately before launch, and inside it gives ENOENT or EACCES.
  - The refusal comes from the egress proxy (403 plus a marker header, and logged in proxy.log), paired with a positive allowlisted-host probe, so that a broken proxy fails the self-test.
  - The control op gets the socket's explicit `forbidden` reply.
  - Two more checks are also recommended: direct network (no non-lo interface, and ENETUNREACH on a raw connect) and host-env-not-inherited.
  - With these, the fix-round-1 self-test above is sufficient for S3 acceptance on bubblewrap.
