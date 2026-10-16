# Management recovery for nonapproval design and planning tasks — d4 (UNREVIEWED)

Design task `btq-zcd1l`, 2026-10-16. **UNREVIEWED author revision; all3/3 actual review rounds exhausted.** Final R3 reviewed `30a904b10c30a386c5867e6d7b8cd8f494b33ca0` and returned REVISE16. This d4 has no reviewer acceptance and cannot authorize implementation or deployment. No fourth model invocation is permitted without a genuine explicit Marmot cap exception. The exact [d3](2026-10-16-management-design-task-recovery.md), its manifest and R1/R2 dispositions remain byte-for-byte historical. [All16 R3 dispositions](../reviews/zcd1l-r3-dispositions.md) and [d4 inputs](../reviews/zcd1l-management-d4-inputs.json) distinguish fixes, limitations and proposed prerequisites. No code or live actions ran.

## 1. Existing authority and bounded amendment

The [approved original management d2](../reviews/approved-inputs/2026-10-15-operator-task-management.md) supports authenticated stopped-holder reclaim, status correction and typed dependency repair for implementation tasks. Preserve its archive bytes and genuine approvals. Queue `6fe57598468ff53bf74dfd9e1cf181ad3177c3f1` currently requires `kind:task` in `Management._route` and valid implementation pins in `_approval`; a planning task cannot use it to resume producing its design. This amendment adds the finite planning classification below and repairs the management journal/packaging seams needed to operate it safely. It does not change normal queue claim/close or primary implementation approval validity. It deliberately narrows the old implementation dependency-repair capability: even an authenticated ordinary management card cannot remove an extra approval/confirm/question or unresolved permission gate. Historical d2 allowed correction of some mistaken extra protected edges; that exceptional repair now needs its own exact scoped OPS authority and remains outside this ordinary endpoint classifier. This changed availability/capability is disclosed for future human ratification, not called unchanged behavior.

Implementation scope is expressly extended beyond the original queue-only scope: queue `bin/btq-manage.py`, its named safe offline tests and documentation; harness `scripts/workstream_manage.py`, `scripts/install_workstream_controller.py` and their named offline tests/documentation, plus queue docs/PICKUP.md for retained review-ledger pickup guidance. A narrow separately delivered admind read/binding prerequisite (§6) adds only typed ask readout/validation/CLI and its safe offline tests; it does not change answer consent semantics, policy, membership or decision writers. Keep queue `bin/btq`, `bin/btq-supervise` and harness `harness/**/*.py` byte-identical to the pinned deployment baseline. This avoids drifting the active controller's separately pinned queue binary or harness digest. Any implementation that cannot fit these boundaries returns for an amended design; it must not quietly re-pin the controller.

The original native approval is `btq-ovvzt`: closed approve by Liam at `2026-10-15T19:58:20Z`, ADR `391516cd919234e69626970cb2e61b94299ed60e`, digest `64ff5d9c77c75a9c69c8ba6d0c6aa28f74bb84f5fe354973dd94704a93c05f31`. Its actual historical channel was `cli`, recording the user’s “approved, proceed” while admind was latched; it is not a Marmot receipt. That approval covered implementation/offline work and excluded deployment, master merge and recovery. This changed design needs its own genuine Marmot approval; present operator merge instructions supersede the old separate merge question, not the design/deployment gates.

The source closure, approved archive provenance and installed baseline digests are in [the input manifest](../reviews/zcd1l-management-d4-inputs.json). That manifest describes observed input, not a future executable release. The implementation and deployment must bind their own exact candidate commits and file hashes.

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

For implementation, retain the exact existing nonempty `design_approval`/`adr_revision`, native `approval_valid` and corresponding final `blocks` edge checks. For planning, both metadata keys may be absent: this confers no implementation authority. If either key is present, both must be nonempty strings and pass those same checks. Also bind scope using the actual native approval's complete retained description/design/acceptance/notes/comments, authentic native decision metadata and exact target edge/pins. Require explicit identification of this planning task as an approved subject; an ordinary mention or transferable approval is insufficient. No ask-body/artifact resolver is presumed. If this native evidence does not establish scope, HOLD without modifying pins; a separately scoped genuine Marmot clarification/repair may supply native inspectable authority. This is a bounded availability restriction, not automatic refusal of every pinned planning task. No fake pins or local approval JSON are accepted.

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
   complete inventory of references actually present in those enumerated records. It cannot discover an unreferenced third approval elsewhere in the database: its completeness claim is limited to these finite native sources plus the authenticated policy principal’s attestation that no omitted protection record exists. No global reverse-text oracle is claimed. Known external protection always overrides ordinary; inability to attest completeness refuses. Unresolved references, conflicting purpose,
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

