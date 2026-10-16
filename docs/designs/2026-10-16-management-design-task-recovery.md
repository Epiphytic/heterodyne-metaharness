# Management recovery for nonapproval design and planning tasks — d3

Design task `btq-zcd1l`; proposed amendment, design only, 2026-10-16. Actual R1 reviewed `e73eab04ac9eeb91ea7a5794cd04bbe2236fde4d` and returned REVISE; its 24 findings and this revision's dispositions are retained in [the review record](../reviews/zcd1l-r1-dispositions.md). Actual R2 reviewed `e1eb90a28c1fab011d73f2860abe0cfb1c96b430` and returned REVISE with 25 findings; [ordered R2 dispositions](../reviews/zcd1l-r2-dispositions.md) retain them. Two of at most three actual issued review rounds are consumed; only final R3 remains. Only the controller runs further reviews, publication and native approval operations. This draft grants no implementation, recovery or deployment authority.

## 1. Existing authority and bounded amendment

The [approved original management d2](../reviews/approved-inputs/2026-10-15-operator-task-management.md) supports authenticated stopped-holder reclaim, status correction and typed dependency repair for implementation tasks. Preserve its archive bytes and genuine approvals. Queue `6fe57598468ff53bf74dfd9e1cf181ad3177c3f1` currently requires `kind:task` in `Management._route` and valid implementation pins in `_approval`; a planning task cannot use it to resume producing its design. This amendment adds the finite planning classification below and repairs the management journal/packaging seams needed to operate it safely. It does not change normal queue claim/close, approval validity or implementation design gates.

Implementation scope is expressly extended beyond the original queue-only scope: queue `bin/btq-manage.py`, its named safe offline tests and documentation; harness `scripts/workstream_manage.py`, `scripts/install_workstream_controller.py` and their named offline tests/documentation. Keep queue `bin/btq`, `bin/btq-supervise` and harness `harness/**/*.py` byte-identical to the pinned deployment baseline. This avoids drifting the active controller's separately pinned queue binary or harness digest. Any implementation that cannot fit these boundaries returns for an amended design; it must not quietly re-pin the controller.

The original native approval is `btq-ovvzt`: closed approve by Liam at `2026-10-15T19:58:20Z`, ADR `391516cd919234e69626970cb2e61b94299ed60e`, digest `64ff5d9c77c75a9c69c8ba6d0c6aa28f74bb84f5fe354973dd94704a93c05f31`. Its actual historical channel was `cli`, recording the user’s “approved, proceed” while admind was latched; it is not a Marmot receipt. That approval covered implementation/offline work and excluded deployment, master merge and recovery. This changed design needs its own genuine Marmot approval; present operator merge instructions supersede the old separate merge question, not the design/deployment gates.

The source closure, approved archive provenance and installed baseline digests are in [the input manifest](../reviews/zcd1l-management-inputs.json). That manifest describes observed input, not a future executable release. The implementation and deployment must bind their own exact candidate commits and file hashes.

## 2. Native schema and classification

Normalize one native snapshot once, then use the same representation in validation, digest, expected state and final comparison. Native objects must be dictionaries; labels must be a present list of unique strings; metadata must be absent or a dictionary with string keys. Do not coerce booleans, numbers, lists or null pin values into strings. For either edge direction, absent list with explicit corresponding integer count 0 (not bool) means empty. An explicit list must contain well-shaped unique `(id, dependency_type)` records and agree with an available integer count. Target `dependencies` omitted with nonzero, missing or malformed count refuse because the requested delta cannot be validated. A missing `dependents` list with a nonnegative integer count is an explicitly count-only reverse view, never fabricated enumeration; missing/malformed count is unknown. Preserve the original implementation operation’s supported count-only reverse view as specified below, rather than claiming every dependent was inspected. Missing labels never mean empty. Preserve raw snapshot/hash alongside normalized facts so normalization cannot erase an unexpected field.

There must be exactly one native workstream label matching the enrolled workstream and exactly one `kind:` label. Conflicting or malformed routing refuses. For reclaim, also prove all native agent/session selectors admit the actual receiving run using the queue's existing routing rules; another agent's task cannot be relabeled or stranded in this controller's lane. For status/dependency repair, preserve routing and show the intended eligible worker route without assigning a worker.

