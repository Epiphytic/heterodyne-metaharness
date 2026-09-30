# ADR 0001: heterodyne-metaharness (workstreams v2)

- Status: Proposed, revision 10 (adds §5.9, the approval-ask context rule). Cross-model review r9 approved revision 9 (`docs/reviews/`; responses in `0001-design-review-r1-response.md`). Waiting for operator approval.
- Review process (set by the operator, 2026-09-29; applies to every agent and harness): **every change is reviewed by a different LLM than its author whenever possible, otherwise by an adversarial fresh-context agent** (§11.1). The two-model brainstorm requirement is retired.
- Date: 2026-09-29
- Author: Claude Opus 5.5 (brainstorm with the operator)
- Replaces: `hermes-workstream-harness` and its `harness-improvements` and `harness-dev` clones

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
6. **`admind`, an independent admin override channel.** The operator's text goes unmodified to a superuser admin agent (itself an LLM), with no beads and no gatekeeper LLM between them.

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

 admind (independent service): own Marmot identity + 2-member group ─► superuser agent
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
| `admind` | The admin override channel (§8). | Passthrough only |

**Core rule:** `wsd` calls the gatekeeper; the gatekeeper never drives `wsd`. If Hermes is down, everything deterministic keeps working: commands, delivery, operator approvals, cron, and pickup of beads that are already ready.

### 3.2 Platform seam

A host is either Linux or macOS. A workstream's platform is its host's platform, and it never changes. At install time the platform selects:

| Concern | Linux | macOS |
|---|---|---|
| Service manager | systemd user units | launchd agents |
| Sandbox backend | bubblewrap | Seatbelt (`sandbox-exec`, generated profile) |
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

The SQLite journal is backed up with the beads backups. The recovery order on startup is: journal integrity check, then read beads, then reconcile actions in `executing`/`uncertain`, then resume park journals, then reconcile sessions, and only then accept events.

### 3.4 Operator identity and ingress authentication

- **Allowlist:** `policy.json` maps each approver to their identities (Marmot npub, GitHub login, Radicle DID). The btq approver name, the Marmot sender and the forge reviewer must resolve to the same approver entry.
- **Marmot:** ingress accepts commands, approvals and steering only when **all** of these hold:
  - the MLS-authenticated sender pubkey (reported by `wn-agent`, never parsed from message text) is on the allowlist;
  - the group ID is a registered workstream or control group;
  - the message ID has not been seen before (replay protection).
- **Membership changes:** a member joining or leaving a registered group, or an identity change, raises an alert in the control group. Approvals from that group are suspended until the operator runs `/trust-group`.
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
```

- **Review mode is derived, not declared.**
  - When `wsd` starts a review, it compares the coder's and reviewer's resolved `model`. If they differ, the review is `cross-model`. If they match, including after a fallback, it is an adversarial fresh session, or the review is refused if `mode_when_same_model = "block"`.
  - **Evidence comes from what ran, not from config.** At launch, `wsd` records a launched-session record on the bead: profile, adapter, the exact model passed on the command line, the model reported in the session transcript or hook payload, and the session ID. For reviews it also records the reviewed `BASE..HEAD`, the findings, and their disposition.
  - The `Code-Review:` line is generated from the reviewer's and author's **recorded** models. If the configured and reported models disagree, the review is invalid and is re-run.
- **Validation at startup and on reload:**
  - every role resolves to a profile, and every profile to a known adapter;
  - models the adapter can list are checked when possible;
  - an invalid config is rejected whole, and the last good config stays active.
  - `/workstreams` shows each workstream's resolved coder → reviewer pairing.
- **Changing configuration:** role and profile changes are harness configuration, which is hard-deny for agents (§5.3). They are made by the operator, through `admind`, or through an approved policy bead.
- Everything above `AgentRuntime` is agent-agnostic.
- **Session identity** is `uuid5(NS, f"{bead}:{role}:{profile}")`, labelled `<short-bead> · <role> · <title>`. The label appears in the session name, the tmux window, the Marmot thread header and `/status`.
- **Swapping agents** is done with a deterministic handoff built from the bead: description, acceptance criteria, comments and decisions, plus `git log` and diffstat on the bead branch. The new session starts from that. The old transcript is not needed, so a swap works even after a crash or context overflow.

### 4.2 Adapter capabilities (verified against the installed CLIs, 2026-09-29)

| Capability | Claude Code 2.1.283 | Codex 0.157.0 |
|---|---|---|
| Launch with a fixed ID | `--session-id <uuid>` | Spike S1 (fallback: record the assigned ID on the bead) |
| Display name | `-n/--name` | Session name (`resume`, `archive`, and `queue` accept a name) |
| Resume | `--resume <id>` | `codex resume <id\|name>` |
| Steer a live session | Channels (research preview) through hermes-channel; fallback `tmux send-keys` | `codex queue --thread <id\|name> --message` |
| Permission mode inside the sandbox | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` (`--yolo`) |
| Pre-tool hook (policy UX layer) | `PreToolUse` → allow / deny with reason (fires in every mode) | Spike S1 (`hooks` stable; pre-tool event to be confirmed) |
| Lifecycle hooks | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, Notification | session_start, user_prompt_submit, stop, pre/post_compact (in use today) |
| Hook trust | Settings file | Trusted hash per hook in `config.toml` (the existing `hook_config.py` logic is carried over) |

