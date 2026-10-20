Skill "code-review" completed (forked execution).

Result:
**Verdict: REVISE.** The proposal hash matches (7d2df775…e4fd), and all 23 packet files match `freeze.json`. Its main ideas are sound: coverage first, enrollment last, original exclusive locks held, and no adoption of existing state. But several points stop it being implementable or safe as written: how the domain gets bootstrapped, where the completion interlock lives and when it is checked, crash windows that leave the domain permanently stuck, and the absence of any way out after activation. I read only the packet files and ran no tests. Findings 8 and 9 depend on code that isn't in the packet (`enrollment()`, `local_inventory`, `native_inventory`, `publish_exclusive`), so treat them as likely rather than confirmed.

```json
[
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 53,
    "summary": "1 (HIGH): Activation cannot be undone and has no successor. Generic maintenance refuses any enrollment with initial_controller_scope (source-Coordinator-maintenance.py:20-21), rollback is forbidden, and the proposal says it 'does not invent that later transition owner'. So once activated, the source closure, the controller run UUID and the eight lane actors are fixed with no supported change path.",
    "failure_scenario": "After activation, any change to an enrolled source (a controller bug fix, the next BTQ release, the K6 owner code) fails with 'enrolled source drift' on every operation. A lane task finishes and the controller claims a new task under actor run+btq-NEW: 'operation outside initial controller scope'. The controller restarts with a new run UUID: every route is out of scope. Strict-v1 readiness can't be restored because the two obligations need the K6 owner, which can't be deployed. Result: everything stays on HOLD indefinitely. The design should at least define the minimal transition (same-domain, EX, initial-scope-to-initial-scope source/actor update) or its exact approval path before activation is allowed."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 9,
    "summary": "2 (HIGH): The first link in the bootstrap chain has no owner. The new owner, the amended coordinator with the interlock, and the authority reader must be installed by 'a preceding separately approved finite deployment operation'. But the only existing publisher (install-agents.py) needs an EX maintenance lease, which needs an existing enrollment, and the single-use 98-target publisher may not be rerun.",
    "failure_scenario": "With no enrollment, Coordinator.maintenance() can't load enrollment, so install-agents.py --plan/--authority holds. Nothing source-owned can place the owner bytes that are supposed to create the enrollment. Line 57 ('authority reader bootstrap must be independently installed/admitted by the authentic administrative deployment path') points back to the same missing path. Either this owner also has to do the pre-enrollment source publication under its own exclusion, or a second named owner is needed. The proposal also doesn't say whether that is one approval or two, or what each one binds."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 11,
    "summary": "3 (HIGH): The authority API doesn't match the actual source reader. enroll_initial(plan_path, deployment_ask_id) takes a Marmot ask ID. The existing native authorize() (source-authorize.py) needs {schema, approval, revision, plan_sha256}, an approval Bead with metadata.via=marmot, approval_valid(), and a git-pinned file whose content equals the plan bytes. The parent design notes that admind approval cards need an existing approval Bead, and creating one before enrollment needs its own one-off bd-create packet.",
    "failure_scenario": "The implementer either writes a new Marmot-ask reader (new trusted authority code that is not 'reusing native authority readout' as line 57 claims), or must first create an approval Bead and commit the plan, including the final scope rows, to a git revision. Neither step is in the plan. Also, authorize() does metadata['ask']['refs'] without a guard, so a malformed record raises KeyError instead of a typed refusal, after the intent has already been written. The ask-to-approval mapping, the plan commit and the pre-enrollment Bead packet need to be defined."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 45,
    "summary": "4 (HIGH): The completion interlock's storage location and its ordering against step 7 are not defined. The completion record can't be referenced from enrollment (that would be a digest cycle), and initial_controller_scope is closed {schema,run,routes}. Step 7 then calls 'actual production enrollment validation' before writing completion.",
    "failure_scenario": "If the interlock goes in _enrollment()/enrollment(), the only place that covers every reader, then step 7's production validation fails because completion doesn't exist yet, so activation can never finish. If it goes only at the three admission seams named in the proposal, other enrollment consumers (administrative_runtime, guarded_gate/borrow_child, inventory) accept a published pair without completion. The proposal also never says the record's fixed path, mode or directory, so ordinary admission can't find it. Simpler option: _enrollment already takes snapshot=, so validate the in-memory pair with full production code before publishing, make the post-publish step a byte and identity readback only, and justify whether a separate completion record is needed at all. If it is kept, put it in one place: _enrollment, with an owner-only allow_incomplete flag."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 29,
    "summary": "5 (MEDIUM-HIGH): Creating directories, locks and files one by one at their final paths leaves crash windows between creation and recording identity. The proposal treats those as permanent HOLD, while also forbidding deletion, adoption and a fresh domain.",
    "failure_scenario": "Power is lost after O_CREAT|O_EXCL of turnstile.lock but before its dev/ino is persisted. Reconcile can't attribute the file (matching empty bytes are 'insufficient'). A new operation refuses because the domain isn't absent. Unlinking is forbidden. So the fixed maintenance-v1 root is stuck for good. Fix at the root: build the whole tree (dirs, three locks, flock EX on the retained FDs) inside a staging directory whose name includes the operation UUID, which the intent fixes before creation. Fsync it, record the identities, then renameat2(RENAME_NOREPLACE) it to maintenance-v1. Inode identities survive the rename, and anything left before the rename can be attributed by name."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 42,
    "summary": "6 (MEDIUM): 'Existing checked atomic exclusive publication' (Coordinator._write) creates a random '.new-'+uuid4 temp file and removes it only in a finally block. A crash leaves an unattributable file in the domain root, which line 51 classifies as an 'unexplained domain' and holds on.",
    "failure_scenario": "SIGKILL or power loss during the coverage.json or enrollment.json write leaves root/.new-<random>. The temp name is never in the intent, so reconcile_initial can't prove it came from this operation and holds permanently ('no generic cleanup'). If publish_exclusive is link-based, a crash can also leave the final file with nlink=2, which _open rejects as 'unsafe private inode'. Temp names should derive from the operation UUID and be listed in the intent, and reconcile needs an explicit rule for them."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 51,
    "summary": "7 (MEDIUM): reconcile_initial does not require the original owner to be dead before acting. Before the lock files exist, flock can't keep a concurrent reconcile away from a live enroll_initial. The existing maintenance() does refuse while owner_alive(marker['owner']).",
    "failure_scenario": "enroll_initial has published its intent and is mid-mkdir or pre-lock when an operator or automation runs reconcile_initial for the same operation. Reconcile sees intent plus a partial tree, finds no locks to contend on, and runs its 'reconcile each original effect' logic alongside the live owner, so the two interleave creations or classify an in-progress object as ambiguous. Reconcile should require the intent's recorded boot/owner to be provably dead, and should hold if that is unknown, before inspecting state."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 51,
    "summary": "8 (MEDIUM): Completing a coverage-only state with fresh authority contradicts the immutable coverage. Line 51 says expiry 'requires new genuine finite authority' and that coverage-only may publish 'the original exact enrollment only after ... original authority permits completion'. But coverage.handover.authority_sha256 and the prepared receipt are already bound to the original authority.",
    "failure_scenario": "The process crashes after coverage publication, and the plan expiry passes before an operator can reconcile. The original authority no longer permits completion. New authority can't be reflected in the immutable coverage, which also can't be deleted or replaced. Under line 27 a fresh operation can't adopt it, so the domain is stuck. The proposal needs to say whether fresh authority may complete the original operation (and how it is recorded, as in the existing recovery-authority-<op>-<digest> receipt) or that expiry permanently strands it."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 43,
    "summary": "9 (MEDIUM, likely): Step 7 calls production enrollment validation and source inventory while holding EX on the original lock FDs. flock conflicts are per open file description, so any production helper that opens turnstile/gate itself and takes LOCK_SH blocks against the owner's own EX, even in the same process.",
    "failure_scenario": "Coordinator.enrollment(), or an inventory adapter or Queue path that goes through _lock(..., LOCK_SH) or operation() for consistency, waits up to the 5s deadline and then raises 'admission busy'. Every activation then stops at step 7 with the pair already published and completion missing, which leaves the domain in the interlocked pair-without-completion state. The design should require step-7 validators to be lock-free (snapshot/read-only) or to borrow the retained FDs, and should list exactly which production entry points are called."
  },
  {
    "file": "initial-controller-enrollment-owner-design/proposal.md",
    "line": 38,
    "summary": "10 (MEDIUM, likely): The proposal's v1/v2 selection doesn't match the frozen producer and validator. initial_controller_inventory always adds obligations['unapplied_targets'] when native is a dict, even with count 0, and so returns v2. validate_initial_controller_inventory adds that key only when retained targets exist, and caps facts['parts'] at one, while the proposal says 'each pinned Hermes part producer'.",
    "failure_scenario": "Case 1: zero unapplied targets, Hermes uncertain rows present. The producer gives obligations {unapplied_targets:{count:0}, hermes...}, the validator expects {hermes...} only, so 'obligation mismatch' HOLD. Case 2: zero obligations of either kind. The producer still returns v2, not the 'existing strict v1' the proposal relies on, and the validator rejects with 'not expected'. Case 3: a second pinned Hermes adapter (coverage allows up to 8). 'invalid complete initial observation'. The owner design should state the exact selection rule and fix or confirm the frozen candidate f67953cc… before review."
  }
]
```