| Native kind | Management class |
| --- | --- |
| `kind:task` | Implementation; original implementation pin/approval/blocker checks retained |
| `kind:brainstorm`, `kind:review`, `kind:research` | Planning; additional rules below |
| `kind:approval` | Always forbidden, including inspection-to-mutation transitions |
| Missing, mixed, malformed, legacy `kind:implementation`, or any other kind | Refuse without conversion |

The class is derived, never supplied by the caller. Validate kind, schema, routing and protection when preparing, authorizing, before every effect and during final readback. Malformed inputs produce a stable safe refusal reason, not a raw traceback or echoed secret-shaped payload. Validation errors use the module's handled `ValueError` boundary; the shim returns typed safe reasons for held states as well as ordinary refusals.

For implementation, retain the exact existing nonempty `design_approval`/`adr_revision`, native `approval_valid` and corresponding final `blocks` edge checks. For planning, both metadata keys may be absent: this confers no implementation authority. If either key is present, both must be nonempty strings and pass those same checks. Also bind the actual approval's authenticated scope to this planning task using its retained exact ask/artifact and native edge; a valid approval for another task is not transferable. Ambiguous scope refuses. No fake pins or local approval JSON are accepted.

Invalid planning pins remain a safe refusal. `approve-bead` is not a planning-pin repair API and is not proposed as one. This amendment does not authorize metadata edits. A needed exceptional pin correction requires a separately scoped, reviewed and authenticated Marmot OPS repair specifying exact old/new metadata and original authority; refusal alone is not its authority. Ordinary unpinned planning recovery has no such prerequisite.

The following protection applies to **both implementation and planning**, pinned or
unpinned. Protect every native approval/confirm/question prerequisite, not merely
the implementation's primary pinned blocker. Native labels expose kind/routing,
not a general harness-gate semantic detector. Proposed versioned classifier rules
`management-endpoint-roles-v1` therefore combine these exact positive predicates:

1. A native `kind:approval`, `kind:confirm` or `kind:question` is `permission_gate`.
   Conflicting/malformed kind fields mean unknown. The target's actual pinned
   approval ID is always protected, independent of endpoint shape.
2. A complete exact line `Management-Role: permission_gate` in endpoint native
   description/design/acceptance/notes or retained native comments is an explicit
   protected declaration. `Management-Role: ordinary_work` is not proof by itself.
   Existing permission evidence is conservatively bound by the request: a native
   approval or retained authenticated restriction that explicitly designates this
   endpoint as an **authorization subject** (not its own implementation design
   approval) must be included as a protection_record below, with exact source ID
   and complete source hash. Its presence means protected; the module need not
   infer a new native metadata schema for the source. This is a proposed typed
   classification input, not a claim an installed authorization-subject field exists.
   A free-text mention alone never grants authority or establishes ordinary work. Unrecognized referenced permission/scope records mean unknown.
3. Exact scope data is supplied through the evidence input below, not invented
   installed metadata. The caller must include all referenced protection records;
   module enumerates native target/endpoint fields and comments and compares the
   complete record/reference inventory. Unresolved references, conflicting purpose,
   or evidence identifying a permission precondition makes removal refuse. No
   general NLP detector or proof that arbitrary text lacks a hidden gate is claimed.
   In particular a title 'Implement rollout' alone cannot establish ordinary work.

Only affirmative exact native work-product evidence, no positive/unknown protection
and the existing authenticated per-operation card's explicit nongate-purpose
attestation can establish `ordinary_work`. This is an operator-ratified factual
classification, never an author/model-only semantic verdict. A genuine existing
gate cannot be waived by that card. If its nature cannot be resolved from retained
native evidence, it remains unknown. An ordinary task's `design_approval` pin
means its own implementation needs approval; it does not make that task a gate.

Proposed `--endpoint-facts-file FILE` is data-only JSON, <=64KiB, <=16 entries:
`{schema:"management-endpoint-roles-v1", entries:[{id, native_snapshot_sha256,
workstream, endpoint_type, purpose, evidence:[{field, sha256, excerpt}],
protection_records:[{source_native_id, source_sha256, subject_id, role}]}]}`.
All keys required/closed; IDs use native slug validation; SHA256 is64 lowercase hex;
purpose is ordinary_work|permission_gate|unknown; role only permission_gate.
`endpoint_type` is exactly target-style unique supported `kind:` value, or `epic`
only when native `issue_type` is epic and native kind labels absent/nonconflicting.
The latter is **endpoint-only**, not new manageable target kind. Workstream records
its exact actual single native selector; cross-workstream endpoints are allowed
only as fixed graph endpoints, never retargeted/reclaimed tasks. Missing/conflicting
routing refuses. Up to4 evidence items, each a literal nonempty native field excerpt
<=128 characters; field is description|design|acceptance|notes. Hash the complete
field bytes, not excerpt. Native comments/protection inventory is retained as full
facts with existing bounded native reads. No arbitrary artifact store/hash lookup,
network fetching, caller code/path loading or native metadata writes is introduced.
If a needed purpose/protection source exists only in an unavailable artifact, HOLD;
use supported native evidence or a separately scoped authenticated repair to make
facts inspectable, not a fabricated artifact resolver.

