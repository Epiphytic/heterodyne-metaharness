# Plan 6C: durable Marmot delivery and recovery — d4

Design task `btq-w2jti`. Codex author, 2026-10-16. Proposed and unapproved.
Historical d1, d2 and signed d3 `4acc2d5a2d6ee3e2266ff465271a72c419493723`
and their exact input manifests/dispositions remain unchanged. Actual Claude
rounds returned REVISE33, REVISE27 and FINAL REVISE15 respectively. All THREE
issued rounds are consumed. This separate d4 is an UNREVIEWED author repair,
not an approval, implementation grant or reset. Any additional review requires
a genuine Marmot exception on these exact published bytes. R3's introductory
three blockers do not discard its other twelve findings.

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
new/changed design choices and deployment retain genuine approval. This d4
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
opened time, and optional predecessor ask ID. The initial controller remains the genuine ask
producer; no arbitrary WSD task renderer originates asks. This is a NEW versioned
protected-post request, not an added assumed field on legacy AskPost:
`producer_intent_id` is 32 lowercase hex generated and durably saved by the
caller BEFORE its first attempt. Its uniqueness domain is configured producer
binding plus source kind. The daemon derives/persists a logical decision ID on
first acceptance and returns both IDs and ask ID. Same intent plus identical
canonical input hash returns that same result after an acknowledgement timeout;
changed input under that intent refuses. New independent decisions after terminal
closure need a NEW durable producer intent; a retry never invents one. A refresh
names its predecessor and does not allocate a new logical decision ID. Retain
intent tombstones with the logical source's retained history; never evict an
intent while its source or an ambiguous caller acknowledgement is live. Capacity
refuses new admission before allocating/acknowledging any new ask.

Under the existing owner serialization, validate the input and immutable source
reference, then atomically insert the canonical row, scope association, logical
identity, protected policy/version, autobump exclusion, frozen original parts,
delivery intent/anchor and outbox rows. All or none: no legacy enqueue followed
by upgrade. Complete the bounded recovery-log durability barrier (§7) before acknowledging or allowing a send.
Repeated identical post intent returns the same ask; conflicting bytes refuse.
A new protected refresh atomically closes/supersedes its predecessor with protected bookkeeping and preserves logical identity and reminder/episode
budgets. At BOTH legacy post and protected commit_approval entry points, inspect
same-bead active asks under ask_post_lock and the Store transaction. If any is
protected, a legacy or unrelated new-intent post REFUSES before superseding any
row. Only an exact protected predecessor refresh may retire it, carrying all
protected associations and budgets. Other deciding/uncertain native fences remain.
After genuinely terminal closure, a fresh independent protected intent is allowed.
This deliberately fences legacy same-bead replacement; it does not infer lineage
from bead/title or silently change existing ask consent or digest.

R29 is a daemon-internal refresh, not a controller re-post. It inherits the exact
protected predecessor association, logical identity and original producer binding.
Its deterministic internal intent is SHA-256 of version/store UUID/predecessor/
current native attempt message ID/new context digest. It is recorded inside the
existing no-await decision-closing transaction, together with the new ask,
protected askd rows and attempt reply. Every associated outgoing row is ineligible
until that transaction's recovery sequence is durable/applied. No await or backup
is inserted inside the transaction, and peer_pid=None is not a new peer identity.
The worker awaits the local barrier AFTER committing and BEFORE releasing those
rows to send; a crash resumes from the retained sequence and never replays the
native approve-bead action.

