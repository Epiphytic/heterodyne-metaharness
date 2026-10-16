# Plan6C actual R2 dispositions — proposed d3

Author Codex. All 27 actual fenced JSON findings are reproduced verbatim in original order. Actual verdict REVISE; two of three rounds consumed. “Addressed” records an author design proposal, not reviewer approval, tested behavior or implementation authority. Actual R1 and its 33-entry dispositions remain unchanged.

Raw report SHA-256: `0a6e76a79749616eab5237497599e4acbc8e6da01dda55904b411fb3aa7c419e`. Reviewed d2 commit: `020c72a96ab527e57be28a0b2a619444308ac018`.

## 1. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 299,
  "summary": "The canonical admind ask card can't be 'protected' or 'excluded from autobump BEFORE protected admission': post_ask/commit_approval write the ask row and enqueue its `ask:<id>:<i>` card rows under legacy policy in one transaction, and the scope association is registered later in a separate request because AskPost has no scope fields.",
  "failure_scenario": "A controller posts an approval through AskPost. The card is enqueued at once with legacy retry, ID[0] root and top-level fallback, and stays eligible for repeating autobump. Only afterwards can the producer register the association. So the card d2 calls 'the control copy' never gets d2's no-retry-after-ambiguity, single-ID, parent-held or one-reminder rules. If registration fails, the ask silently stays legacy."
}
```

**Disposition: addressed in proposed d3**, §2.1, §3, §9. Require a proposed atomic protected-post variant at the actual post_ask/commit_approval/refresh entry points. Canonical row, exact association, logical identity, protected policy, exclusion and frozen rows commit together before enqueue becomes runnable; source backup seal precedes acknowledgment/effect. No retroactive upgrade or invented existing capability. Legacy asks stay explicitly legacy.

## 2. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 143,
  "summary": "A per-ordinal native key `d6c:<48hex>` can't 'attach to the existing outbox row/key'. In admind the row key is the UNIQUE row identity, the send_final idempotency key, and the lookup key used by ask_for_message, card_delivered, bump_target and first_card_message, which all match `ask:<id>:<i>`.",
  "failure_scenario": "If protected canonical card rows take `d6c:` keys, ask_for_message no longer recognises a reaction or reply to the card, so existing admind answering breaks (contradicting §2, 'unchanged'). If they keep `ask:` keys, ordinals 2 and 3 can't have distinct native keys without new rows that break card_delivered's `ask:<id>:<i>` completeness check. The implementer has to pick one, and either way a stated invariant breaks."
}
```

**Disposition: addressed in proposed d3**, §3, §5. Preserve actual ask:<id>:<i> keys, four-character ask alphabet and askr:<id>:<64hex>:<i> repeat grammar. Separate attempt identity from stored native key. Explicit protected current-copy lookup and canonical answering compatibility tests prevent an ID[0]/legacy completeness bypass.

## 3. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 272,
  "summary": "Route partitioning removes ADR §6.2's independent escalation for exactly the cards it covers. For an undelivered canonical approval or question, the 600s control alert is admind-owned and goes to the same admind group and outbox, and the 660s local alert file is relayed by that same admind outbox to that same group.",
  "failure_scenario": "admind's group route is failing, for example on a send to wn-agent or a stuck protected row. The card, the 600s alert and the relayed 660s file all queue behind the same failing single-group outbox_pass. No operator-visible path is independent of the failed route, which ADR §6.2 requires ('doesn't depend on wsd or the gateway's Marmot connection'). This departure isn't in the §8 decision list."
}
```

**Disposition: addressed in proposed d3**, §6, §9. Accept the architecture limitation explicitly. ADR6.2 governs WSD messages and its admind fallback; moving canonical control ownership to admind removes an independent remote route for those cards. Local660s durability does not prove remote independence. This departure is on the genuine human design request, not silently claimed fulfilled.

## 4. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 163,
  "summary": "R1 #2 isn't actually resolved. A wn-agent connect refusal is now proven_unissued, but ordinals 1–3 at 0/1s/5s are used up in about 6 seconds, exhausted-held can't be reset, and d1's operator-authorised marked re-copy for stuck parts was deleted with nothing replacing it.",
  "failure_scenario": "wn-agent restarts and is unreachable for 10s. Every part attempted in that window gets three prewrite refusals and becomes exhausted-held. Those cards never deliver: no source revision can reset them, and no operator recovery copy exists. This is the same 'brief restart → indefinite HOLD' failure R1 #2 reported."
}
```

