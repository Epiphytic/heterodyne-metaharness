# pvbxr merge review R12: author dispositions

Actual MEDIUM built-in review of `d086cf585e1b6f1aa9d905ccdeb5a03159c7b246` found no critical integration flaw and conditioned the source-only PoC on combined suites. The subsequent recorded run passed 2758 tests in 431.68s. This evidence satisfies that test condition only; it is not an APPROVE verdict, native acceptance, activation or deployment authorization. All prior findings and quality/type baseline debt remain OPEN UNWAIVED. No source or test changes accompany this publication.

## Finding 1 — OPEN UNWAIVED

1. `tests/test_admind_t9r1.py:190`: The only hand-resolved conflict (both fulfillment-verification and gateway-custody supervised workers before reconcile_asks) has no regression test; test_run_supervises_the_agent_loop already records every supervised name but only asserts 'agent'.

   Failure scenario: A later merge that drops or reorders either worker line again (the exact conflict seen in 41bb27c) leaves custody or verification dormant at runtime while CI stays green; asserting {'fulfillment-verification','gateway-custody'} <= set(names) would catch it.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `bfa81628e50e3a765eef57f6e11220b26441783006aef2167836ba87f98d44af`.

## Finding 2 — OPEN UNWAIVED

2. `src/heterodyne/admind/daemon.py:767`: Shutdown closes ask_server, recovery_server and custody_server one after another, each waiting up to 2*CLOSE_SECONDS; custody handlers block cancellation on a shielded to_thread worker that can still be waiting in observe() for a loop-side check_group.

   Failure scenario: SIGTERM while a custody permit/settle is in flight: handler tasks are cancelled and aborted after the timeouts, run() returns, and _stop_child stops the gateway child while the custody thread is still running; observe() then times out (5 s) with authority-unavailable, and total shutdown time grows with the extra server (existing shutdown/cancellation OPEN finding, widened by the composition).

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `31500087638ef0aa33cc562d16ea85b6a52c6ed0925e4da61f990fa64e73ae3d`.

## Finding 3 — OPEN UNWAIVED

3. `src/heterodyne/admind/daemon.py:841`: custody_message catches only gateway_custody.Invalid, OSError and sqlite3.Error; errors from a malformed retained bundle (KeyError, json errors) or X84's new Store ValueError('read-only-transaction') escape and crash custody_loop.

   Failure scenario: A corrupted gateway_custody_proposals.bundle makes bundle['descriptor'] raise KeyError: the event is lost with no 'gateway-custody' audit outcome (only a generic task crash), and the supervisor restart's doubling backoff delays the up to 256 queued custody commands.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `847e1277ae9185d8c2b93bb1088d07af9edce817bd76bb0bc2073c1ccc6447d5`.

## Finding 4 — OPEN UNWAIVED

4. `src/heterodyne/admind/gateway_custody.py:902`: Custody worker threads hold the shared Store RLock for whole register/command transactions, while the event loop makes synchronous store calls; with the X84 verification write-back and ask handlers, three parties now contend on one lock.

   Failure scenario: A slow custody transaction (large bundle, slow disk) blocks the loop thread at its next store.get/enqueue, stalling inbound, outbox, hooks and the ask socket for that time (existing performance OPEN finding, made more likely by the composition).

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `80745517084f20773930723ac446783ea0f961ddfa7229a9123da3f7250fd50b`.

## Finding 5 — OPEN UNWAIVED

5. `src/heterodyne/admind/daemon.py:2662`: Custody commands are routed straight from on_event to custody_work, skipping the single ordered worker/work_lock that R27/B7 rely on for operator messages and membership transitions.

   Failure scenario: An operator sends a message and then '!custody settle <token>'; the settle can be processed before the earlier message or while a transition waits on work_lock, so custody decisions are not ordered with other operator inputs (authorised() still refuses while 'changing' is set).

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `b4834d0b1f9ca1d8e09230838f748b87505e2af0b8c9657f3f690063c01ec525`.

## Finding 6 — OPEN UNWAIVED

6. `src/heterodyne/admind/fulfillment_projection.py:66`: The fq2_asks_update trigger's ASK_FIELDS leave out PV's new presentation/render_version/management columns, so changes to management metadata never bump the fulfillment generation.

   Failure scenario: An out-of-band UPDATE that sets management metadata on an already verified legacy ask doesn't mark it dirty; the verification row stays 'valid' for that generation. The capacity predicate still re-checks LEGACY_PRESENTATION live, so the effect is limited to stale diagnostics and inspect output.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `9763ec349d2ea136e8c621a5f2f8d3830ef00da676b860c6add685697a754f91`.

## Finding 7 — OPEN UNWAIVED

7. `src/heterodyne/admind/daemon.py:846`: Custody events still queued in custody_work when shutdown cancels the task are dropped without any audit record.

   Failure scenario: Operator custody commands queued at SIGTERM disappear silently; nothing records them as refused or unprocessed. Recovery depends entirely on native redelivery, which the code comment says is not yet qualified.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `b21311ab86f560a749e06d36281f882c528c3d9034dea818922abcad43c8880d`.

The missing startup worker/order assertion remains OPEN. Both worker lines were preserved in the hand-resolved daemon conflict; PV then merged that composition without a further conflict. Review observations do not replace a dedicated regression. Custody P4 entered accepted main through PR131 at `e212ff569ebb7b62ace8003ef7d56229c6b8010e`; its production prerequisites remain separate.