Legacy unassociated asks retain their existing answer pipeline and are included
in the admind global digest. They are not advertised as protected, mirrored, or
quietly upgraded. Migrating any existing open ask requires a genuine owner
association and an atomic fenced migration with no pending legacy sender;
otherwise it remains legacy. No deployment may claim all asks are protected
until the actual producer paths, including approval commit/refresh, use this
variant. Protected asks must be excluded from legacy autobump in that same
transaction, and protected `!asks repeat` must route to the protected copy
policy rather than emit unmanaged legacy rows. Protected `!asks bump` also uses
that same single reminder reservation: before four hours it returns not-due;
afterward it reserves the ONE reminder if eligible, otherwise returns status.
It never creates legacy asknote bump rows or consumes a second reminder. An
explicit marked full-card repeat is a display copy, labeled as requested and
not a new automatic reminder budget.

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
recovery seal; startup never silently creates a missing previously initialized
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
copies while allowing an explicit marked reissue. Copy record binds random 32-hex ID and ordered parts hash. Canonical control
cards/details MUST retain actual legacy admission and exact complete text: no
6A mirror summary may replace an approval readout. Separate copy profiles:
`canonical-v1` permits 1–1024 frozen parts, <=16KiB UTF-8 per part and <=512KiB
total final UTF-8, including framing; `display-v1` permits 1–20 parts/64KiB for
mirrors/digest summaries. Both obey actual configured chunk_chars/native frame
bounds. 1024 parts/512KiB conservatively cover the native 24000-character
approval readout and 16000-character question with framing at chunk_chars=200;
implementation MUST measure worst-case Unicode/atomic-link packing over EVERY
supported chunk_chars before enabling protected canonical posting. A fixture
exceeding this profile with an otherwise legacy-admitted canonical card blocks
activation and requires correcting the profile, not truncating or withholding
that ordinary admitted card. No dynamic rechunking. Unsupported oversized new
external DTOs refuse before ask creation with exact safe reason; no partial card.

**Native part key is stable across all permitted proven-unissued retries.**
For canonical initial/refresh cards retain `ask:<ask_id>:<part_index>` so existing
ask lookup/completeness conventions remain usable. Protected marked repeats use
existing `askr:<ask_id>:<request_id>:<part_index>` grammar and `ask_repeats` rows;
request_id is a persisted 64-lowercase-hex owner request digest (matching
the actual _CARD_KEY regex), not an attempt ordinal. The ask ID retains the
actual four-character [2-9a-hjkmnp-z] alphabet. Extend
protected current-copy lookup explicitly: current protected generation, not
`_first_full_repeat`'s earliest historical full repeat. Protected reply gates require full current copy/context/native binding before
answering. This includes EVERY askd:<ask_id>:<inbound_mid>:<part_index> source,
both !details and R29 updated readout in the stale target thread. Such details
are protected supplementary copies tied to exact logical ID/current ask/context,
frozen complete parts and native inbound request ID (the copy idempotency key).
A complete supplementary copy of the CURRENT context is answerable only if the
current canonical control copy is itself fully delivered; otherwise status/pointer
only. Partial, old-context and superseded askd targets cannot decide. Preserve
ask_details linkage and actual key grammar; never send protected askd through
legacy redact/retry/ID[0]/thread fallback. No unregistered askd producer bypass. Preserve
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
| reserved | Exact attempt committed/durably logged, no native write. Busy/unavailable pre-dispatch condition holds the SAME attempt. Fenced proof can establish unissued. |
| dispatch-marked | Commit/recovery-log barrier before writer.write. Crash without positive proof becomes uncertain. |
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
cannot do this. Persist dispatch-mark through the bounded recovery-log barrier before first write. Any
write/drain/response timeout, malformed reply or cancellation after write begins
is uncertain. A well-formed PeerError AFTER request write is ALSO uncertain,
regardless of its code or retryable flag, unless the exact deployed connector's
specific branch is independently proven pre-effect. No such post-write proof is
admitted by this design. The inspected mdk-fork commit37c248a source is NOT a
claim about the live connector: messaging.send_final_response calls the runtime
send/reply before propagating errors and only caches successful results;
connection.error_response projects that error to a frame. error.code/retryable
therefore is not a reliable non-dispatch certificate. In particular
relay_unavailable/rate_limited/unavailable/message_send_failed, generic App/IO
errors and send_in_progress never authorize automatic retries. Source inspection
and a disposable failure-injection trace must bind the actual deployed binary
before any future proof allowlist can be proposed. A positive final receipt may
settle uncertainty; ordinary health/group_info success cannot. This conservative
uncertainty is visible and recoverable via one bounded episode-scoped marked
reissue decision (§8), not blind replay of an old native send.
Each native call has30 seconds TOTAL including cleanup, not30 per phase. A prepared outbox intent belongs to the async owner; the five-second
reducer neither waits for nor abandons its native send.

Under the proposed compatible 6B WSD transport guard, verify registration/trust
epoch and local frozen token, reserve/durably record, then dispatch. Membership transitions
must fence/drain that guard. This WSD guard/transport DOES NOT exist at pinned
product076; it is a required compatible implementation, not borrowed current
functionality. admind similarly integrates protected dispatch into its actual
owner serialization. No SQLite transaction crosses network I/O.

