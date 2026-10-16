# Plan 6C: durable Marmot delivery and recovery — d3

Design task `btq-w2jti`. Codex author, 2026-10-16. Proposed and unapproved.
Actual d1 `ee9203f5b2d8567a557b0876ada4dd54e99e0463` received REVISE33
in round 1. Actual d2 `020c72a96ab527e57be28a0b2a619444308ac018` received
REVISE27 in round 2. Both actual reports and ordered dispositions are retained.
Only round 3 remains. This document asserts no approval or implementation.

## 1. Authority, boundaries and prerequisite contracts

The governing ADR17 is `a27acecee8274e6ad8dc65def436614d42b5cb04`,
`docs/adr/0001-workstreams-v2.md` in logical repository
`hermes-workstreams-v2-adr-r17`; the private input map resolves its checkout.
Genuine approval `btq-ovngv` records Liam, 2026-10-14T15:57:38Z, Marmot
`189fd6bb97ba`. The manifest pins its exact blob and SHA-256. A publication
must link those exact accessible bytes or a content-addressed copy; it must not
invent a Git remote. Earlier 586 is historical, not the governing revision.

ADR §§3.3, 6.1–6.2 and 9 require durable message maps, task threads, control
approval/question copies, one four-hour reminder per card, a daily digest,
ten-minute failed-delivery escalation and marked reissue after journal loss.
Receipt means transport receipt, never read, consent or task completion.
Launch `receipt_put` is not a message receipt. All real operator questions,
design/deployment approvals and recovery exceptions use authenticated Marmot
admind decisions. No terminal consent fallback or new native decision writer.

Scope: pure delivery policy, protected existing outbox records, source snapshots,
message maps, reminders/digest, local alert writing and display recovery.
Exclude pickup, native task/decision mutation, executor effects, steering,
invites/membership, timer-unit installation and deployment. `btq-rd4z5` retains
invite recovery. No Plan6D API is assumed. Existing claims, sessions, native run,
groups, decision history and genuine approval dependencies remain intact.

Final compatible Plan6A rendering and Plan6B registered transport/trust contracts
are prerequisites for the adapters using them. Inspected 6A, 6B, Plan5 and
Plan8 documents are provisional, not installed APIs or approval. Pure reducers
and fake adapters can be built after actual 6C design approval without starting
real producers. Real source ports, protected posting, backups, recovery handlers
and route guards must pass the compatibility gates below before activation.
Implementation within an approved contract does not need per-function consent;
new/changed design choices and deployment retain genuine approval. This d3
proposes the choices in §9 for the human design request.

## 2. Route owners, protected canonical posting and source trust

**admind owns its existing operators' group, canonical ask/answer pipeline and
Store outbox. WSD owns transport for separately registered non-admind groups.**
Frozen 6B d4 `e7b0b960e1a7b526a23109684e735121a8955d90` rejects admind aliases;
this design does not edit it. Its read consumer remains response-only. The new
full-card capability belongs to WSD's proposed producer/transport owner, not
that read consumer. Shared pure helpers and a neutral alert writer do not
imply shared DB connections or another opener of MLS state. Both use wn-agent
through their configured route owners.

The canonical admind ask card is the approval/question control copy. Its
workstream mirror is informational, names the canonical ask, and directs answers
to admind. WSD consumes no mirror reply/reaction as a decision. Native approval
effects still require existing authenticated operator, current policy and native
validation; rendering, a title or a source association never proves consent.

### 2.1 Proposed atomic protected post

Existing `post_ask`/`commit_approval` insert an ask and `ask:<id>:<i>` outbox rows
in one transaction. A later association cannot retroactively protect that send.
Therefore activation REQUIRES a reviewed protected-post variant at those actual
entry points. Its typed input contains the existing canonical ask/approval input,
producer binding, exact source/scope binding, logical decision ID, original
opened time, and optional predecessor ask ID. The original controller remains
the genuine ask producer; no arbitrary WSD task renderer originates asks.

Under the existing owner serialization, validate the input and immutable source
reference, then atomically insert the canonical row, scope association, logical
identity, protected policy/version, autobump exclusion, frozen original parts,
delivery intent/anchor and outbox rows. All or none: no legacy enqueue followed
by upgrade. Seal the source backup (§7) before acknowledging or allowing a send.
Repeated identical post intent returns the same ask; conflicting bytes refuse.
A new protected refresh atomically closes/supersedes its predecessor according
to the existing canonical approval refresh rules and preserves logical identity
and reminder budget. It does not silently change existing ask consent or digest.

Legacy unassociated asks retain their existing answer pipeline and are included
in the admind global digest. They are not advertised as protected, mirrored, or
quietly upgraded. Migrating any existing open ask requires a genuine owner
association and an atomic fenced migration with no pending legacy sender;
otherwise it remains legacy. No deployment may claim all asks are protected
until the actual producer paths, including approval commit/refresh, use this
variant. Protected asks must be excluded from legacy autobump in that same
transaction, and protected `!asks repeat` must route to the protected copy
policy rather than emit unmanaged legacy rows.

