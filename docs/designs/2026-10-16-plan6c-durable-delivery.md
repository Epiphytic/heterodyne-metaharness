# Plan 6C: durable Marmot delivery and recovery

Design task `btq-w2jti`. Codex author, 2026-10-16. Proposed, unreviewed;
no implementation or deployment approval. This is a delivery contract, not a
second approval writer. All operator questions and approval requests continue
through Marmot using admind. No terminal-consent fallback is introduced.

## 1. Authority, scope and prerequisites

Current governing ADR17 is `a27acecee8274e6ad8dc65def436614d42b5cb04`,
`docs/adr/0001-workstreams-v2.md`, genuinely approved on `btq-ovngv` by Liam
at 2026-10-14T15:57:38Z, Marmot reference `189fd6bb97ba`. Earlier 586 revision
is historical. The ADR's draft-status prose is historical; native approval is
the decision evidence. The [source manifest](../reviews/plan6c-delivery-inputs.json)
pins source bytes and the native approval snapshot used for this proposal.

ADR sections 3.3 and 6.1–6.2 require derived message maps, durable receipts,
workstream task threads, control mirrors, one four-hour reminder then daily
digests, ten-minute delivery escalation, and reissued open cards after journal
loss. Native task/approval/decision state stays in Beads. Receipts never mean
read, consent, task launch, or committed decision. `wsd.journal.receipt_put`
is a launch receipt and must not be reused for message delivery.

Implement a small delivery package under `wsd`, journal tables, and an explicit
admind delivery bridge. The bridge is necessary because current admind is bound
to one group and its retry algorithm does not prove non-delivery after timeout.
It is a proposed interface, not an existing callable API. Reuse native
`ControlClient.send_final` through admind; wsd gets no separate control socket
or credential. Existing admind asks, their decisions and interim bumps remain
unchanged until an independently approved cutover. No automatic migration of
open asks between two decision writers.

Production activation requires compatible approved and implemented Plan6A
rendering, Plan6B group registration/trust, and the genuine Plan5/6D owner
contract for any approval/question copy. Drafts 6A/6B/5 are provisional inputs,
not implemented APIs or approval evidence. Before code review, a compatibility
manifest must pin their final exact source revisions, schemas and tests. A
different ownership or transport contract returns to design review and Marmot
approval. Drafting this design early grants no sequencing exception for live use.
Task-only delivery may be tested offline first; live delivery is still a deployment.

Excluded: task pickup, native writes/decision processing, inbound classification,
steering, reaction mutation, membership/invites, policy changes, timers, installs,
live probes, and release qualification. Plan8 invokes reconciliation; this library
does not install a scheduler. `btq-rd4z5` owns invite recovery. Hermes management
scripts retain their existing ownership, recovery and genuine approval gates.

## 2. Exact proposed ports and ownership

All ports are new typed in-process interfaces, except the bridge's bounded local
RPC. They are not user commands or arbitrary text/argv hooks. JSON RPC uses
UTF-8, rejects duplicate/unknown keys, invalid Unicode, NaN/floats and unknown
versions, and has a 64KiB frame cap. Local same-UID peers are trusted; claimed
run/session labels are consistency checks, not hostile same-UID isolation.

| Owner | Proposed operations and contract |
| --- | --- |
| Plan5/6D source owner | `snapshot(source_id)` returns current immutable revision, canonical digest, kind, lifecycle, destination bindings, complete approved render input and eligibility. `verify(snapshot_token)` compares exact revision/digest and live lifecycle before each part. Neither method writes a decision. Unavailable means HOLD. |
| Plan6A | Frozen rendered parts for the exact snapshot and surface, complete protected content and no-op redaction verification. No clipping or implementation-local template invention. Unsupported complete content returns to originator, never silently omitted. |
| Plan6B trust owner | `guard(binding)` returns current registered logical producer, release, group/account, observing epoch and fresh policy/membership verdict. Shared trusted guard serializes native send initiation with trust transitions. Never infer roster identity from group count. |
| 6C wsd journal | `reserve(intent)`, `next_due(clock,budget)`, `record(result)`, `resolve(native_target)`, `inspect(source_id)`; one transaction per reducer, immutable event IDs, monotonic version compare-and-set. No queue writes. |
| admind bridge | `submit(attempt_id, binding, parts_hash, part_index, text, reply_target)` and `inspect(attempt_id)`. Durable exact binding reservation precedes effect; same ID/same payload returns retained result, conflicting bytes refuse. Inspect never sends. Results below are closed. |

