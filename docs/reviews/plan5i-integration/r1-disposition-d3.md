# Plan5i integration actual R1 — d3 author dispositions

Actual REVISE with 10 findings. d3 is UNREVIEWED; two actual rounds used. These responses are proposals, not acceptance. Full raw report SHA256 `fa821758210cb2cd73038f4649549def9189388d251d6288cd98064ced2d50ce` is retained privately at the controller-evidence anchor. Full finding objects and canonical hashes: [JSON](r1-disposition-d3.json).

## 1. docs/reviews/plan5i-integration/source-map-d1.json:6

Finding SHA256 `4207eaf6a3b65cd660ed1f2b6281a763194cf6fcfe6d83044b5320e5790778f9`.

> The pinned historical_source 50fc42f has Plan4c T10 without its review fixes: the six c1/c2/c3 commits on p4c-t10c (bf5593c, 451e934, 3ef0f23, 0afc8c8, e144f96, 358dac6) are not ancestors of it, so the pinned harness.py sha (ee90fa55…) is the pre-fix version.

> Overlaying base..50fc42f lands a wsd/harness.py that logs the raw install name and the btq gap text, and may publish readouts it should refuse. Those are the security fixes T10 c1/c2/c3 made, so the integration silently regresses reviewed fixes. The single permitted final T10 review would then be spent on a candidate missing those rounds.

**Author disposition — §§3,4:** Corrected T10 source and stale-aggregate exclusion are explicit; no missing security fixes may be selected.

## 2. docs/adr/2026-10-16-plan5i-current-main-integration-d1.md:52

Finding SHA256 `0bfc95a9a2964b91988911536a1632ca0e6b918e55c8ab3bc3445b5a9cf72b05`.

> 'Reconcile Plan4c T8/T9/T10 and T11' gets predecessor state wrong: p4c-t8 and p4c-t9 are already in the common base 2c0c5b5. p4c-t11 (8 commits) and p4c-t13 (12 commits) are not in the historical source at all. Only part of T10 (via p4c-t10b) is in it, and its latest work is on p4c-t10c.

> A d2 author following this text treats T8/T9 as integration work and treats T11 as something the source-map delta contains. T11's documentation of T10's fixes and T13's live checks are then never mapped or admitted, or are assumed done because they are listed next to completed tasks.

**Author disposition — §§3,4:** Separate already-landed ancestry, unfinished native predecessor state, and explicitly excluded T11/T13 ports.

## 3. docs/adr/2026-10-16-plan5i-current-main-integration-d1.md:100

Finding SHA256 `e2aff0f6af5fac23588996852ca207d597aec87223418acb4b541573d037932c`.

> The ADR applies a strict 'maximum three rounds per change' and 'old reviews do not reset the counter' (line 39), but never records Liam's authenticated choice A (Marmot ask xjgx). That choice allows exactly one final T10 review as a cap exception, and is neither deployment approval nor a blanket waiver.

> T10 has already used rounds c1-c3. As written, the design either leaves T10 with no route to completion, or an executor uses the exception informally with nothing stating its bounds: one review, T10 only, which T10 (Plan4c, not plan5i-2 T10/T10b), and against which exact candidate. That invites a second exception round or treating it as approval to deploy.

**Author disposition — §§3,5:** Retain actual authenticated xjgx one-review exception and remaining-blocker return rule.

## 4. docs/reviews/plan5i-integration/source-map-d1.json:6

Finding SHA256 `9ad2f720627640ceb257776d9333b184b36d5ea45fd82d6c3191c4d86fe86e88`.

> Work that came after the source pin is dropped without a decision: feat/plan5i-p2 is 4 commits ahead of 50fc42f (a8ebecc journal-backed ResumeNotes, 9dd8ee3 review-notice lint, 6237d46 docs). The plan5i-bridge (14), plan5i-close (11) and plan5i-e2e (9) branches have commits found in neither the source nor main.

> The integration lands part 3 without its accepted follow-ups, including a lead-decision feature and a lint test for operator notice bodies. Nobody decides whether these are in or out of scope, so they are lost or come back later as unreviewed additions.

**Author disposition — §§4:** Explicitly include all three successor followups; recompute plan-branch own deltas and exclude code-free plan imports.

## 5. docs/adr/2026-10-16-plan5i-current-main-integration-d1.md:22

Finding SHA256 `fc8136a601bd47642c4c6c443d1e8d62720e3dd97ef260daefffe165e0c5e460`.

> The ADR says the full base-to-source delta must be mapped to original plans and actual review records before a landing sequence is chosen. The source map has only flat path lists, with no commit, task, plan or review mapping. The ADR then chooses a sequence anyway (lines 52-54).