### 2.2 Source ports and trust model

| Owner | Proposed source contract |
| --- | --- |
| WSD task producer | Local bounded immutable task snapshot and local version check: actual task identity/revision, safe render input, lifecycle/opened time, registered destination. Unsupported fields return unavailable. No Queue construction in a renderer or task writes. |
| admind producer | Its own canonical AskRow and protected association under Store serialization. Local source validation, never a synchronous WSD callback. |
| admind display endpoint | One exact associated ask snapshot for a configured WSD producer and exact scope; full safe render input and canonical reference, no global bodies, answers, credentials or decision capability. |
| WSD transport owner | Fetches one immutable ask snapshot per copy BEFORE its proposed local trust/send lock. Checks local copy revision, registration and trust before each part. No DB transaction or send guard spans the RPC. |
| Plan6A | Pure input-to-parts/overflow contract with pinned renderer/redactor identity; no source reads/effects. |
| Global digest | admind-owned canonical inventory of ALL its asks, including legacy/unassociated asks, plus separately admitted bounded task/needs-human/idle/quota projections. No cross-group disclosure to a WSD read consumer. |

Current `ctl.Peer.pid` is audit only. ask.sock accepts same-UID callers; poster
labels are unverified. There is NO existing enrolled authenticated local producer
boundary. V1 deliberately trusts configured same-UID service producers and
owner-maintained association records as an operational convention. Mode0700
state/mode0600 sockets, expected UID, fixed allowlisted release/scope and audit
checks prevent accidental cross-binding, not a malicious same-UID process.
Names, readable digests and PIDs cannot authenticate that peer. This residual
trust choice must be explicitly approved; adversarial same-UID isolation would
need a different reviewed boundary. No false claim that body/title checks
provide authentication. Native human decision authentication is unchanged.

Snapshot scope contains all frozen 6B WorkstreamBinding fields: configured agent,
workstream, opaque queue identity, repository_identity and registration_epoch,
plus exact configured destination account/group. No caller-selected paths/argv.
Producer binding is configured name, exact 40-hex release and established run/
boot identity. Identifiers are 1–128 ASCII bytes except exact native IDs (§5).
The proposed private socket has one version-1 request/reply, request cap 8KiB,
reply cap 256KiB, five seconds TOTAL connect/write/read/close. Exact request keys:
version, operation=`display_snapshot`, request_id=32 lowercase hex,
producer_binding, permitted_scope, ask_id. Reject unknown/duplicate keys,
non-integer version, floats/NaN, invalid Unicode and binding mismatch. Reply tag
is Available(snapshot), Closed(reference), Changed(token), Unavailable(code) or
Rejected(code); fixed safe error codes only. A snapshot echoes request/scope,
source/logical identity, revision, digest, opened time/lifecycle, safe DTO,
renderer contract, canonical reference and immutable token. The 256KiB cap
matches the existing ask request order of magnitude and admits its 16000-character
body plus envelope; exact worst-case encoded DTO must be measured at admission.
If still too large, source owner first supplies the compatible final 6A safe
summary plus durable full-source reference. Return typed unavailable if no such
contract exists; never silently truncate before rendering.

Only one full snapshot RPC per copy/preparation, not per part. Each part checks
the local frozen token/revision and local route/trust epoch. Source closure may
race that immutable snapshot; a queued display can be stale until the next
bounded refresh/supersession event, not consent. Before starting an initial copy,
require snapshot age <=30s; freeze it for that copy, do not restart an already
partial copy merely to refetch identical source text. Material change invalidates
unsent old parts on the next observed update. At most four preparation workers,
one per source, at most 20 starts per sweep. RPC failure holds that source, not
unrelated rows. No synchronous callback from admind to WSD and no NoticePost
alert side channel. Source/snapshot versions must be frozen and tested before
real activation; none of these DTOs is claimed installed today.

## 3. Records, logical identity and compatible exact keys

Reuse admind's existing pending/outbox/ask/thread/receipt records. A versioned
Store migration adds protected metadata and indexed durable due/phase state;
no parallel admind bridge DB. Preserve all legacy rows and policy. Existing
INSERT OR IGNORE is insufficient for immutable conflicts; protected duplicate
admission compares every bound field. Existing generic thread fallback, retryable
error handling and ID[0] selection are explicitly bypassed for protected rows.

WSD gets one private `marmot_delivery` schema-v1 store for its own maps/copies.
Do not alter the exact-schema runtime Journal or 6B read journal. Explicit
initialization records UUID/schema in the deployment receipt and independent
backup seal; startup never silently creates a missing previously initialized
store. Opening admind's existing creating constructor before identity validation
is forbidden in protected startup. Read-only tools open no mutating constructors,
change no WAL/schema mode, and acquire no writer locks.

Source key is `(logical_producer, kind, canonical_source_id)`. For an approval or
question, **logical decision ID** is owner-assigned at first protected post and
preserved across canonical ask refresh, context_digest change and recovery.
A new ask_id alone does not create a new budget. Refresh must name the exact
previous ask/source and match its logical identity; cannot infer lineage from
title/body or merely the same bead ID. A genuinely new independent decision gets
a new logical ID only through the genuine owner post contract. Legacy asks have
no invented lineage and remain distinguishable in global inventory. Task card
identity is its actual native task identity; digest identity is its occurrence.

