# Readable task-management approval cards

Status: proposed; requires one Claude Opus HIGH design review and human approval.
Author: Codex. Design task: btq-pvmni. No deployment authority is requested here.

## Problem and scope

Management cards currently serialize JSON through a legacy sanitizer whose
`[^A-Za-z0-9 ._:/@#()+,-]` rule replaces braces and quotation marks with `?`.
They also appear as `QUESTION: Multiple Options`, although the requested decision
is permission for one exact task-state change. Native question framing repeats the
same choices. The enrolled format2 renderer also emits JSON inventories, so fixing
only the legacy renderer would leave the replacement harness unreadable.

Implement concise prose for both management renderers and an explicitly versioned
operation-consent presentation in admind. This implements the readability required
by management D4, with a narrow amendment for the new admind presentation contract.
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
generic question posting. Accept it only with the two exact choices `Perform this
exact change` and `Leave unchanged and held`, with B recommended. The generated
heading is `APPROVAL: Task management`; it never says `Multiple Options`.
The native kind remains `question`: native `approval` is reserved for the separate
approve-bead protocol and must not be repurposed. The response must expose the
presentation tag so a caller can verify that the requested framing was retained.
Titles, body text and poster names cannot select this presentation implicitly.

Preserve existing answer semantics: authenticated explicit A, without a contrary
answer, authorizes this one exact operation after all fresh-facts checks. B leaves
the task unchanged. A thumbs-up selects recommended B; thumbs-down grants nothing.
Show that mapping once, plainly. Do not use the native approve-bead reaction legend
or label a local posting process as verified. Existing genuine approvals stay valid.

The new closing lists each choice once and has one answer instruction. Existing
ordinary questions and native approval cards keep their exact old framing. Unknown
tags, wrong choices/recommendation, old daemons and unsupported versions refuse;
there is no silent fallback or automatic retry after uncertain posting.

## Human-readable body

Render from the same immutable typed change and captured facts used to authorize
the operation. Do not dump JSON or remove the sanitizer globally. Use existing
screening and literal quoting for untrusted text. Only remove irrelevant sections;
never hide decisive uncertainty, holds, human restrictions or review findings.

Every card shows the task and exact operation; before/after state; complete material
reason and remaining holds; affected routing and genuine approval pins; material
review-budget limits/findings; effect on pickup or dependencies; and a verified
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
instruction, must fit 36 lines and 3200 characters. Preflight actual final native
parts before posting. Missing safe public evidence, redacted material facts,
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
Review: 1 issued, 1 complete; APPROVE for pinned revision; no unresolved findings.
Effect: permits normal pickup; no claim, worker start or review-budget reset.
Audit details: <verified immutable GitHub descriptor URL>
Request: 0123abcd; facts: 89abcdef0123
A = Perform this exact change
B = Leave unchanged and held (recommended)
Reply A or B. Thumbs-up selects B; thumbs-down grants nothing.
```

## Persistence, binding and compatibility

Retain the optional presentation and render version durably and bind both with the
source-owned complete render inputs, body, links, full descriptor digest and exact
native parts. Prefer an additive side table keyed by ask ID rather than extending
positional legacy ask-row writes. Metadata and ask creation must be atomic; it is
immutable after publication. A missing row means historical rendering, not a new
default. Orphan/duplicate/incompatible metadata refuses new-style reproduction.

Keep existing binding-v1 canonical bytes and reproduction available for old asks.
Introduce a closed new binding/render version for new-style cards. Both readers
check exact source-owned retained bytes; unsupported versions HOLD. No global
formatter switch may change a historical card, repeat, details view or genuine
answer. Do not edit, resend or silently supersede existing requests. An uncertain
post is inspected through the existing receipt path before any further posting.

Legacy format1 and enrolled format2 management readers select a renderer from the
retained request version, not today's default. Old requests retain their original
card hash and choice text; new requests bind the new prose and presentation.
All current-policy principal checks, original-message correlation, complete
delivery/history, untruncated exact parts, freshness and same-facts CAS checks
remain required. Changing presentation cannot authorize a design or deployment.

## Work and acceptance

Use separate pinned product and queue implementation tasks under one approval that
names both targets. Product owns posting/CLI/persistence/rendering/binding and
compatibility. Queue owns both prose renderers, final native framing budget,
version selection and exact request binding. Queue depends on accepted product
contract; neither silently edits the ongoing frozen installer or deployed bundle.
A separately reviewed composed artifact and genuine deployment approval are
required before installing either side. The deployment request links this design
and lists deviations; migration/rollback and old/new reader support are verified
offline first. Previous management APIs remain available with their old bindings.

Offline tests reproduce predecessor `??` corruption, then cover status/reclaim/
dependency variants and all material UNKNOWN/hold/review restrictions; readable
golden output without raw JSON; once-only choices and exact label; A/B and reaction
semantics; old-card/repeat/details/binding bytes unchanged; misuse and unknown
versions; atomic metadata and rollback-writer compatibility; tampered metadata,
body/link/descriptor/parts/principal; incomplete delivery/history; stale facts;
hostile prose/Unicode/control/secret/Markdown; and complete-card budget refusal.
No live transport or native task mutation is necessary for these tests.
