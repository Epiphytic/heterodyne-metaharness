# Readable task-management approval cards

Status: proposed; one Claude Opus HIGH design review completed and incorporated once; human approval required.
Author: Codex. Design task: btq-pvmni. No deployment authority is requested here.

## Problem and scope

Management cards currently serialize JSON through a legacy sanitizer whose
`[^A-Za-z0-9 ._:/@#()+,-]` rule replaces braces and quotation marks with `?`.
They also appear as `QUESTION: Multiple Options`, although the requested decision
is permission for one exact task-state change. Native question framing repeats the
same choices. The enrolled format2 renderer also emits JSON inventories, so fixing
only the legacy renderer would leave the replacement harness unreadable.

Freeze legacy format1 posting and its bytes; implement concise prose for new enrolled
format2 management requests and an explicitly versioned
operation-consent presentation in admind. This implements the readability required
by management D4, with a narrow amendment for the new admind presentation contract.
The new presentation intentionally recommends A so ordinary approval words and
positive reactions approve the displayed operation. This narrowly amends the D4
B-recommended convention for new-style requests; legacy answers are unchanged.
It does not change task-management authority, design approval, execution approval,
claim ownership, review budgets, authentication, or deployment gates. Plan6's pure
presentation design is not authority to change live ask kinds or answer policy.

Source references: queue `bin/btq-manage.py` (legacy card and enrolled format2
render), `bin/btq` (code/quote), `bin/btq-supervise` (clean); product
`admind/asks.py`, `store.py`, `binding.py`, daemon post path and CLI.
Governing management design: `4e027cb71e241f02f2d51effeba9adb8e1ef30e0`,
`docs/designs/2026-10-16-management-design-task-recovery-d4.md`.
Retained Plan6 source: `8cc13faee7582f7a07a3a33ce8e2ceaf99da4097`,
`docs/adr/2026-10-16-plan6a-approved-d4-source.md`.

## Presentation contract

Add one optional closed presentation value, `task-management-consent-v1`, to
question posting, only with choices `Perform this exact change` and `Leave
unchanged and held`, A recommended. Generate `APPROVAL: Task management` and
replace the contradictory question icon/kind subtitle with `operation consent`.
The native kind remains `question`; native `approval` is reserved for approve-bead.
This is explicit presentation metadata, never inferred from a title, body or poster.
Expose the persisted tag/version in the post response and binding snapshot.

Authenticated A with no contrary or ambiguous answer authorizes only this exact
operation, after all existing source-owned binding, current-policy and fresh-facts
checks. Existing approval words (approve, approved, yes, y, ok, okay, lgtm) and
positive emoji (thumbs-up with supported skin tones, check mark, both heart forms)
map to recommended A through the existing parser. B leaves the task held. Negative
emoji (thumbs-down with supported skin tones and cross mark) remain `none`, not A;
the complete existing denial-word grammar remains unchanged. The footer says:
`A / approved / 👍 ✅ ❤ ♥ = approve; B / deny / 👎 ❌ = leave held.`
`Any other reply or contrary answer holds this request; a later A cannot override it.`
`Reply A or B, or !answer <id> A|B.`
List the choices once, no repeated choices footer. The word legend is representative;
`!details` also retains the complete accepted-word/skin-tone grammar. No unseen
semantic change is introduced to ordinary questions or old cards.

A tag is presentation, not proof of an authenticated local poster. Keep the truthful
unverified-poster attribution and add `Only a verified management binding authorizes
this change.` Require the closed management posting request to carry the exact
request ID, descriptor digest, immutable public audit URL and binding render version;
reject arbitrary or missing/inconsistent fields. Any same-UID caller could copy
those fields, so neither tag, poster name nor visual heading confers authority.
Management still verifies the exact retained native card and descriptor against its
own source-owned request and current principals before applying anything. Existing
native design/execution approval consumers must never accept these question-kind
cards. Test a forged same-UID post cannot authorize any management operation.

Wrong choices/recommendation, unsupported metadata or old daemons refuse; there is
no fallback or automatic retry after uncertain posting. Existing cards, answers,
reactions, repeat and details retain their historical framing and mappings.

## Human-readable body

Render from the same immutable typed change and captured facts used to authorize
the operation. Do not dump JSON or remove the sanitizer globally. For this new
closed path require redaction-equality screening, reject control/bidi characters,
and quote untrusted text literally without Markdown interpretation. Do not use
legacy code/quote/full/clean: their question-mark and <long> substitution is the
bug. If any material literal cannot be displayed unchanged, refuse the card.
Nonmaterial untrusted syntax may be escaped only if its displayed value is exact.
Only remove irrelevant sections;
never hide decisive uncertainty, holds, human restrictions or review findings.

Every card shows the task and exact operation; before/after state; complete material
reason and remaining holds; affected routing and genuine approval pins; material
review-budget limits/findings (issued, completed and remaining); task kind/class;
A attestation that the known review/protection sources are complete and this is
non-gate work; no review-budget reset or worker resumption; effect on pickup or
dependencies; and a verified
immutable GitHub link to the signed public audit descriptor. Request and facts
prefixes are correlation only. Full digests and inventories remain in retained
evidence and source-owned binding; private evidence is not published.

Status changes show old/new status and holder. Reclaim shows exact old/new holder,
truthful stopped/UNKNOWN process evidence and retained worktree custody. If native
policy allows an UNKNOWN cessation decision, explicitly say A also confirms that
the named worker has stopped; never apply that sentence to a known alive worker.
Dependency changes name edge type, old/new endpoints and the effect/protections of
each endpoint, including parent/dependent consequences. Do not add cessation
boilerplate when ownership is unchanged and it has no material effect.

