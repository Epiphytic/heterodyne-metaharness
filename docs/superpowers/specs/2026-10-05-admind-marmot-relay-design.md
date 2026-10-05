# admind Marmot relay for operator asks and design approvals (interim) — design

- **Status:** proposed, 2026-10-05. Needs operator approval before implementation (shared-queue design gate).
- **Scope:** `admind` (ADR 0001 §8, revision 13) and one small change to `beads-task-queue`'s `approve-bead`.
- **Interim:** this ships ahead of ADR revision 14 and is folded into it (section 10).
- **Plan:** `docs/superpowers/plans/2026-10-05-admind-marmot-relay.md`.

## 1. Why

The operator answers questions and approves designs at a terminal today: a controller session asks in chat, and the operator runs `approve-bead` by hand. The operator wants to do both from the phone (White Noise over Marmot) while a controller session keeps building in a terminal (decision of 2026-10-05). `admind` already has an authenticated Marmot group of the operators, redaction, chunking, threading, `!details`, an audit log and a host control socket, so the relay is built into it.

This is a first, narrow piece of the Marmot-only operation requirement recorded in the revision 13 acceptance run (bead `btq-xv48a`): questions and approvals reach the operator on Marmot, not only on a terminal.

## 2. What the operator gets

1. **Asks.** A local process posts an ask; admind posts it to the operator group as a card with a short ID. There are three kinds:
   - `question`: free-form, with the context needed to answer;
   - `merge`: a request to merge a pull request, carrying its full URL and pinned head SHA; merging stays the operator's own action in GitHub, and admind never merges;
   - `approval`: a request to decide an open `kind:approval` btq bead.
2. **Answers.** The operator replies to the card (a Marmot reply), or sends `!answer <id> <text>`. admind stores the answer, and the poster reads it with `admind ask get <id>` or `admind ask wait <id>`.
3. **Approvals.** For an `approval` ask the card shows the bead's full context, as `approve-bead` hashes it, and the first 12 hex digits of its context digest. The operator replies to the card with `!approve <bead> <digest12>` or `!deny <bead> <reason>`. admind checks everything again and runs `approve-bead` for that operator, recording `via=marmot` and a reference to the Marmot message.

## 3. The trust change being approved

**Approval authority moves from the terminal to an authenticated Marmot message.** Today a btq approval is recorded by a person running `approve-bead` at a terminal as the service user and confirming at its prompt. After this change it can also be recorded by admind, running as the same user, when a Marmot message:

- is MLS-authenticated as an operator's key, in admind's group, not latched, not replayed (the existing ingress rules, §8);
- comes from an operator whose `policy.toml` name is in btq's `approvers`;
- is a reply to the genuine admind card for that bead, names the bead, and repeats the digest shown on the card;
- arrives while the bead is still open, still `kind:approval`, and still hashes to the full digest admind showed.

The typed digest takes the place of the terminal's `[y/N]` confirmation. So **whoever controls an approver's Marmot key, and so their phone or White Noise client, can approve designs.** This is the risk being accepted.

What it does **not** change, stated plainly so the change is not overstated:

- **The admind group already had this power, but not in the open.** The admin agent runs unsandboxed as the service user (§8, operator decision 2026-09-29). An operator message to it could already make it run `approve-bead --yes`. The relay makes that path explicit, pinned to a digest, attributed to a named approver, and audited, instead of leaving it to an LLM's interpretation.
- **btq metadata is still trusted-agent policy, not authentication** (beads-task-queue `CLAUDE.md`). Any process running as the service user can still run `approve-bead`, or `bd` directly. The relay adds no way for such a process to approve: posting an ask never decides anything.
- `approve-bead` at the terminal keeps working (`via=cli`). Every `approve-bead` decision, from the terminal or from the relay, takes the same per-bead lock and re-checks under it that the bead is still open, still `kind:approval`, unchanged and undecided (R6). So among `approve-bead` writers the first decision wins and a second one is refused. A same-user process that edits the bead with `bd` directly is outside that protocol. What it can do is make a recorded approval invalid: btq's `approval_valid` rejects any `*_digest` that differs from the current content, so a racing edit fails closed. It cannot make an approval valid.
- **What the phone shows is all there is.** A card for an approval can only be posted if its whole readout survives admind's redaction unchanged (R21). Content that would be hidden (a token-shaped string, a 64-hex run, a PEM block) means the bead must be decided at the terminal. File, commit and range refs are shown as pinned forge links where the repo has a GitHub remote, and otherwise as their pinned JSON with "read on the host" (R25).

## 4. Decisions

