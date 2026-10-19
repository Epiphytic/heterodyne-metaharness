# X84 repeated cancellation repair

Base: `2125775fe0ea2fee004efb076a2a74359d274ecc`. Root retains the native task claim and review authority. This repair follows the existing review history (at least MED7); it does not reset that history or grant activation.

The worker now repeatedly shields its running executor future while draining after cancellation. Repeated cancellation cannot cancel that future or enter synchronous executor shutdown before it completes. The original cancellation is re-raised after retrieving the outcome. A failing verifier emits only the existing constant warning; exception contents are excluded.

The additive tests exercise an actual worker/executor and a verifier that needs an actual event-loop coroutine callback. They cover success and verifier failure, two cancellation requests, original cancellation arguments, completion, and redacted warning behavior. The verifier callback wait is bounded to two seconds so the predecessor defect cannot hang the test process. This injected verifier is synthetic; it is not live authority evidence.

## Results

- Predecessor with new regressions: 2 FAIL (4.58s), preserved in red.stdout/stderr. The reported failure is replacement of original cancellation arguments by the second cancellation. The bounded callback wait allows the old blocked-loop shutdown to return rather than hang indefinitely.
- First repaired focused run, including unchanged original single-cancellation test: 3 PASS (0.78s).
- Final formatted activation + single/repeated-cancellation compatibility: 43 PASS (10.73s).
- Scoped Pyright: 2 files, 0 errors, 0 warnings. Ruff and format check pass; both changed files compile.
- Quality delta exits 2: worker complexity 17→28 and short-horizon churn 0→4. Both remain OPEN/unwaived. No quality baseline reset or acknowledgment.

The existing cancellation test remains byte-identical, with full AST identity recorded. No original test assertion was changed. Only one runtime file and one additive regression module are candidate changes; candidate.patch is complete. No full all-admind/hosted CI claim is made. A verifier that never finishes can still prevent shutdown; this repair preserves the existing bounded-verifier assumption and does not manufacture cessation or cancel an uncertain effect.

No Git mutation, queue operation, live activation, native DB access, service operation, or reviewer invocation occurred. Root should inspect/sign and select the actual next review ordinal.
