I found no blocking defect in this slice, so source clearance is granted for the bounded preflight as scoped. It clears with advisories that should be fixed before `preflight_resume` is wired into a production lifecycle route.

**Model and reasoning:** I ran as claude-opus-5-5 (Opus 5.5). The reasoning-effort setting I could see was low, not HIGH, so this does not meet a strict "Claude Opus HIGH" requirement. Record it as Opus 5.5 at low reasoning effort.

**Scope:** I read the full diff of `88dd69a` against `d600e5d` and the qualification doc. I also read the enclosing launch, branch-claim, resume, CLI, credential, `_held`/`_end`/`_restore_creates` code and `ControlOwner`. I ran the 24 new `resume_preflight` tests and all passed (32.6s). I made no edits, ran no DB, config or service actions, and touched no managed tmux sessions.

**Why it clears:**
- **Identity check:** it requires an exact match on everything the record stores. It refuses a missing native ID, a session that never ran, or `started_at` of 0. It requires the next generation, `resume=True` without `handoff`, and phase RUNNING or ENDED.
- **No side effects:** it checks the owner and the record before and after. It refuses an unlanded record, owed WIP, an unfinished resolve, or a branch held by another session, and moves nothing in the process.
- **`settle_wip=False`:** it is correct and only reachable after the not-ENDED/unlanded refusal. Launch's default behaviour is unchanged.
- **Type errors:** the pyright repair is genuine. Orchestration stays in the runtime, and the helper only reads public fields.

**Blockers:** none.

**Advisories (not blockers):**
1. A failed owner check escapes as a bare `RuntimeError`, or `OSError` from `fstat`. That is outside the runtime's usual error types, and the test locks this in.
2. Preflight can pass in states where launch would then refuse, which is a concern for the recovery flow. If Hermes ends the executor on a passed preflight, it finds out only at launch. The retained conversation still survives, which is why this is not a blocker.
3. The checks are copied by hand from launch rather than shared with it. That copying is the root cause of item 2 and of the size growth.

**Ripwire gate:** the gate is still red, not green. The class growth from 1441 to 1484 lines is real and correctly disclosed. The dead-code entries are pytest test functions, which the tool can't see being called, and the flagged clones are repeated test setup. I accept these as disclosed maintenance cost, not as a correctness or security issue.

