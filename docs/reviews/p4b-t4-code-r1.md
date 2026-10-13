Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 5e9e459e0dc62e29f2b876d7c5d01bcdae73afb3 (range 5105158ccc8b1a4be22844520a93e2b6f1cc094b..5e9e459e0dc62e29f2b876d7c5d01bcdae73afb3). Verdict: APPROVE.

Note: the one non-blocking finding (a concurrent close-vs-callback test) is deferred per the review-loop rule.

[NON-BLOCKING] tests/test_session_server.py:320 — The post-close test doesn’t exercise an active callback racing with `close()`. A regression moving callback execution outside the lock could let `close()` return before that callback finishes while this test still passes. Add a deterministic concurrent shutdown test. The current locking appears correct.

The diff matches Task 4 and stays within ADR r16. All 275 targeted offline tests pass; pyright, ruff, and diff checks pass. No blocking findings.

Verdict: APPROVE