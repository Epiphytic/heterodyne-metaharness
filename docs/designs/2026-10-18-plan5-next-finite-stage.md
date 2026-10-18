# Plan5 next finite offline stage admission

Author: Codex. Status: sole HIGH review incorporated once for btq-voe5f;
human design approval remains outstanding. The actual review returned REVISE with
ten findings, preserved byte-exact with ordered author dispositions. These repairs
are author work, not reviewer clearance. No second design review or validation is
requested. No implementation, consent, deployment or completed Plan5 milestone is
represented. Publish this exact subject and obtain genuine Marmot approval before
creating/pinning executable targets. Proposed role names below are not task IDs.

## 1. Authority and fixed inputs

This is a finite admission supplement to the existing Plan5 D4, not a rewrite or
new review of that full design. Original D4 is
`45e239a53bdb3bee82d6d44125c021abe296f60d:docs/designs/2026-10-16-plan5-actions-and-executor-d4.md`.
Original D4 is publicly addressable at [the immutable published source](https://github.com/Epiphytic/heterodyne-metaharness/blob/45e239a53bdb3bee82d6d44125c021abe296f60d/docs/designs/2026-10-16-plan5-actions-and-executor-d4.md).
The approved combined archive is
`cb180b72c21a157f721be1e4fa57ccfd03d4a11b`, including
`docs/adr/2026-10-16-plan5-governing-amendment-admission.md` and its byte-exact D4.
That combined subject enacts the seven governing choices and limits initial
implementation to P0/P1/P2/Q0. Approval btq-l6eeo is existing authority for those
four targets; it is not authority for this next batch. D4 §§5–9/12 govern the
contracts here. The seven choices are neither expanded nor re-approved here.

The actual source baseline of this draft is
`a73e91d367bd626556371f33a46e25f003243c6e` in the product repository.
P1 merged source is `dd38be7a39c1cdfdcd61a5555c7e3a0ff8b4ee9c`.
P2 is source `00ff9fc2071ecb22f6d950422232ca1c95a5540e`, with review-filing
commit `1865fdf34e2a906309d08a010a2df6e1fd4d550b` (PR118); this draft does not
assert its merge or hosted CI completion. Q0's second full HIGH reviewed
`f80412a853a2b81ec87d3e7fbf18c6c9c91f7cb2` and returned REVISE10. Its author
repair is `7a631dbd18b9232ed51f60b664127fe694bf4ee7`; controller review is pending
at this incorporation. Neither source is accepted by this document. Q1 acceptance
must select the eventual actually reviewed/accepted repaired Q0 and C1 product
commits and trees together, include predecessor raw reviews/dispositions and run
the reciprocal compatibility cases below. A later source correction requires a
new exact tested pair, never silent reuse of f804's interfaces. These are source
inputs, not installed capabilities. Exact blobs and SHA256 values are in the
[inputs manifest](../designs/2026-10-18-plan5-next-finite-stage-inputs.json).

The byte-exact P1 R11 and P2 R4 outputs are filed under `docs/reviews/2026-10-18-plan5-next-*`.
P1 R11 says APPROVE offline PoC and retains eight findings; P2 R4 says APPROVE
offline PoC and retains ten. Those original ordinals and all earlier histories
remain unchanged. Neither report certifies production adapters or this admission.
The requested historical P1 public filename was absent from the inspected Git
objects; the retained actual raw report, not an invented file, is the source.

## 2. Bounded approval subject

Recommend approving exactly four roles: C1, Q1, P3-offline and P4-offline below.
The last two are offline subsets of the historical P3/P4 proposal, not completion
of their real intake/writer integrations. No P5/P6/H1 target is admitted here.

| Proposed role and repository | Deliverable and scope | Required inputs |
|---|---|---|
| C1 — product: harden the P1/P2 composition boundary | Repair the retained contract issues in §4; preserve every existing reducer oracle; add typed fixture observations and current-snapshot projection checks. No production journal or provider. | Accepted P1 source and accepted P2 source, exact source compatibility record; this new approval. |
| Q1 — queue: pin the offline product bridge and purpose compatibility | Implement a reusable disabled source-bound Contracts bridge and exact full-ask/digest/reducer compatibility, including terminal cleanup. Native purposes remain design/execution/permission; confirm/recovery refuse. No Dolt backend, reader credentials or CLI installation. | Accepted Q0 source and C1 contract pins; this new approval. |
| P3-offline — product: durable request-filing coordinator with injected ports | Compose request intent, allocation/adoption, blocker, park, landing, canonical ask, sufficiency, grooming and filed readback using only disposable fixture stores/ports. | C1 plus Q1, with their exact tested product/queue source pair. |
| P4-offline — product: durable serial decision/finalization coordinator | Compose bounded synthetic inbox, first valid decision, write/readback/close/pins/finish and recovery HOLD for the three supported native purposes; explicitly refuse unsupported confirm/recovery mapping. No live admind ingestion or writer cutover. | P3-offline and the same accepted C1/Q1 pair. |

Controller creates actual per-repository targets and genuine approval blockers,
then pins the published design and actual source bases before claims. Existing
source-task references are P1 btq-efddg, P2 btq-t93jz and Q0 btq-9guj8; their
status alone is not interface proof. C1 can be prepared on a controller-admitted
stack of the pinned P2 source without guessing a future merge SHA. Acceptance
requires P2 hosted CI green and its actual merge before C1 acceptance or merge.
It must name that integrated source and review/test evidence; no worker merges
an unreviewed predecessor or edits a different task worktree. Q1 follows C1;
P3-offline follows both; P4-offline follows P3-offline. Independent fixture/spec
work may run in separate claimed worktrees, without duplicate ownership of files.

A normal source correction within these fixed contracts needs its actual code
review/tests and a refreshed compatibility record, not a new human design ask
per function. A changed contract, authority boundary, target set or deployment
requires the corresponding genuine new design/deployment decision. No future
source pin, branch name, DTO boolean or CLOSED task substitutes for that evidence.

## 3. Exact composition contracts

Reuse `actions.requests`, `decisions`, `dispatch`, `transitions`, `journal`,
`policy` and `judgement`. Use Q0's `btq_actions.core.Queue`, `Backend`,
`Contracts`, `Expected`, `Row` and typed errors as the conditional boundary.
These interfaces are dependency-injected offline seams, not authenticated clients.
Use the actual existing native context-digest/approval validation implementation
in fixture tests: retain title, description, whole ask and resolved references;
never introduce a projection digest as approval authority.

C1 owns common identity/current-policy checking used before durable projections.
A deterministic hard-deny remains non-granting; a DenyProposal is inert data until
its exact current subject, session, policy and canonical intent have been checked.
A stale or unrelated projection cannot create durable same-session denial history.
Permission stays input-profile escalation where the real bridge is unsupported.
No judgement proposal, including sufficient or deny, is authenticated consent.

Q1 explicitly maps `Key.session` to P1 `CreationKey.session_key` without changing
logical-session identity, and keeps generation separate. Full CAS includes key,
generation, version, stage, revision, digest and event. Unknown purpose remains
legacy/unclassified and refuses typed operations; no absent-purpose inference or
bulk migration. D4 §6 explicitly names only three native purposes, while P1's internal decision
model also names confirm/recovery. This is an unresolved mapping, not permission
to add native purpose values. Q1 preserves the closed three-value schema and
refuses unsupported confirm/recovery native mapping before effects. P4 may test
those internal reducer values only as models; it cannot finalize them natively.
Any later mapping must bind the actual approval kind, payload and validation and,
if it changes D4's schema, be genuinely admitted as a design amendment. Generic
design close cannot execute an action; permission cannot approve outward effects;
no recovery model can mint an initial claim.
The new reciprocal source-capsule contract in §3.5 replaces ambient imports.
Supplied fixture sources are synthetic. No origin/native authentication, default
factory, runtime command or installed-source discovery is enabled. Missing atomic,
purpose, cleanup or dependency capability refuses before creating a shell.

P3-offline retains a durable command/observation cursor per creation key. It
commits intent before each potentially mutating injected port call; no fixture
acknowledgement precedes durable admission. Resume re-reads the owning reducer
and exact port readback. It does not restart a possibly issued create/write.
The phase-specific outcome rules in §3.2 govern; a typed exception alone never
proves a previous attempt was unissued. Validate origin,
generation, claim ownership, repository and current policy before each mutation
using independently injected observations. Fixture booleans are never described
as real authority. No daemon, CLI route or default live factory is registered.

The sequence remains D4 §5: durable allocation intent; one keyed shell; native
blocker; park/stop while retaining claim; exact requested-source landing; whole
ask and immutable revision; current sufficiency; full filed readback. Unknown
allocation allows three bounded lookups at 1/5/30 seconds; zero/multiple/failure
never authorizes recreate. At most two grooming revisions return to the same
logical session with a restricted synthetic capability, a new parked readback,
new source/ask/digest and fresh sufficiency. Neither this fixture capability nor
an existing ordinary resume API proves a real restricted grooming launch exists.
All associated shells/blockers must be consistent before capacity is released.
No deadline or three-attempt exhaustion is absence or permission to release.

P4-offline serializes by approval and binds the inbox event ID, exact purpose,
current principal/surface, current digest/revision and policy before reducer use.
Commit inbox receipt first; first valid decision wins. Duplicate/reordered events
are idempotent; invalid/superseded events remain audited. Persist write intent,
then issue once. A lost acknowledgement resumes exact native readback, never
another decision write. Close, native pins and final committed readback are
separate steps; close alone is not completion. Same-event heart bookkeeping
cannot rewrite the decision. Three bounded unresolved attempts lead to retained
HOLD with a typed recovery-needed output. No output is a delivered Marmot notice.
A future real recovery requires D4's exact authenticated scoped event, current
CAS and retained provenance; this batch only models that event synthetically.

Use temporary fixture persistence only. Extend its exact schema/migration APIs
explicitly if needed, preserving old rows/history and refusing unknown/partial
schemas. No installed WSD schema is guessed or migrated. Existing finite limits
are reconciled by §3.4: two outstanding per workstream/bead across generations,
a common batch cap of 64, 4096 retained requests and 16384 events. Full storage
retains HOLD and history.
Coordinator step budgets cannot multiply reducer retries after restart. Duplicate
no-op deliveries do not append unbounded events. Fixture clock samples come from
the journal's injected sampler, never a proposal's timestamps. Retain boot and
monotonic provenance at issuance and every timed observation; missing/legacy
unproven anchors hold. This does not create physical execution or cleanup proof.

### 3.1. Proposed whole-request terminal cleanup

This admission explicitly adds a missing offline queue interface; it does not
claim Q0 already has one. Q1 owns `Queue.withdraw(expected, settlement)` and the
closed Contracts `settle_request(row, settlement) -> Row` bridge; C1 owns any
product projection needed to validate it. Settlement contains the exact creation
key, origin, purpose, expected whole-row version/revision/digest, terminal phase,
all associated shell IDs, per-shell terminal observations, blocker readback,
retained decision/dispatch snapshots and their versions, and the existing typed
P1 CancellationRecovery or AbandonmentRecovery where required. No loose boolean
is sufficient. Observations are synthetic fixtures here; a real authenticated
observer and withdrawal writer remain unavailable.

Within one backend CAS transaction, reload the whole request and all decision/
dispatch facts, apply the actual P1 `requests.settle_request` predicate and prove
all shells terminal and blockers cleared. Only its returned terminal Request may
be projected to `Row.terminal=True`; preserve payload/history and advance version
once. Duplicate exact settlement is read-only; conflict or incomplete observation
retains the row and reservation. Per-bead and global capacity count this same
terminal projection transactionally. Cancellation for an unposted shell is not
allocation absence; filed/ask-pinned/sufficient cancellation needs the existing
exact scoped CancellationRecovery and native withdrawal proof. Unknown allocation
requires the existing bounded lookup history and authenticated AbandonmentRecovery,
not a timeout. Cannot-approve-as-written is a diagnostic reason for a separately
proved cancellation, not a new terminal proof or authority class.

A committed approved execution with no Dispatch has NO supported settlement in
the current source: `settle_dispatch` requires a real attempt, while completed
request settlement requires a genuinely settled dispatch. Do not fabricate a
Dispatch or claim to release this case. It remains explicit unsupported HOLD in
this batch; a future whole-request recovery authority/contract must be genuinely
admitted before enabling that path. Positive cleanup fixtures cover only phases
supported by the pinned reducers, plus refusal of this missing case. Terminal
rows reject every new write/restart; exact retained duplicates may be read only.
Succeeded dispatch effect, claim, invocation and existing evidence stay immutable;
only source-supported monotone cleanup/evidence completion is allowed.

### 3.2. Phase-specific outcomes and durable uncertainty

The coordinator owns a persisted command ID, expected snapshot, attempted flag
and outcome (`prepared`, `issuing`, `observed`, `refused-unissued`, `uncertain`).
Write issuing before a port call. An issuing record without a bound complete
observation on restart is uncertain. No adapter exception can erase this state.

| Call boundary | Accepted translation | Retained effect meaning |
|---|---|---|
| Pure C1/P2 validation, before issuing a native/backend call | Known requests.Refused, judgement refusal or codec InvalidAction -> typed validation Refused with fixed reason | This validation issued nothing; any earlier command uncertainty remains. |
| Q1 read/lookup, before any mutation in this command | Known core.Refused -> typed read Refused; transport/unavailable/malformed result -> read Uncertain | Neither proves absence or resolves an earlier write. |
| Backend allocate/CAS before write with its explicit definite-refusal outcome | core.Refused with exact command/expected binding -> refused-unissued for this command only | A stale Expected after prior uncertainty remains a readback-only conflict. |
| Issued write, ambiguous acknowledgement, timeout, malformed committed readback, foreign exception | typed Uncertain and fixed recovery-needed reason | Never retry the write or reclassify as unissued. |
| Exact command-correlated readback | observed, only after whole-row/reducer checks | May settle that command; advanced unrelated state stays uncertain unless a genuine descendant receipt proves it. |

Q1 normalizes known foreign pure reducer errors at the boundary, preserves known
core.Refused, enforces strict bool ports, and never exposes raw exceptions as a
success/refusal proof. Programming faults fail visibly with retained uncertainty
if issuance was possible. P3/P4 never retry decide with stale Expected to discover
whether it landed: resume is readback only. Backend prewrite classification is a
fixture capability, not proof of a live Dolt adapter.

### 3.3. Clock and provider-attempt ownership

P3/P4 receive one journal-owned injected ClockSource (boot identity, monotonic
sample and diagnostic UTC) and NonceSource. The coordinator samples and persists
at issuance/prepare/lookup, passes that exact bound sample through Q1 ready/
prepare/claim/restart `now` and through P2's explicit clock/boot/nonce arguments.
No caller time, real-host default clock, uuid default or unattested boot label is
allowed in composed fixtures. Changed boot/discontinuity or missing retained
anchor holds under the existing reducer policy; it never resets an allowance.

P3 owns provider intent even though judgement is read only. Before calling the
fake Provider, atomically store command/attempt nonce, canonical input bytes and
digest, requested model/schema/release, purpose, request version/revision/source,
policy identity, boot/start sample and budget. Call P2 assess once with those
explicit injected values. Store exact reply/failure and end sample before using
any projection. A Receipt supplied by a caller is not this retained attempt.
Crash after invocation without a stored matching result yields uncertain-provider
and HOLD, not a fresh nonce or a more favourable reroll. Resume can adopt only a
command-correlated result from the same fake owner; no real provider receipt or
lookup API is invented. H1 must separately prove such an owner before activation.

### 3.4. Capacity and bounded audit

The composed batch configures BOTH journal and queue to 64 outstanding total and
two per (workstream, bead) across generations, validated before admission. Q1's
Backend capability must enforce both atomically; an unsupported backend refuses
before allocation. Product intent reserves first under those limits; a definite
queue refusal retains a capacity-held intent using the same reservation until a
proved unissued cancellation or adoption. It cannot create a 65th queue request,
release unknown issuance or silently discard an admitted intent. Terminal cleanup
releases the shared projection only after §3.1 succeeds.

Per approval, retain at most 32 distinct invalid/superseded event details (each
bounded by the existing event codec), one fixed overflow aggregate with saturated
count and digest of bounded samples, and 128 lifecycle/recovery event slots.
These 161 slots are reserved before admitting a request, counted against the 16384
global event budget. The maximum active reservation is 10304 event slots, leaving bounded space for
retained terminal history; admission also checks that actual remaining space
meets the full reservation. The 64 active-request limit cannot be displaced
by one approval's invalid traffic. Beyond 32, record no new detailed event row;
update the same aggregate transactionally and do no projection/decision work.
Exact duplicate IDs within retained detail are no-ops; overflow input is rejected
without authority evaluation and may increment only the aggregate. Never claim
unlimited-ID deduplication. Terminal detail/history remains under 4096 requests/
16384 events; exhaustion holds new admission, preserves completion reserve and
requires supported operator archival rather than erasing uncertain history.

### 3.5. Reciprocal source capsule

Q1 and P3/P4 acceptance publish `plan5-pair.v1.json` beside their fixture bundle
under a dedicated non-runtime evidence directory. The signed code commit pins
its bytes; it is an integrity manifest, not human consent. The research inputs
JSON accompanying this design is NOT that runtime capsule or a complete runtime
dependency manifest; implementations must produce and test the capsule. It names both exact
product and queue commits/trees, bundle root digest, Python implementation/version/
ABI, stdlib runtime identity and every module path/type/blob/SHA256/size. The
complete transitive closure includes actions contracts/codec, config modules,
gatekeeper project/contracts/validate, package initializers, and actual msgspec
wheel artifact/version plus every loaded C-extension hash/ABI. Names absent from
this closed list cannot resolve from cwd, user site, sys.path or .pyc. A missing
extension or loader dependency holds; a .py-only manifest is insufficient.

Capture each listed source once into private exclusive files while hashing; load
Python from those captured bytes with a closed source-only resolver. Verify the
captured closure, not the original paths later. Extensions load only the captured
regular-file bytes at a private pinned path, with hash/identity checks immediately
before and after load; fixtures reject replacement/symlink/mode/ABI drift. Both
sides consume the same pair manifest: product cannot import ambient btq_actions
and queue cannot import ambient heterodyne. Do not persist sys.path modifications
or accept bytecode. This is trusted same-UID fixture consistency, not isolation
against a malicious owner who can rewrite its private files; OS containment and
production package enrollment remain separate unavailable activation gates.

### 3.6. Fixed offline port contract and actual seam boundary

P3 owns the proposed `FilingPorts` protocol in product actions; Q1 owns only queue
allocate/lookup/read/CAS/withdraw. Every call takes a closed Command containing
command ID, exact creation key/origin/generation, expected versions, source and
policy binding; every Observation repeats that binding and a closed outcome.
Readback takes the same command ID. Fixed filing operations are:

| Operation | Fixed argument / result beyond Command | Owner and compatibility boundary |
|---|---|---|
| ensure_blocker / read_blocker | approval ID and blocked task / exact associated-edge snapshot | Q1 fixture composition; no native adapter. |
| park / read_park | blocked task, exact blockers, retain-claim=true / stopped session IDs, claim holder, source and parked state | Product fixture; actual WSD seam is Parker.park(bead, blockers, why, hold, ref) and replay_park, not a new installed service API. |
| land / read_landing | requested immutable source and repository / exact source/tree and same origin parked snapshot | Product fixture; D26 real landing adapter remains unproven. |
| groom / read_groom | same logical session, generation, revision<=2, restricted synthetic capability and exact requested source / new generation/source, followed by fresh park | Product fixture only; FakeSession.park/land does not prove a restricted resume exists. |
| withdraw_shells / read_cleanup | complete retained associated IDs and required terminal disposition / per-ID terminal plus blocker snapshot | Q1 fixture plus §3.1; cannot infer unknown effect absence. |
| read_filed | exact approval ID / full native ask, digest, revision, source, blocked/parked/claim snapshot | Q1 fixture; validates actual reducers without native authentication. |

Common outcomes are observed, definitely-unissued, uncertain and unavailable,
with §3.2 phase rules; no freeform bool can stand in for those snapshots. All
ports are unavailable by default; explicit fake implementations are accepted
only by disposable test constructors. No factory installs them. At pinned
product a73e, WSD Parker.park stops actual runtime sessions and retains operation
journals/claim checks; daemon control pause/resume is workstream pickup control,
not same-session restricted grooming. Existing actions FakeSession is only a
fixture. The manifest pins these source facts. A later real adapter must produce
a source-owned mapping to these exact arguments/readbacks, prove actual supported
session/landing semantics and retain original H1/scanner/T10 gates; an incompatible
seam requires a new design decision rather than inventing a resume endpoint.

## 4. Retained findings: required fixes and explicit deferred debt

All eighteen originals remain byte-exact in the two report files. This table is
an admission action ledger, not a claim that fixes have been implemented or
reviewed. C1 owns repairs; Q1/P3/P4 tests must enforce their resulting contract.

| Report finding | Required treatment before the affected consumer is accepted |
|---|---|
| P1 R11 #1 caller-clock fallback | Require injected journal clock for timed operations; refuse missing sampler/anchor before issuance. Test forged caller clocks cannot satisfy backoff/deadlines. |
| P1 R11 #2 contradictory absence attestation | Compare all retained child evidence before settlement; contradictory succeeded evidence cannot become an absent completion. Retain conflict/HOLD; no new operator overwrite authority. |
| P1 R11 #3 unconsumed parent with observed success | Durably retain/read the exact child observation without attributing an effect claim or permitting replay. Conflicting unconsumed-success stays unresolved; no fabricated consumed claim or automatic success settlement. A later unsupported disposition remains HOLD. |
| P1 R11 #4 UTC slew | Specify/test monotonic same-boot elapsed versus diagnostic UTC and conservative discontinuity handling. Do not casually widen tolerances or reset deadlines. Any proposed policy change beyond D4 is separately surfaced. |
| P1 R11 #5 approval lookup scan | Add exact indexed approval association in fixture schema with explicit migration and duplicate/unknown refusal; no broad native schema change. |
| P1 R11 #6 capacity reserve scans | Compute/update reservation accounting transactionally without repeated per-event scans; preserve terminal reserve and full-capacity refusal. |
| P1 R11 #7 dead elapsed field | Remove or explicitly reject obsolete caller elapsed input in new fixture schema; retain old audit bytes as historical untrusted observations. |
| P1 R11 #8 denial domain version | Explicitly version canonical identity changes. Preserve old row/domain provenance; do not silently forget old denials. A migration that cannot establish the original canonical intent holds instead of guessing. No production denial migration here. |
| P2 R4 #1 concern labels | Give every closed SemanticGap kind a deliberate safe label; do not invent a target field. Ambiguous refs/context use ambiguous-target only where the actual semantics establish ambiguity. Test complete matrix. |
| P2 R4 #2 denial before allocation | Use existing canonical request denial identity before approval-ID allocation; no fake approval ID. Same-session denied intent remains inert denial. |
| P2 R4 #3 stale hard-deny policy | Validate current bound policy before persisting any denial projection; stale policy cannot poison denial history. |
| P2 R4 #4 unrelated permission subject | Bind exact subject/session/workstream before any projection is recorded against a request, including hard-deny. No cross-request denial writes. |
| P2 R4 #5 terminal requests | Refuse fresh projections for completed/cancelled/reconciled-abandoned/blocker-pending states; retained denied history is not a new decision. |
| P2 R4 #6 duplicate binding authorities | Factor/reuse shared identity validation across permission/sufficiency; check key/origin, validated envelope and cache equality consistently. |
| P2 R4 #7 deadline reason | Apply actual gatekeeper precheck to supported projection paths; expired budget gets deadline, never a misleading ready/allow or false input-profile outcome. |
| P2 R4 #8 None action diagnostic | Restore explicit typed action guard and fixed safe diagnostic; malformed host input refuses. |
| P2 R4 #9 duplicated boot label | One named synthetic fixture boot constant; never present that string as a host-attested boot identity. |
| P2 R4 #10 repeated decoding | Share checked data within one invocation, never cache across source/policy/revision changes; measure bounded fixture work. |

Precisely permitted offline deferrals are P1 R11 #4's conservative UTC false holds,
P1 R11 #5/#6's performance-only implementation details (only if the fixed limits,
reserved terminal slots and bounded tests above hold), and P2 R4 #10 repeated
decoding (only within measured bounded fixtures). Each must be individually
reported as residual debt, never activation evidence. P1 R11 #1–3/#7–8 and P2
R4 #1–9 require their table treatment and regression before affected consumer
acceptance. In particular P2 R4 #4 subject binding is NOT deferrable.

