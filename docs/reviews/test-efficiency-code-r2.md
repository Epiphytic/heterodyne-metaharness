Reviewer: gpt-6.1-sol. Reviewed commit: 8e45dbe. Verdict: REVISE.

1. [BLOCKING] `tests/tier_marks.py:38–40,64–70` — Cycle 1 finding #1 is only partially fixed. The three named cases are protected, but safety classification inside `test_tmux*` and `test_wsd_*` still depends on names. Renaming a digest test to omit the keywords makes it slow-eligible. Explicit `@pytest.mark.safety` is also ignored: I reproduced an eligible item retaining both `safety` and `slow` without a collection error, so `test-fast` drops it. **Fix:** explicitly mark safety tests in eligible files, honor collected safety markers when rejecting slow items, and add regression coverage for a renamed safety test and an item carrying both markers.

Cycle 1 finding #2 is fixed: capacity checks and directory-creation fallback are committed and covered. CI remains full and serial; diagnosis preserves the original exit code. The requested pytest run could not start because uv’s cache lock required a write denied by the read-only filesystem.

REVISE