The whole generated card, including heading, poster, link, choices and answer
instruction, must fit 36 lines and 3200 characters. The daemon, after allocating
the real ask ID and rendering with actual chunk configuration, must refuse before
ask insertion or sending if any part is truncated, redacted, or the complete card
exceeds these limits. The queue must use this native refusal contract, not estimate
framing or duplicate the renderer. Missing safe public evidence, redacted material facts,
truncation or overflow refuses the whole request; a link cannot replace the
decisive facts needed to approve it.

Synthetic example, not a real request (the implementation supplies a real URL):

```text
APPROVAL: Task management
Reopen btq-example for normal pickup
Before: blocked; unassigned
After: open; unassigned
Why: prerequisite repair is complete; the captured stale hold is resolved.
Remaining holds: none in captured task facts.
Route: heterodyne; worker selectors unchanged.
Design gate: btq-gate at revision a1b2c3d4e5f6 remains required.
Class: ordinary non-gate task.
Review: 1 issued, 1 complete, 2 remaining; APPROVE; no unresolved findings.
A attests complete known review/protection sources and non-gate work.
Effect: permits normal pickup; no claim, worker start or review-budget reset.
Audit details: <verified immutable GitHub descriptor URL>
Request: 0123abcd; facts: 89abcdef0123
A = Perform this exact change (recommended)
B = Leave unchanged and held
A / approved / 👍 ✅ ❤ ♥ = approve; B / deny / 👎 ❌ = leave held.
Any other reply or contrary answer holds this request; a later A cannot override it.
Reply A or B, or !answer <id> A|B.
Only a verified management binding authorizes this change.
```

## Persistence, binding and compatibility

Retain optional presentation and render version in additive nullable ask columns,
following the existing all-or-none `_CXMKF_COLUMNS` / `refreshed_from` migration
pattern. Both NULL means historical rendering. New rows insert both in the same
transaction as the ask. Require exact recognized paired values; immutable after
publication. Original fixed-column `_ASK` inserts leave NULL. Verify every captured
legacy writer; a genuinely positional full-row writer must refuse without partial
writes. Do not add a side table or rewrite/backfill old rows.

Keep binding envelope version 1 and all old canonical bytes/reproduction unchanged.
New presentation uses closed binding envelope version 2, binding exact metadata,
complete render inputs, links, descriptor digest and actual delivered native parts.
Format2 management request records remain `format: 2` but add the optional closed
field `render_version`; absent means 1, integer 2 means this presentation, all other
values refuse. V2 history-index validation explicitly admits this paired schema;
old key sets remain valid only with version absent. No version selected from a
current default may change an existing card. Unknown versions HOLD, including
when encountered by an old reader. Interrupted/uncertain posts use existing receipt
reconciliation, never blind reposting.

Legacy format1 is frozen: preserve its sanitizer, recommended A, title-based legacy
authorization limits, request hashes and history. Do not put the new presentation
on format1 or edit baseline btq/btq-supervise hashes protected by D4. New-style
requests require enrolled format2; enabling that is separately reviewed deployment.
Queue implementation branches from accepted 1ier9 commit
`5dcc80abd1fbaaf0a580e0d34b5c98111cf5949c`, in its own new task worktree. It extends
the format2 renderer and closed request/index readers, preserving renderer v1.
It does not edit the frozen 1ier9 or WVG installer candidates. Composition later
repins and reviews the new exact closure under existing publication gates.

Current-policy principals, original-message correlation, complete delivery/history,
untruncated parts and same-facts CAS remain required. The operator owns rollback:
first stop new management submissions, then reconcile every new-style request to a
verified terminal/cancelled state and retain audit evidence. Never cancel an
in-flight/uncertain effect just to roll back; reconcile or hold and escalate it.
The deployment executor must show zero unresolved v2 requests, then roll queue
back before product. If any remain, rollback is refused. Old code may not serve
repeat/details for v2 cards; keep the compatible product reader until drained.
Offline acceptance covers both rollback directions, old-writer compatibility and
partly-applied/uncertain-request refusal. No rollback may mutate historical cards.

## Work and acceptance

Use separate pinned product and queue implementation tasks under one approval that
names both targets. Product owns posting/CLI/persistence/rendering/binding and
compatibility and actual final framing-budget refusal. Queue owns new format2
prose plus retained v1 rendering,
version selection and exact request binding. Queue depends on accepted product
contract; neither silently edits the ongoing frozen installer or deployed bundle.
A separately reviewed composed artifact and genuine deployment approval are
required before installing either side. The deployment request links this design
and lists deviations; migration/rollback and old/new reader support are verified
offline first. Previous management APIs remain available with their old bindings.

Offline tests reproduce predecessor `??` corruption, then cover status/reclaim/
dependency variants and all material UNKNOWN/hold/review restrictions; readable
golden output without raw JSON; once-only choices and exact label; A/B and reaction
semantics including approved/check/cross and ordinary question preservation;
old-card/repeat/details/binding bytes unchanged; same-UID misuse and unknown
versions; atomic metadata and rollback-writer compatibility; tampered metadata,
body/link/descriptor/parts/principal; incomplete delivery/history; stale facts;
hostile prose/Unicode/control/secret/Markdown; and complete-card budget refusal.
No live transport or native task mutation is necessary for these tests.
