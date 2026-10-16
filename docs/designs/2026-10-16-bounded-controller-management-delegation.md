# Bounded controller management delegation d3

Design task `btq-so7ma`, Codex author, 2026-10-16. **Proposed only:** no grant,
implementation or deployment. Actual Claude R1 requested revision (42 findings) and R2 requested revision
(24 findings) on `251d6b5fbb53ab2385195586c9a3dee22d76a6b4`. Both actual
issued rounds and reports remain preserved; only final R3 remains. All questions
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
[R1 dispositions](../reviews/so7ma-r1-dispositions.md) and
[R2 dispositions](../reviews/so7ma-r2-dispositions.md) distinguish historical
contracts, observations and proposals. `btq-zcd1l` d3 is a **proposed prerequisite**
for the shared management journal/dispatcher and all endpoint role classification,
including implementation tasks, as well as nonimplementation kinds. No approval
or deployment is inferred from that proposed revision. Its final R3 at
`30a904b10c30a386c5867e6d7b8cd8f494b33ca0` returned REVISE16; all three Z rounds
are consumed and any d4 correction remains unreviewed pending exact Marmot resolution.
Resolve its genuinely
approved/deployed compatible revision first; this design extends that one surface.
Implementation task pins and native final approval blockers remain mandatory.
The historical controller integration design at full `3158263d049ea0a6b5cd398b40df95b704836d4d`
in the separate heterodyne-metaharness repository governs lifecycle evidence,
not delegated authority. The original management d2 is genuinely approved by native
`btq-ovvzt`, Liam at `2026-10-15T19:58:20Z`, ADR
`391516cd919234e69626970cb2e61b94299ed60e`, digest
`64ff5d9c77c75a9c69c8ba6d0c6aa28f74bb84f5fe354973dd94704a93c05f31`.
Its historical channel was cli recording the user’s approval while admind was
latched, not a fabricated Marmot reference. Scope was implementation/offline only,
excluding deployment/master merge/recovery; archive pending prose is historical.
Its source prose alone is not approval evidence; deployment
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
designs and deployments retain their real approvals, links and deviations. Human
Marmot approval of this changed design ratifies this mechanism; no local manifest
can manufacture that decision. Terminal access permits read-only diagnostics and
repair already covered by exact existing authority only. It never substitutes for
Marmot consent, including exceptional recovery. Durable unanswered asks remain in
admind/outbox during outages; legacy terminal-consent hints are superseded.

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
version=1; array index is the implicit ordinal 0..31, not an accepted field. No
arbitrary numeric fields or explicit ordinal key. SHA256 covers **all exact
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
For every removal/replacement, the same grant card must visibly present each
endpoint purpose, decisive native excerpt and protection inventory required by Z.
Exact full facts digests are bound by the immutable signed descriptor link and
private canonical reproduction manifest; the displayed12-hex digest is only a
human correlation hint, never the apply/authorization comparison. This is an explicit finite alternative to Z’s per-operation
card, not an omission/waiver; unknown/genuine gates still refuse. If these facts
do not fit, reduce the operation set before signing.

Descriptor preparation reads the **whole ordered graph plan**. At operation i,
before facts equal initial facts plus only the exact verified deltas of earlier
operations; endpoint reverse edges/counts follow that same prediction. Verify the
predicted progression mechanically before posting, not independent incompatible
initial snapshots. Later operations are admitted only after every preceding
operation is verified complete. A held, declined, no-intent-aborted, expired or
unknown earlier operation blocks the entire suffix; no skip or refund. A new
finite descriptor/card may cover the remaining actual state after safe settlement.
The card explicitly discloses this serial suffix dependency. Exact unrelated comments/content drift still holds; this is an
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
`prepare --existing-ask ID` imports it only by independently reproducing all body, choices, immutable links and the
closed local render-manifest bytes against the actual stored ask. Existing AskPost
has no attachment field: none is sent or inferred. A body mismatch or unsupported
shape refuses import. Offline rendering/posting does not reserve or activate.

Trusted renderer displays every task/kind, exact operation/reason, before/after
ownership or edges, Z endpoint-purpose/protection facts, stopped-holder/reconciled
worktree summary, controller/run scope, expiry, both policies/eligible approvers,
source links, exclusions, no fresh covered-operation questions, same-UID trust,
serial-suffix blocking, restart invalidation, likely clock holds after NTP/suspend,
and five-second initiation limit. Show public32-hex grant ID, informational12-hex
descriptor digest prefix and exact immutable GitHub blob link pinned to its40-hex
signed commit/path. Full64-hex descriptor/facts digests remain in canonical local
render manifest and linked descriptor, verified exactly; no authorization uses the
short display prefix. This existing LinkSpec form survives the old daemon’s hex
redactor. Do not put bare64-hex text in old question bodies or claim a typed field
can bypass the unchanged daemon. All body/choices/links use existing checks; no
secret-scanner bypass or attachment prerequisite is needed.

