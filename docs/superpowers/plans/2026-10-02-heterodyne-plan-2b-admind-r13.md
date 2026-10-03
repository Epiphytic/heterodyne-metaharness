# heterodyne-metaharness Plan 2b: `admind` revision 13 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bring the shipped `admind` (plan 2) to ADR 0001 revision 13 §8:

- several operators in one group;
- membership changes made only by admind, as journaled transitions;
- one redaction applied to everything admind posts and audits;
- long replies summarized, with a deterministic backstop when summarizing fails;
- `!details` and `!details full`;
- two delivery lanes;
- an untruncated audit.

**Architecture:** the daemon keeps its turn, hold and dispatch machinery unchanged. New behaviour lives in small modules (`redact`, `membership`, `ctl`, `summarize`, `backstop`) and in a few new SQLite tables. The daemon uses them at four points:

- ingress (`on_message`);
- the end of a turn (`on_stop`);
- the outbox (`outbox_pass`);
- a new host control socket, which is how `admind operators add|remove` and `admind rearm` reach the running daemon.

**Tech Stack:** unchanged from plan 2: Python 3.12+, asyncio, sqlite3, msgspec, tmux 3.x, `wn-agent` 0.10.x, Claude Code 2.1.x; pytest, hypothesis, ruff, pyright (strict on `src/`).

**Spec:** ADR 0001 revision 13 is design-repo commit `66b3aecb639e6ec56f108e2f55d483d4dedda485`, approved in bead `btq-5ky39`. Its design review is `reviewer=gpt-6.1-sol author=claude-opus-5-5 mode=cross-model`, records r18–r20. Section numbers (§) refer to it. Task 10 copies it byte-identical into `$HZ/docs/adr/0001-workstreams-v2.md`. Wire shapes: `$HZ/docs/spikes/S4-marmot.md`; this plan's Task 1 adds `$HZ/docs/spikes/S4b-membership.md`.

## Global Constraints

- **Variables:** `$HZ` is the product repo checkout (`heterodyne-metaharness`, a clone, never the live harness or any symlink to it). No step may write any of the following of the reference install into a committed file: an install path, npub, hex account or group ID, token, relay URL or unit name.
- **Live services are off-limits:** never stop, restart, reconfigure or open the home of a running `wn-agent-*`, `hermes-*` or `hermes-workstreams*` unit, or any Marmot home other than admind's own and Task 1's throwaway homes. Never restart the user manager. Never put live `hermes-*` or `wn-agent-*` units in `restart_units`.
- **Tests:**
  - Tests never touch the network, a real `wn-agent`, the real `systemctl`, the real `claude` binary, or `~/.claude`.
  - tmux tests use only private `-L hz-test-<uuid>` servers.
  - The summarizer in tests is a fake script under `tmp_path`.
- **Python** `>=3.12`, managed with `uv`.
  - Runtime dependencies: exactly `msgspec`.
  - Async tests use `asyncio.run` inside sync test functions; no pytest-asyncio.
- **`sys.platform`** appears only in `src/heterodyne/platform.py`.
- **No model names** in `src/`. Test fixtures use made-up names such as `m1`.
- **Install-agnostic repo:** `scripts/check_install_agnostic.py` stays clean. npub literals only under `tests/fixtures/`; tests build npubs with `hex_to_npub`.
- **Config keys must not trip the secret scanner.** The new keys are `summarizer`, `reply_verbatim_lines` and `reply_verbatim_chars`.
- **Never print tokens or npubs.**
  - Errors name the config key, not the value.
  - Operator names from `policy.toml` may be shown, always through `redact`: a name can itself be an npub or a token. The audit redacts every field centrally (B16).
- **Don't patch out the latch.** No test, flag or config may disable it.
- **Review rule** (§11.1): every code task ends with a review by a **different LLM than the implementer**.
  - If the implementer is Claude:
    `~/.codex/packages/app-server-daemon/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex exec -m gpt-6.1-sol -c model_reasoning_effort=medium -s read-only -o /tmp/review-<task>.md "$(cat /tmp/brief-<task>.md)" < /dev/null`, under `timeout 900`.
    - `< /dev/null` is mandatory: without it codex waits on stdin forever.
    - Never `pkill -f`; kill by PID.
  - If the implementer is Codex/GPT: `claude -p --model claude-opus-5-5 --permission-mode plan "<brief>" > /tmp/review-<task>.md`.
  - The brief names the diff range and the ADR sections and asks for `[BLOCKING]`/`[NON-BLOCKING]` findings. Fix or rebut every blocking finding.
  - Close evidence includes `Code-Review: reviewer=<model> author=<model> mode=cross-model range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`):
  - Task 1 is `kind:research`.
  - Tasks 2–10 are `kind:task`, each with `metadata.design_approval=btq-5ky39`, `metadata.adr_revision=66b3aecb639e6ec56f108e2f55d483d4dedda485` and a blocking dependency on `btq-5ky39`.
  - Tasks run in order 1 → 10. Task 5 and Task 7 also depend on Task 1, because its findings decide their constants.
  - The held operator acceptance bead `btq-llc3n` is re-scoped onto this plan and depends on Task 10.
  - Other existing beads are not touched (they wait for plan 9).
- **Branches:**
  - Integration branch `plan-2b-admind` in `$HZ`, from `main` at `68e9d4a`.
  - Each task's worktree (`btq … worktree`) branches from the integration branch's tip. The orchestrator fast-forwards `plan-2b-admind` to the task's reviewed head.
  - Nothing is pushed until Task 10. Merging is the operator's action, and every merge ask carries the full PR URL.
- **Gate before each commit:** `uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`, all clean.

## ADR r13 deviations for operator sign-off

Each row is a place this plan does something other than ADR r13 §8 says. Your OK makes it part of the design; for a no, the last column is what changes. Details are in the decision named in the first column.

| # | ADR says | Plan does | Why | If no |
|---|---|---|---|---|
| 1 (B1) | Control characters are escaped. | Newline and tab stay as they are; all other control characters become `\xNN`. | A reply with no line breaks can't be read. | Escape them too; replies become one long line. |
| 2 (B1) | 64-hex values become markers. | Any run of 64 or more hex digits becomes one marker. | A key can hide inside a longer run. | Mask only exact 64-digit values; longer runs show. |
| 3 (B1) | No such case. | Text that still changes after 10 redaction passes is replaced whole by `<redacted text>`. | Fail closed; can't happen with sane input. | Send the 10th pass as it is. |
| 4 (B2) | Every `operators` entry with an npub is an operator. | An empty name, a name over 128 characters or with control characters, two entries with one key, or no operator at all is a config error. | Names cross the control socket; one key with two names is ambiguous. | Accept them; show names escaped and use the first entry for a key. |
| 5 (Task 5) | No limit on `operators remove`. | Removing the last operator is refused. | A group with only admind can't be commanded. | Allow it; an operator is then added back with `admind operators add` on the host. |
| 6 (B6) | `rearm` is a host command. | `rearm` asks the running daemon; it fails if the daemon is down. | Only the daemon can read the member count (one process per wn-agent home). | `rearm` stops the daemon, reads the count itself, starts it again. |
| 7 (B22) | `rearm` trusts the current count and clears any pending change. | `rearm` refuses if events aren't being read, if the group changed while it read the count, or if the count can't be matched to known operators. | Else a rearm can clear a latch set while it ran, or trust members it can't name. | Trust the count always; the operator set may then be wrong until the next change. |
| 8 (B21) | Policy operators with an npub may command. | An operator must be in `policy.toml` **and** confirmed in the group (stored). A plan-2 upgrade with several policy operators latches until a `rearm`. | A restart must not authorise someone never added. | Authorise every policy operator; only the count is checked. |
| 9 (B8) | Summary is at most about 8 lines. | The summarizer is asked for 8; up to 10 lines are accepted (12 shown with the footer). More than 10 lines, 2,000 characters or 8,000 bytes goes to the backstop. | Slack for a model that adds a line; the limits stop a runaway summary. | Accept only 8 lines; more summaries fall to the backstop. |
| 10 (B8) | The summarizer profile runs headless, read-only, without tools. | The profile's `args` are ignored. | Extra args could turn tools back on. | Append `args`; the no-tools guarantee then depends on them. |
| 11 (deferral) | Any adapter can be configured for the admin agent. (For the summarizer the ADR names only "the configured profile".) | The admin agent stays Claude Code only, as in plan 2; the plan also limits the summarizer to Claude Code. | Only Claude's interactive and headless no-tools launches are verified (plan 2, Task 1). | A later task adds Codex once its launches are verified. |
| 12 (B10) | A batch of 50 lines or fewer is sent whole. | A batch over 60,000 characters has each line shortened (`…(+N chars)`), and if needed its end cut, to fit one message. | One message can't exceed the transport limit (Task 1 checks it). | Send it as several messages (no longer "one message"). |
| 13 (B10) | Not covered. | A batch whose delivery fails reopens with a new 60-second window and is sent again under a new key, until delivered; replies affected meanwhile join it. | Its replies must not be stranded. | Give up after the retries and only audit it. |
| 14 (B10) | A batch closes 60 seconds after its first reply. | No reply joins after 60 s; the batch is posted by a 1-second poll, so up to about 1 s late. | A poll survives restarts simply. | A timer per batch (more state across restarts). |
| 15 (B9) | The backstop drops nothing `!details` can't return. | A reply that can't be read from the transcript (timeout, unknown span, or a file that is shorter than the span, ends inside a record or holds a corrupt record) goes to the backstop as a fixed notice; its text stays only in the transcript on the host. A file replaced by another valid transcript of that session is not detected. | It is better than silence or a partial reply. | Hold dispatch until the reply can be read. |
| 16 (B13) | `!details full` has no cap. | One transcript record over 64 MiB is named with its size; images are shown as type, size and digest; structured results as sorted JSON, with keys that redact alike numbered `#2`, `#3`. | Memory bound; a chat can't show images. | Stream-parse huge records; send images as base64 text. |
| 17 (B19, B20) | `!details full` sends each turn's tool calls. | It says "not available" when the turn's span is unknown, the transcript is busy or slow (30 s), or a record is corrupt; there is no automatic retry. | It must never show another turn's tool calls. | Queue a retry and post it when it succeeds. |
| 18 (B16) | Audit values are redacted to markers. | Message IDs in ID fields become `id:` plus 12 hex digits of their SHA-256. | Records can still be matched up. | Plain markers; records can't be matched. |
| 19 (B17) | Not covered. | After the upgrade, re-chunked pending replies may repeat text already received; a continuation that can't be checked is replaced whole. | Accuracy over deduplication; fail closed. | Drop plan-2 pending replies instead. |
| 20 (deferral) | If wn-agent can't add or remove members, operators change only by `admind init` of a new group. | Task 1 stops and escalates if so; no task builds the init-only path. | Task 1 is expected to confirm add and remove work. | A plan revision adds the init-only path if Task 1 finds it needed. |
| 21 (deferral) | The summary quotes every question and error verbatim. | The prompt asks for it; tests check the prompt wording, not the summaries. The Task 1 spike gives some real evidence. | A model's output can't be guaranteed by a unit test. | Add a check: a summary that lacks a reply line ending in `?` or starting `Error` goes to the backstop. |
| 22 (plan 2, D3) | Operator text goes byte-for-byte into the admin session. | A message holding control characters is refused with a fixed reply (kept from plan 2). | They can break out of bracketed paste and inject keystrokes. | Escape them visibly and send the escaped text. |

### Explicit deferrals (Codex r5 finding 6)

- **Init-only fallback (row 20):** if Task 1 shows `wn-agent` can't add or remove members, the plan stops and escalates as it says; the ADR's alternative (operators change only by a new `admind init`) needs a plan revision, not an improvised task.
- **Admin and summarizer adapters (row 11):** `settings.ADMIN_ADAPTERS` stays `("claude-code",)`, and Task 7's `_profile` keeps that restriction for the summarizer. Supporting another adapter is a later task.
- **Summary content (row 21):** the question-and-error rule is enforced by the prompt only; no automated test proves a real summary keeps them.

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Decision | Why | ADR |
|---|---|---|---|
| B1 | A new `admind/redact.py` replaces each matched secret, npub or 64-or-more-hex run with a marker, and escapes every C0, DEL and C1 character except newline and tab as `\xNN`. One pass runs in a fixed order: PEM blocks, then the other secrets, then npubs, then the control escapes, and the hex runs **last** (so hex digits an escape adds next to a run are caught in the same pass). `redact` repeats the pass until the text stops changing (at most 10 passes; text still changing after that becomes `<redacted text>` whole), so it is idempotent by construction: a marker is a boundary, and a token glued to a hex run is only matched once the run is a marker (Codex r2 finding 1). Every post goes through it (in `Admind.post`, on the whole text before chunking, and again at delivery), as do the audit (B16), the summarizer's input and `!details`. | `config.secret_scan.show` replaces the **whole** string when anything in it matches. An operator message that holds a commit hash would vanish from the audit, and a reply would become one marker. Newline and tab are layout, not terminal control, and a reply without newlines is unreadable. Escaping after the hex pass is not idempotent: `"\x1b" + "f" * 63` would gain two hex digits on the second pass. | §8 Redaction, Audit |
| B2 | `AdmindSettings.operators` is a tuple of `Operator(name, npub, hex)`. Operators without `marmot_npub` are skipped; none left, two with one key, or a name the control socket can't carry (over 128 characters, or a control character) is a `ConfigError`. The daemon re-reads `policy.toml` through an injected `load_operators()` before each add, remove or rearm; the policy read before the change is the one used to authorise after it (B7, B21), so a policy file that can't be read afterwards cannot leave a half-applied change. | "Every entry in `operators` with a Marmot npub is an admind operator"; a temporary operator is added to `policy.toml` while admind runs. | §8 Operators |
| B3 | The trusted member count is kv `expected_members`. On an initialised install that lacks it (a plan-2 group, which always had exactly two members) it is set to `2` at startup and audited as `migrated`. | Upgrading in place must not latch or trust a wrong count. | §8 Group membership |
| B4 | `PeerError(ControlError)` is raised only for a wn-agent `error` frame: "wn-agent reported failure". Any other `ControlError` (timeout, closed socket, protocol error) is "unknown". | The commit/abort rule needs exactly that distinction. | §8 transition step 4 |
| B5 | `membership.settle(reported, count, pending)` is a pure function: `ok` with `to` commits, `failed` with `from` aborts, everything else latches. The daemon also latches, whatever `settle` says, if during the transition a latch was set, the subscription stopped observing, or the subscription generation changed (a replaced subscription may have dropped an event). | Directly testable; matches steps 3–4. Membership events are seen only by a live subscription, so a transition that lost it can't know nothing else happened. | §8 transition |
| B6 | Host commands reach the daemon over `state_dir/ctl.sock`: 0600, in the 0700 state directory, one JSON request per connection. `admind rearm` now needs a running daemon, because only the daemon can read the member count (S4: a wn-agent home can't be opened by a second process). The client waits up to 300 s (a transition first waits for the operator message in hand). A handler that raises answers `failed` with fixed wording; the CLI exits 1. | ADR: "The command asks the running daemon". `rearm` "takes the current member count as trusted". | §8 Group membership |
| B7 | A transition runs under `transition_lock` (one add, remove or rearm at a time). It refuses unless the subscription is observing and the group verified. It then takes `work_lock`, which `worker_loop` holds for each operator message, so the message in hand finishes and no new one starts; sets the in-memory flag `changing`, which blocks dispatch and posting through `authorised()`, `may_post()` and `_flush`; and drains a paste in progress (`dispatch_lock`) and a send in progress (`send_lock`, which `outbox_pass` holds across each send) before it reads the count and journals. Every await is followed by a recheck of the latch, observation and subscription generation. `check_group` takes no lock: a check begun during a transition, or overtaken by one (`membership_epoch`), returns "deferred" and changes nothing, so the subscription reader never waits on a transition. An exception after journaling latches; `changing` is cleared only once the change is settled or latched, otherwise it stays set until a restart latches on the pending record (B15) or `rearm`. | Step 1: "serializes it with every guard check and holds dispatch and outbound posting". A flag alone blocks new work but does not drain work already running. Holding `dispatch_lock` for the whole transition would stall the hooks, which also take it. | §8 transition step 1 |
| B8 | Summarizer launch: `claude -p --model M --tools "" --setting-sources project --settings '{"disableAllHooks": true}' --strict-mcp-config --no-session-persistence --output-format text`, prompt on stdin. It runs in an empty private directory `state_dir/summarizer` and the profile's `args` are **not** appended. Admind appends the footer. Output that is empty, or longer than 10 lines or 2,000 characters, is a failure; stdout is read incrementally and the process is killed once it passes 8,000 bytes. | Headless, read-only, without tools, and unaffected by user-level hooks or MCP servers. `--bare` would need an API key. The ADR's "about 8 lines" gets two lines of slack; the limits stop a runaway "summary" from replacing the backstop, and the byte bound stops one from filling memory. | §8 Replies |
| B9 | Every stage of a reply is durable. `on_stop` writes the turn record, its verbatim posts and the turn-state change in one transaction; if that fails, a second transaction records the turn straight into the backstop with the same state change, and if that fails too the error reaches `hook_loop`, which holds dispatch. An extraction that times out or cannot read the transcript (no span, a bad path, a read error) records a fixed-text turn (`EXTRACT_FAILED`) straight into the backstop; only a readable turn with no assistant text is `NO_REPLY`. Summary jobs are rows with `turns.status = 'summarizing'`; `summary_loop` polls them from the database (woken by an event, at least every 5 s), so a failure at any step leaves the row for the next pass, without a restart. A summary or verbatim post the outbox gives up on sends its turn to the backstop in the same transaction that marks it failed. | A reply must reach the operator whatever fails: summary, backstop, or at worst dispatch is held. | §8 Replies, Backstop |
| B10 | A backstop batch is **one** unthreaded message, rendered by the ADR's rules (a fixed title line, not counted in the 50; at most 50 collapsed lines whole, else the first 10 and last 40) and sent as rendered: `chunk_chars` does not apply to it. Only a batch over `backstop.BATCH_MAX_CHARS` (60,000, the largest message Task 1 confirms is delivered whole) is shortened, each line after the title to an equal share with `…(+N chars)`, then the end with `… (cut at the message size limit)`. Replies join in the order they reach the backstop (`turns.batch_seq`, written in the transaction that batches them), not the order their turns began. A reply joins only a batch whose 60-second window is still running at that moment; after that it opens the next batch, even if `batch_loop` hasn't posted the old one yet. A batch whose delivery is given up on opens again in the same transaction and is posted after a new window under a new key (`batch:<id>.<attempt>`), until it is delivered. `!details` on the batch returns every reply whole. **Deviation, flagged:** the over-60,000 cut (50 lines averaging over 1,000 characters) contradicts "sent whole"; one message can't be bigger than the transport allows. | ADR: "Each batch is one unthreaded message" (Codex r2 finding 8); "closes 60 seconds later. Replies affected meanwhile join it in order" (Codex r5 findings 3, 4). A batch whose delivery failed must not strand its replies (Codex r2 finding 7). | §8 Backstop |
| B11 | New tables: `prompts` (who sent each operator message, when, first words), `turns` (one per reply: redacted text, origin, transcript path and start and end offsets), `batches`, and `posts` (outbox key to turn or batch). | `!details` must work after a restart. | §8 `!details` |
| B12 | `!details` uses the command's `reply_to`: the **sent** outbox row with that `message_id`, then its `posts` row. Without a reply target it uses the latest summary or batch that was **delivered** (`status = 'sent'`); pending and failed rows never count. Verbatim replies get records too, so `!details full` works on them. | ADR text; lookups are exact, and "latest" is what the operator actually saw. | §8 `!details` |
| B13 | `!details full` reads the transcript bytes between the turn's `UserPromptSubmit` and its `Stop` (B19), 1 MiB at a time. Metadata records (such as `queue-operation`, or Claude Code's own `isMeta` user records) are ignored; the turn begins at its first content (an assistant record or a tool result), so a real prompt before that is the turn's own and one after it ends the turn. A complete record in the span that is not a JSON object fails the read like a truncated one (Codex r5 finding 2). A user record that carries a tool result is never a prompt, even with text beside it. It renders `tool_use` and `tool_result` blocks whole (non-text results and tool inputs as JSON with sorted keys, every string in them redacted before serializing so an escape such as `\u0001` can't hide a token from the scanner; keys that redact to the same marker are kept apart as `<marker> #2`, `#3`… (`redact.unique_key`, also used by the audit), so no entry is lost; an image as its media type, size and a SHA-256 prefix, since a text chat can't show it); thinking blocks are never read out. A record over the size guard never begins the turn. The same turn reader (`hook.turn_records`) gives a Stop without its own text its reply: every assistant text of the span, in order; a read failure, a missing span, or a transcript that no longer matches the span (shorter than it, or ending inside a record) is `EXTRACT_FAILED` (B9), never `NO_REPLY` or a partial reply. Nothing is capped or shortened, so redaction always sees whole values. The one exception is a single JSONL record over 64 MiB (`MAX_RECORD`), which is named with its size rather than parsed. | It must show the tool calls of that turn, not of a later one (Codex r2 finding 6), and §8 says `!details` has no cap (finding 5). **Deviation, flagged:** the 64 MiB record guard bounds memory; showing such a record would need a streaming JSON parser. | §8 `!details full` |
| B14 | `outbox.lane` (1 or 2). `Store.next_pending()` returns the lowest lane first, then by sequence, and is re-read after every send. A retry's backoff is a timer for that one row (`Admind._backoff`, keyed by row), not a sleep in the outbox loop: a lane-1 message queued during a lane-2 row's backoff is sent at once (Codex r5 finding 5). A lane-1 row in backoff still holds the lane-1 rows behind it, which keeps lane 1 in order. | "Only when the first lane is empty", and urgent messages are never stuck. | §8 Delivery lanes |
| B15 | A pending membership record found at startup latches before `recover()` or anything else runs. | Step 5. | §8 transition step 5 |
| B16 | `Audit.write` redacts every string field, recursively through dicts, lists and tuples; any other value is redacted as its `str()`. In the identifier fields `message_id`, `reply_to`, `key`, `target` and `anchor`, a 64-hex run is first replaced by `id:` and 12 hex digits of its SHA-256 (`audit.ref_id`), so records still correlate without holding the identifier. Dict keys that redact alike are numbered (`redact.unique_key`), so no value is lost. A message from an authenticated operator that is dropped (latched, replayed, or a malformed ID) is still audited whole under the operator's name; a stranger's text never is (Codex r6 finding 1). | Redacting field by field at each call site misses fields (Codex r1 finding 4). One place cannot be bypassed. | §8 Audit, Redaction |
| B17 | Upgrading a plan-2 database: when `outbox.lane` is missing, the column is added and the kv marker `outbox_needs_redaction` set in one transaction. At startup, before `recover()`, `Store.redact_pending_outbox` joins each reply's pending chunks in order, redacts the whole, re-chunks it under new keys (`<prefix>:r<i>`), and clears the marker, all in one transaction. A value that straddles the boundary with the already-sent chunks is hidden to its end (`<redacted fragment>`); the check runs on **all** the reply's sent chunks joined in order, since a value can span several of them. If any earlier chunk of the reply is missing or not sent, the pending text is replaced whole by `<redacted continuation of a partly sent reply>` (fail closed). `outbox_pass` also redacts every row at delivery. | A plan-2 outbox can hold unredacted, chunked replies. The re-keyed chunks may repeat text the operator already received; accuracy over deduplication. | §8 Redaction |
| B18 | Failures fail closed. A membership transition that raises after journaling latches (with a fixed reason) before the exception propagates; if even the latch can't be written, `changing` stays set. The control server answers a raising handler with fixed wording. | Step 5 covers a crash; the same must hold for a daemon that keeps running. | §8 transition step 5 |
| B19 | A turn's transcript span: `UserPromptSubmit` measures the transcript size before taking the turn lock and stores it as kv `turn_start = "<busy>:<offset>"` in the transaction that starts the busy period. `Stop` measures the size on entry, before the lock. The turn record keeps both only if the Stop is `current` and `turn_start` belongs to the busy period the Stop was captured with; otherwise both are NULL and `!details full` says the tool calls are not available. | A stale Stop, a lost prompt hook or a turn that changed during extraction can't be tied to its bytes; saying so is better than showing another turn's tool calls. | §8 `!details full` |
| B20 | One transcript reader at a time: reply extraction, offset measurement and `!details full` share one reader slot (`Admind.bounded_read`). A read that finds the slot busy or runs past its deadline returns None; the thread runs to completion in the background. Offsets wait 2 s, extraction 10 s, `!details full` 30 s. | A stalled filesystem must not pile up threads (plan 2's extraction rule, extended to the new readers). | §8 `!details full` |
| B21 | Who may command admind is durable: kv `group_operators` holds the sorted hex keys admind has **confirmed** are in the group. `policy.toml` makes a key eligible; `Admind.operators` is the policy operators whose key is confirmed, and no others. `init` writes all of them; a committed transition writes the change in the same transaction as `expected_members`; `rearm` reconciles it (B22); startup loads it. Upgrading a plan-2 database (no `group_operators`): with exactly one policy operator, that one is confirmed (plan 2 allowed only one); with several, admind latches until a rearm. | A restart must not authorise a policy operator who was never added, nor forget one who was; a lost reply must not leave the map behind the group (Codex r2 finding 4). | §8 Operators, `rearm` |
| B22 | `rearm` refuses, changing nothing, unless the subscription's events are being read (`reading`: `confirm_observing` has returned; an acknowledged subscription whose check is still running holds its events unread); it records `sub_gen`, a count of membership events and the latch before reading the count, and refuses if any changed meanwhile. It reconciles the confirmed operators with the trusted count: a pending change whose `to` count matches took effect, one whose `from` count matches did not; the confirmed keys stand if `count == 1 + len(keys)`; else, if `count == 1 +` the number of policy operators, all of them become confirmed (the operator's `rearm` asserts it); else it refuses and explains. **Deviation, flagged:** §8 says rearm takes the count as trusted without conditions; these refusals are added so a rearm can't clear a latch an event set while it ran (Codex r2 finding 3) or authorise operators it can't account for. | A rearm that clears a fresh latch, or trusts a count without knowing who the members are, defeats the latch. | §8 `rearm` |

