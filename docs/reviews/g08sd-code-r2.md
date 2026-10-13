Reviewer: gpt-6.1-sol. Reviewed commit: 46a97209ccc6285f7d86b0e59580df8e03810180. Verdict: APPROVE.

Note: code-review cycle 2 of 3, range 850d531..46a9720; verifies the r1 fixes 20216e0 and 46a9720 and the merge 575c34f.

No BLOCKING findings. Reviewed against the approved design at `5ccb0f3`; no files modified or tests run.

- **NON-BLOCKING — r1 finding 1 resolved.** `20216e0` prevents teardown when the write carrying a new healthy cause fails. The stop/expire regression verifies resources remain live across restart, then confirms the retried cause. Without the fix, its resource and restart assertions fail.
- **NON-BLOCKING — r1 finding 2 resolved.** `46a9720` exercises marker → real runtime → scheduler guard → reset, then verifies a later death counts as 1. Missing marker consumption or lifetime reset would prevent the expected third launch or fail the counter assertions.
- **NON-BLOCKING — rebuilt accounting covered.** Test 14b verifies rebuilt generations remain `reset`, while a generation launched afterward counts normally.
- **NON-BLOCKING — merge resolution and additions sound.** `_claim_branch` retains same-key cleanup before checking other holders and runs before `_resume`; it does not overwrite end evidence or bypass accounting. Marker reads reject links and special files and hold on other read errors. Expiry continues after an individual runtime failure and reports the hold afterward. I found no new false counts, unbounded relaunches, forgeable markers, lost or duplicate accounting, or partial accounting transactions.

Verdict: APPROVE