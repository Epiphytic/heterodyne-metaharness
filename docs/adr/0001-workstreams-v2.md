# ADR 0001: heterodyne-metaharness (workstreams v2)

- Status: Proposed, revision 15 (draft, 2026-10-09; amends §3.2, §4.2, §5.3, §7, §11–§13 and §17, and reorganises Appendix A; see "Revision 15 changes" below). Revision 15 is the S5 amendment (bead btq-56jgz), written on revision 14 (design-repo commit `82b2e4b`). Its approval is pending: it needs its own cross-model review and operator approval (§5.9) before anything cites it. The operator approved revision 14 (btq-k942c, design-repo commit `82b2e4b`). Revision 14 (2026-10-07) amends §3.2, §3.3, §4.1–§4.3, §5.2, §5.4, §5.7, §5.9, §6.2, §6.3, §7, §8, §10–§13 and §15; adds §4.4, §8.1, §8.2, §17 and Appendix A; see "Revision 14 changes" below. The operator approved revision 13 (btq-5ky39, design-repo commit `66b3aec`). Revision 14 is G1 of the accounts change plan. Revision 13 amended §2, §3, §3.4, §6.2, §7, §8, §11 and §13 for admind's operator decisions of 2026-10-01/02 (several operators, summarized replies with `!details`, an untruncated audit). The operator approved revision 12 (btq-freh); cross-model review r17 approved it (rounds r12–r17) (`docs/reviews/`; responses in `0001-design-review-r1-response.md`).
- Review process (set by the operator, 2026-09-29; applies to every agent and harness): **every change is reviewed by a different LLM than its author whenever possible, otherwise by an adversarial fresh-context agent** (§11.1). The two-model brainstorm requirement is retired.
- Date: 2026-09-29
- Author: Claude Opus 5.5 (brainstorm with the operator)
- Replaces: `hermes-workstream-harness` and its `harness-improvements` and `harness-dev` clones

### Revision 15 changes

Revision 15 (bead btq-56jgz) folds in the findings of spike S5 (`docs/spikes/s5-openshell.md` in the product repository, "ADR impact" items 1–11, after its reviews r1–r3). S5 passed items 1, 3, 4 and 5, and item 2 except for one deviation, which this revision resolves. So once the operator approves this revision, OpenShell is the v1 runtime on Linux. Text it adds or changes is tagged "(revision 15)".

1. **The direct-network proof** (§7). Under OpenShell a connection to a literal address never fails as unreachable, because OpenShell's seccomp network broker answers before the kernel routes. The probe's proof under OpenShell becomes the broker's specific answers, with seccomp evidence inside the sandbox and the outer network fence checked from the host. Bubblewrap keeps "unreachable". Approving this revision accepts the deviation S5 recorded.
2. **Egress refusal evidence under OpenShell** (§7). It is the policy-DNS synthetic address, `EACCES` from `connect()` and the supervisor's denial line, instead of S3's CONNECT refusal marker.
3. **The environment allowlist** (§7). It includes OpenShell's fixed, secret-free injected set and, on the agent's tool path, the variables the pinned CLI version adds. The launcher checks the CLI version before every launch.
4. **Per-binary egress covers the CLI's process tree** (§7, §17). Any tool the agent runs can reach its adapter's model host. So the launch self-test runs through the agent's own tool path as well as in a fresh process.
5. **A launcher-owned result channel for the self-test, and probe protection** (§7, §11, §17). Agent-path probe results are accepted only from a peer the launcher verifies from the host. Peer identity doesn't prove the result's integrity, so protecting the probe from the agent while it runs is a mandatory plan 4 acceptance condition, with adversarial controls.
6. **Residual risks under OpenShell** (§7). The SNI risk narrows, because OpenShell's proxy binds the HTTP authority and its upstream TLS to the authorized endpoint. In exchange, its supervisor sees plaintext and is inside the trusted computing base for credentials.
7. **Codex in the managed shape** (§4.2, §5.3, §13). The app-server's real socket directory is bound into the sandbox. Interactive Codex hooks fire with the real `hooks.json` schema, but only once trusted. Codex sessions are still treated as headless until the launcher can establish that trust without an operator step (plan 4).
8. **Plan 4 notes** (§7). These cover the inode pinned by a single-file read-only bind; sandbox naming and the asynchronous delete; the log buffer, which is not an audit log; `sandbox exec` stdin; uid mapping; the gateway settings; and the turn-boundary lifetime stop.
9. **S5's status and the fallback** (§3.2, §7, §12, §13, §17). S5 is recorded as passed, on OpenShell's podman driver with a user-level podman 5 installed alongside the system podman. Bubblewrap stays the fallback, under every rule of this revision, but only if the operator explicitly rejects the direct-network proof. While this revision is pending, plan 4 waits. If plan 4 finds that OpenShell can't enforce a §7 rule, the choice goes back to the operator.

Only the Linux runtime is decided; macOS stays phase 2. Nothing else in revision 14 changes. Appendix A.2 maps each S5 finding to where it lands.

### Revision 14 changes

Revision 14 (beads btq-2tu6a and btq-xv48a) folds in five things. Text it adds or changes is tagged "(revision 14)".

