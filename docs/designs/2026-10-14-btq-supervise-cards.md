# btq-supervise cards that can be decided

Status: design r4 for btq-yzm5c, fixing the two blockers of the last review cycle
(d3). This is design only; nothing here is implemented.

Base: btq master 15b232d. The design changes `bin/btq-supervise` and needs one
admind behaviour from the admind workstream (§5.3).

The Step 4 design this extends is `docs/designs/2026-10-14-btq-step4-escalation.md`
at r3, commit 0ae9ab7 on branch `design/btq-step4`. That branch is not on master.

## Changes since r3 (Codex design review d3, REVISE)

r4 changes only §2.2's summary rule, §6.2, §10.1, their tests (§13) and §15. It
also makes the consistent one-line edits these need in §6.1, §9.1, §11 and §12.

| d3 finding | Fix |
|---|---|
| B5: `bd comments add --author` lets a worker forge a supervisor-authored `decision-summary:`, which makes a card decidable | §10.1: `summary` pins the comment it wrote (its ID and a SHA-256 of its text) in the supervisor's private store. §2.2: only the pinned comment is a summary. If the pin is missing, if no comment has the pinned ID, or if that comment's author, header or text hash differ, the card is not decidable. §9.1 binds the pin |
| B7: `a&commat;b&period;io` and `www.123.456` still link under cmark-gfm | §6.2: every quoted line's text sits in one code span, fenced with one more backtick than its longest backtick run. CommonMark and GFM decode no entities, apply no backslash escapes, and autolink nothing inside a code span. §13 renders the card with cmark-gfm and with markdown-it-py plus linkify, and asserts no anchors |

## Changes since r2 (Codex design review d2, REVISE)

r3 narrows. A card offers release or close only when a decision summary,
written by the controller, sits on the bead (§2.2). Everything else is leave,
groom or a label fix.

| d2 finding | Fix |
|---|---|
| B1: the digest misses a second edit to an already-modified file, untracked contents, notes, metadata, comment text and author, and the remaining gates | §9.1 binds the hash of `git diff HEAD --binary`, the untracked files' contents (bounded), every comment's ID, time, author and text hash, the notes, the whole metadata, and the computed Still required lines. Evidence that can't be read whole makes the card not decidable (§2.2) |
| B2: posting invalidates its own card (the supervisor's comment and label are hashed) | §9.1: the supervisor's own `btq-supervise:` comments and the `needs-human` it added are left out of the evidence, and the digest is computed after its own label write. A posted digest is never rewritten |
| B3: replacement fails at the ask cap, cancellation failures aren't retried, and a scan can publish while the controller carries out an answer | §9.2: cancel the old ask first, then post. A persisted `replacing` state is retried on every scan. A replacement nets zero against `max_open_asks`. §9.3: the controller takes a hold under the scan lock before acting, and no scan publishes for a held bead |
| B4: clean/process filters and textconv can run repository-configured code | §3.2: refuse inspection (`filters`) when the config sets any `filter.*.clean/smudge/process` or `diff.*.textconv`, or any attributes file declares `filter=` or `diff=`. `GIT_ATTR_NOSYSTEM=1` and `core.attributesFile=/dev/null` are set, and diff runs `--no-ext-diff --no-textconv` |
| B5: length stands in for meaningful context | The length rule is gone. §2.2: release and close need a decision summary written by the controller with `btq-supervise summary`, shown whole on the card |
| B6: ASCII-only neutralizing destroys meaning (≤ and ≥ both became `?`) | §6.2 keeps printable Unicode. It strips only controls, format characters (bidi, zero-width) and blank-looking letters, and escapes Markdown |
| B7: `www.example.com` still autolinks | §6.2 brackets every dot in a host-like token (`example[.]com`), as well as `://` and `@`. §13 checks it on rendered output |

r2's fixes for d1 stand: the git guard's roots, association, environment and
bounds; `unknown` for missing evidence; whole-field secret removal; the trust
rule; Changes and Still required; and evidence-only review and containment
lines.

## Problem

The supervisor's first live scan posted five cards, and Liam rejected all five:

> You can't ask me what to do about stalled beads without telling me anything
> about what's in them, I can't consent to those.

Those cards showed the bead's mechanics:

- class, holder and claim;
- process and last activity;
- a worktree count and a URL count.

They did not say what the bead asks for or what was done. They didn't say what
each answer would do, or why A was recommended.

The timer is stopped and the cards are cancelled. The timer stays stopped until
this design is implemented and its cards have been seen (§12).

## Requirements (from the task)

- **R1:** what the bead is, from its description and the work it asks for.
- **R2:** what has happened: progress, related commits, PRs, branches and
  comments, and done, partial or untouched.
- **R3:** what each option does to the bead and to the work.
- **R4:** which option is recommended, and why.
- **R5:** links.
- **R6:** facts that don't fit admind's budget continue threaded under the card,
  not dropped.
- **R7:** redaction and the secret-shape refusal stay.

## Non-goals

- Detection is unchanged: `classify`, `activity`, `threshold`, and the
  post/mark/settle paths.
- The supervisor's writes to beads are unchanged: the `needs-human` flag and its
  comment. The one addition is the controller-run `summary` command (§10.1).
- No LLM, no network and no credentials in the scan.
- No automatic action on an answer: the controller carries it out under OPS.md.

## 1. Card layout and the trust boundary

A card body has six parts, in this order:

1. a header line;
2. "What it is";
3. "What has happened";
4. "Options";
5. "Recommended";
6. "More".

**Trust rule.**

- A line that does not start with `| ` is btq's own text. It is built from fixed
  templates and from values btq computed and validated: counts, enums, times,
  short SHAs, bead IDs that pass `btq.BEAD_ID`, and the ask's own IDs.
- Every value that comes from the bead, from git, or from a worker appears only
  on a line that starts with `| `. That covers titles, descriptions, comments,
  dependency titles, branch names, commit subjects, paths, labels and URLs.
- btq lines refer to such a value by position ("the branch shown above"), never
  by inlining it.

§6 says how a quoted value is neutralized. §11 shows complete cards.

## 2. What it is (R1)

### 2.1 Fields

All of these come from `bd show`, which `examine` already reads:

- title;
- type, priority, labels;
- description, design and acceptance criteria;
- dependencies and their statuses;
- dependent count.

Each untrusted value is quoted and cleaned (§6).

### 2.2 Decidable or not

A card is **decidable** only when both of these hold:

1. **A pinned decision summary is on the bead.** The controller writes it with
   `btq-supervise summary BEAD --file F`. That command pins the comment it wrote
   in the supervisor's private store (§10.1). The summary is the comment the pin
   names, and nothing else. It counts only when all of these hold:
   - the store has a well-formed pin for this bead;
   - the bead has a comment with the pinned ID;
   - that comment's author is the supervisor's identity
     (`bel:<host>:supervisor`), and its first line is
     `decision-summary: <bead> at <time>`;
   - the SHA-256 of its whole text equals the pin's.

   The author and header alone prove nothing: `bd comments add --author` lets
   any writer set both. A forged comment carrying them is an ordinary comment.
   It is evidence (§9.1) and shown as a comment, never as the summary.

   A missing pin gives "no decision summary has been written for this bead". A
   pin whose comment is gone, edited, or has another author or header gives
   "the decision summary was changed or removed after it was written". Both
   make the card not decidable.

   The summary says what the work is, what was done and what the options would
   do to it. It is at most 1,200 characters and 12 lines. The card shows it
   whole, quoted, under "Decision summary (written by the controller at
   <time>)".
