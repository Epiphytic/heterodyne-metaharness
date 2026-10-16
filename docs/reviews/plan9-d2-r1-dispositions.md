# Plan9 d2: actual R1 author dispositions

Status: UNREVIEWED d2; actual R1 REVISE20, one of three rounds used. This is author response, not reviewer clearance or human approval. Historical d1 and all actual review bytes remain unchanged.

Raw actual report SHA-256: `e2c7531a0104e7765ba69eb1fa5280a8ec84d0a66e2b356668dcc3bb1fe0a012`. Extracted exact20 SHA-256: `0edbea5c31993f4b17ec560e0bb966c78aa46a50c9549b04f66343f898ab3ed3`. Invocation-result SHA-256: `c195327bce9b9eabe0c740c4df47377bbb95be0288fd209856bdd2b1ad5d81eb`.

[Ordered exact machine-readable ledger](plan9-d2-r1-dispositions.json) retains every finding and canonical per-finding digest. Original line numbers refer to reviewed d1.

## 1. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:109

The interrupt list treats only steering 'to get work unstuck' as intervention, which quietly relaxes ADR §11's 'without operator intervention beyond approvals'; meanwhile the coverage rows codex_managed, claude_managed and ordinary_operation (lines 76-79) require demonstrated operator steering and intake, and §11 does not disclose this relaxation.

Steering is either intervention or it isn't. If it is, the steering that codex_managed/claude_managed need inside the segment interrupts the clock, so those rows can't pass on a qualifying segment (the read-only Claude reviewer is rarely steered, per ADR §12). If it isn't, the operator can give corrective guidance through ordinary Marmot thread replies for 72h and still qualify, which contradicts 'beyond approvals' without the human approval line 147 says a relaxation needs.

Author disposition — addressed_in_unreviewed_d2 (§§4–5,7,11): Move functional human intake/steering/status/question/picker exercises to two preclock rows. Require seven natural segment rows across both managed adapters, with only genuine operational approvals/denials during the clock. Preserve ADR strictness; no relaxed interpretation.

Exact finding canonical SHA-256: `554249b30e5fad2bb74b6e8c5ac643f31a6c9266d881a1883a6e81205b09105a`.

## 2. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:53

The Drill row is {id,scope,authority_ref,runbook,receipt}: it has no family field from the six required families, no release/config identity, and a bare authority_ref instead of an Authority projection, so a closed-schema evaluator cannot check that all six families are covered or that reused receipts match the release.

An Entry with drills=[one runtime_control receipt] passes the stated rule 'nonempty drills'. A receipt from an older release is reused because 'exact matching release/config/capability identities' exists only in prose, and the Drill/receipt schema carries no field to compare. The accounts or security_and_install drill is silently skipped.

Author disposition — addressed_in_unreviewed_d2 (§§3,6,8): Drill now binds one of six finite families, actual scoped Authority, canonical release identity, runbook and actual receipt; require all six and every declared subcase. Isolated equivalence and release/config/capability comparison are mandatory.

Exact finding canonical SHA-256: `c763722b9359388f3c0efb56ffafb315438a08b722e1273bcd35a555cce8214a`.

## 3. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:51

Gate.name is an arbitrary ID with no closed list of required gates, so 'complete phase/capability/spike and privilege prerequisites' cannot be checked mechanically.

An Entry lists only Plan8 and S7 gates and leaves out S5/OpenShell, S8, the store-helper root host change (ADR §17 item 13), the wsd-act privilege boundary, the ADR §10 deterministic integration suite, the reviewed acceptance collector/evaluator implementation, and the user-schedule gate or its approved deviation. It still satisfies 'nonempty gates' and 'every Artifact resolves', so entry proceeds with missing prerequisites.

Author disposition — addressed_in_unreviewed_d2 (§§3–4,8): Replace arbitrary gate IDs with 18 mandatory distinct GateName values, including all phases, spikes, privileged host changes, acceptance integration, schedules and phase2 boundary. Required source-specific completion and genuine authority cannot be supplied by supplemental evidence.

