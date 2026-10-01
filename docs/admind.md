# admind: the admin override channel

This is the operator runbook for `admind` (ADR 0001 §8). The ADR is authoritative; this page describes what the code does. Placeholders in `<angle brackets>` stand for values that belong to your install and never go in git. `<state>` is the state directory: `$HETERODYNE_STATE_DIR`, else `${XDG_STATE_HOME:-~/.local/state}/heterodyne`.

## 1. What it is

`admind` is the recovery path for when everything else is broken. It runs as its own service unit with its own Marmot identity, in a two-member group made of the operator and the admin bot. The operator's text goes unmodified into a persistent interactive session of the admin agent (an LLM CLI running in a private tmux server), and the agent's replies come back to the group, chunked and, where possible, as thread replies. A handful of `!` commands (`!new`, `!interrupt`, `!tail`, `!ps`, `!restart`) are handled by admind itself and need no LLM. admind also relays `wsd`'s local alert files to the operator (section 6).

**Independence** (ADR §8). admind:

- has its own service unit and shares no dependency with `wsd`, the Hermes gateway, beads or the gatekeeper;
- has its own Marmot identity and connection, through a private `wn-agent` child that it spawns and supervises itself (own home, own socket, own bearer token);
- refuses to operate if the group is not exactly two members, and accepts messages only from the operator's exact npub, MLS-authenticated. Anything else is dropped and logged without its content.