Plan retains exact input bytes/hash; card binds their **full64-hex** facts digest.
Apply never rereads a replacement input file. Canonical native snapshot is sorted-key
compact UTF8 JSON with strict types/duplicate rejection; normalize only documented
omitempty fields/counts. Initial attested digest stays immutable. At boundary n,
compare against the predicted state after this request's verified steps: exact
edge/reverse-edge/count and requested parent delta, native updated_at change only for an endpoint this verified step actually writes,
with the returned/readback timestamp retained in the step result, and exact
request-marked audit/claim append only. An updated_at change on an untouched
endpoint refuses. For a touched endpoint, every other field must match the exact
predicted delta; a timestamp exception never permits unrelated content/edge changes. No unrelated
content, route or external dependent change is ignored. Normalize endpoint
`dependents` absent+count0 and explicit empty identically; list-present/list-absent
transitions at zero are tested. Count-only unknown enumeration stays count-only;
never synthesize a dependent list. Final hash is of predicted final snapshot, not
the initial attestation hash reused after its own mutation.

Rule changes require changed-design approval and reviewed deployment. New supported
endpoint facts under unchanged rules are ordinary runtime input covered by the
**same original per-operation card**: no per-ID design/redeployment, extra model
or function-level consent. Without evidence, status/reclaim/add remain available;
unclassified removal/replacement refuses. Native cycle/protection checks remain.

## 3. Operations, effects and evidence

Only the genuinely enrolled Codex controller and its exact receiving lane may use the pinned `workstream-manage` facade. Raw mutable-checkout `btq` is not the installed route. Existing supported entry points are:

```text
workstream task RUN --controller --workstream WS pause
workstream-manage RUN --workstream WS --lane ID manage ID --plan --change FILE --evidence-file FILE [--post]
workstream-manage RUN --workstream WS --lane ID manage ID --apply --ask ASK --expect DIGEST
```

`RUN`, `WS` and `ID` must match enrolled identity, configured workstream and own
lane. Proposed new options are `--inspect`, `--settle --expect RECORD_DIGEST`,
`--endpoint-facts-file FILE`, `--review-evidence-file FILE` and plan-only
`--predecessor RECORD_DIGEST` for exact reconciliation. They are not installed
syntax. The pinned shim routes **all** manage plan/post/apply/inspect/settle forms
through one management-module dispatcher, never splits old/new plan validation.
It verifies enrollment/runtime closure first, parses a closed mutually exclusive
mode, requires lane==ID, validates native slug and calls Queue._bound(ID).
Plan without post and inspect use Queue(make_state=False), create no dirs/locks,
read no arbitrary native verbs and perform no journal/native/ask writes. Post,
apply and settle may create only their normal private lane state. Plan needs
change+evidence, may have role/review files, and forbids ask/expect; apply needs
ask+full64-hex facts digest and forbids all file/post flags; inspect takes no effect flags;
settle needs exact record digest and forbids ask/change/post. All reject unknown
flags before Queue construction/native calls. No widened raw bin/btq parser.

| Operation | Admitted delta and restrictions |
| --- | --- |
| Planning status | Only blocked/unassigned → open/unassigned. Closed planning is excluded: current native detail cannot enumerate the affected dependent set or prove lifecycle reclose support. No close, in_progress, assignee or arbitrary status override. |
| Existing implementation status | Original d2 closed-source reopen/update remains supported with unchanged pin/authority/whole-target checks. No new universal dependent-enumeration prerequisite is imposed on that existing operation. Card discloses count-only dependent visibility and that prior lifecycle evidence is historical, never a fresh close/deploy proof. Planning closed-source remains excluded as a deliberate narrower new capability. |
| Reclaim | Existing stopped-holder transfer into the actual compatible receiving lane, status unchanged. Native claim/handoff note append, reservation/fence settlement and local current pointer are explicit expected effects, not an assignee-only operation. Alive refuses; unknown needs the existing explicit cessation coverage and real holder locks. No timeout inference. |
| Typed dependencies | Existing parked/blocked ownership rules and typed add/remove/replace; closed planning excluded. Native cycle checking retained. Replace adds before removing. Exact endpoint identities/types and expected inverse/count effects are bound. Parent-child edits additionally bind the exact before/after parent derived from the requested native edges; unrelated parent changes refuse. No ambiguous multiple-parent result. |

