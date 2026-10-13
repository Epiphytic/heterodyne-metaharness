Reviewer: gpt-6.1-sol. Reviewed commit: 724d6677d9660eb4641eb2008b92a178faad9599. Verdict: REVISE.

Note: code-review cycle 1 of 3, range 850d531..724d667.

Reviewed `850d531..724d667` against the design at `5ccb0f3`. No tests run.

- **BLOCKING — healthy stops can become deaths after a failed write.** In [runtime.py](~/repos/heterodyne-metaharness-v2-g08sd-impl/src/heterodyne/sandbox/runtime.py:597), a failed STOPPING write still proceeds with teardown. If the write fails before replacing the record, a live lifetime or intentional stop kills the session without persisting its cause. After recovery, the RUNNING record has no marker or cause, so cleanup records `died`. This can falsely escalate a healthy bead. Preserve durable cause evidence before teardown; add a regression covering write failure followed by restart.

- **BLOCKING — required test 4 lacks its accounting assertion.** [test_a_late_pickup_still_ends_lifetime](~/repos/heterodyne-metaharness-v2-g08sd-impl/tests/test_sandbox_runtime.py:898) verifies the marker becomes `EndCause.LIFETIME`, but never invokes the guard or verifies that an existing crash count resets. The guard test uses `FakeRuntime.lifetime()`, bypassing marker consumption. Add the required marker-to-guard reset coverage.

The reported deviations are **NON-BLOCKING**:

1. Rebuilt entries being `reset` matches the journal-loss recovery path requiring release; the named regression covers it.
2. Both lifetime tests still inject after STOPPING and retain their WIP-once and watcher assertions. They are re-pointed without loosening those checks.
3. Excluding pickup from test 11 is justified: new-claim intent accounts previous generations before the guard. Resume and undefer cover reachable accounting checkpoints.

Verdict: REVISE