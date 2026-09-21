# Supervisor decomposition and upfront worker permissions

Status: proposed design for btq-50u; implementation requires manager and Liam review.
Task revision: 903f38ecadd2dc653b192457660f55b45a2449fa83ca71866b8d00fc6d1d8fa4.
Design baseline: 492464a. No runtime or policy changes are made by this document.

## Decision

Add a supervisor-owned preparation phase before handing any executable task to a worker. Plan the decomposition before claim where current task data permits; if ownership must first be acquired for exact scope/worktree resolution, keep that claim supervisor-held in preparation with no executable worker handoff. It
produces a revision-bound graph of executable subtasks and a least-privilege
permission manifest for each executor. Claim is an atomic ownership operation,
not a place to run a model or silently expand permissions. A claim can exist in
`preparing`; execution cannot begin until the graph, authority, provider support,
and actual sandbox preflight all pass.

Use versioned task-type templates plus bead-specific resolved resources, with an
optional bounded negotiation turn when requirements are unknown. Do not use a
blanket permissive sandbox, a prompt promising permission, or retrospective
approval as a substitute for launch preparation. Bias implementation, tests,
documentation and evidence preparation toward workers. Assign supervisory
coordination and privileged lifecycle operations explicitly to the supervisor or
operator-authorized executor. A supervisor is not automatically a merge or
service authority.

The occurrence detector, classifier, durable recording, feedback-bead admission,
and status emission are deterministic scripts. Planning may use the existing
manager/coding conversation; it is not part of emission or detection. No new
workstream or replacement conversation is required.

## Existing contracts and integration seams

The active [spec entrypoint](../spec/README.md) remains authoritative. This proposal
extends, rather than activates or replaces, these contracts:

- [Tasks](../spec/tasks.md), [queue order](../spec/queue-order.md), and
  [worktrees](../spec/worktrees.md): verified ownership, current scope receipts,
  dependency checks, idle native boundaries and exact-session recovery.
- [Delivery](../spec/delivery-tasks.md), [formulas](../spec/formulas.md), and
  [handoffs](../spec/handoffs.md): separately owned outcomes and pinned evidence.
- [Approvals](../spec/approvals.md) and [native relay](../spec/permission-relay.md):
  authority and native enforcement are distinct; observations cannot approve.
- [Change lifecycle](../spec/changes.md): signed commits, real test evidence,
  private reviews, operator-owned merge/deploy and retained artifacts.
- [Transitions](../spec/transitions.md) and [status](../spec/status.md): existing
  deterministic delivery and status format remain unchanged.

Current seams inspected: `task_workspace.assign/prepare/switch`,
`task_workspace.boundary`, `Supervisor.launch`, `agents.Provider.argv` and exact
provider resume methods, `Beads.claim`, `task_review.claim_concurrent`,
`tasks.admit`, `permission_relay`, `task_order.order_key/prioritize`, and
`task_interrupt.drop`. The current switch persists intent, stops the exact idle
worker and resumes its native identity; it does not attest effective permissions.
Current providers assemble argv but expose no capability-compilation contract.

Independent review is also required wherever enabled. The canonical checkout's
`spec/reviews.md` was read at design time; it is newer than this isolated baseline
and is not copied into a competing specification here. It requires independently
collected artifact-bound evidence, deterministic scheduling, and a reviewer
separate from the coder. Implementation must reconcile against that current
contract and `reviews.py` before landing. A preparation receipt is not a review,
and a permission incident cannot resolve a review finding.

## Decomposition and ownership

Before the first execution turn, the supervisor inventories the requested outcome,
acceptance checks, inputs, side effects, environment, credential use, dependencies,
review handoff, and completion evidence. Every executable subtask names its
executor and why any work is retained by the supervisor. Include bookkeeping,
signing, pushing, review creation, test infrastructure and turn-boundary handoff;
these are commonly omitted from apparently simple coding tasks.