All other labels, routing, metadata, content and history remain unchanged except the explicitly appended management audit/claim records. Parent remains unchanged except the explicit parent-child delta above. Whole-task comparison must include `parent` in addition to the current `evidence_snapshot` fields; reclaimed planning and implementation requests receive the same strengthened final classification/content checks before the existing Handoff finish effects. This deliberately tightens Management readback for both classes without changing normal implementation eligibility or the original pin checks.

Pause parent pickup before plan/post/apply. Quiesce affected workers and acquire the actual holder/lane locks required by the operation; no missing-lock bypass. Dependency endpoints must be stopped/parked as required by the original contract. Do not automatically unpause, unpark, claim or launch reviews after recovery. Preserve all worktrees and histories.

Worktree evidence must examine every native claim/worktree note relevant to the target, including historical holders when the current assignee is empty. Bind each note's holder/run, repository, path, source and inspection result. Distinguish no recorded worktree from recorded-but-missing, remote, inaccessible, malformed or uninspected. The latter states refuse an effectful plan until supported read-only evidence resolves them; never present them as clean/none or delete them. Bound the candidate inventory; overflow refuses. Existing source/branch/unmerged-work checks remain. Unassigned status must not substitute the receiving controller for the historical holder.

Reopen requires an explicit human-visible blocked reason/source: quote the bounded current native blocking note and any retained human hold/scope restriction. If no unambiguous reason is recorded, state that explicitly in the card; if a material reason cannot fit or be safely disclosed, refuse posting. The facts bind the complete original content, not merely that excerpt.

Planning recovery must bind retained **actual issuance history**, not free-text
--evidence-file assertions. New `--review-evidence-file FILE` accepts closed
`management-review-history-v1` JSON (<=64KiB): task/lineage ID, cap, state
not_started|in_progress|approved_final|unknown, exact design revision, and source
inventory of native review-start/result comment IDs/hashes plus finite retained
controller launch/result files `{relative_name,sha256,bytes}`. File entries resolve
only beneath the enrolled run's fixed `management-evidence` directory, regular
mode0600 files with no symlink/path traversal, <=32 files/256KiB each. No arbitrary
hash store, network fetch or caller-selected root. This directory is a proposed
data-only retained-input location; the controller copies its actual start/result
records, never creates synthetic starts to fill gaps. Management independently
reads the full native review marker inventory and reconciles duplicate invocation
IDs, start/result/report hashes and terminal result scope against those exact files.
The existing authenticated card discloses the complete source inventory and requires
the policy principal's attestation that it covers the lineage; caller assertion alone
cannot establish completeness or not_started. Missing/unrecognized sources or
unresolved issuance means unknown/HOLD, not a larger budget. This remains a trusted
controller/human evidence boundary, not a tamperproof universal model-launch logger.

Count distinct issued invocations including failed/aborted/running/unresolved.
Only retained positive proof of never-spawned reservation is excluded; absence of
result is not proof. Recompute remaining=cap-issued; never accept caller remaining.
At cap with exact final APPROVE for current revision and no unresolved starts,
allow publication/human-approval-only recovery, **no further model invocation**.
At cap with REVISE/unknown, refuse work requiring more review and obtain exact
Marmot resolution; do not invent a fourth round or fresh lineage. Unknown issuance
holds even when another result says APPROVE. not_started requires empty native
inventory plus complete-source attestation, not a boolean.

Transfer the exact retained task/lineage issuance ledger through normal claim and
handoff evidence, bound to the task's actual routing and receiving run. Every
enrolled trusted controller must consult it before issuing a review. An existing
native session selector narrows routing normally; no new selector or global pause
requirement is added. Keep this controller's required pickup pause and ordinary
worker/holder locks; do not require quiescence of unrelated matching controllers.
Native Queue.claim/ready is byte-identical and has no global budget gate. This
amendment does not claim enforcement against a hostile/noncompliant same-UID
controller; that residual cannot be fixed by an impossible all-controller lock.
Reopen is no authority to invoke another review. Unknown ledger/history remains
HOLD; final-APPROVE publication-only recovery does not start a model.

