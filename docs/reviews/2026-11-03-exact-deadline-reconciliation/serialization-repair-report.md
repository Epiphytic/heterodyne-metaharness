I found no critical blockers. The serialization change looks good enough to implement as a PoC, with the advisories below kept open.

**Scope and what I ran:** I reviewed the frozen Product head 4facc5ab against the merged baseline b82e01d, in `${OPERATOR_HOME}/repos/heterodyne-exact-session-reconcile-20261103`. I made no edits or commits and touched nothing live. I ran the review in this context, following the inline high-effort recipe I was given (8 angles, dedup only, no verify). I spawned no subagents.
- The safe isolated test (`-k deadline_reconciliation_serializes`) passed: 6 tests, 9.62s.
- `tests/test_manager_control.py` passed: 56 tests, 1.70s.
- I did not re-run the full suite, the full runtime suite or Pyright.

**Model and effort:** the model exposed to me is `claude-opus-5-5` (Opus 5.5). The internal effort setting is not visible to me. The recipe I received was labelled "high effort", even though the request asked for MEDIUM, so I can't claim the run actually used medium.

**What I checked for critical issues:**
- **Deadlock:** none found.
  - Hook callbacks (`heard` → `_report`) and `_notice` only take `queue_lock`.
  - `SessionServer.close()` waits a bounded time for handler threads, and those handlers never take `lifecycle_lock`.
  - `ControlJournal` never calls back into the runtime.
  - The only lock order is `lifecycle_lock` → `walk_lock`/`queue_lock`, and nothing takes them the other way round.
  - The workstream threads in `wsd/daemon.py` hold their `shutdown` lock only while starting a job, not while it runs.
  - The RLock lets `preflight_resume` call `observe_session` without blocking itself.
- **Missed writers:** every public method that changes state is decorated. The undecorated ones are `layout`, `tmux_name`, `reaper_name`, `available` and `verified_cli`. `verified_cli` is documented as lock-free and only writes taint markers and the notice queue.
- **Compatibility:**
  - The `[**P, R]` generic syntax is fine with `requires-python >=3.12`.
  - The only `__new__(SandboxRuntime)` fixtures are the seven in `tests/test_manager_control.py`, and all of them now set the lock.
  - Wiring is still absent: no code outside `runtime.py` calls `own_manager_control` or `close_manager_control`.

**Advisories (none are blockers):**

7 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:927`: signals() and sessions() (scheduler lanes, recovery, sweep) now wait on the single runtime-wide lock behind any lane's launch, expire walk or usage poll.

   Failure scenario: Lane A runs launch(), which does sandbox create, selftest and verify. Lane B's scheduler step calls runtime.signals() or sessions() and stalls for the whole launch, delaying limit/usage handling and other workstreams' expiry. This is the disclosed advisory; it needs a bound or a measurement before wiring under real multi-lane load.

2. `src/heterodyne/sandbox/runtime.py:927`: signals() runs _poll, which reads usage inside the sandbox via a subprocess, while holding lifecycle_lock, so a slow or hung read blocks every lifecycle operation.

   Failure scenario: A session's usage read takes its full timeout. During that time reconcile_session, stop and expire for every workstream are blocked. Consider polling outside the lock and taking it only to drain the queues.

3. `src/heterodyne/sandbox/runtime.py:524`: The lock is per instance, so settle_sandbox and resolution run from a separate host or CLI process are not serialized against the daemon's runtime.

   Failure scenario: An operator settles a sandbox from a CLI process while the daemon's runtime is reconciling the same record. Only the existing record and owner checks stand between them. This is disclosed; Hermes must reuse the single owning runtime.

4. `tests/test_sandbox_runtime.py:3051`: The contender's LaunchFailed, RuntimeUnavailable and ResolveRefused are all swallowed, so the test proves ordering but not that each contender behaves correctly after the release.

   Failure scenario: A regression where expire or resolve, run after the end, wrongly raises RuntimeUnavailable, or silently does the wrong thing, still passes. Assert the expected outcome for each contender.

5. `tests/test_sandbox_runtime.py:3062`: The 'is the contender blocked' check depends on a 50ms timing window.

   Failure scenario: On a heavily loaded CI host, a contender that is not actually serialized might not have finished within 50ms, so the test could pass by accident. The window is acceptable for a PoC but weak as a regression guard.

6. `src/heterodyne/sandbox/runtime.py:317`: The _lifecycle decorator re-implements the per-instance locking-decorator pattern already flagged as similar to Store/Journal (the disclosed quality delta).

   Failure scenario: Two or more near-identical lock wrappers that drift apart. A shared generic 'locked_by(attr)' helper would remove the duplication.

7. `src/heterodyne/sandbox/runtime.py:357`: own_manager_control and close_manager_control still use getattr(self, '_closing_manager_servers', []) as a fixture fallback, while lifecycle_lock is deliberately strict.

   Failure scenario: The handling of skeletal fixtures is inconsistent. Now that all seven fixtures set their fields explicitly, the getattr fallbacks are dead defensive code that hides missing initialization.

The reported full-suite collection failure (`fcntl.F_SEAL_WRITE` missing) is an environment issue that predates this change: the import is identical in the unchanged baseline. Because of it, there is still no all-suite pass to claim.

I found no critical blockers. The serialization change looks good enough to implement as a PoC, with the advisories below kept open.

**Scope and what I ran:** I reviewed the frozen Product head 4facc5ab against the merged baseline b82e01d, in `${OPERATOR_HOME}/repos/heterodyne-exact-session-reconcile-20261103`. I made no edits or commits and touched nothing live. I ran the review in this context, following the inline high-effort recipe I was given (8 angles, dedup only, no verify). I spawned no subagents.
- The safe isolated test (`-k deadline_reconciliation_serializes`) passed: 6 tests, 9.62s.
- `tests/test_manager_control.py` passed: 56 tests, 1.70s.
- I did not re-run the full suite, the full runtime suite or Pyright.

**Model and effort:** the model exposed to me is `claude-opus-5-5` (Opus 5.5). The internal effort setting is not visible to me. The recipe I received was labelled "high effort", even though the request asked for MEDIUM, so I can't claim the run actually used medium.

**What I checked for critical issues:**
- **Deadlock:** none found.
  - Hook callbacks (`heard` → `_report`) and `_notice` only take `queue_lock`.
  - `SessionServer.close()` waits a bounded time for handler threads, and those handlers never take `lifecycle_lock`.
  - `ControlJournal` never calls back into the runtime.
  - The only lock order is `lifecycle_lock` → `walk_lock`/`queue_lock`, and nothing takes them the other way round.
  - The workstream threads in `wsd/daemon.py` hold their `shutdown` lock only while starting a job, not while it runs.
  - The RLock lets `preflight_resume` call `observe_session` without blocking itself.
- **Missed writers:** every public method that changes state is decorated. The undecorated ones are `layout`, `tmux_name`, `reaper_name`, `available` and `verified_cli`. `verified_cli` is documented as lock-free and only writes taint markers and the notice queue.
- **Compatibility:**
  - The `[**P, R]` generic syntax is fine with `requires-python >=3.12`.
  - The only `__new__(SandboxRuntime)` fixtures are the seven in `tests/test_manager_control.py`, and all of them now set the lock.
  - Wiring is still absent: no code outside `runtime.py` calls `own_manager_control` or `close_manager_control`.

**Advisories (none are blockers):**

7 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:927`: signals() and sessions() (scheduler lanes, recovery, sweep) now wait on the single runtime-wide lock behind any lane's launch, expire walk or usage poll.

   Failure scenario: Lane A runs launch(), which does sandbox create, selftest and verify. Lane B's scheduler step calls runtime.signals() or sessions() and stalls for the whole launch, delaying limit/usage handling and other workstreams' expiry. This is the disclosed advisory; it needs a bound or a measurement before wiring under real multi-lane load.

