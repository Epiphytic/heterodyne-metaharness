I'm clearing the bounded source, with no blockers. Nothing is wired to call `preflight_resume` yet. Two items (findings 1 and 3) need fixing before any production caller is connected to it, and the other eight are advisories.

**Effort and model.** My own effective reasoning effort is not exposed to me, so I can't confirm it. What I can see:
- **Model:** the system context says claude-opus-5-5.
- **Environment:** `CLAUDE_CODE_EFFORT_LEVEL=high` is set in this process.
- **Parent process command line:** `claude -p --settings {"disableAllHooks":true,"effortLevel":"high"} --model opus --effort high ...`
- **Global settings:** `~/.claude/settings.json` says `effortLevel: "medium"`. It is overridden for this process but not changed.

**What I did.**
- Read the full diff from d600e5d to 20ce906, plus `qualification.md` and `round1-disposition.md`, and the full functions the new code relies on.
- Ran the specified selection: 50 passed, 271 deselected, 75.87s.
- Ran three throwaway probe tests from `/tmp`, then deleted them. The worktree is still clean, and nothing touched the database, config, services or any managed tmux.

**What the probes showed.**
- **Ended session, normal case:** a session ended through `stop()` passes the preflight correctly. No test in the repo covers this case.
- **Ended record with a live pane:** it passes the preflight.
- **Running record whose pane was killed:** it also passes the preflight.

**Repairs checked and confirmed.**
- Owner failures now raise typed `RuntimeUnavailable` errors.
- Generation bounds are checked through `sandbox_name`.
- Legacy records without the create-ledger migration are held without being migrated.
- There is no import cycle with the helper.
- Owner cleanup runs in `finally`.
- `settle_wip=False` refuses before any settlement.
- The owner and record are rechecked at the end.

The disclosed limits (account/model not stored in the record, running sessions allowed for observation only, taint/alert side effects, the temporary credential lock) are accurately described and not overclaimed.

**Must fix before a production caller is wired.**
- **Finding 1:** an ended record whose pane or sandbox is still live passes the preflight. `observe_session` already refuses this case.
- **Finding 3:** there is no committed test for the normal ended-session case.

**Advisories:** findings 2 and 4–10 below.