## File map

| File | Change |
|---|---|
| `src/heterodyne/admind/redact.py` | **new**: `redact(text) -> str`, `redact_continuation(previous, text) -> str` |
| `src/heterodyne/admind/audit.py` | every field redacted centrally; `ref_id`, `ID_FIELDS` (B16) |
| `src/heterodyne/admind/membership.py` | **new**: `Pending`, `settle` |
| `src/heterodyne/admind/ctl.py` | **new**: control socket server and client |
| `src/heterodyne/admind/summarize.py` | **new**: `needs_summary`, `summarize`, `SummaryFailed` |
| `src/heterodyne/admind/backstop.py` | **new**: `Entry`, `origin`, `first_words`, `collapse`, `render`, `fit` |
| `src/heterodyne/admind/settings.py` | `Operator`, `operators`, summarizer and verbatim keys |
| `src/heterodyne/admind/guard.py` | several operators, an expected count |
| `src/heterodyne/admind/store.py` | lanes, the upgrade's `redact_pending_outbox`, `prompts`, `turns`, `batches`, `posts` |
| `src/heterodyne/admind/commands.py` | `!details [full]` |
| `src/heterodyne/admind/hook.py` | `transcript_size`, `turn_records` and the span-based `reply_text` (Task 8); `turn_tool_calls` (Task 9) |
| `src/heterodyne/admind/daemon.py` | wiring (Tasks 2–9) |
| `src/heterodyne/admind/cli.py` | `init` for all operators, `operators add|remove`, `rearm` via the daemon |
| `src/heterodyne/agents/claude_code.py` | `headless_argv` |
| `src/heterodyne/marmot/control.py` | `PeerError`, `group_member_add`, `group_member_remove` |
| `src/heterodyne/defaults/defaults.toml` | `reply_verbatim_lines = 8`, `reply_verbatim_chars = 800` |
| `tests/fakes/fake_wn_agent.py` | membership requests and modes (including `gated`), `drop_subscriptions()` |
| `tests/fakes/settings.py` | operators tuple, new keys |
| `tests/test_admind_r13_*.py` | **new** test files, one per task |
| `docs/admind.md`, `docs/configuration.md`, `docs/security-model.md`, `docs/adr/…`, `docs/reviews/…`, `docs/spikes/S4b-membership.md` | Tasks 1 and 10 |

---

### Task 1: Verify membership operations and the summarizer launch (`kind:research`)

**Files:**
- Create: `docs/spikes/S4b-membership.md`

This task checks real behaviour, using throwaway identities only. It writes no product code. Its findings set the constants in Task 5 (error codes) and Task 7 (argv). **If a finding contradicts the ADR, stop:** release the bead with the finding as the reason, and the orchestrator escalates to the operator. An example is a `member_count` that doesn't change until the invitee accepts.

- [ ] **Step 1: Prepare three throwaway homes.** Run each in its own `mktemp -d /tmp/s4b-XXXX`, never under `~/.local/share` or a live home.

```bash
S4B=$(mktemp -d /tmp/s4b-XXXX); chmod 700 "$S4B"
for who in admin opA opB; do
  mkdir -m 700 -p "$S4B/$who/ctl"; head -c 32 /dev/urandom | xxd -p -c 64 > "$S4B/$who/token"; chmod 600 "$S4B/$who/token"
done
# RELAYS: the same relay list as [admind.marmot] relays in the host config (read it; do not commit it)
for who in admin opA opB; do
  wn-agent --home "$S4B/$who" --socket "$S4B/$who/ctl/wn.sock" --auth-token-file "$S4B/$who/token" \
    $(for r in $RELAYS; do printf -- '--relay %s ' "$r"; done) > "$S4B/$who/log" 2>&1 &
  echo $! > "$S4B/$who/pid"
done
for who in admin opA opB; do
  wn-agent bootstrap --home "$S4B/$who" --socket "$S4B/$who/ctl/wn.sock" --auth-token-file "$S4B/$who/token" \
    --label "s4b-$who" --invite-policy deny --no-quic --json --wait-for-socket 30 \
    $(for r in $RELAYS; do printf -- '--relay %s ' "$r"; done) > "$S4B/$who/bootstrap.json"
done
```

Never print `bootstrap.json`, a token or an npub. Read the account hex with `jq -r` into shell variables only.

- [ ] **Step 2: Write a small client.** Put it at `$S4B/ctl.py`, and use it for every request below. It prints only `type`, `code`, `retryable`, `member_count` and `pending_welcome_count`.

```python
import asyncio, json, sys
from pathlib import Path
from heterodyne.marmot.control import ControlClient, ControlError

async def main() -> None:
    home, payload = Path(sys.argv[1]), json.loads(sys.argv[2])
    client = ControlClient(home / "ctl" / "wn.sock", (home / "token").read_text().strip())
    try:
        frame = await client.call(payload, payload.pop("_expect"), dict)
        print({k: frame.get(k) for k in ("type", "member_count", "pending_welcome_count")})
    except ControlError as exc:
        print({"error": exc.code, "retryable": exc.retryable})

asyncio.run(main())
```

Run it with `uv run python "$S4B/ctl.py" HOME 'JSON'` from `$HZ`. `call(..., dict)` decodes the whole frame. `KNOWN_ERROR_CODES` turns unknown codes into `unrecognised`, so for this step only, also run with the code allowlist bypassed: temporarily print `exc.detail`'s **code-like first token**, never its free text.

- [ ] **Step 3: Record the facts below in `docs/spikes/S4b-membership.md`, each with PASS/FAIL and the observed values.**
  1. `group_create` by admin with `members=[opA]`, then `group_info.member_count`. Expected: 2.
  2. `group_member_add` of opB. Record the response type, the `member_count` immediately after the response (before opB accepts), and the count after opB accepts its welcome. **The ADR's commit rule assumes the count is `from + 1` right after a successful response.**
  3. `group_member_remove` of opB: response, then count.
  4. `group_member_add` of opA, who is already a member: error code and `retryable`, then count (expected unchanged).
  5. `group_member_remove` of opB again (not a member): error code and count.
  6. With admin subscribed through `subscribe_inbound` during 2–3: confirm **no** `group_state_changed` reaches admin's own subscription. With opA subscribed: record the `change` values opA sees.
  7. Kill the admin child (by PID) between sending a `group_member_add` and reading its reply, and record what the client raises. This is the "lost reply" path.
  8. Admin sends opA one message of exactly 60,000 characters (`backstop.BATCH_MAX_CHARS`: lines of `x` with a line number, newline-separated). Record whether opA receives it whole (compare length and a SHA-256 of the text). If it doesn't, halve the size until one arrives whole, record the largest that did, and set `BATCH_MAX_CHARS` in Task 7 to it.

- [ ] **Step 4: Check the summarizer launch shape with the real CLI.** This is the only place the real `claude` runs in this plan. Use an empty directory and send nothing sensitive.

```bash
D=$(mktemp -d /tmp/s4b-sum-XXXX)
printf 'Summarize in one line: the build passed and 3 tests were skipped.\n' | (cd "$D" && timeout 90 claude -p \
  --model "$(python3 -c 'import tomllib,os;print(tomllib.load(open(os.path.expanduser("~/.config/heterodyne/config.toml"),"rb"))["profiles"]["claude-opus"]["model"])' 2>/dev/null || echo sonnet)" \
  --tools "" --setting-sources project --settings '{"disableAllHooks": true}' --strict-mcp-config \
  --no-session-persistence --output-format text); echo "exit=$?"
ls -A "$D"
```

Record:
- the exit status, and that one line of text came back;
- that `$D` is still empty;
- that no new entry appeared in the queue or brain hook state. Compare `sqlite3 ~/.hermes/workstreams/harness.sqlite3 'select count(*) from brain_offers'` before and after; if the database is absent, record that.

Then check what the summarizer keeps, and that it really has no tools. Use the same flags, in the same empty directory, with Task 7's prompt wording (`summarize.PROMPT`, pasted by hand) around this reply:

```text
Restarted the gateway. It came back after 4 s.
Error: wsd.service failed to start: exit-code 1
Should I roll back to yesterday's build, or keep debugging?
(plus 30 lines of routine log output)
```

Record that the output quotes the error line and the question verbatim, in at most 10 lines. Then run the first command once more with `--output-format stream-json --verbose` and a prompt that asks it to list the files in the directory. Record that the stream holds no `tool_use` event, and that `$D` is still empty.

If `--tools ""` is rejected by this CLI version, record the error and the working alternative (`--disallowedTools` listing every tool, or `--allowedTools` with nothing). Task 7 uses whatever is recorded here.

- [ ] **Step 5: Clean up.** Have admin remove opA. Kill each child by the PID in `$S4B/*/pid`, then `rm -rf "$S4B" "$D"`. Record cleanup in the doc.

- [ ] **Step 6: Commit the doc and close the bead.**

```bash
git add docs/spikes/S4b-membership.md
git commit -m "docs(spikes): S4b membership operations and summarizer launch shape"
```

The close evidence lists every check's PASS/FAIL and states whether the ADR's assumptions hold.

---

### Task 2: One redaction, and an untruncated, redacted audit

**Files:**
- Create: `src/heterodyne/admind/redact.py`
- Modify: `src/heterodyne/admind/audit.py`, `src/heterodyne/admind/daemon.py` (module docstring, `AUDIT_TEXT_CHARS`, `own_text`, `post`, `reply`, `on_stop`, `relay_alert`, the `show(...)` calls in audit records)
- Test: `tests/test_admind_r13_redact.py`

