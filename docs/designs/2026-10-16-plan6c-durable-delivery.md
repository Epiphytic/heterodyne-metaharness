# Plan 6C: durable Marmot delivery and recovery — d2

Design task `btq-w2jti`. Codex author, 2026-10-16. Proposed and unapproved.
Actual d1 `ee9203f5b2d8567a557b0876ada4dd54e99e0463` received REVISE with 33
JSON findings in review round 1 of 3. This revision preserves that history; no
new review or implementation authority is asserted.

## 1. Authority and executable scope

The governing ADR17 is `a27acecee8274e6ad8dc65def436614d42b5cb04`,
`docs/adr/0001-workstreams-v2.md` in repository
`hermes-workstreams-v2-adr-r17` (logical repository; private controller source map resolves the local checkout), not the older same-named
file in this product repository. Genuine approval `btq-ovngv` records Liam,
2026-10-14T15:57:38Z, Marmot `189fd6bb97ba`. The source manifest pins the exact
ADR Git blob and SHA-256. Because that repository has no Git remote, the review
and subsequent approval publication must include a content-addressed ADR copy
or direct accessible document link with these exact bytes; a nonexistent remote
is never invented. Earlier 586 is historical, not the governing pin.

ADR §§3.3, 6.1–6.2 and 9 require durable delivery maps, workstream task threads,
control approval/question copies, one four-hour reminder per card, a daily
digest, ten-minute failed-delivery escalation, and marked reissue of open cards
after journal loss. Native tasks, approvals, decisions and effects retain their
existing authoritative stores and genuine writer. Receipt means transport
receipt only, never read, consent or task completion. Launch `receipt_put` is
not a message receipt. Every real human question, design/deployment approval
and exception uses the authenticated Marmot admind flow. There is no terminal
consent fallback and no new decision writer in this design.

Scope is delivery policy, durable protected outbox metadata, display source
ports, maps, reminder/digest reducers, local alert writing and recovery.
Excluded are pickup, native task/decision mutation, executor effects, steering,
command cutover, membership/invites, timer-unit installation and deployment.
`btq-rd4z5` retains invite recovery. No nonexistent Plan6D API is a prerequisite.
A genuinely compatible final 6A renderer and 6B trust/registered transport owner
are required before their respective adapters are implemented. Inspected drafts
remain unapproved interfaces. Pure reducers and fake adapters can be built
within an approved 6C contract without starting those producers. Real source
ports must be admitted before any source-backed send; deployment remains a
separate genuine approval. No function-by-function permission cycle is added.

## 2. Owners and acyclic process boundary

Use route-partitioned owners. **admind alone owns its existing operators' group,
canonical AskPost/Get/answer pipeline and Store outbox. WSD alone owns sends for
its separately registered non-admind groups.** Frozen 6B d4 `e7b0b960e1a7b526a23109684e735121a8955d90`
refuses admind aliases; do not edit or weaken it. The 6B read consumer retains
its response-only capability. A different proposed full-card producer capability
belongs to the WSD transport owner; it is not handed to that read consumer.
Both producers share pure key/reducer/receipt-policy helpers, not a second
admind database or another opener of MLS state. All native transport remains
through the single wn-agent service and existing configured route owners.

For approval/question delivery the canonical admind ask card IS the control
copy. Never post an additional decision-bearing control card in WSD's read
control group. Its workstream mirror is explicitly informational: it names the
canonical ask/reference and says to answer through admind. WSD does not consume
mirror text or reactions as decisions. Existing admind authenticated answering
and genuine native decision processing remain unchanged; neither a display
snapshot nor the ask title proves native consent. Future Plan5/command cutover
requires its own actual approved writer integration. Existing unrelated asks
are not silently migrated, cancelled or duplicated.

