# ACP adapter: design note (AU-16)

Status: **design note for a P2 design round. Nothing here is decided or implemented.** Any implementation needs its own ADR amendment, cross-model review and operator approval (accounts plan, AU-16 acceptance). Bead btq-0vlc3.

Base: main 676029f (ADR 0001 revision 14). Sources:
- the accounts plan, §AU-16 (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195);
- ADR 0001 r14: §2, §3.3, §4.1 to §4.4 (D1 to D11), §5.3, §7, §10, §12;
- spike S7 (`docs/spikes/s7-accounts-and-usage.md`) for what "login file set" and "demonstrated" mean.

ACP versions checked on 2026-10-09:

| Artefact | Version | Where |
|---|---|---|
| Protocol version (the `protocolVersion` integer) | 1 | `schema/v1/meta.json` |
| Stable schema | `schema-v1.25.0` (2026-10-09) | github.com/agentclientprotocol/agent-client-protocol releases |
| Next major, pre-release | `schema-v2.0.0-alpha.8` (2026-10-09) | same |
| Rust crate | 1.11.0 | same |
| TypeScript SDK | `@agentclientprotocol/sdk` 1.8.0 | npm |
| Python SDK | `agent-client-protocol` 0.12.1 | PyPI |
| Claude adapter | `@agentclientprotocol/claude-agent-acp` 0.89.0, on `@anthropic-ai/claude-agent-sdk` 0.3.293 | npm |
| Codex adapter | `@agentclientprotocol/codex-acp` 2.2.1, "built on the Codex App Server" (the older `@zed-industries/codex-acp` 0.16.0 is superseded) | npm, GitHub README |
| Gemini CLI (native ACP, `--acp`) | `@google/gemini-cli` 0.63.0 (0.29.5 installed on the reference host) | npm, geminicli.com docs |
| OpenCode (native ACP, `opencode acp`) | `opencode-ai` 1.18.35 | npm |

Everything below about message shapes is from the v1.25.0 schema unless it says v2. ACP moves fast (two schema releases in ten days), so a P2 round must re-check against whatever version it pins.

## 1. Summary

- **What ACP is.** JSON-RPC 2.0 over the agent process's stdio. The *client* (here: the harness) starts the agent, calls `initialize`, `session/new` or `session/load`, and sends `session/prompt`. The agent streams `session/update` notifications (message chunks, `tool_call`, `tool_call_update`, `plan`, `usage_update`, …), and calls back into the client with `session/request_permission` and, only if the client advertises them, `fs/*` and `terminal/*`.
- **Recommendation.** An ACP adapter is worth having **only for harnesses that have no interactive-CLI adapter**: Gemini CLI and OpenCode are the realistic first candidates. For Claude Code and Codex it adds nothing that justifies losing §2's properties, and for Claude it would lose subscription login (§4.1).
- **Policy.** `session/request_permission` is a better *policy* point than `PreToolUse`: it is in-protocol, every gated tool waits for the answer, and it is one protocol for every harness (though each harness needs its own input normaliser, §3.1). It is **not a security boundary**, because the agent decides which calls it asks about. The §7 outer sandbox stays the only boundary, unchanged.
- **Headless.** An ACP session is headless, so it is an exception to §2 point 2, of the same kind as `codex exec` in §5.3: allowed only inside the outer sandbox. Section 3 says what replaces `tmux attach` and each hook.
- **Accounts.** D1 applies only where a harness keeps its login in files under a directory that an environment variable can point at, and only after an S7-style spike shows the login file set. API-key credentials are not a D1 account and would need their own amendment.

## 2. Where it would sit

### 2.1 Adapter type

§4.1 says the code knows one adapter type per harness CLI. An ACP adapter breaks that one-to-one: one protocol, several harnesses. Two shapes:

- **(a) One `acp` adapter type, with a closed registry of harness descriptors in code** (command line, config-directory variable, login file set, egress hosts, the mode to start in, the permission-input normaliser of §3.1, which capabilities S7-style runs demonstrated). A profile names `adapter = "acp"` plus `harness = "<name>"`. Adding a harness is a new descriptor and a spike, not a new adapter. This fits AU-14's driver-registry review.
- **(b) One adapter type per harness** (`gemini-acp`, `opencode-acp`), sharing an ACP client library. This keeps §4.1's wording, and the `unavailable` status (AU-14) is per type.