Both CLIs run interactively and unmodified, so subscription-plan auth is preserved. The operator can attach to a session with `tmux attach` and take over at any time.

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
    - Pausing stops new claims only. Running and parked beads continue, unless the operator uses `/stop`.
  - v2-managed beads carry `agent:wsd`. Which coding agent does the work is role configuration (§4.1), not a bead label.
  - Adding the `wsd` identity (a Dolt user plus a btq `AGENTS` entry) is an operator setup step.
  - Agent sessions are children of `wsd`, not btq identities. The claim belongs to `wsd`, not to an agent session, so it survives session crashes, swaps and parks.
- **Worktrees use btq's convention** (`btq worktree`: `<repo>-btq-<id>` on branch `btq/<id>`), so there is only one convention. There is one session per (bead, role) in that worktree.
- **Parking** leaves the bead **claimed by `wsd`** (`in_progress`), labelled `v2:parked`, with a blocking edge to what it waits on. `wsd` never unclaims, which respects PICKUP.md.
  - The park sequence is journaled (§3.3): record intent, commit WIP (recording the SHA), apply the label, add a bead comment. Each step is idempotent and replayed after a crash.
  - A queue write with an uncertain outcome is read back before any retry.
- **Resumable beads** are `wsd`'s own query: beads it has claimed that carry `v2:parked` and whose blocking edges are all closed. They are resumed with the same session ID in the same worktree.
- A workstream runs **at most one active session per role**. A reviewer can review bead A while the coder works on bead B. Running several coder sessions at once is out of scope for v1.

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

- **Triggers:** a turn ends, a bead closes or parks, an approval resolves, or the 60s backstop timer fires.
- When the coder role is idle, `wsd` takes the next bead from two sources, in priority order:
  1. its own resumable parked beads (§4.3);
  2. new work from `Queue('wsd', ws, uuid5(NS, ws)).ready()`, the btq library, equivalent to `btq --agent wsd --ws <ws> --session <uuid> ready`. Each chosen bead is then claimed with `Queue('wsd', ws, uuid5(NS, f"{ws}:{bead}")).claim(bead)` (§4.3).
- Ties go to resumable beads. For new work, `wsd` runs `claim`, then `worktree`, then launches the session. The task card reacts ⏳.
- A parked bead that becomes resumable never pre-empts the running task. It is picked up at the next task boundary.
- A workstream is **idle only when** no bead is ready and none is in progress. `/workstreams` reports it as "all-blocked: N beads on M approvals".

### 5.3 Permission request