| Source / owner | Proposed port and ownership |
| --- | --- |
| WSD task producer | Local `task_snapshot(source_id, scope)` and `task_current(token)` under existing owner APIs, returning task identity/revision, safe render input, lifecycle, original card-open time and registered destination. No queue construction in a renderer and no task writes. Unsupported actual fields are unavailable, not invented. |
| admind ask producer | Local canonical AskRow lifecycle and exact render input, read under its Store transaction; `ask_current(token)` is local. The existing AskGet returns summary/answers, not full body, so it is insufficient as a display snapshot. |
| Proposed admind display endpoint | A bounded read-only `display_snapshot` operation serves an enrolled WSD producer for one exact permitted ask/agent/workstream/queue binding. Returns immutable safe rendering input, canonical ask reference, revision/digest, lifecycle/open time and token; no arbitrary global ask bodies, answers, credentials or decisions. Same endpoint can refresh that exact token. It never calls WSD. |
| WSD transport owner | Reads task source locally or obtains the bounded ask snapshot BEFORE taking its local trust/send guard. Freshness failure holds the part. No SQLite transaction, native decision lock or send guard crosses the RPC. It then checks local registration/trust and dispatches its own route. |
| admind transport owner | Verifies its own ask/source state locally before each protected send under its existing send/trust serialization. It never calls back to WSD to verify a queued part. |
| Plan6A | Pure frozen render input→parts and safe overflow output with exact renderer/redactor identity. No source reads or effects. |
| Digest producer | WSD-owned bounded scoped projection of actual task/needs-human/idle facts, joined with bounded admind open-ask/closed-yesterday display facts through the same read-only endpoint family. This new producer/renderer schema is an explicit activation prerequisite; no assumed Plan6D/Plan5 API. |

The only cross-process path is WSD→admind bounded read or existing canonical ask
submission by its authorized original controller. admind does not synchronously
call WSD. The final check is one owner verification per part, not a preflight
plus callback. Native state can change after checking: stale display is retained
honestly and a decision still requires its own live native validation. No
cross-store atomicity or source lock held across native I/O is promised.

Proposed snapshot IPC uses same-UID private socket owner/mode and peer checks,
fixed enrolled producer/release/scope binding, not caller-selected file paths or
raw argv. Same UID is trusted service consistency, not malicious-peer isolation.
Request v1 has exactly version (integer 1), operation (`display_snapshot` or `digest_snapshot`),
request_id (32 lowercase hex), producer_binding, permitted_scope and ask_id
(nullable only for digest). Producer binding contains configured name, exact
40-hex release and established run/boot identity; scope binds exact configured
agent, workstream and opaque queue identity. IDs are 1–128 ASCII bytes except
native account/message IDs below. Frame cap is 64 KiB, one request per part,
five-second total connect/write/read deadline; reject duplicate/unknown keys,
invalid Unicode, floats/NaN, unknown versions and mismatched bindings. Reply is
exactly Available(snapshot), Closed(reference), Changed(new_token),
Unavailable(typed_reason) or Rejected(typed_reason); no source text in errors.
A snapshot carries that exact request/scope, source key/revision/digest,
original times/lifecycle, safe render DTO, renderer contract, canonical reference
and token. Digest is at most 100 bounded summary rows with explicit completeness
and verified count or unknown; overflow returns the final renderer's approved
summary/pinned-link representation or unavailable, never silently drops rows.
No actual existing DTO is advertised as supporting this new port today. Existing AskPost has no agent/workstream/queue scope fields. The proposed endpoint therefore requires an owner-controlled association registered by the enrolled original canonical-ask producer: exact ask ID, native target/source, configured producer, agent/workstream/queue identity and immutable source digest. Registration authenticates the producer through the existing private peer/enrollment boundary and compares its admitted source binding; caller text, title, poster labels or body cannot create that association. Legacy asks without a genuine association are unavailable for WSD mirrors until the real owner supplies it; admind original ask flow still works. This is an explicit producer compatibility requirement, not an installed mapping inferred from AskGet.

## 3. Durable records, reuse and exact keys

Reuse admind Store's outbox, pending/attempt, thread and receipt records. Extend
that existing store in a versioned transaction with protected namespace policy,
immutable source/copy/part binding, per-attempt dispatch phase and full receipt;
do not create a parallel admind bridge journal. Existing `enqueue` uses INSERT
OR IGNORE, generic `resolve_thread` falls back top-level, and `outbox_pass`
retries retryable errors and retains ID[0]. None is safe for protected cards
without the explicit namespace changes below. Unmanaged legacy rows preserve
their current policy and transaction behavior.