Healthy-absent needs intact initialized UUID, verified seal/frontier (§7),
exclusive admission/dispatch lookup lock and no matching reservation. Every send
requires a committed/recovery-logged prior record. Reserved-no-dispatch needs a fenced
old sender and no dispatch mark. Missing/corrupt/stale/unvalidated store,
incomplete backup recovery, owner outage or replaced identity cannot prove
absence. Native inspect never sends; not_found alone never authorizes retry.

Reuse existing Store delayed/not_before, hold_group, release_delayed and
outbox_pass ownership. Do NOT create a parallel durable timer engine. Add indexed
protected phase/due metadata as a projection of that SAME delivery-group delay
state. The owner transaction sets group delayed/not_before plus protected phase;
extend release_delayed to release a protected group ONLY if its phase is eligible,
its own deadline has passed and parent/source/trust predicates still permit it.
Uncertain, quarantined, exhausted or parent-held groups cannot be released merely
by wall-clock time. Preserve an existing parent-held deadline; no second owner
rewrites it. Existing legacy notice cadence remains unchanged. Select eligible
pending groups fairly after releasing due groups; skip held heads instead of
returning early. Preserve ordering within each copy and serialized binding sends.
Current in-memory _backoff becomes the projection of the protected durable delay,
not an independent retry budget. Owner wakes at nearest delayed deadline and
30s recovery sweep; phase selection reserves under the same Store lock/TXN.
No sleeping reducer, parallel release callback or Plan8 five-minute retry engine.

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
For retained old message IDs with a verified source/logical association, recovery
resolves the new complete current root and queues ONE deduplicated FYI pointer
per authenticated inbound request: old target superseded; answer at new root.
While its new root is incomplete, say recovery pending and name the exact known
canonical ask without fabricating a native target. That pointer is not an answer
or decision. Truly unknown targets remain unknown, never new intake after map
loss; a bounded fixed reply says the old card cannot be resolved and directs the
operator to current !asks inventory, without inventing a source-specific pointer.
ADR3.3's unconditional old-card pointer is explicitly qualified when ALL exact
mapping evidence was lost; this departure needs human design acceptance. No
resolver result itself writes a decision or steering effect.

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
Before clock_ready, NEW protected posts return typed retryable clock-unready
BEFORE any ask/source/UTC anchor is accepted or written; the caller retains the
SAME producer_intent_id/input for retry after readiness, without a new operator
approval. Acknowledged old intents preserve their original anchors; same-boot
known monotonic deadlines can continue. Health exposes rejected-admission count,
not fictional per-card600s clocks during the unready interval. This finite
startup admission delay is an explicit disclosed availability qualification.

Due callbacks handle600/660/4h plus nearest retries;30s sweep recovers while
healthy. A reducer selects<=100 sources, prepares<=20 intents and performs<=20
bounded LOCAL operations in5s, with no awaited RPC/native send/backup. Async
preparation, recovery-log/checkpoint barriers and native workers have separate finite bounds;
pending/oldest age remain visible. Proposed Plan8 may wake recovery but cannot
replace seconds-level due callbacks. Library includes no pickup/model policy.

## 7. Shared alerts, storage seals and actual source restoration

### 7.1 Neutral shared local writer

