# Plan 5: typed policy and a separate action executor

Status: d4 UNREVIEWED author proposal for btq-e7knb; R1 REVISE10, R2 REVISE32, FINAL R3 REVISE30. All three rounds exhausted. No fourth review without an exact authenticated Marmot exception. Historical d3 remains unchanged. Future work only. No implementation,
installation, action execution, or new approval authority is granted by this
file. The controller must publish and review the exact revision and obtain a
new authenticated design approval before implementation admission.

## 1. Sources and current boundary

The governing architecture is ADR 0001 revision 17 rebased on r16 Amendment 1,
in the separate design repository at commit
`a27acecee8274e6ad8dc65def436614d42b5cb04`, file
`docs/adr/0001-workstreams-v2.md`, especially §§3.1, 5.1, 5.3, 5.4, 5.9,
7, 8.1, 8.2 and 17. Genuine native approval btq-ovngv records Liam,
2026-10-14T15:57:38Z, via Marmot `marmot:id:189fd6bb97ba`, approved digest
`c874eb3d81c0647f53005e5cfb1b35f33ca30bf6617bc10e84d24079d7ba6fbb`.
Its predecessor r16 approval is btq-xywu0 at
`93d0e90b121a07db720d92924cda443a1cfc239c`; Amendment 1 is the later
`b2c98953f476ba744ec455892c5a6f33ace60cc9`. None is itself a new approval
of this proposal. The source repository has no remote; exact Git object and
file hashes establish portable identity, not invented forge links. The older
roadmap is sequencing context; the explicit changes in §10 need human approval.

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
Actual review of this proposal at `79aec6f` was R1 REVISE10; at
`150a81ceb23dbdacf33fbe58f5fe5a8b8da8d373`, R2 REVISE32; and at
`0180a4321e74e93c81516ebb50f9ac7f10a0dd4a`, FINAL R3 REVISE30.
D4 addresses that actual final report, without claiming clearance or a fresh budget.
The ordered R3 dispositions and input manifest accompany this proposal. Historical
R1/R2 author responses describe their original revisions, not current-tree facts.
The private exact reports and verbatim ledgers must accompany any authorized further
review; their absence is disclosed, never represented as independently verified.

This proposal expands from the held three-action slice to the future Plan5
contract: policy tiers, materiality boundaries, five registry entries, request
provenance, decision commit, executor isolation and recovery. Detailed deployment
and notify adapters remain disabled until their separately specified target
contracts pass the gates below. A disabled registry entry is explicitly refused;
it is not a fallback shell command or a claim that Plan5 is fully accepted.

Historical questions are resolved or remain gated as follows:

| Held point | Treatment here |
|---|---|
| D4 envelope fields outside payload | Exact canonical action envelope includes idempotency key and request revision; actor identity is attached from authenticated decision and checked separately, without a self-referential digest (§4). |
| D6 draft digest at create | Staged ID allocation, then whole-ask installation only after its gate; read back and recompute shared digest before an approvable card; the bounded pending blocker precedes grooming as specified in §5 (§5). |
| D9 writable Beads/journal forgery | Sandbox reaches neither. Beads is authoritative for decisions, dispatch claims, execution state and results; wsd is the sole supported decision writer within the trusted service-user boundary. The effector has proven read-only access and submits bounded authenticated reports to wsd. Host daemon/DB administrators remain trusted. |
| D16 action label routing versus purpose | A typed readout purpose is authoritative. Labels may index, never select decision writers or substitute for an action payload. |
| Resume text only through status | Durable decision/result notice is delivered on actual resume, with status as recovery view; consumer integration must prove this seam. |
| Drain shorter than action timeout | Shutdown has explicit stop admission and bounded wait; interrupted in-flight work is uncertain and reconciles, regardless of configured durations. |
| Root-owned executor copies | Immutable reviewed release plus root-owned configuration and launcher required; source checkouts are not executable authority. |
| Read-only Beads user unproven | Blocking integration spike and deployment prerequisite (§8), not an assumed ACL recipe. |
| Relay retire versus submit (§17 #9) | Select submission for ALL approval kinds at coordinated cutover (§6). No direct design, terminal or forge decision writer remains active beside wsd. The interim path may remain only while the release does not ship both wsd and gatekeeper; §6 forbids a release with both and an ungated relay. |

## 3. Policy and materiality

Policy evaluates a typed intent, trusted origin, approved host policy revision
and workstream restrictions. Reuse `effective_tiers`; restrictions can only
raise severity. Apply a separate deterministic outward-action escalation floor AFTER effective_tiers in admission, posting and the effect recheck; host auto_approve cannot lower it. Unknown classes, malformed registry entries and unreadable policy
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
an ask does not cause wsd-act to run arbitrary commands. Grey-zone classification calls a distinct proposed permission judgement port
with a 60-second deadline and closed allow|deny|escalate result; unavailable, invalid or
late output escalates, never grants permission. A gatekeeper cannot override a
hard deny or deterministic lint failure. Repeat denied requests with the same
canonical intent in the same session remain denied; materially changed payloads
require a fresh ask and show their relationship to the earlier denial. The denial key is SHA256 of canonical registry kind and resolved effect payload/target facts, excluding prose, ask ID and revision, scoped to the originating logical session across its generations. Rewording or relaunch cannot bypass it; changing an effect field is a new intent.

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

Filing uses a durable creation key scoped to the originating logical session,
bead and request sequence, with at most 2 outstanding requests per bead across generations and a host-configured global cap. A redundant 8/session cap is not claimed as additional protection. Under the owning workstream lane,
revalidate launch generation/ownership/repository before each filing mutation,
including restart recovery. Record allocation intent and create one unposted
shell carrying `kind:approval`, `agent:wsd` and exact ws routing plus creation
key. Confirmations carry `kind:confirm`, `agent:wsd`; neither is exposed to
supervisor ownership/close cards. After exact shell readback, add the native
pending blocker and park/stop the origin while preserving its claim. Only then
use the existing D26 session-end landing path to obtain the exact requested
commit in the host object store. Retain the agent-requested SHA and prove its
relationship to the landed generation; never substitute host HEAD or WIP tip.
A failed/ambiguous landing holds the request and exposes a fixed recovery notice.

Construct the ID-bound whole ask and resolve refs only after that landing.
Persist an unapproved draft and its immutable request revision, pass lint and
mandatory sufficiency. Up to two grooming revisions preserve original intent and history. Steering
returns to the SAME originating parked session through a bounded grooming-only
resumption, never a replacement agent. Its capability permits request correction
and local work only; no outward action or independent approval. Record the resumed
generation under the same logical session, then park it again. If source changes,
land and revalidate the new exact SHA, whole ask, revision and digest before posting;
no old sufficiency record or approval transfers to changed content. This proposed
session capability/resumption seam needs offline proof against actual session APIs;
unsupported recovery holds and asks the operator, without inventing a resume API. The final current whole ask/digest is read back before an approvable
card. A twice-groomed failure becomes cannot-approve-as-written. Render a bounded
Marmot question with safe reason codes (missing-context, ambiguous-target,
unsupported-effect, lint-failed), fixed identity labels and only groom or deny
choices; recommended deny. An actual authenticated deny is required to commit
denial; silence holds indefinitely with no auto-denial or approve route. Groom
returns steering to the same originating session under the preceding restriction;
a fresh valid request must pass the complete pipeline. Unsafe notice rendering
holds for approved Marmot maintenance. This behavior changes ADR exception 3 and
requires the real ADR amendment in §10, not an asserted design override.


Incomplete shells never receive an approve route or count as filed acknowledgement.

A lost allocation reply uses supported bounded creation-key lookup. Exactly one
matching shell can be adopted; ZERO, multiple, unavailable or ambiguous matches
remain allocation-unknown with no repeated create. Read-only lookup has at most
three attempts at 1/5/30 seconds, then needs-human via Marmot. Operator reconciliation
binds all original shell IDs/key/evidence and may designate the unique canonical
shell and close orphan shells as not completed through the same writer; it never
invents an approved ask. Terminal journal states completed, denied, cancelled,
reconciled-abandoned release the outstanding slot only after all associated shells
and native blockers are read back consistent; unknown never releases capacity.
New generation alone cannot free the old slot. A stale-origin cleanup runs only
against shells carrying its exact creation key and expected prior state, under
that origin's filing journal; it cannot mutate a successor's claim or request.
Known unposted shells may be cancelled through the sole writer after readback.
Unknown allocation or ambiguous cleanup retains the slot until bounded lookup
or explicit authenticated recovery establishes canonical/orphan disposition. Native conditional allocation,
lookup, draft install, routing, landing and finalization APIs are implementation
prerequisites to prove in disposable fixtures, not installed support claimed here.

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
digest, surface/event identity, authenticated sender, decision value approve|deny, bounded denial reason (at most 1024 Unicode scalar values), and original normalized reaction token. Preserve native via, observed decision time and exact inbound provenance; no caller can assert those authentication fields. Any later enabled
GitHub, Radicle or terminal approval entrypoint must submit to the same wsd
queue, never write decisions independently. The proposed native purpose schema accepts design, execution and permission.
Execution requires a complete typed action; design retains real ADR/review pins;
permission is a bounded non-action consent scope and never dispatches an effector.
Its strict payload is schema_version=1, host-configured consent_scope_id, exact
intent_digest and bounded question/context; unknown scope/fields refuse. The
host scope definition pins permitted non-executable interpretation and expiry;
it cannot supply a command, credential, destination or action authority. A
non-action answer alone cannot lift policy or trigger an outward effect.
Adding permission and action-aware finalization requires the real ADR17 amendment
and separately admitted queue implementation covered by this design's exact scope.
An absent purpose remains legacy/unclassified and nonselectable: do not infer design
from validation, labels or shape. A genuine operator migration/reissue is needed
before finalization, preserving old history. Unknown values refuse.


Purpose selects validated behavior after decision; it never selects an independent decision
writer. Labels cannot confer authority. A transport acknowledgement does not
prove a committed decision; ambiguous submission reconciles by event identity
and never falls back to a second writer.

For this delivery all operator questions, design approvals, deployment approvals
and action decisions remain on Marmot through admind, under the operator's current
delivery requirement. Terminal/chat are diagnostics, not consent; terminal and
forge decision entrypoints stay disabled/fenced here. The ADR's wider surface
architecture is not permission to enable those channels in this deployment.
The proposed ADR amendments are enumerated once in §10. Existing ADR §8.1
exceptions remain authoritative until an actual reviewed, human-approved amendment
is committed and linked through genuine native admission. This proposal alone
cannot override them or claim implementation is admitted under old ADR pins.
Only Marmot/admind is selected for new consent in this delivery; if safe delivery
fails, HOLD and use separately authorized maintenance, not terminal consent.



One serial wsd decision queue implements ADR §5.4: persist and deduplicate the
inbox event; freshly check sender authority and policy; read the bead; compare
the shown and current digest; conditionally commit the first valid decision;
read back the exact event, digest, decision, operator, via and decision time; then
mark the inbox event committed. Preserve native design approval validation and
metadata through a supported finalization adapter invoked only by that serial
writer. Posting and pre-approve gate preview become purpose-aware: design retains
all native approval_valid checks, execution requires its complete typed payload,
current digest-bound sufficiency and execution policy, other forms require their
explicit contract. Unknown forms refuse; do not skip all previews or fake ADR pins.
The adapter journals update/close/pins.finish stages and on recovery reads each
native result and completes remaining exact pin effects idempotently, including
a closed approval with unfinished pins; a closed status alone is not completion.
 It must not remain a separately callable direct decision writer. Later
conflicting events are recorded as superseded. Bead-write/readback ambiguity
stays pending and reconciles against the authoritative bead; the journal is not
approval authority. Apply the ADR's bounded write retries and needs-human path.

Cutover is a coordinated deployment prerequisite, not a per-request label
switch. Before enabling the queue, retire or convert every old direct writer,
including admind's design path, terminal approve-bead and forge consumers. The deployment-owned release fence disables all supported direct decision
entrypoints before enabling the serial writer, including approvals, confirmations,
settlement and recovery. Retained old binaries are removed from supported paths;
negative integration probes verify supported routing, not hostile same-user denial.
wsd, admind's unsandboxed administrative agent and the operator terminal share a
trusted service UID. They can read that user's credentials and modify the same
Dolt metadata. There is NO credential-enforced decision-key ACL, hostile same-UID
fence or revocation of all shared queue writers here. Service-user/admin compromise
can bypass this protocol; the human must explicitly accept that residual.
Sandbox exclusion and separate effector credential isolation remain mandatory.
A stronger separate-UID/store boundary would require a new approved design, not
an implied deployment recipe. At the first release enabling the shared judgement
service, the relay judgement adapter is installed and tested BEFORE advertising
wsd+gatekeeper availability; fail-closed posting precedes enablement. Writer/executor
failure cannot restore ungated relay operation. Mere executable presence is not
an exemption from ADR's release trigger; approved activation documentation must
state the exact coupled release boundary.


Operator recovery first fences/stops admission and
uses supported authenticated management APIs under existing authority, with
new questions/decisions only through Marmot/admind; it cannot add a concurrent
direct decision writer or manufacture approved metadata.

Approval starts execution only after all execution admission gates pass; it
does not close the action bead. Denial closes with no execution.

An authenticated ❤️ or ♥️ on any supported approval commits an ordinary approval through the same digest,
authority and first-decision checks AND files an idempotent policy-rule proposal,
as required by ADR §5.4 and the existing reaction contract. Rule proposal filing
has a durable association with the decision event and an independent recovery
state. Proposal failure cannot undo the committed decision, repeat execution or
silently install a rule. Rule application requires its own genuine approval. On design/non-action forms the proposal is a typed policy suggestion, never
executable authority. Outward-action escalation remains a hard floor, so an action
heart proposal cannot lower it. Show that limitation; permission proposals may
matter only within their separately approved bounded non-action contract. Preserve the normalized original token in the event so the action writer can implement this choice deterministically.

Fresh authority and current policy are rechecked at the privileged effect
boundary; removals, hard-deny changes, digest changes or target changes prevent
execution. A newer policy that remains equally restrictive may proceed only
if the implementation can prove the same approved effect; otherwise reissue
for approval.

## 7. Executor, credentials and target reconciliation

`wsd-act` runs as ADR §3.1's separate effector user; this proposal additionally chooses a non-login account.
Only it can access forge/deploy/notify credentials. Root owns the reviewed
launcher, complete executable/import/dependency tree, configuration and target
mapping; neither the service user nor effector can replace trusted code or
configuration. No source-checkout import, writable PATH component, user site,
PYTHONPATH, inherited Git config, hook, helper or alternate object store may
redirect privileged execution. Use fixed executables, argv arrays, closed stdin
unless explicitly needed, sanitized environment and bounded output/time. A root-owned fixed broker supervises a dedicated per-attempt cgroup, starts the effector child under its non-login UID, and retains cleanup authority across UID boundaries. Enforce timeout with TERM then KILL,
wait/reap and prove no surviving descendants before recording definitive failure
or permitting a new approval's attempt. Unknown cleanup leaves uncertain, not
failed. Read-only existence checks inspect every retained prior attempt for the
same canonical effect across approvals, not only the current approval marker.

The Linux sudoers capability is exactly the fixed absolute root-owned broker plus
`run <approval-id>`; the broker drops the effect child to the effector user, with no shell and no alternate modes.
A sudo wildcard alone is insufficient: the root-owned launcher enforces exactly
two arguments, strict supported ID grammar and no options, trailing words,
whitespace or path syntax. Check the rule with visudo and negative probes for
extra arguments/environment/alternate binaries. The installer must not grant
wsd the ability to act as the effector through Python, a shell, package manager
or another helper. Use a single reviewed managed installer with plan/apply/verify/rollback and exact
release/config pins for broker, effector account, sudoers, cgroup ownership and
credential placement. No ad hoc terminal credential recipe is authorized. Deployment
requires proof of empty cgroup populated state, reaped child and retired launch
before definitive cleanup; unavailable proof remains uncertain. wsd is not claimed
to signal or enumerate cross-UID children itself. The broker accepts no extra argv,
command, path or arbitrary process identifier. These are proposed interfaces and
deployment prerequisites, not installed facilities.

Executor independently reads the approval through a supported, verified
read-only Beads principal. It requires the complete committed decision fields,
validated whole ask and matching digest, approved operator, request revision,
immutable target pins and dispatch attempt. These authoritative Bead fields are
the queue commit record; a journal row or a writable sidecar is not a substitute.
Recompute the digest using the shared native canonical encoding and a proposed
explicit read-only ref resolver: allowlisted logical repository identity plus
exact commit/path/range maps only to verified effector-owned Git objects. Preserve
the original canonical repo locator in hashed data; replace only the lookup port,
never the hashed ref value. No path access to service-user checkouts and no
safe.directory wildcard. Disposable cross-UID tests must prove byte-identical
native resolution and denied arbitrary paths. Recompute and revalidate current
policy and targets. No CLI parameter may override these facts. The service user
and database administrators remain trusted host principals; this design does
not claim protection from a host administrator able to forge those records.
Sandbox code can reach neither store, the report endpoint nor sudo.

No privileged Git invocation may use an agent-writable repository, its local
transport/upload-pack, configuration, hooks, helpers or alternate object store.
Unprivileged wsd, without effector credentials, stages a bounded Git bundle from the configured source. V1 permits a self-contained
closure up to 64 MiB, 100000 objects and 30 seconds parse/import, otherwise returns
unsupported-source-budget without dispatch. It does not promise arbitrary large
repositories; prerequisite/incremental bundles need a later reviewed contract.
The effector store quota is 1 GiB: mark active/unresolved evidence roots, prune only
unreferenced objects after verified settled retention, refuse new imports if the
safe bound cannot be met; never delete unresolved evidence to make room. Treat all staged bytes as untrusted. The
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
typed target pins, unique attempt ID, dispatch_kind initial|reconcile, expected prior state and one-use
claim state, distinct startup nonce hash/effect-claim nonce hash and a five-second claim deadline beginning ONLY after preflight completes,
clipped to the recorded initial-dispatch deadline: 60 seconds from the first prepared dispatch, persisted with boot identity and UTC/monotonic observations. Retries never extend it; changed boot or ambiguous clock state holds initial dispatch. The root-owned launcher receives a private
inherited anonymous descriptor containing a fresh 256-bit startup-binding nonce through an
explicit fixed sudo preserved-descriptor rule; argv remains run plus approval ID.
The serial writer generates each distinct nonce, persists only its hash, and never sends it
to the sandbox. IPC peer UID and nonce together bind the invocation; a concurrent
launcher has no matching secret. Pin descriptor number and close all others;
launcher copies the startup nonce to bounded memory, closes descriptor before running any
Git/forge child, and does not log it. Exact sudo compatibility is a blocking
cross-UID disposable integration proof; absent support keeps effects disabled,
not a claimed current facility. Supported native reads return a typed complete tuple for the CURRENT stage.
Preparing records contain startup binding only and explicitly no effect claim;
ready records contain the fresh effect-claim hash/deadline. Missing stage-required
fields or a preparing tuple can never be interpreted as effect admission. The serial writer accepts claim/result
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
explicit dispatch_kind: initial or read-only reconcile. Absence, damaged
state, missing fields and ambiguous writes never imply permission to perform
an initial effect. For an initial attempt, perform read-only preflight FIRST: native approval read,
private bundle verification/import (up to 30 seconds), digest/closure/policy checks.
Preflight has no effect permission. Then the broker reports preflight-ready; the
writer freshly checks all source/decision/target facts, creates the one-use nonce
and starts the five-second window. Transfer that nonce over the bound private
channel to the already-running invocation, then request the claim. The startup
binding nonce and effect-claim nonce are distinct, typed and never reused. Total
initial preparation/launch still ends at the original 60-second deadline; preparation
failure consumes no effect claim. Unsupported authenticated channel proof disables
this adapter, rather than extending the five-second claim to cover object import. The single writer checks the claim deadline and fresh effect-claim nonce at commit, then
durably consumes that claim and records executing before acknowledging it;
the effector independently reads back that exact consumed claim before acting.
Only the invocation holding the matching launch claim may proceed. A lost
acknowledgement, restart or crash after claim consumption yields uncertainty
and reconciliation only. It does not restore initial permission or create a
new initial claim. An unconsumed claim cannot later execute: after launcher exit is proven and its
nonce retired in the same serial writer conditional transaction that checks no consumption and records dispatch-not-started. Retirement fences queued claim messages atomically, rather than relying on a separate read. Retry is
limited to three total launch attempts on the same approval and original deadline,
with a fresh nonce and preserved attempt history; ambiguous consumption holds for
reconciliation. A late queued claim after expiry/retirement is rejected. If consumed,
no new initial attempt is allowed even if the effect appears absent.
Before the effect, query whether the exact approved effect
already exists, using the action's evidence contract.

Destination-resource locks (push: repository/remote/destination-ref; merge: repository/base-ref and PR; open_pr: repository/head/base) in sorted canonical order, followed by canonical-effect and per-approval locks, all outside temporary cleanup paths, serialize invocations across approvals as well as within one approval; wsd also serializes the execution lane. Every retained preparing, ready, held, executing or uncertain prior attempt for that destination/effect blocks a new initial attempt until exact no-effect/descendant-exit proof or settlement. A different approval ID cannot bypass this fence. A reconciliation dispatch
uses the same `run <approval-id>` command, with dispatch_kind read from its persisted
record, and permits target reads only. It cannot become an initial effect due
to current target absence. There is no privileged reconcile, settle, force or
retry mode. Lock acquisition, claim validation and effect admission must fail
closed across concurrent invocations and process restarts. Unreadable durable
state stops admission and exposes a recoverable needs-human condition.

| Action | Positive evidence | Uncertain/conflicting evidence |
|---|---|---|
| push | approved expected_sha is demonstrably reachable from the configured destination with complete ancestry; retained pre-effect tip/non-force checks govern whether a new submission is allowed, not whether already-published state is recognized | unreadable/incomplete history, rewrite, conflicting ancestry holds; no force correction or speculative second push |
| open_pr | unique marker bound to approval/revision, included in the exact approved body_ref BEFORE hashing/posting, plus matching repo/head/base/body facts; never append a marker after approval | multiple candidates, missing visibility or mismatched head holds; no second create |
| merge | exact PR and approved head demonstrably merged, verified forge result | unrelated merge/head drift or unavailable result holds; no second merge |
| deploy | target adapter proves approved artifact active and health contract satisfied | partial rollout/unknown version needs-human; no automatic rollback outside approved target contract |
| notify | receipt for the exact approved message/correlation key | unknown delivery stays needs-human; never duplicate-send on missing receipt |

For push_branch, record the configured destination's observed pre-effect tip
(or proven absence for creation) and verify that publishing the exact approved
commit is a non-force fast-forward from that state. Submit only that pinned
commit. Before any credentialed submission, report pre-effect target/ancestry evidence to
the sole writer and require native Beads readback bound to this attempt. No durable
ack means no new push. Target reads and any receipts must identify the same
repository/destination; a lost result receipt cannot erase independently proven
reachability. Reconciliation can mark idempotent succeeded from complete current
reachability alone, explicitly attribution=observed-target (not a claim that this
attempt caused publication). If the destination already contains the
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
blocker. A genuinely new effect attempt requires a new approval. Reconciliation is bounded
read-only work: at most three attempts at 1/5/30 seconds, then needs-human with one
fixed recovery notice; no repeated automatic sudo/network loop. States are closed: dispatch-preparing, dispatch-ready, dispatch-held, executing,
uncertain, succeeded, failed, denied. Three exhausted launch attempts, original
60-second expiry or reboot uncertainty becomes dispatch-held with a durable
needs-human notice, never settled pending. SETTLED_ACTIONS includes only evidenced
succeeded and denied (and definitive no-effect failed only under its explicit
resume contract); preparing/ready/held/executing/uncertain always hold; unknown
values hold. Denial is a settled no-effect outcome, never a new workstream hold.

Manual settlement uses proposed Marmot `!action settle` in reply to
the exact recovery card. Admind authenticates current operator/group and sends a
bounded settlement-request event to the sole writer. The writer prepares a fixed
question showing attempt, target evidence digest, exact attested disposition and
remaining uncertainty; explicit authenticated A in that question thread authorizes
only that displayed disposition, B holds and is the recommendation. Thumbs-up accepts the recommended HOLD; only an explicit A attests the displayed disposition. No arbitrary JSON or commands are
accepted from text. Correlate message/card/attempt/current digest and reject stale
or conflicting answers; all current-principal checks and delivery requirements
apply. This new command/card handler is part of this proposed implementation,
not an existing verb. Manual settlement is an authenticated operator event sent
to the same serial writer. It binds the exact action, current attempt and recorded target evidence;
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
2. Implement the narrow Plan5 sufficiency service before action admission: shared
   typed Hermes sufficiency/groom and permission APIs, pinned judgement schema/model/revision and
   60-second deadline. This explicitly resequences that subset of Plan7 into
   Plan5; full Plan7 intake routing remains downstream. The Plan7 native dependency
   must point to this shared completed subset, never make Plan5 wait on full7
   while7 waits on6/5. Approval must cover this sequencing/scope choice.
3. Prove independent read-only Beads access using the actual configured backend,
   separate principal and supported reader. A `--readonly` flag or filesystem
   ACL is not proof of database grants. Prove denied writes and correct digest
   resolution, including reads from the effector-owned object store.
4. Policy/materiality and strict registry as pure modules; durable request and
   decision state machines with migrations and crash checkpoints; fake effects.
5. Integrate generation-authenticated session requests and park/resume delivery,
   preserving Plan5i contracts rather than adding a second request pipeline.
6. Specify and prove all-kinds decision submission with admind and retirement of supported
   terminal/forge direct writers within the accepted trusted-UID boundary, including native design finalization and
   full displayed payload,
   authenticated sender, durable receipts, cutover fencing and ambiguous answers.
7. Prove the sole-launcher/one-use-claim/report interface, mandatory gatekeeper,
   object ingress and read-only principal; admit push/PR/merge only after their
   complete fake-target success/failure/uncertainty and crash matrices pass.
8. Keep deploy and notify unavailable. Each requires a separately reviewed and
   approved target/receipt contract and its own complete matrix before enablement.
9. Review exact source and propose a separately approved initial deployment with
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
  second effect. All five unavailable adapter entries always refuse at admission, lint, posting, dispatch and independent effect recheck; enabled availability comes only from a root-owned reviewed release/config map pinning each adapter contract and acceptance evidence. Disabled deploy/notify entries always refuse and are advertised
  as unavailable; placeholder success is forbidden. Their later enablement adds
  receipt/health/partial-rollout and no-duplicate-send cases under separately
  reviewed contracts rather than claiming those tests already exist.

- Journal migrations preserve history, identity, decisions and pending holds;
  partial schema and failed migration rollback; stale close/settle cannot race
  active execution. Mutation probes must kill removed digest/head/provenance
  checks, adapter availability at each boundary, speculative retry, broadened sudo argv and weakened tier floor.

- All-kinds single-writer cutover: design/ADR, non-action, action, terminal and
  forge entrypoints cannot race an independent direct writer; native design
  validation is retained. Heart produces exactly one ordinary decision and an
  independently recoverable proposal; proposal loss does not repeat the effect.
- Allocation/finalization interruption at every stage: zero approve routes for incomplete shells; pending blocker and exact parking/landing precede grooming, unique adoption after lost creation reply, duplicate-key
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

## 10. Explicit choices and genuine ADR admission

This is the single authoritative change/approval checklist; other sections refer
to it rather than maintaining incomplete duplicate lists. A real separately
reviewed and authenticated ADR amendment must, BEFORE affected implementation
admission/cutover, amend §§5.3/5.4/5.8/5.9/8.1 and r17 purpose contracts to cover:

1. Marmot-only new consent and replacement of exceptions 3/6 with bounded safe
   groom/deny and decision recovery; no terminal consent fallback.
2. All-kinds supported sole-writer protocol (including confirm/recovery), accepted
   trusted same-UID administrative bypass residual, no credential-enforced ACL claim.
3. Non-action permission purpose, no absent-purpose inference, purpose-aware queue
   finalization; per-repository native implementation tasks/pins for queue changes.
4. Narrow sufficiency AND permission judgement ports resequenced before Plan5;
   sufficient/enrich/groom semantics and originating-session bounded grooming.
5. Canonical request revision/allocated ID and approver identity kept in the
   authenticated decision audit rather than self-referential hashed payload.
6. Heart proposals on all approval forms, with outward escalation floor unchanged.
7. Non-login effector/root cleanup broker, distinct preparation/claim windows,
   authoritative Bead dispatch/result states, object budgets and staged acceptance.

Freeze the actual amended ADR as an immutable reviewable revision and obtain
its genuine design-review/human approval through native admission. Only then create
implementation tasks with the exact amended revision, refs and blocking approvals.
No future revision/ID, old pxtig-style unrelated approval, closed status alone or
approval of this proposal substitutes for that real amendment. Stage0 pure offline
work also requires a genuinely admitted task whose scope covers its dependencies;
this file grants none. Deployment remains a separate authenticated decision.

Decision-write recovery (exception6 replacement): after three bounded write/readback
attempts, suspend new decisions for that approval and post one fixed Marmot recovery
question binding approval, original authenticated event, expected/current digest,
field-level expected before/after and retained evidence digest. Choices are HOLD
(recommended) or explicitly authorize the displayed exact repair. Fresh current
operator authentication and unchanged readback are mandatory. The sole serial writer
performs only the authorized conditional repair, completes/readbacks pending native
pin effects, and retains original and repair provenance. Unknown writes re-read;
never replay blindly, overwrite a foreign decision or fabricate its approver/time.
A conflicting/foreign record needs a new question displaying that conflict and
explicit correction scope, not the original answer. No safe question delivery means
HOLD for separately authorized service maintenance. This proposed native repair port
must prove conditional before-state checks in fixtures before enablement; unsupported
backend behavior stops implementation admission for that slice.



## 11. Unchanged approved supervisor design publication

The separate supervisor input remains published at [the immutable d2 archive](https://github.com/Epiphytic/heterodyne-metaharness/blob/150a81ceb23dbdacf33fbe58f5fe5a8b8da8d373/docs/reviews/approved-inputs/2026-10-14-btq-supervise-cards.md). Its original queue Git commit is `7b2d757364d3f0e21a0c6ec87a5b5af7b244fb81`, source path `docs/designs/2026-10-14-btq-supervise-cards.md`, SHA-256 `46b0102cb4919aa97d75a7f5a0337d1ef537e8e54d72e5210b4bc6d1bd56670d`. Source approval is btq-s9ss7; implementation task btq-j90wi. This is not a Plan5 dependency or a new approval.

Its intentional email examples conflict with the current install-agnostic checker. Historical d3 removed only the duplicate archive from this candidate tree, preserving the original Git object, immutable published d2 link and exact bytes. It does not weaken the checker, rewrite history or encode the evidence. A future supervisor deployment uses that direct published design link and its genuine separate approvals. The controller must retain and verify the published design ref containing 150a81 before any ask; if unavailable, hold publication. Final landing uses a reviewed documentation snapshot onto the approved product base, not the historical planning ancestry containing the duplicate email example. This does not rewrite those historical objects.

## 12. D4 interfaces, activation and evidence

The proposed sufficiency port accepts exact approval/request identity, whole-ask
digest/revision, bounded purpose projection, policy fingerprint and source pins.
Result is sufficient|enrich|groom|unknown, plus bounded quoted steering, input digest
and requested/reported model, schema and release. Enrich adds bounded source context
and regenerates whole ask/digest before judgement; it does not approve or deny.
Groom uses the same-session protocol in §5. Sufficient records are durable and bound
to exact current digest/revision/policy/source/model at posting, recovery, decision
and effect. Unknown/stale never posts approval. The separate permission port accepts
canonical intent/origin/policy and returns allow|deny|escalate; only deterministic
policy can establish hard deny, and unavailable/invalid/late escalates. Neither
port creates operator consent or performs intake routing. Both narrow Plan7 subsets
need genuine sequencing approval under §10; no full Plan7 completion is claimed.

Use ordinary approved secret checks and bounded public labels/short digest prefixes;
no typed 64-hex/public-key bypass or public full payload dump. Full bindings remain
in native records and pinned review artifacts. Unsafe rendering holds or grooms.

All new native/IPC/broker ports are proposed implementation contracts, not claimed
installed APIs. Prove staged filing/cleanup, exact current design finalization,
all-kind writer submission, claim/result/repair CAS, same-session grooming, group
cleanup and read-only cross-UID digest equality in disposable fixtures. No effects
without proof; no broad table mutation, arbitrary terminal repairs or invented
approved fields. Full bundles remain a consciously accepted bounded performance
cost (64MiB/100000objects/30s, 1GiB store); optimization is deferred, not an assertion
of incremental support. All retained unknown effects/resource locks survive restart.

Review input obligations: retain original R1/R2/R3 reports and exact finding-object
hashes. Public disposition hashes use SHA256 of JSON sort_keys=True, compact comma/
colon separators, ensure_ascii=False, UTF-8, no trailing newline. Private verbatim
ledgers preserve every ordered object; their actual paths/hashes are in the input
manifest. Reviewers need those real files supplied; public hashes alone prove no
clearance. D4 is UNREVIEWED, all three rounds used; no additional invocation is
permitted without the exact authenticated review-budget exception.



Terminology: native means the supported queue CLI/conditional adapter, never direct table writes. Session launch provenance and the separate effector invocation record are distinct. The planned purpose-aware and conditional-state native extensions belong to the coordinated implementation dependency; unresolved support blocks enablement, not the ability to implement and test that dependency offline. A restarted read-only reconciliation gets a new bound invocation nonce and cannot reuse initial permission.
