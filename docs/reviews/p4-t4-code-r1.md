Reviewer: gpt-6.1-sol. Reviewed commit: feae4c6. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/sandbox/openshell.py:163` — `logs()` ignores the command’s return code and accepts stdout from a failed log query. I reproduced this with exit status 1 and a timestamped log line. Task 8 consumes these lines as mandatory supervisor corroboration, so partial output can satisfy the launch gate despite a failed evidence read. This defect is copied verbatim from the plan. **Fix:** reject nonzero status with a fixed-message `BackendError` or `BackendUnavailable` before parsing stdout; test failure with otherwise matching evidence.

2. [NON-BLOCKING] `src/heterodyne/sandbox/openshell.py:178` — `_container()` converts a failed `podman ps` into an empty list. Consequently, `kill()` returns normally without attempting a kill, conflating unreadable evidence with confirmed absence. The planned reaper retries regardless, which limits the impact, but the backstop silently masks failures. This defect is also copied verbatim from the plan. **Fix:** raise a fixed-message backend exception on nonzero status; test failed queries separately from successful empty listings.

The focused tests could not start because the read-only sandbox denied temporary-file creation, including under `/tmp`.

REVISE