> 50fc42f is 'plan5i-p3-redo' (part 3 rebuilt on part 2), and the 226 commits interleave p4c-t10c merges into plan5i-p2 and p3-t8 sitting on p2. Without a commit-to-task-to-review map, old review verdicts recorded against the pre-redo commits can be adopted for redo trees they never reviewed. The 'original dispatch order' also cannot be checked against how the history was actually merged.

**Author disposition — §§4:** Provide factual 230-row source inventory plus retained actual review/brief evidence; range discovery never implies acceptance.

## 6. docs/adr/2026-10-16-plan5i-current-main-integration-d1.md:53

Finding SHA256 `29d8a3466ccfd6efe127daf679aa6962618f756ad2b12b4ff0d5c48283a421ea`.

> 'Preserve original dispatch order' is ambiguous because 'T10' names two tasks (Plan4c T10/T10b/T10c and plan5i-2 T10/T10b). The plan5i-2 T10 bridge-flow test was merged as 'strict xfail until T6, T7, T9, T11'.

> The ordering contract cannot tell which T10 must come first. The bridge-flow suite, which the ADR's consumer list requires, may be landed before its prerequisites, or sequenced against the wrong T10.

**Author disposition — §§4:** Disambiguate Plan4c and part2 Task10 and require all original bridge prerequisites and final passing flow.

## 7. docs/adr/2026-10-16-plan5i-current-main-integration-d1.md:81

Finding SHA256 `d3ac7ce2fec5078132d14ba13e745343fd6a6b092874cde5e7c74439e04c03e7`.

> The harness consumer contract has no concrete mechanism. sensitive_kind flags any 64-hex string as a 'hex key', and harness.py:293 scans json.dumps(composed.ask). The ADR says to 'use the existing approved digest representation' but never names it or what the widened scanner will do with it.

> If the widened scanner (unresolved head) also matches the display digest format, or the ask carries a raw sha256 in the effect, refs or Sources lines, every managed install fails with ASK_UNPOSTABLE. The obvious workaround is the global exemption the ADR forbids, and the design gives no pinned test input to show either way.

**Author disposition — §§6:** Pin actual digests functions/format, computed-only callsite trust, full serialized payload tests and human approval for representation changes.

## 8. docs/reviews/plan5i-integration/source-map-d1.json:173

Finding SHA256 `589aa4221b532b5f57297edbf1e8c69f14ab3c77a5df3cf75c76f8fb2821541e`.

> The consumer list was built by grepping for sensitive_kind only. It leaves out wsd/settings.py, a newly present consumer of the scanner module (secret_scan.is_reference and show at settings.py:19-21, 236), and any module that sends text to the relay through notices/asks rather than calling the scanner directly.

> If btq-1wu2i changes show() redaction or is_reference(), settings.py errors and validation change behavior with no 'nonempty actual test selection' recorded for them. That breaks the invariant at ADR line 61 for an unlisted consumer.

**Author disposition — §§6:** Include current settings/accounts and indirect native/relay consumers with actual callsite/source pins.

## 9. docs/adr/2026-10-16-plan5i-current-main-integration-d1.md:62

Finding SHA256 `35cc2eb4f5679a7d40b748a96580fc6c73eb3729ee15174e553d269d58a016cd`.

> The required consumer suite list leaves out tests/test_wsd_refusals.py and tests/test_wsd_notes.py, even though line 69 requires refusal-reason coverage (asks.py:491 REASON_WITHHELD). Line 96 says 'no skipped required test is a pass' without saying whether the BTQ_REPO-gated test_wsd_beads tests (lines 618, 690) are required.

> A focused run follows the list, skips the refusal-withholding regressions, and is still recorded as complete. The env-gated beads tests skip in a hosted run without BTQ_REPO, and that is either wrongly counted as a pass or blocks with no stated rule.

**Author disposition — §§7:** Require refusals/notes plus all seven real-BTQ nodes with honest hosted provenance and skip policy.

## 10. docs/adr/2026-10-16-plan5i-current-main-integration-d1.md:16

Finding SHA256 `6e163eb0b6edaee934d0c11cf6521a1830ca412d8eda1958d60ec1bcc143d6ff`.

> The approval scope for btq-pxtig is given as 'against 076', an abbreviated ref, where everywhere else the design requires exact full source refs (lines 33, 30).

> A later admission check cannot confirm mechanically that the scanner approval's base is 07637745fe75dc614109a0b8ba72d3489f262a97. That weakens the 'treat future HEAD as unreviewed' rule the ADR depends on.

**Author disposition — §§1,2:** Use full original scanner baseline and actual admitted S; no abbreviated ref establishes executable authority.

