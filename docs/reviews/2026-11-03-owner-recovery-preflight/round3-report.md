I found no critical flaws in dd20794, so the source is cleared as a PoC. The two repairs round two required before wiring are both done, and nothing calls `preflight_resume` in production yet.

**Model and effort.** I am claude-opus-5-5. `CLAUDE_CODE_EFFORT_LEVEL=medium` is set in this process. The parent command was `claude -p --settings {..."effortLevel": "medium"} --model opus --effort medium ... /code-review medium ...`. The review instructions loaded for me were the "high effort" recipe, but the invocation itself was medium, so treat this as a medium-effort run. My actual internal effort setting is not visible to me.

**What I did.** I read the diff from 551bda1 to dd20794, the full `preflight_resume` and `observe_session` functions, `validate_identity`, the test helpers, `prewiring-repair.md`, and the round-two disposition and report. The safe isolated selection passed: 3 passed, 321 deselected, 4.41s. The worktree is still clean. I made no edits, commits, DB, config, service or tmux changes.

**The two required repairs:**
- **Finding 1 (live resources on an ENDED record):** fixed at `runtime.py:701`. An ENDED record now goes through the existing read-only `observe_session` and must match the expected record exactly. That refuses a remaining sandbox or pane, an uninspectable pane, a backend that can't list sandboxes, and a record that changes during the check. It does not call the broad `sessions(ws)`. RUNNING records are still observation-only, as disclosed.
- **Finding 3 (positive ended test):** done. The normal stop-ended case is now committed, checking that the record bytes, Git HEAD and absent pane are preserved. The live-pane and live-sandbox refusals are tested separately: the pane case runs with backend names empty, and the sandbox case runs with the pane killed.

The separate gates are unchanged: original owners and UNKNOWN, death evidence, native, account, model and installer.

**Advisories (none blocks the PoC):**
6 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:701`: The live-resource check runs once, early; the closing recheck (line ~726) only re-reads the record and owner, not the sandbox/pane.

   Failure scenario: A pane or sandbox for the key appears while _cli/_logins/_pinned run (install hashing can be slow) -> preflight still succeeds. Mitigated because launch rechecks its own gates; re-running the observe_session comparison in the final recheck would close the window.

2. `src/heterodyne/sandbox/runtime.py:701`: The ENDED check only covers the retained generation's rec.sandbox, not the next generation's sandbox_name(key, generation) or outstanding create-ledger entries for this key.

   Failure scenario: A late or unsettled gen-2 create leaves sandbox <id>g2 alive -> preflight succeeds and the later launch has to reconcile it. Same as round-two advisory 5; Hermes or launch must keep that gate.

3. `src/heterodyne/sandbox/runtime.py:678`: RUNNING records still pass without any liveness answer (round-two advisory 2, unchanged by design).

   Failure scenario: Running record with a killed pane -> preflight succeeds and reports nothing about liveness. Wiring has to supply its own death or end evidence.

4. `tests/test_sandbox_runtime.py:2771`: The ended positive test doesn't patch _end/launch/_settle_wip/_rebind/_restore_creates to fail, unlike the RUNNING preservation test.

   Failure scenario: A regression where ended preflight calls _restore_creates or _rebind with no visible record/HEAD change would still pass. Reusing the 'unexpected' monkeypatch loop would pin the no-lifecycle-effects contract for ENDED too.

5. `tests/test_sandbox_runtime.py:2791`: The refusal tests check record bytes but not that the live pane or sandbox survives the refusal, and the TMUX_ERRORS and backend-unavailable branches have no ENDED-preflight test.

   Failure scenario: If a future change made the refusal path clean up resources, or turned a tmux error into success, these tests would stay green.

6. `src/heterodyne/sandbox/runtime.py:691`: For ENDED records the earlier read_record(layout) != expected check now overlaps with observe_session's own snapshot comparison.

   Failure scenario: Two record reads and comparisons per ENDED preflight. Minor redundancy, harmless, and kept for ordering with the identity gate.

Class maintenance debt from earlier rounds is still carried, as disclosed, and is not cleared by this round.

I found no critical flaws in dd20794, so the source is cleared as a PoC. The two repairs round two required before wiring are both done, and nothing calls `preflight_resume` in production yet.

**Model and effort.** I am claude-opus-5-5. `CLAUDE_CODE_EFFORT_LEVEL=medium` is set in this process. The parent command was `claude -p --settings {..."effortLevel": "medium"} --model opus --effort medium ... /code-review medium ...`. The review instructions loaded for me were the "high effort" recipe, but the invocation itself was medium, so treat this as a medium-effort run. My actual internal effort setting is not visible to me.

**What I did.** I read the diff from 551bda1 to dd20794, the full `preflight_resume` and `observe_session` functions, `validate_identity`, the test helpers, `prewiring-repair.md`, and the round-two disposition and report. The safe isolated selection passed: 3 passed, 321 deselected, 4.41s. The worktree is still clean. I made no edits, commits, DB, config, service or tmux changes.

**The two required repairs:**
- **Finding 1 (live resources on an ENDED record):** fixed at `runtime.py:701`. An ENDED record now goes through the existing read-only `observe_session` and must match the expected record exactly. That refuses a remaining sandbox or pane, an uninspectable pane, a backend that can't list sandboxes, and a record that changes during the check. It does not call the broad `sessions(ws)`. RUNNING records are still observation-only, as disclosed.
- **Finding 3 (positive ended test):** done. The normal stop-ended case is now committed, checking that the record bytes, Git HEAD and absent pane are preserved. The live-pane and live-sandbox refusals are tested separately: the pane case runs with backend names empty, and the sandbox case runs with the pane killed.

The separate gates are unchanged: original owners and UNKNOWN, death evidence, native, account, model and installer.

**Advisories (none blocks the PoC):**
6 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:701`: The live-resource check runs once, early; the closing recheck (line ~726) only re-reads the record and owner, not the sandbox/pane.

   Failure scenario: A pane or sandbox for the key appears while _cli/_logins/_pinned run (install hashing can be slow) -> preflight still succeeds. Mitigated because launch rechecks its own gates; re-running the observe_session comparison in the final recheck would close the window.

