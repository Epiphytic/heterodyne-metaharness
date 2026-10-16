# Bounded controller management delegation d4 — UNREVIEWED

Design task `btq-so7ma`, Codex author, 2026-10-16. **Proposed only:** no grant,
implementation or deployment. Actual Claude R1 reviewed `fb8ce8381df91eb15f1e50ca6ae7951e7057d67c`
and returned REVISE42; R2 reviewed `251d6b5fbb53ab2385195586c9a3dee22d76a6b4`
and returned REVISE24; final R3 reviewed `8c3db8cb7ac2e42817eb1efb9002021b81f1f063`
and returned REVISE15. All **3/3 issued rounds are exhausted**. This new d4 has no
review verdict; author dispositions do not constitute approval. Any additional
review requires an actual authenticated Marmot cap exception, never an automatic
fourth round. Human design approval is still required before implementation.
Questions, decisions and recovery consent use Marmot/admind exclusively.
Historical d3 and all earlier dispositions remain byte-preserved.

## 1. Scope, prerequisites and trust

The operator wants blocking approvals for changed/new designs and deployments,
without a fresh blocking question for each routine controller operation. This
amendment adds a finite delegated mode alongside unchanged per-request management
in [historical d2](../reviews/approved-inputs/2026-10-15-operator-task-management.md).
It does not give an open-ended controller mandate: unlisted/reactively discovered
repairs still require existing per-request authority or HOLD. A new finite grant
is itself an explicit operational authorization through Marmot, not an extra code
review or a design change when the immutable rules and installed code are unchanged.

[D4 inputs](../reviews/bounded-controller-delegation-d4-inputs.json),
[R3 dispositions](../reviews/so7ma-r3-dispositions.md), and unchanged
[R1](../reviews/so7ma-r1-dispositions.md)/[R2](../reviews/so7ma-r2-dispositions.md)
retain the historical evidence. The exact **proposed, unreviewed** Z d4 dependency
is `docs/designs/2026-10-16-management-design-task-recovery-d4.md`, SHA256
`171ffd925ff55d8aeb487a7234d6ce3442ab73f8707ae85f028803bd6ee0e097`,
published documentation commit `4e027cb71e241f02f2d51effeba9adb8e1ef30e0` in
`btq-zcd1l`. Z also exhausted three REVISE rounds. Neither design is approved or
installed. Required compatibility is closed, not an author's later judgement:

| Z d4 contract | Delegated extension required by this design |
| --- | --- |
| Separate `management/request.json` and retained history; composition dispatcher, no legacy Handoff state reuse | Versioned `authority_mode=delegated`, grant/op/step IDs; reject mixed legacy/per-request shapes |
| `manage ID --plan --change FILE --evidence-file FILE [--endpoint-facts-file FILE] [--review-evidence-file FILE] [--predecessor DIGEST] [--post]`, `--apply --ask ASK --expect FACTS_DIGEST`, `--inspect`, `--settle --expect RECORD_DIGEST` | Existing per-request grammar unchanged; new proposed delegated entry is `manage ID --apply-grant GRANT --operation OP --expect FACTS_DIGEST`; no arbitrary command hook |
| `management-endpoint-roles-v1`, <=64KiB/16 entries, exact snapshots/purpose/excerpts/protection records, genuine gates override ordinary | Same canonical facts and classifier result; delegated authority binding defined below, no waiver or alternate ordinary classifier |
| Immutable `management-card-descriptor-v1` link and proposed source-owned `ask.binding.v1` readback | Grant daemon reproduces all per-operation facts and binds authenticated grant receipt to descriptor and rendered question bytes |
| Predicted associated deltas and native timestamp normalization; unknown purpose/history refuses | Same normalizer and complete role/review history inputs, including own earlier verified operation deltas |
| Parent→lane→nonblocking holder lock order; release all before authority RPC; held local cleanup and predecessor-bound reconciliation | Same locks/local-artifact rules and inspect/settle routing; no inherited handoff.json residue |
| Closed step plans, per-next-step bounded fresh-authority attempts, review issuance ledger | Retain those limits and add this design's 5s permit, exact native intent and no automatic reissue after intent |