## 4. Authenticated decision and finite card

Each effectful recovery retains original d2's per-operation authenticated Marmot question. Only a fresh actual current-policy principal's authenticated intent covering the exact operation counts. The policy may legitimately include Bel following the operator's explicit restoration instruction; this design neither removes Bel nor infers authority from a key's presumed human/agent nature. A controller/model cannot fabricate, send itself, or synthesize the required consent. Mere policy membership, local text, a review, a cessation statement or this design is not an authenticated decision. Original task/design/deployment approvals remain distinct.

The current user authorizes merging after actual Claude review passes and necessary real approvals are obtained; a separate historical `master go` question is superseded by that explicit instruction. New/changed designs and deployments still require blocking Marmot approval. D2 currently also requires each management card; this amendment retains that approved contract without adding a new routine gate. The separately proposed bounded delegation design is not current authority.

Cards must be self-contained: task/title/kind/class, exact before/after delta, reason/hold, routing and source/worktree facts, liveness/cessation, protected gates/pins, review history/remaining budget, known dependent/parent effects, limitations, nonce, full64-hex facts digest and finite choices. Show unsupported effects as refusal, never as a harmless count. Use literal hostile-text quoting, secret checks and supported admind transport bounds. Keep the existing conservative 36-line/3200-character management limit within admind's 40/3500 limits; use fixed compact labels without dropping material facts. Bound reason/hold and decisive purpose excerpts to128 characters each at input, evidence summary to256, and worktree inventory to4 entries each <=160. Inputs exceeding these limits refuse before post rather than silently truncating. One ordinary removal/reopen/reclaim fixture with typical native facts must fit; fixed budget is an explicit availability bound, not a promise every historical inventory fits. Full facts remain in the card-bound record; the card shows all decisive facts and exact full digest. Full digest has a dedicated exact64-lowercase-hex renderer field, exempt only from misclassification as a secret; scan every other user text normally. This is not a generic secret bypass. Plan validates with the full 12-character posted nonce width and exact encoded layout. A maximum-size nonce/card is tested. Overflow posts nothing; it does not truncate decisive facts or fall back to terminal consent.

## 5. Versioned journal, liveness and failure recovery

Implement a management-specific format2 state machine in `btq-manage.py`, sharing
the lane storage path and durable helpers, **not** relying on base Handoff.load's
format/phase validator. Override load/save validation, prepare/post routing,
apply/run/_run/check_ask, reclaim move/recover/finish/abort and resume_command for
format2; preserve compare-before-save and safe write/fsync helpers. Reuse pure
native evidence, exact fences/reservations, nonblocking holder locks and argument
builders, not base HANDOFF_PHASES.index or base abort-on-drift/cancel exceptions.
The implementation must inventory all inherited calls into base phase-dependent
methods and prove new records cannot reach them. Native bin/btq stays unchanged.
Legacy records use a distinct read-only parser/dispatch, never upgraded by class.

Format2 requires immutable request/nonce/task/run/lane, raw+normalized fact hashes,
full64-hex facts digest, exact delta/predicted effect sequence, endpoint/review inputs,
card/real ask identity, authenticated decision snapshot, per-step intent/dispatch/
verified or unknown state, bounded exact audit marker/payload/hash, phase/reason and
append-only transitions. Retain original bytes by content hash before terminal
archive; archives/index below lane state bind predecessor/successor. A top-level
format discriminator makes unchanged ordinary Handoff.load reject active new data
safely; management dispatcher alone owns these records. No fake ask or overwritten
unknown record. Management-only output gives supported inspect/settle commands.

Locks are always acquired parent -> receiving lane -> native nonblocking holder
parent/lane in the existing order. **Release the entire acquired lock stack** before
any admind call; never hold a lane while reacquiring parent. Durable native fence/
reservation remains as data during the gap, not an OS-lock reversal. After refresh,
reacquire in original order, reread exact record digest and every required native/
worker/endpoint fact. Concurrent apply/settle is detected by record compare; no
stale writer continues. Read-only inspect takes no effect lock/state mutation.

At most3 complete authority-refresh/preflight attempts per apply invocation. Each
records timeout/snapshot-age/refusal reason; if current ask+fact validation cannot
fit the5-second initiation window, return held_unissued with concrete diagnostics,
never spin indefinitely. A later explicit apply may retry only unissued exact
current request, not automatically poll/repost. Preflight native reads may be done
before refresh, but full final validation still has to pass within the bound;
slow hosts can legitimately HOLD. No weakened oracle or authority-age extension.

