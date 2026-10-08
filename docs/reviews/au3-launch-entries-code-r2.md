Reviewer: gpt-6.1-sol. Reviewed range: d06b004..48a1bbb. Verdict: REVISE.

1. **[NON-BLOCKING] R1 #1 — Resolved.** `src/heterodyne/wsd/park.py:708` commits abandonment and operation completion together; line 745 also prevents dispatch of an entry with an outcome. The new crash tests cover both abandonment checkpoints with the session ending before replay. **Fix:** none.

2. **[NON-BLOCKING] R1 #2 — Resolved for zero and negative generations.** `src/heterodyne/wsd/launches.py:30` rejects these during decoding, and the added guard tests assert `UNEXPECTED_STATE` without another launch. A separate upper-bound defect remains below. **Fix:** none for the original cases.

3. **[NON-BLOCKING] R1 #3 — Resolved.** `src/heterodyne/wsd/recovery.py:157` now raises `BeadsUnavailable` for adoption append conflicts or unreadable metadata, preserving the unsettled row and preventing pickup. Interpretation 7’s withdrawal matches the spec. **Fix:** none.

4. **[NON-BLOCKING] R1 #4 — Resolved.** `tests/test_wsd_launches.py:263` exercises launches under two distinct credential keys and checks identical session keys and byte-identical `wsd_session`. The assertion is no longer vacuous. **Fix:** none.

5. **[BLOCKING] Oversized generations still crash the guard.** `src/heterodyne/wsd/launches.py:30` validates only the lower bound. A bead entry with generation `9223372036854775808` decodes successfully, then reconstruction at `src/heterodyne/wsd/park.py:682` raises uncaught `OverflowError: Python int too large to convert to SQLite INTEGER`. Generation `9223372036854775807` rebuilds successfully but causes the same exception when `_pin` increments it at line 794. Both failures were reproduced using the actual guard methods and an in-memory journal. This bypasses the specified escalation behavior for edited bead metadata. **Fix:** reject generations outside SQLite’s signed-integer range, and explicitly escalate generation exhaustion before incrementing the maximum. Add guard tests for both boundaries.

Interpretations judged individually: **1 acceptable** (accounts are host-only configuration, with workstream views); **2 acceptable**; **3 acceptable**; **4 acceptable**; **5 acceptable**; **6 acceptable** and preserves the required single escalation; **7 wrong as originally written, now correctly withdrawn and fixed**; **8 acceptable**.

The upgrade uses one transaction and no `executescript` on its commit path. In-memory rollback checks with nonempty adoption and launch inserts passed at all eight upgrade checkpoints. Review of acceptance tests 1–13 found no additional vacuous test or unjustified semantic change. Q’s direct insertion and dedicated abandonment crash tests are acceptable; separate spool/transcript variants remain uncovered. Pytest could not start because the read-only sandbox forbids temporary-file writes.

REVISE
