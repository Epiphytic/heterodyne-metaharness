> Imported copy: local paths were normalised (`<repos>/`, `~`). The canonical, digest-pinned record is the design repo at commit `e36f6d0` (bead `btq-96hm`).

REJECT

**R1 findings**

1. Resolved. The ADR specifies the `wsd` queue identity as an operator setup step and adopts btq’s worktree convention.
2. **Unresolved [BLOCKING].** Parking keeps claims under one stable `wsd` worker, but current [`Queue.claim()`](<repos>/beads-task-queue/bin/btq:123) rejects a new claim while that worker has *any* in-progress bead. The first parked bead stops pickup of further work. Specify the required btq change and its ownership rules.
3. Resolved. Action approval now closes only after successful execution.
4. Resolved. The sandbox boundary and launch checks are specified, with model-credential exposure documented as residual risk.
5. Resolved. Actions have typed payloads, a separate effector, and revalidation.
6. **Unresolved [BLOCKING].** The decision commits to SQLite before Beads. If the Beads write fails, the two stores disagree; §3.3 gives no reconciliation rule for that gap or for journal loss. “First decision wins” is therefore not durable as written.
7. Resolved. The ADR specifies authenticated sender and group checks, allowlisting, replay checks, and membership-change handling.
8. Resolved. The outage exception is limited to locally classified sandbox-confined calls.
9. **Unresolved [BLOCKING]** for the decision record gap in finding 6. The narrowed state-ownership claim resolves the other records.
10. Resolved. The [response](<repos>/hermes-workstreams-v2/docs/reviews/0001-design-review-r1-response.md) explicitly records the operator’s decision and the residual risk: authenticated operator-message access grants `<operator-user>`-level host control. The operator owns that trade-off.
11. Resolved. Review evidence is tied to launched sessions and the reviewed HEAD.
12. Resolved. Missing delivery receipts have an alert and relay path.
13. Resolved. The ADR defines a v1 slice and separate phase-2 gates.

**New issues**

- **[BLOCKING]** [§5.3](<repos>/hermes-workstreams-v2/docs/adr/0001-workstreams-v2.md:234) says `wsd` automatically pushes branches on park and close. This conflicts with its own operator-only push rule and [PICKUP.md](<repos>/beads-task-queue/docs/PICKUP.md), which forbids automatic pushes. Define one authorized push path before implementation.
- **[NON-BLOCKING]** The ADR’s `kind:confirm` bead has no stated queue routing. Current [btq routing](<repos>/beads-task-queue/bin/btq:77) accepts only `brainstorm`, `task`, `review`, and `research`; clarify whether confirmation beads are deliberately excluded from pickup.