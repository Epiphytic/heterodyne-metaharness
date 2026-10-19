# Actual eleventh MEDIUM review dispositions

Actual Opus found no new critical flaw in the reviewed source; the fixture is compatible and safe for this PoC. All seven findings below remain OPEN UNWAIVED, and prior reports remain authoritative historical debt. No native approval evidence or deployment clearance is manufactured.

## Finding 1 — OPEN UNWAIVED

1. `src/heterodyne/admind/daemon.py:301`: Prior debt, still open (R10 #1), not new: supervised() catches every Exception and restarts without checking asyncio.current_task().cancelling(), so the 6618750 fix covers only the verifier and the other 11 supervised loops can still hang shutdown.

   Failure scenario: SIGTERM cancels supervised('outbox'), the loop's cleanup raises sqlite3.OperationalError and replaces the CancelledError, the supervisor writes 'crashed', sleeps and restarts the factory forever, and the TaskGroup never finishes.

Author disposition: Retained; no additional production supervision, bounded shutdown, audit, lock-latency or test-completeness claim. Existing source-only and deployment holds remain.

## Finding 2 — OPEN UNWAIVED

2. `tests/test_admind_t9r1.py:185`: The fake records only names, and the test checks only that 'agent' is among them, so nothing checks that run() starts 'fulfillment-verification' at all or with base=30 and cap=30.

   Failure scenario: A later edit drops the verifier's supervised(...) call from run(), or drops base=30, cap=30 (the fake silently falls back to its own 1.0/30.0 defaults). The verifier then never runs, or retries at 1s, 2s, 4s instead of a fixed 30s, and this test plus the 14-module and 75 supervisor/worker runs still pass.

Author disposition: Retained; no additional production supervision, bounded shutdown, audit, lock-latency or test-completeness claim. Existing source-only and deployment holds remain.

## Finding 3 — OPEN UNWAIVED

3. `tests/test_admind_t9r1.py:186`: The fake repeats supervised()'s default values (1.0 and 30.0) by hand, so it drifts silently when those defaults change and breaks again whenever run() passes another supported keyword (healthy, clock, sleep).

   Failure scenario: run() starts passing healthy=120 to one loop and this test fails with TypeError again, as it did before this commit. Or the real default base changes to 2.0 and the fake keeps advertising 1.0. Either `**_: object` or recording the keyword values and asserting on them would avoid both.

Author disposition: Retained; no additional production supervision, bounded shutdown, audit, lock-latency or test-completeness claim. Existing source-only and deployment holds remain.

## Finding 4 — OPEN UNWAIVED

4. `src/heterodyne/admind/fulfillment_projection.py:448`: Prior debt, still open (R10 #2/#3): the shutdown wait on the running pass is neither shielded nor time-limited, and the finally block then calls executor.shutdown(wait=True) on the event-loop thread.

   Failure scenario: A second cancel arrives while verify_pass is stuck on a locked SQLite file (the read connection waits up to 1s, the write step's writer lock up to 5s per statement). The wait is abandoned and executor.shutdown(wait=True) blocks the whole event loop until the thread finishes, stalling every other task's cleanup.

Author disposition: Retained; no additional production supervision, bounded shutdown, audit, lock-latency or test-completeness claim. Existing source-only and deployment holds remain.

## Finding 5 — OPEN UNWAIVED

5. `src/heterodyne/admind/fulfillment_projection.py:450`: Prior debt, still open (R10 #4): a pass that fails during shutdown is logged only as fixed text and never reaches the audit as a 'crashed' record with its error type.

   Failure scenario: A malformed row makes verify_pass raise Invalid exactly at SIGTERM. The audit has no 'fulfillment-verification crashed error=Invalid' entry, so an operator cannot tell it apart from an OperationalError.

Author disposition: Retained; no additional production supervision, bounded shutdown, audit, lock-latency or test-completeness claim. Existing source-only and deployment holds remain.

## Finding 6 — OPEN UNWAIVED

6. `src/heterodyne/admind/fulfillment_projection.py:394`: verify_pass's write step runs store.transaction() in the worker thread, holding the shared store lock and the SQLite writer lock, so event-loop code that needs the store blocks the whole loop for that time.

   Failure scenario: Another process holds the SQLite writer lock and the worker thread waits up to 5s inside BEGIN IMMEDIATE while holding the store lock. Any event-loop store call (outbox, inbound, ask posting) waits on that lock synchronously, freezing all daemon loops and socket servers for up to about 5s per pass.

Author disposition: Retained; no additional production supervision, bounded shutdown, audit, lock-latency or test-completeness claim. Existing source-only and deployment holds remain.

## Finding 7 — OPEN UNWAIVED

7. `tests/test_admind_fulfillment_worker_cancel.py:26`: Prior debt, still open (R10 #5/#6/#7): the cancellation test runs worker() without supervised(), closes the Store only after a successful run, and relies on a single sleep(0) to get the ordering it claims.

   Failure scenario: A regression that makes supervised() spin during shutdown passes this test. A failing assertion leaks the SQLite handle and the executor thread into the high-FD tests that follow. Under load the test may exercise an already-failed pass instead of one failing mid-drain.

Author disposition: Retained; no additional production supervision, bounded shutdown, audit, lock-latency or test-completeness claim. Existing source-only and deployment holds remain.

Exact runtime6618750 completed 2579 tests passing with one inherited supervisor test double signature failure in424.68s. Fixture-only04f2622 preserves all37 original assertion ASTs;14 module+75 supervisor/worker tests PASS. Fresh full admind run is still pending.