**Interfaces:**
- Produces:
  - `heterodyne.admind.redact.redact(text: str) -> str`. Later tasks call it for every post, the summarizer input and `!details`;
  - `heterodyne.admind.redact.redact_continuation(previous: str, text: str) -> str` (used by Task 4's upgrade);
  - `heterodyne.admind.redact.unique_key(key: str, taken: Container[str]) -> str` (used by `audit.clean` and Task 9's `_redacted`);
  - `heterodyne.admind.audit.ref_id(value: str) -> str` and `audit.ID_FIELDS`;
  - `Audit.write` redacts every field (B16).

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_redact.py
import asyncio
import json
import re
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from heterodyne.admind.audit import Audit, ref_id
from heterodyne.admind.redact import redact, redact_continuation, unique_key
from heterodyne.marmot.nip19 import hex_to_npub

HEX = "ab" * 32
NPUB = hex_to_npub("c3" * 32)
TOKEN = "ghp_" + "A" * 30
# Characters that interact: hex digits, controls whose escapes add hex digits, and pattern prefixes.
TRICKY = st.text(alphabet="0123456789abcdefABCDEF\x00\x01\x1b\x7f\x9b\n\t xgh_p-npub1skAKIAeyJ.",
                 max_size=400)


def test_replaces_only_the_match() -> None:
    assert redact(f"commit {HEX} is bad") == "commit <redacted hex key> is bad"
    assert redact(f"ask {NPUB}, please") == "ask <redacted npub>, please"
    assert redact(f"token {TOKEN} end") == "token <redacted GitHub token> end"


def test_longer_hex_runs_are_hidden_whole() -> None:
    assert redact("x" + "f" * 65 + "y") == "x<redacted hex key>y"


def test_pem_block_is_hidden_to_its_end() -> None:
    pem = "-----BEGIN " + "PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"   # split: gitleaks
    assert redact(f"a\n{pem}\nb") == "a\n<redacted PEM private key>\nb"


def test_layout_kept_controls_escaped() -> None:
    assert redact("a\tb\nc\rd\x1b[2Je\x9b") == "a\tb\nc\\x0dd\\x1b[2Je\\x9b"


def test_an_escape_next_to_hex_is_redacted_in_one_pass() -> None:
    # Codex r1 finding 5: escaping after the hex pass turned 63 hex digits into a 65-digit run.
    once = redact("\x1b" + "f" * 63)
    assert once == "\\x<redacted hex key>"
    assert redact(once) == once


def test_a_marker_that_makes_a_boundary_is_redacted_again() -> None:
    # Codex r2 finding 1: a hex run glued to a token hides it from the token pattern until the run
    # becomes a marker; the fixpoint loop catches it in the same call.
    once = redact("f" * 64 + TOKEN)
    assert once == "<redacted hex key><redacted GitHub token>"
    assert redact(once) == once


def test_a_control_before_a_secret_does_not_hide_it() -> None:
    assert redact("a\x01" + TOKEN) == "a\\x01<redacted GitHub token>"


@given(st.one_of(st.text(), TRICKY))
def test_idempotent(text: str) -> None:
    once = redact(text)
    assert redact(once) == once


@given(st.one_of(st.text(alphabet="0123456789abcdefABCDEF xyz\n", max_size=300), TRICKY))
def test_no_hex_key_survives(text: str) -> None:
    assert re.search(r"[0-9A-Fa-f]{64}", redact(text)) is None


@given(st.text())
def test_no_control_survives(text: str) -> None:
    out = redact(text)
    assert all(c in "\n\t" or not (ord(c) < 32 or 127 <= ord(c) <= 159) for c in out)


def test_continuation_hides_the_rest_of_a_split_value() -> None:
    assert redact_continuation("key ghp_AAAAAAAAAA", "A" * 20 + " rest") == "<redacted fragment> rest"
    pem_head = "-----BEG"
    pem_rest = "IN " + "PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----\nafter"   # split: gitleaks
    assert redact_continuation("see " + pem_head, pem_rest) == "<redacted fragment>\nafter"
    assert redact_continuation("plain words. ", "more words") == "more words"
    # A value glued to a hex run that straddles the boundary is found the way redact finds it.
    assert redact_continuation("f" * 64 + "ghp_AAAA", "A" * 26 + " rest") == "<redacted fragment> rest"


def records(path: Path) -> list[dict[str, object]]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_audit_redacts_every_field(tmp_path: Path) -> None:
    audit = Audit(tmp_path / "audit.jsonl")
    audit.write("probe", message_id=HEX, key=f"cmd:{HEX}:0", text=f"hi {NPUB}\x1b",
                nested={"list": [TOKEN, 3, None], "deep": (HEX,)}, other=Path(f"/x/{HEX}"))
    raw = (tmp_path / "audit.jsonl").read_text()
    assert HEX not in raw and NPUB not in raw and TOKEN not in raw and "\x1b" not in raw
    r = records(tmp_path / "audit.jsonl")[0]
    assert r["message_id"] == ref_id(HEX) and r["key"] == f"cmd:{ref_id(HEX)}:0"
    assert r["text"] == "hi <redacted npub>\\x1b"
    assert r["nested"] == {"list": ["<redacted GitHub token>", 3, None], "deep": ["<redacted hex key>"]}
    assert r["other"] == "/x/<redacted hex key>"
    assert ref_id(HEX) == ref_id(HEX.upper()) and re.fullmatch(r"id:[0-9a-f]{12}", ref_id(HEX))


def test_audit_keeps_keys_that_redact_alike(tmp_path: Path) -> None:
    audit = Audit(tmp_path / "audit.jsonl")
    audit.write("probe", nested={"a" * 64: "first", "b" * 64: "second"})
    r = records(tmp_path / "audit.jsonl")[0]
    assert r["nested"] == {"<redacted hex key>": "first", "<redacted hex key> #2": "second"}
    assert unique_key("k", {"k", "k #2"}) == "k #3" and unique_key("k", set()) == "k"
```

Also add a daemon test to the same file, using the `Harness` from `tests/test_admind_daemon.py`. Import it with `from test_admind_daemon import Harness, run_with`, which is the existing pattern in the `test_admind_t9r*.py` files. The test checks that the audit keeps a long operator message whole, that no audit record holds a raw 64-hex identifier, and that a reply's hex is redacted in the chat:

```python
from test_admind_daemon import Harness, run_with  # noqa: E402  (shared harness)
from admind_waits import wait_until  # noqa: E402


def test_audit_is_whole_and_posts_are_redacted(tmp_path: Path) -> None:
    long_text = "line " * 1000 + HEX          # over the old 2,000-char cut

    async def scenario(h: Harness) -> None:
        await h.say("hello")                           # join signal
        await wait_until(lambda: any("listening" in t for t in h.texts()))
        mid = await h.say(long_text)
        await wait_until(lambda: any(r.get("kind") == "inbound" and len(str(r.get("text", ""))) > 4000
                                     for r in records(h.settings.state_dir / "audit.jsonl")))
        assert any(r.get("message_id") == ref_id(mid) for r in records(h.settings.state_dir / "audit.jsonl"))
        h.daemon.post("probe", f"see {HEX}", None)
        await wait_until(lambda: any("see <redacted hex key>" == t for t in h.texts()))

    h = run_with(tmp_path, scenario)
    raw = (h.settings.state_dir / "audit.jsonl").read_text()
    assert re.search(r"[0-9A-Fa-f]{64}", raw) is None
    inbound = [r for r in records(h.settings.state_dir / "audit.jsonl")
               if r.get("kind") == "inbound" and "line line" in str(r.get("text", ""))]
    assert inbound and str(inbound[-1]["text"]).endswith("<redacted hex key>")
    assert all(HEX not in t for t in h.texts())
```

(If `run_with` needs tmux and tmux is absent, mark the test with the same `needs_tmux` skip the daemon tests use. `asyncio` is used by later tests in this file.)

- [ ] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/test_admind_r13_redact.py -q`. Expected: `ModuleNotFoundError: heterodyne.admind.redact`.

- [ ] **Step 3: Implement `redact.py`.**

```python
"""One redaction for everything admind posts, summarizes or audits (ADR 0001 §8, revision 13; B1).

Each match is replaced by a marker and the rest of the text is kept: secrets (the patterns of
`config.secret_scan`, with a PEM block hidden to its END line), npubs, and runs of 64 or more hex
digits. Newline and tab are kept (they are layout); every other C0, DEL and C1 character is escaped as
`\\xNN`.

One pass matches secrets and npubs on the text, then escapes controls, then replaces hex runs, so hex
digits an escape adds next to a run are caught in the same pass. A pass can still make a new match: a
marker is a boundary, and a secret pattern that refused to start right after a hex digit starts right
after `>`. So `redact` repeats the pass until the text stops changing, which makes it idempotent by
construction. Each pass that changes the text replaces at least one match by a marker, and markers
match nothing, so this ends quickly; `MAX_PASSES` is a backstop, and text that is still changing after
it becomes `UNSTABLE` whole (fail closed).
"""

import re
from collections.abc import Container, Iterator

from heterodyne.config.secret_scan import IDENTIFIER_VALUES, SECRET_VALUES

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
                  re.DOTALL)
_SECRETS = tuple((kind, pattern) for kind, pattern in SECRET_VALUES if kind != "PEM private key")
_NPUB = dict(IDENTIFIER_VALUES)["npub"]
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{64,}")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
FRAGMENT = "<redacted fragment>"
UNSTABLE = "<redacted text>"
MAX_PASSES = 10


def _pass(text: str) -> str:
    text = _PEM.sub("<redacted PEM private key>", text)
    for kind, pattern in _SECRETS:
        text = pattern.sub(f"<redacted {kind}>", text)
    text = _NPUB.sub("<redacted npub>", text)
    text = _CONTROLS.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
    return _HEX_RUN.sub("<redacted hex key>", text)


def redact(text: str) -> str:
    for _ in range(MAX_PASSES):
        after = _pass(text)
        if after == text:
            return text
        text = after
    return UNSTABLE


def unique_key(key: str, taken: Container[str]) -> str:
    """A redacted dict key, made distinct from the keys already `taken`: two keys can redact to one marker,
    and neither entry may be lost, so later ones become `key #2`, `key #3`… (Codex r5 finding 1, r6
    finding 3)."""
    out, n = key, 1
    while out in taken:
        n += 1
        out = f"{key} #{n}"
    return out


def _spans(text: str) -> Iterator[tuple[int, int]]:
    """Every span `redact` would hide, in offsets of `text`. Like `redact`, it repeats: found spans are
    masked with `<` (a boundary, as a marker is) at the same length, and the text is scanned again."""
    patterns = (_PEM, *(p for _, p in _SECRETS), _NPUB, _HEX_RUN)
    for _ in range(MAX_PASSES):
        found = [(m.start(), m.end()) for p in patterns for m in p.finditer(text)]
        if not found:
            return
        yield from found
        for start, end in found:
            text = text[:start] + "<" * (end - start) + text[end:]
    yield 0, len(text)          # still finding after the backstop: treat it all as one value


def redact_continuation(previous: str, text: str) -> str:
    """`text` continues `previous`, which was already sent (B17). A value that straddles the boundary is
    hidden in `text` up to its end (its start went out already; nothing can undo that); then `text` is
    redacted as usual."""
    edge = len(previous)
    cut = max((end - edge for start, end in _spans(previous + text) if start < edge < end), default=0)
    if cut:
        text = FRAGMENT + text[cut:]
    return redact(text)
```

- [ ] **Step 4: Redact the audit centrally (B16).** In `audit.py`:

```python
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, cast

from heterodyne.admind.redact import redact, unique_key
from heterodyne.admind.store import now, private_dir

# Fields that hold message IDs or outbox keys. A 64-hex run in them becomes a stable reference instead of
# a redaction marker, so records about one message still correlate (B16).
ID_FIELDS = frozenset({"message_id", "reply_to", "key", "target", "anchor"})
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{64,}")


def ref_id(value: str) -> str:
    """How the audit names a 64-hex identifier: `id:` and 12 hex digits of its SHA-256."""
    return "id:" + hashlib.sha256(value.lower().encode()).hexdigest()[:12]


def clean(value: object, field: str | None = None) -> object:
    """A field value as it may be written: every string redacted, recursively; identifiers referenced."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if field in ID_FIELDS:
            value = _HEX_RUN.sub(lambda m: ref_id(m.group()), value)
        return redact(value)
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k, v in cast(dict[Any, Any], value).items():
            out[unique_key(redact(str(k)), out)] = clean(v, str(k))
        return out
    if isinstance(value, list | tuple):
        return [clean(v, field) for v in cast(list[Any] | tuple[Any, ...], value)]
    return redact(str(value))
```

In `Audit.write`, the record becomes `record = {"ts": now(), "kind": kind, **{k: clean(v, k) for k, v in fields.items()}}`. Add to the module docstring: "Every field is redacted (`redact`), and 64-hex identifiers in `ID_FIELDS` are written as `ref_id` references (ADR 0001 §8, revision 13; plan 2b B16)."

`redact.py` imports only `config.secret_scan`, so `audit → redact` adds no import cycle.

- [ ] **Step 5: Wire it into the daemon.**
  - `from heterodyne.admind.redact import redact`.
  - Delete `AUDIT_TEXT_CHARS`. `own_text` becomes `return redact(text)`, with the docstring "The operator's own text for the audit log, whole (revision 13), redacted (B1; the audit redacts again, idempotently)."
  - In audit records, pass keys and names unchanged: replace `key=show(row.key, False)`, `key=show(key, False)` and `previous=show(previous, False)` with the raw value. The audit redacts them (B16). `show` stays for user-facing errors.
  - `post` becomes:

```python
    def post(self, key: str, text: str, reply_to: str | None, lane: int = 1) -> None:
        """Queue one message. Everything admind posts is redacted here (B1); callers that chunk redact the
        whole text first, so a value can't escape redaction by straddling a chunk boundary."""
        if self.store.enqueue(key, redact(text), reply_to):
            self.wake.set()
```

  (`lane` is stored from Task 4; until then `enqueue` ignores it, so here call `self.store.enqueue(key, redact(text), reply_to)` and keep the parameter.)
  - `reply` chunks `redact(text)` instead of `text`.
  - In `on_stop`, `parts = chunk.split(redact(text), self.s.chunk_chars)`.
  - In `relay_alert`, `text = redact(alerts.render(name, alert, self.s.chunk_chars))`.
  - Update the module docstring's "Output safety" list. The fourth and fifth bullets become:
    - "an operator's own text, audited whole after `redact`; every audit field is redacted centrally";
    - "the admin agent's reply, a summary, a batch, `!details` or the `!tail` screen, relayed to the operators in chat after `redact`, never written to the audit log".

- [ ] **Step 6: Run the tests and confirm they pass.** Run `uv run pytest tests/test_admind_r13_redact.py -q`, then the full gate. Fix the existing tests that this changes, keeping each test's intent:
  - tests that asserted the 2,000-character cut or an unredacted reply (`grep -rn "AUDIT_TEXT_CHARS\|2000" tests/`);
  - tests that compare an audit record's `message_id`, `reply_to` or `key` with a raw 64-hex value (`grep -rn "message_id\"\] ==\|\"message_id\": \|message_id=" tests/test_admind_*.py`): compare with `ref_id(mid)` instead.

- [ ] **Step 7: Commit.**

```bash
git add src/heterodyne/admind/redact.py src/heterodyne/admind/audit.py src/heterodyne/admind/daemon.py tests/
git commit -m "feat(admind): one redaction for posts and audit; untruncated audit (ADR r13 §8)"
```

- [ ] **Step 8: Cross-model review** of `BASE..HEAD` against §8 Redaction and Audit; fix blocking findings.

---

### Task 3: Several operators and a trusted member count

**Files:**
- Modify: `src/heterodyne/admind/settings.py`, `src/heterodyne/admind/guard.py`, `src/heterodyne/admind/daemon.py` (`__init__`, `on_message`, `check_group`, `run`), `src/heterodyne/admind/cli.py` (`init`), `tests/fakes/settings.py`, `tests/test_admind_logic.py`, `tests/test_admind_settings.py`, `tests/test_admind_cli.py`
- Test: `tests/test_admind_r13_operators.py`

**Interfaces:**
- Produces:
  - `settings.Operator(name: str, npub: str, hex: str)`, a frozen dataclass;
  - `settings.operators(cfg: Config) -> tuple[Operator, ...]`;
  - `AdmindSettings.operators: tuple[Operator, ...]`, which replaces `operator_hex` and `operator_npub`;
  - `guard.Verdict(action, reason, operator: str | None = None)`;
  - `guard.judge_message(ev, *, group_id: str, operators: Mapping[str, str], latched: bool)`, where `operators` maps hex to name;
  - `guard.judge_member_count(count: int, expected: int) -> Verdict`;
  - `Admind.expected_members() -> int`;
  - `Admind.load_operators: Callable[[], tuple[Operator, ...]]`;
  - `Admind.policy_operators: tuple[Operator, ...]` (the policy's operators with an npub, as last loaded);
  - kv `group_operators`: a JSON sorted list of the hex keys of the operators admind has confirmed are in the group (B21);
  - `Admind.confirmed_operators() -> set[str] | None` and `Admind.authorise(confirmed: Iterable[str]) -> None`;
  - `Admind.operators: dict[str, str]` (hex → name): the policy operators whose key is in `group_operators`, and no others;
  - `settings.MAX_NAME = 128` and `settings.NAME_CONTROLS`, shared with the control server (Task 6);
  - `tests/fakes/settings.py` exports `OPERATOR_HEX`, `SECOND_HEX = "d4" * 32` and `operator(name, hex) -> Operator`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_operators.py
from pathlib import Path

import pytest
from fakes.settings import OPERATOR_HEX, SECOND_HEX, make_settings, operator

from heterodyne.admind import guard
from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.marmot.control import GroupStateChanged, InboundMessage, Message, Sender
from heterodyne.marmot.nip19 import hex_to_npub
from test_admind_settings import BASE_CONFIG  # noqa: E402

GROUP = "b2" * 32


def policy(d: Path, body: str) -> dict[str, str]:
    (d / "config.toml").write_text(BASE_CONFIG)
    (d / "policy.toml").write_text(body)
    return {"HETERODYNE_CONFIG_DIR": str(d), "HETERODYNE_STATE_DIR": str(d / "state"), "HOME": str(d)}


def two_ops(extra: str = "") -> str:
    return (f'approvers = ["a", "b", "c"]\noperators = ["a", "b", "c"]\n'
            f'[identities.a]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n'
            f'[identities.b]\nmarmot_npub = "{hex_to_npub(SECOND_HEX)}"\n{extra}')


def test_every_operator_with_an_npub(tmp_path: Path) -> None:
    env = policy(tmp_path, two_ops())          # "c" has no identity: skipped
    s = resolve(load(None, env), env)
    assert [(o.name, o.hex) for o in s.operators] == [("a", OPERATOR_HEX), ("b", SECOND_HEX)]


def test_no_operator_with_an_npub_is_an_error(tmp_path: Path) -> None:
    env = policy(tmp_path, 'approvers = ["a"]\noperators = ["a"]\n')
    with pytest.raises(ConfigError, match="at least one"):
        resolve(load(None, env), env)


def test_duplicate_keys_are_an_error(tmp_path: Path) -> None:
    body = (f'approvers = ["a", "b"]\noperators = ["a", "b"]\n'
            f'[identities.a]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n'
            f'[identities.b]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n')
    env = policy(tmp_path, body)
    with pytest.raises(ConfigError, match="same marmot_npub"):
        resolve(load(None, env), env)


@pytest.mark.parametrize("name", ["x" * 129, "bad\x1bname", "tab\tname"])
def test_operator_names_follow_the_control_contract(tmp_path: Path, name: str) -> None:
    # Codex r2 finding 10: a name `admind operators remove NAME` can't carry is refused in policy too.
    body = (f'approvers = ["{name}"]\noperators = ["{name}"]\n'
            f'[identities."{name}"]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n')
    env = policy(tmp_path, body.replace("\x1b", "\\u001b").replace("\t", "\\t"))
    with pytest.raises(ConfigError, match="operator name"):
        resolve(load(None, env), env)


def msg(sender: str) -> InboundMessage:
    return InboundMessage("a1" * 32, GROUP, Message("0" * 64, Sender(sender, False), "hi", 1))


def test_guard_names_the_operator() -> None:
    ops = {OPERATOR_HEX: "a", SECOND_HEX: "b"}
    v = guard.judge_message(msg(SECOND_HEX.upper()), group_id=GROUP, operators=ops, latched=False)
    assert (v.action, v.operator) == ("process", "b")
    assert guard.judge_message(msg("e5" * 32), group_id=GROUP, operators=ops, latched=False).action == "drop"
    v = guard.judge_message(msg(SECOND_HEX), group_id=GROUP, operators=ops, latched=True)
    assert (v.action, v.operator) == ("drop", "b")          # latched, but still named for the audit
    assert guard.judge_message(msg("e5" * 32), group_id=GROUP, operators=ops, latched=True).operator is None


def test_member_count_against_expected() -> None:
    assert guard.judge_member_count(3, 3).action == "process"
    v = guard.judge_member_count(2, 3)
    assert v.action == "latch" and v.reason == "group has 2 members, expected 3"
```

Add daemon tests, built with the shared harness:
- two operators (`make_settings(tmp_path, operators=(operator("a", OPERATOR_HEX), operator("b", SECOND_HEX)))`);
- `FakeWnAgent(member_count=3)`;
- kv `expected_members = "3"` and kv `group_operators = json.dumps(sorted([OPERATOR_HEX, SECOND_HEX]))`.

They assert:
1. a message from `SECOND_HEX` is processed, and its audit `inbound` record has `operator == "b"`;
2. a stranger is dropped;
3. with `expected_members` absent and `member_count=2`, startup sets it to `"2"` and audits `{"kind": "guard", "action": "migrated"}`;
4. **configured is not confirmed:** with kv `group_operators = json.dumps([OPERATOR_HEX])` and `member_count=2, expected_members="2"`, a message from `SECOND_HEX` (in policy, not in the group) is dropped as "sender is not an operator";
5. **the plan-2 upgrade with one policy operator:** the default settings (one operator), `group_operators` absent: startup sets it to `[OPERATOR_HEX]`, audits `{"kind": "guard", "action": "migrated-operators", "operators": 1}`, and that operator's message is processed;
6. **the plan-2 upgrade with several policy operators:** two operators, `group_operators` absent, `member_count=2`: startup latches (reason starts "admind does not know which operators are in the group") and leaves `group_operators` absent; `h.daemon.operators == {}`. (Task 5's scenario 18 rearms it.)
7. **a latched operator's message is audited whole (Codex r6 finding 1):** latch the daemon (`h.daemon.latch("test")`), then `SECOND_HEX` sends `text = "line " * 1000 + "ghp_" + "A" * 36`. Nothing is dispatched or posted, and the audit has one `drop` record with `operator == "b"`, the reason "admind is latched…", and `text == redact(text)` (whole, over 5,000 characters, no token). Unlatched, an operator message delivered twice with one message ID (`h.fake.message_event` with the same ID): the second is audited the same way, with reason "replayed message id".

(`Harness.__init__` takes the settings from `make_settings`; add an optional `settings_overrides` argument to `Harness` in `tests/test_admind_daemon.py` and pass it through.)

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ImportError: cannot import name 'SECOND_HEX'` / `operator`.

- [ ] **Step 3: Implement the settings.** In `settings.py`:

```python
@dataclass(frozen=True)
class Operator:
    name: str
    npub: str
    hex: str


MAX_NAME = 128
NAME_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def operators(cfg: Config) -> tuple[Operator, ...]:
    """Every `policy.toml` operator with `identities.<name>.marmot_npub` (ADR 0001 §8, revision 13).
    Its name must be one `admind operators add|remove NAME` can carry: 1 to `MAX_NAME` characters and no
    control characters (the control server checks the same, Task 6)."""
    found: list[Operator] = []
    seen: set[str] = set()
    for name in cfg.policy.operators:
        npub = cfg.policy.identities.get(name, {}).get("marmot_npub")
        if not npub:
            continue
        if not 1 <= len(name) <= MAX_NAME or NAME_CONTROLS.search(name):
            raise ConfigError(f"policy.toml: operator name {show(name, False)!r} must be 1-{MAX_NAME} "
                              "characters with no control characters")
        where = f"policy.toml: identities.{show(name, False)}.marmot_npub"
        try:
            key = npub_to_hex(npub).lower()
        except Nip19Error as exc:
            raise ConfigError(f"{where} is not a valid npub ({exc})") from None
        if key in seen:
            raise ConfigError(f"{where} is the same marmot_npub as another operator's")
        seen.add(key)
        found.append(Operator(name, npub, key))
    if not found:
        raise ConfigError("policy.toml: admind needs at least one entry in operators with "
                          "identities.<name>.marmot_npub")
    return tuple(found)
```

Delete `_operator`. Add `import re`. In `AdmindSettings`, replace `operator_hex` and `operator_npub` with `operators: tuple[Operator, ...]`, and in `resolve` pass `operators=operators(cfg)`.

`tests/fakes/settings.py`:

```python
OPERATOR_HEX = "c3" * 32
SECOND_HEX = "d4" * 32


def operator(name: str, key: str) -> Operator:
    return Operator(name, hex_to_npub(key), key)
```

and in `make_settings` the base uses `operators=(operator("op", OPERATOR_HEX),)`.

- [ ] **Step 4: Implement the guard.**

```python
@dataclass(frozen=True)
class Verdict:
    action: Literal["process", "drop", "ignore", "latch"]
    reason: str
    operator: str | None = None


def judge_message(ev: InboundMessage, *, group_id: str, operators: Mapping[str, str],
                  latched: bool) -> Verdict:
    if ev.group_id_hex.lower() != group_id:
        return Verdict("drop", "message from another group")
    sender = ev.message.sender
    if sender.is_self:
        return Verdict("ignore", "admind's own message")
    name = operators.get(sender.account_id_hex.lower())
    if name is None:
        return Verdict("drop", "sender is not an operator")
    if latched:     # an authenticated operator: the message is still audited in full, under their name
        return Verdict("drop", "admind is latched; run `admind rearm` on the host", name)
    return Verdict("process", "operator message", name)


def judge_member_count(count: int, expected: int) -> Verdict:
    if count == expected:
        return Verdict("process", f"{count} members, as expected")
    return Verdict("latch", f"group has {count} members, expected {expected}")
```

Update the module docstring:
- "Only MLS-authenticated messages from an operator's exact key ...";
- "a member count other than the trusted expected count".

- [ ] **Step 5: Implement the daemon and CLI changes.**
  - `Admind.__init__` gains a keyword `load_operators: Callable[[], tuple[Operator, ...]] | None = None`:

```python
        self.load_operators = load_operators or (lambda: settings.operators)
        self.policy_operators = settings.operators
        self.operators: dict[str, str] = {}     # set by run() from group_operators (B21)
```

  - The durable authorization state (B21). Being in `policy.toml` makes a key **eligible**; only a confirmed group membership (a committed transition, `init`, or a rearm) makes it **authorised**:

```python
    def confirmed_operators(self) -> set[str] | None:
        """The keys admind has confirmed are in the group; None before the plan-2 migration."""
        raw = self.store.get("group_operators")
        return None if raw is None else {str(k) for k in json.loads(raw)}

    def authorise(self, confirmed: Iterable[str]) -> None:
        """Who may command admind: the policy operators whose key is confirmed in the group."""
        keys = set(confirmed)
        self.operators = {o.hex: o.name for o in self.policy_operators if o.hex in keys}
```

  - `expected_members`:

```python
    def expected_members(self) -> int:
        """The trusted member count (B3). Absent only before the startup migration has run."""
        return int(self.store.get("expected_members") or "2")
```

  - `run()` starts with:

```python
        if self.store.get("expected_members") is None:     # a plan-2 group: always two members (B3)
            self.store.set("expected_members", "2")
            self.audit.write("guard", action="migrated", expected_members=2)
        confirmed = self.confirmed_operators()
        if confirmed is None:          # a plan-2 database (B21)
            if len(self.policy_operators) == 1:     # plan 2 allowed exactly one operator, so it is that one
                confirmed = {self.policy_operators[0].hex}
                self.store.set("group_operators", json.dumps(sorted(confirmed)))
                self.audit.write("guard", action="migrated-operators", operators=1)
            else:
                confirmed = set()
                self.latch("admind does not know which operators are in the group (upgraded with several "
                           "operators in policy.toml); make policy.toml's operators match the group's, then "
                           "run `admind rearm` on the host")
        self.authorise(confirmed)
```

  Imports: `json`, and `Iterable` from `collections.abc`.

  - `check_group`: `verdict = guard.judge_member_count(info.member_count, self.expected_members())`.
  - `on_message`: `verdict = guard.judge_message(ev, group_id=self.group, operators=self.operators, latched=self.latched())`. The `inbound` audit record gets `operator=verdict.operator`. §8 logs **each operator message** in full with its operator (Codex r6 finding 1), so a dropped message from an authenticated operator is audited whole too; a stranger's text still is not. The `drop` branch and the two drops after it become:

```python
        if verdict.action == "drop":
            if verdict.operator is not None:    # an operator, while latched: logged in full, never acted on
                self.audit.write("drop", operator=verdict.operator, message_id=mid, reason=verdict.reason,
                                 text=own_text(ev.message.text))
                return
            # The sender is peer-supplied: a short prefix correlates without recording an identifier,
            # and the text, which a stranger controls, is not recorded at all.
            self.audit.write("drop", sender_prefix=ev.message.sender.account_id_hex[:8],
                             reason=verdict.reason, text_chars=len(ev.message.text))
            return
        if not MESSAGE_ID.fullmatch(mid):
            self.audit.write("drop", operator=verdict.operator, reason="malformed message id",
                             text=own_text(ev.message.text))
            return
        if not self.store.claim_inbound(mid):
            self.audit.write("drop", operator=verdict.operator, message_id=mid, reason="replayed message id",
                             text=own_text(ev.message.text))
            return
```

  (The audit redacts every field, and `message_id` becomes an `id:` reference, B16.)
  - `cli.init`:
    - `created = await client.group_create(account, s.group_name, [o.npub for o in s.operators])`;
    - in the same block, in one `store.transaction()`, `store.set("expected_members", str(1 + len(s.operators)))` and `store.set("group_operators", json.dumps(sorted(o.hex for o in s.operators)))`;
    - the final message becomes "Created admind's identity and its group with N operator(s). Each operator accepts the invite in their Marmot client; then start admind and send any message in the group: admind answers once it sees one of you."
  - `cli._run_with_child` passes `load_operators=lambda: resolve(hconfig.load(), os.environ).operators` to `Admind`.

- [ ] **Step 6: Update the existing tests.** `test_admind_logic.py` passes `operators={OP: "op"}`. `test_admind_settings.py` asserts `s.operators[0].hex == OPERATOR_HEX`. `test_admind_cli.py` checks that no operator's npub is in the output. Run the full gate.

- [ ] **Step 7: Commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): several operators and a trusted member count (ADR r13 §8)"
```

Cross-model review against §8 Operators and the latch rule.

---

### Task 4: Two delivery lanes, the outbox upgrade and redaction at delivery

**Files:**
- Modify: `src/heterodyne/admind/store.py`, `src/heterodyne/admind/daemon.py` (`__init__`, `post`, `outbox_pass`, `run`)
- Test: `tests/test_admind_r13_lanes.py`

**Interfaces:**
- Produces:
  - `Store.enqueue(key, text, reply_to, lane: int = 1) -> bool`;
  - `Store.next_pending() -> OutboxRow | None`;
  - `Store.redact_pending_outbox(chunk_chars: int) -> int` (B17);
  - `OutboxRow.lane: int`;
  - `Admind.post(key, text, reply_to, lane=1)` now stores the lane;
  - `Admind.send_lock: asyncio.Lock`, held by `outbox_pass` across each send (Task 5 drains it);
  - `Admind.send_failed(seq: int, key: str)`, which Task 8 extends.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_lanes.py
import sqlite3
from pathlib import Path

from heterodyne.admind.store import Store

TOKEN = "ghp_" + "A" * 30
HEX = "ab" * 32

OLD_OUTBOX = ("CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, "
              "reply_to TEXT, text TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending', 'sent', "
              "'failed')), attempts INTEGER NOT NULL DEFAULT 0, message_id TEXT);")


def old_database(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """A plan-2 database: no lane column, rows queued before redaction existed."""
    db = sqlite3.connect(path)
    db.executescript(OLD_OUTBOX)
    db.executemany("INSERT INTO outbox(key, text, status) VALUES (?, ?, ?)", rows)
    db.commit()
    db.close()


def pending(s: Store) -> list[tuple[str, str]]:
    return [(r.key, r.text) for r in s.pending()]


def test_lane_one_first_then_lane_two(tmp_path: Path) -> None:
    s = Store(tmp_path / "a.db")
    s.enqueue("d1", "details 1", None, lane=2)
    s.enqueue("d2", "details 2", None, lane=2)
    s.enqueue("c1", "command reply", None)
    row = s.next_pending()
    assert row is not None and row.key == "c1" and row.lane == 1
    s.mark_sent(row.seq, None)
    row = s.next_pending()
    assert row is not None and row.key == "d1"
    s.mark_sent(row.seq, None)
    s.enqueue("alert", "urgent", None)          # arrives while lane 2 is being sent
    row = s.next_pending()
    assert row is not None and row.key == "alert"


def test_old_database_gains_the_lane_column(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("k", "t", "pending")])
    s = Store(tmp_path / "old.db")
    row = s.next_pending()
    assert row is not None and row.lane == 1
    assert s.get("outbox_needs_redaction") == "1"


def test_upgrade_redacts_a_secret_split_across_pending_chunks(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("reply:s:1:0", "token " + TOKEN[:12], "pending"),
                                       ("reply:s:1:1", TOKEN[12:] + f" and {HEX}", "pending"),
                                       ("ready", f"hello {HEX}", "pending")])
    s = Store(tmp_path / "old.db")
    assert s.redact_pending_outbox(4000) == 3
    assert sorted(pending(s)) == [("ready", "hello <redacted hex key>"),
                                  ("reply:s:1:r0", "token <redacted GitHub token> and <redacted hex key>")]
    assert s.get("outbox_needs_redaction") is None
    assert s.redact_pending_outbox(4000) == 0          # once only


def test_upgrade_hides_the_rest_of_a_value_begun_in_a_sent_chunk(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("reply:s:2:0", "token " + TOKEN[:12], "sent"),
                                       ("reply:s:2:1", TOKEN[12:] + " end", "pending")])
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(4000)
    assert pending(s) == [("reply:s:2:r0", "<redacted fragment> end")]


def test_upgrade_hides_a_value_that_spans_several_sent_chunks(tmp_path: Path) -> None:
    # Codex r2 finding 2: the chunk just before the pending one holds no token prefix.
    long_token = "ghp_" + "Z" * 600
    old_database(tmp_path / "old.db", [("reply:s:3:0", long_token[:250], "sent"),
                                       ("reply:s:3:1", long_token[250:500], "sent"),
                                       ("reply:s:3:2", long_token[500:] + " end", "pending")])
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(4000)
    assert pending(s) == [("reply:s:3:r0", "<redacted fragment> end")]


def test_upgrade_hides_a_continuation_whose_start_is_gone(tmp_path: Path) -> None:
    old_database(tmp_path / "old.db", [("reply:s:4:0", "start", "sent"),
                                       ("reply:s:4:2", "Z" * 40 + " end", "pending")])   # :1 is missing
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(4000)
    assert pending(s) == [("reply:s:4:r0", "<redacted continuation of a partly sent reply>")]


def test_upgrade_rechunks(tmp_path: Path) -> None:
    # Not hex: a 600-character hex run would be one marker.
    old_database(tmp_path / "old.db", [("cmd:x:0", "x" * 300, "pending"), ("cmd:x:1", "y" * 300, "pending")])
    s = Store(tmp_path / "old.db")
    s.redact_pending_outbox(250)
    rows = pending(s)
    assert [k for k, _ in rows] == ["cmd:x:r0", "cmd:x:r1", "cmd:x:r2"]
    assert "".join(t for _, t in rows) == "x" * 300 + "y" * 300


def test_a_new_database_needs_no_upgrade(tmp_path: Path) -> None:
    s = Store(tmp_path / "new.db")
    assert s.get("outbox_needs_redaction") is None
    assert s.redact_pending_outbox(4000) == 0
```

Daemon tests with the shared harness:
1. **Lane order:** while lane-2 rows are pending, a command reply queued after them is sent before the remaining lane-2 rows. Use `h.fake.on_send` to queue a lane-1 post the first time a lane-2 text is sent, then assert the order of `h.texts()`.
2. **Redaction at delivery:** before `run()` (the `before` hook of `run_with`), insert a raw row with `h.store.enqueue("raw", f"see {HEX}", None)`, which bypasses `Admind.post`. After the join signal, the fake receives `"see <redacted hex key>"` and never `HEX`.
3. **Upgrade at startup:** build the harness's database as a plan-2 database first (call `old_database(h.settings.state_dir / "admind.db", [...])` before `Harness` opens it; add an optional `before_store` callback to `Harness.__init__` that runs before `Store(...)`). A pending `reply:s:1:0`/`:1` pair holding a split token is delivered as one `<redacted GitHub token>` message, and the audit has `{"kind": "outbox", "action": "redacted-after-upgrade", "rows": 2}`.
4. **Backoff is per row (Codex r5 finding 5, r6 finding 2):** replace `h.daemon._sleep` with a coroutine that, for a delay of 2 s or more (a backoff: the first is 2 s), appends a fresh `asyncio.Event` to a list `gates` and waits on it, and otherwise returns at once (other loops' polls stay fast). Once the outbox is empty, set `h.fake.fail_sends = 2`. Post a lane-2 row (`h.daemon.post("d", "details", None, lane=2)`); its first send fails and `gates[0]` is its backoff. Then `h.daemon.post("c", "urgent", None)`: it is sent at once (lane 1 does not wait for lane 2's backoff), fails, and `gates[1]` is its backoff. Set `gates[1]`: `"urgent"` is delivered, and the fake still saw exactly one attempt at `"details"` (its own timer is still running). Set `gates[0]`: `"details"` is delivered. A variant with `fail_sends = 1` checks that `"urgent"` is delivered while `gates[0]` is unset.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `TypeError: enqueue() got an unexpected keyword argument 'lane'`.

- [ ] **Step 3: Implement the store changes.**
  - `from heterodyne.admind import chunk`, `import re` and `from heterodyne.admind.redact import redact, redact_continuation`. Neither module imports the store, so there is no cycle.
  - In `SCHEMA`'s `outbox`, add `lane INTEGER NOT NULL DEFAULT 1 CHECK (lane IN (1, 2)),` after `reply_to`.
  - After `executescript(SCHEMA)` in `Store.__init__`:

```python
        columns = {str(r[1]) for r in self.db.execute("PRAGMA table_info(outbox)")}
        if "lane" not in columns:       # a database created before revision 13 (B17)
            with self.transaction():    # the column and the redaction marker: both or neither
                self.db.execute("ALTER TABLE outbox ADD COLUMN lane INTEGER NOT NULL DEFAULT 1")
                self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES ('outbox_needs_redaction', '1')")
```

  - `OutboxRow` gains `lane: int`.
  - `enqueue` inserts `lane`.
  - `pending()` orders `BY lane, seq` and selects `lane`.
  - New methods:

```python
_CHUNKED = re.compile(r"\A(.*):(\d+)\Z", re.DOTALL)
PARTLY_SENT = "<redacted continuation of a partly sent reply>"


