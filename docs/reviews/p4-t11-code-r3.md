Reviewer: gpt-6.1-sol. Reviewed commit: b73e00c. Verdict: REVISE.

Controller adjudication (cycle 3 of 3, plan2-controller): finding 1 is deferred and does not block this proof of concept. It needs wsd to stall for more than 72 hours (LINGER_SECONDS) exactly between `_reaper(rec)` and `backend.create(...)`, and then to crash before cleanup. Under ADR r15 section 7, a sandbox that outlives its deadline is a lifetime-policy gap: the sandbox stays the isolation boundary, no agent commit is lost or wrongly landed, and no other generation's sandbox is touched. Follow-up: before submitting, `_start` should refuse a create once the deadline has passed. Better still, the reaper should learn from wsd's durable record whether the create is conclusive. Carry this into Task 12, or into a follow-up bead.

1. [BLOCKING] `src/heterodyne/sandbox/reaper.py:45–46` — **The 72-hour exit restores the late-create race.** wsd can pause after `_reaper(rec)` and before `backend.create(...)` (`runtime.py:383–384`). After deadline + 72 hours, the reaper confirms absence and exits. When wsd resumes, it submits the create; a crash before cleanup leaves the resulting sandbox running past its deadline without a watcher. The client’s 300-second timeout cannot bound a pause before submission or prove cancellation of an outstanding request.

   An injected-clock reproduction confirmed that `reap` returns at deadline + 259,200 seconds and a subsequent create remains alive. The new linger tests assert this unsafe exit; the delayed-create tests only cover creation while the watcher survives. This is a regression introduced in cycle 2, rather than code copied verbatim from the plan.

   **Fix:** remove the time-based exit for inconclusive creates. Retain the generation-specific watcher until creation is conclusively completed or cancelled and teardown is confirmed. Add regressions for both a paused pre-create launch and an outstanding create completing after the linger limit, followed by a wsd crash.

Six pure tests passed when called directly, and an in-memory check confirmed that expiry attempts the second record after the first stop fails. Full pytest execution was blocked by the environment’s read-only `/tmp`.

REVISE