Lean: (a). The descriptor is exactly the per-harness data D7, D9 and §7 already need, and §4.1's lint (no model names in source) still holds, because a descriptor names a harness, not a model. Either way, a harness that isn't in the registry can't be configured.

### 2.2 Process layout: an in-sandbox bridge

The ACP client has to live somewhere, and the agent's stdio is the only channel. Three places:

| Option | wsd restart | Untrusted JSON parsed on the host | Verdict |
|---|---|---|---|
| ACP client inside `wsd`, agent's stdio piped across the sandbox boundary | Kills in-flight turns (the agent's stdin closes). This is one of the reasons §2 rejected Paseo. | All of it, in the control plane | No |
| A per-session client process on the host, in the bead's tmux session | Survives | All of it, in a host process with no sandbox | No |
| **A per-session bridge (`ws-acp`) inside the sandbox**, started in the bead's tmux session as the sandbox's main process, with the agent as its child | Survives: the bridge and agent keep running, and `wsd` reconnects | None beyond what the session socket already accepts | **Lean** |

The bridge is the ACP client. On one side it speaks ACP to the agent over stdio; on the other it uses the **existing per-session socket** (§7), with the per-launch token, to send hook-equivalent events and permission requests to `wsd`. For the `wsd` → session direction (prompts, cancel), it binds a socket in the session's bridge directory, as the Codex app-server's queue socket does (§4.2, §7). `wsd` connects to it from the host; nothing else is reachable.

The bridge is in the same sandbox as the agent, so a compromised agent can compromise it. That changes nothing: today's hook shim is in the same position, and its events are already untrusted (§3.3). The bridge holds no credential and no control operation, and the session socket still refuses control operations.

### 2.3 What the bridge advertises

`initialize` from the bridge advertises **no** client capabilities: no `fs`, no `terminal`, no `elicitation`, no `auth` extensions, and no MCP-over-ACP. It answers every `_`-prefixed extension method with "method not found".
- With `fs` or `terminal` off, the agent uses its own built-in tools for files and commands, inside the sandbox, as today.
- Turning `terminal` on later would make every command the agent runs through ACP pass through the bridge, which is a real choke point for those commands. But an agent can still use its own tools, the bridge would become an executor, and it is a larger trusted surface. That is for a later round, after the first version is in use.
- `session/new` passes `mcpServers = []`, except the harness's own steering or channel server, if a round adds one. Any MCP server passed would run inside the sandbox.

## 3. Permission requests and the §5.3 policy engine

### 3.1 The mapping

`session/request_permission` carries `sessionId`, a `toolCall` and `options`. In schema-v1.25.0 the `toolCall` is a **partial** `ToolCallUpdate`: only `toolCallId` is required, and `title`, `kind` (∈ {read, edit, delete, move, search, execute, think, fetch, switch_mode, other}), `rawInput` and `locations` are all optional. `rawInput` is any JSON value, with no common schema across harnesses. Each option has an `optionId`, a `name` and a `kind` ∈ {`allow_once`, `allow_always`, `reject_once`, `reject_always`}. ACP doesn't require any particular kind to be offered. The response is `selected` (with one of the offered `optionId`s) or `cancelled`.

So a request can't go to the policy engine as it arrives. The bridge builds the input first:

1. **Tool-call state.** The bridge keeps one record per (session, `toolCallId`). It starts with the `tool_call` notification and merges every later `tool_call_update`, then the `toolCall` in the permission request, field by field: a field that is present replaces the earlier value, and a field that is absent keeps it. The permission request is evaluated against the merged record.
2. **Per-harness normalisation.** The harness descriptor (§2.1) includes a normaliser: a pure function from the merged record to the policy engine's **existing** input (tool, input and working directory, the shape the `PreToolUse` hook sends in §5.3 step 1). For example, it says which `rawInput` field holds a shell command, or which holds a file path. That is what lets the existing tiers match, and lets `wsd` turn, say, a `git push origin main` into the typed `push_branch` request of §5.3 step 3. Each normaliser is pinned to the harness version, and golden tests built from recorded requests check it.
3. **Unclassifiable input escalates.** A record that the normaliser can't map completely is never auto-approved and never sent to the gatekeeper as if it were complete. It escalates as operator-only, and the card says the input couldn't be classified. Examples: no `kind`, an unknown `kind`, an `execute` with no command, an edit with no path, or a `rawInput` that doesn't match the normaliser's schema. The same holds for a request whose `toolCallId` has no earlier record and no usable fields of its own.