Generation-free copy request key hashes version, logical/source identity,
source revision/digest, exact destination binding, reason, renderer/redactor
identity and **reason occurrence**. Initial/update/mirror use their exact source
revision; reminder uses logical decision ID only; digest uses UTC date; recovery
uses independently sealed loss epoch; join/operator reissue uses authenticated
inbound request ID. Same request key returns the existing copy/generation;
changed payload under that key is integrity refusal. Only a genuinely new key
allocates the next generation atomically. This prevents reconcile-created
copies while allowing an explicit marked reissue. Copy record binds random
32-hex ID, ordered parts hash, 1–20 frozen parts, <=16KiB UTF-8 per part and
<=64KiB per copy or stricter actual renderer/native bound. No dynamic rechunking.

**Native part key is stable across all permitted proven-unissued retries.**
For canonical initial/refresh cards retain `ask:<ask_id>:<part_index>` so existing
ask lookup/completeness conventions remain usable. Protected marked repeats use
existing `askr:<ask_id>:<request_id>:<part_index>` grammar and `ask_repeats` rows;
request_id is a persisted 64-lowercase-hex owner request digest (matching
the actual _CARD_KEY regex), not an attempt ordinal. The ask ID retains the
actual four-character [2-9a-hjkmnp-z] alphabet. Extend
protected current-copy lookup explicitly: current protected generation, not
`_first_full_repeat`'s earliest historical full repeat. Protected reply gates
require full current copy/context/native binding before answering. Preserve
legacy helpers unchanged for unmanaged asks. Original and repeat IDs resolve
to the SAME canonical decision, never a new ask/decision due to display recovery.
WSD native keys use `d6c:` plus48 lowercase hash hex of full immutable copy/part
binding; full digest remains private. No ordinal appears in either native key.

Separate attempt identity is `(owner_store_uuid, copy_id, part_index, ordinal)`.
It binds the stable native key, frozen text/hash/ordered-parts hash, source token,
renderer/redactor revision, trust/registration epoch and reply target. Retry
never changes those bytes or target; a changed rendering needs a new copy with
retained history. Full native receipt and bounded raw returned IDs attach to the
attempt. Same-key native dedup from S4 is an extra guard, not permission to retry
a possibly issued send or a promise of undocumented dedup retention.

## 4. Dispatch phases, retries and fair due processing

Closed result tags: confirmed(receipt), proven_unissued(proof), uncertain(phase),
held(code), refused(code); inspect can also return not_found, which is not proof.
Proof kinds: prewrite-refusal, healthy-absent, reserved-no-dispatch. Uncertain:
write-started, response-lost, invalid-receipt, recovered-dispatch-mark. Held:
source-unavailable, source-changed, trust-unavailable, trust-denied, clock-unready,
contention, capacity, parent-incomplete, source-closed, exhausted, recovery-required.
Refused: invalid-shape, binding-conflict, unsupported-policy, integrity.

| State | Transition / recovery |
| --- | --- |
| prepared | Durable frozen intent; can cancel or reserve after current local source/trust checks. |
| reserved | Exact attempt committed/sealed, no native write. Busy/unavailable pre-dispatch condition holds the SAME attempt. Fenced proof can establish unissued. |
| dispatch-marked | Commit/seal before writer.write. Crash without positive proof becomes uncertain. |
| confirmed | Exactly one valid matching native message ID, terminal transport result. |
| proven_unissued | Retained positive proof permits next ordinal, SAME native key/text/target. |
| uncertain | Inspect retained owner evidence only; settle confirmed or positively unissued, otherwise hold. No automatic duplicate or next ordinal. |
| exhausted-held | Three automatic proven-prewrite attempts consumed; source revision cannot reset it. Fixed authenticated recovery action below. |
| cancelled | Never issued or positively proven unissued only. Issued/uncertain history survives as superseded. |

Automatic ordinals1..3 are due at first eligibility +0s, +5s, +30s (not cumulative
0/1/5). Next attempt requires prior positive non-dispatch AND current local
source/trust checks; scheduler delays do not accelerate later deadlines. If the
previous attempt finishes after its next deadline, next eligible attempt may run
then, never two concurrently. This proposed timing change keeps THREE total
automatic attempts and survives the concrete 10s service restart. After the
third proven-prewrite failure, exhausted-held stays visible in the digest/health.
It is not a permanent unsupported lock: §8 defines an authenticated marked
recovery action. No source edit, daemon restart or new native key resets limits.

A proposed owner phase adapter distinguishes `_open` failure BEFORE any request
write from writer.write/drain/read failures. Current `socket_io`/retryable alone
cannot do this. Persist dispatch-mark and backup seal before first write. Any
write/drain/response timeout, malformed reply or cancellation after write begins
is uncertain. Each native call has30 seconds TOTAL including cleanup, not30 per
phase. A prepared outbox intent belongs to the async owner; the five-second
reducer neither waits for nor abandons its native send.

