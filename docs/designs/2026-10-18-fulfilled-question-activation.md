# Fulfilled-question activation closure

Status: proposed design; no implementation or deployment authority. Task `btq-ck0hk`.
Author: Codex. Baseline: `a73e91d367bd626556371f33a46e25f003243c6e` (merged disabled core).
This is a new bounded activation amendment, not another review of the original design.
One future HIGH design review, incorporation once, publication and genuine human approval remain required.
The source manifest and retained historical review are in `docs/reviews/fulfilled-question-activation-*`.

## 1. Authority, scope and preserved contract

The approved predecessor is `e8b5db3fb3e506ff909540dfb298b2c873446448:docs/adr/2026-10-16-fulfilled-question-lifecycle.md`.
Its question/merge-only eligibility, complete-context declaration, controller terminal-receipt authority, unchanged original answers/status and immutable fulfillment receipts, native-approval exclusion, replay keys and rollback history remain binding.
The historical code review approved only an offline PoC with `ENABLED=False`; it explicitly required revision before deployment. Its eight findings motivate this amendment, not activation consent.
This design adds bounded diagnostics and verification and corrects six local contract defects. It does not add summarization, provenance backfill, external completion proof, automatic operation replay, automatic disposal, or approval reconciliation.
All existing legacy Ask DTOs, ten wire statuses, fulfillment request/receipt schemas and canonical digest domains remain unchanged. New diagnostic DTOs are opt-in.
Implementation must still land disabled. Enabling any producer or migrating a real database requires a separately authenticated Marmot deployment decision with published design, actual source/configuration pins, deviations and rollback scope.

## 2. Visible pending-notice recovery (historical finding 1)

An answer's durable `inbound.sender_key` is the only existing requester-owner source. NULL remains unknown; display labels, present operator membership, cached senders and same-UID access cannot reconstruct historical consent.
Unknown ownership remains quarantined; revoked ownership remains suppressed under the existing fresh-policy delivery gate. No diagnostic makes either deliverable.
Propose and identical re-propose return their existing result type but replace misleading queued/retained messages with one exact code: `notice-pending`, `notice-delivered`, `notice-quarantined`, `notice-suppressed`, `notice-expired`, or `notice-unavailable`.
Get retains its existing view grammar; its message reports the same pending-notice diagnostic when applicable. These codes describe observation, not a promise of future delivery.
A new local same-UID opt-in request is `{"type":"fulfillment.inspect","limit":N,"continuation":C}`: N integer 1..40, C null or a validated existing-alphabet intent ID. Unknown fields are refused.
Its closed reply is `{schema:"fulfillment-inspect-v1", entries:[Entry], continuation:string|null}` within the existing 128-KiB reply limit.
Entry is `{ask_id, intent_id, digest, created_at, state, reason, actions}`. The first five fields are existing public IDs/timestamp strings; state and reason use the finite table below. Actions is an ordered subset of `get`, `dispose`, `replace`, `controller-receipt`, `hold`.
The CLI is `admind ask fulfillment inspect --limit N [--continuation ID] --json`; default 40, no native writes, network fetches or notice retries.
Rows are keyset-paged by intent_id, selecting only current pending intents. No full answer bodies, declaration, raw owner keys or notice text are returned. A second page reflects a fresh snapshot, not a claimed immutable inventory.

| State | Fixed reason | Supported next action |
| --- | --- | --- |
| pending | delivery-pending | get, dispose, replace, hold |
| delivered | delivery-complete | get, dispose, hold |
| quarantined | unproven-owner | dispose, replace, controller-receipt, hold |
| suppressed | requester-revoked | dispose, replace, controller-receipt, hold |
| expired | intent-expired | dispose, replace, controller-receipt, hold |
| unavailable | delivery-unresolved | get, dispose, controller-receipt, hold |

