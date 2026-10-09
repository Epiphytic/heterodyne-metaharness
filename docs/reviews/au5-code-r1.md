Reviewer: gpt-6.1-sol. Reviewed range: c38b810..2fba155. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/wsd/daemon.py:313` — **Caller cancellation can lose the quota wake.** If `_run` is cancelled before its shielded job finishes, `fut.done()` is false; the worker later completes with `wake_at` set, but nothing arms it. Confirmed with an in-memory reproduction. **Fix:** arrange wake arming from job completion independently of the awaiting caller, retaining the stopping check. Add a cancellation regression test.

2. [BLOCKING] `src/heterodyne/wsd/usage.py:77` — **Out-of-range integer percentages can crash ingestion.** `used_percent=10**400` raises `OverflowError` in `math.isfinite`, instead of returning `Dropped("bad_percent")` as D3 requires. **Fix:** check numeric type and bounds before converting or testing finiteness; test large positive and negative integers.

3. [BLOCKING] `tests/test_wsd_pickup.py:674`, `src/heterodyne/wsd/scheduler.py:191` — **Interpretation 10 violates the approved DEFERRED oracle.** An uncertain launch can leave a listed session and finish `ENDED`; subsequent quota skips then produce `DEFERRED` without rechecking role occupancy. The replacement assertion merely allows coder sessions already present in `listed`, weakening the required “no coder session is listed” check. **Fix:** recheck occupancy before returning `DEFERRED`, report `BUSY` or the applicable human-recovery outcome, and restore the strict oracle with a deterministic uncertain-launch-plus-quota test.

REVISE