Under the proposed compatible 6B WSD transport guard, verify registration/trust
epoch and local frozen token, reserve/seal, then dispatch. Membership transitions
must fence/drain that guard. This WSD guard/transport DOES NOT exist at pinned
product076; it is a required compatible implementation, not borrowed current
functionality. admind similarly integrates protected dispatch into its actual
owner serialization. No SQLite transaction crosses network I/O.

Healthy-absent needs intact initialized UUID, verified seal/frontier (§7),
exclusive admission/dispatch lookup lock and no matching reservation. Every send
requires a committed/sealed prior record. Reserved-no-dispatch needs a fenced
old sender and no dispatch mark. Missing/corrupt/stale/unvalidated store,
incomplete backup recovery, owner outage or replaced identity cannot prove
absence. Native inspect never sends; not_found alone never authorizes retry.

Current admind `_backoff` is in memory and `outbox_pass` can return on a delayed
head row. This design REQUIRES new indexed durable next_due/phase selection for
protected rows, not claimed reuse of existing durable timers. Select oldest
eligible due/source key, exclude held/uncertain from runnable selection, reserve
under owner transaction, retain fair cursor. Preserve relative ordering within
a copy and unrelated legacy policy. A delayed/uncertain protected row must not
block another eligible row or escalation; sends remain serialized per binding,
with bounded fair selection across classes. One pending network operation may
delay the next by its30s bound, not an unbounded head-of-line hold. Owner event
loop wakes at nearest durable due time with30s recovery sweep; no sleeping
reducer and no dependence on Plan8's five-minute reconcile for retries.

Copy is delivered only when ALL exact parts of ONE copy confirm. A confirmed
prefix plus blocked/cancelled remainder is partial-held, any unresolved dispatch
is uncertain-held. Neither resolves complete. Supersession cancels never-issued
or positively unissued continuations; issued/uncertain parts remain history and
cannot spawn further continuations. Late receipt can settle that history but
cannot resurrect a closed/superseded copy or authorize a reminder.

## 5. Receipts, thread roots, bytes and decisions

Pinned native account/sender/message/reaction IDs are exactly64 lowercase hex.
Group uses configured exact even hex with1–256 bytes local bound. These are new
adapter checks, not existing FinalSent validators. Confirm only EXACTLY ONE
valid unique returned message ID matching attempt/account/group/part/text hash;
retain full bounded raw list privately (<=64 entries/16KiB). Empty, multiple,
malformed or cross-copy colliding IDs mean uncertain/invalid-receipt. Never ID[0]
or synthetic IDs. A definitive retained matching receipt can settle uncertainty.

Continuation needs confirmed part0 of that same copy/destination/binding.
Protected thread resolver returns parent-held rather than existing top-level
fallback. Part0 permits that copy's continuations, not answering an incomplete
card. Protected current-copy map must integrate `ask_for_message`, `card_delivered`,
`bump_target`, `first_card_message` and approval reply processing without breaking
existing canonical source/current-context/operator/native checks. Old contexts
and partial/superseded targets cannot bypass current approval validation. Mirrors
accept no answers. One receipt across two copies is never full delivery.

Read-only resolution returns complete-current, partial-current, superseded,
uncertain or not_found, with exact source/copy/current pointer where proven.
Unknown targets remain unknown, never new intake after map loss. No resolver
result itself writes a decision or steering effect.

Frozen text is final rendering AFTER redaction/framing, with exact renderer,
redactor and config identity and SHA-256. At protected send, verify bytes/hash
and that current safety redaction leaves bytes identical. Do NOT silently run
legacy outbox_pass's redact-and-send transformation on protected rows. A redactor
change that would alter text holds unsent parts and requires new safe rendering/
copy; old hashes/receipts survive. No old unsafe text is sent to preserve a hash.
This differs from unmanaged rows, whose existing redaction behavior remains.

Final6A overflow contract must preserve effect/digest/material facts and explicit
summary label with pinned safe bead/design links whose full source is already
durable. 6C writes no bead. No safe source/reference/actual renderer contract
means HOLD and return typed reason, not clipping or an assumed !details API.
Renderer generation changes never reset logical reminder or delivery episode.

## 6. Anchors, escalation, one reminder, global digest and clock

Original opened time belongs to logical decision identity across ask refresh.
Four-hour reminder uses that time, never the current ask/revision timestamp.
**Delivery intent** is recorded by protected canonical posting BEFORE send
eligibility, trust, snapshot fetch or outbox capacity. Initial escalation episode
is `(logical_decision_id, initial)`, anchored to that original protected intent.
Renderer update/ask refresh/retry/manual join copy does not open a new episode.
Only detected owner-store loss plus validated source restoration opens a sealed
recovery epoch episode anchored when the restored source is enumerated. One
600s alert and one660s local signal per such episode. Complete current canonical
control delivery settles that episode; later normal updates do not silently
re-arm an alert. This finite policy and recovery re-arming need human acceptance.

