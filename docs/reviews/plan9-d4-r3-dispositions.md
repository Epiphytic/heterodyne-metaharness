# Plan9 FINAL R3 ordered author dispositions

Actual R1/R2/R3 remain REVISE20/22/17. Cap3 exhausted. This d4 is an unreviewed author repair, not an APPROVE, fourth review, human admission or live acceptance. Exact source findings below retain parsed original strings and order; JSON companion retains per-finding canonical hashes. Proposed fixture scenarios are requirements for future implementation, not tests executed by this docs task.

Raw report SHA256: `78c206dc5c4dc29c03d865e00f497cbbaa1efcb5f1457f99b45237823242837f`.

## 1. The user_schedules approved_deviation path needs a separately approved native ADR amendment, but governing_authority and design_authority are hard-coded to btq-ovngv at a27, so an amendment can never become the governing revision.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:64`

**Exact failure scenario:** Plan8 chooses the deferral and Liam approves an ADR amendment, which in this lineage is a new ADR revision (r16 Amendment 1 was folded in via the ovngv rebase). approval_valid(amendment, a27) fails because its adr_revision is not a27. If it passes as r18, governing_authority still has to be ovngv/a27 and Release.governing_adr has to match. Entry either holds forever or runs under an ADR revision that isn't governing.

**Author response** (§3 GoverningLink and gate matrix; §11): Explicit authenticated successor chain retains original a27/ovngv and every exact reviewed amendment; final design_authority, governing gate and release revision all bind the last actual approved successor. user_schedules deviation must occur in that chain and remain carried by its final scope. No successor or approval is invented.

**Future offline fixture:** Accept exact approved a27→successor carrying schedule omission; reject missing/foreign link, deployment-only waiver and mismatched final revision.

## 2. NativeAuthority requires the literal via:"marmot" plus a bound Marmot audit, but real predecessor native approvals were recorded with via="cli".

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:37`

**Exact failure scenario:** The pinned snapshot has genuine approvals btq-xnnvz (admind membership guard d4 and its implementation plan) and btq-ovvzt with via=cli and no Marmot via_ref or audit. Any plan5–8 or acceptance_integration gate that relies on them cannot be represented. Entry holds permanently, or Liam has to re-approve existing designs through Marmot, which is the routine re-approval §1 says is not introduced.

**Author response** (§3 NativeAuthority historical provenance; §9): Native via preserves actual marmot/cli metadata. Distinct historical_native evidence binds original grant/scope/record and current native validity without fictitious Marmot ID/audit. Source-backed xnnvz/ovvzt remain historical-only, not a new terminal channel. Missing genuine evidence holds for evidence recovery; no routine re-approval.

**Future offline fixture:** Represent actual CLI readbacks with null absent via_ref; reject invented Marmot attribution, bare closed status and new terminal grant.

## 3. The terminal transaction checks the expected ledger head and binds a precomputed event sequence (seq and previous_sha256), but the collector keeps appending heartbeat, corpus and approval Events to the same ledger and nothing pauses or fences it during evaluation.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:152`

**Exact failure scenario:** Final checkpoint at 72h+5s; evaluation takes 40s while the collector appends two heartbeats. The head check fails and the intent is aborted, and the next endpoint does the same. The segment never seals, or an implementation re-chains segment_stop onto the new head, so its hash differs from the manifest and recovery reports a conflicting manifest.

**Author response** (§8 fenced publisher and terminal transaction): Collector/evaluator share publication fencing; acquisition continues into durable bounded spool. Freeze exact checkpoint/head/sequence, reconcile delayed sources through endpoint, then commit/recover identical reserved terminal bytes. Postendpoint observations resume outside the sealed segment. Overflow/missing cursor holds rather than drop or rechain.

**Future offline fixture:** Concurrent heartbeat/approval acquisition cannot alter frozen head; crash at fence/intent/commit restores exact sequence; delayed interval contradiction aborts.

## 4. release_complete requires the current Identity to equal the qualified Entry Identity exactly, yet §8 allows workstream expansion before archive and requires old-repository dependencies to be replaced after qualification. Both change code or configuration components.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:166`