Inspect reads the exact intent's bounded outbox group, ownership and existing guard state in one read snapshot. Known unknown provenance reports quarantined even before the outbox worker has classified it; fresh-policy revoked ownership reports suppressed even before delivery. Unreadable policy or ambiguous ownership metadata is unavailable. Reuse the existing pure ownership/policy checks without issuing a send or mutating its queue. Expiry takes precedence; all chunks sent with IDs and exact reassembly is delivered; any unknown/missing/inconsistent/failed or possibly-issued part is unavailable. Uniform quarantine/suppression yields that state; mixed states are unavailable. Otherwise the intact pending group is pending.
Before materializing notice text, enforce its retained 3,500-character total and valid metadata using bounded byte-length/row-count queries. At most 3,500 chunks are inspected for one intent: split of the approved nonempty <=3,500-character notice cannot produce more. A larger or malformed retained group is unavailable, not truncated or retried. Each page stops before its reply byte budget and returns the last emitted intent cursor; an individually invalid row yields bounded unavailable IDs, never blocks later pages.
The global cap remains 40 current pending intents, including expired/quarantined/suppressed ones, and one per ask. Inspect/get/dispose/controller apply remain available at the cap. There is no 24-hour automatic release.
Dispose and explicit replacement retain the existing expected-intent digest, tombstone and audit semantics. Disposal releases only that pending slot, not capacity or replay history. Replacement always creates a fresh intent/notice; unknown provenance remains unknown on the replacement.
Retired/expired/consumed intents can never authorize a later `!fulfill`, including replies to previously delivered chunks. Retirement does not erase a possibly delivered/unknown outbox row or resend it. Controller receipt remains an alternative only when its genuine terminal evidence and binding exist.
Complete context still must fit 3,500 characters and 40 LF lines and pass redaction unchanged. There is no excerpt/summarization fallback. The exact refusal is `notice-redaction-or-bounds`; explanation belongs in documentation, not the machine code (finding 6).

## 3. Reconcile and transaction/confirmation corrections (findings 2–6)

Reconcile keeps its existing manifest limit 256 KiB, 1..20 distinct asks, digest-bound continuation and fulfillment-reconcile-v1 result grammar. Validate the whole local input before any apply.
Each entry has its own bounded decode/validation/transport boundary. An invalid local receipt is incomplete and advances; a valid server refusal maps to its existing typed outcome and advances. Applied this run remains `applied`; observed existing identical receipt remains `already-applied`.
A malformed/legacy reply, invalid Get projection, `Invalid`, `DecodeError` or unavailable transport during a server exchange yields `unavailable` for that entry, stops, and returns all preceding results plus the cursor at that unresolved entry. Never discard already committed results in command-level generic-error handling.
Continuation starts with read-only Get for the same exact receipt/binding. A positive matching stored receipt proves already-applied; a conflict holds. Absence is not proof an external operation did not occur. Only the predecessor's identical metadata application/readback rules are permitted; never replay the completed external operation.
`Store.transaction` tracks the outer transaction's read-only mode. Nested writer entry under a read-only outer block raises fixed `read-only-transaction` before yielding or changing depth. Nested reads under either mode and nested writers under a writer retain existing atomicity. Clear depth/mode on BEGIN, COMMIT and exception failures. This is a trusted API restriction, not a sandbox against arbitrary raw SQLite access.
The daemon intercepts literal `!fulfill` only when ENABLED is true. Disabled behavior follows the exact pre-feature command/bump/ask routing, without a new authorization reply.
When enabled, fresh authorization and nonempty durable `inbound_sender(mid)` must both succeed; confirmation reads that durable principal and records it in the immutable receipt in the same mutation transaction. A missing cache entry cannot become an empty principal, and a cache value cannot override durable identity.
Persistent reply failure leaves durable inbound work recoverable; a fixed refusal reply is never fabricated as delivered.

## 4. Bounded verification projection (finding 7)

