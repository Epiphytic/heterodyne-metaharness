# Plan 4 Task 14 code review, cycle 1

Reviewer: gpt-6.1-sol. Reviewed commit: 9dcc09d352cba275b53e7c0166aa657d44758eb7. Verdict: REVISE.

Note: fixed in 4953a7c (finding 3), 4f9eb1e (1) and 4e916ea (2).

1. [BLOCKING] `tests/live/test_live_sandbox.py:217` — Teardown suppresses `RuntimeUnavailable`, then kills the tmux server and deletes the scratch directory. When sandbox deletion fails, this removes the surviving reaper and recovery records while leaving the sandbox running. Other exceptions can also skip the remaining cleanup. **Fix:** attempt cleanup for every session, collect failures, and confirm resource removal. Preserve records and the reaper for uncertain teardown, and fail the fixture visibly.

2. [BLOCKING] `tests/live/test_live_sandbox.py:199` — The test does not record the OpenShell/podman/CLI versions or runtime image digest claimed in the brief. Its evidence prints only models, freeze attempts, curl, and policies. D23 explicitly says filter counts cannot establish artifact identity, so a passing run lacks the required artifact evidence. **Fix:** add a bounded preflight that checks the approved OpenShell artifact and driver configuration, and records sanitized versions and the actual runtime image digest before launching.

3. [NON-BLOCKING] `src/heterodyne/sandbox/channel.py:157` — Polling can exceed the deadline by `FREEZE_POLL`: it sleeps the full interval even when less time remains, and checks the deadline only after the next read. The new deadline test explicitly accepts this overrun. Acceptance remains fail-closed through the later budget checks, but the stated hard wait cap is unmet. **Fix:** check the remaining budget before polling and cap each sleep to that remainder; reject a frozen result observed after the deadline.

REVISE