Plan retains exact input bytes/hash; card binds their full facts digest through the exact immutable signed descriptor link and the daemon stored-card binding described in §6; a short display prefix never authorizes. The canonical descriptor contains full64-hex digests.
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

Reopen requires an explicit human-visible blocked reason/source: quote the complete current native blocking note and each retained human hold/scope restriction (each <=512 characters). No caller-selected excerpt establishes a complete hold. The reason source is explicitly identified; a literal complete short native note is not a truncation. Longer or materially ambiguous notes refuse this bounded card; use a narrower separately scoped genuine Marmot clarification without editing away the original hold. If no unambiguous reason is recorded, state that explicitly in the card; if a material reason cannot fit or be safely disclosed, refuse posting. The facts bind the complete original content, not merely that excerpt.

Planning recovery must bind retained **actual issuance history**, not free-text
--evidence-file assertions. New `--review-evidence-file FILE` accepts closed
`management-review-history-v1` JSON (<=64KiB): task/lineage ID, cap, state
not_started|in_progress|approved_final|unknown, exact design revision, and source
inventory of native review-start/result comment IDs/hashes plus finite retained
controller launch/result files `{origin_run,relative_name,sha256,bytes}`. origin_run is an exact enrolled run ID derived from native holder/review-start evidence, not a caller filesystem root. File entries resolve only beneath that enrolled run’s fixed `management-evidence` directory, regular
mode0600 files with no symlink/path traversal, <=32 files/256KiB each. No arbitrary
hash store, network fetch or caller-selected root. This directory is a proposed
data-only retained-input location; the controller copies its actual start/result
records, never creates synthetic starts to fill gaps. Management independently
reads the full native review marker inventory and reconciles duplicate invocation
IDs, start/result/report hashes and terminal result scope against those exact files.
For stopped prior holders, the management adapter performs read-only source resolution under the same enrolled runs root, verifies retained file hashes and enrollment/task identity, and makes an immutable provenance-preserving copy in the receiving run’s evidence directory. Missing original files require authentic prior retained copies with exact origin/hash provenance; unsupported/unknown source holds. Native Queue.claim receives no new arguments and performs no transfer. The management journal records the completed transfer before changing ownership. The authenticated card discloses exact counts of issued/completed/unresolved invocations, latest verdict/revision, remaining budget and source kinds/counts, and requires
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

The management adapter stores a task/lineage issuance manifest at its fixed workstream management-evidence root, keyed by native task ID, with origin run and content-hash references. Copies do not erase original evidence. Updated queue docs/PICKUP.md instructs enrolled trusted controllers to consult this exact retained ledger before model issuance; no implicit Queue.claim evidence parameter or globally enforced launch hook is claimed. The management facade consults it on every planning recovery and transfers its bound references on reclaim. The controller protocol remains trusted cooperation, not a hostile-local-process barrier. An existing
native session selector narrows routing normally; no new selector or global pause
requirement is added. Keep this controller's required pickup pause and ordinary
worker/holder locks; do not require quiescence of unrelated matching controllers.
Native Queue.claim/ready is byte-identical and has no global budget gate. This
amendment does not claim enforcement against a hostile/noncompliant same-UID
controller; that residual cannot be fixed by an impossible all-controller lock.
Planning blocked→open recovery explicitly refuses unknown history and exhausted REVISE, even when no model is immediately requested, because unassigned ready pickup could otherwise encourage another round. Exact final APPROVE at cap may publication-only recover with the visible no-new-review restriction and retained ledger. Reclaim/dependency diagnostics do not mint model budget; all effectful recovery with unknown history holds. This is cooperative protocol enforcement; hostile/noncompliant controllers remain outside scope.