The bridge sends the normalised input, plus the raw merged record for the audit trail, to `wsd` on the session socket as a `permission` event. The policy engine runs the tier file unchanged and returns allow or deny. The bridge answers according to which option kinds the request offers:

| Decision | `allow_once` offered | `reject_once` offered | Neither offered |
|---|---|---|---|
| **Allow** (auto-approve, or the gatekeeper allows) | `selected` the `allow_once` option | Treated as a deny (next rows), recorded as "no one-time allow offered" | Same as the previous column |
| **Deny** (escalate, hard deny, unclassifiable, or `wsd` unreachable) | No reject option: cancel (below) | `selected` the `reject_once` option | Cancel (below) |

**Cancel** means *cancellation requested*: the bridge sends `session/cancel` (once per turn), and answers this request and every other pending permission request of the session with `cancelled`. Until the turn completes, it answers any further permission request with `cancelled` at once. It never selects an option the request didn't offer, and never invents an `optionId`. A request with an empty `options` list is a protocol error: the bridge cancels the same way, and `wsd` stops the session.

**Cancellation requested is not turn completed.** `session/cancel` is a notification: the agent may go on sending updates and running operations until it answers the original `session/prompt`. So the bridge tracks three states per turn, and the steps below wait for the one they need:

1. **Cancel requested:** `session/cancel` sent, and pending permissions answered `cancelled`.
2. **Turn completed:** the original `session/prompt` has returned (with `stopReason` `cancelled`, or any other). Only then may the bridge send another `session/prompt`.
3. **Quiescent:** the turn has completed and the agent process tree has been stopped, with its exit confirmed. Only then may `wsd` commit WIP. Turn completion alone isn't enough, because a tool can leave a child process that still writes to the worktree; and no tool-call record still `pending` or `in_progress` can be trusted as proof either, because those updates are the agent's own.

**Bounded wait.** If the turn hasn't completed within `acp.cancel_timeout` (a fixed default, for example 30s, set in host config) of the cancel request, the runtime terminates the sandbox's process tree: first a polite signal, then a kill after a short grace. `wsd` then confirms the exit from the runtime's own process state (the launch receipt's pane PID and the sandbox runtime's report), not from anything the bridge or agent says. If the exit can't be confirmed, the bead is held, escalated `unexpected_state`, and no WIP is committed (§4.3).

What happens after a deny:

| Reason for the deny | Then |
|---|---|
| Operator-only (escalate and park), including unclassifiable input | If the turn isn't already cancel-requested, the bridge requests cancellation. `wsd` can record the escalation (approval bead, §5.9 gate, cards; §5.3 step 3) at once, because that touches no worktree. The §4.3 park sequence waits for **quiescence**: turn completed (or the bounded wait expired and the process tree was terminated), then the session stopped with its exit confirmed. Only then does `wsd` commit WIP, as its own park step, so it doesn't depend on the agent obeying a message. |
| Hard deny | `wsd` waits for **turn completed** (after a `reject_once`, the turn usually goes on; after a cancel, the original prompt still has to return), then sends a fixed `session/prompt` with the reason. Nothing is escalated. If the bounded wait expires instead, the process tree is terminated and the session is handled as an agent crash (§10): reconcile resumes it, and the reason is the first prompt after resume. |
| Grey zone | The request stays open while the gatekeeper decides (60s timebox). Its answer is then an allow or one of the deny rows above; timeout or unavailable escalates, as today. |
| `wsd` unreachable | The request stays open for the §10 hook-shim wait (5s), then is denied as above. This fails closed, like the hook shim. The bridge may allow §10's narrow local-only class itself, from its cached copy of the auto-approve tier, as the shim does today, but only for a normalised record (step 3 above applies first). |