10 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:694`: preflight_resume accepts an ENDED retained record whose tmux pane/sandbox are still live; the phase gate is only a string check in validate_identity and never repeats observe_session's 'an ended session still has live resources' refusal.

   Failure scenario: Probe: launch gen1, rewrite record phase=ENDED while pane is alive, call preflight_resume(gen2) -> returns success with rig.tmux.has_session(...) True. A future Hermes caller treating success as 'resume-ready' would relaunch next to a live executor. Fix: call observe_session(key) (or its RUNNING/ENDED resource checks) and compare with expected instead of the bare phase string check.

2. `src/heterodyne/sandbox/runtime.py:678`: RUNNING records pass preflight without any liveness determination, so a dead or uncertain pane is reported identically to a live one.

   Failure scenario: Probe: launch gen1, tmux.kill(pane), preflight_resume(gen2) -> success. Disclosed as observation-only, but the method's success carries no live/dead bit, so the caller cannot distinguish 'live, must not resume' from 'dead, resumable' without a separate unsynchronized observe call.

3. `tests/test_sandbox_runtime.py:2733`: The only positive preflight test uses a RUNNING generation; the primary deadline-recovery case (generation ENDED via stop/expire, then preflight) has no positive test.

   Failure scenario: A regression in _resume/_land/pin behaviour after an end (e.g. landing changes the worktree HEAD or home state) would make every real ended-generation preflight refuse while all 50 selected tests stay green; the probe showed it currently passes, but nothing in-repo guards it.

4. `src/heterodyne/sandbox/runtime.py:721`: Credential checks use caller-supplied spec.account/credential_key with no binding to the retained generation, so a cross-account resume without handoff passes preflight.

   Failure scenario: Caller passes account='other' with that account's fresh credential_key and handoff=False -> _logins/_pinned succeed and preflight reports the original native conversation resumable under a different account (D7 says cross-account must be a fresh handoff). Disclosed limitation; Hermes must check the account pin independently.

5. `src/heterodyne/sandbox/runtime.py:699`: Preflight skips launch's create-ledger reconciliation (_restore_creates(key=...)) and does no read-only check of outstanding/unsettled creates for this key.

   Failure scenario: A late or unsettled sandbox create for the session key is still in the ledger; preflight succeeds while the real launch would first run restore/cleanup and might refuse or recover differently. Disclosed as remaining recovery work.

6. `src/heterodyne/sandbox/runtime.py:720`: _check_reasoning_resume already calls _resume and (for medium) _cli; preflight then calls _resume and _cli again, verifying the pinned install twice.

   Failure scenario: For a medium Codex profile each preflight hashes/walks the full install manifest twice and probes the home twice, doubling I/O and widening the window for taint side effects. Fix: have the reasoning gate return the VerifiedInstall/resume result, or check reasoning after a single _resume/_cli.

7. `src/heterodyne/sandbox/runtime.py:720`: The 'readonly' preflight can still write taint markers and queue InstallTainted notices through _cli -> _verified -> _tainted.

   Failure scenario: A preflight against a corrupted pinned install writes the taint marker and queues InstallTainted signals for every live session on that install. Disclosed, but it contradicts 'no lifecycle effects' for any caller that assumes the preflight has no side effects at all.

8. `src/heterodyne/sandbox/resume_preflight.py:25`: validate_identity collapses about 17 distinct mismatch conditions (generation, native_id, resume/handoff, phase, ran/started_at, every pin) into one refusal string.

   Failure scenario: An operator or Hermes caller refused on a recovery cannot tell a stale generation from a changed install digest or a STOPPING phase; the disposition's 'actual refusal reasons' claim holds only for the later gates. Naming the mismatched field (path-free) would preserve diagnosability.

9. `src/heterodyne/sandbox/runtime.py:723`: The owner-check try/except block is duplicated verbatim at the start and end of preflight_resume.

   Failure scenario: Two copies of the same refusal mapping must stay in sync (exception set, message); a small _check_owner(owner) helper removes the duplication and the cognitive-complexity growth the ripwire delta reports (12->16).

10. `tests/test_sandbox_runtime.py:2785`: test_resume_preflight_refuses_changed_identity accepts either 'exact retained next-generation identity' or 'retained resume record changed' for every parameter, so it does not pin which gate refused.

    Failure scenario: If identity validation were weakened for a field and a different pre-identity gate later started refusing with the record-changed text, the case would still pass; asserting the specific reason per parameter (record-changed only for session_key) would catch it.

I'm clearing the bounded source, with no blockers. Nothing is wired to call `preflight_resume` yet. Two items (findings 1 and 3) need fixing before any production caller is connected to it, and the other eight are advisories.

**Effort and model.** My own effective reasoning effort is not exposed to me, so I can't confirm it. What I can see:
- **Model:** the system context says claude-opus-5-5.
- **Environment:** `CLAUDE_CODE_EFFORT_LEVEL=high` is set in this process.
- **Parent process command line:** `claude -p --settings {"disableAllHooks":true,"effortLevel":"high"} --model opus --effort high ...`
- **Global settings:** `~/.claude/settings.json` says `effortLevel: "medium"`. It is overridden for this process but not changed.

**What I did.**
- Read the full diff from d600e5d to 20ce906, plus `qualification.md` and `round1-disposition.md`, and the full functions the new code relies on.
- Ran the specified selection: 50 passed, 271 deselected, 75.87s.
- Ran three throwaway probe tests from `/tmp`, then deleted them. The worktree is still clean, and nothing touched the database, config, services or any managed tmux.

**What the probes showed.**
- **Ended session, normal case:** a session ended through `stop()` passes the preflight correctly. No test in the repo covers this case.
- **Ended record with a live pane:** it passes the preflight.
- **Running record whose pane was killed:** it also passes the preflight.

**Repairs checked and confirmed.**
- Owner failures now raise typed `RuntimeUnavailable` errors.
- Generation bounds are checked through `sandbox_name`.
- Legacy records without the create-ledger migration are held without being migrated.
- There is no import cycle with the helper.
- Owner cleanup runs in `finally`.
- `settle_wip=False` refuses before any settlement.
- The owner and record are rechecked at the end.

The disclosed limits (account/model not stored in the record, running sessions allowed for observation only, taint/alert side effects, the temporary credential lock) are accurately described and not overclaimed.

**Must fix before a production caller is wired.**
- **Finding 1:** an ended record whose pane or sandbox is still live passes the preflight. `observe_session` already refuses this case.
- **Finding 3:** there is no committed test for the normal ended-session case.

**Advisories:** findings 2 and 4–10 below.

10 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:694`: preflight_resume accepts an ENDED retained record whose tmux pane/sandbox are still live; the phase gate is only a string check in validate_identity and never repeats observe_session's 'an ended session still has live resources' refusal.

   Failure scenario: Probe: launch gen1, rewrite record phase=ENDED while pane is alive, call preflight_resume(gen2) -> returns success with rig.tmux.has_session(...) True. A future Hermes caller treating success as 'resume-ready' would relaunch next to a live executor. Fix: call observe_session(key) (or its RUNNING/ENDED resource checks) and compare with expected instead of the bare phase string check.