2. **The evidence was read whole.** No worktree fact is in this list:
   - `outside-roots`
   - `not-owned`
   - `not-associated`
   - `partial-clone`
   - `filters`
   - `git-error`
   - `too-large`
   - `budget`

   These mean a worktree was recorded but btq couldn't bind what's in it.
   `none recorded`, `other-host` and `missing` are evidence the digest can
   state, so they don't block (§3.3, §9.1).

Neither the bead's own description nor its length makes a card decidable. The
description is still shown (8 lines on the card, whole in More), as context.

A card that is not decidable says why on its header line:

```
NOT DECIDABLE HERE: no decision summary has been written for this bead.
NOT DECIDABLE HERE: the decision summary was changed or removed after it was written.
NOT DECIDABLE HERE: btq could not read its recorded worktree whole (too-large).
```

Such a card offers only choices that change neither the claim nor the work:

- **leave it**, recommended;
- **send it back for grooming**, offered only when the summary is missing. The
  controller writes the decision summary from the bead, its worktree and its
  comments, asking the worker if it is reachable. That comment changes the
  evidence, so a replacement card follows (§9.2), and it can offer release or
  close;
- the label fixes that need no summary: `set kind:X` and `remove needs-human`
  for a stale flag.

"Release it" (blocked, open, or once exited), "close it, not completed" and
"clear needs-human and open it" are never offered on a card that is not
decidable.

## 3. What has happened (R2)

### 3.1 Sources

| Fact | Source |
|---|---|
| holder, claim time, process, last activity | as today: `claim_bound`, `process_state`, `activity` |
| comments | `bd comments`, already read. The card shows the count, how many are by the holder, and the newest non-btq comment quoted. More has all of them, newest first, up to 30, each up to 400 characters |
| worktree, branch, commits | git, under §3.2, only for a worktree recorded in the holder's newest worktree note |
| PRs, runs, issues | GitHub URLs in the notes and comments, as today. They are listed and linked, never fetched |
| escalation reason | for `escalated`, the newest holder comment, quoted |

### 3.2 The git guard

Git runs only when every check in this section passes. Otherwise the worktree
fact is `not inspected (<reason code>)`. The reason codes are a fixed list:

- `other-host`
- `outside-roots`
- `not-owned`
- `missing`
- `not-associated`
- `partial-clone`
- `filters`
- `git-error`
- `too-large`
- `budget`

**Paths.**

- The note's `worktree` and `repository` are resolved with
  `os.path.realpath(strict=True)`.
- Each must lie inside one of the supervisor config's new `worktree_roots`.
  That is a list of absolute directories, read fail-closed, default
  `["~/repos"]`, compared with `os.path.commonpath` after resolving.
- Each must be a directory owned by the supervisor's uid.
- A symlink that leaves the roots fails the check (`outside-roots`).

**Association.**

- `git rev-parse --path-format=absolute --git-common-dir` gives the same
  resolved directory when run in the worktree and in the repository.
- The resolved worktree path appears in
  `git -C repository worktree list --porcelain -z`, or is the repository itself.
- A worktree that doesn't belong to its recorded repository is `not-associated`.

**Environment.** Each git process gets a fresh environment, not the
supervisor's:

| Variable | Value |
|---|---|
| `PATH` | `/usr/bin:/bin` |
| `HOME` | an empty temporary directory |
| `LC_ALL` | `C` |
| `GIT_CONFIG_NOSYSTEM` | `1` |
| `GIT_CONFIG_GLOBAL` | `/dev/null` |
| `GIT_TERMINAL_PROMPT` | `0` |
| `GIT_ASKPASS`, `SSH_ASKPASS` | `/bin/false` |
| `GIT_SSH_COMMAND` | `/bin/false` |
| `GIT_NO_REPLACE_OBJECTS` | `1` |
| `GIT_OPTIONAL_LOCKS` | `0` |
| `GIT_NO_LAZY_FETCH` | `1` (git 2.44 and later; the host has 2.43, so the partial-clone refusal below is what counts) |
| `GIT_ATTR_NOSYSTEM` | `1` |

**Options on every call:**

- `-c core.fsmonitor=false`
- `-c core.hooksPath=/dev/null`
- `-c protocol.allow=never`, which turns off every transport, so any fetch
  attempt fails
- `-c log.showSignature=false`, so no gpg
- `-c core.untrackedCache=false`
- `-c core.attributesFile=/dev/null`
- `-c diff.external=`
- `--no-pager`

**Partial clones.** A repository with `extensions.partialClone` or any
`remote.*.promisor` set is `partial-clone`, and git doesn't read it. A missing
object there would mean a lazy fetch.

**Filters and textconv (`filters`).** Git can run a configured command whenever
it compares file contents, `status` included: `ce_compare_data`, then
`convert_to_git`, then `apply_filter`. So these checks run before any git
command that reads the working tree, and any hit refuses inspection:

1. **Config.** `git config -z --get-regexp '^(filter\..+\.(clean|smudge|process)|diff\..+\.textconv)$'`
   run in the worktree under the environment above. It reads only config,
   includes too. Any output is a hit.
2. **Attributes files.** The files read are:
   - `info/attributes` in the common git dir and in the worktree's own git dir;
   - every `.gitattributes` path from
     `ls-files -z --cached --others --exclude-standard`. `ls-files` lists names
     and runs no filter.

   Each file's working-tree copy is read, or its index blob
   (`cat-file blob :path`) when there is no working-tree copy. Reads are
   `O_NOFOLLOW`, regular files only, and capped at 64 KiB each and 200 files.
   A token that starts with `filter=` or `diff=` on any line is a hit. That
   includes `[attr]` macro lines.

Past a cap is `too-large`. `core.attributesFile=/dev/null` and
`GIT_ATTR_NOSYSTEM=1` shut out the global and system attributes files. Diff
also runs with `--no-ext-diff --no-textconv`.

The remaining config git might execute is never reached by these commands:

- `core.sshCommand`, `credential.helper` and every transport: `protocol.allow=never`;
- `core.pager`: `--no-pager`;
- `gpg.program`: `log.showSignature=false`;
- hooks and fsmonitor: off.

**Commands**, after the checks:

- `rev-parse HEAD`
- `rev-parse --abbrev-ref HEAD`
- `status --porcelain=v1 -z --ignore-submodules=all`
- `diff HEAD --binary --no-ext-diff --no-textconv --no-color --ignore-submodules=all`.
  Its output is streamed into SHA-256 and never kept (§9.1).
- `ls-files -z --others --exclude-standard`. Each untracked path is then hashed
  by btq, not git:
  - a regular file: its contents, opened `O_NOFOLLOW`;
  - a symlink: its target text, never followed;
  - anything else: its type.
- `rev-list --count BASE..HEAD`
- `log -n 20 --format=%H%x00%cI%x00%s BASE..HEAD`
- `log --all -n 10 --fixed-strings --grep=BEAD --format=...` in the repository
- `merge-base --is-ancestor HEAD REF` (§3.3)
- `branch -r --contains HEAD --format=%(refname)`

**Bounds.**

- Each command runs in its own process group with stdin `/dev/null`.
- stdout is read up to 256 KiB and stderr up to 16 KiB. Past either limit the
  group is killed and the fact is `too-large`.
- The time limit is 10 s per command, 15 s per bead and 90 s per scan.
- When the bead or scan budget runs out, the bead is **deferred**: no card this
  scan, reported as `deferred: inspection budget`. After three consecutive
  deferrals, the card is posted with `not inspected (budget)`.
- Commit lists are capped by `-n`, and status entries at 1,000.
- The diff and the untracked contents are hashed as streamed. Together they are
  capped at 32 MiB and 2,000 untracked paths. Past either cap the fact is
  `too-large`, and the card is not decidable (§2.2).

### 3.3 Progress and evidence

Progress is one of three values, and the card shows it as btq's reading:

| Progress | When |
|---|---|
| `untouched` | the recorded worktree was inspected: 0 commits ahead of base, 0 changed or untracked files, no commit elsewhere in the repository naming the bead, and no holder comment or note after its claim |
| `started` | at least one commit ahead, at least one changed file, a commit naming the bead, or a holder comment or note after the claim |
| `unknown` | anything else: no worktree recorded, or `not inspected (...)`, and no holder comment after the claim |

`unknown` means missing evidence. It never means "no work". The card says it
plainly:

> btq could not see this bead's work (no worktree recorded); work may exist
> elsewhere.

Two further facts are shown as evidence, never as progress:

- `review line recorded`: a `Code-Review:` line by the holder. That is a review
  assertion, not completion.
- `HEAD contained in <ref> (local refs; last fetched <time of FETCH_HEAD, or
  unknown>)`. That is local containment as of the last fetch. It is not proof of
  a merge, and the card never calls it done.

## 4. Links (R5)

admind takes up to 8 links in its pinned forms. In priority order:

1. PR URLs (up to 4);
2. HEAD as `commit/<40hex>`, only when a remote-tracking ref contains it and the
   remote is on github.com;
3. runs and issues.

Every URL that isn't linked is quoted, defanged (§6.2), in More.

## 5. Budget and overflow (R6)

### 5.1 The card budget

admind's question card shows the title, a blank line and the body, after
redaction, up to 40 lines and 3,500 characters (`CARD_LINES`, `CARD_CHARS` in
`src/heterodyne/admind/asks.py`, heterodyne-metaharness-v2 at 1638035). btq's
cap for the header, sections 1-4 and the request line is 36 lines and 3,200
characters.

When a card is longer than `chunk_chars`, admind already threads its parts
(`↳ part i/n`, cxmkf K22/K23).

### 5.2 Fitting the card

Sections 1-4 shrink in fixed steps. Each step moves what it removes to More:

1. the description lines, 8 down to 3;
2. the commit lines, down to 1;
3. the comment excerpt, down to 100 characters;
4. the dependency titles (the IDs and statuses stay).

The decision summary, the option and gate lines (§7) and the recommendation are
never shrunk. A test
generates maximal beads and pins that the card always fits after these steps. If
it ever doesn't, the card is `too-long`, as today.

### 5.3 Overflow needs admind

Today admind shows a question card's body up to the budget, adds
`(n lines shown of m; reply !details for the rest)`, and keeps the rest for
`!details`. The rest isn't lost, but it isn't threaded.

**admind A1** is a dependency for the admind workstream: `ask post --whole` for
question asks. The context is packed whole, up to `MAX_BODY`, by `chunk.pack`
into threaded parts. Redaction, links, choices and footer behave as today, and
the closing lines stay last.

btq passes `--whole` when the supervisor config has `admind_whole: true`.

Rejected alternatives:

- notices: they have no reply-to, so they wouldn't sit under the card;
- a second ask: it would make two answerable cards for one bead.

Until A1 lands, More is behind `!details`. Q1 asks Liam whether that is
acceptable once he has seen rendered cards.

## 6. Sanitizing and rendering (R7)

### 6.1 `prose(value, limit)`, whole field first

1. **Whole field.** Remove every secret span (`_SECRETS`, repeated until stable,
   as `clean()` does) from the *complete original value*, before anything else.
   PEM blocks span lines, so this must run before any split.
