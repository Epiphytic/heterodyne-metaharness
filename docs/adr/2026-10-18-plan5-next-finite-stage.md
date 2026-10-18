# Plan5 next finite offline stage admission

Author: Codex. Status: initial UNREVIEWED design for btq-voe5f. No implementation,
consent, deployment, or completed Plan5 milestone is represented by this document.
The controller will obtain one actual HIGH design review, incorporate its findings,
publish the resulting exact design, and seek genuine Marmot design approval before
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
assert its merge or hosted CI completion. Q0 candidate is
`f80412a853a2b81ec87d3e7fbf18c6c9c91f7cb2` in the queue repository, pending
its controller-owned second full HIGH review at research time. These are source
inputs, not installed capabilities. Exact consulted blobs and SHA256 values are
in `2026-10-18-plan5-next-finite-stage-inputs.json` beside this file.

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
must name the actual integrated source and review/test evidence; no worker merges
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
Q1's reusable bridge verifies the exact admitted product-source manifest and
closed Python dependency closure before import, without accepting extra importables,
bytecode substitution or persistent hostile sys.path changes. Supplied fixture
source is explicitly synthetic. It loads no origin/native authentication and
has no default factory, runtime command or automatic installed-source discovery.
The existing Backend/Contracts protocols remain supplied explicitly. A missing
atomic or purpose capability refuses without creating a shell or mutating state.

P3-offline retains a durable command/observation cursor per creation key. It
commits intent before each potentially mutating injected port call; no fixture
acknowledgement precedes durable admission. Resume re-reads the owning reducer
and exact port readback. It does not restart a possibly issued create/write.
Only typed pre-effect Refused is a proven refusal; Uncertain, timeout, lost reply
and other possible post-issue failures allow readback only. Validate origin,
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
remain: two outstanding per workstream/bead across generations, 128 fixture-wide,
4096 retained requests, 16384 events; full storage retains HOLD and history.
Coordinator step budgets cannot multiply reducer retries after restart. Duplicate
no-op deliveries do not append unbounded events. Fixture clock samples come from
the journal's injected sampler, never a proposal's timestamps. Retain boot and
monotonic provenance at issuance and every timed observation; missing/legacy
unproven anchors hold. This does not create physical execution or cleanup proof.

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

#4's conservative false holds and remaining bounded performance debt may be
recorded as noncritical offline PoC limitations if not changed. They cannot be
waived into activation evidence. Safety-affecting #1–3 and denial/binding issues
must have explicit regression coverage before composed acceptance. Changes that
need new authority must remain unavailable rather than being hidden in a helper.

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
Q1: all three supported native purpose-specific positives; explicit confirm/recovery refusal; missing/unknown purpose; wrong-purpose
payload; exact native digest parity; stale CAS; duplicate/conflicting decisions;
closed-but-unpinned; wrong generation; source-import closure; refusal versus
uncertainty. Never load unverified extra modules or prefer stale bytecode.
P3-offline: crash before/after every intent/call/readback/checkpoint; lost create
ack; unique adoption; zero/duplicate results; shell/blocker cleanup; cap exhaustion;
new-generation spoof; source changes while parked; bounded grooming; unavailable
provider; full ask drift; secret/unsafe rendering refusal. No automatic capacity
release from elapsed time or failed lookup.
P4-offline: two competing valid/invalid events; replay of invalid/superseded input;
principal revocation at each await; changed ask; write-unknown; readback advanced
by another legitimate writer; close/pin crash; heart proposal recovery; confirm/
recovery unsupported-native HOLD and internal model semantics. Advanced readback is uncertain unless an exact proven
descendant receipt justifies it; no demand for equality that licenses overwrite.

The composed fixture proves request → filed → exact synthetic decision → native
pin finalization and durable restart with at most one intended write. It must also
prove blocked/unknown paths remain held and that all five effect adapters refuse.
It proves neither a live authenticated action nor successful physical cleanup.
Each acceptance artifact records actual source pair/tree, review ordinals, named
commands/counts, static/quality debt and retained unknowns. Code review follows
current operator policy; this admission never resets predecessor review history.

## 6. Later gates and operator choices

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