| # | Decision | Why |
|---|---|---|
| R1 | Asks reach admind over a **new** host socket, `<state>/admind/ask.sock` (0600, in the 0700 state directory, one JSON request per connection, at most 256 KiB, read within 5 s), served by a JSON-lines Unix socket server factored out of `ctl.CtlServer` (`ctl.JsonSocketServer`), which both sockets use, each with its own limit, request type, checks, refusal replies and audit kind. The CLI is `admind ask post\|get\|wait\|list\|cancel`. | `ctl.sock`'s 4096-byte limit and its fixed refusal set are pinned by tests and the runbook; an ask's context is larger. Sharing the server code reuses the symlink, stale-socket and identity checks. The trust boundary is the same: whoever can use the socket can already act as the service user. |
| R2 | `admind ask wait` polls `get` every 2 s on the client side, with a default timeout of 540 s, exit 3 on timeout. The daemon holds no long-lived connections. | It survives a daemon restart (a connection failure is retried until the timeout), and 540 s fits under a 10-minute tool-call limit. |
| R3 | Ask IDs are 4 characters from `23456789abcdefghjkmnpqrstuvwxyz` (no `0 1 i l o`), random, unique among all stored asks, matched case-insensitively. | Easy to type on a phone; about 920,000 values, far more than will ever exist. |
| R4 | **Every ask must carry its context** (§5.9's "no bare approve X?"), checked deterministically when it is posted. `question` and `merge` need a one-line title (1–200 characters) and a body with at least 80 non-space characters (at most 16,000 characters). `merge` also needs `--pr` matching `https://github.com/<owner>/<repo>/pull/<n>` and `--head`, a 40-hex SHA. `approval` takes only a bead ID: its context is the bead, and a bead that `approve-bead` reports gaps for is refused (the gaps go back to the poster, not to the operator). | The deterministic part of §5.9's gate. There is no gatekeeper judgement in the interim; for approvals, btq's own lint is the gate. |
| R5 | admind reads the bead through `approve-bead <bead> --json` (new, read-only) and decides through `approve-bead <bead> --as=NAME --yes --expect-digest=FULL --via=marmot --via-ref=REF` (plus `--deny --note=TEXT`). It never writes bead metadata itself, and it imports no btq code. The binary is the optional host-config key `[admind] approve_bead` (an absolute path). Without it, approval asks are refused and everything else works. | One tool writes approvals, with all its checks. Running it as a subprocess keeps admind's own process free of btq code; if beads is down, only the approval kind fails, and admind's recovery role (§8) is unaffected. |
| R6 | **`--expect-digest` and a per-bead lock close the gap between admind's check and the write.** `approve-bead` refuses unless the digest it computes equals the full digest admind showed, both at its first read and at its pre-write re-check. admind passes the full digest it stored when it rendered the card, never the 12 typed characters. Every `approve-bead` decision (relay or terminal) and every `--json` read holds an exclusive `flock` on `<btq state>/approve-bead/<bead>.lock` (60 s wait, then "busy", exit 4) from its first read until after its last write. Under the lock, the pre-write re-check covers status `open`, the `kind:approval` label, the absence of every decision field (`decision`, `approved_*`, `denied_*`, `via_ref`), the approver list and the digest. After writing, `approve-bead` reads the bead back and recomputes the digest. If it changed, it says so and exits 5: a racing direct edit has invalidated the approval, and `approval_valid` will reject it. | Without `--expect-digest`, an edit between admind's check and `approve-bead`'s own read would be approved unseen, because `approve-bead --yes` approves whatever it reads. Passing the full digest also makes a 12-hex prefix collision useless. The lock serialises the two writers that follow the protocol (finding r1-1). Because `--json` takes the lock too, a read-back never observes a write still in progress (r1-2). |
| R7 | **A decision must be a Marmot reply to the card** (any of its chunks, or a chunk of that card's `!details`). The bead and digest in the command must equal the card's. `!approve`/`!deny` sent unthreaded, or as a reply to anything else, are refused with a hint. | The admin agent's replies come from admind's own identity too, and it could print a card look-alike that misdescribes a real open ask. Only rows admind itself queued under an `ask:` or `askd:` outbox key count as cards, so a forged card cannot be replied to with effect. |
| R8 | **The whole context must have been delivered.** admind stores how many chunks the card has (`card_parts`). `!approve` is refused until every one of them is `sent`. If the card was shortened, it is also refused until the approving operator has asked for `!details` on that ask and every chunk of that `!details` (count stored in `ask_details.parts`) is `sent`. An empty or partial set never counts. `!deny` needs neither. | §5.9: an approval is evidence only for content that was shown. The digest covers the whole bead, so its whole text must have reached the approver. A pending or failed last chunk is not delivery (r1-6). |
| R9 | **Only the newest card for a bead counts.** Posting an approval ask for a bead that already has an open ask marks the old one `superseded`, and a notice is threaded to it. | An old card with an old digest must not stay actionable beside a new one. |
| R10 | The approver name is the operator's `policy.toml` name, which must equal a name in btq's `approvers` (read from `approve-bead --json`, so admind never reads `~/.config/beads-task-queue`). There is no mapping table. | ADR §3.4: the btq approver and the Marmot sender resolve to the same approver entry. A test operator that is not a btq approver is refused, and `approve-bead` is never run for it. |
| R11 | `!approve`/`!deny` run inside the worker that already holds `work_lock` for the operator's message, with a 60 s limit on each `--json` read and 90 s on the decision run. So a host membership change cannot happen mid-decision (it waits for `work_lock`, B7). admind re-checks `authorised(mid)` after every await. **The authorisation commit point is the launch of the decision run.** Authorisation is checked synchronously right before the child is started. Once started, the child is not cancelled when admind latches, loses its subscription or sees a group change: killing `approve-bead` mid-write would leave a partial write. The decision then settles as usual. It is audited with `latched_during=true`, and its reply waits in the outbox like every post of a latched admind. The residual risk is a decision authorised up to 90 s before a latch, from a message that was authentic when it was checked. Other operator messages wait meanwhile, as they do behind `!details full`. | Re-uses the r13 serialisation. A latch is a stop for new actions; tearing down an in-flight write is less safe than finishing it (r1-5). |
| R12 | **The outcome is read back, not parsed.** After the decision run, whatever its exit status, admind runs `--json` again; this waits for the per-bead lock, so any writer has finished. The read-back is compared with the **persisted attempt** (R23):<br>- closed, `decision`, `{approved,denied}_by`, `{approved,denied}_digest` and `via_ref` equal to the attempt's: `approved`/`denied`;<br>- open with none of the decision fields: nothing was recorded, and the ask returns to `open`;<br>- open with any decision field, or closed with fields that are not the attempt's: `blocked`. The relay takes no further decision on that ask, and the operator is told to resolve it at the terminal;<br>- the read fails: `uncertain`, which also accepts no further decision; the next restart or `!asks` retries the read-back.<br>For an approval, the read-back also carries `gate_valid` and `gate_reasons` (btq's `approval_valid` on the closed bead), and the reply says whether btq's design gate accepts it. "Recorded" and "the gate accepts it" are different lines. The first line of `approve-bead`'s stderr (redacted, at most 300 characters) is quoted in the thread. | As §5.4: "the decision exists only once the read-back matches". Update, comment and close are separate `bd` writes, so a partial write must block a retry instead of inviting one (r1-4). |
| R13 | A plain reply to a `question` or `merge` card is its answer. A plain reply to an `approval` card is stored as a **note** that the poster sees. admind answers "Noted; this is not a decision", with the two commands. A reply such as "yes" never approves. | The requirement says so, and a note lets the operator ask for more context from the phone. |
| R14 | A reply to a card is never pasted to the admin agent. Every other message, replies to other messages included, behaves exactly as in revision 13. | Before this change no card existed, so nothing else changes. |
| R15 | Limits:<br>- at most 20 active asks (`open`, `answered`, `deciding`, `uncertain`), and at most 30 posted in any 60 minutes. `blocked` is terminal and does not count (r2-4);<br>- an answer or note of at most 16,000 characters; at most 50 answers and notes per ask, and at most 64,000 characters of them in total;<br>- a card of at most 40 lines of rendered context and 3,500 characters;<br>- at most 2 posts in flight on the socket at once (the third is refused "busy").<br>Anything over a limit is refused with a fixed message. | Bounds a flood from a misbehaving local process (for example a prompt-injected agent) and keeps a card readable on a phone. The answer total bounds a `get` reply (R24). |
| R16 | The poster names itself with `--from LABEL` (`[a-z0-9][a-z0-9._-]{0,31}`, default `local`). The card shows it as "posted by LABEL (a local process; unverified)". The audit also records the peer PID from `SO_PEERCRED` where the platform has it. | Any same-user process can post, so the label can be faked. The card says so, and the PID helps trace a fake ask afterwards. |
| R17 | Asks are posted only while admind may post (§8 gates: not latched, operator seen, group verified). An ask posted while latched is **refused**; an ask posted before the join signal is queued like any notice. `get`, `list` and `cancel` work while latched. | A latched admind acts on nothing; refusing tells the poster at once instead of leaving it waiting. |
| R18 | What is posted goes through `Admind.post` (one redaction, B1) and `chunk.split`. What is stored is the poster's text as given. Answers are returned to the poster verbatim. The audit records asks by length and by IDs, and operator text whole (as for every inbound message, revision 13). | The same output safety as every other admind post. The answer is the operator's own words to a local process of the same user, like a prompt to the admin agent. |
| R19 | The note given to `--deny` is the operator's reason after `redact`, at most 1,000 characters, passed as one `--note=` argument. Every argument admind passes uses the `--flag=value` form, and the bead ID must match `[a-z0-9]{1,16}-[a-z0-9.]{1,32}`. | Bead comments are synced. A value starting with `-` must never become an option. |
| R20 | The provenance recorded by `approve-bead` is `via=marmot` and `via_ref=marmot:<ref>`. `<ref>` is `audit.ref_id` of the operator's message ID (`id:` and 12 hex of its SHA-256), the same form admind's audit records, so the bead and the audit line up. btq's `approval_valid` does not read `via`, and `via_ref` does not end in `_digest`, so it changes no gate. | It is the minimal change the requirement asked for. A raw 64-hex message ID would be redacted in every admind output anyway. |
| R21 | **An approval card must survive redaction unchanged.** admind renders the card with digests written deliberately as 12-hex prefixes (the root digest, and each linked bead's `pinned digest` line, which `approve-bead` prints in full, rewritten by one exact-match rule). Then it splits the card and the `!details` text into the **exact chunks that will be queued** (`chunk.split(redact(text), chunk_chars)`, as `reply` does). It checks `redact(chunk) == chunk` for every chunk, which is what `Admind.post` applies to each one, and `redact(text) == text` for each whole text. A split can create a match that the whole text did not have (r2-1). If any check fails, the ask is refused at post time with "this bead holds text admind would redact; decide it at the terminal". Nothing is posted, and the poster gets the reason. The checked chunks are the ones queued, so what is delivered is what was checked. Question and merge cards are redacted as usual, because they decide nothing; their budget is counted after redaction. All texts are redacted whole, then split. | Two different texts can redact to the same marker, and an unterminated PEM header hides everything after it, including the decision lines. Shown content must equal hashed content, or the typed digest proves nothing (r1-3). |
| R22 | **Posting is serialised.** Posts take an `asyncio.Lock` (`ask_post_lock`) for their whole run, `--json` read included. After the read, still under the lock, admind re-checks the latch, the limits and the same-bead state before inserting the ask. If another ask for the bead is `deciding` or `uncertain`, the post is refused. A `blocked` ask needs no rule: its bead holds decision fields, which `postable` refuses. An `open` or `answered` ask for the bead is superseded (R9). The status check and the `open → deciding` transition of a decision happen in one transaction with no await between them, so they cannot interleave with a supersede. | Concurrent posts could all pass the limits before their reads finished (r1-7). Posts are rare, so a lock costs nothing. |
| R23 | **A decision attempt is persisted before the child starts.** One transaction holds the inbound row `executing`, the ask `deciding` with `asks.attempt` set to the operator's message ID, and a new `ask_attempts` row: the ask, the message ID, the action, the operator's name, `ref`, the full digest and the redacted note. `asks.attempt` names the **current attempt**; reconcile and settle use only that row. An attempt's `settled` is NULL while it is unresolved, and `uncertain` while it is unresolved after a failed read-back. Every terminal value (`refused` before launch, `recorded`, `untouched`, `blocked`) is written in the same transaction as the ask's new status, the clearing of `asks.attempt` and the reply or notice. Each such transaction is conditional: it updates only if the ask still has that status and that `attempt` (compare-and-set), and otherwise it changes nothing and audits `ask/conflict`. Before any settlement, the child is reaped: on every exit path, its own exit included, admind kills its process group (a group already gone is fine), drains both pipes and waits for it, bounded at 5 s, then closes the pipes (r4-1). A descendant that outlives the parent is killed too, and `approve-bead` passes its lock to every `bd` child, so the R6 lock is held until the last possible writer exits (r3-1). | A restart must be able to tell the attempt's outcome from someone else's (r1-2). Reaping first means admind never reopens an ask while its writer is alive, and the lock in R6 covers a child that outlived admind itself. |
| R24 | **Replies on `ask.sock` are bounded.** A reply is at most 1 MiB, and the client reads with a matching limit. `get` returns one ask and all its answers (at most 64,000 characters, R15). `list` returns summaries without answers: every active ask (R15), and the 20 most recent others. | A request limit alone does not bound replies; the default stream limit is 64 KiB (r1-9). |
| R25 | **Refs are readable from the phone, or the card says they are not.** `approve-bead --json` adds `links`: for each numbered file, commit or range ref whose repo has a `github.com` remote (`git remote get-url origin`, HTTPS or SSH form), a permalink pinned to the ref's commit (`/blob/<sha>/<path>` with the path percent-encoded, `/commit/<sha>`, `/compare/<a>...<b>`). The card shows the link under the ref. A ref without one shows "(no forge link; read it on the host with approve-bead --doc N)". A link does not prove the commit is pushed; a 404 means the operator should not approve from the phone. | §5.9 asks for clickable pinned links wherever a forge exists (r1-13). |
| R26 | **Recovery is serialised with decisions.** At startup, before any loop starts, `run()` takes a snapshot of the asks in `deciding` or `uncertain`, with each one's status and current attempt; the compare-and-set expects that status (r3-2). The reconcile task works only on that snapshot. It holds `work_lock` for each ask, so it cannot run while the worker is deciding. A retry of `uncertain` asks from `!asks` runs in the worker, under `work_lock` too. With the compare-and-set of R23, a reconcile can never reopen an attempt that a live decision owns. | A decision started after startup is not in the snapshot, and it holds `work_lock` for its whole run (r2-3). |

## 5. Data model

New tables and one index in `admind.db`, created with `CREATE … IF NOT EXISTS`. An existing database needs no migration, and the old code ignores them:

```sql
CREATE TABLE IF NOT EXISTS asks (
    ask_id TEXT PRIMARY KEY,                    -- R3
    kind TEXT NOT NULL CHECK (kind IN ('question', 'merge', 'approval')),
    poster TEXT NOT NULL,                       -- R16 label
    peer_pid INTEGER,                           -- audit only
    title TEXT NOT NULL,
    body TEXT NOT NULL,                         -- question/merge context; approval: the --json readout, as JSON
    pr_url TEXT, head_sha TEXT,                 -- merge
    bead TEXT, digest TEXT,                     -- approval: the full digest the card shows the prefix of
    truncated INTEGER NOT NULL DEFAULT 0,       -- the card left something out (R8)
    card_parts INTEGER NOT NULL,                -- how many ask:<id>:<i> chunks were queued (R8)
    attempt TEXT,                               -- the current attempt's message ID while deciding/uncertain (R23)
    status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'deciding', 'approved', 'denied', 'stale',
                                           'superseded', 'cancelled', 'blocked', 'uncertain')),
    outcome TEXT,                               -- fixed wording plus the quoted stderr line (R12)
    decided_by TEXT,                            -- the operator's policy name
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ask_answers (
    ask_id TEXT NOT NULL, seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('answer', 'note')),
    operator TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE, text TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (ask_id, seq));
CREATE TABLE IF NOT EXISTS ask_details (        -- who asked for an ask's !details, with which message (R8)
    ask_id TEXT NOT NULL, operator TEXT NOT NULL, message_id TEXT NOT NULL,
    parts INTEGER NOT NULL,                     -- how many askd:<id>:<mid>:<i> chunks were queued
    PRIMARY KEY (ask_id, message_id));
CREATE TABLE IF NOT EXISTS ask_attempts (       -- one decision attempt, persisted before the child starts (R23)
    ask_id TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('approve', 'deny')),
    operator TEXT NOT NULL, ref TEXT NOT NULL, digest TEXT NOT NULL, note TEXT,
    started_at TEXT NOT NULL, exit_status INTEGER,
    settled TEXT CHECK (settled IN ('refused', 'recorded', 'untouched', 'blocked', 'uncertain')),  -- NULL: unresolved
    PRIMARY KEY (ask_id, message_id));
CREATE INDEX IF NOT EXISTS outbox_message_id ON outbox(message_id);
```

Outbox keys (existing `outbox` table, unchanged schema):

- `ask:<id>:<i>`: the card's chunks, lane 1, unthreaded;
- `askd:<id>:<request-mid>:<i>`: the `!details` of an ask, lane 2, threaded to the `!details` message;
- `asknote:<id>:<what>:<mid-or-n>:<i>`: notices about an ask (superseded, cancelled, reconciled), lane 1, threaded to the card's first sent chunk;
- replies to an operator's message use the existing `reply()` keys, with the tag `ask`.

`Store.ask_for_message(message_id)` maps a delivered message to its ask: the **sent** outbox row with that `message_id` whose key starts with `ask:` or `askd:`. Pending and failed rows never count, and neither do `asknote:` rows or any other key. This is the R7 check.

`Store.card_delivered(ask_id)` is true when rows `ask:<id>:0` … `ask:<id>:<card_parts-1>` all exist and are `sent`. `Store.details_delivered(ask_id, operator)` is true when, for one of that operator's `ask_details` rows, all `parts` rows exist and are `sent`.

The poster's view (`AskView`, returned by `get` and `wait`) has `ask_id, kind, status, title, bead, digest12, delivered` (the whole card is sent), the answers and notes `[{kind, operator, text, at}]`, `outcome` and `decided_by`. `list` returns `AskSummary` rows: everything in `AskView` except the answers, plus an answer count.

## 6. Message formats

All cards are posted unthreaded. Every text is redacted whole, then split into chunks (R18, R21). The first line is fixed wording built by admind. In the examples, `…` stands for elided text.

**Question:**

```
❓ Ask k7m2 · question · posted by controller (a local process; unverified)
Which relay should the test operator use?

The throwaway home needs one relay. The reference install has two: …
(30 lines shown of 52; reply !details for the rest)

Answer: reply to this message, or send !answer k7m2 <text>
```

**Merge:**

```
🔀 Ask m3qp · merge request · posted by controller (a local process; unverified)
Merge plan 3 (wsd intake)
PR: https://github.com/<owner>/<repo>/pull/17
Head: 0123456789abcdef0123456789abcdef01234567

All 4 tasks reviewed cross-model; CI green on the head above. …

Merging is yours to do in GitHub; admind never merges. Reply to this message (or !answer m3qp <text>) when it is merged, or with what to change.
```

**Approval:** the context lines are `approve-bead`'s own injective rendering (`render_value`, `show_ask`). Each hashed line is one row behind `│`, invisible and ambiguous characters are escaped, and a `╎` row follows any line holding non-ASCII. So the phone shows what the terminal readout shows. There is one deliberate change: digest lines are cut to 12 hex digits (R21).

```
🛂 Approval ask p4xw · bead btq-ab12c · posted by controller (a local process; unverified)
digest 1a2b3c4d5e6f
title:
  │ Approve the interim admind Marmot relay
effect:
  - │ admind may record btq approvals from operator Marmot replies
excludes:
  - │ merging, any change to the btq design gate
why:
  │ …
risks:
  - │ …
refs:
  - ref {"id": "…", "kind": "file", "path": "docs/adr/0001-workstreams-v2.md", "repo": "…"}  [--doc 1]
      resolved: "…"
      link: https://github.com/<owner>/<repo>/blob/<sha>/docs/adr/0001-workstreams-v2.md
description (12 lines shown of 64):
  │ …
(shortened: 52 more lines. Reply !details to this message and read it before approving.)

Decide by replying to this message:
  !approve btq-ab12c 1a2b3c4d5e6f
  !deny btq-ab12c <reason>
!approve records your approval of btq-ab12c in btq, as you, via Marmot. A reply without !approve or !deny decides nothing.
```

The card shows the title, then the ask fields in `ASK_FIELDS` order with their refs (linked beads included), then the description. The 40-line and 3,500-character budget (R15) is spent in that order. Whatever does not fit goes to `!details`, which sends the title, the whole description, the whole ask with linked beads, the links, and the digest prefix again. The decision lines are fixed framing, appended after the budgeted part, and never cut.

**Replies admind sends in thread** (fixed wording; names go through `redact`):

| Event | Reply |
|---|---|
| answer stored | `Answer recorded for ask k7m2.` (or `Added to ask k7m2; it was already answered, and the poster sees both.`) |
| note on an approval card | `Noted on ask p4xw; this is not a decision. To decide, reply to the card with !approve btq-ab12c 1a2b3c4d5e6f or !deny btq-ab12c <reason>.` |
| approved, gate accepts | `Approved btq-ab12c as <name> (digest 1a2b3c4d5e6f, via Marmot). btq's design gate accepts it.` |
| approved, gate rejects | `Approved btq-ab12c as <name> (digest 1a2b3c4d5e6f, via Marmot), but btq's design gate rejects it: "<first gate reason>". Check it on the host.` If `gate_reasons` is empty (some `approval_valid` checks give no reason), the quote is replaced by `(btq gave no reason; run approve-bead btq-ab12c on the host)`. |
| denied | `Denied btq-ab12c as <name> (via Marmot).` |
| not a reply to the card | `To decide, reply to the approval card itself (or its !details). Nothing recorded.` |
| wrong bead or digest | `That does not match ask p4xw (bead btq-ab12c, digest 1a2b3c4d5e6f). Nothing recorded.` |
| card not fully delivered | `Ask p4xw has not been fully delivered yet. Wait for every part, then reply again. Nothing recorded.` |
| needs !details | `Ask p4xw was shortened. Reply !details to it and read it first. Nothing recorded.` |
| bead changed | `btq-ab12c changed after it was shown (shown 1a2b3c4d5e6f, now 9f8e7d6c5b4a). Nothing recorded; ask p4xw is stale and the poster must post it again.` |
| not an approver | `<name> is not a btq approver. Nothing recorded.` |
| already decided / superseded / not open | `Ask p4xw is already <status>[ by <name>][; see ask q9rt]. Nothing recorded.` |
| approve-bead refused, bead untouched | `Not recorded: "<stderr line>". btq-ab12c is unchanged; you can decide again.` |
| partial or foreign write (`blocked`) | `btq-ab12c holds a decision admind cannot confirm as yours ("<stderr line>"). Nothing more will be done from Marmot. Resolve it on the host with approve-bead btq-ab12c.` |
| uncertain | `admind could not read btq-ab12c back. No further decision is taken from Marmot until it can; check it on the host with approve-bead btq-ab12c.` |
| busy | `btq-ab12c is being decided elsewhere right now. Nothing recorded; try again in a minute.` |
| not configured | `Approval asks are not configured on this host ([admind] approve_bead).` |

**New `!` commands** (`commands.HELP` gains `!asks · !answer <id> <text> · !approve <bead> <digest> · !deny <bead> <reason>`):

- `!asks`: the active asks (R15), one line each: `k7m2 question · 3h · <first 60 characters of the title>`. If any ask is `uncertain`, it first retries those read-backs.
- `!answer <id> <text>`: `<text>` is everything after the ID, newlines included. Refused for an approval ask, with the hint.
- `!approve <bead> <digest12>`: exactly two arguments. The digest is 12 hex digits, case-insensitive.
- `!deny <bead> <reason>`: the reason is everything after the bead and must not be empty.
- `!details` as a reply to a card or its details: the ask's full text (R8). `!details full` on a card is the same.

## 7. State machine

```
              post (lint ok, R21 ok)
                   │
                   ▼
     ┌──────────► open ───────── poster cancel ─────────► cancelled
     │            │  │  │
     │            │  │  └─ newer ask for the same bead ──► superseded
     │            │  └──── answer / !answer (question, merge) ──► answered ── more answers stay answered
     │            │                                               (cancel / supersede apply as for open)
     │       !approve / !deny (all checks R7–R11 pass; attempt persisted, R23)
     │            ▼
     │         deciding ── --json shows a changed digest ──► stale
     │            │
     │            ├── read-back matches the attempt ─────────────► approved | denied
     │            ├── read-back: a decision field that is not the attempt's,
     │            │   or a partial write ────────────────────────► blocked
     │            ├── read-back fails ──────────────────────────► uncertain ── read-back retried
     │            │                                                (restart, !asks) → any outcome here
     └────────────┴── read-back: open, no decision field (nothing recorded; not an approver; busy)
```

- Notes never change the status.
- A refusal before `deciding` (not a reply, wrong digest, not delivered, not configured, needs `!details`) leaves the ask `open`.
- `blocked` and `uncertain` accept no decision from Marmot. `blocked` is terminal: the operator resolves the bead at the terminal, and the ask no longer counts toward the limits. `uncertain` stays active until a read-back settles it.
- **Restart:** an ask found in `deciding` at startup was being decided when admind stopped, and its `ask_attempts` row says what was attempted. The unit's control group kills any child when admind stops. The R6 lock also covers a child that outlived admind, because the read-back waits for it. One task reads back each ask in the startup snapshot (R26) and settles it against its current attempt (R12). Its notices are queued, and sent once admind may post. If nothing was recorded, the ask returns to `open` with the notice "admind restarted while recording this decision; nothing was recorded. Decide again." The notice is threaded to the card. The operator's command message is answered by the existing `recover()` path (`RESTARTED_NOTICE`), because its inbound row was `executing`.

## 8. Request flow

**Post an approval ask** (the ask-socket handler, under `ask_post_lock`, R22; it writes the store but does not take `work_lock`):

1. Validate the request (R4, R16, R19). Refuse if latched (R17), if `approve_bead` is not set, if over a limit (R15), or if two posts are already in flight.
2. Run `approve-bead <bead> --json`. Refuse if:
   - it fails, or prints a `busy` reply;
   - `digest` is null;
   - the status is not `open`, or the bead is not `kind:approval`;
   - there are gaps (the gaps go back to the poster);
   - `design_review` is invalid;
   - a posted `context_digest` differs from the computed digest;
   - any decision field is already set.
3. Render the card and the details. Refuse if either fails R21.
4. Re-check the latch, the limits and the same-bead state (R22). Then, in one transaction, without an await:
   - mark any `open` or `answered` ask for the bead `superseded` and queue its notice;
   - insert the ask with the full digest, the readout and `card_parts`;
   - queue the card's chunks.
5. Reply `{ask_id, digest12}`. Audit `ask/posted`, with the kind, bead, digest prefix, poster, PID and the lengths.

**Decide** (in `handle`, under `work_lock`, after the existing control-character and authorisation checks):

1. Parse. If the message is not a reply to a card (R7), refuse.
2. Load the ask. Refuse unless:
   - it is an approval ask, for the same bead;
   - its status is `open`;
   - the digest prefix matches;
   - for `!approve`, the card is delivered and, if the card was shortened, the operator's `!details` is too (R8).
3. Check that the sender is still authorised (`authorised(mid)`), and that `approve_bead` is set.
4. In one transaction:
   - set the inbound row to `executing`;
   - set the ask to `deciding`;
   - insert the `ask_attempts` row and set `asks.attempt` (R23).

   Audit `ask/deciding`.
5. Run `--json` (60 s). If the digest changed, mark the ask `stale`. Otherwise return the ask to `open` and refuse if any of the following holds. Either way, the attempt is closed as `refused` in the same compare-and-set transaction:
   - the operator is not in `approvers`;
   - the bead is not open or not `kind:approval`;
   - a decision field is set (this one goes to `blocked` instead);
   - the reply is `busy`.
6. Check `authorised(mid)` again, synchronously, with nothing awaited before the launch. If it fails, return the ask to `open`, close the attempt as `refused`, and deny the message (the existing `deny`), all in one transaction. This is the commit point (R11).
7. Run the decision (90 s, a new process group, output capped at 1 MiB). On every exit path, a normal exit included, kill the process group, drain both pipes and reap the child, bounded at 5 s, closing the pipes if that runs out, before going on (R23).
8. Read back with `--json` (R12) and settle the ask against the attempt. Then, in one compare-and-set transaction:
   - record `exit_status` and `settled`, and clear `asks.attempt` unless the outcome is `uncertain`;
   - set the ask;
   - set the inbound row to `done`;
   - queue the thread reply.

   Audit `ask/decided`, with the outcome word, the exit status, `gate_valid` and `latched_during`.

## 9. Security analysis

| Threat | Handling |
|---|---|
| **Any same-user process can post asks.** | Accepted, and stated in §3. Such a process can already run `approve-bead --yes` or `bd` itself, so posting adds no approval power. Asks are capped (R15), labelled unverified (R16) and audited with a PID. The socket is 0600 in a 0700 directory, and sandboxed agents cannot reach admind's state directory (§7, as for the `wn-agent` socket). |
| **A fake ask impersonating the controller.** | The label is shown as unverified. An approval card's content comes from the bead, not from the poster, so a fake poster can only ask the operator to decide a real bead, rendered faithfully. A fake question can mislead, but the operator's answer only goes back to local processes. |
| **A card look-alike in the admin agent's reply.** | R7: only admind's own `ask:`/`askd:` rows count as cards. A reply to a look-alike is refused. |
| **Free-text "yes".** | R13: it is a note, never a decision. |
| **Replay of an old `!approve`.** | A message ID is accepted once (the existing `inbound` table, kept forever; `ask_attempts.message_id` is unique). A new message with the same text meets a closed bead (refused), a superseded ask (R9) or a changed digest (stale). |
| **The digest changes between render and approval.** | admind's `--json` read refuses (stale). A change after that read is refused by `approve-bead --expect-digest`, at its read and at its pre-write re-check, under the per-bead lock (R6). A direct `bd` edit after that re-check makes the recorded approval fail `approval_valid` (a conflicting `*_digest`). `approve-bead`'s post-write read reports it (exit 5), and the reply says the gate rejects it. |
| **Two writers decide one bead at once (relay and terminal).** | The per-bead lock and the undecided re-check under it (R6). The second writer is refused, and its ask is settled from the read-back. |
| **Content hidden by redaction.** | R21: an approval card that redaction would change is never posted. The digest prefixes are formatted deliberately, and the fixed decision lines come after the budgeted context, so they cannot be cut or swallowed. |
| **A prefix collision on the 12 digits.** | The typed digits only select the card; `approve-bead` is given the full digest (R6). |
| **Latched admind.** | The guard drops every operator message (existing), and posting is refused (R17). A decision already launched finishes and is audited with `latched_during` (R11). |
| **A removed operator.** | Their key leaves `Admind.operators` when the removal commits (B21), so the guard drops their message. A host removal waits for `work_lock`, so it cannot interleave with a decision (R11). `authorised(mid)` is re-checked after every await, and synchronously at the launch. |
| **A test operator who is not a btq approver.** | Refused before `approve-bead` is run (R10). `approve-bead` would refuse too (`--as` must be in `approvers`). |
| **Argument injection into `approve-bead`.** | Argv list with no shell, `--flag=value` form, and the bead ID pattern (R19). |
| **A huge or hostile bead.** | The `--json` output is capped at 8 MiB and parsed with a strict schema. Rendering is bounded (R15), and `!details` uses the existing lane 2. Text is rendered by `approve-bead`'s escaping, and must then pass R21. |
| **Rate and size abuse.** | R15, R22, R24. A refused post costs no Marmot message. |
| **`approve-bead` fails, hangs or writes partly.** | Timeouts with a process-group kill and a reap, then a read-back under the lock, compared with the persisted attempt (R12, R23). A partial write is `blocked`, never "nothing recorded", so it cannot invite a conflicting retry. An open bead with partial metadata fails `approval_valid` anyway. |
| **Audit.** | New `ask` records: `posted`, `refused` (with a reason word), `superseded`, `answered`, `noted`, `details`, `deciding`, `decided` (outcome, exit status, `gate_valid`, `latched_during`), `reconciled`, `cancelled`. They carry IDs, lengths and names. The operator's text is in the existing `inbound` record. Everything goes through `audit.clean`. |
| **Secrets in answers or notes.** | Posts and the audit are redacted. Answers are returned to the poster verbatim (R18), like any prompt to the admin agent. A deny reason is redacted before it reaches the bead (R19). |
| **A swap of a group member (the residual risk of §3.4).** | A swapped-in key is not in `policy.toml`, so the guard drops its messages: it cannot answer or approve. The residual risk grows only on the reading side. It can read cards, approval context included, and see that approvals happen. §10 records this. |

## 10. Relationship to ADR revision 14

Revision 14 folds in three things: Marmot-only operation (`btq-xv48a`), OpenShell sandboxing, and an accounts amendment. This relay is interim, and the r14 editor has three choices:

- keep it as admind's part of Marmot-only operation (the controller's questions and approvals);
- extend it to the admin agent's own option pickers and permission prompts (the `btq-xv48a` finding), which would post through the same ask path;
- once `wsd`'s §5.4 decision queue exists, either retire the approval kind or make admind submit to that queue. §5.4 makes `wsd` the only writer of decision fields, and the interim relay writes them through `approve-bead`, as a human does today.

**ADR §8 delta for the r14 editor.** Add after "Built-in commands":

> - **Asks and approvals relay (interim, 2026-10-05; to be reconciled with §5.4 when `wsd` ships):**
>   - Local processes running as the service user post asks over a second host socket (`ask.sock`, 0600; `admind ask post|get|wait|list|cancel`). The kinds are `question`, `merge` (a full PR URL and pinned head SHA; merging stays the operator's action) and `approval` (an open `kind:approval` btq bead). Each ask must carry its context; an approval ask's context is the bead, read through `approve-bead --json`, and a bead with gaps is refused.
>   - admind posts each ask as a card with a short ID and redacts it like every post. The operator answers by replying to the card or with `!answer <id> <text>`; the poster reads the answer with `admind ask get|wait`.
>   - An approval card shows the bead's hashed content as `approve-bead` renders it, pinned forge links for its refs where a forge exists, and the first 12 characters of its `context_digest`. A bead whose content admind would redact is not posted; it is decided at the terminal. Only a reply to that card, `!approve <bead> <digest12>` or `!deny <bead> <reason>`, decides. The sender must be a current operator whose name is a btq approver. The whole card, and for a shortened card its `!details`, must have been delivered to the approver first.
>   - admind persists the attempt, re-reads the bead, and runs `approve-bead --as <name> --yes --expect-digest <full> --via marmot --via-ref <ref>`. Every `approve-bead` decision holds a per-bead lock and re-checks the bead under it. admind then reads the outcome back, compares it with the attempt, and reports in the thread whether it was recorded and whether btq's design gate accepts it. A partial or unconfirmed write blocks further Marmot decisions on that bead. A plain reply is a note, never a decision.
>   - **Trust change, accepted by the operator:** approval authority for btq beads extends from the terminal to an MLS-authenticated Marmot reply from an approver's key. admind's own dependency on beads is limited to this relay. Without `[admind] approve_bead`, approval asks are refused and everything else works.
>   - The §3.4 residual risk (a count-preserving member swap) now also covers reading approval context. A swapped-in key is not an operator, so it still cannot answer or approve.

**ADR §5.9 delta (interim exception).** Add at the end of §5.9:

> Interim exception (2026-10-05, until `wsd` and the gatekeeper ship): approval asks posted through admind's relay (§8) skip the gatekeeper's sufficiency judgement and enrichment. Instead:
>
> - btq's deterministic lint (`approve-bead` gaps, `design_review` validity, a posted `context_digest`) gates posting;
> - the whole hashed content is shown, unredacted or not at all;
> - refs get pinned GitHub permalinks where the repo has a GitHub remote, and are otherwise marked "read on the host";
> - the approver's typed digest prefix replaces the confirmation.
>
> The operator accepts that a phone approval rests on their own reading, without a gatekeeper verdict. Revision 14 decides whether the gatekeeper step becomes mandatory for relayed asks.

Also in §5.4, after "The existing Hermes `/approve`, `/deny` and reaction commands are kept as the interface", add: "Until `wsd` ships, admind's interim relay (§8) is the Marmot surface for btq approvals. It records `via=marmot` through `approve-bead`. Revision 14 decides whether it is retired or feeds the decision queue."

## 11. What must not break

- `ctl.sock`: its limits, requests, refusals and replies are unchanged (R1), and the parameterised server keeps them as they are.
- `!new`, `!interrupt`, `!tail`, `!restart`, `!ps` and `!details` (on summaries and batches) keep their behaviour. HELP gains the new commands, and the ready notice, which embeds HELP, changes accordingly.
- Passthrough is unchanged apart from R14: a reply to a summary or to an agent message still goes to the agent.
- Summaries, the backstop, lanes, the latch, membership transitions and `rearm` are untouched. Cards and notices are lane-1 posts, and ask `!details` is lane 2.
- Schema: only new tables are added (§5). An older admind ignores them.
- Config: `approve_bead` is a new optional key. **Rollback note:** an older admind rejects unknown `[admind]` keys (exit 78), so remove the key before rolling back.

## 12. Testing

No test touches the network, a real `wn-agent`, the real `claude`, `~/.claude`, a real btq queue or `~/.config/beads-task-queue`.

- **admind unit tests:**
  - `asks.validate` (every R4/R16/R19 bound);
  - the renderers (budget, the shortened flag, fixed wording, digest lines cut to 12 hex, the R21 check, links);
  - `commands.parse` for the four new commands (a multi-line remainder, a bad digest, an empty reason, wrong arity);
  - the store (`ask_for_message` counts only sent `ask:`/`askd:` rows, the rate and open counts, supersede, answers);
  - `approvals.settle` (a pure mapping from the read-back and the attempt to the outcome);
  - the shared `JsonSocketServer` (`ask.sock` takes 256 KiB, `ctl.sock` still refuses over 4096 with its existing replies and audit).
- **admind integration tests**, using the existing `Harness` with the fake `wn-agent` and a fake `approve-bead` script in `tmp_path` that serves bead JSON from a file and logs its argv:
  - a question answered by reply and by `!answer`;
  - a merge ask that is missing its PR (refused);
  - the approval happy path, checking the exact argv;
  - a decision that is not a reply, or a reply to an agent message;
  - a wrong digest;
  - a digest change at admind's read, and at `approve-bead`'s read (the fake honours `--expect-digest`);
  - an operator who is not an approver;
  - latched, and a removed operator;
  - a shortened card without `!details`, then after it;
  - "yes" as a note;
  - a replayed message ID;
  - a closed bead;
  - deny with a reason;
  - an `approve-bead` failure;
  - a timeout (`uncertain`);
  - a restart while `deciding`;
  - supersede;
  - the rate and open limits;
  - a post while latched;
  - `ask wait` across a daemon restart;
  - a card whose last chunk is pending or failed (approve refused);
  - a bead whose readout contains a token-shaped string, a 64-hex run outside the digest lines, or an unterminated PEM header (post refused, nothing posted);
  - concurrent posts at the limit (exactly the limit admitted), a latch during a post's `--json` read (refused), and a post for a bead whose ask is `deciding` (refused);
  - a latch and a lost subscription during the decision run (the decision completes, `latched_during`, no reply until rearm);
  - a partial write and a foreign decision (`blocked`; later `!approve` refused);
  - the read-back after a restart compared with the current attempt; refused → retried → restart (only the current attempt is used); `uncertain` → a successful reconcile; a reconcile racing a live decision paused after its attempt was inserted (the live attempt is not touched);
  - a card and a details text whose whole form survives redaction but one chunk does not (refused at post);
  - posting after 20 `blocked` asks (admitted);
  - a gate rejection with empty `gate_reasons` (fallback wording);
  - `get` and `list` replies near the 1 MiB bound, and a malformed or failing `ask.sock` request (fixed refusal, `ctl.sock` replies unchanged);
  - The whole existing suite must stay green.
- **beads-task-queue tests** run offline: a fake `bd` serves fixture beads from a JSON file in a temp directory, with `HOME`, `BTQ_CONFIG_DIR` and `BTQ_POLICY` pointed at temp files. They cover:
  - `--json` (fields, readout lines equal `render_value`, no writes);
  - `--expect-digest` (a mismatch at the first read and at the re-check writes nothing; a match writes);
  - `--via marmot --via-ref`, recorded and shown in the comment;
  - `--via-ref` without `--via marmot`, and a bad ref, refused;
  - the default stays `via=cli`;
  - `approval_valid` accepts a `via=marmot` approval;
  - `--json` with a missing or malformed `metadata.ask` (digest null, gaps listed);
  - the per-bead lock: a second decision while one holds the lock waits, then sees the decision and refuses; `--json` waits for a holder; a stale lock file with no holder does not block;
  - the pre-write re-check refuses an existing decision field, a removed `kind:approval` label and a closed bead;
  - the post-write read detects an edit that raced the write (exit 5);
  - `links` for GitHub HTTPS and SSH remotes, and none for others;
  - `bd -C` gets the resolved queue repo, which the fake checks.
  - The gate for this repo is the offline selection `python3 -m unittest tests.test_approve_bead_relay tests.test_add_agent -v`. `tests/test_queue.py` builds a real `Queue` against the shared server and mutates it, so it is not run by this work.
- **Live acceptance:** the operator's checklist is in the plan's Task 4.

## 13. Rejected alternatives

- **Reactions (👍/👎) as decisions,** as §5.4 does for `wsd`: a reaction carries no digest, so it cannot show the ask was read, and admind ignores reactions today. A later revision can add them.
- **admind writing approval metadata with `bd`:** that bypasses `approve-bead`'s checks, and two writers would drift apart.
- **Importing btq into admind:** it ties admind's process to btq's code and its Dolt connection, against §8's independence. A subprocess fails alone.
- **Raising `ctl.sock`'s limit:** that changes a pinned contract and mixes membership operations with a bulk-text surface.
- **Accepting `!approve` that is not a reply:** forged cards (R7).
- **A blocking `wait` held in the daemon:** long-lived connections, and lost on a restart.
- **Showing redacted approval content and asking the operator to check it on the host:** two different texts can redact to the same marker, so the phone could not tell them apart (R21).
- **Cancelling an in-flight `approve-bead` on a latch:** a kill mid-write leaves a partial write, which is worse than finishing a decision authorised moments earlier (R11).
- **Re-rendering a card automatically when the digest changes:** a bead that keeps changing would loop, and the poster should know its ask changed.
- **A mapping from Marmot names to btq names:** §3.4 says the names are the same entry, and a map would be one more thing to get wrong.
- **Waiting for `wsd`'s decision queue:** it is plans away, and the operator needs this now.