A combined deployment+grant uses existing **question** AskPost with A=approve exact
deployment and disclosed finite grant, B=decline/leave paused, recommended B. It is
not native kind:approval and sets no bead targets/pins; the real stored authenticated
question decision is retained by deployment verification. Native design approval
Beads remain separate genuine prerequisites. The trusted body includes design link,
reviewed release closure and deviations (including explicit none), plus every grant
fact above. Pure renderer enforces **36 lines/3200 characters including generated
form/footer**, within native40/3500, on the entire combined output. Reduce operations
before signing if necessary; 32 is only a ceiling. Require typical one-operation
combined and standalone positive fixtures. No multipart, optional hidden facts or
partial post. If even one safe operation cannot fit, standalone later scope is the
supported bounded alternative, not an invented transport. No mandatory new card
merely due installation order.

Local closed `delegation-render-v1` manifest records canonical descriptor digest,
commit/path/blob, private-facts digest, exact body/form/choices/recommended/links
and their hash, policy mapping and render candidate identity. It is data for
reproduction, not an AskPost attachment or authority. Store binds the actual ask ID
and sent card receipt. New daemon adds one transactionally ordered delegation
event sequence containing card-delivery and eligible answer events, not comparisons
between existing outbox.seq and ask_answers.seq. Only A events after that durable
delivery event count. Receipt proves delivery, never operator acceptance.

**First-install exception requires a fresh reply, not a new question:** old Store
cannot prove delivery-before-answer. On import after deployment, record an immutable
baseline containing actual delivered card proof and the complete existing answer
IDs/hashes. No pre-baseline A activates. The original card clearly says deployment
may use its existing authenticated A, but grant activation requires another literal
A in this **same thread after deployed import is acknowledged**. Proposed import
queues a bounded same-thread status acknowledgement identifying the baseline and
fresh-reply instruction; it is part of this requested card lifecycle, not unsolicited
messaging. Import alone is not the eligibility boundary: the new daemon must verify
that this post-install acknowledgement was actually delivered and record its ordered
delivery event. Only a fresh A event strictly after that event can activate; an A
received while acknowledgement delivery is pending remains ineligible. Do not infer
this ordering from stock timestamps. Keep the question open/answered; do not post another approval card.
Existing contrary answers are never erased by the baseline. Any unknown old history,
failed delivery, refusal or inability to reproduce import holds; no inferred order
from second-resolution timestamps. This extra first-install consent message is an
explicit availability/interaction cost, not a hidden automatic grant.

Before the new daemon is installed, the card instructs **B reply to its existing
thread or existing `!answer ASK B`**, and requires admind’s recorded acknowledgement;
`!grant` is explicitly unavailable then. Import checks every stored answer and
terminal ask status; B/any non-A tombstones. Old answer-limit exhaustion/refusal
means no grant import, never presumed consent. Do not cancel/supersede the card
before import; ambiguous status/answer history HOLD. After installation, new revoke
commands and priority withdrawal intake below apply even when ordinary ask limits
are exhausted or the ask is terminal.

Use existing A/B mapping exactly: recommended B means thumbs-up **declines**, as
shown by the existing footer. Literal A (no grant nonce appended) selects A;
`A <grant_id>` is not the syntax. Every mapped policy principal’s recorded answer
whose mapped choice is not A, including free text/unknown reactions/nonce-bearing
text, is contrary, matching legacy conservative check_ask semantics. The card warns
to put clarifying questions through a separate Marmot question, not this decision
thread. No reaction is advertised as activation; a stored reaction mapped B cannot
accidentally grant. This explicit mapping avoids calling a choice=None free answer A.

