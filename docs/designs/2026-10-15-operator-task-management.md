# btq operator task management, design d2

Codex author, 2026-10-15; answers Claude Opus round-one review. Design only, pending round-two review and human approval. Maximum three review rounds. Scope: one local queue command for reclaim, status correction and dependency repair; no new service, retired-harness edits or run/checkpoint migration.

## Implementation home and existing work

Implement only in an isolated `beads-task-queue` worktree, using its `bin/btq` backend. V2 already calls btq's Queue library through `src/heterodyne/wsd/beads.py`; future Hermes controllers can use the same command/library. A future wsctl bridge is optional, not part of this PoC.

Reuse the existing integration branch `btq/ywnix-integrate`, worktree `beads-task-queue-ywnix-integrate`, at `2017239`. It merges ywnix onto master `79becda` and resolves gl4c6's split_worker placeholder. Its yzm5c evidence/git-guard/quoting placeholders remain the reviewed local implementation until that separate work lands; do not make its unapproved design a new prerequisite.

Ywnix already supplies Handoff's durable request, exact facts digest, authenticated ask verification, holder fencing, reservations, single-write/read-back and resume behavior. Existing parked-bead passthrough handles ordinary owner-directed dependency changes. Existing approve-bead is the only pin-repair path. Reuse these functions rather than build another workflow.

Saved `review-clfmf-c2.md` approved `61e0852` and reported 400 tests. It did not review later commits `37996db`, `7968330` or integration merge `2017239`; require a delta review of `61e0852..2017239` before landing.

Legacy recover/update/dep contracts are useful references only. They cover retained-owner recovery, idempotent addendums and ordinary dependencies, not reclaim or status repair. The installed workstream facade runs harness-improvements; neither that retired checkout nor hermes-workstream-harness is an implementation target. Its bound-issue rerouting refusal is a separate real integration limitation, not a permission-classifier warning. This design does not resolve that facade limitation by editing checkpoints or creating replacement runs.

## Bootstrap and human gates

First use the existing approved OPS procedure “Recovering a bead to a controller lane” for btq-ywnix: post a concrete Liam card, reconcile effects and preserve pins byte-for-byte while changing assignee only. This uses existing operator recovery, not this unimplemented command. Record the user's confirmation that all prior workers stopped as cessation evidence, preserving actual session and worktree facts. The ywnix holder lacks harness.json; do not manufacture one or impersonate it.

Then obtain the delta review, close through `--lane btq-ywnix close` with evidence, and obtain Liam's explicit btq master go before merging the integration branch. Ywnix's existing design approval excludes master merge and real handoffs. Placeholders may land as reviewed; their later replacement is separate work.

Three distinct gates apply: approval of this management design before implementing its extension; Liam's explicit btq master go before landing queue changes; an approved operator card for every actual reclaim/override request. Model review or cessation evidence substitutes for none of them.

## One command, two modes

```text
btq <controller identity> --lane ID manage ID --plan --change FILE --evidence-file FILE [--post]
btq <controller identity> --lane ID manage ID --apply --ask ASK --expect DIGEST
```

Plan mode prints structured JSON containing exact facts, intended delta, digest and safely rendered card. `--post` stores/posts using Handoff's existing request/ask record. Apply rechecks authority and facts, executes or resumes that same request, and returns structured JSON with completion or a concrete refusal/inspection reason. No separate service, status tracker or alternative approval-file format.

The change JSON contains one operation, exact old/new state or edge delta, and reason. Controllers retain their own receiving lane and actor. Scope the command to one task; normal owner operations continue using existing commands without a new approval ceremony.

Allowed operations:

- **Reclaim:** move another stopped holder's claim into this controller's own lane using Handoff. `ended` uses existing checks. If liveness is `unknown`, show its exact reason and cessation evidence on the card; the approved choice must explicitly cover that uncertainty. Never accept `alive`, elapsed age or a stop request alone. Recheck immediately before mutation; a new alive verdict refuses regardless of the card.
- **Status:** only blocked/unassigned → open/unassigned, or closed `kind:task` → open/unassigned. Reopen using native bd reopen/update, never create with an existing ID. Refuse kind:approval status changes. Never close through an override; close still requires btq close and its evidence/review gates. No direct in_progress status assignment outside native claim/reclaim.
- **Dependencies:** explicit typed add/remove/replace with endpoint IDs. Operator authority can correct obsolete/wrong protected edges; ordinary controllers retain existing protections. Adding relies on native cycle checking; removing an edge needs no eligibility from the broken task. Exact current endpoint state is part of the digest.

Refuse changes to kind:* or btq:* labels, design_approval or adr_revision. Only existing approve-bead may pin a genuinely approved replacement.

## Authority, invariants and recovery

Authority is the btq-posted admind card answered by a current policy approver, verified using Handoff's ask identity, choice and digest checks. No invented local attestation, issuer string or self-authored approval JSON grants authority. Preserve authenticated answer provenance in the audit.

Dependency repair must retain or validate a replacement native blocking edge for the task's valid metadata-pinned design approval. Metadata pin validity alone is insufficient: protocol requires the matching native approval blocker too. Before unpausing or claiming, verify both. If a wrong pin requires correction, use approve-bead first; a partial repair remains paused/blocked. Removing a mistaken extra approval edge never removes the required approval requirement. Genuine design/action permissions and ordinary close gates remain in force.

Use existing locks, durable records and fenced-holder behavior. Require affected pickup paused during multi-step repairs. Reread exact status, assignee, labels, metadata, dependency endpoints, worktree/effects and relevant process facts before writing; stale facts refuse without mutation. Add a validated replacement before removing an old blocker. Persist step outcomes and verify native read-back. Hold ambiguous writes for inspection rather than replaying them.

All mutations use native Beads under the actual controller actor, preserving Beads/Dolt history. Append a request-marked reason/authority/before-after comment and redacted audit record. Audit or notice retries cannot repeat completed mutation. Tests cover unknown-explicitly-covered versus alive liveness, stale/forged authority, reopen, required approval edge plus pin, native cycles, interrupted replacements and ambiguous writes. Preserve existing queue test prohibitions.
