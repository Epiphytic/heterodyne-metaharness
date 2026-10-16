# Management recovery for nonapproval design and planning tasks

Design task `btq-zcd1l`; proposed amendment, design only. Codex author, 2026-10-16. Root conducts actual Claude cross-model review, at most three rounds, publishes the exact design, and obtains human approval through Marmot/admind. No implementation, deployment or recovery is authorized by this draft.

## Existing boundary and problem

The [original operator management d2](../reviews/approved-inputs/2026-10-15-operator-task-management.md) grants authenticated stopped-holder reclaim, status correction and typed dependency repair for implementation tasks. A blocked/unassigned planning task cannot use the deployed command: `_route` requires `kind:task` and `_approval` unconditionally requires valid design pins and a native blocker. Requiring implementation approval before the task can resume producing its design is circular.

The archive above preserves the exact original published bytes, including its historical pending-status prose. Publication/provenance is not new approval evidence. Source commit `391516cd919234e69626970cb2e61b94299ed60e`, source path `docs/designs/2026-10-15-operator-task-management.md`, SHA256 `3249c0a3b6207da6bc6172e5291895779a920d3afd56de6e38d0365df0ee2a39`.

Observed installed queue source is `6fe57598468ff53bf74dfd9e1cf181ad3177c3f1`. Bundle hashes: `btq` `ec2bc67b227326c80bf5d9a32eb661b220ea9af6942ef31bff04d8c06941bd2e`, `btq-manage.py` `670ea807fd546f939f3441823dee2e1980e03f7026be580a62d7f91a4e6582b6`. Its pinned wrapper loads an immutable three-file bundle; updating a mutable checkout alone does not deploy a fix.

## Exact classification and approval protection

Validate native label/metadata shapes, exactly one matching workstream route, and exactly one `kind:` label. Derive management class from this closed table; never accept a caller-supplied class:

| Native kind | Class and protection |
| --- | --- |
| `kind:task` | Implementation; existing valid pins and matching native approval blocker required unchanged |
| `kind:brainstorm` | Planning; rules below |
| `kind:review` | Planning; rules below |
| `kind:research` | Planning; rules below |
| `kind:approval` | Always forbidden for every operation |
| Missing, mixed, malformed or any other kind | Forbidden |

The native queue already recognizes brainstorm/task/review/research. Legacy `kind:implementation` remains excluded pending separately reviewed classification. No title inference, label conversion, old-task cutoff or approved recovery card overrides this table. Recompute classification before consuming authority, before each native write and at final readback. A label change invalidates the request.

For `kind:task`, preserve existing `_approval` checks for every operation: require nonempty metadata `design_approval` and `adr_revision`, native `approval_valid` at that exact revision, and the corresponding `blocks` edge in the final graph. This amendment does not touch normal implementation eligibility, claim, close or approval-validity semantics.

For planning, both pin fields may be absent. Absence asserts no implementation authority. Do not create substitute pins, infer approval from a file or use approve-bead merely to permit recovery. If either pin key is present, both values must be nonempty and pass exactly the same approval/blocker checks as implementation. Partial, empty, malformed or invalid existing pins refuse; genuine pin repair uses the existing approval protocol.

For unpinned planning, retain every existing prerequisite whose endpoint is `kind:approval` and native edge type is `blocks`. Removing or replacing any such edge refuses; other typed dependency repairs remain available. Adding an approval prerequisite never records its approval. Incomplete endpoint facts or ambiguous endpoint labels refuse. This conservative boundary prevents a recovery card discarding an existing genuine design/action permission. All labels, parent, metadata and source/worktree history remain unchanged.

## API, mutations and authority

Reuse the existing API, current controller actor and exact receiving lane:

```text
btq <current identity> --lane ID manage ID --plan --change FILE --evidence-file FILE [--post]
btq <current identity> --lane ID manage ID --apply --ask ASK --expect DIGEST
```

Read-only plan grants no authority. Each actual mutation still requires the posted card answered by a current policy approver, using existing ask identity, title, choice, digest and authenticated provenance checks. No local approval JSON, issuer string, design file, cessation statement or model review substitutes for that decision. Recovery grants operational permission only, never approval of the design being resumed.

- Status permits blocked/unassigned to open/unassigned, or closed to open/unassigned. Closed is a permitted source, never destination. No close override, arbitrary assignee change or direct in_progress assignment is added.
- Reclaim changes only another stopped holder's assignee into the controller's own lane, retaining status. Preserve cessation evidence, holder locks, fences, cap/reservation checks and fresh liveness. Alive always refuses; unknown needs explicit authenticated coverage and cannot bypass missing holder locks. Age or a stop request proves nothing.
- Dependencies retain existing parked/blocked/closed ownership preconditions and typed add/remove/replace. Validate endpoints, rely on native cycle checking, and add a replacement before removing the old edge. Preserve the approval protection above. No other field changes are admitted.

Keep parent pickup paused during planning/posting/applying. Quiesce affected workers before dependency repair. Do not automatically unpause, unpark, claim or resume work after recovery; deliberate normal commands must pass their own native gates and lane limits.

