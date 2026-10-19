# Actual R6 findings and author dispositions

First two FULL HIGH retained; invocation3 aborted without terminal review. R6 is fifth substantive review. All ten findings and prior findings remain OPEN UNWAIVED.

## 1. OPEN UNWAIVED

> 1. `src/heterodyne/admind/fulfillment_projection.py:389`: verify_pass maps every per-item sqlite3.Error (and other transient errors) from the read snapshot to state 'conflict', and the write phase then saves that verdict at the current generation, so a passing read error becomes a permanent conflict that is never retried.
> 
>    Failure scenario: The snapshot connection hits SQLITE_BUSY, SQLITE_IOERR or an interrupt while measuring or reading a correctly fulfilled receipt -> results gets (ask_id, gen, 'conflict') -> the write transaction does INSERT OR REPLACE verifications state='conflict', valid=0 and deletes the dirty row. The generation never changes again, so remaining() and the dirty queue skip it forever. The ask stays unsuppressed and keeps counting against MAX_OPEN capacity until an unrelated write bumps its generation.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 2. OPEN UNWAIVED

> 2. `src/heterodyne/admind/fulfillment_projection.py:446`: The drain awaits the future once without a shield, so a second cancellation leaves the pass running and falls into finally: executor.shutdown(wait=True), which blocks the event loop synchronously for the rest of the pass, whose length is unbounded.
> 
>    Failure scenario: The TaskGroup tears down (first cancel), then a SIGTERM escalation or the outer guarded/TaskGroup sends a second cancel while verify_pass is hashing an 8 MiB unit or waiting on a 5s busy timeout. CancelledError escapes `await future`, and shutdown(wait=True) freezes all other coroutines (hook, outbox, shutdown bookkeeping) until the thread finishes. daemon.py:826-838 already has the correct loop (`while not task.done(): await shield(task)` with continue on CancelledError and break on Exception) and should be reused here.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 3. OPEN UNWAIVED

> 3. `src/heterodyne/admind/daemon.py:301`: The fix patches one loop. supervised() still treats any Exception as a restartable crash even when its own task has a cancellation pending, so any supervised loop whose cleanup raises after a swallowed cancel recreates the infinite-retry hang.
> 
>    Failure scenario: Any other supervised loop (batches, autobump, maintenance-retry) whose cancellation handler awaits something that raises: supervised logs 'crashed', sleeps and restarts, and the TaskGroup never finishes shutting down. Fixing this at the root means checking `asyncio.current_task().cancelling()` in supervised (re-raise CancelledError instead of retrying), which covers every loop rather than one worker.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 4. OPEN UNWAIVED

> 4. `src/heterodyne/admind/daemon.py:733`: The verification worker starts unconditionally, even when the projection has not been migrated (the default, opt-in state), so every pass raises Invalid('fulfillment-migration-required') and is treated as a failure.
> 
>    Failure scenario: Every daemon on an unmigrated store (and every daemon test fixture) logs 'fulfillment-verification-unavailable' and opens a new read-only SQLite connection every 30s forever. The pass is permanently in its failure path, which is the condition that produced the reproduced shutdown hang. Starting the worker only when validate() passes, or backing off quietly on migration-required, would avoid this.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 5. OPEN UNWAIVED

> 5. `src/heterodyne/admind/fulfillment_projection.py:418`: verify_pass returns len(results), which also counts results the write phase skipped (gen None or generation changed), so the worker treats a pass that wrote nothing as productive and loops every 1s.
> 
>    Failure scenario: A receipt with no fulfillment_generations row (that table has no delete protection, and validate() never checks for it) always yields (ask_id, None, 'conflict'). It is never written, never leaves remaining()/dirty, and count >= 1 on every pass, so the worker re-scans and opens a new connection every second forever instead of using the 30s idle delay.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 6. OPEN UNWAIVED

> 6. `tests/test_admind_fulfillment_worker_cancel.py:30`: The regression test relies on a single sleep(0) to deliver the cancel before the pass fails. If the failure lands first, the worker takes the Exception branch (sleep(30)), the cancel then interrupts that sleep, and the test passes on the old buggy code. Store cleanup is also skipped when the test fails.
> 
>    Failure scenario: Under load, or with different event-loop scheduling, the thread's exception callback is processed before the task observes the cancellation. The test still sees CancelledError and goes green with the predecessor code, so it can no longer catch a regression. When an assertion fails, store.close() is never reached because it is outside any finally, leaking the connection and descriptor into later tests (and towards the FD_SETSIZE limit).

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 7. OPEN UNWAIVED

> 7. `src/heterodyne/admind/fulfillment_projection.py:228`: capacity_predicate() runs the full validate() (kv checks, the validate_schema_db DDL loop, 17 sqlite_master lookups plus a trigger count) on every capacity_count, capacity_asks, inactive_asks and fulfilled_asks call.
> 
>    Failure scenario: Each ask post, approval post, apply (which calls require_projection and then capacity_count) and card listing repeats around 20 or more schema queries on the event-loop thread while holding the Store lock. The validated state could be cached per schema_version / data_version and only re-checked when it changes.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 8. OPEN UNWAIVED

> 8. `src/heterodyne/admind/store.py:1346`: transaction() resets _verification_dirty at the start of each outer transaction, so dirty marks set by autocommit @_locked writes outside transaction() are silently discarded and never wake the worker.
> 
>    Failure scenario: An autocommit write that fires an fq2 trigger (for example an asks UPDATE of a fingerprinted field outside a transaction block) sets the flag, and the next transaction() clears it before checking. The wake is lost and re-verification waits for the 30s poll, which contradicts the docstring's claim that connection-local hooks observe new dirty writes.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 9. OPEN UNWAIVED

> 9. `src/heterodyne/admind/fulfillment.py:537`: duplicate() no longer requires valid=1, so a repeated apply with the same digest returns 'already-applied: fulfilled; original answer retained' even when the projection marks the receipt as conflict or unverified.
> 
>    Failure scenario: An ask was fulfilled, then an old writer edited it (generation bumped, verification state 'conflict', ask back in capacity). Re-applying the same receipt reports 'fulfilled', while Get reports lifecycle 'conflict' and the ask still uses capacity, so the operator is told it was fulfilled when it is not suppressed.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.

## 10. OPEN UNWAIVED

> 10. `src/heterodyne/admind/store.py:37`: CAPACITY_ACTIVE_SQL is now dead code: every capacity query moved to fulfillment.capacity_predicate(), and nothing in src or tests references the constant.
> 
>     Failure scenario: A stale second definition of 'capacity-active', with different semantics (valid=0 vs the projection's FALLBACK/SUPPRESSED), stays at the top of store.py. A future caller could pick it up and get capacity counts that disagree with the projection.

Retained as reported. Offline source PoC only; no production, integration, task-close or deployment clearance.