WSD uses a separate private `marmot_delivery` schema-v1 store for its own group
copies/maps and due state. Do not add tables or indexes to the exact-schema WSD
runtime Journal or to 6B's separate read journal. Shared pure policy helpers do
not imply shared database connections. First install/migration is explicit,
transactional, private permissions, retaining every existing row and failing
without replacing data. Startup validates previously initialized stores; missing
or corrupt storage is loss, not a fresh empty store. Read-only inspection never
constructs a mutating Journal/Queue or switches schema/WAL mode.

Source key is `(logical_producer, kind, canonical_source_id)`. A **card ID** is
its original logical approval/question/task identity, stable across source
revisions, renderer changes, mirrors and recovery copies. True new native asks
are new cards; rewording the same card is not. The source owner supplies this
identity, original opened time and immutable revision/digest. Copy key binds
source key, revision/digest, destination binding, reason, renderer/redactor
identity and positive copy generation. Generation is allocated atomically by
the route owner; an identical request returns the existing copy, changed bytes
under that key refuse integrity rather than overwrite. Reasons are
initial/update/mirror/recovery/join-reissue/delivery-alert/reminder/digest.
Copies have random 32-hex copy IDs and 1–20 frozen parts; exact UTF-8 text is
at most 16 KiB per part and 64 KiB per copy, or any stricter final renderer/native
limit. Include per-part SHA-256 and ordered parts hash; no dynamic rechunking.

Each attempt key is SHA-256 of canonical version/owner-store-identity/copy-ID/
part-index/attempt-ordinal, rendered to native key `d6c:` plus its first 48 hex
characters; the full hash stays private. Ordinals are 1..3. A new ordinal is
allowed ONLY after durable proven_unissued for the previous ordinal. Inspection
or a lost response uses the same old ordinal, never allocates another. Exact
binding includes source token, renderer, trust epoch, copy generation,
parts hash, part hash, text, reply target and native key. Reuse with different
bytes is an integrity hold. No reliance on undocumented native dedup lifetime.
For admind protected rows these fields attach to the existing outbox row/key.

## 4. Closed transitions and native dispatch proof

Results are exactly `confirmed(receipt)`, `proven_unissued(proof)`,
`uncertain(phase)`, `held(reason)` or `refused(reason)`. Inspect may additionally
return `not_found`; this is not proof by itself. Reasons/phases are fixed enums,
not native free text. Held reasons are source-unavailable/source-changed,
trust-unavailable/trust-denied, clock-unready, contention, capacity,
parent-incomplete and source-closed; refusal reasons are invalid-shape,
binding-conflict, unsupported-policy and integrity. Proof kind is
prewrite-refusal, healthy-absent or reserved-no-dispatch. Uncertain phase is
write-started, response-lost, invalid-receipt or recovered-dispatch-mark.

| Local part state | Transition / restart meaning |
| --- | --- |
| prepared | Can cancel on closure/supersession or reserve attempt1 after source/trust proof. No send yet. |
| reserved | Exact local intent exists. Before native write, positive owner proof allows proven_unissued; a busy/unavailable guard yields held without allocating another attempt. |
| dispatch-marked | Persist before the first native writer.write. Any crash without a positive phase receipt is uncertain, not retried. |
| confirmed | Exactly one valid native ID and matching durable receipt; terminal transport result. |
| proven_unissued | Retain phase proof. Retry same frozen part only under next ordinal, due after 1s then 5s; after ordinal3 becomes exhausted-held, never a fourth attempt. |
| uncertain | Inspect actual owner receipts only; confirmed or positive proven_unissued can settle it. Otherwise remains held, does not block unrelated sources. |
| held / exhausted-held | Typed reason, visible to health/digest; eligibility may clear a pre-dispatch held condition inside the same attempt. Exhaustion cannot reset by an ordinary source revision. |
| cancelled | Only prepared, held-before-dispatch or positively unissued work cancels; already issued/uncertain work remains historical with superseded flag. |