Exact finding canonical SHA-256: `f94e5d29b596a52cd995cef88770a81ab21864c06cf140a60a9e935c4993132d`.

## 4. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:41

The Segment.reason enum (none|manual_intervention|release_change|observation_gap|clock_uncertain|invariant_failure) has no truthful value for interrupt causes that §7 lists: an operational hold that needs human repair before any repair happens, intentional fault injection, and break-glass.

A reconcile reaches needs-human after two failures, and line 109 says that interrupts 'even if the actual repair has not occurred'. The collector has to record reason=manual_intervention, which claims an intervention that never happened, or invariant_failure, which is also inaccurate. Interrupted-segment history becomes misleading.

Author disposition — addressed_in_unreviewed_d2 (§§3,7): Add truthful human_repair_required, intentional_drill and break_glass reasons; keep distinct from actual manual_intervention. Preserve original cause before an operator repairs anything.

Exact finding canonical SHA-256: `aa37304a502d600d763d0cd58e40fedebe088ca0622a6838d974710a606d26a0`.

## 5. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:45

The Segment lifecycle across qualification and archive is undefined: it is not said who sets status=qualified or when relative to the Assessment, whether a segment_stop is recorded at qualification, whether Segment receipts are versioned per status, or what the final Assessment 'revalidates' after later workstreams, archive operator actions or interruptions.

The archive is itself an operator action, and new workstreams are a configuration change. Both happen after qualified_pending_archive. If the segment is still running they interrupt it, and release_complete then points at a segment that was later interrupted. If Segment.status=qualified is written before the Assessment, the Segment asserts qualification without one. Implementers choose different orderings and the noncircular chain becomes ambiguous.

Author disposition — addressed_in_unreviewed_d2 (§§3,8): Define immutable running/interrupted/sealed receipt versions. Collector/evaluator orders final checkpoint, stop, sealed Segment, qualified_pending_archive Assessment and qualification event. Independent readback precedes archive/expansion; later changes do not retroactively interrupt a stopped interval. Contradicting historical evidence yields retained reassessment/hold.

Exact finding canonical SHA-256: `220eea040df39008c88349c1eef38ef7f03d8f051bfed5795f02af2d5d985112`.

## 6. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:102

Loaded release/config identity is checked only at Entry and final Assessment plus source_change events. Nothing requires the loaded inventory to be re-captured after each allowed service-manager restart or session-generation launch, and the inventory scope does not name the sandbox runtime, Dolt, interpreter, Marmot/wn daemons or unit files, which OS updates can change.

Mid-segment, unattended-upgrades replaces podman/OpenShell or Python, or someone pip-installs a different wsd. An allowed automatic restart loads it, and it is then reverted before the end. The final identities match Entry and no source_change event was emitted, so the segment qualifies on a release that did not run the whole 72h.

Author disposition — addressed_in_unreviewed_d2 (§§3,7): Inventory includes interpreters, runtime/backend dependencies, queue/Dolt, Marmot/wn, installs, units and effective policy. Recapture every restart, generation, re-exec, observation and final checkpoint; freeze upgrades or prove identical loaded identity.

Exact finding canonical SHA-256: `547a578eb9db35ea2baa4fdfc72e4ee2dd0032547d854b713a8b1398af64688c`.

## 7. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:102

The rule that commits in corpus projects are not release changes ignores self-hosting. Migrated corpus beads are harness work that can merge into the repositories the running release executes from, such as the btq checkout pinned as Release.queue_commit.

A surviving corpus bead merges a change to beads-task-queue/bin/btq in the working checkout the running queue invokes. The running acceptance release changes at that moment, but it looks like ordinary reviewed corpus development and no source_change event is recorded.

Author disposition — addressed_in_unreviewed_d2 (§§3–4,7): Corpus commits are allowed only in isolated worktrees unable to mutate the running release. Mutable self-hosted queue/product changes count as release_change; no corpus exemption.

