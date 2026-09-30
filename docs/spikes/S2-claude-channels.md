# Spike S2 — Claude Code "channels" (research preview)

**Task:** Task 5 of plan 1. Phase-2 gate, timeboxed to 2 hours, does not block v1.
**Question:** Can Claude Code "channels" push messages into a live interactive Claude
session (as an alternative to `tmux send-keys` for phase 2)?

**Result: UNAVAILABLE** (for this account/environment, within the timebox). The
protocol contract is fully documented and well understood; the live push test could
not be completed end-to-end because of an org-policy gate and a credential-replication
limitation in the scratch harness (both explained below). v1 is unaffected: it uses
`tmux send-keys` for the Claude reviewer regardless (§12).

## Step 1: Capability check

- `claude --help | grep -i -A3 channel` — no output. Neither `--channels` nor
  `--dangerously-load-development-channels` appears in `claude --help`. This matches
  the official docs, which state the flags are intentionally hidden while the feature
  is in research preview even though they work.
- `claude mcp --help` — standard MCP server management subcommands only
  (`add`, `list`, `get`, `login`, `logout`, `remove`, `serve`, ...). No channel-specific
  subcommand; channels are a session-launch flag (`--channels` /
  `--dangerously-load-development-channels`), not an `mcp` subcommand.
- No `claude-code-guide` agent was used per task instructions; instead, documentation
  was fetched directly:
  - <https://code.claude.com/docs/en/channels> — feature overview, setup, enterprise
    controls, security model.
  - <https://code.claude.com/docs/en/channels-reference> — the wire contract for
    building a custom channel server.

### The contract (from the docs above)

- A channel is a normal MCP server, spawned by Claude Code as a subprocess over
  **stdio**. The only hard requirement stated by Anthropic is the
  `@modelcontextprotocol/sdk` package on a Node-compatible runtime (Bun/Node/Deno);
  nothing in the wire format is actually Node-specific — it is plain JSON-RPC 2.0
  over newline-delimited stdio, so a from-scratch implementation in another language
  is possible if it correctly implements the `initialize` handshake and notification
  framing (see below).
- Server declares `capabilities.experimental["claude/channel"] = {}` in its
  `initialize` response. Presence of this key is what registers Claude Code's
  notification listener for that server.
- To push a message, the server emits an MCP **notification** (not a request) of
  method `notifications/claude/channel` with params:
  - `content: string` — becomes the body of a synthesized `<channel>` tag Claude sees
    in its context.
  - `meta: Record<string,string>` — optional; each entry becomes an XML attribute on
    the `<channel>` tag (identifier-safe keys only).
- One-way channels (alerts/webhooks) omit `capabilities.tools`. Two-way channels
  (chat bridges) also declare `tools: {}` and expose a `reply` tool Claude can call to
  send a message back out; the docs also define an optional permission-relay
  capability (`claude/channel/permission`) so a channel can forward tool-approval
  prompts to a remote device.
- Delivery is **not** mid-turn: events queued while Claude is mid-turn are delivered
  together on Claude's next turn. If the session did not load the server as a
  channel, the event is silently dropped (no error surfaces to the server).
- **Auth / access model — this is the key gate:**
  - Requires Anthropic authentication via claude.ai or a Console API key. Not
    available on Bedrock, Vertex/Google Cloud Agent Platform, or Microsoft Foundry.
  - **Pro/Max individual accounts (no org) skip all policy checks**: channels are
    available and the user opts a server in per-session with `--channels`.
  - **claude.ai Team/Enterprise orgs are blocked by default.** An org Owner must set
    `channelsEnabled: true` in managed settings (or via claude.ai admin UI) before any
    channel — including a custom development one — can deliver messages. Without it,
    the MCP server still connects and its tools still work, but channel events never
    arrive, and Claude Code prints a startup warning telling the user to ask an admin.
  - Even with `channelsEnabled: true`, **only allowlisted plugins register** during
    the research preview (Anthropic's `claude-plugins-official` marketplace, or an
    org's own `allowedChannelPlugins` override). A custom/self-written server (like
    the probe below) is never on that allowlist, so it must be run with
    `--dangerously-load-development-channels server:<name>` (or
    `plugin:<name>@<marketplace>`), which shows a full-screen "local development only"
    confirmation dialog and bypasses the allowlist check *only* — the
    `channelsEnabled` org policy still applies on top of it.
- Both `--channels` and `--dangerously-load-development-channels` are described by
  Anthropic as subject to change: "the `--channels` flag syntax and protocol contract
  may change based on feedback."

## Step 2: Minimal channel server

Since the contract is documented, a minimal channel server was built at
`spikes/s2/channel.py`. It is a **stdlib-only Python** implementation of the MCP
stdio transport (no `@modelcontextprotocol/sdk` dependency, since this plan's runtime
dependency list is empty) that:

