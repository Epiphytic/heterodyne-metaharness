# Plan 5: typed policy and a separate action executor

Status: design proposal for btq-e7knb; future work only. No implementation,
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
  authority and design-approval flow remain independent of this future path.

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
No fresh review budget for unchanged historical content is asserted here. Root
must retain actual review artifacts and count any applicable prior rounds when
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
| D6 draft digest at create | Compute a candidate, persist whole ask, read back and recompute using the existing shared digest contract; only that read-back digest may be posted. |
| D9 writable Beads/journal forgery | Sandbox has access to neither; host daemon and database administrators remain trusted. Executor needs proven independent read-only access. No claim of protection from a compromised daemon/DB administrator. |
| D16 action label routing versus purpose | A typed readout purpose is authoritative. Labels may index, never select decision writers or substitute for an action payload. |
| Resume text only through status | Durable decision/result notice is delivered on actual resume, with status as recovery view; consumer integration must prove this seam. |
| Drain shorter than action timeout | Shutdown has explicit stop admission and bounded wait; interrupted in-flight work is uncertain and reconciles, regardless of configured durations. |
| Root-owned executor copies | Immutable reviewed release plus root-owned configuration and launcher required; source checkouts are not executable authority. |
| Read-only Beads user unproven | Blocking integration spike and deployment prerequisite (§8), not an assumed ACL recipe. |
| Relay retire versus submit (§17 #9) | Propose submission for typed action approvals only (§6). This is an explicit operator decision in this design approval, not a choice the implementation may infer. |

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
authenticated confirmation. Typos/reference resolution alone are minor. The
classification proposes a result; it neither authorizes an action nor approves
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

Canonical action data consists of schema version, typed effect and resolved
immutable target facts, request-origin identity, idempotency_key equal to the
approval ID, and approval revision. Here approval revision is an immutable
request revision identifier assigned before hashing, distinct from the
`decided_digest` of the full ask. The authenticated approver is added to the
execution envelope after decision, and must match the committed decision fields;
it is not an agent-selected identity or a field hashed into its own digest.
The operator sees the effect and immutable target facts verbatim. The shared
ADR/btq context_digest remains unchanged: title, description, whole ask and
resolved references; workflow and decision metadata stay excluded.

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

Filing on the owning workstream lane validates current origin and target facts,
creates an unroutable approval bead, reads back its whole immutable ask and
resolved digest, adds the blocking edge and parks the originating task. Bounded
file/post failures are retriable by recorded state, not repeated bead creation.
No card before deterministic lint; implement ADR §5.9 sufficient/enrich/groom
at the gatekeeper release boundary. A release without the gatekeeper may use
only the ADR's existing interim relay exception, explicitly labelled in its
release acceptance; this is not permanent exemption or full Plan7 completion.
A release shipping both must enforce judgement on every relayed ask too.

Resume requires the actual dependency/decision state and retained ownership.
Denial resumes with explicit denial text; success resumes with approved exact
action and verified result; failure/uncertainty stays parked with a recovery
condition. Status exposes the same durable notice. Neither an answer timestamp
nor a comment standing alone is a decision or a completion receipt.

## 6. Single decision writer and Marmot handoff

Proposed §17 #9 choice: admind authenticates and submits **typed action** events
to wsd's decision queue, including targeted approval ID, shown digest, stable
surface event ID and authenticated operator identity. Existing design approvals
continue through their current native `approve-bead` gate. Unknown purposes
refuse; no label-based guess at which path owns an ask. The interface must be
jointly specified with the current admind/consumer contracts before coding it.
A transport reply is distinct from a durable decision read-back; ambiguous
submissions reconcile by event identity, never fall back to another writer.

One serial decision queue executes ADR §5.4: durable inbox dedup on surface and
event, fresh policy/authority check, bead read, posted/current digest equality,
conditional first-decision write and exact read-back, then inbox committed.
Conflicting later decisions are superseded. Crash after bead write is repaired
from that bead's matching event/digest; journal loss does not invent a decision.
Failed writes remain pending and escalate after the ADR retry limit. Independent
host scripts must not write action decisions behind this queue.

Approval starts execution; it does not close the action bead. Denial closes with
no execution. A heart reaction files a policy-rule proposal only: the existing
rule set is unchanged until its separately approved application. Fresh authority
and current policy are rechecked at the privileged effect boundary; removals,
hard-deny changes, digest changes or target changes prevent execution. A newer
policy that remains equally restrictive may proceed only if the implementation
can prove the same approved effect; otherwise reissue for approval.

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

Executor independently reads the approval through verified read-only access,
requires committed approve fields and exact envelope/digest binding, validates
current policy and targets, and verifies decision provenance from the trusted
queue commit record. No CLI parameter can override any of those facts. The
journal is operational evidence, not a substitute for the bead decision. The
service user and database administration are in the trusted host boundary;
protection against a malicious host with authority to forge both stores is not
claimed. Sandbox-only code must be unable to reach either store or sudo.

Fetch allowed objects into an effector-owned bare object store, without running
Git in an agent-writable checkout or reading its hooks/config. Configured source
paths/remotes are fixed; use explicit protocol, refspec and environment rules,
reject symlink/ownership substitution, and disable credential forwarding to
local untrusted transports. Revalidate immutable commit/body/head facts in this
store. Pin artifact bytes and trusted definitions independently of agent paths.
Git/forge adapters and queue readers must expose bounded typed results; a
truncated or invalid response is unknown, not proof of absence.

A per-approval executor lock serializes all run/reconcile/settle operations;
wsd also serializes the execution lane. Persist executing intent before effect.
Before an initial action, query whether that exact idempotent effect already
exists. After timeout, crash, lost output or ambiguous target reply, persist
uncertain and run reconciliation only; never replay the effect speculatively.

| Action | Positive evidence | Uncertain/conflicting evidence |
|---|---|---|
| push | configured destination is exactly expected_sha | different destination or unreadable remote holds; no force correction |
| open_pr | unique marker bound to approval/revision plus matching repo/head/base/body facts | multiple candidates, missing visibility or mismatched head holds; no second create |
| merge | exact PR and approved head demonstrably merged, verified forge result | unrelated merge/head drift or unavailable result holds; no second merge |
| deploy | target adapter proves approved artifact active and health contract satisfied | partial rollout/unknown version needs-human; no automatic rollback outside approved target contract |
| notify | receipt for the exact approved message/correlation key | unknown delivery stays needs-human; never duplicate-send on missing receipt |

Success stores evidence and closes approval; definitive failure stores evidence,
keeps it open/needs-human; uncertainty stays open and unresolved. Reconciliation
may be retried as read-only bounded work with backoff; executing again requires
a new approval. Shutdown timeout never implies failure or releases the blocker.
Manual settlement requires authenticated operator authority, exact action and
recorded target evidence; a note cannot turn an unobserved effect into verified
success. Capture an explicit operator-attested disposition if machine proof is
impossible, preserving that distinction in the resume/result record.

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
5. Resolve and approve §17 #9 interface with admind, including full displayed
   payload, sender authority, receipts, restart and ambiguous-answer behavior.
6. Isolated executor with push/PR/merge adapters and fake target reconciliation;
   gatekeeper availability/release enforcement explicitly tested.
7. Independently specify versioned deploy targets and receipt-capable notify
   adapters before enabling those registry entries. No placeholder success.
8. Review code and exact source pin; propose operator-approved deployment with
   direct design link, deviations, ownership/sudoers/database proof, fresh
   stopped backup/migration rehearsal, rollback and live scratch acceptance.

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
- Five target adapters' success/failure/uncertainty matrices; descendants cleaned
  on timeout, conflicting remote state never overwritten, marker collision never
  duplicates PR, missing notify receipt never sends again, partial deployment
  never claims health. Disabled adapter always refuses.
- Journal migrations preserve history, identity, decisions and pending holds;
  partial schema and failed migration rollback; stale close/settle cannot race
  active execution. Mutation probes must kill removed digest/head/provenance
  checks, speculative retry, broadened sudo argv and weakened tier floor.

Live acceptance requires its own deployment approval and a scratch repository,
explicit test channel and disposable deployment target. Demonstrate actual
separate-user permissions, forbidden sudo forms, no credentials in sandbox,
real approve/deny, restart during uncertain action and target reconciliation.
Record exact reviewed source, config/target revisions, artifact hashes,
observed receipts, before/after state and rollback limits without credential
contents. Neither this document nor a passing offline test authorizes that run.

## 10. Explicit choices for the operator

Approval of the reviewed design must cover: action-only admind submission to
wsd (§17 #9), request revision/envelope interpretation (§4), initially disabled
deploy/notify entries until their target contracts are approved, and staged
acceptance without claiming whole-roadmap completion. An ADR conflict stops
implementation for a separately reviewed amendment. Unanswered choices remain
blocking; no timeout, supervisor status override or escalation path fabricates
an approval. Recovery remains available through the independent admind/operator
channel and supported task-management APIs, preserving records and real gates.

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
