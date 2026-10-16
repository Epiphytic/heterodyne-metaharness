# btq-zcd1l actual R1 dispositions — proposed d2

Actual R1 on `e73eab04ac9eeb91ea7a5794cd04bbe2236fde4d` returned **REVISE**, 24 findings. This is author disposition, not a new reviewer verdict. Original raw report remains unchanged. One actual round consumed; no new round was run by the author. Sources are pinned in [the input manifest](zcd1l-management-inputs.json). Sections refer to [the revised design](../designs/2026-10-16-management-design-task-recovery.md).

## R1.1: Fixed

§6 explicitly adds manifest-bound installer/shim support; §1 includes harness scripts/tests. Keep bin/btq and harness Python byte-identical so existing controller load_runtime pins stay valid. The old hardcoded installer is identified as insufficient, never called a supported new-byte installer.

Source basis: harness scripts/install_workstream_controller.py: install_management; scripts/workstream_controller.py: source_digest/load_runtime.

## R1.2: Fixed

§2 defines absent dependencies plus integer count zero as empty throughout the same normalization path; nonzero/unknown counts and missing labels refuse. §7 requires real omitempty fixtures across phases.

Source basis: queue bin/btq-manage.py: _edges/_snapshot/_expected_after; bin/btq: evidence_snapshot.

## R1.3: Fixed

§3 binds explicit parent-child before/after parent and endpoint effects; unrelated parent drift refuses. Final comparison now includes parent, absent from evidence_snapshot today.

Source basis: queue bin/btq-manage.py: _edge/_observed_bead; bin/btq: pins/evidence_snapshot.

## R1.4: Fixed; compatibility tightening disclosed

§3 requires a management-owned validation wrapper before reclaim finish for both classes and names claim-note/current/fence/reservation effects. Original implementation eligibility/pin semantics remain, but Management readback deliberately tightens.

Source basis: queue bin/btq-manage.py: finish; bin/btq: Handoff._landed/finish.

## R1.5: Fixed false route; bounded exclusion

§2 removes the nonexistent approve-bead planning repair claim. Invalid pins safely refuse, with no metadata writes in this amendment. A genuinely needed exceptional correction requires separately scoped reviewed/authenticated OPS authority; ordinary unpinned planning works without it. No generic pin-repair API is invented.

Source basis: queue bin/btq: approve-bead target kind/open checks and CREATE_ONLY_META; bin/btq-manage.py: _approval.

## R1.6: Fixed with explicit enforcement limit

§3 defines retained lineage/issuance/report hashes and recomputes budget from distinct issued invocations, including failed/aborted/running/unresolved calls. Only proven unspawned reservations are excluded; unknown issuance/history HOLD and never creates budget. Unknown/exhausted history cannot reopen or authorize work-resuming reclaim; normal review invocation separately checks it. No claim that existing general claim/model-launch code already enforces this new operational guard.

Source basis: queue bin/btq: design_required/review_recorded; harness Controller.allowed.

## R1.7: Unsupported disqualification rebutted; consent rule clarified

§4 preserves genuine configured principals including user-authorized Bel. Native authenticated exact intent is necessary; mere policy membership or model-generated consent is insufficient. Agency cannot be inferred from a key name and is not a basis to revoke user-authorized policy. No policy mutation is proposed.

Source basis: queue bin/btq: Handoff.check_ask; retained explicit operator instruction restoring Bel, parent controller verified.

## R1.8: Fixed

§5 defines held_unissued/unknown/intermediate conflict outcomes; no moving phase before intent. Authority refresh outside queue lock, strict noncancelled A before each new effect, bounded five-second initiation. Revoke after issued effect cannot be undone. Replace partial graph remains visible and reconcileable under a fresh exact card.

Source basis: queue bin/btq-manage.py: move/_mutate_steps/abort; bin/btq: Handoff._run/check_ask.

## R1.9: Fixed via proposed versioned commands

§5 proposes management_format 2, inspect/settle with expected-record digest, immutable archives and predecessor binding. No-effect proof allows fresh requests; unknown phases remain preserved. Rollback refuses active format-2 records; old contract completion uses retained old bundle. These are new scoped module/shim support, not current commands.

Source basis: queue bin/btq-manage.py: load/_prepare/abort; bin/btq: Handoff._run; harness scripts/workstream_manage.py: parser/dispatch.

## R1.10: Fixed

§3 inventories relevant historical holder notes and distinguishes none/remote/missing/uninspected. Unresolved source evidence refuses without false clean/none. Newly added closed planning status excluded.

Source basis: queue bin/btq: evidence_snapshot and _WORKTREE_NOTE holder filtering.

## R1.11: Scope narrowed; existing limits explicit

§3 excludes closed planning. Original implementation closed grammar is retained but no dependent enumeration or lane reclose promise is invented; absent real effect/lifecycle proof refuses. §7 binds this negative behavior.