The future shared classifier adapter accepts only the proposed closed union
`{mode:per_request, ask_id, card_descriptor_hash, binding_hash}` or
`{mode:delegated, grant_id, operation_id, descriptor_hash, activation_receipt_hash}`.
Every hash is the full canonical SHA256, never its displayed prefix. For delegated
mode the trusted daemon receipt must prove the exact operation's canonical Z
endpoint facts were reproduced, visibly disclosed in the complete sent card and
covered by current eligible nonconflicting A consent. The adapter independently
recomputes native classification; a receipt cannot turn a gate into ordinary work.
This is an explicit proposed extension to Z's per-request authority input, not an
existing accepted input. Exact Z d4 fields, limits, states and commands above plus
this union must be included in the eventual jointly approved/deployed contract.
A changed union, classifier, normalization, effect plan or authority predicate
requires design reconciliation and genuine approval; renaming installed paths with
identical bytes is merely a pinned installation detail. No silent compatibility
claim may substitute for Z's unresolved review/human gates.
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
  For reclaim only, delta also contains `claim_stamp` (UTC second precision
  `YYYY-MM-DDTHH:MM:SSZ`) and `claim_nonce` (12 lowercase hex), fixed at
  descriptor preparation. They are public correlation values, not authority or
  secrets. Other deltas reject those keys. No wildcard, predicate, future task,
  alternative delta or floating-point value.

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
approve **deployment only** through the existing authenticated Marmot flow; install
and restart; verify deployed closure, policy, guard and healthy new epoch. Only then
prepare/sign/publish an exact finite runtime descriptor and have the new daemon
post a **separate real grant question**. Grant consent is a distinct human decision:
approving installation grants zero runtime operations, and declining a grant does
not undo deployment. This intentionally replaces d3's combined-card promise; human
approval of d4 must explicitly accept the extra post-install grant decision. There
is no first-install offline renderer/import, old-answer baseline or acknowledgement
activation exception. Future runtime grants use the same one grant-card path.
The descriptor pins deployed candidate/design identities but contains neither its
own containing commit nor a future decision ID; an activation receipt binds those
later. A restart invalidates pending registrations as well as active grants and
permits: a **new ID and new grant question after restart** are always required.

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
human correlation hint, never the apply/authorization comparison. This uses the exact proposed delegated classifier authority union in §1; it is not an installed alternative to Z’s per-operation card. Unknown/genuine gates still refuse. If these facts
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

## 4. Trusted preparation, complete disclosure and post-install consent

New proposed local command `admind delegation prepare --descriptor-ref REF --facts-file FILE`
validates canonical signed artifact bytes/allowed signers, **currently installed**
release identity, current epoch/policies and complete matching private facts. Caller
body/choices are forbidden. It durably reserves the unique grant ID before posting
an ordinary question with A=`Grant listed operations`, B=`Decline; keep paused`,
recommended B. It creates no native approval bead or fake metadata pin. Existing
native design approvals remain independent prerequisites. Changing bytes, a contrary
answer or any daemon restart requires a new ID/question; no import or resurrection.

The trusted renderer must disclose every task/kind/operation/reason, exact before/
after status/ownership/edges, Z purpose/excerpt/protection inventory, stopped-holder
and reconciled worktree/Git/external effects summary, controller/run/workstream scope,
expiry, both policies/eligible principals, release/design/artifact links, exclusions,
no fresh questions for covered operations, same-UID trust, serial suffix blocking,
restart invalidation, clock holds and the <=5s initiation window. Operation count32
and tasks32 are ceilings, not guaranteed phone capacity. Reduce scope **before**
signing if the complete card does not fit. No hidden required facts in !details,
optional appendix, partial post, silent truncation or split decision across cards.

Use the existing immutable40-hex GitHub LinkSpec form for full signed descriptor
identity; show only its informational12-hex SHA prefix and public32-hex grant ID.
Full64-hex hashes remain in verified canonical files, never an unsupported public
renderer bypass. Local `delegation-render-v1` records canonical descriptor/facts
hashes, commit/path/blob, exact body/form/choices/recommended/links, full rendered
card hash, exact transport-part bytes/hashes, policy mapping and installed renderer
identity. It is retained data, not an AskPost attachment or authority.