The bridge must use the same registration/trust owner and effect boundary as
6B. It alone schedules native sends for its namespace; 6B read replies are either
left in their existing distinct namespace or explicitly transferred once under
the compatibility manifest, never enqueued by both owners. Existing admind
`outbox_pass` automatic retry policy is not reused for 6C attempts. The bridge
stores its own versioned attempt namespace and dispatches each once.

Source IDs, account/group/native message IDs use validated opaque values from
their owners, not assumed UUID/32-byte formats. A source key is
`(logical_producer, source_kind, source_id)`; its revision and canonical digest
identify exact material, independently of any display copy. IDs and digests
remain private except what final approved renderer explicitly permits.
An intent has fixed fields: version=1, random 32-lowercase-hex intent ID,
source key/revision/digest, source snapshot token, renderer identity, reason,
created UTC microseconds, and one or two destinations (workstream and/or control).
Reason enum is initial/update/mirror/recovery/join-reissue/delivery-alert/reminder/digest.
No null destination, duplicate destination, caller-supplied sender or arbitrary group.

Each destination copy has a random 32-hex copy ID, positive generation, trust
binding, parent source/card reference or none, and an ordered frozen part array.
Each part has zero-based index, exact UTF-8 bytes, SHA256 and atomic rendered line
boundaries. Array length is 1..20; each native text frame <=16KiB and the full copy
<=64KiB. These are extra delivery ceilings: content that cannot fit is visibly
held, never truncated. Any stricter native/renderer limit also applies. Uniqueness
on `(source key,revision,digest,destination,reason,generation)` prevents duplicate
reservation. A collision with different bytes is integrity HOLD, not an update.

## 3. Attempt/effect/receipt state machine

Copy states: prepared, sending, delivered, held, superseded. Part states:
prepared, reserved, issued, confirmed, proven_unissued, uncertain, cancelled.
`issued` means the bridge has durably recorded native intent, not successful send.
Persist the wsd reservation, then call submit. The bridge validates exact payload
and registration, persists reserved, takes the shared send/trust guard, revalidates
source eligibility through the trusted owner and its fresh binding, persists issued
and invokes the one native send. No network retry is permitted after issued.
Source-owner failure before native intent is proven_unissued. A timeout, crash,
disconnect, malformed/empty response or ambiguous native error after intent is
uncertain. Source/native state cannot atomically commit together: a source may
change immediately after final verification. A delivered old card remains an
honest stale copy; actual answers must independently revalidate native authority.

Native `FinalSent.message_ids_hex` is accepted only as a nonempty, bounded list
of unique IDs that pass the control protocol's exact validator, with at most64
IDs. Save every ID in the bridge transaction and return them with exact attempt,
account/group, copy/part identity and payload hash. A duplicate ID already bound
to another part/copy is integrity HOLD. Partial/malformed receipt never confirms
a part. A confirmed part establishes transport receipt only, not reader visibility.
Its first returned ID is the continuation root when part0; all returned IDs map
to that same part for target resolution. Native receipt cardinality greater than
one is retained, not interpreted as multiple approvals.

If the submit response is lost, wsd calls inspect with the same ID, never submit
under a new ID. Retained confirmed receipt can settle uncertainty. A durable
reserved record with positive bridge proof native intent never occurred is
proven_unissued; crash recovery with missing dispatch proof is uncertain. A missing
record alone does not prove unissued. No callback, transcript, event spool or
operator statement fabricates a transport receipt. Inspect cannot change bytes.

At most three attempts per part, and only after positive proven_unissued; 1s then
5s backoff, due-state persisted. Issued/uncertain never automatically retries.
Operator recovery may authorize a *new marked display copy*, retaining the old
uncertainty; it does not authorize another task effect or rewrite receipt history.
Unknown native idempotency retention is not an exactly-once guarantee. Definite
pre-issue refusal can progress other sources fairly. One in-flight native send
per binding; no SQLite transaction is held over RPC/native I/O. Durable reservation
and version checks fence concurrent reducers; nonblocking effect guard contention
returns HOLD. Old epoch reservations cannot issue after a trust transition.

Each continuation replies to the real confirmed part0 ID from the same copy and
destination. Without it, hold the continuation: this design deliberately chooses
no top-level fallback for these protected copies, avoiding an orphan decision.
An update may reply to an established current task root only from a verified map;
an unavailable root holds rather than guesses. Independent control/workstream
copies have independent roots. Supersession cancels only proven-unissued parts;
in-flight/uncertain ones retain their status and become superseded observations.

A full copy is confirmed only when every frozen part in that *single copy* is
confirmed under one exact binding. Combining parts across generations, surfaces,
epochs or digests is forbidden. One complete eligible surface satisfies the
no-surface delivery condition. It does not cancel the other required mirror.