Check current policy and authentic ask outside locks; after acquiring all locks,
validate same real ask/card/full digest and at least one policy A with no contrary
policy answer, fresh native/policy facts and snapshot age using CLOCK_BOOTTIME.
After successful checks fsync exact intent, then check age immediately at native
write dispatch; if deadline exhausted, hold intent_not_dispatched, never silently
replay. Cancelled/B/expired/revoked/unknown never authorizes another native write,
including audit. A5-second initiation bound is not native-atomic revocation.

| Observed state | Required supported outcome |
| --- | --- |
| Prepared/posted/authorized, no issued native/audit intent | Drift, B, cancellation, expiry or stale/unknown authority produces `held_unissued`; inspect exposes the reason. Settle archives as `aborted_no_effect` only after fresh readback proves exact before-state and no issued intent. A new card may then be prepared. |
| Validated step ready | Persist exact native effect intent immediately before calling the adapter; only now enter `moving`. Do not mark moving merely to begin preflight. |
| Issued step, response absent/error | Hold `outcome_unknown`; read back exact expected pre/post states and unique markers. A unique landed result becomes verified without repeating the call. Exact pre-state after an ambiguous issued call does not prove nonexecution; no automatic reissue. Conflicting/partial/unknown state stays held. |
| Replace add verified, remove not issued | Both edges deliberately remain. Refresh authority/facts before removing. Refusal/revocation holds this intermediate graph, describes it explicitly and does not silently roll back or issue remove. A later valid original decision may resume only if still exact/current; otherwise a new authenticated reconciliation card must bind this intermediate state and the remaining delta using the predecessor-candidate path below. |
| All native steps verified, final readback changed | Hold `post_effect_conflict`; never stamp done, claim work or restore earlier facts. Inspect preserves exact verified steps and conflict. Reconciliation stages a new predecessor-bound card while the prior record remains blocking, then authentic apply atomically archives it as `superseded_with_verified_effects` before starting the exact new delta, binding predecessor and actual current state. Unknown effects cannot be automatically superseded; only the explicit bounded authenticated reconciliation route below can retire them without replay. |
| Reclaim assignee/note landed, authority cancelled or lost | Record verified transfer and held_reclaim_completion. Retain fence/reservation and pickup pause; inspect reports actual assignee/note/current state. Observational settle may record native effect but cannot write native audit or complete claim bookkeeping under revoked authority. New authenticated reconciliation card must explicitly cover the exact remaining local current/fence/reservation/audit steps; do not run base Handoff.finish on a late cancelled ask. |
| Audit preparation read fails | No audit intent was issued; retry the read/build safely. Prepare and persist the exact bounded payload/hash/unique marker before saving `audit_issued`, then call comments-add. |
| Audit issued, answer lost | Search exact unique marker/payload under fresh readback; one match verifies without reposting, multiple/conflicting matches hold. No match after an ambiguous call remains unknown; do not resend. |
| Done | Exact native/parent/class/protection checks, unique audit and local current/reservation/fence effects verified; archive original and transitions before admitting a successor. No automatic worker resumption. |

A terminated native process plus exact pre-state does **not** prove no execution:
a write could have committed and been changed back or have external effects. No
automatic retry follows. Supported escape is original-record inspect, cessation/
Git/native-effect reconciliation, then a new explicit Marmot management card that
quotes the ambiguous predecessor, its known bounds and exact current state, and
specifies a **new reconciliatory delta**, not retroactive declaration 'never ran'.
Only after authentic decision may journal settle mark `superseded_unknown_reconciled`
with immutable old evidence and explicit accepted unresolved effect scope; fences/
capacity release are themselves exact card-covered effects. This does not permit
unknown permission/gate effects, missing locks, irreversible unbounded consequences
or resetting review history: those remain HOLD for separately scoped OPS resolution.
The old operation is never reissued or erased. No new generic retry token.