Source basis: queue bin/btq-manage.py: _validate_change; native dependent_count source; lifecycle prior-close guard.

## R1.12: Fixed

§2 verifies native selector compatibility for actual receiving run on reclaim, without routing edits. Status/dependency operations preserve selectors without pretending to assign a receiver.

Source basis: queue bin/btq-manage.py: _route; bin/btq: matches/pins; harness Controller.allowed.

## R1.13: Fixed conservatively

§2 protects all approval-edge types equally in both planning pin states. It explicitly adds management-endpoint-roles-v1 closed versioned RULES and proposed --endpoint-facts-file per-request data-only purpose attestations. Native approval/confirm/question and known protected-gate evidence always override; unknown/contradictory roles refuse removal. Exact fresh snapshot/source evidence and decisive purpose excerpt are covered by the existing per-operation card, with no extra model, approval or redeployment for a new ordinary endpoint. No installed gate detector is claimed and ordinary implementation pins do not themselves classify a gate. Rule changes remain reviewed design/deployment changes; ordinary runtime endpoint facts are not rule changes. Pinned planning additionally verifies task scope from actual approval artifacts, not only approval_valid.

Source basis: queue bin/btq: approval_valid/bd_dep; bin/btq-manage.py: _approval/_validate_change.

## R1.14: Fixed historical attribution

§6 distinguishes task-specific ywnix bootstrap and sbjv authority from a general API. Refusal never grants OPS permission. All new exceptional repair questions use authenticated Marmot scope and preserve real gates/locks.

Source basis: approved d2 bootstrap paragraph; queue docs/OPS.md and docs/MANAGE.md retained fallback context.

## R1.15: Partly fixed, partly factually rebutted

§6 explicitly says doctor zero is not transport health; real read-only transport/config checks required. A retained fn9r policy before/after DOES exist; exact hashes pinned in manifest. Bel restoration was a separate user-authorized change, not falsely bundled into fn9r.

Source basis: P management-admind-policy-prerequisite deployment-result.json; scripts/workstream_controller.py doctor; actual fn9r policy hashes.

## R1.16: Superseded gate rebutted; remaining gates retained

§4/§7 record current explicit user merge authorization after actual review and real approvals; historical additional master-go is superseded, not resurrected. Code/delta review and no-substitute rules remain. No review reset or human design/deployment bypass.

Source basis: approved d2 historical landing paragraph plus current operator instruction supplied by controller.

## R1.17: Fixed timestamp; scope remains useful

§6 records authenticated sbjv A05:47:37Z/applied05:51:26Z; no pending claim. Specific historical OPS resolution does not implement reusable reclaim/status/dependency management.

Source basis: P design-task-reopen authenticated authority/deployment records, controller verified.

## R1.18: Fixed

Input manifest records exact three-file closure, immutable installed shim/management manifest and source hashes. Candidate release must separately bind its own hashes and preserve retained files.

Source basis: installed management bundle and manifest; queue 6fe5759 Git objects.

## R1.19: Fixed

§3 shows real Codex enrolled workstream-manage and separate workstream task pause. Proposed inspect/settle dispatch explicitly belongs to shim/module without modifying raw bin/btq grammar.

Source basis: harness scripts/workstream_manage.py: verified_runtime/parser; scripts/workstream_controller.py: facade dispatch.

## R1.20: Fixed

§2/§5 require stable safe reason codes and inspect for every management request/phase; no raw RuntimeError-only output. No credentials or unsafe payload traceback.

Source basis: harness scripts/workstream_manage.py: exception handler; queue Management phases.

## R1.21: Fixed

§5 prepares/persists exact audit payload/hash/marker before issued. Read/build failure remains retryable; ambiguous actual comments-add stays unknown until unique marker readback and is never blindly repeated.

Source basis: queue bin/btq-manage.py: finish/comment/_audit_after.

## R1.22: Fixed bounded layout; overflow remains intentional

§4 uses full 12-character nonce at plan preflight, conservative36/3200 budget within transport40/3500. Material facts remain; if no safe self-contained card fits, no post. No unsupported promise all pathological task content fits.

Source basis: queue bin/btq-manage.py: card/plan/_prepare; admind ask limits.

## R1.23: Fixed

§2 specifies dictionary/key/value types, absent-vs-present pin semantics and handled ValueError safe refusal. §7 negative fixtures include number/list/null/malformed metadata.

Source basis: queue bin/btq-manage.py: _approval/_run exception boundaries; harness shim exception handler.

## R1.24: Fixed

§3/§4 require current human hold/block reason visibly quoted and source-bound; absent or ambiguous reason is explicit, unsafe/material overflow refuses. Digest alone cannot hide a decisive human hold.

Source basis: queue bin/btq-manage.py: card; bin/btq: evidence_snapshot notes/content.