## 4. Mirrors, queries and operator safety

Initial task cards create one workstream root; approval/question cards require
workstream and control copies pointing to the same canonical source identity.
Their ownership remains Plan5/6D. Only that owner can supply an already committed
decision/result snapshot for a later mirror; emoji, receipt and text cannot.
Configured operator mentions occur only on control approval/question cards under
the final approved renderer; never infer principal IDs from live lookup here.

Resolver returns exactly current/superseded/uncertain/not_found with source key,
revision/digest and genuine current-root reference if known. The result is a
read-only hint for the eventual owner, not permission to decide/steer. The owner
must reread native lifecycle/digest at its write boundary. Old-card replies may
receive a pointer to the verified current copy; no guessed pointer, automatic
decision migration or old approval reuse. Unknown old targets use a fixed Marmot
unavailable explanation through admind; they never become ordinary task intake
merely because a map was lost. Full reply classification belongs to 6D.

Final stored and sent text must match frozen rendering after native redaction
and chunk framing; a changed redactor/configuration holds all remaining parts.
No hidden !details appendix substitutes for material approval facts. Context too
large or unsafe goes to the owner for grooming. All questions, cap exceptions,
design approvals and deployment approvals use admind's authenticated Marmot flow;
neither this transport nor a host diagnostic can approve them.

## 5. Deterministic due events and clocks

The source owner's original open/revision UTC time is the anchor for ten-minute
delivery escalation and four-hour reminder. It cannot be later than first local
observation; unknown/inconsistent time holds scheduling and emits one local
clock diagnostic. Replacement display copies never reset that source anchor.
The ten-minute condition applies only to approval/question cards with zero full
copies on eligible surfaces; at `now >= anchor+600s` reserve one control delivery
alert for `(source key,revision,digest)`. A later full receipt cancels a still
proven-unissued alert; issued alerts remain historical. Alert text says delivery
unconfirmed, never operator absent or decision rejected.

If the alert has no full receipt within60s after its reservation (or a definite
pre-issue refusal), atomically write the independent local alert immediately.
This **new 60s secondary deadline** resolves an unspecified ADR detail and needs
explicit human design approval. Reconciliation delay adds the Plan8 tick latency;
it is not a claim of sub-minute wall-clock alerting. Local file uses the existing
`admind.alerts.Alert` schema/name/size contract; create safe fixed text, mode0600,
O_EXCL/O_NOFOLLOW temporary regular file, fsync file, atomic rename, fsync verified
directory. Path is `<state>/alerts/<stable-opaque-id>.json`, with stable <=64-char
name. Never include raw sensitive content or actionable terminal instructions.
Failure writing this file is a durable local failure, not a successful alert.
admind relay success is independent evidence, not assumed from file creation.

At `now >= anchor+4h`, reserve at most one reminder per logical revision *if a
complete current copy exists*. It replies to that copy's original part0 with
the fixed renderer reminder, without repeating material. If still undelivered,
retain delivery alert state and mark reminder withheld, never thread to a fiction.
Once a complete copy becomes available, the overdue single reminder may issue.
Reminder uncertainty consumes that one reminder: no automatic duplicate. Source
closure/supersession cancels unsent reminder and digest entries transactionally.

After the four-hour event, open cards enter the next UTC-date digest at08:00 UTC;
one digest intent per date/control binding, matching the proposed Plan8 default.
The trusted digest producer also supplies ADR9's closed-yesterday, needs-human
and idle-workstream summaries with explicit source freshness/completeness. These
are distinct summary items, not card decisions or invented receipt evidence.
Catch-up produces only the latest due date, records skipped dates locally and
does not flood historical digests. The
digest is FYI, never an ask and never enters MAX_OPEN. A future renderer unable
to fit all supplied summaries produces its approved explicit overflow output,
not silent omission. Digest failure has ordinary receipt/uncertainty semantics,
not recursive alert-of-alert generation. These anchor/digest choices are explicit
human decisions; they are not attributed to the current implementation.

Use injected integer UTC microseconds and same-boot CLOCK_BOOTTIME anchors.
While a process/boot anchor is available, compare elapsed clock deltas; >1s
divergence, backwards UTC or invalid future anchors holds due generation. Restart
on the same boot retains anchors. New boot requires a trusted host-clock readiness
signal from Plan8 before using saved UTC deadlines; no inference from elapsed
uptime. Unknown signal holds scheduling, not an infinite implicit reset. Delivery
effects also retain the independent fresh trust check. Plan8 supplies clock and
invocation cadence; library sleeps/polling are prohibited.

