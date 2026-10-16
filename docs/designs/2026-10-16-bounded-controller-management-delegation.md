# Bounded controller management delegation d2

Design task `btq-so7ma`, Codex author, 2026-10-16. **Proposed only:** no grant,
implementation or deployment. Actual Claude R1 requested revision (42 findings);
its report and consumed round remain preserved. Two rounds remain. All questions
and consent use authenticated Marmot/admind, never terminal/chat substitutes.

## 1. Scope, prerequisites and trust

The operator wants blocking approvals for changed/new designs and deployments,
without a fresh blocking question for each routine controller operation. This
amendment adds a finite delegated mode alongside unchanged per-request management
in [historical d2](../reviews/approved-inputs/2026-10-15-operator-task-management.md).
It does not give an open-ended controller mandate: unlisted/reactively discovered
repairs still require existing per-request authority or HOLD. A new finite grant
is itself an explicit operational authorization through Marmot, not an extra code
review or a design change when the immutable rules and installed code are unchanged.

[Inputs](../reviews/bounded-controller-delegation-inputs.json) and
[R1 dispositions](../reviews/so7ma-r1-dispositions.md) distinguish historical
contracts, observations and proposals. `btq-zcd1l` d2 is a **proposed prerequisite**
for nonimplementation kinds; no approval or deployment is inferred from its
signed source. Resolve its genuinely approved/deployed compatible revision first.
Implementation task pins and native final approval blockers remain mandatory.
The historical controller integration design at full `3158263d049ea0a6b5cd398b40df95b704836d4d`
in the separate heterodyne-metaharness repository governs lifecycle evidence,
not delegated authority. Its source prose alone is not approval evidence; deployment
must retain genuine native approval provenance. This amendment never resets review
history. Count distinct actual **issued** model invocations, including failed,
aborted, running and unresolved issuance; proven unspawned reservations differ.
Unknown history holds. No permission recovery manufactures additional review slots.

Existing admind authenticates local control peers by same UID; run/session/host
assertions and executable digests establish consistency within that trusted local
host boundary, **not unforgeable credentials against hostile same-UID processes**.
The daemon and local executor must remain trusted. A hostile same-UID peer could
impersonate a controller or consume its exact slot; this feature does not claim
OS isolation or introduce another executor user. Human authority still comes only
from authenticated current operator messages, never a caller/body/model claim.

Implementation requires new daemon/Store methods, a scoped Queue/Management
adapter and compatible pinned Hermes management installer. Every API below is
**proposed**, not currently callable. Code review and explicit deployment approval
must precede use. Current explicit operator merge authority supersedes historical
additional master-go wording; no new per-merge question is imposed. New/changed
designs and deployments retain their real approvals, links and deviations.

## 2. Exact descriptor, facts and canonical bytes

Separate public artifact from private evidence. Public signed descriptor contains
only opaque subject/repository/store identity digests, task IDs/kinds, typed deltas,
nonsecret concise reasons, evidence hashes, finite time bounds, source/design
links and release/policy fingerprints. Raw native snapshots, host/session IDs,
worktree/process paths, notes/comments, external-effects reconciliation and signing
material remain private mode-0600 evidence. Both are screened **before publication**;
raw facts are never blindly copied into public Git. Sensitive reason/scope cannot
be safely replaced with an unexplained digest: refuse publication/activation and
use a narrower adequately disclosed request. Exact private evidence is retained
for authorized verification and binds the public descriptor by digest.

Closed schema `controller-management-grant-v1` has exactly:

- `version` integer 1; random public `grant_id` (32 lowercase hex, uniqueness only,
  not a bearer secret); `subject` with agent plus host/run/session/workstream/store/
  repository and lane identity digests; `release` with exact installed queue,
  adapter, wrapper and admind closure identities and approved design/reviewed-candidate references;
  `design` with full commit/path/blob/SHA256 and genuine approval reference.
- `policy`: separate SHA256 of normalized queue approver policy and admind operator/
  approver policy, explicit name-to-pubkey mapping and sorted eligible policy principal
  pubkeys. Every queue-policy approver must map uniquely to a current confirmed
  admind principal permitted by fresh admind policy; missing/duplicate/extra ambiguous
  mapping refuses. Changes to either fingerprint or required membership hold grants.