1. Answers the client's `initialize` request with
   `capabilities.experimental["claude/channel"] = {}` (one-way channel: no `tools`
   capability).
2. Waits for `notifications/initialized`, then after a short settle delay, emits one
   `notifications/claude/channel` push with `content = "Reply only with CHANNEL-OK"`.

### Live test setup

- A scratch home/config directory was created with `mktemp -d`; `HOME` and
  `CLAUDE_CONFIG_DIR` were both pointed at it for the test subprocess, so the real
  `~/.claude`, `~/.claude.json`, and any running session were never touched.
- Before touching credentials: `expiresAt` in the real credentials file was checked
  (without printing the token). The access token had roughly 4.4 hours of remaining
  validity — comfortably outside the "do not run a live session" threshold of 2 hours
  — so the live-session step was permitted to proceed.
- sha256 and mtime of the real credentials file were recorded before and after the
  test. **They are identical before and after — the real credentials file was not
  modified.**
- The session was started in a uniquely named tmux session (`s2-spike`) as:
  `claude --mcp-config '{"mcpServers":{"s2chan":{"command":"python3","args":["<repo>/spikes/s2/channel.py"]}}}' --dangerously-load-development-channels server:s2chan`
- The tmux session was killed and the scratch directory removed at the end of the
  test, regardless of outcome.

### What actually happened

Two blockers surfaced, in order:

1. **Environment-replication limit (this attempt only):** copying
   `.credentials.json` into the scratch config directory was not sufficient for
   Claude Code to recognize an authenticated session — it presented a fresh
   "select login method" / OAuth-authorize-URL flow instead of resuming with the
   copied token. This looks like the credential is bound to additional local state
   (e.g. device/install identifiers in `settings.json` or an OS keyring reference)
   that a same-machine directory copy did not carry over. Per the safety rules for
   this spike, no real OAuth login was completed in the scratch environment (that
   would create a new grant, which is out of scope for a 2-hour research spike), so
   the live push itself was never exercised end-to-end.
2. **Org-policy gate (independent of the above, and would apply regardless):** this
   machine's Claude Code account is a managed **Enterprise** seat
   (`organizationRole: managed`, `seatTier: enterprise_usage_based`), not an
   individual Pro/Max account. No local managed-settings document with
   `channelsEnabled: true` was found on this host. Per the documented behavior, that
   means channels — including a `--dangerously-load-development-channels` custom
   server — would report "blocked by org policy" and never deliver messages, even if
   authentication had succeeded, unless an org Owner explicitly enables
   `channelsEnabled` first.

Given (1) and (2) together, **Step 2 is recorded as attempted-but-not-completed**:
the probe server and harness are in place and match the documented contract, but no
`CHANNEL-OK` was observed in a live session within the timebox. This is an
environment/policy limitation, not a refutation of the documented mechanism.

## Step 3: Findings and consequences for phase 2

- **Result: UNAVAILABLE** for this environment today, primarily because of org
  policy (`channelsEnabled` unset for a managed Enterprise seat), secondarily because
  of a scratch-credential replication gap encountered during this spike.
- **The mechanism itself is real, documented, and coherent**: channels are a
  legitimate one-way (or two-way) MCP-based push path into a live session, distinct
  from a normal MCP server (which is pull-only). For an individual Pro/Max
  subscription with no org, channels are reportedly available with no admin
  involvement at all, which is directly relevant to "does it work under subscription
  (non-API-key) auth?" — yes, for accounts outside a Team/Enterprise org; for
  accounts inside one (like this host), an Owner must opt in first.
- **Consequences for phase 2, if channels are revisited:**
  - Any dependency on channels requires either (a) an org Owner to set
    `channelsEnabled: true`, or (b) running under an individual Pro/Max/Console
    account outside org policy — this is an operational/organizational prerequisite,
    not a code problem.
  - A custom (non-Anthropic-marketplace) channel server will always need
    `--dangerously-load-development-channels`, an explicitly unstable, hidden,
    confirmation-gated flag, for as long as the feature stays in research preview.
    That is not a good fit for an unattended harness process.
  - Delivery is queued/next-turn, not mid-turn-interrupt, and silently dropped if the
    session didn't opt the server in — a harness relying on channels would need its
    own delivery confirmation (e.g. a reply tool) since Claude Code gives no
    server-side ack.
  - Given the "research preview," "flag syntax may change," and org-policy gating,
    channels are **not** a safe replacement for `tmux send-keys` in phase 2 at this
    time. v1's choice to always use `tmux send-keys` for the Claude reviewer (§12) is
    reaffirmed by this spike; channels can be revisited later if/when they graduate
    out of research preview and this environment's org enables them.

## Artifacts

- `spikes/s2/channel.py` — stdlib-only Python probe implementing the documented
  channel contract (kept for future re-testing once `channelsEnabled` is available).

## Sources

- <https://code.claude.com/docs/en/channels>
- <https://code.claude.com/docs/en/channels-reference>