Each reconcile call reads at most100 due sources, emits at most20 new intents
and at most20 bridge operations within a5s wall budget, with deterministic
oldest-due/source-key ordering and a durable cursor for fairness. Hitting budget
returns pending, not success for omitted items. No internal self-trigger. Operator
direct work and genuine pending approvals do not prevent unrelated ready tasks.

## 6. Persistence, membership and recovery

Journal migration adds delivery_sources/copies/parts/attempts/targets/due/events
tables in one versioned transaction, retaining every existing row/table/index.
Unique keys and FKs enforce bindings above. Startup integrity failure preserves
the damaged journal and refuses ordinary send startup; never treat it as empty.
Fsync-backed SQLite settings and backup/restore tests are code acceptance gates.
Inspection is bounded and read-only; no native-DB schema bypass or ad hoc edits.

Clean restart loads pending states and reconciles bridge attempts before new
sends. An issued row without a retained result stays uncertain. Journal loss
requires explicit Marmot recovery/deployment authority before changing files:
preserve damaged evidence, rebuild only source-owner-proven open/current sources,
create a new recovery epoch and marked `re-issued after recovery` copies. This
accepts possible duplicate *display* after lost state, as the ADR requires; no
task/action replay follows. A healthy bridge journal is consulted by stable source
bindings before any new copy, but absence still is not non-delivery proof. If
both stores are lost, all previous sends remain unknown. Do not revive terminal
sources or forge lost IDs. Native old target IDs not reconstructable stay unknown.
First valid current owner-confirmed root becomes the safe pointer for later old
replies whose canonical source can actually be established.

Membership owner events can arm visibility-pending by principal/binding/epoch;
add success and count alone cannot prove member visibility. V1 **does not fire
automatic join reissue**, because no native visibility signal was proven in S4.
An authenticated Marmot operator request may authorize one marked current-card
display reissue after fresh binding/eligibility checks; it never implies receipt,
membership repair or approval. This explicit nonautomatic V1 choice/departure
must be accepted by the human. Future automatic signal support needs demonstrated
source evidence and a reviewed design amendment. No remove/add/invite loop here.

Retain all uncertainty/attempt/source bindings until explicit reviewed retention
policy exists; caps10000 sources,100000 parts and256MiB delivery data pause new
admission with a local alert. Never prune unconfirmed/decision-related history to
make capacity. These ceilings and lack of automatic retention are disclosed
availability costs, not proof of unlimited unattended operation.

## 7. Delivery, acceptance and decisions

Order: approve this contract; freeze compatible final6A/6B/source-owner ports;
implement pure reducers and migration; bridge trust/intent/receipt handling;
owner-fed projections/targets; due/local-alert handling; offline integration;
real Claude built-in code review (maximum3 rounds); explicit deployment request
with this published design link and all deviations. Human approval of this
design alone authorizes none of those deployments, live sends or recovery copies.

Required named offline tests use fake native clients, clock, source and trust
owners, never production queue DBs or live Marmot. Cover transactional migration
rollback/preserved rows; duplicate/reservation collision; final trust/source drift;
every crash across reserve/issued/native/receipt/return; inspect-only recovery;
empty/malformed/multi-ID/colliding receipts; cross-copy partial refusal; real part0
thread mapping; changed redactor/renderer; supersession during send; remote native
timeout with unknown outcome and zero retries; at most3 proven-unissued attempts;
control/workstream independence; writer conflict refusal; no decision from display.

Clock tests cover exactly600s/4h/60s boundaries, source anchors, delayed first
observation, restart/suspend/NTP/new-boot readiness, late full receipt cancellation,
single reminder, digest catch-up/date rollover/budgets/fairness, and alert-directory
symlink/FIFO/write/fsync failures. Recovery tests preserve damaged bytes, never
revive terminal sources, retain unknown old targets, mark duplicates honestly and
refuse unapproved automatic join reissue. Mutation tests must break complete-copy
checking, intent-before-effect, no-retry-after-issued, source revision binding and
one-reminder uniqueness and show the named assertions fail. No tests or live proof
are claimed by this design document.

The Marmot design approval must explicitly cover: admind as the sole transport
bridge for this namespace; no retry after ambiguous send and possible indefinite
delivery HOLD; no protected continuation fallback; source/transport race handled
by independent decision revalidation; original-source reminder anchor and UTC
digest catch-up; new60s secondary alert deadline plus scheduler latency; manual
join visibility recovery in V1; bounded capacity/retention holds and same-UID trust.
If those are not acceptable, keep implementation blocked and amend the design.
