Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 18eafa0249b505d76e15f194b189ab035f1dbbf1 (range fe64b01bb13ff1fda4e1b1d69ae3eeec46985b06..18eafa0249b505d76e15f194b189ab035f1dbbf1). Verdict: REVISE.

Note: both findings fixed in 9b102be under controller option A (start-time rule, see PR body).

- [BLOCKING] `src/heterodyne/sandbox/hostread.py:129` — Recovery skips every `PermissionError` as though the process belonged to another user. A same-user app-server can make `/proc/<pid>/environ` unreadable with `prctl(PR_SET_DUMPABLE, 0)`. Reproduced: `recover()` logged `DIAG_RECOVERED` and deleted the scratch home while the matching process remained alive, destroying the next startup’s recovery record. Only skip independently verified foreign-user processes; otherwise treat the scan as incomplete, retain the homes, and log `DIAG_RECOVERY_KEPT`. Add a regression test for an unreadable same-user environment.

- [BLOCKING] `src/heterodyne/sandbox/hostread.py:247` — Normal cleanup removes the scratch home without confirming that escaped descendants have ended. The shared client kills only its process group; a descendant calling `setsid()` survives. Reproduced using the existing fake’s `escaped` fault: the read timed out and emptied the scratch directory, but the descendant remained alive after the credential lock was released. It can retain copied credentials, and startup recovery loses its marker. Confirm and terminate remaining scratch-home holders before deletion, preserving the home when cleanup is unconfirmed. Add host-read coverage for escaped descendants.

The authorized offline suite passed: **216 tests**. Ruff and diff whitespace checks passed. Capabilities remain off. No repository files were edited.

Verdict: REVISE