## 4. Authenticated decision and finite card

Each effectful recovery retains original d2's per-operation authenticated Marmot question. Only a fresh actual current-policy principal's authenticated intent covering the exact operation counts. The policy may legitimately include Bel following the operator's explicit restoration instruction; this design neither removes Bel nor infers authority from a key's presumed human/agent nature. A controller/model cannot fabricate, send itself, or synthesize the required consent. Mere policy membership, local text, a review, a cessation statement or this design is not an authenticated decision. Original task/design/deployment approvals remain distinct.

The current user authorizes merging after actual Claude review passes and necessary real approvals are obtained; a separate historical `master go` question is superseded by that explicit instruction. New/changed designs and deployments still require blocking Marmot approval. D2 currently also requires each management card; this amendment retains that approved contract without adding a new routine gate. The separately proposed bounded delegation design is not current authority.

Cards must be self-contained for the decision: task/kind/class, exact before/after delta, complete reason/human hold, routing/worktree/cessation facts, primary pins/protected gates, review issued/completed/unresolved counts and verdict/revision/remaining budget, exact parent/dependent effects, finite choices and nonce. Decisive endpoint purpose and protection inventory are visible, but repetitive source-file names/full hashes are not all duplicated into the phone card. Full inventories remain in the exact immutable signed request descriptor and retained private evidence; the card shows source kinds/counts, exact descriptor commit/path link and informational12-hex facts prefix. The daemon-bound stored body/link must match exactly (§6); full descriptor/facts hashes are compared locally and through that immutable link, never by the display prefix. No bare64-hex text is put through the old generic redactor; no undeployed digest-field exemption is claimed.

Proposed public `management-card-descriptor-v1` is closed data: version=1, task, request_nonce, workstream, class, exact typed change, before_after_summary, complete reason/hold strings, worktree/cessation summaries, primary pin references, review counts/latest verdict/revision, source inventory kinds/counts, endpoint-purpose/protection display facts, and full facts/private-evidence SHA256. Canonical sorted-key compact UTF8 JSON rejects duplicate/unknown keys and malformed types; private raw snapshots/session paths never appear. It contains neither its own containing commit/link nor the future ask ID/render hash. Sign and publish those exact screened bytes under existing trusted signer verification; only then pure rendering adds that exact40-hex GitHub commit/path link. Local reproduction and daemon binding compare that link/body and full linked artifact hashes. No self-commit/render-hash cycle, mutable URL, arbitrary signed code or signer enrollment from the artifact is permitted. Publication is part of this planned per-operation authority flow, not a new design/model/approval round; inability to safely publish this bounded descriptor holds that request.

Keep36 lines/3200 characters including generated form/footer, within native40/3500. Use actual supported question A/B with recommended B, literal A grants, current-policy A/no contrary semantics unchanged. Reason <=256, complete blocking/hold note <=512, evidence summary <=256, at most4 worktree summaries <=160 each. A single dependency operation needs at most3 endpoint entries (target, removed, added); display each decisive literal excerpt and any material protection, not up to16×4 unbounded excerpts. Internal input limits remain conservative; overflow posts nothing. Do not remove material facts to meet the budget. At least typical reopen/reclaim/replacement and a six-record/three-issued-review history must fit; source provenance hashes do not consume per-record display lines. Extreme worktrees/holds/protection inventories can still refuse and require narrower genuine Marmot scope, explicitly disclosed rather than claiming universal phone capacity. Literal hostile-text quoting and secret screening apply to every public descriptor and card before publication. A descriptor that cannot safely be published must HOLD, never expose private native payloads or use terminal consent. No partial post.

## 5. Versioned journal, liveness and failure recovery

Implement a management-owned format2 journal and dispatcher in btq-manage.py, **separate from unchanged Handoff and its lane handoff.json**. Proposed lane path `management/request.json`, with append-only content-addressed records/index under `management/history/`. Neither active nor terminal format2 bytes ever occupy handoff.json. The adapter composes pure native read/build helpers; it does not subclass phase-dependent Handoff load/move/recover/abort/run methods or mutate base globals(). Queue’s exact read/mutation boundary can be wrapped for managed effects, but ordinary Handoff remains its own format1 state machine. Manage routes only through the new dispatcher. Pin exact supported base hashes and fail before any effect on an unknown base version; do not claim compatibility with arbitrary future internals.