The core's current startup rebuild scans and hashes all retained history under one writer transaction. Replace that implementation; a schema/version flag alone is not verification.
Keep every original receipt byte, receipt ID, operation core, consumed answer reference and audit row. Receipt binding/canonicalization remains fulfillment-v1; the new schema version describes only derived validity.
New additive tables are `fulfillment_projection_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch TEXT NOT NULL, scheme TEXT NOT NULL, scan_cursor TEXT)` and `fulfillment_generations(ask_id TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>=0 AND generation<=9223372036854775806))`.
Meta contains a fresh 64-lowercase-hex epoch and exact scheme `fulfillment-projection-v2`. Add nullable `verified_epoch TEXT`, `verified_generation INTEGER` and `verification_state TEXT` to ask_fulfillments; state is null or `valid`, `conflict`, `too-large`.
Add `fulfillment_dirty(ask_id TEXT PRIMARY KEY, generation INTEGER NOT NULL)` and indexes on verification epoch/ask_id and dirty ask_id. Epoch is not derived from a binary version or a human's assertion that nothing changed.
A record suppresses capacity only if valid=1, verification_state=valid, verified_epoch equals current epoch and verified_generation equals that ask's current generation. Missing generations, stamps or meta never suppress.
Unverified, dirty, too-large and conflict receipts remain capacity-active, including when their retained ask's raw status is terminal. A receipt whose ask is missing is a store-integrity HOLD for posting; diagnostics expose the affected ID without fabricating an AskRow or status.
All list/count/eligibility projections use that one predicate. A current exact verification stamp can be reused without rehashing complete history. A lifecycle view returns a typed unavailable Reply for an unverified binding and never says eligible/fulfilled on the strength of old valid=1 alone.

### 4.1 Exact mutation coverage, including old binaries

The implementation must ship literal deterministic DDL for these named triggers; compare its complete stored sqlite_master definitions and supporting table/index schemas with the pinned implementation before suppression. No IF-NOT-EXISTS success, version-only trust, or PRAGMA schema_version-only trust.
BUMP(k) denotes the same-transaction bounded statements: upsert/increment generation for non-NULL k; set the matching receipt valid=0, stamps NULL and state NULL; upsert dirty(k,current generation) only when that receipt exists. Overflow aborts the originating write; never wraps or converts to REAL.
The trigger set is finite and names are fixed:

| Trigger | Event and exact coverage |
| --- | --- |
| fq2_asks_insert | BEFORE INSERT asks: BUMP(NEW.ask_id), covering new rows and INSERT OR REPLACE same-ID victims |
| fq2_asks_update | BEFORE UPDATE OF ask_id,kind,poster,title,body,pr_url,head_sha,bead,digest,question_form,choices,links,created_at,status: BUMP distinct OLD/NEW ask_id |
| fq2_asks_delete | BEFORE DELETE asks: BUMP(OLD.ask_id), including foreign-key cascades if introduced by supported migration |
| fq2_answers_insert | BEFORE INSERT ask_answers: BUMP NEW.ask_id and every existing victim ask_id matching NEW.message_id OR (NEW.ask_id,NEW.seq) |
| fq2_answers_update | BEFORE UPDATE ask_answers (all columns): BUMP OLD/NEW ask_id and every existing victim matching NEW.message_id OR (NEW.ask_id,NEW.seq) |
| fq2_answers_delete | BEFORE DELETE ask_answers: BUMP OLD.ask_id; covers explicit and cascading deletion |
| fq2_receipt_insert | AFTER INSERT ask_fulfillments: BUMP NEW.ask_id before the new implementation verifies/stamps it |
| fq2_receipt_immutable | BEFORE UPDATE OF ask_id,request_digest,receipt_id,receipt,namespace,operation_id,core_digest: abort `fulfillment-history-protected` |
| fq2_receipt_delete | BEFORE DELETE ask_fulfillments: abort `fulfillment-history-protected` |
| fq2_store_id_update | BEFORE UPDATE of kv's key/value affecting OLD or NEW key ask_store_id: abort `fulfillment-history-protected` |
| fq2_store_id_delete | BEFORE DELETE kv WHERE OLD.key=ask_store_id: abort `fulfillment-history-protected` |
| fq2_store_id_insert | BEFORE INSERT kv WHERE NEW.key=ask_store_id AND that key exists: abort `fulfillment-history-protected`, including REPLACE |