2. Split into lines. Replace PEM armor remnants with `<pem>` and runs of 20 or
   more letters and digits with `<long>`.
3. Clean each line (§6.2 step 1) and cut it to its limit with `...`, then quote
   it as `| ` and one code span (§6.2 step 2).
4. Mark a value that changed `(sanitized)` on its first line.

`clean()` keeps serving the one-line enum-like fields.

### 6.2 Neutralizing for the renderer

The renderer is the Marmot client's message view. btq can't test it, and whether
it renders Markdown or linkifies bare domains isn't pinned anywhere. So quoted
text is made inert for the worst Markdown case: CommonMark with GFM's
extensions, which are tables, strikethrough and extended autolinks (`www.`,
schemes, emails), plus a linkifier.

d3 showed that escaping can't be made complete. Entities (`&commat;`,
`&period;`) are decoded after btq's text rules have run, and GFM's autolink
grammar takes numeric labels (`www.123.456`). So r4 doesn't escape. It quotes
each line inside a code span, where CommonMark and GFM:

- decode no entities;
- apply no backslash escapes;
- parse no emphasis, links, images or raw HTML;
- run no autolinking.

These steps run in order, on each line, after §6.1's secret removal.

1. **Characters.** Printable Unicode is kept, so `≤ 5 ms` and `≥ 5 ms` stay
   distinct, and names in any script read as written. Removed:
   - controls (Cc);
   - format characters (Cf), which covers every bidi mark and zero-width
     character;
   - surrogates, private-use and unassigned (Cs, Co, Cn);
   - line and paragraph separators (Zl, Zp);
   - enclosing marks (Me);
   - the blank-looking letters approve-bead already lists (U+115F, U+1160,
     U+3164, U+FFA0, U+2800).

   Other spaces (Zs) become U+0020. A run of more than 3 combining marks (Mn) is
   cut to 3. A line where this step removed anything is marked `(sanitized)`, on
   btq's side of the quote.

2. **The code span.** The line, already cut to its limit, becomes
   ``| <fence> <text> <fence>``. `<fence>` is a run of backticks one longer than
   the longest backtick run in the text, so no run inside can close it. One
   space pads each side, and CommonMark strips exactly one from each side. That
   keeps text that starts or ends with a backtick intact. An empty line is `|`
   alone.

   Backticks in the text stay as written; only the fence grows. A line holds
   exactly one code span, and nothing quoted sits outside it.

The r3 backslash escapes are dropped, because inside a code span they would show
as literal backslashes. So is the `[.]`, `[@]` and `[:]//` defanging, because
the code span already stops every Markdown link form. For a plain-text client
that runs its own linkifier, see §15.

A `|`-led line can't start a GFM table. A table needs a delimiter row such as
`| --- |`, and every quoted line's text is inside backticks, so no quoted line is
one. btq's own lines never are either.

Real links come only from admind's `--link` block, which only btq fills.

### 6.3 The refusal stays

`secret_shaped()` runs on all of these:

- the bead ID;
- every link;
- the title;
- every choice;
- the **whole body**, More included.

If any match, the card is refused with the fixed `SecretShaped` message, and the
bead ID is never logged.

## 7. Options: what changes, and what still applies (R3)

Choice strings stay as they are, in a stable order per class. A card that is not
decidable drops the release, close and open choices (§2.2) and adds "send it
back for grooming" when the summary is missing. Each option gets two btq lines:

- **Changes:** what the controller's action does to the bead, from a fixed
  table.
- **Still required:** the gates that stay after that action, computed from this
  scan's read. The pickup gates btq evaluates are:
  - status;
  - assignee;
  - open blockers other than this bead;
  - `needs-human`, and who added it;
  - route problems;
  - btq's design gate (`Queue.design_allowed`, read-only).

  If none remain, the line reads `none known to btq; pickup also needs a free
  worker on its route`. These lines are part of the evidence digest (§9.1).

| Choice | Needs a summary | Changes | Work |
|---|---|---|---|
| leave it / keep it | no | nothing | nothing |
| release it once its processes have exited | yes | after confirmed exit, as "release it, blocked" | as below |
| release it, blocked (incl. "worker is gone") | yes | holder cleared, status blocked, release note with the ask ID | the recorded worktree and branch stay; the note names them |
| release it, open (incl. "worker is gone") | yes | holder cleared, status open; the `needs-human` that btq-supervise added is removed (one added by anyone else stays, and is listed under Still required) | the worktree and branch stay; the note names the branch |
| close it, not completed | yes | closed, reason `not completed (ask <id>)`. This bead stops blocking its N dependents; each of them still has its own blockers, labels, route and design gate, which btq does not evaluate on this card | the worktree and branch stay; nothing is merged or deleted |
| clear needs-human and open it | yes | `needs-human` removed, status open | none |
| set kind:X | no | the bad kind label is replaced by `kind:X` | none |
| leave it for a person | no | nothing | none |
| remove needs-human | no | `needs-human` removed | none |
| send it back for grooming (§2.2) | no (offered only without one) | the controller writes the decision summary; a replacement card follows | none |

"Worker is gone" options keep the OPS rule that unreachable is not dead: the
controller confirms termination first, and the line says so.

OPS.md's answer procedure takes this table, including the `needs-human` rule for
"release it, open". A test asserts every choice has a row.

## 8. Recommendation (R4)

`recommend(facts)` returns a letter and a reason, passed as `--recommended`.
The first matching rule wins:

| Facts | Recommended | Reason, as printed |
|---|---|---|
| stale-flag | remove needs-human | the claim btq flagged has ended |
| unroutable, a kind fix exists | set kind:X | the kind label is its only routing problem |
| not decidable, no summary (§2.2) | leave it | no decision summary has been written; choose grooming to have one written, and a new card follows |
| not decidable, evidence not read whole (§2.2) | leave it | btq couldn't read the recorded work whole, so no answer could be bound to it |
| holder, process alive | leave it, keep the claim | its processes still run; releasing could leave two holders |
| holder, process unknown | leave it | btq can't tell whether the worker is alive; unreachable is not dead |
| holder, process gone, progress `untouched` | release it, open | the worker is gone and its recorded worktree shows no work |
| holder, process gone, progress `started` or `unknown` | release it, blocked | the worker is gone and work exists or can't be seen; blocked keeps anyone from starting over until you choose how to resume |
| unroutable, otherwise | leave it for a person | its route needs a decision btq can't offer |
| escalated, no holder | leave it | a worker asked for a person; see its comment above |