def _like(text: str) -> str:
    """`text` as a literal LIKE prefix (escape `\\`, `%` and `_`)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


    @_locked
    def next_pending(self) -> OutboxRow | None:
        """The next message to send: lane 1 (commands, alerts, notices, summaries, batches, verbatim
        replies) before lane 2 (`!details`), each in order (B14)."""
        r = self.db.execute("SELECT seq, key, reply_to, text, attempts, lane FROM outbox "
                            "WHERE status = 'pending' ORDER BY lane, seq LIMIT 1").fetchone()
        return None if r is None else OutboxRow(int(r[0]), str(r[1]), None if r[2] is None else str(r[2]),
                                                str(r[3]), int(r[4]), int(r[5]))

    @_locked
    def redact_pending_outbox(self, chunk_chars: int) -> int:
        """Once, after the upgrade to revision 13 (B17): redact what a plan-2 admind queued and never sent.
        The pending chunks of one reply (keys `<prefix>:<i>`) are joined in order first, so a value split
        across their boundaries is still found, then re-chunked under new keys `<prefix>:r<i>`. If the
        reply's earlier chunks were already sent, they are joined in order and a value they began is hidden
        to its end (it can span several of them); if one of them is missing or not sent, the pending text is
        replaced whole (fail closed). Returns the number of rows rewritten; one transaction."""
        if self.get("outbox_needs_redaction") is None:
            return 0
        with self.transaction():
            rows = self.db.execute("SELECT seq, key, reply_to, text FROM outbox WHERE status = 'pending' "
                                   "ORDER BY seq").fetchall()
            groups: dict[str, list[tuple[int, int, str | None, str]]] = {}
            for seq, key, reply_to, text in rows:
                m = _CHUNKED.match(str(key))
                prefix, index = (m.group(1), int(m.group(2))) if m else (str(key), -1)
                groups.setdefault(prefix, []).append((index, int(seq), None if reply_to is None else str(reply_to),
                                                      str(text)))
            for prefix, parts in groups.items():
                parts.sort()
                first, reply_to = parts[0][0], parts[0][2]
                joined = "".join(p[3] for p in parts)
                if first <= 0:
                    clean = redact(joined)
                else:
                    sent = self.db.execute(
                        "SELECT key, text FROM outbox WHERE key LIKE ? ESCAPE '\\' AND status = 'sent'",
                        (_like(prefix) + ":%",)).fetchall()
                    earlier = {int(m.group(2)): str(t) for k, t in sent
                               if (m := _CHUNKED.match(str(k))) and m.group(1) == prefix}
                    if all(i in earlier for i in range(first)):
                        clean = redact_continuation("".join(earlier[i] for i in range(first)), joined)
                    else:
                        clean = PARTLY_SENT
                self.db.executemany("DELETE FROM outbox WHERE seq = ?", [(p[1],) for p in parts])
                if first < 0:       # an unchunked key keeps its key
                    pieces = [(prefix, clean)]
                else:
                    pieces = [(f"{prefix}:r{i}", part) for i, part in enumerate(chunk.split(clean, chunk_chars))]
                self.db.executemany("INSERT INTO outbox(key, reply_to, text, status, lane) "
                                    "VALUES (?, ?, ?, 'pending', 1)", [(k, reply_to, t) for k, t in pieces])
            self.db.execute("DELETE FROM kv WHERE key = 'outbox_needs_redaction'")
        return len(rows)
```

  - `relay_alert`'s insert names `lane` explicitly as `1`.

- [ ] **Step 4: Implement the daemon changes.**
  - `__init__`: `self.send_lock = asyncio.Lock()   # held across each send; a membership transition drains it (B7)` and `self._backoff: dict[int, asyncio.Future[None]] = {}  # each row waiting out a retry: its own timer`. `run()`'s `finally` cancels every timer left in `_backoff`.
  - `post` passes `lane` to `enqueue`.
  - In `run()`, before `self.recover()` (and, from Task 5, after the B3 migration and the B15 latch):

```python
        rewritten = self.store.redact_pending_outbox(self.s.chunk_chars)   # B17: a plan-2 outbox
        if rewritten:
            self.audit.write("outbox", action="redacted-after-upgrade", rows=rewritten)
```

  - `outbox_pass` becomes:

```python
    async def outbox_pass(self) -> None:
        """One sweep of the outbox (a loop iteration, callable on its own). Each send holds `send_lock`,
        and the gate and the next row are read under it: a membership transition that has drained the
        lock sees no send in progress and none can start (B7). Rows are redacted again at delivery (B1).
        A retry's backoff is a timer, not a sleep in this loop: the pass ends, and a row queued meanwhile
        (`post` sets `wake`) is sent at once if it comes first, so a lane-1 message never waits behind a
        lane-2 row's backoff (B14). The timer sets `wake` when it ends."""
        if self.held:
            await self.flush()              # emits NOT_READY once the start timeout passes
        self.notify_held()
        while True:
            async with self.send_lock:
                if not self.may_post():     # rechecked per row: a latch mid-batch stops the rest
                    return
                row = self.store.next_pending()     # re-read each time: a new lane-1 row goes next (B14)
                if row is None:
                    return
                timer = self._backoff.get(row.seq)
                if timer is not None and not timer.done():
                    return                  # its retry is not due; its timer or a new row wakes the loop
                try:
                    sent = await self.client.send_final(self.account, self.group, redact(row.text),
                                                        row.reply_to, row.key)
                except ControlError as exc:
                    attempts = self.store.mark_attempt(row.seq)
                    if not (exc.retryable and attempts < MAX_SEND_ATTEMPTS):
                        self._backoff.pop(row.seq, None)
                        self.send_failed(row.seq, row.key)
                        self.audit.write("send", key=row.key, action="failed", code=exc.code)
                        continue
                    self.audit.write("send", key=row.key, action="retry", attempts=attempts, code=exc.code)
                    timer = asyncio.ensure_future(self._sleep(min(60, 2 ** attempts)))
                    timer.add_done_callback(lambda _: self.wake.set())
                    self._backoff[row.seq] = timer      # per row: another row's retry never cuts this one short
                    return
                else:
                    self._backoff.pop(row.seq, None)
                    self.store.mark_sent(row.seq, sent.message_ids_hex[0] if sent.message_ids_hex else None)
                    self.audit.write("send", key=row.key, action="sent")
                    continue

    def send_failed(self, seq: int, key: str) -> None:
        """A message admind gave up on (Task 8 adds the backstop for replies)."""
        self.store.mark_failed(seq)
```

  (`continue` inside `async with` releases the lock before the next row, so a transition waiting on it gets in between rows.)

- [ ] **Step 5: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): two outbox lanes; redact the outbox on upgrade and at delivery (ADR r13 §8)"
```

Review against §8 Delivery lanes and Redaction, and B14 and B17.

---

### Task 5: Journaled membership transitions

**Files:**
- Create: `src/heterodyne/admind/membership.py`
- Modify: `src/heterodyne/marmot/control.py`, `tests/fakes/fake_wn_agent.py`, `src/heterodyne/admind/daemon.py` (`__init__`, `may_post`, `authorised`, `_flush`, `check_group`, `worker_loop`, `run`, new methods)
- Test: `tests/test_admind_r13_membership.py`

**Interfaces:**
- Consumes:
  - `Admind.expected_members()`, `Admind.load_operators`, `Admind.policy_operators`, `Admind.operators`, `confirmed_operators()`, `authorise()` and kv `group_operators` (Task 3);
  - `Admind.send_lock` (Task 4);
  - `docs/spikes/S4b-membership.md` (Task 1): the error codes to allowlist.
- Produces:
  - `control.PeerError(ControlError)`;
  - `ControlClient.group_member_add(account, group, members: list[str]) -> MembershipUpdated`;
  - `ControlClient.group_member_remove(account, group, members: list[str]) -> MembershipUpdated`;
  - `membership.Pending`;
  - `membership.settle(reported: Reported, count: int | None, pending: Pending) -> Outcome`;
  - `Admind.change_membership(op: str, name: str) -> tuple[str, str]`, whose result is one of `committed`, `aborted`, `latched` or `refused`;
  - `Admind.rearm() -> tuple[str, str]`, whose result is `rearmed` or `refused`;
  - `Admind.changing: bool`, `Admind.membership_epoch: int`, `Admind.group_events: int`, `Admind.reading: bool`;
  - `Admind.transition_lock` and `Admind.work_lock`, both `asyncio.Lock`;
  - in the fake: `membership_mode` (`ok`, `fail`, `ok-no-count`, `fail-count`, `hang`, `lost`, `gated`), `membership_gate: asyncio.Event`, `info_gate: asyncio.Event | None` and `drop_subscriptions()`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_membership.py
import pytest

from heterodyne.admind.membership import Pending, settle

P = Pending("add", "b", "d4" * 32, 2, 3, "2026-10-02T00:00:00+00:00")


@pytest.mark.parametrize(("reported", "count", "outcome"), [
    ("ok", 3, "commit"),
    ("ok", 2, "latch"),            # success reported, count disagrees
    ("ok", None, "latch"),         # count unreadable
    ("failed", 2, "abort"),
    ("failed", 3, "latch"),        # failure reported, but the change happened
    ("unknown", 2, "latch"),       # lost reply: count alone never confirms
    ("unknown", 3, "latch"),
])
def test_settle(reported: str, count: int | None, outcome: str) -> None:
    assert settle(reported, count, P) == outcome  # type: ignore[arg-type]


def test_pending_round_trips() -> None:
    assert Pending.load(P.dump()) == P
```

Daemon scenarios, in the same file, with the shared harness, `FakeWnAgent(member_count=2)`, a `load_operators` stub that returns operators `op` and `b` (`policy` below), kv `expected_members = "2"` and kv `group_operators = [OPERATOR_HEX]`. Each scenario first sends the join signal and waits for "listening", so the subscription is observing and the group verified. Start a transition with `task = asyncio.create_task(h.daemon.change_membership("add", "b"))`; `requested(h, kind)` is `any(r.get("type") == kind for r in h.fake.requests)`.

1. **commit:** the call returns `("committed", …)`:
   - `expected_members == "3"`;
   - no `membership_pending`;
   - the fake received a `group_member_add` with `members == [SECOND_HEX]`;
   - the audit records `pending` then `committed`;
   - a notice "Operator b was added to the group." is posted;
   - `h.daemon.operators` now maps `SECOND_HEX` to `"b"`, kv `group_operators` holds both keys, and `changing` is false.
2. **abort:** with `fake.membership_mode = "fail"`, the call returns `("aborted", …)`, `expected_members == "2"`, and nothing is latched.
3. **latch on disagreement:** `"ok-no-count"` (success, count unchanged) and `"fail-count"` (error, count changed) each return `("latched", …)`, with `latched` set and `membership_pending` kept.
4. **latch on a lost reply:** with `"hang"`, a `ControlClient` timeout of 0.5 s gives `("latched", …)`.
5. **event during the transition:** with `"gated"`, wait until `requested(h, "group_member_add")`, push a `group_state_changed` `member_removed` event, then `h.fake.membership_gate.set()`. Result: `latched`.
6. **subscription replaced during the transition:** with `"gated"`, wait for the request, record `gen = h.daemon.sub_gen`, `await h.fake.drop_subscriptions()`, wait until `h.daemon.sub_gen != gen`, then open the gate. Result: `latched` (B5), even though the add succeeded and the count is right.
7. **holds and drains:**
   - **A paste in progress:** the test acquires `h.daemon.dispatch_lock` itself, then starts the add. For 0.3 s: `changing` is true, `may_post()` and `authorised()` are false, and the fake has **no** `group_info` or `group_member_add` request after the one the join signal caused (count them before). After `dispatch_lock.release()` the add commits.
   - **A send in progress:** the same, holding `h.daemon.send_lock`.
   - **The operator message in hand:** the test holds `h.daemon.work_lock`, then starts the add. For 0.3 s the fake has no new `group_info` or `group_member_add` request. (`changing` stays false meanwhile: the transition sets it only once it holds `work_lock`, so the message in hand is finished under the old rules rather than deferred.) After `work_lock.release()` the add commits.
8. **a message during a transition waits and is then processed:** with `"gated"`, wait for the request, `mid = await h.say("hello during the change")`, sleep 0.3 s and assert the message is not in `h.store.inbound_with_status("dropped")` and the fake claude log (`h.log`) doesn't contain it. Open the gate. The add commits, then the prompt is pasted (the log contains it) and no `UNVERIFIED` text is sent.
9. **group checks during a transition are deferred:** with `"gated"`, wait for the request, then `assert await h.daemon.check_group() is False`. The audit has `{"kind": "guard", "action": "group-check-deferred"}`, nothing is latched and `group_ok` is still true. Open the gate; the add commits.
10. **an exception after journaling latches:** replace `h.daemon.client.group_member_add` with an async function that raises `RuntimeError`. `change_membership` raises `RuntimeError`; afterwards `latched` is set (reason starts "a membership change failed before it was settled"), `membership_pending` is kept and `changing` is false.
11. **if even the latch fails, the hold stays:** as in (10), and also replace `h.daemon.latch` with a function that raises `OSError`. `change_membership` raises; `changing` is still true, `may_post()` is false, `membership_pending` is kept, and the audit has `{"kind": "membership", "action": "held-unsettled"}`. (The restart that follows latches: scenario 13.)
12. **refusals, each changing nothing and sending no `group_member_*` request:** unknown name; adding `b` when it is already in `h.daemon.operators`; removing the last operator (`expected_members` 2, remove `op`); latched; a pending record already present; not observing (call it before the join signal's check has run: set `h.daemon.observing = False` first); `load_operators` raising `ConfigError`.
13. **startup latch:** with `membership_pending` set before `run()`, the daemon latches before dispatching anything and audits the reason.
14. **rearm:** after (3), `rearm()` reads the count, sets `expected_members` to it, clears `latched` and `membership_pending`, and posting resumes. After (11), `rearm()` also clears `changing`. With `fake.fail_group_info = True` it returns `("refused", …)` and changes nothing. With a count of 1 it is refused. With `h.daemon.reading = False` (events not being read) it is refused before any `group_info` request.
15. **rearm races an event (Codex r2 finding 3):** latch via (3), then `h.fake.info_gate = asyncio.Event()`; start `task = asyncio.create_task(h.daemon.rearm())`; wait until the fake has a new `group_info` request; push a `group_state_changed` `member_added` event and wait until `h.daemon.group_events` grows; set the gate. `rearm` returns `("refused", …)`; `latched` is still set and `expected_members` unchanged. The same with `drop_subscriptions()` instead of the event (`sub_gen` changes): refused.
    **Acknowledged but not yet reading (Codex r3 finding 3):** latch via (3); set `h.fake.info_gate = asyncio.Event()` and `await h.fake.drop_subscriptions()`; wait until the new subscription's `group_info` request arrives (its `confirm_observing` is now blocked, `acked` true, `reading` false); push a `group_state_changed` `member_added` event (buffered: the reader is not reading). `await h.daemon.rearm()` returns `("refused", …)` at once without a further `group_info` request. Open the gate: the reader processes the buffered event, and `latched` is still set.
16. **lost reply on an add, then rearm (Codex r2 finding 4):** `membership_mode = "lost"` (the fake applies the change, then never answers) and a `ControlClient` timeout of 0.5 s: `("latched", …)`; `group_operators` is still `[OPERATOR_HEX]`. `rearm()` reads 3, which is the pending record's `to` count: it returns `rearmed`, `group_operators` holds both keys, and a message from `SECOND_HEX` is processed.
17. **lost reply on a remove, then rearm:** start with `group_operators` both keys, `expected_members = "3"`, `member_count=3`; `"lost"` on `change_membership("remove", "b")` latches; `rearm()` reads 2 (`to`): `group_operators == [OPERATOR_HEX]` and a message from `SECOND_HEX` is dropped. A variant where the fake did **not** apply the change (`"hang"`): `rearm()` reads 3 (`from`): `group_operators` keeps both keys.
18. **rearm reconciliation without a record:** latched, no pending record, `group_operators` absent or out of step:
   - the count equals 1 + the confirmed keys: those stay;
   - otherwise, the count equals 1 + the policy operators: all of them become confirmed (the operator asserts it by running `rearm`; audit `{"action": "rearm", "operators": "policy"}`);
   - otherwise refused ("the group has 3 members but policy.toml lists 1 operator…"), nothing changed, still latched.
   Covers Task 3's scenario 6 (count 3, two policy operators → rearmed, both confirmed; count 2 → refused).
19. **restart after a commit:** commit the add of `b` as in (1), close the daemon, and start a new `Admind` on the same state directory with the same two-operator settings: `b` is authorised from `group_operators` (its message is processed) without any rearm. A second restart with a policy that no longer lists `b`: `b`'s message is dropped (policy makes a key eligible; the group makes it authorised).

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ModuleNotFoundError: heterodyne.admind.membership`.

- [ ] **Step 3: Implement `membership.py`.**

```python
"""Membership changes as journaled transitions (ADR 0001 §8, revision 13). Pure; the daemon acts."""

import json
from dataclasses import asdict, dataclass
from typing import Literal

Reported = Literal["ok", "failed", "unknown"]
Outcome = Literal["commit", "abort", "latch"]


@dataclass(frozen=True)
class Pending:
    op: Literal["add", "remove"]
    name: str
    member_hex: str
    from_count: int
    to_count: int
    started: str

    def dump(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def load(text: str) -> "Pending":
        raw = json.loads(text)
        return Pending(raw["op"], raw["name"], raw["member_hex"], int(raw["from_count"]),
                       int(raw["to_count"]), raw["started"])


def settle(reported: Reported, count: int | None, pending: Pending) -> Outcome:
    """Commit only on reported success with the `to` count; abort only on reported failure with the
    `from` count; anything else latches. A count alone never confirms a change (step 4)."""
    if reported == "ok" and count == pending.to_count:
        return "commit"
    if reported == "failed" and count == pending.from_count:
        return "abort"
    return "latch"
```

- [ ] **Step 4: Implement the control client.** In `control.py`:

```python
class PeerError(ControlError):
    """wn-agent answered with an `error` frame: it reported failure (B4). Every other ControlError
    (timeout, closed socket, malformed frame) leaves the outcome unknown."""


class MembershipUpdated(msgspec.Struct, frozen=True):
    group_id_hex: str
```

- `decode_head` raises `PeerError(...)` (same arguments) instead of `ControlError` for an error frame.
- Add each error code recorded in `docs/spikes/S4b-membership.md` to `KNOWN_ERROR_CODES` (for example the codes for adding a present member and removing an absent one).
- Add the two client methods:

```python
    async def group_member_add(self, account: str, group: str, members: list[str]) -> MembershipUpdated:
        return await self.call({"type": "group_member_add", "account_id_hex": account, "group_id_hex": group,
                                "members": members, "initial_admins": []},
                               "group_membership_updated", MembershipUpdated)

    async def group_member_remove(self, account: str, group: str, members: list[str]) -> MembershipUpdated:
        return await self.call({"type": "group_member_remove", "account_id_hex": account,
                                "group_id_hex": group, "members": members},
                               "group_membership_updated", MembershipUpdated)
```

- [ ] **Step 5: Implement the fake.** In `FakeWnAgent.__init__`:

```python
        self.membership_mode = "ok"         # ok, fail, ok-no-count, fail-count, hang, gated
        self.membership_gate = asyncio.Event()      # "gated": the request waits until the test sets it
        self.info_gate: asyncio.Event | None = None # when set to an Event, group_info waits for it
```

and the existing `group_info` branch of `_handle` starts with `if self.info_gate is not None: await self.info_gate.wait()`.

In `_handle`:

```python
            elif kind in ("group_member_add", "group_member_remove"):
                await self._membership(writer, rid, kind, req)
```

```python
    async def _membership(self, writer: asyncio.StreamWriter, rid: str, kind: str, req: dict[str, Any]) -> None:
        mode = self.membership_mode
        delta = len(req["members"]) * (1 if kind == "group_member_add" else -1)
        if mode == "hang":
            await asyncio.sleep(3600)
        if mode == "lost":                  # the change happens; the reply never comes
            self.member_count += delta
            await asyncio.sleep(3600)
        if mode == "gated":
            await self.membership_gate.wait()
        if mode in ("ok", "gated", "fail-count"):
            self.member_count += delta
        if mode in ("fail", "fail-count"):
            await self._reply(writer, rid, {"type": "error", "code": "not_group_admin",
                                            "message": "refused", "retryable": False})
            return
        await self._reply(writer, rid, {"type": "group_membership_updated", "group_id_hex": self.group_id,
                                        "pending_welcome_count": 0})

    async def drop_subscriptions(self) -> None:
        """End every open subscription stream, as a wn-agent restart would: the daemon resubscribes."""
        for queue in list(self._subscribers):
            await queue.put({"type": "_close"})
```

In `_subscribe`, end the stream on that marker: after `event = await queue.get()`, add `if event.get("type") == "_close": return`.

(`"ok-no-count"` falls through to a success reply without changing the count.)

- [ ] **Step 6: Implement the daemon changes.**
  - `__init__`:

```python
        self.transition_lock = asyncio.Lock()   # one membership change or rearm at a time (B7)
        self.work_lock = asyncio.Lock()         # held by worker_loop for each operator message
        self.changing = False                   # a transition holds dispatch and posting (B7)
        self.membership_epoch = 0               # bumped when a transition or rearm begins; see check_group
        self.group_events = 0                   # membership events seen; rearm refuses if one arrives meanwhile
        self.reading = False                    # the subscription's events are being read (B22)
```

  - `confirm_observing` ends by setting `self.reading = True`, on both paths that return (a passing check, or a failing one while latched): `subscribe` reads events as soon as it returns, with no await in between. Its body becomes:

```python
        self.acked = True       # check_group observes on success, here and on every later passing check
        if not await self.check_group() and not self.latched():
            raise ControlError("group could not be re-verified", "unverified", True)
        self.reading = True     # acknowledged but not observing if latched; a rearm then restores it
```

  - `inbound_loop` sets `self.reading = False` next to `self.acked = False` when a subscription ends.

  - `on_event`: the `GroupStateChanged` branch starts with `self.group_events += 1`, before it judges the event.

  - `may_post()` and `authorised()` both gain `and not self.changing`, and `_flush`'s first gate becomes `if self.latched() or not self.group_ok or not self.observing or self.changing:`.
  - `worker_loop` holds `work_lock` for each message:

```python
    async def worker_loop(self) -> None:
        """Process operator messages one at a time, in arrival order, apart from the subscription reader.
        Each holds `work_lock`: a membership transition waits for the message in hand, and the next one
        waits for the transition (B7), so a message is never denied just because a transition ran."""
        while True:
            event = await self.work.get()
            try:
                async with self.work_lock:
                    await self.on_message(event)
            except Exception as exc:  # noqa: BLE001 - one bad message must not end the worker
                self.audit.write("handler", action="failed", error=type(exc).__name__)
```

  - `check_group` takes no lock, so the subscription reader (`confirm_observing`) never waits for a transition. It defers instead. The start of its body becomes:

```python
        generation = self.sub_gen
        acked_at_start = self.acked
        epoch = self.membership_epoch
        if self.changing:
            self.audit_quietly("guard", action="group-check-deferred")
            return False        # the transition reads the count itself and settles (B7)
        try:
            info = await self.client.group_info(self.account, self.group)
        except ControlError as exc:
            if self.changing or epoch != self.membership_epoch:
                self.audit_quietly("guard", action="group-check-deferred")
                return False
            self.group_ok = False
            self.audit.write("guard", action="group-check-failed", code=exc.code)   # never the peer's detail
            return False
        if self.changing or epoch != self.membership_epoch:
            # A transition or rearm began while this check waited: its count may be from either side of
            # the change, so it decides nothing; group_ok stays as it is.
            self.audit_quietly("guard", action="group-check-deferred")
            return False
        verdict = guard.judge_member_count(info.member_count, self.expected_members())
```

  The rest of `check_group` is unchanged. A deferred check during `confirm_observing` raises "unverified" as any failed check does: the subscription is retried, and the transition, which saw `sub_gen` change, latches (B5).
  - `run()`, after the B3 migration and before the Task 4 outbox upgrade and `recover()`:

```python
        if self.store.get("membership_pending") is not None:      # B15: a count can't tell which change
            self.latch("a membership change was interrupted; check the group's members in your client, "
                       "then run `admind rearm` on the host")
```

  - New methods:

```python
    def membership_refusal(self) -> str | None:
        """Why a membership change can't start (or go on) now; None if it can."""
        if self.latched():
            return "admind is latched; check the group's members, then run `admind rearm` first."
        if self.store.get("membership_pending") is not None:
            return "a membership change is already pending; run `admind rearm`."
        if not (self.observing and self.group_ok):
            return ("admind is not watching the group yet (its membership subscription is not verified); "
                    "nothing changed. Try again shortly.")
        return None

    async def change_membership(self, op: str, name: str) -> tuple[str, str]:
        """`admind operators add|remove NAME` (ADR 0001 §8, revision 13; B7, B18). Returns (result, message);
        the message is admind's own wording plus the operator's policy name (the caller redacts it)."""
        async with self.transition_lock:
            refusal = self.membership_refusal()
            if refusal is not None:
                return "refused", refusal
            try:
                loaded = {o.name: o for o in self.load_operators()}
            except Exception as exc:  # noqa: BLE001 - ConfigError, or a policy file that can't be read
                self.audit.write("membership", action="refused", why="policy-unreadable", error=type(exc).__name__)
                return "refused", "policy.toml could not be read; nothing changed."
            if op == "add":
                target = loaded.get(name)
                if target is None:
                    return "refused", (f"{name} is not an operator with a marmot_npub in policy.toml; add it "
                                       "there first. Nothing changed.")
                if target.hex in self.operators:
                    return "refused", f"{name} is already an operator in the group; nothing changed."
                member_hex = target.hex
            else:
                found = [key for key, known in self.operators.items() if known == name]
                if not found:
                    return "refused", f"{name} is not an operator in the group; nothing changed."
                member_hex = found[0]
            policy = tuple(loaded.values())
            # Step 1: hold. The message in hand finishes first; then nothing new is dispatched or sent, and
            # a paste or a send already under way is waited for, before the count is read.
            async with self.work_lock:
                self.changing = True
                self.membership_epoch += 1
                journaled = settled = False
                try:
                    async with self.dispatch_lock:
                        pass
                    async with self.send_lock:
                        pass
                    generation = self.sub_gen
                    refusal = self.membership_refusal()     # a latch or a lost subscription meanwhile
                    if refusal is not None:
                        settled = True
                        return "refused", refusal
                    try:
                        count = (await self.client.group_info(self.account, self.group)).member_count
                    except ControlError as exc:
                        settled = True
                        return "refused", f"could not read the group's member count ({exc.code}); nothing changed."
                    refusal = self.membership_refusal() or (
                        None if generation == self.sub_gen else
                        "the membership subscription was replaced; nothing changed. Try again.")
                    if refusal is not None:
                        settled = True
                        return "refused", refusal
                    expected = self.expected_members()
                    if count != expected:
                        self.latch(f"group has {count} members, expected {expected}")
                        settled = True
                        return "latched", f"the group has {count} members, not the expected {expected}; latched."
                    to = expected + 1 if op == "add" else expected - 1
                    if to < 2:
                        settled = True
                        return "refused", "the last operator can't be removed."
                    pending = membership.Pending("add" if op == "add" else "remove", name, member_hex, expected,
                                                 to, now())
                    journaled = True        # from here an exception latches (B18)
                    self.store.set("membership_pending", pending.dump())
                    self.audit.write("membership", action="pending", op=pending.op, operator=name,
                                     from_count=expected, to_count=to)
                    reported = await self.member_call(pending)
                    try:
                        after: int | None = (await self.client.group_info(self.account, self.group)).member_count
                    except ControlError:
                        after = None
                    outcome = membership.settle(reported, after, pending)
                    if self.latched() or not self.observing or generation != self.sub_gen:
                        outcome = "latch"   # an event, a latch or a lost subscription during it (step 3, B5)
                    result = self.settle_membership(pending, outcome, reported, after, policy)
                    settled = True
                    return result
                except BaseException:
                    if journaled and not settled:
                        with contextlib.suppress(Exception):
                            self.latch("a membership change failed before it was settled; check the group's "
                                       "members in your client, then run `admind rearm` on the host")
                    raise
                finally:
                    if settled or not journaled or self.latched():
                        self.changing = False
                    else:   # pending, unsettled and not latched: hold until rearm or the restart latch (B15)
                        self.audit_quietly("membership", action="held-unsettled")
                    self.wake.set()

    async def member_call(self, pending: membership.Pending) -> membership.Reported:
        try:
            if pending.op == "add":
                await self.client.group_member_add(self.account, self.group, [pending.member_hex])
            else:
                await self.client.group_member_remove(self.account, self.group, [pending.member_hex])
        except PeerError as exc:
            self.audit.write("membership", action="refused-by-wn-agent", code=exc.code)
            return "failed"
        except ControlError as exc:
            self.audit.write("membership", action="no-answer", code=exc.code)
            return "unknown"
        return "ok"

    def settle_membership(self, pending: membership.Pending, outcome: membership.Outcome, reported: str,
                          after: int | None, policy: tuple[Operator, ...]) -> tuple[str, str]:
        """Apply the outcome. A commit writes the count, the confirmed operators (B21) and the notice and
        clears the record in one transaction, then authorises from them (B2)."""
        if outcome == "commit":
            verb = "added to" if pending.op == "add" else "removed from"
            confirmed = applied(self.confirmed_operators() or set(), pending)
            with self.store.transaction():
                self.store.set("expected_members", str(pending.to_count))
                self.store.set("group_operators", json.dumps(sorted(confirmed)))
                self.store.delete("membership_pending")
                self.post(f"membership:{pending.started}:{pending.op}",
                          f"Operator {pending.name} was {verb} the group.", None)
            self.policy_operators = policy
            self.authorise(confirmed)
            self.audit_quietly("membership", action="committed", op=pending.op, operator=pending.name,
                               member_count=pending.to_count)
            return "committed", f"Operator {pending.name} was {verb} the group ({pending.to_count} members)."
        if outcome == "abort":
            self.store.delete("membership_pending")
            self.audit_quietly("membership", action="aborted", op=pending.op, operator=pending.name)
            return "aborted", "wn-agent refused the change and the member count is unchanged; nothing changed."
        self.latch(f"membership change {pending.op} ended unconfirmed (reported {reported}, "
                   f"count {'unknown' if after is None else after}, expected {pending.to_count})")
        return "latched", ("the change could not be confirmed, so admind latched. Check the group's members "
                           "in your client, then run `admind rearm` on the host.")

    async def rearm(self) -> tuple[str, str]:
        """`admind rearm`: trust the current member count and clear the latch, any pending change and a
        held transition (§8: "takes the current member count as trusted"). It also reconciles which
        operators are confirmed in the group (B21), and refuses, changing nothing, if the count can't be
        reconciled with them or if the group changed while the count was read (B22)."""
        async with self.transition_lock:
            if not self.reading:
                # Acknowledged is not enough: until confirm_observing returns, events wait unread in the
                # stream, and a resubscription would drop them (Codex r3 finding 3).
                return "refused", ("admind is not reading the group's events right now, so a change during "
                                   "rearm could be missed; nothing changed. Try again shortly.")
            try:
                policy = self.load_operators()
            except Exception as exc:  # noqa: BLE001 - ConfigError, or a policy file that can't be read
                self.audit.write("guard", action="rearm-refused", why="policy-unreadable", error=type(exc).__name__)
                return "refused", "policy.toml could not be read; nothing changed."
            generation, events, latched_before = self.sub_gen, self.group_events, self.store.get("latched")
            try:
                count = (await self.client.group_info(self.account, self.group)).member_count
            except ControlError as exc:
                return "refused", f"could not read the group's member count ({exc.code}); nothing changed."
            if (not self.reading or generation != self.sub_gen or events != self.group_events
                    or self.store.get("latched") != latched_before):
                self.audit_quietly("guard", action="rearm-refused", why="changed-meanwhile")
                return "refused", ("the group changed while rearm read it (a membership event, a new latch or "
                                   "a resubscription); nothing changed. Check the group's members, then run "
                                   "`admind rearm` again.")
            if count < 2:
                return "refused", f"the group has {count} member(s) and no operator; nothing changed."
            confirmed, how = reconcile(count, self.confirmed_operators(), self.store.get("membership_pending"),
                                       policy)
            if confirmed is None:
                self.audit_quietly("guard", action="rearm-refused", why="operators-unknown", member_count=count,
                                   policy_operators=len(policy))
                return "refused", (f"the group has {count} members but policy.toml lists {len(policy)} "
                                   f"operator(s) and admind has confirmed {how}; make policy.toml's operators "
                                   "match the group's members, then run `admind rearm` again. Nothing changed.")
            previous = self.store.get("latched")
            with self.store.transaction():
                self.store.set("expected_members", str(count))
                self.store.set("group_operators", json.dumps(sorted(confirmed)))
                self.store.delete("membership_pending")
                self.store.delete("latched")
            self.policy_operators = policy
            self.authorise(confirmed)
            self.changing = False           # a transition held unsettled (B18) ends here
            self.membership_epoch += 1      # a check begun before this one judged against the old count
            self.audit_quietly("guard", action="rearm", member_count=count, previous=previous, operators=how)
        await self.check_group()
        self.wake.set()
        return "rearmed", f"Cleared the latch; the trusted member count is now {count}."
```

  - Module-level helpers (pure; unit-tested in the same file with a table like `test_settle`):

```python
def applied(confirmed: set[str], pending: membership.Pending) -> set[str]:
    """The confirmed keys once `pending` took effect."""
    return confirmed | {pending.member_hex} if pending.op == "add" else confirmed - {pending.member_hex}


def reconcile(count: int, confirmed: set[str] | None, pending_raw: str | None,
              policy: tuple[Operator, ...]) -> tuple[set[str] | None, str]:
    """Which operators rearm confirms for a trusted `count` (B21). Returns (keys, how), or (None, what
    admind had) when the count can't be reconciled. A pending change whose `to` count matches took effect;
    one whose `from` count matches did not. Then the confirmed keys stand if the count fits them; else,
    if the count fits every policy operator, the operator's rearm asserts they are all in the group."""
    keys = set() if confirmed is None else set(confirmed)
    if pending_raw is not None:
        pending = membership.Pending.load(pending_raw)
        if count == pending.to_count:
            keys = applied(keys, pending)
    eligible = {o.hex for o in policy}
    keys &= eligible
    if confirmed is not None and count == 1 + len(keys):
        return keys, "confirmed"
    if count == 1 + len(eligible):
        return eligible, "policy"
    return None, f"{len(keys)}"
```

  (`keys &= eligible` drops a confirmed key whose operator left `policy.toml`: it can no longer be authorised, and the count check then tells whether it is still in the group.)

  Imports: `json`, `from heterodyne.admind import membership`, `from heterodyne.admind.settings import Operator` and `from heterodyne.marmot.control import PeerError`.

  Lock order, which keeps this free of deadlock: `transition_lock` → `work_lock` → `dispatch_lock` (briefly) → `send_lock` (briefly). `worker_loop` takes `work_lock` → `dispatch_lock` (`!interrupt`, `!new`, `flush`); `outbox_pass` calls `flush` before it takes `send_lock`, never under it; hooks take only `dispatch_lock`. Nothing that holds `dispatch_lock` or `send_lock` waits for `work_lock` or `transition_lock`.

  The latch reasons above are built only from fixed words, numbers and the allowlisted `op`, as `latch()` requires. The operator `name` appears only in the audit (redacted, B16), the returned message (redacted by `on_ctl`, Task 6) and the posted notice (redacted by `post`).

- [ ] **Step 7: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): journaled membership transitions and rearm (ADR r13 §8)"
```

Review against the five transition steps, the latch, `rearm`, and B5, B7 and B18.

---

### Task 6: Host control socket and CLI

**Files:**
- Create: `src/heterodyne/admind/ctl.py`
- Modify: `src/heterodyne/admind/daemon.py` (`run`, `on_ctl`), `src/heterodyne/admind/cli.py`
- Test: `tests/test_admind_r13_ctl.py`

**Interfaces:**
- Consumes: `Admind.change_membership` and `Admind.rearm` (Task 5).
- Produces:
  - `ctl.CTL_SOCKET = "ctl.sock"`;
  - `ctl.CtlRequest(op: Literal["add", "remove", "rearm"], name: str | None = None)`;
  - `ctl.CtlReply(result: str, message: str)`;
  - `ctl.CtlServer(path, handler, audit)`, with `start()` and `close()`;
  - `ctl.request(path, req, timeout=300.0) -> CtlReply` (B6: a transition first waits for the operator message in hand);
  - the CLI subcommands `admind operators add NAME`, `admind operators remove NAME` and `admind rearm`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_ctl.py
import asyncio
import os
import stat
from pathlib import Path

from heterodyne.admind import ctl
from heterodyne.admind.audit import Audit
from heterodyne.marmot.nip19 import hex_to_npub


def test_round_trip_and_mode(tmp_path: Path) -> None:
    seen: list[ctl.CtlRequest] = []

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        seen.append(req)
        return ctl.CtlReply("committed", "ok")

    async def body() -> None:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            reply = await ctl.request(path, ctl.CtlRequest("add", "b"))
            assert reply == ctl.CtlReply("committed", "ok")
            # Any policy name is allowed (finding 13); the daemon looks it up exactly.
            odd = await ctl.request(path, ctl.CtlRequest("add", "../x"))
            assert odd == ctl.CtlReply("committed", "ok")
            for bad in (ctl.CtlRequest("add", "a\x1b[2Jb"), ctl.CtlRequest("add", "x" * 129),
                        ctl.CtlRequest("add", ""), ctl.CtlRequest("add"), ctl.CtlRequest("rearm", "b")):
                assert (await ctl.request(path, bad)).result == "refused"
        finally:
            await server.close()

    asyncio.run(body())
    assert seen == [ctl.CtlRequest("add", "b"), ctl.CtlRequest("add", "../x")]
    text = (tmp_path / "s" / "audit.jsonl").read_text()
    assert "\\u001b" not in text and "x" * 129 not in text      # refused names are never logged


def test_handler_error_is_fixed_wording(tmp_path: Path) -> None:
    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        raise RuntimeError("detail that must not reach the host")

    async def body() -> ctl.CtlReply:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            return await ctl.request(path, ctl.CtlRequest("rearm"))
        finally:
            await server.close()

    reply = asyncio.run(body())
    assert reply == ctl.CtlReply("failed", "admind hit an internal error; see the audit log.")
    assert '"error": "RuntimeError"' in (tmp_path / "s" / "audit.jsonl").read_text()
    assert "must not reach" not in (tmp_path / "s" / "audit.jsonl").read_text()


def test_names_are_redacted_in_the_audit(tmp_path: Path) -> None:
    npub = hex_to_npub("c3" * 32)

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        return ctl.CtlReply("refused", "no")

    async def body() -> None:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            await ctl.request(path, ctl.CtlRequest("add", npub))
        finally:
            await server.close()

    asyncio.run(body())
    assert npub not in (tmp_path / "s" / "audit.jsonl").read_text()


def test_no_daemon(tmp_path: Path) -> None:
    async def body() -> None:
        try:
            await ctl.request(tmp_path / ctl.CTL_SOCKET, ctl.CtlRequest("rearm"), timeout=1)
        except ctl.CtlUnavailable:
            return
        raise AssertionError("expected CtlUnavailable")

    asyncio.run(body())
```

Daemon test (shared harness, in the same file): `on_ctl` returns `redact(message)`. With a `change_membership` stub that returns `("refused", f"{NPUB} is not an operator")`, `await h.daemon.on_ctl(ctl.CtlRequest("add", NPUB))` returns a message without the npub. With a `flush` stub that raises `OSError` after a `committed` stub result, `on_ctl` still returns `committed`, and the audit has `{"kind": "ctl", "action": "flush-failed"}`.

CLI tests in `tests/test_admind_cli.py` style:
- with a stub server on the socket path, `main(["operators", "add", "b"])` prints the reply's message and exits 0 for `committed`, 1 otherwise;
- `main(["rearm"])` with no server exits 1 and prints "admind is not running";
- `main(["operators", "add"])` exits 2 with the fixed argparse message.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ModuleNotFoundError: heterodyne.admind.ctl`.

- [ ] **Step 3: Implement `ctl.py`.**

```python
"""admind's host control socket (ADR 0001 §8, revision 13; plan 2b B6).

`admind operators add|remove NAME` and `admind rearm` run on the host as the service user and ask the
running daemon, which owns the wn-agent connection. One JSON request per connection, one JSON reply.
The socket is 0600 inside the 0700 state directory; anything able to use it can already act as admind.
"""

import asyncio
import contextlib
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.admind.audit import Audit
from heterodyne.admind.settings import MAX_NAME, NAME_CONTROLS as CONTROL   # one contract with policy (B2)
from heterodyne.admind.store import private_dir

CTL_SOCKET = "ctl.sock"
MAX_REQUEST = 4096
READ_SECONDS = 5.0
INTERNAL_ERROR = "admind hit an internal error; see the audit log."


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["add", "remove", "rearm"]
    name: str | None = None


class CtlReply(msgspec.Struct, frozen=True):
    result: str
    message: str


class CtlUnavailable(Exception):
    """No daemon is listening (or it did not answer)."""


Handler = Callable[[CtlRequest], Awaitable[CtlReply]]


