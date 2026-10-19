I found no critical integration flaw in the merge. It is good enough to proceed as a PoC, provided the combined all-admind suites now running come back green. That condition is still open: I ran no tests, as the review was read-only.

**What I checked**
- **Conflict scope:** only `src/heterodyne/admind/daemon.py` changed on both sides since b4b8c75. The one conflict hunk keeps both startup workers exactly as each side had them, inside the TaskGroup and before `reconcile_asks(snapshot)`:
  - the fulfillment-verification worker, supervised with `base=30, cap=30`;
  - the gateway-custody worker, supervised with default backoff.
- **Shutdown:** the custody socket open/close and dormant custody intake (the `on_event` and `handle` intercepts, with authority checks before queuing) come through unchanged from main.
- **Fulfillment side:** the notice-authority hook, the Get/Inspect handling, the ask capacity checks and the `!fulfill` changes are all intact. Custody commands (`!custody …`) and `!fulfill` can't overlap.
- **Schemas:** the custody table touches no ask or fulfillment tables, so it doesn't trip the fulfillment schema checks or triggers. The custody authority callback runs outside any store transaction, so it can't deadlock with the verifier's snapshot or write.
- **Review records:** the merge adds no new authority and does not touch the review docs, so the earlier HIGH/MEDIUM findings stay open. I did not audit every disposition file.

The only correctness candidate is a shutdown interaction that I'm unsure of. The rest are coverage and cleanup items, plus earlier open findings that the merge exposes again.

6 findings, most severe first:

1. `src/heterodyne/admind/fulfillment_projection.py:466`: Now that both workers are composed, the fulfillment worker's synchronous executor.shutdown(wait=True) in its finally block can block the event loop while the custody worker thread is waiting on that same loop for its authority check.

   Failure scenario: During shutdown (or a second cancellation that interrupts the unshielded `await future`), a verify_pass still running (up to about 10s) makes executor.shutdown(wait=True) block the loop thread. A custody permit or message thread inside gateway_custody_control.independent.observe() waits up to 5s for custody_authority to run on that blocked loop, then fails with Invalid('authority-unavailable'). An in-flight custody operation is refused or audited as refused even though authority was valid.

2. `tests/test_admind_t9r1.py:189`: No test pins the merge result: the only startup supervision test checks only that 'agent' is in the supervised names, so dropping either 'fulfillment-verification' or 'gateway-custody', or reordering them after reconcile_asks, would still pass.

   Failure scenario: A later conflict resolution on this same hunk keeps only one side (exactly the risk this merge carried). The suites pass and the daemon ships with no verification worker (receipts are never re-validated) or no custody worker (queued custody commands are never processed).

3. `src/heterodyne/admind/fulfillment_projection.py:452`: The verification worker now starts unconditionally on main, including on stores never migrated with the projection; every pass raises fulfillment-migration-required, which the worker logs as 'fulfillment-verification-unavailable' every 30s indefinitely.

   Failure scenario: A current-main deployment where the explicit quiesced migration was never applied gets a permanent warning every 30s and a wasted read snapshot plus schema validation per pass, which hides real verification outages behind expected noise. Already an open durability/operations finding; the merge onto main re-exposes it.

4. `src/heterodyne/admind/daemon.py:828`: custody_message catches only Invalid/OSError/sqlite3.Error; any other exception (decode/KeyError/TypeError from a malformed retained bundle) escapes custody_loop, so the supervisor restarts it and the dequeued event is silently lost with only a 'crashed' audit.

   Failure scenario: An operator's `!custody settle <id>` hits a bundle that raises KeyError in Service.message: the event is consumed, no outcome or refusal is recorded under gateway-custody, and the loop restarts. The command is effectively dropped. This comes from main but now runs next to the verifier in the same TaskGroup.

5. `src/heterodyne/admind/daemon.py:2565`: on_event intercepts custody commands and queues them on custody_work with put_nowait while the normal work path also queues on handle(); with no dedup by message id, a native redelivery can be processed twice.

   Failure scenario: Native redelivery of the same `!custody decline <id>` message queues it twice; custody_message runs twice under custody_lock. This relies on service-level idempotency, and the code itself notes production enrollment must qualify redelivery. Already an open finding.

6. `src/heterodyne/admind/daemon.py:752`: The function-local import of fulfillment_projection.worker happens inside the TaskGroup after earlier supervised tasks were created; a module-level import (or a bound method like custody_loop) would be simpler and fail before any task starts.

   Failure scenario: An import error in fulfillment_projection appears only after the 11 loops and the socket servers have started, forcing a TaskGroup teardown of live tasks instead of failing cleanly before startup. Also inconsistent with the custody worker added in the same hunk, which uses a module-level import and a bound method.

On the merge resolution itself, the startup and shutdown order is correct and both workers are preserved exactly. The items above are low to medium; none blocks a PoC. Everything was read from `<operator-home>/repos/heterodyne-metaharness-v2-btq-btq-x84mg`.