- `not_before` and `expires_at`: exact UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`, parsed
  to integer epoch microseconds, inclusive start/exclusive expiry. Interval >0 and
  <=24h. No leap seconds, offsets, variable precision or string-order comparison.
- `operations`: ordered 1..32 entries. Derive task set, count, operation classes
  and receiving lanes from this array, with no redundant caller-supplied lists.
  Each has unique 32-hex `operation_id`, unique existing task ID, exact native kind,
  derived class, repository/lane binding, reason, `before_hash`, `after_hash`,
  `endpoint_role_facts_hash`, `private_facts_hash`, and exactly one typed `delta`.
  No wildcard, predicate, future task, alternative delta or floating-point value.

Canonicalization is UTF-8 without BOM, JSON sorted keys recursively, compact
`,`/`:` separators, `ensure_ascii=false`, no trailing newline. Strings must be
Unicode scalar values (reject surrogate code points), NFC already normalized
(reject rather than rewrite). Keys are fixed ASCII. Reject duplicate keys,
unknown keys, floats/NaN/infinity, negative/out-of-range integers, unexpected null,
empty/duplicate IDs and unknown versions. Arrays preserve operation order; sets
are sorted and unique as schema specifies. Schema integer domains are explicit:
version=1 and ordinal=0..31; no arbitrary numeric fields. SHA256 covers **all exact
canonical descriptor bytes**, with no embedded self-hash or self-signature.
Signed Git commit/path/blob bind these bytes; installed allowed-signers trust must
verify signatures, never enroll a key because the artifact names it. Keep golden
canonical vectors, including non-ASCII, duplicate keys and surrogate rejection.

Order avoids circularity: approve mechanism design; review exact candidate bytes;
prepare private facts and descriptor pinning those fixed identities; sign/publish
descriptor; then trusted daemon rendering produces either the deployment card with
explicit finite operational grant, or a later standalone operational grant card.
One deployment decision may approve both exact installation and disclosed grant;
activation waits for its actual deployment receipt proving those same bytes.
Descriptor contains neither its own containing commit nor future deployment/
activation decision ID; daemon activation envelope binds those later. Generic
design/deployment approval without explicit grant disclosure grants no operations.
A separate activation card is allowed for new scope, not mandatory due to ordering.

## 3. Closed operations and sequential native states

| Operation | Admitted transition |
| --- | --- |
| Status | blocked/unassigned -> open/unassigned only; no closed reopen |
| Dependency | exact typed add/remove, or add-new-then-remove-old with distinct endpoints, preserving all genuine permission/approval prerequisites |
| Reclaim | one named freshly confirmed ended local holder -> named controller own receiving lane, status unchanged |

Always forbid approval/confirm/question targets, routing/kind/parent/metadata/pin
changes, permission-gate removal, closed destination, in_progress assignment except
existing reclaim semantics, direct DB writes, worktree deletion, history edits,
review-counter reset, merge/deploy/service/policy/member changes and arbitrary
commands/messages. An approval endpoint can only be added/preserved as a blocker.
Same-endpoint edge-type replacement is excluded: native one-edge-per-pair behavior
cannot safely promise add-before-remove. Unknown native/protection roles refuse;
use the separately deployed Z classifier and exact data-only endpoint purpose facts,
never title-only role inference or approval-card waiver of known gate evidence.

Descriptor preparation reads the **whole ordered graph plan**. At operation i,
before facts equal initial facts plus only the exact verified deltas of earlier
operations; endpoint reverse edges/counts follow that same prediction. Verify the
predicted progression mechanically before posting, not independent incompatible
initial snapshots. Exact unrelated comments/content drift still holds; this is an
intentional availability cost. Only deterministic fields changed by the recorded
native steps (including native update timestamps with verified associated delta)
are normalized under the same explicit Z readback contract, never blanket ignored.

Dependency operations require the target already parked blocked in its exact own
controller lane, or another state explicitly supported by the deployed management
contract. V1 uses **blocked/assigned-to-own-lane** only. Descriptor binds that state;
it does not park, release or unpark automatically. Status unassigned source and
reclaim holder source use their distinct predicates. Preparation refuses incompatible
states rather than quietly modifying them before grant execution.

Reclaim retains exact reason, worktree/path identity digest, dirty/unpushed/pushed
Git state, commits/PRs and external effects reconciliation, local host/boot/PID/start
identity and confirmed cessation evidence. Alive/unknown/remote/inaccessible refuses;
age/stop request is insufficient. Preserve receiving/holder locks, native reservation,
fences, lane capacity, pins and worktree history. Multiple listed reclaims are an
explicit amendment to OPS's manual one-bead-at-a-time workflow **only as a finite
sequence**: at most one operation executing across the grant, one actual reclaim
critical section at a time, each fully read back before the next. No bulk command.

## 4. Trusted preparation, disclosure, delivery and consent

New local command `admind delegation prepare --descriptor-ref REF --facts-file FILE`
requests daemon validation, trusted rendering and durable registration. It accepts
only verified canonical artifact bytes/allowed signature, current deployed identities,
complete matching private facts and a currently valid scope. A combined deployment
request pins the reviewed candidate rather than falsely claiming it is installed. Caller-provided card
body/choices are forbidden. Registration reserves unique grant ID and generation;
unknown duplicate, superseded or revoked IDs cannot be activated. An unposted
registration may be abandoned; tombstone persists. Changing bytes requires a new
ID and question. This is not installed CLI syntax yet. For first installation, the reviewed candidate's
pure `admind delegation render --offline --descriptor-ref REF --facts-file FILE`
produces the same closed render bundle without daemon writes. Existing supported
ask posting delivers that exact body through admind; after deployment, proposed
`prepare --existing-ask ID` imports it only by independently reproducing all body,
choice and attachment bytes and verifying actual stored delivery/decision. No trust
is assigned to the poster's assertions. A changed body or unsupported attachment
refuses import. Offline rendering/posting does not activate or reserve anything.
The deployment card can therefore also grant finite scope without needing the new
RPC before its own installation; activation still waits for actual deployed proof.

Trusted daemon renderer displays every task/kind, exact operation/reason, before/
after ownership or edges, stopped-holder and reconciled-worktree evidence summary,
controller/run scope, expiry, both policies/required approvers, source links,
exclusions, no fresh covered-operation questions, same-UID trust and five-second
initiation limit. Show public grant nonce, 12-hex digest prefix and immutable GitHub
artifact link; exact full hash is structured attachment data, not free text that
triggers existing high-entropy secret heuristics. Context-specific nonce/hash
validation is not a generic secret-scanner bypass. Run existing escaping, secret
and link checks on all human prose and structured allowlisted digest fields.

Use one existing question body only, <=40 lines and <=3500 characters, including
forms/footer; actual renderer must satisfy existing narrower deployed limits too.
No claimed multipart question API. Pure render preflight before publication ensures
all mandatory facts fit; if not, reduce operations **before signing/publishing**.
32 is a hard ceiling, not a guarantee a 32-operation card fits. Never truncate,
post a partial card or hide operation facts in optional More/digests. Mechanical
scope reduction creates no new model invocation or design review round.

`prepare` persists exact trusted rendered-body/choices/attachment digest. Proposed
`controller-management-grant-v1` attachment binds canonical descriptor bytes,
commit/path/blob/hash, private-facts digest, eligible policy principal set and action.
Do not set ask.targets to replace existing implementation primary pins. Bind the
real generated question ask ID and outbox part/receipt. Consent eligibility begins
only after every required card part has confirmed sent delivery; v1 is one part.
An answer processed before the durable delivery event cannot count: require fresh
explicit answer afterward, based on Store sequence ordering, not remote timestamps.
Receipt success is delivery evidence, not human consent.

Choice A explicitly activates this exact descriptor; B leaves paused/declines.
No recommended activation reaction: require explicit textual A with exact grant
nonce in the registered thread (`A <grant_id>`); reactions/free text do not grant.
Serialize native authenticated answer intake and activation in the daemon work lock
and Store transaction order. At activation, at least one current mapped queue-policy
approver must have explicitly answered A after delivery, and **no recorded non-A**
from any mapped policy approver may exist. Snapshot all eligible answer sequence
IDs in the activation envelope; no first-answer/last-answer selection. This preserves
historical Handoff.check_ask semantics, without inventing an every-operator consent
gate. Any authenticated contrary answer or withdrawal tombstones pending generation
or transactionally revokes active grant; later A never erases it. This also applies
to a combined deployment-and-grant card. Changed policy, loss of every consenting
principal's current authority, or contradictory history holds. New generation needs
new genuine operational authority; ordinary unanswered principals do not block.

## 5. Proposed daemon API and revocation

All RPCs have closed versioned schemas, <=64KiB request, bounded responses and
exact actor consistency tuple. All identifiers request_id/operation_id/step_id/
attempt_id/event_id/reservation_id/permit_id are 32 lowercase hex; hashes are
64 lowercase hex; counts/revisions are nonnegative signed64 integers; boottime
values are nonnegative integer microseconds. Ask IDs use the actual bounded native
ask grammar, not grant-ID coercion. Outcome/state enums below are closed; unknown
fields, omitted required fields, duplicate keys and schema/type mismatch refuse.
Receipts return only identifiers/state/revision, policy/release hashes, boot/epoch,
deadlines and bounded refusal codes, not arbitrary task data. Responses bind daemon instance epoch, descriptor,
request/operation/step IDs, journal revision and current state. No caller receipt
file is authority. CLI renders HOLD with exact reason; no synchronous human prompt
inside reserve/permit/native execution. Existing per-request mode is unchanged.

| New RPC | Exact semantics |
| --- | --- |
| `delegation.activate(grant_id, descriptor_hash, request_id)` | Idempotently recompute trusted artifact/render/delivery, current nonconflicting policy-approver consent, no tombstone, current release/policies/confirmed membership, no membership mutation/latch, stable epoch/time and valid interval. Register immutable activation envelope and return active or HOLD; caller supplies no decision assertion. |
| `delegation.status(grant_id, descriptor_hash)` | Return registered/awaiting_delivery/awaiting_consent/active/held/revoked/expired/superseded, revision, activation ask, eligible policy principals and operation ledger. Reads never grant execution authority. |
| `delegation.reserve(grant_id, operation_id, request_id, before_hash)` | After successful local preflight, atomically reserve exact next ordered operation; request ID durably exists locally before RPC. Identical retry returns same reservation, conflicting binding refuses. >=60s grant lifetime required; no refund/reset. |
| `delegation.permit(reservation_id, step_id, attempt_id, before_hash)` | Fresh active authority, policy, time, revision and prior-step verification. Return at most 5s permit clipped to grant expiry, unique permit ID, UTC deadline and server boottime deadline/boot ID. One live attempt per step. |
| `delegation.renew(reservation_id, step_id, prior_permit_id, attempt_id, no_native_intent_hash)` | Only an expired unstarted permit with exact durable executor proof of no native-intent record, same epoch and unchanged preflight. Atomically retire old attempt, issue new 5s attempt; at most 3 total permit attempts/step. No operation refund, no grant extension, no renewal after intent/uncertainty/revoke/expiry. |
| `delegation.settle(reservation_id, step_id, event_id, evidence_hash, outcome)` | Append idempotent observational result: verified_exact_after, verified_no_intent, or unknown. Same event/hash retry stable; conflicting event/hash holds as integrity error. Original enrolled trusted actor may settle/read after expiry, policy change or revoke; this grants no effect. Later read-only evidence may upgrade unknown to verified_exact_after, preserving both events. |

Proposed daemon Marmot grammar: `!grant revoke <32hex-grant-id>` in the registered
authenticated operator group, or `!grant revoke` as a reply to its bound card.
At most80 characters; exact ASCII grammar, bounded lookup, idempotent inbound
message ID. Check fresh current sender authority and exact group at intake.
**The daemon itself atomically records authenticated command/answer and monotonic
revocation/tombstone in one Store transaction before acknowledgement**, including
registered not-yet-active grants. It does not post a second approval question or
wait for a local client to redeem an answer. Any current mapped queue-policy
approver may withdraw; mapped-policy-principal B/withdrawal in the activation thread
uses this same transaction. Current-authorized withdrawal is never undone when
that sender later loses membership. No free-form/foreign-group/unknown-ID revocation.
`!grant status <id>` returns bounded state/expiry/outstanding steps; help and the
activation card give exact revoke instructions. These are proposed command-handler
extensions, not claims about today's verbs parser.

No new unsolicited audit-notice channel is authorized. Existing native/local durable
audit remains; only bounded registered card delivery, command acknowledgements and
status responses through existing admind outbox are part of this protocol. Rate
limits/deduplication reuse native inbound and outbox controls; failures remain
queued, never forge successful delivery or undo committed withdrawal.

## 6. Time, revocation boundary and rollback protection

Reserve/permit require both fresh policies, at least one consenting policy approver still confirmed and all policy mappings valid,
no pending membership uncertainty, healthy daemon latch and exact installed closure.
Status cache is never effect authority. Revocation linearizes at daemon transaction:
no later permit/renewal is issued. An earlier valid permit may initiate its exact
native step within its original <=5s deadline. Completion may occur later. This
maximum initiation window is disclosed; zero-after-revoke needs a separately
designed native atomic authorization mechanism, not a stronger promise here.

Use Linux CLOCK_BOOTTIME (includes suspend) and UTC microseconds, recorded before
RPC and checked after response. Require common verified host boot ID; never compare
unrelated monotonic epochs. Grant validity requires both UTC window and boottime
elapsed allowance derived at activation. Clock sample divergence >1s from the saved
UTC/boottime offset in either direction permanently holds that grant. Slow/suspended
RPC reduces remaining permit lifetime; no local completion resets it. Check both
deadlines at the **actual native write dispatch**, not before worktree/lock setup.

Every daemon startup establishes a new random instance epoch and atomically
invalidates **all prior-epoch grants/permits**, even if DB restored to an old active
snapshot. Never resume a persisted active flag. Executor pins the live epoch and
holds on changes. This deliberately sacrifices seamless daemon restart; fresh
finite operational consent is required after restart. Live database rollback is
unsupported: SQLite remains daemon-owned; restore procedure must stop the service,
then startup invalidation applies. Backup tools cannot authorize live replacement.
Durable deployment-owned epoch guard outside the backed-up database is advanced
and fsynced before accepting delegation RPCs; missing/changed/rollback guard or
unexpected journal incarnation holds delegation. It contains no grant authority. Every authority-changing journal transaction first
fsyncs a next guard sequence/hash intent outside SQLite, then commits that exact
sequence/hash with the database transition, then finalizes the guard. RPC checks
require matching finalized guard/journal heads. Any crash/mismatch holds all grants;
no automatic head repair accepts an older database. Deployment/backup procedures
preserve the guard separately and never rewind it. Restart invalidation and explicit
recovery establish a new epoch with old grants invalid. This two-phase guard favors
HOLD over availability; it is not a cross-filesystem atomic transaction.
It detects partial/live snapshot substitution; simultaneous hostile restoration of
all host state is outside trusted-host scope, not claimed prevented. Clock recovery
uses authenticated Marmot diagnostics/repair authority for a stable new epoch;
old held/revoked/expired grants never revive. No local clock edit grants consent.

## 7. Executor state machine and exact native boundary

Acquire existing controller/receiving/holder locks in native order, pause pickup,
verify worktree/Git/external effects, liveness, capacity, pins, endpoint roles and
full predicted before state **before reserve and before native-intent**. Persist
request ID and complete canonical facts first; lost reserve reply retries that
same ID. Never generate another ID to evade consumed/ambiguous history. One grant
operation at a time; native lane capacity remains an independent bound.

Use a new scoped Queue subclass in the management adapter, not a generic caller
command hook. Its `bd_op` override intercepts an exact allowlist of expected mutation
argv, target and step hash; read-only calls remain ordinary. Existing `Queue.bd_op`
is the subprocess boundary used by Management._mutate_steps and Handoff.move.
Do not depend on entry into Handoff.move as the authorization instant: it can do
worktree/fence/reservation work before its `bd_op`. Proposed adapter must hold
all established locks and perform the last exact native-facts checks **before**
requesting a permit. Inside override, validate permit/deadlines, fsync native-intent,
then recheck boottime/UTC immediately before `subprocess.Popen` of the exact native
write. No awaited/RPC/worktree work may intervene. If fsync used up the window,
record intent_not_dispatched and HOLD; do not renew/reissue automatically. Native
process creation is the defined initiation boundary; scheduler and DB completion
are not atomically tied to remote revocation. Preserve timeout/unknown handling.
Never relax native gates or permit arbitrary argv to obtain this boundary.

Durable states: prepared -> reserved -> permitted -> native_intent -> dispatched ->
verified/unknown -> settled. `permitted` is a daemon authorization, distinct from
executor `native_intent`. Preflight refusal records HOLD before intent; expired
unstarted permit can use bounded renew only with proven absence of intent. Any
native_intent, even crash before dispatch, prohibits automatic second invocation.
After dispatch/timeout/crash, recover by read-only full readback and settle; never
infer retry authority from apparently unchanged state. Unknown effects retain
fence/reservation/capacity and paused pickup until exact evidence or a **new scoped
Marmot management card** authorizes specific reconciliation. No blind refund/abort.

New authority_mode=per_request|delegated request schema rejects mixed/old structures.
For reclaim, preserve real Handoff fence/reservation fields: its `ask` is the actual
activation question ID, never grant ID or fabricated ask. A management-only resume
router reads version/mode and verifies the grant ledger instead of misapplying
legacy check_ask to this card. Existing records keep original per-request routing;
old readers refuse the new version. Delegated resume distinguishes native Handoff's
prewrite `moving` phase from the new durable native_intent: `moving` alone never
proves dispatched. Only a delegated mode with proven absent intent may resume
preflight/permit renewal; intent present always takes read-only recovery. Do not
route new records through old Handoff.recover_moving heuristics blindly. Existing real note/current/reservation/fence
readbacks and corresponding scoped audit mutation still use their correct IDs.
Each operation has a mechanically derived closed step plan (at most three native
writes: two dependency edges plus one audit; reclaim note/assignee is one atomic
update plus its bounded existing audit). Arbitrary additional steps refuse.
Audit write is a separately enumerated protocol step with same intent/permit
rules; descriptor predicts its exact request-marked note/comment delta. It is not
an unbounded message-send exception or another task operation slot.

Dependency replacement verifies add first, then obtains a new permit for remove.
Expiry/revoke/drift between them retains the added edge, pauses and records partial
state; no rollback fiction. Reclaim's note+assignee native update remains one write,
followed by native existing local-state/fence completion with exact readback. No
automatic pickup/unpause/worker launch is included.

HOLD is recoverable without effect authority: proposed `manage inspect --request ID`
and `manage settle --request ID --evidence-file FILE` perform read-only native
inspection and append observational evidence. A terminal verified/unknown journal
can be archived as `superseded` only retaining immutable consumption and any native
fence/reservation until existing authenticated management reconciliation proves
safe release. New grant cannot bypass unresolved native ownership/reservation.
Do not make every non-effect diagnostic require a new permission card; only a new
mutation beyond the old finite grant needs genuine existing operational authority.

## 8. Delivery plan and executable acceptance contract

Implement after actual design approval: (1) pure schema/render/prediction and closed
state reducers; (2) Store migration/daemon commands, epoch guard and typed RPC;
(3) management-only boundary subclass/resume/versioning; (4) pinned wrapper/installer
closure including all new adapter/daemon sources, exact compatibility with native
bin/btq; (5) named offline acceptance and genuine code review; (6) explicit deployment
card with direct design link and all deviations. No live initial grant implicitly.
If boundary subclass cannot preserve native invariants, HOLD implementation and
amend design rather than silently patch unreviewed controller identity contracts.

Required disposable offline tests (future acceptance, **not run for this design**):

| Boundary | Required assertions |
| --- | --- |
| Canonical bytes | UTF-8 golden vector, unicode/surrogates, duplicate/unknown keys, null/float/NaN, malformed UTC/IDs, signature mismatch; zero effects |
| Bounds | 0/33 tasks or ops, duplicate task/op, >24h, inverted/exact expiry, parent/kind/routing/closed/gate changes refuse |
| Consent | one or more current policy approvers A; no A, A then B, other-principal B, reaction, pre-delivery A, superseded/pending-revoked ID never activate |
| Disclosure | exact rendered bytes/choices/attachment; hand-written understated card, oversized32 ops, secret/path/nonce/hash handling, partial/failed receipt refuse |
| Policies | separate queue/admind changes, mapping collision, absent confirmed principal, unknown membership and latch -> HOLD |
| Revocation | phone command commits directly, duplicate inbound, removed-later sender, preactivation withdrawal, lost acknowledgement; no later permits |
| Time | RPC delay, suspend, UTC forward/back >1s, exact 5s boundary, fsync crossing deadline, daemon restart, stale DB backup, missing guard; no cached grant |
| Native | full preflight/cap/holder lock before reserve/intent, wrong argv, reclaim worktree/PR effects, predicted endpoint progression, type-only replacement refusal |
| Crash | failure before/after reserve/permit/intent/Popen/readback/settle/audit; same ID retry, no automatic issued retry; three total permit attempts max |
| Recovery | policy/revoke/expiry allows observational settlement, conflicting evidence hash holds, unknown->verified append only, retained capacity and explicit reconciliation |
| Compatibility | native approval/pins/history/review cap and old Handoff ask routing intact; no imaginary current APIs or queue DB writes |

Named fake native/admind/clock tests must cover each branch without live queue DBs,
broad discovery, live scans/sends or model invocations. Human acceptance must include
finite nonreactive scope, same-UID risk, nonconflicting authenticated activation, restart invalidation,
clock holds and <=5s initiation boundary; no author-only acceptance of these risks.