10 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:687`: preflight_resume calls owner.check() (at 687 and 714), which raises a bare RuntimeError (or an OSError from os.fstat), not the RuntimeUnavailable/LaunchFailed the runtime's other refusals use; test_resume_preflight_refuses_closed_owner locks this in.

   Failure scenario: Hermes wires preflight and catches (LaunchFailed, RuntimeUnavailable) as the refusal set. The owner fd is closed or the process forked, so check() raises RuntimeError('manager control owner is unavailable'). That escapes the recovery decision as an unexpected crash instead of a typed 'not qualified', and an EBADF from fstat comes out as a raw OSError. Wrap it as RuntimeUnavailable.

2. `src/heterodyne/sandbox/runtime.py:678`: Preflight does not check several things launch refuses on: the sandbox_name generation limit (MAX_GENERATION), create-ledger/late-create state (_restore_creates, legacy create_ledger=False), and leftover live sandbox/tmux for an ENDED record (which observe_session refuses). A pass does not mean launch will start.

   Failure scenario: A record at generation == MAX_GENERATION, or with an outstanding create-ledger entry for the key, passes preflight. Hermes ends the executor relying on that pass. launch() then raises SpecRefused ('generation must be from 1 to ...') or RuntimeUnavailable('create protection can't be confirmed'), and the session is left ended without the resume the preflight qualified.

3. `src/heterodyne/sandbox/resume_preflight.py:21`: The 'exact identity' check does not cover the account or model (spec.account, spec.model). The credential check only confirms spec.account's current key, so a resume under a different account passes even though cross-account relaunches are meant to be fresh-session handoffs (D7).

   Failure scenario: The retained generation ran under account 'default'. The proposed spec has account='alt', handoff=False, resume=True and alt's valid credential_key. Every gate passes and the original native conversation is qualified to resume under a different login. Launch has the same gap, but this method claims exact original-conversation identity.

4. `src/heterodyne/sandbox/runtime.py:690`: RUNNING records pass without a liveness or overdue check. launch() treats a live RUNNING record as idempotent (returns _started(prev) with no next generation), and an overdue one gets a lifetime _end that lands commits and writes host WIP, which may itself leave the record unlanded or owing WIP.

   Failure scenario: Preflight passes on a live RUNNING record. If Hermes calls launch before its own end, launch returns the old generation's Started instead of generation N+1. If the record is overdue, the lifetime end marks it unlanded and launch refuses. Either way the preflight result does not describe what launch will do; the qualification doc only says that launch rechecks its gates.

5. `src/heterodyne/sandbox/resume_preflight.py:24`: Requiring started_at > 0 refuses an ENDED record whose latest generation failed before running (ran=True and native_id kept from an earlier generation, but started_at=0 for the new record), even though launch resumes exactly that state.

   Failure scenario: Generation 3 fails in CREATING and ends with started_at=0, ran=True and native_id kept. Generation 4 resume is legitimate for launch, but preflight raises 'exact retained next-generation identity', blocking owner-deadline recovery for a recoverable conversation.

6. `tests/test_sandbox_runtime.py:2808`: test_resume_preflight_reports_holds_without_cleanup accepts any LaunchFailed or RuntimeUnavailable with no message match, so no case proves its intended gate fired. The identity test also never varies worktree, repo, install name or session_key, or the record-side ran, started_at or native_id=None.

   Failure scenario: A regression that makes preflight refuse early for every input, for example _held raising for all intents or adapter lookup failing, still passes the unlanded, wip, missing-state and expired-login cases. LOGIN_STALE and the missing-native-state refusal are never actually confirmed.

7. `src/heterodyne/sandbox/runtime.py:678`: preflight_resume re-implements launch's checks by hand (pin plus linked-worktree check, adapter lookup, reasoning check, _resume, _cli, _logins, _pinned) instead of sharing one helper, so the two sequences can drift; finding 2 shows they already have.

   Failure scenario: Pulling launch's checks into one shared helper, say _qualify_resume(spec, prev, settle_wip), would make preflight and launch check the same things, close finding 2 by construction, and shrink SandboxRuntime instead of growing it from 1441 to 1484 lines (the class-size gate the qualification doc reports).

8. `src/heterodyne/sandbox/runtime.py:707`: For medium-reasoning profiles, the install is verified and resumable state is checked twice: _check_reasoning_resume already calls _resume and _cli (verify_pin), then preflight calls both again at 708 and 711.

   Failure scenario: The medium p-two path walks and hashes the whole pinned install twice and scans the session home twice per preflight. That is extra I/O and a wider window for the taint and alert side effects. Keep the reasoning check's resume result, or skip the second _cli when the check already verified the same pin.

9. `src/heterodyne/sandbox/resume_preflight.py:18`: The helper imports Phase from runtime inside the function at call time (and SessionRecord under TYPE_CHECKING) while runtime imports this module, a circular import created to keep 10 lines out of the class.

   Failure scenario: The import cycle is a maintenance trap: a future module-level import of runtime here breaks the import order. Passing the allowed phases in, or moving Phase/SessionRecord to a leaf module, removes the cycle.

10. `tests/test_sandbox_runtime.py:2755`: test_owned_resume_preflight_preserves_running_generation calls close_manager_control() outside any try/finally, unlike its sibling tests.

    Failure scenario: If preflight or an assertion fails, the owner flock and every session server stay open for the rest of the test's teardown. That can surface as misleading follow-on errors or a leaked lock instead of the real failure.

I found no blocking defect in this slice, so source clearance is granted for the bounded preflight as scoped. It clears with advisories that should be fixed before `preflight_resume` is wired into a production lifecycle route.

**Model and reasoning:** I ran as claude-opus-5-5 (Opus 5.5). The reasoning-effort setting I could see was low, not HIGH, so this does not meet a strict "Claude Opus HIGH" requirement. Record it as Opus 5.5 at low reasoning effort.

**Scope:** I read the full diff of `88dd69a` against `d600e5d` and the qualification doc. I also read the enclosing launch, branch-claim, resume, CLI, credential, `_held`/`_end`/`_restore_creates` code and `ControlOwner`. I ran the 24 new `resume_preflight` tests and all passed (32.6s). I made no edits, ran no DB, config or service actions, and touched no managed tmux sessions.

**Why it clears:**
- **Identity check:** it requires an exact match on everything the record stores. It refuses a missing native ID, a session that never ran, or `started_at` of 0. It requires the next generation, `resume=True` without `handoff`, and phase RUNNING or ENDED.
- **No side effects:** it checks the owner and the record before and after. It refuses an unlanded record, owed WIP, an unfinished resolve, or a branch held by another session, and moves nothing in the process.
- **`settle_wip=False`:** it is correct and only reachable after the not-ENDED/unlanded refusal. Launch's default behaviour is unchanged.
- **Type errors:** the pyright repair is genuine. Orchestration stays in the runtime, and the helper only reads public fields.

**Blockers:** none.

**Advisories (not blockers):**
1. A failed owner check escapes as a bare `RuntimeError`, or `OSError` from `fstat`. That is outside the runtime's usual error types, and the test locks this in.
2. Preflight can pass in states where launch would then refuse, which is a concern for the recovery flow. If Hermes ends the executor on a passed preflight, it finds out only at launch. The retained conversation still survives, which is why this is not a blocker.
3. The checks are copied by hand from launch rather than shared with it. That copying is the root cause of item 2 and of the size growth.

**Ripwire gate:** the gate is still red, not green. The class growth from 1441 to 1484 lines is real and correctly disclosed. The dead-code entries are pytest test functions, which the tool can't see being called, and the flagged clones are repeated test setup. I accept these as disclosed maintenance cost, not as a correctness or security issue.

10 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:687`: preflight_resume calls owner.check() (at 687 and 714), which raises a bare RuntimeError (or an OSError from os.fstat), not the RuntimeUnavailable/LaunchFailed the runtime's other refusals use; test_resume_preflight_refuses_closed_owner locks this in.

   Failure scenario: Hermes wires preflight and catches (LaunchFailed, RuntimeUnavailable) as the refusal set. The owner fd is closed or the process forked, so check() raises RuntimeError('manager control owner is unavailable'). That escapes the recovery decision as an unexpected crash instead of a typed 'not qualified', and an EBADF from fstat comes out as a raw OSError. Wrap it as RuntimeUnavailable.