Use existing delivery/formula children for independently claimable outcomes. Do
not create a second implementation child for an existing delivery role. Smaller
steps within one owned outcome are ordered plan entries on that Bead, not
independent competing claims. Native Beads edges govern cross-Bead dependencies;
plan edges govern local steps. Validate cycles and ownership in both. Parent-child
grouping alone never proves an outcome complete.

Example decomposition for btq-dik:

| Step | Executor | Requirements and evidence |
| --- | --- | --- |
| Design/spec changes | worker | Owned checkout write, scoped code/spec reads; reviewed design artifact |
| Implementation and full suite | worker | Checkout/temp writes, isolated local sockets/tmux, controlled test environment; exact command and log hash |
| Sign, push, open WIP review | worker | Scoped Git/signing/push/review capability for the authorized private repository and task branch; signed SHA and actual draft identity |
| Record lifecycle | worker through task service | Owned-bead stage operation with retained evidence; no direct shared DB write |
| Switch to priority work | supervisor | Verified native idle event, current queue authority and task boundary; persisted transition |
| Review/merge/deploy | separately authorized owner | Existing delivery and operator gates; never inferred from worker completion |

For this design-only btq-50u turn, the executable scope is research, design file,
document validation, signed design artifact and review handoff. Implementation is
a blocked future phase. Do not silently change its legacy lifecycle into a
research formula or close the Bead at design submission.

## Manifest and storage contract

Proposed schema (illustrative symbolic values are resolved before activation):

```json
{
  "schema": "harness.execution-plan.v1",
  "issue_id": "btq-example",
  "scope_revision": "sha256-of-current-native-scope",
  "plan_revision": 1,
  "template": {"id": "implementation", "version": 1, "digest": "sha256"},
  "authority_refs": ["retained-authority-artifact"],
  "steps": [{
    "id": "verify",
    "executor": "worker",
    "depends_on": ["implement"],
    "outcome": "Full suite passes at the submitted commit",
    "operation": "run-suite",
    "argv": ["python3", "-m", "pytest", "-q"],
    "cwd": "owned-checkout",
    "inputs": ["signed-commit"],
    "effects": ["temporary-test-state"],
    "capabilities": ["checkout.read", "temp.write", "test.local-sockets"],
    "evidence": ["command", "commit", "log_sha256", "result"]
  }],
  "permissions": {
    "filesystem": [{"resource": "owned-checkout", "access": "write"}],
    "network": {"egress": [], "local_bind": "isolated-test-namespace"},
    "services": [{"name": "task-state", "operations": ["stage", "addendum"]}],
    "credentials": [],
    "native_approval_policy": "never"
  }
}
```

The full manifest additionally binds run, worker identity, repository/common-Git
identity, branch, owned checkout, provider/version, lifecycle/delivery role,
explicit denied operations, resource limits, and a sorted capability-to-step map.
Executor values are worker, supervisor, or an existing authorized delivery owner;
no free-form executable supplied by task text becomes a trusted service operation.
Supervisor-owned steps require an executor acceptance receipt too.

Beads owns the plan reference, digest, phase and dependency state. Store immutable
plan bytes and receipts under the supervisor-owned run/task artifacts directory;
checkpoint contains only projections. Workers receive read-only copies. Permission
receipts bind scope and plan digests, actual resolved paths, compiled provider
configuration digest, launch generation/native identity, preflight results and
retained evidence. Credentials are references, never secret values. Resolve paths
and symlinks before granting access; recheck on resume and reject path drift.

Templates supply requirements, not authority. Task-specific manifests may narrow
or request reviewed extensions; neither can override operator/provider constraints.
A scope addendum, artifact-changing review request, executor change or provider
upgrade invalidates affected receipts and returns execution to preparation. Stage
metadata alone does not invalidate the scope baseline.

## Permission enforcement and preflight

Effective access is the intersection of operator authorization, host/provider
policy, workstream role/delivery role, and task manifest. Required capabilities
must be a subset of that intersection, or dispatch is blocked. `approval_policy=never`
means requests cannot gain interactive approval; it does not grant access and is
not superseded by the manifest. Unsupported capabilities are errors, not silently
ignored fields or an excuse to choose a permissive fallback. No approval keys,
agent-side sandbox edits or arbitrary privileged command broker are introduced.