The operator now prefers blocking approvals only for new/changed designs and deployments. D2 nevertheless authenticates every management recovery via Marmot. This amendment preserves that approved contract and adds no routine approval gate. A future separately reviewed/human-approved bounded controller-delegation policy could reduce cards; none is implicitly granted here. No generic bypass is introduced.

## Snapshot, card, phases and readback

Reuse Management/Handoff, native adapter, evidence snapshot and durable phases. Add deterministic `management_class`, `native_kind` and `approval_protection` facts. Protection binds either exact validated pins/blocker, or explicit unpinned-planning status plus preserved approval prerequisite IDs/types. Include them in the facts digest and saved request; recompute from native state at all authorization/effect boundaries.

Cards show kind/class, exact operation/delta, protection and limitations, termination/cessation/effects, worktree facts, endpoint digest, nonce and facts digest. Planning cards explicitly state no design, implementation, deployment or close authority. Retain quoting, secret checks and budgets; overflow refuses rather than truncating facts. Full underlying labels, metadata, task content, comments/worktree evidence and endpoint facts remain bound even when represented by digest in the card.

Before every write and at finish revalidate task classification, approval decision, mandatory edges, endpoint facts and authenticated authority. Whole-bead readback permits only the intended field delta; routing, kind, metadata, parent and history remain intact. Stale facts before writing need a fresh card; once a write is issued, uncertain results are held for reconciliation, never reissued or discarded.

Preserve single-write issued/verified history, add-before-remove, fences/reservations, authenticated answers and request-marked native audit. Audit retries cannot replay a successful mutation. Record class/kind/protection, exact delta, authority, before/after, effects hash and verified steps. Old saved requests lack new class/protection facts: refuse automatic reinterpretation, preserve for OPS inspection, settle safely before creating a fresh request. Never reuse an old answer under expanded scope.

## Prerequisites, escalation and review budgets

The missing-admind setting prerequisite was separately fixed: Liam choice A on `fn9r` at `2026-10-16T05:25:51Z`, deployment at `05:26:23Z`, doctor exit 0. Only the existing policy admind executable setting changed. It did not extend kind permissions. Check current runtime/transport health at deployment; historical success is not perpetual readiness.

The separate `sbjv` OPS request for design tasks `btq-e7knb` and `btq-fu366` is still pending in retained evidence as of this draft. This design neither executes nor supersedes it. Recheck actual authority and task state before future action; prose is not an answer.

Retain d2's concrete OPS native recovery path if the supported command refuses or transport is unavailable: scoped operator authority, current attribution, exact before/after and durable audit. Use Marmot/admind for questions where available; broken transport retains the existing explicit operator/terminal escalation route. No fabricated card, borrowed identity, direct native DB edits or missing-lock bypass. This document requires no ordinary-chat confirmation.

Reopening or reclaiming preserves each task's original revision, findings, actual completed review rounds and remaining budget. At the cap, hold for an explicit scoped operator decision; never reset counters or accept an author-only waiver. Design approval, implementation review and deployment are separate gates. Implementation belongs in an isolated queue worktree after genuine approval; reviewed deployment must replace the pinned bundle through the existing installer, with source/hash and health proof.

## Executable acceptance contract

These are required future offline assertions, not tests claimed by this docs change. Use named tests, disposable fake native/admind state and no actual model invocation. Do not run known live-database queue tests, broad discovery, live scans or sends.

| Cases | Required result |
| --- | --- |
| Each planning kind, no pins, blocked/unassigned or closed source | Plan works; authenticated reopen ends open/unassigned and retains history |
| Approval target at every phase; after.status closed for any kind | Refuse with zero new native writes |
| Missing/mixed/unknown/legacy implementation kind | Refuse without relabeling or fake pins |
| Implementation valid/missing/partial/invalid pins and missing blocker | Existing valid behavior retained; every invalid state refuses |
| Planning absent/partial/empty/invalid/valid pins | Absent works; invalid refuses; valid checks and preserves exact protection |
| Unpinned planning with approval prerequisite | Remove/replace blocker refuses; unrelated repair retains it |
| Task kind/content/pins, endpoint or authority drift | Invalidate before write; hold issued outcomes without replay |
| Reclaim ended/alive/unknown plus missing/busy locks | Existing stopped-holder behavior retained; alive never moves |
| Forged/stale/copied/wrong-choice/unauthorized ask | No mutation; local text never authenticates authority |
| Crash at each native-write/verification/replacement/audit phase | Resume reads back; completed native mutation issued once |
| Card overflow, hostile quoting, secret-shaped fields | Safe refusal, no partial decision card or fallback leak |
| Legacy saved request missing classification | Preserve and refuse reinterpretation; no ambiguous request deletion |
| Wrapper/bundle packaging | Current actor/lane/source integrity retained; installed files exactly reviewed |
| Review/approval provenance after recovery | No cap reset, completion, automatic resumption or invented authority |

Completion is a reviewed, published, human-approved design with honest evidence. No executable code or host behavior changes here.
