No critical blockers. The change is good enough to implement as a PoC.

I reviewed the frozen range `21ede04..e005d87` in `${OPERATOR_HOME}/repos/heterodyne-exact-session-reconcile-20261103`. It touches `src/heterodyne/sandbox/runtime.py` and `tests/test_sandbox_runtime.py`. I ran the new test plus the existing reconcile tests with `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sandbox_runtime.py -k "preflight_is_readonly or reconcile"` and all 21 passed. I made no edits, commits or messages. I did not run the full runtime suite.

What holds up:
- **Side effects:** the receipt write is the only durable write before the new `READY` return, and preflight now skips it.
  - `_held`, `_claim_branch(settle_wip=False)`, `_names`, `_alive` and the `recovery_evidence` functions only read.
  - The ending and WIP steps (`_end`, `_settle_wip`) run only after the early return.
- **Owner checks:** preflight runs under the same lifecycle lock and runs every owner check and record-match check that reconcile does, up to the point where it returns.
- **Partial receipts:** when a receipt already exists, preflight checks it the same way reconcile does and writes nothing.

Everything below is advisory; none of it blocks the PoC.

8 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:679`: Preflight returns READY for an ENDED record without the resource check (`_running`) or the WIP-owed check that the real ENDED path does, so READY can be wrong.

   Failure scenario: Receipt retained, record ENDED, but a sandbox or reaper of the generation is still running. Preflight says READY, the caller fences, and reconcile_session then fails with 'the ended generation still has resources'. The fence was taken for an ending that can't finish.

2. `src/heterodyne/sandbox/runtime.py:679`: An operation that is already fully ENDED reports READY, so the caller can't tell 'nothing left to do' apart from 'ending still needed'.

   Failure scenario: Retry after a successful reconcile: the record is ENDED with no WIP or resources left. Preflight returns READY instead of a done state, so Hermes takes a recovery fence and goes through admission again only to get ENDED back.

3. `tests/test_sandbox_runtime.py:3236`: The new test covers only the no-receipt states (live, deadline, missing-marker). Preflight is not tested with a partial receipt (receipt retained, record STOPPING or ENDED), which is the case the review asked about.

   Failure scenario: A later change makes the retained-receipt branch write, or return the wrong state, when preflight is set. No test fails, because none calls preflight after a partial `_end`.

4. `tests/test_sandbox_runtime.py:3257`: The read-only guard patches only the runtime module's `write_private` and `_end`. Other write paths (`_settle_wip`, `_phase`, `gitwip`, `_write_intent`) aren't blocked, and the test doesn't check that the record file is left unchanged on disk.

   Failure scenario: Someone adds a `_phase(...)` or `_settle_wip` call to the shared path before the `if preflight` return. It writes the record through a path the patch doesn't cover. The test only compares record bytes in the states it runs, so a write in an ENDED or receipt state goes unnoticed.

5. `tests/test_sandbox_runtime.py:3266`: Nothing checks the stated contract end to end: after READY, reconcile_session with the same original still ends the session (ENDED). No test changes the owner or the record during preflight either.

   Failure scenario: Preflight's in-memory receipt and reconcile's freshly built receipt drift apart, for example in the captured pane tail or validation. Preflight still says READY but the real ending refuses, and no test catches it.

6. `src/heterodyne/sandbox/runtime.py:661`: The read-only guarantee depends on a boolean flag passed through a function that writes. It holds only while every future durable write is either placed after line 679 or wrapped in its own `if not preflight` check.

   Failure scenario: A later maintainer adds a cleanup or journal write between lines 663 and 678 and forgets the flag, so preflight silently writes. Splitting the function into a read-only observe step (returning current, evidence, retained) and a separate act step would make this impossible by design.

7. `src/heterodyne/sandbox/runtime.py:653`: On the no-receipt path, preflight still captures 40 lines of pane output and builds the full receipt only to check it. The tail is never used, and the real reconcile captures it again.

   Failure scenario: Each preflight makes an extra tmux capture round-trip and re-encodes the receipt. Only the marker and pane evidence are needed to report READY.

8. `src/heterodyne/sandbox/runtime.py:609`: Preflight errors are mislabelled. Errors raised inside the shared code say 'reconciliation ...' even when they come from preflight, while wrapped OS and launch errors say 'exact deadline preflight is held'.

   Failure scenario: An operator or Hermes log shows 'reconciliation requires the actual manager owner' from a read-only preflight. That reads as if an ending was attempted, which muddies diagnosis of liveness findings.

No CLAUDE.md rule is broken by this diff.

No critical blockers. The change is good enough to implement as a PoC.

I reviewed the frozen range `21ede04..e005d87` in `${OPERATOR_HOME}/repos/heterodyne-exact-session-reconcile-20261103`. It touches `src/heterodyne/sandbox/runtime.py` and `tests/test_sandbox_runtime.py`. I ran the new test plus the existing reconcile tests with `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sandbox_runtime.py -k "preflight_is_readonly or reconcile"` and all 21 passed. I made no edits, commits or messages. I did not run the full runtime suite.

What holds up:
- **Side effects:** the receipt write is the only durable write before the new `READY` return, and preflight now skips it.
  - `_held`, `_claim_branch(settle_wip=False)`, `_names`, `_alive` and the `recovery_evidence` functions only read.
  - The ending and WIP steps (`_end`, `_settle_wip`) run only after the early return.
- **Owner checks:** preflight runs under the same lifecycle lock and runs every owner check and record-match check that reconcile does, up to the point where it returns.
- **Partial receipts:** when a receipt already exists, preflight checks it the same way reconcile does and writes nothing.

Everything below is advisory; none of it blocks the PoC.

8 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:679`: Preflight returns READY for an ENDED record without the resource check (`_running`) or the WIP-owed check that the real ENDED path does, so READY can be wrong.

   Failure scenario: Receipt retained, record ENDED, but a sandbox or reaper of the generation is still running. Preflight says READY, the caller fences, and reconcile_session then fails with 'the ended generation still has resources'. The fence was taken for an ending that can't finish.