Reconciliation is an explicit format2 branch, not an overwrite. Proposed plan-only
`--predecessor RECORD_DIGEST` reads the exact held record and stages one separate
successor candidate/card under its content hash while the predecessor remains
blocking. It binds both record hashes, actual current facts, accepted unresolved
scope (if permitted above) and the exact new effect sequence. No ordinary successor
or native effect is admitted merely by posting it. `--apply --ask SUCCESSOR_ASK
--expect SUCCESSOR_FACTS_DIGEST` selects that saved candidate; under fresh current
authority and the same locks, verify both unchanged records and the new card,
then atomically archive/link the predecessor before recording the successor's first
intent. Crash recovery recognizes the durable link and never repeats predecessor
effects. All local fence/current/reservation changes required for succession are
in that new card's sequence. At most one candidate exists; failed/ambiguous old
ask cancellation still blocks admission. Old asks must be terminal read back
(cancel if still answerable) before this transition, and old record tombstones
reject late decisions. Unchanged original A is not presumed to authorize this new
scope. Observational `--settle` alone cannot approve or perform this transition.

`--inspect` is read-only and includes all management requests, not only reclaim, returning schema/phase/reason, record/facts digests, known issued/verified steps and safe next actions. It never exposes credentials or unchecked raw payloads. `--settle` operates only on the expected exact record digest and appends journal/archive transitions; it cannot change a task, reissue writes or erase unknown outcomes. It does not cancel an ask as an implicit side effect. To retire an unissued old card, controller uses the existing supported `admind ask cancel ASK` separately, then reads back terminal cancellation before admitting a successor; failed/ambiguous cancellation retains blocking old record. Journal tombstone always prevents late A from applying archived request. This is authorized cleanup of the old request, not a new task-management operation. New effectful reconciliation uses the ordinary authenticated plan/apply path with the predecessor reference and exact current state. If an audit outcome remains irreducibly unknown, retain that lane held and report it; other isolated lanes remain usable. A separately scoped audited repair may be proposed through Marmot, never silently inferred from refusal.

Legacy records are not applied under broadened classification. Inspect exposes their original bytes/digest and old phase. Zero-issued records may be archived only after the same no-effect proof and current ask read; old ambiguous posting, moving, comment-issued or unrecognized phases stay held unless existing readback uniquely establishes their actual effects. There is no automatic abort-and-overwrite. Old records still valid under the original implementation-only contract may complete under the exact retained old bundle before upgrade; they are not silently upgraded. Rollback to that bundle is permitted only with no active format-2 records; otherwise retain format-2 read-only inspection and fail closed until a reviewed compatible forward runtime is restored. Archive preservation and rollback refusal are installer checks.

## 6. Packaging, prerequisites and escalation

The existing installer hardcodes old reviewed program bytes and cannot deploy this amendment unchanged. The implementation therefore includes a new manifest-bound management installer mode in the harness script and the matching pinned shim. It first validates the **existing** installed controller helper/manifest, original queue binary and harness digest without refreshing their pins. It accepts only an exact reviewed candidate Git commit with a closed three-file closure (`bin/btq`, `bin/btq-manage.py`, `bin/btq-supervise`) plus exact shim/installer source commit/hash, and authenticated deployment authority binding that candidate manifest. Proposed installer
`--deployment-ask ASK --expect-manifest SHA256` reads the real admind stored question,
current policy and authenticated A/no contrary via existing supported ask-get, and
verifies the exact request-marked candidate manifest hash/design links/choice body
against a retained trusted rendered request. Authority files are readback evidence
only, never consent by digest alone. Unsupported/missing transport/body/receipt
means no install. No new authority issuer or invented current RPC. The retained `btq` and `btq-supervise` hashes must equal the baseline; only management module bytes change. No arbitrary caller checksum or dirty checkout is a release authority. Stage the content-addressed bundle/shim/manifest, verify imports resolve to those staged files and the canonical repo remains the pinned queue identity, then atomically switch only the management facade. Preserve the prior facade and all request records. Controller helper/manifest/harness digest, canonical binary, native database, policy, membership and services are outside this deployment delta. Its wrapper intercepts management doctor/resume diagnostics and prints a dominant current Marmot-only plus inspect/settle route; inherited terminal/--apply hints from unchanged controller/bin are explicitly legacy and not active instructions. No need patch those pinned sources. A failed preflight makes no installed changes. Prove the prior facade remains usable before switching; validate new inspect/plan on disposable state before live readiness. This is proposed installer support, not a claim it exists today.

`fn9r` changed the missing queue-policy admind executable setting only, with authenticated Liam A at `2026-10-16T05:25:51Z`, deployed `05:26:23Z`. Retained before/after policy SHA256 values are in the input manifest. Doctor exit zero proves neither transport availability nor admind health. Deployment must separately verify the configured executable/import root, supported read-only ask-get/status transport, current authenticated policy availability and the pinned facade using their real interfaces; unavailable/unknown blocks effectful management. Do not read/print credentials for health evidence or send an unsolicited test card.

