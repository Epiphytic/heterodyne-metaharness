# Plan9 source acquisition boundary

Status: proposed boundary after the ONE actual Claude Opus HIGH design review,
which returned REVISE. Its findings are incorporated once below; no second
review or validation is claimed. Human design approval remains required. No
implementation, deployment, source admission or qualification clock is authorized.

Parent: [approved Plan9 d4](https://github.com/Epiphytic/heterodyne-metaharness/blob/e009a2c6d1a4c7887bb5c5effd5f7d9bbbd023cc/docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d4.md),
native approval `btq-e9toz`. This proposal concretizes acquisition and retention;
it changes no qualification predicate, completeness obligation or 72-hour duration.
All questions, decisions and approvals use Marmot admind; diagnostics are not consent.

## 1. Actual boundaries and proposed topology

`Ledger.spool` accepts strict JSON, addresses records by content digest, has
1024 active entries and 100000 lifetime files, and terminal commit/release take
publisher.lock then spool.lock. Release retirement knows ledger spool digests,
not source acquisition receipts. It is unsuitable as an all-acquisition store.
`Ledger.retain(Artifact, raw)` can retain arbitrary exact bytes plus an immutable
ID binding under publisher.lock. Neither API establishes source authority.

The proposed initial WSD adapter is an in-process diagnostic owner facade over
an already opened `Journal`, sharing WSD's UID, privileges and failure domain.
It is not a separate security principal or an existing transport API. The
collector receives this fixed owner facade at trusted construction; evidence
cannot select a path, executable, database or class. A separate-process WSD
collector would require a separately specified owner transport, not construction
of another Journal. `Journal.events_since` currently coerces values to strings
and bounds only row count. It is not the new lossless snapshot method.

The acquisition store is a NEW acceptance-local component, physically separate
from Ledger objects, spool and locks. It never calls Ledger.spool. One source
owner reader writes a dedicated source lane; terminal publication only reads
completed immutable acquisition receipts. This is proposed software, not an
installed capability. Default production Proofs remain Unavailable.

## 2. Registration, occurrence and bounded read contract

A retained closed registration contains schema `acquisition-source.v1`, source_id
(existing ID grammar, 1..128 chars), fixed adapter/configuration digests,
retention profile digest and epoch tagged union. Epoch is either
`{kind:unestablished}` or `{kind:admitted,id:ID,evidence_sha256:Digest}`.
Admission requires genuine source-specific evidence of restore, loss, retention
and completeness behavior; the tag alone is not that evidence. The initial
WSD registration is unestablished and diagnostic only. A local lane UUID is
storage identity, NEVER an owner epoch. A lane belongs to exactly one registration
and one explicit binding union: `{kind:diagnostic}` or
`{kind:ledger,entry_sha256:Digest,run_id:ID}`. Do not reuse a lane across runs.

Proposed `capture(after, expected_anchor, record_limit, byte_limit)` returns a
closed immutable batch: registration digest, after, high_water, anchor digest
at after (null only at zero), and ordered (ordinal, original_bytes). Limits:
128 records, 1048576 original bytes total; ordinal is exact int 0..2147483646,
not bool. Zero is only initial cursor. Read anchor and rows in the SAME source
snapshot. Missing/changed acknowledged anchor, high-water regression, gaps or
unexpected types HOLD without advancing. Records start at after+1 and remain
contiguous through at most high_water. Positive batches must advance. Empty is
valid only at after==high_water and creates no retained batch/receipt.

An anchor detects replacement at that cursor, not an arbitrary rewritten middle
range, restore followed by catch-up, or loss outside this log. No anchor, inode,
mtime, sequence, local UUID or receipt chain establishes a restore-safe epoch or
complete audit. Until a source-specific admitted epoch/continuity mechanism
proves those properties, captures stay diagnostic and operational AuditInterval
and terminal-fence Proofs HOLD. Never silently reset after a source change.

Byte-native sources retain original bytes, including non-JSON, duplicate keys,
floats and malformed text, without passing through acceptance codec.load.
Decoding/classification occurs AFTER durable retention; a strict evidence parser
may refuse publication as structured evidence but cannot erase originals or
wedge subsequent diagnostic acquisition. Oversized records HOLD before retaining
an incomplete batch; no truncation or skip-as-complete.

## 3. WSD SQL snapshot and exact envelope

The NEW owner method holds Journal.lock only for a bounded read snapshot and
copy. Reject an already active transaction. Use explicit BEGIN, a first SELECT
to establish the snapshot, fixed schema/version checks and bounded SELECTs,
then ROLLBACK in finally. Do not use transaction()/snapshot() (BEGIN IMMEDIATE),
Journal constructor, migration, checkpoint, or `_check`/integrity_check per poll.
Existing open-time validation is not a promise against later corruption: query,
schema or I/O failures return fixed HOLD codes. No writer transaction is taken.
Measure the actual mutex window with concurrent WSD writes before deployment;
if the approved capture cadence cannot be met, this topology is ineligible.
No hard real-time latency or separate failure-domain claim is made.

The versioned SQL original is a canonical envelope of the seven named columns
seq, at, ws, bead, kind, detail, ref, each with an explicit SQLite storage class.
Null is `{storage:null}`; integer is `{storage:integer,decimal:<exact signed
base10>}`; real is `{storage:real,ieee754_be:<16 lowercase hex>}` from the exact
SQLite returned binary64 value; text/blob are respectively `{storage:text,hex:H}`
and `{storage:blob,hex:H}`. TEXT hex is obtained by hex(CAST(column AS BLOB)),
not UTF-8 decoding or str(). This preserves stored text bytes, including invalid
UTF-8, and distinguishes identical BLOB/TEXT bytes. It does not claim database
page, original SQL literal or WAL byte fidelity. Seq must actually have INTEGER
storage and satisfy the ordinal bound; other storage classes remain represented,
not implicitly coerced into valid events.

Before fetching value contents, query typeof and length(CAST(column AS BLOB))
for TEXT/BLOB in the same snapshot. Fixed seven-column metadata is bounded;
fetch at most 128 row metadata entries plus one existence sentinel. Compute the
exact canonical envelope size from ASCII hex lengths, fixed field syntax and
bounded integer/real representations before fetching hex content. Reject any
row/batch exceeding the remaining byte budget. Hex creates exactly two ASCII
bytes per stored byte, with no JSON escaping multiplier. Recheck fetched lengths
and final serialized bytes before exposing a batch. No fetchall of unbounded
values; no full-table scan/integrity check masquerading as a size guard. Busy,
corrupt, schema/type, budget or snapshot failures become typed HOLDs; private
exception text is retained diagnostically, not projected as authority.

## 4. Independent durable occurrence store

All writers use one collector quota lock before a source-lane lock; quota
reservations/counts are durably bound to the pending batch, with crash replay
accounting once. The quota lock is not held during source reads or publication.
No code acquires quota while holding a lane lock. Each lane has a private 0700
directory, 0600 single-link regular files, a fixed
source-lane lock inode, originals/, receipts/, and at most ONE pending batch.
No symlink traversal or caller-selected location. File operations use no-replace
publication, fsync file then directory, verify exact existing bytes on replay.
A trusted same-UID local owner can tamper with these files; hashes are integrity
bindings, not independent authentication.

Concrete finite profile: at most 1000000 retained occurrences, 100000 positive
batch receipts and 4 GiB retained originals per lane, at most 16 lanes per
collector; the whole collector shares a 4 GiB original-byte budget. A pending
batch reserves its final counts/bytes BEFORE capture acknowledgement and uses
at most 1 MiB payload plus bounded metadata. No empty-poll receipt or permanent
completed pending-file accumulation. Missing capacity HOLDs before capture
acknowledgement and never drops a source event. These are finite admission
limits, not a guarantee of 72 hours at arbitrary event rates. Admission must
measure the actual source rate, disk/inode budget and terminal publication
capacity; 100000-row temporary benchmarks must fit this profile.

For each positive batch, under the lane lock:

1. Verify contiguous receipt chain and last anchor, reserve capacity, and durably
   publish the complete immutable pending batch including exact originals,
   registration/lane binding and predecessor receipt hash.
2. Retain every original under its OCCURRENCE identity, not content digest alone.
   Identity tuple is [1,lane_uuid,registration_sha256,ordinal,original_sha256].
   Its hash uses UTF-8 compact JSON arrays, no whitespace/newline. Thus identical
   bytes at distinct ordinals remain distinct occurrences. Same ordinal with
   different bytes is a conflict, never an overwrite.
3. Write the next contiguous numbered immutable receipt, then fsync. Only this
   receipt advances the acknowledged cursor. Retire the pending file only after
   verifying that receipt and all its originals; unlink+directory fsync. Crash
   leftovers replay exact bytes idempotently, never produce a second branch.

Receipt schema `acquisition-receipt.v1` has lane_uuid, registration_sha256,
binding, number, previous_sha256 (null only number zero), after, through,
high_water, anchor_before_sha256, anchor_after_sha256, ordered occurrences
(ordinal, occurrence_sha256, original_sha256, original_byte_count), and
capture_observation. The latter contains exact sampled boot_id and monotonic
start/end plus observation UTC, or `{kind:unavailable}`; it describes local
capture only. A proposed source adapter must supply these observations honestly;
they do not establish native event time, run duration, or source completeness.
A completed receipt may optionally bind a previously supplied immutable terminal
request {entry_sha256,run_id,fence_sha256}; absent is explicit null. This binding
records requested correlation, not proof that the endpoint was sampled after
that fence. Actual source-fence proof remains a separate required capability.

Numbered receipts are the ONLY frontier. Recover from zero and verify contiguous
names/hash links, not max filename, an overwritable pointer, mtime or guessed
cursor. Indexes/cached counters may accelerate verified prefix reads but are
rebuildable hints, never independent authority. Crash after receipt/before
pending deletion completes deletion, not source recapture. Unknown tail,
missing original, conflicting receipt or broken capacity accounting HOLD.

Terminal Ledger.release cannot retire any acquisition original: it has neither
this namespace nor its lock/retirement API. Snapshot A's publication names exact
completed receipt hashes and bounded occurrence ranges; an incomplete batch B
is not selectable. B remains retained even if ledger commit/release runs.
After completion, B is independently selectable even if bytes equal A's bytes.

Retirement is explicit and whole-lane only: stopped capture, no pending batch,
no active ledger binding/fence or unfulfilled artifact references, and an
independently verified durable export of ALL receipt/original bytes. A retained
retirement manifest binds export hashes, counts and final receipt. Only a later
separately authorized deployment/operational retention action may remove local
bytes; this proposal authorizes no deletion, automatic eviction or history reset.
If export/reference discharge cannot be proved, retain and HOLD on exhaustion.

## 5. Publication without invented authority

Mapping canonical object schema `acquisition-mapping.v1` contains the complete
identity tuple, lane binding, exact receipt Artifact, original Artifact and
ordinal. Raw Artifact.id is `acq_` plus occurrence SHA256 (68 chars); mapping
Artifact.id is `acqm_` plus SHA256 of canonical mapping bytes (69 chars). The
receipt Artifact ID is `acqr_` plus receipt hash. Receipt contains occurrence
identities, not mapping IDs, avoiding a circular hash. All satisfy existing ID
bounds. Publish exact receipt, original and mapping bytes through Ledger.retain;
the mapping is itself retained, never merely an opaque hash label. Byte identity
is checked before publication; missing mapping/original HOLDs. Ledger's own
100000 object capacity remains real: do not promise a million-object terminal
publication. Measure the selected bounded publication set; exhaustion HOLDs.

Acquisition originals and receipts continue independently while REAL commit or
release holds BOTH publisher.lock and spool.lock. Publication waits for the
publisher; acquisition never acquires either lock, calls a proof callback or
performs native reads under them. The only overlap is copying completed immutable
bytes outside acquisition lock, then checking their hashes at publication.
No source/lane lock is held while waiting for publisher. Product Journal.lock is
released before filesystem persistence or any acceptance lock. This is the
closed lock order, not a claim of bounded external proof-callback execution.

Parent AuditInterval cursor IDs are separately retained `cur_` plus SHA256 of
[registration_sha256,lane_uuid,ordinal,original_sha256]; a mapping Artifact retains
the tuple. Capture monotonic time is NOT AuditInterval event coverage time.
A real admitted adapter must prove the interval endpoints, entire durable source
history and source-fence ordering using its actual source capability. Diagnostic
receipts cannot fill those authority fields from local timestamps or supplied
run/fence labels. WSD progress rows alone cannot establish complete operational
audit. All unsupported gate/native/Marmot/host proofs remain Unavailable.

## 6. Finite implementation and offline acceptance

Future code tasks: independent occurrence store and byte codec; owner-local WSD
bounded read facade; diagnostic publication mappings; then separately admitted
source-specific authority adapters. No native CLI, service, model, network or
queue effect is authorized by this design. Implementation gets actual code
review and separate deployment approval. Deployment requests link approved design
and deviations. No operational admission/72-hour acceptance is claimed here.

Required offline fixtures preserve all existing ledger oracles and cover:

- 100000 occurrences, more than 4320 empty polls, pending cleanup, byte/count
  exhaustion, identical bytes at distinct ordinals, terminal A versus pending B.
- Non-JSON/floats/duplicate keys/deep bytes retained exactly; strict publication
  refusal does not drop diagnostic occurrences; oversized source remains HOLD.
- SQL INTEGER/REAL/NULL/BLOB/TEXT including invalid UTF-8, control characters and
  four-byte text, exact pre-materialization bounds, concurrent WAL writer and
  explicit read BEGIN, no `_check` or writer transaction per capture.
- Changed anchor, restored-middle-range limitations, unestablished epoch never
  qualifying, exact run/lane binding and diagnostic timing/fence nonauthority.
- Crash at every pending/original/receipt/fsync/deletion step, conflicting tails,
  replay after receipt, capacity reservation and stopped export retirement.
- Separate process holding publisher only AND actual commit/release holding
  publisher+spool through a blocked fake proof callback: acquisition still makes
  durable progress; exact publication later retains mapping/receipt/raw bytes.
- No lock inversion between Journal, acquisition and publisher; concurrent
  captures serialize per lane; diagnostic adapters never fabricate Proofs.

Measure time, memory, file/inode count and mutex windows using temporary data
and the actual topology, including restart chain replay and terminal object
capacity. No benchmark or 300/600-second audit cadence result is claimed by this
prose. Failure to establish bounded capacity/cadence or real source admission
keeps operational qualification HOLD; collection cannot manufacture silence.

## 7. Human decision and provenance

Human design approval would authorize future implementation of this finite
retention/read boundary and diagnostic mappings. It does not approve production
source completeness, new source permissions, retention deletion, deployment or
clock start. Initial WSD in-process shared-failure-domain diagnostics and finite
capacity/explicit stopped retirement are disclosed choices. The independent
occurrence store replaces the earlier all-acquisition Ledger.spool proposal;
parent terminal spool remains unchanged. Exact reviewed source and all ten
verbatim findings plus the additional frontier note are retained in the companion
review record and input manifest. The actual REVISE verdict remains history;
incorporation is not a fabricated APPROVE.