**Redaction must be a no-op:** before registration/post, reproduce `_shown`, full
`question_card`, `_packed` and every final transport part under the actual installed
redactor/configuration. Require untruncated context, byte-exact intended body,
choices/links/framing and `redact(whole)==whole` **and** `redact(part)==part` for
all parts. Check after final native chunk framing too. Any change/refusal/oversize
holds with no decision card posted; fix the public scope, sign a new descriptor,
and render again. Do not log private raw offending text. Same checks bind actual
stored body and complete delivered part hashes before activation; a stale renderer/
redactor/configuration or partial receipt cannot activate. This stricter proposed
path is required because current ordinary question bodies are silently redacted.

Pure renderer limits the **complete** output, including native label/head/Links,
blank lines, choices, answer line and footer, to36 lines/3200 Unicode characters
and native transport constraints. Multiple physical chunks are allowed only when
the unchanged packer preserves the complete single decision card and every chunk
passes the no-op check; activation waits for every exact part receipt. Nothing
allows alternate hidden chunk contents. Link count <=8 and every link must pass
native validation. The complete sample layouts below demonstrate a bounded ordinary
operation fits; arbitrary long excerpts/worktrees still fail closed rather than
promise every operation fits. Preparation verifies exact size before ID posting.

New Store adds one durable ordered delegation event sequence for complete card
**delivery** and authenticated eligible answers. Require A strictly after delivery
in that sequence, never infer order from second-resolution timestamps or compare
unrelated outbox/answer counters. A receipt is delivery, not acceptance. The daemon
reproduces descriptor, current policy, question and complete answer sequence at
activation. At least one currently mapped queue-policy approver must have eligible
literal A and no recorded non-A/conflict/withdrawal may exist. No unanimity rule.
Unanswered principals alone do not block. A contrary answer tombstones pending or
revokes active grants transactionally; later A cannot erase it.

With recommended B, **only literal A grants**. 👍 and every approve-word reply
`approve`, `approved`, `yes`, `y`, `ok`, `okay`, `lgtm` map to B and decline; 👎 maps
to none and also declines. A nonce-bearing reply `A <grant_id>`, free text and
unknown reactions are contrary, not A. The card explicitly says approve-words
and 👍 decline and asks that clarifying questions use another Marmot thread.
This inherited mapping's availability cost is deliberate: accidental contrary
consent needs a new finite ID/question, not a rewritten answer history.

### Complete bounded card examples

These are static feasibility examples, **not actual asks or grants**, using a
syntactically immutable example40-hex link and existing question framing. Source
links identify example artifacts only, not approval evidence. No root/user/real
subject authority is inferred. Scope digests in the signed descriptor identify
exact private identities; visible scope retains their nonsecret human names.
The exact counts of the two fenced cards are recorded in the input manifest.
All native framing is counted, including the blank before choices. Fixed disclosure
lines are shared; variable facts must fit the same hard budget, never be elided.

```text
QUESTION: Multiple Options
❓ Ask k7m2 · question · posted by btq-delegation (a local process; unverified)
Finite management grant

Grant 0123456789abcdef0123456789abcdef; descriptor hint abcdef012345
Scope: codex/controller-run-7; heterodyne; repository harness; lane own.
One task/one operation; serial order; any HOLD blocks the remaining suffix.
btq-a1b2c implementation: remove blocks edge to btq-d4e5f; then canonical audit.
Before: blocked, owned by this lane, edge present; after: same owner/status, edge absent.
Reason: ordinary completed test work no longer blocks this task.
Endpoint purpose: ordinary test work; native description: "offline fixture cleanup".
Inventory: 2 records inspected; approval/gate records 0; unknown roles 0.
Pin/approval valid; native final approval edge retained; no gate waiver.
Worktrees: 1 clean, pushed; effects: none; ownership and pickup remain paused.
Window: Oct16 06:00 to Oct17 06:00 UTC 2026; <=24h; max32 tasks/ops, one op/task.
Queue policy q1; admind policy a1; eligible: Liam, Bel; exact hashes in descriptor.
Installed release r1; approved design link below; this card is NOT deployment.
No fresh covered-op questions; unlisted work needs separate authority.
Excluded: gate/pin/approval edits, DB writes, services/deploy, policy/member changes.
Trusted same-UID daemon/executor; no hostile same-UID isolation.
Restart invalidates; clock drift/suspend may HOLD; no automatic revival.
Issued permit may initiate its exact write within <=5s after revoke; no later permits.
Only A grants. Approve/yes/ok words and 👍 DECLINE; other answers also stop grant.
Ask clarifying questions in a separate Marmot thread; no terminal consent.
Revoke: !grant revoke 0123456789abcdef0123456789abcdef or reply B / !answer k7m2 B.
Links:
Descriptor: https://github.com/example/harness/blob/1111111111111111111111111111111111111111/docs/grant.json
Design: https://github.com/example/harness/blob/2222222222222222222222222222222222222222/docs/design.md

A = Grant listed operations
B = Decline; keep paused (recommended)
Answer: 👍 = B (recommended) · 👎 = none · or reply with a letter or your own answer (!answer k7m2 <text>)
Choices: A = Grant listed operations · B = Decline; keep paused (recommended)
```

