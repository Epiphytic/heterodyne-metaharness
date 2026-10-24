# admind: the admin override channel

This is the operator runbook for `admind` (ADR 0001 §8, revision 13). The ADR is authoritative; this page describes what the code does. Placeholders in `<angle brackets>` stand for values that belong to your install and never go in git. `<state>` is the state directory: `$HETERODYNE_STATE_DIR`, else `${XDG_STATE_HOME:-~/.local/state}/heterodyne`.

## 1. What it is

`admind` is the recovery path for when everything else is broken. It runs as its own service unit with its own Marmot identity, in a group made of the admin bot and one or more operators. The operator's text goes unmodified into a persistent interactive session of the admin agent (an LLM CLI running in a private tmux server), and the agent's replies come back to the group, verbatim when short, otherwise summarized or batched (section 4), and where possible as thread replies. A handful of `!` commands (`!new`, `!interrupt`, `!tail`, `!ps`, `!restart`, `!details`) are handled by admind itself and need no LLM. admind also relays `wsd`'s local alert files to the operator (section 6), and relays questions, merge requests and btq design approvals from local processes (section 10, interim).

**Independence** (ADR §8). admind:

- has its own service unit and shares no dependency with `wsd`, the Hermes gateway, beads or the gatekeeper;
- has its own Marmot identity and connection, through a private `wn-agent` child that it spawns and supervises itself (own home, own socket, own bearer token);
- requires a live acknowledged subscription and verified positive member count, and accepts authenticated operator keys only when persisted confirmations and fresh policy both authorize them. Counts/events do not identify operators or latch admind. Unauthorized input is dropped and logged without its content.