Serialize authenticated intake/activation under daemon work lock and Store order.
At least one current mapped queue-policy approver must have eligible A and no
recorded non-A may exist; bind the full answer sequence, not first/last selection.
No unanimity gate. Any genuine contrary/withdrawal tombstones pending or revokes
active grant transactionally; later A never erases it. Policy/membership changes or
loss of current consenting authority HOLD. A new generation requires new genuine
operational authority; unanswered principals alone do not block.

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
| `delegation.status(grant_id, descriptor_hash)` | Return registered/awaiting_delivery/awaiting_consent/active/held/revoked/expired/superseded/abandoned/declined, revision, activation ask, eligible policy principals and operation ledger. Reads never grant execution authority. |
| `delegation.reserve(grant_id, operation_id, request_id, before_hash)` | After successful local preflight, atomically reserve exact next ordered operation; request ID durably exists locally before RPC. Identical retry returns same reservation, conflicting binding refuses. >=60s grant lifetime required; no refund/reset. |
| `delegation.permit(reservation_id, step_id, attempt_id, before_hash)` | Fresh active authority, policy, time, revision and prior-step verification. Return at most 5s permit clipped to grant expiry, unique permit ID, UTC deadline and server boottime deadline/boot ID. One live attempt per step. |
| `delegation.renew(reservation_id, step_id, prior_permit_id, attempt_id, no_native_intent_hash)` | Only an expired unstarted permit with exact durable executor proof of no native-intent record, same epoch and unchanged preflight. Atomically retire old attempt, issue new 5s attempt; at most 3 total permit attempts/step. No operation refund, no grant extension, no renewal after intent/uncertainty/revoke/expiry. |
| `delegation.settle(reservation_id, step_id, event_id, evidence_hash, outcome)` | Append idempotent observational result: verified_exact_after, verified_no_intent, verified_not_dispatched, or unknown. Same event/hash retry stable; conflicting event/hash holds as integrity error. Original enrolled trusted actor may settle/read after expiry, policy change or revoke; this grants no effect. Old-epoch records may be settled after restart when the guard is consistent; epoch mismatch forbids effects, not observation. Later read-only evidence may upgrade unknown to verified_exact_after, preserving both events. Guard mismatch uses the recovery path below. |

Proposed daemon Marmot grammar: `!grant revoke <32hex-grant-id>` in the registered
authenticated operator group, or `!grant revoke` as a reply to its bound card.
At most80 characters; exact ASCII grammar, bounded lookup, idempotent inbound
message ID. Check fresh current sender authority and exact group at intake.
**The daemon itself atomically records authenticated command/answer and monotonic
revocation/tombstone in one Store transaction before acknowledgement**, including
registered not-yet-active grants. It does not post a second approval question or
wait for a local client to redeem an answer. Any current mapped queue-policy
approver may withdraw; mapped-policy-principal B/withdrawal in the activation thread
uses this same transaction **before** ordinary answer_refusal, ask-status/count/byte limits.
It records a minimal authenticated withdrawal event even if the original ask is
terminal/full, without appending oversized arbitrary answer text. Late A never
reopens a terminal question or restores a grant. Current-authorized withdrawal is never undone when
that sender later loses membership. No free-form/foreign-group/unknown-ID revocation.
`!grant status <id>` returns bounded state/expiry/outstanding steps; help and the
activation card give exact revoke instructions. These are proposed command-handler
extensions, not claims about today's verbs parser.

Revocation intake is ahead of ordinary command authorised() readiness refusal.
When authenticated native sender/group and fresh policy are available, it commits
withdrawal even if a nonauthority readiness condition would deny other commands.
If current authority cannot be established, retain a bounded authenticated potential
withdrawal event and immediately HOLD that named grant pending resolution; it
cannot grant anything. Unauthenticated/foreign-group/malformed events never enter
this path. Resolve with fresh policy to recorded revoke or explicit rejected-event
evidence; never silently discard an acknowledged potential withdrawal. Any gap in
registered-group observing ingress, membership uncertainty or command processing
health permanently holds affected grants, with no automatic resume after recovery:
this prevents a lost offline revoke from being followed by resumed permits. Permit/
renew/reserve require healthy observing revocation ingress and no pending withdrawal.
Fresh operational consent is needed after such a visibility hold, not a liveness
assumption. Bound/deduplicate these events using existing native message IDs.

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