Exact finding canonical SHA-256: `76bcc3ec4df51af162a10ec57639d2db9a70d06ddb27257082fc47e205b1ce83`.

## 8. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:33

The Authority projection {native_bead_id,decision_ref,context_digest,adr_revision} has no readback Artifact, approver identity, approved_at, approved_digest or channel ref. A receipt cannot show that it is a projection of an actual native readback rather than minted.

An Entry carries design_authority with a plausible bead ID and a context_digest copied from the published design. The evaluator cannot check approved_digest==context_digest, approver ∈ btq approvers, or the Marmot via_ref, which are the fields the manifest's own historical approval evidence relies on. A self-asserted authority passes.

Author disposition — addressed_in_unreviewed_d2 (§§1,3): Authority variants bind real decision readback, approver, timestamp, digest equality, exact scope, policy authorization and authenticated Marmot attribution/audit. Native design authority remains required; a genuine scoped deployment decision need not invent a native bead.

Exact finding canonical SHA-256: `8f4352ae48c5cc3a88bb46afdd9aef087ca4735b8ffd7e099187aef6adf1aa0b`.

## 9. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:43

The Archive and ArchiveRow schemas have no Authority field, so the human authorization for the high-impact archival of all three old repositories is not bound in the closed evidence; ArchiveRow also has no per-row result.

An Archive with result=complete is produced from operation artifacts with no genuine scope approval recorded. The final Assessment's release_complete resolves cleanly even though line 122's 'genuine scope authority' was never evidenced.

Author disposition — addressed_in_unreviewed_d2 (§§3,8): Archive carries actual Authority; every one of three ArchiveRows has held/complete result and mandatory operation/history/read-only/dependency proof. Reuse a real deployment grant only if its scope actually covers archive.

Exact finding canonical SHA-256: `d3e0acd154cc7b698e48d931c279a79cc4af5d37a0cb259870086689107a2061`.

## 10. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:104

The 300s heartbeat rule conflicts with the 600s delayed-collection allowance. The intervention-audit sources don't cover host-side actions (SSH/sudo/systemctl, config edits, the host exceptions in ADR §17 item 7), and line 102's 'captured clock-health bound' and gap-measurement clock are left unspecified and are not among the §11 choices.

A heartbeat arrives 450s after the previous one. One implementation interrupts ('at most 300s apart') and another keeps the segment continuous ('<=600s ... can remain continuous'). An operator restarts a unit over SSH or approves at the host terminal. No authenticated audit stream sees it, so 'no unobserved intervention' is asserted without a source. The UTC tolerance is chosen ad hoc.

Author disposition — addressed_in_unreviewed_d2 (§§3,7,11): Choose 300s nominal and <=600s admissible only with complete continuity proof; >600s interrupts. Clock arithmetic is exact, <=1s UTC/monotonic discrepancy and no boot crossing. Explicit finite trusted-operator model uses supported host/control audits and authenticated declarations; missing required proof holds, no hostile-root proof asserted.

Exact finding canonical SHA-256: `ceb58afcc6b29a175ae7a6a32599e71d040ad3648fbb2743680c9c7b77fce0e6`.

## 11. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:53

Corpus/culling completeness can't be verified. Entry has no artifact for the full old-open-bead inventory, there is no disposition state for beads not yet migrated, and culled rows sit in the corpus with required_coverage; the ADR §14 'each open bead' pass and real surviving intent rest only on prose.

Execution migrates a convenient subset ('bounded candidate set') and leaves inconvenient open beads unmentioned. Entry still validates because the corpus is nonempty. Later rows 'from that same migration inventory' cannot be traced to any recorded inventory, and a culled bead with nonempty required_coverage is accepted.

Author disposition — addressed_in_unreviewed_d2 (§§3–5,8): Add complete per-source-queue cutoff inventory, exactly one migrated/culled/deferred disposition per old-open bead, original intent/history and delta reconciliation. Only genuine survivors enter Corpus. Deferred rows remain open and must be resolved before whole migration/archive completes.