1. **Accounts and usage-aware scheduling** (§4.4, with edits to §3.3, §4.1, §4.3, §5.2, §6.2, §6.3, §7, §8, §10–§13 and §15). Merged from the amendment proposal `docs/adr/proposals/0001-accounts-and-usage.md` (draft after its reviews r1–r6) and its change plan (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md`, items AU-0 to AU-17). The fixes for its review r6 are listed in Appendix A and were re-reviewed in G1 (review records r21–r24).
2. **Marmot-only operation** (§8.1, with edits to §4.2, §6.3 and §10). Questions, permission prompts and dialogs reach operators on Marmot, and pickers become a new agent-bound ask kind with a defined delivery cycle; content that can't be shown safely goes back to its originator, and the remaining host exceptions are listed; every hold states its reason; tmux is break-glass only.
3. **admind's interim Marmot relay, as built** (§8.2, §5.4, §5.9). Recorded as decisions by requirement ID: R1–R26 (2026-10-05), R27–R31 with revised R7, R8, R13 and R15 (2026-10-06), and B1–B14 (2026-10-07).
4. **Two plan 3 decisions** (operator, 2026-10-04): a `v2:held` park needs no blocking edge (§4.3), and `/pause` stops new claims only (§6.3).
5. **OpenShell as the sandbox runtime**, gated on spike S5 (§3.2, §7, §12, §13). If S5 fails, only the Linux runtime selection falls back to revision 13's bubblewrap; every other rule of this revision still applies.

Decisions this revision leaves to the operator are in §17.

## 1. Context

The current harness is about 13.5k lines of Python in about 85 modules. It runs coding agents in tmux and works out agent state by regex-scraping the terminal. Observed failures (2026-09-28/29):

- **Approvals never reach the operator.** A permission prompt goes through a scraped pane, then a manager bead, then the manager LLM. The manager must run `workstream ask enqueue` to notify anyone, and it is failing. It hit `Permission denied` (exit 126) on `workstream` and context-compression failures, so approval beads sit undispatched.
- **Delivery is fragile.**
  - Mentions come from a live `wn` lookup against a backup Marmot home.
  - Delivery is FIFO per group, so one failed message blocks every later one in that group.
  - Reactions on harness messages match a table that nothing writes to any more.
- **Messages are unreadable.** They carry 64-hex IDs, run UUIDs, CLI instructions for the manager, raw pane dumps and a `[redacted]` command. Nothing is threaded, and full hourly reminders resend everything.
- **The core is tangled.** `Supervisor.tick_run` runs about 10 unrelated jobs in sequence, and behaviour is keyed off inbox-ID prefixes. One run shows `recovery_count: 48` and a `'next_at'` crash.
- **`/workstreams` has no handler** and falls through to the LLM. The Hermes cron sweeps have been paused since 2026-09-26; they were patching the DB by hand.

About 30 queued beads each fix one feature on this foundation. None of them removes the root causes: state read from terminal text, and delivery that depends on an LLM.

## 2. Decision

Build v2 in a new repo. The old harness is deadlocked, so there is no live cutover: v2 starts with fresh workstreams fed by migrated beads (§14). v2 is:

1. **A deterministic control-plane daemon, `wsd`.** It owns intake, pickup, approvals, delivery, commands, cron and reconcile. It calls an LLM for judgement and never depends on one to move.
2. **Vanilla interactive agent CLIs** (`claude`, `codex`), one session per bead and role. Each runs in its own tmux session under a service unit that does not depend on Hermes. Agent state comes from **hooks**, never from screen scraping.
3. **Hermes as intermediary and gatekeeper**, behind a narrow request→response interface. It rewrites operator messages, judges grey-zone permissions, and answers or nudges stalled agents.
4. **Beads for task intent, decisions and audit; a `wsd` journal for operational state.** Section 3.3 says which store owns which record.
5. **Marmot as the human interface.** One group per workstream with a thread per bead, plus one control group. For internal repos, GitHub or Radicle PR/patch review is an equivalent place to approve.
6. **`admind`, an independent admin override channel.** An operator's text goes unmodified to a superuser admin agent (itself an LLM), with no beads and no gatekeeper LLM between them.

### Rejected alternatives

| Option | Why rejected |
|---|---|
| Execute the ~30 existing beads | They add features on top of pane-scraping and LLM-dependent delivery, the two root causes. |
| In-place rewrite behind flags | It means fighting the existing coupling (tick loop, prefix routing, three clones) throughout. |
| Paseo as the execution layer | Its Claude provider uses the Agent SDK. The SDK docs don't allow third-party harnesses to use claude.ai subscription login without approval, and the operator's plan needs the interactive CLI. Paseo is also pre-1.0, and a daemon restart kills in-flight turns. It can be revisited later as an `AgentRuntime` adapter. |
| Paseo instead of Hermes | No Marmot, no LLM intermediary, no beads, no approval queue. |
| Live, workstream-by-workstream cutover | This was the original plan. It became moot when the old harness deadlocked (2026-09-29). |

## 3. Architecture

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

 admind (independent service): own Marmot identity + operators' group ─► superuser agent
```

### 3.1 Components

| Unit | Responsibility | Uses an LLM? |
|---|---|---|
| `wsd` | A single asyncio daemon that owns every deterministic flow. It is the **single writer** for approvals and actions (§5.4). Its SQLite journal holds operational state (§3.3). | No |
| `wsd-act` | The effector. It runs as a separate OS user (`hermes-act`) that holds the credentials for privileged actions (push to shared branches, PRs and merges, deploy). `wsd` can invoke only `wsd-act run <approval-id>` (a sudoers rule on Linux); `wsd-act` re-reads and revalidates the approved typed action itself (§5.3). | No |
| router and commands | Classifies inbound messages as command, threaded reply, reaction or unthreaded message, and answers commands directly (§6). | No |
| approvals | The approval-bead state machine, with a map from message or forge review to bead. The first decision wins. | No |
| policy engine | Applies the versioned tier file (§5.3). | No |
| scheduler | Pickup at task boundaries, park and resume, cron, reconcile, the nudge limit. | No |
| renderer | A pure function from an event to message text, pinned by golden tests. | No |
| outbox | Per-group delivery with retry and backoff. A message that fails permanently is recorded and skipped, so it never blocks the queue. Ordering is kept within each thread. | No |
| forge bridge | Links approval beads to PRs or patches, polls reviews, and mirrors decisions (§5.5). | No |
| `AgentRuntime` | `launch(bead, role, worktree, sandbox)`, `resume`, `steer`, `interrupt`, plus an event stream. Adapters: `claude`, `codex`. | No |
| hook shim | A tiny script that agent hooks call. It forwards the event to the `wsd` session socket (§7) and returns the decision. When `wsd` is unreachable it fails closed, except for a narrow local-only class (§10). | No |
| hermes-channel | An MCP channel server that pushes steering into live Claude sessions (§4.2). | No |
| gatekeeper | A Hermes endpoint. Typed request→response with a timeout. The only LLM in the loop. | Yes |
| `admind` | The admin override channel (§8). | Passthrough in; summarized replies out (§8) |

**Core rule:** `wsd` calls the gatekeeper; the gatekeeper never drives `wsd`. If Hermes is down, everything deterministic keeps working: commands, delivery, operator approvals, cron, and pickup of beads that are already ready.

### 3.2 Platform seam

A host is either Linux or macOS. A workstream's platform is its host's platform, and it never changes. At install time the platform selects:

| Concern | Linux | macOS |
|---|---|---|
| Service manager | systemd user units | launchd agents |
| Sandbox backend (revisions 14–15, §7) | OpenShell (spike S5 passed; revision 15), with bubblewrap as the fallback | Seatbelt (`sandbox-exec`, generated profile), phase 2; whether OpenShell replaces it is decided with the phase 2 macOS work (§17) |
| Boot ID | `/proc/sys/kernel/random/boot_id` | `sysctl kern.boottime` |

`sys.platform` appears only in `platform.py`. The choices are saved in the host config at install time and not re-probed at runtime. Aggregating several hosts into one control group is out of scope (§12).

### 3.3 State ownership and recovery

| Record | Authoritative store | Recovery if `wsd`'s SQLite is lost |
|---|---|---|
| Task intent, dependencies, claims | Beads | n/a (already in beads) |
| Approval requests, typed action payloads, decisions, action results | Beads (approval bead metadata and comments), written by `wsd` before any side effect | Rebuilt from beads |
| Decision inbox (pending events, dedup keys) | SQLite journal. The **decision itself is on the bead** (§5.4). | Pending events are lost, and their cards are re-posted. Replayed events are checked against the bead's decision, which is idempotent. |
| Action execution state (`pending → executing → succeeded/failed/uncertain`) | Beads, updated at each transition | Rebuilt from beads. `executing` or `uncertain` goes to target-state reconciliation (§5.4). |
| Park journal (intent → WIP commit SHA → label) | SQLite journal plus a bead comment when complete | Replayed idempotently from the worktree and bead state. |
| Session registry | Derived: `uuid5(bead, role, profile)` plus the launched-session record on the bead (§4.1) | Recomputed |
| Message ↔ bead map, outbox receipts, reminder state | SQLite journal | Lost. Open approval and question cards are re-posted with a "re-issued after recovery" note, and replies to old cards get a pointer to the new one. |
| Hook spool (events observed while `wsd` was down) | Local spool files | Replayed as **untrusted observations** for the audit trail only; never treated as approvals or evidence. |
| Account usage cache (revision 14): trusted windows and exhaustion marks per credential key; untrusted windows per launch, in a separate table | SQLite journal. Advisory; only trusted observations are shared between sessions (§4.4). | Lost. Every account is unknown, which is eligible, until the next observation. |
| Launch entries (revision 14): session key, profile, generation, role, account, credential key, models, dispatch mark, native ID, outcome, adopted | SQLite journal, then the bead's `metadata.wsd_launches` before each launch | Rebuilt from the bead. |
| Launch receipts (revision 14): session key, generation, the runtime's own spawn result (started or refused) | SQLite journal only, written by `wsd` on the host (§4.4 D2) | Lost. An entry with a dispatch mark and no outcome then has no receipt, so it holds for operator recovery; it is never inferred from spool events or transcripts. |
| Deferral records (revision 14): session key, deferral number, role, profile, reason, `defer_until` or none | SQLite journal plus the `v2:deferred` label and a `wsd-defer` bead comment | Not rebuilt: a deferred bead with no journal row is escalated `journal_lost`, as any other bead. |

The SQLite journal is backed up with the beads backups. The recovery order on startup is: journal integrity check, then read beads, then reconcile actions in `executing`/`uncertain`, then resume park journals (including defer and undefer operations, revision 14), then reconcile sessions, and only then accept events.

**Journal upgrades (revision 14).** A schema upgrade runs in one transaction, keeps every existing table and row, and on any failure leaves the file as it was and refuses to start. A journal is never discarded to recover from a failed upgrade. The upgrade that adds launch entries also adopts every session launched before they existed (§4.4, D2 legacy sessions): a verified one gets its adopted entry, and any other is marked for operator recovery, so nothing resumes on an unknown launch history.

### 3.4 Operator identity and ingress authentication

- **Allowlist:** `policy.json` maps each approver to their identities (Marmot npub, GitHub login, Radicle DID). The btq approver name, the Marmot sender and the forge reviewer must resolve to the same approver entry.
- **Marmot:** ingress accepts commands, approvals and steering only when **all** of these hold:
  - the MLS-authenticated sender pubkey (reported by `wn-agent`, never parsed from message text) is on the allowlist;
  - the group ID is a registered workstream or control group;
  - the message ID has not been seen before (replay protection).
- **Membership changes:** a *detected* change (a member joining or leaving a registered group, or an identity change) raises an alert in the control group. Approvals from that group are suspended until the operator runs `/trust-group`. Detection covers the cases below; the one gap is a residual risk.
  - `wsd` learns of other members' changes from `wn-agent`'s membership events. Those events are not delivered for a change made by the harness's own identity (S4), and `wn-agent` offers no member list to compare against.
  - So **every membership change by the harness identity goes through `wsd`**, which journals it and raises the same alert and suspension itself. No sandbox can reach the `wn-agent` control socket (§7).
  - As a backstop, reconcile compares each registered group's member count with its last trusted value, and a mismatch is treated as a membership change.
  - **Residual risk, for operator acceptance with revision 12:** a change made directly on the control socket, bypassing `wsd`, by something running as the service user (for example `admind`'s agent, §8) and keeping the count unchanged, such as swapping one member for another, is not detected, so it raises no alert and suspends nothing. The same risk applies to `admind`'s own group (§8), which now holds several operators.
- **Forge:** reviews count only from allowlisted identities. A forge review is pinned by the head SHA, which is part of the ask's `context_digest`. A review is bound to a revision (§5.5). A review that is dismissed or revoked before execution starts cancels the pending decision.
- **Non-allowlisted input** is logged and ignored, with no reply, so the system doesn't confirm it exists.

## 4. Agents

### 4.1 Roles and interchangeability

**Agents and models are configuration, not code.**
- The code knows only **adapter types**, one per harness CLI: `claude-code` and `codex`.
- Adding a model is a config change. Adding a harness means writing a new adapter.
- A lint test fails if any model name or a specific agent pairing appears in the source outside test fixtures.

Role and profile settings follow the single precedence order in §15: built-in defaults, then host `config.toml`, then `workstreams/<ws>.toml`, then a bead label `role:<role>=<profile>` (for example `role:coder=claude-opus`, overriding a single bead).

```toml
# Agent profiles: a named combination of adapter type, model and launch options.
[profiles.gpt-sol]
adapter = "codex"
model   = "gpt-6-sol"
effort  = "medium"          # adapter maps this to its own flag
args    = []                # extra CLI args, passed verbatim

[profiles.claude-opus]
adapter = "claude-code"
model   = "claude-opus-5-5"
args    = []

# Roles map to profiles. This is the reference install's local config (GPT-6-Sol codes, Claude Code reviews);
# the shipped defaults name no models (§15).
[roles]
coder    = "gpt-sol"
reviewer = "claude-opus"

[review]
mode_when_same_model = "adversarial"   # §11.1 backstop; "block" refuses instead
fallback_reviewer    = "gpt-sol"       # used if the reviewer profile is unavailable

# Accounts (revision 14, §4.4): a named login for one adapter (host config only, §15).
[accounts.codex-main]
adapter   = "codex"
login_dir = "<directory holding this login>"

[accounts.codex-team]
adapter   = "codex"
login_dir = "<directory holding this login>"

# A profile may list accounts; this extends [profiles.gpt-sol] above.
#   accounts = ["codex-main", "codex-team"]   # order is preference; omit for the default login
#   failover = "next"                         # default "none": defer only (§4.3)

[usage]
reserve_percent             = 5     # a trusted window blocks at >= 100 - reserve_percent used
stale_minutes               = 30
unknown_backoff_minutes     = 30
untrusted_max_defer_minutes = 60    # the longest a session's own untrusted report can defer it
min_recheck_seconds         = 60
max_window_hours            = 192   # the longest reset horizon accepted
```

- **Review mode is derived, not declared.**
  - When `wsd` starts a review, it compares the coder's and reviewer's resolved `model`. If they differ, the review is `cross-model`. If they match, including after a fallback, it is an adversarial fresh session, or the review is refused if `mode_when_same_model = "block"`.
  - **Evidence comes from what ran, not from config.** At launch, `wsd` records a launched-session record on the bead: profile, adapter, the exact model passed on the command line, the model reported in the session transcript or hook payload, and the session ID. The models and native session ID are recorded per launch, in the launch entry (revision 14, §4.4). For reviews it also records the reviewed `BASE..HEAD`, the findings, and their disposition.
  - The `Code-Review:` line is generated from the reviewer's and author's **recorded** models, read from their launch entries. If the configured and reported models disagree, the review is invalid and is re-run.
- **Validation at startup and on reload:**
  - every role resolves to a profile, and every profile to a known adapter;
  - models the adapter can list are checked when possible;
  - an invalid config is rejected whole, and the last good config stays active.
  - accounts and usage (revision 14): every account names a known adapter and a `login_dir`; account names are plain identifiers and not `default`; no two credential identities overlap; every account a profile lists exists and uses the profile's adapter; `accounts` is not empty; `failover` is `none` or `next`; accounts and `failover = "next"` are accepted only on adapters with the capabilities S7 demonstrated (§4.4, D7, D9). `reserve_percent` is an integer from 0 to 50. The other `[usage]` values are positive integers, with `min_recheck_seconds` no more than 60 × the smallest of `stale_minutes`, `unknown_backoff_minutes` and `untrusted_max_defer_minutes`, and `untrusted_max_defer_minutes` and `unknown_backoff_minutes` no more than 60 × `max_window_hours`.
  - `/workstreams` shows each workstream's resolved coder → reviewer pairing.
- **Accounts (revision 14).** A profile may name an ordered list of accounts, each a login for the profile's adapter; without one it uses the adapter's default login. Accounts are host configuration only. The session key never includes the account. `metadata.wsd_session` is the session's immutable identity; each launch adds a launch entry (`metadata.wsd_launches`) with its session key, profile, generation (counted per session key), role, account, credential key and models (§4.4).
- **Usage-aware launch (revision 14).** Every candidate at pickup, and every launch, resume and relaunch, goes through the headroom gate (§4.4, D4) on its own resolved profile. With `failover = "next"` it takes the first eligible account; with `"none"` it uses the first account or defers.
- **Changing configuration:** role and profile changes are harness configuration, which is hard-deny for agents (§5.3). They are made by the operator, through `admind`, or through an approved policy bead.
- Everything above `AgentRuntime` is agent-agnostic.
- **Session identity** is `uuid5(NS, f"{bead}:{role}:{profile}")`, labelled `<short-bead> · <role> · <title>`. The label appears in the session name, the tmux window, the Marmot thread header and `/status`.
  - The `uuid5` is the harness's logical session key. Claude Code's first launch uses it as its session ID; a handoff relaunch on another account uses a generation-specific ID recorded in its launch entry (revision 14, §4.4 D7). Codex assigns its own thread ID (§4.2), so for Codex, resume, steering and reconcile use the assigned thread ID recorded in the launched-session record, or a confirmed post-launch name.
- **Swapping agents** is done with a deterministic handoff built from the bead: description, acceptance criteria, comments and decisions, plus `git log` and diffstat on the bead branch. The new session starts from that. The old transcript is not needed, so a swap works even after a crash or context overflow.

### 4.2 Adapter capabilities (verified against the installed CLIs, 2026-09-29; Codex column per spike S1)

| Capability | Claude Code 2.1.283 | Codex 0.157.0 |
|---|---|---|
| Launch with a fixed ID | `--session-id <uuid>` | None. The thread ID is server-assigned: `wsd` reads it right after launch and records it on the bead. A deterministic label can be applied after launch with `/rename`, and `resume` and `queue` accept it. |
| Display name | `-n/--name` | Session name (`resume`, `archive`, and `queue` accept a name) |
| Resume | `--resume <id>` | `codex resume <id\|name>` |
| Steer a live session | Channels (research preview) through hermes-channel; fallback `tmux send-keys` | `codex queue --remote unix://<sock> --thread <id\|name> --message`. It needs a dedicated per-session `codex app-server --listen unix://<sock>`, with the TUI launched with the same `--remote`. The app-server runs inside the sandbox (§7). Under OpenShell (S5, codex-cli 0.160.0; revision 15), the app-server does not bind the `--listen` path itself. It binds a socket in `/tmp/codex-daemon-<uid>`, which must be mode 0700, and leaves only a symlink at the `--listen` path, which dangles from the host. So the launcher binds a 0700 host directory at `/tmp/codex-daemon-<uid>`, and the host's `codex queue --remote` dials the real socket in it. |
| Permission mode inside the sandbox | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` (`--yolo`) |
| Pre-tool hook (policy UX layer) | `PreToolUse` → allow / deny with reason (fires in every mode) | **Fires in interactive sessions once trusted** (S5, codex-cli 0.160.0; revision 15). Under a remote app-server, `PreToolUse` and `UserPromptSubmit` run with the harness's real `hooks.json` schema (nested `hooks` arrays, `matcher`), and a `PreToolUse` deny with a reason works. Under `codex exec` no command hook runs at all (S1). Not relied on until the launcher can establish hook trust (next row; §5.3). |
| Lifecycle hooks | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, Notification | session_start, user_prompt_submit, stop, pre/post_compact (in use today) |
| Hook trust | Settings file | **Per-hook trust** (S5; revision 15). Codex runs only trusted hooks. It skips a new `hooks.json` until each hook is trusted, and records trust as a `trusted_hash` in `config.toml`. S5 did not find how that hash is derived. S1's `--dangerously-bypass-hook-trust` could not be reproduced. Plan 4 must either pre-seed the trust state or find the derivation. The hook command must not embed the per-session token, because that would make the hash per session; the hook reads the token from a file under `/run/hz` instead. |

Both CLIs run interactively and unmodified, so subscription-plan auth is preserved. **`tmux attach` is break-glass only (revision 14, §8.1):** the operator can still attach to any session and take over, but no documented procedure, prompt or recovery step may require it. Steering goes through Marmot (§5.6), and anything a session would otherwise show only in its terminal reaches the operator as a Marmot card (§5.7, §8.1).

### 4.3 Session model

- **Queue identity:** `wsd` uses `btq` as its own queue agent, `wsd`, with **one btq worker per bead**: session `uuid5(NS, f"{ws}:{bead}")`, so the worker is `wsd:<host>:<bead-session>`.
  - `Queue.claim()` allows one active claim per *worker*, and `ready()` accepts unlabelled beads from any session of the right agent and workstream. So `wsd` lists ready work with the workstream session `uuid5(NS, ws)`, then claims each bead with its per-bead worker. btq's claim and ready logic is unchanged.
  - **Required btq and setup change (prerequisite, its own bead):**
    - add `wsd` to btq's `AGENTS`;
    - create a Dolt user `wsd` with the same grants as the other agent users;
    - add a `wsd` entry to btq's local credentials file;
    - add a PICKUP.md note that `agent:wsd` beads are worked by v2;
    - make `approval_valid()` also require `decided_digest` (or `approved_digest` for CLI approvals) to equal the recomputed `context_digest` (§5.9). This is a shared function used by btq, `wsd` and `approve-bead`, so all three compute the digest the same way.
    - make the `Queue` constructor's locations configurable, so the §15 host config can supply them: config directory, credentials file, TLS certificate, Dolt host, port and database, and the beads repo.
      - These become optional constructor arguments and `BTQ_*` environment variables.
      - Their defaults are today's values, so existing btq users are unaffected.
    - No change to routing, claim, ready, or gate logic.
  - Holding several parked claims doesn't block pickup.
  - The per-bead worker is recomputed deterministically, so `wsd` can always `owned()` and `close` its own claims after a restart.
  - **Pause is one shared gate per workstream:** the btq `paused` flag of the workstream-session worker.
    - The supported pause commands are `/pause <ws>` (from Marmot) and `wsctl pause <ws>` (a local CLI that goes through `wsd`). Both take the claim lock below before they set the flag and acknowledge.
    - A direct `btq pause` on that worker sets the same flag, but it isn't lock-synchronised. It is honoured from the next claim check onward, so at most one already-started claim can complete after it. It is documented as best-effort, and `wsctl` is the command to use.
    - Per-bead workers have their own btq state directories, so their `ready()` doesn't see that flag. Instead, `wsd` performs every claim inside a per-workstream claim lock and re-checks the shared flag **immediately before** `claim()`.
    - `/pause` takes the same lock, so no claim can start after a pause has been acknowledged.
    - Pausing stops new claims only. Running and parked beads continue, unless the operator uses `/stop`: a parked bead whose blockers close still resumes while the workstream is paused (§6.3 matches this, revision 14).
  - v2-managed beads carry `agent:wsd`. Which coding agent does the work is role configuration (§4.1), not a bead label.
  - Adding the `wsd` identity (a Dolt user plus a btq `AGENTS` entry) is an operator setup step.
  - Agent sessions are children of `wsd`, not btq identities. The claim belongs to `wsd`, not to an agent session, so it survives session crashes, swaps and parks.
- **Worktrees use btq's convention** (`btq worktree`: `<repo>-btq-<id>` on branch `btq/<id>`), so there is only one convention. There is one session per (bead, role) in that worktree.
- **Parking** leaves the bead **claimed by `wsd`** (`in_progress`), labelled `v2:parked`, with a blocking edge to what it waits on. `wsd` never unclaims (PICKUP.md's `agent:wsd` exception, plan 3 decision (c)).
  - The park sequence is journaled (§3.3): record intent, commit WIP (recording the SHA), apply the label, add a bead comment. Each step is idempotent and replayed after a crash.
  - A queue write with an uncertain outcome is read back before any retry.
- **Held parks (revision 14; plan 3 decision (a), 2026-10-04).** `v2:held` is a park reason that **needs no blocking edge**. An operator `/stop` (§6.3) parks with `v2:held`, written first, plus `v2:parked`. Escalations that need operator recovery hold the same way. `v2:held` is checked before every launch, and a held bead is never resumable. **A journaled release (plan 3's `Parker.release`) is the only exit:** it removes `v2:held` and `needs-human`, then puts a parked bead back to waiting on its blockers, or hands an unparked one to a resume through the launch guard. Removing the labels by hand changes nothing in `wsd`.
- **Deferring (revision 14)** is parking on time rather than on a bead (§4.4, D5).
  - When the headroom gate gives a deadline or `account_changed` for a bead `wsd` holds (a due resume, or a session that reported a limit), the bead stays claimed by `wsd` (`in_progress`), is labelled `v2:deferred`, and gets no blocking edge.
  - The defer sequence is journaled: intent (session key, deferral number, role, profile, reason, `defer_until` or none) → stop the bead's sessions → commit the WIP → label → a `wsd-defer` bead comment.
  - A quota deferral is resumable once `defer_until` has passed. An `account_changed` deferral has no deadline: it is gated again at startup, on a configuration reload or on the operator's release, and is resumable once that gives an account. It is **never retried on a timer** (§17). Either way the bead must carry neither `v2:held` nor `needs-human`. Undeferring goes through the gate again, then removes the label, then launches through the launch guard.
  - Only the bead's own session can defer it on untrusted evidence, and only for a bounded time (§7).
- **Resumable beads** are `wsd`'s own query: beads it has claimed that carry `v2:parked` with all blocking edges closed, or `v2:deferred` whose deferral is over (its `defer_until` passed, or its `account_changed` wait gated to an account; both conditions when it carries both labels), and that carry neither `v2:held` nor `needs-human` (revision 14). They are resumed as the same session (§4.1) in the same worktree.
- A workstream runs **at most one active session per role**. A reviewer can review bead A while the coder works on bead B. Running several coder sessions at once is out of scope for v1.

### 4.4 Accounts and usage-aware scheduling (revision 14)

**Why.** Each adapter had exactly one login, the operator's own, bound into every sandbox (§7). When that subscription reaches a usage limit, every session on the adapter fails, and the control plane learns of it only after the fact. An operator with more than one subscription for a harness had no way to use the second. The ideas below come from [t3code](https://github.com/pingdotgg/t3code) (MIT), as ideas, not code: several logins per harness, each its own config directory, and subscription usage windows read from the harness. Its transport (Agent SDK, headless app-server, ACP), its permission model and its task model are not taken; they conflict with §2, §5.3 and §7. Change-plan items AU-14 and AU-15 later borrow its driver-registry shape and its Codex app-server protocol schemas, as non-blocking tasks.

- **D1. Accounts are host configuration.**
  - An *account* is a named login for one adapter: `[accounts.<name>]` with `adapter` and `login_dir`, the directory that holds that login.
  - A profile may list accounts in order: `profiles.<p>.accounts = [...]`. Each must use the profile's adapter. An empty list is rejected; leave the key out instead.
  - A profile without `accounts` uses the adapter's default login, as before. That login is the **implicit account** `default` of its adapter. The name `default` is reserved, and account names are plain identifiers (no `@`, no path separators).
  - Each account, the implicit one included, has a **credential identity**: its adapter plus the canonical (symlink-resolved) paths of its login files, the file set S7 establishes for the adapter (D7). Two accounts are rejected if their login directories are the same or contain one another, or if any login file of one resolves to the same file as any login file of the other, through a symlink or a hard link. Aliases are impossible, at directory and at file level.
  - The **credential key** is a digest of the credential identity. Usage rows, exhaustion marks and refresh locks are keyed by it, never by the account's name, so the two adapters' `default` accounts never share a row, and pointing an account at another login starts from unknown usage. Rows whose key no configured account has are ignored.
  - Accounts live only in the host layer (§15 layer 2). Workstream config, bead labels and the environment can't name or select an account.
- **D2. An account is not part of session identity. Each launch gets its own entry.**
  - The session key stays `uuid5(NS, f"{bead}:{role}:{profile}")` (§4.1). The account is never an input.
  - `metadata.wsd_session` stays the immutable logical identity it is in plan 3 (role, profile, session key, repository, worktree). It never carries an account, and `wsd` never rewrites it.
  - Every launch of a session (first launch, resume, relaunch, failover) gets a **launch entry**: the session key and profile, generation (1, 2, …), role, account name, credential key, the model passed on the command line, and, each set once later, the dispatch mark, the native session or thread ID, the reported model, and the outcome (`launched` or `abandoned`).
  - **Generations are per logical session.** Entries are keyed by (session key, generation), so each (bead, role, profile) counts its own generations from 1. A reviewer that falls back from profile P to Q and later returns to P continues P's sequence; Q has its own. Entries carry the session key, so the sequences can be rebuilt from `metadata.wsd_launches` alone.
  - **The account is pinned.** The gate chooses the account once, when the launch operation's intent is journaled, and the entry records it and its credential key. Replay launches exactly the journaled account and model, never a fresh choice. Immediately before the launch, the guard checks that the account's current credential key still equals the journaled one and that the account is still eligible. If either check fails, the entry's outcome is set to `abandoned`, and a new attempt with the next generation chooses again. A generation is never reused. A new generation whose credential key differs from the last launched entry's is an account switch (D7), whatever the account's name.
  - **Dispatch and reconciliation.** The journal records a **dispatch mark** on the entry immediately before `AgentRuntime.launch` is called, and the runtime tags the launch with its session key and generation (the tmux session's options, the hook spool's launch tag, and, for Claude, the generation's native ID). An entry with no dispatch mark never reached the runtime and may be abandoned. An entry with a dispatch mark and no outcome is **unresolved**.
  - **Launch receipts (revised after the G1 r1 review).** When `AgentRuntime.launch` returns, `wsd` itself, on the host, journals a **launch receipt** keyed by (session key, generation) before it does anything else with the result. The receipt records the runtime's own result of the spawn call: `started`, with what the runtime created (the tmux session and pane IDs on `wsd`'s own tmux server, and the pane's PID), or `refused`, with the runtime's error, when nothing was started. Only `wsd` writes receipts, into its journal, which no session can write (§7). The outcome is then set from the receipt, journal first and then on the bead with read-back.
  - Before an unresolved entry is abandoned, and before the gate chooses an account for any later launch of that session, `wsd` reconciles it **against its receipt only**. A `started` receipt sets `launched`, whether the session is still running or has ended. A `refused` receipt sets `abandoned`. **No receipt** (a crash between the dispatch mark and the receipt, or a lost journal) holds the bead, escalated `unexpected_state` for operator recovery. Hook-spool events, transcripts under the native ID and the tmux session's tags stay **untrusted observations** (§3.3): they may be shown to the operator in the escalation, but they never set an outcome, because a session can forge any of them. An unresolved entry is never abandoned on a timer and never ignored by the continuity check (D4).
  - **Legacy sessions.** Sessions launched before launch entries existed (plan 3, before AU-3) are adopted once, by the journal upgrade (§3.3), before anything resumes them. Adoption is **verified** only when the session's adapter has no configured accounts at upgrade time, so the session can only have run on the adapter's default login, and that login's credential identity resolves. A verified session gets one adopted entry: generation 1, account `default`, the default login's current credential key, the native ID it already uses, outcome `launched`, marked `adopted`. The bead copy is appended at startup, with read-back, before pickup. In every other case (accounts already configured for the adapter, an unresolvable default login, or journal and bead disagreeing) the bead holds, escalated `unexpected_state`, and is never resumed on a guessed account.
  - Launch entries are journaled first, then appended to the bead's `metadata.wsd_launches` before the launch, with read-back. An entry is never removed or changed, except that an empty field may be set once. On replay: an identical entry is kept; a missing one is written; a different one under the same generation escalates `unexpected_state`.
  - This is a new metadata key on a claimed bead, so it needs a PICKUP.md change (change-plan item AU-0).
  - A relaunch on a different account is still the same logical session.
- **D3. Usage is an advisory cache with two trust levels.**
  - `wsd` keeps the latest usage windows per account: window ID, kind, percent used, reset time, when it was observed, the source and the trust level.
  - **Ingestion.** Each observation gets a **receipt sequence**, a counter persisted in the journal, and `observed_at`, `wsd`'s own UTC clock at receipt; a timestamp in the payload is ignored. The account is the one on the launch entry of the channel that delivered the observation, never a value from the payload; a payload naming another account is dropped. `used_percent` must be a finite number in [0, 100], otherwise the observation is dropped. `resets_at` must lie after `observed_at` and no later than `observed_at + usage.max_window_hours`, otherwise the reset time is unknown.
  - **Replacement.** Trusted and untrusted observations are stored in **separate tables**, each with its own replacement key: (credential key, window ID) for trusted rows, (session key, generation, window ID) for untrusted rows. Within a key, the observation with the higher receipt sequence replaces the other. A row in one table never replaces or hides a row in the other. Ordering never depends on the clock, so a newer read wins even after the clock jumps back. `observed_at` is used only for staleness.
  - **Trusted** observations are made by `wsd` on the host, outside every sandbox, with that account's login (a host-side usage read, D9). **Untrusted** observations come from inside a sandbox: the in-session Codex app-server, a Claude status line, a transcript or a hook. This is the §3.3 hook-spool rule.
  - **Scope.** Trusted observations enter the shared per-account cache and affect every launch on that account. Untrusted observations affect **only the launch that produced them**: they can defer that session's own bead (D5) and nothing else. Another session of the same bead (for example a reviewer on another profile) never reads them. They never mark an account exhausted, never move a launch to another account, and never make a reviewer unavailable.
  - Usage only ever affects *scheduling*. It never affects policy, approvals, review evidence or any decision field.
- **D4. The headroom gate is a deterministic pure function** of (resolved profile, previous launch, adapter capabilities, cache, now, settings). The previous launch is the credential key of the last entry with outcome `launched` and the same session key, or none for a first launch. The gate is never called while that session has an unresolved entry (D2): reconciliation comes first, so a launch that happened is never mistaken for a first launch.
  - A trusted window **blocks** while all of these hold: `used_percent >= 100 - usage.reserve_percent`; `observed_at + usage.stale_minutes > now`; and its `resets_at` is unknown or later than `now`.
  - A trusted **exhaustion mark** (from a trusted read confirming a reached limit) blocks while its `until` is later than `now`. `until` is the window's reset time, or `now + usage.unknown_backoff_minutes` when that is unknown.
  - An account is *eligible* when nothing blocks it. Unknown, stale and expired data are eligible, so the gate never blocks on missing data.
  - **Permitted accounts**, an ordered list, built in two steps:
    1. The mode's list: with `failover = "none"`, the profile's first account; with `"next"`, all of its accounts, in order.
    2. **Credential continuity**, for a later launch (a first launch skips this step):
       - with `"none"`, whatever the adapter's capabilities: the list keeps the first account only if its current credential key equals the previous launch's; otherwise the list is empty;
       - with `"next"` on an adapter with a demonstrated way to switch (D9 (b) or (f)): the account with the previous launch's credential key moves to the front (account affinity, D7), and the others follow in order;
       - with `"next"` on an adapter that can't switch: the list keeps only the account with the previous launch's credential key, or is empty.
  - So reordering, removing or repointing accounts never switches a session's login under `"none"`, nor on an adapter that can't switch. Only `"next"` on an adapter that can switch moves a session to another login.
  - The same permitted list is used by pickup, the launch guard's check, the freshness-gate refusal path (D8, §7) and the wake time, so they never disagree.
  - **Result:** the first eligible permitted account; `account_changed` when the permitted list is empty; or else a deadline. A window's clear time is the earlier of `resets_at` and `observed_at + stale_minutes`; a mark's is its `until`. An account's clear time is the latest of its conditions' clear times. The deadline is the earliest permitted account's clear time, and at least `now + usage.min_recheck_seconds`, so it is always in the future.
  - **Clock.** Times are stored as UTC epoch seconds. After the clock jumps back, a window or mark whose `observed_at` is later than `now` is ignored (unknown) until a newer receipt replaces it or the clock catches up, and a stored deadline or `until` later than `now + max_window_hours` is rewritten once, in the journal, to that bound. A rewritten value is never moved again, so a deadline can't keep sliding while the clock is behind. A forward jump only releases work early, and the gate then checks again. Tests drive the gate, the defer cycle and replay with an injected clock.
  - The gate runs **per candidate**: for new work, on the bead's resolved profile (a `role:` label override included); for a resume, on the profile of that role's own record (the coder's `wsd_session` record, or another role's deferral record, D5). It also runs at every launch, resume and relaunch. There is no role-wide precheck.
- **D5. Deferred parking.**
  - A bead `wsd` has already claimed is **deferred** when the gate gives a deadline or `account_changed` for it: a due resume or relaunch finds no eligible account, or its running session reports a limit (D7, D9). It stays claimed and `in_progress`, labelled `v2:deferred`, with no blocking edge (§4.3).
  - Pickup never claims a bead only to defer it. A ready bead whose resolved profile has no eligible account is skipped (§5.2).
  - **Deferral records** are keyed by (session key, deferral number). The deferral number counts that session's deferrals from 1, separately from launch generations. A record holds the role, the profile, the reason (`quota` or `account_changed`), `defer_until` (none for `account_changed`) and the trust of its evidence. A session's **current** deferral is its highest-numbered record; a lower-numbered one is superseded and is never due.
  - **Defer** is a journaled operation: intent (the record) → stop every session of the bead, confirmed (unconfirmed: hold, as a park does) → WIP commit (SHA recorded) → label `v2:deferred` → a bead comment with a machine-readable line `wsd-defer session=<key> n=<number> role=<role> until=<UTC or none> reason=<reason>`. Each step is idempotent and replayed after a crash. A session deferred again gets the next deferral number and a new comment.
  - A deferral on untrusted evidence only (the producing launch's own rows, D3) gets `defer_until` from the observation's reset hint, or `now + unknown_backoff_minutes` when it has none, clamped to between `now + min_recheck_seconds` and `now + usage.untrusted_max_defer_minutes`.
  - **Undefer** is a journaled resume, taken when a quota deferral's `defer_until` has passed, or when an `account_changed` deferral is gated again (below): intent → the gate again (a deadline: a new quota deferral number, the label stays; `account_changed`: see the transition below, and the operation ends) → remove `v2:deferred` → launch through plan 3's launch guard, with all of its checks. A bead that also carries `v2:parked` doesn't launch: undefer ends with the bead parked, and the parked resume takes it once its blockers close.
  - **`account_changed` deferral** (D4): the same operation with reason `account_changed` and no `defer_until`. It is outside every quota timer and wake time, and gets one comment and one alert. It is gated again at startup, on every configuration reload, and on the operator's release (`Parker.release`, which for a deferred bead only triggers this). If the gate then gives an account, the wait is marked over in the journal, and the next pickup undefers it like a due quota deferral, with all its checks (ownership, blockers, `v2:held`, `needs-human`, the launch guard). If it gives a deadline, the wait becomes a quota deferral (the next deferral number, a new comment). If it still gives `account_changed`, nothing is written. **It is never re-gated on a timer**; this is an operator decision point (§17).
  - **Quota to `account_changed`.** When a due quota deferral, or a running session's limit, gates to `account_changed`, `wsd` journals one transition: a new deferral record with the next deferral number, reason `account_changed` and no `defer_until`, which supersedes the quota record. The quota record is then never due again, and no timer or backstop rechecks it. The transition writes its one comment and sends its one alert, each idempotent on the new deferral number, so a replay after a crash at any step finishes them once. Only a deferral whose current record is already `account_changed` stays unchanged when the gate gives `account_changed` again. The reverse transition is the deadline case above.
  - **Deferral is per role.** Coder pickup resumes only coder deferrals. A reviewer deferral is a **review wait**: its record also holds the reviewed `BASE..HEAD`, and when it is due it re-enters review selection (§5.8, §11.1), never coder pickup. A review wait whose bead branch has moved past the recorded HEAD is dropped, and review selection starts afresh.
  - **Precedence.** `v2:held`, `needs-human`, and a HELD or STUCK journal row win: such a bead is never undeferred. A bead carrying both `v2:parked` and `v2:deferred` launches only when its deferral is over **and** all its blockers are closed.
  - **Lost journal.** Plan 3 D8 is unchanged: a deferred bead the journal has no row for is escalated `journal_lost`, never relaunched. The defer comment shows the operator the last reason and deadline, if any.
- **D6. Failover is opt-in and needs trusted evidence.**
  - `profiles.<p>.failover = "none" | "next"`, default `"none"`. With `"none"`, only the profile's first account is used, and usage only defers work. With `"next"`, a launch, resume or relaunch uses the first eligible permitted account, judged on the trusted cache.
  - **After a reactive limit** (always untrusted): `wsd` runs the defer operation up to the WIP commit, then a trusted host-side read of that account (D9). If the read confirms the limit, the account is marked exhausted and the gate runs again: on an eligible permitted account the session relaunches at once, where D7 allows the switch (the same logical session, a new launch entry); with none, the defer completes. If the read doesn't confirm it, or the adapter has no trusted read, the bead defers on its own account. A forged signal can therefore never move a session onto another account.
  - `failover = "next"` is rejected for an adapter with no demonstrated trusted host-side read (D9).
  - Whether a provider's terms allow switching accounts automatically is the operator's call (change-plan gate G3). The docs say so; the code doesn't check.
  - `config check` warns when a profile lists more than one account with `failover = "none"`.
- **D7. Account binding reuses the §7 synthetic home.**
  - An account only chooses *which* login files the sandbox can reach. The session's transcripts and state stay in its synthetic home whichever account it ran under.
    - Claude: `CLAUDE_CONFIG_DIR` points at the synthetic home's config directory; `HOME` is never overridden for it (on macOS that moves the keychain lookup).
    - Codex: `CODEX_HOME` points at the synthetic Codex home, with the account's `auth.json` the only login file reachable in it.
  - Only the chosen account's login files are reachable. Before every launch, the compiled sandbox policy is checked: every credential-bearing source must be in the chosen account's login file set (by canonical path), and a source inside any other account's login directory, the implicit `default` included, refuses the launch. A self-test probe then checks the result inside the sandbox (§7).
  - **Conditional on S7.** For each adapter, accounts ship only once S7 has shown that adapter's login file set and a working login through the synthetic home on Linux. Until then, `config check` rejects `accounts` on that adapter.
  - **Switching a session's account.** A session keeps the account of its last launch entry while that account is eligible. It moves to another account only when that one is ineligible, `failover = "next"`, and S7 demonstrated a way to continue on another account for the adapter: (b) cross-account resume, or (f) a handoff relaunch, a fresh native session under the new account in the same synthetic home, started from the §4.1 deterministic handoff. Without (b) or (f), `"next"` only chooses the account of a session's first launch.
  - **Native session IDs.** Generation 1 of a Claude session uses the session key as its native ID (§4.1). A handoff relaunch uses `uuid5(NS, f"{session_key}:{generation}")`, recorded in its launch entry. Resume, steering and reconcile use the native ID of the latest launched entry, as they already do for Codex thread IDs.
- **D8. The freshness gate runs per credential identity.** The §7 launch freshness gate checks and refreshes the token of the account being launched. Refreshes are serialised by a host file lock keyed by the credential identity (D1). Every refresh the harness starts (the `wsd` launcher, the trusted usage read, `heterodyne setup`) takes the same lock and rereads the login files under it before deciding. Refreshes the CLIs make by themselves outside the harness (the `admind` agent, the operator's own use) aren't serialised, as before.
- **D9. Usage sources and signals are capabilities that S7 must demonstrate.** Per adapter, on its pinned version and on Linux, each is enabled only once S7 has demonstrated it: (a) **login binding** (D7); (b) **cross-account resume**; (c) an **in-session usage source** (untrusted); (d) a **trusted host-side usage read**, made by `wsd` on the host under the D8 lock; (e) a structured **limit-reached signal** (a hook payload or a transcript entry); (f) a **handoff relaunch** under another account.
  - Codex candidates: (c) `account/rateLimits/read` at launch and `account/rateLimits/updated` through the per-session app-server (§4.2); (d) the same read from a short-lived host-side app-server using the account's `CODEX_HOME`. Claude candidates: (c) the status-line JSON or structured transcript entries; (d) unknown (the Agent SDK's `get_usage` is ruled out by §2).
  - Without (c) and (d), usage is unknown, which is eligible. Without (d) there is no shared exhaustion, no failover and no quota-based reviewer fallback for that adapter. Without (e) a limit isn't recognised as one, and the session's end is handled by the existing §10 rows.
  - Screen scraping stays out of bounds (§2), so a signal that appears only in the terminal can't be used.
- **D10. Visibility.**
  - `/workstreams` and `/status` show each role's account and its headroom, marking untrusted data as such (§6.3).
  - A workstream whose remaining work waits only on quota shows "deferred: quota until HH:MM". An `account_changed` wait shows "deferred: account changed", with its recovery condition (restore the account's login or order, then reload or release), and never a time.
  - When every permitted account of a role is exhausted on trusted evidence, the control group gets one alert per episode. The daily digest lists accounts that hit a limit.
  - No message shows a login path or an email.
- **D11. `admind`.** Each `admind` process (the admin agent and the summarizer) resolves its own profile and adapter (§8). When that profile lists accounts, the process uses the first one, with `CLAUDE_CONFIG_DIR` or `CODEX_HOME` set to that account's `login_dir`. `admind` isn't sandboxed, so there is no synthetic home. It ignores the headroom gate and never fails over: it is the recovery path, and a gate must never stop it from launching.

**Gates.** G1 is this revision's review and approval. G2 is the S7 findings (§13). G3 is the operator's reading of each provider's terms before any `failover = "next"` is configured. The change plan's dependency graph is authoritative for the order of AU-0 to AU-17; plan 3 is unchanged, and the work runs as plans 3b and 4b after it.

**Rejected alternatives (accounts):**

| Option | Why rejected |
|---|---|
| Use t3code as the execution layer, or port its drivers | Its Claude driver uses the Agent SDK (rejected in §2). It runs every harness headless, which loses the interactive CLI and hook-derived state (§2). It is TypeScript (§16). |
| Make the account part of the session key | A failover would become a new session, which loses resume and the session's recorded history. The account is an attribute of the launch, like the model. |
| Put the account in `metadata.wsd_session` and update it on failover | Plan 3 keeps that record immutable, and one field can't describe several launches. Launch entries do both. |
| Fail over automatically by default | The provider's terms may not allow it. Deferring is always safe. |
| Let sandbox observations mark an account exhausted for everyone | One compromised session could then starve every workstream on that account, or push launches onto other accounts. |
| A separate state-store home per account (t3code's Claude approach) | §7 already gives each session its own synthetic home. Binding only the login files avoids sharing or copying transcripts between accounts. |
| Block a bead on an "account" bead instead of deferring | It would put a non-task bead into the queue and make btq ready logic carry quota state. A time-based defer stays inside `wsd`. |
| A role-wide gate before pickup | A bead's `role:` override can select another profile, and one ineligible candidate would stop eligible ones behind it. |

## 5. Flows

Every flow has the same shape: **observe** (hook, Marmot or forge), then **decide** (policy, then gatekeeper, then operator), then **record** (bead), then **act** (`wsd`). The LLM only ever fills the *decide* step, and it always has a deterministic fallback.

### 5.1 Intake: an unthreaded message in a workstream group

1. The router reacts 👀 immediately.
2. The gatekeeper rewrites the message, classifies it as **minor** or **material**, and proposes a title, description, acceptance criteria and dependency edges against the workstream's open beads.
3. **Minor:** create the bead, then reply in thread "📝 Understood as: …" with the task card.
4. **Material:** create a `kind:confirm` bead and ask "Did you mean …?". It has no `agent:` label, so btq routing deliberately never picks it up; only `wsd` resolves it. Dispatch waits for 👍 or a correcting reply; 👎 cancels. Other work continues meanwhile.
   - **Material means** a change to the target workstream or repo, an added or removed requirement, a question turned into an action, or anything destructive or outward-facing.
   - Fixing typos or resolving references is not material.
5. **Gatekeeper unavailable:** create the bead from the raw text, labelled `unreviewed`, and say so in the thread.

### 5.2 Pickup (deterministic)

- **Triggers:** a turn ends, a bead closes or parks, an approval resolves, the next quota wake time passes (revision 14), or the 60s backstop timer fires.
- When the coder role is idle, `wsd` takes the next bead from two sources, in priority order:
  1. its own resumable parked beads (§4.3);
  2. new work from `Queue('wsd', ws, uuid5(NS, ws)).ready()`, the btq library, equivalent to `btq --agent wsd --ws <ws> --session <uuid> ready`. Each chosen bead is then claimed with `Queue('wsd', ws, uuid5(NS, f"{ws}:{bead}")).claim(bead)` (§4.3).
- Ties go to resumable beads. For new work, `wsd` runs `claim`, then `worktree`, then launches the session. The task card reacts ⏳.
- A parked bead that becomes resumable never pre-empts the running task. It is picked up at the next task boundary.
- **Usage (revision 14, §4.4):**
  - Each candidate is gated on its own resolved profile (a resume on its recorded profile). A candidate with no eligible account is skipped, never claimed and never waited on, and pickup goes on to the next one. Claiming a bead only to defer it would churn the queue.
  - Source 1 includes deferred beads whose deferral is over (a quota deferral past its `defer_until`, or an `account_changed` wait marked over), in the same order as parked ones.
  - The **quota wake time** is computed at the end of every pickup that leaves the coder role idle. It is the earliest of: the `defer_until` of every coder quota deferral in the journal that is still ahead (whether or not pickup considered it); the current gate deadline of each due coder deferral that is still ineligible; and the gate deadline of each skipped ready bead. Held, stuck and blocked beads, and `account_changed` deferrals, don't count. A wake time that isn't after `now` is never armed. It is not stored: startup runs a pickup, which rescans the ready list and recomputes it.
- A workstream is **idle only when** no bead is ready and none is in progress. `/workstreams` reports it as "all-blocked: N beads on M approvals". A workstream whose remaining work waits only on quota reports "deferred: quota until HH:MM", not idle (revision 14).

### 5.3 Permission request

**Enforcement model.**
- Agents run fully permissive inside the external sandbox (§7): Codex with `--dangerously-bypass-approvals-and-sandbox` (`--yolo`), Claude with `--permission-mode bypassPermissions`. Neither raises permission prompts.
- **The sandbox is the security boundary.** It contains no git push credentials at all, and no deploy tokens, messaging tokens or secrets. Agents commit to the local `btq/<id>` branch in their worktree, and **nothing is pushed automatically**, consistent with PICKUP.md.
  - The only way anything leaves the host is an approved `push_branch`, `open_pr` or `merge_pr` action, executed by `wsd-act`. In v2, this approved path is how completed worktrees get integrated, the role PICKUP gives to Bel.
  - The only credential inside the sandbox is model auth (§7).
- The `PreToolUse` hook is the **UX layer**. It fires before every tool call in Claude Code, in every permission mode. It never fires under `codex exec` (S1). In *interactive* Codex sessions (the managed TUI model that `codex queue` needs, §4.2), it fires with the harness's actual `hooks.json` schema, but only once the hooks are trusted (S5; revision 15; §4.2). Until the launcher can establish that trust without an operator step (plan 4), **every Codex session is treated like a headless run**: the outer sandbox is its only enforcement. Where it runs (Claude Code; Codex once verified), it recognises operator-only intents early and turns them into clear, parked asks, instead of letting them fail obscurely against the sandbox. Matching commands in the hook is not a security control.
- **Headless Codex runs** (`codex exec`), and for now every Codex session, have no hook to rely on, so there is no UX layer and no hook fail-closed path (§10). Such a run is allowed only if it is `-s read-only`, or if it runs inside the outer sandbox with the sandbox as its only enforcement. In the second case, operator-only intents simply fail against the sandbox, and the failure is caught after the fact and the bead parked.
- **Approved operator-only actions are typed and executed by `wsd-act`** (§3.1), outside the sandbox. The agent requests; `wsd` records; the operator approves the exact payload; `wsd-act` revalidates and executes.
- **The action registry** is a closed, allowlisted set with fixed fields. Nothing else can be approved for execution:
  - `push_branch{repo, from_ref, to_ref, expected_sha}`
  - `open_pr{repo, head, base, title, body_ref}`
  - `merge_pr{repo, pr, expected_head_sha, method}`
  - `deploy{target}` (targets from config only)
  - `notify{channel_ref, text}`
  - Each payload also carries `idempotency_key = approval-id`, `approver` and `revision`.
- Anything outside the registry, such as sending email, spending money or touching secrets, is an **ask, not an action**. The approval authorises the operator or the agent to proceed within the sandbox, and `wsd` never executes it.

1. The `PreToolUse` hook sends the tool, its input, the session and the working directory to `wsd`.
2. The policy engine checks the versioned tier file (`policy.toml`; changes only through an approval bead):
   - **Auto-approve:** reversible actions confined to the bead's worktree and sandbox. That covers dependency installs, tests and local commits.
   - **Operator-only, always escalated:**
     - pushing to shared branches, and opening or merging PRs
     - deploys, and sending messages or email
     - spending money, and anything touching secrets or credentials
     - deleting outside the worktree
   - **Hard deny, never escalated:**
     - an agent changing harness, policy or sandbox config, which would let it widen its own permissions
     - these changes happen only through the operator, `admind`, or an approved policy-rule bead
   - **Grey zone:** goes to the gatekeeper with a 60s timebox. It returns allow, deny or escalate. If it times out or is unavailable, the request escalates.
3. **Escalation (park and continue):**
   - Create an approval bead with a plain-English summary, the reason it escalated, and, if it is an action, the **typed payload verbatim**. Add a blocking edge from the task to it.
   - Before any card is posted, the ask must pass the **context sufficiency gate** (§5.9). An ask that fails it never reaches the operator.
   - Agents request actions with `ws-request <type> <json>` over their session socket, which only creates a pending approval. A `PreToolUse` interception of, say, `git push origin main` is turned into the equivalent `push_branch` request.
   - The hook returns deny: "Parked pending operator approval. Commit WIP; this task will resume later."
   - Commit the WIP, react ⏸️, post the cards (§6.2), and run pickup.
4. Every decision adds a bead comment with the tier, the decider and the reason.

### 5.4 Approval resolution (Marmot)

- The operator reacts 👍 (approve), 👎 (deny) or ❤️ (always), or sends `/approve` or `/deny` as a reply, on either card.
- `wsd` looks up the message→bead map, which is persisted and survives restart, and resolves by the targeted message, not in arrival order.
- **The commit point is the approval bead.** All decision events from Marmot, GitHub and Radicle go through a single serialised decision queue inside `wsd`.
  - `wsd` is the only writer of decision fields; the policy forbids agents and humans from writing them directly with `bd`. Because processing is serial, there is no concurrent writer, so read-then-write is a valid compare-and-set.
  - For each event, in order:
    1. Put it in the SQLite **inbox** as `pending`. Drop it if its (surface, event ID) is already there.
    2. Read the approval bead. If a decision is already recorded, this event is a conflict or a duplicate. Mark it `superseded`, log it, and reply "already approved/denied by X via Y at T". It has no effect.
    3. Otherwise recompute the ask's `context_digest` (§5.9).
       - If it differs from the digest on the card the operator answered, the decision is rejected: "the ask changed after it was shown". The card is re-issued.
       - If it matches, write `decision`, `decided_by`, `via`, `decided_at`, `event_id` and `decided_digest` to the bead metadata, then **read it back**. The decision exists only once the read-back matches.
    4. Mark the inbox row `committed`, then mirror the decision to the other surfaces.
  - **Failure handling:**
    - If the write fails or the read-back doesn't match, no decision exists. The event stays `pending` and is retried in order; the operator sees "recording…", and after 3 failures the approval is marked `needs-human`.
    - A crash between steps 3 and 4 is repaired on startup, because the inbox reconciles against the bead.
    - Losing the journal loses only uncommitted `pending` events. Their cards are re-posted (§3.3) and the operator decides again.
  - SQLite is never the source of truth for a decision.
- **Transitions after a committed decision.** Nothing below runs unless the bead's committed `decision` field (read back in step 3) says so.
  - **`deny`** (any approval):
    - Set `action_state = denied` (for actions), or leave it unset.
    - Close the approval bead with `decision=deny`. **Nothing is executed.**
    - The task unblocks and is resumed with an explicit denial: "Operator **denied** `<summary>` (reason: `<reply text or none>`). Do not perform it. Continue without it, or stop and explain why the task can't be finished."
    - Tier 2 immediately denies a repeat of the same request in that session.
  - **`deny` on a design approval:** the bead closes without `approved_by`/`approved_at`. btq's `approval_valid()` requires those, so the design gate stays shut. No task is resumed.
  - **`approve`, non-action:** close the approval bead, and resume the task with "Operator **approved** `<summary>`. Proceed."
  - **`approve`, action:** run the execution sequence below. `wsd-act` itself refuses to run unless the committed decision is `approve`.
- **Execution** (approved actions only), with the approval bead's `action_state` updated at each step:
  1. `pending → executing`: `wsd` invokes `wsd-act run <approval-id>`. `wsd-act` re-reads the bead, recomputes `context_digest` and refuses unless it equals `decided_digest`, then revalidates the payload (for example `expected_sha` still matches, or the target is still in config) immediately before acting.
  2. `→ succeeded`: the result is recorded, the approval bead is **closed**, and the task unblocks.
  3. `→ failed`: the result is recorded, the bead **stays open**, it is labelled `needs-human`, a card goes out, and the task stays parked.
  4. `→ uncertain` (a timeout or lost response): **reconcile against the target's state** (is the branch at the SHA? is the PR merged?). That resolves to succeeded or failed; if it can't be resolved, the bead goes to `needs-human`. The action is never blindly retried.
- On resume the task is told the decision and, for actions, the result: "Approved; `wsd` performed `<action>` → `<result>`. Proceed."
- ❤️ also files a policy-rule proposal bead. The rule is never applied silently.
- The existing Hermes `/approve`, `/deny` and reaction commands are kept as the interface.
- **Interim Marmot surface (revision 14).** Until `wsd` ships, admind's interim relay (§8.2) is the Marmot surface for btq approvals. It records `via=marmot` through `approve-bead`, as a person at the terminal does, so it is not a writer inside this decision queue. Whether the relay is retired when the decision queue ships, or submits its decisions to it, is open (§17).

### 5.5 Approval resolution (GitHub or Radicle)

- **Scope:** internal repos with a configured forge, and approvals about a code change: merging or pushing to a shared branch, and design/ADR approvals (a PR against `docs/adr/`). Other approvals stay in Marmot only.
- **Linking:** the approval bead stores `forge = github:<owner/repo>#<n>` or `rad:<rid>/<patch>`. The cards link to the PR or patch, and the PR/patch body links to the bead.
- **Decisions:** the forge bridge polls every 60s (`gh api …/reviews`, `rad patch show --json`). Only allowlisted reviewer identities count, meaning the approvers' GitHub logins and Radicle DIDs from local policy.
  - **Approved** counts only if the review's commit or revision equals the approval payload's `expected_sha`/`expected_head_sha`. A review of an older revision is ignored, and a new push invalidates any earlier forge approval. Accepted reviews go into the §5.4 decision queue with `via: github|radicle`.
  - **Changes requested** and review comments become steering on the bead (§5.6).
- **Mirroring:** the first decision wins across Marmot, GitHub and Radicle, and is mirrored to the others (a reaction and reply, or a PR/patch comment).
- **Merging** remains an explicit action, performed only when an approval explicitly authorised it.

### 5.6 Steering (a reply in a thread, or a forge review comment)

- The reply is routed to that bead's thread. The gatekeeper rewrites it, and the minor/material rule from §5.1 applies.
- **Running bead:** deliver through the adapter's steer path (Claude: `tmux send-keys` in v1, channels in phase 2 (§12); Codex: `codex queue`).
- **Parked or queued bead:** attach the text as a bead comment, delivered on resume.
- The bead comment records both the original text and the rewrite.

### 5.7 Stop without close (answer and nudge)

- The Stop hook fires while the bead is still open.
- **Progress** is measured deterministically: new commits on `btq/<id>`, or a change to the acceptance-criteria checklist.
- The gatekeeper classifies the stop as a *question* (answered from the bead, repo docs and `workstream-recall`), *stalled* (it gives a concrete next step) or *done without evidence* (it asks for close evidence). Its reply is delivered through the steer path and posted in the thread.
- After 3 nudges without progress, the bead is marked `needs-human`, a question card goes to the control group, and pickup continues.
- **Pickers (revision 14, §8.1).** A question asked through a CLI's interactive question tool would show only in the terminal. Where the pre-tool hook runs (Claude Code; Codex once verified, §4.2), it denies that tool with "ask the question as plain text and end your turn", so the question arrives at a Stop and follows this flow, ending as a question card if the gatekeeper can't answer it. Sessions run with permission prompts bypassed (§5.3), so they raise no permission dialogs.

### 5.9 Approval ask requirements (context sufficiency and evidence chain)

**An approval is valid evidence only for content that was pinned and shown.** This applies on every surface: Marmot, the CLI and the forge.

- **Required content of every ask:**
  1. *What*: one plain sentence.
  2. *Exact effect*: the typed payload for actions (§5.3). For design or other decisions, what approving authorizes, and what it explicitly does not.
  3. *Why*: why it's needed, and why it escalated.
  4. *Context*: enough to decide without opening anything else, for example a diff summary, the key decisions, the risks being accepted, and the alternatives.
  5. *Pinned references*: each given by an immutable identifier, and rendered as a clickable permalink wherever a forge exists, otherwise as an inline excerpt or an `approve-bead --doc` pointer.
     - Allowed: a commit plus path, a PR or patch **head SHA**, a `BASE..HEAD` range, or a bead ID plus its `context_digest`.
     - Floating references are rejected: branch names, "latest", unpinned URLs.
- **The ask is one immutable metadata object,** `metadata.ask`. It is written once, when the ask passes the gate, and holds:
  - `effect`: the typed payload, or the scope that approving authorizes;
  - `excludes`: what approval does not authorize;
  - `why`;
  - `risks`;
  - `refs`: a list of `{kind: commit|file|pr|range|bead, repo, id, path?}`.
  - Grooming or enrichment replaces the whole object, and that means a new digest and a new card.
- **`context_digest`** is SHA-256 over canonical JSON (sorted keys, UTF-8, no insignificant whitespace) of exactly these fields:
  - `title`, `description` and `ask`;
  - `resolved`: each ref mapped to its content ID. That is the git blob or tree ID at the pinned commit, the PR or patch head SHA, the `BASE..HEAD` pair, or a linked bead's own `context_digest`.
  - **Excluded:** every other metadata key, including `context_digest` itself, all decision fields (`decision`, `decided_*`, `approved_*`, `denied_*`, `via`, `event_id`), `action_state` and results; and all labels, status, timestamps, comments and assignee. So the digest isn't self-referential, and workflow state changes don't invalidate it.
  - `wsd` computes it when the card is posted, stores it on the approval bead, and shows its first 12 characters on the card.
  - A decision records `decided_digest`. The decision queue (§5.4 step 3), `wsd-act` and btq's design gate each re-verify it. Any change to the ask's content after it was shown invalidates the approval, and the ask is re-issued.
- **Context sufficiency gate** (before any card goes to a human):
  1. **Deterministic lint:** all five parts are present, every reference is pinned and resolves, the description is not a stub, and the action payload validates against the registry.
  2. **Gatekeeper judgement:** "could the operator decide from this card alone, without guessing?" It returns sufficient, enrich or groom.
  3. **Enrich:** if the gatekeeper has the missing context (bead, diff, ADR, session transcript), it adds it, marked as gatekeeper-added, and the lint runs again. The digest covers the enriched content.
  4. **Groom:** if it doesn't have the context, the ask goes **back to its originator**, not to the operator. That's the agent session (as steering, which counts toward the nudge limit in §5.7), or the bead's creator. It is labelled `needs-grooming` with the specific gaps listed.
  5. If grooming fails twice, the ask goes to the control group as a **"cannot approve as written"** notice, with deny as the default and the gaps listed. There are no 👍 buttons: the operator can deny it, or groom it themselves.
- **The gatekeeper never auto-approves** an ask that failed the lint.
- **Binding the approval to what gets implemented:** a digest proves the ask didn't change. The design gate also has to prove that the work uses the approved revision:
  - A design approval's `ask.refs` must include the ADR as `{kind: file, repo, id: <commit>, path}`. Its `adr_revision` must equal that commit.
  - Every implementation bead carries `design_approval` and `adr_revision`. The gate requires a valid digest, and the task's `adr_revision` to equal the approval's pinned commit (btq already checks this equality). A bead that cites a revision without an approval fails.
  - **Any change to the ADR after approval needs a new approval** before a bead may cite the new revision. The old approval keeps authorizing only the old revision.
  - The code review brief (§5.8) includes the ADR **at `adr_revision`**. A reviewer finding that the implementation departs from it is blocking.
- **Interim exception for admind's relay (revision 14; relay spec 2026-10-05 and its 2026-10-06 delta).** Until `wsd` and the gatekeeper ship, approval asks posted through admind's relay (§8.2) skip the gatekeeper's sufficiency judgement and enrichment. Instead:
  - btq's deterministic lint (`approve-bead` gaps, `design_review` validity, a posted `context_digest`) gates posting (R4, R5);
  - the whole hashed content is shown, never shortened, and a bead whose content admind would redact, or that is over the size limit, is not posted: it goes back to its originator for grooming, and is decided at the terminal only if it still can't be shown (R8, R21, §8.1 host exception 3);
  - refs get pinned forge permalinks where the repo has a forge remote, and are otherwise marked "read on the host" (R25);
  - an explicit approve action on admind's own card (an approve reaction, or a reply that is exactly an approve word) takes the place of the terminal's confirmation (R7, R27, R28). The full digest stays pinned: admind stores it when it renders the card and passes it to `approve-bead --expect-digest`, so an approval binds to exactly the content on the card.
  - The operator accepts that a phone approval rests on their own reading, without a gatekeeper verdict, and that an accidental 👍 on an approval card records an approval (operator, 2026-10-06). The exception ends when `wsd` and the gatekeeper ship: from then on, the sufficiency judgement is **mandatory** for asks posted through the relay, as for every other card. Enforcement starts in the release that ships both `wsd` and the gatekeeper. Before that release, the gatekeeper may run in shadow on relayed asks: its verdicts are recorded and shown, but they decide nothing. From that release on, a relayed approval without a passing sufficiency judgement is refused. There is no interval of running both. A permanent exemption for the relay, or for any ask kind, would weaken the approval gate. It therefore needs its own security change, separately scoped and reviewed, and a new revision of this ADR; no implementation may decide it.

### 5.8 Review and close

1. The coder runs `ws close <bead> --evidence-file`. `wsd` runs the gates.
2. If the workstream has a `reviewer` role, the bead moves to `review`. A reviewer session starts in the same worktree and sandbox profile, **read-only**.
3. Findings are recorded as bead comments, and as PR review comments if there is a PR.
4. Blocking findings become steering for the coder, with at most 3 rounds, then `needs-human`.
5. When review passes, `wsd` checks that the bead branch HEAD still equals the reviewed HEAD. If code changed after review, the review is re-run. Then `wsd` runs `btq close` with the generated `Code-Review:` line. The task card reacts ✅ with a one-line summary, dependent beads unblock automatically, and pickup runs.

## 6. Marmot interface

### 6.1 Topology

- **One group per workstream.** Each bead gets a root task card (short bead ID, title, agent and role, branch, one-line state) and a thread for everything about it.
- **An unthreaded message** in a workstream group becomes a new bead (§5.1). **A threaded reply** steers or approves.
- **The control group** receives mirrored approval cards, question cards (`needs-human`, material confirmations), the daily digest, and all-blocked notices. Approvals work from either group.
- **Mentions** of the operator appear only on approval and question cards in the control group. The operator npub comes from config, never from a live lookup.

### 6.2 Rendering rules (golden-tested)

These rules govern `wsd`'s messages. `admind`'s output follows §8.

- **No internal IDs** other than a short bead ID. No UUIDs, hex strings, pane dumps or instructions meant for agents. There are two exceptions, both on approval cards: the 12-character `context_digest` (§5.9), and short commit SHAs inside pinned permalinks.
- **An approval card** renders the five required parts of §5.9: *what*, *exact effect* (in a code block), *why*, *context*, and *pinned links* as permalinks. It then adds *how to answer* ("👍 approve · 👎 deny · ❤️ always · reply to steer") and the short `context_digest`. If the context is too long for a card, the card shows a summary plus pinned links, and the full text lives on the approval bead, which the digest covers.
- **Length:** at most about 8 lines per message. Detail goes in `/status <bead>`.
- **Task-card reactions:** 👀 received, ⏳ running, 🔍 in review, ⏸️ parked or deferred, ❓ needs operator, ✅ closed, ❌ failed.
- **Deferred beads (revision 14):** the task card's state line gives the current deferral's reason. For "quota" it gives the resume time. For "account changed" it gives the recovery condition (restore the account's login or order, then a configuration reload or the operator's release) and no time.
- **Held beads (revision 14):** the task card's state line says the bead is held and why (an operator `/stop`, or the escalation reason), and that only a release resumes it.
- **Reminders:** one reminder after 4h, as a reply to the original card, without repeating its content. After that, only the daily digest. This rule is `wsd`'s; admind's interim relay has its own bumps (§8.2, B14).
- **Delivery that can't be confirmed:**
  - If no surface has a confirmed delivery receipt for an approval or question card within 10 minutes, `wsd` raises a delivery alert in the control group.
  - If that also can't be confirmed, `wsd` writes a local alert file. `admind` watches the alert directory on its own and pushes it to its operators, ahead of any `!details` output (§8). This path doesn't depend on `wsd` or the gateway's Marmot connection.

### 6.3 Commands (deterministic, target reply under 1s)

| Command | Output |
|---|---|
| `/workstreams` | One line per workstream: state (running, idle, all-blocked, deferred or paused), current bead, and ready, parked, held, deferred and approval counts. Each role's headroom (for example "coder 62% · reviewer unknown"), and "deferred until HH:MM" or "deferred: account changed" when relevant (revision 14). |
| `/status` | In a workstream group: the running bead and its duration, the ready queue, parked, held and deferred beads with reasons, open approvals. In the control group: `/workstreams` plus every open approval and `needs-human` item. Revision 14 adds a section listing each account in use by name, its windows, their reset times, the age of each observation and whether it is trusted. |
| `/status <bead>` | State, branch, session label, the last 5 events, decisions, the transcript path, and the account of each launch entry (revision 14). |
| `/approvals` | Open approvals, oldest first. |
| `/approve`, `/deny` (as a reply) | Resolves the targeted approval bead. |
| `/pause`, `/resume` `[ws]` | Stops or restarts **new claims** (revision 14; plan 3 decision (d), matching §4.3). The running task and parked beads continue; a parked bead whose blockers close still resumes. Use `/stop` to stop a running task. |
| `/stop <bead>` | Parks the bead immediately as **held** (§4.3): interrupt, then commit the WIP. It needs no blocking edge, and only the operator's release resumes it. |
| release `<bead>` (revision 14) | The operator's release (`Parker.release`, §4.3): ends a hold or an escalation, and re-gates an `account_changed` deferral. Plan 6 names the command (§17). |
| `/queue [ws]` | The ready list in pickup order, with blockers. |

- Commands are answered from the `wsd` cache, which is refreshed from beads if it is more than 30s old.
- If beads is unreachable, the answer comes from the cache with "⚠️ beads unreachable, data as of HH:MM".

## 7. Sandboxing

- **v1:**
  - A workstream-level, platform-neutral `sandbox.toml`: writable paths (worktree, tmp, package caches), read-only binds, and a network allowlist.
  - It is compiled at launch into the sandbox runtime's own policy, and wraps whichever agent CLI is launched. The runtime is OpenShell on Linux (spike S5 passed; revision 15), with bubblewrap as the fallback; on macOS it is a Seatbelt profile (phase 2). See **Runtime** below (revision 14). The rules in this section say what a sandbox can reach; each runtime must enforce them, and the self-test proves it does.
  - Coder and reviewer share one profile; the reviewer's worktree bind is read-only.
  - Inside the sandbox both agents run fully permissive: Codex `--yolo`, Claude `bypassPermissions`. Neither agent's native sandbox is used, except `-s read-only` for a headless `codex exec` run (§5.3).
  - The sandbox is the only security boundary (§5.3). **The only credential inside is model auth.** There is no git push, forge, deploy, messaging or secret material.
  - **Environment:** the launcher clears the environment and sets only an explicit allowlist (locale, terminal, user, `HOME`, `PATH`, the session socket and the proxy settings). The host environment can carry tokens and socket paths, and it changes CLI behaviour. A self-test probe enforces this.
    - **The runtime's injected set (revision 15).** The allowlist also holds the variables the runtime itself injects, none of which may hold a secret. Under OpenShell these are `OPENSHELL_SANDBOX`, `OPENSHELL_USER_ENVIRONMENT` (whose keys are checked too), its CA variables (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS` and similar), `container`, `HOSTNAME`, `DEBIAN_FRONTEND` and `SHELL`. It also holds the shell's own `PWD`, `SHLVL`, `_` and `OLDPWD`. The host environment is never forwarded.
    - **The agent's tool path (revision 15).** The agent CLI adds variables to the environment of the tools it runs. The allowlist for that path lists them per pinned CLI version. Before every launch the launcher checks that the CLI is the pinned version, and refuses the launch otherwise.
  - **Home isolation:** the sandbox gets a synthetic `$HOME` containing only the agent's config and auth files. The **auth files are read-only**, and only those of the account chosen for this launch (revision 14, §4.4 D7). Before the launch, the compiled sandbox policy is checked: every credential-bearing source must be in the chosen account's login file set, and a source in any other account's login directory (the adapter's default login included) refuses the launch. Claude Code finds its config through `CLAUDE_CONFIG_DIR` and Codex through `CODEX_HOME`, both pointing inside the synthetic home, so a session's state stays in its synthetic home whichever account it runs under. The rest of the synthetic home is a **writable per-session copy**, because both CLIs write session state there. The real home (including SSH keys, forge CLI tokens and the queue client's credentials), other workstreams' worktrees, and the `wsd` journal are not reachable.
  - **Shared refresh token and the launch freshness gate:** the auth files hold an operator login, including the refresh token that the host shares. A refresh inside the sandbox could rotate that token and break the host login, and the read-only files would stop the rotated token from being saved. So before every launch the launcher checks the chosen account's access token's expiry. Refreshes the harness starts are serialised by a host lock per credential identity, and each rereads the login files under the lock (revision 14, §4.4 D8). If it expires within the session's maximum lifetime plus a stop margin, the launcher refreshes on the host first, or refuses to launch. A session that reaches its maximum lifetime is stopped: at a turn boundary if one comes within the stop margin, otherwise by a hard interrupt, as `/stop` does (interrupt, then commit the WIP, §6.3). Either way it stops before the token expires. It is then relaunched at once as the same session (§4.1) through the gate. This is not §4.3's blocked parking: the bead stays claimed and in progress, with no blocking edge. If the gate refuses the relaunch, the bead becomes `needs-human`. Per-sandbox credentials are future work.
    - **A single-file read-only bind pins the inode (revision 15, S5).** Inside the sandbox, a write gives `EROFS` and a rename gives `EBUSY`, which is what "no refresh inside" needs. A host refresh that replaces the file by rename is not seen by a running session. The gate's lifetime bound covers that.
  - **Model credentials may reach account connectors:** the model OAuth token probably also reaches the account's MCP connectors (for Claude, through `mcp-proxy.anthropic.com`; for Codex, under `chatgpt.com`), which can include mail, chat and drive. S3 inferred this from the traffic; what each connector can do from the sandbox was not tested. The proxy denies `mcp-proxy.anthropic.com`, and the Claude adapter config disables claude.ai MCP servers. The Codex adapter config disables connectors, because the proxy can't filter paths on `chatgpt.com`.
  - **Network:** an allowlist enforced by a per-sandbox egress proxy, not just DNS. It names **exact hosts** per adapter (no suffix wildcards), and covers model endpoints, package registries and the git remote over read-only fetch.
    - **Under bubblewrap** (S3's proxy), the proxy checks only the host in the CONNECT request. It does not check TLS SNI or the authority inside the tunnel, and it can't see paths. It is a host-level boundary, not a TLS-peer or path boundary.
    - **Under OpenShell (revision 15, S5)**, the proxy terminates TLS with a per-sandbox CA and originates its own TLS to the authorized endpoint, whatever SNI the client sent. It refuses a request whose HTTP authority is not that endpoint (`request_authority_mismatch`). So authority and SNI are bound to the authorized endpoint. Paths are still not checked: it is a host-level boundary, not a path boundary.
    - **Per-binary grants cover the process tree (revision 15, S5).** OpenShell's egress policy is per binary, and it matches the executable that owns the socket *or any of its executable ancestors*. So the agent CLI's grant to its model host covers every tool the agent runs (curl, git, python). The allowlist is therefore enforced per adapter process tree, not per tool. OpenShell's per-binary policy alone can't stop a tool from reaching the model host (§17).
  - **Usage observations (revision 14)** that reach `wsd` from inside the sandbox (the in-session Codex app-server, a Claude status line, a transcript or a hook) are untrusted. They are attributed to the launch that delivered them, and they can only defer that session's own bead, for at most `usage.untrusted_max_defer_minutes`. Marking an account exhausted for other sessions, moving a launch to another account, and treating a reviewer as unavailable all need a trusted read made on the host (§4.4 D3, D6).
  - **Session socket, not the `wsd` socket:** each session gets its own socket, bound in, authenticated by a per-launch token. It accepts only that session's hook events and `ws-request` calls, and exposes **no** control operations: no approve, no config, no other sessions.
  - **Codex app-server:** the per-session `codex app-server` that `codex queue` needs (§4.2) sets the session's working directory, and is inferred to execute the tool calls. So it runs **inside** the sandbox, not just the `--remote` TUI client. Its queue socket, which it binds from inside, is the Codex steering surface at the boundary, on a writable path of that session's own bridge directory. The launch self-test runs against this launch shape, not against a bare `codex` process.
  - **Residual risk, accepted and documented:**
    - A compromised agent could exfiltrate its own model credentials, or repository content, through allowlisted endpoints. In v1 those credentials are the operator's own login (per-sandbox credentials are future work).
    - Under bubblewrap, because the allowlist checks only the CONNECT host, a client can present a different SNI behind an allowed host, which matters for shared front ends and CDNs. Under OpenShell this does not hold for HTTPS through the proxy (Network, above; revision 15).
    - Under OpenShell, any tool the agent runs can reach its adapter's model host (Network, above; revision 15, §17).
    - Under OpenShell, the supervisor terminates TLS and sees plaintext, including the bearer token in model traffic. So OpenShell's supervisor is inside the trusted computing base for credentials. This weighs more if proxy-side credential injection is adopted (§17; revision 15).
    - The proxy's logs record CONNECT hosts only, not paths or content, so they can't show *what* was sent to an allowlisted host.
    - Codex connector traffic shares a host with model traffic, so only adapter config, not the proxy, keeps it off.
  - **Launch self-test (mandatory, fail closed):** it runs before **every** agent launch, including resumes and relaunches, and a failing or skipped self-test refuses the launch. Each probe must prove the *specific* enforcement, because a generic failure (a missing canary, a dead proxy, a timeout) looks like enforcement.
    - **Two execution paths (revision 15).** Per-binary egress gives a fresh process and the agent's own tools different policy (Network, above), so the probes run twice. They run first in a fresh process inside the sandbox before the agent starts. They run again through the agent's own tool path (Claude's Bash tool, the Codex app-server's exec) as the session's first input. The session takes no work until both pass, and a failure on either path stops it. A cached result never counts: every launch reruns the whole self-test.
    - **Real home:** a canary file in the real home is checked readable *outside* the sandbox immediately before the launch (otherwise the self-test aborts), and inside it gives ENOENT or EACCES.
    - **Egress:** a non-allowlisted host is refused *by the egress proxy*, shown by its marker in the refusal and its log entry, and a paired allowlisted-host control succeeds, so a broken proxy fails the self-test.
      - **Under OpenShell (revision 15)** there is no CONNECT refusal to carry a marker. The specific evidence is that the host resolves through OpenShell's policy DNS to its synthetic range (198.18.0.0/15), and `connect()` gives `EACCES`. The supervisor's log also has this run's denial line for it (`DENIED ... transparent_tcp_policy_denied`). The allowlisted control succeeds through the proxy, shown by the OpenShell sandbox CA as the certificate issuer and by this run's `ALLOWED` log line.
      - **Model host by path (revision 15, OpenShell):** the adapter's model host is refused on the fresh-process path and reachable on the agent path. Each result is shown by its supervisor log line, which also proves which path ran the probes.
    - **Direct network:** namespace evidence shows no route: no interface except loopback, no IPv4 route, and IPv6 routes on loopback only. Beyond that, the proof depends on the runtime (revision 15):
      - **bubblewrap:** a raw connect to a literal address fails as unreachable.
      - **OpenShell:** OpenShell's seccomp network broker traps every INET `socket()`, `connect()` and `sendto()`, and answers before the kernel routes, so unreachable is never observable. The proof has three parts, all required:
        - the broker's specific answers: `EACCES` for TCP connects to literal IPv4 and IPv6 addresses and for a UDP connect; `EDESTADDRREQ` for a UDP `sendto` to a non-DNS address, accepted only together with that UDP `EACCES`; and `EPROTONOSUPPORT` for raw and ICMP sockets;
        - inside the sandbox, `Seccomp: 2`, `NoNewPrivs: 1` and an empty effective capability set;
        - from the host, the workload container's `NetworkMode=none`, the kernel fence under the broker, and the supervisor's log line for this run's literal-address denial.

        Any other errno fails the probe, including the kernel's unreachable, which would mean the broker is absent.
    - **Control op:** a control operation on the session socket gets the socket's explicit `forbidden` reply.
    - **Environment:** no variable outside the launcher's allowlist is present, on either path (revision 15). The launcher also reads the agent-path probe's environment from the host.
    - **Runtime control material (revision 15, OpenShell):** the workload can't read the supervisor's channel, TLS key or token (ENOENT or EACCES).
    - **Other accounts (revision 14):** immediately before the launch, the launcher classifies every login file of every other account of the adapter (the default login included) on the host as *present* (readable), *absent* (its directory is readable and lists no such file) or *unknown* (anything else). Inside the sandbox, each present or unknown file, opened through both its configured path and its canonical path, must give ENOENT or EACCES; an absent file must give ENOENT. Absent files are never a reason to refuse, so a missing default login or an unused account doesn't stop a valid launch. So that the check can't pass vacuously, a **canary** file that the launcher writes on the host, outside every mount, just before the launch must be readable outside and give ENOENT or EACCES inside; and every login file's content inside the sandbox must match the chosen account's file on the host.
    - **Result channel (revision 15):** the launcher accepts probe results only through a channel it owns, never from anything the agent can write. On the agent path it verifies each peer from the host. It checks:
      - the peer's credentials from the socket (`SO_PEERCRED`);
      - its executable and exact argv;
      - that it is untraced;
      - that it is in the workload's network namespace;
      - that its environment is within the allowlist;
      - that it has an ancestor in that namespace which is the adapter's CLI binary.

      It checks again when the run reports done. It accepts exactly one verified run with every agent-path probe present and passing. A rejected peer, a missing probe or a timeout fails the self-test.

      These checks identify the process. They do not establish the integrity of its result. A process in the workload could change the probe between two checks, for example by attaching with ptrace, changing its code, data or socket, and detaching. It could also overlay `/run/hz` with a mount if the sandbox allows unprivileged user namespaces. S5 did not test either attack. Checking the probe's memory maps or mount information would not close this either, because neither check stops the probe's data or socket being changed.
    - **Probe protection (revision 15; a mandatory plan 4 acceptance condition).** The agent path's result counts only if the agent can't modify the probe's code, configuration, memory or result channel at any point while it runs. Plan 4 must meet this in one of two ways:
      - run the probe on a path the supervisor controls, which still gives it the adapter's CLI as an ancestor, because the per-binary policy depends on that ancestry; or
      - deny the workload every way to tamper with the probe during its run (ptrace, `/proc/<pid>/mem`, mounts over the probe's files), and have the self-test check that denial each time. Candidates include a Yama `ptrace_scope` setting and a probe the workload's processes can't trace; S5 measured none of them.

      Each mechanism needs the adversarial controls of §11 to pass. Until plan 4 meets this condition, no session takes work on the strength of an agent-path result (§17).
    - S3's acceptance criteria were the probes above it on both backends (the Other accounts probe is new). S5 demonstrated all of them under OpenShell, the direct-network probe in its OpenShell form (revision 15). One exception: S5 showed that a result the agent only writes or sends is refused, but it did not show that the probe resists tampering while it runs. That is plan 4's Probe protection condition (above).
  - **Still to verify for v1 (implementation plan 4):** egress for package registries and read-only git fetch, and the reviewer's read-only worktree bind. S5 verified the self-test, the login and hooks against the managed Codex launch shape, with the app-server inside the sandbox (revision 15). Plan 4 still has to establish Codex hook trust without an operator step (§4.2). S5 did not cover these either (revision 15):
    - OpenShell's proxy-side credential injection (§17);
    - macOS;
    - the cause of Codex's non-fatal warning that it couldn't save diagnostic logs to its local database.
- **Why an outer sandbox:** a single boundary to review means swapping agents never changes the security posture. The agents' native sandboxes have different semantics.
- **Runtime (revisions 14–15): OpenShell. Spike S5 passed (revision 15).**
  - **Decision.** The v1 sandbox runtime on Linux is NVIDIA OpenShell (Landlock, seccomp, network namespaces, a per-binary egress policy, credential injection at its proxy). S5 passed on the reference host (revision 15): items 1, 3, 4 and 5 as specified, and item 2 with the direct-network probe in the OpenShell form defined above. The operator prefers it over bubblewrap, Seatbelt and CubeSandbox because one policy model could cover Linux, macOS and later WSL. Only Linux is decided here; macOS stays phase 2 (§12).
  - **The rules above, in OpenShell's terms.** The `sandbox.toml` paths become its filesystem policy: the worktree, tmp, package caches and the per-session synthetic home writable; the chosen account's login files, and the reviewer's worktree, read-only; nothing else reachable. The network allowlist becomes its egress policy, with exact hosts per adapter, and no direct route. The session socket (and, for Codex, the app-server's bridge directory and its socket directory, §4.2; revision 15) is the only host socket reachable. Nothing is weakened by the translation: every rule above applies unchanged, and the self-test proves each one under OpenShell.
  - **Credential injection is not used for model auth in v1.** The login files stay the only credential in the sandbox, read-only, under the freshness gate and the account rules (§4.4), because S7 and the refresh-token rules are written for file-bound logins. Moving model auth to proxy injection is open (§17).
  - **The residual risks above are narrowed for OpenShell (revision 15).** S5 showed that its proxy binds the HTTP authority and upstream TLS to the authorized endpoint (Network, above), so the SNI risk does not apply to HTTPS through it. The other risks stand. Two are added: tools reaching the model host, and the supervisor inside the credential trusted computing base.
  - **S5 had to demonstrate, on the reference host** (§13), and did (revision 15):
    1. both CLIs in the managed launch shape under OpenShell: Claude Code interactive with its hooks; Codex interactive with its per-session `codex app-server` inside the sandbox and `codex queue` steering from outside; each started from a host tmux session (§4.2), so send-keys steering and break-glass attach still work;
    2. every launch self-test probe above, the Other accounts probe included, each with its specific evidence, and a failing probe refusing the launch (demonstrated with the direct-network probe in its OpenShell form; revision 15);
    3. a working login with read-only login files and no refresh inside the sandbox;
    4. hook events and `ws-request` reaching the per-session socket, and the control-op refusal;
    5. a runtime driver that works on the reference host. The first attempt, OpenShell v0.1.2's VM driver, failed inside the guest (2026-09-30: `EACCES` reading `/proc/self/status`); its container driver needs a newer podman than the host has. Any host change this needs (for example a podman upgrade) is an operator decision (§17). Demonstrated with OpenShell's podman compute driver (revision 15). It ran on a user-level podman 5.8.8 that serves only the service user's socket, installed alongside the system podman, which was left unchanged (operator decision, 2026-10-08).
  - **Plan 4 notes for OpenShell (revision 15, from S5).** These are not rules; they record what plan 4 has to handle.
    - Sandbox names are limited to 19 characters, so the launcher needs a short naming scheme.
    - `sandbox delete` is asynchronous, so teardown waits until the sandbox is gone.
    - The supervisor's log buffer holds only a few hundred lines. The launcher reads its self-test checks right after the probes. The buffer is not an audit log; `wsd` journals what it needs.
    - `sandbox exec` must be given its stdin explicitly, never an inherited one, or it can hang. A command without input gets `/dev/null`. The probe configuration, which holds the session token, travels on a pipe the launcher owns, never on argv.
    - The workload runs as the host uid and gid (podman `keep-id`). A host file owned by the service user is readable inside if it is mounted, so ownership and mode bits give no protection: only the mount table keeps a file out. The real-home and Other accounts probes are what check this.
    - The gateway needs bind mounts enabled and its resource admission disabled, because admission rejects bind mounts.
    - S5's lifetime stop was a hard stop at the maximum (interrupt, then delete). The turn-boundary stop within the margin, and the relaunch through the gate (above), are the launcher's to build.
  - **Fallback: bubblewrap (revision 15).** Three cases are kept apart:
    - **The operator explicitly rejects** this revision's OpenShell form of the direct-network probe. S5 then counts as failed, and the next bullet applies. This is the only case that selects bubblewrap.
    - **This revision is still pending,** including while it is revised for any other defect. Plan 4 doesn't start, and nothing falls back.
    - **Plan 4 finds that OpenShell can't enforce a rule of this section** on the reference host (for example in the items still to verify, or the Probe protection condition). Plan 4 stops, and the choice goes back to the operator; nothing falls back automatically.
  - **If the operator rejects the direct-network proof** (S5 then counts as failed; this is the only trigger): only the Linux runtime selection falls back to revision 13. v1 on Linux uses bubblewrap, which must still enforce every rule of this section and every other rule of this revision that applies to the runtime: the synthetic home and its isolation, the read-only login files and freshness checks, the account rules of §4.4 (pinning, credential keys, the Other accounts probe among the launch self-test probes) and the per-session socket's control-op refusal. Nothing else in this revision changes. Plan 4 builds bubblewrap from the S3 prototype, and OpenShell is revisited in a later revision. S5's findings are recorded either way. Plan 4 (the sandbox runtime) starts only once S5 has either passed or been closed as failed. S5 has passed, so plan 4 starts once this revision is approved (revision 15).
- **v2 (future):** a `CubeRuntime` (TencentCloud CubeSandbox microVMs, Linux/KVM only) behind the same interface, with credentials injected at the egress proxy and a snapshot on park.

## 8. Admin override channel (`admind`)

- **Independent:**
  - Its own service unit, sharing no dependency on `wsd`, the Hermes gateway, beads or the gatekeeper.
  - Its own Marmot identity and connection, in one MLS group of the operators plus the admin bot.
- **Operators (revision 13):**
  - Every entry in `policy.toml`'s `operators` with a Marmot npub is an admind operator. There may be several. admind reads `policy.toml` itself, with no dependency on `wsd`.
  - **Ingress authentication** (as §3.4, but bound to admind's own group, which is not a `wsd`-registered group): admind processes a message only if its MLS-authenticated sender key, reported by `wn-agent` and never parsed from text, is an operator's; it arrived in admind's configured group; and its message ID has not been seen before. Anything else is dropped and logged, with no reply. The audit names the operator who sent each message.
  - The join signal (plan 2, D5) is the first message from any operator.
  - All operators share the group, see everything, and may each send messages and `!` commands.
- **Group membership (revision 13):** `wn-agent` reports a member *count*, never a member list, and its membership events never name the member (S4). So membership is controlled, not inspected:
  - `admind init` creates the group with admind and every operator. admind is the group's only admin.
  - Members change only through the host command `admind operators add NAME` or `admind operators remove NAME`. For `add`, NAME must already be an operator in `policy.toml`. The command asks the running daemon, which makes the change through its own connection. admind's own changes produce no events for itself (S4), so they don't latch it.
  - **A membership change is a journaled transition:**
    1. The daemon serializes it with every guard check and holds dispatch and outbound posting until it ends.
    2. It journals the pending change with its `from` and `to` counts. The trusted expected count stays `from`.
    3. It makes the change and waits for `wn-agent`'s reply, then reads the group's member count. Membership events keep being processed throughout; since admind's own change produces none, any event during the transition latches.
    4. It commits only if `wn-agent` reported success **and** the count equals `to`: `to` becomes the trusted expected count and the pending record is cleared. It aborts only if `wn-agent` reported failure **and** the count equals `from`: the pending record is cleared and nothing else changes. Every other outcome (a timeout, a lost reply, or a count that disagrees with the reported result) latches.
    5. A pending record found on startup latches before anything else runs, because a count alone can't show which change happened. Count alone never confirms a change.
  - **`admind rearm`** is the host recovery for every latch. The operator checks the group's members in their own client, then runs `admind rearm`, which takes the current member count as trusted and clears any pending record.
  - Plan 2b first verifies that `wn-agent`'s control socket can add and remove group members. If it can't, the operator set changes only by creating a new group with `admind init`.
  - **Latch:** any membership or admin event (necessarily a change made by someone else, such as an operator leaving), or a member count other than the expected count, latches admind until `admind rearm` on the host. A latched admind posts nothing and acts on nothing.
  - **Residual risk:** something running as the service user could swap a member through admind's control socket while keeping the count, undetected (as in §3.4).
  - **Temporary operators:** a test identity is added as a temporary `policy.toml` entry, added with `admind operators add`, and removed with `admind operators remove` when the test ends.
- **Passthrough:**
  - Operator text goes byte-for-byte into a persistent interactive session of the `admin` profile (initially `claude-opus`; any adapter can be configured).
  - The agent runs as the harness's service user (the operator's login user on the reference install) with permission prompts bypassed, no sandbox, and no root. **This is a deliberate operator decision** (2026-09-29): its purpose is to repair anything the harness can break, so a least-privilege identity would defeat it. The review's objection is recorded and rebutted in the r1 response.
  - **Mitigations that keep the purpose intact:**
    - the sender must be MLS-authenticated as an operator's npub (§3.4);
    - the controlled membership and the latch above;
    - admind's own Marmot identity keys are readable only by the admind unit;
    - every message and action goes to the append-only log.
  - **Accounts (revision 14, §4.4 D11):** the admin agent and the summarizer each run on the first account of their own profile, if it lists any (`CLAUDE_CONFIG_DIR` or `CODEX_HOME` set to that account's `login_dir`). They ignore the headroom gate and never fail over.
- **Redaction (revision 13):** one redaction applies to everything admind posts (verbatim replies, summaries, batches and both `!details` modes), to the summarizer's input, and to the audit: secrets, npubs and 64-hex values are replaced by markers, and control characters are escaped. "Unabridged" and "verbatim" below mean nothing is omitted or reworded apart from those markers. The unredacted text stays in the agent transcript on the host.
- **Replies (revision 13):** each reply is the agent's text for the turn (the `Stop` hook's `last_assistant_message`, or the assistant text blocks of the transcript as a fallback). Thinking and tool calls are never part of a reply. A reply is threaded to the operator message that started its turn.
  - **Short replies are sent verbatim:** up to `reply_verbatim_lines` lines (default 8) and `reply_verbatim_chars` characters (default 800).
  - **Longer replies are summarized** by the configured `[admind] summarizer` profile, run headless, read-only and without tools, with a 60-second timeout. Its input is the reply after the audit redaction (secrets, npubs and 64-hex values). The summary is at most about 8 lines, quotes verbatim any question the agent asks and any error it reports, and ends with "summary · reply `!details` for everything". The summarizer is admind's own subprocess; it uses no `wsd`, gatekeeper or Hermes component, so admind stays independent.
  - **Backstop:** if the summarizer is not configured, fails, times out or returns nothing, or any other step of the reply pipeline fails, the affected replies go to the backstop:
    - A batch opens with the first affected reply and closes 60 seconds later. Replies affected meanwhile join it in order.
    - Each reply in the batch is headed by its origin: the sending operator, the time, and the first words of the operator message that started its turn.
    - Runs of identical consecutive lines are collapsed to one line marked "(×k)". Line counts are taken after this collapse.
    - A batch of 50 lines or fewer is sent whole. A longer one is sent as the first 10 lines, a line "… N lines skipped …", and the last 40 lines.
    - The batch is one message, posted unthreaded because it can answer several operator messages. This is deterministic and drops nothing that `!details` can't return.
  - **`!details`**, as a reply to a summary or batch message (or, without a reply target, for the latest one), sends the full reply text, unabridged, chunked at `chunk_chars`. For a batch it sends every included reply, in order, each headed by its origin.
  - **`!details full`** also sends the tool calls and their results of each included turn, read from the transcript. Thinking is never sent.
  - admind keeps the record behind each summary and batch (which turns, which replies) so `!details` works after a restart.
  - **Delivery lanes:** the outbox has two lanes. The first carries command replies, alerts, notices, summaries, batches and verbatim replies, in order. The second carries `!details` chunks, in order, and only when the first lane is empty. There is no cap on `!details` length, so urgent messages are never stuck behind a long one.
- **Built-in commands (no LLM, `!` prefix):**
  - `!new` starts a fresh session
  - `!interrupt` sends Esc
  - `!tail [n]` shows the pane tail
  - `!restart <unit>` restarts a unit from a fixed allowlist in admind's config: `wsd`, the Hermes gateway, and runner units
  - `!ps` shows unit health, plus one line per active hold (revision 14, §8.1)
  - `!status` shows the agent's state and every active hold with its reason (revision 14, §8.1)
  - `!details [full]` (above)
  - `!asks [bump|repeat]`, `!answer <id> <text>`, `!approve`, `!deny` (interim relay, §8.2)
- **Audit:** an append-only local JSONL log plus the agent transcript, kept separate from beads on purpose. Each operator message is logged **in full** (revision 13: no truncation), with the redaction above and control characters escaped, together with the timestamp, the sending operator and the action. Operator add and remove, latches, summarizer failures and backstop batches are logged too.
- **Alert relay:** admind watches `wsd`'s local alert directory and pushes new alerts to the operators (§6.2).
- **Build order:** `admind` is built early, right after the spikes, so every later step has a recovery path.

### 8.1 Marmot-only operation (revision 14, btq-xv48a)

**Rule.** In operation, Marmot is the operators' primary path. Everything the admin agent or a managed session waits on reaches them on Marmot, and they answer it there.

**Recovery by originator grooming.** When something can't be shown safely on Marmot, the first recovery is grooming by whoever posted it, not the host. An ask that fails redaction (R21) or the size limit (R8, `TOO_LONG`), or a picker that would (below), is refused back to its originator with the reason. The originator rewrites it so that it passes: secrets replaced by refs, and refs pinned so that they survive redaction (forge permalinks at a fixed SHA, R25). Then they post it again; for an approval that means a changed bead and a new digest. Content is never trimmed, partly shown, or shown unredacted to make it fit. Redaction (R18, R21) and the rule that an approval shows its whole hashed content (§5.9, R8) are unchanged.

**Host exceptions.** The host remains necessary for these, and only these, each deliberate (§17 asks the operator to confirm the list):

1. **Install and setup**, including the startup-dialog preflight (below).
2. **Trust changes:** `admind operators add|remove` and latch recovery (`admind rearm`). These change who is trusted, so the group itself must not be able to make them.
3. **Terminal approvals.** An approval bead whose hashed content still fails redaction or the size limit after grooming is decided at the terminal with `approve-bead` (§5.9, §8.2). An approval must bind to its full content, and Marmot can't show it.
4. **Host-only refs.** A ref in a repository with no forge remote is marked "read on the host" (R25), and reading it needs the host. Originators pin refs to a forge wherever one exists.
5. **Startup break-glass.** An admin agent that can't start because of something only its terminal can clear (below, startup dialogs).
6. **Blocked decisions (R12).** A relayed decision whose read-back shows a partial write, or decision fields that aren't the attempt's, settles as `blocked`. The relay takes no further decision on it, and the operator resolves the bead at the terminal with `approve-bead`. A partial or foreign write can't be repaired safely from a chat message. Blocked-decision handling and its notice are unchanged (§8.2).
7. **Withheld sensitive alerts.** An alert whose text or timestamp holds a secret, an npub or a 64-hex identifier is not relayed. The operators get the fixed notice naming the file and the kind of thing found, and read the file on the host (`docs/admind.md`, alert contract). Withholding is unchanged. The writer should groom its alerts so that this stays rare.

Nothing else may require the host.

- **Questions in plain text.** A question the admin agent asks in its reply text reaches the operators as that reply, and they answer by passthrough, as today.
- **Picker asks (new behaviour, revision 14; not part of the built relay).** A picker is the admin CLI's interactive question tool. It gets a new ask kind, `picker`, separate from the relay's `question` asks. The two differ:

  | | `question` (as built, §8.2) | `picker` (new) |
  |---|---|---|
  | Posted by | A local process, over `ask.sock` | admind, from the admin agent's pre-tool hook |
  | Answer goes to | The poster, who polls with `admind ask get` or `admind ask wait` (R2, R18) | The admin agent's launch that asked it, pasted as its next message |
  | Answers kept | Several: a later answer is added and the poster sees all of them | Exactly one, selected atomically |

  - **Bound to its launch.** A picker records the agent launch that asked it: the launch nonce of the agent pane (the one the built hook checks compare against), the native session ID from the hook, and the busy period (turn) in which it was denied. Its answer is delivered only into that launch. If the agent is relaunched (`!new`, stuck recovery, or a restart that relaunched it), the picker is settled by the settlement rule below, so it is never wrongly reported as undelivered: an answer that was never reserved is `abandoned`, and the card is marked so. An answer to an abandoned picker gets "this question came from a previous agent session; your answer was not delivered".
  - **One answer, chosen atomically.** The first answer that passes the ingress checks below moves the picker from `open` to `answered` in one store transaction, a compare-and-set on its status. That transaction records the operator, the route (reply, `!answer` or reaction), the message or event ID and the text. A later answer gets "already answered by `<name>`" and is not kept as an answer. This is new behaviour. It is not R31, which governs approval decisions.
  - **Operators only.** An operator answers by replying to any chunk of the card (free text or an option number), by `!answer <id> <text>`, or by an option's number-emoji reaction on the card. Each answer passes the §8 ingress guard: MLS-authenticated sender, admind's group, not replayed, not latched. The guard is applied again at delivery (below).
  - **Redacted throughout.** The card (the question and its numbered options) and every confirmation are redacted like every post. A picker whose question or options would be redacted, or would exceed the card limit, is not posted in part. Instead, the hook's denial tells the agent to ask again in plain text without that content; this is originator grooming for pickers. The answer itself is pasted to the agent byte for byte, after one attribution line naming the operator, the picker and the route, as passthrough does. The audit records the answer redacted, with the operator, picker, route and time.
  - Managed `wsd` sessions get a pre-tool hook too, routed to §5.7 instead (question cards).
- **Picker cycle (new behaviour, revision 14).** This builds on the built dispatcher.
  - **The dispatcher's gates.** The dispatcher pastes only when the agent is idle, nothing is in flight, no accepted hook is unapplied or waiting to be accepted, and a lost hook hasn't blocked dispatch. It reserves the message durably (`in_flight`, the inbound row `dispatched`, `busy`) in one transaction before pasting (`_flush` in the product's `admind/daemon.py`).
  - **States.** A picker is in one of these states:
    - `open`;
    - `answered`;
    - `reserved` (delivery reserved, paste may have happened);
    - `delivered` (acknowledged);
    - `uncertain`;
    - a closed state: `abandoned`, `cancelled` or `superseded`.
  - **Turn-end evidence.** Separately from the agent's `busy` state, a picker carries a **turn-ended mark**. It is set only through the built current-`Stop` path, and only by a `Stop` that passes all of that path's checks:
    - its launch nonce matches the current launch (the built classification, which compares nonces in constant time), and it is not from another session;
    - it was accepted above the turn floor;
    - its turn identity (session, reservation, anchor, busy period, launch), captured under the turn lock, is still current when its effects apply.

    The mark is stored with the launch nonce and the busy period of the turn it ended. It counts for P only if both equal the picker's own launch and its denial turn, or a later turn of the same launch. Nothing else sets the mark: not a quiet pane, not `!interrupt` (which clears `busy` without a `Stop`, because Claude Code runs no `Stop` hook for a user interrupt), not a stale or below-floor `Stop`, and not any other hook event.

  **The progression rule.** One rule, P, decides when the answer moves. It is evaluated, in the same transaction, whenever either event happens:
  - an answer is selected;
  - the turn-ended mark is set.

  P holds when **both** are true: the picker is `answered`, and its turn-ended mark is set. When P holds, the answer is queued at the head of the held queue, and the dispatcher is woken. When P doesn't hold, nothing moves. So an answer that arrives before the `Stop` waits for it, and a `Stop` that arrives before the answer waits for the answer.

  **The cycle:**
  1. **Denied, nonblocking.** The pre-tool hook fires on the question tool. In one transaction, admind persists the picker (`open`, bound to the launch) and enters the `asking` hold. The hook returns at once with a denial: "your question was sent to the operators as picker `<id>`; end your turn now, and their answer will arrive as your next message". The hook never waits for the answer, so the CLI's hook timeout is never at stake. The card is posted afterwards, from the outbox.
  2. **Answer or `Stop`, in either order.** Each one is persisted when it arrives and then evaluates P. Nothing is pasted while P doesn't hold.
  3. **Delivery checks and reservation.** The dispatcher takes the queued answer under its dispatch lock. In the transaction that makes the built reservation (`in_flight`, inbound `dispatched`, `busy`), it re-reads the picker and runs the checks below. If they pass, it compare-and-sets the picker from `answered` to `reserved`, recording the reservation's ID, and releases the `asking` hold. Only then does it paste. If the picker is no longer `answered` (it was cancelled, superseded or abandoned meanwhile), the queued entry is dropped and nothing is pasted.
  4. **Acknowledged.** The agent's `UserPromptSubmit` that anchors the reservation moves the picker from `reserved` to `delivered`, in the same transaction as the anchor.

  **Delivery checks.** Each failure has its own outcome, applied in the transaction of step 3. Notices go out only through the existing posting gates, so a latched admind stays silent.
  - **Stale launch** (the agent was relaunched before reservation): the picker is `abandoned`, its queued answer and timers are dropped, its card is marked, and the answering operator is told the answer was not delivered. This is true here, because nothing was reserved or pasted.
  - **Answerer revoked** (no longer an operator): that answer is invalidated (kept in the audit, marked invalid) and the picker returns to `open`. The `asking` hold is still in force, because it is released only in step 3 after the checks pass, so no passthrough message can overtake the reopened question. The turn-ended mark is kept, so the next valid answer satisfies P at once.
  - **Latched, or group not verified:** delivery is frozen. Nothing changes: the picker stays `answered` with its answer queued and the hold in place. When the gate reopens (after `admind rearm`, or once the group is verified), the dispatcher runs step 3 again from the start.
  - **`TmuxError`** (nothing reached the pane): in the built release transaction, the picker returns from `reserved` to `answered`, the `asking` hold is restored, and the answer goes back to the head of the queue.
  - **`TmuxPasteUncertain`:** the settlement rule below makes the picker `uncertain`. It is never pasted again.

  **Settlement rule.** Every path that abandons the dispatcher's reservation does so in the built `abandon_in_flight` transaction. The picker's settlement is written in that same transaction, so no path can leave a picker `reserved` without a reservation. Those paths are:
  - `recover()` at startup;
  - `!interrupt`;
  - `!new`;
  - a restarting `SessionStart`;
  - the agent's death or readiness timeout and its relaunch;
  - an uncertain paste;
  - a prompt that started a new turn while an anchored one was still open.

  The rule, by the picker's state:
  - **`reserved`, never acknowledged → `uncertain`**, with a notice ("pasted; the agent may or may not have received it; check with `!tail`"). It is never replayed, even if a later hook shows the agent idle.
  - **`delivered` stays `delivered`.** It was acknowledged by its `UserPromptSubmit`.
  - **`open` or `answered`, never reserved → `abandoned`**, only when the path also ends the picker's launch (`!new`, a restarting `SessionStart`, death or readiness-timeout relaunch). The other paths leave such a picker as it is: `!interrupt` doesn't end the launch, and doesn't set the turn-ended mark.

  The only exception is `TmuxError` (above). Nothing reached the pane, so non-delivery is proven, and the picker returns to `answered` for a retry.

  **What `asking` blocks.** Only ordinary operator passthrough messages. They stay queued behind the picker's answer, so none overtakes it, and the agent takes no new instruction while its question is open. The hold never blocks:
  - the picker's own answer;
  - `!` commands;
  - replies and reactions to cards.

  **Cancel and supersede.** Both take the dispatcher's dispatch lock, so neither can interleave with step 3, and act in one transaction:
  - **`!asks cancel <id>`** (new). If the picker is `open` or `answered`, it moves to `cancelled` (a compare-and-set). The same transaction drops the queued answer and the picker's timers and ends the `asking` hold, which lets the queued passthrough messages through.
  - **Supersede.** A second picker from the same launch closes the first in the same way, as `superseded`. The hold passes to the new picker rather than ending.
  - **Once delivery is reserved**, neither changes the delivery. The operator is told its actual state: `delivered` (acknowledged), `uncertain`, or `reserved` ("pasted; not yet acknowledged"). A `reserved` picker always settles, by acknowledgement or by the settlement rule, and they are told the result. "Not delivered" is said only of an answer that was never reserved.

  **Recovery.**
  - *Early answer* (before the denial completes, or before `Stop`): persisted, waiting for P. It is never pasted into a running turn.
  - *`Stop` before the answer:* the mark is persisted, and the answer, when it is selected, satisfies P at once.
  - *Missing `Stop`.* If the turn-ended mark isn't set within `[admind] picker_stop_seconds` (default 300) after the denial, admind posts one notice. The notice names the picker, says whether it has an answer, and gives the remedies:
    - `!tail` to look;
    - `!asks cancel <id>`, after which the answer can be sent again as an ordinary message, under the dispatcher's normal gates;
    - `!new`, which abandons the picker.

    Only a later authenticated `Stop` for the same launch can set the mark. `!interrupt`, or another hook event that leaves the agent idle, doesn't. The answer is never pasted without the mark.
  - *admind restarts mid-cycle.* Pickers, their selected answers (with the answering operator's authenticated identity: the policy name and the MLS sender key) and their turn-ended marks are in admind's store, so they survive the restart.
    - The built `recover()` drops ordinary held inbound rows (`received`) with a "resend it" notice. A picker answer is not an ordinary held row. Its inbound row is marked as a picker answer when it is selected, so `recover()` leaves it alone, and the picker row is the source of truth.
    - The settlement rule runs in `recover()`'s abandonment transaction. A `reserved` picker becomes `uncertain`; this covers a crash before the paste, after the paste, and after the paste but before the `UserPromptSubmit`. A `delivered` one stays delivered.
    - Then each `answered` picker that was never reserved is rebuilt once into the held queue, from its row, with the stored sender identity. Only after that is P evaluated. Step 3 still re-checks that sender, the latch, the group and the launch. The adopted agent's `adopted` hold must also have ended (an idle hook) before step 3.
    - If the agent was relaunched, the settlement rule applies.
- **Permission prompts.** The admin agent runs with permission prompts bypassed (§8), so none are expected. One that appears anyway, reported by the CLI's notification hook, puts the agent in a `prompt` hold and posts its text as a card. If S8 shows the CLI's hook can return the decision, the operator answers it with 👍 or 👎 (or a reply word, R28) and admind returns that decision through the hook. Otherwise the card offers `!interrupt` (Esc, which declines) and `!new`. admind never chooses a dialog option by reading the screen (§2).
- **Startup dialogs** (first-run, workspace trust, bypass-mode acceptance) appear before any hook runs. `[admind] workdir` is a dedicated directory, never the home directory. Setup pre-accepts the dialogs through the CLI's own configuration, where S8 shows that's possible. Then, before enabling the agent, setup verifies on the host that one launch in that workdir reaches `SessionStart` with no terminal input (the preflight). An adapter for which S8 can't show pre-acceptance is not supported as the admin agent (§13 S8). The runbook step that accepts dialogs by `tmux attach` is replaced.
  - **Failure path.** A restart reproduces the same dialog, so relaunching is not a remedy and admind never relaunches in a loop. If the agent shows no `SessionStart` within the readiness timeout, admind posts a `not-started` hold naming the likely causes (a startup dialog, a login problem) and offers `!tail` and one `!new`. If the relaunch also shows no `SessionStart`, admind marks the agent stuck and stops relaunching. The built `AgentStuck` stops after three launches; this stops after the first repeat. The one-relaunch budget is stored, and it survives `!new` and admind restarts; the built `new()` resets its counter, and that reset is removed. Only a `SessionStart`, or the host preflight (exception 1), restores the budget. While the agent is stuck, `!new` doesn't relaunch: it replies with the break-glass notice. It then posts that this is a startup break-glass case (host exception 5), naming the adapter and the likely dialog. admind never types into a dialog or chooses an option by reading the screen (§2).
- **Every hold states its reason.** A hold is any state in which admind won't paste the next operator message, or the agent is waiting on an operator. The holds are: `busy` (a turn is running; messages queue), `asking` (a picker is open, or answered and not yet reserved for delivery; it blocks only passthrough, above), `prompt`, `not-started`, `adopted` (admind restarted and adopted a running agent whose state it can't know), `lost-hook` (a hook event was lost) and `break-glass` (below). On entering a hold, other than `busy`, admind posts one notice that names the hold, the agent's actual state as far as admind knows it (idle, in a turn since T, waiting on ask `<id>`, or unknown, never a guess), and what the operator can do. The adopted-pane notice says "unknown" rather than "until the agent finishes its current turn". Leaving a hold posts a one-line notice. `!status` lists the agent's state, every active hold with its reason and start time, the number of queued messages and the open asks. `!ps` adds one line per active hold to its unit health.
- **tmux is break-glass.** No procedure, notice or runbook step may require `tmux attach`, for admind's agent or for a managed session (§4.2). Attaching stays possible. admind audits client attach and detach on its own tmux server and posts "an operator attached on the host". After a detach it treats the agent's state as unknown, in an `adopted`-style hold, until the next hook event shows it, because keystrokes typed on the host may have changed it.
- **Reactions.** Inbound operator reactions are handled (R27). Picker answers by number emoji are new here (above). **Progress reactions** on an operator's message (👀 queued, ⏳ in a turn, ✅ replied, ⚠️ failed) are driven by the hooks admind already installs (`UserPromptSubmit`, `Stop`). They need admind to send reactions, and to remove the previous one, which no spike has shown (`remove_reaction` is untested; S4 tested sending only). How to ship them is open (§17).
- **Latch visibility** is open (§17). Until decided, a latched admind stays silent, as in revision 13.
- **Spike S8** (§13) sets the minimum capabilities each admin-agent adapter needs for this section, and demonstrates them end to end. An adapter that lacks one is not supported as the admin agent, unless S8 demonstrates an alternative. `!interrupt` and `!new` are recovery steps, not an alternative.

### 8.2 Interim Marmot relay for asks and approvals (revision 14, as built)

Built from the relay spec (2026-10-05, R1–R26), its replies and reactions delta (2026-10-06: R27–R31, revising R7, R8, R13 and R15) and the bump delta (2026-10-07, B1–B14), all in the product repository's `docs/superpowers/specs/`. The specs are authoritative for detail; the decisions are:

- **Posting.** Local processes running as the service user post asks over a second host socket, `ask.sock` (0600; R1), with `admind ask post|get|wait|list|cancel` (`wait` polls, R2). Ask IDs are 4 easy-to-type characters (R3). The kinds are `question`, `merge` (a full PR URL and pinned head SHA; merging stays the operator's action) and `approval` (an open `kind:approval` btq bead). Every ask must carry its context, checked by a deterministic lint (R4). The poster's `--from` label is shown as unverified (R16). Limits (R15, revised), argument hygiene (R19) and bounded socket replies (R24) apply. Posting is serialised (R22) and refused while latched (R17). A new ask for the same bead supersedes the old one (R9).
- **Approval content.** admind reads the bead through `approve-bead --json` and never writes bead metadata (R5). It needs `[admind] approve_bead`; without it approval asks are refused and everything else works. An approval card shows the whole hashed content, never shortened, split into message-sized chunks. `MAX_APPROVAL_CARD` (24,000 characters) caps the whole readout (title, ask lines and description together), not each chunk; a readout over it is refused as too long (`TOO_LONG`) and goes back to its originator (§8.1); a legacy shortened card keeps the `!details` rule (R8, revised). It must survive redaction unchanged, or it is not posted and the bead is decided at the terminal (R18, R21). Refs are pinned forge permalinks, or marked "read on the host" (R25). Card wording is fixed (R30).
- **Deciding.** A decision is a reply or reaction to any chunk of the card (or of its `!details`); the bead and the full digest come from the card (R7, revised). Reactions get the same guard as messages and replay on `r:<event_id>`; 👍 ✅ ❤️ ♥️ approve, 👎 ❌ deny; removing a reaction is ignored, and a decision is final (R27). A reply approves only if it is exactly an approve word. It denies in two cases. The first is `no` or `n` as the whole reply; "no idea" is a note. The second is a reply whose first word is a deny word (`deny`, `denied`, `reject`, `rejected`) or a deny emoji, where the rest of the reply is the reason, which may be empty (R28); any other reply to an approval card is a note, and a plain reply to a question or merge card is its answer (R13, revised). Replies to cards never reach the agent (R14); the only exception is the answer to a `picker`, a new kind (§8.1), which goes to the launch that asked it. The approver's name is their `policy.toml` name and must be in btq's `approvers` (R10). With two operators the first decision wins (R31).
- **Recording.** The attempt is persisted before the decision run (R23). admind runs `approve-bead --expect-digest`, which re-checks the digest under a per-bead lock (R6), inside the worker that holds `work_lock` (R11), and records `via=marmot` with a `via_ref` matching the audit (R20). The outcome is read back, not parsed, and settled as approved, denied, blocked or uncertain (R12); recovery after a restart is serialised with decisions (R26).
- **Stale cards.** A card whose bead changed gets a fresh card in the same settlement. If the bead's pin no longer matches, its updated content is delivered in the stale card's thread, marked undecidable, and the poster sees `stale`; admind never renews a pin (R29).
- **Bumps and repeats.** `!asks bump` replies to every open ask saying it is still outstanding (B1–B5). It threads to the original card's chunk 0 if that was sent; otherwise to chunk 0 of the earliest repeat whose chunks were all sent; otherwise that ask gets no bump (B13). A bump is not a card: a reply or reaction on it decides nothing and gets a hint (B6). It is idempotent, bounded and audited (B7–B9) and listed in HELP (B10). `!asks repeat` reposts every open ask's card as new top-level messages, which are cards; delivery (R8) is satisfied by the original or one full repeat (B11–B12). Open asks are bumped automatically after `[admind] ask_bump_hours` without activity (default 12, 0 disables, 0–720), checked every 5 minutes, never while latched (B14).
- **Trust change, accepted by the operator.** Approval authority for btq beads extends from the terminal to an MLS-authenticated Marmot reply or reaction from an approver's key on admind's own card. Whoever controls an approver's Marmot key can approve designs. An accidental 👍 records an approval (operator, 2026-10-06). The admind group already had this power through the unsandboxed admin agent; the relay makes it explicit, pinned to a digest, attributed and audited. admind's own dependency on beads is limited to this relay. The §3.4 residual risk (a count-preserving member swap) now also covers reading approval context; a swapped-in key is not an operator, so it still cannot answer or approve.
- **Rejected alternative, reversed.** The relay spec rejected reactions as decisions; the 2026-10-06 delta adopted them (R27) at the operator's request.
- **Future.** When `wsd`'s §5.4 decision queue exists, the approval kind is either retired or submits to it (open, §17). The gatekeeper's sufficiency judgement becomes mandatory for relayed asks in the release that ships `wsd` and the gatekeeper; before that it may only run in shadow (§5.9).

## 9. Scheduling and cron

These are service-manager timers that call `wsd tick <job>`, with no LLM:

| Job | Interval | Purpose |
|---|---|---|
| pickup backstop | 60s | Pickup in case an event trigger was missed. |
| forge poll | 60s | Collect PR/patch review decisions. |
| reconcile | 5m | Compare runners, sessions and bead states, and repair drift. An in-progress bead with no live session is resumed; after 2 failures it becomes `needs-human`. |
| reminder sweep | 15m | Apply the one-reminder rule. |
| daily digest | daily | Closed yesterday, open approvals, `needs-human`, idle workstreams. |

User jobs live in `schedules.toml`. Each job either runs a fixed command or files a bead into a workstream. A job needing judgement files a bead; it never calls an LLM directly.

## 10. Failure handling

| Failure | Behaviour |
|---|---|
| Hermes or gatekeeper down | Deterministic flows continue. Intake falls back to `unreviewed`, and grey-zone requests escalate. |
| `wsd` down | The service manager restarts it, and agents keep running. The hook shim waits up to 5s, then **fails closed**, with one exception: tool calls the shim can classify locally, from a cached copy of the auto-approve tier, as sandbox-confined (file edits in the worktree, running tests, local git). Everything else is denied with "control plane unavailable; retry shortly". Spooled events are untrusted observations (§3.3). This applies only where the hook is known to run: Claude Code, and interactive Codex sessions once that is verified (§4.2). A Codex session without a verified hook, including every headless `codex exec` run, has no fail-closed path, so it is allowed only as §5.3 says: read-only, or enforced by the outer sandbox alone. |
| Agent or runner crash | Reconcile resumes the session keyed by `uuid5(bead, role, profile)` (for Codex, its recorded thread ID, §4.1). After 2 failures the bead becomes `needs-human`. |
| Reboot | Units start, reconcile resumes in-progress beads, and parked beads wait on their blockers. |
| Dolt unreachable | Pickup, close and **new approvals** pause. Decisions and actions need the bead write first (§5.4), so they wait. Commands use the cache with a warning. Hooks decide from policy plus cache. Audit comments go to the spool and are replayed later. |
| Marmot relay down | The outbox retries with backoff, and work continues. |
| Forge unreachable | Forge approvals are delayed. Marmot approvals still work. |
| Everything wedged | `admind`. |
| All permitted accounts of a role exhausted (trusted) (revision 14) | Beads already claimed defer (§4.3) until the gate's deadline; new candidates for that profile are skipped; other profiles, roles and workstreams continue. One alert per episode in the control group. |
| A session reports a limit (untrusted) (revision 14) | Stop and commit the WIP, then a trusted read of the account. Confirmed: fail over (`"next"`) or defer. Not confirmed: the bead defers on its own account, for a bounded time. |
| Usage source unavailable or stale (revision 14) | The account's usage is unknown, which is eligible. A mid-turn limit is still caught by the reactive signal where the adapter has one. |
| A session's account is reordered, removed or repointed, and continuity forbids the switch (`failover = "none"`, or an adapter that can't switch) (revision 14) | The gate gives `account_changed`. The bead defers with that reason and no `defer_until`: it has no quota timer, gets one comment and one control-group alert, and isn't re-deferred while it waits. It is gated again at startup, on a configuration reload, or on the operator's release, never on a timer; it undefers once that gives an account. The operator restores the account's login or order. |
| Account login invalid (the freshness gate refuses) (revision 14) | With `failover = "next"`, try the next eligible account; otherwise the bead becomes `needs-human`, as before. |
| admin agent waiting on a question, prompt or dialog, or not started (revision 14) | A hold with its reason on Marmot, and a card where there is something to answer (§8.1). Never a silent wait that needs `tmux attach`. |
| Picker answered but no `Stop` arrives, the paste is uncertain, admind crashed with the answer reserved, or the agent was relaunched (revision 14) | Hold and one notice; the answer is never pasted without an authenticated `Stop`, never replayed or resent, and launch replacement applies §8.1's settlement rule. |
| Admin agent still not started after one relaunch (revision 14) | Stuck, no further relaunches, and a startup break-glass notice (§8.1). |

## 11. Testing

- **Unit:**
  - router classification
  - policy tiers
  - the approval state machine, including races between Marmot and forge
  - materiality handling
  - the nudge counter and progress detection
  - session ID derivation
  - sandbox profile compilation for both backends
- **Golden:** every renderer event type has a fixture showing exactly what the operator sees. For `admind` this includes summaries, backstop batches (with skipped-line counts and collapsed runs) and `!details` chunking.
- **admind (revision 13):** the guard with several operators and an expected member count; `admind operators add|remove` and the latch on every unexpected change; summarizer failure, timeout and empty output each falling back to the backstop; `!details` and `!details full` (redacted, no thinking); and delivery-lane ordering. The summarizer, Marmot and the agent are fakes.
- **Accounts and usage (revision 14):** all with an injected clock, including clock jumps:
  - the headroom gate, as a pure function of (profile, previous launch, capabilities, cache, now, settings), with hypothesis properties: unknown, stale and expired data are eligible; `failover = "none"` never picks a later account and never changes a session's credential key, even on an adapter that can switch; a deadline is always after `now`; an untrusted observation never changes the result for another session, bead or account (a reviewer moving P → Q → P reads only each launch's own rows);
  - ingestion: malformed percentages and out-of-range resets, replacement by receipt sequence (a newer low-usage read after the clock jumps back wins), payloads naming another account, rows keyed by credential key;
  - account pinning: a crash after the launch entry is written, with usage changing before replay, launches the pinned account or abandons that generation, never another account under it;
  - dispatch: a crash after the launch receipt and before the outcome is recorded, with the session still running and with it already ended, reconciles to `launched` from the receipt; a `refused` receipt reconciles to `abandoned`; a credential replacement under `"none"` afterwards gives `account_changed`, never a first launch; a crash after the dispatch mark and before the receipt holds the bead, even when forged or genuine hook-spool events, a transcript or a tagged tmux session for that generation exist;
  - legacy adoption: upgrading a populated journal with sessions launched before launch entries, then changing the account configuration, keeps each session on the default login or gives `account_changed`; an upgrade with accounts already configured for the adapter holds those sessions;
  - pickup: a quota-ineligible candidate is skipped and an eligible later one starts;
  - a crash at each step of defer, undefer and failover, replayed to the same end state, and a lost journal escalating a deferred bead;
  - `account_changed`: reordering or repointing under `"none"` with switching demonstrated gives it; startup, a reload and a release each re-gate it once, and no timer does; the undefer that follows keeps every check and survives a crash at each step; a due quota deferral that gates to `account_changed` journals one superseding record, one comment and one alert, and the reverse transition gives one quota record, each surviving a crash at every step;
  - the journal upgrade: from a populated previous-version journal, and interrupted part-way;
  - the session key staying the same when the account changes, and launch entries never rewritten.
- **Marmot-only (revision 14):** the S8 end-to-end picker cases, with fakes: a picker becomes a `picker` card, and its first operator answer (reply, `!answer`, number reaction) reaches its own launch once, attributed, after `Stop`; a second answer gets "already answered"; a non-operator's answer is dropped, and one whose sender was revoked before delivery is not pasted; `asking` holds passthrough but not the answer or `!` commands; the progression rule fires on either order of answer and `Stop`, and never on `!interrupt`; a revoked answerer, a latch, a stale launch, a missing `Stop`, a crash before or after the paste or before its acknowledgement, `!interrupt`, `!new` and a restarting `SessionStart` between reservation and acknowledgement, an uncertain paste, and cancel or supersede racing the reservation each end as §8.1's settlement rule says; a stale or below-floor `Stop` never sets the turn-ended mark; a never-reserved answer survives a restart and is rebuilt once; the one-relaunch budget survives `!new`; a `not-started` agent is relaunched at most once; an ask that fails redaction or the size limit is refused to its originator and never posted in part; each hold posts its reason once and `!status`/`!ps` list it; the adopted-pane notice never claims a turn is running when admind doesn't know; a host attach and detach are audited and lead to the unknown-state hold. The relay's own tests are listed in its specs.
- **Sandbox runtime (revision 15):** each launch self-test probe has a negative control that must refuse the launch: a missing canary, a canary inside a mount, a wrong pinned login hash, a leaked variable, a dead session socket and a failed outer-fence check. The probes also run without the runtime (plain `--network=none`, and a connected network) and must fail there, which shows they recognise its specific enforcement rather than any failure. A failure injected after a passing self-test refuses the launch, so a cached result never counts. The agent-path channel has two controls: a forged result from a process that is not the probe, and a variable present only on the tool path. Probe protection (§7) has adversarial controls, each of which must fail the self-test or be shown impossible by the chosen mechanism:
  - a ptrace attach that changes the probe's code or data, then detaches, between the launcher's checks;
  - a write to the probe's memory through `/proc/<pid>/mem`;
  - a mount over the probe's files from a user namespace;
  - a change to the probe's configuration, or to its result socket, after the probe has started.
- **Integration:** a fake `claude` and `codex` that emit hook events, a fake gatekeeper, a mock Marmot, a fake forge, and a throwaway Dolt. They exercise flows 5.1–5.8 and every row in §10. The fakes gain scripted usage windows and a limit-reached signal, and the fake host-side usage read can confirm or deny a limit (revision 14).
- **Platform:** a CI matrix of Linux and macOS for the platform seam and the sandbox backends.
- **Acceptance:** the migrated-bead corpus (§14) running through v1 end to end, starting with one workstream and adding others once it has run for 3 days without operator intervention beyond approvals.

### 11.1 Code review

This rule is the same for every agent and harness, and for v2's own development:

- **Two LLMs are recommended, not required.** A deployment with a single profile, or a single model, is fully supported.
  - Every review and design round then runs in `adversarial` mode, a fresh-context session of the same model, and the evidence records that.
  - `heterodyne setup` and `config check` recommend adding a second profile, but never refuse to run without one.
  - Only an operator who sets `mode_when_same_model = "block"` makes a second LLM mandatory.

- **The same rule gates designs.** An implementation task needs a design approval bead that records `design_review: reviewer= author= mode=` from a back-and-forth between the two configured LLMs (or an adversarial fresh-context round when only one is configured), followed by a human approval from the `approvers` in the btq policy. Tasks created before the policy's `design_gate_since` are grandfathered.

- **Cross-model (required whenever possible):** a different LLM than the author reviews the change in a read-only oneshot session. Examples:
  - a Claude author: `codex exec -m gpt-6-sol -c model_reasoning_effort=medium -s read-only "..."`
  - a GPT author: `claude -p --model <model> --permission-mode plan "..."`
- **Adversarial backstop:** used only when no second model is available. A fresh-context agent reviews it; it may be the same model, but shares no session or transcript. Its brief assumes the change is wrong. The evidence states why a cross-model review wasn't possible.
- **Evidence line:** `Code-Review: reviewer=M author=M mode=cross-model|adversarial range=BASE..HEAD`, followed by the findings. `btq close` rejects `kind:task` evidence without a valid line, and rejects `cross-model` when reviewer equals author.
- **Blocking findings** are fixed, or explicitly rebutted in writing, before close.
- **In v2** this becomes the `reviewer` role (§5.8). By default `wsd` assigns the reviewer role to a different agent or model than the coder, and falls back to an adversarial fresh session.

## 12. Scope

**v1 minimum slice (the first thing to run the acceptance corpus):**
- Linux host only: systemd and the §7 sandbox runtime (OpenShell, S5 passed; bubblewrap as the fallback, §7; revisions 14–15).
- Accounts with the headroom gate and deferred parking, on Linux, for each adapter whose capabilities S7 demonstrated (§4.4 D9; revision 14).
- Marmot-only operation of `admind` (§8.1; revision 14).
- Both adapters (`codex`, `claude-code`), because the reference install's role config uses both. Any deployment can configure just one (§11.1). Codex is steered with `codex queue`. The Claude reviewer is steered through the `send-keys` fallback; it is rarely steered, because it is read-only.
- Marmot as the only approval surface.
- `wsd`, `wsd-act`, `admind`, gatekeeper, cron and reconcile.

**Phase 2, each gated on its spike passing and on its own design-review and approval round:**
- macOS (launchd and Seatbelt)
- Claude channels steering (S2)
- GitHub approvals
- Radicle approvals

**Out of scope:**

- CubeSandbox runtime.
- Concurrent coder sessions per workstream.
- Aggregating several hosts into one control group (the "remote mode" of the portability plan).
- GitHub webhooks (polling only).
- A shared memory layer beyond bead notes and `workstream-recall`.
- Paseo adapter.
- Native Windows.
- Accounts on macOS, with the phase 2 Seatbelt work (revision 14).
- An ACP adapter; revisit with the Paseo adapter (change-plan item AU-16 is a design note; revision 14).

## 13. Spikes (before implementation beads)

**Status: S1–S4 are done.** Their findings and evidence are in the product repository's `docs/spikes/`, one document per spike. Revision 12 folds the findings that contradicted this ADR into §3.4, §4.1, §4.2, §5.3, §5.6, §7 and §10. Remaining open items are noted per spike. S5 is done (revision 15). S7 and S8 (revision 14) are open.

- **S1, Codex parity:** done.
  - Does 0.157.0 have a pre-tool hook event that can deny with a reason under `--yolo`? **Unsettled.** S1 observed it in an interactive session; under `codex exec` it never fires (§5.3). S5 (revision 15): yes in interactive sessions on 0.160.0 with the harness's schema, but only once the hooks are trusted (§4.2). Until the launcher can establish that trust, the fallback applies to every Codex session: catching the sandbox failure after the fact and parking the bead. That is acceptable, because the sandbox is still the boundary.
  - Can `--dangerously-bypass-hook-trust` replace per-hook trust hashes for runner-launched sessions? **Unsettled:** S1 observed it, but an independent re-run could not reproduce it. S5 found that trust is a per-hook hash in `config.toml`, whose derivation is still unknown (§4.2; revision 15). The hooks are generated by `wsd`, which vets their source.
  - Can the session ID or name be set at launch? **No launch flag.** `wsd` records the assigned ID; a post-launch rename is optional (§4.2).
  - Does `codex queue` deliver into a live interactive TUI session? **Yes**, through a dedicated per-session app-server socket (§4.2). S1 ran that app-server outside any sandbox. §7 requires it to run inside, and S5 verified that placement under OpenShell (revision 15).
- **S2, Claude channels** (phase 2 gate): a custom hermes-channel MCP server under subscription auth. Done: the live push was not demonstrated within the timebox, so the gate is not passed. v1 uses `tmux send-keys` for the Claude reviewer, unchanged.
- **S3, sandbox:** run both CLIs inside bubblewrap (v1) and Seatbelt (phase 2 gate) with synthetic home, egress proxy and session socket. Acceptance is exactly the §7 launch self-test probes plus a working login and hooks. If a backend can't pass, that platform doesn't ship. Done for bubblewrap: the shape it tested passes (both CLIs headless, plus the Claude hook). The managed Codex shape is a plan-4 item (§7). Seatbelt remains open with the phase 2 macOS work.
- **S4, Marmot:** threads (reply-to) and reactions end-to-end on the current mdk bindings, for both a harness identity and a separate `admind` identity. Done: the harness identity through its `wn-agent` control socket, and the scratch `admind` identity through its own `wn` daemon, in both directions. The operator's real-client check is still pending.
- **S5, OpenShell (gates the §7 runtime; revision 14):** demonstrate the five items in §7 ("S5 had to demonstrate") on the reference host: the managed launch shape of both CLIs from a host tmux session, every launch self-test probe (Other accounts included), a working read-only login, hooks and `ws-request` on the session socket, and a runtime driver that works on the host. **Done (revision 15).** Items 1, 3, 4 and 5 passed. Item 2 passed except that a direct connection gets the broker's `EACCES` instead of unreachable, which this revision accepts with an alternative proof (§7). S5 also found:
  - OpenShell's proxy binds the HTTP authority and SNI to the authorized endpoint;
  - per-binary egress covers the agent's process tree;
  - interactive Codex hooks fire with the real schema once trusted (§4.2).

  Its findings and evidence are in the product repository's `docs/spikes/s5-openshell.md`, and Appendix A.2 lists where each one lands. Bubblewrap becomes the v1 runtime only if the operator explicitly rejects the direct-network proof (§7, Fallback).
- **S7, accounts and usage (Linux; revision 14):** for each adapter on its pinned version: (1) the login file set with `CLAUDE_CONFIG_DIR` or `CODEX_HOME` inside the synthetic home, and a working login; (2) whether a session resumes after its login files change to another account; (3) an in-session usage source (Codex `account/rateLimits/read` and `account/rateLimits/updated` through the per-session app-server; Claude status-line JSON or transcript entries); (4) a trusted host-side usage read, and whether it refreshes tokens; (5) a structured limit-reached signal; (6) the freshness gate with two accounts of one adapter; (7) a handoff relaunch: a fresh native session under another account, in the same synthetic home, with a generation-specific native ID for Claude. No screen scraping. Each finding is recorded as demonstrated, not demonstrated or unknown; only a demonstrated one enables its capability (§4.4 D9). macOS (keychain) is a phase 2 question. If an accepted finding changes a §4.4 decision, this ADR is revised and reviewed again before the affected work starts. S7 is change-plan gate G2. If S7 runs under OpenShell, it also covers OpenShell's filesystem policy for the login files.
- **S8, Marmot-only capabilities (revision 14; gates §8.1):** for each admin-agent adapter on its pinned version, with each finding recorded as demonstrated, not demonstrated or unknown, as for S7.
  - **Minimum capabilities.** An adapter is supported as the admin agent only if S8 demonstrates all four:
    1. a pre-tool hook on the interactive question tool that receives the question and options and returns a denial with a reason at once (nonblocking);
    2. `UserPromptSubmit` and `Stop` hooks that admind receives for every turn, including a turn ended right after a denied picker;
    3. the startup dialogs pre-accepted through the CLI's configuration, with the setup preflight reaching `SessionStart` with no terminal input, in a fresh workdir and again after a restart;
    4. no permission prompt in bypass mode, or a hook that reports one with its text. Whether that hook can return the decision is recorded but not required.

    An adapter that falls short is excluded as the admin agent unless S8 demonstrates an alternative for the missing capability. `!interrupt` and `!new` are not alternatives.
  - **End-to-end acceptance, host-free.** On a real CLI with a scripted Marmot group, a picker must go through the whole cycle (§8.1): picker, denial, card, operator answer, `Stop`, answer pasted. The agent must receive the attributed answer exactly once, with no host action at any step. The same harness must show each of these:
    - a non-operator's answer, and an answer from a revoked operator, never reaching the agent;
    - a second answer getting "already answered";
    - a picker with redactable content not being posted, and its denial asking for plain text;
    - an answer arriving before the denial completes and before `Stop`, and an answer arriving after `Stop`;
    - `!interrupt` instead of `Stop`, not counting as turn completion;
    - a missing `Stop`, giving the notice and no paste;
    - an operator revoked between selection and delivery, reopening the picker with the hold still in force;
    - a latch between selection and delivery, freezing delivery;
    - an admind crash before the paste, after the paste, and after the paste but before `UserPromptSubmit`, the last two ending `uncertain` with no replay, and a never-reserved answer rebuilt once after restart with its sender;
    - `!interrupt`, `!new` and a restarting `SessionStart`, each fired between reservation and acknowledgement, leaving the picker `uncertain` (never `reserved`, never replayed), and each fired after acknowledgement, leaving it `delivered`;
    - a stale or below-floor `Stop`, or a `Stop` from another launch, not setting the turn-ended mark;
    - an uncertain paste, giving `uncertain` and no resend;
    - `!asks cancel` and a superseding picker racing the reservation, and each arriving after it, reporting the actual delivery state;
    - a relaunch between answer and delivery, abandoning the picker.
  - **Startup failure path.** With a dialog deliberately not pre-accepted: one `not-started` hold, one `!new`, then stuck with the break-glass notice, and no further relaunches.
  - **Other findings**, which §8.1 uses only where demonstrated:
    - `wn-agent` sending and removing a reaction on an operator's message in admind's group, the target of progress reactions;
    - inbound number-emoji reactions on a picker card reaching admind;
    - tmux client attach and detach events on admind's tmux server.

## 14. Migration

1. **The old harness stays as-is.** There is no live cutover (§2), so it gets no stabilisation fixes. It is archived after step 3.
2. **Build order:**
   - spikes S1–S4
   - `admind`
   - `wsd` core: beads adapter with the `wsd` queue identity, `AgentRuntime` and adapters, session sockets, policy, approvals and decision queue, `wsd-act`
   - Marmot router with ingress authentication, renderer and outbox
   - gatekeeper interface
   - cron and reconcile
   - (phase 2) forge bridge, macOS, Claude channels
3. **First workstreams:** start v2 with fresh workstreams fed by migrated beads (step 4). There is no live cutover, because the old harness is deadlocked. Then archive `hermes-workstream-harness`, `harness-improvements` and `harness-dev`.
4. **Migrate the existing beads.** The old harness is deadlocked (2026-09-29), so v2 is built without waiting on it. Existing beads stay untouched until v2 can run workstreams. Then each open bead goes through two passes:
   - **Cull:** if v2 makes the bead irrelevant (it fixes old-harness internals such as pane scraping, the outbox, the babysitter or manager beads), close it as superseded, citing this ADR.
   - **Rewrite:** update anything that refers to the old way of working (tmux/regex detection, `workstream` CLI facades, manager escalation, per-harness paths) to the v2 equivalents, and re-route the bead into its v2 workstream.
   - **Surviving beads are v2's acceptance corpus.** They are the first real work v2 runs, and they double as end-to-end tests of intake, pickup, park and resume, approvals and review. The cull itself is done as v2 work.

### Carried over from the old harness

- beads/btq and the PICKUP.md rules
- the service-unit patterns
- the deterministic `/status` intercept in the Marmot adapter (rewired to `wsd`)
- Codex hook-trust handling (`hook_config.py`)
- btq worktrees, plus reflinked dependency caches from the old harness
- the fake-executable test approach

## 15. Repository, packaging and configuration layering

- **Name and repository:** the product is **heterodyne-metaharness**. It replaces the entire contents of the existing `Epiphytic/heterodyne-metaharness` repository (GitHub, mirrored to Radicle).
  - The old contents are v1-era harness code that nobody else uses. They are removed in a single PR, keeping only `LICENSE` (Apache-2.0), and git history preserves them.
  - The ADR and review record move there under `docs/adr/` and `docs/reviews/`.
  - The build delivers a usable `README.md` (what it is, quick start, architecture sketch), `docs/` (install on Linux and macOS, configuration reference, operations and runbooks, security model, adapter authoring), and a guided `heterodyne setup`.
- **The repository is install-agnostic.** Nothing in the repo may contain an *install-specific value*: a particular host, user, home path, npub, group ID, database address, approver, credential or local repo.
  - Platform support is not install-specific. Linux and macOS backends, templates and install docs belong in the repo.
  - Platform choice follows §3.2: `heterodyne setup` probes the platform at install time and writes the chosen backends to host config. They are not re-probed at run time, and never hard-coded.
- **Effective precedence** is one order for all settings. It is the only order, and §4.1 refers to it. Lowest first:

  | # | Layer | Location | In git? |
  |---|---|---|---|
  | 1 | Built-in defaults | `heterodyne/defaults/*.toml` in the package: tier rules, sandbox profile templates, timeouts, rendering. Adapters are defined, but no models are chosen. | Yes |
  | 2 | Host config | `$HETERODYNE_CONFIG_DIR`, default `${XDG_CONFIG_HOME:-~/.config}/heterodyne/` on both OSes: `config.toml` (host settings, profiles, default roles, platform backends, integrations, and, from revision 14, accounts and usage settings) and `policy.toml` (approvers and identities) | **No** |
  | 3 | Workstream config | `$HETERODYNE_CONFIG_DIR/workstreams/<ws>.toml` | **No** |
  | 4 | Bead override | a `role:<role>=<profile>` label (roles only) | n/a (in beads) |
  | 5 | Environment and CLI | `HETERODYNE_*` variables and flags, limited to locations and debugging; they can't change policy or roles | No |

  - **Policy is not layered.** `policy.toml` (approvers, identities, the operator allowlist, hard-deny rules, the action registry, and the tier floor) is read **only** from the host config directory.
    - Workstream config, bead labels and environment/CLI can't set any `policy.toml` key. The config loader rejects such keys in those layers as a startup error, not a silent ignore.
    - **Tightening lives in a separate, additive table.** Within a workstream file, policy tightening goes only in a `[restrict]` table, which has two lists:
      - `hard_deny` adds rules to the hard-deny tier;
      - `escalate` moves action classes from auto-approve to escalate.
    - The loader computes effective policy = host policy ∪ `[restrict]`. Rules can be added, never removed. Any `[restrict]` entry that would allow something host policy doesn't is rejected at startup.
    - Workstream and host config files are outside every sandbox (§7), so agents can't edit them. Changes go through the operator, `admind`, or an approved policy-rule bead (§5.3).
  - **What workstream config may override:** role and profile choices, repositories, sandbox *additions* within the host's allowlists (extra read-only mounts, extra egress hosts from a host-approved list), cron jobs, rendering, and timeouts. Workstreams choose profiles, never accounts (revision 14, §4.4 D1).
  - **Account login paths (revision 14):** the key is `login_dir`, not `auth_dir`, because the secret-name check (`docs/configuration.md`) flags any key with an `auth` segment. `login_dir` is a path, not a secret, and must not hold a secret reference.
  - `examples/` holds commented sample host and workstream configs, for example "Codex codes, Claude reviews". It is **not a layer**: nothing loads it, and `heterodyne setup` only copies from it.

  - **State** lives in `${XDG_STATE_HOME:-~/.local/state}/heterodyne/`: journal, spool, alerts and logs.
  - **Secrets never appear inline** in any layer. Config refers to them by file path or credential command (`secret = { command = "…" }`).
  - btq, Hermes and `wn-agent` are **external integrations** whose endpoints and credential locations come from host config. They are not vendored or assumed.
- **Enforcement:**
  - `.gitignore` excludes the host layers.
  - A CI job, plus a pre-commit hook, fails on absolute home paths, IP:port literals, npub/DID/key patterns and secrets (gitleaks) outside `examples/` and test fixtures. Examples use placeholders only.
  - `heterodyne config check` validates the merged config and prints each value with its source layer.
  - `heterodyne setup` writes the host layer interactively. It seeds from an example, and can import values from an existing install, which on the reference install means the current btq, `policy.json` and Marmot settings.
- **Migration of our install:** the reference install's settings (paths, groups, approvers, Dolt endpoint, model roles) are written to its host layer by `heterodyne setup`, and never committed.

## 16. Language and frameworks

- **Python 3.12+ for v1.** This is the path of least effort.
  - btq's `Queue` library, the Hermes gatekeeper interface and the Marmot/`wn-agent` client are already Python, so `wsd` can call them in-process instead of re-implementing them.
  - The daemon is I/O-bound (sockets, subprocesses, SQLite), which `asyncio` handles well.
  - The standard library covers the core: `asyncio`, `sqlite3`, `tomllib`, `uuid`.
  - Runtime dependencies are kept small: `msgspec` for typed events, action payloads and config schemas, with strict decoding at every trust boundary.
  - Development uses `pytest` and `hypothesis` (for the decision queue and state machines), with `ruff` and `pyright` in strict mode as the gate.
  - Packaged with `uv` and `pyproject.toml`, exposing the console scripts `wsd`, `wsctl`, `wsd-act`, `admind`, `ws-hook`, `ws-request` and `heterodyne`.
  - Service units (systemd, launchd) are generated by `heterodyne setup` from templates, not shipped as fixed files.
- **One planned exception: `ws-hook` in Rust**, as a follow-up once the shim's wire protocol is frozen.
  - It runs on every agent tool call, inside the sandbox. A static binary means no interpreter startup (tens of milliseconds per call), no dependence on the sandbox's Python, and a minimal attack surface on the security boundary.
  - v1 ships a Python shim with the same socket protocol, so the swap needs no other change.
- **Not chosen:**
  - Go or Rust for `wsd`: it would re-implement the btq and Hermes client code for no v1 benefit.
  - TypeScript: it would add a second runtime without reuse.
  - `wsd-act` in Rust: its surface is small, so it can be revisited if it grows.

## 17. Open decisions for the operator (revisions 14–15)

These are not decided by this revision. Each needs the operator's answer; where the text above assumes an answer, it says so. (Whether the gatekeeper becomes mandatory for relayed asks, and when, is decided in §5.9 and is no longer listed here.)

1. **An `account_changed` wait is never retried on a timer** (§4.3, §4.4 D5, §10). It is re-gated only at startup, on a configuration reload or on a release. A bead can therefore wait indefinitely after an account is repointed until the operator acts; the alert and the card's recovery condition are the only prompts. The alternative is a slow periodic re-gate (for example hourly), which would also undefer it automatically once the login is restored. This revision is written for "never on a timer"; please confirm or choose the alternative.
2. **S5 fallback and timebox** (§7). No longer open (revision 15). S5 passed, so there is no timebox to set. Bubblewrap stays the fallback, under the conditions §7 now states, and approving this revision confirms them. The one S5 decision left is the direct-network proof itself (§7), which approving this revision accepts.
3. **Host changes for OpenShell** (§7 S5 item 5). Settled on 2026-10-08 (recorded in revision 15). The operator chose a user-level podman 5.8.8, installed alongside the system podman and serving only the service user's socket. The system podman and its containers are unchanged.
4. **OpenShell on macOS** (§3.2). Whether OpenShell replaces Seatbelt is left to the phase 2 macOS work; its macOS support is unverified.
5. **Credential injection for model auth** (§7). v1 keeps read-only login files. Moving model auth to OpenShell's proxy injection would remove the token from the sandbox but changes the freshness and account rules; it needs its own revision. S5 adds that the supervisor already sees plaintext model traffic, so it is inside the credential trusted computing base either way (revision 15).
6. **Latch visibility** (§8.1). A latched admind is silent, so operators can't tell from Marmot that it is latched. Options: stay silent (revision 13); post one fixed, content-free notice when it latches ("admind is latched; recovery needs `admind rearm` on the host"), which also tells a swapped-in member they were noticed; or answer `!status` with that fixed line while latched.
7. **Host exceptions** (§8.1). Marmot is primary, and content that can't be shown safely goes back to its originator for grooming, never trimmed. The host stays necessary for seven things: install and setup; trust changes (`admind operators add|remove`, `admind rearm`), because the group must not be able to re-trust itself; terminal approvals for content that still can't be shown after grooming; reading refs marked "read on the host"; startup break-glass; resolving `blocked` relay decisions (R12); and reading withheld sensitive alerts. Confirm the list, or name an exception you want removed (and accept its cost: for example, no terminal approvals means such a bead can't be approved at all).
8. **Progress reactions** (§8.1). Ship them add-only (each state adds a reaction and none is removed) now, or wait until S8 shows `remove_reaction` works.
9. **The interim relay's future** (§5.4, §8.2). When `wsd`'s decision queue ships, retire the relay's approval kind, or have admind submit its decisions to that queue.
10. **The release command's name** (§6.3). Plan 6 names it; this revision only fixes what it does.
11. **Tools reaching the model host** (§7; revision 15). Under OpenShell, every tool the agent runs can reach its adapter's model host, because a binary's egress grant covers its process tree. This revision accepts that as residual risk, beside the existing risk of exfiltration through allowlisted endpoints. If tools must not reach the model host, OpenShell's per-binary policy is not enough, and another control needs its own revision.
12. **If plan 4 can't protect the probe** (§7 Probe protection; revision 15). Plan 4 may find that neither a supervisor-controlled probe path nor tamper denial can be shown. The agent-path result is then not trustworthy, and plan 4 stops. The options are: accept a launch gated only by the fresh-process self-test, with the agent path's policy unproven, as documented risk; find another mechanism in a later revision; or fall back to bubblewrap, whose policy doesn't depend on the process tree. This revision chooses none of them in advance.

## Appendix A. Amendment records

Each amendment folded into this ADR from outside material records here where that material landed (revision 15).

### A.1 Accounts amendment: review r6 fixes (re-reviewed in G1)

The amendment's review r6 (`docs/reviews/0001-accounts-amendment-r6.md` in the product repository) returned REVISE with three major and two minor findings. The fixes below were made after it and merged here. The G1 cross-model review of this revision re-reviewed them (`docs/reviews/0001-design-review-r21.md` to `r24.md`): round 1 found that fix 2 needed revising (below), and rounds 2–4 confirmed all five.

1. **Quota to `account_changed`.** §4.4 D5 journals one superseding `account_changed` record with its one comment and one alert; a superseded record is never due. Crash tests in both directions (§11; change-plan AU-4).
2. **Crash after dispatch.** §4.4 D2: a dispatch mark, and reconciliation against generation-specific runtime evidence; unresolved entries hold, and D4 never gates past them. Tests for a surviving and an ended session, and for a replacement under `"none"` (§11). **Revised after the G1 r1 review of this revision:** the r6 fix accepted tagged hook-spool events and transcripts as evidence, which §3.3 forbids. Reconciliation now trusts only a launch receipt that `wsd` journals on the host from the runtime's own spawn result; spool events, transcripts and tags stay advisory, and a missing receipt holds the bead.
3. **Legacy sessions.** §4.4 D2 and §3.3 adopt sessions launched before AU-3 in the upgrade transaction, verified only on the default login; anything else holds for operator recovery.
4. **Untrusted table.** §4.4 D3: separate tables with their own replacement keys; AU-3's upgrade names `account_usage_untrusted` and `deferrals`.
5. **Deferred cards.** §6.2, §6.3 and §4.4 D10 render `account_changed` with its recovery condition and no time; AU-9's fixtures include its alert.

Reviews r1–r5 of the amendment and the responses to them are in the proposal file (`docs/adr/proposals/0001-accounts-and-usage.md`, product repository), which this revision supersedes once approved.

### A.2 S5 amendment: spike findings (revision 15)

Spike S5 (`docs/spikes/s5-openshell.md` in the product repository, bead btq-le4mq, reviews r1–r3) lists eleven "ADR impact" items. This is where each one lands in this revision.

1. **Egress refusal evidence and the direct-network deviation.** In §7 Launch self-test, the Egress probe gains its OpenShell evidence, and the Direct network probe gains its OpenShell form: the broker's specific answers, seccomp evidence inside and `NetworkMode=none` from outside. §13 records S5 as passed under this proof, and §7 Fallback covers its rejection.
2. **OpenShell's fixed injected environment.** §7 Environment, "The runtime's injected set".
3. **Authority and SNI bound at the proxy, and the supervisor in the credential trusted computing base.** §7 Network ("Under OpenShell") and Residual risk; §17 item 5.
4. **The Codex socket directory.** §4.2 Steer, and §7 Runtime ("the rules above, in OpenShell's terms").
5. **Codex hooks in interactive sessions.** §4.2 Pre-tool hook and Hook trust, §5.3 and §13 S1. Codex sessions stay treated as headless until plan 4 establishes hook trust without an operator step.
6. **Single-file read-only binds pin the inode.** §7, the freshness gate.
7. **Operational limits.** §7 "Plan 4 notes for OpenShell": names, asynchronous delete, the log buffer, `sandbox exec` stdin.
8. **The lifetime stop.** §7 "Plan 4 notes for OpenShell". The turn-boundary rule of the freshness gate is unchanged.
9. **Per-binary egress covers the process tree.** §7 Network ("Per-binary grants cover the process tree"), the two execution paths of the launch self-test, Residual risk, and §17 item 11.
10. **Not covered by S5.** §7 "Still to verify for v1": credential injection, macOS and the Codex diagnostic-log warning. Package registry and git egress, and the reviewer's read-only bind, were already listed there.
11. **A launcher-owned result channel and the per-version tool environment.** §7 Launch self-test ("Result channel" and "Probe protection"), and §7 Environment ("The agent's tool path"). §11 adds the agent-path controls, including the tampering attacks S5 disclosed but did not test, and §17 item 12 covers the case where plan 4 can't meet the condition. §7 "Plan 4 notes" covers the stdin rule for `sandbox exec`.

S5's host change, the side-by-side podman, is recorded in §7 S5 item 5 and §17 item 3.