2. `src/heterodyne/sandbox/runtime.py:678`: RUNNING records pass preflight without any liveness determination, so a dead or uncertain pane is reported identically to a live one.

   Failure scenario: Probe: launch gen1, tmux.kill(pane), preflight_resume(gen2) -> success. Disclosed as observation-only, but the method's success carries no live/dead bit, so the caller cannot distinguish 'live, must not resume' from 'dead, resumable' without a separate unsynchronized observe call.

3. `tests/test_sandbox_runtime.py:2733`: The only positive preflight test uses a RUNNING generation; the primary deadline-recovery case (generation ENDED via stop/expire, then preflight) has no positive test.

   Failure scenario: A regression in _resume/_land/pin behaviour after an end (e.g. landing changes the worktree HEAD or home state) would make every real ended-generation preflight refuse while all 50 selected tests stay green; the probe showed it currently passes, but nothing in-repo guards it.

4. `src/heterodyne/sandbox/runtime.py:721`: Credential checks use caller-supplied spec.account/credential_key with no binding to the retained generation, so a cross-account resume without handoff passes preflight.

   Failure scenario: Caller passes account='other' with that account's fresh credential_key and handoff=False -> _logins/_pinned succeed and preflight reports the original native conversation resumable under a different account (D7 says cross-account must be a fresh handoff). Disclosed limitation; Hermes must check the account pin independently.

5. `src/heterodyne/sandbox/runtime.py:699`: Preflight skips launch's create-ledger reconciliation (_restore_creates(key=...)) and does no read-only check of outstanding/unsettled creates for this key.

   Failure scenario: A late or unsettled sandbox create for the session key is still in the ledger; preflight succeeds while the real launch would first run restore/cleanup and might refuse or recover differently. Disclosed as remaining recovery work.

6. `src/heterodyne/sandbox/runtime.py:720`: _check_reasoning_resume already calls _resume and (for medium) _cli; preflight then calls _resume and _cli again, verifying the pinned install twice.

   Failure scenario: For a medium Codex profile each preflight hashes/walks the full install manifest twice and probes the home twice, doubling I/O and widening the window for taint side effects. Fix: have the reasoning gate return the VerifiedInstall/resume result, or check reasoning after a single _resume/_cli.

7. `src/heterodyne/sandbox/runtime.py:720`: The 'readonly' preflight can still write taint markers and queue InstallTainted notices through _cli -> _verified -> _tainted.

   Failure scenario: A preflight against a corrupted pinned install writes the taint marker and queues InstallTainted signals for every live session on that install. Disclosed, but it contradicts 'no lifecycle effects' for any caller that assumes the preflight has no side effects at all.

8. `src/heterodyne/sandbox/resume_preflight.py:25`: validate_identity collapses about 17 distinct mismatch conditions (generation, native_id, resume/handoff, phase, ran/started_at, every pin) into one refusal string.

   Failure scenario: An operator or Hermes caller refused on a recovery cannot tell a stale generation from a changed install digest or a STOPPING phase; the disposition's 'actual refusal reasons' claim holds only for the later gates. Naming the mismatched field (path-free) would preserve diagnosability.

9. `src/heterodyne/sandbox/runtime.py:723`: The owner-check try/except block is duplicated verbatim at the start and end of preflight_resume.

   Failure scenario: Two copies of the same refusal mapping must stay in sync (exception set, message); a small _check_owner(owner) helper removes the duplication and the cognitive-complexity growth the ripwire delta reports (12->16).

10. `tests/test_sandbox_runtime.py:2785`: test_resume_preflight_refuses_changed_identity accepts either 'exact retained next-generation identity' or 'retained resume record changed' for every parameter, so it does not pin which gate refused.

    Failure scenario: If identity validation were weakened for a field and a different pre-identity gate later started refusing with the record-changed text, the case would still pass; asserting the specific reason per parameter (record-changed only for session_key) would catch it.