Exact finding canonical SHA-256: `46cc22896494c2e437b6ef6e2384686312014c6f883055ee71a0beb4d1b5f23d`.

## 12. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:68

Coverage result 'fail' and Assessment.unresolved have no defined consequences. A failing row (for example a duplicate execution seen in park_resume) is not tied to interrupting the segment, and nothing says qualified requires unresolved=[], all eight rows passing, and duration_ns>=259200e9.

park_resume records 'fail' after a real duplicate execution and later flips to 'pass' on another bead, which hides an invariant failure. Or an Assessment with nonempty unresolved and result=qualified_pending_archive is schema-valid because no cross-field invariant is stated.

Author disposition — addressed_in_unreviewed_d2 (§§5,7–8): A failed coverage observation is append-only invariant_failure and interrupts; later passing work cannot erase it. Qualifying assessment requires seven passing segment rows, unresolved acceptance faults empty, exact duration and unchanged evidence. Normal safe pending work remains separately recorded.

Exact finding canonical SHA-256: `9ee7b44307e384819c16851b7e0fc556509a1221939a6b3f4a747bbdd7d60e7a`.

## 13. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:98

The scheduling-proof entry gate requires forge poll at 60s even though forge approvals and the forge bridge are phase 2 (ADR §12/§14), and the Plan8 d1 design keeps forge_poll disabled. Unlike user schedules, no phase-2 exemption is stated.

Entry is held forever on a job that v1 deliberately leaves disabled. Or someone enables a phase-2 polling surface just to satisfy the gate.

Author disposition — addressed_in_unreviewed_d2 (§§3,6,11): Explicit forge_phase2_boundary requires disabled ingress/bridge/polling in v1 and applicability proof, not enabling phase2 to satisfy a scheduler gate. Original user-schedule requirement remains unless a genuine approved explicit deviation exists.

Exact finding canonical SHA-256: `d7fcaffd90e56119a67a904d193dc78248492278271a77965d39d3664c70e13b`.

## 14. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:98

The design reacts to 'a sibling Plan8 draft' and 'Plan8 d1's local-health observation' (line 104) without pinning that source; the manifest's 11 sources leave out Plan8 d1 (c9fa794, docs/designs/2026-10-16-plan8-scheduling-reconciliation.md).

Plan8 d2 changes its user-schedule or babysitter wording. Reviewers and the human approver can't tell which draft the Plan9 user-schedule gate and the 'not the existing observer' statements answer, so the distinction from sibling-draft authority has no pinned basis.

Author disposition — addressed_in_unreviewed_d2 (§§3,6–7; inputs manifest): Pin exact unapproved Plan8 d1 c9fa7942386556b5227d96cbe73b8ca4a9223efc bytes/blob and immutable URL; its local-health observer and proposed deferral are provisional, not installed collector or authority.

Exact finding canonical SHA-256: `edcd19a741165925ea79d985561c022c6450d26ae5238cfb1974667a58c73893`.

## 15. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:142

The §11 human-choice row says 'All Plan8→7→6 gates remain execution gates' and leaves out Plan5, while §1 (line 14) keeps Plan5 in the predecessor chain.

The human approves the §11 ask as worded. A later reader treats Plan5 completion as not part of the approved execution gate, and execution starts without Plan5's executor/intake integration.

Author disposition — addressed_in_unreviewed_d2 (§§1,3–4,11): All execution and human-choice predecessor chains now explicitly include Plan5 through Plan8. No executor/intake gate is waived.

Exact finding canonical SHA-256: `9c7ae1be414f9929e283d640db58e5a7abb1e69047f58d3c5356762d7602ccbd`.

## 16. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:10

The governing ADR blob a27acec still says 'Proposed, revision 17 (draft ...) needs its own cross-model review and operator approval before anything cites it'. The design asserts it is 'genuinely approved' without disclosing that its in-file status is stale and that approval rests only on native ovngv metadata.

