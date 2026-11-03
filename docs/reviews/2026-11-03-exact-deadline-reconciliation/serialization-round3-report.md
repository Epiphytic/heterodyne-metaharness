**Verdict: not ready at this head. One blocker (B1).** It's a regression in the existing test suite, not a production correctness bug. The lock design itself looks sound enough for a PoC. Fix B1, rerun the whole suite (not only `test_sandbox_runtime.py`), and the serialization repair can go ahead. The two existing HIGH reviews are untouched and this counts as round three. I edited nothing, committed nothing, touched no live tmux, DB, service or config, and spawned no agents.

**Model and effort:** this was run by `claude-opus-5-5` (Opus 5.5). I can't see the reasoning-effort setting, so I can't confirm the "medium" you asked for was applied. The review instructions I was given said "high effort, 8 angles, no verify", and I followed those in this one context.

**Blocker (B1).** Five tests in `tests/test_manager_control.py` now fail with `AttributeError: 'SandboxRuntime' object has no attribute 'lifecycle_lock'`. Those tests build the runtime with `SandboxRuntime.__new__(SandboxRuntime)`, which skips `__init__`, and then call `own_manager_control`, `close_manager_control` or `observe_session`. The new wrapper in `runtime.py` reads `self.lifecycle_lock` directly. I checked both sides: 56/56 pass at the baseline b82e01d, and 5 fail / 51 pass at 71d1c70. The qualification doc only cites the targeted tests and the 348-test run of `tests/test_sandbox_runtime.py`, so this file was never run. The fix is either a guarded lock lookup in the wrapper, like the existing `getattr(self, "_closing_manager_servers", [])`, or setting the lock in those tests.

**What I checked and found clear:**
- **Deadlock:** none found. Session hooks only take `queue_lock`. `close_manager_control` joins handler threads while holding the lifecycle lock, but those handlers never take it. `walk_lock` and the credential lock are only taken inside the lifecycle lock, so the order is consistent. The RLock covers preflight calling observe.
- **Missed writers:** the only public methods left unwrapped are `verified_cli`, `available`, `layout`, `tmux_name` and `reaper_name`. The usage reader calls `verified_cli` without the lock, but it only verifies the install and may queue a taint notice; it never changes a session record.
- **Process boundary:** every wsd runtime call goes through the one daemon instance built in `wsd/cli.py` `run`. The in-process lock therefore covers wsd's own lanes, provided Hermes reconciliation runs in that same process.
- The isolated test `deadline_reconciliation_serializes` passes (6/6).

**Advisories (not blocking):**
1. The lock is now global across all workstream lane threads. A long `launch` (startup wait, self-test, credential lock wait) or an `expire` taint walk in one workstream stalls `expire`, `stop` and `signals` in every other workstream. In the worst case that pushes lifetime ends later, leaving only the reaper as backstop.
2. The lock does not change the "expire wins first" outcome. A deadline-reaped record that wsd's `expire()` ends before reconciliation still causes a permanent hold ("changed without a receipt"). That conservative hold was accepted in round two, but wiring should expect it to be the common path.
3. The lock coordinates threads only, and the doc says so. Hermes wiring must call `reconcile_session` from inside the wsd daemon process, not a second runtime instance.
4. Quality: the decorator's similarity to the Store/Journal locks and the class growth are disclosed. The new test is fine.