Close is never recommended. Release open is recommended only when the work was
inspected and found absent. The first two rows are label fixes, which need no
summary. Every release row comes after the not-decidable rows, so it applies only
to a decidable card.

## 9. Binding the answer to what was shown

### 9.1 The evidence digest

`AUTH_FACTS` gains `evidence`. It is the SHA-256, cut to 12 hex, of the
canonical JSON (sorted keys, no whitespace) of the following.

**The bead, raw, before cleaning:**
- title, type, priority, status, assignee;
- description, design, acceptance criteria;
- **notes**;
- the **whole `metadata` object**: approval pins, `stall_hours` and any other
  key;
- labels, sorted, without the `needs-human` btq-supervise added (below);
- dependency IDs, types and statuses.

`updated_at` is left out: btq's own writes move it.

**Comments**, except btq-supervise's own (below). For each: ID, `created_at`,
author, and the SHA-256 of its text. The decision summary is one of these, so
writing or changing it changes the digest. Over 500 comments is `too-large`.

**Worktree:**
```
{"head": 40hex, "branch": name, "ahead": n,
 "status": sha256 of the -z status output,
 "diff": sha256 of `diff HEAD --binary ...`,
 "untracked": sha256 over the sorted (path, kind, content sha256) list}
```
That shape applies when the worktree was inspected. Otherwise it is
`{"unknown": <reason code>}` or `{"none": "no worktree recorded"}`.
- A second edit to an already modified file changes `diff`.
- An edit to an untracked file changes `untracked`.

**Commits:** the branch's commit SHAs and the bead-naming commit SHAs.

**URLs:** the set of URLs.

**Derived values:**
- progress;
- review line yes/no;
- containment yes/no/unknown;
- decidable, and its reason;
- the decision summary's pin: its comment ID and text SHA-256 (§2.2, §10.1);
- every option's Still required line (§7).

**btq-supervise's own writes are not evidence.**
- **Its comments.** A comment is left out when its author is the supervisor's
  identity *and* its text starts with `btq-supervise: escalation`. It is left
  out of the card too, so the digest still covers everything the card shows.
  This is not `decision-summary:`, which is evidence.
- **Its label.** `needs-human` is left out of the label set while the record
  says btq-supervise added it (`flag_added`, settled by `attribute()` as
  today). A `needs-human` that anyone else added stays in the set and in Still
  required.

**When the digest is computed.** After the supervisor's own label write: `act()`
recomputes the facts from the read-back that `flag()` already does, then builds
the card. Its own comment, written after the post, is not evidence. So the next
scan, with no one else acting, computes the same digest.

A posted card's digest (`facts_digest` on its generation) is never rewritten.
If the facts change, a new generation is posted (§9.2).

The controller's existing step 2 refuses to act when the dry-run digest differs
from the card's. r1's Q3 claim that the excerpt was "covered by the digest" is
now true: the whole text, the work and the summary are.

### 9.2 Replacement cards

`due()` gains one rule. A replacement is due when all of these hold:

- the record's current generation is `posted`;
- the current digest differs from its `facts_digest`;
- `replace_after_minutes` (new config key, default 30) have passed since
  `posted_at`;
- the bead is not held (§9.3).

A replacement **cancels first, then posts:**

1. The record moves to `replacing`, holding the old ask's ID, and is saved and
   fsynced.
2. `admind ask get OLD`:
   - If it is terminal with an answer (`approved` or `denied`), the replacement
     is dropped. The generation closes as today, and the controller handles
     that answer.
   - If it is `cancelled` or another terminal state, go to step 4.
3. `admind ask cancel OLD`, then `ask get OLD` again.
   - Not cancelled: the record stays `replacing`, nothing is posted, and the
     next scan retries from step 2.
   - An answer arrived in between: as in step 2.
4. The new generation, n + 1, made by `begin()`, posts through the normal
   `posting` path. It carries one btq line: `Replaces ask <id>: the facts
   changed (<which parts: text, comments, notes, metadata, worktree, commits,
   links, process, gates>)`.

`status` lists `replacing` records with the old ask and the attempts.

**Capacity.** A replacement takes one ask off before it adds one, so it is
not counted against `max_open_asks`. That works at the cap, and the cap never
grows. It does count against `max_new_asks_per_scan`, which bounds churn. It
only posts after the cancel is confirmed, so two answerable cards for one bead
never exist.

Closed records keep the quiet-threshold rule, so answered beads don't churn.

**Fail closed.** If the dry-run can't compute a digest for the bead, the
controller acts on nothing. That covers a deferred bead, a bd error, or a bead
that is no longer in any class. It tells Liam the card is stale and that a new
one follows when the facts settle.

r1's statement that "new commits are activity" is withdrawn. `activity()` doesn't
read git, and detection is unchanged. Changed work reaches the operator through
replacement cards, not through the stall clock.

### 9.3 The controller's hold

A scan holds the store lock for its whole run, and `mark` already refuses
while a scan holds it. `hold` uses the same lock:

- `btq-supervise hold BEAD --ask ASK`. Under the store lock it records
  `held: {ask, at}` on the bead's record. While a scan runs it refuses, and the
  controller retries. So a hold can only land between scans: a scan that
  collected facts before the hold has finished before the hold is written.
- `btq-supervise hold BEAD --done`. It clears the hold and closes the current
  generation (`closed_at` now), so the quiet threshold starts.

While a bead is held, a scan publishes nothing for it:

- no new ask;
- no replacement;
- no cancel;
- no record comment.

It reports `held (ask <id>)`. The hold never expires by time. `status` lists
it, so a forgotten hold is visible.

OPS's answer procedure becomes:

1. Read the answer.
2. `hold BEAD --ask ASK`.
3. Dry-run and compare digests.
4. Cancel the ask.
5. Carry out the answer.
6. `hold BEAD --done`.

If the digest differs at step 3, the controller cancels nothing, runs
`hold --done`, and tells Liam a replacement card follows.

## 10. Records and the bead comment