Before starting management, detect any active ordinary handoff and refuse; before routing a regular handoff through the installed facade, reject any active management request. The durable active management marker plus existing reservation/fence prevents accidental concurrent handoff through supported enrolled entrypoints; raw unsupported bypass is outside trust scope. Terminal management archives clear only that active marker after durable archive/index readback. No format2 residue remains on the regular handoff storage path. Rollback is refused while any management active/unknown record exists; terminal-only history never breaks unchanged Handoff.load. Inventory composition calls and test exact existing regular-handoff-after-done behavior. Legacy format1 management records remain in their original path until safely archived via the retained exact old bundle; they are not silently converted.

Format2 retains immutable request/nonce/task/run/lane, raw+normalized facts/full digests, exact predicted step plan, endpoint/review inputs, actual ask/card binding, authenticated decision snapshot, per-step intent/dispatch/verification/unknown, fixed audit payload/marker, append-only reasons and predecessor links. Retain original bytes by content hash before archiving. Management inspect/settle own only these records; no base HANDOFF_PHASES indexing, automatic moving-abort or injected runtime class replacement.

Locks are always acquired parent -> receiving lane -> native nonblocking holder
parent/lane in the existing order. **Release the entire acquired lock stack** before
any admind call; never hold a lane while reacquiring parent. Durable native fence/
reservation remains as data during the gap, not an OS-lock reversal. After refresh,
reacquire in original order, reread exact record digest and every required native/
worker/endpoint fact. Concurrent apply/settle is detected by record compare; no
stale writer continues. Read-only inspect takes no effect lock/state mutation.

Each exact **next unissued step** has at most3 authority-refresh/final-preflight attempts per explicit apply invocation, not three calls across the entire multistep request. Persist step index and verified prefix. Closed plans are finite (status mutation+audit, dependency add/remove+audit, reclaim fence/reservation/assignee-note/current/fence-release/reservation-release/audit; all actual effectful steps enumerated, no arbitrary loop). At most7 steps and therefore21 bounded refresh attempts in one apply. Every attempt records refusal/age/timeout; no background polling. A later explicit apply may continue only an exact unissued next step after its verified prefix under still-current original authority; it never repeats issued/unknown steps. Revoked/B/cancelled/unknown authority holds remaining work and requires appropriate reconciliation, not reset by another apply.

Expensive complete Git/worktree inventory and retained immutable evidence hashing occur before obtaining authority, outside parent locks. For retained immutable evidence, final locked validation rechecks complete file guards (device/inode/mode/size/mtime_ns/ctime_ns) against the already verified mode0600 retained copy. Mutable worktree/Git evidence is not made immutable by stat caching: recompute its existing supported predicates at final validation, together with native/worker/endpoint facts and exact record/step hash. Any changed guard or Git state requires full reinspection before a new refresh, never trust a cache across drift. The authority snapshot’s5-second bound starts after complete preflight; do not repeat8MiB of retained immutable report hashing under the parent lock. Required final mutable Git checks may still need supported read-only Git subprocesses; they are not skipped for speed. Native reads can still exceed it and HOLD with bounded diagnostics. Readback after a completed write is observational, not charged as another issued mutation. Local journal-only observations do not need fresh effect authority; native writes and local fence/reservation/current effects do.

Check current policy and authentic ask outside locks; after acquiring all locks,
validate the exact daemon-provided stored-card binding, immutable descriptor/full facts digest and at least one policy A with no contrary
policy answer, fresh native/policy facts and snapshot age using CLOCK_BOOTTIME.
After successful checks fsync exact intent, then check age immediately at native
write dispatch; if deadline exhausted, hold intent_not_dispatched, never silently
replay. Cancelled/B/expired/revoked/unknown never authorizes another native write,
including audit. A5-second initiation bound is not native-atomic revocation.