Copy states are prepared, sending, delivered, partial-held, uncertain-held,
cancelled or superseded. All exact parts confirmed gives delivered. A confirmed
prefix plus blocked/cancelled remainder is partial-held; no complete-copy
eligibility. Any unresolved dispatch yields uncertain-held, even if a prefix
is confirmed. Superseded preserves every prior attempt/receipt. Prepared and
never-submitted parts AND positively unissued parts/alerts are cancelled on
closure/supersession or when their condition clears. In-flight parts retain
uncertainty and become historical; they cannot launch further continuations.

The transport owner takes the same existing trust/send lock for its route,
rechecks local registration/epoch and final verified source token, durably
reserves, then initiates the native call with immutable payload. No SQLite
transaction spans native I/O. Membership/trust transition drains/fences that
same guard. WSD's source refresh happened before acquiring the guard; a local
version supersession observed in the meantime refuses. Unknown remote source
freshness never confers consent.

The wrapper must distinguish **before writer.write** from any later phase.
Pinned ControlClient._open fails before a request can leave; that phase is
positive non-dispatch. Current `socket_io`/retryable alone is NOT sufficient:
_write calls writer.write before drain, and _readline can raise similar errors.
Any error after write begins, including drain/response timeout or malformed
reply, is uncertain. Add a narrow phase-reporting native wrapper within this
approved boundary, not exception-message inference. Persist proof/result before
releasing its guard. A crash after dispatch-mark but before actual write stays
conservatively uncertain unless retained owner evidence proves no dispatch.

For a lost local reservation result, a healthy intact owner store under the
same admission/dispatch lock can prove absence: verify exact initialized store,
identity/generation/history and no matching attempt; all sends require its prior
committed row, and no concurrent same-ID dispatch is possible during lookup.
This yields healthy-absent. A reserved row that never received a dispatch mark
also proves unissued only after restart has fenced any prior active sender.
Missing/corrupt/replaced store, unknown schema, owner outage or incomplete
recovery yields uncertain, never healthy-absent. Any retry still uses atomic
unique insertion so a later concurrent request cannot double-admit. An owner
inspect never sends. No transcript or operator statement fabricates a receipt.

Each send has a 30-second TOTAL open/write/read/close deadline, including cleanup,
not 30 seconds for each stage. A prepared intent is durably admitted and owned
by the async outbox; a five-second reducer does not await or abandon that send.
Owner restart handles its exact retained phase. Existing await cancellation is
not proof of non-dispatch. Protected backoff uses the owner's existing durable
due state and event-loop nearest-deadline wake, like admind's existing wake/timer
pattern, not a sleep inside a reducer and not a Plan8 minutes-later retry.
Only one native send per binding, with fair progress for other bindings.

## 5. Receipts, roots, resolution and renderer safety

At the pinned protocol, account/sender/message/reaction IDs are exactly 64
lowercase hex characters (32 bytes), consistent with 6B. Group IDs use the
configured exact even hex value with 1–256 byte local resource bound. This
validator is a new shared adapter validation, not an existing `FinalSent` method:
that DTO currently contains plain list[str]. A native receipt confirms only
when the list contains **exactly one** valid unique message ID, matching the
current attempt/account/group/copy/part/text hash. Retain the full bounded raw
list privately (at most 64 entries and 16 KiB) for diagnostics. Empty, multiple,
malformed or cross-copy colliding IDs produce uncertain/invalid-receipt and no
root; never choose ID[0]. A definitive retained matching receipt may settle
uncertainty; no generated ID substitutes for one.

A continuation needs the real confirmed part0 ID of the same copy/destination
and current binding. Protected `resolve_thread` returns parent-held for missing,
failed, partial, uncertain or wrong-binding parent instead of falling back
top-level. A fully confirmed part0 alone permits continuing that copy; it does
not authorize interpreting a reply as an answer to the full copy. Task updates
may use a verified current task-root map. Control/workstream copies have
independent roots and cannot combine parts to claim one full delivery.