Q0 R2's ten original ordinals are preserved in the byte-exact companion report
and its source-pinned author dispositions. Q0 R2 #1 undispatched approved execution
settlement and #5 execution multiline denial remain explicit unsupported HOLD:
the pinned P1 execution event codec still rejects multiline denial even where
queue metadata permits it. Do not sanitize the decision or claim this accepted.
Q1 must verify repaired #2 immutable success, #3 terminal write refusal, #4 emoji
metadata vs identifier controls, #6 phase-typed backend errors, #7 malformed row
handling, #8 strict typed ports, #9 approve-with-denial refusal and #10 bounds.
Any later resolution of #1/#5 needs an exact accepted product/queue pair and the
source-owned contract; current candidate code is evidence, not approval.

## 5. Offline acceptance and delivery

Each target retains every original oracle and adds failing-first named tests for
its boundary. Tests use disposable SQLite, fake clocks, fake native CAS/session/
provider ports and synthetic principals. No backend credentials, shared database,
network, real model, production journal, installer, process launcher or service.
Source imports must not discover or activate a live adapter. Tests expose forbidden
calls as failures rather than relying only on a narrative promise.

C1: forged/missing anchors, conflicting retained success versus absent repair,
unconsumed-success audit progress without effect attribution, legacy denial
identity, stale policy, wrong subject, pre-allocation denial, every terminal phase,
complete concern reasons, current input/profile/deadline and cache invalidation.
Q1: whole-request cancellation/abandonment cleanup and capacity recovery only
with exact supported reducer proof; approved-undispatched HOLD; reciprocal capsule
replacement/extension/ambient-import refusal; all three supported native purpose-specific
positives; explicit confirm/recovery refusal; missing/unknown purpose; wrong-purpose
payload; exact native digest parity; stale CAS; duplicate/conflicting decisions;
closed-but-unpinned; wrong generation; source-import closure; refusal versus
uncertainty. Never load unverified extra modules or prefer stale bytecode.
P3-offline: crash before/after every intent/call/readback/checkpoint; lost create
ack; unique adoption; zero/duplicate results; shell/blocker cleanup; cap exhaustion;
new-generation spoof; source changes while parked; bounded grooming; unavailable
provider; persisted provider-intent crash/no-reroll and explicit clock/nonce
propagation; per-approval overflow isolation; full ask drift; secret/unsafe
rendering refusal. No automatic capacity
release from elapsed time or failed lookup.
P4-offline: two competing valid/invalid events; replay of invalid/superseded input;
principal revocation at each await; changed ask; write-unknown; readback advanced
by another legitimate writer; close/pin crash; heart proposal recovery; confirm/
recovery unsupported-native HOLD and internal model semantics. Advanced readback
is uncertain unless an exact proven descendant receipt justifies it; no demand for equality that licenses overwrite.