Avoid direct worker write access to harness SQLite, Beads' shared state, broad Git
common directories, native credential stores or service configuration. Implement a
narrow supervisor-owned task service using authenticated local IPC/provider
transport. Bind its short-lived capability to run, worker, issue, scope, plan,
launch generation, operation set and request key. Keep credentials outside the
checkout and logs. The server performs existing owner, route, approval, recovery,
revision and evidence checks on every call. Revocation or scope changes invalidate
the capability. Enforce sizes, path allowlists, timeouts and request rate limits.
Tokens authenticate a caller; a worker-provided `issuer` string does not.

The service exposes typed task/evidence operations, not shell execution or raw SQL.
The worker should submit its own stage/addendum via the existing facade, backed by
this service, without a human relay. The server owns WAL, database directory and
queue-state writes. A disconnected worker retains requests in a narrowly writable
outbox outside the Git checkout; the supervisor drains them idempotently. Lost
acknowledgments reconcile using durable request keys rather than retrying effects.

For Git, prefer an isolated clone with writable task-local metadata or a scoped
Git service for signed commits and named-ref pushes. Shared worktree metadata is
not just the `.git` pointer file: common objects, locks and refs may live outside
the checkout. Granting the whole canonical `.git` exposes unrelated refs. Choose
and test the isolation strategy before rollout. Existing worktrees are retained;
no forced conversion. Signing uses a narrowly scoped signer, not key-file access.
Push/review grants pin private repository, branch/ref namespace and action; no
force push, remote substitution, public publish, merge or deploy.

A preflight runs with the same effective sandbox and environment as the executable
worker, not just in the manager shell. It checks declared reads/writes in disposable
scoped resources, task-service authentication and dry-run validation, Git metadata
strategy, signer availability, private remote/ref resolution, local socket bind and
isolated tmux startup where needed, dependency executables and evidence storage.
Do not push, merge or deploy as a probe. Test subprocesses receive the same limits;
required loopback/Unix sockets are separate from Internet egress. Pin or sanitize
Python/test environment to avoid importing another checkout through PYTHONPATH.
Record the interpreter, cwd, dependency lock/version and module origins, not secret
environment values. Registry/forge requirements must name allowed endpoints and
authorized credential references; a manager-side network probe is insufficient.
The executable handoff includes the exact versioned stage schema (including
full_suite, retained-log hashing and review URI schemes) and a deterministic dry-run
validator, so schema discovery does not require mutating Beads or asking approvals.

Native session readiness alone is insufficient: require a launch receipt and
preflight acknowledgment before delivering executable task text. Providers unable
to verify this stay negotiation-only. Preflight limits and sandbox controls are
enforcement; argv allowlists alone are not a safe boundary for arbitrary tests,
Git hooks, shell interpreters or repository code.

## Preparation and exact resume

Proposed states: `preparing -> negotiating (optional) -> validated -> launching
-> preflight -> executable`; failures go to `blocked` or `reconcile-required`.
These are preparation states, not new completion stages. Existing Beads status and
approval/recovery states remain authoritative.

1. At a valid idle boundary, verify claim/route/scope and prepare the owned worktree.
   Persist plan intent before admitting children or changing launch configuration.
2. Validate the graph and authority. If unknowns remain, allow only a bounded
   read-only negotiation turn in the same coding conversation; it cannot implement.
3. Retain the proposed permission delta and actual operator authorization where
   needed. At confirmed idle, stop that exact worker process, persist transition
   intent and resume the exact native session with the validated configuration.
4. Attest the effective launch and run preflight before executable dispatch. Persist
   receipt, then dispatch once using the existing durable inbox identity.
5. On interruption, recover from recorded phase and native identity. Never infer
   completion from a pane disappearance or start a second worker because an ACK was
   lost. An uncertain process remains blocked until explicit reconciliation.

