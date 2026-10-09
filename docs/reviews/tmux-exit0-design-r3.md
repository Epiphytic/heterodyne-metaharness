Reviewer: gpt-6.1-sol. Reviewed commit: 8241cb9. Verdict: REVISE.
Cycle cap reached; the single inventory finding was folded into 4caa3e0, verified by the controller.

The r2 parser finding is resolved: revision 3 strips the verified prefix before splitting the ID and name.

1. [BLOCKING] `docs/superpowers/specs/2026-10-08-tmux-exit0-design.md:133` — The fake-update inventory misses `Calls` in `tests/test_tmux_watchdog.py:30`. It returns empty stdout, and `_drive()` calls `new_session()`, so both launcher variants of both socket-selector tests would now raise `TmuxError`: four existing failures. **Fix:** include `Calls` in the required updates and make successful `new-session` calls return the nonce marker derived from argv, as specified for the other fakes.

REVISE