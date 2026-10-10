Reviewer: gpt-6.1-sol. Reviewed commit: c1e3f1f. Verdict: APPROVE.

Controller adjudication (cycle 2 -> 3): cycle 2 #1 reclassified NON-BLOCKING per ADR r15 section 7 (PreToolUse is the UX layer, the sandbox is the boundary); deferred with the timeout-cancel risk. No code change between r2 and r3.

No blocking findings remain under r15’s threat model. The settings isolation, anchored state discovery, and trust-response validation address the earlier blockers. Tests could not run because the filesystem denied temporary writes.

1. [NON-BLOCKING] `src/heterodyne/agents/base.py:29` — Killing the wrapper or reaching the CLI hook timeout can still permit a tool operation. Deferral is consistent with ADR r15 lines 422–427: the sandbox enforces security; hooks provide UX. **Fix:** later adopt a verified CLI-level blocking failure mechanism and test wrapper termination.

2. [NON-BLOCKING] `tests/fakes/fake_agent_cli.py:126` — The fake discards hook decisions and exit status and never exercises `PreToolUse`. It cannot establish that denial prevents an operation. **Fix:** add a fake tool operation with allowed, denied, and terminated-hook cases.

3. [NON-BLOCKING] `src/heterodyne/agents/claude_code.py:94`, `src/heterodyne/agents/codex.py:88` — Mid-session configuration changes remain unverified. Launch-time isolation and trust verification do not establish immutable hook behavior throughout a session. Under D24, missed hooks affect lifecycle handling rather than sandbox enforcement. **Fix:** verify pinned-CLI reload behavior and add adversarial mid-session tests.

APPROVE