**Disposition: addressed in proposed d3**, §4, §8, §9. Keep three total automatic attempts but change durable due times to0/5/30s, recovering the cited10s restart. Longer prewrite-only failure has a fixed authenticated exact one-send allowance preserving native key/history; uncertain sends cannot use it. Timing and writer deployment are proposed explicit human choices, not installed actions or a second automatic budget.

## 5. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 99,
  "summary": "Association registration relies on an 'existing private peer/enrollment boundary' that doesn't exist. ctl.Peer holds only a pid ('audit only; R16'), ask.sock accepts any same-UID process, and AskPost `poster` is self-declared ('a local process; unverified').",
  "failure_scenario": "Any same-UID agent session that can post asks can also present the configured producer name, release and run/boot values (all readable as that UID) and register an association binding an ask to another workstream or queue. WSD then mirrors it into an unrelated registered group. 'No text-forged scope' can't be enforced, and the claimed boundary is an invented existing capability."
}
```

**Disposition: addressed in proposed d3**, §2.2, §9. Remove nonexistent enrollment/authentication claims. ctl.Peer PID is audit-only; same-UID producers/associations are a trusted operational convention with no malicious-peer isolation. Fixed bindings/mode checks prevent accidental mismatch only. Native human authentication remains independent. This residual trust is explicitly proposed for human acceptance.

## 6. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 201,
  "summary": "admind Store loss can't be detected. Store() opens with O_CREAT and runs CREATE TABLE IF NOT EXISTS, so a deleted or replaced database comes back silently empty. d2 requires 'exact initialized store, identity/generation/history' for healthy-absent and loss recovery but names no identity anchor outside the database.",
  "failure_scenario": "The admind state DB is deleted or restored from an old copy. On restart admind sees a valid fresh or older store, so healthy-absent and journal-loss handling (preserve damaged bytes, mark reminder budgets unknown, marked reissue) never trigger. Admitted protected rows are simply gone or treated as first-time state."
}
```

**Disposition: addressed in proposed d3**, §3, §4, §7.2. Require installed UUID receipt and separate catalog sequence/hash seal, validated before any creating Store constructor or absence proof. Deleted, wrong-UUID or behind-seal DB holds; ahead-of-seal recovery finishes from intact data under fencing. Total-anchor rollback/loss cannot be detected/proven safe and remains HOLD. No cryptographic same-host isolation claim.

## 7. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 375,
  "summary": "The restored 'automatic ADR3.3 reissue' (R1 #22) can't happen for approval or question cards under the route partition. Canonical cards and their asks live only in admind's Store, admind can't originate asks, and WSD loss only affects informational mirrors.",
  "failure_scenario": "If admind's Store is lost, the asks are gone and §7 says HOLD with no reconstruction. If WSD's store is lost, canonical cards are untouched and only mirrors are reissued. In neither case is an open approval card re-posted with 're-issued after recovery', so the disposition is nominal."
}
```

**Disposition: addressed in proposed d3**, §7.2–§7.3. Back up canonical AskRows, source identities, logical lineage, decisions and frozen inputs before acknowledgments/effects. Explicit supported restoration validates owner/source first. Then automatic marked protected askr copies restore the SAME canonical ask/decision, not only mirrors. Missing or unverifiable source is unavailable; nothing reconstructs unknown consent. Same-host/UID and total-loss limitations disclosed.

## 8. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 359,
  "summary": "Alert-file settlement requires admind's relay delivery evidence, which WSD has no read API for and may not open admind's database to get. The 256-unsettled cap then stops new protected admission. This conflicts with the pinned Plan8 shared-writer contract ('Never gate WSD admission on observer capacity', a shared 256 outstanding cap, coalescing at capacity).",
  "failure_scenario": "WSD writes task-card alert files. It can't prove relay receipt, so none settle. After 256 episodes, all new protected admission stops, so an alert backlog blocks card delivery. Meanwhile Plan8 p8_ files share one writer whose capacity policy is incompatible. 'ONE shared writer' with two contradictory capacity rules can't be implemented."
}
```