5 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:327`: _lifecycle wrapper reads self.lifecycle_lock unconditionally, so runtimes built with SandboxRuntime.__new__ (no __init__) now raise AttributeError from own_manager_control/close_manager_control/observe_session.

   Failure scenario: tests/test_manager_control.py: 5 tests fail at 71d1c70 (AttributeError: 'SandboxRuntime' object has no attribute 'lifecycle_lock'): test_cross_process_observer_does_not_rebind_or_unlink_owner_socket, test_runtime_owner_release_waits_for_all_handlers, test_reacquired_owner_can_rebind_previously_closed_socket, test_ended_observer_translates_uncertain_tmux_failure, test_shutdown_busy_server_removed_from_rebind_cache_but_owner_retained; all 56 pass at b82e01d. The qualification's 'full348' run covers only test_sandbox_runtime.py, so this was missed.

2. `src/heterodyne/sandbox/runtime.py:717`: One runtime-wide lifecycle lock now serializes every wsd lane thread, and launch holds it through self-test, credential lock wait and SessionStart polling.

   Failure scenario: Lane A launches and waits up to the startup/credlock timeouts while holding lifecycle_lock; lane B's scheduler calls expire(ws_b, now) for a session at its hard deadline and blocks, so B's lifetime stop slips past the deadline (only the reaper bounds it), and B's signals/sessions/stop also stall.

3. `src/heterodyne/sandbox/runtime.py:908`: expire() now runs its taint walk (_watch hashes installs) and signals() runs per-session usage exec (_poll, USAGE_SECONDS timeout) while holding the global lifecycle lock.

   Failure scenario: Several running sessions with usage polling due: signals(ws) runs backend.exec for each under lifecycle_lock, blocking other lanes and any Hermes observe_session/reconcile_session for the total exec time.

4. `src/heterodyne/sandbox/runtime.py:876`: Serialization makes ordering deterministic but leaves the expire-first race: wsd's expire() ends a deadline-reaped RUNNING record with no recovery receipt, after which reconcile_session holds permanently.

   Failure scenario: Record passes its deadline; the lane's pickup runs expire() first (it runs on every pickup) and ends it with LIFETIME; Hermes then calls reconcile_session(original) and gets RuntimeUnavailable('the original recovery record changed without a receipt') on every retry.

5. `src/heterodyne/sandbox/runtime.py:337`: lifecycle_lock is an in-process threading.RLock; it does not serialize a reconcile_session issued from any runtime instance other than the wsd daemon's.

   Failure scenario: If Hermes recovery IPC/installer wiring builds its own SandboxRuntime (separate process or second build_runtime), its reconcile_session and wsd's expire() race exactly as in the round-2 finding, with only the manager owner file lock and record re-reads as protection.

**Verdict: not ready at this head. One blocker (B1).** It's a regression in the existing test suite, not a production correctness bug. The lock design itself looks sound enough for a PoC. Fix B1, rerun the whole suite (not only `test_sandbox_runtime.py`), and the serialization repair can go ahead. The two existing HIGH reviews are untouched and this counts as round three. I edited nothing, committed nothing, touched no live tmux, DB, service or config, and spawned no agents.

**Model and effort:** this was run by `claude-opus-5-5` (Opus 5.5). I can't see the reasoning-effort setting, so I can't confirm the "medium" you asked for was applied. The review instructions I was given said "high effort, 8 angles, no verify", and I followed those in this one context.

**Blocker (B1).** Five tests in `tests/test_manager_control.py` now fail with `AttributeError: 'SandboxRuntime' object has no attribute 'lifecycle_lock'`. Those tests build the runtime with `SandboxRuntime.__new__(SandboxRuntime)`, which skips `__init__`, and then call `own_manager_control`, `close_manager_control` or `observe_session`. The new wrapper in `runtime.py` reads `self.lifecycle_lock` directly. I checked both sides: 56/56 pass at the baseline b82e01d, and 5 fail / 51 pass at 71d1c70. The qualification doc only cites the targeted tests and the 348-test run of `tests/test_sandbox_runtime.py`, so this file was never run. The fix is either a guarded lock lookup in the wrapper, like the existing `getattr(self, "_closing_manager_servers", [])`, or setting the lock in those tests.

**What I checked and found clear:**
- **Deadlock:** none found. Session hooks only take `queue_lock`. `close_manager_control` joins handler threads while holding the lifecycle lock, but those handlers never take it. `walk_lock` and the credential lock are only taken inside the lifecycle lock, so the order is consistent. The RLock covers preflight calling observe.
- **Missed writers:** the only public methods left unwrapped are `verified_cli`, `available`, `layout`, `tmux_name` and `reaper_name`. The usage reader calls `verified_cli` without the lock, but it only verifies the install and may queue a taint notice; it never changes a session record.
- **Process boundary:** every wsd runtime call goes through the one daemon instance built in `wsd/cli.py` `run`. The in-process lock therefore covers wsd's own lanes, provided Hermes reconciliation runs in that same process.
- The isolated test `deadline_reconciliation_serializes` passes (6/6).

**Advisories (not blocking):**
1. The lock is now global across all workstream lane threads. A long `launch` (startup wait, self-test, credential lock wait) or an `expire` taint walk in one workstream stalls `expire`, `stop` and `signals` in every other workstream. In the worst case that pushes lifetime ends later, leaving only the reaper as backstop.
2. The lock does not change the "expire wins first" outcome. A deadline-reaped record that wsd's `expire()` ends before reconciliation still causes a permanent hold ("changed without a receipt"). That conservative hold was accepted in round two, but wiring should expect it to be the common path.
3. The lock coordinates threads only, and the doc says so. Hermes wiring must call `reconcile_session` from inside the wsd daemon process, not a second runtime instance.
4. Quality: the decorator's similarity to the Store/Journal locks and the class growth are disclosed. The new test is fine.

5 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:327`: _lifecycle wrapper reads self.lifecycle_lock unconditionally, so runtimes built with SandboxRuntime.__new__ (no __init__) now raise AttributeError from own_manager_control/close_manager_control/observe_session.

   Failure scenario: tests/test_manager_control.py: 5 tests fail at 71d1c70 (AttributeError: 'SandboxRuntime' object has no attribute 'lifecycle_lock'): test_cross_process_observer_does_not_rebind_or_unlink_owner_socket, test_runtime_owner_release_waits_for_all_handlers, test_reacquired_owner_can_rebind_previously_closed_socket, test_ended_observer_translates_uncertain_tmux_failure, test_shutdown_busy_server_removed_from_rebind_cache_but_owner_retained; all 56 pass at b82e01d. The qualification's 'full348' run covers only test_sandbox_runtime.py, so this was missed.

