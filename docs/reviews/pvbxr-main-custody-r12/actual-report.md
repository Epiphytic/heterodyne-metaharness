I found no critical integration flaws. As a PoC this is good enough to implement, provided the combined all-admind suites now running pass. I haven't seen them pass, so I'm not claiming they do. All earlier HIGH/MEDIUM findings and dispositions stay OPEN. This is not a reviewer APPROVE and does not authorise deployment.

**What I checked**
- **The one hand-resolved conflict is correct.** In `daemon.py` at merge 41bb27c, startup creates the fulfillment-verification worker (`base=30, cap=30`) and then the gateway-custody worker, both before `reconcile_asks`. Both lines are byte-identical to their source branches.
- **The final merge d086cf5 resolved without conflicts.** Compared with the PV parent, it only adds main's custody, recovery, `ctl` and `cli` changes. Compared with the X84 side, it only adds PV's management cards, binding V2, `require_legacy_presentation` and the original cancellation drain. Nothing on either side was lost.
- **Custody works with X84's stricter `Store.transaction`.** That function now rejects a write nested inside a read-only transaction and fires a wake after commit. None of custody's read-only blocks (`issue_permit`, `get`, `message`) write. `notice_status` is a plain read. The custody authority callback (`observe`) is never called while a transaction is open. Custody writes don't touch `fulfillment_dirty`, so they don't trigger the wake. The wake itself is thread-safe (`call_soon_threadsafe`).
- **The projection checks ignore custody's table.** `validate` and `validate_schema_db` check named tables and the `fq2_*` trigger count only, so custody's lazily created `gateway_custody_proposals` table doesn't break the fulfillment-projection checks.
- **The verification pass doesn't hold the Store lock.** It reads through its own snapshot connection, so it doesn't stall custody's authority callbacks.
- **Inbound routing doesn't collide.** `!custody` matches before maintenance-recovery and generic claim, and it can't collide with `!fulfill` or management-card replies. A latched daemon still blocks custody through `authorised()`.

**Findings (none critical; ranked)**

7 findings, most severe first:

1. `tests/test_admind_t9r1.py:190`: The only hand-resolved conflict (both fulfillment-verification and gateway-custody supervised workers before reconcile_asks) has no regression test; test_run_supervises_the_agent_loop already records every supervised name but only asserts 'agent'.

   Failure scenario: A later merge that drops or reorders either worker line again (the exact conflict seen in 41bb27c) leaves custody or verification dormant at runtime while CI stays green; asserting {'fulfillment-verification','gateway-custody'} <= set(names) would catch it.

2. `src/heterodyne/admind/daemon.py:767`: Shutdown closes ask_server, recovery_server and custody_server one after another, each waiting up to 2*CLOSE_SECONDS; custody handlers block cancellation on a shielded to_thread worker that can still be waiting in observe() for a loop-side check_group.

   Failure scenario: SIGTERM while a custody permit/settle is in flight: handler tasks are cancelled and aborted after the timeouts, run() returns, and _stop_child stops the gateway child while the custody thread is still running; observe() then times out (5 s) with authority-unavailable, and total shutdown time grows with the extra server (existing shutdown/cancellation OPEN finding, widened by the composition).

3. `src/heterodyne/admind/daemon.py:841`: custody_message catches only gateway_custody.Invalid, OSError and sqlite3.Error; errors from a malformed retained bundle (KeyError, json errors) or X84's new Store ValueError('read-only-transaction') escape and crash custody_loop.

   Failure scenario: A corrupted gateway_custody_proposals.bundle makes bundle['descriptor'] raise KeyError: the event is lost with no 'gateway-custody' audit outcome (only a generic task crash), and the supervisor restart's doubling backoff delays the up to 256 queued custody commands.

4. `src/heterodyne/admind/gateway_custody.py:902`: Custody worker threads hold the shared Store RLock for whole register/command transactions, while the event loop makes synchronous store calls; with the X84 verification write-back and ask handlers, three parties now contend on one lock.

   Failure scenario: A slow custody transaction (large bundle, slow disk) blocks the loop thread at its next store.get/enqueue, stalling inbound, outbox, hooks and the ask socket for that time (existing performance OPEN finding, made more likely by the composition).

5. `src/heterodyne/admind/daemon.py:2662`: Custody commands are routed straight from on_event to custody_work, skipping the single ordered worker/work_lock that R27/B7 rely on for operator messages and membership transitions.

   Failure scenario: An operator sends a message and then '!custody settle <token>'; the settle can be processed before the earlier message or while a transition waits on work_lock, so custody decisions are not ordered with other operator inputs (authorised() still refuses while 'changing' is set).

6. `src/heterodyne/admind/fulfillment_projection.py:66`: The fq2_asks_update trigger's ASK_FIELDS leave out PV's new presentation/render_version/management columns, so changes to management metadata never bump the fulfillment generation.

   Failure scenario: An out-of-band UPDATE that sets management metadata on an already verified legacy ask doesn't mark it dirty; the verification row stays 'valid' for that generation. The capacity predicate still re-checks LEGACY_PRESENTATION live, so the effect is limited to stale diagnostics and inspect output.

7. `src/heterodyne/admind/daemon.py:846`: Custody events still queued in custody_work when shutdown cancels the task are dropped without any audit record.

   Failure scenario: Operator custody commands queued at SIGTERM disappear silently; nothing records them as refused or unprocessed. Recovery depends entirely on native redelivery, which the code comment says is not yet qualified.

None of these blocks a PoC. They are tracking items alongside the existing OPEN production, durability, supervisor, cancellation and performance findings. Before calling it qualified, I'd add the startup assertion in `tests/test_admind_t9r1.py` and wait for the combined suites to pass.