At intent+600s, an undelivered approval/question reserves one admind-owned
control delivery alert in its existing protected outbox, with full receipt check
available locally. Informational mirror delivery cannot suppress a missing
canonical control card. At+660s, if that alert lacks full receipt, request local
alert writing whether or not the alert was ever enqueued. Late full delivery
cancels only never-issued/positively unissued alerts. No alert-of-alert loop.
This machinery applies ONLY to approval/question cards, as ADR6.2; WSD task
outages expose bounded aggregate local health/digest, not per-task NoticePost
bursts. NoticePost is NOT used: its posted result means enqueue, not receipt,
and its12/hour,10-undelivered limits cannot implement this protocol.

Capacity before a full copy is not a fictional per-card send admission. Each
accepted protected post has a minimal durable source/intent/anchor before the
outbox reservation can fail. If even the bounded source catalog cannot accept
that intent, reject the post before creating/enqueuing an ask; keep the genuine
caller's durable request for retry and set a fixed aggregate capacity episode
anchored at FIRST rejected admission in a preallocated bounded health slot in the
independent catalog (excluded from normal source capacity). If that slot cannot
be durably updated, report local durability failure without claiming an anchor. Aggregate local signal is immediate;
aggregate control alert eligible at+600s and fallback local status at+660s.
No per-card clock is claimed for a source the service never accepted. Snapshot
unavailable for a known accepted ask or missing trust does not change its anchor.
This bounded rejection/aggregate behavior is an explicit availability qualification
of ADR escalation, not an unlimited hidden backlog promise.

Local files are independent of the WSD connection and durably observable on host.
Their relay still uses admind/wn-agent and can fail along with a canonical ask.
There is NO guaranteed independent REMOTE escalation route here. This is an
explicit ADR6.2 departure proposed for approval, rather than a claim that two
rows on the same failing transport are independent. Service/host outage can
delay660s writing; persisted due state is recovered on restart. No external
channel, operator presence inference or gateway permission workaround is invented.

At logical_open+4h, at most ONE reminder across all asks/revisions/routes/recovery
copies. admind owns it for canonical approval/question cards, only on complete
current control part0. Incomplete copy records withheld, allowing it only after
full current delivery. Reserve budget atomically before native effect; ambiguous
outcome consumes it. Closure cancels unsent reminder; refresh preserves budget
and updates only a not-yet-issued reminder. Loss with unknown history marks
budget consumed; daily digest still provides visibility. Legacy autobump remains
only for unmanaged asks, not an additional protected reminder. Task cards retain the ADR one-reminder rule separately: WSD owns one reminder
per actual task identity, anchored to its original card-open time and addressed
only to the complete current registered task root. It makes no approval mention
or decision request. Closure cancels it, unknown history consumes it, and source
revision/recovery cannot reset it. It does not get per-task delivery escalation.

**admind is the sole global daily digest producer and sender to its configured
operators' group.** It directly reads canonical open asks, including legacy and
unassociated asks, and closed-yesterday asks from its Store. Distinct logical
refresh aliases collapse to the current ask, with audit counts; genuinely unrelated
asks remain. It joins proposed bounded safe WSD projections for needs-human,
idle workstreams and accounts that hit quota limits (ADR rev14). WSD publishes
these immutable redacted projections as private atomic owner snapshots at fixed
configured paths; admind reads them without a WSD callback or DB access. Exact
producer identity, capture generation/time, scope/count/completeness and payload
hash are in the snapshot envelope. Same-UID trust is the convention in §2, not
cryptographic isolation; reject symlinks/foreign ownership/invalid bounds. These
projection ports are activation prerequisites, not existing Plan5 APIs. admind never asks
WSD to send or verify a queued digest; it captures immutable snapshots before
send, and missing sections are explicitly unknown/partial. No global ask-body
endpoint or unscoped disclosure to WSD is introduced. WSD exposes only safe
registered projection summaries to this configured producer; its read consumer
gets no full-card capability. Canonical asks remain included even if no WSD
projection exists. Digest is FYI, not AskPost/MAX_OPEN or NoticePost.

Occurrence is `(configured admind account/group, UTC date)`, due08:00 UTC.
Include ALL current open asks even younger than4h, closed yesterday in[D-1 00:00,
D00:00), needs-human/idle and quota-hit sections with freshness/completeness.
Each source projection is <=100 bounded rows/256KiB, explicit total/unknown and
stable page cursor; a reducer advances at most one bounded page per source per
sweep. Freeze capture watermark/completeness and approved final6A summary/pinned
reference if over20parts/64KiB; never claim 100 full workstreams fit that budget.
Unknown totals/partial pages stay labeled. Catch up latest due date only, record
skipped range; uncertain digest never automatically reposts.