2. `src/heterodyne/sandbox/runtime.py:678`: Preflight does not check several things launch refuses on: the sandbox_name generation limit (MAX_GENERATION), create-ledger/late-create state (_restore_creates, legacy create_ledger=False), and leftover live sandbox/tmux for an ENDED record (which observe_session refuses). A pass does not mean launch will start.

   Failure scenario: A record at generation == MAX_GENERATION, or with an outstanding create-ledger entry for the key, passes preflight. Hermes ends the executor relying on that pass. launch() then raises SpecRefused ('generation must be from 1 to ...') or RuntimeUnavailable('create protection can't be confirmed'), and the session is left ended without the resume the preflight qualified.

3. `src/heterodyne/sandbox/resume_preflight.py:21`: The 'exact identity' check does not cover the account or model (spec.account, spec.model). The credential check only confirms spec.account's current key, so a resume under a different account passes even though cross-account relaunches are meant to be fresh-session handoffs (D7).

   Failure scenario: The retained generation ran under account 'default'. The proposed spec has account='alt', handoff=False, resume=True and alt's valid credential_key. Every gate passes and the original native conversation is qualified to resume under a different login. Launch has the same gap, but this method claims exact original-conversation identity.

4. `src/heterodyne/sandbox/runtime.py:690`: RUNNING records pass without a liveness or overdue check. launch() treats a live RUNNING record as idempotent (returns _started(prev) with no next generation), and an overdue one gets a lifetime _end that lands commits and writes host WIP, which may itself leave the record unlanded or owing WIP.

   Failure scenario: Preflight passes on a live RUNNING record. If Hermes calls launch before its own end, launch returns the old generation's Started instead of generation N+1. If the record is overdue, the lifetime end marks it unlanded and launch refuses. Either way the preflight result does not describe what launch will do; the qualification doc only says that launch rechecks its gates.

5. `src/heterodyne/sandbox/resume_preflight.py:24`: Requiring started_at > 0 refuses an ENDED record whose latest generation failed before running (ran=True and native_id kept from an earlier generation, but started_at=0 for the new record), even though launch resumes exactly that state.

   Failure scenario: Generation 3 fails in CREATING and ends with started_at=0, ran=True and native_id kept. Generation 4 resume is legitimate for launch, but preflight raises 'exact retained next-generation identity', blocking owner-deadline recovery for a recoverable conversation.

6. `tests/test_sandbox_runtime.py:2808`: test_resume_preflight_reports_holds_without_cleanup accepts any LaunchFailed or RuntimeUnavailable with no message match, so no case proves its intended gate fired. The identity test also never varies worktree, repo, install name or session_key, or the record-side ran, started_at or native_id=None.

   Failure scenario: A regression that makes preflight refuse early for every input, for example _held raising for all intents or adapter lookup failing, still passes the unlanded, wip, missing-state and expired-login cases. LOGIN_STALE and the missing-native-state refusal are never actually confirmed.

7. `src/heterodyne/sandbox/runtime.py:678`: preflight_resume re-implements launch's checks by hand (pin plus linked-worktree check, adapter lookup, reasoning check, _resume, _cli, _logins, _pinned) instead of sharing one helper, so the two sequences can drift; finding 2 shows they already have.

   Failure scenario: Pulling launch's checks into one shared helper, say _qualify_resume(spec, prev, settle_wip), would make preflight and launch check the same things, close finding 2 by construction, and shrink SandboxRuntime instead of growing it from 1441 to 1484 lines (the class-size gate the qualification doc reports).

8. `src/heterodyne/sandbox/runtime.py:707`: For medium-reasoning profiles, the install is verified and resumable state is checked twice: _check_reasoning_resume already calls _resume and _cli (verify_pin), then preflight calls both again at 708 and 711.

   Failure scenario: The medium p-two path walks and hashes the whole pinned install twice and scans the session home twice per preflight. That is extra I/O and a wider window for the taint and alert side effects. Keep the reasoning check's resume result, or skip the second _cli when the check already verified the same pin.

9. `src/heterodyne/sandbox/resume_preflight.py:18`: The helper imports Phase from runtime inside the function at call time (and SessionRecord under TYPE_CHECKING) while runtime imports this module, a circular import created to keep 10 lines out of the class.

   Failure scenario: The import cycle is a maintenance trap: a future module-level import of runtime here breaks the import order. Passing the allowed phases in, or moving Phase/SessionRecord to a leaf module, removes the cycle.

10. `tests/test_sandbox_runtime.py:2755`: test_owned_resume_preflight_preserves_running_generation calls close_manager_control() outside any try/finally, unlike its sibling tests.

    Failure scenario: If preflight or an assertion fails, the owner flock and every session server stay open for the rest of the test's teardown. That can surface as misleading follow-on errors or a leaked lock instead of the real failure.