The exact identity table is `kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)` in Store.SCHEMA. Its initial identity creation precedes trigger installation; UPDATE matching either OLD.key or NEW.key, INSERT OR REPLACE, and DELETE are all covered.
All 12 hashed ask fields are enumerated above. Eligibility adds status; identity adds ask_id and global ask_store_id. Answers include ask_id,seq,kind,operator,message_id,text,at,choice, including notes and changes to ordering/consumed-ID membership.
truncated/card_parts/attempt/outcome/decided_by/updated_at/refreshed_from/last_activity do not enter this binding or eligibility; activity-only updates must not dirty a receipt. They still retain their original behavior.
Victim checks are bounded by the two current answer uniqueness constraints, at most two distinct victims plus OLD/NEW. This explicitly covers REPLACE conflict deletions even if a legacy connection has recursive_triggers off; do not rely on its implicit DELETE trigger firing. No foreign-key exists on ask_answers at this baseline; any later cascading schema must demonstrate the same coverage before enrollment.
Mutation triggers make old binaries invalidate valid immediately in the transaction that changes binding/eligibility. A crash/rollback cannot leave new answers with an old suppressing cache. Old code may not write verification stamps; its valid=1 writes alone cannot restore new-code suppression.
Global identity changes or receipt deletion are refused rather than silently losing immutable evidence. Direct trusted-host tampering, disabling schema guards or arbitrary hostile same-UID SQL is not a security guarantee of this design.

### 4.2 Integrity gate, startup and repair

Before each suppression-bearing count/list/view and each fulfillment mutation, validate meta identity/shape and the exact finite DDL set inside the same Store snapshot. Missing/modified triggers/tables/indexes or missing/malformed meta produce fixed `fulfillment-projection-unavailable`; no capacity suppression is allowed while asynchronous work proceeds.
A damaged projection is a capability/posting HOLD, not an empty capacity result. Legacy non-fulfillment inspection remains available where its Store schema is readable. Never recreate missing triggers and keep old stamps trusted.
Normal startup performs only bounded schema/meta validation, starts with unverified history capacity-active, and schedules bounded verification. It does not hash all histories synchronously before starting unrelated services.
Installation/migration is an explicit deployment step with supported writer quiescence, a coherent complete database backup and rollback receipt. Add columns with NULL stamps, install the literal schema and fresh epoch in one migration transaction, preserving historical bytes; no full-history hash or per-row stamp update is required.
Schema creation/index construction can itself take time proportional to retained data: measure it offline on the actual migration inventory and approve its downtime separately. It is not routine daemon-start work.
Repair after schema damage requires approved stopped-writer repair/rollforward. Restore the expected schema and replace epoch atomically; every old stamp becomes unusable before any suppression. Do not infer missing mutations from an unchanged binary or reuse an epoch after repair.

### 4.3 Finite resumable verification

Each pass visits at most 20 receipt IDs using indexed keyset selection and persists its cursor in projection metadata separately from immutable history. Dirty work and epoch-unverified work each receive at least one slot when both exist, preventing a busy dirty ask starving old history.
A record is processed at most once per pass. No fresh attempt is an assertion that a previous attempt did not happen. Verification has no external effect or notice resend.
Bound each ask to at most 50 answer/note rows, at most 256 KiB of complete canonical ask+answer input, and receipt bytes <=128 KiB; batch total input <=1 MiB. First use indexed LIMIT 51 and SQLite byte-length checks before loading fields. Reject malformed UTF-8/surrogates and oversized canonical serialization conservatively. No truncation or SELECT-all fallback.
Rows exceeding any bound are marked too-large at their observed generation and remain capacity-active with a fixed manual-HOLD diagnostic. They are skipped until generation/epoch changes or an explicit bounded verification request; other IDs continue. Manual resolution needs a separately approved higher-bound/archival design, not journal edits or consent fabrication.
Read/hash one bounded binding in a deferred snapshot, outside any writer transaction. A brief writer transaction rechecks epoch, exact schema and generation before publishing verified stamps/validity and removing that exact dirty generation. A concurrent mutation leaves it unverified; do not loop until it becomes quiet.
The existing SQLite busy timeout remains 5 seconds; no await, network, transport or hash loop occurs under the writer lock. A pass's work limits bound input and iterations, not OS scheduling time or cryptographic protection against root.
Progress/cursor/stamp writes are atomic and restartable. An interrupted pass repeats only pure verification. Conflicts never become completed merely because a later startup ran.
Known rollback compatibility is limited to the exact predecessor product source `07637745fe75dc614109a0b8ba72d3489f262a97` and this merged disabled core `a73e91d367bd626556371f33a46e25f003243c6e`, after offline forward/backward fixtures demonstrate their SQL against this additive schema. No arbitrary old binary is declared compatible.
Rollback stops the activated writer, preserves the whole database and projection schema, and restores approved code/config only. The pre-core binary counts answered asks again; the core can see conservative invalid bits. Forward startup validates all trigger/schema bytes and verifies dirty/unverified records before suppressing them. Never restore a stale backup over genuine new history.