The composed fixture proves request → filed → exact synthetic decision → native
pin finalization and durable restart with at most one intended write. It must also
prove blocked/unknown paths remain held and that all five effect adapters refuse.
It proves neither a live authenticated action nor successful physical cleanup.
Each acceptance artifact records actual source pair/tree, review ordinals, named
commands/counts, static/quality debt and retained unknowns. Code review follows
current operator policy; this admission never resets predecessor review history.

## 6. Later gates and operator choices

The human ask must explicitly approve this narrow deviation from cb180's staged
graph and D4 §8: admit these four disposable offline roles despite unresolved live
queue/decision integration, and omit H1/scanner/T10 from ONLY the offline-subset
edges. This is proposed new design authority, not an interpretation that the old
approval already allowed it. If the human declines, no such subset proceeds.
Offline acceptance satisfies NONE of the original graph's P3, P4, H1, scanner,
T10 or cutover prerequisite edges. Separate actual integration targets retain all
original dependencies; do not rename or close them using fixture evidence.

Approval explicitly admits the four offline roles only. It preserves the prior
seven governing amendments, current sole supported writer until an independently
approved cutover, current identities/claims and every genuine approval pin.
No installation, posting, actual model call, origin authentication, production
schema migration, operational decision write, effector, cleanup or deployment.