2. `src/heterodyne/sandbox/runtime.py:717`: One runtime-wide lifecycle lock now serializes every wsd lane thread, and launch holds it through self-test, credential lock wait and SessionStart polling.

   Failure scenario: Lane A launches and waits up to the startup/credlock timeouts while holding lifecycle_lock; lane B's scheduler calls expire(ws_b, now) for a session at its hard deadline and blocks, so B's lifetime stop slips past the deadline (only the reaper bounds it), and B's signals/sessions/stop also stall.

3. `src/heterodyne/sandbox/runtime.py:908`: expire() now runs its taint walk (_watch hashes installs) and signals() runs per-session usage exec (_poll, USAGE_SECONDS timeout) while holding the global lifecycle lock.

   Failure scenario: Several running sessions with usage polling due: signals(ws) runs backend.exec for each under lifecycle_lock, blocking other lanes and any Hermes observe_session/reconcile_session for the total exec time.

4. `src/heterodyne/sandbox/runtime.py:876`: Serialization makes ordering deterministic but leaves the expire-first race: wsd's expire() ends a deadline-reaped RUNNING record with no recovery receipt, after which reconcile_session holds permanently.

   Failure scenario: Record passes its deadline; the lane's pickup runs expire() first (it runs on every pickup) and ends it with LIFETIME; Hermes then calls reconcile_session(original) and gets RuntimeUnavailable('the original recovery record changed without a receipt') on every retry.

5. `src/heterodyne/sandbox/runtime.py:337`: lifecycle_lock is an in-process threading.RLock; it does not serialize a reconcile_session issued from any runtime instance other than the wsd daemon's.

   Failure scenario: If Hermes recovery IPC/installer wiring builds its own SandboxRuntime (separate process or second build_runtime), its reconcile_session and wsd's expire() race exactly as in the round-2 finding, with only the manager owner file lock and record re-reads as protection.