## 5. COUNT and lock placement (finding 8)

Replace both daemon post-capacity materializations with capacity_count(), retaining the identical shared predicate and checks before and after every relevant await. No full bodies/answer rows are materialized merely to compare MAX_OPEN=20.
The migration changes the predicate once for both methods; tests assert identical membership/count under valid/unverified/conflict/too-large/terminal conditions. Approval flows, 30/hour and two-in-flight limits remain unchanged.

## 6. Future implementation, acceptance and delivery

The proposed implementation task title is `Fulfilled-question activation closure implementation`. Root must create or identify that separately admitted implementation target before an implementation approval ask; no new native ID is invented here. Its approval must pin this final signed ADR and block implementation through genuine native design admission. The existing wpina task/history is not repinned or reopened by this document.
The implementation owns fulfillment.py, fulfillment_cli.py, store.py, daemon.py and focused lifecycle/guard/CLI fixtures; chunk.py/settings.py/guard_state.py behavior is reused. Existing authentication, fresh policy, projection and redaction helpers must be reused rather than a parallel authority path.
The ordered work is: closed diagnostics and local fixes; projection migration/triggers/integrity gate; bounded worker and count integration; offline compatibility/consumer inventory; code reviews and clean disabled-source delivery. No activation is hidden in an implementation close.
Required offline acceptance, each with a real failing-first regression where applicable:

1. Unknown pre-migration owner and revoked owner produce distinct visible diagnostics; no backfill, no send, full 40-slot inspect/dispose/controller route remains usable.
2. Partial/unknown/sent notice groups, >3,500 malformed parts, pagination/reply limits, disposal and explicit replacement retain exact intent/message history; old !fulfill cannot confirm replacement.
3. Reconcile entry1 applies and entry2 malformed/legacy/unavailable preserves entry1 plus unresolved cursor; continuation uses exact readback and cannot repeat the external operation.
4. Nested writer-under-reader refuses before SQL; mode/depth reset after all failure seams; independent reader/writer contention preserves existing serialization.
5. Durable sender wins over missing/forged cache; missing durable sender refuses. Disabled !fulfill follows byte-equivalent legacy routing. Fixed refusal code has no prose suffix.
6. Each enumerated ask field and each answer field INSERT/UPDATE/DELETE, PK reassignment, notes, REPLACE victim on both uniqueness constraints, and permitted cascades invalidates immediately under both known rollback binaries and recursive_triggers settings. Activity-only updates do not invalidate.
7. Missing/modified trigger, table, index, epoch, store identity and overflow fail closed before count/view; repair changes epoch and cannot reactivate stale stamps. No receipt/identity deletion or mutation succeeds.
8. Restart with a large retained corpus performs zero full-history hash passes before serving; 20-ID/1-MiB and per-ask limits, changing generations, cursor crashes, too-large rows and continuous dirty work preserve conservative counts and fair progress.
9. Controller apply/confirm stamp only exact current bindings, transactional crash points retain original receipts/status/answers/audit. Legacy and fulfillment wire goldens, both rollback directions, and unknown-response recovery preserve predecessor semantics.
10. Both post checks use COUNT with the same projection pre/post await, including an answer mutation during await. Approval exclusions and all unchanged limits remain covered.

Before deployment, inventory each actual wake/status, queue Handoff/manage/supervisor, daemon and adapter consumer and its exact loaded source/config hashes. Unsupported callers remain held/manual; a fulfilled overlay is neither a new answer nor new wake/approval authority. Keep quality debt UNWAIVED until actually resolved.
The separate deployment ask must explicitly authorize additive schema migration/downtime, selected enabled producers, unknown-owner handling through diagnostics rather than backfill, conservative temporary capacity, the finite verification limits and retained history/storage cost. It must link published immutable ADR, actual implementation review findings/deviations, recovery/rollback evidence and exact source/config pins.
A live acceptance plan must test actual authorized question/merge confirmation, controller receipt, restart/rollforward, blocked provenance and producer compatibility without manufacturing approvals or rewriting decisions. No such acceptance, deployment, implementation permission or live clock is claimed here.
