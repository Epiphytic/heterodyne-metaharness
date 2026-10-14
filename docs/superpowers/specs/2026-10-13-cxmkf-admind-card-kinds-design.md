# admind: card kinds, FYI notices, structured choices, clickable links and threaded parts (design delta)

Status: r1, for review (btq-cxmkf). No implementation yet. It amends the relay design (`2026-10-05-admind-marmot-relay-design.md`, R1–R26), the reply/reaction delta (`2026-10-06-admind-relay-replies-reactions-design.md`, R27–R31) and the bump delta (`2026-10-07-admind-asks-bump-design.md`, B1–B14). It changes the ADR 0001 §8.2 admind surface, so it needs an ADR amendment (§7) as well as design review and approval.

Operator feedback, 2026-10-13, from the bead:

> FYIs must never be asks. Today post-merge/status FYIs go out as --kind question asks titled 'FYI, no answer needed (react 👍 to dismiss)'. They show approve/answer affordances, ask for a dismissal, and count as active (status answered) toward asks.MAX_OPEN=20 until the asker cancels them; 18 FYIs filled the queue and blocked a real approval ask.

> A question card must show at its top that it is MULTIPLE CHOICE (or YES/NO), not an approval, and must repeat the choices very briefly at the very bottom (e.g. 'Choices: A = ... (recommended) · B = ... · C = ...'). Reactions: 👍 = the recommended/default option, 👎 = no. Only approval cards read as approve/deny.

> Labels: the top line of each card reads 'QUESTION: Multiple Options', 'QUESTION: yes/no', 'APPROVAL: design' or 'APPROVAL: execution'; FYI notices are labelled as FYI. Approval kind (design vs execution) should be a bead or ask field that admind renders.

> (1) links must be clickable, especially GitHub PR links and links to designs, plans and docs (refs should render as URLs, e.g. GitHub blob/PR URLs pinned to the SHA, not bare repo paths); (2) continuation parts of a multi-part message (part 2/3 etc.) must be threaded as replies under the original message, not posted as new top-level messages.

## 1. Requirements

### 1.1 FYI notices

