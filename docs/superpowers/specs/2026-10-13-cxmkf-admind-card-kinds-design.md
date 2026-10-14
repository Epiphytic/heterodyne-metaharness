# admind: card kinds, FYI notices, structured choices, clickable links and threaded parts (design delta)

Status: r3, for review (btq-cxmkf). No implementation yet. r1 → Codex review r1 REVISE (5 blocking, 5 non-blocking) → r2 → Codex review r2 REVISE (4 blocking, 2 non-blocking) → r3; the changes are listed in §8. It amends the relay design (`2026-10-05-admind-marmot-relay-design.md`, R1–R26), the reply/reaction delta (`2026-10-06-admind-relay-replies-reactions-design.md`, R27–R31) and the bump delta (`2026-10-07-admind-asks-bump-design.md`, B1–B14). It changes the ADR 0001 §8.2 admind surface, so it needs an ADR amendment (§7) as well as design review and approval. One part (K17, the approval purpose) also needs a small btq change, which needs its own review.

Operator feedback, 2026-10-13, from the bead:

> FYIs must never be asks. Today post-merge/status FYIs go out as --kind question asks titled 'FYI, no answer needed (react 👍 to dismiss)'. They show approve/answer affordances, ask for a dismissal, and count as active (status answered) toward asks.MAX_OPEN=20 until the asker cancels them; 18 FYIs filled the queue and blocked a real approval ask.

> A question card must show at its top that it is MULTIPLE CHOICE (or YES/NO), not an approval, and must repeat the choices very briefly at the very bottom (e.g. 'Choices: A = ... (recommended) · B = ... · C = ...'). Reactions: 👍 = the recommended/default option, 👎 = no. Only approval cards read as approve/deny.

> Labels: the top line of each card reads 'QUESTION: Multiple Options', 'QUESTION: yes/no', 'APPROVAL: design' or 'APPROVAL: execution'; FYI notices are labelled as FYI. Approval kind (design vs execution) should be a bead or ask field that admind renders.

> (1) links must be clickable, especially GitHub PR links and links to designs, plans and docs (refs should render as URLs, e.g. GitHub blob/PR URLs pinned to the SHA, not bare repo paths); (2) continuation parts of a multi-part message (part 2/3 etc.) must be threaded as replies under the original message, not posted as new top-level messages.

## 1. Requirements

### 1.1 FYI notices