**Enforcement model.**
- Agents run fully permissive inside the external sandbox (§7): Codex with `--dangerously-bypass-approvals-and-sandbox` (`--yolo`), Claude with `--permission-mode bypassPermissions`. Neither raises permission prompts.
- **The sandbox is the security boundary.** It contains no git push credentials at all, and no deploy tokens, messaging tokens or secrets. Agents commit to the local `btq/<id>` branch in their worktree, and **nothing is pushed automatically**, consistent with PICKUP.md.
  - The only way anything leaves the host is an approved `push_branch`, `open_pr` or `merge_pr` action, executed by `wsd-act`. In v2, this approved path is how completed worktrees get integrated, the role PICKUP gives to Bel.
  - The only credential inside the sandbox is model auth (§7).
- The `PreToolUse` hook, which fires before every tool call regardless of permission mode, is the **UX layer**. It recognises operator-only intents early and turns them into clear, parked asks, instead of letting them fail obscurely against the sandbox. Matching commands in the hook is not a security control.
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
- **Running bead:** deliver through the adapter's steer path (Claude: channel; Codex: `codex queue`).
- **Parked or queued bead:** attach the text as a bead comment, delivered on resume.
- The bead comment records both the original text and the rewrite.

### 5.7 Stop without close (answer and nudge)

- The Stop hook fires while the bead is still open.
- **Progress** is measured deterministically: new commits on `btq/<id>`, or a change to the acceptance-criteria checklist.
- The gatekeeper classifies the stop as a *question* (answered from the bead, repo docs and `workstream-recall`), *stalled* (it gives a concrete next step) or *done without evidence* (it asks for close evidence). Its reply is delivered through the steer path and posted in the thread.
- After 3 nudges without progress, the bead is marked `needs-human`, a question card goes to the control group, and pickup continues.

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

- **No internal IDs** other than a short bead ID. No UUIDs, hex strings, pane dumps or instructions meant for agents. There are two exceptions, both on approval cards: the 12-character `context_digest` (§5.9), and short commit SHAs inside pinned permalinks.
- **An approval card** renders the five required parts of §5.9: *what*, *exact effect* (in a code block), *why*, *context*, and *pinned links* as permalinks. It then adds *how to answer* ("👍 approve · 👎 deny · ❤️ always · reply to steer") and the short `context_digest`. If the context is too long for a card, the card shows a summary plus pinned links, and the full text lives on the approval bead, which the digest covers.
- **Length:** at most about 8 lines per message. Detail goes in `/status <bead>`.
- **Task-card reactions:** 👀 received, ⏳ running, 🔍 in review, ⏸️ parked, ❓ needs operator, ✅ closed, ❌ failed.
- **Reminders:** one reminder after 4h, as a reply to the original card, without repeating its content. After that, only the daily digest.
- **Delivery that can't be confirmed:**
  - If no surface has a confirmed delivery receipt for an approval or question card within 10 minutes, `wsd` raises a delivery alert in the control group.
  - If that also can't be confirmed, `wsd` writes a local alert file. `admind` watches the alert directory on its own and pushes it to the operator. This path doesn't depend on `wsd` or the gateway's Marmot connection.

### 6.3 Commands (deterministic, target reply under 1s)

| Command | Output |
|---|---|
| `/workstreams` | One line per workstream: state (running, idle, all-blocked or paused), current bead, and ready, parked and approval counts. |
| `/status` | In a workstream group: the running bead and its duration, the ready queue, parked beads with reasons, open approvals. In the control group: `/workstreams` plus every open approval and `needs-human` item. |
| `/status <bead>` | State, branch, session label, the last 5 events, decisions, the transcript path. |
| `/approvals` | Open approvals, oldest first. |
| `/approve`, `/deny` (as a reply) | Resolves the targeted approval bead. |
| `/pause`, `/resume` `[ws]` | Stops or restarts pickup. The running task finishes its turn and is then parked. |
| `/stop <bead>` | Parks the bead immediately: interrupt, then commit the WIP. |
| `/queue [ws]` | The ready list in pickup order, with blockers. |