**Deliberate privilege.** The admin agent runs as the harness's service user, with permission prompts bypassed, no sandbox and no root. Its purpose is to repair anything the harness can break, so a least-privilege identity would defeat it. This is an operator decision (2026-09-29). The design review's objection and the rebuttal are recorded in the [r1 response](reviews/0001-design-review-r1-response.md) ("Finding 10: admind"); the accepted residual risk is repeated in the [security model](security-model.md#the-admin-override-channel-admind-8).

## 2. Install

1. **Host config.** In `config.toml`, add `[admind]` and `[admind.marmot]` (see `examples/config.toml` and [configuration.md](configuration.md#admin-channel-admind)). `profile` names a profile whose adapter is `claude-code`; `marmot.relays` lists your relays; `restart_units` lists the only units `!restart` will accept. In `policy.toml`, list exactly one name under `operators` and give that name an `identities.<name>.marmot_npub`. `[platform] service_manager` must be set (run `heterodyne setup` first).
2. **Create the identity and group.**

   ```sh
   admind init
   ```

   This starts the private `wn-agent` child, creates admind's identity (if its home has none) and a group with the operator, and stops the child. The bootstrap output holds invite details, so admind captures it and prints nothing from it. The operator then accepts the invite in their Marmot client. `admind init` refuses to run again once a group exists; to start over, stop admind and remove `<state>/admind/` (this abandons the identity and group).
3. **Render the unit.**

   ```sh
   admind unit > ~/.config/systemd/user/heterodyne-admind.service
   ```

   `admind unit` captures the current shell's `PATH` (and `HETERODYNE_CONFIG_DIR`, `HETERODYNE_STATE_DIR`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME` when set), so render it from a shell where `claude` and `wn-agent` resolve. The unit is never committed. `admind unit` **refuses** (exit 78, `EX_CONFIG`, nothing on stdout) when any captured value contains whitespace, a quote, a backslash, a control character, or sensitive material (a secret, an npub or a 64-hex value). It does not redact, because the unit must be installable verbatim: fix the path or environment and render again. The unit has `RestartPreventExitStatus=78`, so a configuration error does not restart-loop.
4. **Enable it.**

   ```sh
   systemctl --user daemon-reload && systemctl --user enable --now heterodyne-admind
   ```

5. **Join signal.** Send any message to the group. admind posts nothing until it has seen a message from the operator (the join signal, D5; Task 10 verifies this against a live client). It then replies with the ready notice and releases any held alerts.

## 3. Before first use of the admin agent

On its first launch in `workdir`, Claude Code may show its bypass-permissions acceptance and workspace-trust dialogs. Accept them once:

```sh
tmux -L heterodyne-admind attach -t admin
```

Detach with `Ctrl-b d`. Until then the agent cannot start a turn, and `!tail` shows the dialog on the screen.

## 4. Using it

- **Passthrough.** Any message that does not start with `!` is pasted, byte for byte, into the admin agent's session. Messages are delivered one at a time, only to an idle agent (see the latch and the held queue below).
- **Thread replies.** The agent's reply to a message is posted as a reply in that message's thread, when admind has evidence that the agent took that exact prompt (the agent's `UserPromptSubmit` hook, matched ignoring whitespace). Otherwise it is posted top-level (see section 5, "How replies are anchored").
- **Chunking.** A reply longer than `chunk_chars` (default 4000) is cut into chunks, preferably after a newline in the second half of a chunk, whose concatenation is exactly the reply. **There is currently no cap on the number of chunks.** A very long reply (the hook frame limit is 1 MiB, and a transcript fallback can be larger) becomes as many messages as it needs. This is an open operator decision; if it matters to you, keep the agent's answers short until a cap is decided.
- **Reply source.** The reply is the `Stop` hook's `last_assistant_message`. If a Claude Code build omits it, admind falls back to the last assistant text of the current turn in the session's own transcript file (never the screen), except for a stale `Stop` (see "Launches and stale events"), which never uses the transcript, and a `Stop` whose turn changed while the transcript was being read, whose extracted text is discarded (see "Stop semantics"). The fallback is bounded: it starts from the last 8 MiB of a regular file (never through a symlink) and, if the file grows while it is being read, reads at most 16 MiB in total. A single transcript record larger than the initial 8 MiB window is excluded and yields no text (and so `NO_REPLY`); a record appended during the read can still be extracted.
- **Empty replies.** A reply that is empty or only whitespace is delivered as `(the admin agent's turn ended without a text reply)`, never as an empty message.
- **Commands.** Text starting with `!` is always a command and never reaches the agent. A mistyped `!restrat` is an error reply, not a prompt, and a leading `!` would otherwise switch Claude Code's input box to bash mode.

  | Command | Effect | Example |
  |---|---|---|
  | `!new` | Retire the current agent session and start a fresh one. Messages in flight get a "no reply" notice. | `!new` |
  | `!interrupt` | Send Esc to the agent, end the turn in flight, and release the held queue. Use it when a turn is stuck or its prompt was lost. | `!interrupt` |
  | `!tail [n]` | Show the last `n` lines (1 to 500, default 40) of the agent's screen. This is the only place admind reads the screen, and only on request. | `!tail 80` |
  | `!ps` | Status of each unit in `restart_units`, plus `wn-agent (admind)` and `admin agent`. | `!ps` |
  | `!restart <unit>` | Restart one unit from `restart_units`. Any other unit is refused with a reply that lists the allowed units and does not repeat what you typed. | `!restart <unit-name>.service` |

- **Control characters.** A message containing a C0 or C1 control character (other than tab and newline) is refused with a reply, never altered: such a character can break out of the terminal's bracketed paste. The check runs before commands are parsed, so a command name or argument holding one is never parsed or echoed.
- **Output policy.** Anything admind prints or sends that quotes your input goes through one helper that replaces a secret, npub or 64-hex value with `<redacted …>` and escapes control characters as `\xNN`. That covers command-parse errors, `!restart` output, unit names, alert names and the audit log. Failures are reported with fixed wording (`a tmux command failed`, `internal error`), never the underlying error text, which can contain paths or identifiers. The agent's own reply and the `!tail` screen are relayed verbatim by design, and are never written to the audit log.

## 5. The latch

**What triggers it.** admind latches when:

- the member count is anything other than 2 (checked at start, after each message, every `group_check_seconds`, and on every resubscribe), or
- the membership subscription reports any membership or admin event (`member_added`, `member_removed`, `member_left`, `admin_added`, `admin_removed`) in admind's group.

`group_info` returns a count, not a member list, so a swap that keeps the count at 2 is visible only as an event.

**While latched, admind is silent.** Every inbound message is dropped, nothing is posted (replies, notices and alerts alike), and queued prompts stay held. The latch is stored, so it survives a restart. It does not clear itself.

**Recovery.** Look at the group's member list in your Marmot client. If it is the two members you expect, run on the host:

```sh
admind rearm
```

It clears the latch and prints what it was. The running admind needs no restart: its next periodic group check (every `group_check_seconds`) re-verifies the count, and if the membership subscription is still live, posting and dispatch resume. If the subscription had dropped, they resume once it is re-established and verified. admind re-checks the member count, but it cannot see a one-for-one swap, so the check in your client is the real one.

**Posting needs a live subscription.** admind posts and dispatches only while the membership subscription is live and the group has been verified. After any outage the order is: resubscribe, then verify the count, then observe events. Nothing is sent while the subscription is down, even if a count check succeeds. Every subscription attempt has its own generation, and a count check is tied to the generation it started on: only a check that began after the subscription was acknowledged, and that finishes while it is still the current one, can turn observation on. A slow answer that arrives after a disconnect (from an earlier subscription) is discarded and cannot enable posting on the replacement; the replacement is always verified by its own check. An old answer that reports a wrong member count still latches admind (it fails closed).

**Authorisation is rechecked before side effects.** A message accepted while admind was authorised is checked again after each wait: after the group check, immediately before a command runs or a prompt is dispatched, and (for `!interrupt` and `!new`) after taking the dispatch lock and before any Esc or state change. If admind has stopped being authorised meanwhile, the message is dropped with fixed wording; the operator gets a "could not verify the group membership, try again" reply unless admind is latched, in which case nothing is sent. The check happens before the action starts; it does not cancel one that is already running. A command that has begun (a slow `!restart`, say) runs to completion if a latch lands meanwhile, and its reply is held in the outbox by the outbound gate until the latch is cleared and the group is verified.

**Residual risks** (ADR §3.4, accepted):

- A one-for-one membership swap made on the `wn-agent` control socket by a process running as the same user is invisible to admind: the count stays at 2 and no event reaches it.
- After an outage of the subscription, re-verification is by count only. A swap that happened while admind was not watching and left the count at 2 is not detected. A membership event already buffered when re-verification passes is handled immediately after, so there is a brief window in which the outbox could post before it is read.
- A process running as the same user can write to the hook socket and forge a reply event for the current session. The socket is mode 0600 in a 0700 directory, and events for any other session are dropped, but the same-user boundary is the limit.

**How replies are anchored.** admind pastes a prompt, then waits for the agent's `UserPromptSubmit` hook with the same text (whitespace-insensitive). Only then is the prompt confirmed, and only then is the reply threaded to the operator's message. A reply appears **top-level** instead when:

- the turn ended after `!interrupt`, or
- the turn was started at the terminal (not by admind), or
- the turn was in progress across an admind restart, or the prompt hook was lost, or
- a `Stop` arrives while a message is reserved but its `UserPromptSubmit` has not been processed (its text is posted unthreaded and the reservation, its busy period and its anchor are left alone), or
- the turn the `Stop` belonged to was replaced while the `Stop` was being handled (the reply is posted unthreaded when the `Stop` itself carries the text; a reply read from the transcript is discarded, see "Stop semantics" below), or
- the `Stop` comes from an earlier launch of the agent (see "Launches and stale events" below).

A `Stop` from a session retired by `!new` is suppressed entirely. If another prompt starts a turn while a message is anchored, the anchored message gets a "no reply" notice so that the new turn's reply cannot take its thread. Identical text typed at the terminal is indistinguishable from the paste and is threaded; only the operator can reach the private tmux server, and the reply answers the same words.

**The held queue.** The next message is pasted only on evidence that the agent is idle: a `Stop`, a `SessionStart`, `!interrupt` or `!new`. A timeout never dispatches. After `start_timeout_seconds` without the agent confirming the prompt, or `turn_notice_seconds` of a running turn, the operator is told once that later messages are held and pointed at `!tail` and `!interrupt`.

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
- **Deadlines.** The transcript fallback is bounded (10 seconds); on expiry nothing is posted and `reply-extraction-timeout` is recorded. At most one extraction thread runs: while an abandoned one is still reading, a new fallback is skipped the same way. Each event's whole processing is bounded (30 seconds): on expiry it is cancelled, `hook-deadline` is recorded, the hook is answered `err` and its slot is released, so one stuck event cannot hold every later hook.
- Residual: if Claude Code kills a hook after its frame was written but before admind processed it, the event is still processed, in arrival order, before any later hook. A frame from an older hook that still carries a `seq` field is accepted and the field ignored.
- *To be confirmed against real Claude Code in Task 10:* that hooks are synchronous, that `Stop` does not fire on an interrupt, and the `"timeout"` semantics (seconds, and that the hook is killed at that point).

**Stop semantics.** A `Stop` releases the busy state, deletes a reservation or threads a reply only when the current turn's anchor was set by a `UserPromptSubmit` that admind processed earlier in the same launch, or when no message is reserved (a turn begun at the terminal). A `Stop` that finds a reservation in flight but not anchored (dispatched with no prompt hook yet, or its prompt hook still to come) changes none of that: its own text is posted top-level, otherwise nothing is posted and `stale-stop-unrecoverable` is recorded. Such a `Stop` marks the reservation as stopped, and a `UserPromptSubmit` for a stopped reservation is ignored and recorded as `ignored-late-prompt`: it cannot anchor the reservation or start a busy period. The operator releases the held turn with `!interrupt`. When a `Stop` has no text of its own, the reply is read from the transcript in a worker thread; the turn identity is captured before the read, and if the turn changed meanwhile (`!interrupt`, a new dispatch, `!new`, a relaunch) the extracted text is discarded, nothing is posted and `stale-stop-unrecoverable` is recorded. Only a `Stop` that is still current after the read uses the fallback text.

**Audit records for stale or held events.**

- `stale-prompt` (an `agent` record): a `UserPromptSubmit` that was accepted before its turn was released (for example it waited behind `!interrupt`) was ignored. Nothing to do: the queue is not held by it. If messages stay held anyway, `!interrupt` releases the turn.
- `reply-extraction-timeout` (an `agent` record): a `Stop` without text could not be read from the transcript in time (or another read was still running). No reply is posted for that turn; read `!tail` for what the agent said. Repeats point at a stalled filesystem under the transcript directory.
- `hook-deadline` (a `hook` record): processing one hook event exceeded its deadline and was cancelled. The queue moves on; if the turn state looks wrong afterwards, `!interrupt` releases it and `!new` starts a fresh launch.
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
| `<state>/admind/admind.db` | 0600 | SQLite (WAL): accepted message IDs, outbox, relayed alerts, latch, agent session, turn state |
| `<state>/admind/audit.jsonl` | 0600 | append-only audit log, one JSON object per line (see below) |
| `<state>/admind/hook.sock` | 0600 | the agent's hooks reach admind here |
| `<state>/admind/claude-settings.json` | 0600 | the hook settings admind passes to the agent |
| `<state>/admind/marmot/` | 0700 | the private `wn-agent` home (location is `[admind.marmot] home`) |
| `<state>/admind/marmot/control.token` | 0600 | bearer token for the child's control socket; generated by admind, never printed or logged |
| `<state>/admind/marmot/ctl/` | 0700 | the child's control socket |
| `<state>/admind/marmot/wn-agent.log` | 0600 | the child's stdout and stderr (see below) |
| `<state>/alerts/` | writer's choice | alert files written by `wsd` (section 6); admind only reads it |

**The audit log** records timestamps, actions and outcomes, not content. Operator text is recorded after redaction and escaping, capped at 2000 characters. A message from anyone else is recorded as an 8-character sender prefix, the reason and a character count, never its text. Agent replies, terminal prompts and command results are recorded by length. The session ID of the admin agent's own accepted events (launch, dispatch, `SessionStart`, reply) is recorded; admind generates it as a local UUID and it is not a secret. What is omitted: any `detail` from the `wn-agent` peer (only an allowlisted error code), stranger and agent text bodies (lengths only), transcript paths, the group ID, and the identifiers of rejected input (the session ID and event name of a hook event that is ignored or malformed, and a malformed peer message ID). Exceptions are recorded by type, or by fixed admind wording passed through the redaction helper (for example a `wn-agent` restart failure records its type and message). Message IDs (event hashes) of accepted messages are recorded in full so entries can be correlated.

**The child log.** The private `wn-agent` writes its output to `wn-agent.log` in its home, not to the journal, because that output can contain invites, keys or the token. The file is truncated each time the child is spawned, so it holds only the current run. It is created 0600 (an older, looser file is tightened), opened without following symlinks, and must be a regular file. Read it on the host when `wn-agent` misbehaves; do not paste it into chat.

**Fresh install only.** There is no schema migration yet. `admind.db` is created if absent, and an existing database from an earlier build is not migrated (for example, an older `inbound` status constraint or text alert names are left as they were). Start from an empty `<state>/admind/` when upgrading across such a change.

## 8. Troubleshooting

- **`wn-agent` does not start, or "runtime root is already in use".** Another process holds the home (S4 finding). The private home can be opened by one process only: do not run `wn-agent` against `<state>/admind/marmot` by hand while admind runs, and stop admind before inspecting it. Check `wn-agent.log` for the cause. admind restarts the child with a doubling back-off of 1 to 60 seconds, reset once the child has stayed up for 60 seconds. (The background loops have their own, separate cap of 30 seconds; see below.)
- **The agent died, or never started.** admind checks the agent's pane every 5 seconds (`AGENT_POLL`). If the pane has died, or a launch has gone 120 seconds (`READY_TIMEOUT`) without a `SessionStart` hook, admind abandons whatever was in flight (the operator gets the usual "no reply" notice for that message), clears the busy state so the queue is released, and relaunches the agent (resuming its session when it had started before). The audit log has an `agent` record with `action` `died` or `ready-timeout`.
- **The agent keeps exiting (`AgentStuck`).** After three launches in a row without a `SessionStart` hook, admind stops relaunching and tells the operator ("did not start after 3 launches; use !tail, then !new"), either in reply to the messages then held or as a notice of its own. Messages sent meanwhile are refused with that reason. It does not retry on a timer. Use `!tail` to read the dead pane (it is kept), fix the cause (often the first-launch dialogs, section 3, or a missing `claude` on the unit's `PATH`), then `!new`; only `!new` resets the count (restarting admind does not, and the next check still reports the same stuck state).
- **admind stayed silent after a latch was cleared.** Run `admind rearm` once the group is right; the running daemon recovers by itself at its next group check (within `group_check_seconds`) and no restart is needed.
- **A background loop crashed.** The inbound reader, worker, hook, outbox, alert, group and agent-supervision loops each run under a supervisor. If one raises or returns, a `task` record (`name`, `action` `crashed` with the exception type only, or `returned`) is written and the loop restarts after a delay that doubles from 1 to 30 seconds and resets after a run of 60 seconds. This 30-second cap is the loops' own; the `wn-agent` child's cap is 60 seconds. Until the loop is back, what it does is paused (for example, no alerts are relayed while the alert loop is down).
- **No replies.** The reply comes from the `Stop` hook. Check `!tail` to see whether the agent is still working or waiting on a dialog, and look for `hook` records in `audit.jsonl` (`ignored-other-session`, `ignored-unknown-event`, or a `dropped` record with an error type). If the reply is missing but the turn finished, the hook may not have reached admind (or the event was held as `ignored-stale-launch` or `stale-stop-unrecoverable`, see "Audit records for stale or held events"); `!interrupt` releases the held queue and `!new` starts a fresh launch.
- **Replies arrive without a thread.** Expected in the cases listed under "How replies are anchored".
- **Later messages are not answered ("held").** The agent is not provably idle. Use `!tail`, then `!interrupt`; after an uncertain delivery, also resend the message.
- **A message was answered with "admind restarted before this message reached the admin agent".** admind stopped between accepting your message and handing it to the agent (or while a command was executing). It is never replayed, because running a prompt or `!restart` twice is worse than asking you to resend (D6). Resend if it is still needed. A turn that was in flight across the restart is closed with a notice, and its late reply, if any, appears top-level.
- **A message was dropped with no reply.** A peer message whose ID is not 64 lowercase hex characters (after lower-casing) is dropped before dispatch, with a `drop` record (`reason` `malformed message id`) and nothing else. This points at a broken peer or an integration bug. A repeated ID is dropped too (`replayed message id`).
- **admind is silent.** Check whether it is latched (`audit.jsonl`, `guard` records), then section 5. It is also silent until the operator's first message after `admind init`.
- **A send keeps failing.** A retryable send failure backs off (up to 60 seconds) before the next attempt, and holds back later outbox rows while it waits (they are sent in order). After 10 attempts the row is marked failed and admind moves on.
- **A command line fails with "invalid arguments".** `admind` never echoes a rejected argument (it could be an npub or a token); run `admind --help`.

## 9. Known limits

- **Codex is not supported as the admin adapter yet** (D7). A `codex` admin profile is a configuration error. It arrives with plan 4.
- **systemd only (v1).** `launchd` is phase 2.
- **The join signal is the operator's first message** (D5). Until it arrives, admind posts nothing.
- **Reply chunking has no message-count cap** (section 4); open operator decision.
- **Count-only re-verification** after an outage and **same-user forgery** on the control and hook sockets are accepted residual risks (section 5).
- **Missed membership events** while disconnected cannot be detected.
- **No schema migration** (section 7).