Actual P3 intake requires accepted Plan5i/scanner/T10 source and authenticated
launch/generation/ownership + restricted same-session grooming interfaces. The
historical btq-bikr3/btq-1wu2i/btq-d1v9d graph is context; the controller must
verify the precise accepted source/receipt rather than using a design brainstorm
or CLOSED bit as proof. These are later integration gates, not edges that block
this independent offline batch on full receiving-scanner work.
Actual H1 requires a source-owned repository/provider contract, actual model/
schema binding and timeout behavior; no Hermes backend API is invented here.
Actual Q backend requires supported atomic CAS, reader grants and authenticated
native sources; no `atomic=True` fixture or `--readonly` flag proves that.
Actual P4 cutover must handle every approval kind and fence retired direct
writers, with live Marmot authentication/receipt compatibility proved separately.
P5 needs immutable object ingress, sole launcher, read-only cross-UID reader,
non-login effector, broker and genuine process-tree cleanup proof. P6 needs all
accepted predecessors and push/PR/merge target matrices. Deploy/notify remain
unavailable and require their separate contracts. Delegated operations additionally
require genuine S/Z and finite operational authority; this design grants none.

If a real interface cannot realize D4 safely, return an exact typed unavailable/
recovery-needed outcome and surface the missing contract to the controller. Human
questions and new authority go exclusively through Marmot; an unavailable Marmot
path holds with read-only diagnosis, never terminal consent or borrowed identity.
Approval of this design is not approval of a future deployment. A later deploy
ask must bind exact source/config/installed-state differences, backup/migration,
rollback limits, cutover, live scratch scope and direct immutable design links.