| # | Requirement | Rationale |
|---|---|---|
| K1 | **`admind notice post`** is a new client command: `admind notice post --title T [--body-file F \| --body-file -] [--link LABEL=URL]... [--ref PATH[@REV]]... [--from NAME] [--json]`. It sends a new request `NoticePost` (msgspec tag `"notice"`, `forbid_unknown_fields`) on the existing `ask.sock`, with the same peer-credential check and the same poster rule (`--from`, `POSTER`) as `ask post`. The reply is `posted <notice id>` or `refused: <why>`. A notice ID uses the ask ID alphabet and is 6 characters long, so it can never be mistaken for an ask ID (4 characters). | The bead's suggested surface. The socket, auth and poster identity already exist for asks. |
| K2 | **A notice is not an ask.** It writes no `asks` row, never counts toward `MAX_OPEN` or `MAX_PER_HOUR`, and never appears in `ask list`, `!asks`, bumps or repeats. It gets no ask ID, no status and no cancel. It is stored only in a new `notices` table (K5), for the rate limit and the audit. | The defect: 18 FYIs filled `MAX_OPEN=20` and blocked an approval. |
| K3 | **Nothing is requested of the operator.** The card has no answer footer, no reaction prompt and no `!answer` hint (K10 shows the exact text). A reaction on a notice is ignored silently, as on any non-card message today (R27). A reply to a notice is ordinary chat: it reaches the admin agent exactly as a reply to any other non-card message does (held, then pasted). `!answer <id> <text>` is addressed by ask ID and is unaffected; `!approve`, `!deny` and `!details` as replies to a notice get today's handling for a reply to a non-card message. No new routing code is needed: notice outbox keys (`notice:<id>:<i>`) are outside `_CARD_KEY`, `ask_for_message` and `bump_for_message`, and the tests (§2) pin that. | The operator asked for "no reaction or reply requested". Replies stay possible because the operator may want to talk about the news. |
| K4 | **Limits.** At most `NOTICE_PER_HOUR = 12` notices are accepted in any rolling hour (counted from `notices.created_at`), and at most `NOTICE_UNDELIVERED = 10` notices may have any chunk still `pending`. A post over either limit is refused with `busy: <n> notices in the last hour` or `busy: <n> notices not yet delivered`; nothing is queued. A refusal goes back to the poster, which decides whether to retry or fold the news into a later notice (the §8.1 originator-grooming rule). While the latch is set the post is refused (`admind is latched`), as asks are. Before the group is verified, or before an operator has been seen, the notice is accepted and stored and waits in the outbox like every other post. Both limits are module constants, not settings (open question Q7). | Notices must not become a new flood: they share lane 1 with cards and replies, and a long queue of notices would delay a card. The undelivered cap bounds head-of-line blocking when the sender is backing off. |
| K5 | **Storage.** A new table `notices(notice_id TEXT PRIMARY KEY, poster TEXT NOT NULL, peer_pid INTEGER, title_chars INTEGER NOT NULL, body_chars INTEGER NOT NULL, links INTEGER NOT NULL, parts INTEGER NOT NULL, created_at TEXT NOT NULL)`. It holds lengths, not text: the text lives only in its outbox rows, which already hold the redacted message. The notice row and every outbox row (`notice:<id>:<i>`, lane 1, chunk 0 top-level, chunks 1.. threaded by K22) are written in one transaction. Rows older than 7 days are pruned with the existing housekeeping pass. | The rate limit needs created_at; the audit needs the ID. No text means no new place to redact. |
| K6 | **Body and budget.** The title follows R4's title rule (one line, 1–200 characters, no controls). The body is optional (an FYI can be its title alone) and, when given, is at most `CARD_CHARS` (3,500) characters and `CARD_LINES` (40) lines after redaction. Over that, the post is refused (`the notice is over 40 lines / 3,500 characters; shorten it or link to the rest`). A notice is never truncated and never has `!details`, because nothing tracks it after delivery. | The originator grooms; admind never trims (§8.1). Links (K17) carry the long form. |
| K7 | **Redaction.** The whole notice text is redacted before it is measured and split, and each chunk is redacted again at delivery, as for every outbox row. A link that redaction would change is refused (K19). | Same rule as cards (P5). |
| K8 | **Audit.** One `notice` record per request: `{kind: "notice", action: "posted" \| "refused", notice_id, poster, peer_pid, title_chars, body_chars, links, parts, why}` (`why` only on a refusal, `notice_id` and `parts` only when posted). Never the title or body text. Delivery is audited by the normal `sent`/`failed` records of its outbox rows. | Untruncated audit (r13), and R18's no-text rule. |
| K9 | **Migration away from FYI-as-question.** The controller docs and the agent memory that tell posters to send FYIs as `--kind question` asks are changed to use `admind notice post` (outside this repository; listed in the implementation plan as a follow-up, not code). FYI asks that are already active stay until their poster cancels them; admind does not guess which questions are FYIs. `docs/admind.md` §10 gets a "Notices" section. | The bead's last scope item. |

### 1.2 Card labels and rendering

| # | Requirement | Rationale |
|---|---|---|
| K10 | **Every card's first line is its label**, alone on that line, before today's head line: `QUESTION: Multiple Options`, `QUESTION: yes/no`, `APPROVAL: design`, `APPROVAL: execution`, or for a notice `FYI: no reply needed`. Merge asks are not in the operator's list; this design proposes `ACTION: merge` and flags it (Q1). Question rows from before the migration (no form, K12) render `QUESTION` alone. The label is fixed framing: it is outside the card budget, never cut, and part of the text that is redacted and (for approvals) R21-checked. | The operator's exact wording: the kind must be readable from the top line before anything else. |
| K11 | **The head line stays**, under the label, unchanged except for notices: `ℹ️ Notice · posted by <poster> (a local process; unverified)`. The notice ID is not shown on the card (there is nothing to address by ID). The `❓`/`🔀`/`🛂` heads keep their ask ID, poster and unverified marker (R30). | The ID and the unverified marker are still needed on asks; only the label is new. |