The record's `body` holds the whole posted body, and `status --json` shows it.

The `btq-supervise:` comment keeps its two header lines, so `SUPERVISOR_COMMENT`
and `reseed` are unchanged. It then holds only btq's own lines:

- class;
- progress;
- process;
- counts;
- the evidence digest;
- the recommended letter;
- the choices.

It holds no bead text, so a bead's own text is never echoed back into its
comments. It isn't evidence (§9.1).

### 10.1 `btq-supervise summary BEAD --file F`

This is the controller's tool for writing a decision summary.

1. It reads F: at most 1,200 characters and 12 lines. It refuses anything that
   matches `secret_shaped`, and empty text.
2. It takes the store lock, so it never runs alongside a scan.
3. It writes one comment as the supervisor's identity: the line
   `decision-summary: <bead> at <time>`, then the text.
4. It confirms the comment by reading the bead's comments back. It needs exactly
   one comment whose author, first line and whole text equal what it wrote, and
   that wasn't in the comments read before the write. Otherwise it writes no pin
   and exits non-zero, saying the comment may have landed and that a re-run
   writes a new one. An orphan comment is never a summary, because nothing pins
   it.
5. It pins the comment in the store, at `summaries/<bead>.json` (mode 0600; tmp,
   fsync, rename, fsync the dir), still under the store lock:
   `{"format": 1, "bead": ..., "comment_id": ..., "created_at": ..., "author": ...,
   "text_sha256": ..., "pinned_at": ...}`.
   A later `summary` for the same bead replaces the pin, and only the newest pin
   counts.

The store is the supervisor's private state, `~/.local/state/beads-task-queue/supervisor/`
(0700). Only btq-supervise writes it: `scan`, `mark`, `hold` and `summary`. The
pin has the same trust as the store's records, which a scan already relies on.
Writing it needs filesystem access to that directory. Writing a comment needs
only bd access. The two are separate grants, and only the second is a worker's.

**Reading.** `examine` loads the pin for each bead it builds a card for, and
applies §2.2's rule against the comments it has already read. A malformed pin
counts as missing, and `status` reports it. The pin never makes a card decidable
on its own: the comment has to exist and match.

The card shows the pinned comment, quoted (§6), with its time. OPS says what a
summary must cover:

- what the work is;
- what was done, with its evidence;
- what each option would do to it.

## 11. Rendered cards

These are synthetic, and they illustrate layout only. They are not the rollout
evidence. The five real stalled beads are rendered whole from the controller's
dry-run before Q1 is asked (§12).

The bodies below are what btq posts. admind adds its label, the head, the links
block and the choices footer around them. The A-D letters are admind's.

These samples predate r4. On a posted card, each `| ` line's text sits in one
code span (§6.2 step 2), with no backslash escapes or `[.]`. The samples show
the text bare, for readability.

**Case 1: malformed routing labels, no summary (unroutable, not decidable)**

```
NOT DECIDABLE HERE: no decision summary has been written for this bead.
Class: unroutable bead. Bead bq-k2m7d.
What it is
  Title, type, priority (from the bead):
  | Split the backup job into dump and upload steps (task, P2)
  Description (8 of 19 lines; all of it in More):
  | The nightly backup runs dump and upload in one script; a failed upload
  | discards the dump. Split them so the dump is kept and upload retries\.\.\.
  Labels (from the bead):
  | agent:claude ws:hermes kind:impl kind:task
  Route problems: kind labels (2 found)
What has happened
  Holder: none. Status: open. Progress: untouched (no worktree recorded; nobody
    has claimed it). Comments: 0.
Options
  A set kind:task. Changes: the two kind labels become kind:task.
    Still required: none known to btq; pickup also needs a free worker on its route.
  B leave it for a person. Changes: nothing. Still required: its route.
  C send it back for grooming. Changes: the controller writes a decision
    summary; a new card follows.
Recommended: A. The kind label is its only routing problem.
More
  ...
request 4fzq7n2kd8xa; facts 3c1e0b7a9d24
```

A label fix needs no summary, so A is offered and recommended. Close is not
offered without a summary.

**Case 2: Codex session gone about three weeks, no worktree note, summary
written**

```
Class: stalled claim, no activity for 512.4h (threshold 2h). Bead bq-r8t3w. Decidable.
What it is
  Decision summary (written by the controller at 2026-10-15T09:10Z):
  | Adds one retry to btq bd OP on a bd timeout, then fails closed\. Nothing
  | was pushed: no branch or PR names the bead, and the worktree note is
  | missing, so any local work is unknown\. Releasing blocked keeps it held
  | back until a person says how to resume; releasing open lets any Codex
  | worker start over\.
  Title (from the bead):
  | Retry bd passthrough once on a timeout (task, P2)
What has happened
  Holder: codex on this host, claimed 2026-09-23T10:02Z.
  Process: gone (no live process in its session since boot 2026-10-01).
  Progress: unknown. btq could not see this bead's work (no worktree recorded);
    work may exist elsewhere.
  Comments: 2 (0 by the holder; 1 decision summary).
Options
  A release it, blocked. Changes: holder cleared, status blocked, release note
    with this ask. Still required: a person unparks it; needs-human (added by
    btq-supervise) stays until then.
  B release it, open. Changes: holder cleared, status open, btq-supervise's
    needs-human removed. Still required: none known to btq.
  C leave it. Changes: nothing.
  D close it, not completed. Changes: closed; it stops blocking 1 bead, which
    still has its own gates.
Recommended: A. The worker is gone and its work can't be seen; blocked keeps
  anyone from starting over until you choose how to resume.
request ...; facts ...
```

**Case 3: work in the default branch (local containment)**

```
What has happened
  Holder: claude on this host, claimed 2026-10-13T08:00Z. Process: gone.
  Progress: started. 4 commits ahead of base, 0 changed files.
  Branch and commits (from git):
  | feat/backup-split
  | 1c0d4e5f6a7b 2026-10-13T11:20Z split upload into its own unit
  | (3 more in More)
  Evidence: HEAD contained in origin/master (local refs; last fetched
    2026-10-14T06:00Z). Review line recorded by the holder. Neither proves the
    work is complete.
Options
  A release it, blocked ...
  ...
Recommended: A. The worker is gone and work exists; blocked keeps anyone from
  starting over until you choose how to resume.
```