**Disposition: addressed in proposed d3**, §7.1, §9. Neutral shared writer has one shared256-file cap including coalesced overflow. Capacity never gates delivery/admission. Producer-owned local condition settlement plus bounded atomic settled metadata permits cleanup without WSD reading admind receipts; relay is separate and not promised. Final Plan8 compatibility remains required, not assumed.

## 9. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 273,
  "summary": "WSD task delivery alerts use NoticePost to admind, a third cross-process path that contradicts §2 line 74 ('the only cross-process path is WSD→admind bounded read or existing canonical ask submission'). NoticePost also returns 'posted' on enqueue only, and there is no NoticeGet, so WSD can't evaluate the 660s 'alert lacks full receipt' condition.",
  "failure_scenario": "A task card fails, and WSD posts a NoticePost at 600s and gets 'posted'. At 660s it can't tell whether the notice was delivered, so it either always writes the local alert or never does. Existing NOTICE_PER_HOUR=12 and NOTICE_UNDELIVERED=10 also refuse bursts during a wn-agent outage, and there's no mapping to the closed result enum."
}
```

**Disposition: addressed in proposed d3**, §2, §6. Remove WSD NoticePost delivery escalation entirely. No receipt inference from posted enqueue or NoticeGet invention. Per-card600/660 applies only approval/question control cards owned locally by admind; WSD task outages use aggregate health/digest. NoticePost limits are cited as the reason it is unsuitable.

## 10. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 122,
  "summary": "Card identity contradicts itself for approvals. Reminder and alert budgets are 'stable across source revisions', but 'true new native asks are new cards', and admind's R29 refresh and ADR re-issue on a context_digest change create a NEW ask_id for the same bead decision.",
  "failure_scenario": "An approval is refreshed after enrichment (R29) three times. Each refresh is a new ask_id, so a new card with a new 4h reminder and new 600s/660s episode. That gives three reminders and alerts for one decision, which is R1 #19's failure scenario, so that disposition isn't addressed."
}
```

**Disposition: addressed in proposed d3**, §2.1, §3, §6. Logical decision identity and original open time are assigned by the genuine protected producer and preserved through exact predecessor-linked ask refresh/context changes. New ask_id alone cannot reset reminder/alert budget. Legacy sources without genuine lineage are not guessed together.

## 11. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 266,
  "summary": "The delivery anchor is set at durable first-send admission, which capacity exhaustion or source unavailability prevents. Those cards get only a 'local health failure' and never reach the 600s/660s alert path, contradicting R1 #3's disposition that capacity/trust 'cannot postpone that origin'.",
  "failure_scenario": "The store reaches the 10000-source cap, or a source snapshot stays Unavailable. New approval or task cards are never admitted, so no anchor is recorded. No control alert or local alert file is ever produced for those cards, only an aggregate service-journal diagnostic."
}
```

**Disposition: addressed in proposed d3**, §2.1, §6. Record minimal source intent/anchor during accepted protected post before copy capacity, trust or snapshot. Full catalog rejection occurs before creating/enqueuing an ask; caller retains durable request. A preallocated catalog health slot anchors fixed aggregate capacity failure, honestly not per-card clocks for unaccepted sources. Disk failure reports no durable anchor. Explicit bounded availability qualification replaces the impossible unlimited backlog claim.

## 12. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 305,
  "summary": "The digest's destination is unspecified. 'Control binding' could mean admind's group or WSD's 6B read control group. The scoped digest_snapshot also can't include legacy or unassociated asks, which contradicts 'list ALL current open approvals/questions' and 'never silently drops rows'.",
  "failure_scenario": "Implementers diverge: one sends via WSD's control route, another via admind NoticePost, which can't carry it (4 parts, 6000 chars, 12 per hour). Open asks without a registered association have no agent/workstream/queue scope, so they are silently missing from the 'all open' digest."
}
```