Resolver is read-only and returns exactly complete-current, partial-current,
superseded, uncertain, not_found, with source/copy identity and a verified
canonical/current pointer if known. A target in a prefix of an incomplete copy
is partial-current, never complete-current. No answer/steer/approval follows
from any resolver result; WSD mirrors do not accept answers at all. Old or
unknown targets never turn into new task intake because a map was lost. Actual
canonical admind answering retains its own complete-card/native authority
checks; required compatibility fixtures must prove no incomplete card can
supply material missing from an accepted decision.

Text must match frozen final 6A rendering after redaction/framing. Renderer and
redactor revisions/configuration are in the copy key; upgrades allocate a new
copy generation, retain old receipts and cancel unsent old parts. They never
reset the card reminder or alert budget. If protected content exceeds limits,
use final 6A's approved summary plus exact pinned bead/design links, with full
text already durable on the source bead by its genuine owner. Summary must
preserve the required effect/digest/material facts and explicit overflow label.
6C does not write the bead. If no actual safe overflow contract/link exists,
HOLD and return the reason to the owner. There is no hidden !details substitute,
clipping, or assumption the provisional6A supports this output.

## 6. Due times, one reminder, digest and clock recovery

Separate the card-open anchor from delivery attempts. Four-hour reminder anchors
to the original card opened time, never revision/reissue time. Delivery escalation
anchors to the first eligible display intent's durable first-send admission time,
recorded BEFORE trust/capacity/send attempts; it never resets on retry, mirror,
renderer change or reissue. This is an explicit clarification/change from d1's
source-open delivery anchor and is listed for genuine design acceptance. If
admission capacity is exhausted, the owner retains the source/due reference in
the existing bounded backlog; no card silently becomes untracked. Capacity
failure itself produces local health failure, not a fabricated per-card row.

At delivery_anchor+600s, if no eligible single copy has fully confirmed, reserve
one control delivery alert per card ID/delivery episode, not per revision/digest.
For approval/question cards only the complete canonical admind control copy is an eligible decision surface; an informational workstream mirror cannot suppress an alert for an undelivered canonical ask. For task cards the complete registered workstream copy is eligible. Canonical admind ask control delivery is admind-owned; a WSD task failure uses
a closed NoticePost safety request to admind, not another question or decision.
This proposed exact reason/reference notice profile must be real before that
adapter activates. No caller-supplied arbitrary card/approval effect is allowed.
At delivery_anchor+660s, if that alert lacks full receipt, write the independent
local alert **whether or not the control alert was ever reserved**. A definite
pre-dispatch refusal may write it earlier. Late full delivery cancels any
prepared/never-issued/positively-unissued alert; issued/uncertain alerts remain
history. No recursive alert-of-alert. Content says delivery unconfirmed, not
operator absent or approval rejected.

Each route owner maintains one nearest-due event-loop wake for pending retries
and 600s/660s/4h boundaries, persisted UTC plus same-boot monotonic anchor. This
is a narrow delivery wake within the existing owner, not an installed cron or
new general scheduling engine. No sleeping/reentrant reducer loop. A 30-second
bounded due sweep is a recovery backstop while the owner is healthy; due wake
is not delegated to Plan8's 5-minute reconcile or 15-minute reminder_sweep.
Deadline means eligibility at that exact instant; event-loop/host outage can
still delay execution and must be visible. Plan8 may wake recovery/observe
backlog, but is not required to meet the retry or secondary-alert cadence.

At card_open+4h, reserve at most ONE reminder for that card across every revision,
copy, route and recovery generation. Canonical approval/question reminder is
admind-owned, targeted only to its complete current control card's part0; WSD
never sends a second workstream reminder. If control copy is incomplete, record
withheld and issue only when a complete eligible control copy exists. Unknown
outcome consumes the reminder budget and prohibits automatic duplicate. Exact
managed ask namespace excludes that ask from legacy admind autobump BEFORE
protected admission, in the same transaction; unrelated asks keep their policy.
No source may simultaneously be a legacy independently bumped ask and a second
6C decision card. Closure cancels never-issued reminders; material supersession
updates future reminder text/target without allocating a new reminder budget.