Permission-only restart still uses the existing clean/idle boundary. If unexpected
permissions leave dirty work that cannot be committed, retain it and block; this
proposal does not bypass the boundary by stash, reset, forced stop or claiming a
new task. Recovery requires the existing operator procedure. Pending native approval
must be explicitly resolved before restart. Preserve workdir, plan digest, artifact
pins, outstanding request IDs and last acknowledged dispatch in the checkpoint.
An operator-ordered priority change must select an explicit existing transition:
concurrent review handoff after real tested/pr-open evidence, or the authorized
park/drop procedure in queue-order.md when its prerequisites are met. Provide the
procedure and executor in the plan; ordinary FIFO never overrides the operator.
Repair metadata decoding before trusting park/drop readback. Do not manufacture
review stages merely to force a switch. A worker's in-turn request to switch queues
only enqueues intent; the supervisor
consumes it after the verified turn-end event, without repeated manager nudges.

## Occurrence capture, classification and feedback

Every mid-execution permission/approval request is a planning/spawn defect to
investigate, not authorization to continue. Capture denied syscalls/tool results as
well as visible prompts: policy `never` often produces no approval panel at all.
Distinguish those from planned business approval gates such as merge approval;
those remain ordinary gates unless accidentally assigned to the worker.

Prefer structured provider events and typed facade errors. Reuse native relay for
recognized panels, retaining its unauthenticated-observation caveat. Unknown or
clipped UI evidence records uncertainty; it cannot become a verified request.
Detection coverage is explicit per provider: pane heuristics cannot guarantee
observation of every syscall. Provider adapters report coverage gaps and the
worker can submit a structured incident report. Do not claim universal detection
until native telemetry and integration fixtures demonstrate it.

Classify deterministically by the approved plan:

- **a — subtask creation:** missing/ambiguous required effect, capability or executor;
  an operation falls outside its declared scope. Stop and refine the plan.
- **b — worker spawn:** the authorized subtask requires the capability but effective
  launch/transport/preflight did not provide it. Stop and repair preparation.

Persist both cause flags when appropriate and a primary kind. Missing evidence
gets provisional `a` with `classification_pending=true`, not silent suppression;
new evidence appends a correction without rewriting history. Unknown authority
never becomes a requested blanket grant. An unrelated functional failure is a
separate defect, not forcibly labeled a permission incident.

An occurrence contains stable provider event/attempt ID, run/task/scope and plan
revisions, executor/native identity, phase, requested operation/resource class,
expected/effective capability hashes, a/b classification and confidence basis,
retained redacted evidence, first/last observed time and feedback admission state.
Repeated sampling of one unresolved native request is one occurrence; a distinct
attempt is another occurrence, linked to the same defect when appropriate.

Persist occurrence and feedback intent in one supervisor transaction. Use existing
`tasks.admit` with a deterministic key derived from occurrence identity to create
one harness-improvement Bead per occurrence, routed to the operator-configured
improvement workstream. Its acceptance text names the missing contract, regression
fixture, and expected preflight failure. Never claim it automatically or bypass
queue/approval rules. Lost create ACKs reconcile exact fingerprints. If admission
or DB access fails, retain intent in the supervisor journal or worker outbox and
surface an existing-format blocked status; do not drop the incident or spawn a
recursive stream of incidents about the same failed admission.

Feedback closure requires a regression fixture and a versioned template/provider
fix. It does not globally broaden future sandboxes. New template versions affect
future preparation only; active manifests require explicit revalidation. Track
occurrences per launch, a/b totals, repeats and admission backlog; raw transcripts
and credentials never go into public feedback Beads or status messages.

## Occurrence log: motivating evidence

These are design case studies, not claims that automated occurrence Beads have
already been created. Paths identify retained local evidence, not public URLs.
The five named cases below are supplied by the manager from Liam's brief; their
classification is preserved. Reported timing and network behavior are not
independently measured in this checkout. The additional rows distinguish directly
observed failures from those reports.