| Observed state | Required supported outcome |
| --- | --- |
| Prepared/posted/authorized, no issued native/audit intent | Drift, B, cancellation, expiry or stale/unknown authority produces `held_unissued`; inspect exposes the reason. Settle may archive as `aborted_no_effect` only after exact no-native-intent proof, unchanged native before-state, and verification that no management-created local fence/reservation/current effect remains. Journal-only settle never cleans those artifacts. If a fence/reservation exists, retain held_local_cleanup; its removal is an explicit effectful step requiring still-valid original card authority or a new predecessor-bound genuine Marmot reconciliation card. Never call it no-effect while orphaning capacity/locks. A new ordinary card is admitted only after safe cleanup/readback. |
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
Only authentic successor `--apply` (never observational `--settle`) may atomically archive/link the predecessor as `superseded_unknown_reconciled`
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
`--deployment-ask ASK --expect-manifest SHA256` uses the new read-only `ask binding` prerequisite below, not existing ask-get. It checks exact stored title/body/form/choices/recommended/links, delivery and complete authenticated answer history against the retained reproduced request; current-policy A/no contrary must cover the exact immutable signed candidate manifest link. Candidate/full facts are compared by full digest through that exact immutable link, never title/digest12 alone. Authority files remain readback evidence, not consent. Missing binding transport or changed/stale/malformed response refuses before install. The retained `btq` and `btq-supervise` hashes must equal the baseline; only management module bytes change. No arbitrary caller checksum or dirty checkout is a release authority. Stage the content-addressed bundle/shim/manifest, verify imports resolve to those staged files and the canonical repo remains the pinned queue identity, then atomically switch only the management facade. Preserve the prior facade and all request records. Controller helper/manifest/harness digest, canonical binary, native database, policy, membership and services are outside this deployment delta. Its wrapper intercepts all management errors and next/resume hints, replacing them with its own inspect/settle or typed Marmot-only diagnostics. It never points a consent/recovery next-hint at unchanged controller doctor, whose terminal wording is outside this patch. Read-only controller doctor may be used separately for health facts, never approval. No inherited terminal/--apply hint is emitted verbatim as active instructions. No need patch those pinned sources. A failed preflight makes no installed changes. Prove the prior facade remains usable before switching; validate new inspect/plan on disposable state before live readiness. This is proposed installer support, not a claim it exists today.

### Explicit prerequisite: source-owned stored-card binding readout

Existing AskView supplies summary/digest12/answers, **not** body/choices/links. Proposed new local same-UID read-only RPC `ask.binding.v1` and CLI `admind ask binding ASK --json` return a closed version1 envelope: actual ask ID/kind/status/title/body/form/choices/recommended/links, full SHA256 over canonical stored render inputs, delivered/card-parts/truncated proof, complete eligible answer records with native IDs/operator/choice/text/recorded time and complete-history indicator, current policy fingerprint, read-at boottime and boot ID. Stored choices encode the actual recommendation; implementation derives/verifies it rather than inventing absent Store metadata. The operation reads under daemon serialization and returns bounded typed refusals for missing/incomplete/oversized state, no caller-selected path/SQL or writes. The installer/management module verifies exact expected bytes/hash and fresh native authenticated policy answers with unambiguous current queue-name/admind-principal mapping; ambiguous names/history refuse; no response field by itself creates approval. Use exact stored redacted/rendered representation as appropriate, reproduce transport output, and refuse any unexpected truncation/redaction. Original native answer storage and authentication remain authoritative; no caller-authored answer or local file becomes consent.

This is a real separate implementation/review/deployment dependency, not currently installed syntax. Its narrow source scope is admind asks/control/CLI/daemon readout/Store query plus named offline tests, with no consent/membership/service-policy changes. It must be delivered before the self-checking management installer is used. Its own first deployment uses the already supported operator-controlled deployment flow with genuine Marmot question and retained authenticated reply/explicit exact candidate scope, independently verified by the controller/operator; it does **not** require this uninstalled RPC to authorize itself, nor silently weaken the management installer to legacy title matching. A blocked prerequisite needs normal genuine design/code/deployment authority, not a fabricated bootstrap pass. One exact deployment may bundle compatible bytes only if its already-supported controller verification establishes this explicit prerequisite installation first; no automated installer may assume future readout exists. This d4’s review cap exhaustion blocks such implementation until genuinely resolved.

