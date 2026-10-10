Reviewer: gpt-6.1-sol. Reviewed commit: 714cb5f. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/session/server.py:108,144–151` — Shutdown races with thread-start failure. `_admit()` registers an unstarted thread before calling `start()`. If `close()` snapshots it and `start()` then fails, admission removes it, but shutdown still joins the stale snapshot and raises `RuntimeError: cannot join thread before it is started`, skipping socket-path cleanup. Reproduced without filesystem writes, including the acceptor join. Fix by synchronizing registration, thread startup, and failure cleanup with shutdown; join only successfully started threads. Add a deterministic regression test combining shutdown with thread-start failure.

The cap, request deadline, and malformed-input fixes otherwise address cycle 1. Focused tests could not run because temporary-file creation was denied, including under `/tmp`.

REVISE