# x84mg merge review R7: author dispositions

Actual MEDIUM built-in review of `41bb27cce99d9c848a8669d79541fd392c9ef5ea` found no critical integration flaw and conditioned the source-only PoC on combined suites. The subsequent recorded run passed 2530 tests in 428.91s. This evidence satisfies that test condition only; it is not an APPROVE verdict, native acceptance, activation or deployment authorization. All prior findings and quality/type baseline debt remain OPEN UNWAIVED. No source or test changes accompany this publication.

## Finding 1 — OPEN UNWAIVED

1. `src/heterodyne/admind/fulfillment_projection.py:466`: Now that both workers are composed, the fulfillment worker's synchronous executor.shutdown(wait=True) in its finally block can block the event loop while the custody worker thread is waiting on that same loop for its authority check.

   Failure scenario: During shutdown (or a second cancellation that interrupts the unshielded `await future`), a verify_pass still running (up to about 10s) makes executor.shutdown(wait=True) block the loop thread. A custody permit or message thread inside gateway_custody_control.independent.observe() waits up to 5s for custody_authority to run on that blocked loop, then fails with Invalid('authority-unavailable'). An in-flight custody operation is refused or audited as refused even though authority was valid.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `a1ba5311ee5be3618f9be05ac20e369128e6f6cc8bab5fe1bb8a57b80d68f941`.

## Finding 2 — OPEN UNWAIVED

2. `tests/test_admind_t9r1.py:189`: No test pins the merge result: the only startup supervision test checks only that 'agent' is in the supervised names, so dropping either 'fulfillment-verification' or 'gateway-custody', or reordering them after reconcile_asks, would still pass.

   Failure scenario: A later conflict resolution on this same hunk keeps only one side (exactly the risk this merge carried). The suites pass and the daemon ships with no verification worker (receipts are never re-validated) or no custody worker (queued custody commands are never processed).

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `2f1441c8dd6e6c5796c84b4af9b21617cf0362af86fec59c8cf7d80ad871ee5a`.

## Finding 3 — OPEN UNWAIVED

3. `src/heterodyne/admind/fulfillment_projection.py:452`: The verification worker now starts unconditionally on main, including on stores never migrated with the projection; every pass raises fulfillment-migration-required, which the worker logs as 'fulfillment-verification-unavailable' every 30s indefinitely.

   Failure scenario: A current-main deployment where the explicit quiesced migration was never applied gets a permanent warning every 30s and a wasted read snapshot plus schema validation per pass, which hides real verification outages behind expected noise. Already an open durability/operations finding; the merge onto main re-exposes it.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `fbe3130ec508bdaed02a0824ec5d69df7399c838214d9086265eba49be207162`.

## Finding 4 — OPEN UNWAIVED

4. `src/heterodyne/admind/daemon.py:828`: custody_message catches only Invalid/OSError/sqlite3.Error; any other exception (decode/KeyError/TypeError from a malformed retained bundle) escapes custody_loop, so the supervisor restarts it and the dequeued event is silently lost with only a 'crashed' audit.

   Failure scenario: An operator's `!custody settle <id>` hits a bundle that raises KeyError in Service.message: the event is consumed, no outcome or refusal is recorded under gateway-custody, and the loop restarts. The command is effectively dropped. This comes from main but now runs next to the verifier in the same TaskGroup.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `7bc3bf7c3b748df639cbe53f8f8ca29d232b44dc9e693679b5e8b0e5956f8c96`.

## Finding 5 — OPEN UNWAIVED

5. `src/heterodyne/admind/daemon.py:2565`: on_event intercepts custody commands and queues them on custody_work with put_nowait while the normal work path also queues on handle(); with no dedup by message id, a native redelivery can be processed twice.

   Failure scenario: Native redelivery of the same `!custody decline <id>` message queues it twice; custody_message runs twice under custody_lock. This relies on service-level idempotency, and the code itself notes production enrollment must qualify redelivery. Already an open finding.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `f7eaea035657fd4fb64933a368a1746bed11bf778e08a41399fcb12f7884345d`.

## Finding 6 — OPEN UNWAIVED

6. `src/heterodyne/admind/daemon.py:752`: The function-local import of fulfillment_projection.worker happens inside the TaskGroup after earlier supervised tasks were created; a module-level import (or a bound method like custody_loop) would be simpler and fail before any task starts.

   Failure scenario: An import error in fulfillment_projection appears only after the 11 loops and the socket servers have started, forcing a TaskGroup teardown of live tasks instead of failing cleanly before startup. Also inconsistent with the custody worker added in the same hunk, which uses a module-level import and a bound method.

Disposition: Retained without source/test repair. Source-only PoC integration finding; no clearance, native acceptance or deployment authority inferred.

Verbatim SHA256: `f83ac9e51bc9159f39543e58f1a32f0b50d24f722079c12a02b757100a19b17a`.

The missing startup worker/order assertion remains OPEN. Both worker lines were preserved in the hand-resolved daemon conflict; PV then merged that composition without a further conflict. Review observations do not replace a dedicated regression. Custody P4 entered accepted main through PR131 at `e212ff569ebb7b62ace8003ef7d56229c6b8010e`; its production prerequisites remain separate.