The reclaim variant uses the same fixed lines, replacing only operation lines8–14
with the following **seven** complete lines. The claim timestamp is a frozen
preparation marker, not a claim about actual execution time; that time is journaled.

```reclaim-lines
btq-a1b2c implementation: reclaim ended holder h1 to own lane; canonical audit.
Before: blocked/holder h1; after: blocked/own lane; native pin and final edge valid.
Reason: authenticated stop completed; fresh local process/boot identity confirms ended.
Holder role ordinary; inventory: target 1; approval/gate endpoints unchanged.
Worktree: 1 clean/pushed, commit c1, PR merged; external effects reconciled none.
Preserve worktree/history; fence/reservation/capacity verified; no worker launch.
Claim stamp 2026-10-16T06:00:00Z; nonce abcdef012345 (preparation markers).
```

A reclaim with four worktrees or long decisive facts that exceeds the complete
budget refuses posting. Acceptance must also exercise a longest-admitted one-op
fixture and verify refusal, with zero partial post, for the first byte/line over
limit. Size reduction may narrow tasks/reasons or scope but cannot remove required
Z evidence or alter literal source excerpts to conceal decisive facts.

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
| `delegation.settle(reservation_id, step_id, event_id, evidence_hash, outcome)` | Append idempotent observational result: verified_step_after, verified_no_intent, verified_not_dispatched, or unknown. Each outcome settles one enumerated step, not an entire operation; completion is derived only from all required step results. Same event/hash retry stable; conflicting event/hash holds as integrity error. Original enrolled trusted actor may settle/read after expiry, policy change or revoke; this grants no effect. Old-epoch records may be settled after restart when the guard is consistent; epoch mismatch forbids effects, not observation. Later read-only evidence may upgrade unknown to verified_step_after, preserving both events. Guard mismatch uses the recovery path below. |

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
All supported ingress routes are included: direct thread replies, mapped reactions,
and `!answer ASK TEXT` (including B, approve-words and free-text contrary answers),
as well as explicit `!grant revoke`. Normalize native authenticated sender/group,
message ID and bound ask/grant first; then select this priority withdrawal reducer
before ordinary `bang_answer`, `answer_ask`, command readiness or terminal refusal.
For bounded auth-valid replies that map contrary, retain minimal event/source hash
rather than arbitrary oversized text. Answer-byte/count exhaustion cannot bypass
revocation; mapped reaction B/none is identical. Unknown authority follows HOLD
below, never the ordinary answer-limit rejection path.
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
invalidates **all prior-epoch pending registrations, grants and permits**, even if DB restored to an old active
snapshot. Never resume a persisted active flag. Executor pins the live epoch and
holds on changes. This deliberately sacrifices seamless daemon restart; a new ID, new posted grant question and fresh
finite operational consent are required after restart. Pending answers cannot migrate epochs. Live database rollback is
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

Use Z d4’s management-owned versioned journal/state machine and a new scoped Queue subclass in the management adapter, not a generic caller
command hook. Never store delegated records in shared handoff.json or subclass legacy Handoff load/check/run/reclaim dispatch. Its `bd_op` override intercepts an exact allowlist of expected mutation
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