**Deliberate privilege.** The admin agent runs as the harness's service user, with permission prompts bypassed, no sandbox and no root. Its purpose is to repair anything the harness can break, so a least-privilege identity would defeat it. This is an operator decision (2026-09-29). The design review's objection and the rebuttal are recorded in the [r1 response](reviews/0001-design-review-r1-response.md) ("Finding 10: admind"); the accepted residual risk is repeated in the [security model](security-model.md#the-admin-override-channel-admind-8).

## 2. Install

1. **Host config.** In `config.toml`, add `[admind]` and `[admind.marmot]` (see `examples/config.toml` and [configuration.md](configuration.md#admin-channel-admind)). `profile` names a profile whose adapter is `claude-code`; `marmot.relays` lists your relays; `restart_units` lists the only units `!restart` will accept. In `policy.toml`, list one or more names under `operators` and give each an `identities.<name>.marmot_npub` (see section 2, "Operators"). `summarizer` and the `reply_verbatim_*` keys are optional (see [configuration.md](configuration.md#admin-channel-admind)). `[platform] service_manager` must be set (run `heterodyne setup` first).
2. **Create the identity and group.**

   ```sh
   admind init
   ```

   This starts the private `wn-agent` child, creates admind's identity (if its home has none) and a group with every operator in `policy.toml` that has a `marmot_npub`, and stops the child. The bootstrap output holds invite details, so admind captures it and prints nothing from it. Each operator then accepts the invite in their Marmot client. `admind init` refuses to run again once a group exists; to start over, stop admind and remove `<state>/admind/` (this abandons the identity and group).
3. **Render the unit.** The platform installer, `deploy/install.sh` ([deploy/store-helper/README.md](../deploy/store-helper/README.md)), does this step and the next: it renders the unit from its release, replaces a hand-made one only with `--adopt` (keeping a backup), and enables and starts it once `admind init` has run. It asks `admind initialised`, which reads a consistent snapshot of the database in a private copy and changes nothing: exit 0 initialised, 1 not, and 2 can't tell (a write was in progress, say), in which case the install fails and you run it again. Rendering it by hand still works:

   ```sh
   admind unit > ~/.config/systemd/user/heterodyne-admind.service
   ```

   `admind unit` captures the current shell's `PATH`, so render it from a shell where `claude` and `wn-agent` resolve. It resolves the config and state directories from its environment, as heterodyne does, and writes them into the unit explicitly (`HETERODYNE_CONFIG_DIR`, `HETERODYNE_STATE_DIR`), with `UnsetEnvironment=BTQ_REPO XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME`, so the user manager's environment can't move them. The installer's form takes them from `--config-dir`, `--state-dir` and `--release` instead. The unit is never committed. `admind unit` **refuses** (exit 78, `EX_CONFIG`, nothing on stdout) when any captured value contains whitespace, a quote, a backslash, a control character, or sensitive material (a secret, an npub or a 64-hex value). It does not redact, because the unit must be installable verbatim: fix the path or environment and render again. The unit has `RestartPreventExitStatus=78`, so a configuration error does not restart-loop.
4. **Enable it** (by hand; the installer does this itself).

   ```sh
   systemctl --user daemon-reload && systemctl --user enable --now heterodyne-admind
   ```

5. **Join signal.** Send any message to the group. admind posts nothing until it has seen a message from an operator (the join signal, D5; the operator acceptance run verifies this against a live client). It then replies with the ready notice and releases any held alerts.

### Operators

Every name under `operators` in `policy.toml` with an `identities.<name>.marmot_npub` identifies a configured operator. Names must be 1 to 128 characters without control characters, keys must be unique, and at least one operator must exist. Effective authority is the persisted `group_operators` confirmed set intersected with freshly validated policy. Policy is read at potentially authorized message/reaction ingress and immediately before each command, paste, answer, approval, details operation and outbound chunk, including after lock waits. Removing a policy entry revokes its next effect without a restart; adding an entry alone grants nothing. An unreadable/invalid policy closes the current gate without a permanent latch; a later valid read restores operation.

Membership conveys read access, not command authority. Observed joins, departures and counts never add or remove confirmed keys. A departed key may remain confirmed until policy explicitly revokes it; MLS protects subsequent group epochs. Neither a count nor an event identifies which operator left.
Change the group with the running daemon, as the service user. `admind` is a console script of the heterodyne package, so it is on `PATH` only where the package's environment is (an activated virtualenv, or its `bin/`); `python -m heterodyne.admind` with that environment's interpreter is equivalent:

```sh
admind operators add NAME      # NAME is a policy.toml operator; admind adds its npub to the group
admind operators remove NAME   # NAME is a policy operator; it may be unconfirmed
```

Both ask the running daemon over its host control socket (section 7). Local changes are journaled and serialized with dispatch/sends. Explicit wn-agent success commits only the requested confirmed-set addition/removal; explicit peer failure leaves the set unchanged. Events or a different post-count cannot reverse that result. Without a valid post-count, successful changes may record a provisional expected count; the next valid observation replaces it. A timeout, cancellation or missing response leaves an unresolved membership-only hold, never retries the mutation and grants no authority. Ordinary confirmed-policy work resumes after group verification, but further membership mutations need explicit recovery.

Removing the last policy-confirmed operator remains forbidden, and local mutation retains its minimum target-count safeguard; accepting observed count 1 or rearming at 1 does not authorize an unsafe local removal. Removing an unconfirmed policy key is permitted for recovery and leaves the confirmed set unchanged on success. Unknown/non-policy keys are refused. The result is printed and audited as a `membership` record.
### Temporary operators

To let a test key command admind for a while:

1. Add the name to `operators` **and to `approvers`** in `policy.toml`, with an `identities.<name>.marmot_npub`. Every operator must also be an approver, or the configuration is invalid: `admind operators add` refuses with "not in approvers", and a restart while the file is in that state fails to start.
2. Run `admind operators add NAME`.
3. Run the test.
4. Run `admind operators remove NAME`.
5. Delete the entry from `policy.toml` (from `operators`, `approvers` and `[identities.<name>]`).

Keep this order when removing both membership and policy trust. Policy deletion itself immediately revokes subsequent effects; group removal additionally removes read access. Removal resolves the exact key from fresh policy, so retain the entry until the membership operation completes. If an unknown add actually succeeded but a duplicate add is rejected, verify membership in your client and explicitly rearm, remove the unconfirmed policy key, then add it again. Only the add's own explicit success confirms it. A failed remove grants nothing and is not proof of absence.
## 3. Before first use of the admin agent

On its first launch in `workdir`, Claude Code may show its bypass-permissions acceptance and workspace-trust dialogs. They appear before any hook runs, so admind cannot relay them to Marmot. Clear them once, as part of setup on the host, before the operators start using the agent:

```sh
tmux -L heterodyne-admind attach -t admin
```

Accept the dialogs, then detach with `Ctrl-b d`. Until then the agent cannot start a turn, and `!tail` shows the dialog on the screen.

This is a setup step only (ADR 0001 §8.1, host exception 1). In operation, operators work through Marmot, and attaching to the agent's tmux session is break-glass: its trigger is an agent that cannot start because of something only its terminal can clear, such as a dialog that came back (host exception 5, and `AgentStuck` in section 8). ADR revision 14 replaces this attach step with pre-accepting the dialogs through the CLI's own configuration and a host preflight that checks one launch reaches `SessionStart` with no terminal input. That preflight, and its `not-started` hold with a single relaunch, are specified but not built yet (§13 S8); until then, the steps above and the `AgentStuck` behaviour in section 8 are what you get.

**Use a dedicated `workdir`.** Claude Code does not persist workspace trust for the home directory, so with the default `workdir = "~"` every relaunch blocks on the trust dialog again, reaches the 120-second readiness timeout three times and ends in `AgentStuck`. Set `[admind] workdir` to a dedicated directory (for example `~/admin-workdir`), create it, and accept the dialogs there once during setup; trust is then remembered.

**The agent outlives admind.** On systemd, admind starts the private tmux server through `systemd-run --user --scope`, in a transient scope named `heterodyne-admind-tmux-<random>.scope` (a fresh name per start). Every session start goes through a scope, never inside the unit; if a server is already running, the short-lived wrapped client just asks it and its scope is collected when it exits. The server and the agent therefore sit outside the `heterodyne-admind.service` cgroup, so `systemctl --user stop` or `restart heterodyne-admind` leaves the agent running and the next start adopts it (section 4, "An adopted pane is held at startup"), without killing a turn in flight. `wn-agent` stays inside the unit and dies with it. The unit keeps `KillMode=control-group`, which is now correct. If `systemd-run` is missing or cannot create the scope, starting the agent fails with a fixed error ("tmux server could not be started through the launcher"); there is no fallback to a server inside the unit. On launchd nothing special is done: tmux daemonises on its own and launchd does not kill by cgroup.

## 4. Using it

- **Passthrough.** Any message from an operator that does not start with `!` is pasted, byte for byte, into the admin agent's session. Messages are delivered one at a time, only to an idle agent (see the latch and the held queue below).
- **Thread replies.** The agent's reply to a message is posted as a reply in that message's thread, when admind has evidence that the agent took that exact prompt (the agent's `UserPromptSubmit` hook, matched ignoring whitespace). Otherwise it is posted top-level (see section 5, "How replies are anchored").
- **Reply source.** The reply is the `Stop` hook's `last_assistant_message`. If a Claude Code build omits it, admind falls back to every assistant text block of the current turn, joined by a blank line, read from the session's own transcript file (never the screen), except for a stale `Stop` (see "Launches and stale events"), which never uses the transcript, and a `Stop` whose turn changed while the transcript was being read, whose extracted text is discarded (see "Stop semantics"). The read covers exactly the turn's byte span, from its `UserPromptSubmit` to the `Stop`, in 1 MiB windows, never through a symlink. It fails (no text) if the span is unknown, the file is shorter than the span, the span ends inside a record, a record is not valid JSON, or a single record is over 64 MiB.
- **Replies.** A turn's reply text is bounded: at most `MAX_REPLY`, 64 MiB of UTF-8 bytes (separators between text blocks included), and at most 100,000 text blocks (empty ones count). A turn over either bound, a reply that cannot be read, and a reply whose text cannot be encoded as UTF-8 (a lone surrogate) get the fixed notice "admind could not read this reply from the transcript in time. Reply `!details full` for the turn's tool calls, or ask the agent to repeat it.", sent through the backstop (below); its text stays in the transcript on the host. What is sent otherwise depends on the size:
  - **Verbatim.** A reply of at most `reply_verbatim_lines` lines (default 8) and `reply_verbatim_chars` characters (default 800) is sent as it is, cut into `chunk_chars` chunks only if `chunk_chars` is smaller than that (one message per chunk, threaded as in section 5).
  - **Summary.** A longer reply goes to the summarizer if `[admind] summarizer` names a profile (claude-code only). It runs headless, without tools, hooks, MCP servers or user settings, for 60 seconds at most, and is asked for 8 lines. Up to 10 lines and 2000 characters are accepted. The summary is redacted and followed by the footer ``summary · reply `!details` for everything``, and is threaded like a verbatim reply.
  - **Backstop.** A reply goes to the backstop instead when no summarizer is configured, the summarizer fails, times out, or answers empty or too long, or the reply could not be read. Backstopped replies are collected into one batch message, posted 60 seconds after the batch's first reply (a 1-second poll, so up to about a second later) and never threaded. It opens with the title ``⚠️ Replies batched (summaries unavailable) · reply `!details` for everything``. Each reply adds an origin line (the operator, the time in UTC and the first words of their message, or `terminal`) and its lines; identical consecutive lines collapse to `line (×k)`. A batch of more than 50 lines shows the first 10, a `… N lines skipped …` line and the last 40. Those lines are kept whole, up to the 60,000-character message limit: only a batch over that has each line shortened (`…(+N chars)`) and, if needed, its end cut. A batch that fails to deliver is reopened with a new 60-second window and posted again under a new key until it is delivered; replies that arrive meanwhile join it.
- **`!details` and `!details full`.** `!details` returns the full redacted reply of the summary or batch it replies to (threaded to your command) or, sent on its own, of the latest delivered summary or batch. For a batch, it returns every reply in it, each under its origin line. `!details full` adds each turn's tool calls and results, read from the agent's transcript (an image is shown as its type, size and digest; a structured result as sorted JSON). **Neither is uncapped:**
  - The rendered text is capped at the smaller of 64 MiB of UTF-8 bytes and 20,000 parts of `chunk_chars` characters each (at the default 4000 that is 64 MiB; at `chunk_chars` 200 it is about 4 MB). Where the output stops at the cap it ends with a notice: `(details truncated at 64 MiB)` or, for the smaller limit, `(details truncated at N MiB)` or `(details truncated at N bytes)`. A tool call or result is never shown in part: the first one that would pass the cap, and everything after it, is replaced by the notice. A stored reply is shown whole, so the output can pass the cap by one reply, and the cap counts the text before redaction, which can expand it.
  - One command has one transcript read budget, 30 seconds in total (`DETAILS_READ_SECONDS`), however many turns a batch holds. A turn the budget ran out before shows `(not read: time limit)`. A turn whose transcript is busy or slow shows `(the transcript is busy or slow; try `!details full` again)`. A turn with no known span, or with a corrupt record, shows that its tool calls are not available, and a record over 64 MiB makes the turn unreadable. There is no automatic retry.
  - `!details` output is sent in lane 2 (below). It is staged in the `details_stage` table in slices, with a yield between them, and published in one statement, so a long one does not stall the daemon. The table is emptied at startup, and a command that fails or is cancelled discards what it staged.
- **Two lanes.** The outbox sends lane 1 (command replies, alerts, notices, verbatim replies, summaries and batches) before lane 2 (`!details` and posted FYI notices, section 10), each in order. A lane-1 message never waits behind a lane-2 message that is backing off after a failure.
- **Memory.** The accepted reply is bounded as above, but the transient peak is not: a reply is redacted before its size is checked, control escaping can grow text about fourfold, and each pass makes a copy. No multiple is claimed and this is not an RSS guarantee (see the comment at `MAX_REPLY` in `hook.py`). Transcript reads run one at a time, so peaks do not stack. `!details` loads a batch's replies one at a time as it renders them, up to the cap, and none past the cap is loaded.
- **Empty replies.** A reply that is empty or only whitespace is delivered as `(the admin agent's turn ended without a text reply)`, never as an empty message.
- **Commands.** Text starting with `!` is always a command and never reaches the agent. A mistyped `!restrat` is an error reply, not a prompt, and a leading `!` would otherwise switch Claude Code's input box to bash mode.

  | Command | Effect | Example |
  |---|---|---|
  | `!new` | Retire the current agent session and start a fresh one. Messages in flight get a "no reply" notice. | `!new` |
  | `!interrupt` | Send Esc to the agent, end the turn in flight, and release the held queue. Use it when a turn is stuck or its prompt was lost. | `!interrupt` |
  | `!tail [n]` | Show the last `n` lines (1 to 500, default 40) of the agent's screen. This is the only place admind reads the screen, and only on request. | `!tail 80` |
  | `!ps` | Status of each unit in `restart_units`, plus `wn-agent (admind)` and `admin agent`. | `!ps` |
  | `!restart <unit>` | Restart one unit from `restart_units`. Any other unit is refused with a reply that lists the allowed units and does not repeat what you typed. | `!restart <unit-name>.service` |
| `!details [full]` | The full reply behind a summary or batch, and with `full` its tool calls. Reply to the summary or batch, or send it alone for the latest. Capped, see "`!details` and `!details full`" above. As a reply to an ask's card, the whole ask (section 10). | `!details full` |
| `!asks [bump\|repeat]` | The active asks, one line each. `bump` posts a reminder under each open ask's card; `repeat` posts each open ask's card again (section 10). | `!asks bump` |
| `!answer <id> <text>` | Answer a question or merge ask without replying to its card (section 10). | `!answer k7m2 use the first relay` |
| `!approve` | Approve a btq approval bead, as a reply to its card (section 10). A 👍 reaction or a reply of `approve` does the same. Optional arguments `<bead> [<digest12>]` must be the card's. | `!approve` |
| `!deny [<bead> [<reason>]]` | Deny it, as a reply to its card (section 10). A 👎 reaction or a reply of `deny <reason>` does the same. | `!deny <bead> the scope is too wide` |

- **Control characters.** A message containing a C0 or C1 control character (other than tab and newline) is refused with a reply, never altered: such a character can break out of the terminal's bracketed paste. The check runs before commands are parsed, so a command name or argument holding one is never parsed or echoed.
- **Output policy.** Everything admind posts, summarizes or audits goes through one redactor (`admind/redact.py`): a recognized secret pattern, npub or run of 64 or more hex digits is replaced by a marker such as `<redacted hex key>`, and every control character except newline and tab is escaped as `\xNN`. It runs on the whole text before it is chunked and again at delivery, and repeats until the text stops changing (text still changing after 10 passes becomes `<redacted text>`). That covers replies, summaries, batches, `!details`, `!tail`, command replies, alerts, notices and the audit log. This replaces the earlier "relayed verbatim" rule for the agent's reply and the `!tail` screen: the text is the agent's, but a recognized secret in it is masked and the rest is kept. The redactor does not detect arbitrary passwords or credentials, so none of this output is guaranteed secret-free. A short reply is therefore verbatim apart from redaction, not byte for byte. Failures are reported with fixed wording (`a tmux command failed`, `internal error`), never the underlying error text, which can contain paths or identifiers.

## 5. Membership observations and recovery

**Events and counts do not latch admind.** The five allowlisted membership/admin event kinds (`member_added`, `member_removed`, `member_left`, `admin_added`, `admin_removed`) in admind's group request a coalesced check and a bounded notice. Other groups are ignored. A stable count of at least 1 is adopted as observation only, including increases, decreases and equal counts. The confirmed operator set is unchanged. No event arithmetic settles local mutations.

**Posting needs a live subscription.** Count checks share one lock and capture the acknowledged subscription generation and membership epoch. Before any adoption, a stale generation, changed epoch, active local transition or lost acknowledgement invalidates the response without changing replacement-subscription state. A failed read or count below 1 leaves the group temporarily unverified and blocks effects until bounded checking recovers; it does not create a global latch or prove removal from the group. No automatic init, new identity or new group is attempted.

**Notices are bounded and durable.** One constant-size accumulator retains first/last counts, saturated event-kind counters and a sequence. Counts carry no identities; event counters are observed signals, not distinct people. Equal-count/admin-only signals still generate notices. The group-check timer flushes without requiring another event. At most one unresolved observation snapshot (`membership-event:<sequence>`) exists beside the accumulator, with at least `group_check_seconds` between successful observation deliveries. No future-due snapshot is queued as selectable pending work. Clock rollback/recovered not-due snapshots enter durable delayed state, which does not block replies/cards; delivery and its timestamp settle atomically. Local-operation result notices (`membership:<change_id>`) retain ordinary notice rules.

**Authorisation and queued ownership.** Fresh policy is checked after waits and before every effect/chunk. Authenticated requester ownership survives queues, continuations, details, summaries, batches and restart; a multi-owner batch needs all owners current or must be rebuilt without revoked contributions. Revoked delivery groups are terminally suppressed, never retried as backstop output. System notices/cards are explicitly system-owned. Legacy rows with no provable durable provenance are atomically quarantined, preserving payload, threads, original status and migration evidence; null never means system. Quarantined/suppressed/delayed rows are excluded from pending selection and cannot block later eligible rows. Release from quarantine requires an explicit operator decision and validated whole-group provenance. A policy read failure may temporarily stall all delivery, including system rows; that fail-closed PoC limit is intentional. An already-started external effect cannot be undone, but its next effect/send is checked again.

**Migration and rollout.** Migration preserves historical sent/failed records, payloads, thread/request links, sequence high-water marks and indexes in one transaction; failure rolls back rather than discarding the database. Before deployment, test the reviewed migration on a consistent private backup and report row/group counts, especially quarantine totals, without exposing identities. Migration performs no sends. Do not deploy, rearm, change membership or send a live verification message merely by following this document: use the separately authorized rollout and marked verification procedure. After traffic, reverting the old daemon/schema or restoring a pre-traffic backup can lose decisions or replay effects and requires a reconciled operator rollback plan.

**Remaining global latches.** Unknown authority at startup/migration, an unresolved local membership mutation discovered at startup, malformed stored authority, and existing non-membership integrity safeguards still require operator recovery. In-process mutation ambiguity holds only further membership changes after cleanup; a restart with that unresolved record retains the conservative startup latch. A global latch survives restart and is never automatically cleared. Membership events, count mismatch and subscription replacement cannot create one.

**Recovery.** Inspect actual membership in your Marmot client first, then use the host control socket:

```sh
admind rearm
```

Rearm requires a stable acknowledged subscription, fresh valid policy and a count of at least 1; an event or subscription change during its read refuses it without changes. With a valid stored confirmed set, it keeps exactly confirmed∩fresh policy, adopts the observed count and clears pending mutation/global latch. It never grants authority from count and needs no temporary policy edits to account for extra members. With no historical confirmed record, the existing explicit migration assertion may confirm policy keys only when the count equals 1 plus policy operators and the operator invoked rearm after visual verification. Malformed stored authority requires operator repair, never silent replacement. A single-known-operator migration retains its prior behavior. Restart and continuous adversarial churn remain PoC availability limits: unknown pending startup needs rearm, and rearm can keep refusing during churn.

**Residual risks.** Additional members can read group traffic without becoming operators. Counts/events do not identify membership; equal-count swaps and missed events cannot be attributed. Re-verification after an outage is numeric only. Same-user processes can forge hook events or reach private control sockets; the service user remains the host trust boundary. Per-launch hook nonces separate stale turns but are not authentication against that user.

**How replies are anchored.** admind pastes a prompt, then waits for the agent's `UserPromptSubmit` hook with the same text (whitespace-insensitive). Only then is the prompt confirmed, and only then is the reply threaded to the operator's message. A reply appears **top-level** instead when:

- the turn ended after `!interrupt`, or
- the turn was started at the terminal (not by admind), or
- the turn was in progress across an admind restart, or the prompt hook was lost, or
- a `Stop` arrives while a message is reserved but its `UserPromptSubmit` has not been processed (its text is posted unthreaded and the reservation, its busy period and its anchor are left alone), or
- the turn the `Stop` belonged to was replaced while the `Stop` was being handled (the reply is posted unthreaded when the `Stop` itself carries the text; a reply read from the transcript is discarded, see "Stop semantics" below), or
- the `Stop` comes from an earlier launch of the agent (see "Launches and stale events" below).

A `Stop` from a session retired by `!new` is suppressed entirely. If another prompt starts a turn while a message is anchored, the anchored message gets a "no reply" notice so that the new turn's reply cannot take its thread. Identical text typed at the terminal is indistinguishable from the paste and is threaded; only the operator can reach the private tmux server, and the reply answers the same words.

**The held queue.** The next message is pasted only on evidence that the agent is idle: a `Stop`, a `SessionStart`, `!interrupt` or `!new`. The time a turn has been running never dispatches by itself (the only timeouts that act are the hook-lost hold below, which holds rather than dispatches). After `start_timeout_seconds` without the agent confirming the prompt, or `turn_notice_seconds` of a running turn, the operator is told once that later messages are held and pointed at `!tail` and `!interrupt`.

**Uncertain delivery.** If the paste fails at any point after the text may have reached the pane (anything after the buffer was loaded), admind cannot tell whether the agent received it. It does **not** retry: a retry could run a prompt twice. Instead:

1. the operator gets a fixed notice that delivery is uncertain and the message was not retried;
2. the queue stays held, because nothing shows the agent idle;
3. the operator recovers with `!tail` to look, then `!interrupt` (release the queue, keeping the session) or `!new` (fresh session), and resends the message if it is still needed.

A failure strictly before anything reached the pane (loading the tmux buffer) is a definite non-delivery: the message is kept and retried, and the agent session is checked.

**Launches and stale events.** Every launch or relaunch of the agent (first start, a resume after a crash, `!new`) gets a fresh random launch nonce (32 hex characters), written into that launch's hook command as `--launch <nonce>`; the hook forwards it with each event and admind records the current one. When a launch is being replaced, admind forgets the departing launch's nonce and clears readiness before it starts the replacement, and the replacement's nonce is stored before its pane starts, so the replacement's own early hooks are accepted and a late hook from the departing launch is not.

**Event ordering.** There is no counter. Ordering rests on how Claude Code runs hooks and on how admind receives them:

- Claude Code runs `UserPromptSubmit` and `Stop` command hooks **synchronously**: it waits for the hook to exit (or kills it at the hook's `timeout`) before it processes the prompt or finishes the turn. It does not run `Stop` on a user interrupt. So the hooks of one launch are invoked in turn order, and the next hook does not start until the previous one has exited. The settings admind generates give each hook an explicit `"timeout"` of 15 seconds, above the hook's own 8-second bound, and never mark a hook `async`; **do not configure these hooks as async**, because the ordering depends on them waiting.
- The hook connects to the socket first (right after reading its arguments and stdin; no transcript or other work comes before the connect), sends its frame, and waits up to 8 seconds for admind's answer. It always exits 0.
- admind gives each accepted connection an arrival index and hands events to the hook loop strictly in that order, one at a time. Event i+1 is held until event i has been processed, or dropped (EOF, a partial, oversized or invalid frame, or a frame not completed within about 5 seconds). The answer (`ok`, or a fixed `err` if processing raised) is written only after the event's effects were applied, so the hook exits only then, and Claude Code's serialisation gives causal order.
- Hook events are applied under the same lock as dispatch, `!interrupt`, `!new` and agent supervision, so an event is never applied half-way through one of them. That lock is not held while a transcript is read.
- **Validation happens under that lock.** A hook's session and launch nonce are decided inside the same critical section that applies its effects (a cheap check before the lock only drops events for another session). An event that waited for the lock across a relaunch is judged against the launch that is current when it runs, so an old `SessionStart` can neither mark the replacement ready nor reset its launch-failure count, and readiness records the nonce the event carried.
- **The dispatch gate counts accepted hooks.** The hook server counts every connection from the moment it is accepted until its slot completes (processed or dropped). admind pastes no held message while that count is above zero, rechecked under the lock just before the paste, and flushes when the count returns to zero. A prompt that Claude has already submitted and whose hook is still queued behind a slow predecessor therefore reaches admind before any held message is dispatched.
- **Arrival-index floor.** Whenever admind releases a turn (`!interrupt` after the Escape succeeded, an abandoned reservation, a relaunch, `!new`) it records the newest arrival index accepted at that moment, under the lock and after the action. A `UserPromptSubmit` accepted at or below the floor belonged to the released turn: it sets no busy state, anchors nothing, touches no reservation, and is recorded as `stale-prompt`. A `Stop` at or below the floor changes no state and posts only its own `last_assistant_message` top-level (no transcript fallback). A `Stop` or hook that itself ends or restarts a turn raises the floor to its own index only, so hooks accepted behind it are still applied. The floor is in memory: indices restart with the daemon, whose startup recovery already fails closed.
- **Deadlines.** The transcript fallback is bounded (10 seconds); on expiry (or when it fails) `reply-extraction-failed` is recorded and the fixed notice goes to the backstop (section 4, "Replies"). At most one transcript reader runs, shared by the fallback, the offset measurements and `!details full`: while an abandoned one is still reading, a new read is skipped the same way. Each event's processing is bounded (30 seconds, measured from the moment the event holds the turn lock; waiting for that lock, which can take as long as a dispatch's tmux calls, has its own 120-second bound): on expiry it is cancelled, `hook-deadline` is recorded, the hook is answered `err` and its slot is released, so one stuck event cannot hold every later hook. The deadline is an asyncio timeout and cannot interrupt a synchronous filesystem stall (an audit `fsync`, a SQLite call) on the event-loop thread: during such a stall the bound does not apply and the slot stays held until the call returns.
- **A lost hook holds dispatch.** An accepted hook connection whose event was not fully applied might have been a turn start. This covers a deadline expiry (processing or lock wait), a processing exception, and a frame dropped after accept (EOF, partial, oversized or invalid, or read timeout; also from a client that is not the hook). The hold is established first, in memory, before any audit write, store write or notice: a flag that dispatch checks (under the dispatch lock, beside the busy state and the pending-hook count) and that cannot fail. Then, as separate best-effort steps, each contained so that a failure changes nothing already established: busy is persisted and the dispatch generation advanced (so that a send failing meanwhile cannot clear it), `hook-lost-hold` is recorded, and the notice is queued in its own transaction (if the dispatch lock cannot be had within 120 seconds, these run without it). The hold therefore survives a failing audit log, a failing SQLite or outbox write and a failing notice; a notice that could not be queued is retried by the next lost event, because the episode flag is set only with the notice. The same ordering applies to every lost-hook path: the hold precedes the audit record, so a failing audit write cannot skip it. Events found stale or outside the current session or launch, and unknown events, are marked no-ops before their audit record, so even a failing audit holds nothing. Once per hold episode the operator gets the fixed notice "admind lost an agent hook event; new messages are held. When the agent is idle, send !interrupt (or !new) to resume." The hold ends through the existing paths only: a current `Stop`, `!interrupt` or `!new` (and a supervision relaunch), which clear the flag together with the busy state and the episode flag. After an admind restart only the persisted busy state remains, and it holds as before.
- **Abandoning a turn commits its safety change first.** Releasing a reservation (supersession by a terminal prompt, a restart, a dead agent, `!interrupt`, `!new`) clears the anchor and the reservation (and, where the caller is releasing the agent, the busy state) in one transaction of its own. Only then is the "No reply to this message" notice queued, in a second transaction, and the audit record written. A failing notice or audit write therefore cannot keep an obsolete anchor (a later Stop cannot thread to the abandoned message) and cannot stop supervision relaunching a dead agent.
- **An adopted pane is held at startup.** When startup adopts a live pane (not a fresh launch, whose own `SessionStart` is the idle evidence), admind cannot know whether it is mid-turn: a terminal prompt may have been accepted by the hook server and lost when the previous process died before its busy change was applied, and the hook fails open. Readiness (the pane is there) and permission to paste are therefore separate. Before any flush can run, the same fail-closed hold as for a lost hook is set (the in-memory flag, then persisted busy), and the operator gets the fixed notice "admind restarted and adopted the running agent; new messages are held until the agent finishes its current turn or you send !interrupt (or !new)." Release is by the existing evidence only: a current-launch `Stop` above the turn floor, a successful `!interrupt`, or `!new`. A second restart before release holds again. If the adopted agent was in fact idle, the operator releases with `!interrupt`.
- **The acceptance gate starts at acceptance.** admind owns the hook socket's accept: the listening socket is non-blocking and registered with the event loop's reader, and its callback is a plain synchronous function. For each connection it accepts it makes the connection non-blocking, increments `pending_hooks`, takes the arrival index and the ordering slot, and schedules the task that processes it, all in that one callback with no yield in between. An accepted connection is therefore always counted before control returns to the loop, so a dispatcher that was already scheduled sees it. Until it is accepted, a connection waits in the listening backlog, and under the dispatch lock immediately before reserving and pasting the dispatcher polls the listening socket for readability with a zero timeout; a waiting connection defers the paste, the message stays queued, and the accept path's idle edge flushes later. If `accept()` fails for any reason other than an empty backlog (for example, out of file descriptors), admind records it quietly, stops reading the socket and tries again after 100 ms rather than spinning; the connection stays in the backlog meanwhile, so the poll keeps the gate closed. Each reader callback makes at most 64 accept attempts (successes and aborted accepts alike) and then returns, so a continuously refilled backlog cannot starve the rest of the daemon; 8 aborted accepts in one callback take the same 100 ms back-off. Every accepted connection ends in one finish path: applied (its event was routed) or lost (anything else: a failed stream setup or read, a dropped frame, cancellation, a failure to create its task). A lost one applies the lost-hook hold first, then closes its socket; its slot and pending count are released only after the previous connection's slot has resolved, so a failure never lets a later hook overtake an earlier one. If the listener cannot be registered at startup, the socket is closed before the error propagates. On shutdown admind sets a `shutting_down` flag synchronously, before any cancellation propagates (the daemon's loops and `run()` set it as soon as they are cancelled); while it is set no flush, idle flush or dispatch pastes, so a connection released by the teardown cannot dispatch a held message. A delivery cancelled while it is being processed applies the lost-hook hold (in memory, then a best-effort persist) before its delivery is resolved. A lost connection re-applies its hold when its ordered release runs, so a predecessor's Stop cannot lift it. Admind then stops reading, closes the listening socket and cancels the pending connections, releasing their slots (a cancelled connection whose event never reached the daemon applies the lost-hook hold). The per-connection bookkeeping is built (future, record, set membership) before the count is incremented, and any failure after that goes through the same finish path, so the count is never raised without a releasable record. Inherent residual: a terminal prompt typed at the same instant as admind's paste races at the agent itself, which no admind check can order.
- **A replacement interrupted by a restart is held.** Launch intent and its consumed state are committed before kill or spawn. A pending replacement, missing launch identity, missing acknowledgement or login mismatch does not authorize another launch. admind preserves the original obligations, holds dispatch and asks the operator to inspect with `!tail`. Only an authenticated `!new` chooses a fresh conversation; it is never replayed automatically after a crash. Resend undelivered work with its context after the fresh conversation is ready.
- Residual: if Claude Code kills a hook after its frame was written but before admind processed it, the event is still processed, in arrival order, before any later hook. A frame from an older hook that still carries a `seq` field is accepted and the field ignored.
- *To be confirmed against real Claude Code in Task 10:* that hooks are synchronous, that `Stop` does not fire on an interrupt, and the `"timeout"` semantics (seconds, and that the hook is killed at that point).

**Stop semantics.** A `Stop` releases the busy state, deletes a reservation or threads a reply only when the current turn's anchor was set by a `UserPromptSubmit` that admind processed earlier in the same launch, or when no message is reserved (a turn begun at the terminal). A `Stop` that finds a reservation in flight but not anchored (dispatched with no prompt hook yet, or its prompt hook still to come) changes none of that: its own text is posted top-level, otherwise nothing is posted and `stale-stop-unrecoverable` is recorded. Such a `Stop` marks the reservation as stopped, and a `UserPromptSubmit` for a stopped reservation is ignored and recorded as `ignored-late-prompt`: it cannot anchor the reservation or start a busy period. The operator releases the held turn with `!interrupt`. When a `Stop` has no text of its own, the reply is read from the transcript in a worker thread; the turn identity is captured before the read, and if the turn changed meanwhile (`!interrupt`, a new dispatch, `!new`, a relaunch) the extracted text is discarded, nothing is posted and `stale-stop-unrecoverable` is recorded. Only a `Stop` that is still current after the read uses the fallback text.

**Audit records for stale or held events.**

- `stale-prompt` (an `agent` record): a `UserPromptSubmit` that was accepted before its turn was released (for example it waited behind `!interrupt`) was ignored. Nothing to do: the queue is not held by it. If messages stay held anyway, `!interrupt` releases the turn.
- `reply-extraction-failed` (an `agent` record): a `Stop` without text could not be read from the transcript (in time, because another read was still running, or because the turn was over the reply bounds). The operator gets the fixed "could not read this reply" notice in a backstop batch; read `!tail` for what the agent said. Repeats point at a stalled filesystem under the transcript directory.
- `hook-deadline` (a `hook` record): processing one hook event exceeded its deadline and was cancelled. The queue moves on; if the turn state looks wrong afterwards, `!interrupt` releases it and `!new` starts a fresh launch.
- `hook-lost-hold` (a `hook` record): a hook event was lost after it was accepted (see "A lost hook holds dispatch"), so dispatch is held and the operator was sent the notice. Look at `!tail`; once the agent is idle send `!interrupt` (keeps the session) or `!new`.
- `ignored-stale-launch` (a `hook` record): an event from an earlier launch, or without a valid launch nonce, was dropped. Nothing to do; if the agent seems stuck behind it, `!interrupt` releases a held turn and `!new` starts a fresh launch.
- `stale-stop-unrecoverable` (an `agent` record): a `Stop` had no text of its own and the transcript could not be trusted for it (stale launch, an unanchored reservation, or a turn change during extraction). No reply is posted for it. The operator sees the usual "no reply" or held-queue notices; `!interrupt` releases a held turn and `!new` starts a fresh launch.

A stale event never changes the turn, the busy state, the anchor or the reservation. A stale `Stop` whose own `last_assistant_message` is present has it posted top-level. Every other stale event is recorded as `ignored-stale-launch` and dropped. A frame whose `launch` is not 32 lowercase hex characters is dropped when it is decoded and recorded as a dropped hook; it never reaches the hook loop. This is correlation, not authentication: a process running as the service user can write the socket and the settings file as well (the same-user residual risk of section 5).

**Readiness is bound to its launch.** `ready` is set by a `SessionStart` of the current launch (or by adopting a running pane) and remembers the nonce that set it; it counts only while that is still the current nonce. A late `SessionStart` from a departing launch, arriving while the replacement is starting, therefore cannot mark the replacement ready, and the 120-second readiness timeout still applies to it.

**SessionStart `source`.** admind treats `SessionStart` according to its `source` field, and only for the session it has already seen start. `compact` on the same session (the context shrinking, mid-turn or not) is not a restart and changes nothing but the audit log. `startup` or `resume` of the same idle session is a no-op beyond marking the agent ready. Everything else restarts the picture: `clear`, an unknown or missing source, a busy session starting again, or a different session ID. Whatever was in flight is then abandoned with a "no reply" notice. *To be confirmed against real Claude Code in Task 10:* the set of `source` values and when each fires are taken from Claude Code's documented hook schema and have not been run against a live agent.

## 6. Alert relay contract

`wsd` (plans 6 and 8) writes one file per alert into `<state>/alerts/`; admind is the reader.

- **Name:** `<id>.json`, where `<id>` matches `[A-Za-z0-9_-]{1,64}`.
- **Written atomically:** to a dotfile first, then renamed into place. admind ignores dotfiles.
- **Content:** `{"id": "<id>", "created_at": "<RFC 3339 UTC>", "text": "<plain text>"}`. `id` must equal the file name.
- **Relayed once:** admind relays each file once, as a top-level message, and records it as relayed. A restart does not repeat it.
- **Never deleted or modified:** cleanup is the writer's job.
- **Held until the operator has been seen:** alerts are not posted until the operator's first message (D5), and not while latched, unverified or unsubscribed. They are relayed when posting becomes possible.
- **Ignored silently:** dotfiles, directories (even one named `x.json`) and entries whose name does not end in `.json`.
- **Malformed files** are reported. A regular `.json` file that fails validation (the name stem does not match `[A-Za-z0-9_-]{1,64}`, bad JSON, `id` not equal to the name stem, or larger than 64 KiB), and a `.json` entry that cannot be read as a regular file (a symlink, a FIFO or another special file), get a fixed "malformed alert" notice naming the file, so a broken writer is noticed.
- **Sensitive alerts are withheld.** If an alert's text or timestamp contains a secret, an npub or a 64-hex identifier, the operator gets a fixed notice naming the file and the kind of thing found, and reads the file on the host. Other control characters are escaped, so a file cannot drive the chat client. Long alerts are truncated to `chunk_chars` with a marker.
- **Opaque outbox keys.** The idempotency key of an alert's message is `alert:` plus the first 32 hex characters of the SHA-256 of the raw file name *stem* bytes (the name without `.json`), never the name itself (a name may be an npub or a secret). For `example.json` the stem is `example`, so the key is `alert:50d858e0985ecc7f60418aaf0cc5ab58` (`printf %s example | sha256sum | cut -c1-32` gives the hex part). File names are bytes and need not be valid UTF-8; undecodable bytes are shown as `\xNN`.
- **One failing alert does not stop the others.** A failure while relaying one file is audited by exception type and opaque key, the operator gets a fixed "could not be relayed (ref …)" notice, and the other alerts proceed. One exception: if registering the alert and registering that fallback notice both fail (for example, the database is unwritable), nothing was queued for the operator. The file is then skipped only in memory for the rest of that admind run (an `alert` record with `action` `skipped`), and a restart tries it again.

## 7. Files

Everything admind creates is private: directories 0700, files 0600. What is enforced:

- `private_dir()` (the state, home and socket directories) refuses a final path component that is a symlink, or a directory owned by another user, and sets the mode to 0700 on the directory it checked (lstat, then an `O_NOFOLLOW | O_DIRECTORY` open compared with it). Parent directories above it are not inspected.
- The audit log is opened with `O_NOFOLLOW`, must be a regular file, and is set to 0600 on every open, so a looser existing file is tightened.
- `admind.db` is opened with `O_NOFOLLOW`, must be a regular file, and is set to 0600. SQLite then reopens it by pathname, so admind checks just before connecting that the path is still that regular file and not a symlink. A swap in the gap that remains would need write access to the 0700 directory owned by the service user, which is the mitigation.
- The directory prerequisite: `<state>/admind/` must be a real directory owned by the service user, not a symlink to one.

| Path | Mode | Contents |
|---|---|---|
| `<state>/admind/` | 0700 | admind's state directory |
| `<state>/admind/admind.db` | 0600 | SQLite (WAL): accepted message IDs, outbox (two lanes), reply turns and batches, `details_stage`, relayed alerts, latch, confirmed operators, agent session, turn state; and for asks (section 10) the four tables `asks`, `ask_answers` (answers and notes), `ask_details` (who asked for an ask's `!details`, and how many chunks) and `ask_attempts` (each decision attempt, persisted before `approve-bead` runs) |
| `<state>/admind/audit.jsonl` | 0600 | append-only audit log, one JSON object per line (see below) |
| `<state>/admind/hook.sock` | 0600 | the agent's hooks reach admind here |
| `<state>/admind/ctl.sock` | 0600 | the host control socket for `admind operators` and `admind rearm` (below) |
| `<state>/admind/ask.sock` | 0600 | the host socket for `admind ask` (section 10); same server and checks as `ctl.sock`, with its own limits (256 KiB requests, 1 MiB replies) |
| `<state>/admind/claude-settings.json` | 0600 | the hook settings admind passes to the agent |
| `<state>/admind/marmot/` | 0700 | the private `wn-agent` home (location is `[admind.marmot] home`) |
| `<state>/admind/marmot/control.token` | 0600 | bearer token for the child's control socket; generated by admind, never printed or logged |
| `<state>/admind/marmot/ctl/` | 0700 | the child's control socket |
| `<state>/admind/marmot/wn-agent.log` | 0600 | the child's stdout and stderr (see below) |
| `heterodyne-admind-tmux-<random>.scope` (systemd, transient) | n/a | the cgroup of the private tmux server `tmux -L heterodyne-admind`; separate from the unit, so it survives a unit stop or restart; the dead pane kept for `!tail` also survives |
| `<state>/alerts/` | writer's choice | alert files written by `wsd` (section 6); admind only reads it |

**The control socket** (`ctl.sock`). `admind operators add|remove` and `admind rearm` run as the service user and ask the daemon, which owns the `wn-agent` connection, over this socket: one JSON request per connection, one JSON reply. The daemon creates it 0600 inside the 0700 state directory. At startup it refuses a path that exists and is not a socket (`lstat`, so a symlink is refused too) and otherwise unlinks whatever socket is there, without probing for a live listener; at shutdown it removes the socket only if the path is still the one it bound (device, inode, change and modify times are compared). A request is limited to 4096 bytes (the newline does not count) and must arrive within 5 seconds; a malformed, oversize or invalid request (a NAME of 0 or over 128 characters, or with control characters; `rearm` with a NAME) is refused with a fixed reply. Anyone who can use the socket can already act as the service user, which is the trust boundary. Failures fail closed: a handler error gets a fixed reply and the type goes to the audit log. **There is no daemon-level single-instance lock.** Do not run two admind daemons on the same state directory (workdir): a second daemon would unlink the first's live control socket (the startup check only looks that the path is a socket, not that no one is listening) and nothing stops both from running against one database and one `wn-agent` home. This is a known follow-up.

**The audit log** records timestamps, actions and outcomes. As of revision 13 it holds **whole messages**, redacted: an operator's text is recorded in full (not capped; it goes through `redact` and then `audit.clean`), under the operator's name from `policy.toml`, for accepted messages and for messages dropped while latched. Every string in every record is redacted by one function (`audit.clean`) before anything is formatted, recursively: bytes are decoded, containers are walked, compound dictionary keys are cleaned, an exception becomes `{"type", "args"}` with its args cleaned, an object whose `str()` contains a backslash is written as `<Type: withheld>` (an escaped rendering could hide where a token starts; the exact stdlib types whose `str()` is plain text, such as paths, dates, UUIDs and decimals, `PureWindowsPath` included, are redacted directly even with backslashes), a cycle is `<cycle>` and nesting deeper than 64 levels is `<too deep>`. A 64-hex identifier in an identifier field (`message_id`, `reply_to`, `key`, `target`, `anchor`) is written as `id:` plus 12 hex digits of its SHA-256, so records about one message still correlate without holding the ID. A message from anyone who is not an operator is recorded as an 8-character sender prefix, the reason and a character count, never its text. Agent replies, summaries and command results are recorded by length. The session ID of the admin agent's own accepted events is recorded; admind generates it as a local UUID and it is not a secret. What is omitted: any `detail` from the `wn-agent` peer (only an allowlisted error code), stranger text bodies, transcript paths, the group ID, and the identifiers of rejected input. New record kinds: `membership` (a change's journal and outcome), `summary` (queued, failed with a fixed reason word), `backstop` (a reply queued, a batch posted), `ctl` (each control request, by operation and name) and `ask` (asks and decisions, section 10).

**The child log.** The private `wn-agent` writes its output to `wn-agent.log` in its home, not to the journal, because that output can contain invites, keys or the token. The file is truncated each time the child is spawned, so it holds only the current run. It is created 0600 (an older, looser file is tightened), opened without following symlinks, and must be a regular file. Reading it is a break-glass host step (section 8), for when `wn-agent` is down or keeps restarting and admind cannot reach Marmot; do not paste it into chat.

**Upgrading from plan 2.** `admind.db` is migrated in place at startup; nothing needs to be removed.

- **`expected_members`.** A group from plan 2 always had two members, so a database without the stored count gets `2` (audited as `guard` / `migrated`).
- **`group_operators`.** A database that has not recorded which operators are confirmed in the group takes the single `policy.toml` operator as confirmed (audited as `migrated-operators`). With **several** operators in `policy.toml` at upgrade time admind cannot know which are in the group, so it latches until an explicit `rearm` migration assertion after visual membership verification (section 5).
- **The outbox.** The `lane` column is added (existing rows are lane 1) and the replies queued but not yet sent are redacted once. A reply's pending chunks are joined, redacted and chunked again under new keys, so text already received may be re-sent; a continuation that cannot be checked against what was already sent is replaced whole by a fixed notice. This is recorded as an `outbox` record (`redacted-after-upgrade`).
- **A pending membership record** latches admind on startup (a change interrupted by a stop is not trusted by count).
- The `details_stage` table is created if missing (`CREATE TABLE IF NOT EXISTS`) and emptied at every start.
- The four ask tables (section 10) and an index on the outbox's message IDs are created the same way. An older admind ignores them, so a rollback leaves them in place.

An older database from before plan 2 (for example an older `inbound` status constraint or text alert names) is still not migrated: start from an empty `<state>/admind/` for that.

## 8. Troubleshooting

**Start on Marmot.** The diagnostic path in operation is the group: `!ps` (admind's units, its `wn-agent` and the admin agent), `!tail` (the agent's screen), `!asks` (the active asks, after reconciling stranded decisions), then `!interrupt`, `!new` or `!restart <unit>` to recover. Steps marked **break-glass (host)** below are for when that path cannot help: admind or its `wn-agent` is down or keeps restarting, admind is silent or latched, Marmot is unreachable, or a `!` command reports a cause only the host can see. They are run on the host as the service user (ADR 0001 §8.1).

- **`wn-agent` does not start, or "runtime root is already in use".** Another process holds the home (S4 finding). The private home can be opened by one process only: do not run `wn-agent` against `<state>/admind/marmot` by hand while admind runs. If `!ps` still answers, it shows `wn-agent (admind)`. **Break-glass (host)**, when admind cannot reach Marmot at all: check `wn-agent.log` for the cause, and stop admind before inspecting the home. admind restarts the child with a doubling back-off of 1 to 60 seconds, reset once the child has stayed up for 60 seconds. (The background loops have their own, separate cap of 30 seconds; see below.)
- **The agent died, or never acknowledged startup.** admind checks the agent pane every 5 seconds (`AGENT_POLL`). A confirmed death or 120 seconds (`READY_TIMEOUT`) without a qualified startup acknowledgement puts recovery on hold, without killing, resuming, replacing or abandoning the original turn. Probe errors are unknown liveness, not death. Inspect with `!tail`; `!new` explicitly chooses a fresh conversation. A matching startup event during a persisted busy turn also holds dispatch rather than claiming the old turn finished.
- **The agent keeps exiting (`AgentStuck`).** After three launches in a row without a `SessionStart` hook, admind stops relaunching and tells the operator ("did not start after 3 launches; use !tail, then !new"), either in reply to the messages then held or as a notice of its own. Messages sent meanwhile are refused with that reason. It does not retry on a timer. Use `!tail` to read the dead pane (it is kept), fix the cause (often the first-launch dialogs, section 3, or a missing `claude` on the unit's `PATH`), then `!new`; only `!new` resets the count (restarting admind does not, and the next check still reports the same stuck state).
- **`AgentStuck` right after the first restart.** If `workdir` is the home directory (the default), Claude Code asks for workspace trust on every launch and never remembers it, so the relaunch times out three times. Set `[admind] workdir` to a dedicated directory, accept the dialogs there once on the host (section 3), then `!new`.
- **admind stayed silent after a latch was cleared.** Run `admind rearm` once the group is right; the running daemon recovers by itself at its next group check (within `group_check_seconds`) and no restart is needed.
- **A background loop crashed.** The inbound reader, worker, hook, outbox, alert, group and agent-supervision loops each run under a supervisor. If one raises or returns, a `task` record (`name`, `action` `crashed` with the exception type only, or `returned`) is written and the loop restarts after a delay that doubles from 1 to 30 seconds and resets after a run of 60 seconds. This 30-second cap is the loops' own; the `wn-agent` child's cap is 60 seconds. Until the loop is back, what it does is paused (for example, no alerts are relayed while the alert loop is down). The supervisor's own `task` record is best-effort: if the audit log is unwritable, the restart still happens, and a failing audit write never ends the other loops. For alerts, the fallback notice (or the in-memory skip) is recorded before the audit write that reports it.
- **No replies.** The reply comes from the `Stop` hook (a long one arrives as a summary or a batch, section 4: wait up to a minute and a bit for a batch). Check `!tail` to see whether the agent is still working or waiting on a dialog. If the turn has finished and no reply came, the hook may not have reached admind (or the event was held as `ignored-stale-launch` or `stale-stop-unrecoverable`, see "Audit records for stale or held events"). Recover on Marmot: `!interrupt` releases the held queue; if the next message still gets no reply, `!new` starts a fresh launch. Only if that fails too, or admind no longer answers commands, is it **break-glass (host)**: look for `hook` records in `audit.jsonl` (`ignored-other-session`, `ignored-unknown-event`, or a `dropped` record with an error type).
- **Replies arrive without a thread.** Expected in the cases listed under "How replies are anchored".
- **Later messages are not answered ("held").** The agent is not provably idle. Use `!tail`, then `!interrupt`; after an uncertain delivery, also resend the message.
- **A message was answered with "admind restarted before this message reached the admin agent".** admind stopped between accepting your message and handing it to the agent (or while a command was executing). It is never replayed, because running a prompt or `!restart` twice is worse than asking you to resend (D6). Resend if it is still needed. A turn that was in flight across the restart is closed with a notice, and its late reply, if any, appears top-level.
- **A message was dropped with no reply.** A peer message whose ID is not 64 lowercase hex characters (after lower-casing) is dropped before dispatch, with a `drop` record (`reason` `malformed message id`) and nothing else. This points at a broken peer or an integration bug. A repeated ID is dropped too (`replayed message id`).
- **admind is silent.** After `admind init`, admind posts nothing until an operator's first message (the join signal, section 2 step 5), so send a message first. Then send `!ps`: a reply shows admind is up and lists its units, its `wn-agent` and the admin agent. If `!ps` gets no reply either, admind may be down, latched or unable to reach Marmot, and this is **break-glass (host)**: check whether it is latched (`audit.jsonl`, `guard` records), then section 5.
- **A send keeps failing.** A retryable send failure backs off (up to 60 seconds) before the next attempt, and holds back later outbox rows while it waits (they are sent in order). After 10 attempts the row is marked failed and admind moves on.
- **A command line fails with "invalid arguments".** `admind` never echoes a rejected argument (it could be an npub or a token); run `admind --help`.

## 9. Known limits

- **Codex is not supported as the admin adapter yet** (D7), nor as the summarizer. A `codex` admin or summarizer profile is a configuration error. It arrives with plan 4.
- **`systemctl --user stop heterodyne-admind` does not stop the agent.** The agent's tmux server runs in its own scope so that it survives restarts. From Marmot, `!interrupt` ends a turn and `!new` replaces the session, but nothing stops the agent for good. Taking admind and its agent out of service is a **break-glass (host)** step, in this order: first stop the daemon, `systemctl --user stop heterodyne-admind` (otherwise its supervision loop relaunches the agent within seconds), then stop the agent's private tmux server, `tmux -L heterodyne-admind kill-server`, or its scopes, `systemctl --user stop 'heterodyne-admind-tmux-*.scope'`.
- **systemd only (v1).** `launchd` is phase 2.
- **The join signal is the operator's first message** (D5). Until it arrives, admind posts nothing.
- **No daemon-level single-instance lock** (section 7): never run two admind daemons on one state directory. A known follow-up.
- **A summary's quality is not tested:** the summarizer is asked to quote every question and error verbatim, but only the prompt wording is checked.
- **Count-only re-verification** after an outage and **same-user forgery** on the control and hook sockets are accepted residual risks (section 5).
- **Missed membership events** while disconnected cannot be detected.
- **Schema upgrades** preserve historical rows and sequence identities transactionally. Unknown legacy delivery provenance is quarantined before recovery/delivery, never guessed or sent (section 5).
- **Approver names are not mapped** (section 10): an operator's `policy.toml` name must equal a name in btq's `approvers` to approve from Marmot.
- **Asks and approvals are interim** (section 10): there is no gatekeeper judgement on an ask's context, and ADR revision 14 absorbs the relay.
- **No picker asks yet.** ADR revision 14 (§8.1) specifies a `picker` ask kind, so that the agent's interactive question tool reaches the operators on Marmot as a card bound to its launch, and `!asks cancel <id>` to withdraw one. Neither is built. Today the agent should ask in its reply text, which reaches the operators as that reply, and they answer by passthrough. A picker shown anyway waits in the pane: `!tail` shows it, and `!interrupt` (Esc) dismisses it unanswered.

## 10. Asks and approvals (interim)

A local process, such as a controller session, can put a question, a merge request or a btq design approval in front of the operators, and they answer or decide from their Marmot client. This is interim: it ships ahead of ADR revision 14, which absorbs it. The design is the [relay spec](superpowers/specs/2026-10-05-admind-marmot-relay-design.md), as amended by the [reply and reaction delta](superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md); the `R` numbers below are their decisions (R27 to R31 are the delta's). Placeholders as above, plus `<BTQ-LIVE>` for the live beads-task-queue checkout.

### The trust change

Copied verbatim from the spec, §3 (`§8` there is ADR 0001 §8):

**Approval authority moves from the terminal to an authenticated Marmot message.** Today a btq approval is recorded by a person running `approve-bead` at a terminal as the service user and confirming at its prompt. After this change it can also be recorded by admind, running as the same user, when a Marmot message:

- is MLS-authenticated as an operator's key, in admind's group, not latched, not replayed (the existing ingress rules, §8);
- comes from an operator whose `policy.toml` name is in btq's `approvers`;
- is a reply to the genuine admind card for that bead, names the bead, and repeats the digest shown on the card;
- arrives while the bead is still open, still `kind:approval`, and still hashes to the full digest admind showed.

The typed digest takes the place of the terminal's `[y/N]` confirmation. So **whoever controls an approver's Marmot key, and so their phone or White Noise client, can approve designs.** This is the risk being accepted.

**Amended by the delta (§3 there).** The typed digest is gone. Its place is taken by **an explicit approve action on admind's own card**: an approve reaction, or a reply that is exactly one approve word (R28). The digest pin is unchanged: admind stores the full digest when it renders the card and passes it to `approve-bead --expect-digest`, so an approval still binds to exactly the content on the card it answers. What remains of the friction the typed digest gave:

- a free-form reply never decides: only a reply that is exactly an approve word, or begins with a deny word;
- a reaction other than the listed ones decides nothing;
- every decision is confirmed in the thread as "Approved `<bead>` as `<name>` …", and audited;
- a decision is final: removing the reaction does not undo it.

**Accepted risk:** an accidental 👍 on an approval card records an approval. The operator chose this on 2026-10-06.

What it does **not** change, stated plainly so the change is not overstated:

- **The admind group already had this power, but not in the open.** The admin agent runs unsandboxed as the service user (§8, operator decision 2026-09-29). An operator message to it could already make it run `approve-bead --yes`. The relay makes that path explicit, pinned to a digest, attributed to a named approver, and audited, instead of leaving it to an LLM's interpretation.
- **btq metadata is still trusted-agent policy, not authentication** (beads-task-queue `CLAUDE.md`). Any process running as the service user can still run `approve-bead`, or `bd` directly. The relay adds no way for such a process to approve: posting an ask never decides anything.
- `approve-bead` at the terminal keeps working (`via=cli`). Every `approve-bead` decision, from the terminal or from the relay, takes the same per-bead lock and re-checks under it that the bead is still open, still `kind:approval`, unchanged and undecided (R6). So among `approve-bead` writers the first decision wins and a second one is refused. A same-user process that edits the bead with `bd` directly is outside that protocol. What it can do is make a recorded approval invalid: btq's `approval_valid` rejects any `*_digest` that differs from the current content, so a racing edit fails closed. It cannot make an approval valid.
- **What the phone shows is all there is.** A card for an approval can only be posted if its whole readout survives admind's redaction unchanged (R21). Content that would be hidden (a token-shaped string, a 64-hex run, a PEM block) means the bead must be decided at the terminal. File, commit and range refs are shown as pinned forge links where the repo has a GitHub remote, and otherwise as their pinned JSON with "read on the host" (R25).

### Setting it up

- **Questions and merge requests** need nothing new: `ask.sock` is created at every start.
- **Approvals** need `[admind] approve_bead`, the absolute path of `<BTQ-LIVE>/bin/approve-bead` (see [configuration.md](configuration.md#admin-channel-admind), which also has the rollback order). Without it an approval ask is refused with "Approval asks are not configured on this host ([admind] approve_bead)." and everything else works. admind runs `approve-bead` as a child in the unit's environment, so `bd` and btq's credentials must resolve there. admind never writes bead metadata itself and imports no btq code.
- **Enabling it on a running host.** After adding `approve_bead` to `config.toml`, and before restarting admind, run `admind ask list` as the service user. It reads the configuration the way the daemon will at its next start. Exit 78 means the value is bad: stop, fix `config.toml`, and do not restart, because the new daemon would refuse to start as well. Exit 69 is expected while an older daemon, which has no `ask.sock`, is still running. Exit 0 means a daemon with the relay is already running. On 0 or 69, restart admind, then run `admind ask list` again: it should now exit 0.
- **The approver names (an existing gap).** admind decides as the operator's `policy.toml` name, passed as `--as=NAME`. There is no mapping table: for approvals, that name must equal a name in btq's own `approvers` list. admind never reads btq's policy; it takes the list from `approve-bead --json`. An operator whose name is not on it is refused with "`<name>` is not a btq approver. Nothing recorded.", and no decision is run. Keep the two lists in step by hand for every operator who approves. Being in `policy.toml`'s `approvers` (section 2) is a different list and is not enough.

### Posting and reading: `admind ask`

Run on the host as the service user. `admind ask` is a client of `ask.sock` (section 7) and opens no database.

```sh
admind ask post --kind question --title "<one line>" --body-file <file|-> \
    (--choice <text> --choice <text> ... --recommended <letter> | --yes-no [--recommended yes|no]) \
    [--link <label>=<url>]... [--ref <path>[@<rev>]]... [--from <label>] [--json]
admind ask post --kind merge --title "<one line>" --body-file <file|-> \
    --pr https://github.com/<owner>/<repo>/pull/<n> --head <40-hex-sha> \
    [--link <label>=<url>]... [--ref <path>[@<rev>]]... [--from <label>] [--json]
admind ask post --kind approval --bead <bead> [--from <label>] [--json]
admind ask get <id> [--json]
admind ask wait <id> [--timeout <seconds>] [--json]
admind ask list [--json]
admind ask cancel <id>
admind notice post --title "<one line>" [--body-file <file|->] \
    [--link <label>=<url>]... [--ref <path>[@<rev>]]... [--from <label>] [--json]
```

| Exit | Meaning |
|---|---|
| 0 | Done. `post` printed `ask <id> posted`; `get`, `list` and `cancel` were answered; `wait` found an answer or note, or the ask ended. |
| 1 | Refused or failed, with the reason on stderr as `admind: <reason>`. This includes a body file that cannot be read, a rule the client checks before it connects, and, for `wait`, a refusal from the daemon (no such ask). |
| 2 | Invalid arguments. The message is fixed (`admind: invalid arguments (see --help)`) and never repeats what was typed. |
| 3 | `wait` timed out: `admind: no answer yet (timed out)`, or the not-running message if the daemon never answered. |
| 69 | `post`, `get`, `list` or `cancel` found no daemon: `admind is not running (or did not answer).` A `post` that got no answer adds that the ask may still have been posted (see `post` below). |
| 78 | A configuration error, for example a bad `approve_bead`. |

- **`post`** checks the rules below, then stores the ask and queues its card. The client checks the same rules before it connects, so most refusals need no round trip; the daemon checks them again. `--body-file -` reads stdin. `--from` (default `local`) is shown on the card and is not verified: any process of the service user can post. The audit also records the poster's PID where the platform reports it. A post's client waits long enough for the reads of the posts queued ahead of it (see Limits). If it still gets no answer it exits 69, but a post the daemon received goes on without its client and may be stored with its card sent: check `admind ask list` before posting it again.
- **`get`** prints `ask <id> · <kind> · <status>` (with ` · not yet delivered` until every chunk of the card is sent), then each answer or note as `--- <answer|note> from <operator> at <time>` followed by its text, then `outcome: …` once there is one. An answer on a question with choices names the choice it was read as: `--- answer (choice A) from <operator> at <time>`, then the verbatim text or emoji; `--json` carries it as `choice` (`A`–`F`, `yes`, `no` or `none`, null for a free answer).
- **`wait`** polls `get` every 2 seconds until the ask has an answer or note, or reaches a final status (`approved`, `denied`, `stale`, `superseded`, `cancelled`, `blocked`). The default `--timeout` is 540 seconds, under a 10-minute tool-call limit. A daemon that is down or restarting is retried until the timeout. For an approval ask the first note also ends the wait, so exit 0 from `wait` is not approval evidence. Only the read-back is: `get` (status `approved` or `denied`, and its `outcome`), or `approve-bead <bead>` on the host.
- **`list`** prints one line per ask, `<id> <kind> · <status> · <n> answers · <title>`: every active ask and the 20 most recent others.
- **`cancel`** works on an `open` or `answered` ask only. It posts "Ask `<id>` was cancelled by its poster. Nothing more is needed." in the card's thread.
- **`--json`** prints the daemon's whole reply as JSON, refusals included, for `post`, `get` and `list`. For `wait`, a refusal goes to stderr as `admind: <reason>` with exit 1, and nothing is printed on stdout. The exit status still tells the outcome. The text output masks secrets and identifiers and escapes control characters line by line; `--json` returns the operators' answers verbatim.
- `get`, `list` and `cancel` work while admind is latched. A `post` while latched is refused ("admind is latched and posts nothing; the ask was not posted."). A post before the join signal is stored and its card waits like any notice.

**What a post must carry** (R4, R16). Every ask carries its context:

- question and merge: `--title`, one line of 1 to 200 characters, and a body of at most 16,000 characters with at least 80 that are not spaces;
- a question: either 2 to 6 `--choice`, lettered A to F in the order given, with `--recommended <letter>`, or `--yes-no` with `--recommended yes` (the default) or `no` (see "Questions with choices"). A question with neither is still accepted for now, with `warning: questions without --choice or --yes-no will be refused in the next release` on stderr; the next release refuses it;
- question and merge: at most 8 links in all from `--link` and `--ref` (see "Links");
- merge also needs `--pr`, matching `https://github.com/<owner>/<repo>/pull/<n>`, and `--head`, 40 lowercase hex digits; `--pr` and `--head` are refused on other kinds;
- approval: `--bead` only, without choices or links. Its context is the bead, read with `approve-bead <bead> --json`;
- `--from` must match `[a-z0-9][a-z0-9._-]{0,31}`, and `--bead` must match `[a-z0-9]{1,16}-[a-z0-9.]{1,32}`.

**Posting an approval** reads the bead first, up to 60 seconds. The post is refused, with the reason going back to the poster and not to the operators, when:

- the bead is not open, is not a `kind:approval` bead, or already holds a decision;
- approve-bead computed no digest for it, or reports gaps ("send it back for grooming");
- its `design_review` is not in valid two-LLM format, or its ask changed after it was posted;
- the bead is busy, or another ask for it is `deciding` or `uncertain`;
- the read failed: "admind could not read `<bead>` (`<word>`); try again.", where the word is `unavailable`, `timed out` or `bad output`;
- any part of the card would be changed by redaction: "this bead holds text admind would redact; decide it at the terminal". Nothing is posted (R21);
- the readout is longer than 24,000 characters (`MAX_APPROVAL_CARD`, R8): "this bead is too long to decide from the phone; decide it at the terminal";
- one of its `link:` lines is wider than a chunk can hold: "this bead has a link longer than this group's message size allows (`<n>` characters, `<usable>` usable at chunk_chars `<c>`); shorten the link in the bead or raise chunk_chars". A link is never cut across messages;
- its readout's `purpose` is set to anything but `design` or `execution` (see "Cards").

Posts run one at a time, and a third post while two are in flight is refused as busy. The latch and the limits are checked again after the read. A new approval ask for a bead that already has an `open` or `answered` ask supersedes the old one (R9). The old card gets "Ask `<old>` is superseded by ask `<new>`, a newer card for `<bead>`. This card decides nothing any more." in its thread.

### Cards

Cards are posted in lane 1. Each is redacted whole, then split into chunks (see "Parts and threads"). An ask ID is 4 characters from `23456789abcdefghjkmnpqrstuvwxyz` and is matched in any case.

**Every card's first line is its label**, so its kind is readable before anything else: `QUESTION: Multiple Options`, `QUESTION: yes/no`, `QUESTION` (a question with no choices), `ACTION: merge`, `APPROVAL: design`, `APPROVAL: execution`, `APPROVAL: purpose not set`, or `FYI: no reply needed` for a notice. The head line follows, as before.

```
QUESTION
❓ Ask k7m2 · question · posted by controller (a local process; unverified)
<title>

<body>
(40 lines shown of 52; reply !details for the rest)

Answer by replying or reacting to this message, or send !answer k7m2 <text>
```

A merge card is labelled `ACTION: merge` and headed `🔀 Ask m3qp · merge request · posted by …`. It shows `PR: <url>` and `Head: <sha>` under the title, and ends with "Merging is yours to do in GitHub; admind never merges. React 👍 or reply when it is merged, or reply with what to change."

```
APPROVAL: design
🛂 Approval ask p4xw · bead <bead> · posted by controller (a local process; unverified)
👍 approve · 👎 deny — react to any part of this card, or reply approve / deny <reason>
digest 1a2b3c4d5e6f
<approve-bead's readout: the title, then "ask:" with the ask's fields, refs and linked beads, then the description>

Approving records your approval of <bead> in btq, as you, via Marmot. Any other reply is a note for the poster.
```

- **The approval label** comes only from the readout's `purpose` field, which is part of the bead's ask and of its digest: `design` or `execution`, or `purpose not set` when the bead has none (every bead until approve-bead reports the field). admind never infers it, and nothing on the host sets or overrides it. Any other value is a readout gap: the post is refused and the bead goes back for grooming. An approval ask stored before labels keeps its stored chunks, so its repeats have no label.
- **The digest line** (`digest12`) is the first 12 hex digits of the bead's context digest, as `approve-bead` computes it. admind stores the full digest. A decision passes the full digest to `approve-bead` (`--expect-digest`), never the 12 typed characters. Linked beads' `pinned digest` lines are cut to 12 digits the same way.
- **Refs.** Under each numbered ref is `link: <permalink>` pinned to the ref's commit, where the repo has a GitHub remote. Otherwise the card says `(no forge link; read it on the host with approve-bead --doc N)`. A link does not prove the commit is pushed: if it gives a 404, do not approve from the phone.
- **An approval card is never shortened** (R8). Its whole readout, digest line and closing lines are the card, split into `chunk_chars` chunks, and any chunk is the card for a reaction or a reply. The readout is capped at 24,000 characters (`MAX_APPROVAL_CARD`); a longer one is refused at post time (above).
- **The budget** (R15) applies to question and merge cards only: at most 40 lines and 3,500 characters of context, counted after redaction, cut at 3,500 characters, then at 40 lines, so the last shown line can end mid-line. The first line and the closing lines are fixed framing and are never cut. When something is left out, the card says so, and `!details` has the rest.
- **`!details` on a card.** Sent as a reply to any chunk of a card, or of its `!details`, `!details` (or `!details full`) returns the whole ask in lane 2, threaded to the command. For an approval, these are the chunks checked when the ask was posted, the same as the card. admind records which operator asked and how many chunks went out. It is no longer a step before approving, except for an ask stored before the delta (below).
- **What counts as a card.** Only messages admind itself queued as a card, as an ask's `!details` or as an `!asks repeat` of the card, and that were sent, count. A look-alike printed by the admin agent does not. A reminder (`!asks bump`, or an automatic one) is not a card, and neither is a notice.

### Questions with choices

A question posted with `--choice` or `--yes-no` ends with its choices, rendered by admind from the post, never from the body:

```
QUESTION: Multiple Options
❓ Ask k7m2 · question · posted by team-lead (a local process; unverified)
Which runtime for the T15 rerun?

<body>

A = rerun on bel-ci (recommended)
B = rerun on laptop-ci
C = skip the rerun
Answer: 👍 = A (recommended) · 👎 = none · or reply with a letter or your own answer (!answer k7m2 <text>)
Choices: A = rerun on bel-ci (recommended) · B = rerun on laptop-ci · C = skip the rerun
```

A yes/no question ends `Answer: 👍 = yes (recommended) · 👎 = no · or reply with your own answer (!answer p3qz <text>)` and `Choices: yes (recommended) · no`; with `--recommended no`, `Answer: 👍 = no (recommended) · 👎 = yes · …` and `Choices: yes · no (recommended)`. The `Choices:` footer is always the very last line of the card. When it is wider than a chunk, it is shortened to the letters (`Choices: A (recommended) · B · C`). A choice is one line of 1 to 80 characters, without controls, `·` or `=`, and must not be changed by redaction ("choice B would be redacted; reword it"). Choices are never cut: when a card is over the budget, the body is.

**How an answer is read on a question with choices** (on a question without them, as before: the reply or emoji is the answer):

| You send | Recorded as |
|---|---|
| 👍 (any skin tone), ✅, ❤️ or ♥️ | the recommended choice |
| 👎 (any skin tone) or ❌ | the option not recommended on a yes/no card (`no`, or `yes` when `no` is recommended), `none` ("none of these") on a multiple-choice card |
| a reply that is one choice letter (`a`, `B.`, `(c)`) | that letter |
| on a yes/no card: `yes` or `y`; `no` or `n` | `yes`; `no` (whichever is recommended) |
| on a multiple-choice card: `yes`, `y`, `ok`, `okay`, `lgtm`, `approve`, `approved` | the recommended choice |
| on a multiple-choice card: `no` or `n` | `none` |
| anything else, any other emoji | a free answer, with no choice |

The answer's text is always stored verbatim. The reply on the card says how it was read: "Recorded for k7m2: A = rerun on bel-ci (from 👍)." **Only approval cards decide approvals**: on a question (even one posted with `--bead`), a merge card or a notice, no reply, command or emoji ever starts an approval.

### Links

`--link <label>=<url>` adds a link to a question, a merge or a notice. The label is one line of 1 to 40 characters. The URL is at most 400 characters and must be a GitHub URL in a pinned form (`https://github.com/<owner>/<repo>/` then): `pull/<n>` (optionally `/files` or `/commits/<40-hex>`), `blob/<40-hex>/<path>`, `tree/<40-hex>/<path>`, `commit/<40-hex>`, `compare/<40-hex>...<40-hex>`, `actions/runs/<n>` or `issues/<n>`. The path is percent-encoded; no query, fragment, user or port. Anything else, a branch or a short SHA included, is refused ("--link must be a GitHub URL pinned to a 40-hex commit, or a PR, run or issue"). admind checks the form only and never opens the URL.

`--ref <path>[@<rev>]` makes the link for you, from the repository you run it in: `<rev>` (default `HEAD`) is resolved locally to its commit, and the client then asks GitHub (with `gh api`, for github.com only) whether the commit and the path exist at the repository `origin` names. Refused: "`<sha>` is not on GitHub at `<owner>/<repo>`; push it first", "`<path>` does not exist at `<sha>` on GitHub", "origin is not a GitHub repository", and, when `gh` is missing, not logged in, offline or slow, "can't check the link on GitHub; pass --link with a pinned URL instead". The label is the path. git runs with a fixed configuration and environment and never touches the network; local refs such as `refs/remotes/...` are never trusted.

The card shows the links in a `Links:` block after the body, one per line as `<label>: <url>`, with the URL last. A link line is never split across messages; one wider than a chunk is refused. If `chunk_chars` is lowered after a post so that one of its links no longer fits, `!asks repeat` skips that ask (`<id>: a link is longer than this group's message size now allows; raise chunk_chars to repeat it`) and `!details` on it is refused with the lengths, both with nothing sent; the `!details` refusal is audited as `details-refused` with reason `link too long`. The client prints a hint on stderr for a path in the title or body that is a file tracked in your repository, such as `hint: docs/x.md is a bare path; --ref docs/x.md renders it as a link`; it never refuses the post.

### Notices

`admind notice post` posts an FYI: news for the operators that asks nothing of them. Use it for status, not `--kind question`.

```
FYI: no reply needed
ℹ️ Notice · posted by team-lead (a local process; unverified)
PR #69 merged

Plan 4b Task 11 Part A is in main.
Links:
PR #69: https://github.com/<owner>/<repo>/pull/69
```

- It prints `posted <id>`, a 6-character ID for the audit, or `refused: <why>` on stderr (exit 1). `--json` prints the reply.
- **A notice is not an ask.** It has no status, no answer footer and no `!details`, and it is not in `ask list`, `!asks`, bumps or repeats. It never counts toward the active-ask or hourly ask limits.
- A reaction on a notice is ignored. A reply to it is ordinary chat, sent to the admin agent like a reply to any other message.
- Notices go in lane 2, behind every card, reply and command, so they never delay a card. A busy group can delay notices.
- Bounds: the title as for asks; the body is optional, at most 3,500 characters and 40 lines; at most 8 links; the whole notice, after redaction, at most 6,000 characters and 4 messages at the group's `chunk_chars`. At most 12 notices in any hour and 10 waiting to be delivered. Over a bound the notice is refused and nothing is queued. While latched, a notice is refused; before the group is verified it is stored and waits.

### Parts and threads

A card or notice too long for one message is split into parts. Chunk 0 is posted top-level and ends with `↳ part 1/<n>` (its label stays the first line); each later part starts with `↳ part <i>/<n>` and is posted as a reply to chunk 0, once chunk 0 is sent. If chunk 0 failed, or was sent without a message ID, the later parts go top-level instead (audited as `thread_fallback`); the outbox never waits for it. If a later part of a card, a repeat or a notice fails after all its retries, admind posts `part <k>/<n> failed to deliver, ask the poster to repeat` under chunk 0 (top-level if chunk 0 has no message ID), once, and audits `part_failed`. That message is not a card: nothing on it answers or decides anything. A failed chunk 0 is not reported this way (the card stays undelivered; `!asks repeat` sends it again), and neither are `!details` parts. Every part is at most `chunk_chars`, marker included. Lines are kept whole where they can be: the label, the head, link lines, choices, the answer line and the footer are never cut, and the choices block stays in the last part when it fits. A reaction or reply on any part counts as on the card, and a card counts as delivered only when all its parts are sent, as before.

### Answering and deciding

| You send | Effect |
|---|---|
| a reply or a reaction to a question or merge card, or to its `!details` | Stored as the answer: "Answer recorded for ask `<id>`." (or "Added to ask `<id>`; it was already answered, and the poster sees both."). The status becomes `answered`. A reaction's answer is its emoji. |
| a reaction to an approval card, or to its `!details` | 👍 (any skin tone), ✅, ❤️ or ♥️ approves; 👎 (any skin tone) or ❌ denies, with no reason. A trailing U+FE0F is ignored on every one. Any other emoji is ignored: no reply, audited `reaction-ignored` (R27). |
| a reply to an approval card, or to its `!details` | Read by R28 (below): an approve, a deny, or otherwise a **note** the poster sees, never a decision: "Noted on ask `<id>`; this is not a decision. React 👍 to approve or 👎 to deny, or reply approve / deny `<reason>`." |
| `!answer <id> <text>` | The same as a reply, from anywhere. `<text>` is everything after the ID, newlines included. Refused for an approval ask: "Ask `<id>` is an approval ask. To decide, react to its card or reply to it. Nothing recorded." |
| `!asks` | First reconciles any ask stranded in `deciding` or `uncertain` (see "Outcomes"), then lists the active asks, one line each: `k7m2 question · 3h · <first 60 characters of the title>`. If none, "No active asks." |
| `!asks bump` | The backstop as for `!asks`, then one reminder per `open` ask, oldest first, threaded to its card (see "Reminders"). The reply: "Bumped `<n>` asks: `<id>`, `<id>`." and one line per ask not bumped. |
| `!asks repeat` | The backstop, then each `open` ask's whole card again, top-level (see "Reminders"). The reply: "Repeated `<n>` asks: …" and the same lines. |
| `!approve` | Approve, as a reply to the approval card or to its `!details`. Arguments are optional: `!approve <bead> [<digest12>]`, and if given they must be the card's bead and digest (any case), or the command is refused (R7). |
| `!deny [<bead> [<reason>]]` | Deny, as a reply to the card or its `!details`. With arguments, the first must be the card's bead; the rest is the reason. |
| `!details` (as a reply to a card) | The ask's full text (above). |

**How a reply on an approval card is read** (R28). The text is trimmed, Unicode-casefolded, and stripped of trailing `.`, `!` and whitespace. It is an **approve** when the result is exactly one of `approve`, `approved`, `yes`, `y`, `ok`, `okay`, `lgtm`, or exactly one approve emoji. It is a **deny** when its first word, compared the same way and with one trailing `:`, `,`, `-` or `—` removed, is `deny`, `denied`, `reject`, `rejected` or a deny emoji, or the whole reply is `no` or `n`. The rest after the first word, trimmed, is the deny reason: redacted, at most 1,000 characters, possibly empty, and passed to `approve-bead` as the denial's note. A reason that opens with one of those marks on its own, followed by whitespace, loses the mark and the whitespace. So "rejected: too broad", "deny, too broad" and "deny - too broad" deny with the reason "too broad". An approve word takes no such mark. Anything else is a note. So "yes, but what about X?", "approve, but", "approve it later" and "no idea" are notes. The word and emoji lists live in one place, `heterodyne.admind.verbs`.

A reply or reaction to a card is never pasted to the admin agent, and neither is any reaction. A reply that starts with `!` is a command. Every other message, replies to other messages included, behaves as in sections 4 and 5. A reaction to anything that is not a card is ignored. An answer is refused if the ask is no longer `open` or `answered`, if it is empty, or if it would pass the limits below.

**Reactions** (R27) go through the same guard and the same worker as messages, judged on the reacting member's key: one from another group, from a non-operator or while latched is dropped and audited; admind's own is ignored. Each reaction is claimed once, under its event ID (`r:<event id>` in the database), so a replay is dropped; a reaction without a 64-hex event ID is dropped (`malformed reaction id`). admind's reply to a reaction is threaded to the reacted message, never top-level. Removing a reaction changes nothing: a recorded decision is final, and an answer stays.

**How a decision is checked**, in order, whether it is a reaction, a reply or a command. Each refusal leaves the ask as it was and records nothing:

1. It is a reaction or reply to a chunk of the card, or of its `!details`.
2. Any arguments of `!approve` or `!deny` are the card's bead (and, for `!approve`, digest).
3. The ask is `open`. A second decider is told who decided first (R31).
4. For an approve, every chunk of the card is sent, or every chunk of one `!asks repeat` of it (chunks of different repeats never combine). For an ask stored before the delta (`asks.truncated = 1`, a shortened card), **this operator** has also asked for `!details` on it, and every chunk of that `!details` is sent. A deny needs neither (R8).
5. admind is still authorised for the message (not latched, group verified), and `approve_bead` is set.

Then admind persists the attempt (`deciding`) and runs `approve-bead <bead> --json` (60 seconds). The decision stops before anything is written if the bead is busy, has changed since the card (`stale`), already holds a decision (`blocked`), is no longer open or no longer `kind:approval`, or the operator is not in its `approvers`. Right before it starts the decision run, admind checks authorisation once more (R11): this is the commit point. The run is `approve-bead <bead> --as=<name> --yes --expect-digest=<full digest> --via=marmot --via-ref=marmot:id:<12 hex>`, with `--deny --note=<reason>` for a denial, every value as one `--flag=value` argument. For a reaction, the `<12 hex>` is of the reaction's event ID. It has 90 seconds. Once started, the run is not cancelled by a latch, a lost subscription or a group change, because killing it mid-write would leave a partial write. The reply then waits in the outbox like every post of a latched admind, and the audit says `latched_during`. On every exit, admind kills the run's process group and reaps it before it settles anything.

### Reminders

**`!asks bump`** posts one reminder per `open` ask, oldest first: `Still outstanding: ask <id> · <kind> · <age> · <title>.`, then "React 👍 or 👎 on the card above, or reply to it with approve or deny `<reason>`." for an approval, or "Reply to the card above to answer." otherwise. The title is redacted whole and then cut at 80 characters, so a secret is never partly shown. Every reminder, however often you bump, is threaded to chunk 0 of the ask's original card, never to a `!details` chunk or an earlier reminder; a refreshed ask (R29) is bumped on its own card. An ask that is not `open` is named in the reply instead: `<id> is answered; awaiting its asker` (an answered ask waits on its poster, even after it collected the answer), `<id> is deciding`, `<id> is uncertain`. An ask whose card's chunk 0 was never sent is named `<id>: card not delivered; try !asks repeat`. With no active asks, the reply is "No outstanding asks." The reminders, the reply and the command's `done` are one transaction, so a replay posts nothing twice. `!asks bump`, `!asks` and `!answer <id> <text>` work as replies to anything, a reminder included.

**A reminder is not a card.** Reacting or replying to it never decides or answers. A plain reply to a reminder, `!approve`, `!deny` or `!details` sent as a reply to one, or a 👍 or 👎 on one, gets one reply and nothing else: "That was a reminder. React or reply on ask `<id>`'s card (the message the reminder replies to)." It is threaded to your reply, or for a reaction to the reminder. Any other reaction on a reminder is ignored. Nothing on a reminder reaches the admin agent.

**`!asks repeat`** posts each `open` ask's whole card again as new top-level messages, for a card that failed to deliver or that you cannot find. An approval's repeat is the chunks checked when it was posted, unchanged even if `chunk_chars` has changed since; a question's or merge's is its whole text, split. A repeat is a card: react or reply to any chunk of it as to the original. Once every chunk of one repeat is sent, the ask counts as delivered for an approve. A repeat does not replace `!details` for an ask stored before the delta (step 4 below). While the original card's chunk 0 is not sent, reminders thread to chunk 0 of the earliest wholly sent repeat; once the original is sent, to the original.

**Automatic reminders.** Every 5 minutes admind bumps each `open` ask that has been quiet for `ask_bump_hours` (`[admind]`, default 12, 0 turns them off; see [configuration](configuration.md)). The reminder starts `Still outstanding (automatic reminder, every <h> h):` and threads as above; no reply is posted. An ask's clock starts when it is posted and moves to the latest of: a reminder or repeat queued for it; and a reply, reaction, note, answer, decision attempt or `!details` on its card, or an `!answer <id>` naming it, once admind accepted it, even if it then refuses it (an oversized `!deny`, a decision that is not possible yet, a control character); a message dropped at the door or replayed, a reminder hint or a listing moves nothing. An ask whose earlier reminder or repeat is still waiting to be sent is skipped until that row is sent or has failed. Nothing is posted while admind is latched, the group is unverified or no operator has been seen; the check then waits for the next run and moves no clock. After a long stop each ask gets one reminder, not one per missed period. On upgrade every existing ask's clock starts at the upgrade, so the first automatic reminders come `ask_bump_hours` later.

### Outcomes and recovery

The outcome is **read back**, never parsed from the exit status (R12). Whatever the run did, admind reads the bead again with `--json` and compares it with the persisted attempt:

- The bead is closed with this attempt's decision, name, digest and `via_ref`: `approved` or `denied`. For an approval, the reply also says whether btq's design gate accepts it.
- The bead is open with no decision field: nothing was recorded, and the ask is `open` again.
- Any other decision field, or a partial write: `blocked`. This is final; resolve the bead at the terminal. A blocked ask no longer counts toward the limits.
- The read failed, or was still busy after 3 more tries 20 seconds apart: `uncertain`. No decision is taken from Marmot until a read-back settles it.

Exit 5 ("written, but the content changed during the write") and "written, but the gate rejects it" are settled the same way, from the read-back.

**A changed bead gets a fresh card** (R29). If the decision read finds the bead's digest differs from the card's, or the read-back after the run finds nothing written (`--expect-digest` refused it) and a new digest, nothing is recorded and the ask becomes `stale`. In the same settlement, still in the worker and holding the post lock, admind posts a **fresh approval ask** for the bead's current content, with every check a post has (above), except that the stale ask's own attempt does not count as "another ask for it is `deciding`". The fresh ask keeps the stale one's `--from` and records `refreshed_from`. Closing the attempt, the fresh ask, its card and the reply are one transaction: a crash before it leaves the attempt `deciding` for the restart read-back (the ask is `open` again and the next decision refreshes), and after it they all exist. The decision is never carried over: decide the fresh card. If the fresh card cannot be posted, the reply says why. A bead whose stored `context_digest` pin no longer matches its content cannot be decided until its originator renews the pin and posts it again; admind never writes that pin. It still sends the updated content, threaded where the reply goes (the decision reply, or the chunk that was reacted to), headed "Updated content of `<bead>` (now `<digest12>`). It cannot be decided yet: its stored pin no longer matches, so its originator must renew the pin and post it again. Reactions and replies here decide nothing." Those chunks belong to the stale ask, so a reaction or reply on them gets "already stale". If the updated content would be redacted or is over the cap, the reply says so and nothing more is posted.

**After a restart.** A decision interrupted by a stop, whether started by a reply or by a reaction, leaves its ask `deciding`. At startup, before any loop runs, admind takes a snapshot of the asks in `deciding` or `uncertain` that have a persisted attempt. The read-back itself (`Admind.reconcile_asks`) then runs under the worker's lock as a task alongside the loops, since a busy bead can hold it for minutes. A notice goes to the decision's thread: under the reply that started it, or under the chunk of the card or of `!details` that was reacted to. If nothing was recorded, the notice is "admind restarted while recording this decision; nothing was recorded. Decide again." Startup's generic recovery, which answers each interrupted message with "admind restarted", leaves the message of such an attempt to the read-back: the decision may have been recorded, and "resend it" would then be wrong. The read-back marks the message done, so its notice is the only one. `!asks` runs the same reconcile for anything left stranded. If nothing was recorded there, the notice is "admind could not finish settling the last decision on ask `<id>`; nothing was recorded. Decide again." If `approve_bead` has been removed since (for a rollback, say), there is nothing to read back with: the message is marked done and the thread gets the one "could not check whether this decision … was recorded" notice from the table below, never "decide again". The ask and its attempt are left as they are, so once `approve_bead` is set again the next start settles them from the read-back.

**Replies in the thread** (fixed wording; `<line>` is the first line of `approve-bead`'s stderr, redacted, at most 300 characters):

| Event | Reply |
|---|---|
| approved, gate accepts | `Approved <bead> as <name> (digest <digest12>, via Marmot). btq's design gate accepts it.` |
| approved, gate rejects | `Approved <bead> as <name> (digest <digest12>, via Marmot), but btq's design gate rejects it: "<first gate reason>". Check it on the host.` With no reason from btq: `(btq gave no reason; run approve-bead <bead> on the host)` in place of the quote. |
| denied | `Denied <bead> as <name> (via Marmot).` |
| recorded or uncertain, with a stderr line | the reply above (or the uncertain one), then a line `approve-bead said: "<line>"` |
| nothing recorded | `Not recorded: "<line>". <bead> is unchanged; you can decide again.` (without the quote if there was no line) |
| blocked | `<bead> holds a decision admind cannot confirm as yours ("<line>"). Nothing more will be done from Marmot. Resolve it on the host with approve-bead <bead>.` |
| uncertain | `admind could not read <bead> back. No further decision is taken from Marmot until it can; check it on the host with approve-bead <bead>.` |
| stale, fresh card posted | `<bead> changed after this card was posted (shown <digest12>, now <digest12>). Nothing recorded. A fresh card follows: ask <new>.` |
| stale, no fresh card | `<bead> changed after this card was posted (shown <digest12>, now <digest12>). Nothing recorded, and admind could not post a fresh card: <reason>.` For a pinned bead whose updated content cannot be shown, ` Its updated content is not shown: <reason>.` follows. |
| stale, after the run | either stale reply, then a line `approve-bead said: "<line>"` |
| `!approve`/`!deny` not on a card | `To decide, react to the approval card or reply to it. Nothing recorded.` |
| arguments not the card's | `This card is for <bead>. React 👍 to approve or 👎 to deny, or reply approve / deny <reason>. Nothing recorded.` |
| deny reason too long | `Not recorded: a deny reason is at most 1,000 characters.` |
| card not fully delivered | `Ask <id> has not been fully delivered yet. Wait for every part, then react or reply again. Nothing recorded.` |
| needs `!details` (an ask stored before the delta) | `Ask <id> was shortened. Reply !details to it and read it first. Nothing recorded.` |
| not open | `Ask <id> is already <status>[ by <name>][; see ask <newer>]. Nothing recorded.` |
| not an approver | `<name> is not a btq approver. Nothing recorded.` |
| bead closed or not an approval | `<bead> is <status>, not open. Nothing recorded.` / `<bead> is not a kind:approval bead. Nothing recorded.` |
| busy | `<bead> is being decided elsewhere right now. Nothing recorded; try again in a minute.` |
| the check failed | `admind could not check <bead> (<word>); nothing was recorded. Try again.` The word is `timed out`, `unavailable`, `bad output` or `error`. |
| not configured | `Approval asks are not configured on this host ([admind] approve_bead).` |
| settled concurrently | `Ask <id> changed while this decision was settled; see !asks. Check <bead> on the host.` |
| interrupted, and `approve_bead` is no longer set | `admind could not check whether this decision on <bead> was recorded: approve-bead is not configured on this host ([admind] approve_bead). Check it on the host with approve-bead <bead>.` Sent once per interrupted decision. |

**The host steps in these replies are break-glass.** The wording is fixed, but the host is the last step, not the first (ADR 0001 §8.1):

- `blocked` is host exception 6: the relay takes no further decision on that bead, and only `approve-bead` at the terminal resolves it.
- For `uncertain` and "settled concurrently", send `!asks` first: it reconciles any ask stranded in `deciding` or `uncertain` and shows where each ask stands. Go to the host only if the ask stays `uncertain`.
- For "interrupted, and `approve_bead` is no longer set", admind cannot check from Marmot at all until `[admind] approve_bead` is configured again, which is a host change.
- For "approved, gate rejects", the decision is recorded; the gate's reason is for the bead's originator to act on.

### Limits

| Limit | Value |
|---|---|
| active asks (`open`, `answered`, `deciding`, `uncertain`) | 20 |
| asks posted in any 60 minutes | 30 |
| posts in flight on `ask.sock` | 2 (the third is refused as busy) |
| `ask.sock` request | 262,144 bytes (256 KiB), read within 5 seconds |
| `ask.sock` reply | 1,048,576 bytes (1 MiB); the client allows 30 seconds for each request, and a `post` 160 seconds: 2 posts in flight × (a 60-second read + 5 seconds to reap it) + 30 |
| title | 1 line, 1 to 200 characters |
| body (question, merge) | at least 80 non-space characters, at most 16,000 characters |
| question or merge card | 40 lines and 3,500 characters of context |
| choices | 2 to 6, each 1 to 80 characters |
| links | 8 per post (`--link` and `--ref` together); label 40 characters, URL 400 |
| notice | title as above; body 3,500 characters and 40 lines; 6,000 characters and 4 messages in all |
| notices | 12 in any 60 minutes; 10 waiting to be delivered |
| approval card readout (`MAX_APPROVAL_CARD`) | 24,000 characters |
| one answer or note | 16,000 characters |
| automatic reminders | checked every 5 minutes; one per ask per `ask_bump_hours` of quiet (default 12, at most 720) |
| answers and notes per ask | 50, and 64,000 characters in total |
| deny reason | 1,000 characters, after redaction |
| `approve-bead --json` read | 60 seconds; stdout capped at 8 MiB, stderr at 1 MiB |
| decision run | 90 seconds; stdout and stderr capped at 1 MiB each |
| reaping a child | 5 seconds |
| a busy read-back | retried 3 times, 20 seconds apart |
| quoted stderr line | 300 characters |

### Audit records

Every record about asks has kind `ask`. Asks are recorded by IDs and lengths, never by text. Operator text is recorded whole in the `inbound` record, as for every message (section 7). A message ID appears only as `id:` plus 12 hex digits of its SHA-256; a reaction's key appears as `r:id:<12 hex>`, the hash being of its event ID.

- A request on `ask.sock`: `op` (`post`, `get`, `list`, `cancel`, `notice`), with `ask_kind`, `poster`, `bead`, the title and body lengths, `form` and the number of `choices` and `links` for a post.
- `posted`: `ask_id`, `ask_kind`, `poster`, `pid`, `parts`, `truncated`; for a question or merge also `title_chars`, `body_chars`, `form`, `choices` and `links` (counts, never text); for an approval also `bead`, `digest12`, `details_parts`, `superseded` (the asks it replaced) and `refreshed_from` (the stale ask a fresh card replaced, or null). A fresh card's `pid` is null.
- `refused`: a post or a decision refused before it started. `reason` is one of `limits`, `busy`, `not postable`, `redaction`, `too long`, `not a card reply`, `no match` (arguments not the card's), `reason too long`, `not open`, `not delivered`, `needs details`, `not configured`, `stale`, `decided`, `not approval`, `not an approver`, `link too long`, or a check's word (`unavailable`, `timed out`, `bad output`, `error`). A refusal after the attempt was persisted also has the ask's new `status`. A `stale` one also has `fresh` (the fresh ask, or null), `refresh_refused` (why there is none, or null) and `updated_parts` (chunks of a pinned bead's updated content).
- A reaction's `inbound` record has `what` `reaction`, `ref` (`id:<12 hex>`), `emoji` and `target`. Dropped reactions are `drop` records with `what` `reaction`.
- `deciding`: an attempt is about to run (`message_id`, `ask_id`, `bead`, `decision`, `operator`).
- `decided`: `outcome` (`recorded`, `untouched`, `blocked`, `uncertain`), `status`, `exit_status`, `gate_valid`, `latched_during`. An `untouched` read-back with a new digest is `reason` `stale`, with `fresh`, `refresh_refused` and `updated_parts` as above.
- `!asks bump` and `!asks repeat` are `command` records with `command` `asks`, `sub` (`bump` or `repeat`), `bumped` (the ask IDs) and `skipped` (`ask_id` and `why`: `answered`, `deciding`, `uncertain` or `card not delivered`). An automatic check that bumped or skipped something is an `autobump` record with `bumped` and `skipped` (`why` may also be `pending delivery`); a check that found nothing due writes nothing.
- `bump-hint`: a reply or a decision emoji on a reminder was answered with the hint (`message_id`, `ask_id`).
- `answered` has `choice` (the choice an answer was read as, or null).
- A notice is one record of kind `notice`: `action` `posted` or `refused`, `poster`, `peer_pid`, `title_chars`, `body_chars`, `links`, and `notice_id` and `parts` when posted, or `why` when refused. `why` is a fixed code: `latched`, `title`, `body-too-long`, `notice-too-long`, `too-many-parts`, `too-many-links`, `link-label`, `link-format`, `link-too-long`, `link-line-too-long`, `link-redacted`, `rate-hour`, `undelivered`, or `internal` with the exception's class name as `error`. No notice text is ever audited.
- `part_failed` (kind of its own): a later part failed for good and its report was queued; `key` (the failed part) and `parent` (chunk 0's key).
- `thread_fallback` (kind of its own): a later part went top-level; `key`, `parent` (chunk 0's key) and `why` (`no-message-id`, `parent-failed`, `parent-missing` or `parent-pending`).
- Also `reaction-ignored` (an emoji that decides nothing, on an approval card), `answered`, `noted`, `answer-refused`, `details`, `cancelled`, `conflict` (a compare-and-set found the ask changed), `read-back-failed`, `reconciled` (`was`, `restarted`), `reconcile-unverified` (the notice above was sent because `approve_bead` is not set), `reconcile-skipped` and `reconcile-failed`. A decision dropped because authorisation was lost is a `drop` record with `what` `decision`.

**Matching a bead to the audit.** A bead decided from Marmot holds `via: marmot` and `via_ref: marmot:id:<12 hex>`. The `id:<12 hex>` part is exactly how the audit names the operator's message, or a reaction's `ref` (R27). Search `audit.jsonl` for it to find that message's or reaction's `inbound`, `deciding` and `decided` records:

```sh
grep -F 'id:<12 hex>' <state>/admind/audit.jsonl
```

### Complete local ask binding evidence

`admind ask binding ASK --json` reads the new `ask.binding.v1` operation over the running daemon's
local ask socket. It returns a versioned, closed evidence envelope with the complete stored render
inputs (including chunk size and historical approval in-flight counts) and canonical SHA-256, reproduced card parts and their local outbox delivery state, all retained
answer/note records with native message and sender provenance, and separately named native decision
attempts. Native `outcome`, `decided_by` and `current_attempt` are included separately;
a settled attempt does not substitute for these stored fields. The recommendation comes from the stored form, including a recommendation other than A.
Policy principals and their canonical fingerprint are read afresh; the envelope records Linux boot ID
and BOOTTIME at the read. An answer's `current_policy` flag describes only its configured principal,
not current group membership or new authority. Revoked actors and contrary answers remain in history.

Exit 0 means complete evidence was returned, not that an operator approved anything. `delivered` means
the original or a full repeat's outbox parts are marked sent; it does not prove remote visibility or
human acceptance. Decision attempts remain attempts, including uncertain or unsettled ones. Existing
`get` and `list` response formats are unchanged. The reader does not write SQLite, execute native task
commands, accept caller policy/files, or bypass a latch.

Exit 1 returns a fixed typed refusal when evidence is missing, incomplete, oversized, redacted, truncated,
not reproducible by the current renderer, or has invalid state, unavailable policy, or unavailable boot
clock evidence. It never returns partial evidence as complete. Legacy records without durable sender
provenance, stored inputs rejected by current posting rules, and cards rendered with an incompatible
chunk configuration may therefore be refused. An originally truncated card remains refused even after
a full repeat. Free answer/note prose gets no public-digest exemption: if redaction would change it,
the whole binding is refused without modifying the native record. Typed decision digests remain typed
provenance; this does not make arbitrary answer text safe to expose.

The CLI also emits fixed JSON for invalid IDs and incompatible/legacy replies (`invalid-state` or
`invalid-reply`), and for a connected response deadline (`response-timeout`, exit 1). Exit 69 includes
fixed JSON with `transport-unavailable`; it means connection/transport failed, not that an approval
was declined. A daemon cannot select these client-only failure codes, and an envelope for a different
ask is rejected. Error messages never echo peer text. The bounded read uses an independent read-only
SQLite WAL snapshot off the event loop; it holds neither the post lock nor the live Store lock while
rendering. Uncommitted writes are excluded, and ordinary daemon reads/writes can continue. All returned
approval prose is screened, including fields not printed on the card; only validated typed digest and
Marmot reference identities are exempt. Historical raw prose that would redact still refuses. New
settlement outcomes are redacted in the existing atomic decision transaction, without changing their
native status/actor/attempt identity. This interface requires deployment before use.

## Read-only ingress status

`admind ingress status --json` samples the running daemon through its same-user
`<state>/admind/ingress.sock`. It does not rearm, reconnect, refresh policy, read
message history, acquire dispatch locks, or contact the upstream transport. Polls
append no request audit records. The request is limited to 4 KiB, its reply to
16 KiB, and the cooperative client operation to five seconds, including cleanup.
The diagnostic client aborts its transport rather than awaiting graceful close;
this is not a hard scheduling or process-preemption guarantee. An absent endpoint, incompatible
reply, foreign peer or timeout fails closed; an older installed release can lack
this endpoint. A diagnostic socket bind failure leaves the main daemon running
and records a fixed diagnostic-unavailable audit event; it does not unlink a
foreign file or symlink.

The closed JSON reply reports the current process's opaque instance identifier,
monotonic ages, subscription generation and existing acknowledgement/reading/
observation gates, actual task states, work/transition/custody lock holders, queue
counts, and fixed progress/drop counters. A held lock whose category is not known
reports `unknown`. Custody control and autobump have distinct holder categories;
a custody worker waiting behind custody control reports `waiting_custody_lock`.
Recovery task activity is observed through its actual startup task and work lock.
Later maintenance retries and recovery socket control use distinct `maintenance`
and `recovery_control` lock holders, even after startup recovery has finished.
`dropped_guard` counts normal message/reaction guard refusals;
`dropped_custody_guard` counts custody command-auth refusals. Authentication and
enqueue counts distinguish normal and custody traffic; decoded input, capacity,
and handler failures are shared observations. `handler_failed` counts a
failed event once at its reader or normal/custody handler boundary, including
caught custody source/store failures; cancellation is not an event failure.
`connecting` precedes the connection/write milestone; `awaiting_ack` follows the
subscription write. `closing` is an owned transport teardown. `reconnects` counts
ended/failed subscription cycles, not successful reconnects. Counters saturate
with an overflow marker and reset with a new process instance. They are local
observations, not a persistent history or receipt.

Queue byte accounting measures the canonical normalized event payload retained
in queued and dequeued unfinished slots. It is not allocator/RSS usage. Durable
disposition completion, release authentication and the last transport error
category remain unavailable. The `completed_normal`
and `completed_custody` counters therefore remain zero: returning from a handler
must not be interpreted as durable completion. `claimed` counts successful Store
claims; `ask_answered` counts the local answer transaction returning, which does
not establish end-to-end delivery or an independent durable completion receipt.
`decoded_any` observes decoded events only, not raw transport frames. Task
supervision can retry an existing background loop; the snapshot reports the
current phase rather than a complete failure history.

A responsive endpoint, quiet queues, or `reading=true` cannot establish transport
health, current native-group equality, replay completeness, or permission to
change anything. Status itself grants no recovery authority.

`admind ingress reconnect --instance ID --generation N --request-id UUID` asks the
same-user daemon to cut over its existing subscription. Copy the opaque instance
and generation from status and use a new canonical UUID. A busy/refused reply
changes no generation or gate; an accepted reply identifies a retained lifecycle
task, not restored delivery. Repeating that exact request returns its latest
in-memory result even after its generation becomes old. The daemon retains at
most 64 request records (pending records cannot be evicted); restart loses them.

Reconnect closes new subscription intake, retains already decoded, queued and dequeued events,
and waits for the exact original reader and transport to cease before opening a
replacement. It preserves asks, attempts, membership authority, conversation and
all execution holds. It never restarts wn-agent, replays answers or sends invites.
The shared recovery budget is 125 seconds, original teardown at most two seconds,
and connection/write/ACK/group verification phases at most 30 seconds each within
the remaining budget. Uncertain teardown retains the original handles and closed
gates, with one bounded audit and system outbox escalation notice per unknown episode; a fresh
exact-generation request retries that custody. The notice is advisory, not delivery proof. Observation failure
returns control promptly when the replacement reader ends, or at the shared deadline,
to the one automatic backoff supervisor. Unexpected loop faults retain transport custody and
permit that supervisor to restart; they do not set the daemon shutdown flag. Only a current-generation
ACK plus stable group verification can reopen workers. A stop cancels the retained
cutover and prevents a replacement.

Each normal/custody queue admits at most 256 unfinished events, sharing 8 MiB of
normalized retained bytes. Dequeue does not release capacity. Overflow is refused
before Store claim, counted, and audited once per capacity episode; nothing is
evicted. Already decoded messages/reactions remain bounded in these queues across intent or
generation invalidation; workers cannot claim them until current observation. Stale non-message
events are counted and audited without adopting membership observation. `dropped_intake` remains
a compatibility counter, not a claim that buffered frames were discarded during cutover. This is
not a durable inbox: restart may lose unclaimed memory, and transport history or
replay completeness remains unqualified.

Reconnect mutation requests, refusals and handler failures are audited with fixed categories;
status polls remain quiet. Invalid local reconnect selectors return usage error 2 before socket
access, distinct from an unavailable endpoint. Ended/failed reader cycles retain bounded lifecycle
audit categories without peer error detail.