Daily digest runs at **08:00 UTC**, with occurrence key `(control binding, UTC
date)`. This 6C adapter accepts only UTC and08:00; a Plan8 non-UTC configuration
must disable/reject this adapter, not reinterpret its keys. At D 08:00, list ALL
current open approvals/questions as of capture, even those younger than 4h, plus
all closed-yesterday cards (D-1 00:00 inclusive to D00:00 exclusive), needs-human
and idle-workstream facts with source freshness/completeness. An item opening
at D 05:00 appears at D 08:00. A card reaching4h at D 03:00 is in D 08:00 if still
open; one reaching4h at D 09:00 is next included at D+1 08:00. No four-hour filter
limits open-card inventory. Digest is FYI, never AskPost/MAX_OPEN. Missing source
sections are explicitly unknown/partial; overflow follows the actual final 6A
contract. Catch up at most the latest due UTC date; record skipped range, never
flood older copies. Digest send uncertainty never automatically reissues it.

Use integer UTC microseconds and same-boot monotonic/BOOTTIME samples. Final
Plan8 clock_ready interface is provisional, not installed. Freeze its exact
bytes and compatibility fixture before dependent use; it must expose boot,
clock epoch, synchronization, ready state and persisted UTC high-water. New
boot/process begins unready, completes recovery, obtains affirmative platform
time-sync proof and two observations at least 30s apart with <= 2s elapsed-clock
difference and UTC not behind admitted high-water. Within a boot compare recent
samples, not a four-hour cumulative drift; normal NTP slew does not accumulate
into permanent1s hold. Backward UTC, loss of synchronization or >2s sample jump
holds new clock-dependent events; reestablish readiness through the same finite
30s observation rule, retain all prior keys/deadlines and never extend a due
card by resetting its anchor. If UTC remains behind high-water, hold until it
catches up. Same-boot already-anchored delivery fallback may still run from its
monotonic deadline while new UTC scheduling is held; on a new boot with unknown
time emit fixed local clock-health failure, not invented elapsed delivery.
No new terminal permission is a clock-readiness proof.

A reconcile reducer selects at most 100 sources, prepares at most 20 intents and
performs at most 20 bounded LOCAL state operations within 5s, no awaited native
send/RPC. Snapshot refresh is a separately owned bounded async preparation;
its five-second timeout returns held, not incomplete success. At most four snapshot preparation workers per owner and one per source run concurrently, with at most 20 started per due sweep; no per-source unbounded fanout. Native workers
have their own 30s total limit and at most one per binding. Durable oldest-due/
source-key cursor provides fairness. Pending counts/oldest age remain visible.
Library contains no task pickup policy. Backend unavailability does not require
a gatekeeper model call or prevent unrelated deterministic eligible sources.

## 7. Shared local alerts, persistence loss and recovery

Add ONE shared writer to `admind/alerts.py`, used by 6C and Plan8. Existing module
is a reader only. API takes a fixed namespace/episode and fixed safe text profile,
not arbitrary paths. 6C namespace is `d6c_` plus 32 lowercase hex; Plan8 uses a
distinct prefix. Names stay under 64 characters and avoid raw 64-hex identifiers.
Verified private directory, O_EXCL/O_NOFOLLOW mode0600 temporary regular file,
write+fsync, atomic rename, directory fsync. Existing matching final bytes settle
idempotently; conflict refuses. Journal records desired deterministic filename/
content hash first, then writes file, THEN records written. Crash before file
write retries; crash after rename verifies file/hash then records written. It
never records success first. ENOSPC/fsync/rename failure retains desired state
and typed local failure; retry on next bounded due wake, without claiming relay
or remote receipt. Existing admind relay/outbox receipts are separate evidence.

At most 256 unsettled6C alert files, plus bounded pending episode records within
store capacity. One file per episode, no revision multiplication. A capacity
hold emits a local structured service-journal diagnostic and stops new protected
admission, not an unbounded series of alert files. Shared cleanup archives only
explicitly settled/retired episodes with manifest and dedup keys; relay enqueue
alone is not receipt. Never delete another writer's namespace or suppress an
unrelated ask. No automatic unbounded retention is promised. Delivery store
caps 10000 sources,100000 parts, 256MiB per route owner; reaching a cap holds new
admission and exposes backlog/capacity. Retain uncertainty, attempt and card
reminder uniqueness until a reviewed retention/archive contract permits compacting.