2. `src/heterodyne/sandbox/runtime.py:701`: The ENDED check only covers the retained generation's rec.sandbox, not the next generation's sandbox_name(key, generation) or outstanding create-ledger entries for this key.

   Failure scenario: A late or unsettled gen-2 create leaves sandbox <id>g2 alive -> preflight succeeds and the later launch has to reconcile it. Same as round-two advisory 5; Hermes or launch must keep that gate.

3. `src/heterodyne/sandbox/runtime.py:678`: RUNNING records still pass without any liveness answer (round-two advisory 2, unchanged by design).

   Failure scenario: Running record with a killed pane -> preflight succeeds and reports nothing about liveness. Wiring has to supply its own death or end evidence.

4. `tests/test_sandbox_runtime.py:2771`: The ended positive test doesn't patch _end/launch/_settle_wip/_rebind/_restore_creates to fail, unlike the RUNNING preservation test.

   Failure scenario: A regression where ended preflight calls _restore_creates or _rebind with no visible record/HEAD change would still pass. Reusing the 'unexpected' monkeypatch loop would pin the no-lifecycle-effects contract for ENDED too.

5. `tests/test_sandbox_runtime.py:2791`: The refusal tests check record bytes but not that the live pane or sandbox survives the refusal, and the TMUX_ERRORS and backend-unavailable branches have no ENDED-preflight test.

   Failure scenario: If a future change made the refusal path clean up resources, or turned a tmux error into success, these tests would stay green.

6. `src/heterodyne/sandbox/runtime.py:691`: For ENDED records the earlier read_record(layout) != expected check now overlaps with observe_session's own snapshot comparison.

   Failure scenario: Two record reads and comparisons per ENDED preflight. Minor redundancy, harmless, and kept for ordering with the identity gate.

Class maintenance debt from earlier rounds is still carried, as disclosed, and is not cleared by this round.