**Disposition: addressed in proposed d3**, §2.2, §6. admind alone produces/sends global digest to its own configured operators group. Canonical ALL asks include legacy/unassociated rows locally. WSD contributes only immutable bounded safe projections, not global ask bodies; no digest_snapshot on the scoped ask endpoint or NoticePost size workaround.

## 13. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 308,
  "summary": "The digest content omits an ADR requirement: ADR a27 rev14 (around line 404) says 'The daily digest lists accounts that hit a limit', but d2's digest producer covers only open approvals, closed-yesterday, needs-human and idle facts.",
  "failure_scenario": "A coder account is exhausted for a day. The 08:00 UTC digest leaves it out, so the operator doesn't learn about quota exhaustion through the channel the ADR requires, and no departure is declared."
}
```

**Disposition: addressed in proposed d3**, §6, §9. Add explicit quota-hit account section (ADR rev14) with bounded safe producer snapshot/freshness/unknown contract. No installed Plan5 or quota projection API is invented; real producer/renderer compatibility required before activation.

## 14. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 272,
  "summary": "Extending the 600s/660s delivery-alert machinery to task cards goes beyond ADR §6.2, which applies only to approval and question cards. This expansion isn't in the §8 decision list.",
  "failure_scenario": "During a group outage, every undelivered task card produces a NoticePost and a local alert file. That quickly hits the notice rate limits and the 256-file cap (which then halts admission), adding operator noise the human never approved."
}
```

**Disposition: addressed in proposed d3**, §6. Remove per-task600/660 alert expansion. Task failures remain aggregate health/digest; ADR task one-reminder policy remains explicit and separate. No per-task NoticePost burst or unexplained added operator noise.

## 15. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 135,
  "summary": "Deriving a distinct native idempotency key per ordinal departs from frozen 6B d4 ('every permitted retry of that exact part uses the same stored key'), even though both WSD producers are said to share key helpers. It also throws away the native same-key dedup that S4 confirmed, which is a free guard if proven_unissued is ever wrong.",
  "failure_scenario": "A healthy-absent or prewrite proof is wrong, for example a wrapper phase bug. Ordinal 2 is sent under a new key, so wn-agent posts a duplicate card. With 6B's same-key rule, native dedup would have returned the original ID. The 6B and 6C helpers also can't be the same function."
}
```

**Disposition: addressed in proposed d3**, §3–§4. One immutable native key per part through every proven-unissued attempt; ordinal is local audit identity only. Preserve actual ask keys and WSD stable d6c key. Native same-key dedup remains extra defense, never sufficient authority to retry a possibly issued send.

## 16. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 247,
  "summary": "The frozen-byte binding conflicts with admind's outbox_pass, which re-runs `redact(row.text)` at send time. d2 doesn't exempt protected rows or require a post-redaction hash check.",
  "failure_scenario": "The redactor configuration changes between admission and dispatch. admind sends bytes different from the part hash bound in the attempt, and the receipt then 'confirms' text the copy key never described. Or the implementation notices and holds every in-flight part. Neither behaviour is specified."
}
```

**Disposition: addressed in proposed d3**, §5. Protected dispatch verifies stored final text/hash and current safety redaction leaves it identical. Never silently send transformed bytes. A changing redactor holds unsent work for safe re-render/new copy while preserving prior evidence; unmanaged legacy rows retain their actual redaction behavior.

## 17. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 90,
  "summary": "The 64 KiB snapshot frame cap is smaller than legitimate existing ask content: AskPost allows 16,000-character bodies (up to about 64,000 UTF-8 bytes) and ask.sock MAX_REQUEST is 256 KiB, before the snapshot envelope and approval details are added.",
  "failure_scenario": "An approval with a long non-ASCII body produces a display_snapshot reply over 64 KiB. It's rejected as Unavailable, so the mirror is never built. The renderer's summary/pinned-link overflow never runs, because the overflow happens at the transport frame, not in rendering."
}
```

**Disposition: addressed in proposed d3**, §2.2, §9. Bound proposed request8KiB/reply256KiB with one total5s deadline and admission measurement against worst-case UTF-8/envelope. Compatible producer summary/reference must precede transport if oversized; otherwise typed unavailable. Add legitimate16000 non-ASCII body regression rather than assuming rendering can fix a rejected frame.

## 18. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 128,
  "summary": "The copy key includes the generation, which the owner allocates, yet 'an identical request returns the existing copy'. No generation-free dedup key is defined, so it's ambiguous when a new generation is allocated.",
  "failure_scenario": "One implementation allocates a new generation per reconcile pass and posts duplicate copies. Another never increments it and collides as an integrity hold on a recovery or join reissue. The idempotency rule can't be implemented consistently."
}
```