Clean restart validates identity/schema, fences old senders, inspects retained
attempt phases and recovers nearest deadlines before new sends. No second
mutating store is opened to infer missing state. Healthy retained admind rows
are reused by stable source bindings, not blindly duplicated. Preserve damaged
bytes on journal loss. Under an actually approved implementation of this recovery
contract, rebuild owner-proven current open approval/question sources and
**automatically** allocate marked `re-issued after recovery` display copies in a
new recovery epoch, as ADR3.3 requires. No additional human consent per copy is
required. Operational file restoration/replacement or deployment still uses its
existing genuine Marmot authority; missing approvals cannot be bypassed by
recovery. Rebuilt display never replays a task, action or decision. If the
canonical admind ask source itself cannot be reconstructed from genuine native
owner facts, HOLD that source, report unavailable and do not invent an ask or
approve it. Unknown old native message targets stay unknown; a later verified
canonical association may point to the current root, never guess.

If reminder history is lost, mark existing open cards' reminder budgets unknown/
consumed for safety, send the ADR-marked recovery card, and thereafter use the
daily digest rather than inventing an unused single reminder. This conservative
availability deviation must be explicitly included in design acceptance.
Membership add success/count cannot prove visibility. V1 has NO automatic join
reissue: an authenticated Marmot request can authorize one marked display reissue
after fresh trust/lifecycle checks. No invite or remove/add loop, fake membership
receipt, inferred approval or budget reset. Future automatic visibility support
requires demonstrated native evidence and genuine amended design approval.

## 8. Compatibility, verification and explicit decisions

Implementation order: approved delivery contract; pure shared policy/DTO/reducer
and fake-clock fixtures; admind versioned protected namespace migration preserving
legacy Store rows; WSD separate store and existing transport-owner integration;
real source/snapshot/renderer compatibility gates; local alerts/due callbacks;
actual cross-model code review (maximum3 rounds); separately authorized exact
release/configuration deployment. No source code, tests, effects or deployment
have been performed by this document. User questions remain exclusively Marmot.

Required offline tests: existing Store migration rollback/legacy behavior;
exact scoped IPC/no callback/lock across read; immutable duplicate conflict;
pre-open refusal vs writer.write/drain/response ambiguity; crash at every local
and native boundary; healthy absent under fenced owner vs lost-store unknown;
exact 3-attempt cap and 1s/5s due callbacks without Plan8 polling; exhausted/partial/
uncertain transitions; one-ID-only roots and cross-copy collision; incomplete
copy cannot resolve complete; prepared cancellation on closure/late receipt;
renderer-generation change without reminder reset; approved summary/pinned-link
overflow; one canonical ask/decision/control surface, informational mirror only;
scoped autobump exclusion without changing unrelated asks; 600/660/4h exact
eligibility with capacity/trust failure before alert reservation; shared writer
crash/fsync/ENOSPC/conflict/namespace/cap/cleanup; UTC08 digest young-open cards,
closed-yesterday/partial counts/catch-up; newboot and sameboot clock recovery;
loss/rebuild automatic marked cards without task effects; unknown old targets;
retained original claims/session/group/queue/native decision identities.

The published Marmot design request must explicitly name these choices and
ADR refinements/departures: route-partitioned owners rather than d1's sole-admind
bridge; canonical admind decision surface and informational workstream mirrors
rather than answering approval cards in workstream groups; source-vs-send race
and independent native decision revalidation; no retry after ambiguous send,
possible indefinite hold and no protected continuation fallback; single native
ID only; delivery clock anchored to first eligible send admission while reminder
clock uses original card open; new660s local fallback eligibility and actual
owner-outage latency; owner-local deadline callbacks plus Plan8 clock compatibility;
one reminder per card and unknown/consumed reminder budget after loss; all-open
UTC 08:00 digest; manual join reissue; bounded storage/alerts and same-UID trusted
service model. Automatic ADR-marked open-card reissue after loss is retained,
not an extra human approval gate. Any refusal of these choices requires revising
this same design lineage before implementation, not silently editing frozen 6B.
