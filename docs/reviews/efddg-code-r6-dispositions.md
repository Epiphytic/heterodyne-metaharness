Actual SIXTH MEDIUM verdict REVISE; no ordinal reset or claimed gate pass.

1. Fixed critical decoy recovery bypass. Journal rejects multiple recovery observations and record-kind mismatch before evaluating clock-discontinuity flags. Six actual fail-first regressions reproduced bypass with/without configured sampler, then passed.
2. Fixed parent resource release with live reconciliation child. Parent terminal dispatch transaction checks every retained reconciliation child before releasing resources; refusal rolls back parent write and preserves resources. Actual fail-first regression reproduced release, now passes. Request completion still requires settled parent, so cannot bypass this journal fence.
3. Retained: ordinary allocation/reconciliation lookup timing uses supplied typed observation clock, not configured sampler. Discontinuity recovery uses the injected sampler. All observations are synthetic private fixture facts; no live adapter or authenticated clock capability exists. Do not claim real elapsed-time authority.
4. Retained: initial dispatch and allocation-intent progress may use terminal reserve. Hard event cap remains enforced; this reduces finite recovery headroom and is not an indefinite-availability guarantee.
5. Retained: contradictory reconciliation success after definitive absence is not rejected. No live effect can be launched by this fixture journal, but real adapter composition must reject contradictory native evidence before reliance.
6. Retained: unused recovery metadata can decorate other terminal paths; no actual Marmot authentication is claimed for synthetic fixture records. Do not treat operator-event strings as approval evidence.
7. Retained: missing explicit recovery clock anchor can differ from latest retained lookup; journal and pure settler still validate different defaults. Requires explicit current anchor for integration.
8. Retained: request transition operation routing remains proposal-shape based, though dispatch now uses explicit tags. Finite reducers are journal-revalidated.
9. Retained: bounded event/history scans and reservation checks have N+1 cost. No production throughput claim.
10. Retained: terminal marker charging after owner removal may consume unreserved capacity; fixed total cap remains enforced and full history holds. No archival/pruning behavior added.

No deployment or live authority; all five adapters unavailable. Full original Pyright46 fixture errors and prior quality48gating90rows remain unwaived. Latest quality delta is measured separately against R5 HEAD and does not clear prior debt. Request another actual MEDIUM critical-flaws/PoC review; do not manufacture an approval.
