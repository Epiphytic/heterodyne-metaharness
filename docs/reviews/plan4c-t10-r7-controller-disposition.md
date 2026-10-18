# T10 actual seventh review: controller escalation

Reviewed source: `117b28f2bb78114221279cb5dc5ff497588297aa..46fcd0363e46097c4af54c77c0cc6d99ff47dbf6`.
Actual Claude Opus builtin `/code-review`, MEDIUM, returned REVISE. Raw SHA256 `f53848891dc94859a224151e6463170ab5e079251fd1d1290433a8900b52118d`. This escalation follows the operator-authorized post-third human decision route; no reviewer APPROVE is claimed. Four historical reviews and actual Claude reviews five through seven remain preserved.

## Requested decision

Approve merging this source as an undeployed PoC despite the recorded critical findings, permitting dependent offline integration. This is not deployment approval, operational acceptance, or permission to activate managed install publication. The unchanged source has 584 named tests passing with four optional skips, configured strict source typing zero errors, Ruff/hooks/diff passing. Quality 15 gates/28 rows remains UNWAIVED.

## Disposition of all ten findings

1. ACCEPTED, critical: unknown/wrong-name store publication can permanently hold an adapter. Original Task10 has no operator recovery operation and explicitly forbids approval-bead mutation after create. A separate design amendment must introduce supported inspection and authenticated recovery/quarantine before publication activation. Timeout or manual DB edits are prohibited.
2. ACCEPTED, critical: ASK_UNPOSTABLE conflates an unknown native/card post outcome with a positively refused post. Before activation, preserve a truthful outcome and supported correlation/reconciliation so a live card is not represented as absent. A terminal row already cannot publish on a later approval, but duplicate cards remain possible.
3. ACCEPTED: verify/taint alert concurrency needs a shared bounded alert critical section before activation, preserving the R6 no-network-wait taint path.
4. ACCEPTED: selectors for non-published entries require state-aware resolution before activation; do not expose package text that was withheld.
5. ACCEPTED: inline fetch/approval work can monopolize HARNESS_LANE. Bounded scheduling and responsiveness need measured acceptance before activation.
6. ACCEPTED: subprocess timeout must cover inherited pipes/process groups before claiming a hard Git deadline.
7. ACCEPTED: ignored unchecked bytecode is a provenance limitation. A clean Git source commit alone does not attest arbitrary executable caches; activation needs a controlled interpreter/source closure.
8. ACCEPTED: staged-row/close-all JournalBusy startup isolation is incomplete and needs repair before activation.
9. RETAINED noncritical performance debt: snapshot in-use inputs once per list/prune rather than repeat scans under locks.
10. RETAINED noncritical cleanup: remove dead worker state and update documentation when scheduling/outcome behavior is repaired.

## Guardrails and next work

All live publication, selection changes, service/timer installation and deployment remain gated. Keep store/journal history and real approval evidence; never clear unknown effects by timeout. A new reviewed design will be published for operator approval for the operational recovery gap. Its eventual deployment request must link that design and list deviations. Dependent Plan8 integration must preserve HARNESS_LANE isolation and must not rearm scheduler pickup from a harness command. No claim that this PoC releases v2 or begins the 72-hour acceptance period.