**Disposition: addressed in proposed d3**, §3. Define generation-free copy request key with exact reason occurrence. Identical request returns existing generation, different bytes conflict; only new source/reason occurrence allocates generation atomically. Reconcile repetition cannot generate fresh copies, while sealed recovery epoch/authenticated inbound request scopes actual reissues.

## 19. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 391,
  "summary": "The manual join reissue ('an authenticated Marmot request can authorize one marked display reissue') has no defined handler. Frozen 6B makes the read consumer answer mutation commands with a fixed no-action notice, and admind's only reissue (`!asks repeat`) creates extra answerable legacy-policy `askr:` cards.",
  "failure_scenario": "A new operator joins a workstream group and asks for a reissue. No WSD command exists, 6B refuses it, and `!asks repeat` re-posts a decision-bearing card under legacy retry/ID[0] policy, contradicting 'never an additional decision-bearing control card'."
}
```

**Disposition: addressed in proposed d3**, §2.1, §8, §9. Define proposed typed delivery_join_reissue writer authenticated through real Marmot, exact source/destination/registration/digest/phase/request binding and durable idempotency. It is distinct from frozen6B response-only consumer. Unsupported action stays no-action. Protected canonical repeat uses same decision identity; no invite/new consent or legacy retry shortcut.

## 20. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 211,
  "summary": "The claimed reuse of admind's 'existing durable due state' and 'fair progress' is wrong. The existing backoff is the in-memory `_backoff` dict, and outbox_pass `return`s whenever next_pending's row has a pending timer, which blocks every other row.",
  "failure_scenario": "A protected row in its 1s or 5s backoff (or a held or uncertain row left in 'pending') blocks all admind sends, including alerts and other cards, until its timer fires. A restart loses the in-memory timers. Fairness and durable 1s/5s wakes need new mechanisms that d2 presents as existing."
}
```

**Disposition: addressed in proposed d3**, §4. Correct actual-source claim: existing _backoff is in-memory and delayed head return blocks later rows. Require new protected indexed durable due/phase selection and bounded fair scheduler, excluding held/uncertain rows while retaining within-copy order and legacy policy. A live native call has finite30s serialization, not infinite head-of-line hold.

## 21. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 271,
  "summary": "'Delivery episode' is never defined: the delivery anchor never resets on retry, renderer change or reissue, yet the alert budget is 'per card ID/delivery episode'.",
  "failure_scenario": "After loss-recovery reissue or a later delivery failure, one implementation opens a new episode and alerts again; another never does, so a card delivered and then lost never escalates. The behaviour differs and can't be tested against the spec."
}
```

**Disposition: addressed in proposed d3**, §6. Close episode identity to logical initial intent or independently sealed detected-loss recovery epoch. Source update/refresh/renderer/manual join do not re-arm. Each epoch permits one600s alert/660s local signal; delivered ordinary updates do not create new episodes. Explicit human policy, not undefined episode inference.

## 22. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 87,
  "summary": "The snapshot scope binds agent/workstream/opaque queue only and omits frozen 6B's WorkstreamBinding repository_identity and route registration_epoch, despite claiming consistency with frozen 6B registrations.",
  "failure_scenario": "Two registered bindings share an agent and queue label but have different repositories, or a route re-registers at a new epoch. The 6C snapshot scope can't tell them apart, so a mirror can bind to a stale or wrong registration that 6B would have rejected."
}
```

**Disposition: addressed in proposed d3**, §2.2, §3. Bind exact frozen6B agent/workstream/queue/repository_identity/registration_epoch plus destination in source snapshot/copy. Registration changes cannot reuse stale scope. Require strict negative compatibility fixtures.