Rendered examples (chunk 0, before redaction; `…` stands for the poster's body):

```
QUESTION: Multiple Options
❓ Ask k7m2 · question · posted by team-lead (a local process; unverified)
Which runtime for the T15 rerun?

…body…

A = rerun on bel-ci (recommended)
B = rerun on laptop-ci
C = skip the rerun
Answer: 👍 = A (recommended) · 👎 = no · or reply with a letter or your own answer (!answer k7m2 <text>)
Choices: A = rerun on bel-ci (recommended) · B = rerun on laptop-ci · C = skip the rerun
```

```
QUESTION: yes/no
❓ Ask p3qz · question · posted by team-lead (a local process; unverified)
Keep the fd readlink test skipped for T15?

…body…

Answer: 👍 = yes (recommended) · 👎 = no · or reply with your own answer (!answer p3qz <text>)
Choices: yes (recommended) · no
```

```
APPROVAL: design
🛂 Approval ask m4tr · bead btq-cxmkf · posted by team-lead (a local process; unverified)
👍 approve · 👎 deny — react to any part of this card, or reply approve / deny <reason>
…readout as today…
```

```
FYI: no reply needed
ℹ️ Notice · posted by team-lead (a local process; unverified)
PR #69 merged

Plan 4b Task 11 Part A is in main.
Links:
PR #69: https://github.com/<owner>/<repo>/pull/69
```

### 1.3 Structured questions

| # | Requirement | Rationale |
|---|---|---|
| K12 | **Two question forms.** `ask post --kind question` takes exactly one of: (a) `--choice TEXT`, repeated 2–6 times, plus `--recommended LETTER` (required, exactly one). admind assigns the letters A–F in the order given; or (b) `--yes-no [--recommended yes\|no]` (default `yes`). `AskPost` gets `form: Literal["choices", "yes-no"] \| None`, `choices: list[str] = []`, `recommended: str \| None`. `check()` refuses any other combination, `--choice` on a merge or approval ask, and a question with no form (`a question needs --choice ... --recommended X, or --yes-no`); see Q2 for the alternative of an open form. Stored as new `asks` columns `question_form TEXT` and `choices TEXT` (JSON `{"choices": [...], "recommended": "A"}`). | The operator asked for the header and footer to be rendered by admind from structured data, not trusted to body text. Forcing a recommended option makes 👍 always meaningful. |
| K13 | **Choice text** is one line of 1–80 characters, with no controls and no `·`, `=` or newline (they would make the footer ambiguous). It must survive redaction unchanged; otherwise the post is refused (`choice B would be redacted; reword it`). It is fixed framing: outside the card budget, so the poster's body is what gets truncated, never a choice. Six choices of 80 characters stay well inside one chunk. | The footer is the operator's quick reference; it must be complete and literal. |
| K14 | **Rendering.** After the body: one line per choice (`A = text`, with ` (recommended)` on the recommended one), then the answer line, then, as the very last line of the card, the footer `Choices: A = text (recommended) · B = text · C = text` (yes/no: `Choices: yes (recommended) · no`). When the card is split, the choice block, the answer line and the footer are kept together in the last chunk (the split is told the footer's length, K23), so the bottom of the last part always shows the choices. `!details` (full text) and repeats render the same way. | The operator asked for the choices "at the very bottom". |
| K15 | **Reaction and reply mapping on question cards** (amends R13/R27/R28 for questions only). An emoji from the verbs approve set (👍 in every skin tone, ✅, ❤, ♥) maps to the recommended choice (`yes` for yes/no unless `no` is recommended). An emoji from the deny set (👎 in every skin tone, ❌) maps to `no`. A reply that is exactly one choice letter, folded (`a`, `B.`, `(c)`), or exactly `yes`/`no` on a yes/no card, maps to that choice. An exact approve word (`yes`, `ok`, `lgtm`, …) maps to the recommended choice; `no`/`n` alone map to `no`. Anything else, and any other emoji, is a free answer with no choice, stored verbatim as today. On a multiple-choice card, `no` means "none of these": it is stored as the answer `no` with choice `none` (Q3). | The operator's mapping: 👍 = the recommended/default option, 👎 = no. Free text stays possible so no answer is ever lost. |
| K16 | **Recording.** `ask_answers` gets a nullable `choice TEXT` column (`A`–`F`, `yes`, `no`, `none`). The answer `text` is still the operator's verbatim text or emoji. `AnswerView` gains `choice: str \| None = None`; `admind ask get`/`wait` print `answer (choice A): 👍` and `--json` carries the field. The confirmation reply on the card states the mapping: `Recorded for k7m2: A = rerun on bel-ci (from 👍).` Merge asks are unchanged (👍 still means "merged"). Only approval cards read reactions as approve/deny (unchanged). | The poster reads the decision without re-parsing emoji, and the operator sees how the reaction was read. |

### 1.4 Approval kind

| # | Requirement | Rationale |
|---|---|---|
| K17a | **`asks.approval_kind`** is a new column, `design` or `execution`. `ask post --kind approval` takes an optional `--approval-kind design\|execution`. Without it, admind derives it from the approve-bead readout: `design_review_valid is True` → `design`, `None` (the bead has no design_review) → `execution`. (`False` is already refused by `postable`.) A flag that disagrees with the readout is refused (`the bead carries a design review; this is a design approval`). | The operator asked for the kind to be a field admind renders. The readout already tells design approvals apart, so posters can't mislabel them. |
| K17b | **Where it is rendered.** In the label line (K10), which is in the approval card's `top`, so it is part of the chunks that `_checked` verifies and stores in `details`. Repeats reuse the stored chunks, so they keep the label the ask was posted with. The kind is not part of the bead digest: changing it does not make the ask stale, and approve-bead never sees it. This is stated in `docs/admind.md`. Q4 asks whether design_review is exclusive to design approvals; if not, the kind should become a bead field read by approve-bead instead, and K17a's derivation goes away. | A label must never contradict the content the operator decides on; the stored chunks make that true for repeats. |

### 1.5 Clickable links

| # | Requirement | Rationale |
|---|---|---|
| K18 | **`--link LABEL=URL`** (repeatable, at most 8) on `ask post --kind question\|merge` and `notice post`. The label is 1–40 characters of one line. The URL must be one of these pinned GitHub forms (`https://github.com/<owner>/<repo>/` then): `pull/<n>` (optionally `/files` or `/commits/<40hex>`), `blob/<40hex>/<path>`, `tree/<40hex>/<path>`, `commit/<40hex>`, `compare/<40hex>...<40hex>`, `actions/runs/<n>`, `issues/<n>`. Anything else (branch names, `main`, short SHAs, other hosts) is refused (`--link must be a GitHub URL pinned to a 40-hex commit, or a PR, run or issue`). A merge ask's `--pr` URL is rendered as a link too. | "Refs should render as URLs pinned to the SHA." A branch URL changes under the operator; a pinned one shows what was asked about. |
| K19 | **`--ref PATH[@REV]`** (repeatable, shares K18's cap of 8) is resolved by the **client** in its own working directory, not by the daemon (the daemon never reads the poster's repository): `git rev-parse --verify REV^{commit}` (REV defaults to `HEAD`), `git cat-file -e <sha>:<path>`, the `origin` URL parsed as a GitHub remote, and the commit contained in a remote-tracking branch (`git branch -r --contains`), else refused `push <sha> first: GitHub can't show an unpushed commit`. Git runs with `-c core.fsmonitor= -c core.hooksPath=/dev/null -c protocol.allow=never`, `GIT_CONFIG_NOSYSTEM=1`, no network and a 5-second timeout. The result is sent as an ordinary link (`LABEL` = the path's last component) with the path percent-encoded per segment, so the daemon validates it with K18's rules and never trusts the client's resolution. | Posters know repo paths, not URLs; resolving client-side keeps the daemon out of arbitrary repositories. Hardened git so a hostile checkout can't run code. |
| K20 | **Rendering.** A `Links:` block after the body (before the choices), one link per line: `<label>: <url>`, the URL last on its line with no brackets, quotes or trailing punctuation, so client linkifiers find its end. Approval cards keep R25's `link: <url>` lines. A link line is never split across chunks (K23). Links are fixed framing outside the card budget; a link that redaction would change is refused (K7). | Clickability depends on the client's linkifier; a bare URL on its own line is the form every Marmot client tested linkifies (Q5 asks which links failed). |
| K21 | **Bare repo paths.** The client lints the title and body for bare tracked paths (`docs/…\.md`, `src/…`, `tests/…`, `plans/…` that exist in the client's repo) and refuses with `use --ref <path> so it renders as a link (found docs/…)`. Q6 offers warn-only instead. Approval descriptions are digest-bound bead text: their bare paths are btq grooming (approve-bead's `links`), out of scope here. | The operator's complaint was bare paths; a lint at the source fixes it without the daemon rewriting text. |

### 1.6 Threaded continuation parts

The send path supports threading: `send_final(account, group, text, reply_to, key)` takes `reply_to_message_id_hex`, the S4 spike proved it in both directions, and bumps and `!details` already use it. What is missing is a target for continuation chunks: card chunks are queued together before chunk 0 has a message ID, so all of them are queued with `reply_to = None` and go out top-level.

| # | Requirement | Rationale |
|---|---|---|
| K22 | **`outbox.thread_key`** is a new nullable column. When a post's chunk 0 is top-level (an ask card `ask:<id>:0`, a repeat `askr:<id>:<mid>:0`, or a notice `notice:<id>:0`; alerts are one chunk already), chunks 1..n are queued with `reply_to = NULL` and `thread_key` = chunk 0's key. At send time, the outbox resolves the target: if the `thread_key` row is `sent` with a message ID, that ID is used as `reply_to` and written to the row's `reply_to` before sending (so a crash and retry use the same target); if it is `failed`, the chunk goes out top-level and a `thread_fallback` audit record is written; if it is still `pending`, the chunk waits (it is behind chunk 0 in seq order on the same lane, so in practice chunk 0 is always resolved first). Chunks that already have a `reply_to` (replies, bumps, `!details`) are unchanged. | The operator's request. The stored message ID makes the target crash-safe; the failed-chunk fallback means a lost chunk 0 never stalls the rest. |
| K23 | **Part marker and split.** Each continuation chunk starts with `↳ part <i>/<n>`; chunk 0 of a multi-part post ends with `(continued in replies, <n> parts)`. The split reserves room for the marker so a marked chunk never exceeds `chunk_chars`. The split is also changed to never cut inside a line that is a link line or the K14 choice block when that line fits in a chunk (it cuts before the line instead). For approval cards the markers are part of the `_checked` chunks. | A client without threading still shows how the parts belong together; a cut URL is not clickable. |
| K24 | **Reactions and replies on any part still count** (R27, R28, B12 unchanged): continuation chunks keep their `ask:`/`askr:` keys and `_CARD_KEY` match. Bumps (B3/B13) still target chunk 0. | Threading changes where parts appear, not what they mean. |

### 1.7 Compatibility and schema

| # | Requirement | Rationale |
|---|---|---|
| K25 | **Wire compatibility.** `AskPost` and the new `NoticePost` use `forbid_unknown_fields`. A new client talking to an old daemon gets a clean decode refusal (`refused: unknown field "form"`); an old client talking to a new daemon still works for approvals and merges, but an old-style question (no form) is refused per K12 until the poster upgrades. The client and daemon ship in one release. | No silent misreads across versions. |
| K26 | **Migration**, in the store's existing migration step (one transaction, each `ALTER TABLE ... ADD COLUMN` guarded by `PRAGMA table_info`): `outbox.thread_key`, `asks.question_form`, `asks.choices`, `asks.approval_kind`, `ask_answers.choice`, and `CREATE TABLE IF NOT EXISTS notices`. Existing rows keep NULLs: legacy questions render `QUESTION` (K10) and map reactions as today; legacy approvals render `APPROVAL` without a kind (their stored chunks are reused unchanged by repeats, so they keep today's text); existing pending outbox rows have no `thread_key` and send as today. | Additive only; no backfill; no ask changes meaning at deploy. |
| K27 | **HELP and docs.** `docs/admind.md` §10 describes notices, labels, forms, mapping, links and threading; `docs/configuration.md` is unchanged (no new settings). `admind ask post --help` and `admind notice post --help` show the new flags. | |

## 2. Tests

### 2.1 Offline

`tests/test_admind_notices.py`:
- A notice posts with no `asks` row, is absent from `ask list` and `!asks`, and does not count toward `MAX_OPEN`: with 20 active asks, a notice still posts and an approval is still refused for MAX_OPEN as before; with 19 active asks and 5 notices, an approval posts.
- Card text: label line, `ℹ️ Notice` head, title, body, links; no `!answer`, no reaction prompt, no ID.
- A reaction on a notice chunk is ignored (no answer, no audit beyond the inbound record); a plain reply is held and pasted to the admin agent; `!approve`/`!details` as a reply get today's non-card handling.
- `NOTICE_PER_HOUR` and `NOTICE_UNDELIVERED` refusals; nothing queued on refusal; latch refusal; accepted while unverified and delivered after verification.
- Over-budget body refused; title rule; body optional.
- Redaction: a secret in the body is redacted in the outbox row; a link redaction would change is refused.
- Audit records carry lengths and never text (search the audit file for the title).
- Notices older than 7 days are pruned.

`tests/test_admind_question_forms.py`:
- `check()` matrix: choices 2–6 with exactly one recommended; yes/no with default; refusals for 1 or 7 choices, missing `--recommended`, unknown letter, `--choice` on merge/approval, no form, choice text with `·`, `=`, controls, over 80 characters, or changed by redaction.
- Rendering: label, choice block, answer line, footer as the very last line; a long body is truncated and the choices are not; a split card keeps the choice block and footer in the last chunk.
- Mapping table (property test over skin tones and folds): approve-set emoji → recommended; deny-set → `no`; `a`, `B.`, `(c)` → letter; `yes`/`no` on yes/no; approve words → recommended; `no` on a multiple-choice card → `none`; other emoji and free text → no choice, verbatim text. Confirmation text states the mapping.
- `ask get`/`wait` print and `--json` carry `choice`; legacy answers show none.
- Legacy question rows (NULL form) render `QUESTION` and keep today's reaction handling.

`tests/test_admind_approval_kind.py`:
- Derivation: `design_review_valid` True → design, None → execution; a disagreeing flag refused; the label is in the checked chunks; a repeat reuses them; the kind does not affect the digest or staleness.

`tests/test_admind_links.py`:
- K18 allow/deny table (pinned forms accepted; branch, short SHA, other host, `http:`, embedded credentials, query strings refused).
- `--ref` resolution in a temporary git repo with a fake GitHub `origin` and a fake remote-tracking branch: path to blob URL with percent-encoding; unpushed commit refused; missing path refused; non-GitHub origin refused; the hardened `-c` options are passed (assert on the argv); timeout handled.
- Bare-path lint refuses `docs/x.md` when it is tracked and ignores it when not.
- Split never cuts a link line or the choice block when they fit.

`tests/test_admind_threading.py` (fake transport recording `reply_to`):
- A three-part card: chunk 0 top-level, chunks 1 and 2 reply to chunk 0's message ID; markers `↳ part 2/3`, `↳ part 3/3`; every chunk ≤ `chunk_chars` at the 200-character minimum.
- Chunk 0 failed → later chunks top-level plus a `thread_fallback` audit record.
- Crash after resolving the target but before sending: the retry uses the stored `reply_to`.
- Repeats and notices thread the same way; bumps and `!details` unchanged.
- A reaction on part 3 still decides an approval and answers a question.

`tests/test_admind_store.py` additions: migration from a database built with the previous schema (fixture created by the old SCHEMA string) adds every column once, keeps rows, and is idempotent on restart.

Fixtures use short placeholder IDs, never 64-hex strings (gitleaks).

### 2.2 Live (`tests/live/`, `HZ_LIVE=1`, run by the controller, not in CI)

One run in the test group: a three-part question card, a notice with a PR link and a blob link, and a design approval. Check on the operator's phone and laptop clients that the parts appear as replies under chunk 0, that the links open, and that 👍 on part 3 of the question records the recommended choice.

## 3. Risks

- **Client threading display.** Some clients may collapse replies, so parts 2..n are hidden until the operator opens the thread. The `(continued in replies, <n> parts)` line on chunk 0 says so. If the operator prefers top-level parts on one client, that is a per-client issue we cannot fix in admind.
- **Linkification is client-side.** A pinned URL on its own line is the best admind can do. If a client does not linkify GitHub URLs at all, nothing in this design fixes it (Q5).
- **Head-of-line blocking.** A continuation chunk waits for chunk 0; chunk 0 in backoff already blocks the lane today, so no new stall, but notices add lane-1 traffic. K4's undelivered cap bounds it.
- **Wire lockstep.** Posters with an old client can no longer post open questions (K12, K25). The controller docs change in the same release.
- **Approval kind derivation.** If some execution approvals carry a design_review, K17a mislabels them as design; Q4 resolves this before implementation.
- **Mapping surprises.** An operator who reacts ❤ to mean "thanks" on a question records the recommended choice. The confirmation reply makes that visible and a later reply overrides it (answers append, as today).
- **`--ref` runs git in the poster's checkout.** Hardened options and a timeout limit it; it runs as the poster, never as the daemon.

## 4. Out of scope

- The §8.1 picker kind (number-emoji options). It should reuse K12–K16's rendering and mapping when it is built.
- Rewriting bare paths inside approval bead text (btq grooming).
- Editing the controller docs and memory (K9) is a follow-up outside this repository.
- Cancelling today's FYI-as-question asks (their posters' job).

## 5. Open questions for the operator

- **Q1.** Label for merge asks: `ACTION: merge` (recommended), or `QUESTION: merge`?
- **Q2.** Unstructured questions: refuse them (recommended; every question has a recommended answer), or allow `QUESTION: open` with no 👍 mapping?
- **Q3.** 👎 on a multiple-choice card: record "none of these" (recommended), or ignore it and ask for a reply?
- **Q4.** Is design_review only ever on design approvals? Yes → derive the kind (recommended); no → make it a bead field.
- **Q5.** Which links weren't clickable, and in which client (phone or laptop)? That decides whether bare-URL lines are enough.
- **Q6.** Bare repo paths in ask bodies: refuse (recommended), or warn only?
- **Q7.** Notice limits: 12 per hour and 10 undelivered as constants (recommended), or settings?
- **Q8.** Part marker `↳ part 2/3` (recommended), or no marker?

## 6. Implementation outline (for the plan)

1. Store: migration (K26), `notices` table, `thread_key` resolution in `next_pending`/`mark_sent` (K22).
2. Split: marker reservation and keep-lines rule (K23).
3. Notices: `NoticePost`, `notice post` CLI, daemon handler, limits, audit (K1–K8).
4. Labels and heads (K10, K11), approval kind (K17).
5. Question forms, rendering and mapping (K12–K16).
6. Links: daemon validation, client `--ref` resolution and lint (K18–K21).
7. Docs (K27), live test (§2.2).

## 7. ADR amendment needed (r17)

ADR 0001 §8.2 describes admind's posting surface and card wording. This design changes it, so an amendment is needed before implementation:

- A new one-way **notice** surface on `ask.sock` that is not an ask (K1–K8).
- **R30 card wording**: the label line on every card (K10, K11).
- **Question forms and the reaction mapping on question cards** (K12–K16), which amend R13 and R27 for questions; approvals are unchanged.
- **The approval kind** as an ask field rendered in the checked chunks and not digest-bound (K17).
- **Link rules** extending R25 to questions, merges and notices (K18–K21).
- **Threaded continuation parts** (K22–K24), which amends §8.2's "`!asks repeat` reposts as new top-level messages": chunk 0 of a repeat is still top-level; its continuation parts become replies.
- A note in §8.1 that the future picker reuses the choice rendering.