Closed step states: `prepared`, `held_unissued`, `reserved`, `permitted`,
`native_intent`, `dispatched`, `verified`, `unknown`, `settled`,
`intent_not_dispatched`. Dispatch ordering is prepared→reserved→permitted→
native_intent→dispatched→verified|unknown→settled. Pre-intent refusal records
held_unissued with reason; no-dispatch intent is legal only with positive live
executor proof Popen was never invoked. Its settle outcome is verified_not_dispatched.
A crash with intent and no positive proof is unknown, not proof of no effect.

Operation states are separately closed: `prepared`, `reserved`, `executing`,
`held_unissued`, `held_local_cleanup`, `held_partial`, `held_audit_completion`,
`held_reclaim_completion`, `post_effect_conflict`, `unknown`, `completed`,
`aborted_no_effect`. Only completed allows the serial suffix. The reducer derives
state from exact step records: a verified dependency-add prefix with unissued
remove is held_partial; a verified main status/dependency/reclaim write with
unissued canonical audit is held_audit_completion; verified claim with remaining
local bookkeeping is held_reclaim_completion. Unexplained readback drift is
post_effect_conflict, not completion. Lack of evidence is unknown. Missing audit
is a known incomplete effect, **not** unknown and not aborted_no_effect. Original
grant cannot issue the audit after revoke/expiry. Read-only settlement records the
verified prefix without manufacturing operation-final after_hash. Existing exact
Marmot predecessor reconciliation may authorize just the missing audit/completion
against observed prefix state, preserving original authority/effect records;
never replay the verified main mutation or erase original incompleteness. The
successor does not automatically unlock the old grant suffix: remaining operations
need a new finite descriptor from actual current state. No automatic refund.

Grant states remain the separate API enum in §5. `permitted` differs from durable
intent. Expired unstarted permits get at most three attempts only with proven
absence of intent and unchanged authority/preflight. Any intent, including positive
intent_not_dispatched, forbids automatic native reissue for that step. A positive
no-dispatch record permits only exact original local cleanup, not a refunded slot.
After dispatch/timeout/crash, recover by read-only full readback and settle; never
infer retry authority from apparently unchanged state. Unknown effects retain
fence/reservation/capacity and paused pickup until exact evidence or a **new scoped
Marmot management card** authorizes specific reconciliation. No blind refund/abort. Before native intent, or with verified_not_dispatched,
management-owned versioned reducer may release only exact locally created fence/reservation/
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
writes: dependency edge add/remove (two only for distinct-endpoint replacement),
then canonical audit; status update then canonical audit; reclaim note/assignee
atomic update then canonical audit. Reclaim also has Z d4's exact seven total
local/native steps including fences/reservation/current/cleanup/audit, all journaled;
no extra native comment may be smuggled into local completion). Arbitrary additional steps refuse.
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

Delegated reclaim never calls the legacy runtime note generator or dynamic
Handoff.finish/comment template. Derive exactly `claimed by {worker} at {claim_stamp}`
then a newline and exactly `handoff from {holder}: handoff-request={claim_nonce}`,
using the descriptor's frozen stamp/nonce, bound old holder and receiving worker.
Use the pinned native `--append-notes` semantics for the complete predicted notes,
verified in disposable native CLI golden vectors before candidate review. Queue
passes the two-line payload without choosing the native append separator. No
unverified guessed separator is permitted; a mismatch blocks candidate acceptance
and signing of runtime grants, never a post-effect prediction rewrite. The signed after_hash includes those
literal bytes. `claim_stamp` means preparation time; private receipt records actual
dispatch time. Generation of a fresh stamp/nonce at execution is a protocol error.
Canonical audit above is the only audit payload, never the bounded existing audit.

Status has exactly two native steps: update blocked/unassigned→open/unassigned,
then canonical audit. It changes no owner/pause state or pickup permission. Before
prepare, before each write and in readback, implementation targets require valid
metadata design pin **and** native approval_valid/final approval dependency; neither
is inferred from card consent. Planning kinds use exact Z d4 allowlist/classifier
and issued review ledger: unknown issuance or exhausted REVISE refuses reopening;
final APPROVE at exhausted cap may support publication-only progress, not another
review. Apply the same genuine pin/gate checks to dependency/reclaim. Status makes
a task claimable only through ordinary native pickup gates, never this receipt.