## 23. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 177,
  "summary": "d2 says the WSD transport owner takes 'the same existing trust/send lock for its route', but WSD at the pinned source has no Marmot transport, ControlClient use, registered routes or send/trust lock. These exist only in unapproved 6B.",
  "failure_scenario": "Reviewers and the human approver read 'existing' as installed serialization. An implementation may skip building the trust-transition drain/fence and assume a guard that doesn't exist, losing the stale-epoch send protection."
}
```

**Disposition: addressed in proposed d3**, §1, §4, §9. Clearly mark WSD route registration/transport/trust/send guard as proposed compatible6B prerequisites absent from product076. Implement/fence/drain before activation; no existing lock assumed or another lineage silently edited.

## 24. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 90,
  "summary": "'One request per part' means one cross-process snapshot RPC per part (up to 20 per copy, each with a 5s deadline) although each copy is bound to one immutable revision/digest. A per-copy snapshot plus a cheap per-part token check would do the same job.",
  "failure_scenario": "A 20-part mirror needs 20 RPCs, and with at most 4 workers and 20 starts per sweep that is about 100s of worst-case wall time per copy. Each extra RPC is another chance for Changed or Unavailable mid-copy, which turns copies partial-held."
}
```

**Disposition: addressed in proposed d3**, §2.2, §6. Fetch one full immutable snapshot per copy/preparation, check token/revision and route/trust locally per part. Bound age of initial snapshot30s and disclose display race; native decision still live-validates. Four workers/one per source prevent per-part RPC multiplication. No cross-process read under local send guard.

## 25. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 346,
  "summary": "Putting the shared writer in `admind/alerts.py` for use by WSD (6C task alerts and Plan8) creates a new wsd→admind package dependency. Today no wsd module imports admind.",
  "failure_scenario": "WSD now imports the admind package, coupling its release and test surface to admind's. A neutral shared module, such as one next to fsutil holding the alert-file contract, would serve both writers and admind's reader without that layering inversion."
}
```

**Disposition: addressed in proposed d3**, §7.1. Move proposed writer to neutral heterodyne/local_alerts.py beside fsutil; admind reader and WSD/Plan8 consume the shared file contract without WSD importing admind. File/lock/cap/coalescing/settlement contract is explicit and proposed, not an existing writer.

## 26. Actual finding

```json
{
  "file": "docs/reviews/plan6c-delivery-inputs.json",
  "line": 6,
  "summary": "The source manifest doesn't pin source files d2's claims depend on: admind/notices.py (the NoticePost profile and limits), admind/ctl.py (the claimed peer/enrollment boundary) and admind/redact.py (send-time redaction).",
  "failure_scenario": "These modules can change, for example NoticePost limits or the Peer fields, without any pinned-input mismatch, so the compatibility gate can't detect that d2's NoticePost alert path or peer-authentication assumption has drifted."
}
```

**Disposition: addressed in proposed d3**, Input manifest. Add exact product076 blobs/SHA-256 for notices.py, ctl.py, redact.py, and fsutil.py; retain Journal source for backup_to inspection. NoticePost profile, audit-only peer, redaction and actual durability gaps are source-backed, not installed future capabilities.

## 27. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 406,
  "summary": "d2 dropped d1's required mutation tests (breaking complete-copy checking, intent-before-effect, no-retry-after-issued, revision binding and one-reminder uniqueness must make named assertions fail) and the fsync-backed SQLite/backup-restore acceptance gate, with no replacement.",
  "failure_scenario": "An implementation passes the d2 test list while a mutation that removes the single-reminder uniqueness check or the dispatch-mark-before-write ordering goes undetected. Durability claims for the new WSD store also have no synchronous-setting gate."
}
```

**Disposition: addressed in proposed d3**, §7.2, §9. Restore named mutation gates for intent/seal before effect, complete-copy validation, no retry after issued, source revision binding and one-reminder uniqueness. Require verified SQLite FULL/WAL plus fsync-backed atomic backup/restore and crash-cut fixtures for canonical source and delivery data. No runtime tests were run by this design revision.