Use integer UTC microseconds and same-boot monotonic/BOOTTIME samples. Retained
Plan8 c9fa bytes are provisional, not latest approved compatibility. Freeze final
clock_ready contract/fixture before use: boot, epoch, synchronization, ready,
persisted UTC high-water. New boot/process begins unready, recovers, gets positive
platform sync and two observations>=30s apart, <=2s elapsed-clock difference and
UTC>=high-water. Check recent samples, not cumulative four-hour drift. Rollback,
lost sync or>2s jump holds new UTC events; recover with that finite observation
rule without resetting keys/deadlines. If behind high-water, wait to catch up.
Existing same-boot monotonic delivery deadlines may still fire; newboot with
unknown time emits fixed local clock-health status, not invented elapsed time.

Due callbacks handle600/660/4h plus nearest retries;30s sweep recovers while
healthy. A reducer selects<=100 sources, prepares<=20 intents and performs<=20
bounded LOCAL operations in5s, with no awaited RPC/native send/backup. Async
preparation, backup barriers and native workers have separate finite bounds;
pending/oldest age remain visible. Proposed Plan8 may wake recovery but cannot
replace seconds-level due callbacks. Library includes no pickup/model policy.

## 7. Shared alerts, storage seals and actual source restoration

### 7.1 Neutral shared local writer

Add a neutral `heterodyne/local_alerts.py` beside fsutil; both WSD/Plan8 writers
and admind's existing alerts reader use its file contract. No WSD→admind package
import. Use the actual `<state>/alerts/<id>.json` contract: exact JSON fields `id`,
`created_at` (RFC3339 UTC), `text`; <=64KiB and existing secret-shape/control
safety checks. Existing admind scanner deduplicates by filename and NEVER cleans;
no installed publisher, settlement or receipt API is claimed. Proposed pure
`publish(intent)` returns written/existing/coalesced/held, and
`settle(namespace, episode_id, expected_hash)` records only the owner-proven local
condition clearing. Intent binds namespace (`d6c` or `p8`), durable episode ID,
created time, safe fixed-profile text and content hash, not arbitrary paths.
Normal filename is namespace + `_` + first32 lowercase hex of SHA-256(canonical
namespace/owner-store-UUID/durable-episode-ID) + `.json`. Full digest stays in
owner state; a short-name collision with different full identity/hash refuses.
Failure and recovery observations use distinct durable sequence/episode IDs;
unknown observation cannot clear a failure. Shared writer never invents an
owner's recovery condition.

Private verified dir, O_EXCL/O_NOFOLLOW0600 temp regular file, write+fsync,
atomic rename, directory fsync. Persist desired filename/hash before write, mark written only
after durability. Crash-after-rename verifies exact bytes; conflict refuses.
ENOSPC/fsync/rename retains desired status, not fake relay receipt.

One SHARED capacity of256 outstanding files across namespaces, including a
reserved fixed coalesced overflow file. Exact episode files occupy at most255;
when full, atomically update reserved `shared_overflow.json` with bounded
counts/oldest/fixed status and service-journal diagnostic, never block WSD
admission or ask delivery on observer capacity. This ONE coalesced file is an
explicit mutable exception to immutable episode bytes; lock serializes updates.
Existing filename dedup means admind may relay its FIRST state only, not later
updates. It is durable LOCAL current state, not a remote freshness guarantee.
No unbounded generation of overflow files to defeat dedup. The owning producer settles its local condition from its own state;
settlement means local condition cleared/retired, NOT remote receipt. A single cross-process directory lock serializes count/admission/rename/settlement
under a finite five-second lock deadline; lock contention retains desired state
for a later bounded wake and never gates task admission. Atomic
bounded settled metadata allows the shared writer/reader cleanup to reclaim
files without any WSD access to admind DB. Settlement metadata is a private atomic dotfile (scanner ignores it), bounded
to the256 occupied slots; it records namespace/episode/hash/settled flag only.
Under the same lock, retire locally settled older exact episode files and their
slot metadata; each owner retains its durable episode sequence/dedup state in
its own store so cleanup cannot recreate an old event. A coalesced owner keeps
latest bounded local status in the shared overflow slot. This is deliberately
allowed BEFORE remote relay, so a short-lived settled diagnostic can disappear
without ever being sent remotely. Relay may already have enqueued an old file:
preserve honest diagnostic history, no unsafe deletion of another namespace or
claim it was delivered. No cleanup requires WSD to inspect admind DB. Coalesced output cannot promise individual episode text at capacity.
This matches Plan8's shared cap/observer policy; final compatible bytes required.

### 7.2 Store identity and backup ordering

Protected startup and absence proof require an independent private recovery
catalog OUTSIDE each delivery DB. It is a small manifest plus consistent source
backups, not a second event log. A separately installed owner receipt pins store
UUID/schema/path; catalog seal binds UUID, monotonic sequence, backup hash,
source-contract identity and recovery epoch. Both are checked BEFORE a creating
Store constructor. Missing DB, wrong UUID, DB sequence behind seal, missing seal
for an enrolled store or hash/schema mismatch is loss/hold, never first install.
DB ahead of seal after a crash may seal from its intact committed state under
exclusive owner recovery lock before any effect; no healthy-absent until aligned.
First initialization/migration is explicit, never inferred from missing files.

