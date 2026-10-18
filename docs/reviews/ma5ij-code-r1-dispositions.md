# ma5ij first HIGH review dispositions

Reviewer: Claude Opus builtin `/code-review`; author: Codex; exact original range
`597a1d4aec3d4fc081e5774ff3ffc7aa33aa16be..e8bf0b94ef77c49f3bc89b7231e407d908b62c5a`.
Actual verdict REVISE. Raw ten findings are retained verbatim in `ma5ij-code-r1-high.md`.
No approval is inferred from this disposition document. The approved design remains
`e613fd9e91acd4263e9871b402049b338171924a`, native approval btq-ev9kk / Marmot uyqm.

1. Fixed: legacy posts of any kind cannot attach to a source-owned bead. Historical questions and
merges do not belong to approval lineage and cannot poison it. Both orders have regressions.
2. Fixed: a withdrawn stale attempt closes cancelled and completes its original inbound when the
caller already has positive preflight refusal or untouched native readback. No replacement is posted.
3. Fixed: a withdrawal observed at the final issuing fence settles the unissued attempt instead of
leaving deciding forever. A pending-maintenance retry reads native first after withdrawal: recorded
results reconcile normally, untouched results cancel, unreadable/conflicting results retain the hold.
Withdrawal itself never proves absence. Native issuance is never retried through a tombstone.
4. Fixed: fulfillment-protected rows retain their original answer while the operation tombstone and
cancellation of other open lineage rows commit. Repeated withdrawal does not add duplicate transitions.
The focused protected-row test exercises the existing protection predicate seam; existing fulfillment
compatibility modules exercise real lifecycle records. It is not a new lifecycle integration proof.
5. Fixed: replacement lineage admission is checked before stale settlement; capacity/incomplete lineage
refuses only the descendant, while the positively stale original attempt and inbound settle atomically.
6. Fixed: recognized operation requests receive the closed operation Reply on validation, handler and
reply-capacity failure. The actual disposable private Unix socket test checks UUID, missing binding,
unknown fields and duplicate fields, without sending anything to Marmot.
7. Fixed: enrollment of existing approval history bypasses creation-only capacity/latch checks. New
card allocation still uses the original creation gate; enrollment adds no card or outbox message.
8. Fixed: source operation creation emits the same audit_posted record, including superseded IDs,
after the successful store transaction.
9. Fixed: requests reuse approvals.BEAD_ID. Existing valid `abc-1.2` / `a-1` identifiers are accepted,
and identifiers outside that exact existing grammar are refused.
10. Fixed: one typed full-row approval lineage query replaces per-row lookups; status batches links,
answers, attempt references and outbox keys. The forced outbox-outer CROSS JOIN scans outbox once and
looks up indexed lineage IDs. Existing part/reply bounds and completeness checks remain. A 21-row
regression previously made 107 SELECTs and now requires fewer than 16. This is a query-count regression,
not an end-to-end latency claim; total outbox scan cost still grows with retained outbox history.

## Original oracle disposition

All original test functions remain, with no skips or removed assertions. One assertion in
`test_refresh_after_withdraw_does_not_emit_descendant` expected the exact permanent deciding defect
from finding 2. It now requires cancelled, cleared attempt and withdrawn result. Its outbox expectation
allows exactly one original-attempt completion reply, rather than forbidding that required settlement.
The before-review whole test module SHA256 is
`fc9234ff7ae6846118dad9412ac4fdbd64a29f468cd7c9f4a40190bdfbf0e6eb`;
original bytes are retained in controller evidence `ma5ij-r1-original-test-admind-operations.py`.
Raw actual red logs, initial fixture mistakes and corrected counterexamples remain in controller evidence.
The invalid-module test invocation collected no tests and is retained separately, never counted as proof.

## Limits

This remains the server-only offline PoC. Same UID is trusted correlation, not cryptographic source
identity. Unknown issuance remains held, and genuine native consent and current membership are retained.
No Store/Journal/HarnessOps client integration, deployment, live native write or outbound send occurred.
Quality regressions remain UNWAIVED, with no baseline or acknowledgement changes. Source-level query
batching does not attest runtime latency or complete end-to-end operation delivery.

## Final repair validation

Ten named compatibility modules, including 15 new regression cases, pass: 428 tests in 91.56s.
Full configured Pyright: 0 errors, 0 warnings. Changed Python paths pass Ruff; the whole-repository
Ruff scan retains 12 F811 findings in unchanged tests/test_wsd_harness_r5.py (byte identical to e8bf).
Those existing findings remain UNWAIVED. Incremental quality versus e8bf reports 60 regressions,
38 gating, 0 acknowledged; original source quality debt also remains UNWAIVED. No clean quality
or whole-repository style result is claimed. New test formatting and git diff checks pass.