`fn9r` changed the missing queue-policy admind executable setting only, with authenticated Liam A at `2026-10-16T05:25:51Z`, deployed `05:26:23Z`. Retained before/after policy SHA256 values are in the input manifest. Doctor exit zero proves neither transport availability nor admind health. Deployment must separately verify the configured executable/import root, supported read-only ask binding/status prerequisite transport, current authenticated policy availability and the pinned facade using their real interfaces; unavailable/unknown blocks effectful management. Do not read/print credentials for health evidence or send an unsolicited test card.

The separate `sbjv` exact status-only OPS repair for `btq-e7knb`/`btq-fu366` was authenticated by Liam A at `05:47:37Z` and applied at `05:51:26Z`; this is historical specific authority, not pending and not a generic approval. Their recovery does not remove the future need for supported status/dependency/reclaim functions.

D2's `btq-ywnix` bootstrap and historical OPS notes do not create a universal refusal bypass. Safety refusals retain their reason and facts. All new questions, design/deployment approvals and exceptional repair decisions go exclusively through Marmot/admind. If transport fails, retain the durable unanswered request/outbox and repair transport only under already granted exact authority. Terminal/chat may carry diagnostics, never consent or an alternative approval. No direct native DB edits, borrowed identities or invented authenticated receipts. An exceptional native OPS action needs its own exact authenticated scope and before/after audit; it cannot waive missing locks, design gates or review caps by implication.

## 7. Acceptance and delivery gates

These are executable **future offline acceptance requirements**, not tests run by this docs change. Use named safe tests with disposable fake native/admind state, realistic omitted fields, and no actual model or live queue calls. Preserve prior valid implementation behavior except the explicitly disclosed narrower extra protected-edge removal. Test the changed refusal rather than claiming every old fixture stays valid; keep original primary pin checks unchanged.

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
| Independent format2 composition/journal routing; legacy posted B/expired/cancelled; format2 rollback; predecessor reconciliation | Safe inspect/archive with no-effect proof, durable history; unknown remains held, no overwrite |
| Parent→lane contention with refresh gaps; three failed freshness attempts; maximum nonce/full digest, typical bounded reason/worktree/budget, hostile/secret content and overflow | Exact posted layout preflight; no partial card, hidden decisive facts or fallback leak |
| Installer exact closure, old pins, candidate drift, interrupted atomic switch and rollback | Canonical/controller sources unchanged; pinned new module/shim only; records preserved; failure leaves prior facade |

**D4 remains UNREVIEWED after3/3 exhausted actual rounds.** No additional review is authorized by this author document; a genuine exact Marmot cap exception or operator-selected different non-review resolution is needed before proceeding. A new lineage cannot be invented to reset the count. Normal delivery still requires actual bounded cross-model design review, published immutable design and human Marmot approval before implementation; then meaningful offline verification, actual code/delta review within the existing cap and a separately authenticated deployment approval with exact source pins/deviations. Current explicit merge authority applies after those real review/approval gates; no additional merge question is added. Model review, cessation evidence and a local journal substitute for none of the genuine approvals.

### D4 regression acceptance additions

Future named offline cases must prove: full binding readout missing on old daemon refuses without deployment writes; signed candidate link/body/choice tampering refuses; prerequisite first-install is independently authorized without circular RPC dependence; management done/terminal rollback permits later regular Handoff unchanged; active cross-state interlocks refuse both directions;7-step reclaim and add/remove/audit finish with per-step retry budgets; verified-prefix resume never repeats an issued step; revocation with local fence/reservation retains held cleanup until a genuine authorized effectful path clears them; stopped previous-run review files transfer with provenance without changing Queue.claim; six retained review records fit the compact decision card; unknown/exhausted-REVISE planning reopen refuses; final-APPROVE publication-only remains allowed with zero model budget; complete200-character hold fits and600-character hold refuses without excerpt substitution; hidden unreferenced protection residual is disclosed and requires real principal attestation; settle cannot supersede an unknown; no emitted next-hint proposes terminal consent; file-identity drift forces expensive reinspection outside timed locked authority check. These are acceptance specifications, not tests run by this docs change.