Add a neutral `heterodyne/local_alerts.py` beside fsutil; both WSD/Plan8 writers
and admind's existing alerts reader use its file contract. admind ALSO writes
its canonical660s failures as a d6c producer, binding its initialized admind Store
UUID/logical episode to the configured shared alerts directory. It participates
in the SAME cross-process lock/cap/settlement protocol as WSD/Plan8. The configured
directory capability is a deployment prerequisite; no inferred relative sibling
path or unreviewed WSD-to-admind import. Distinct owner UUIDs prevent collisions. No WSD→admind package
import. Use the actual `<state>/alerts/<id>.json` contract: exact JSON fields `id`,
`created_at` (RFC3339 UTC), `text`; <=64KiB and existing secret-shape/control
safety checks. Existing admind scanner deduplicates by filename and NEVER cleans;
no installed publisher, settlement or receipt API is claimed. The proposed shared API/schema is `local-alert-v1`, not installed behavior.
`publish(intent)` returns written/existing/coalesced/held. Intent has exact fields:
version=1, namespace=d6c|p8, owner_store_uuid=32 lowercase hex,
owner_generation (positive integer), durable episode_id (1–128 ASCII bytes),
kind=failure|recovery|retirement, created_at (RFC3339 UTC), safe text and SHA-256
of its complete alert JSON bytes. No arbitrary paths. Full identity is namespace/
owner UUID/generation/episode. `settle(namespace, owner_uuid, generation,
episode_id, expected_hash, disposition)` admits only disposition=cleared|retired|
published. `cleared` requires the OWNER's known-clear predicate; unknown never
means recovered. `retired` requires exact owner/generation retirement proof and
means scope ended, NOT successful recovery. `published` is only for recovery/
retirement notice kinds AFTER publish verified atomic durable bytes; those
one-shot notices locally settle then, not forever awaiting absent relay receipts.
Same-ID/hash repeat is idempotent; mismatch holds. Old-generation episode slots
must be settled with truthful retired status when that exact generation retires,
not mislabeled recovery. Owner retains durable sequence/dedup evidence before
settlement so local cleanup cannot recreate the episode.

Under the SAME five-second directory lock, private atomic metadata version1 has
at most255 exact occupied entries with exact owner UUID/generation/namespace/
episode/kind/filename/full JSON hash/settled disposition-or-null; one fixed
overflow status slot, and two fixed namespace aggregate records (d6c,p8) containing
saturating uint64 coalesced/retired counts and oldest-known timestamp-or-unknown.
No per-retired-owner unbounded list, dynamic code or SQL. Counting, exact-byte
checks, publish, settlement and cleanup all use this single versioned schema.
Final jointly reviewed bytes/fixtures must resolve any differing sibling draft
before shared activation; this draft does not declare stale Plan8/6C agreement.
Coordinated Plan8 d4 proposal (not an approved sibling capability): its helper
owner_store_uuid is the durable observer epoch UUID (32 lowercase hex), and
owner_generation is1 for that epoch, NOT the installation configuration UUID.
Its episode_id is the full64hex SHA-256 of its closed canonical tuple including
opening configuration generation, code, durable sequence and phase. A later
configuration switch does not retire that observer epoch or clear an episode:
retain it until the actual predicate is false; unknown retains it. Observer-only
state loss creates a fresh durable UUID epoch only under Plan8's explicitly
proposed human choice, with a fixed reset notice kind=retirement, settled as
published after durable publication. Unknown old files remain untouched; this
residual does not reset effect/native authority. The old-generation retirement
rule above applies only when that exact HELPER owner generation is proven ended,
not merely when configuration changes. Final jointly reviewed bytes/fixtures
must implement these exact mappings, or shared activation remains withheld.

Identity bytes are UTF-8 canonical JSON array
`[1,namespace,owner_store_uuid,owner_generation,episode_id]`, compact separators
comma/colon, ensure_ascii=false, no newline. Normal filename is namespace + `_`
+ first32 lowercase hex of SHA-256 of exactly those bytes + `.json`. Full digest
stays in owner state; a short-name collision with different full identity/hash
refuses. Complete alert JSON has ONLY `{id,created_at,text}`, sorted keys, compact
comma/colon separators, ensure_ascii=false, UTF-8 with NO final newline. The
full alert hash is SHA-256 of exactly those file bytes. These shared byte rules
are proposed; jointly reviewed byte fixtures are required before activation,
not a claim of installed serialization support.
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
Existing filename dedup means admind may relay its FIRST state ONCE PER DATABASE LIFETIME only, not later
updates or a later week's overflow episode. It is durable LOCAL current state, not a remote freshness guarantee.
No unbounded generation of overflow files to defeat dedup. The owning producer settles its local condition from its own state;
settlement means local condition cleared/retired or a one-shot recovery/retirement notice durably published, NOT remote receipt. A single cross-process directory lock serializes count/admission/rename/settlement
under a finite five-second lock deadline; lock contention retains desired state
for a later bounded wake and never gates task admission. Atomic
bounded settled metadata allows the shared writer/reader cleanup to reclaim
files without any WSD access to admind DB. Settlement metadata is a private atomic dotfile (scanner ignores it), bounded
to the256 occupied slots; it records the exact bounded local-alert-v1 slot fields above.
Under the same lock, retire locally settled older exact episode files and their
slot metadata; each owner retains its durable episode sequence/dedup state in
its own store so cleanup cannot recreate an old event. A coalesced owner keeps
latest bounded local status in the shared overflow slot. This is deliberately
allowed BEFORE remote relay, so a short-lived settled diagnostic can disappear
without ever being sent remotely. Relay may already have enqueued an old file:
preserve honest diagnostic history, no unsafe deletion of another namespace or
claim it was delivered. No cleanup requires WSD to inspect admind DB. Coalesced output cannot promise individual episode text at capacity.
This is the proposed6C policy, NOT a claim of compatibility with pinned Plan8 d1
c9fa or d2 09411b3: their admission/drain policies differ. Final jointly compatible
reviewed/human-approved helper bytes and fixtures are a blocking prerequisite for
shared activation. Until then no cross-owner shared writer starts. The mutable
fixed overflow status is explicitly LOCAL-only after its first lifetime relay;
no remote overflow re-notification guarantee or per-episode relay promise. Each
owner retains latest coalesced state for health/digest, not an unbounded spool.