Guard mismatch does not block ordinary Marmot question/answer transport or read-only
`delegation.status`/management inspect. Proposed `admind delegation recover --inspect`
returns exact old/current guard/journal heads and retained operation digests without
repair. Proposed `admind delegation recover --ask ASK --expect RECOVERY_DIGEST`
accepts only a genuine current-policy authenticated Marmot question covering those
exact heads, preserved unknown steps and creation of a new invalidating epoch.
It takes the daemon work lock, durably retains both heads/original records, advances
the external guard and SQLite to a new consistent invalidating incarnation, never
rolls either back, and leaves all old grants invalid. Lost recovery response is
idempotent by saved recovery ID/digest; inconsistent further drift HOLD. This is
explicit new recovery support, not an existing API or a terminal approval route.
After consistency, the original enrolled actor may append observational settlement
for old-epoch reservations; no new permit or grant revival follows. Unknown native
effects still require Z’s exact predecessor-bound Marmot reconciliation, independently
of repairing delegation journal integrity.

## 7. Executor state machine and exact native boundary

Acquire existing controller/receiving/holder locks in native order, pause pickup,
verify worktree/Git/external effects, liveness, capacity, pins, endpoint roles and
full predicted before state **before reserve and before native-intent**. Persist
request ID and complete canonical facts first; lost reserve reply retries that
same ID. Never generate another ID to evade consumed/ambiguous history. One grant
operation at a time; native lane capacity remains an independent bound.

Use Z’s management-owned format2 state machine and a new scoped Queue subclass in the management adapter, not a generic caller
command hook. Its `bd_op` override intercepts an exact allowlist of expected mutation
argv, target and step hash; read-only calls remain ordinary. Existing `Queue.bd_op`
is the subprocess boundary used by Management._mutate_steps and Handoff.move.
Do not let legacy Management._mutate_steps save issued or Handoff.move save moving
as a native-intent proxy. The new management-owned adapter separates those states,
overrides both dependency and reclaim reducers, and classifies a permit refusal
as held_unissued, never catches it as a dispatched native error.
Do not depend on entry into Handoff.move as the authorization instant: it can do
worktree/fence/reservation work before its `bd_op`. Proposed adapter performs full preflight under established locks before
requesting a permit, then performs final locked revalidation after receiving it. As in Z, release the entire parent/lane/holder lock stack
before every admind RPC; retain only durable fence/reservation evidence. After
reserve/permit, reacquire in original order and revalidate exact record/native facts
within the unchanged permit window. Never retain lane while reacquiring parent.
If this cannot finish within5s, HOLD or bounded no-intent renewal; do not extend
the deadline. Inside the write-boundary override, validate permit/deadlines, fsync native-intent,
then recheck boottime/UTC immediately before `subprocess.Popen` of the exact native
write. No awaited/RPC/worktree work may intervene. If fsync used up the window,
record intent_not_dispatched and HOLD; do not renew/reissue automatically. Native
process creation is the defined initiation boundary; scheduler and DB completion
are not atomically tied to remote revocation. Preserve timeout/unknown handling.
Never relax native gates or permit arbitrary argv to obtain this boundary.

Closed executor step states: prepared -> reserved -> permitted -> native_intent ->
dispatched -> verified|unknown -> settled. Additional terminal no-dispatch state
`intent_not_dispatched` is legal only after durable intent plus positive live-executor
proof Popen was never invoked; its settle outcome is `verified_not_dispatched`.
A crash leaving intent without that positive record is unknown, never inferred safe.
Grant registration additionally has abandoned/declined terminal states as in status. `permitted` is a daemon authorization, distinct from
executor `native_intent`. Preflight refusal records HOLD before intent; expired
unstarted permit can use bounded renew only with proven absence of intent. Any
native_intent, including proven intent_not_dispatched, prohibits automatic second
invocation under that operation; positive no-dispatch can release resources safely,
not refund or retry the slot. A crash without proof remains unknown.
After dispatch/timeout/crash, recover by read-only full readback and settle; never
infer retry authority from apparently unchanged state. Unknown effects retain
fence/reservation/capacity and paused pickup until exact evidence or a **new scoped
Marmot management card** authorizes specific reconciliation. No blind refund/abort. Before native intent, or with verified_not_dispatched,
management’s format2 reducer may release only exact locally created fence/reservation/
capacity/current artifacts after lock-protected native prestate and ownership proof,
using the closed cleanup plan mechanically derived from the original typed operation
and this request’s journaled local artifact IDs. This is not another caller-supplied
descriptor field or arbitrary cleanup code; original scope explicitly covers only
these exact local artifacts. No fresh card is needed for
this originally disclosed no-effect cleanup; never call legacy moving-abort. No
native comment/audit write occurs without its own permit. Durable local reason and
cleanup evidence remain. If artifacts changed, ownership is ambiguous or native
intent lacks positive non-dispatch proof, keep held for Z reconciliation.

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
rules. Replace legacy dynamic audit templates for delegated mode only with exact
precomputed canonical bytes: ASCII prefix `management-delegation-v1 ` followed
by compact sorted-key canonical JSON `{v:1,grant:grant_id,op:operation_id,index:i,
delta:delta,reason:reason}` with the descriptor’s exact public values and no
trailing newline. This derived payload has no caller-supplied template. No consent names,
response-time timestamps, steps/native_error or observed after-state enter native
comment bytes. Those remain the private actual receipt/journal. The descriptor’s signed fields deterministically derive the exact audit bytes;
sequential after_hash snapshots append those bytes. Do not include the descriptor
hash in its own predicted native text: that would create a hash cycle. The private
receipt instead binds audit hash to full descriptor hash. Native-generated timestamps use Z’s verified associated-delta normalization. It is not
an unbounded message-send exception or another task operation slot.

