Reviewer: gpt-6.1-sol. Reviewed commit: c9c72fc. Verdict: REVISE.

1. [BLOCKING] `src/heterodyne/sandbox/runtime.py:535–536, 164–169` — **The late-create race remains when wsd cleans up or relaunches.** An OpenShell create request can time out after submission while creation remains outstanding. `_fail` calls `_end`; `_teardown` observes no sandbox and confirms absence, so `_end` kills the reaper and records ENDED. If creation subsequently completes and wsd crashes, that sandbox has no lifetime backstop. An in-memory reproduction returned `LaunchFailed`, removed the reaper, then admitted the late sandbox without a watcher. Furthermore, `_reaper` unconditionally removes the previous generation’s watcher before launching the next generation, producing the same exposure even if cleanup left that watcher alive.

   Indefinite polling fixes the reaper’s autonomous exit, but **absence still does not establish creation quiescence**. This remaining defect comes from the plan’s teardown/replacement contract. The new delayed-create test passes but never exercises wsd cleanup or replacement.

   **Fix:** retain each generation’s watcher until its creation is conclusively completed or cancelled and teardown is confirmed. Give retained watchers generation-specific tmux names so a subsequent launch cannot remove them. Add regressions for create timeout → absence-confirmed cleanup → late creation → wsd crash, and for late creation after the next generation launches.

REVISE