### 7.2 Bounded recovery log and periodic checkpoints

The independent private recovery directory is OUTSIDE each delivery SQLite DB,
under the same trusted host/UID. It contains an installed enrollment receipt
(store UUID/schema/path), a sealed full SQLite checkpoint, a bounded append-only
recovery log and an atomic frontier manifest. These are NEW helpers, not installed
Store/SQLite capabilities. Source baseline shows ordinary SQLite Store transactions,
WSD's online backup exemplar and agents.base.write_at private atomic/fsync file helper; no SQLite
session extension, magic WAL copying or existing change capture is assumed.

Do NOT full-copy256MiB before every send. Proposed `RecoveryDeltaV1` is a finite
DATA-only record: store UUID, monotonic seq, transaction ID, previous record hash,
versioned schema ID, and ordered row changes `(allowlisted table, primary key,
before-row hash-or-absent, complete after-row-or-delete)`, including necessary
sqlite_sequence values. Exact allowed table/column/type maps are generated from
and reviewed with the Store migration; no SQL/argv/path payloads. ALL Store
writers (including legacy asks/answers/inbound replay protection and outbox/
ownership rows) must use the shared transaction adapter and record their exact
row changes. Triggers/cascades/index semantics must be covered by the schema
adapter; an untracked writer means activation FAILS, not partial recovery proof.
This is a real implementation prerequisite, not a current Store method.

Under the owner writer lock: prepare changes/validate prestate; encode <=4MiB
record; append length+hash-framed record and fsync the recovery log; apply its
exact row changes and recovery_seq atomically to SQLite; commit FULL synchronous;
only then acknowledge or expose sends. The log is authoritative for that DATA
transaction once its complete record is durable; a crash before SQLite commit
replays its after-images under the recovery lock. Prestate mismatch refuses;
a matching after-state/seq is already applied. No native action/approve-bead/send
is replayed by applying data. A durable dispatch mark is uncertainty after crash,
even if no real send occurred. A complete valid tail beyond a lagging manifest is first fsynced/validated and
adopted under the exclusive recovery lock before SQLite alignment; it may reflect
an accepted intent whose reply was lost, so producer idempotency still applies.
Incomplete trailing frame is discarded only when
provably beyond the fsynced manifest frontier; any ambiguous gap/hash conflict
holds. Frontier publication/fsync follows log fsync; no external ack/effect until
both and the SQLite commit succeed. Failure retains exact bytes, no claimed ack.
The adapter must prevent continuing from a partially durable unknown frontier.

A checkpoint worker uses a SEPARATE read-only SQLite WAL connection, opened
mode=ro (not immutable, so live WAL is included), never Store's creating writer.
Validate existing WAL mode and exact installed source identity without modifying
it. Execute BEGIN followed by an actual schema/data SELECT to establish a stable
read snapshot BEFORE backup(); retain that explicit READ transaction through all
copy steps, then rollback/close in finally. No destination transaction is opened
before backup. Concurrent writer commits cannot restart this pinned copy from
page0. Refuse a non-WAL source for this route. No WRITER transaction, owner writer
lock or daemon connection/RLock spans copying, hashing, fsync or network I/O.
Capture the exact applied recovery sequence from the copied snapshot, retain
every log delta after that sequence, verify backup/hash/schema/UUID, fsync files
and directories, publish new checkpoint/frontier atomically, THEN retire covered
log segments. No mutation through the source backup connection.

