# Sole HIGH design review: ordered author dispositions

Actual verdict REVISE retained. Incorporated once; no second review/validation, implementation or approval claim. Actual complete finding objects are preserved in review-r1/findings-verbatim.json and dispositions.json.

## 1. 1 (HIGH): Activation cannot be undone and has no successor. Generic maintenance refuses any enrollment with initial_controller_scope (source-Coordinator-maintenance.py:20-21), rollback is forbidden, and the proposal says it 'does not invent that later transition owner'. So once activated, the source closure, the controller run UUID and the eight lane actors are fixed with no supported change path.

Incorporated: section7 defines finite same-original-domain administrative refresh/reconcile with genuine exact native deployment authority, original EX, preserved unknowns and source/run/actor updates. Generic maintenance remains refused.

## 2. 2 (HIGH): The first link in the bootstrap chain has no owner. The new owner, the amended coordinator with the interlock, and the authority reader must be installed by 'a preceding separately approved finite deployment operation'. But the only existing publisher (install-agents.py) needs an EX maintenance lease, which needs an existing enrollment, and the single-use 98-target publisher may not be rerun.

Incorporated: sections1/3 name new independently bootstrapped signed finite administrative publisher; one approval may explicitly cover publication plus enrollment, otherwise separate exact approvals. No lease-requiring installer or98reuse.

## 3. 3 (HIGH): The authority API doesn't match the actual source reader. enroll_initial(plan_path, deployment_ask_id) takes a Marmot ask ID. The existing native authorize() (source-authorize.py) needs {schema, approval, revision, plan_sha256}, an approval Bead with metadata.via=marmot, approval_valid(), and a git-pinned file whose content equals the plan bytes. The parent design notes that admind approval cards need an existing approval Bead, and creating one before enrollment needs its own one-off bd-create packet.

Incorporated: section2 uses actual native authority approval Bead/revision/plan_sha256 and immutable Git plan refs; existing finite administrative approval-record creation precedes ask/enrollment. Malformed authorize shape is separately confirmed source-hardening deliverable, not fixed.

## 4. 4 (HIGH): The completion interlock's storage location and its ordering against step 7 are not defined. The completion record can't be referenced from enrollment (that would be a digest cycle), and initial_controller_scope is closed {schema,run,routes}. Step 7 then calls 'actual production enrollment validation' before writing completion.

Incorporated: section5 fixes completion path/mode and central admission enforcement; pure snapshot pair validation returns data only, permission-bearing disk reads require completion and no transition marker. No allow_incomplete grant.

## 5. 5 (MEDIUM-HIGH): Creating directories, locks and files one by one at their final paths leaves crash windows between creation and recording identity. The proposal treats those as permanent HOLD, while also forbidding deletion, adoption and a fresh domain.

Incorporated: section4 stages attributed complete tree under UUID then renameat2NOREPLACE preserving original FDs; explicitly handles unknown rename/directory fsync and no arbitrary adoption.

## 6. 6 (MEDIUM): 'Existing checked atomic exclusive publication' (Coordinator._write) creates a random '.new-'+uuid4 temp file and removes it only in a finally block. A crash leaves an unattributable file in the domain root, which line 51 classifies as an 'unexplained domain' and holds on.

Incorporated: deterministic UUID/role temps named in intent, no random Coordinator._write path; exact partial/link/identity reconciliation and collision HOLD.

## 7. 7 (MEDIUM): reconcile_initial does not require the original owner to be dead before acting. Before the lock files exist, flock can't keep a concurrent reconcile away from a live enroll_initial. The existing maintenance() does refuse while owner_alive(marker['owner']).

Incorporated: section6 requires positively established original-owner cessation, never timeout, before recovery; same rule applies to recovery owner.

## 8. 8 (MEDIUM): Completing a coverage-only state with fresh authority contradicts the immutable coverage. Line 51 says expiry 'requires new genuine finite authority' and that coverage-only may publish 'the original exact enrollment only after ... original authority permits completion'. But coverage.handover.authority_sha256 and the prepared receipt are already bound to the original authority.

Incorporated: section6 append-only renewed recovery-authority addendum permits exact original completion without editing old coverage/receipt or backdating consent.

## 9. 9 (MEDIUM, likely): Step 7 calls production enrollment validation and source inventory while holding EX on the original lock FDs. flock conflicts are per open file description, so any production helper that opens turnstile/gate itself and takes LOCK_SH blocks against the owner's own EX, even in the same process.

Incorporated with qualification: section5 names lock-free snapshot validator and direct source observers, no ordinary SH/Queue admission under EX. Read-only author inspection confirms existing _enrollment snapshot body does not lock; actual owner integration remains required.

## 10. 10 (MEDIUM, likely): The proposal's v1/v2 selection doesn't match the frozen producer and validator. initial_controller_inventory always adds obligations['unapplied_targets'] when native is a dict, even with count 0, and so returns v2. validate_initial_controller_inventory adds that key only when retained targets exist, and caps facts['parts'] at one, while the proposal says 'each pinned Hermes part producer'.

Partly corrected proposal; suspected runtime defect not confirmed by source: native_inventory335–338 returns dict only for nonempty targets and list otherwise, so actual producer emits no zero-count clause. Exactly one combined Hermes component matches frozen parser/unique producer validation; former plural wording corrected. No code change/test/second review.