| Case | Evidence and provenance | Kind and lesson |
| --- | --- | --- |
| OCTO-1 | Manager reports worker could not write harness.sqlite3 for completion-review handoff, requiring three manager round-trips | b: provision the scoped handoff/task-state transport and its authentication before dispatch |
| OCTO-2 | Manager reports crates.io DNS/network and `gh api` failures in worker context while manager networking worked | b: resolve and preflight required registry/forge egress and credential references in the actual worker context; never infer access from manager success |
| OCTO-3 | Manager reports repeated BeadsError discovery of `full_suite:true`, `log_sha256`, and accepted `pr_url` schemes | a: include versioned evidence schemas, examples and side-effect-free validation in the subtask handoff; no trial-and-error lifecycle writes |
| HARNESS-IMP-1 | Manager reports approximately 30 minutes lost to PYTHONPATH shadowing by `~/.hermes/hermes-agent/tests` | b: environment is part of spawn correctness; pin imports/test cwd and preflight module origins. This is distinct from the directly observed socket denials below |
| HARNESS-IMP-2 | Manager reports tested+pr-open binding gate blocked priority handoff without a documented operator-reordering path, requiring manager stage recording | a/b: specify who handles transition and its trigger, and provision evidence operations; do not fabricate tests or remove lifecycle guards. Current source also has an explicit parked-interruption path whose metadata readback needs repair |
| btq-dik Git staging | Observed `index.lock` creation denied in `/home/operator/repos/harness-improvements/.git/worktrees/checkout1`; later signed commit 19e5970 succeeded | b: owned checkout write does not imply shared Git metadata write; preflight actual Git storage strategy |
| btq-dik task writes | Observed EROFS on `/home/operator/.hermes/workstreams/harness.sqlite3` for addendum/stage commands; successful and rejected later attempts both retained in task notes | b: worker bookkeeping transport was not consistently available; launch-generation receipts and a scoped service replace assumed DB access |
| btq-dik pickup pause | EROFS writing the BTQ `paused` state file | b: pause/bookkeeping belongs in the declared service operations; failure cannot be treated as a persisted pause |
| btq-dik full suite | `/tmp/btq-dik-resume-pytest.log`: failures and 120s timeout; fresh Marmot test denied `server.bind` with EPERM; tmux launch failed | b: local test sockets/process environment were absent; distinguish test failures from product regressions and preflight them |
| btq-dik manager verification | `/tmp/btq-dik-test-log.txt`, SHA256 `2f852f70423a18cb7fcd4e8196d33b8261d0a0377d0a0a9f9204d0b10ebf406d`: 417 passed, 1 skipped, 39 subtests at 19e5970; manager recorded tested/pr-open | Recovery evidence, not proof the worker sandbox was fixed; preserve executor provenance |
| btq-dik priority boundary | Repeated claim rejections required confirmed idle turn; ultimately coherent WIP pushed, private Radicle draft opened and stages recorded before manager bound btq-50u | a: transition ownership/trigger was missing from executable decomposition; legitimate boundary guards stay intact |
| btq-50u design commit | This design-only turn: `git add` failed creating `/home/operator/repos/harness-improvements/.git/worktrees/checkout2/index.lock` with EROFS; no commit created | b: the new worktree still lacks declared Git bookkeeping capability; retain the design file for manager review without claiming a signed artifact |
| Metadata representation | Manager reports bd returns JSON strings; btq-50u snapshot actually has string-valued `harness_queue_order`; `order_key` requires dict, `prioritize` and `drop` compare raw value to dict | Functional serialization defect; a planning-contract lesson, not a Git filesystem denial or a fabricated approval event. Normalize before validation/comparison and reconcile uncertain writes |

The last row must remain separate from actual Git/DB permission denials. At this
baseline, prioritize can write the correct intent then reject its readback solely
because of representation. Drop may persist a park, see a false mismatch and set
recovery holds. Do not clear those holds automatically or reissue the mutations.

## Metadata normalization contract