2. `src/heterodyne/sandbox/runtime.py:927`: signals() runs _poll, which reads usage inside the sandbox via a subprocess, while holding lifecycle_lock, so a slow or hung read blocks every lifecycle operation.

   Failure scenario: A session's usage read takes its full timeout. During that time reconcile_session, stop and expire for every workstream are blocked. Consider polling outside the lock and taking it only to drain the queues.

3. `src/heterodyne/sandbox/runtime.py:524`: The lock is per instance, so settle_sandbox and resolution run from a separate host or CLI process are not serialized against the daemon's runtime.

   Failure scenario: An operator settles a sandbox from a CLI process while the daemon's runtime is reconciling the same record. Only the existing record and owner checks stand between them. This is disclosed; Hermes must reuse the single owning runtime.

4. `tests/test_sandbox_runtime.py:3051`: The contender's LaunchFailed, RuntimeUnavailable and ResolveRefused are all swallowed, so the test proves ordering but not that each contender behaves correctly after the release.

   Failure scenario: A regression where expire or resolve, run after the end, wrongly raises RuntimeUnavailable, or silently does the wrong thing, still passes. Assert the expected outcome for each contender.

5. `tests/test_sandbox_runtime.py:3062`: The 'is the contender blocked' check depends on a 50ms timing window.

   Failure scenario: On a heavily loaded CI host, a contender that is not actually serialized might not have finished within 50ms, so the test could pass by accident. The window is acceptable for a PoC but weak as a regression guard.

6. `src/heterodyne/sandbox/runtime.py:317`: The _lifecycle decorator re-implements the per-instance locking-decorator pattern already flagged as similar to Store/Journal (the disclosed quality delta).

   Failure scenario: Two or more near-identical lock wrappers that drift apart. A shared generic 'locked_by(attr)' helper would remove the duplication.

7. `src/heterodyne/sandbox/runtime.py:357`: own_manager_control and close_manager_control still use getattr(self, '_closing_manager_servers', []) as a fixture fallback, while lifecycle_lock is deliberately strict.

   Failure scenario: The handling of skeletal fixtures is inconsistent. Now that all seven fixtures set their fields explicitly, the getattr fallbacks are dead defensive code that hides missing initialization.

The reported full-suite collection failure (`fcntl.F_SEAL_WRITE` missing) is an environment issue that predates this change: the import is identical in the unchanged baseline. Because of it, there is still no all-suite pass to claim.
