# Actual R10 author dispositions

Narrow fix only: no critical flaw according to actual reviewer. All seven findings and all prior open debt remain OPEN UNWAIVED. Generic supervisor cancellation, repeated-cancel drainage and bounded cleanup are not resolved. Full-suite/native/deployment gates remain.

## 1. OPEN UNWAIVED

> 1. `src/heterodyne/admind/daemon.py:301`: The root cause is still there: supervised() catches every Exception and restarts without checking asyncio.current_task().cancelling(). Any loop that loses its CancelledError during cleanup (an exception raised in a finally/__aexit__ or a drain, like the verifier had) still makes the supervisor restart forever with cancellation pending.
> 
>    Failure scenario: On SIGTERM the TaskGroup cancels supervised('outbox', ...). The loop's cleanup raises sqlite3.OperationalError, which replaces CancelledError. supervised logs 'crashed', calls sleep(delay) (which is not interrupted because the cancel request was already consumed) and restarts the factory forever, so the daemon never shuts down. The 6618750 patch only fixes the one verifier call site. The general fix: after catching Exception, if current_task().cancelling() is non-zero, raise CancelledError.

Retained as reported; no claim of general lifecycle clearance.

## 2. OPEN UNWAIVED

> 2. `src/heterodyne/admind/fulfillment_projection.py:448`: The drain awaits the executor future without asyncio.shield. A second cancel during the drain cancels the wrapper future while the thread keeps running, and the finally block then calls executor.shutdown(wait=True) on the event-loop thread.
> 
>    Failure scenario: Shutdown cancels the task, then a second cancel arrives (for example from an outer wait_for/timeout or a repeated TaskGroup abort) while verify_pass is still busy on a locked SQLite DB. The second CancelledError leaves the drain, and the finally's executor.shutdown(wait=True) blocks the whole event loop until the thread finishes. Every other task's cleanup stalls.

Retained as reported; no claim of general lifecycle clearance.

## 3. OPEN UNWAIVED

> 3. `src/heterodyne/admind/fulfillment_projection.py:448`: The drain has no time limit. The docstring promises a 'bounded running pass', but nothing bounds verify_pass, so a slow or blocked pass delays shutdown indefinitely.
> 
>    Failure scenario: verify_pass is blocked on a busy SQLite writer lock or a large scan when SIGTERM arrives. The worker waits on the future with no timeout, the TaskGroup cannot finish, and admind looks hung on shutdown, which is the same symptom as the bug being fixed.

Retained as reported; no claim of general lifecycle clearance.

## 4. OPEN UNWAIVED

> 4. `src/heterodyne/admind/fulfillment_projection.py:450`: A pass that fails during shutdown now produces only a fixed-text log line. The audit 'crashed' record with the exception type, which supervised() used to write, is gone.
> 
>    Failure scenario: A malformed projection row makes verify_pass raise Invalid exactly at shutdown. Before, the audit trail recorded 'fulfillment-verification crashed error=Invalid'. Now only a generic warning is logged, so operators cannot tell an Invalid from an OperationalError from the audit.

Retained as reported; no claim of general lifecycle clearance.

## 5. OPEN UNWAIVED

> 5. `tests/test_admind_fulfillment_worker_cancel.py:26`: The new test runs worker() directly, not wrapped in supervised(). It therefore never checks the reported symptom (the supervisor restarting forever), and a regression at the supervisor level would not be caught.
> 
>    Failure scenario: Someone later changes worker() to re-raise a different exception type during drain, or a different loop replaces cancellation. supervised() would still spin forever, but this test passes because it only asserts that worker() raises CancelledError.

Retained as reported; no claim of general lifecycle clearance.

## 6. OPEN UNWAIVED

> 6. `tests/test_admind_fulfillment_worker_cancel.py:22`: The disposable Store is created outside any try/finally, and store.close() runs only after asyncio.run succeeds. A failing assertion leaks the SQLite handle and the failing_pass thread into later tests.
> 
>    Failure scenario: On the predecessor code the test fails with Invalid. store.close() is skipped, and in a full high-FD suite run the open connection or descriptor stays around and can disturb later FD-sensitive tests.

Retained as reported; no claim of general lifecycle clearance.

## 7. OPEN UNWAIVED

> 7. `tests/test_admind_fulfillment_worker_cancel.py:30`: The ordering comment says one asyncio.sleep(0) guarantees the worker is draining before the pass fails. That is not strictly guaranteed, so the test may sometimes exercise an already-finished future instead of a failure in mid-drain.
> 
>    Failure scenario: Under load the executor thread sets the exception on the future before the worker task handles the cancellation. The test still passes, but it is checking 'the future already failed when the drain started', not the claimed in-flight path, so its proof of the race is weaker than the commit message says.
> 
> The first finding matters most for the PoC: the hang this commit fixes can still happen in the 11 other supervised loops, because the supervisor itself does not respect a pending cancellation. All four open quality gates and the other holds you listed are unaffected by this commit.

Retained as reported; no claim of general lifecycle clearance.