- Commands are answered from the `wsd` cache, which is refreshed from beads if it is more than 30s old.
- If beads is unreachable, the answer comes from the cache with "⚠️ beads unreachable, data as of HH:MM".

## 7. Sandboxing

- **v1:**
  - A workstream-level, platform-neutral `sandbox.toml`: writable paths (worktree, tmp, package caches), read-only binds, and a network allowlist.
  - It is compiled at launch to bubblewrap arguments on Linux or a Seatbelt profile on macOS, and wraps whichever agent CLI is launched.
  - Coder and reviewer share one profile; the reviewer's worktree bind is read-only.
  - Inside the sandbox both agents run fully permissive: Codex `--yolo`, Claude `bypassPermissions`. Neither agent's native sandbox is used.
  - The sandbox is the only security boundary (§5.3). **The only credential inside is model auth.** There is no git push, forge, deploy, messaging or secret material.
  - **Home isolation:** the sandbox gets a synthetic `$HOME` containing only the agent's config and auth files, copied in read-only. The real home (including SSH keys, forge CLI tokens and the queue client's credentials), other workstreams' worktrees, and the `wsd` journal are not mounted.
  - **Network:** an allowlist enforced by a per-sandbox egress proxy, not just DNS. It covers model endpoints, package registries and the git remote over read-only fetch.
  - **Session socket, not the `wsd` socket:** each session gets its own socket, bound in, authenticated by a per-launch token. It accepts only that session's hook events and `ws-request` calls, and exposes **no** control operations: no approve, no config, no other sessions.
  - **Residual risk, accepted and documented:** a compromised agent could exfiltrate its own model credentials, or repository content, through allowlisted endpoints. That's mitigated by per-sandbox credentials where the provider supports them, and by the egress proxy's logs.
  - **Launch self-test (fail closed):** before starting an agent, the runner probes from inside the sandbox. Reading a canary file in the real home must fail, reaching a non-allowlisted host must fail, and calling a control operation on the session socket must fail. If any probe succeeds, the launch is refused. S3's acceptance criteria are exactly these probes on both backends.
- **Why an outer sandbox:** a single boundary to review means swapping agents never changes the security posture. The agents' native sandboxes have different semantics.
- **v2 (future):** a `CubeRuntime` (TencentCloud CubeSandbox microVMs, Linux/KVM only) behind the same interface, with credentials injected at the egress proxy and a snapshot on park.

## 8. Admin override channel (`admind`)

- **Independent:**
  - Its own service unit, sharing no dependency on `wsd`, the Hermes gateway, beads or the gatekeeper.
  - Its own Marmot identity and connection, in a two-member MLS group of the operator plus the admin bot.
  - It refuses to operate if the group has more than two members, and accepts messages only from the operator's exact npub. Anything else is dropped and logged.
- **Passthrough:**
  - Operator text goes byte-for-byte into a persistent interactive session of the `admin` profile (initially `claude-opus`; any adapter can be configured).
  - Replies come back verbatim, split into chunks, as thread replies.
  - The agent runs as the harness's service user (the operator's login user on the reference install) with permission prompts bypassed, no sandbox, and no root. **This is a deliberate operator decision** (2026-09-29): its purpose is to repair anything the harness can break, so a least-privilege identity would defeat it. The review's objection is recorded and rebutted in the r1 response.
  - **Mitigations that keep the purpose intact:**
    - the sender must be MLS-authenticated as the operator's npub (§3.4);
    - the two-member group check;
    - admind's own Marmot identity keys are readable only by the admind unit;
    - every message and action goes to the append-only log.
- **Built-in commands (no LLM, `!` prefix):**
  - `!new` starts a fresh session
  - `!interrupt` sends Esc
  - `!tail [n]` shows the pane tail
  - `!restart <unit>` restarts a unit from a fixed allowlist in admind's config: `wsd`, the Hermes gateway, and runner units
  - `!ps` shows unit health