Rules:
- **Never `allow_always` or `reject_always`.** A remembered choice lives in the agent's own state, so later calls would skip `wsd`. When those are the only kinds offered, the decision table above applies: an allow becomes a deny, and a deny is a cancel.
- **There is no reason field.** Neither v1.25.0 nor v2.0.0-alpha.8 has a way to return a reason with a rejection: the outcome is only `selected` or `cancelled` (v2 adds an open `other`), and `_meta` must not be interpreted by the agent. So "deny with a reason" is a rejection or cancel followed by a `session/prompt` that carries the reason, and the park message of §5.3 step 3 ("Parked pending operator approval…") becomes that prompt, where one is sent at all.
- **Cancellation.** After any `session/cancel`, whoever triggered it, the client must answer every pending request with `cancelled`. The bridge does this as soon as it sends the cancel, reports *cancel requested* and *turn completed* to `wsd` as separate events, and drops the session's tool-call records only after the turn has completed.
- `ws-request` is unchanged: the agent runs it through its own shell tool. That raises a permission request, which the normaliser maps to a command and the tier file auto-approves.

### 3.2 Why it is stronger than `PreToolUse`, and where it is not

Stronger:
- **It blocks by construction.** The agent can't run a gated tool until the client answers. If the bridge dies, the agent's stdio closes and the call never runs. A hook that crashes or times out depends on how each CLI treats hook failure.
- **One protocol for every harness.** There is no per-CLI hook configuration or hook-trust step, which are the open questions for Codex in §4.2. The input is still harness-specific (§3.1): each harness needs its own normaliser.
- **It covers harnesses with no hook system at all.**

Not stronger:
- **The agent decides what it asks about.** Which calls raise a request depends on the harness and on the mode it runs in. In a full-access mode (codex-acp's `agent-full-access`, Gemini's auto-approve mode set through `session/set_mode`) nothing asks. So an ACP session must run in the harness's *asking* mode, the opposite of §5.3's "fully permissive inside the sandbox". The descriptor (§2.1) names that mode, and the bridge checks `currentModeId` after `session/new` and on every `current_mode_update`. A change to a non-asking mode that `wsd` didn't make stops the session.
- **`rawInput` is supplied by the agent**, so it is untrusted, exactly like hook input today.
- **Coverage is per harness.** A harness may run reads, searches or its own sub-agents without asking. The auto-approve tier covers most of that anyway, but the P2 spike has to list, for each harness and mode, which tool kinds raise a request.

So it is a better *policy and UX* layer, and the sandbox stays the security boundary. §5.3's statement that hook matching "is not a security control" applies here word for word.

## 4. Headless operation, and the exception to §2

§2 point 2 requires vanilla interactive CLIs, with agent state from hooks and `tmux attach` available. An ACP agent has no terminal UI, and its state arrives as protocol messages. It would be an **exception of the `codex exec` kind** (§5.3): allowed only when it runs inside the outer sandbox with the sandbox as its enforcement, and stated in the amendment as a named exception, not as a change to §2.

What replaces each interactive feature:

