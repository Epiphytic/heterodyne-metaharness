Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 012a2bb92dcfb5f957c0ee79605464bf8ebf3c02 (range 5105158ccc8b1a4be22844520a93e2b6f1cc094b..012a2bb92dcfb5f957c0ee79605464bf8ebf3c02). Verdict: APPROVE.

Note: the one non-blocking finding (assert the SandboxRuntime signals() result, not only its presence) is deferred per the review-loop rule; Task 9 exercises the drain.

[NON-BLOCKING] `tests/test_wsd_signals.py:37–40`: The SandboxRuntime test checks method presence without calling `signals`. If it returned `None`, this test and pyright could pass, but Task 9’s consumer would fail when iterating the drain. Assert the actual empty-list result.

No blocking findings. Task 1 matches the approved design and ADR; exclusion only removes candidates, and every capability remains off.

Validation: 1,096 offline wsd tests passed, 2 skipped; ruff, pyright and diff checks passed.

Verdict: APPROVE