Dependency replacement verifies add first, then obtains a new permit for remove.
Expiry/revoke/drift between them retains the added edge, pauses and records partial
state; no rollback fiction. Reclaim's note+assignee native update remains one write,
followed by native existing local-state/fence completion with exact readback. No
automatic pickup/unpause/worker launch is included.

HOLD uses Z’s **single grammar**: `manage ID --inspect` and
`manage ID --settle --expect RECORD_DIGEST` through the pinned enrolled facade.
No alternate request-first CLI grammar. Inspect is read-only; settle appends
observational evidence bound to saved mode/IDs and exact current record, with no
native writes. A delegated extension carries the exact readback evidence hash in
that record, not a second arbitrary CLI file resolver. No automatic unknown archive
or generic superseded outcome. Use compatible Z d3’s explicit predecessor candidate
`manage ID --plan ... --predecessor RECORD_DIGEST --post`, genuine authenticated
Marmot reconciliation and exact apply to archive/link retained old evidence before
a new delta. Unknowns stay unknown; a new grant cannot bypass unresolved native
ownership/reservation. Verified no-dispatch cleanup remains the bounded original
scope above. No new permission for non-effect diagnostics; only a mutation beyond
the finite grant requires real existing operational authority.

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
| Bounds | 0/33 tasks or ops, duplicate task/op/grant ID registration, >24h, before not_before, inverted/exact expiry, permit clipped to expires_at, parent/kind/routing/closed/gate changes refuse |
| Consent | one or more current policy approvers A; no A, A then B, other-principal B, recommended-B reaction, free text/nonce-less/nonce-bearing mapping; pre-delivery A and imported pre-baseline A never activate; fresh same-thread A, full/terminal withdrawal, superseded/pending-revoked ID |
| Disclosure | exact rendered bytes/choices/links/local manifest; hand-written understated card, oversized32 ops, secret/path/nonce/hash handling, partial/failed receipt refuse |
| Policies | separate queue/admind changes, mapping collision, absent confirmed principal, unknown membership and latch -> HOLD |
| Revocation | phone command commits directly; unauthorized sender, foreign group, unknown/malformed ID, >80 chars refuse; duplicate inbound, removed-later sender, preactivation B, lost acknowledgement, ask full/terminal, readiness drop/ingress gap; no resumed/later permits |
| Time | RPC delay, suspend, UTC forward/back >1s, exact 5s boundary, fsync crossing deadline, daemon restart, stale DB backup, missing guard; no cached grant |
| Native | full preflight/cap/holder lock before reserve/intent, wrong argv, reclaim worktree/PR effects, Z role disclosure, predicted endpoint/audit progression, failed-prefix blocks suffix, type-only replacement refusal |
| Crash | failure before/after reserve/permit/intent/Popen/readback/settle/audit; same ID retry, no automatic issued retry; three total permit attempts max |
| Recovery | policy/revoke/expiry/old epoch allows observational settlement; guard mismatch read-only inspect then exact Marmot invalidating repair; conflicting evidence hash holds; unknown->verified append only; no-dispatch local cleanup vs ambiguous capacity retention and Z predecessor reconciliation |
| Compatibility | native approval/pins/history/review cap and old Handoff ask routing intact; no imaginary current APIs or queue DB writes |

Named fake native/admind/clock tests must cover each branch without live queue DBs,
broad discovery, live scans/sends or model invocations. Human acceptance must include
finite nonreactive scope, same-UID risk, nonconflicting authenticated activation,
first-install same-thread fresh reply, serial suffix blocking, restart invalidation,
likely clock holds after NTP/suspend and <=5s initiation boundary; no author-only acceptance of these risks.