| # | Requirement | Rationale |
|---|---|---|
| K1 | **`admind notice post`** is a new client command: `admind notice post --title T [--body-file F \| --body-file -] [--link LABEL=URL]... [--ref PATH[@REV]]... [--from NAME] [--json]`. It sends a new request `NoticePost` (msgspec tag `"notice"`, `forbid_unknown_fields`) on the existing `ask.sock`, with the same peer-credential check, request size limit (`MAX_REQUEST`) and poster rule (`--from`, `POSTER`) as `ask post`. The reply is `posted <notice id>` or `refused: <why>`. A notice ID uses the ask ID alphabet and is 6 characters long, so it can never be mistaken for an ask ID (4 characters). | The bead's suggested surface. The socket, auth and poster identity already exist for asks. |
| K2 | **A notice is not an ask.** It writes no `asks` row, never counts toward `MAX_OPEN` or `MAX_PER_HOUR`, and never appears in `ask list`, `!asks`, bumps or repeats. It gets no ask ID, no status and no cancel. It is stored only in a new `notices` table (K5), for the limits and the audit. | The defect: 18 FYIs filled `MAX_OPEN=20` and blocked an approval. |
| K3 | **Nothing is requested of the operator.** The card has no answer footer, no reaction prompt and no `!answer` hint (§1.2 shows the exact text). A reaction on a notice is ignored silently, as on any non-card message today (R27). A reply to a notice is ordinary chat: it reaches the admin agent exactly as a reply to any other non-card message does (held, then pasted). `!answer <id> <text>` is addressed by ask ID and is unaffected; `!approve`, `!deny` and `!details` as replies to a notice get today's handling for a reply to a non-card message. No new routing code is needed: notice outbox keys (`notice:<id>:<i>`) are outside `_CARD_KEY`, `ask_for_message` and `bump_for_message`, and §2 pins that, including that nothing on a notice can ever start an approval attempt. | The operator asked for "no reaction or reply requested". Replies stay possible because the operator may want to talk about the news. |
| K4 | **Size bounds, per notice.** The whole rendered notice (label, head, title, body, `Links:` block; after redaction) is at most `NOTICE_MAX_CHARS = 6,000` characters and at most `NOTICE_MAX_PARTS = 4` chunks at the configured `chunk_chars` (K23's split, markers included). The title follows R4's title rule (one line, 1–200 characters, no controls). The body is optional (an FYI can be its title alone) and, when given, at most `CARD_CHARS` (3,500) characters and `CARD_LINES` (40) lines. Links: at most 8 (K18), each label 1–40 characters, each URL at most `MAX_URL = 400` characters, each link line within one chunk (K23). Over any bound, the post is refused and nothing is queued. A notice is never truncated and never has `!details`, because nothing tracks it after delivery. | Bounding notices by count alone let one request under the 256 KiB socket limit become hundreds of chunks. The originator grooms; admind never trims (§8.1 of the ADR). |
| K5 | **Rate and queue bounds.** At most `NOTICE_PER_HOUR = 12` notices are accepted in any rolling hour (counted from `notices.created_at`), and at most `NOTICE_UNDELIVERED = 10` notices may have any chunk still `pending`. The undelivered count is read from the outbox itself (distinct notice IDs among `pending` rows whose key matches `notice:<id>:<i>`), never from the `notices` table, so pruning (K7) cannot hide a queued notice. With K4, that bounds the queue to 40 notice chunks and 48 notice chunks an hour. While the latch is set the post is refused, as asks are. Before the group is verified, or before an operator has been seen, the notice is accepted and stored and waits in the outbox like every other post. All limits are module constants, not settings (Q7). | Notices must not become a new flood. |
| K6 | **Lane and atomicity.** Notice chunks go in **lane 2**, behind every lane-1 row (cards, replies, bumps, commands), so a notice can never delay an approval or question card: `next_pending` orders by lane then seq, and a lane-2 row in backoff does not block lane 1 (B14). The handler runs under `ask_post_lock` (R22): the latch check, the K4 render and measure, the K5 counts, the `notices` insert and every outbox insert happen in one transaction with no await between them, so two concurrent posts cannot both pass a limit that only one fits. **Lane 2 has no liveness guarantee:** continuous lane-1 traffic can delay notices without bound (accepted: an FYI that waits is better than a card that waits); a notice send already in progress is not pre-empted, so a new card can wait for that one `send_final` to finish; and `!details` replies, also lane 2, can wait behind a notice in backoff. K5's undelivered cap stops new notices while old ones are stuck. | Approvals first. One lock and one transaction, as for asks. |
| K7 | **Storage.** A new table `notices(notice_id TEXT PRIMARY KEY, poster TEXT NOT NULL, peer_pid INTEGER, title_chars INTEGER NOT NULL, body_chars INTEGER NOT NULL, links INTEGER NOT NULL, parts INTEGER NOT NULL, created_at TEXT NOT NULL)`. It holds lengths, not text: the text lives only in its outbox rows, which already hold the redacted message. Outbox keys are `notice:<id>:<i>`; chunk 0 is top-level and chunks 1.. are threaded under it (K22). A row is pruned by the existing housekeeping pass only when it is older than 7 days **and** none of its outbox rows is `pending` (delivery has ended, sent or failed). The hourly count only needs the last hour of rows, and the undelivered count comes from the outbox (K5), so neither limit depends on retention. | The hourly limit needs `created_at`; the audit needs the ID. No text means no new place to redact. |
| K8 | **Redaction.** The whole notice text is redacted before it is measured and split, and each chunk is redacted again at delivery, as for every outbox row. A link whose line redaction would change is refused (K20). | Same rule as cards (P5). |
| K9 | **Audit.** One `notice` record per request: `{kind: "notice", action: "posted" \| "refused", notice_id, poster, peer_pid, title_chars, body_chars, links, parts, why}` (`notice_id` and `parts` only when posted, `why` only on a refusal). `why` is one of a fixed set of codes, never built from submitted text or an exception message: `latched`, `title`, `body-too-long`, `notice-too-long`, `too-many-parts`, `too-many-links`, `link-label`, `link-format`, `link-too-long`, `link-line-too-long`, `link-redacted`, `rate-hour`, `undelivered`, `internal`. An unexpected exception is audited as `internal` plus its exception class name only. Delivery is audited by the normal `sent`/`failed` records of its outbox rows. "No text" applies to the new posting records (notices here, and the ask `post` records K16 extends); the existing inbound records keep their redacted operator text as today. | Untruncated audit (r13) and R18's no-text rule, without a refusal path copying the poster's text into the audit. |
| K10 | **Migration away from FYI-as-question.** The controller docs and the agent memory that tell posters to send FYIs as `--kind question` asks are changed to use `admind notice post`. These are outside this repository, but they are **completion evidence** for btq-cxmkf: the bead is not closed until the changed doc and memory files are listed in its close note, and they take effect in the same rollout step as the daemon (§6). FYI asks that are already active stay until their poster cancels them; admind does not guess which questions are FYIs. `docs/admind.md` §10 gets a "Notices" section. | The bead's last scope item; the convention must not survive the release. |

### 1.2 Card labels and rendering

| # | Requirement | Rationale |
|---|---|---|
| K11 | **Every card's first line is its label**, alone on that line, before today's head line: `QUESTION: Multiple Options`, `QUESTION: yes/no`, `APPROVAL: design`, `APPROVAL: execution`, or for a notice `FYI: no reply needed`. Merge asks are not in the operator's list; this design proposes `ACTION: merge` and flags it (Q1). Question rows with no form (legacy rows, and form-less posts during the K14 grace window) render `QUESTION`. An approval whose bead has no purpose renders `APPROVAL: purpose not set` (K17). The label is fixed framing: outside the card budget, never cut, and part of the text that is redacted and (for approvals) R21-checked. | The operator's exact wording: the kind must be readable from the top line before anything else. |
| K12 | **The head line stays**, under the label, unchanged except for notices: `ℹ️ Notice · posted by <poster> (a local process; unverified)`. The notice ID is not shown on the card (there is nothing to address by ID). The `❓`/`🔀`/`🛂` heads keep their ask ID, poster and unverified marker (R30). | The ID and the unverified marker are still needed on asks; only the label is new. |

Rendered examples (chunk 0, before redaction; `…` stands for the poster's body):

```
QUESTION: Multiple Options
❓ Ask k7m2 · question · posted by team-lead (a local process; unverified)
Which runtime for the T15 rerun?

…body…

A = rerun on bel-ci (recommended)
B = rerun on laptop-ci
C = skip the rerun
Answer: 👍 = A (recommended) · 👎 = none · or reply with a letter or your own answer (!answer k7m2 <text>)
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
| K13 | **Two question forms.** `ask post --kind question` takes one of: (a) `--choice TEXT`, repeated 2–6 times, plus `--recommended LETTER` (required, exactly one); admind assigns the letters A–F in the order given; or (b) `--yes-no [--recommended yes\|no]` (default `yes`). `AskPost` gets `form: Literal["choices", "yes-no"] \| None = None`, `choices: list[str] = []`, `recommended: str \| None = None`. `check()` refuses any mix of the two, a missing or unknown recommendation, `--choice`/`--yes-no` on a merge or approval ask, and choice text that breaks K15. Stored as new `asks` columns `question_form TEXT` and `choices TEXT` (JSON `{"choices": [...], "recommended": "A"}`). A question may still carry `--bead` as context; that never makes it an approval (K19b). | The operator asked for the header and footer to be rendered by admind from structured data, not trusted to body text. A forced recommendation makes 👍 always meaningful. |
| K14 | **Form-less questions: grace, then refusal.** A module constant `REQUIRE_QUESTION_FORM` controls a question with no form. In the first release it is `False`: the question is accepted, renders `QUESTION` with today's footer and today's reaction handling (verbatim answers), and the `posted` reply carries a fixed warning (`warning: questions without --choice or --yes-no will be refused in the next release`). The release that flips it to `True` refuses them (`a question needs --choice ... --recommended X, or --yes-no`). The flip is its own small change, made only after the K10 doc and memory updates are in place and one controller run has posted only structured questions (Q2). | The known producers are controller agents driven by docs and memory; a grace window lets them move without a broken release. |
| K15 | **Choice text** is one line of 1–80 characters, with no controls and none of `·`, `=` or newline (they would make the footer ambiguous). It must survive redaction unchanged; otherwise the post is refused (`choice B would be redacted; reword it`). It is fixed framing outside the card budget, so the poster's body is what gets truncated, never a choice. | The footer is the operator's quick reference; it must be complete and literal. |
| K16 | **Rendering.** After the body (and the `Links:` block, K21): one line per choice (`A = text`, with ` (recommended)` on the recommended one), then the answer line, then, as the very last line of the card, the footer `Choices: A = text (recommended) · B = text · C = text` (yes/no: `Choices: yes (recommended) · no`). When the full footer does not fit in one chunk (K23's usable width), the compact footer is used instead: `Choices: A (recommended) · B · C` (at most 47 characters, so it always fits). K23 says how the block is split. `!details` (full text) and repeats render the same way. The ask `post` audit record gains `form` and `choices` (a count), never choice text. | The operator asked for the choices "at the very bottom". The compact footer keeps that promise at the smallest `chunk_chars`. |
| K17q | **Reaction mapping on question cards** (amends R27 for questions only). With a form: an emoji from the verbs approve set (👍 in every skin tone, ✅, ❤, ♥) maps to the recommended choice; an emoji from the deny set (👎 in every skin tone, ❌) maps to `no` on a yes/no card and to `none` ("none of these") on a multiple-choice card (Q3). Any other emoji is a free answer with no choice, stored verbatim as today. Without a form (legacy or grace window): unchanged, the emoji is stored verbatim with no choice. Reaction attribution is unchanged: the answer row keeps the reaction event's actor, its event ID as `message_id`, and the exact reacted part. | The operator's mapping: 👍 = the recommended/default option, 👎 = no. |
| K18q | **Reply mapping on question cards** (amends R28 for questions only). With a form: a reply that is exactly one choice letter after folding (case, surrounding spaces, one trailing `.`/`)`, one leading `(`; so `a`, `B.`, `(c)`) maps to that letter if it is a choice; on a yes/no card, exactly `yes`/`y` or `no`/`n` maps to that; an exact verbs approve word (`yes`, `ok`, `okay`, `lgtm`, `approve`, `approved`, `y`) maps to the recommended choice on a multiple-choice card; an exact `no`/`n` maps to `none` on a multiple-choice card. Deny words (`deny`, `reject`, …) are not special on questions: they are free answers. Anything else is a free answer with no choice, stored verbatim. Without a form: unchanged. | Free text stays possible so no answer is ever lost; short replies get the same mapping as reactions. |
| K19 | **Recording and the answer view.** `ask_answers` gets a nullable `choice TEXT` column (`A`–`F`, `yes`, `no`, `none`). The answer `text` is still the operator's verbatim text or emoji. `AnswerView` gains `choice: str \| None = None`; reply structs do not forbid unknown fields, so an older client decodes the new view and ignores the field. `admind ask get`/`wait` print `answer (choice A): 👍`, and `--json` carries `choice`. The confirmation reply on the card states the mapping: `Recorded for k7m2: A = rerun on bel-ci (from 👍).` Merge asks are unchanged (👍 still means "merged"). | The poster reads the decision without re-parsing emoji, and the operator sees how the reaction was read. |
| K19b | **Only approval cards decide approvals** (unchanged, now pinned by tests). Routing separates answers from decisions by the ask's stored `kind` and the sent outbox key (`daemon.py` reaction and reply handlers, `store.ask_for_message`): on a question card, a merge card or a notice, `!approve`, `!deny`, an approve or deny word, or any approve- or deny-set emoji never creates an `ask_attempts` row and never runs approve-bead, even when the question carries `--bead`. Threading (K22) does not change that separation. | The operator: "Only approval cards read as approve/deny." |

### 1.4 Approval purpose

| # | Requirement | Rationale |
|---|---|---|
| K17 | **The purpose is a bead field, not a derivation.** The approval's purpose comes from the bead: a new optional ask field `ask.purpose`, `design` or `execution`, shown by approve-bead and covered by the context digest like every other ask key (the whole `ask` object is hashed). approve-bead's `--json` readout gains `purpose: "design" \| "execution" \| null`; any other value is a readout gap, so `postable()` refuses the bead and it goes back for grooming. admind renders the label only from `readout.purpose` and has no flag to set or override it: `design` → `APPROVAL: design`, `execution` → `APPROVAL: execution`, `null` → `APPROVAL: purpose not set`. It never infers a purpose from `design_review_valid` or anything else. Because the purpose is digest-bound, changing it after posting makes the ask stale (R29), and the label in the stored checked chunks (K11) is always the purpose the operator approves. | r1 derived the kind from `design_review_valid`, which `postable()` allows on execution approvals too, so an execution approval could be labelled design. A digest-bound field the operator sees in the readout cannot misrepresent what is authorized. |
| K17b | **btq must treat `purpose` as optional, not required** (a requirement on the separate btq bead; this design does not change beads-task-queue). Today `ASK_FIELDS` plays two roles: `ask_digest()` reports a gap for every entry that is empty (`bin/btq` around line 401), and `approval_valid()` requires the ask's key set to be exactly `ASK_FIELDS` (around line 584). Adding `purpose` there would make every existing bead unpostable and invalidate its approval gate. So btq splits them: `ASK_REQUIRED = ('effect', 'excludes', 'why', 'risks', 'refs')` (today's set, each must be non-empty) and `ASK_OPTIONAL = ('purpose',)`. The shown and allowed key set is their union: a key outside it is still a gap (unshown keys are never approved); `purpose`, when present, must be `design` or `execution`, else a gap; when absent it is not a gap. `approval_valid()` accepts an ask whose keys are all of `ASK_REQUIRED` plus any subset of `ASK_OPTIONAL`. The btq bead's tests: a purpose-less bead posted before and after the upgrade is admitted with the same digest and no new gap; an approval recorded before the upgrade still passes `approval_valid()` after it; a bead with `purpose: design` or `execution` is admitted and its digest changes when the purpose changes; `purpose: other` and an unknown extra key are gaps; an approval gate on a bead with a purpose passes. | Existing beads and recorded approvals must stay valid across the upgrade. |
| K17c | **Sequencing across repositories.** `approvals.Readout` uses `forbid_unknown_fields`, so admind must ship `purpose: str \| None = None` on `Readout` **before** approve-bead emits it; until then every approval renders `APPROVAL: purpose not set`. The btq change (K17b's optional field, the readout field, a gap for an unknown value) is a separate btq bead with its own design review; this design states the contract only. Existing beads have no `purpose` and their digests do not change. Whether btq later makes `purpose` required for new approval beads is Q4. | No version skew can break approval posting. |

### 1.5 Clickable links

| # | Requirement | Rationale |
|---|---|---|
| K20 | **`--link LABEL=URL`** (repeatable, at most 8) on `ask post --kind question\|merge` and `notice post`. The label is 1–40 characters of one line, no controls. The URL is at most `MAX_URL = 400` characters and must be one of these pinned GitHub forms (`https://github.com/<owner>/<repo>/` then): `pull/<n>` (optionally `/files` or `/commits/<40hex>`), `blob/<40hex>/<path>`, `tree/<40hex>/<path>`, `commit/<40hex>`, `compare/<40hex>...<40hex>`, `actions/runs/<n>`, `issues/<n>`. The path is percent-encoded per segment; no query, fragment, userinfo or port. Anything else (branch names, `main`, short SHAs, other hosts, `http:`) is refused (`--link must be a GitHub URL pinned to a 40-hex commit, or a PR, run or issue`). The daemon checks the **form** only; it never fetches the URL, so a `--link` is guaranteed pinned but not guaranteed to exist. A merge ask's `--pr` URL is rendered as a link too. A link line that redaction would change is refused. | "Refs should render as URLs pinned to the SHA." A branch URL changes under the operator; a pinned one shows what was asked about. Links never authorize anything, so form-only checking is enough for `--link`. |
| K21 | **`--ref PATH[@REV]`** (repeatable, shares K20's cap of 8) is resolved by the **client**, in its own working directory, into a `blob` or `tree` link, which the daemon then validates with K20's rules (it never trusts the client's resolution). The client resolves the commit locally and **verifies on GitHub** that the commit and the path exist at the repository named by `origin`: (1) `git rev-parse --verify --end-of-options REV^{commit}` gives the 40-hex SHA (REV defaults to `HEAD`); (2) `git cat-file -e <sha>:<path>` and `git cat-file -t` give blob or tree; (3) `git remote get-url --end-of-options origin` is parsed as a GitHub remote (`https://github.com/<o>/<r>(.git)` or the scp-style SSH form for host github.com), else refused `origin is not a GitHub repository`; (4) `gh api --hostname github.com --method GET --include repos/<o>/<r>/commits/<sha>` must return 200 with that exact SHA, then `gh api --hostname github.com --method GET --include repos/<o>/<r>/contents/<encoded path>?ref=<sha>` must return 200. `--hostname github.com` is always passed and `GH_HOST`, `GH_ENTERPRISE_TOKEN` and `GITHUB_ENTERPRISE_TOKEN` are removed from gh's environment, so the check is always against the host the link names. The HTTP status is read from the first line of `--include` output (`HTTP/<v> <code>`); gh's stderr is discarded, never shown or audited. Refusals are distinct: commit 404 → `<sha> is not on GitHub at <o>/<r>; push it first`; commit 200 and path 404 → `<path> does not exist at <sha> on GitHub`; anything else (no `gh`, not logged in, offline, timeout, another status) → `can't check the link on GitHub; pass --link with a pinned URL instead`. These are client-side refusals; nothing reaches the daemon or its audit. Remote-tracking refs are never consulted, so a stale or fabricated `refs/remotes/...` cannot make an unpushed commit pass. The guarantee is "the link opened on GitHub when it was posted" (GitHub also serves commits from a repository's fork network under its URL; that still opens). Q9 offers an offline alternative with a weaker guarantee. | An offline check cannot prove GitHub has the commit (the r1 `git branch -r --contains` check accepted a fabricated ref). The poster's `gh` login already exists for PRs. |
| K21h | **Hardening contract for `--ref`.** Every git and gh call is an argument array (`subprocess.run([...], shell=False)`), with a 5-second timeout (10 for gh), stdin from `/dev/null`, and output size-limited. Inputs are validated before any call: REV matches `[0-9A-Za-z][0-9A-Za-z._/@^~-]{0,199}` (so it cannot start with `-`) and is passed after `--end-of-options`; PATH is relative, 1–1,000 characters, has no empty, `.` or `..` segment, no leading `/` or `-`, no `:`, no controls; `<sha>:<path>` is only built from the 40-hex SHA that step (1) returned. git runs as `git --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -c protocol.allow=never -c core.sshCommand=false -c credential.helper=` with an environment built from scratch: `PATH`, `HOME`, `LANG=C`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, and none of `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_EXEC_PATH` or other inherited `GIT_*`. These calls never touch the network. gh runs with `--hostname github.com`, `GH_PROMPT_DISABLED=1`, `GH_NO_UPDATE_NOTIFIER=1`, no `GH_HOST`/`GH_ENTERPRISE_TOKEN`/`GITHUB_ENTERPRISE_TOKEN`/`GH_DEBUG`, its normal config (for the login), and an endpoint built only from the parsed owner and repo (`[A-Za-z0-9._-]`), the SHA and the encoded path. | A hostile or odd checkout must not run code or turn a revision into an option. |
| K22l | **Rendering.** A `Links:` block after the body (before the choices), one link per line: `<label>: <url>`, the URL last on its line with no brackets, quotes or trailing punctuation, so client linkifiers find its end. Approval cards keep R25's `link: <url>` lines. A link line is never split across chunks (K23). Links are fixed framing outside the card budget. | Clickability depends on the client's linkifier; a bare URL on its own line is the most portable form (Q5 asks which links failed). |
| K22p | **Bare repo paths: warn, don't refuse.** The client scans the title and body for bare paths and prints a hint to stderr; it never refuses (Q6). Tokens are split on whitespace; surrounding quotes, backticks, brackets and one trailing `.`, `,`, `:`, `;` or `)` are stripped; tokens containing `://` are skipped (already links); a token matches only if it is exactly a path tracked in the client's repository (`git ls-files --full-name -z`, under K21h's contract) and contains a `/`. The hint: `hint: docs/x.md is a bare path; --ref docs/x.md renders it as a link`. At most 5 hints. Approval descriptions are digest-bound bead text: their bare paths are btq grooming (approve-bead's `links`), out of scope here. | Refusing would reject legitimate diagnostics like "the failure is in `src/auth.py`". |

### 1.6 Threaded continuation parts

The send path supports threading: `send_final(account, group, text, reply_to, key)` takes `reply_to_message_id_hex`, the S4 spike proved it in both directions, and bumps and `!details` already use it. What is missing is a target for continuation chunks: card chunks are queued together before chunk 0 has a message ID, so all of them are queued with `reply_to = None` and go out top-level.

| # | Requirement | Rationale |
|---|---|---|
| K22 | **`outbox.thread_key`.** A new nullable column. When a post's chunk 0 is top-level (an ask card `ask:<id>:0`, a repeat `askr:<id>:<mid>:0`, a notice `notice:<id>:0`), chunks 1..n are queued in the same lane as chunk 0, with `reply_to = NULL` and `thread_key` = chunk 0's key. When `outbox_pass` picks a row whose `thread_key` is set, it resolves the target, **persists the resolution, and never waits**: in one transaction it writes the row's `reply_to` (or leaves it NULL) and clears `thread_key`, then sends. The resolution, by the state of the `thread_key` row: **sent with a message ID** → `reply_to` = that ID; **sent without a message ID** (the sender stores NULL when `message_ids_hex` is empty), **failed**, or **missing** → top-level (`reply_to` NULL), with one `thread_fallback` audit record `{key, parent, why: "no-message-id" \| "parent-failed" \| "parent-missing"}`; **pending** → top-level with `why: "parent-pending"`. The pending case cannot occur in normal operation (the parent is in the same lane with a lower seq, so `next_pending` returns it first, and a parent in backoff stops the pass before any later row); it is a terminal fallback so a bug can never stall the lane. Because the resolution is stored before the send, a restart or retry sends to the same target. Rows that already have a `reply_to` (replies, bumps, `!details`) are queued without `thread_key` and are unchanged. | The operator's request. Every parent state has a terminal answer, so threading can never block the outbox. |
| K22d | **Delivery checks are unchanged.** A fallback-sent continuation does not make a card delivered: R8/B12's `card_delivered` still needs every chunk of the card (or of one repeat) to be `sent`, and a failed chunk 0 leaves the card undelivered (`!asks repeat` is the remedy, as today). | Threading changes where parts appear, not when a card counts as delivered. |
| K23 | **Part markers and the split.** Each continuation chunk starts with `↳ part <i>/<n>` and a newline; chunk 0 has no marker. Every chunk, including chunk 0, is packed to at most `usable = chunk_chars − 16` characters before the marker is added, so a marked chunk never exceeds `chunk_chars` (`chunk_chars` is at least 200, so `usable` is at least 184). The split works on lines. **Body lines** may be cut as today (after a newline in the second half, else at the limit). **Atomic lines** are never cut: the label, the head, each link line, each choice line, the answer line and the footer. Admission guarantees each atomic line fits in `usable`: a choice line is at most 98 characters, the answer line at most 120, the compact footer at most 47, the head at most 110; a link line longer than `usable` is refused (`link-line-too-long`, K9). The **closing block** (choice lines, answer line, footer) stays together in the last chunk when it fits in `usable`; otherwise it is packed line by line at line boundaries into as many chunks as needed, and the footer is always the final line of the final chunk. For six 80-character choices at `chunk_chars = 200`, the block (about 1,170 characters with the full footer) uses the compact footer and spans several chunks, each within 200. Approval cards use the same split for their checked chunks (`_checked`), with their R25 `link:` lines atomic. **An approval link line longer than `usable` is never cut: the post is refused** with a new refusal `LINK_TOO_LONG` (`this bead has a link longer than this group's message size allows (<n> characters, <usable> usable at chunk_chars <c>); shorten the link in the bead or raise chunk_chars`), returned to the poster like `TOO_LONG` (R8, originator grooming); the same applies to R29's updated-content card. Example: a 39-character owner, a 100-character repository and `docs/design.md` give a 226-character `link:` line, refused at `chunk_chars = 200` (184 usable) and accepted at the default 4,000. So no card, question, notice or approval, is ever sent with a cut URL. | A client without threading still shows how the parts belong together; a cut URL is not clickable; the footer is always at the very bottom. |
| K24 | **Reactions and replies on any part still count** (R27, R28, B12 unchanged): continuation chunks keep their `ask:`/`askr:` keys and `_CARD_KEY` match. Bumps (B3/B13) still target chunk 0. | Threading changes where parts appear, not what they mean. |

### 1.7 Compatibility and schema

| # | Requirement | Rationale |
|---|---|---|
| K25 | **Wire compatibility.** Request structs (`AskPost`, the new `NoticePost`) use `forbid_unknown_fields`; reply structs do not. New client → old daemon: a post with new fields, or a `notice` request, gets a clean decode refusal (`refused: ...`); nothing is stored. Old client → new daemon: approvals and merges work unchanged; a form-less question works during the K14 grace window (with the warning) and is refused after the flip; `ask get`/`wait` decode the new `AnswerView` and ignore `choice`. The client and daemon ship in one release. | No silent misreads across versions. |
| K26 | **Migration**, in the store's existing migration step (one transaction, each `ALTER TABLE ... ADD COLUMN` guarded by `PRAGMA table_info`): `outbox.thread_key`, `asks.question_form`, `asks.choices`, `ask_answers.choice`, and `CREATE TABLE IF NOT EXISTS notices`. (No approval column: K17's purpose lives in the bead.) Existing rows keep NULLs: legacy questions render `QUESTION` and keep today's reaction and reply handling; legacy approvals keep their stored chunks, so repeats show today's text without a label; existing pending outbox rows have no `thread_key` and send as today. | Additive only; no backfill; no ask changes meaning at deploy. |
| K27 | **HELP and docs.** `docs/admind.md` §10 describes notices, labels, forms, the mappings, links and threading; `docs/configuration.md` is unchanged (no new settings). `admind ask post --help` and `admind notice post --help` show the new flags. | |

## 2. Tests

### 2.1 Offline

`tests/test_admind_notices.py`:
- A notice posts with no `asks` row, is absent from `ask list` and `!asks`, and does not count toward `MAX_OPEN`: with 20 active asks, a notice still posts and an approval is still refused for MAX_OPEN as before; with 19 active asks and 5 notices, an approval posts.
- Card text: label line, `ℹ️ Notice` head, title, body, links; no `!answer`, no reaction prompt, no ID. Chunks are in lane 2.
- Lane: with notice chunks pending and a lane-1 card queued after them, the card is sent first; a notice chunk in backoff does not delay a lane-1 row.
- A reaction on a notice chunk is ignored; a plain reply is held and pasted to the admin agent; `!approve`/`!details` as replies get today's non-card handling.
- Size bounds: the maximum notice (3,500-character body, 8 links with 400-character URLs) is refused for `notice-too-long`; at `chunk_chars = 200`, a notice needing 5 parts is refused for `too-many-parts` and one needing 4 is accepted; a 401-character URL, a link line over `usable`, a 9th link, a bad label, each refused; nothing queued on any refusal.
- Rate and queue: the 13th notice in an hour and the 11th undelivered notice are refused; latch refusal; accepted while unverified and delivered after verification.
- Concurrency: 20 concurrent `notice post` clients against a store with 9 undelivered notices: exactly one is accepted (or 3 with an hourly count of 9: exactly 3), and the outbox holds exactly the accepted notices' chunks.
- Redaction: a secret in the body is redacted in the outbox row; a link line redaction would change is refused.
- Audit: sentinel strings placed in the title, body, a link label and a link URL never appear in the audit file, on the posted path and on every refusal path (including a link refused for format, which must not echo the URL); every refusal's `why` is one of K9's codes; an injected exception is audited as `internal` plus a class name.
- Pruning: with delivery gated (group unverified), 10 notices queued and their `notices` rows aged past 7 days, a housekeeping pass keeps those rows, and an 11th notice is still refused `undelivered`; after delivery ends the rows are pruned. With rows deleted by hand while chunks are pending, the undelivered count (from the outbox) is unchanged.

`tests/test_admind_question_forms.py`:
- `check()` matrix: 2–6 choices with exactly one recommended; yes/no with its default; refusals for 1 or 7 choices, a missing or unknown `--recommended`, both forms at once, `--choice` on merge/approval, choice text with `·`, `=`, controls, over 80 characters, or changed by redaction.
- K14: with `REQUIRE_QUESTION_FORM = False`, a form-less question is accepted with the warning and renders `QUESTION` with today's footer and verbatim reaction answers; with `True`, it is refused.
- Rendering: label, choice block, answer line, footer as the very last line; a long body is truncated and the choices are not.
- Split at `chunk_chars = 200` with six 80-character choices: every chunk is at most 200 characters including its marker, no choice line is cut, the compact footer is the last line of the last chunk; at 4,000 the full block stays in one chunk with the full footer.
- Reaction mapping (property test over every skin tone of 👍 and 👎, plus ✅, ❤, ♥, ❌ and an unrelated emoji) on multiple-choice, yes/no and form-less cards.
- Reply mapping (K18q): `a`, `B.`, `(c)`, ` b `, `G` (not a choice: free answer), `yes`, `y`, `ok`, `lgtm`, `no`, `n`, `deny`, `no thanks` (free) on each card form; form-less cards unchanged.
- Recording: `choice` stored; the confirmation text states the mapping; `ask get`/`wait` print it and `--json` carries it; legacy answers show none.
- Attribution: a reaction's answer row keeps the event's actor, event ID and reacted part, as before.
- Approval separation (K19b): on a multiple-choice card, a yes/no card, a form-less card, a question posted with `--bead`, a merge card and a notice, each of `!approve`, `!deny`, `approve`, `approved`, `yes`, `lgtm`, `deny` and every approve- and deny-set emoji creates no `ask_attempts` row and never calls the (faked) approve-bead.

`tests/test_admind_approval_purpose.py`:
- Approval link lines: the 226-character example refused `LINK_TOO_LONG` at `chunk_chars = 200` and accepted at 4,000 with the line whole in one chunk; the same for an updated-content card; no chunk ever contains part of a URL.
- Readout with `purpose` `design`, `execution` and `null` renders `APPROVAL: design`, `APPROVAL: execution` and `APPROVAL: purpose not set`; the label is inside the checked chunks; a repeat reuses them; a readout without the field (old approve-bead) still decodes and renders `purpose not set`; `design_review_valid` True with purpose `execution` renders `execution` (no inference).

`tests/test_admind_links.py`:
- K20 allow/deny table: every pinned form accepted; branch, short SHA, other host, `http:`, userinfo, port, query, fragment, unencoded spaces and a 401-character URL refused.
- `--ref` in a temporary git repository with a fake `gh` on `PATH` (a script that answers from a fixture table): path to blob URL and directory to tree URL, with percent-encoding; fake gh 404 for the commit → `push it first`; path 404 → refused; commit 200 and path 404 → the distinct path refusal; gh missing, gh exiting non-zero, an unexpected status and gh timing out → `can't check the link`; fake gh writing a sentinel to stderr: the sentinel appears in no output or file.
- **Hostile `GH_HOST`:** with `GH_HOST=ghe.example` and `GH_ENTERPRISE_TOKEN` set, the recorded gh argv contains `--hostname github.com` and gh's environment has neither variable; a fake gh that answers 200 only for `ghe.example` makes the post refuse.
- **Stale and fabricated refs:** an unpushed commit with a fabricated `refs/remotes/origin/main` and with `refs/remotes/other/main` pointing at it is still refused when fake gh says 404.
- **Wrong and changed remotes:** a non-GitHub `origin`, no `origin`, an `origin` whose URL has extra path segments, and an `origin` changed between two posts each resolve to (or are refused for) the URL of the `origin` read in that post; the gh endpoint always names exactly that owner and repo.
- **Hostile arguments:** REV `--output=x`, `-p`, `HEAD;id`, PATH `../x`, `/etc/passwd`, `-x`, `a:b`, a path with a newline: each refused before any subprocess runs (assert on a recorded argv list).
- **Hostile configuration:** a repository whose local config sets `core.fsmonitor`, `core.hooksPath`, `core.pager` and `core.sshCommand` to a script that writes a marker file; after `--ref` resolution the marker does not exist. An inherited `GIT_DIR` pointing elsewhere is ignored.
- Bare-path hints: a tracked `docs/x.md` in prose, in backticks, in quotes and before a period gets a hint; the same path inside a `https://` URL, an untracked path and a token without `/` get none; the post is never refused; at most 5 hints.

`tests/test_admind_threading.py` (fake transport recording `reply_to`):
- A three-part card: chunk 0 top-level, chunks 1 and 2 reply to chunk 0's message ID; markers `↳ part 2/3`, `↳ part 3/3`; every chunk at most `chunk_chars` at 200.
- Chunk 0 sent without a message ID, chunk 0 failed, and chunk 0's row deleted: the later chunks go top-level, each with a `thread_fallback` record with the right `why`, and the outbox keeps draining (a following unrelated row is sent).
- A child forced to be picked while its parent is pending (test hook): it goes top-level with `parent-pending`; nothing waits.
- Persistence: crash (exception injected) after the resolution transaction and before the send; on restart the retry sends to the stored target and `thread_key` stays cleared; a retryable send error then success keeps the same `reply_to`.
- R8: with chunk 0 failed and chunks 1..n fallback-sent, `card_delivered` is False.
- Repeats and notices thread the same way; bumps and `!details` unchanged.
- A reaction on part 3 still decides an approval and answers a question.

`tests/test_admind_wire.py` (mixed versions, using frozen copies of the old request and reply structs):
- An old `AskPost` (no new fields) to the new daemon: approval and merge accepted; form-less question accepted with the warning, then refused with the flag flipped.
- A new `AskPost` with `form` and a `NoticePost` decoded by the old request type: clean refusal, nothing stored.
- A new `AskReply` with `choice` decoded by the old reply type: succeeds, `choice` ignored.

`tests/test_admind_store.py` additions: migration from a database built with the previous schema (fixture created by the old SCHEMA string) adds every column and the table once, keeps rows, and is idempotent on restart.

Fixtures use short placeholder IDs and 40-hex SHAs built at runtime, never 64-hex literals (gitleaks).

### 2.2 Live (`tests/live/`, `HZ_LIVE=1`, run by the controller, not in CI)

One run in the test group: a three-part question card, a notice with a PR link and a blob link, and a design approval. Check on the operator's phone and laptop clients that the parts appear as replies under chunk 0, that the links open, and that 👍 on part 3 of the question records the recommended choice.

## 3. Risks

- **Client threading display.** Some clients may collapse replies, so parts 2..n are hidden until the operator opens the thread. The `↳ part i/n` markers say how parts belong together.
- **Linkification is client-side.** A pinned URL on its own line is the best admind can do. If a client does not linkify GitHub URLs at all, nothing in this design fixes it (Q5).
- **`--ref` needs `gh` and the network** on the poster's side. Without them, the poster falls back to `--link`, which is checked for form only.
- **Cross-repo purpose.** Until the btq change ships, every approval says `APPROVAL: purpose not set`. That is honest but not what the operator asked for; K17c sequences it.
- **Mapping surprises.** An operator who reacts ❤ to mean "thanks" on a question records the recommended choice. The confirmation reply makes that visible, and a later reply adds a new answer (answers append, as today).
- **Grace-window drift.** If the K14 flip never happens, form-less questions survive. The flip is listed in §6 as its own step with an evidence condition.
- **Long approval link lines** at very small `chunk_chars` make the bead unpostable (K23's `LINK_TOO_LONG`) until it is groomed or `chunk_chars` is raised; at the default 4,000 that needs a link over 3,984 characters, which approve-bead's links never are.
- **Notice starvation.** Lane 2 has no liveness guarantee (K6); a busy lane 1 can delay FYIs indefinitely.

## 4. Out of scope

- The §8.1 picker kind (number-emoji options). It should reuse K13–K19's rendering and mapping when it is built.
- Rewriting bare paths inside approval bead text (btq grooming).
- The btq side of K17 (its own bead and review).
- Cancelling today's FYI-as-question asks (their posters' job).

## 5. Open questions for the operator

- **Q1.** Label for merge asks: `ACTION: merge` (recommended), or `QUESTION: merge`?
- **Q2.** Form-less questions: one release of grace with a warning, then refuse (recommended), or allow them for good as `QUESTION: open` with no 👍 mapping?
- **Q3.** 👎 on a multiple-choice card: record "none of these" (recommended), or ignore it and ask for a reply?
- **Q4.** Approval purpose is a bead field (K17). Should btq later make it required on new approval beads, so `purpose not set` disappears? Recommended: yes, once the btq change ships.
- **Q5.** Which links weren't clickable, and in which client (phone or laptop)? That decides whether bare-URL lines are enough.
- **Q6.** Bare repo paths in ask bodies: warn the poster (recommended), or refuse?
- **Q7.** Notice limits (12 an hour, 10 undelivered, 4 parts, 6,000 characters) as constants (recommended), or settings?
- **Q8.** Part marker `↳ part 2/3` (recommended), or no marker?
- **Q9.** `--ref` checks GitHub with `gh` (recommended; the link is known to open), or an offline check that only proves the commit exists locally (works without network, but an unpushed commit can produce a broken link)?

## 6. Implementation and rollout outline (for the plan)

1. Store: migration (K26), `notices` table, `thread_key` resolution in `outbox_pass` (K22).
2. Split: line-based packing, atomic lines, markers, closing block, compact footer (K23, K16).
3. Notices: `NoticePost`, `notice post` CLI, handler under `ask_post_lock`, bounds, lane 2, audit codes (K1–K9).
4. Labels and heads (K11, K12); `Readout.purpose` and the approval label (K17, K17c).
5. Question forms, rendering, mappings, `AnswerView.choice` (K13–K19b), with `REQUIRE_QUESTION_FORM = False`.
6. Links: daemon validation, client `--ref` with `gh`, hardening, path hints (K20–K22p).
7. Docs (K27).
8. **Rollout:** the K10 controller doc and memory changes land alongside the release (they describe `notice post`, `--choice` and `--yes-no`); restart admind; the live test (§2.2); the close note lists the changed doc and memory files.
9. **Follow-ups, separate beads:** the btq purpose field (K17c); the `REQUIRE_QUESTION_FORM = True` flip, once one controller run has posted only structured questions.

## 7. ADR amendment needed (r17)

ADR 0001 §8.2 describes admind's posting surface and card wording. This design changes it, so an amendment is needed before implementation:

- A new one-way **notice** surface on `ask.sock` that is not an ask, with its size, rate, lane and audit rules (K1–K10).
- **R30 card wording**: the label line on every card (K11, K12).
- **Question forms** and their admission, including the grace window and the later refusal of form-less questions (K13–K16).
- **Reaction mapping (R27) and reply mapping (R28) on question cards** (K17q, K18q), and the statement that only approval cards decide approvals (K19b).
- **The answer view wire change** (`AnswerView.choice`, K19).
- **The approval purpose** as a digest-bound bead field that admind renders and never infers (K17, K17c); this also touches the btq/approve-bead readout contract and btq's ask field sets (required vs optional, K17b).
- **Link rules** extending R25 to questions, merges and notices, `--ref` verification on GitHub with a fixed host (K20–K22p), and the new approval refusal `LINK_TOO_LONG` (R8 amended: an approval link line is never cut; K23).
- **Threaded continuation parts** (K22–K24), which amends §8.2's "`!asks repeat` reposts as new top-level messages": chunk 0 of a repeat is still top-level; its continuation parts become replies.
- A note in §8.1 that the future picker reuses the choice rendering.

## 8. Changes from r1

- **Blocking 1 (choice block vs chunk size):** K23 is now a line-based split with atomic lines, a closing block that splits at line boundaries when it does not fit, and the compact footer (K16); link lines that cannot fit are refused. Tested at `chunk_chars = 200` with six 80-character choices.
- **Blocking 2 (notice bounds):** K4 caps the rendered notice (6,000 characters, 4 parts) and every link; K5 bounds the queue; K6 moves notices to lane 2 and makes admission atomic under `ask_post_lock`. Concurrency and maximum-size tests added.
- **Blocking 3 (`--ref` proof):** K21 verifies the commit and path with `gh api` against `origin`'s GitHub repository and never consults remote-tracking refs; `--link` is stated to be form-only. Wrong-remote, changed-origin, stale and fabricated-ref tests added; Q9 offers the weaker offline option.
- **Blocking 4 (approval kind):** K17 replaces the derivation with a digest-bound bead field `ask.purpose` rendered from the readout; no admind flag, no inference, `purpose not set` when absent. K17c sequences the `Readout` change before btq's.
- **Blocking 5 (threading states):** K22 resolves every parent state, including sent without a message ID and a missing parent, persists the resolution, and never waits; K22d keeps R8 unchanged. Restart and retry tests added.
- **Non-blocking:** the `--ref` hardening contract (K21h) with hostile-argument and hostile-config tests; bare-path hints are warn-only with defined token rules (K22p, Q6 now recommends warn); fixed audit reason codes and the scope of "no text" (K9), with sentinel tests; R28 reply mapping (K18q), the answer-view wire change (K19), approval separation (K19b) and attribution tests; the Q2 rollout as a grace window (K14) with mixed-version tests and K10 as completion evidence.

## 9. Changes from r2

- **Blocking 1 (optional purpose):** K17b specifies, for the separate btq bead, split required and optional ask field sets so purpose-less beads stay postable and recorded approvals stay valid, with the upgrade tests. beads-task-queue is not changed by this design.
- **Blocking 2 (pruning vs the undelivered bound):** K5 counts undelivered notices from pending outbox rows; K7 prunes a `notices` row only after its delivery has ended. Test with pruning while delivery is gated.
- **Blocking 3 (GH_HOST):** K21/K21h always pass `--hostname github.com` and strip `GH_HOST` and the enterprise tokens. Hostile `GH_HOST` test.
- **Blocking 4 (approval link lines):** K23 never cuts a link line on any card; an approval with one longer than `usable` is refused `LINK_TOO_LONG` back to the poster. Tests and the ADR list updated.
- **Non-blocking:** K6 states lane 2's lack of liveness guarantees (and §3 lists notice starvation); K21 gives distinct commit and path refusals, reads the status from `--include` output, and never forwards gh stderr.