Use pages=128 with a progress callback enforcing a30-minute monotonic deadline,
SQLite busy timeout<=5s and stop/cancel checks. Abort if source WAL growth since
snapshot start exceeds256MiB or filesystem free space falls below the installed
safety floor (at least512MiB). A read snapshot can pin WAL pages; this explicit
limit bounds that residual and reports checkpoint failure, never bypasses the
log cap or deletes uncovered records. Callback failure closes both private
read/copy connections and retains the prior verified recovery set. Do not
start another copy until the prior worker is positively gone and ownership
fencing is reacquired. This matches the proposed Plan8 bounded snapshot
mechanism, not a claim of installed shared checkpoint code. Reuse/strengthen
Journal.backup_to's private temporary-copy pattern and agents.base.write_at's
file/directory fsync pattern (fsutil.private_dir only hardens directories);
neither currently supplies this whole protocol.

Log hard cap64MiB; trigger checkpoint at32MiB or50 records (whichever first),
with at most one checkpoint worker. Rotation never drops uncovered deltas. On
cap/failed checkpoint, hold new data transactions/effects visibly; do not delete
the only validated checkpoint. Bound each delta4MiB and each local barrier30s;
a typical part changes only bounded rows, not the full history. Admission checks
canonical worst cases before starting a transaction; bulk legacy migration/
redaction exceeding4MiB must finish as explicitly bounded crash-safe chunks
BEFORE enrolling protected delivery. No silent transaction splitting of ask+
parts/source association. Retain current+previous verified full checkpoint and
one temporary copy (<=768MiB at256MiB DB cap) plus64MiB log and at most256MiB additional pinned WAL growth; record actual resource
bounds before deployment.10000 sources/100000 parts/256MiB remain admission caps.
Offline real-SQLite crash-cut tests MUST prove all Store write paths, foreign-key
state and sqlite_sequence replay, checkpoint/log races and bounded per-part I/O.
The claimed performance improvement is bounded write size, not measured latency.

Healthy-absent proof requires intact enrollment UUID, validated checkpoint/log
frontier, complete contiguous sequence, exclusive recovery/dispatch lock and no
matching intent/reservation. Missing/corrupt/stale anchors never prove absence.
DB ahead/behind only resumes after exact deterministic data alignment. Unknown
same-UID tampering/rollback of ALL anchors or host loss is outside this same-host
trust model; no cryptographic isolation or off-host disaster recovery is claimed.
No unknown ask is reconstructed from titles, bead text or operator recollection.

### 7.3 Automatic startup restoration and marked display reissue

Enrollment/designed deployment authorizes the following bounded startup recovery;
no NEW Marmot decision is required to restore an exact validated SAME-identity
checkpoint plus complete recovery log. This is a newly disclosed design choice.
BEFORE calling Store's creating constructor, acquire exclusive owner recovery
lock, validate enrollment path/UUID/schema and all checkpoint/log hashes/frontier,
preserve missing/corrupt/stale original DB/WAL/SHM bytes in private quarantine,
restore into an exclusive private temporary DB, replay DATA deltas, verify exact
UUID/sequence/schema/foreign keys, fsync files/directory and atomically install.
No source identity/policy/group change, native action or unknown decision replay.
Startup must resume ordinary authenticated Marmot ingress after successful data
recovery; restoration cannot depend on asking the broken daemon for permission.
Repeated crash is idempotent via recovery transaction ID/frontier. A lock timeout,
unknown/missing/gapped log, wrong identity, unsupported schema or hash mismatch
holds; do not create a blank Store or overwrite the only backup. Total anchor
loss remains an explicit unsupported disaster; genuine Marmot recovery through
an intact separately admitted route is required when available, never terminal
consent. A later changed-identity/manual restoration is outside this automatic path.

After successful recovery, allocate/fsync a new recovery epoch ONCE, enumerate
known current open canonical asks and AUTOMATICALLY prepare marked `re-issued
after recovery` copies within that epoch. Preserve logical IDs, ask IDs, consent,
reminder budgets and original anchors. Use protected askr and current pointer;
WSD mirror loss alone cannot create canonical asks. Validate current source/native
facts before accepting a later decision; a source whose lifecycle cannot be
verified stays unavailable. Unknown old dispatches remain uncertain, while the
marked NEW display is an explicitly approved loss-recovery policy, not retry or
fabricated receipt. Unknown reminder history is consumed. Old known targets get
the §5 pointer; truly unknown mapping gets the disclosed safe inventory response.

