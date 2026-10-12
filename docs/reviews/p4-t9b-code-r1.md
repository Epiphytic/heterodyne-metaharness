Reviewer: gpt-6.1-sol. Reviewed commit: e0fe848. Verdict: REVISE.

The reviewer could not run tests in its sandbox (temp-file creation denied), so it reproduced the issue in memory. Fixed in fecb1af.

1. [BLOCKING] `src/heterodyne/sandbox/spec.py:205–207` — **Lexical normalization misses symlink paths into `/proc`.** A read-only reproduction showed `build_spec` accepts `/var/run/../proc`: `normpath` yields `/var/proc`, but with `/var/run -> /run`, kernel resolution reaches `/proc`. The original spelling remains in `read_write`, allowing Landlock’s writable rule to override `/proc` protection. `/dev/fd/..` is also accepted and resolves inside `/proc`. This defect follows the plan’s lexical check. **Fix:** validate paths against resolution in the sandbox’s filesystem, including symlinks and bind targets; reject unresolved aliases. Add regression tests covering both examples and a symlink targeting `/proc`.

Targeted tests could not run because the environment denied temporary-file creation, including under `/tmp`. The in-memory reproduction succeeded.

REVISE