**Exact failure scenario:** After qualification the roadmap adds the other workstreams (a config change), or the pre-archive dependency audit finds a runtime dependency on harness-dev and replaces it (a code/config change). The current Identity differs, release_complete holds, and a new 72h qualification is needed. It never converges if expansion comes first. The dependency audit belongs before Entry, and workstream config needs explicit treatment in the registry.

**Author response** (§4 step1; §8 pre-entry dependencies and expansion order; §11): Complete planned retiring-repository replacement before Entry and pin roster/configuration in Identity. Recheck drift before archive. Archive/release_complete precede expansion; any code/config drift needs a genuinely new qualified release. Later expansion cannot rewrite historical baseline completion.

**Future offline fixture:** Prepared dependency-free release archives without Identity change; replacement after Entry or expansion before completion holds instead of exemption.

## 5. Drill reuse after a release change requires 'a typed new Equivalence', but Equivalence must be null for release scope and release-scope identity must equal Entry.identity. Equivalence also has no field linking back to the original receipt.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:80`

**Exact failure scenario:** An unrelated config change interrupts with release_change and a new Entry is produced. The real relay/Hermes outage drill (release scope, unchanged projection) cannot carry an Equivalence and its identity no longer equals the new Entry.identity. The schema rejects reuse and forces a re-run that §3 says is unnecessary, or an implementation accepts it ad hoc with no typed link to the original test.

**Author response** (§3 DrillReuse and Equivalence): DrillReuse explicitly links original Drill/Entry/receipt and current projection comparison/revalidation for either scope. Equivalence remains isolated-only. New release-scope wrapper binds current identity while retaining truthful old receipt/time; reuse never claims a fresh execution.

**Future offline fixture:** Reuse unchanged relay projection under changed unrelated component; reject changed relevant component, missing original receipt and release Equivalence.

## 6. CorpusAddition during the segment assumes more survivors are migrated and routed mid-clock, but migration (rewrite, resolving blockers, routing) is done by root or a human, and §5 treats any intake or operator participation in the segment as an interruption.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:78`

**Exact failure scenario:** review_close is still missing at hour 30, so root rewrites and routes another old bead into the fresh workstream and records a corpus_transition plus CorpusAddition. The schema accepts it as valid, but it is human intake inside the segment and should have interrupted as manual_intervention. Implementations will disagree on which applies.

**Author response** (§3 Corpus reserve/activation; §4 corpus sequence): Entry records finite exact pre-migrated/routed reserve. Automatic activation may supplement coverage; human rewrite/intake/routing during the interval interrupts regardless of CorpusAddition. No new migration during clock is inferred.

**Future offline fixture:** Activate pre-entry reserve automatically; reject undeclared/newly rewritten row and interrupt actual human supplementation.

## 7. The cache invalidates on any 'process launch/re-exec/restart' and then needs a full recapture before continuity, without limiting this to registered component processes or to the affected components.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:142`

**Exact failure scenario:** Natural corpus work launches CLI sessions and thousands of sandboxed child processes (git, python, tests). Each one triggers a full recapture of both managed trees and the runtimes, which is the R2.20 cost problem again. Collection overruns 300/600s and the segment interrupts as observation_gap because of the observer's own load.

**Author response** (§7 registered owner and dependency cache scope): Only registered component-owner launches/reexec/restarts or relevant object changes invalidate affected dependency closure. Ordinary corpus child creation alone does not force full recapture. Unknown owner/mutation continuity holds; no live code exemption.

**Future offline fixture:** Thousands of ordinary child launches preserve proven cache; registered daemon restart recaptures its closure; child registered-object mutation invalidates.

## 8. The preclock Coverage rows (operator_surfaces steering for both adapters) have no Identity or projection binding, unlike Drills, so a steering exercise from an earlier release can satisfy Entry.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:111`

**Exact failure scenario:** Codex queue steering and Claude send-keys steering are exercised, then a fix changes the steering code. Entry is produced with the new Identity and reuses the old operator_surfaces pass. Steering on the qualified release was never shown, and nothing typed detects it.