The card leaves "it's merged, close it as done" to Liam, with the evidence
above. btq never offers "done".

**Case 4: unpushed branch with uncommitted changes**

```
What has happened
  Progress: started. 2 commits ahead of base, 3 changed files, not on any
    remote-tracking ref (unpushed).
  | feat/retry-timeout
  | 9f3c2e1a7b04 2026-10-14T09:40Z add retry test
Options
  A release it, blocked. Changes: holder cleared, status blocked. Work: the
    worktree with its 3 uncommitted files and the unpushed branch shown above
    stay where they are; the release note names them.
  ...
```

The digest binds HEAD, the status, the diff against HEAD and the untracked
contents. One more commit, or one more edit to any of those files, makes a
replacement due (§9.2).

**Case 5: another host**

```
What has happened
  Holder: claude on another host, claimed 2026-10-14T07:00Z.
  Process: unknown (another host; unreachable is not dead).
  Progress: unknown. btq could not see this bead's work (other-host); this is
    missing evidence, not absent work.
Recommended: leave it. btq can't tell whether the worker is alive.
```

**Case 6: a long description but no summary (not decidable)**

```
NOT DECIDABLE HERE: no decision summary has been written for this bead.
Class: stalled claim ... Bead bq-p0v9c.
What it is
  | Implement the agreed recovery change; see chat (task, P3)
  | Implement the agreed recovery change; see chat\. Implement the agreed ...
Options
  A leave it. Changes: nothing.
  B send it back for grooming. Changes: the controller writes a decision
    summary; a new card follows.
Recommended: A. No decision summary has been written; choose B to have one
  written, and a new card follows.
```

This is d2's 383-character repetition. Its length no longer matters.

**Case 7: a filter in the worktree (not decidable)**

```
NOT DECIDABLE HERE: btq could not read its recorded worktree whole (filters).
...
  Progress: unknown. btq did not inspect the recorded worktree: its
    repository configures a content filter or textconv, which git would run.
Options
  A leave it. Changes: nothing.
Recommended: A. btq couldn't read the recorded work whole, so no answer could
  be bound to it.
```

With a summary present and only "leave it" left, the card has a single choice.
So it is posted as a notice to Liam ("needs a person: <reason>"), not as an ask.

## 12. Rollout

1. TDD implementation, then Codex review. The timer stays stopped.
2. The controller writes decision summaries (§10.1) for the five stalled beads
   it can describe. Then it runs `btq-supervise scan --dry-run --json` and
   writes the five real cards, whole (header to More), into a doc. Liam reads
   them, and Q1 is asked with that doc linked. This is the rollout gate.
3. On Liam's go, run one live scan with `max_new_asks_per_scan: 1`. Check the
   card on Marmot against `status --json`. Check that quoted lines render as
   literal code text, and that no quoted text renders as a link.
4. Re-enable the timer.

## 13. Tests (tests/test_supervise.py)