## 8. Closed operator recovery actions

New handlers are proposed compatibility/deployment work, NOT installed commands
and NOT extensions of the frozen6B read consumer. Define typed owner actions
`delivery_recover` and `delivery_join_reissue`, invoked only by a genuine
authenticated Marmot admind decision through a separately admitted command
writer. Existing `!asks repeat` alone does not implement them. Input binds
version1, action, exact logical/source/copy IDs plus a finite part manifest, source digest, destination/
registration, expected phase, retained proof hash, recovery request ID and
expiry. No free-form native argv, store path or permission override. Durable
native operator request ID is action dedup key; same ID with changed bytes
refuses. Current operator policy/trust/source must validate at execution; actual
approval/answer audit is retained. Design approval admits implementing this
closed writer, deployment approval admits starting it, per-action decisions
supply only the exact recovery scope, not a blanket native approval.

`delivery_recover` is one EPISODE-scoped authenticated decision, not one new
card per part. The owner first freezes an immutable manifest (version, store UUID,
random recovery episode ID, generation, <=1000 exact copy/part IDs and current
phase/proof/source/destination hashes, action per entry, created/expiry<=1h,
manifest hash). All included sources must belong to the SAME configured route
and owner; exclusions/unknowns are explicit counts. A short self-contained Marmot
card links exact immutable safe descriptor bytes under the real supported public
reference contract and discloses counts/duplicate risk; without accessible full
scope, refuse that batch (never invent !details/host retrieval). Native command
recovery with the same full inline bounded scope is allowed only through the
separately reviewed authenticated Marmot writer. Proposed command pair
`!delivery recovery <episode_id>` and `!delivery recover <episode_id> <choice>`
selects only the owner's frozen displayed manifest; no caller-supplied path,
arbitrary hash, glob, part wildcard or automatic scope expansion. The first
command renders the complete safe scope (or exact accessible immutable link)
with explicit A=perform listed recovery, B=leave held. The subsequent reply or
second command is accepted only from a current authenticated policy operator in
that SAME registered thread after the complete scope has been delivered. Record
native request/answer message IDs and exact manifest hash. This is a PROPOSED
closed delivery writer, not installed !answer/native approval functionality.
It is not AskPost and does not consume MAX_OPEN, so20 outstanding asks cannot
block their recovery; its bounded status/scope copy has independent command
request identity and no automatic recovery-of-recovery loop. No native task/
approval decision is made. No terminal authority. One A
binds this exact hash and at most one execution per entry; before each effect,
current policy/source/trust/phase must still match or that entry holds. Changed
facts require another explicit scope, not an expanded allowance.

For proven-nondispatch exhausted entries, it permits ONE fresh bounded attempt
allowance each; the whole prior automatic history must prove non-dispatch.
Keep SAME stored native key/text/target, preserve all consumed ordinals; the new
attempt record names the authenticated allowance ID (one send per exact manifest entry).
No automatic allowance loop and no generic retry of uncertain/issued parts.
An ambiguous old part instead stays held; the SAME episode decision may explicitly authorize one NEW marked full copy
per uncertain copy, retaining all old uncertainty and a visible possible-duplicate
warning; never retry the ambiguous attempt or classify PeerError as unsent.
Entries for the same copy collapse to one marked copy, not one per old part.
New copies preserve logical reminder/escalation budgets; superseded source or
native deciding/uncertain approval action fences still hold. Recovery-scope card
itself is an explicit authenticated command interaction on the now-healthy
route, not a disguised reset of an old failed card or an AskPost capacity bypass
for general questions. If this route is still down, remain held/local diagnostic;
no promised remote recovery while the sole transport is unavailable. It cannot change canonical ask consent, reset reminder budget or hide
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
protected Store migration/posting/lookup + bounded recovery-log/checkpoint/neutral alert helpers;
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
  legacy same-bead collision refusal, internal R29 association inheritance and
  canonical answer/decision/native-context integrity; lost acknowledgement retries
  reuse durable producer intent, a later genuine independent decision does not.
