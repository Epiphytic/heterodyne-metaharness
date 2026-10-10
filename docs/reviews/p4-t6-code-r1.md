Reviewer: gpt-6.1-sol. Reviewed commit: f504710. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/session/shim.py:59` — The socket timeout does not enforce the overall `wait_seconds` deadline. Every retry gets the full timeout, and `readline()` can continue indefinitely if bytes arrive before each socket timeout. An in-memory reproduction exceeded a 1-second budget by reaching 2 seconds. **Fix:** apply the remaining absolute deadline to every operation and bound reply length. Add retry and slow-reply tests.

2. [BLOCKING] `src/heterodyne/session/shim.py:96` — Spooling can hang the agent indefinitely. `$HOME` is agent-writable; if `.hz/spool.jsonl` is a FIFO without a reader, `open("ab")` blocks outside any timeout. **Fix:** open nonblocking with `O_NOFOLLOW`, verify through `fstat` that the descriptor is a regular file, and skip unsafe destinations. Add a bounded FIFO regression test.

3. [BLOCKING] `src/heterodyne/session/shim.py:71` — Untrusted hook inputs can crash the shim instead of returning a protocol decision. Reproductions produced `TypeError` for `tool_name: []`, `ValueError` for an edit path containing NUL, and `RecursionError` for deeply nested JSON at line 105. **Fix:** validate field types, bound JSON nesting, and handle path errors by denying PreToolUse through stdout with exit 0. Add adversarial input tests.

4. [BLOCKING] `src/heterodyne/session/shim.py:82` — Relative targets are resolved against the configured worktree rather than the tool’s actual working directory. A hook with `cwd="/outside"` and `file_path="a.py"` is classified as an allowed worktree edit. **Fix:** require absolute targets, or resolve relative targets against a validated hook working directory before checking containment. Test differing working directories.

5. [NON-BLOCKING] `src/heterodyne/session/shim.py:112` — Missing or unreadable configuration silently drops non-tool events without attempting to spool them. Separately, line 92 silently loses events when `$HOME` is missing. Creating the fixture home reflects the planned launch setup, but removes coverage of that loss condition. **Fix:** spool non-tool events on configuration failure; create missing parents where permitted and test missing-home behavior explicitly.

These defects are inherited from Task 6’s sample implementation. Existing tests contain meaningful assertions but miss these cases; execution was blocked because the review environment denied temporary-file creation.

REVISE