**Author response** (§5 CoverageBinding/CoverageReuse): Preclock evidence binds complete runbook-fixed identity projection including both steering implementations/config/install/policy. Reuse requires exact old receipt/Entry and current comparison/revalidation; changed steering requires affected exercise again.

**Future offline fixture:** Old steering after relevant fix refuses; same relevant projection with unchanged evidence revalidates without invented rerun.

## 9. Login/sudo/admin access is a required audit class, but there is no rule separating passive read-only access (allowed) from operational intervention. The ADR also requires host access for 'read on the host' refs, withheld alerts and blocked relay decisions.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:138`

**Exact failure scenario:** Liam logs into the host to read an approval card's ref marked 'read on the host' (ADR R25) and then approves through Marmot. The login stream records an admin session. One evaluator interrupts with manual_intervention and another accepts it as passive, so the same ledger qualifies or not depending on the implementation.

**Author response** (§7 passive access classification): Login alone is audited access, not automatic intervention. Admitted read-only viewer/export plus complete effect audit permits host artifact inspection before Marmot approval; operational action interrupts, unknown unaudited shell effects are observation_gap. No declaration substitutes for proof.

**Future offline fixture:** Read-host-only evidence then Marmot approval continues; task/service/attach action interrupts; missing audit cannot be declared passive.

## 10. Replacement segments after an interruption have no rule for whether a new or revalidated Entry is required. d2's 'start a new segment after revalidated entry' was removed, and Segment.entry_sha256 lets a later segment reuse the old Entry.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:146`

**Exact failure scenario:** A segment is interrupted with human_repair_required at hour 50. A new segment_start reuses the original Entry, with gates, policy revalidation, drills and the migration delta from days earlier and no re-check. Another implementation demands a full new Entry, with re-run preclock steering and drills. Outcomes diverge.

**Author response** (§7 replacement Entry): Every replacement segment requires new immutable Entry with fresh native/current-policy/gates/identity/migration/audit/claim revalidation. Exact unchanged drills and preclock exercises reuse their explicit proof contract; only changed scope/deployment needs new human authority. No elapsed/budget carry.

**Future offline fixture:** Old Entry hash cannot restart; new Entry with current deltas and unchanged proven reused projections can start without routine re-approval.

## 11. Several drill families mix release-scope and isolated subcases (communications: real relay outage plus fake forge; runtime_control: real restart plus disposable-host reboot; marmot_only), but a Drill has one scope, one flat projection_ids list, and no subcase or result field.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:121`

**Exact failure scenario:** A single release-scope communications Drill carries no Equivalence for its isolated fake-forge subcase, or it is split into two Drills with nothing typed showing that the pair covers every subcase. 'Every specified subcase, actual PASS' can only be checked inside an untyped receipt, so a missing subcase passes the closed schema.

**Author response** (§3 closed 17-subcase registry; §6): Each Drill is one exact mandatory family/subcase/scope with result=pass; table enumerates all17 and runbook expands every actual ADR assertion. Every isolated case needs its own Equivalence. Missing subcase cannot hide inside untyped family label.

**Future offline fixture:** Omitting isolated fake forge/reboot refuses despite other communications/runtime passes; mixed scope receipt cannot replace required distinct records.

## 12. The duration is 'final checkpoint.monotonic_ns minus segment_start.monotonic_ns', which mixes a Checkpoint endpoint with the start Event's own monotonic_ns, even though segment_start binds a start Checkpoint that has its own sample.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:134`

**Exact failure scenario:** The start Checkpoint is sampled at t0 and the segment_start Event is created 0.3s later. One evaluator computes 259199.9s and fails, another computes 259200.2s and qualifies, so the exact 259199999999999/259200000000000 boundary fixture gets different answers.

**Author response** (§7 duration and §8 endpoint binding): Duration uses exact end Checkpoint minus the exact start Checkpoint referenced by segment_start, exclusively. Event creation/publication time never enters elapsed time.