The separate `sbjv` exact status-only OPS repair for `btq-e7knb`/`btq-fu366` was authenticated by Liam A at `05:47:37Z` and applied at `05:51:26Z`; this is historical specific authority, not pending and not a generic approval. Their recovery does not remove the future need for supported status/dependency/reclaim functions.

D2's `btq-ywnix` bootstrap and historical OPS notes do not create a universal refusal bypass. Safety refusals retain their reason and facts. All new questions, design/deployment approvals and exceptional repair decisions go exclusively through Marmot/admind. If transport fails, retain the durable unanswered request/outbox and repair transport only under already granted exact authority. Terminal/chat may carry diagnostics, never consent or an alternative approval. No direct native DB edits, borrowed identities or invented authenticated receipts. An exceptional native OPS action needs its own exact authenticated scope and before/after audit; it cannot waive missing locks, design gates or review caps by implication.

## 7. Acceptance and delivery gates

These are executable **future offline acceptance requirements**, not tests run by this docs change. Use named safe tests with disposable fake native/admind state, realistic omitted fields, and no actual model or live queue calls. Preserve prior tests for valid implementation behavior; add negative assertions for the deliberately stronger Management checks.

| Cases | Required proof |
| --- | --- |
| Three planning kinds; real zero-edge omitempty fixture | Plan/post/apply blocked-unassigned reopen succeeds; original description/history/routes retained; missing dependencies+count 0 works at every phase |
| Missing dependencies with nonzero/unknown count; labels absent; malformed metadata/pin values; duplicate edges/routes/kinds | Typed safe refusal, no traceback, disclosure or native write |
| Approval/unknown kind at every boundary; planning closed; status destination closed/in_progress | Zero writes, no reinterpretation |
| Implementation valid/missing/partial pins and planning absent/partial/valid/wrong-scope pins | Existing gate remains; no fake planning-pin repair; no borrowed approval |
| Implementation and pinned/unpinned planning extra approval/confirm/question/gate edges | Removal/replacement forbidden equally; ordinary proven nongate repair works |
| Parent-child add/remove/replace, native epic/cross-workstream endpoint and unrelated parent drift | Exact parent delta read back; cycles/ambiguity/unrequested parent changes refuse |
| Reclaim compatible/incompatible selectors; ended/alive/unknown; locks; post-move kind/content drift | Real locks and cessation rules retained, note/current effects explicit; full final validation before done |
| Unassigned with historical, remote, missing or multiple worktree notes | Correct inventory, never false none/clean; unresolved facts refuse |
| Review 0/3, 2/3, 3/3; running/failed/aborted/unresolved issued calls, unspawned reservations, missing/conflicting history; explicit human hold | Issued counts/artifacts bound; only proven unspawned reservations excluded; unknown or exhausted REVISE cannot enable further review; exhausted final APPROVE can publication-only resume; hold reason visible |
| Genuine current configured principal including operator-authorized Bel; forged/self-generated/copied/late/cancelled/B answer | Actual authenticated intent required; policy membership alone does not authorize; no key-based agency guess |
| Drift before first intent, after replacement add, after native completion; revoke between steps | Unissued safely settleable; intermediate graph explicit; no destructive rollback or unauthorized next step |
| Crash at every journal/native/local/audit boundary, including audit-payload preparation | Unique landed effects verified once; unknown never replayed; no false done or dropped record |
| Format2 full override/load/phase routing; legacy posted B/expired/cancelled; format-2 rollback; predecessor reconciliation | Safe inspect/archive with no-effect proof, durable history; unknown remains held, no overwrite |
| Parent→lane contention with refresh gaps; three failed freshness attempts; maximum nonce/full digest, typical bounded reason/worktree/budget, hostile/secret content and overflow | Exact posted layout preflight; no partial card, hidden decisive facts or fallback leak |
| Installer exact closure, old pins, candidate drift, interrupted atomic switch and rollback | Canonical/controller sources unchanged; pinned new module/shim only; records preserved; failure leaves prior facade |

Delivery requires actual bounded cross-model design review, published immutable design and human Marmot approval before implementation; then meaningful offline verification, actual code/delta review within the existing cap and a separately authenticated deployment approval with exact source pins/deviations. Current explicit merge authority applies after those real review/approval gates; no additional merge question is added. Model review, cessation evidence and a local journal substitute for none of the genuine approvals.
