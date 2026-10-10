Reviewer: gpt-6.1-sol. Reviewed commit: 2b47ad6. Verdict: REVISE.

1. [BLOCKING] `tests/test_sandbox_scheduler.py:40–53` — The fixture opens a real SQLite Journal but never closes it. Its descriptors remain open until garbage collection, which can occur during `test_wsd_gate`’s descriptor-count assertion and cause an unrelated failure. This defect is copied from the plan. **Fix:** register `rig.journal.close()` immediately after `make_rig`, with cleanup guaranteed even if runtime setup or tmux teardown raises, before removing the scratch directory.

The real scheduler, parker and guard paths are exercised; the lifetime and refusal assertions match the merged contracts. Persistent WIP failure reaching `needs-human` through the launch budget is acceptable. Task 12 must still address the separately adjudicated unlanded park/defer hold.

Test execution was blocked by uv attempting to write its cache on the read-only filesystem; no test result was obtained.

REVISE