- **Audit:** an append-only local JSONL log (timestamp, sender, text, action) plus the agent transcript. It is kept separate from beads on purpose.
- **Alert relay:** admind watches `wsd`'s local alert directory and pushes new alerts to the operator (§6.2).
- **Build order:** `admind` is built early, right after the spikes, so every later step has a recovery path.

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
| `wsd` down | The service manager restarts it, and agents keep running. The hook shim waits up to 5s, then **fails closed**, with one exception: tool calls the shim can classify locally, from a cached copy of the auto-approve tier, as sandbox-confined (file edits in the worktree, running tests, local git). Everything else is denied with "control plane unavailable; retry shortly". Spooled events are untrusted observations (§3.3). |
| Agent or runner crash | Reconcile resumes `uuid5(bead, role, profile)`. After 2 failures the bead becomes `needs-human`. |
| Reboot | Units start, reconcile resumes in-progress beads, and parked beads wait on their blockers. |
| Dolt unreachable | Pickup, close and **new approvals** pause. Decisions and actions need the bead write first (§5.4), so they wait. Commands use the cache with a warning. Hooks decide from policy plus cache. Audit comments go to the spool and are replayed later. |
| Marmot relay down | The outbox retries with backoff, and work continues. |
| Forge unreachable | Forge approvals are delayed. Marmot approvals still work. |
| Everything wedged | `admind`. |

## 11. Testing

- **Unit:**
  - router classification
  - policy tiers
  - the approval state machine, including races between Marmot and forge
  - materiality handling
  - the nudge counter and progress detection
  - session ID derivation
  - sandbox profile compilation for both backends
- **Golden:** every renderer event type has a fixture showing exactly what the operator sees.
- **Integration:** a fake `claude` and `codex` that emit hook events, a fake gatekeeper, a mock Marmot, a fake forge, and a throwaway Dolt. They exercise flows 5.1–5.8 and every row in §10.
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
- Linux host only: systemd and bubblewrap.
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

## 13. Spikes (before implementation beads)

- **S1, Codex parity:**
  - Does 0.157.0 have a pre-tool hook event that can deny with a reason under `--yolo`? If not, the UX layer for Codex falls back to catching the sandbox failure after the fact and parking the bead. That is acceptable, because the sandbox is still the boundary.
  - Can `--dangerously-bypass-hook-trust` replace per-hook trust hashes for runner-launched sessions? The hooks are generated by `wsd`, which vets their source.
  - Can the session ID or name be set at launch?
  - Does `codex queue` deliver into a live interactive TUI session?
- **S2, Claude channels** (phase 2 gate): a custom hermes-channel MCP server under subscription auth. v1 uses `tmux send-keys` for the Claude reviewer.
- **S3, sandbox:** run both CLIs inside bubblewrap (v1) and Seatbelt (phase 2 gate) with synthetic home, egress proxy and session socket. Acceptance is exactly the §7 launch self-test probes plus a working login and hooks. If a backend can't pass, that platform doesn't ship.
- **S4, Marmot:** threads (reply-to) and reactions end-to-end on the current mdk bindings, for both a harness identity and a separate `admind` identity.

## 14. Migration

1. **Stabilise the old harness now (independent fixes):**
   - fix exit 126 on `workstream`
   - point the admin lookup at the live Marmot home
   - these confirm spike S4
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
  | 2 | Host config | `$HETERODYNE_CONFIG_DIR`, default `${XDG_CONFIG_HOME:-~/.config}/heterodyne/` on both OSes: `config.toml` (host settings, profiles, default roles, platform backends, integrations) and `policy.toml` (approvers and identities) | **No** |
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
  - **What workstream config may override:** role and profile choices, repositories, sandbox *additions* within the host's allowlists (extra read-only mounts, extra egress hosts from a host-approved list), cron jobs, rendering, and timeouts.
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