Dependency replacement verifies add first, then obtains a new permit for remove.
Expiry/revoke/drift between them retains the added edge, pauses and records partial
state; no rollback fiction. Reclaim's note+assignee native update remains one write,
followed by the enumerated Z d4 local-state/fence completion with exact readback. No
automatic pickup/unpause/worker launch is included.

HOLD uses Z’s **single grammar**: `manage ID --inspect` and
`manage ID --settle --expect RECORD_DIGEST` through the pinned enrolled facade.
No alternate request-first CLI grammar. Inspect is read-only; settle appends
observational evidence bound to saved mode/IDs and exact current record, with no
native writes. A delegated extension carries the exact readback evidence hash in
that record, not a second arbitrary CLI file resolver. No automatic unknown archive
or generic superseded outcome. Use compatible proposed Z d4’s explicit predecessor candidate
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
card with direct design link and all deviations. No live initial grant implicitly; first-install deployment card grants no operations. The later standalone grant question is mandatory and a separate real human choice, not a disguised acknowledgement.
If boundary subclass cannot preserve native invariants, HOLD implementation and
amend design rather than silently patch unreviewed controller identity contracts.

Required disposable offline tests (future acceptance, **not run for this design**):

| Boundary | Required assertions |
| --- | --- |
| Canonical bytes | UTF-8 golden vector, unicode/surrogates, duplicate/unknown keys, null/float/NaN, malformed UTC/IDs, signature mismatch; zero effects |
| Bounds | 0/33 tasks or ops, duplicate task/op/grant ID registration, >24h, before not_before, inverted/exact expiry, permit clipped to expires_at, parent/kind/routing/closed/gate changes refuse |
| Consent | one or more current policy approvers A; no A, A then B, other-principal B, recommended-B reaction, free text and nonce-bearing mapping; pre-delivery A never activates; literal A grants, every approve-word/👍/nonce-bearing reply declines; full/terminal withdrawal, superseded/pending-revoked ID |
| Disclosure | exact rendered bytes/choices/links/local manifest; hand-written understated card, oversized32 ops, whole-card and every final chunk redaction no-op, token/login-dir/per-chunk sensitive joins refuse; static complete sample budgets, secret/path/public-ID/hash handling, partial/failed receipt refuse |
| Policies | separate queue/admind changes, mapping collision, absent confirmed principal, unknown membership and latch -> HOLD |
| Revocation | phone command commits directly; unauthorized sender, foreign group, unknown/malformed ID, >80 chars refuse; duplicate inbound, removed-later sender, preactivation B, lost acknowledgement, all reply/reaction/!answer routes before ordinary refusal, ask full/terminal/answer-byte exhaustion, readiness drop/ingress gap; no resumed/later permits |
| Time | RPC delay, suspend, UTC forward/back >1s, exact 5s boundary, fsync crossing deadline, daemon restart, stale DB backup, missing guard; no cached grant |
| Native | full preflight/cap/holder lock before reserve/intent, wrong argv, reclaim worktree/PR effects, Z role disclosure, frozen claim stamp/nonce golden vector, status pin/approval gate, predicted endpoint/audit progression, failed-prefix blocks suffix, type-only replacement refusal |
| Crash | failure before/after reserve/permit/intent/Popen/readback/settle/audit; same ID retry, no automatic issued retry; effect verified/audit unissued and add-only partial states, no duplicate main effect; three total permit attempts max |
| Recovery | policy/revoke/expiry/old epoch allows observational settlement; guard mismatch read-only inspect then exact Marmot invalidating repair; conflicting evidence hash holds; unknown->verified append only; no-dispatch local cleanup vs ambiguous capacity retention and Z predecessor reconciliation |
| Compatibility | native approval/pins/history/review cap and old Handoff ask routing intact; no imaginary current APIs or queue DB writes |

Named fake native/admind/clock tests must cover each branch without live queue DBs,
broad discovery, live scans/sends or model invocations. Human acceptance must include
finite nonreactive scope, same-UID risk, nonconflicting authenticated activation,
deployment-only first install followed by a separate new grant decision, serial suffix blocking, restart invalidation,
likely clock holds after NTP/suspend and <=5s initiation boundary; no author-only acceptance of these risks.