2. `src/heterodyne/sandbox/runtime.py:679`: An operation that is already fully ENDED reports READY, so the caller can't tell 'nothing left to do' apart from 'ending still needed'.

   Failure scenario: Retry after a successful reconcile: the record is ENDED with no WIP or resources left. Preflight returns READY instead of a done state, so Hermes takes a recovery fence and goes through admission again only to get ENDED back.

3. `tests/test_sandbox_runtime.py:3236`: The new test covers only the no-receipt states (live, deadline, missing-marker). Preflight is not tested with a partial receipt (receipt retained, record STOPPING or ENDED), which is the case the review asked about.

   Failure scenario: A later change makes the retained-receipt branch write, or return the wrong state, when preflight is set. No test fails, because none calls preflight after a partial `_end`.

4. `tests/test_sandbox_runtime.py:3257`: The read-only guard patches only the runtime module's `write_private` and `_end`. Other write paths (`_settle_wip`, `_phase`, `gitwip`, `_write_intent`) aren't blocked, and the test doesn't check that the record file is left unchanged on disk.

   Failure scenario: Someone adds a `_phase(...)` or `_settle_wip` call to the shared path before the `if preflight` return. It writes the record through a path the patch doesn't cover. The test only compares record bytes in the states it runs, so a write in an ENDED or receipt state goes unnoticed.

5. `tests/test_sandbox_runtime.py:3266`: Nothing checks the stated contract end to end: after READY, reconcile_session with the same original still ends the session (ENDED). No test changes the owner or the record during preflight either.

   Failure scenario: Preflight's in-memory receipt and reconcile's freshly built receipt drift apart, for example in the captured pane tail or validation. Preflight still says READY but the real ending refuses, and no test catches it.

6. `src/heterodyne/sandbox/runtime.py:661`: The read-only guarantee depends on a boolean flag passed through a function that writes. It holds only while every future durable write is either placed after line 679 or wrapped in its own `if not preflight` check.

   Failure scenario: A later maintainer adds a cleanup or journal write between lines 663 and 678 and forgets the flag, so preflight silently writes. Splitting the function into a read-only observe step (returning current, evidence, retained) and a separate act step would make this impossible by design.

7. `src/heterodyne/sandbox/runtime.py:653`: On the no-receipt path, preflight still captures 40 lines of pane output and builds the full receipt only to check it. The tail is never used, and the real reconcile captures it again.

   Failure scenario: Each preflight makes an extra tmux capture round-trip and re-encodes the receipt. Only the marker and pane evidence are needed to report READY.

8. `src/heterodyne/sandbox/runtime.py:609`: Preflight errors are mislabelled. Errors raised inside the shared code say 'reconciliation ...' even when they come from preflight, while wrapped OS and launch errors say 'exact deadline preflight is held'.

   Failure scenario: An operator or Hermes log shows 'reconciliation requires the actual manager owner' from a read-only preflight. That reads as if an ending was attempted, which muddies diagnosis of liveness findings.

No CLAUDE.md rule is broken by this diff.
