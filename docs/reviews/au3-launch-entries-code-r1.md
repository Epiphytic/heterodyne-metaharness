Reviewer: gpt-6.1-sol. Reviewed range: d06b004..f05adbe. Verdict: REVISE.

1. **[BLOCKING] `src/heterodyne/wsd/park.py:709` — Crash replay can dispatch an abandoned generation.** The already-live branch abandons the pinned entry but leaves `generation` in the open operation. Crash at `abandoned` or `abandoned!`, then let the listed session end: replay reaches `_pin_and_dispatch` and launches that abandoned generation. An in-memory reproduction produced a `started` receipt followed by `EntryConflict` when setting `launched`. **Fix:** journal abandonment and completion of the already-live operation atomically; prevent dispatch of any entry with an outcome. Test both checkpoints with the session ending before replay.

2. **[BLOCKING] `src/heterodyne/wsd/launches.py:30` — Invalid generations crash reconstruction.** `decode_launches` accepts generation 0 or negative generations. `_reconcile` then inserts them into `launches`, raising an uncaught SQLite `IntegrityError` instead of escalating `UNEXPECTED_STATE`. This was reproduced in memory. **Fix:** enforce `generation >= 1` during decoding and raise `LaunchesUnreadable`; test the guard with malformed bead metadata.

3. **[NON-BLOCKING] `src/heterodyne/wsd/recovery.py:157` — Interpretation 7 changes the specified startup failure contract.** The spec says an adoption append failure makes recovery fail and prevents pickup. Here, conflicts become a settled escalation and recovery can succeed. This safely stops the affected bead, but contradicts the approved failure behavior. **Fix:** propagate the append failure as `BeadsUnavailable`, keeping adoption unsettled, or obtain approval for the changed contract. The `NotOurs` handling is acceptable.

4. **[NON-BLOCKING] `tests/test_wsd_launches.py:212` — Acceptance test 1 passes vacuously.** The two account views are constructed, but neither participates in the session-key assertion or a launch. The assertion compares a fixed `role_session` call with the same precomputed value. **Fix:** exercise launches under both account views and verify identical session identity and unchanged `wsd_session`.

Interpretations **1–6 and 8 are acceptable** for this PoC; interpretation **7 is wrong about append-failure handling**, as described above. Q’s direct insertion and dedicated abandonment crash tests are acceptable coverage choices; spool/transcript variants remain untested.

Pytest could not start because the sandbox forbids temporary writes. In-memory rollback checks passed at all eight upgrade checkpoints with nonempty adoption and launch inserts; the upgrade commit path uses no `executescript`.

REVISE