| Today (interactive CLI) | ACP replacement | Trust |
|---|---|---|
| `tmux attach`, break-glass (§4.2) | The bead still has its tmux session; its pane runs the bridge, which renders the `session/update` stream as a read-only transcript. Attaching shows what the agent is doing. **There is no takeover**: the pane takes no input. The operator's options are steering, `/stop`, or relaunching on another profile. Under r14 no procedure may need attach anyway (§4.2, §8.1), so losing takeover removes only the last resort. | n/a |
| `SessionStart` hook | The `session/new` or `session/load` response, with the agent's `sessionId` | Untrusted |
| `UserPromptSubmit` hook | Not needed: `wsd`, through the bridge, sends every `session/prompt`, so it knows each prompt first-hand. | Trusted (its own) |
| `PreToolUse` hook | `session/request_permission` for gated tools; `tool_call` with status `pending` for the rest | Untrusted |
| `PostToolUse` hook | `tool_call_update` with `completed` or `failed` | Untrusted |
| `Stop` hook | The `session/prompt` response's `stopReason` (`end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`) | Untrusted |
| `Notification` (waiting for input) | Implicit: between turns, the agent is waiting by definition. | n/a |
| Steering: Claude `send-keys` or channels, Codex `codex queue` | A `session/prompt`, held by the bridge until the current turn completes. An urgent steer requests cancellation, then sends the prompt once the original prompt has returned (§3.1, bounded wait included). v1.25.0 has no stable way to inject into a running turn. | n/a |
| `/stop` interrupt (§6.3) | Cancellation requested, then the park sequence once the session is quiescent (§3.1) | n/a |
| Resume as the same session (§4.1) | `session/load` (if `loadSession`), or `session/resume` (if `sessionCapabilities.resume`), with the recorded `sessionId`. Without either, the §4.1 deterministic handoff into a fresh session. | n/a |
| Fixed session ID (Claude `--session-id`) | None: the agent assigns `sessionId`. Recorded on the bead right after launch, as for Codex thread IDs (§4.2). | Untrusted, as Codex's |
| Launch receipt (§4.4 D2) | Unchanged: the runtime's spawn result for the tmux session and sandbox, journaled by `wsd` on the host | Trusted |

Everything the bridge reports comes from inside the sandbox, so it is treated exactly as hook-spool events are today (§3.3): observations for state and audit, never approvals or evidence, and never a launch outcome.

## 5. Harnesses, auth and accounts

### 5.1 Candidates