- **R1 / §2.2:**
  - no summary: not decidable, whatever the description's length (d2's
    383-character repetition included). Exactly leave, groom and any label
    fix are offered;
  - **pinned summary (d3's B5):**
    - a comment with the supervisor's author string and a correct
      `decision-summary:` header, written with `bd comments add --author` and
      no pin, does not count. The card is not decidable, and the comment is
      shown as an ordinary comment;
    - the same forged comment, newer than a real pinned summary: the pinned one
      is shown, and the forged one is an ordinary comment;
    - the pinned comment's text edited under the same ID, its author changed,
      or its header changed: each makes the card not decidable with "changed or
      removed";
    - the pinned comment deleted: not decidable, with "changed or removed";
    - a pin naming another real supervisor comment (substitution): its text
      hash fails, so not decidable;
    - a malformed pin, or one for another bead, counts as missing;
    - `summary` writes the pin only after the read-back finds exactly one new,
      matching comment;
    - a read-back that finds none or two writes no pin and exits non-zero;
    - a second `summary` replaces the pin;
    - a changed pin changes the digest;
  - the pinned summary is shown whole;
  - each worktree reason in §2.2's list makes a card not decidable, and
    `none recorded`, `other-host` and `missing` do not;
  - a not-decidable card with one choice becomes a notice, not an ask;
  - `summary` refuses over 1,200 characters, over 12 lines, a secret shape,
    empty text, and running during a scan.
- **§3.2, the guard** (temp repos, argv and env asserted):
  - roots, symlink escape, not owned;
  - not associated (a worktree of another repository);
  - partial clone refused before any git read;
  - `protocol.allow=never`, `core.attributesFile=/dev/null`,
    `GIT_ATTR_NOSYSTEM=1` and the env on every call;
  - **filters (d2's B4):**
    - a repository with `filter.x.clean` set and a `.gitattributes` with
      `* filter=x`, where the filter command writes a marker file;
    - then each alone;
    - `diff.x.textconv`;
    - `filter=` in `info/attributes`;
    - a `[attr]` macro;
    - an untracked `.gitattributes` in a subdirectory;
    - one deleted from the worktree but in the index.

    Each gives `filters`, the marker file is never written, and no `status` or
    `diff` runs;
  - output over the cap killed and reported `too-large`, and the diff and
    untracked cap (32 MiB, 2,000 paths) likewise;
  - budget exhausted defers, and the third deferral posts with
    `not inspected (budget)`.
- **§3.3:**
  - each progress row;
  - no worktree note gives `unknown`, never `untouched`;
  - the review line and containment appear as evidence, never as progress.
- **§7:**
  - every choice has a row;
  - release, close and open are never offered without a summary;
  - Still required lists a remaining `needs-human` added by someone else;
  - the close effect never says "ready".
- **§8:** each row; close is never recommended; release open only with
  `untouched`.
- **§9.1, d2's B1:** the digest changes on each of these, with progress
  unchanged:
  - a second edit to an already-modified file;
  - an edit to an untracked file;
  - a new commit;
  - a staged-only change;
  - an edited description;
  - an edited notes field;
  - a metadata key;
  - a comment's text edited under the same ID, then its author;
  - a summary written;
  - a Still required line changing (another `needs-human`).
- **§9.1, d2's B2:**
  - post a card, then run three scans with no other activity. Each computes the
    same digest, nothing is replaced, and the posted `facts_digest` is
    unchanged;
  - the same with the flag written in the posting scan;
  - a comment by another author starting `btq-supervise:` is evidence, and the
    supervisor's own is not.
- **§9.2-9.3, d2's B3:**
  - at `max_open_asks`, a due replacement cancels, then posts, and the open
    count never exceeds the cap;
  - a cancel that fails leaves `replacing`, posts nothing, and is retried next
    scan;
  - an old ask answered before the cancel drops the replacement;
  - a held bead gets no ask, replacement, cancel or comment;
  - `hold` refuses while a scan holds the lock;
  - `hold --done` closes the generation;
  - a deferred bead gives the controller no digest.
- **§6:**
  - a PEM block across lines leaves no payload line;
  - every `_SECRETS` shape is caught: in a description, in a comment, in a
    commit subject, in a summary, and split across a cut;
  - the rendered card passes through admind's `question_card` and `redact`.
    These are imported from a heterodyne checkout at the pinned commit; without
    one, the test is skipped and says so loudly. The card is unchanged by
    redaction, or refused.
- **§6.2, d2's B6:**
  - `latency ≤ 5 ms` and `latency ≥ 5 ms` render differently, and both
    unchanged inside their code spans;
  - a bidi override, a zero-width joiner and a Hangul filler are removed and
    marked `(sanitized)`;
  - CJK and accented text is unchanged.
- **§6.2-6.3, spoofing and links, on rendered output (d2's and d3's B7).** The
  whole card is rendered twice:
  - with cmark-gfm (the `cmarkgfm` package's `github_flavored_markdown_to_html`,
    which turns on the autolink, table, strikethrough and tagfilter extensions);
  - with markdown-it-py using `linkify` (linkify-it-py) and its table and
    strikethrough rules.

  If either renderer is missing, the test is skipped and says so loudly.
  - Inputs. A description, a title, a branch name, a commit subject and a
    summary each carry these, one per line, then all on one line:
    - d3's PoCs: `a&commat;b&period;io` and `www.123.456`;
    - entities: `www&#46;example&#46;com`, `https&colon;//example&period;com`,
      `&#x40;`, `&lt;b&gt;`;
    - numeric domains: `www.123`, `www.a.123`, `www.foo.123abc`;
    - email: `a@b.io`, `mailto:a@b.io`, `xmpp:a@b.io`, `@mention`;
    - www and bare domains: `www.example.com`, `example.com`,
      `xn--80ak6aa92e.com`, a full-width-dot domain;
    - schemes: `https://e.invalid`, `ftp://e.invalid`, `<https://e.invalid>`,
      `http://[::1]/`;
    - Markdown: `Recommended: D`, `[x](https://e.invalid)`, `![i](x)`, `**A**`,
      `~~s~~`;
    - raw HTML: `<a href="https://e.invalid">x</a>`, `<img src=x>`;
    - pipes: `| --- | --- |`, `a | b`;
    - backticks: `` `www.example.com` ``, ``` ``a``b` ```, a lone backtick, a
      trailing backtick;
    - edges: a leading and trailing space, a trailing backslash, a bidi mark.
  - In both renderings, the only tags are `p`, `code` and the line breaks btq's
    own layout makes. There is no `a`, `img`, `em`, `strong`, `del`, `table` and
    no raw HTML.
  - Each quoted line renders exactly one `<code>` element. Its text, after HTML
    unescaping, equals the line's text after §6.2 step 1, so nothing is decoded
    or lost.
  - Every line not starting with `| ` matches a btq template.
  - The choices and the recommendation are unchanged.

  The code-span rule was checked before r4 was written. cmark-gfm 2025.10.22 and
  markdown-it-py with linkify-it-py 2.2.0 rendered every input above as one
  code span, with no other tags, and both of d3's PoC inputs became plain code
  text.

## 14. Questions for Liam (asked after he has seen §12 step 2's cards)

- **Q1:** Until admind A1 lands, More is behind `!details`.
  - A: ship it that way (recommended). The decision summary, the evidence, the
    options with their effects, and the recommendation are on the card. More
    adds the bead's own full text and the full lists, one reply away.
  - B: keep the timer off until A1 lands.
- **Q2:** PR state (open, merged, closed) on the card.
  - A: no, show links and local containment, labelled as such (recommended).
  - B: yes, via `gh` with a read-only token, which adds network access to the
    timer.
- **Q3:** Summaries.
  - A: the controller writes the decision summary, which is shown whole and
    bound by the digest (recommended). Release and close are offered only with
    one.
  - B: a model-written summary at scan time, which can't be bound or
    reproduced.

## 15. Known residuals

- Work on a branch that wasn't recorded is invisible to btq. Such a card reads
  `unknown` and gets a conservative recommendation, but btq can't find the
  branch.
- Another host's worktree is never inspected.
- **Ignored files and submodules** are not in the evidence. `--exclude-standard`
  and `--ignore-submodules=all` skip them, and the card says "changed files"
  only of what git reports.
- **The summary is the controller's text.** btq binds it and shows it whole, but
  can't check that it is true. OPS sets what it must cover, and the evidence
  lines sit beside it on the card.
- **A forged supervisor comment.** A writer can forge the supervisor's author,
  because bd doesn't bind comment authors to an identity. A forged
  `btq-supervise: escalation` comment is hidden from both the evidence and the
  card, so the two still agree. A forged `decision-summary:` comment is not
  hidden, and it can't make a card decidable, because only the pinned comment
  counts (§2.2, §10.1).
- **The pin's trust is the store's.** A writer with filesystem access to the
  supervisor's store can forge a pin, as it could already forge the store's
  records. That is a different grant from bd write access.
- **Homoglyphs** in quoted lines now show as written. The `| ` prefix and the
  template rule keep them off btq's own lines.
- admind still rewrites login-dir paths on the rendered card. `status --json` is
  the reference.
- **Code spans assume a Markdown renderer.** On a plain-text client, the
  backticks show. A plain-text client that runs its own linkifier could link a
  domain or email inside a quoted line, because r4 no longer defangs dots or
  `@`. §12 step 3's check on Marmot covers this. If Marmot linkifies plain text,
  r3's `[.]`/`[@]` defanging comes back inside the code span, where it reads
  literally.
