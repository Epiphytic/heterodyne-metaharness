Reviewer: gpt-6.1-sol. Reviewed range: d06b004..e44c679. Verdict: APPROVE.

Deferred: separate spool/transcript variants of acceptance test 8.

1. **[NON-BLOCKING] R2 finding 5 is resolved.** `src/heterodyne/wsd/launches.py:31` rejects oversized generations; `src/heterodyne/wsd/park.py:802` escalates exhaustion before incrementing SQLite’s maximum. In-memory checks passed for both boundaries. **Fix:** none.

2. **[NON-BLOCKING] Interpretation 1: acceptable, as ruled in r2.** `src/heterodyne/wsd/settings.py:40` supplies host upgrade facts and workstream account views. Missing upgrade accounts propagate as `ConfigError` to EX_CONFIG. The protocol and `LaunchSpec` additions support pinning. **Fix:** none.

3. **[NON-BLOCKING] Interpretation 2: acceptable.** `src/heterodyne/wsd/upgrade.py:171` uses Claude’s session key and Codex’s recorded `thread_id`, otherwise leaving Codex’s native ID unset. **Fix:** none.

4. **[NON-BLOCKING] Interpretation 3: acceptable.** `src/heterodyne/wsd/park.py:587` checks adoption and reconciles before listed-session liveness; line 677 rebuilds every bead entry and rejects foreign workstream/bead references. **Fix:** none.

5. **[NON-BLOCKING] Interpretation 4: acceptable.** `src/heterodyne/wsd/journal.py:388` enables foreign-key enforcement on the working connection, preventing receipts without entries. **Fix:** none.

6. **[NON-BLOCKING] Interpretation 5: acceptable.** `src/heterodyne/wsd/upgrade.py:243` conservatively refuses release when the historical adapter is unknown and any adapter had configured accounts. Line 179 permits only timestamp differences in an otherwise identical adoption. **Fix:** none.

7. **[NON-BLOCKING] Interpretation 6: acceptable.** `src/heterodyne/wsd/recovery.py:107` leaves the relevant open escalation for step 4a, which settles adoption atomically with the handoff and replays that escalation. This preserves the required single escalation. **Fix:** none.

8. **[NON-BLOCKING] Interpretation 7: wrong as stated, but corrected in the code.** `src/heterodyne/wsd/recovery.py:159` converts adoption append conflicts/unreadable metadata to `BeadsUnavailable`, failing recovery and retaining the unsettled adoption. `NotOurs` only settles. This preserves r2’s ruling. **Fix:** update the author’s interpretation; no code change required.

9. **[NON-BLOCKING] Interpretation 8: acceptable.** `src/heterodyne/wsd/park.py:547` keeps the coder role occupied while the uncertain session remains listed. The affected bead stays STUCK; another bead waits BUSY. **Fix:** none.

10. **[NON-BLOCKING] Upgrade and replay meet the PoC safety bar.** `src/heterodyne/wsd/upgrade.py:256` commits schema, adoption rows, entries and version in one transaction, without `executescript` on the upgrade path. All eight checkpoint rollback checks passed in memory with nonempty adoption/entry inserts, followed by successful retries. `src/heterodyne/wsd/park.py:632` completes receipt-backed dispatches without relaunching; abandonment drops the pin atomically, and no-receipt dispatches remain unresolved. I found no remaining blocking duplicate launch, guessed-account launch, fabricated outcome, entry loss or half-applied upgrade. **Fix:** none.

11. **[NON-BLOCKING] Acceptance coverage is sufficient for this final PoC cycle, with verification limits.** `tests/test_wsd_launches.py:450` and `tests/test_wsd_journal_upgrade.py:119` cover the principal receipt, reconstruction, adoption and upgrade cases. Tests 1–13 were reviewed; existing expectation changes follow the spec’s changed uncertain-launch semantics. Q’s direct insertion and dedicated abandonment crash tests remain acceptable. Separate spool/transcript variants remain uncovered. Pytest could not run because the sandbox forbids temporary-file creation. **Fix:** add those variants and run the suites in a writable test environment. The code is good enough for a PoC to merge.

APPROVE