A reviewer or evaluator reading the pinned ADR bytes sees an uncitable draft and concludes the Plan9 authority chain is invalid. Or a future evaluator uses in-file status as authority and rejects the governing revision.

Author disposition — addressed_in_unreviewed_d2 (§1; inputs manifest): Disclose the immutable a27 header is historically stale. Actual governing authority is the pinned genuine ovngv metadata including review, decision, approver, digest and Marmot ref; closed status alone is insufficient.

Exact finding canonical SHA-256: `2d56e7ca98ff4a1497142a8552a5360a254e9ad3e3e54670aad74af91e1a64b2`.

## 17. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:40

monotonic_ns is a JSON integer Count with no stated precision or range rule; CLOCK_MONOTONIC in ns passes 2^53 after about 104 days of uptime.

On a host up for more than 104 days, a float64-based JSON parser (JavaScript, jq) rounds monotonic_ns. Event-to-event elapsed differences pick up errors of several ns at the 259199/259200s boundary, or validators disagree on duration.

Author disposition — addressed_in_unreviewed_d2 (§§3,7,10): Use canonical uint64 decimal strings for all nanosecond fields and exact integer arithmetic; forbid floats, overflow and precision loss above 2^53. UTC precision/bounds and future boundary fixtures are explicit.

Exact finding canonical SHA-256: `beb1c0ab829f01ecd6f726595d2f3819bfefc076cfbbb2b6efef87af6f0d9f69`.

## 18. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:33

Artifact.role is an arbitrary ID with no fixed vocabulary, so the evaluator cannot tell which evidence artifact is the final loaded inventory, the claim readback, or the dependency audit.

An Assessment.evidence list includes an artifact with role 'loaded_inventory_final' and another with 'final-inventory'. The evaluator can't deterministically pick the one to compare against Entry, and a closed-schema check is impossible.

Author disposition — addressed_in_unreviewed_d2 (§3): Remove arbitrary Artifact.role. Mandatory named typed fields identify authoritative evidence; generic evidence arrays are supplementary and cannot substitute for them.

Exact finding canonical SHA-256: `2c3016d1e6197e4e050263d58cdc2fc36af0c2c090ccad783faa9d05f329d84c`.

## 19. docs/designs/2026-10-16-plan9-release-corpus-and-acceptance.md:42

Redundant derivable fields: Assessment.drill_receipts repeats Entry.drills[].receipt, and Segment.release_sha256 repeats the release already reachable through entry_sha256.

Two sources of truth can diverge, for example an Assessment listing a drill receipt that is not in Entry. Each validator then needs extra consistency rules, or it accepts a mismatched pair.

Author disposition — addressed_in_unreviewed_d2 (§§3,8): Remove Assessment.drill_receipts and Segment.release_sha256. Resolve actual drills and Release through the one immutable Entry binding; no competing derived sources of truth.

Exact finding canonical SHA-256: `1da5bb30f26a7d85b9fd448a1af8c74bd6052726c0a940f5914f06d34aa585e2`.

## 20. docs/reviews/plan9-d1-inputs.json:136

All eight private_inputs carry the same 'obsolete586/tz5o8 governing-authority claim explicitly corrected' role, including inputs that never made that claim (CI result, guard rearm, native issues), and none says which input sources the ovngv/xywu0 approval projection.

A reviewer can't tell which pinned input backs historical_approval_evidence (it is in fact release-resume-2026-10-16/native-workstream-issues.json). The copy-pasted role misdescribes inputs such as the hosted-CI result as corrected authority claims.

Author disposition — addressed_in_unreviewed_d2 (inputs manifest): Assign per-input truthful roles. Name and hash the native-issues snapshot as the source of both approval projections; limit obsolete-authority correction to preparation inputs that actually carried it. Retain actual R1 artifacts and historical d1 byte pins.

Exact finding canonical SHA-256: `72c6430395afdadc5597102b3524b44920025c47ed2998a26d0c553683287c38`.
