# Scanner planning continuation and admission proposal

Status: proposed, not implementation authorization. Task btq-18sfj currently
owns planning only. Its controller retained the stopped-worker recovery audit;
this publication neither changes task ownership nor adds native dependencies.

Inputs and exact source pins are in
[the manifest](../../reviews/scanner-boundary/frozen-artifacts.json); design and
review limitations are in
[the reconciliation](../specs/2026-10-16-scanner-boundary-reconciliation.md).

The distinct proposed current-main alternative is now specified in
[integration addendum d1](../specs/2026-10-16-scanner-current-main-integration-d1.md).
It is pending its own bounded integration review and human decision; neither
publication supersedes the historical prerequisite without that actual admission.

## Recommended continuation

Preserve design d3, plan r5, historical ask r5 and all six historical review
reports unchanged. Publish these portable reconciliation documents while keeping
the complete historical evidence available in the durable handoff. Record any
new operator decision against the exact new proposal and frozen-input hashes;
never backdate approval to r4 or r5.

Retain the frozen plan's plan5i integration prerequisite. The controller should
identify the existing plan5i integration/approval tasks, verify their actual
authority and dependencies, and reconcile their independently reviewed commits
with current guard/create-lifetime main before scanner implementation. This is
separate integration work; this task does not merge that substantial branch.

An alternative is an explicitly scoped plan amendment permitting scanner work on
current main before plan5i. That changes the frozen plan's base and consumer/test
assumptions. It needs honest admission and applicable independent review/human
approval; clean dry-apply results are insufficient. Do not disguise that amendment
as a documentation correction, or reset the historical review counters to obtain
a fourth review of the same change.

The controller must resolve the final-r5 review discrepancy without manufacturing
an approval: record the exact independently reviewed r4 and subsequent r5 delta,
then obtain the applicable genuine operator disposition under the review policy.
If policy requires a further independent review that the exhausted round limit
does not permit, hold and escalate that explicit policy conflict. This document
does not grant an exception.

## Execution boundary after actual approval

The existing btq-18sfj record describes the scanner defect and says design approval
is needed before implementation; its recovered planning snapshot has no native
dependency edges. That snapshot cannot establish whether a separate implementation
task already exists elsewhere. Only the controller may inspect the queue and
choose an existing implementation task or create one linked to this planning work
and a real approval blocker. Do not duplicate an existing task or convert this
planning claim into implementation silently.

Once admitted, use an isolated receipt-bound checkout at the actually approved
base. Extract and hash-check Appendix A and Appendix C from the frozen plan.
Reconcile both Appendix B patches while preserving guard fixture changes. Keep
the scanner and redactor changes atomic. Re-measure exact red-node sets on that
base before relying on Appendix C: historical counts are evidence, not guarantees
about a changed tree. Red execution must distinguish assertion failures from
collection/setup/runtime failures. Run approved targeted and full offline gates,
retain actual exit codes/logs, and exclude live backends. Interpreter/environment
selection must meet the actual approved test contract; the old shell paths are
historical locations, not authority to create or overwrite unrelated worktrees.

Keep scanner before the install-check task btq-4lsqy, then btq-b7oa3, btq-oaw8k,
btq-h6y9w and btq-qdyj7 as the requested critical chain. The controller must verify
actual scope/dependencies before recording edges. The frozen scanner plan says
4lsqy has no textual overlap; it still requires the semantic superset/install-check
checks when both changes have landed.

Task 4's stored-marker skip is conditional on actual source at implementation
time. If inbox/event scrub code lands first, retain the frozen stop rule and obtain
the required concrete amendment rather than guessing symbol names or skipping a
necessary version bump. The separate install-check and case-sensitivity follow-ups
remain explicit residuals, not work approved by this scanner plan.

## Proposed operator request

Approve the frozen scanner behavior in design d3 and the precisely identified
execution scope only after resolving the plan-base and final-r5 review questions
above. The historical ask's 7419-pass result and zero-new-match measurements refer
to its historical branch/scratch runs; they are not validation of current main.

The fresh request should state the chosen base/landing order, source and document
hashes, remaining scanner gaps and false-positive costs, the status of the r5
mechanics delta, required offline gates and independent code review, and whether
PR publication/merge and deployment are included. The controller creates the real
approval issue/block edge and sends/records an authenticated Marmot decision.
This proposed text is not an approval card, signed operator grant, approval digest
or permission to implement.