Existing WSD Journal.backup/backup_to uses SQLite's online backup API and0600
temp+rename. It is a reuse exemplar, NOT an existing admind backup API or proof
of fsync durability: strengthen/extract a neutral backup helper with file and
directory fsync, no backup inside a DB transaction. Protected databases require
WAL and synchronous=FULL with verified actual settings and fsync-backed storage.
A backup worker serializes with owner admission/effect barriers: commit critical
source/phase/budget sequence, end transaction, make consistent backup, fsync and
rename it, then atomically fsync the catalog seal. Only after seal matches may
an ack or protected effect leave. No lock waits across native I/O; a protected
write barrier can span local backup, not an event-loop blocking call. Timeout
30s and disk failure leave recovery-required with no new effect; old source/
attempt bytes survive. Cross-file atomicity is not claimed: crash between any
steps is resolved by UUID/sequence/hash comparison before resuming.

Apply the source checkpoint barrier to canonical ask lifecycle changes, including
legacy ask rows needed for restore/global inventory. Legacy content/answer policy
stays unchanged; its acknowledgement waits for durable source backup. Back up
canonical ask rows, source association/digest, logical lineage/status,
answers/decisions, immutable render input and protected attempt/budget state,
not just WSD mirrors. Native decision/action effects are never replayed by this
backup. WSD delivery backup holds its own source references/maps; its runtime
Journal remains separately owned. Retain current+previous complete backup and
one in-progress temp; source DB cap256MiB implies <=768MiB backup allocation
plus bounded seal/receipts and DB/WAL reserve. On cap/no space, do not delete
only good backup to admit new effect. Explicit retention/rotation verifies new
seal before old deletion; no unbounded catalog history. Maximum10000 retained
sources/100000 parts/256MiB DB per owner; admission rejection is honest and
visible (§6). Terminal/history compaction requires a reviewed bounded retention
contract and cannot erase live uncertainty or reminder uniqueness silently.

The catalog, backups and installed receipt remain same host/UID failure domain.
They detect isolated DB deletion/stale restoration, not malicious rollback of
ALL anchors or total host loss. They provide no cryptographic service isolation
or independent off-host disaster recovery. Complete loss has no absence proof;
HOLD, local failure and genuine Marmot recovery when available. Do not recreate
unknown asks from titles, native bead text or operator recollection.

### 7.3 Recovery and automatic marked display reissue

Clean restart validates/fences/aligns seals, settles retained phases, recovers
due times and then sends. Preserve corrupt bytes. If DB/source loss is detected,
stop that owner's dispatch. A supported separately authorized restore selects
and verifies the exact catalog/backup and native owner identity, restores source
state under exclusive recovery lock, validates current native lifecycle/digest
where required and records a new sealed recovery epoch. Partial or stale source
that cannot be validated remains unavailable; it does not become a new ask.
Restoration/replacement and new handler deployment use genuine Marmot authority,
not terminal permission or an automatic destructive action.

AFTER valid source restoration, enumerate its genuine current open asks and
sources and AUTOMATICALLY prepare marked `re-issued after recovery` copies in
that epoch (no new human consent per display). This restores ADR3.3 canonical
control cards, not only WSD mirrors: admind has its restored canonical AskRow,
uses protected askr rows and current-copy pointer, preserving ask/decision IDs.
Revalidate live decision facts before any later decision; source unavailable
holds. WSD loss alone reissues informational mirrors from genuine owner snapshots,
not new canonical asks. Unknown old dispatches remain uncertain; marked reissue
is an explicitly approved recovery display policy, not a fabricated receipt or
retry of that attempt. Unknown reminder history becomes consumed. Old targets
stay unknown or superseded until an exact verified mapping exists.

## 8. Closed operator recovery actions

New handlers are proposed compatibility/deployment work, NOT installed commands
and NOT extensions of the frozen6B read consumer. Define typed owner actions
`delivery_recover` and `delivery_join_reissue`, invoked only by a genuine
authenticated Marmot admind decision through a separately admitted command
writer. Existing `!asks repeat` alone does not implement them. Input binds
version1, action, exact logical/source/copy/part IDs, source digest, destination/
registration, expected phase, retained proof hash, recovery request ID and
expiry. No free-form native argv, store path or permission override. Durable
native operator request ID is action dedup key; same ID with changed bytes
refuses. Current operator policy/trust/source must validate at execution; actual
approval/answer audit is retained. Design approval admits implementing this
closed writer, deployment approval admits starting it, per-action decisions
supply only the exact recovery scope, not a blanket native approval.

`delivery_recover` permits one fresh bounded attempt allowance ONLY for a part
whose entire prior automatic history proves non-dispatch and now is exhausted.
Keep SAME stored native key/text/target, preserve all consumed ordinals; the new
attempt record names the authenticated allowance ID (one send per allowance).
No automatic allowance loop and no generic retry of uncertain/issued parts.
An ambiguous old part instead stays held; a specifically authorized marked
reissue may create one new copy generation while retaining that uncertainty and
warning. It cannot change canonical ask consent, reset reminder budget or hide
a prior delivery. Legacy mixed histories without complete proof hold for owner
recovery; human action does not manufacture a phase receipt.

