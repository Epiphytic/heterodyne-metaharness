Reviewer: gpt-6.1-sol. Reviewed commit: 749302d. Verdict: REVISE.

Controller adjudication at cycle 3: path 2 of finding 1 (an outstanding create across recovery ENDED or a generation replacement) is deferred to btq-7hokw. Settling it needs a cancellation proof OpenShell lacks, or a new watcher-bound policy; that is a design decision. Path 1 was fixed in 7880c46.

Cycle 1 finding 1 is fixed. The environment regression meaningfully checks retained variables, overrides and secret exclusion. Targeted pytest execution was blocked by denied temporary-file creation, including under `/tmp`; in-memory checks succeeded.

1. [NON-BLOCKING] `src/heterodyne/sandbox/runtime.py:395–404`, `src/heterodyne/sandbox/reaper.py:48–54` — **Cycle 1 finding 2 remains partially unresolved: settlement still permits late creation after the watcher exits.** Two paths remain:
   - wsd pauses after the deadline check but before durably writing `create_started=True`. The watcher sees “never submitted” and exits after the linger. wsd resumes, writes the mark, creates the sandbox and crashes before cleanup.
   - A submitted create remains outstanding when recovery confirms current absence and records `ENDED` (`runtime.py:565–573`). `settled()` accepts that phase, or a replacement generation, although neither proves the outstanding create was cancelled. It can complete after the watcher exits.

   In-memory controls confirmed watcher exit at 259,200 seconds for both record states, followed by an unwatched late sandbox. The new tests cover an outstanding create before recovery, but explicitly accept recovery’s premature settlement.

   **Fix:** durably mark creation outstanding before starting the watcher, then check the deadline before submission. Preserve outstanding-create evidence across teardown and generation replacement until completion or cancellation is confirmed; `ENDED` alone must not settle it. Add regressions for both paths beyond the linger limit. This retains the adjudicated proof-of-concept severity.

REVISE