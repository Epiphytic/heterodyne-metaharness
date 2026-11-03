# Actual first review and repair disposition

The genuine built-in `/code-review` completed at 21:16:11Z, exit 0, with 43 stream events. It exposed `claude-opus-5-5`; HIGH was set by valid CLI/settings/environment and the reviewer stated the recipe was high. Internal effective effort was not exposed. Published start metadata substitutes `${OPERATOR_HOME}` only; original unmodified runner/start/full stream/stderr/report/result remain in the durable host handoff directory. Reviewer independently ran all ten original reconciliation cases successfully. Actual verdict: no critical safety blockers in bounded source, with one major I/O contract finding to fix before wiring, three moderate findings and six advisories. This is not represented as an unconditional approval.

Repairs incorporate the actual findings:

1. Public API now wraps filesystem and branch-gate errors as typed RuntimeUnavailable holds, including receipt/STOPPING/WIP/read errors. Crash tests require typed holds rather than allowing raw OSError.
2. Existing readonly resolve-intent and other-key branch/WIP guards run before receipt or end effects. No unrelated WIP is settled.
3. A durable exact death receipt can continue after positively observed pane absence. If a pane exists, its exact ID/PID/death/exit evidence must equal the retained receipt; any live/replacement/uncertain pane holds. Added a receipt-only crash followed by pane removal.
4. An ending performed by another path without a retained receipt remains a hold. No consumed marker/death proof is fabricated from phase alone. Hermes must serialize its owner operation and preserve fences; an independently normally ENDED record can be qualified through the existing separate ended-resume preflight. This bounded API does not claim historical adoption of unreceipted ends.
5. Exact serialized identity and current-clock validity remain conservative holds. Schema migration across a live receipt requires a separately qualified successor; clock rollback waits rather than relaxing trust. Neither is a live deployment assumption.
6. LIVE now rechecks owner and complete retained record. Reporting LIVE while demonstrably live, including the small reaper mark/kill interval, is intentional: this API never kills a live executor.
7. Additional cases cover foreign sandbox/extra marker fields, pane loss, identity mismatch, resolve hold, sibling branch conflict, owner loss, and a live pane despite a missing backend listing. Remaining negative receipt/landing combinations are noncritical coverage opportunities, not claimed tested.
8. One shared closed pane-pattern constant is used by tmux observation and receipt validation. The older general lifetime parser remains unchanged for compatibility; only the new strict reconciliation uses the bounded exact marker contract.
9. Evidence construction moved into the evidence module; redundant identity tests after the local owner guard were removed.
10. `_end` alone journals lifetime STOPPING/WIP. The exact already-validated deadline evidence is passed through its private implementation parameter, preserving authentic cause after the original marker was consumed. Other callers retain existing cause derivation. No separate public end override is added.

This is source repair before the second full HIGH review, not a validation-only rerun or a reset of PR160/161 history. No deployment or native/task/input/UNKNOWN mutation occurred.