| Harness | ACP entry | Auth methods | Subscription login through ACP | Fit |
|---|---|---|---|---|
| Claude Code | `claude-agent-acp` adapter, built on the Claude Agent SDK | API key; whatever the SDK's bundled CLI finds | **No.** The Agent SDK overview says: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK." This is the §2 reason for rejecting Paseo. | Don't add. The `claude-code` adapter stays. |
| Codex | `codex-acp` 2.x, which starts the Codex App Server and translates | ChatGPT login (browser; `NO_BROWSER=1` disables it), `CODEX_API_KEY`, `OPENAI_API_KEY` | Technically yes, through the App Server's login. Whether a third-party ACP adapter may use it is a G3 question (the operator reads the provider's terms). | Low value: the `codex` adapter already runs a per-session app-server (§4.2), and AU-15 records its protocol. ACP would add a translation layer and remove nothing. |
| Gemini CLI | Native, `gemini --acp` | Google login (OAuth), Gemini API key, Vertex AI (from Gemini CLI's own docs; what it advertises in `authMethods` under `--acp` needs checking) | Google login is a personal or subscription login; G3 applies. | **First candidate.** No interactive-CLI adapter exists, and ACP is a first-class mode. |
| OpenCode | Native, `opencode acp` | Its own `auth login` per provider, plus provider API keys | Depends on the provider behind it; G3 per provider. | Candidate. One harness, many providers, which complicates D1 (§5.2). |
| Others in the ACP agent list (Goose, Qwen Code, Kimi CLI, Mistral Vibe, Junie, Cursor, Copilot, Factory Droid, …) | Native | Various | Unknown | Not evaluated. Each would be a descriptor plus a spike. |

The terms rule is G3 (§4.4): the operator reads each provider's terms before anything depends on a subscription login reached through a third-party client.

### 5.2 How the accounts decisions apply

- **D1 (accounts are host configuration).** An account is `[accounts.<name>]` with `adapter` (and, under shape (a), `harness`) and `login_dir`. The credential identity is the harness's login file set, by canonical path. That needs, per harness, (1) an environment variable that moves the config directory, and (2) an S7-style spike that names the login file set. Gemini CLI and OpenCode have neither established yet: where each keeps its login, and whether one variable moves all of it, is the first thing the P2 spike must show. Until then `config check` rejects `accounts` on that harness, as D7 already does for an adapter S7 hasn't covered.
- **API keys are not a D1 account.** D1, D7 and D8 are written for file-bound logins with a refresh token (§7). An API key is a static secret, has no expiry to gate, and would have to reach the sandbox through the environment, which §7's environment allowlist forbids. Supporting API keys (for Claude through ACP, or for any provider) needs its own amendment, probably together with §17 item 5 (credential injection at the proxy).
- **OpenCode's providers.** One OpenCode login directory can hold several providers' credentials. A D1 account would then be "this login directory", and the headroom gate would have no idea which provider a session is spending. A round has to choose between restricting an OpenCode account to one provider (checked at launch) and leaving accounts off OpenCode.
- **D2 (launch entries).** Unchanged. The native ID is the ACP `sessionId`, recorded once after `session/new`. A handoff relaunch is a fresh `session/new`; there is no ID the client can choose.
- **D3 and D9 (usage).** ACP's `usage_update` reports the **context window** (`used`, `size`, optional `cost`), not subscription windows, so it is not a D9(c) usage source. A harness-specific extension (`_meta`, or a `notice`) might be one, but it would be untrusted and per launch (§7). No ACP harness has a known D9(d) trusted host-side read, so there is no shared exhaustion, no failover and no quota-based reviewer fallback for ACP harnesses until a spike shows one.
- **D5 (limit reached).** No `stopReason` means "quota". A limit would have to come from a harness-specific signal, demonstrated per D9(e); without one, the session's end goes to the existing §10 rows.
- **D7 (binding through the synthetic home).** The descriptor's config-directory variable points inside the synthetic home, and only the chosen account's login files are bound read-only, as for Claude and Codex. The Other accounts self-test probe applies per harness.
- **D8 (freshness gate).** The gate must read the token expiry from the harness's login files. A harness whose expiry can't be read on the host can't pass the gate, so it can't launch. That puts a hard requirement on the spike.
- **D11 (`admind`).** Unaffected. `admind` runs the interactive CLIs and isn't sandboxed; an ACP harness isn't offered as an `admind` profile.

## 6. The outer sandbox (§7) is unchanged

- The agent and the bridge both run inside the sandbox, under the same compiled `sandbox.toml` policy, synthetic home, read-only login files, environment allowlist and egress proxy.
- Per harness, the descriptor adds exact egress hosts (model endpoints and the harness's own auth host), with no suffix wildcards. Each harness's connectors or account-linked MCP features are turned off in its config, as §7 does for Claude and Codex.
- The only host sockets reachable stay the session socket and the session's bridge directory.
- The launch self-test runs against the ACP launch shape (bridge plus agent), not a bare agent process, and every probe applies, the Other accounts probe included.
- Because the bridge advertises no `fs` or `terminal` capability (§2.3), no file or command operation crosses the sandbox boundary through ACP.

## 7. For the P2 round

**Decisions to take:**
1. Adapter shape: one `acp` type with a harness registry, or one type per harness (§2.1).
2. Bridge inside the sandbox, as proposed (§2.2), or another placement.
3. Wording of the §2 exception, and confirming that losing `tmux attach` takeover is acceptable for ACP sessions (§4).
4. Which harness goes first (lean: Gemini CLI) and whether OpenCode accounts are single-provider or off (§5.2).
5. Whether API-key credentials are in scope at all; if they are, they need a separate amendment (§5.2).

**A spike (S-ACP) before any implementation**, on the reference host, for each harness taken forward:
- `initialize` and `session/new` through a bridge inside the §7 sandbox, with the agent pinned to a version, and the protocol version recorded;
- which tool kinds raise `session/request_permission` in the asking mode; which `toolCall` fields and option kinds each request actually carries; that `reject_once`, where offered, and `session/cancel` each stop the call; and how long each harness takes from `session/cancel` to the prompt's return, to set `acp.cancel_timeout`;
- recorded permission requests for the normaliser's golden tests (§3.1), including the shapes of shell commands, file edits and `git push`;
- that a `wsd` restart doesn't end the turn, and that `wsd` reconnects to the bridge;
- `session/load` or `session/resume` after the agent process restarts;
- the login file set, the config-directory variable, token expiry readable on the host, and a working login with read-only login files (the D7 and D8 preconditions);
- any usage or limit signal, as D9 (c) to (e) candidates.

**Out of scope for this note:** the Paseo adapter (§12), concurrent sessions per role, and any change to Claude Code or Codex sessions, which stay on their interactive adapters.