**Future offline fixture:** Start Event delayed0.3s leaves same exact duration; one ns below72h refuses, exact72h passes only other gates satisfied.

## 13. Corpus and Coverage members are keyed by (queue_id, bead_id) without saying whether these are the original old-queue IDs or the rewritten bead in the fresh v2 workstream.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:78`

**Exact failure scenario:** Migration re-creates old bead hwh-ab123 as a new bead in the v2 queue. Corpus lists the source IDs while the pickup and review_close evidence cites the target bead. The membership check fails, or implementations map between them ad hoc, which the closed schema was meant to prevent.

**Author response** (§3 BeadRef/Disposition/Corpus; §5): Origin and target native pairs are distinct explicit fields; complete Disposition source→target mapping must verify. All Coverage.members use target IDs and current workstream. No ad hoc old/new ID mapping.

**Future offline fixture:** Rewritten target review_close maps to source origin; using old IDs as target or ambiguous reused IDs refuses.

## 14. Checkpoint.result can be pass, hold or fail, but nothing says what a hold Checkpoint does to the segment or whether it resets the 600s gap.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:136`

**Exact failure scenario:** A transient unreadable source gives a hold Checkpoint at 400s and a pass at 700s. One evaluator treats the hold as an interruption, another ignores it and measures 700s from the last pass (interrupt), and a third counts the hold as the gap reset (continues).

**Author response** (§7 Checkpoint hold/fail): Only pass advances audited frontier; hold/fail immediately interrupts with actual typed cause, never resets gap or accrues elapsed time. Diagnostic records remain preserved.

**Future offline fixture:** Hold at400s then pass at700s cannot continue old segment; hold source loss reason observation_gap, no grace-reset.

## 15. The configuration, policy, capability and install digests appear up to three times (Release fields, top-level Identity fields, Identity components), with equality rules added to keep them consistent.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:54`

**Exact failure scenario:** An extra mismatch surface and extra fixture burden for no new information. Deriving Release and Identity summary digests from components, or dropping them, removes the 'must equal' checks and the chance of an implementation checking only one copy.

**Author response** (§3 Release/Identity/Install): Remove duplicate config/policy/capability/install summary digests. Component is canonical stable content location; Install references component_id and evidence resolves exact descriptors. Identity canonical hash derives components only.

**Future offline fixture:** No conflicting duplicated digest accepted; missing/mismatched component reference refuses; capture/PID differences remain observations.

## 16. The <=300s 'nominal' and 300-600s 'counts only with complete proof' bands differ in nothing, because every Checkpoint already requires complete audit and identity continuity.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:140`

**Exact failure scenario:** An implementer reads 'nominal' as meaning <=300s intervals are accepted without full continuity proof. One rule (passing full Checkpoints <=600s apart) would say the same thing unambiguously.

**Author response** (§7 checkpoint single rule): 300s is schedule target only. Every passing interval <=600s requires the same complete audit/identity proof; all longer or incomplete intervals interrupt. No nominal evidence shortcut.

**Future offline fixture:** <=300s with missing cursor refuses identically to301–600s; complete600s accepts,601s interrupts.

## 17. The R2.2 timestamp fix was applied only to NativeAuthority. MarmotAuthority.approved_at is still strict 6-fraction UTC with no preserved original bytes, and Gate.source_revision is a single SHA even for gates whose completion spans the product and queue repositories.

Original location: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d3.md:38`

**Exact failure scenario:** An admind decided_at recorded at seconds precision has to be rewritten to .000000Z with no raw-byte comparison against the readback. A marmot_only or acceptance_integration gate completed across heterodyne and btq cannot bind both completion sources.

**Author response** (§3 authority timestamps and Gate.sources): MarmotAuthority preserves original NativeTime plus separately derived UTC exactly as NativeAuthority. Gate.sources lists exact repository/commit/tree for every completion repository, separating native design revision.

**Future offline fixture:** Seconds-precision Marmot source remains exact original; product+queue completion binds both; missing repository or rewritten original timestamp refuses.
