# Plan 5: typed policy and a separate action executor

Status: revised design proposal d2 for btq-e7knb; future work only. No implementation,
installation, action execution, or new approval authority is granted by this
file. The controller must publish and review the exact revision and obtain a
new authenticated design approval before implementation admission.

## 1. Sources and current boundary

The governing architecture is ADR 0001 revision 16 with Amendment 1, in the
separate design repository at commit
`93d0e90b121a07db720d92924cda443a1cfc239c`, file
`docs/adr/0001-workstreams-v2.md`, especially §§3.1, 5.1, 5.3, 5.4, 5.9,
7, 8.2 and 17. The controller's native approval evidence is btq-xywu0;
the document's historical Proposed header is not itself approval evidence.
Its review history includes `docs/reviews/0001-design-review-r30.md` at
`a6903c5fe58a53f6726d18d163dca1d7d98d817b`.
The roadmap in `docs/superpowers/plans/2026-09-29-heterodyne-v1-roadmap.md`
is sequencing context; its older architecture pin does not replace r16 A1.

This proposal is written against product commit
`07637745fe75dc614109a0b8ba72d3489f262a97`. At that commit:

- `config/policy.py` parses effective tiers and restrictive workstream overrides;
  it does not implement request classification or action execution.
- `session/server.py` authenticates a per-generation session socket. It accepts
  hook events, but action requests remain unsupported. `session/shim.py` has
  a request transport, not an execution capability.
- `wsd/runtime.py` defines `ActionReconciler` and `HoldingReconciler`;
  the latter holds unsettled actions without probing their targets.
- `wsd/daemon.py:assemble` supplies that holding reconciler. There is no reason
  to treat existing callbacks or human approvals as a functioning executor.
- admind is an independent operational admin channel. Its current guarded
  authority and design-approval flow remain unchanged until the coordinated
  all-kinds decision-writer cutover in §6.

The unmerged Plan5i consumers and scanner integration are separately owned by
btq-fu366. This design does not select their commits, change their retention
contract, or authorize their merge. Implementation must refresh this map at the
then-current integrated source and name which request/provenance APIs it reuses.

## 2. Relation to held Plan5a and review history

Retain the held draft unchanged at product branch plan5a commit
`317f37b65f3fcd4cdb10d9ed4ba7d27f4092e655`, file
`docs/superpowers/plans/2026-10-14-plan5a-actions.md` (initial draft
`22f3e5c`). It explicitly says HELD, not reviewed, not approved. Its large
embedded implementation is not copied into this design or treated as accepted
code. Its open review points are author-recorded issues, not a successful review.
Actual review of this proposal at `79aec6f` was design round 1 of 3,
**REVISE**, with ten structured findings (the report introduction says eight
problems). This d2 addresses all ten; no new review or approval is claimed.
The original report and source remain retained. No fresh review budget for
unchanged historical content is asserted here. Root must retain actual review artifacts and count any applicable prior rounds when
presenting this proposal; no review has been invoked by this author.

This proposal expands from the held three-action slice to the future Plan5
contract: policy tiers, materiality boundaries, five registry entries, request
provenance, decision commit, executor isolation and recovery. Detailed deployment
and notify adapters remain disabled until their separately specified target
contracts pass the gates below. A disabled registry entry is explicitly refused;
it is not a fallback shell command or a claim that Plan5 is fully accepted.

Historical questions are resolved or remain gated as follows:

| Held point | Treatment here |
|---|---|
| D4 envelope fields outside payload | Exact canonical action envelope includes idempotency key and approval revision; actor identity is attached from authenticated decision and checked separately, without a self-referential digest (§4). |
| D6 draft digest at create | Staged ID allocation, then whole-ask installation only after its gate; read back and recompute shared digest before any card or blocking edge (§5). |
| D9 writable Beads/journal forgery | Sandbox reaches neither. Beads is authoritative for decisions, dispatch claims, execution state and results; wsd is the only writer. The effector has proven read-only access and submits bounded authenticated reports to wsd. Host daemon/DB administrators remain trusted. |
| D16 action label routing versus purpose | A typed readout purpose is authoritative. Labels may index, never select decision writers or substitute for an action payload. |
| Resume text only through status | Durable decision/result notice is delivered on actual resume, with status as recovery view; consumer integration must prove this seam. |
| Drain shorter than action timeout | Shutdown has explicit stop admission and bounded wait; interrupted in-flight work is uncertain and reconciles, regardless of configured durations. |
| Root-owned executor copies | Immutable reviewed release plus root-owned configuration and launcher required; source checkouts are not executable authority. |
| Read-only Beads user unproven | Blocking integration spike and deployment prerequisite (§8), not an assumed ACL recipe. |
| Relay retire versus submit (§17 #9) | Select submission for ALL approval kinds at coordinated cutover (§6). No direct design, terminal or forge decision writer remains active beside wsd. The interim path remains unchanged until that cutover is ready. |

## 3. Policy and materiality

Policy evaluates a typed intent, trusted origin, approved host policy revision
and workstream restrictions. Reuse `effective_tiers`; restrictions can only
raise severity. Unknown classes, malformed registry entries and unreadable policy
refuse admission with a recorded reason, without performing the tool's effect.
Workstream data cannot widen the host tier or change targets or credentials.

The deterministic floor is ADR §5.3: worktree-local, reversible sandbox actions
such as tests and local commits may be auto-approved; outward actions, deploys,
messages, money, secrets and deletion outside the worktree require operator
escalation. Changes that widen harness/policy/sandbox permissions are hard denied
for agent execution. Operator-driven maintenance and approved policy proposals
use their own authenticated workflow, never a disguised action request.

The registry executes only the five kinds in §4. An escalated request outside
it is an ask with a bounded scope, not executable authority. A human approving
an ask does not cause wsd-act to run arbitrary commands. Grey-zone classification
calls the typed gatekeeper with a 60-second deadline; unavailable, invalid or
late output escalates, never grants permission. A gatekeeper cannot override a
hard deny or deterministic lint failure. Repeat denied requests with the same
canonical intent in the same session remain denied; materially changed payloads
require a fresh ask and show their relationship to the earlier denial.

Materiality is separate from permission. Rewriting intake changes a target
workstream/repository, requirement, question into action, or destructive/outward
effect => `kind:confirm`, no worker routing label and no dispatch until an
authenticated confirmation. A typo or reference resolution is minor only when
the original request already
uniquely identifies the same canonical repository/workstream and resolving it
introduces no choice or changed requirement/effect. Ambiguous reference, a new
target choice or a different canonical target requires authenticated
`kind:confirm` before routing. Preserve the original text and display the proposed
interpretation; neither a resolver nor a fallback may guess that choice into an
action request. Confirmation does not itself approve a design or outward
effect. The classification proposes a result; it neither authorizes an action nor approves
a design. The §5.1 raw-text/unreviewed fallback preserves original intent;
it cannot bypass later permissions, design approval, or executable validation.
Changes to an approved action's effect invalidate it; enrichment replaces the
whole ask and creates a new card/digest. An implementation must not interpret
material changes as small edits to an already approved payload.

## 4. Closed action contract

Decode strict tagged objects: reject unknown fields, duplicate JSON keys,
wrong scalar types (including boolean-as-integer), control characters,
oversized/deep input and unsupported schema versions. No shell, command string,
environment overrides, credential handles or arbitrary filesystem targets.
Agent requests supply only intent and explanatory context. Host configuration
maps repository aliases, deployment targets and channel references to fixed
resources; agents cannot submit paths, remotes or transport endpoints.

| Type | Effect fields and immediate checks |
|---|---|
| push_branch | repo alias, from_ref, to_ref, expected_sha; source belongs to the requesting bead branch; push exactly the pinned commit, not a later branch tip; destination protected by configuration; no force or deletion. |
| open_pr | repo alias, head, base, title, body_ref; body_ref is a pinned commit/path; additionally bind the resolved head commit in the canonical envelope so later branch movement requires re-approval; require expected head at execution. |
| merge_pr | repo alias, PR number, expected_head_sha, fixed merge/squash/rebase method; same-repo target and head must match; honor forge checks and branch protection; never admin/auto/delete-branch. |
| deploy | target alias only from configured targets; envelope binds immutable release/artifact and target-definition revision resolved from that alias; reject mutable or unversioned target definitions. No generic deployment adapter is enabled by this document. |
| notify | channel_ref and exact text; configured channel only, bounded secret-checked text, no arbitrary recipient; adapter must provide a correlation/receipt/reconciliation contract before enablement. |

Availability is separate from registry decoding: all five adapters are unavailable
by default. Push/PR/merge are candidate Stage 1 capabilities, not enabled merely
because this table names them. An unresolved native claim/result/reader API,
undefined target reconciliation/receipt contract or unpassed acceptance matrix
keeps the affected adapter unavailable. Deploy/notify remain disabled until
separate reviewed contracts, approvals and deployment gates; future table rows
do not constitute executable capability.

Canonical action data consists of schema version, typed effect and resolved
immutable target facts, trusted request-origin identity, idempotency_key equal
to the allocated approval ID, and immutable request revision assigned before
hashing. Allocation therefore precedes whole-ask construction, using the staged
protocol in §5. The authenticated approver is added to the execution envelope
only after decision and must match the committed decision; it is neither agent
selected nor hashed into its own digest. The operator sees the effect and exact
target facts verbatim. The shared ADR/btq context_digest remains unchanged:
title, description, whole ask and resolved references; workflow and decision
metadata remain excluded.

This explicitly interprets ADR's payload revision as a request revision, while
`decided_digest` binds the complete content. If review concludes ADR requires
another representation, implementation stops for an ADR amendment/approval;
it does not silently introduce another digest algorithm.

Branch syntax and object IDs are validated by fixed grammar and Git's own ref
checks. The implementation plan must state supported object formats and issue-ID
shapes using the integrated queue contract, rather than copying Plan5a's
hardcoded SHA-1 and bead assumptions. No arbitrary revision expressions. Body
content comes from the executor's verified object store, never a working tree.
The push commit need not equal a WIP parking commit: the displayed expected_sha
is the only push authority.

## 5. Request, ask and persistence

The launcher attaches trusted workstream, bead, session identity, generation,
repository mapping and worktree from its own launch record after socket-token
validation. Reject stale/replaced generations, unowned beads and mismatched
repository relationships. Ignore/refuse client attempts to supply these fields.
A session socket never exposes decide, settle, configuration, cross-session
status or arbitrary queue operations. Duplicate request delivery has a stable
origin-scoped key and returns the recorded request, not a second approval.

Persist admission before acknowledging it. Journal requests have bounded input,
per-origin outstanding limits, lifecycle state and association with the approval.
Use the integrated journal migration/checkpoint APIs; version numbers must be
selected against the final base, not taken from Plan5a's historical v4 proposal.
Migrations preserve history and refuse partial schemas. A full spool cannot
silently lose a request already acknowledged as durable.

Filing uses a durable origin-scoped creation key and recorded stages. Record
creation intent before allocating one unposted, unroutable approval shell tagged
with that key. Read back its stable native ID, construct the ID-bound whole ask,
pass deterministic lint and mandatory sufficiency judgement, install the whole
ask once at that gate and read back its resolved digest. An incomplete shell is
not a filed ask, cannot receive a card or decision, cannot block the originating
task and cannot satisfy a completed-filing acknowledgement. Only after the whole
ask and digest are verified may filing add the blocking edge, park the task and
post the exact card, with each stage recorded for recovery.

A lost creation acknowledgement reconciles the unique creation key through a
supported bounded native lookup. Exactly one matching shell may be adopted;
multiple matches, unavailable lookup or ambiguous ownership hold for supported
operator reconciliation, never blind repeated creation. Preserve the shell and
history on failure. Retrying later stages reuses the same ID and revision;
materially changed content follows the normal new ask/revision protocol and
cannot reuse an already approved envelope. A pre-implementation spike must prove
native allocation, unique-key lookup, conditional installation and partial-state
visibility. Missing APIs block coding of this path rather than permitting an
assumed preallocated ID or an apparently completed half-formed ask.

New Plan5 action cards and execution remain disabled until mandatory ADR §5.9
sufficiency judgement is available and enforced. The existing narrowly scoped
interim admind-relay exception is not extended to typed wsd actions. Pure module,
fake-backend and disabled-path development may proceed under their own approved
implementation scope; this does not enable posting or effects. At coordinated
wsd/gatekeeper cutover, every ask, including formerly interim relay asks, passes
the mandatory gate. A permanent or new type-specific exception would require a
separately reviewed and genuinely approved ADR change, outside this design.

Resume requires the actual dependency/decision state and retained ownership.
Denial resumes with explicit denial text; success resumes with approved exact
action and verified result; failure/uncertainty stays parked with a recovery
condition. Status exposes the same durable notice. Neither an answer timestamp
nor a comment standing alone is a decision or a completion receipt.

## 6. Single decision writer and Marmot handoff

The §17 #9 choice is submission for every approval kind, including design/ADR,
non-action asks and executable actions. Sender authentication is retained by
admind, which submits bounded events containing the approval ID, exact shown
digest, surface/event identity and authenticated sender. Any later enabled
GitHub, Radicle or terminal approval entrypoint must submit to the same wsd
queue, never write decisions independently. Purpose selects
validated behavior after decision; it never selects an independent decision
writer. Labels cannot confer authority. A transport acknowledgement does not
prove a committed decision; ambiguous submission reconciles by event identity
and never falls back to a second writer.

For this delivery all operator questions, design approvals, deployment approvals
and action decisions remain on Marmot through admind, under the operator's current
delivery requirement. Terminal/chat are diagnostics, not consent; terminal and
forge decision entrypoints stay disabled/fenced here. The ADR's wider surface
architecture is not permission to enable those channels in this deployment.
Any later channel enablement needs an explicit reviewed policy/deployment choice
and the same single writer, never a second direct writer.

One serial wsd decision queue implements ADR §5.4: persist and deduplicate the
inbox event; freshly check sender authority and policy; read the bead; compare
the shown and current digest; conditionally commit the first valid decision;
read back the exact event, digest, decision, operator and decision time; then
mark the inbox event committed. Preserve native design approval validation and
metadata through a supported finalization adapter invoked only by that serial
writer. It must not remain a separately callable direct decision writer. Later
conflicting events are recorded as superseded. Bead-write/readback ambiguity
stays pending and reconciles against the authoritative bead; the journal is not
approval authority. Apply the ADR's bounded write retries and needs-human path.

Cutover is a coordinated deployment prerequisite, not a per-request label
switch. Before enabling the queue, retire or convert every old direct writer,
including admind's design path, terminal approve-bead and forge consumers. A
recorded writer mode/release fence must reject old direct entrypoints while the
queue is enabled. If any entrypoint or native finalization API cannot satisfy
this, the future queue and executor remain disabled and the existing interim
relay remains unchanged. Operator recovery first fences/stops admission and
uses supported authenticated management APIs under existing authority, with
new questions/decisions only through Marmot/admind; it cannot add a concurrent
direct decision writer or manufacture approved metadata.

Approval starts execution only after all execution admission gates pass; it
does not close the action bead. Denial closes with no execution.

An authenticated heart commits an ordinary approval through the same digest,
authority and first-decision checks AND files an idempotent policy-rule proposal,
as required by ADR §5.4 and the existing reaction contract. Rule proposal filing
has a durable association with the decision event and an independent recovery
state. Proposal failure cannot undo the committed decision, repeat execution or
silently install a rule. Rule application requires its own genuine approval.

Fresh authority and current policy are rechecked at the privileged effect
boundary; removals, hard-deny changes, digest changes or target changes prevent
execution. A newer policy that remains equally restrictive may proceed only
if the implementation can prove the same approved effect; otherwise reissue
for approval.

## 7. Executor, credentials and target reconciliation

`wsd-act` runs as the separate non-login effector user prescribed by ADR §3.1.
Only it can access forge/deploy/notify credentials. Root owns the reviewed
launcher, complete executable/import/dependency tree, configuration and target
mapping; neither the service user nor effector can replace trusted code or
configuration. No source-checkout import, writable PATH component, user site,
PYTHONPATH, inherited Git config, hook, helper or alternate object store may
redirect privileged execution. Use fixed executables, argv arrays, closed stdin
unless explicitly needed, sanitized environment and bounded output/time.

The Linux sudoers capability is exactly the fixed absolute launcher plus
`run <approval-id>` as the effector user, with no shell and no alternate modes.
A sudo wildcard alone is insufficient: the root-owned launcher enforces exactly
two arguments, strict supported ID grammar and no options, trailing words,
whitespace or path syntax. Check the rule with visudo and negative probes for
extra arguments/environment/alternate binaries. The installer must not grant
wsd the ability to act as the effector through Python, a shell, package manager
or another helper. Credential setup is a separate operator deployment step.

Executor independently reads the approval through a supported, verified
read-only Beads principal. It requires the complete committed decision fields,
validated whole ask and matching digest, approved operator, request revision,
immutable target pins and dispatch attempt. These authoritative Bead fields are
the queue commit record; a journal row or a writable sidecar is not a substitute.
Recompute the digest using the shared native contract and revalidate current
policy and targets. No CLI parameter may override these facts. The service user
and database administrators remain trusted host principals; this design does
not claim protection from a host administrator able to forge those records.
Sandbox code can reach neither store, the report endpoint nor sudo.

No privileged Git invocation may use an agent-writable repository, its local
transport/upload-pack, configuration, hooks, helpers or alternate object store.
Unprivileged wsd, without effector credentials, stages a bounded self-contained
Git bundle from the configured source; all resulting bytes are untrusted. The
handoff is a fixed service-to-effector directory protocol, never an agent-chosen
path or command. The effector opens the expected regular file without following
symlinks, validates the opened descriptor/size, copies it into its private store
and uses only that private copy thereafter. Agent path replacement cannot alter
the bytes under verification. Reject unsupported object formats, incomplete
closures, malformed bundles and exceeded byte/object/time limits.

Verify/import the bundle only in the effector-owned bare store using fixed
executables, sanitized environment, root-owned configuration and disabled hooks,
helpers and external transports. Bundle data cannot install configuration,
remotes or alternates. Independently verify the complete approved object closure
and exact commit/body/head facts before any credentialed adapter call. No
privileged process runs Git with an agent checkout as its working repository.
Credentials never enter the unprivileged staging environment. Prove the exact
bundle/parser, descriptor-transfer and configuration isolation behavior using
negative probes before admitting an adapter. Git/forge responses remain bounded
and typed; invalid or truncated output is unknown, not proof of absence.

wsd alone persists execution intent, dispatch attempts, state transitions,
results, evidence and closure on Beads through supported native APIs. The
journal tracks operational delivery/deduplication only. The effector cannot
write Beads and has no separate authoritative execution database. It submits
bounded typed claim/result reports over host IPC authenticated by the effector
OS peer identity and bound to approval ID, attempt ID, request revision, digest
and the recorded launch. The endpoint accepts no arbitrary metadata, commands
or file paths. Wrong peer, stale attempt, mismatched pins or oversized reports
are rejected without state changes. The exact native conditional-write and IPC
contracts must be demonstrated before enabling effects.

The dispatch record contains approval ID, request revision, decided digest,
typed target pins, unique attempt ID, purpose, expected prior state and one-use
claim state. Supported native reads must return these together without a partial
record being interpreted as admission. The serial writer accepts claim/result
transitions only against that exact record and expected prior state, then reads
back the committed tuple; mismatch is uncertainty. A report also binds the
trusted recorded launch identity, not just an effector UID or client-claimed
attempt. The integration spike must prove that the OS-authenticated connection
belongs to that invocation across sudo/exec/restarts and cannot be reused by a
second concurrent invocation. No new privileged argv, agent-provided path or
unverified process identity may supply that proof. If the configured native API
or host IPC cannot enforce this, all effects remain disabled pending a concrete
reviewed interface correction; no implied SQL or effector-writable fallback.

The sole privileged command remains the fixed launcher `run <approval-id>`.
Before invoking it, wsd records and reads back a dispatch attempt with an
explicit purpose: initial effect or read-only reconciliation. Absence, damaged
state, missing fields and ambiguous writes never imply permission to perform
an initial effect. For an initial attempt, the effector first verifies the
committed approval and requests its one-use dispatch claim. The single writer
durably consumes that claim and records executing before acknowledging it;
the effector independently reads back that exact consumed claim before acting.
Only the invocation holding the matching launch claim may proceed. A lost
acknowledgement, restart or crash after claim consumption yields uncertainty
and reconciliation only. It does not restore initial permission or create a
new initial claim. Before the effect, query whether the exact approved effect
already exists, using the action's evidence contract.

A per-approval effector lock outside temporary cleanup paths serializes all
invocations; wsd also serializes the execution lane. A reconciliation dispatch
uses the same `run <approval-id>` command, with purpose read from its persisted
record, and permits target reads only. It cannot become an initial effect due
to current target absence. There is no privileged reconcile, settle, force or
retry mode. Lock acquisition, claim validation and effect admission must fail
closed across concurrent invocations and process restarts. Unreadable durable
state stops admission and exposes a recoverable needs-human condition.

| Action | Positive evidence | Uncertain/conflicting evidence |
|---|---|---|
| push | approved expected_sha is demonstrably reachable from the configured destination; the retained pre-effect tip, ancestry checks and non-force submission match the same target and attempt | unreadable/incomplete history, rewrite, conflicting ancestry or unbound submission holds; no force correction or speculative second push |
| open_pr | unique marker bound to approval/revision plus matching repo/head/base/body facts | multiple candidates, missing visibility or mismatched head holds; no second create |
| merge | exact PR and approved head demonstrably merged, verified forge result | unrelated merge/head drift or unavailable result holds; no second merge |
| deploy | target adapter proves approved artifact active and health contract satisfied | partial rollout/unknown version needs-human; no automatic rollback outside approved target contract |
| notify | receipt for the exact approved message/correlation key | unknown delivery stays needs-human; never duplicate-send on missing receipt |

For push_branch, record the configured destination's observed pre-effect tip
(or proven absence for creation) and verify that publishing the exact approved
commit is a non-force fast-forward from that state. Submit only that pinned
commit. Target reads and retained adapter receipts must identify the same
repository, destination and attempt. If the destination already contains the
approved commit, record read-only idempotent success without another push. A
later legitimate descendant does not erase proof that the approved commit is
published. Reconciliation requires complete ancestry evidence sufficient for
this claim; shallow, truncated, unavailable or conflicting history is unknown,
never proof of absence. No mismatch authorizes force, reset or a repair push.

After effect execution, the effector sends a typed result/evidence report to
wsd. The writer validates its bound attempt, persists the result and action
state and reads them back before acknowledging. Success with the prescribed
positive evidence closes the approval; definitive failure records evidence and
leaves it open/needs-human; ambiguous outcome remains open/uncertain. Lost result
acknowledgement reconciles the bead and target; it never authorizes a repeated
effect. If wsd is unavailable, no new claim is granted. An already consumed
attempt remains executing/uncertain until supported read-only reconciliation
can establish its result. Shutdown timeout cannot imply failure or release a
blocker. A genuinely new effect attempt requires a new approval.

Manual settlement is an authenticated operator event sent to the same serial
writer. It binds the exact action, current attempt and recorded target evidence;
wsd may schedule a read-only reconciliation dispatch through the sole launcher.
A comment is not a result or decision. If machine proof is impossible, retain
an explicitly operator-attested disposition and its exact authorized scope;
never label it machine-verified success. Closure/resume from such a disposition
requires explicit operator authority for that disposition and must show the
attestation and remaining uncertainty. No manual event can rewrite the original
approval, grant another initial effect or silently release a different blocker.

## 8. Dependencies and staged implementation admission

These are proposed implementation slices, not newly claimed tasks:

1. Reconcile scanner/retention and consumer integration (btq-fu366; scanner
   btq-1wu2i/pxtig) on a reviewed current base. Pin request-origin and safe
   retained-evidence contracts; no weakening scanner oracles for this feature.
2. Prove independent read-only Beads access using the actual configured backend,
   separate principal and supported reader. A `--readonly` flag or filesystem
   ACL is not proof of database grants. Prove denied writes and correct digest
   resolution, including reads from the effector-owned object store.
3. Policy/materiality and strict registry as pure modules; durable request and
   decision state machines with migrations and crash checkpoints; fake effects.
4. Integrate generation-authenticated session requests and park/resume delivery,
   preserving Plan5i contracts rather than adding a second request pipeline.
5. Specify and prove all-kinds decision submission with admind and fencing of
   terminal/forge direct writers, including native design finalization and
   full displayed payload,
   authenticated sender, durable receipts, cutover fencing and ambiguous answers.
6. Prove the sole-launcher/one-use-claim/report interface, mandatory gatekeeper,
   object ingress and read-only principal; admit push/PR/merge only after their
   complete fake-target success/failure/uncertainty and crash matrices pass.
7. Keep deploy and notify unavailable. Each requires a separately reviewed and
   approved target/receipt contract and its own complete matrix before enablement.
8. Review exact source and propose a separately approved initial deployment with
   direct design link, deviations, writer cutover, database/IPC/sudoers proof,
   stopped backup/migration rehearsal, rollback and scratch acceptance limited
   to enabled push/PR/merge. Later adapters require their own deployment approval.

Implementation admission stops if required scanner/retention, current-base
release revisions or queue/decision interfaces remain unresolved. Release
completion is a separate gate: enabled actions proven, disabled entries exposed
as unavailable, no claim of full five-action Plan5 until all adapters meet it.
Plan6 Marmot topology, Plan7 full intake/gatekeeper and Plan8/9 operations and
three-day migration acceptance remain distinct downstream work. This future
proposal does not delay the separately approved interim harness by claiming
it already depends on an installed action executor.

## 9. Acceptance and failure proofs

Offline tests use disposable local repositories, fake forge/target executables,
synthetic identities, injected clocks, fake queue/read-only backends and temp
journals. No real sudo, network, credentials, service manager, shared Beads DB,
Claude/Codex login or operator messages. Named modules only until test safety
is audited; do not infer broad queue tests are offline.

Required contract cases:

- Strict payload/property tests; unknown fields/kinds, duplicate keys, malformed
  IDs/refs, branch traversal, target alias drift and secret-bearing input refuse.
- Tier floors/restrictions; hard-deny cannot be elevated by operator-card route;
  grey-zone unavailable/late output escalates; material confirm cannot authorize
  execution; repeated deny and corrected intent behave distinctly.
- Cross-session/generation/repository spoofing, replaced launch, wrong ownership,
  request retry after lost acknowledgement and bounded pending capacity.
- Ask mutation before/after posting, target/body/head movement, enriched digest,
  denied/conflicting events, authority/policy removal during each await and
  immediately before effect; typed-purpose routing independent of labels.
- Every persist/write/readback/inbox/action boundary interrupted and restarted;
  exactly one decision/effect or honest unresolved state; journal loss never
  manufactures approval. Parked tasks retain ownership and resume exact notices.
- Executor argv/env/config/import/credential isolation; no agent-writable Git
  execution, symlink/config/helper injection or inherited sudo environment;
  read-only reader demonstrably rejects writes.
- Every enabled adapter has a complete success/failure/uncertainty matrix and
  restart proof. Initial push/PR/merge cases cover later destination descendants,
  ancestry conflicts, PR marker collisions, ambiguous replies and no speculative
  second effect. Disabled deploy/notify entries always refuse and are advertised
  as unavailable; placeholder success is forbidden. Their later enablement adds
  receipt/health/partial-rollout and no-duplicate-send cases under separately
  reviewed contracts rather than claiming those tests already exist.

- Journal migrations preserve history, identity, decisions and pending holds;
  partial schema and failed migration rollback; stale close/settle cannot race
  active execution. Mutation probes must kill removed digest/head/provenance
  checks, speculative retry, broadened sudo argv and weakened tier floor.

- All-kinds single-writer cutover: design/ADR, non-action, action, terminal and
  forge entrypoints cannot race an independent direct writer; native design
  validation is retained. Heart produces exactly one ordinary decision and an
  independently recoverable proposal; proposal loss does not repeat the effect.
- Allocation/finalization interruption at every stage: zero cards/edges for
  incomplete shells, unique adoption after lost creation reply, duplicate-key
  ambiguity held, and whole-ask digest computed only after native read-back.
- One-use execution claim and typed result reports interrupted before/after each
  write, read-back and acknowledgement, including concurrent launches and writer
  outage. Missing/consumed/damaged dispatch records never become initial effects;
  reconcile mode cannot mutate targets; forged/stale reports cannot close tasks.
- Object ingress rejects agent Git config/upload-pack/helpers, replaced symlinks,
  malformed or oversized bundles, incomplete object closure and inherited
  credentials. No privileged Git process executes within an agent repository.

Stage 0 is pure policy/codec/state-machine work using offline fakes, with posting
and effects disabled. Stage 1 completion requires the all-kinds writer cutover,
mandatory gatekeeper, proven read-only access and enabled push/PR/merge matrices,
plus refusal/unavailable exposure for deploy and notify. Stage 1 live acceptance
requires its own deployment approval and a scratch repository; exercise actual
separate-user permissions, forbidden sudo forms, credential exclusion, approve/
deny, uncertain restart and read-only target reconciliation only for the enabled
adapters. It does not require a nonexistent deployment/notify adapter or authorize
production messages. Record source/config/target pins, receipts, before/after
state and rollback limits without credential contents.

Later deploy and notify stages each require their own reviewed contract, human
approval, complete offline matrix and separately scoped live deployment/test
channel approval. Full five-action Plan5 completion is claimed only after all
five adapters pass their applicable gates. Neither this design nor offline
success authorizes a live run. Plan6/7/8 milestones retain their independent
scope and acceptance; mandatory sufficiency for these action cards is not a
claim that all Plan7 intake behavior is complete.

## 10. Explicit choices for the operator

Approval of the revised design must cover all-kinds admind submission to the
sole wsd decision writer and fencing of terminal/forge decision entrypoints,
coordinated retirement of direct
writers, native design finalization, the request revision and ID-allocation
protocol, authoritative Bead execution claims/results with read-only effector
reports, safe object ingress, mandatory gatekeeper before action enablement and
staged push/PR/merge acceptance only after exact supported interfaces and
adapter contracts pass, with all undefined adapters (including deploy/notify)
disabled. These choices do not expand the interim exception or authorize implementation/deployment on their
own. Unresolved native API or ADR conflicts stop for a concrete reviewed
amendment; timeout and management overrides cannot fabricate approval.

## 11. Unchanged approved supervisor design publication

The controller requested a byte-preserving archive of a separate, already
approved design because its queue repository has no Git remote. The archive at
`../reviews/approved-inputs/2026-10-14-btq-supervise-cards.md` is exactly the Git
blob from the beads-task-queue repository, commit
`7b2d757364d3f0e21a0c6ec87a5b5af7b244fb81`, source path
`docs/designs/2026-10-14-btq-supervise-cards.md`. Its SHA-256 is
`46b0102cb4919aa97d75a7f5a0337d1ef537e8e54d72e5210b4bc6d1bd56670d`. The source approval is btq-s9ss7;
implementation task btq-j90wi. This publication changes no byte of that design,
adds no approval, and resets no review history. It makes the existing immutable
input available for a direct GitHub design link in a later separately approved
supervisor deployment ask. It is not a Plan5 implementation dependency.

Publication validation limitation: the unchanged archive's line 1067 contains
intentional email-format test examples and triggers the current install-agnostic
checker's email rule. The archive has not been edited or decorated with allow
markers, because that would cease to be byte-preserving. The controller must
resolve publication packaging/checker scope explicitly before a passing full
publication check can be claimed. This Plan5 document passes its named check.