class CtlServer:
    def __init__(self, path: Path, handler: Handler, audit: Audit) -> None:
        self.path = path
        self.handler = handler
        self.audit = audit
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISSOCK(os.lstat(self.path).st_mode):    # a stale socket from an earlier run
                self.path.unlink()
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.path),
                                                      limit=MAX_REQUEST + 1)
        os.chmod(self.path, 0o600)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    @staticmethod
    def check(req: CtlRequest) -> str | None:
        """Why a request is refused before it is logged or handled; None if it may go on. Any policy
        name is allowed (the daemon looks it up exactly), apart from control characters and length."""
        if req.op == "rearm":
            return None if req.name is None else "rearm takes no NAME"
        if req.name is None or not 1 <= len(req.name) <= MAX_NAME or CONTROL.search(req.name):
            return f"NAME must be an operator name from policy.toml (1-{MAX_NAME} printable characters)"
        return None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_SECONDS)
                req = msgspec.json.decode(line, type=CtlRequest)
            except (TimeoutError, ValueError, msgspec.DecodeError):
                reply = CtlReply("refused", "malformed request")
            else:
                refusal = self.check(req)
                if refusal is not None:
                    reply = CtlReply("refused", refusal)
                else:
                    self.audit.write("ctl", op=req.op, name=req.name)     # redacted centrally (B16)
                    try:
                        reply = await self.handler(req)
                    except Exception as exc:  # noqa: BLE001 - fixed wording; the type goes to the audit
                        self.audit.write("ctl", action="handler-failed", op=req.op, error=type(exc).__name__)
                        reply = CtlReply("failed", INTERNAL_ERROR)
            writer.write(msgspec.json.encode(reply) + b"\n")
            await writer.drain()
        except Exception as exc:  # noqa: BLE001 - one bad client must not end the server
            self.audit.write("ctl", action="failed", error=type(exc).__name__)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def request(path: Path, req: CtlRequest, timeout: float = 300.0) -> CtlReply:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 5)
    except (OSError, TimeoutError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    try:
        writer.write(msgspec.json.encode(req) + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return msgspec.json.decode(line, type=CtlReply)
    except (OSError, TimeoutError, msgspec.DecodeError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
```

`check` runs before the audit, so a name with terminal controls, an oversized one, or a stray `rearm` argument is never logged. Any other name is accepted, because `policy.toml` accepts any string as a name (finding 13): the daemon looks it up exactly, and the audit (B16) and the reply (`on_ctl`) redact it.

- [ ] **Step 4: Implement the daemon side.** In `Admind`:

```python
    async def on_ctl(self, req: ctl.CtlRequest) -> ctl.CtlReply:
        """A host command. The reply's message names the operator, so it is redacted like any output."""
        if req.op == "rearm":
            result, message = await self.rearm()
        else:
            result, message = await self.change_membership(req.op, req.name or "")
        if result in ("committed", "rearmed"):
            try:
                await self.flush()      # prompts held during the transition may go now
            except Exception as exc:  # noqa: BLE001 - the change is settled; the worker flushes later
                self.audit_quietly("ctl", action="flush-failed", error=type(exc).__name__)
        return ctl.CtlReply(result, redact(message))
```

In `run()`, after `await server.start()`. The control server may start before the subscription is observing: `membership_refusal()` (Task 5) refuses until it is, and `rearm` needs only the count:

```python
            control = ctl.CtlServer(self.s.state_dir / ctl.CTL_SOCKET, self.on_ctl, self.audit)
            await control.start()
```

Close it in the `finally` next to `server.close()`. Initialise `control = None` before the `try`, and close it only if it is set.

- [ ] **Step 5: Implement the CLI.** In `build_parser`:

```python
    ops = sub.add_parser("operators", help="add or remove a group member (an operator in policy.toml)")
    ops.add_argument("action", choices=["add", "remove"])
    ops.add_argument("name")
    sub.add_parser("rearm", help="trust the group's current member count and clear the latch "
                                 "(check the members in your client first)")
```

Replace the old `rearm(s, store, audit)` with:

```python
def ask_daemon(s: AdmindSettings, req: ctl.CtlRequest) -> int:
    try:
        reply = asyncio.run(ctl.request(s.state_dir / ctl.CTL_SOCKET, req))
    except ctl.CtlUnavailable:
        print("admind is not running (or did not answer). Start it first; it starts latched if a "
              "membership change was interrupted.", file=sys.stderr)
        return 1
    print(reply.message)
    return 0 if reply.result in ("committed", "rearmed") else 1
```

`_with_settings` routes `rearm` to `ask_daemon(s, ctl.CtlRequest("rearm"))`, and `operators` to `ask_daemon(s, ctl.CtlRequest(args.action, args.name))`. Pass `args` through `_with_settings(args)` instead of just the command name. The `rearm` printout now comes from the daemon. Before running it, the operator checks the group's members in their client: the reminder stays in `--help` and in `docs/admind.md`.

- [ ] **Step 6: Run the gate and update `test_admind_cli.py`'s old rearm tests to the new behaviour. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): host control socket; operators add|remove and rearm via the daemon"
```

---

### Task 7: Summarizer runner and backstop renderer

**Files:**
- Create: `src/heterodyne/admind/summarize.py`, `src/heterodyne/admind/backstop.py`
- Modify: `src/heterodyne/agents/claude_code.py`, `src/heterodyne/admind/settings.py`, `src/heterodyne/defaults/defaults.toml`, `tests/fakes/settings.py`
- Test: `tests/test_admind_r13_summary.py`

**Interfaces:**
- Consumes: `redact` (Task 2) and the argv recorded by Task 1.
- Produces:
  - `claude_code.headless_argv(binary, profile) -> list[str]`;
  - `AdmindSettings` gains `summarizer: Mapping[str, Any] | None`, `summarizer_binary: str | None`, `reply_verbatim_lines: int` and `reply_verbatim_chars: int`;
  - from `summarize`: `SUMMARY_TIMEOUT = 60.0`, `FOOTER`, `SummaryFailed(reason)`, `needs_summary(text, lines, chars) -> bool`, and `async summarize(argv, cwd, reply, timeout) -> str`;
  - from `summarize` also `MAX_LINES = 10`, `MAX_CHARS = 2000` and `MAX_BYTES = 8000`;
  - from `backstop`: `BATCH_SECONDS = 60.0`, `BATCH_MAX_CHARS = 60000`, `TITLE`, `CUT`, `Entry(origin, text)`, `first_words(text) -> str`, `origin(operator, at_iso, words) -> str`, `collapse(lines) -> list[str]`, `render(entries) -> str` and `fit(text, limit) -> str`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_summary.py
import asyncio
import stat
from pathlib import Path

import pytest

from heterodyne.admind import backstop, summarize
from heterodyne.admind.redact import redact
from heterodyne.agents.claude_code import headless_argv
from heterodyne.marmot.nip19 import hex_to_npub


def script(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "summ.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return [str(p)]


def test_headless_argv_has_no_tools_hooks_or_user_settings() -> None:
    argv = headless_argv("claude", {"adapter": "claude-code", "model": "m1", "args": ["--evil"]})
    assert argv[:2] == ["claude", "-p"] and "--evil" not in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert '"disableAllHooks": true' in argv[argv.index("--settings") + 1]
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv


def test_needs_summary() -> None:
    assert not summarize.needs_summary("a\n" * 8, 8, 800)
    assert summarize.needs_summary("a\n" * 9, 8, 800)
    assert summarize.needs_summary("x" * 801, 8, 800)


def test_summary_gets_the_footer_and_reads_stdin(tmp_path: Path) -> None:
    argv = script(tmp_path, 'read -r first; echo "got: $first"')
    out = asyncio.run(summarize.summarize(argv, tmp_path / "w", "long reply", timeout=5))
    assert out.startswith("got: You summarize") and out.endswith(summarize.FOOTER)


def test_summarizer_gets_the_whole_redacted_reply(tmp_path: Path) -> None:
    # Larger than a pipe buffer, so stdin must be fed while stdout is read.
    reply = f"ask {hex_to_npub('c3' * 32)}\x1b[2J\n" + "y" * 200_000
    seen = tmp_path / "stdin.txt"
    out = asyncio.run(summarize.summarize(script(tmp_path, f'cat > "{seen}"; echo ok'), tmp_path / "w",
                                          reply, timeout=10))
    assert out == f"ok\n\n{summarize.FOOTER}"
    assert seen.read_text() == summarize.PROMPT.format(reply=redact(reply))
    assert "npub1" not in seen.read_text() and "\x1b" not in seen.read_text()


def test_prompt_keeps_questions_and_errors() -> None:
    assert "Quote verbatim, in full, every question" in summarize.PROMPT
    assert "every error it reports" in summarize.PROMPT


@pytest.mark.parametrize(("body", "reason"), [
    ("exit 3", "failed"), ("true", "empty"), ("sleep 5", "timeout"),
    ("i=0; while [ $i -lt 11 ]; do echo line; i=$((i+1)); done", "too-long"),     # 11 lines
    ("head -c 2001 /dev/zero | tr '\\0' x", "too-long"),                          # 2,001 characters
    ("head -c 100000 /dev/zero | tr '\\0' x; sleep 5", "too-long"),               # killed at 8,000 bytes
])
def test_failures(tmp_path: Path, body: str, reason: str) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(script(tmp_path, body), tmp_path / "w", "r", timeout=0.5))
    assert info.value.reason == reason


def test_not_configured(tmp_path: Path) -> None:
    with pytest.raises(summarize.SummaryFailed) as info:
        asyncio.run(summarize.summarize(None, tmp_path / "w", "r"))
    assert info.value.reason == "not-configured"


def test_collapse_counts_runs() -> None:
    assert backstop.collapse(["a", "a", "a", "b", "a"]) == ["a (×3)", "b", "a"]


def test_render_short_batch_whole() -> None:
    text = backstop.render([backstop.Entry("— op · 10:00 UTC · “hi”", "one\ntwo")])
    assert text == f"{backstop.TITLE}\n— op · 10:00 UTC · “hi”\none\ntwo"


def test_render_long_batch_head_and_tail() -> None:
    body = "\n".join(f"line {i}" for i in range(100))
    lines = backstop.render([backstop.Entry("H", body)]).splitlines()
    assert lines[0] == backstop.TITLE
    rest = lines[1:]
    assert len(rest) == 51 and rest[:10] == ["H"] + [f"line {i}" for i in range(9)]
    assert rest[10] == "… 51 lines skipped …"          # 101 lines (H + 100) - 50 shown
    assert rest[11:] == [f"line {i}" for i in range(60, 100)]


def test_counts_are_after_collapse() -> None:
    body = "\n".join(["same"] * 200)
    assert backstop.render([backstop.Entry("H", body)]) == f"{backstop.TITLE}\nH\nsame (×200)"


def test_fit_leaves_a_short_batch_alone() -> None:
    text = backstop.render([backstop.Entry("H", "one\ntwo")])
    assert backstop.fit(text, 4000) == text


def test_a_batch_of_long_lines_is_sent_whole_below_the_transport_limit() -> None:
    # Codex r2 finding 8: 49 lines of 300 characters are well under BATCH_MAX_CHARS; nothing is cut.
    text = backstop.render([backstop.Entry("H", "\n".join(f"{i:03d}" + "z" * 300 for i in range(49)))])
    assert backstop.fit(text, backstop.BATCH_MAX_CHARS) == text


def test_fit_shortens_each_line_to_keep_one_message() -> None:
    text = backstop.render([backstop.Entry("H", "\n".join(f"{i:03d}" + "z" * 300 for i in range(49)))])
    out = backstop.fit(text, 4000)
    lines = out.split("\n")
    assert len(out) <= 4000 and lines[0] == backstop.TITLE and len(lines) == 51
    assert lines[2].startswith("000") and lines[2].endswith(f"…(+{303 - 64} chars)")       # share: 4000 // 50 - 16


def test_fit_cuts_the_end_last() -> None:
    text = backstop.render([backstop.Entry("H", "\n".join(f"{i:03d}" + "w" * 100 for i in range(49)))] * 40)
    out = backstop.fit(text, 1000)
    assert len(out) <= 1000 and out.startswith(backstop.TITLE) and out.endswith(backstop.CUT)


def test_origin_and_first_words() -> None:
    assert backstop.first_words("please restart the gateway and then check the logs now") == \
        "please restart the gateway and then check the…"
    assert backstop.origin("op", "2026-10-02T09:15:00+00:00", "hi") == "— op · 09:15 UTC · “hi”"
    assert backstop.origin(None, "2026-10-02T09:15:00+00:00", None) == \
        "— terminal · 09:15 UTC · (no operator message)"
```

Settings tests, in the same file and in `test_admind_settings.py` style:
- `summarizer = "admin"` resolves `summarizer_binary == "claude"`;
- an unknown summarizer profile is a `ConfigError` naming `[admind] summarizer`;
- `reply_verbatim_lines = 0` is a `ConfigError`;
- the defaults are 8 and 800.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `ImportError: cannot import name 'backstop'`.

- [ ] **Step 3: Implement `headless_argv`.** In `claude_code.py` (if Task 1 recorded a different no-tools flag, use that flag here and in the test):

```python
def headless_argv(binary: str, profile: Mapping[str, Any]) -> list[str]:
    """A one-shot run that reads its prompt on stdin and prints text: no tools, no hooks, no MCP
    servers, no user or local settings, no saved session. The profile's `args` are not appended: they
    could re-enable tools (plan 2b B8)."""
    argv = [binary, "-p"]
    model = profile.get("model")
    if isinstance(model, str) and model:
        argv += ["--model", model]
    return argv + ["--tools", "", "--setting-sources", "project", "--settings", '{"disableAllHooks": true}',
                   "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"]
```

- [ ] **Step 4: Implement the settings.**
  - `ADMIND_KEYS` gains `"summarizer"`, `"reply_verbatim_lines"` and `"reply_verbatim_chars"`.
  - `defaults.toml` `[admind]` gains `reply_verbatim_lines = 8` and `reply_verbatim_chars = 800`.
  - Extract the profile-and-binary lookup in `resolve` into `_profile(cfg, name, key) -> tuple[Mapping[str, Any], str]`, with the same errors and `[admind] {key}` in the message. Use it for both `profile` and `summarizer`:

```python
    summarizer_name = admind.get("summarizer")
    summarizer, summarizer_binary = (None, None) if summarizer_name is None else \
        _profile(cfg, summarizer_name, "summarizer")
```

  - Pass `reply_verbatim_lines=_int(admind, "reply_verbatim_lines", 1, 200)` and `reply_verbatim_chars=_int(admind, "reply_verbatim_chars", 50, 60000)`.
  - In `tests/fakes/settings.py`, add `summarizer=None, summarizer_binary=None, reply_verbatim_lines=8, reply_verbatim_chars=800`.

- [ ] **Step 5: Implement `summarize.py`.**

```python
"""Summaries of long admin-agent replies (ADR 0001 §8, revision 13; plan 2b B8).

The summarizer is admind's own subprocess: headless, without tools or hooks, 60 s at most. Its input is
the reply after `redact` (redacted here as well, so no caller can skip it); its output is read in bounded
pieces, redacted again, must be non-empty and short, and gets admind's fixed footer. Any failure is a
`SummaryFailed` with a fixed reason word; the caller sends the reply to the backstop. Whatever happens,
the summarizer's process group is killed before `summarize` returns.
"""

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from heterodyne.admind.redact import redact
from heterodyne.admind.store import private_dir

SUMMARY_TIMEOUT = 60.0
MAX_LINES = 10                  # "about 8 lines", with two of slack (B8)
MAX_CHARS = 2000
MAX_BYTES = MAX_CHARS * 4       # stdout is read until this many bytes, then the process is killed
READ_CHUNK = 65536
FOOTER = "summary · reply `!details` for everything"
PROMPT = """You summarize an admin agent's reply for operators who read it on a phone.
Write at most 8 short lines of plain text. Quote verbatim, in full, every question the reply asks the
operators and every error it reports. Add nothing that is not in the reply. Do not add a closing line;
one is appended for you. The reply is everything between the two marker lines.
<<<REPLY
{reply}
REPLY>>>
"""


class SummaryFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason        # fixed words only: not-configured, not-run, failed, timeout, empty, too-long


def needs_summary(text: str, lines: int, chars: int) -> bool:
    return len(text.splitlines()) > lines or len(text) > chars


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


async def _collect(proc: asyncio.subprocess.Process, data: bytes) -> bytes:
    """Feed stdin while reading stdout, so neither pipe can fill and stall; stop past MAX_BYTES."""
    assert proc.stdin is not None and proc.stdout is not None
    stdin, stdout = proc.stdin, proc.stdout

    async def feed() -> None:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):     # it may stop reading early
            stdin.write(data)
            await stdin.drain()
            stdin.close()

    feeder = asyncio.create_task(feed())
    out = bytearray()
    try:
        while chunk := await stdout.read(READ_CHUNK):
            out += chunk
            if len(out) > MAX_BYTES:
                raise SummaryFailed("too-long")
        await proc.wait()
    finally:
        feeder.cancel()
        await asyncio.gather(feeder, return_exceptions=True)
    return bytes(out)


async def summarize(argv: list[str] | None, cwd: Path, reply: str, timeout: float = SUMMARY_TIMEOUT) -> str:
    if argv is None:
        raise SummaryFailed("not-configured")
    private_dir(cwd)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    except OSError:
        raise SummaryFailed("not-run") from None
    try:
        out = await asyncio.wait_for(_collect(proc, PROMPT.format(reply=redact(reply)).encode()), timeout)
    except TimeoutError:
        raise SummaryFailed("timeout") from None
    finally:
        _kill(proc)                 # the whole group: also anything it started and left running
        await proc.wait()           # reaped after SIGKILL; returns at once if it had exited
    if proc.returncode != 0:
        raise SummaryFailed("failed")
    text = redact(out.decode("utf-8", errors="replace")).strip()
    if not text:
        raise SummaryFailed("empty")
    if len(text.splitlines()) > MAX_LINES or len(text) > MAX_CHARS:
        raise SummaryFailed("too-long")
    return f"{text}\n\n{FOOTER}"
```

- [ ] **Step 6: Implement `backstop.py`.**

```python
"""The backstop batch: deterministic, drops nothing `!details` can't return (ADR 0001 §8, revision 13)."""

from dataclasses import dataclass

BATCH_SECONDS = 60.0
WHOLE_MAX = 50
HEAD = 10
TAIL = 40
TITLE = "⚠️ Replies batched (summaries unavailable) · reply `!details` for everything"
WORDS = 8
WORDS_CHARS = 60
CUT = "\n… (cut at the message size limit)"
BATCH_MAX_CHARS = 60000     # the largest message Task 1 confirmed is delivered whole (chunk_chars's maximum)


@dataclass(frozen=True)
class Entry:
    origin: str
    text: str


def first_words(text: str) -> str:
    words = text.split()
    shown = " ".join(words[:WORDS])
    if len(words) > WORDS or len(shown) > WORDS_CHARS:
        shown = shown[:WORDS_CHARS].rstrip() + "…"
    return shown


def origin(operator: str | None, at_iso: str, words: str | None) -> str:
    who = operator or "terminal"
    said = f"“{words}”" if words else "(no operator message)"
    return f"— {who} · {at_iso[11:16]} UTC · {said}"


def collapse(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        k = j - i + 1
        out.append(lines[i] if k == 1 else f"{lines[i]} (×{k})")
        i = j + 1
    return out


def render(entries: list[Entry]) -> str:
    lines: list[str] = []
    for e in entries:
        lines.append(e.origin)
        lines.extend(e.text.splitlines() or [""])
    lines = collapse(lines)
    if len(lines) > WHOLE_MAX:
        lines = lines[:HEAD] + [f"… {len(lines) - HEAD - TAIL} lines skipped …"] + lines[-TAIL:]
    return "\n".join([TITLE, *lines])


def fit(text: str, limit: int) -> str:
    """Keep a rendered batch to one message of at most `limit` characters (B10). The caller passes
    `BATCH_MAX_CHARS`, the transport's limit, so a batch the ADR's line rules produce is sent exactly as
    rendered unless it can't be delivered as one message at all (over 60,000 characters: 50 lines of over
    1,000 characters each). Only then is each line after the title cut to an equal share, with a count of
    what was cut, and if that is still too long, the end. `!details` on the batch returns every reply whole."""
    if len(text) <= limit:
        return text
    title, *lines = text.split("\n")
    share = max(20, limit // max(1, len(lines)) - 16)
    lines = [line if len(line) <= share else f"{line[:share]}…(+{len(line) - share} chars)" for line in lines]
    text = "\n".join([title, *lines])
    if len(text) <= limit:
        return text
    return text[: limit - len(CUT)] + CUT
```

  (`origin` takes the first words already redacted and shortened by `first_words`. `fit` runs on text that is already redacted: cutting it can only shorten a marker, never expose what it hid.)

- [ ] **Step 7: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): summarizer runner and backstop renderer (ADR r13 §8)"
```

---

### Task 8: The reply pipeline in the daemon

**Files:**
- Modify: `src/heterodyne/admind/store.py`, `src/heterodyne/admind/hook.py`, `src/heterodyne/admind/daemon.py`
- Test: `tests/test_admind_r13_replies.py`

**Interfaces:**
- Consumes: Tasks 2, 4 and 7. Task 4's `Admind.send_failed`, which this task extends.
- Produces:
  - Store methods:
    - `record_prompt(mid, operator, words)` and `prompt(mid) -> PromptRow | None`;
    - `add_turn(key, session, reply_to, origin, text, transcript, start, end, status) -> int`;
    - `turn(turn_id) -> TurnRow | None` and `turns_with_status(status) -> list[TurnRow]`;
    - `set_turn_status(turn_id, status)` and `add_to_batch(turn_id, batch_id)`;
    - `open_batch(at: float, seconds: float) -> int`, `due_batches(at: float, seconds: float) -> list[tuple[int, int]]` (batch and attempt), `batch_turns(batch_id) -> list[TurnRow]`, `close_batch(batch_id)` and `reopen_batch(batch_id, at: float)`;
    - `record_post(key, kind, turn_id, batch_id)` and `post_record(key) -> PostRow | None`;
    - `details_target(message_id: str | None) -> PostRow | None`.
  - Hook module: `transcript_size(ev: HookEvent) -> int | None`; `READ_WINDOW`, `MAX_RECORD`, `_lines`, `_is_prompt`, `_has_result`, `turn_records(path, start, end)` (Task 9 uses them too); and `reply_text(ev, start, end) -> str | None`, which replaces the tail-reading fallback (`last_assistant_text`, `_read_tail` and `MAX_TRANSCRIPT` are deleted).
  - Daemon:
    - constants `OFFSET_SECONDS = 2.0`, `SUMMARY_POLL = 5.0` and `EXTRACT_FAILED`;
    - `bounded_read(timeout, fn, *args)`, which replaces the `_extraction` slot with `_reader` (B20);
    - `transcript_offset(ev)`, `turn_span(busy, end)` and `end_turn(anchor, arrival)`;
    - `queue_backstop(turn_id)`, `summary_loop()`, `summarize_turn(row)`, `batch_loop()` and `close_due_batches()`;
    - `summary_wake: asyncio.Event`;
    - the replaceable attributes `summarizer_argv`, `summary_timeout`, `summary_poll`, `offset_timeout`, `batch_seconds`, `batch_poll` and `wallclock`.

- [ ] **Step 1: Write the failing tests.** Use the shared harness.
  - Set a fake summarizer with `h.daemon.summarizer_argv = script(...)` (the helper from Task 7's tests).
  - Set `h.daemon.batch_seconds = 0.3`, `h.daemon.batch_poll = 0.05` and `h.daemon.summary_poll = 0.1`.
  - A threaded reply comes from the fake claude's echo: `await h.say(text)` gets `echo: <text>` back, threaded to the prompt.
  - A terminal turn is a Stop put straight on the queue: `await h.daemon.hooks.put(h.event("Stop", sid, None, reply))`, where `sid = h.agent.session_id`.
  - "A batch arrives" means `await h.until(lambda: any(t.startswith(backstop.TITLE) for t in h.texts()))`.

  Scenarios:

1. **Short reply:** a short reply (≤8 lines, ≤800 chars) is posted verbatim, threaded to the prompt, with key `reply:<session>:<n>:0`. A `turns` row with status `verbatim` and a `posts` row of kind `verbatim` exist.
2. **Summarized reply:** a long reply (`await h.say("long " + "w" * 900)`) with the summarizer `echo "short summary"` is posted as one message, `"short summary\n\n" + FOOTER`, threaded to the prompt. The long text itself is never sent, and the turn's status is `summarized`.
3. **Summarizer failures:** the summarizer fails (`exit 1`), times out (`sleep 5` with `summary_timeout = 0.3`), returns nothing (`true`), or is not configured (`None`). Each case leads to one unthreaded message within `batch_seconds`, which:
   - starts with `backstop.TITLE`;
   - contains the origin header `— op · HH:MM UTC · “long wwww…”`;
   - contains the reply.

   The audit has `{"kind": "summary", "action": "failed", "reason": <word>}` and `{"kind": "backstop", "action": "posted"}`.
4. **One batch:** two failing long replies within one window produce **one** batch message holding both, in order, each under its own origin.
5. **A big batch is still one message, lines whole:** three failing replies of 100 lines × 200 characters each, with `chunk_chars` 4000, give exactly one batch message: the title, then exactly the first 10 and last 40 collapsed lines, each whole (no `…(+` marker), so `len(text) > 4000` (`chunk_chars` does not apply to a batch). `!details` on it returns every line of all three. A variant with 50 lines of 2,000 characters (over `BATCH_MAX_CHARS`) gives one message of at most 60,000 characters whose lines end with `…(+N chars)`.
6. **Restart with a summary pending:** a turn left `summarizing` (insert it with `add_turn` in `before=`) is summarized after startup, with no requeue step.
7. **Restart with a batch open:** in a first `run_with`, a failing reply is batched with `batch_seconds = 3600`, and the scenario ends before the batch is due. A second `run_with` on the same `tmp_path` sets `batch_seconds = 0.3` and opens nothing new; the batch, holding the first run's reply, is posted.
8. **Transient failure in the backstop itself:** wrap `h.store.open_batch` so its first call raises `sqlite3.OperationalError`. A failing summary is still batched and posted without a restart: the row stayed `summarizing`, and the next pass of `summary_loop` (`summary_poll` 0.1 s) succeeds.
9. **Recording a reply fails:** wrap `h.store.record_post` so its first call raises `sqlite3.OperationalError`. A short reply's first transaction rolls back, so its verbatim post is never sent. The second transaction records the turn straight into the backstop:
   - a batch arrives holding the reply;
   - the turn is idle afterwards (`busy` is cleared);
   - the audit has `{"kind": "reply", "action": "record-failed"}`.
10. **Delivery gives up on a summary:** set `h.daemon._sleep` to a no-op coroutine. Once the ready notice is delivered and the outbox is empty, set `h.fake.fail_sends = MAX_SEND_ATTEMPTS`, then send a long prompt (summarizer `echo "short summary"`). Every attempt to send the summary fails as retryable. Afterwards the summary row is `failed`, and in the same transaction its turn became `batched`. The batch arrives and holds the full reply.
11. **Extraction fails:** set `extract_timeout = 0.1`, and monkeypatch `heterodyne.admind.daemon.reply_text` with a function `(ev, start, end)` that waits on a `threading.Event` (at most 5 s; the test sets it at the end). Then put a Stop for the current turn without `last_assistant_message`. A batch arrives holding `EXTRACT_FAILED`, and the turn is idle. Variants without the monkeypatch, each also giving a batch holding `EXTRACT_FAILED` (never `NO_REPLY`): the transcript path is a symlink; the turn has no span (no `UserPromptSubmit` was seen for it).
12. **Transcript span:**
    - **Current turn:** write `<tmp>/<sid>.jsonl` with 100 bytes, then put a `UserPromptSubmit` for that path. Append 50 bytes, then put a current `Stop` with a reply. The turn row has `transcript_start == 100` and `transcript_end == 150`.
    - **Stale launch:** a Stop from a stale launch (`launch=None`) that carries its own text gets `transcript_start` and `transcript_end` both NULL.
    - **Turn changed during extraction:** hold extraction as in (11), with `extract_timeout = 5`. Run `h.daemon.on_stop(h.event("Stop", sid, path), validate=True)` as a task. While it waits, `await h.daemon.on_hook(h.event("UserPromptSubmit", sid, path, prompt="next"), validate=True)` starts another turn. Release the event. The Stop is audited `stale-stop-unrecoverable` and adds no `turns` row.
13. **Redaction:** a hex value in the reply never appears in any sent text.
14. **Delivery gives up on a batch (Codex r2 finding 7):** `h.daemon._sleep` is a no-op coroutine. A failing summary is batched; just before the batch is due, set `h.fake.fail_sends = MAX_SEND_ATTEMPTS`. The batch's outbox row (`batch:<id>.0`) becomes `failed`, and in the same transaction the batch is open again with `attempt == 1` and a fresh `opened_at`; the audit has `{"kind": "backstop", "action": "reopened"}`. Once `batch_seconds` passes again the batch arrives (key `batch:<id>.1`), holding the reply, and `!details` without a target returns it.
15. **Restart after a failed batch:** as (14), but with `batch_seconds = 3600` once the batch is reopened, end the first `run_with`. A second `run_with` on the same `tmp_path` with `batch_seconds = 0.3` posts the reopened batch with the reply.
16. **Fallback reads the whole turn (Codex r3 finding 2):** a `UserPromptSubmit` for `<tmp>/<sid>.jsonl`, then the test appends `said("Which unit, gateway or relay?")`, a tool call and result, and `said("Checked both.")`, then a current Stop without `last_assistant_message`. The reply (summarized or verbatim, per its size) and its `!details` record hold both texts in order.
17. **Arrival order across overlapping failures (Codex r5 finding 3):** `h.daemon._sleep` is a no-op coroutine, `batch_seconds = 2`, `summary_timeout = 5`, and the summarizer is `sleep 1; exit 1`. Send a long prompt A; once `h.store.turns_with_status("summarizing")` holds its turn, set `h.fake.fail_sends = MAX_SEND_ATTEMPTS` and send a short prompt B with `h.fake.message_event(...)` (not `h.say`, which waits for a delivered reply), whose verbatim reply fails delivery and joins the backstop first. A's summary then fails and joins second. The batch shows B's origin and reply before A's, and `!details` on the batch returns them in that order.

Unit tests of the fallback, in the same file (`append`, `prompt`, `tool`, `result` and `said` are the same small JSONL helpers Task 9's tests define; define them in this file too):

```python
def stop(path: Path, sid: str = "s") -> HookEvent:
    return HookEvent("Stop", sid, str(path), None, None)


def test_fallback_keeps_every_assistant_text_of_the_turn(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    start = append(t, [prompt("old"), said("old answer")])
    end = append(t, [prompt("check it"), said("Which unit?"), tool("Bash", command="ls"), result("a b"),
                     said("Done.")])
    append(t, [prompt("next"), said("next answer")])
    assert reply_text(stop(t), start, end) == "Which unit?\n\nDone."


def test_fallback_reads_a_record_larger_than_the_old_tail_limit(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), said("x" * (9 * 1024 * 1024))])
    assert reply_text(stop(t), 0, end) == "x" * (9 * 1024 * 1024)


def test_fallback_silent_turn_is_empty_not_a_failure(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), tool("Bash", command="true"), result("")])
    assert reply_text(stop(t), 0, end) == ""


def test_a_result_record_with_text_is_not_a_prompt(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    mixed = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"},
                                                     {"type": "text", "text": "note"}]}}
    end = append(t, [prompt("go"), tool("Bash", command="ls"), mixed, said("Final."), prompt("next"),
                     said("next answer")])
    assert reply_text(stop(t), 0, end) == "Final."


def test_a_changed_transcript_is_a_failure(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), said("hi")])
    assert reply_text(stop(t), 0, end + 100) is None              # shorter than the span: truncated
    with t.open("a") as fh:
        fh.write('{"type": "assistant", "message": {"content": [{"type": "text", "text": "par')
    assert reply_text(stop(t), 0, t.stat().st_size) is None       # the span ends inside a record


def test_a_corrupt_record_is_a_failure(tmp_path: Path) -> None:
    # Codex r5 finding 2: a complete record that can't be parsed fails the read, never a partial reply.
    for i, bad in enumerate(('{"type": "assistant", "message": {"content": [{"type": "te', "[1, 2]", "\udcff")):
        (tmp_path / str(i)).mkdir()
        t = tmp_path / str(i) / "s.jsonl"                     # the session's own name: else None anyway
        end = append(t, [prompt("go"), said("first answer")])
        assert reply_text(stop(t), 0, end) == "first answer"
        with t.open("a", errors="surrogateescape") as fh:
            fh.write(bad + "\n")
        assert reply_text(stop(t), 0, t.stat().st_size) is None


def test_fallback_failures_are_none(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), said("hi")])
    link = tmp_path / "l" / "s.jsonl"
    link.parent.mkdir()
    link.symlink_to(t)
    assert reply_text(stop(link), 0, end) is None                 # a symlink is never followed
    assert reply_text(stop(t), None, None) is None                # no span
    assert reply_text(stop(tmp_path / "missing" / "s.jsonl"), 0, 10) is None
    assert reply_text(stop(t, sid="other"), 0, end) is None       # not this session's transcript
```

(`HookEvent`'s fields are `hook_event_name, session_id, transcript_path, last_assistant_message, prompt`. Existing tests that monkeypatch `heterodyne.admind.daemon.reply_text` with `lambda ev: ...` take `(ev, start, end)` instead; `grep -rn "daemon.reply_text" tests/`. Tests of `last_assistant_text` in `tests/test_admind_agent.py` and `tests/test_admind_daemon.py` are deleted with it; the ones above replace them.)

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `AttributeError: 'Admind' object has no attribute 'summarizer_argv'`.

- [ ] **Step 3: Implement the store.** Append to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS prompts (
    message_id TEXT PRIMARY KEY, operator TEXT NOT NULL, received_at TEXT NOT NULL, words TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    session TEXT NOT NULL,
    reply_to TEXT,
    origin TEXT NOT NULL,
    text TEXT NOT NULL,
    transcript TEXT,
    transcript_start INTEGER,
    transcript_end INTEGER,
    status TEXT NOT NULL CHECK (status IN ('verbatim', 'summarizing', 'summarized', 'batched')),
    batch_id INTEGER,
    batch_seq INTEGER,              -- the order replies joined the backstop in, across batches (B10)
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'posted')),
    attempt INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS posts (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('verbatim', 'summary', 'batch')),
    turn_id INTEGER,
    batch_id INTEGER);
```

Row types and methods (each `@_locked`):

```python
@dataclass(frozen=True)
class PromptRow:
    operator: str
    received_at: str
    words: str


@dataclass(frozen=True)
class TurnRow:
    turn_id: int
    key: str
    session: str
    reply_to: str | None
    origin: str
    text: str
    transcript: str | None
    transcript_start: int | None
    transcript_end: int | None
    status: str
    batch_id: int | None


@dataclass(frozen=True)
class PostRow:
    key: str
    kind: str
    turn_id: int | None
    batch_id: int | None


_TURN = ("turn_id, key, session, reply_to, origin, text, transcript, transcript_start, transcript_end, status, "
         "batch_id")


def _opt_int(v: object) -> int | None:
    return None if v is None else int(cast(int, v))


def _opt_str(v: object) -> str | None:
    return None if v is None else str(v)


def _turn(r: tuple[object, ...]) -> TurnRow:
    return TurnRow(int(cast(int, r[0])), str(r[1]), str(r[2]), _opt_str(r[3]), str(r[4]), str(r[5]),
                   _opt_str(r[6]), _opt_int(r[7]), _opt_int(r[8]), str(r[9]), _opt_int(r[10]))


def _post(r: tuple[object, ...]) -> PostRow:
    return PostRow(str(r[0]), str(r[1]), _opt_int(r[2]), _opt_int(r[3]))
```

```python
    @_locked
    def record_prompt(self, message_id: str, operator: str, words: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO prompts(message_id, operator, received_at, words) "
                        "VALUES (?, ?, ?, ?)", (message_id, operator, now(), words))

    @_locked
    def prompt(self, message_id: str) -> PromptRow | None:
        r = self.db.execute("SELECT operator, received_at, words FROM prompts WHERE message_id = ?",
                            (message_id,)).fetchone()
        return None if r is None else PromptRow(str(r[0]), str(r[1]), str(r[2]))

    @_locked
    def add_turn(self, key: str, session: str, reply_to: str | None, origin: str, text: str,
                 transcript: str | None, start: int | None, end: int | None, status: str) -> int:
        self.db.execute("INSERT OR IGNORE INTO turns(key, session, reply_to, origin, text, transcript, "
                        "transcript_start, transcript_end, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, session, reply_to, origin, text, transcript, start, end, status, now()))
        return int(self.db.execute("SELECT turn_id FROM turns WHERE key = ?", (key,)).fetchone()[0])

    @_locked
    def turn(self, turn_id: int) -> TurnRow | None:
        r = self.db.execute(f"SELECT {_TURN} FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        return None if r is None else _turn(r)

    @_locked
    def turns_with_status(self, status: str) -> list[TurnRow]:
        return [_turn(r) for r in self.db.execute(f"SELECT {_TURN} FROM turns WHERE status = ? ORDER BY turn_id",
                                                  (status,)).fetchall()]

    @_locked
    def set_turn_status(self, turn_id: int, status: str) -> None:
        self.db.execute("UPDATE turns SET status = ? WHERE turn_id = ?", (status, turn_id))

    @_locked
    def add_to_batch(self, turn_id: int, batch_id: int) -> None:
        """The reply joins the batch, after every reply that joined a batch before it: §8's "in order" is
        the order replies reach the backstop, not the order their turns began (Codex r5 finding 3). An
        older reply whose summary fails late comes after a newer one whose delivery failed first."""
        self.db.execute("UPDATE turns SET status = 'batched', batch_id = ?, "
                        "batch_seq = (SELECT COALESCE(MAX(batch_seq), 0) + 1 FROM turns) WHERE turn_id = ?",
                        (batch_id, turn_id))

    @_locked
    def open_batch(self, at: float, seconds: float) -> int:
        """The open batch whose window is still running at `at`, or a new one opened at `at` (a batch opens
        with its first reply). A batch whose window has passed takes no more replies, even before
        `batch_loop` posts it (Codex r5 finding 4)."""
        r = self.db.execute("SELECT batch_id FROM batches WHERE status = 'open' AND opened_at + ? > ? "
                            "ORDER BY batch_id LIMIT 1", (seconds, at)).fetchone()
        if r is not None:
            return int(r[0])
        return int(self.db.execute("INSERT INTO batches(opened_at, status) VALUES (?, 'open')", (at,)).lastrowid or 0)

    @_locked
    def due_batches(self, at: float, seconds: float) -> list[tuple[int, int]]:
        rows = self.db.execute("SELECT batch_id, attempt FROM batches WHERE status = 'open' "
                               "AND opened_at + ? <= ? ORDER BY batch_id", (seconds, at)).fetchall()
        return [(int(r[0]), int(r[1])) for r in rows]

    @_locked
    def batch_turns(self, batch_id: int) -> list[TurnRow]:
        return [_turn(r) for r in self.db.execute(f"SELECT {_TURN} FROM turns WHERE batch_id = ? ORDER BY batch_seq",
                                                  (batch_id,)).fetchall()]

    @_locked
    def close_batch(self, batch_id: int) -> None:
        """Its message is queued; delivery is the outbox's job."""
        self.db.execute("UPDATE batches SET status = 'posted' WHERE batch_id = ?", (batch_id,))

    @_locked
    def reopen_batch(self, batch_id: int, at: float) -> None:
        """Its message was given up on: post it again after a new window, under a new key (the attempt)."""
        self.db.execute("UPDATE batches SET status = 'open', opened_at = ?, attempt = attempt + 1 "
                        "WHERE batch_id = ?", (at, batch_id))

    @_locked
    def record_post(self, key: str, kind: str, turn_id: int | None, batch_id: int | None) -> None:
        self.db.execute("INSERT OR IGNORE INTO posts(key, kind, turn_id, batch_id) VALUES (?, ?, ?, ?)",
                        (key, kind, turn_id, batch_id))

    @_locked
    def post_record(self, key: str) -> PostRow | None:
        r = self.db.execute("SELECT key, kind, turn_id, batch_id FROM posts WHERE key = ?", (key,)).fetchone()
        return None if r is None else _post(r)

    @_locked
    def details_target(self, message_id: str | None) -> PostRow | None:
        """The record behind a delivered message (B12): the sent outbox row with that message ID, or without
        one the latest summary or batch that was delivered. Pending and failed rows never count."""
        if message_id is not None:
            r = self.db.execute("SELECT p.key, p.kind, p.turn_id, p.batch_id FROM outbox o JOIN posts p "
                                "ON p.key = o.key WHERE o.message_id = ? AND o.status = 'sent'",
                                (message_id,)).fetchone()
        else:
            r = self.db.execute("SELECT p.key, p.kind, p.turn_id, p.batch_id FROM posts p JOIN outbox o "
                                "ON o.key = p.key WHERE p.kind IN ('summary', 'batch') AND o.status = 'sent' "
                                "ORDER BY o.seq DESC LIMIT 1").fetchone()
        return None if r is None else _post(r)
```

Store unit tests, in the same file:
- `details_target(None)` with a sent summary followed by a pending summary and a failed batch returns the sent summary;
- `details_target(mid)` for a row that is still pending returns None;
- `add_turn` with the same key twice returns the same `turn_id`.
- **Join order (Codex r5 finding 3):** add turns 1 and 2; `add_to_batch(2, b)` then `add_to_batch(1, b)`; `batch_turns(b)` returns turn 2, then turn 1. After a `reopen_batch(b, …)`, a turn 3 that joins comes after both.
- **Batch window at join time (Codex r5 finding 4):** `b = open_batch(100.0, 60.0)`; `open_batch(159.999, 60.0) == b`; `open_batch(160.0, 60.0) != b` (the window has passed, though `b` is still open), and `due_batches(160.0, 60.0)` lists `b` first, then the new one only once its own window passes.

- [ ] **Step 4: Implement `transcript_size` and the turn reader in `hook.py`.**

```python
def transcript_size(ev: HookEvent) -> int | None:
    """The size of this session's transcript now: at UserPromptSubmit, where the turn's bytes begin; at
    Stop, where they end (plan 2b B19). Same path rules as the reply fallback; never follows a symlink or
    blocks on a FIFO."""
    if not ev.transcript_path:
        return None
    path = Path(ev.transcript_path)
    if path.name != f"{ev.session_id}.jsonl" or not path.is_absolute():
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        return st.st_size if stat.S_ISREG(st.st_mode) else None
    finally:
        os.close(fd)
```


The turn's records, shared by the reply fallback here and by `!details full` (Task 9). Add `from collections.abc import Iterator`:

```python
READ_WINDOW = 1024 * 1024               # a turn's bytes are read this much at a time
MAX_RECORD = 64 * 1024 * 1024           # a longer JSONL record is named, not parsed: memory (plan 2b B13)


def _lines(fd: int, start: int, end: int) -> Iterator[bytes | int]:
    """The complete lines in bytes [start, end), read READ_WINDOW at a time. A line longer than MAX_RECORD
    is not kept: its size in bytes is yielded instead. The span was measured at the turn's edges, so it
    ends on a newline: a file shorter than `end`, or a last line without its newline, means the transcript
    changed under us, and raises OSError (the caller reports a failed read, never a partial one)."""
    pos, buf, skipped = start, bytearray(), 0
    while pos < end:
        data = os.pread(fd, min(READ_WINDOW, end - pos), pos)
        if not data:
            raise OSError("the transcript is shorter than the turn's span")
        pos += len(data)
        while data:
            nl = data.find(b"\n")
            piece, data = (data, b"") if nl < 0 else (data[:nl], data[nl + 1:])
            if skipped:
                skipped += len(piece)
            else:
                buf += piece
                if len(buf) > MAX_RECORD:
                    skipped, buf = len(buf), bytearray()
            if nl >= 0:
                if skipped:
                    yield skipped
                else:
                    yield bytes(buf)
                skipped, buf = 0, bytearray()
    if buf or skipped:
        raise OSError("the turn's span ends inside a record")


def _is_prompt(record: dict[str, Any]) -> bool:
    """A real user prompt: text from the user, as opposed to a record that carries tool results or one
    Claude Code adds itself (`isMeta`, such as a caveat or a local command's output). A record that
    carries a tool result is never a prompt, even if it also has text (Codex r4 finding 2)."""
    blocks = _blocks(record)
    return record.get("type") == "user" and not record.get("isMeta") and not _has_result(record) and (
        isinstance(blocks, str) or bool(blocks and any(b.get("type") == "text" for b in blocks)))


def _has_result(record: dict[str, Any]) -> bool:
    blocks = _blocks(record)
    return (record.get("type") == "user" and isinstance(blocks, list)
            and any(b.get("type") == "tool_result" for b in blocks))


def turn_records(path: Path, start: int, end: int) -> Iterator[dict[str, Any] | int]:
    """The content records of one turn: transcript bytes [start, end), from its UserPromptSubmit to its
    Stop (plan 2b B13, B19). The turn begins at its first content (an assistant record or a tool result);
    a real prompt before that is the turn's own and is skipped, one after it is the next turn's and ends
    this one. Anything else is metadata (such as `queue-operation`) and skipped. A record over MAX_RECORD
    is yielded as its size in bytes and never begins the turn: it may be metadata. Raises OSError if the
    file can't be read, or if a complete record in the span is not a JSON object: a corrupt record could
    be the error or question the operator needs, so the read fails rather than skip it (Codex r5
    finding 2). Never follows a symlink or blocks on a FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        begun = False
        for item in _lines(fd, start, end):
            if isinstance(item, int):
                yield item
                continue
            try:
                record: Any = json.loads(item)
            except ValueError:          # also UnicodeDecodeError
                raise OSError("a transcript record is not valid JSON") from None
            if not isinstance(record, dict):
                raise OSError("a transcript record is not a JSON object")
            rec = cast(dict[str, Any], record)
            if _is_prompt(rec):
                if begun:
                    return                  # the next turn's prompt
                continue                    # this turn's own prompt
            if rec.get("type") == "assistant" or _has_result(rec):
                begun = True
                yield rec
    finally:
        os.close(fd)


def reply_text(ev: HookEvent, start: int | None, end: int | None) -> str | None:
    """A current Stop's reply (B9, B19). The event's own text if it has one; otherwise every assistant text
    of the turn's span, in order, so an early question or error is kept. None when it can't be read: no
    span, a path that is not this session's transcript, or a read error. "" is a turn that said nothing."""
    if ev.last_assistant_message:
        return ev.last_assistant_message
    if start is None or end is None or not ev.transcript_path:
        return None
    path = Path(ev.transcript_path)
    if path.name != f"{ev.session_id}.jsonl" or not path.is_absolute():
        return None
    parts: list[str] = []
    try:
        for rec in turn_records(path, start, end):
            if isinstance(rec, int):
                parts.append(f"(a transcript record of {rec} bytes was skipped: too large to read)")
                continue
            blocks = _blocks(rec)
            if rec.get("type") == "assistant" and isinstance(blocks, list):
                parts.extend(b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str))
    except OSError:
        return None
    return "\n\n".join(parts)
```

Delete `last_assistant_text`, `_read_tail` and `MAX_TRANSCRIPT`.

- [ ] **Step 5: Implement the daemon changes.**
  - Imports: `from heterodyne.admind import backstop, summarize`, `from heterodyne.admind.hook import transcript_size`, `from heterodyne.admind.store import TurnRow` and `from heterodyne.agents.claude_code import headless_argv`.
  - Constants:

```python
OFFSET_SECONDS = 2.0        # a transcript size measurement's deadline (B19, B20)
SUMMARY_POLL = 5.0          # summary_loop re-reads the database at least this often (B9)
EXTRACT_FAILED = ("(admind could not read this reply from the transcript in time. Reply `!details full` "
                  "for the turn's tool calls, or ask the agent to repeat it.)")
```

  - `__init__`: replace `self._extraction` with `self._reader: asyncio.Future[Any] | None = None  # the one transcript-read thread allowed (B20)`. Add:

```python
        self.summary_wake = asyncio.Event()
        self.summarizer_argv: list[str] | None = (
            None if settings.summarizer is None or settings.summarizer_binary is None
            else headless_argv(settings.summarizer_binary, settings.summarizer))
        self.summary_timeout = summarize.SUMMARY_TIMEOUT    # replaced in tests
        self.summary_poll = SUMMARY_POLL                    # replaced in tests
        self.offset_timeout = OFFSET_SECONDS                # replaced in tests
        self.batch_seconds = backstop.BATCH_SECONDS         # replaced in tests
        self.batch_poll = 1.0                               # replaced in tests
        self.wallclock: Callable[[], float] = time.time     # replaced in tests; batches persist across restarts
```

  - The reader slot. `extract(self, ev, start, end)` becomes `return await self.bounded_read(self.extract_timeout, reply_text, ev, start, end)`, with the docstring "The reply of a Stop without its own text, read from the turn's span (B19) in the reader slot. None if it can't be read in time or at all; the caller then sends `EXTRACT_FAILED` to the backstop (B9)."

```python
    async def bounded_read[T](self, timeout: float, fn: Callable[..., T], *args: object) -> T | None:
        """Run one transcript read in a thread, bounded: None if it took longer than `timeout`, or if an
        earlier read is still running. One slot for every transcript reader (extraction, offsets and
        `!details full`), so a stalled filesystem cannot pile up threads (B20). An abandoned thread runs to
        completion in the background."""
        if self._reader is not None and not self._reader.done():
            return None
        task = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        self._reader = task
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())  # never "not retrieved"
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            return None

    async def transcript_offset(self, ev: HookEvent) -> int | None:
        return await self.bounded_read(self.offset_timeout, transcript_size, ev)
```

  - The turn's start (B19). In `on_hook`'s `else:` branch, before `async with self.turn_lock():`, add `start = await self.transcript_offset(ev) if ev.hook_event_name == "UserPromptSubmit" else None`, and call `self.on_prompt(ev, arrival, start)`. `on_prompt` gains the parameter `start: int | None = None`, and its busy transaction becomes:

```python
        with self.store.transaction():      # the busy period, its transcript start and the anchor: one step
            self.set_busy()                 # a turn is running, whoever started it
            if start is None:
                self.store.delete("turn_start")
            else:
                self.store.set("turn_start", f"{self.store.get('busy')}:{start}")
            pasted = self.store.get("in_flight_text")
            ...                             # the anchor check, unchanged
```

  - In `on_message`, right after the `inbound` audit record, add `self.store.record_prompt(mid, verdict.operator or "?", backstop.first_words(redact(text)))`.
  - `run()` adds two supervised loops to the task group, `("summaries", self.summary_loop)` and `("batches", self.batch_loop)`. There is no startup requeue: both loops read the database.
  - `on_stop`:
    - Its first line, before the first `async with self.turn_lock()`, becomes `end = await self.transcript_offset(ev)`: the turn's bytes end here, before any later turn can write.
    - In the first `async with self.turn_lock()`, right after `identity = self.turn_identity(ev.session_id)`, add `span = self.turn_span(identity[3], end)`: the turn's bytes, known before the read.
    - The extraction block becomes:

```python
        raw: str | None = own
        if need_fallback:
            raw = await self.extract(ev, *span)     # may read the transcript file (lock not held)
            if raw is None:
                self.audit.write("agent", action="reply-extraction-failed")
```

    - In the second `async with self.turn_lock()`, keep everything up to and including the `if need_fallback and not current:` return. Replace the rest of the block (from `text = raw if raw.strip() else NO_REPLY` to the final `reply` audit record) with:

```python
            reply_to = anchor if current else None
            start, stop = span if current else (None, None)
            origin = self.origin_for(reply_to)
            if raw is None:                         # current, but the reply could not be read (B9)
                text, mode = EXTRACT_FAILED, "backstop"
            else:
                text = redact(raw if raw.strip() else NO_REPLY)
                mode = ("summary" if summarize.needs_summary(text, self.s.reply_verbatim_lines,
                                                             self.s.reply_verbatim_chars) else "verbatim")
            parts = chunk.split(text, self.s.chunk_chars) if mode == "verbatim" else []

            def record(how: str) -> int:
                """The reply's record, its posts or its route, and the turn's end: one transaction."""
                with self.store.transaction():
                    reply_seq = int(self.store.get("reply_seq") or "0") + 1
                    self.store.set("reply_seq", str(reply_seq))
                    key = f"reply:{ev.session_id}:{reply_seq}"
                    turn_id = self.store.add_turn(key, ev.session_id, reply_to, origin, text, ev.transcript_path,
                                                  start, stop, "verbatim" if how == "verbatim" else "summarizing")
                    if how == "verbatim":
                        for i, part in enumerate(parts):
                            self.post(f"{key}:{i}", part, reply_to)
                            self.store.record_post(f"{key}:{i}", "verbatim", turn_id, None)
                    elif how == "backstop":
                        self.queue_backstop(turn_id)
                    if current:
                        self.end_turn(anchor, arrival)
                return turn_id

            try:
                turn_id = record(mode)
            except Exception as exc:  # noqa: BLE001 - the reply must still reach the operator (B9)
                self.audit_quietly("reply", action="record-failed", error=type(exc).__name__)
                mode = "backstop"
                turn_id = record(mode)              # if this fails too, hook_loop holds dispatch
            if mode == "summary":
                self.summary_wake.set()
            elif mode == "backstop":
                self.audit_quietly("backstop", action="queued", turn=turn_id)
            if not current:
                self.audit_quietly("agent", action="late-stop")    # fixed wording; nothing from the event
            # The agent's text goes to the operator's chat only; the audit log records its size.
            self.audit_quietly("reply", session=ev.session_id, reply_to=reply_to, chars=len(text),
                               chunks=len(parts), mode=mode)
```

  `record` holds the cleanup that the old `with self.store.transaction():` block held, moved into `end_turn`. Since the turn has ended by then, the audit records after it use `audit_quietly`.
  - New helpers:

```python
    def turn_span(self, busy: str | None, end: int | None) -> tuple[int | None, int | None]:
        """The transcript bytes of the turn a current Stop ends (B19): from its UserPromptSubmit to the Stop.
        Unknown (both None) unless that prompt's mark belongs to the busy period the Stop was captured with."""
        mark = self.store.get("turn_start")
        if busy is None or end is None or mark is None:
            return None, None
        owner, _, offset = mark.partition(":")
        if owner != busy or not offset.isdigit() or int(offset) > end:
            return None, None
        return int(offset), end

    def end_turn(self, anchor: str | None, arrival: int | None) -> None:
        """A current Stop's effects on turn state. Store calls and in-memory flags only (the caller's
        transaction); repeating them after a rollback is harmless."""
        if anchor is not None:
            self.store.delete("anchor")
            self.store.delete("in_flight")
            self.store.delete("in_flight_text")
        self.store.delete("turn_start")
        self.set_idle()                 # the turn ended; an unconfirmed in_flight still holds
        if arrival is not None:
            self.raise_floor(arrival)   # a prompt accepted before this Stop is that turn's

    def origin_for(self, mid: str | None) -> str:
        row = None if mid is None else self.store.prompt(mid)
        if row is None:
            return backstop.origin(None, now(), None)
        return backstop.origin(row.operator, row.received_at, row.words)

    def queue_backstop(self, turn_id: int) -> None:
        """Put a reply in the open backstop batch, opening one. Store calls only, in one transaction that
        joins the caller's; the caller audits after it commits."""
        with self.store.transaction():
            batch = self.store.open_batch(self.wallclock(), self.batch_seconds)
            self.store.add_to_batch(turn_id, batch)

    async def summary_loop(self) -> None:
        """Summarize every turn that waits for one (B9). Rows are read from the database on every pass, so
        a failure at any step leaves the row for the next pass, without a restart."""
        while True:
            self.summary_wake.clear()
            for row in self.store.turns_with_status("summarizing"):
                try:
                    await self.summarize_turn(row)
                except Exception as exc:  # noqa: BLE001 - the row stays `summarizing`; the next pass retries
                    self.audit_quietly("summary", action="pass-failed", turn=row.turn_id, error=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.summary_wake.wait(), self.summary_poll)

    async def summarize_turn(self, row: TurnRow) -> None:
        """Post a summary, or send the reply to the backstop. Either outcome is one transaction; if even the
        backstop's fails, the exception reaches summary_loop and the row is tried again."""
        try:
            text = await summarize.summarize(self.summarizer_argv, self.s.state_dir / "summarizer", row.text,
                                             self.summary_timeout)
            with self.store.transaction():
                for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
                    self.post(f"{row.key}:s{i}", part, row.reply_to)
                    self.store.record_post(f"{row.key}:s{i}", "summary", row.turn_id, None)
                self.store.set_turn_status(row.turn_id, "summarized")
        except Exception as exc:  # noqa: BLE001 - any failure sends the reply to the backstop (§8)
            reason = exc.reason if isinstance(exc, summarize.SummaryFailed) else "internal"
            self.audit_quietly("summary", action="failed", reason=reason, turn=row.turn_id,
                               error=None if reason != "internal" else type(exc).__name__)
            self.queue_backstop(row.turn_id)
            self.audit_quietly("backstop", action="queued", turn=row.turn_id)
            return
        self.audit_quietly("summary", action="queued", turn=row.turn_id)

    async def batch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.batch_poll)    # not self._sleep: tests make that one return at once
            self.close_due_batches()

    def close_due_batches(self) -> None:
        """Post each batch whose window has passed, as one unthreaded message (B10)."""
        for batch, attempt in self.store.due_batches(self.wallclock(), self.batch_seconds):
            rows = self.store.batch_turns(batch)
            key = f"batch:{batch}.{attempt}"        # a new key per attempt: the outbox ignores a known key
            text = backstop.fit(redact(backstop.render([backstop.Entry(r.origin, r.text) for r in rows])),
                                backstop.BATCH_MAX_CHARS)      # one message, not chunk_chars (B10)
            with self.store.transaction():
                self.post(key, text, None)
                self.store.record_post(key, "batch", None, batch)
                self.store.close_batch(batch)
            self.audit_quietly("backstop", action="posted", batch=batch, replies=len(rows),
                               lines=len(text.splitlines()), chars=len(text))
```

  `origin_for` uses `now()` for a turn begun at the terminal.
  - `send_failed` (Task 4) becomes:

```python
    def send_failed(self, seq: int, key: str) -> None:
        """A message admind gave up on. If it carried a reply or a summary, the reply goes to the backstop
        in the same transaction (B9). If it was a batch, the batch opens again in the same transaction and
        is posted after a new window under a new key, so its replies are never stranded (B10); this
        repeats until it is delivered, and the audit records each attempt."""
        queued = reopened = None
        with self.store.transaction():
            self.store.mark_failed(seq)
            post = self.store.post_record(key)
            turn = None if post is None or post.turn_id is None else self.store.turn(post.turn_id)
            if turn is not None and turn.status in ("verbatim", "summarized"):
                self.queue_backstop(turn.turn_id)
                queued = turn.turn_id
            if post is not None and post.kind == "batch" and post.batch_id is not None:
                self.store.reopen_batch(post.batch_id, self.wallclock())
                reopened = post.batch_id
        if queued is not None:
            self.audit_quietly("backstop", action="queued", turn=queued, why="send-failed")
        if reopened is not None:
            self.audit_quietly("backstop", action="reopened", batch=reopened, why="send-failed")
```

  The other chunks of a verbatim reply may still be delivered. Its turn is batched by the first failure, so later failures don't queue it again.

- [ ] **Step 6: Run the gate.** Existing tests that expect a long reply to come back verbatim must be adapted:
  - **Settings.** They set `reply_verbatim_lines` and `reply_verbatim_chars` high (`make_settings(..., reply_verbatim_lines=200, reply_verbatim_chars=60000)`). That keeps their intent: those tests are about turn state, not summaries.
  - **Extraction timeouts and failures.** Tests that expected one to post nothing, or a missing transcript to post `NO_REPLY`, now expect a backstop batch holding `EXTRACT_FAILED` once `batch_seconds` passes.
  - **The reader slot.** Tests that reached into `_extraction` use `_reader` instead.

- [ ] **Step 7: Commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): summarized replies with a durable backstop (ADR r13 §8)"
```

Review against B9, B10, B12, B19 and B20.

---

### Task 9: `!details` and `!details full`

**Files:**
- Modify: `src/heterodyne/admind/commands.py`, `src/heterodyne/admind/hook.py`, `src/heterodyne/admind/daemon.py` (`on_message`, `handle`, new `details` and `tool_calls`)
- Test: `tests/test_admind_r13_details.py`

**Interfaces:**
- Consumes: the Task 8 records, `bounded_read` (Task 8), lane 2 (Task 4) and `redact`.
- Produces:
  - `commands.Command("details", arg="full" | None)`;
  - `hook.turn_tool_calls(path: Path, start: int, end: int) -> str`;
  - it consumes Task 8's `hook.turn_records`, `READ_WINDOW` and `MAX_RECORD` (64 MiB; nothing shown is capped, B13);
  - in the daemon: `DETAILS_READ_SECONDS = 30.0`, `DETAILS_BUSY`, `Admind.details(mid, cmd, target)`, `Admind.tool_calls(row)` and the replaceable attribute `details_timeout`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_admind_r13_details.py
import json
from pathlib import Path

import pytest

from heterodyne.admind import commands, hook
from heterodyne.admind.hook import turn_tool_calls


def test_parse_details() -> None:
    assert commands.parse("!details") == commands.Command("details")
    assert commands.parse("!details full") == commands.Command("details", arg="full")
    with pytest.raises(commands.CommandError):
        commands.parse("!details everything")
    assert "!details [full]" in commands.HELP


def append(path: Path, records: list[dict[str, object]]) -> int:
    """Append JSONL records; return the file's size afterwards."""
    with path.open("a") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in records))
    return path.stat().st_size


def prompt(text: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": text}}


def tool(name: str, **inp: object) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def result(content: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": content}]}}


def said(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_tool_calls_of_this_turn_only(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    start = append(t, [prompt("old prompt"), tool("Old")])            # an earlier turn
    end = append(t, [
        prompt("check the gateway"),
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "secret plan"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "systemctl --user status x"}}]}},
        result("active (running)"),
        said("It is running."),
    ])
    append(t, [prompt("later"), tool("Later")])                       # the next turn, after this Stop
    out = turn_tool_calls(t, start, end)
    assert "▸ Bash" in out and "systemctl --user status x" in out and "◂ active (running)" in out
    assert "Old" not in out and "secret plan" not in out and "Later" not in out


def test_metadata_before_the_prompt_does_not_start_the_turn(tmp_path: Path) -> None:
    # Codex r2 finding 6: a queue-operation record before the turn's own prompt.
    t = tmp_path / "s.jsonl"
    end = append(t, [{"type": "queue-operation", "operation": "enqueue", "content": "mine"},
                     prompt("mine"),
                     {"type": "user", "isMeta": True, "message": {"content": "a caveat"}},
                     tool("Mine"), result("done"), prompt("queued next"), tool("Next")])
    out = turn_tool_calls(t, 0, end)
    assert "▸ Mine" in out and "◂ done" in out and "Next" not in out


def test_a_later_prompt_inside_the_span_ends_the_turn(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("mine"), tool("Mine"), prompt("queued next"), tool("Next")])
    out = turn_tool_calls(t, 0, end)
    assert "Mine" in out and "Next" not in out


def test_no_tool_calls(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("hi"), said("hello")])
    assert turn_tool_calls(t, 0, end) == "(no tool calls in this turn)"


def test_long_turn_keeps_its_first_calls(tmp_path: Path) -> None:
    # More than MAX_TRANSCRIPT (8 MiB) of output between the first and the last call.
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), tool("Early"), *[said("t" * (1024 * 1024)) for _ in range(9)], tool("Late")])
    out = turn_tool_calls(t, 0, end)
    assert "▸ Early" in out and "▸ Late" in out


def test_record_larger_than_a_read_window_is_shown_whole(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), tool("Cat"), result("r" * (2 * 1024 * 1024)), tool("After")])
    out = turn_tool_calls(t, 0, end)
    assert "◂ " + "r" * (2 * 1024 * 1024) + "\n" in out and "▸ After" in out


def test_oversized_metadata_before_the_prompt_does_not_start_the_turn(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Codex r3 finding 3.
    monkeypatch.setattr(hook, "MAX_RECORD", 1000)
    t = tmp_path / "s.jsonl"
    end = append(t, [{"type": "queue-operation", "content": "q" * 5000}, prompt("mine"), tool("Mine"),
                     prompt("next"), tool("Next")])
    out = turn_tool_calls(t, 0, end)
    assert "▸ Mine" in out and "Next" not in out


def test_non_text_results_are_kept_distinct(tmp_path: Path) -> None:
    # Codex r3 finding 4: structured and image results are rendered, not collapsed to one placeholder.
    t = tmp_path / "s.jsonl"
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    end = append(t, [prompt("go"), tool("Shot"),
                     {"type": "user", "message": {"content": [{"type": "tool_result", "content": [
                         {"type": "text", "text": "saved"}, img, {"type": "resource", "uri": "file:///x"}]}]}},
                     {"type": "user", "message": {"content": [{"type": "tool_result",
                                                                "content": {"rows": 2, "ok": True}}]}},
                     {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                                                                "content": "denied"}]}}])
    out = turn_tool_calls(t, 0, end)
    assert "◂ saved\n[image image/png, 12 base64 characters, sha256 " in out
    assert '{"type": "resource", "uri": "file:///x"}' in out
    assert '◂ {"ok": true, "rows": 2}' in out
    assert "◂ (error) denied" in out


def test_values_are_redacted_before_they_are_serialized(tmp_path: Path) -> None:
    # Codex r4 finding 1: json.dumps turns "\x01ghp_…" into "\\u0001ghp_…", where the token follows "1".
    token = "ghp_" + "A" * 30
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), tool("Bash", command="\x01" + token),
                     {"type": "user", "message": {"content": [{"type": "tool_result",
                                                                "content": {"env": "\x01" + token}}]}}])
    out = turn_tool_calls(t, 0, end)
    assert "AAAAAAAAAA" not in out and out.count("<redacted GitHub token>") == 2


def test_keys_that_redact_alike_keep_every_entry(tmp_path: Path) -> None:
    # Codex r5 finding 1: two hex keys both become "<redacted hex key>"; neither entry may be lost.
    both = {"a" * 64: "first result", "b" * 64: "second result"}
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), tool("Bash", **both),
                     {"type": "user", "message": {"content": [{"type": "tool_result", "content": both}]}}])
    out = turn_tool_calls(t, 0, end)
    assert out.count("first result") == 2 and out.count("second result") == 2
    assert '"<redacted hex key> #2"' in out and "aaaa" not in out


def test_a_corrupt_record_is_unreadable(tmp_path: Path) -> None:
    t = tmp_path / "s.jsonl"
    append(t, [prompt("go"), tool("Bash", command="ls")])
    with t.open("a") as fh:
        fh.write("not json\n")
    assert turn_tool_calls(t, 0, t.stat().st_size) == "(the transcript could not be read)"


def test_oversized_record_is_named_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook, "MAX_RECORD", 1000)
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), result("r" * 5000), tool("After")])
    out = turn_tool_calls(t, 0, end)
    assert "a transcript record of" in out and "was skipped" in out and "▸ After" in out
    assert "rrrr" not in out


def test_no_total_cap(tmp_path: Path) -> None:
    # §8: `!details` has no length cap (Codex r2 finding 5).
    t = tmp_path / "s.jsonl"
    end = append(t, [prompt("go"), *[tool(f"T{i}", n="x" * 1000) for i in range(300)]])
    out = turn_tool_calls(t, 0, end)
    assert all(f"▸ T{i} " in out for i in range(300)) and len(out) > 300_000


def test_unreadable_transcript(tmp_path: Path) -> None:
    link = tmp_path / "s.jsonl"
    link.symlink_to(tmp_path / "elsewhere.jsonl")
    assert turn_tool_calls(link, 0, 10) == "(the transcript could not be read)"
```

Store test, in the same file: build a Store, record a turn, a summary post and its outbox row, and mark the row sent with message ID `m`. Then `close()` and reopen the Store from the same path. `details_target("m")` returns that post, and `details_target(None)` returns it too.

Daemon scenarios with the shared harness:

1. **Reply to a summary:** after a summarized reply, a `!details` that **replies to the summary's message ID** returns the full redacted reply under its origin. Send it with `h.fake.message_event(..., reply_to=<the summary's message ID, from the outbox row whose key ends in :s0>)`. The reply is chunked at `chunk_chars`, posted in lane 2 and threaded to the `!details` message.
2. **No reply target:** `!details` without a reply target expands the latest summary or batch that was delivered.
3. **On a batch:** it returns every reply in order, each under its origin.
4. **`!details full`:** it adds the turn's tool calls and never shows thinking. Use a transcript the test writes at `<tmp>/<sid>.jsonl`: one part before a `UserPromptSubmit` for that path, then this turn's records, then the Stop. Tool calls written before the prompt are not shown.
5. **Turn without a span:** `!details full` on a turn without a span (a terminal Stop with no prompt hook) says "(tool calls are not available for this turn)".
6. **Busy reader:** with the reader slot occupied (`h.daemon._reader = asyncio.get_running_loop().create_future()`), `!details full` answers with `DETAILS_BUSY` in place of the tool calls. Clear it afterwards.
7. **No record:** `!details` replying to a message with no record (for example `READY_NOTICE`) answers "!details: that message has no details. Reply to a summary or a batch." and posts nothing in lane 2.
8. **Lane order:** while a long `!details` is still in lane 2, an alert relayed meanwhile is sent before the remaining chunks. Use `chunk_chars=200` and a long reply.
9. **Restart:** a first `run_with` delivers a summary. A second `run_with` on the same `tmp_path` then answers a `!details` that replies to that summary's message ID with the full reply.

- [ ] **Step 2: Run the tests and confirm they fail.** Expected: `CommandError: Unknown command !details`.

- [ ] **Step 3: Implement the command parsing.** In `commands.py`:
  - `HELP = "admind commands: !new · !interrupt · !tail [n] · !restart <unit> · !ps · !details [full]"`;
  - `CommandName` gains `"details"`;
  - in `parse`:

```python
    if name == "details":
        if args not in ([], ["full"]):
            raise CommandError("Usage: !details [full], as a reply to a summary or batch (or alone, for the latest).")
        return Command("details", arg="full" if args else None)
```

  `CommandRunner.run` never receives `details`, because the daemon handles it. Raise `CommandError("internal")` there if it ever does.

- [ ] **Step 4: Implement `turn_tool_calls` in `hook.py`.** Add `import hashlib` and `from heterodyne.admind.redact import redact, unique_key` (redact imports only the secret scanner, so there is no cycle), and:

```python
def _block_text(block: Any) -> str:
    """One block of a tool result, deterministically. Text is shown as it is; an image is named by its
    type, size and digest (a text chat can't show it; flagged, B13); anything else is its JSON, keys
    sorted, with every string redacted first (`_json`)."""
    if isinstance(block, dict):
        b = cast(dict[str, Any], block)
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            return b["text"]
        source = b.get("source")
        if b.get("type") == "image" and isinstance(source, dict):
            data = str(cast(dict[str, Any], source).get("data", ""))
            digest = hashlib.sha256(data.encode()).hexdigest()[:12]
            return (f"[image {cast(dict[str, Any], source).get('media_type', '?')}, {len(data)} base64 "
                    f"characters, sha256 {digest}]")
    return _json(block)


def _redacted(value: Any) -> Any:
    """`value` with every string in it (keys too) redacted, before it is serialized: JSON escapes can put
    a digit right before a secret (`\\u0001ghp_…`), which the scanner's start rule then refuses (B1)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in cast(dict[Any, Any], value).items():
            out[unique_key(redact(str(k)), out)] = _redacted(v)    # keys that redact alike: both kept
        return out
    if isinstance(value, list):
        return [_redacted(v) for v in cast(list[Any], value)]
    return value


def _json(value: Any) -> str:
    return json.dumps(_redacted(value), ensure_ascii=False, sort_keys=True)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_block_text(b) for b in cast(list[Any], content))
    return _json(content)


def _tool_lines(record: dict[str, Any]) -> list[str]:
    """The tool calls and results in one record. Thinking blocks are never read out."""
    blocks = _blocks(record)
    if not isinstance(blocks, list):
        return []
    if record.get("type") == "user":
        return [f"◂ {'(error) ' if b.get('is_error') else ''}{_result_text(b.get('content'))}"
                for b in blocks if b.get("type") == "tool_result"]
    if record.get("type") == "assistant":
        return [f"▸ {b.get('name')} {_json(b.get('input'))}"
                for b in blocks if b.get("type") == "tool_use"]
    return []


def turn_tool_calls(path: Path, start: int, end: int) -> str:
    """The tool calls and results of one turn (plan 2b B13, B19), read with `turn_records` (Task 8).
    Nothing is shortened: §8 puts no cap on `!details`. A record over MAX_RECORD is named with its size."""
    out: list[str] = []
    try:
        for rec in turn_records(path, start, end):
            if isinstance(rec, int):
                out.append(f"(a transcript record of {rec} bytes was skipped: too large to read)")
            else:
                out.extend(_tool_lines(rec))
    except OSError:
        return "(the transcript could not be read)"
    return "\n".join(out) if out else "(no tool calls in this turn)"
```

  `turn_records` keeps at most one record plus one window in memory while reading; the rendered lines are kept whole. `MAX_RECORD` is read when `_lines` runs, so tests can lower it. The lines are redacted whole, never after a cut: `details` redacts the joined text (B1), and nothing in between shortens it.

- [ ] **Step 5: Implement the daemon changes.**
  - Constants and `__init__`:

```python
DETAILS_READ_SECONDS = 30.0     # `!details full`'s transcript read (B20)
DETAILS_BUSY = "(the transcript is busy or slow; try `!details full` again)"
```

```python
        self.details_timeout = DETAILS_READ_SECONDS         # replaced in tests
```

  - `on_message` passes `target = ev.reply_to.message_id_hex.lower() if ev.reply_to is not None else None` to `self.handle(mid, text, target)`.
  - `handle(self, mid, text, target=None)`, after `self.store.set_inbound(mid, "executing")`:

```python
            if cmd.name == "details":
                await self.details(mid, cmd, target)
                return
```

  - New methods:

```python
    async def details(self, mid: str, cmd: commands.Command, target: str | None) -> None:
        """`!details [full]`: the full redacted reply (or every reply of a batch), each under its origin,
        in lane 2 and threaded to the command (ADR 0001 §8, revision 13)."""
        if not self.authorised():
            self.deny(mid, "command")
            return
        post = self.store.details_target(target)
        if post is None:
            why = ("that message has no details. Reply to a summary or a batch." if target is not None
                   else "there is no delivered summary or batch yet.")
            self.finish(mid, "done", f"!details: {why}", "cmd")
            return
        rows = ([r for r in [self.store.turn(post.turn_id)] if r is not None] if post.turn_id is not None
                else self.store.batch_turns(post.batch_id or 0))
        full = cmd.arg == "full"
        sections: list[str] = []
        for row in rows:
            body = row.text
            if full:
                body += "\n\n" + await self.tool_calls(row)
            sections.append(f"{row.origin}\n{body}")
        text = redact("\n\n".join(sections))
        with self.store.transaction():
            self.store.set_inbound(mid, "done")
            for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
                self.post(f"details:{mid}:{i}", part, mid, lane=2)
        self.audit.write("command", message_id=mid, command="details", full=full, replies=len(rows),
                         chars=len(text))

    async def tool_calls(self, row: TurnRow) -> str:
        if row.transcript is None or row.transcript_start is None or row.transcript_end is None:
            return "(tool calls are not available for this turn)"
        path = Path(row.transcript)
        if path.name != f"{row.session}.jsonl" or not path.is_absolute():
            return "(tool calls are not available for this turn)"
        out = await self.bounded_read(self.details_timeout, turn_tool_calls, path, row.transcript_start,
                                      row.transcript_end)
        return DETAILS_BUSY if out is None else out
```

  Import `turn_tool_calls` from the hook module (`TurnRow` came with Task 8). The paths stored for a turn passed the same session-name rule when it was recorded.

  Nothing in `!details` or `!details full` is capped (B13); a long answer is simply more lane-2 messages, which never hold up lane 1 (B14). The only exception is a single transcript record over `MAX_RECORD` (64 MiB), named with its size because parsing it would need that much memory at once (flagged to the operator).

- [ ] **Step 6: Run the gate. Then commit and review.**

```bash
git add -A src tests
git commit -m "feat(admind): !details and !details full in the second delivery lane (ADR r13 §8)"
```

Review against B12, B13, B19 and B20.

---

### Task 10: Docs, ADR sync and the PR

**Files:**
- Modify: `docs/admind.md`, `docs/configuration.md`, `docs/security-model.md`
- Replace: `docs/adr/0001-workstreams-v2.md`, byte-identical with the design repo at `66b3aec`
- Create: `docs/reviews/0001-design-review-r18.md`, `-r19.md` and `-r20.md`; update `docs/reviews/0001-design-review-r1-response.md` from the design repo

- [ ] **Step 1: Sync the ADR and review records.**

```bash
D=~/repos/hermes-workstreams-v2; git -C "$D" rev-parse HEAD   # must print 66b3aecb639e6ec56f108e2f55d483d4dedda485 on branch design
for f in docs/adr/0001-workstreams-v2.md docs/reviews/0001-design-review-r18.md docs/reviews/0001-design-review-r19.md \
         docs/reviews/0001-design-review-r20.md docs/reviews/0001-design-review-r1-response.md; do
  git -C "$D" show 66b3aec:"$f" > "$f"
done
cmp <(git -C "$D" show 66b3aec:docs/adr/0001-workstreams-v2.md) docs/adr/0001-workstreams-v2.md && echo identical
```

- [ ] **Step 2: Update `docs/admind.md`.** Add these sections:
  - **Operators:** every `policy.toml` operator with a `marmot_npub`; adding and removing them with `admind operators add|remove NAME`.
  - **Temporary operators,** step by step: add the entry to `policy.toml`, run `admind operators add NAME`, run the test, run `admind operators remove NAME`, then delete the entry.
  - **The latch and `admind rearm`:** check the members in your client first; rearm needs the daemon running and subscribed; it refuses if the group changes while it reads the count, and it reconciles which operators are confirmed in the group (B21, B22): if it can't account for the count, make `policy.toml`'s operators match the group and rearm again.
  - **Replies:** verbatim up to 8 lines and 800 characters; summaries (at most 10 lines) with the footer; the backstop batch as one message, with its title, origins, `(×k)`, skipped-lines line lines kept whole up to the 60,000-character message limit, and a failed batch posted again; `!details` and `!details full`, uncapped apart from the 64 MiB record guard; the two lanes.
  - **Audit:** whole messages, redacted, with operator names, and the new record kinds `membership`, `summary`, `backstop` and `ctl`.
  - **Upgrading from plan 2:** the `expected_members` and `group_operators` migrations (several operators in `policy.toml` at upgrade time latch until a rearm); the outbox upgrade, which redacts queued replies and may re-send text already received under new keys; and that a pending record latches on startup.

- [ ] **Step 3: Update `docs/configuration.md`.** Document `summarizer`, `reply_verbatim_lines` and `reply_verbatim_chars`, with defaults and the claude-code-only note. Update `docs/security-model.md`:
  - the redaction applies to everything posted and to every audit field, with 64-hex identifiers written as `id:` references (B1, B16);
  - the summarizer runs without tools, hooks, MCP servers or user settings (B8);
  - the control socket's trust boundary (B6), and that failures fail closed (B18);
  - the residual count-preserving-swap risk, unchanged.

- [ ] **Step 4: Run the full gate. Then commit and push.**

```bash
git add docs
git commit -m "docs(admind): revision 13 operators, membership, replies, details; sync ADR 0001 r13"
git push -u origin plan-2b-admind
gh pr create --base main --head plan-2b-admind --title "admind: ADR 0001 revision 13 (plan 2b)" \
  --body "Implements ADR 0001 r13 §8 (66b3aec, approved btq-5ky39): several operators, journaled membership, one redaction, summarized replies with a batching backstop, !details [full], two delivery lanes, untruncated audit. Per-task cross-model reviews are recorded on the plan 2b beads."
```

The PR body ends with the attribution lines the session requires. Merging is the operator's action: report the **full PR URL** to the operator.

- [ ] **Step 5: Hand over to acceptance.** Re-scope `btq-llc3n` (operator acceptance) as orchestrator. It is unassigned and blocked, so park it per PICKUP "Orchestrating the task graph", then make these changes and note them on the bead:
  - acceptance steps, run while the operator watches:
    - add a generated test key with `admind operators add`;
    - byte-exact passthrough from the test key;
    - a long reply summarized;
    - a forced summarizer failure leading to a batch;
    - `!details` and `!details full`;
    - `admind operators remove`;
    - a deliberate latch and `rearm`;
  - a dependency on this task's bead, plus a note that results go into a follow-up PR from `main` after the merge.

## Self-review notes (plan author)

- **Spec coverage (§8 r13):**

| §8 requirement | Task |
|---|---|
| Operators | 3 |
| Ingress | 3 (existing replay and group checks kept) |
| Join signal | unchanged; generic |
| Membership init | 3 |
| add/remove via the daemon | 5 and 6 |
| Transition steps 1–5 | 5 |
| rearm | 5 and 6 |
| Verify wn-agent first | 1 |
| Latch | 3 and 5 |
| Temporary operators | 10 docs, plus the acceptance in `btq-llc3n` |
| Redaction | 2, and 4 for the upgrade |
| Replies, verbatim | 8 |
| Summaries | 7 and 8 |
| Backstop (one message) | 7 and 8 |
| `!details` | 8 and 9 |
| `!details full` | 9 |
| Records across restarts | 8 and 9 |
| Lanes | 4 |
| Audit | 2, plus the new kinds in 5, 6 and 8 |
| Commands | 9 |
| §11 tests | each task |

- **Type consistency:**
  - `Operator.hex` is used by guard, daemon and CLI.
  - `PostRow` and `TurnRow` are defined in Task 8 and used in Task 9. `queue_backstop` calls `open_batch(at, seconds)` and `add_to_batch(turn_id, batch_id)`; `batch_turns` orders by `batch_seq`; `set_turn_status(turn_id, status)` no longer takes a batch.
  - `post(..., lane=)` is defined in Task 2 and stored in Task 4.
  - `summarizer_argv` is defined in Task 8 and is `None` when not configured.
  - `send_lock`, `_backoff` and `send_failed(seq, key)` are defined in Task 4; Task 5 drains `send_lock`, and Task 8 extends `send_failed`.
  - `work_lock`, `transition_lock`, `changing` and `membership_epoch` are defined in Task 5; `worker_loop` holds `work_lock`.
  - `bounded_read` and `_reader` replace `_extraction` in Task 8; Task 9's `tool_calls` uses them.
  - `TurnRow` has `transcript_start` and `transcript_end` (Task 8); `turn_tool_calls(path, start, end)` takes both (Task 9).
  - `audit.ref_id` and `redact_continuation` are defined in Task 2 and used in Tasks 2 and 4.
  - `redact.unique_key` is defined in Task 2 and used by `audit.clean` (Task 2) and `hook._redacted` (Task 9).
  - `guard.Verdict.operator` is set for a latched operator's message too (Task 3); `on_message` audits it whole.
  - The fake's `membership_gate`, `info_gate`, the `lost` mode and `drop_subscriptions()` are defined in Task 5.
  - kv `group_operators`, `confirmed_operators()`, `authorise()` and `policy_operators` are defined in Task 3; Task 5's commit and `rearm` write them (`applied`, `reconcile`).
  - `settings.MAX_NAME` and `NAME_CONTROLS` are defined in Task 3 and imported by Task 6's control server.
  - `backstop.BATCH_MAX_CHARS` is defined in Task 7, confirmed by Task 1's fact 8 and used by Task 8's `close_due_batches`; `reopen_batch` (Task 8) is called by `send_failed`.
  - `Admind.group_events` and `Admind.reading` are defined in Task 5 and read by `rearm`.
  - `hook.turn_records`, `_lines`, `_is_prompt`, `READ_WINDOW` and `MAX_RECORD` are defined in Task 8 (the reply fallback) and reused by Task 9's `turn_tool_calls`.
- **Lock order** (Task 5): `transition_lock` → `work_lock` → `dispatch_lock` → `send_lock`, each held briefly after the second. Nothing holding a later lock waits for an earlier one.
- **Deviations from the ADR's wording, for the operator:** see "ADR r13 deviations for operator sign-off" near the top of this plan; it is the one complete list.