- Stable ask:/askr:/askd: lookup and current-copy roots; foreign/partial/superseded
  target refusal, reply/reaction completeness and one logical decision budget
  across R29 new ask IDs. Missing lineage cannot be guessed.
- Exact scoped DTO/bounds (including16000 non-ASCII chars), all five binding
  fields and epoch; same-UID convention explicitly not security enrollment;
  one snapshot per copy and no guard/TXN held across RPC; canonical24000-character
  Unicode readout at minimum chunk size fully delivered without summary/truncation.
- Immutable generation-free duplicate key conflict, same native key on every
  proven-unissued retry, three total automatic attempts due0/5/30;10s outage
  recovers, longer outage becomes visibly recoverable by ONE finite episode-scoped decision.
- Pre-open vs writer.write/drain/read ambiguity; every intent/recovery-log/checkpoint/dispatch/
  result crash cut; uncertain never retries; healthy absence only with intact
  seal/fencing; delayed or uncertain head cannot block another eligible row.
- Receipt one-ID policy, bounded raw malformed IDs, cross-copy collisions,
  parent-held instead of top-level fallback, complete copy only, closure/late
  receipt cancellation, protected !details and !asks bump routes, redactor changes never silently alter frozen bytes.
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
  after automatic validated same-identity restoration without decision/task replay.
- Fixed recovery action authentication/dedup/revocation/phase scope, no allowance
  reset by source revision; unsupported6B mutation path remains fixed no-action;
  preserve claims/session/native/group history; one bounded recovery manifest for
 20 exhausted parts,20 active asks do not block recovery-scope command, no unknown
 part auto-replay. Clock-unready new post creates neither source nor UTC anchor.

Source audit anchors (exact manifests resolve repositories, never implied live pins):
product076 daemon.commit_approval (same-bead supersession), internal refresh and
askd producers; Store.hold_group/release_delayed (existing timer mechanism),
Store constructor/transaction (creating WAL DB, no change capture),
asks.MAX_APPROVAL_CARD/MAX_BODY and chunk packing; control.decode_head/call
(PeerError and write boundary); alerts filename dedup; Journal.backup_to and
agents.base.write_at. Inspected mdk37c248a connection.handle_request/error_response,
messaging.send_final_response and error projections establish that generic
post-write error frames lack a non-dispatch proof. The local whitenoise-rs tree
95fa0b85 is a different source layout, not proof of the deployed wn-agent;
actual deployed connector equivalence remains unverified and no error allowlist
is enabled on that basis.

Meaningful mutation gates are REQUIRED: removing intent/seal-before-effect,
complete-copy checking, no-retry-after-issued, source revision binding or
single-reminder uniqueness must make named assertions fail. Record the exact
mutants/tests and baseline results; mocks alone are not persistence proof.
Runtime integration and live delivery/receipt evidence require their genuine
deployment authority and are distinct from offline PASS.

The published Marmot design card must expose these choices/departures plainly:
route partition; admind-only canonical decisions and informational mirrors;
new atomic protected posting/refresh/current-copy/details compatibility and legacy same-bead collision refusal; trusted same-UID
producer convention (not authenticated enrollment); snapshot race and native
revalidation; stable native keys/separate attempts and0/5/30 three-attempt timing;
fixed episode-scoped authenticated recovery writer with retained budgets/possible-duplicate disclosure; one logical identity
across ask refresh; finite initial/loss escalation episodes; accepted-source
intent anchor and rejected-capacity aggregate qualification; local 660s durability
WITHOUT guaranteed independent remote ADR6.2 route; no task-card escalation;
one reminder per decision/task with unknown consumed after loss; admind-owned all-ask UTC08 digest
including quota; neutral shared alert cap/coalescing; same-host backup/seal trust,
bounded data-only recovery log/periodic checkpoints; automatic validated SAME-identity
startup restoration without a new broken-daemon permission card; clock-unready
new-post refusal; protected canonical profile preserving actual native admission;
local-only lifetime overflow diagnostic; qualified unknown-old-target inventory
response; source restoration before automatic marked reissue, and HOLD when
sources/anchors cannot be proven. No approval of these choices is claimed here.
If refused, revise this same lineage before implementation; do not silently
weaken frozen6B, change native decision semantics or create another review reset.