`delivery_join_reissue` authorizes one informational workstream display copy
for an already valid source/destination after fresh trust/lifecycle checks;
no new invite, membership mutation or second answerable canonical ask. Existing
admind protected repeat for the operators' group similarly keeps the same
canonical decision identity. No automatic reissue from membership count or
assumed visibility. Handler absence yields fixed no-action/unsupported, not a
silent route through6B. All questions about unavailable recovery use Marmot
when restored; while it is down retain hold and read-only diagnosis.

## 9. Implementation gates and genuine human design choices

Implementation order: actual design approval; pure helpers/DTO/reducers;
protected Store migration/posting/lookup + neutral durable backup/alert helpers;
separate WSD delivery store/compatible transport guard; exact renderer/source/
projection compatibility; recovery handlers and owner due callbacks; named
offline tests and actual cross-model code review (max3 rounds per change);
separately approved exact deployment/configuration. No runtime code/tests or
live effects are performed by this document. Freeze final6A/6B and producer
protocol bytes before dependent implementation review. Do not invent Plan5
ports or overwrite another design's exhausted review lineage.

Required offline acceptance:

- Real existing Store migration/rollback, protected-post atomic association,
  autobump exclusion BEFORE enqueue, canonical post/approval commit/refresh,
  legacy behavior and canonical answer/decision/native-context integrity.
- Stable ask:/askr: lookup and current-copy roots; foreign/partial/superseded
  target refusal, reply/reaction completeness and one logical decision budget
  across R29 new ask IDs. Missing lineage cannot be guessed.
- Exact scoped DTO/bounds (including16000 non-ASCII chars), all five binding
  fields and epoch; same-UID convention explicitly not security enrollment;
  one snapshot per copy and no guard/TXN held across RPC.
- Immutable generation-free duplicate key conflict, same native key on every
  proven-unissued retry, three total automatic attempts due0/5/30;10s outage
  recovers, longer outage becomes visibly recoverable by fixed exact allowance.
- Pre-open vs writer.write/drain/read ambiguity; every intent/backup/dispatch/
  result crash cut; uncertain never retries; healthy absence only with intact
  seal/fencing; delayed or uncertain head cannot block another eligible row.
- Receipt one-ID policy, bounded raw malformed IDs, cross-copy collisions,
  parent-held instead of top-level fallback, complete copy only, closure/late
  receipt cancellation, redactor changes never silently alter frozen bytes.
- Source intent anchor before trust/snapshot/copy-capacity; rejected-source
  aggregate clock,600/660 exactly once per initial/loss epoch, no per-task
  escalation or NoticePost dependence, local≠remote receipt/independence.
- One4h reminder with unknown consumed and refresh/recovery history retained;
  admind-owned UTC08 digest includes legacy asks, young-open, closed-yesterday,
  quota limits, needs-human/idle, partial/unknown counts and bounded overflow;
  latest-only catch-up and unchanged time keys across reboot/rollback.
- Shared neutral writer cap/coalescing without admission gating; crash/fsync/
  ENOSPC/conflict/namespace/settlement without remote receipt requirement.
- Verified SQLite synchronous/WAL settings, fsync-backed file tests and actual
  consistent backup/restore for canonical sources AND delivery phases; wrong
  UUID/deleted/stale DB, crash before/after seal, backup failure, isolated loss
  vs total-anchor loss, unknown old target and automatic marked canonical reissue
  after explicit valid restoration without decision/task replay.
- Fixed recovery action authentication/dedup/revocation/phase scope, no allowance
  reset by source revision; unsupported6B mutation path remains fixed no-action;
  preserve claims/session/native/group history.

Meaningful mutation gates are REQUIRED: removing intent/seal-before-effect,
complete-copy checking, no-retry-after-issued, source revision binding or
single-reminder uniqueness must make named assertions fail. Record the exact
mutants/tests and baseline results; mocks alone are not persistence proof.
Runtime integration and live delivery/receipt evidence require their genuine
deployment authority and are distinct from offline PASS.

The published Marmot design card must expose these choices/departures plainly:
route partition; admind-only canonical decisions and informational mirrors;
new atomic protected posting/refresh/current-copy compatibility; trusted same-UID
producer convention (not authenticated enrollment); snapshot race and native
revalidation; stable native keys/separate attempts and0/5/30 three-attempt timing;
fixed authenticated recovery writer with retained budgets; one logical identity
across ask refresh; finite initial/loss escalation episodes; accepted-source
intent anchor and rejected-capacity aggregate qualification; local 660s durability
WITHOUT guaranteed independent remote ADR6.2 route; no task-card escalation;
one reminder per decision/task with unknown consumed after loss; admind-owned all-ask UTC08 digest
including quota; neutral shared alert cap/coalescing; same-host backup/seal trust,
explicit source restoration before automatic marked reissue, and HOLD when
sources/anchors cannot be proven. No approval of these choices is claimed here.
If refused, revise this same lineage before implementation; do not silently
weaken frozen6B, change native decision semantics or create another review reset.