Introduce one typed decoder at the Beads adapter boundary for known harness
metadata keys. Accept an object or exactly one JSON encoding of the expected
object; preserve original raw bytes for evidence. Reject malformed, oversized,
duplicate-key, scalar/list, double-encoded or schema-invalid values. Do not parse
arbitrary user notes or unknown keys recursively. Normalize consistently on show,
list, snapshot, ordering and write verification; compare validated canonical
objects with stable digests. Check integer types (excluding booleans) and bounds.
Do not rewrite historical scope digests during rollout: retain the original scope
receipt and require explicit reconciliation when adopting normalized projections.

For prioritize/drop, retain intent before mutation, read back and decode the exact
operation identity, then reconcile success if semantically equal. Missing or
conflicting native values remain uncertain and require existing recovery handling.
Test the actual installed bd serialization contract in addition to dict-only mocks.
Inventory other known harness metadata consumers before enabling this change;
normalizing only `order_key` would leave readback and epoch calculation broken.

## Alternatives and limits

A blanket writable home or canonical Git directory would reduce friction but
expose other tasks and credentials; reject it. Letting workers edit the DB directly
would couple sandbox grants to SQLite side files and shared authority; reject it.
Doing all writes in ad hoc manager turns reproduces the PR-9 relay problem; typed
service calls provide planned supervisory execution without conversational relays.
Pure per-bead negotiation is flexible but repetitive; templates plus explicit
resource resolution retain that flexibility with reviewable defaults.

No grant can promise success for arbitrary future commands. The contract is that
all declared work has proven capabilities before dispatch; new requirements cause
an incident and re-preparation. Network outages, absent private seeds, stale remote
refs and provider version changes remain infrastructure failures, never fake PRs
or passing stages. The existing native approval policy remains in force throughout.

## Proposed implementation sequence after approval

1. Specify preparation/manifest and occurrence schemas, typed metadata decoding,
   and required extensions to tasks/worktrees/approvals/permission-relay specs.
   Prove JSON-string round-trip fixtures and preserve historical evidence.
2. Add immutable plans, dependency validation and supervisor preparation gating
   around `task_workspace.assign`; keep model planning outside claim transactions.
3. Implement narrow task-state transport, authenticated capabilities and durable
   idempotent outboxes using current admission/stage validators.
4. Add provider capability compilation, effective-policy receipts and sandbox
   preflight to launch/resume. Initially enable only verified provider profiles.
5. Wire deterministic occurrence detection and feedback admission into structured
   tool/native events and existing relay/outbox paths; retain status format.
6. Exercise exact-session negotiation/restart, delivery/review gates and a canary
   task. Manager controls deployment and explicitly enables the feature per run.

These are proposed scopes for future Beads, not an alternate task queue. Manager
approval should pin the design artifact digest, selected Git isolation strategy,
transport authentication, provider support matrix and rollout boundary. No runtime
implementation or policy activation is authorized by accepting a task snapshot.

## Acceptance and rollback

Required fixtures: incomplete graph/authority blocks dispatch; no executable prompt
before receipt; missing local socket/Git storage/service rights fail preflight;
policy `never` cannot be relaxed; undeclared paths/refs/credentials and revoked
capabilities fail closed; forged/replayed service requests cannot mutate other
Beads; scope/provider changes invalidate receipts; JSON-string and object metadata
are equivalent while malformed/double-encoded data fail; uncertain prioritize/drop
writes reconcile without duplicates; every distinct incident admits exactly one
feedback Bead across lost ACKs; failed admission survives restart without recursion.

Integration checks must cover real sandboxed socket/tmux tests, signed task-local
Git commits, private review creation, stage writes without raw DB access, and exact
native identity across negotiation/resume. Fault-inject before and after every
transition persistence/launch/ACK boundary. Confirm no second process, lost dirty
work, replayed task prompt or synthetic completion. Confirm review/deploy still
require actual operator authority and enabled independent review; completion and
status delivery formats remain unchanged.

Roll out disabled by default with explicit per-run adoption and retained old
manifests. Rollback stops new executable dispatch, revokes new capability tokens
and preserves pending requests/evidence for reconciliation. It never silently
launches the same work under broader legacy permissions or removes recovery holds.
