# Spike S7: accounts and usage

Status: **client side done; real-account runs still open (needs Liam).** No capability is demonstrated on a real account yet, so under §4.4 D9 every capability stays disabled for both adapters. The runs did establish where each signal lives, its shape and what triggers it. Each "needs Liam" item below gives the exact commands for the real-account run. Bead btq-dx6qa (change plan AU-1; the plan names this file `S7-accounts-usage.md`). This is gate G2.

CLIs tested: Claude Code 2.1.286 and codex-cli 0.160.0, as installed. The ADR §4.2 table names 2.1.283 and 0.157.0, and neither CLI was upgraded. The real-account runs must record the version they ran on, because D9 is per pinned version.

Method (scripts in `spikes/s7/`; `$S7` is a scratch directory, `/tmp/s7` by default):
- Every run used a fresh throwaway `HOME`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` under `$S7`.
- Every run was in its own network namespace with only loopback, so no request could leave the host. Each `run-*.sh` sources `isolate.sh` first. It re-execs the script under `bwrap --unshare-net`, then refuses to go on (rc 2) if `/proc/net/dev` lists any interface but `lo`. So a script run by hand fails closed instead of reaching a real endpoint.
- Every CLI ran under `env -i` with an explicit variable list (`PATH`, `HOME`, `CLAUDE_CONFIG_DIR` or `CODEX_HOME`, `TERM`, plus the scenario's dummy token and stub URL), so no inherited token or endpoint variable reached it.
- Claude ran against `stub.py`, a local Anthropic API stand-in, through `ANTHROPIC_BASE_URL`. Codex ran against `cxstub.py`, a ChatGPT-backend stand-in, through `chatgpt_base_url` in `config.toml`.
- Only dummy tokens were used (`dummy-login-A`, unsigned fake JWTs from `fakeauth.py`). The stubs log a sha256 prefix of each `authorization` header, never the header itself.
- No real login, no live account, no other agent's home and no provider API was touched. Each run's `tmux` server and stub were killed by PID.

Reading the result column:
- **demonstrated:** shown on a real account. Nothing in this spike reaches that level.
- **client side shown:** the CLI behaves as described against the stub with dummy credentials. What the real server does is still open.
- **not demonstrated:** no structured source exists, or the run showed it doesn't work.
- **unknown:** not tested.

Under D9 only **demonstrated** enables a capability. A client-side result is the precondition for a real run, not a substitute for it.

| Scenario | Script | What it does |
|---|---|---|
| A | `run-a.sh` | `claude -p`, dummy API key, stub answers 429 with `anthropic-ratelimit-unified-*` headers; hooks `StopFailure`, `Stop` and `SessionStart` dump their payloads. |
| B | `AUTH=oauth MODE=429\|ok run-b.sh` | Interactive `claude` in a private `tmux` server, dummy `CLAUDE_CODE_OAUTH_TOKEN`, a status-line command that dumps its JSON, and the same hooks. |
| C | `run-c.sh` | One config dir: `--session-id G1` under dummy login A, `--resume G1` under dummy login B, then `--session-id G2` under B. |
| D | `run-d.sh` | Login binding: a dummy `.credentials.json` per account bound read-only into each config dir. Accounts A and B, plus A2 (access token already expired). Records each file's hash and mtime before and after. |
| E | (inline) | `codex app-server` in a fresh `CODEX_HOME` with no login: `account/rateLimits/read`. |
| F | `run-f.sh`, `EXPIRES_IN=-3600 run-f.sh` | `codex app-server` with a dummy ChatGPT `auth.json`: `account/rateLimits/read` against the stub. The second run uses an expired access token. |
| G | `run-g.sh` | `codex exec` with a dummy ChatGPT login and the stub answering 429 `usage_limit_reached`. |

## Result summary: Claude Code 2.1.286

| S7 question / D9 capability | Result | Evidence (trimmed) | Consequence |
|---|---|---|---|
| **(1) / (a) login binding.** Login file set with `CLAUDE_CONFIG_DIR` in the synthetic home, and a working login. | **Client side shown.** Real login: **needs Liam** (L1, L2). | The login file set is one file, `$CLAUDE_CONFIG_DIR/.credentials.json` (plaintext on Linux; the binary builds the path as `join(configDir, ".credentials.json")`). Scenario D: with a dummy file ro-bound into each of two config dirs, `claude auth status --json` gives `{"loggedIn": true, "authMethod": "claude.ai", "configDirectory": "$S7/runD/A/claude", "subscriptionType": "max"}`. The stub got each account's own bearer (digests `5864f7d6` for A, `a0400788` for B, never crossed). Both files kept their sha256 and mtime. Claude writes only `.claude.json`, `projects/` and `sessions/` in the config dir (plus `backups/` and `session-env/` in scenario A). Nothing is written under `HOME` that matters for login. Required scopes: `user:inference`, `user:profile`. A real `~/.claude` also holds `.credentials.lock` (names listed only), the CLI's own refresh lock. | D7's Claude binding works as designed on the client side. The login file set is `{.credentials.json}`, and `.credentials.lock` must stay writable or outside the bind (see (6)). An env-token login (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`) also works, but it is not a login file and D7 does not cover it (§17 #5). |
| **(2) / (b) cross-account resume.** | **Client side shown.** Real accounts: **needs Liam** (L3). | Scenario C: under login B, `--resume G1` sent the earlier turn (the request body contained the marker word from login A's turn), kept `session_id` G1, `is_error: false`. Bearer digests: A `7d0b0c01`, then B `6aa3cf3d`. The transcript stayed `projects/<cwd>/G1.jsonl` in the same config dir. | The CLI doesn't tie a transcript to the account that wrote it. Whether the server accepts another account's session state (prompt cache, server-side tool state) is unknown until L3. |
| **(3) / (c) in-session usage (untrusted).** | **Client side shown.** Real headers: **needs Liam** (L2). | Status-line JSON, scenario B (`ok` mode): `"rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": <epoch s>}, "seven_day": {"used_percentage": 17, ...}}`, from the `anthropic-ratelimit-unified-5h-utilization` / `-7d-utilization` and `-reset` headers. In `429` mode, `five_hour.used_percentage` is `100`. The first status-line call, before any API response, has no `rate_limits`. The `Stop` payload has no usage field (keys: `cwd`, `hook_event_name`, `last_assistant_message`, `permission_mode`, `prompt_id`, `session_id`, `stop_hook_active`, `transcript_path`, and so on), and a successful transcript has no rate entry. | The status line is the only in-session source. It runs only in interactive sessions, which is the managed shape anyway. `wsd` would install a status-line command that writes to the hook spool (untrusted, §3.3). Headless `stream-json` also emits `rate_limit_event`, and a `get_usage` control request exists, but both belong to the SDK protocol §2 rules out. |
| **(4) / (d) trusted host-side read; does it refresh?** | **Not demonstrated**: no source. | No CLI subcommand prints usage. `/usage` is a TUI screen (scraping, §2). The `get_usage` control request is Agent SDK protocol, ruled out by D9 and §2. | Claude has no (d), so no shared exhaustion, no `failover = "next"` and no quota-based reviewer fallback (D9). The only route to a (d) is an operator decision to allow `get_usage`, or a later CLI version. |
| **(5) / (e) structured limit-reached signal.** | **Client side shown.** Real limit: **needs Liam** (L5, opportunistic). | The `StopFailure` hook ("Fires instead of Stop when an API error … ended the turn"; output and exit code ignored). Scenario A and B payload: `{"hook_event_name": "StopFailure", "error": "rate_limit", "session_id": …, "transcript_path": …, "prompt_id": …, "last_assistant_message": "You've hit your session limit · resets 12:10pm (America/Vancouver)"}`. The other `error` values are `overloaded`, `authentication_failed`, `billing_error`, `server_error` and others. There is no structured reset time (only the message text). **No `Stop` hook fired.** Transcript entry: `{"type": "assistant", "isApiErrorMessage": true, "error": "rate_limit", "apiErrorStatus": 429}`, model `<synthetic>`. Interactive pane: `Usage limit reached · continuing automatically at 12:10pm · esc to cancel`. | Two structured sources: the `StopFailure` hook (`error == "rate_limit"`) and the transcript entry. Both are untrusted and need a trusted read to confirm, which Claude lacks (D9). Two plan consequences: turn-end detection must accept `StopFailure` as a turn end, and **the interactive CLI resumes on its own at the reset**. So a deferred Claude session must be stopped, not left running. |
| **(6) freshness gate with two accounts.** | **Unknown.** Client side: no cross-talk. **Needs Liam** (L6). | Scenario D, A2: an expired dummy access token was sent as is (bearer digest `8eee6652`), the run exited 0 against the stub, and the ro-bound file was unchanged. The stub accepts any token, so the refresh path never ran. | Whether Claude refreshes inside a session, writes `.credentials.json` and takes `.credentials.lock`, and whether that breaks with a read-only bind, is the open D8 question. S3 recorded the same gap. |
| **(7) / (f) handoff relaunch with a generation-specific ID.** | **Client side shown.** Real accounts: **needs Liam** (L4). | Scenario C: under login B, `--session-id 33874076-5796-543e-8eae-9388f5280f5c` (generation 2; the spike derived it as `uuid5(UUID(G1), "G1:2")`, the ADR formula is `uuid5(NS, f"{session_key}:{generation}")`) created `projects/<cwd>/<G2>.jsonl` beside `G1.jsonl` in the same config dir. `session_id` G2, `is_error: false`. | `--session-id` accepts any caller-chosen UUID, so D7's native-ID rule works as designed. |

## Result summary: codex-cli 0.160.0

| S7 question / D9 capability | Result | Evidence (trimmed) | Consequence |
|---|---|---|---|
| **(1) / (a) login binding.** Login file set with `CODEX_HOME`, and a working login. | **Client side shown.** Real login: **needs Liam** (L1). | A fresh `CODEX_HOME` gives `codex login status` → `Not logged in` (rc 1). The login file is `$CODEX_HOME/auth.json` (a real `~/.codex` has it; names listed only). Scenarios F and G: with a dummy ChatGPT `auth.json` (`auth_mode: "chatgpt"`, fake JWTs), every backend request carried that file's bearer (one digest) and a `chatgpt-account-id` header. `auth.json` kept its sha256 while its access token was valid. The app-server writes `state_5`, `logs_2`, `goals_1`, `memories_1` and `queue_1` sqlite files, `installation_id`, `skills/` and `.tmp/` into `CODEX_HOME`. Codex refuses to create its helper binaries under `/tmp` (a warning only). | D7's Codex binding works on the client side: the login file set is `{auth.json}`, and everything else in `CODEX_HOME` is per-home state that the synthetic home owns. |
| **(2) / (b) cross-account resume.** | **Unknown. Needs Liam** (L3). | S1 showed `codex resume <id>` within one home. Swapping `auth.json` under a dummy login gets nowhere offline: scenario G fails at `workspace routing discovery failed` before any model request. | Not testable offline. |
| **(3) / (c) in-session usage (untrusted).** | **Client side shown** (the read). `account/rateLimits/updated` and the real backend: **needs Liam** (L2). | Scenario F, `account/rateLimits/read` → `{"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": <epoch s>}, "secondary": {"usedPercent": 17, "windowDurationMins": 10080, …}, "planType": "pro", "rateLimitReachedType": null, …}, "rateLimitsByLimitId": {…}}`. Its backend calls: `GET /backend-api/wham/usage` and `GET /backend-api/wham/rate-limit-reset-credits`. Scenario E, no login: error `-32600` "codex account authentication required to read rate limits". Schema (`codex app-server generate-json-schema`, offline): `AccountRateLimitsUpdatedNotification {rateLimits: RateLimitSnapshot}`; `RateLimitReachedType` = `rate_limit_reached`, `workspace_{owner,member}_credits_depleted`, `workspace_{owner,member}_usage_limit_reached`. | The D9 Codex candidate is real and well-shaped: percent, window and reset time. The per-session app-server can serve it (an untrusted row, §3.3). Whether `updated` fires mid-turn, and how often, is for L2. |
| **(4) / (d) trusted host-side read; does it refresh?** | **Client side shown: the read works and, with an expired token, attempts a refresh.** Whether a refresh succeeds, rotates the token or writes `auth.json`: **unknown**, needs Liam (L6). The trusted path itself: **needs Liam** (L7). | The same read from a short-lived host-side app-server works (scenario F, [`evidence/runF.txt`](../../spikes/s7/evidence/runF.txt)). With an **expired** dummy access token (`EXPIRES_IN=-3600 run-f.sh`, [`evidence/runF-exp-3600.txt`](../../spikes/s7/evidence/runF-exp-3600.txt)), the app-server logged `codex_login::auth::manager: Failed to refresh token: error sending request for url (https://auth.openai.com/oauth/token)` seven times, about every 5 s. The namespace blocked each attempt, so none of them reached the endpoint. It still sent the expired bearer to `/wham/usage` and returned the stub's numbers. `auth.json` kept its sha256, which is expected, since no refresh completed. `account/read` takes `{"refreshToken": bool}` ("When true, requests a proactive token refresh"). Starting the app-server also calls plugin and `wham/accounts/check` endpoints. | A host-side Codex read **attempts a token refresh by itself when the access token is expired**, so it must run under the D8 lock, as D8 already requires. The run sent no `account/read`, so the attempt doesn't depend on that call's `refreshToken` flag. Whether a successful refresh rotates the refresh token, writes `auth.json`, or fails on a read-only file is unknown until L6. Until then, run the freshness gate first, so the read normally sees a valid token. The read also contacts more than the usage endpoint (plugin and `wham/accounts/check` calls), which the egress policy must allow. |
| **(5) / (e) structured limit-reached signal.** | **Unknown.** Schema only. **Needs Liam** (L5, opportunistic). | No `StopFailure`-like hook: Codex hooks are `SessionStart`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `UserPromptSubmit`, `Stop`, `PreCompact`, `SessionEnd` and `Notification`. App-server: `ErrorNotification {error, threadId, turnId, willRetry}` and `TurnCompleted` carry `codexErrorInfo` with `usageLimitExceeded` / `rateLimitExceeded`. `RateLimitSnapshot.rateLimitReachedType` (see (3)). Scenario G, `codex exec --json` with the stub answering 429 `usage_limit_reached`, never reached the model request (`turn.failed: workspace routing discovery failed`). | Candidates are the app-server's `codexErrorInfo` (in-session, untrusted) and the trusted read's `rateLimitReachedType`. A hook-only design can't see a Codex limit. |
| **(6) freshness gate with two accounts.** | **Unknown. Needs Liam** (L6). | Per-home `auth.json` means two accounts never share a file. Where a completed refresh writes, if anywhere, is not observed (see (4)). | Same D8 question as Claude, and more pressing: Codex attempts a refresh by itself, without being asked (4). |
| **(7) / (f) handoff relaunch.** | **Unknown. Needs Liam** (L4). | A fresh `codex exec` in the same `CODEX_HOME` always gets a new server-assigned thread ID (S1: no launch-time ID flag). | Any fresh launch is a new native session. `wsd` records the assigned ID in the launch entry, as for every Codex launch. |

## Needs Liam

Each item needs a real interactive login, or a second real account per adapter. I didn't attempt any of them.

Every command goes through `spikes/s7/sandbox.sh`, which runs one CLI in a minimal bubblewrap sandbox:
- It exposes only `/usr`, `/etc`, the CLI's own install directory, `spikes/s7/`, one synthetic home and at most one login file (read-only unless `rw`).
- `HOME` is a tmpfs, so the real `~/.claude`, `~/.codex`, every other home and the other scratch account aren't there.
- The environment is rebuilt with `env -i` from an allowlist (`PATH`, `HOME`, `CLAUDE_CONFIG_DIR` or `CODEX_HOME`, `TERM`, `LANG`). No inherited `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_*` or `OPENAI_*` variable reaches the CLI.

I checked it offline (`NONET=1`) with dummy logins:
- `claude auth status` and `codex login status` read the bound file.
- `env` inside showed only the allowlisted names, even with those three variables set outside.
- `/home` showed only the bound paths, and the other account's directory was absent.
- A write to the bound file failed with "Read-only file system".
- A full `claude -p` against the stub, and a Codex rate-limit read against the stub, both worked inside it.

Each login made here is a dedicated scratch login. When done, run `rm -rf "$L"`, and revoke the sessions in each provider's account settings if wanted. Never paste token contents, emails or account IDs into a report. The commands print only hashes, counts and the usage fields.

Setup, once, from the repo root:

```sh
S=$PWD/spikes/s7
L=$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/s7.XXXX")    # scratch; codex warns under /tmp
"$S/sandbox.sh" claude - "$L/v" -- claude --version; "$S/sandbox.sh" codex - "$L/v" -- codex --version   # record both
A=$L/cc-a/claude/.credentials.json B=$L/cc-b/claude/.credentials.json
XA=$L/cx-a/codex/auth.json XB=$L/cx-b/codex/auth.json
snap() { for f in "$A" "$B" "$XA" "$XB"; do echo "${f#"$L"/} $(sha256sum "$f" | cut -c1-16) $(stat -c %Y "$f")"; done; }
```

**L1. Real login per account (the login file set).** Each login lands in its own login home. Nothing is bound.

```sh
"$S/sandbox.sh" claude - "$L/cc-a" -- claude auth login --claudeai   # account A: open the URL, paste the code
"$S/sandbox.sh" claude - "$L/cc-b" -- claude auth login --claudeai   # account B (a second real account)
"$S/sandbox.sh" claude - "$L/cc-a" -- claude auth status --json | jq '{loggedIn, authMethod, subscriptionType}'
ls -A "$L/cc-a/claude"                                               # expect .credentials.json (names only)
"$S/sandbox.sh" codex - "$L/cx-a" -- codex login --device-auth       # account A
"$S/sandbox.sh" codex - "$L/cx-b" -- codex login --device-auth       # account B
"$S/sandbox.sh" codex - "$L/cx-a" -- codex login status; ls -A "$L/cx-a/codex"   # expect auth.json
```

**L2. A working login through the synthetic home, and in-session usage (c).** These are isolated client tests: everything runs inside `sandbox.sh`, so nothing here is the trusted path (L7 is). `cc-s` and `cx-s` are the synthetic homes, and they see only account A's login file, read-only.

```sh
mkdir -p "$L/cc-s/claude"
printf '{"statusLine":{"type":"command","command":"%s %s"},"hooks":{"StopFailure":[{"hooks":[{"type":"command","command":"%s %s"}]}]}}\n' \
  "$S/dump.sh" "$L/cc-s/statusline.jsonl" "$S/dump.sh" "$L/cc-s/stopfailure.jsonl" > "$L/cc-s/claude/settings.json"
python3 -c 'import uuid; print(uuid.uuid4())' > "$L/g1"; snap > "$L/before.txt"
"$S/sandbox.sh" claude "$A" "$L/cc-s" -- claude --session-id "$(cat "$L/g1")"
#   in the TUI: "Remember the word FLAMINGO. Reply OK.", then /exit
snap | diff "$L/before.txt" -                                       # expect no change
jq -c '.rate_limits' "$L/cc-s/statusline.jsonl" | tail -1          # real five_hour / seven_day?
"$S/sandbox.sh" codex "$XA" "$L/cx-s" -- codex exec --json --skip-git-repo-check "Remember the word FLAMINGO. Reply OK." </dev/null \
  | tee "$L/cx-a.jsonl" | jq -r 'select(.type=="thread.started").thread_id' > "$L/t1"
"$S/sandbox.sh" codex "$XA" "$L/cx-s" -- env AS_ERR="$L/cx-s/as-err.txt" python3 "$S/appserver.py" account/rateLimits/read \
  | grep -o '"primary": {[^}]*}\|"rateLimitReachedType": [^,]*'      # (c): real usedPercent / resetsAt?
"$S/sandbox.sh" codex - "$L/cx-a" -- env AS_ERR="$L/cx-a/as-err.txt" python3 "$S/appserver.py" account/rateLimits/read \
  | grep -o '"primary": {[^}]*}'                                    # the (d) read's shape on the account's own home, still sandboxed
snap | diff "$L/before.txt" -
```

**L3. Cross-account resume (b).** The same synthetic homes, now bound to account B's login file.

```sh
"$S/sandbox.sh" claude "$B" "$L/cc-s" -- claude -p --resume "$(cat "$L/g1")" "What was the word?" --output-format json </dev/null \
  | jq '{session_id, is_error, result}'                             # expect FLAMINGO and the same session_id
"$S/sandbox.sh" codex "$XB" "$L/cx-s" -- codex exec resume --skip-git-repo-check "$(cat "$L/t1")" "What was the word?" </dev/null
```

**L4. Handoff relaunch (f), mechanics only.** Account B, the same synthetic home, a fresh native session. This doesn't test continuation from a real §4.1 deterministic handoff: that needs the handoff generator, and is deferred to the AU task that builds it (see the D9 table).

```sh
G2=$(python3 -c "import uuid,sys; print(uuid.uuid5(uuid.UUID(sys.argv[1]), sys.argv[1] + ':2'))" "$(cat "$L/g1")")
"$S/sandbox.sh" claude "$B" "$L/cc-s" -- claude -p --session-id "$G2" "Handoff: reply OK." --output-format json </dev/null \
  | jq '{session_id, is_error}'                                      # expect G2, no error
"$S/sandbox.sh" codex "$XB" "$L/cx-s" -- codex exec --json --skip-git-repo-check "Handoff: reply OK." </dev/null \
  | jq -c 'select(.type=="thread.started" or .type=="turn.completed") | {type}'
ls "$L/cc-s/claude/projects/"*/                                      # G1.jsonl and G2.jsonl side by side
```

**L5. A real limit (e). Opportunistic:** run this only when an account is already at its limit. Never burn quota to get here. Use that account's login file in place of `$A` or `$XA`.

```sh
"$S/sandbox.sh" claude "$A" "$L/cc-s" -- claude    # send one prompt, then /exit
jq -c '{hook_event_name, error}' "$L/cc-s/stopfailure.jsonl"
"$S/sandbox.sh" codex "$XA" "$L/cx-s" -- codex exec --json --skip-git-repo-check "Reply OK." </dev/null \
  | jq -c 'select(.type=="error" or .type=="turn.failed")'
```

**L6. Refresh behaviour (isolated client tests for the trusted read and the freshness gate with two accounts).** This uses only the scratch logins from L1, so a rotated refresh token costs nothing.

For Claude, `expiresAt` is a plain field, so it can be set to the past. For Codex, the access token's expiry is inside the signed JWT and can't be edited. `last_refresh` is set to an old date instead. That this triggers a refresh is an assumption: if the stderr count is 0, record "no refresh attempted".

```sh
old_cc() { python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["claudeAiOauth"]["expiresAt"]=0; json.dump(d,open(p,"w"))' "$A"; }
old_cx() { python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["last_refresh"]="2020-01-01T00:00:00Z"; json.dump(d,open(p,"w"),indent=2)' "$XA"; }
# Claude, writable (A's own login home), then read-only (bound into the synthetic home):
old_cc; snap > "$L/before.txt"
"$S/sandbox.sh" claude - "$L/cc-a" -- claude -p "Reply OK." --output-format json </dev/null | jq '{is_error}'
snap | diff "$L/before.txt" -; ls -A "$L/cc-a/claude"                # A changed? B must not; .credentials.lock?
old_cc; snap > "$L/before.txt"
"$S/sandbox.sh" claude "$A" "$L/cc-s" -- claude -p "Reply OK." --output-format json </dev/null | jq '{is_error}'
snap | diff "$L/before.txt" -
# Codex, the host-side read writable, then read-only, then account/read with refreshToken true:
old_cx; snap > "$L/before.txt"
"$S/sandbox.sh" codex - "$L/cx-a" -- env AS_ERR="$L/cx-a/l6.txt" python3 "$S/appserver.py" account/rateLimits/read | grep -c usedPercent
snap | diff "$L/before.txt" -; grep -c "refresh token" "$L/cx-a/l6.txt"
old_cx; snap > "$L/before.txt"
"$S/sandbox.sh" codex "$XA" "$L/cx-s" -- env AS_ERR="$L/cx-s/l6.txt" python3 "$S/appserver.py" account/rateLimits/read | grep -c usedPercent
snap | diff "$L/before.txt" -; grep "refresh token" "$L/cx-s/l6.txt" | sed 's/\x1b\[[0-9;]*m//g' | cut -d' ' -f3- | sort | uniq -c
snap > "$L/before.txt"
"$S/sandbox.sh" codex - "$L/cx-a" -- env AS_ERR="$L/cx-a/l6b.txt" python3 "$S/appserver.py" 'account/read={"refreshToken": true}' | grep -o '"planType": "[a-z]*"'
snap | diff "$L/before.txt" -
```

What to record for each step:
- which login files changed and which didn't (B's must never change);
- whether each read-only run failed or kept working;
- whether `.credentials.lock` appeared;
- the refresh line counts.

**L7. The trusted host-side read (d), Codex only (Claude has no source).** This is the only step that runs outside every sandbox, as D3 and D9 require. It still uses `env -i` with only the dedicated scratch login from L1, and it runs under a file lock that stands in for D8. The lock is keyed, as in D1, by a digest of the canonical login-file path. The real lock path is fixed by the AU task that implements D8.

```sh
LOCK=$L/locks/$(readlink -f "$XA" | sha256sum | cut -c1-16).lock; mkdir -p "$L/locks"
snap > "$L/before.txt"
flock -w 30 "$LOCK" env -i PATH="$(dirname "$(readlink -f "$(command -v codex)")"):/usr/bin:/bin" HOME="$L/cx-a/home" \
  CODEX_HOME="$L/cx-a/codex" AS_ERR="$L/cx-a/l7.txt" python3 "$S/appserver.py" account/rateLimits/read \
  | grep -o '"primary": {[^}]*}\|"rateLimitReachedType": [^,]*'      # real usedPercent / resetsAt?
snap | diff "$L/before.txt" -; grep -c "refresh token" "$L/cx-a/l7.txt"
```

## Recommended D9 defaults

Until the real-account runs are recorded here:

| Capability | Claude Code | Codex | Enable when |
|---|---|---|---|
| (a) login binding; `accounts` accepted by `config check` | off | off | L1 and L2 pass for that adapter. This is the first gate: no other capability matters without it. |
| (b) cross-account resume | off | off | L3 passes (the answer recalls the earlier turn). |
| (c) in-session usage (untrusted) | off (status line) | off (`account/rateLimits/read` and `updated`) | L2 shows real numbers. Good early candidates on both: a cheap source with a known shape. |
| (d) trusted host-side read | **off, no source** | off | Codex: L7 returns real numbers, from the host outside every sandbox and under the lock. L6's refresh outcome must also be recorded. Claude: only by an operator decision on `get_usage` (§2) or a new CLI version. |
| (e) limit-reached signal | off (`StopFailure` with `error == "rate_limit"`, transcript `isApiErrorMessage`) | off (app-server `codexErrorInfo`) | L5 sees the signal on a real limit. |
| (f) handoff relaunch | off | off | L4 passes **and** a continuation from a real §4.1 deterministic handoff works. L4 covers only the relaunch mechanics (a fresh native session under account B with the right ID). Testing continuation needs the handoff generator, so it is deferred to the AU task that builds it. |
| `failover` | `none` | `none` | Claude can't have `next` without (d). Codex gets `next` only once (d) and either (b) or (f) are on. |

Usage is "unknown, eligible" for both adapters until (c) or (d) is on (D9). So turning accounts on (a) alone is safe: launches pick the first permitted account and never fail over.

## ADR impact

No §4.4 decision has to change on the client-side evidence. Four findings refine the design and should go into the AU tasks. They would become ADR edits only if L1–L6 confirm them:

1. **Claude's limit ends the turn with `StopFailure`, not `Stop`** (scenario A and B). Anything that waits for `Stop` to mark a turn's end (the §10 rows, `admind`'s turn tracking in §8.1) must also accept `StopFailure`, or a limited turn looks like a hang. This touches S8 as well.
2. **Claude's interactive CLI continues by itself at the reset** ("continuing automatically at …"). The D-defer path (§4.4: defer up to the WIP commit) must stop or interrupt the session, not just leave it idle, or it resumes outside the gate.
3. **A host-side Codex read attempts a token refresh by itself** when the access token has expired (scenario F; the attempt was blocked). D8 already puts the trusted read under the lock and rereads first, which holds. The read needs egress to the auth endpoint. Whether it also needs a writable `auth.json`, and whether it rotates the refresh token, is for L6.
4. **The Claude login file set is `{.credentials.json}`, and the CLI keeps its own `.credentials.lock` beside it.** The D1 credential key (the canonical path of `.credentials.json`) is unaffected. Where the lock file goes under a read-only bind is part of L6.

Version drift: tested 2.1.286 and 0.160.0 against the ADR §4.2 table's 2.1.283 and 0.157.0. Pin whichever versions L1–L6 run on.
