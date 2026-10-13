Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 5749a592d956bea36aebd3e689ebf9d792e2637f (range 543c9e3b5af9b4597c78caa6400e35e0370ce465..5749a592d956bea36aebd3e689ebf9d792e2637f). Verdict: APPROVE.

Note: the reviewer was asked to judge the author's rewrite of the plan's beads-outage test and found it preserves the intended proof.

No blocking or non-blocking findings.

The diff implements Task 9 within ADR r16. Capability gates prevent disabled limit handling and account switching; duplicate/stale reports and replay preserve single deferral handling.

The rewritten outage test preserves the intended proof: the report is drained during the outage, retained, then handled once on the next pickup without another report. Queueing before the patch fixes the helper’s setup failure without weakening that proof.

Validation: 406 offline tests passed; pyright, ruff, and diff checks passed. Additional checks passed for batch ordering, signals arriving during handling, retention bounds, and reset clamps. No repository files changed.

Verdict: APPROVE