**Verdict: REVISE.** I reviewed the full range 35176c85..c4e06634 (approvals.py, daemon.py, store.py, the new tests and the R1 docs) offline and read-only. I ran no tests, probes, DB, native or network calls.

These parts hold up:
- The exit-75 check accepts only the exact closed HOLD JSON.
- The retry entitlement is used up in a transaction before the child launches.
- Only an exact matching native record settles, and a lost compare-and-set is now audited.
- Undecided beads that aren't open, and conflicting native decisions, stay held.
- Rollback overlay rows are replaced or cleaned up only for the exact current attempt.

The blocker is new: on every restart, and during any subscription reconnect, the retry loop posts a false "authority" hold notice. Two evidence gaps also remain. The quality gate exits 2 with its debt unwaived, and the notice's promised "operator recovery" has no implemented path, which the R1 dispositions admit. This is not proof that the reserved deployment profile works.

10 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:1561`: retry_maintenance treats a temporary loss of authority (group_ok/observing False at startup or during a reconnect, or a membership change in progress) as revoked authority, and posts a durable 'authority' hold notice saying operator recovery is required.

   Failure scenario: admind restarts with one held ask that is past its retry time. The R1 #8 fix runs overdue work before the first sleep, so the loop calls retry_maintenance before the subscription is acknowledged. check_group() before the loops returns 'superseded' while acked is False, so group_ok and observing are still False and authorised(mid) is False. hold_maintenance(a, 'authority') then queues 'held (authority) ... changed authority require operator recovery' for every held attempt on every restart, and on any later stream reconnect. The operator is told his authority changed when it did not.

2. `src/heterodyne/admind/daemon.py:1580`: Holds for native-conflict, changed-card and authority never end, and no recovery command exists. Each one stays 'deciding', which counts toward MAX_OPEN=20 and makes the bead ASK_BEAD_BUSY, so the 128-row capacity guard (run_attempt:1306, store.py:1003) can never be reached through normal admission.

   Failure scenario: After a maintenance window, the host approves several held beads natively, which gives a different via_ref. Every retry then holds as native-conflict forever. cancel_ask refuses 'deciding', and approval_post_refusal returns ASK_BEAD_BUSY for those beads. Once 20 asks are held, post_ask and post_approval refuse every new ask with ASK_TOO_MANY. The notice points the operator to an 'operator recovery' that has no command, so the harness cannot recover through Marmot. The 128 checks and the RuntimeError branch are dead code.

3. `src/heterodyne/admind/daemon.py:1327`: Held attempts never expire, so a decision held during a latch or a revocation runs automatically once authority comes back, even much later, and nobody confirms it again.

   Failure scenario: The operator approves during maintenance and the attempt is held. A membership anomaly then latches admind. The normal path would drop the decision through deny_rows, but the parametrized 'latch' test confirms it stays held. Days later, after rearm (or after a removed operator is added back with the same sender key), the next 30s retry finds authorised(mid) True and the digest unchanged, and issues the old approval with no fresh confirmation.

4. `src/heterodyne/admind/daemon.py:1506`: Removed behaviour: reconcile_one now returns early for held attempts before the approve_bead-is-None branch. With approvals unconfigured (a rollback config), retry_maintenance labels the hold 'authority' instead of answering it once through unverified().

   Failure scenario: The operator removes approve_bead from the config, for example during a rollback, and restarts. Before this change, unverified() posted its notice, marked the inbound done and settled the ask. Now the overlay causes a skip, and retry_maintenance holds it as 'authority' every 30s forever. The operator gets a wrong reason, and the inbound stays 'executing'.

5. `src/heterodyne/admind/daemon.py:1363`: In run_attempt's retry path, every non-None stop is labelled 'changed-card': Busy readouts, approve-bead read errors (BtqError 'bad output' or 'unavailable'), a native decision made between the two reads, and a spawn OSError before launch.

   Failure scenario: On a retry, the second ab.read returns Busy (exit 4) because btq is briefly locked. preflight returns ('open', BEAD_BUSY, 'busy'), and hold_maintenance(a, 'changed-card') posts a durable 'held (changed-card)' notice. The card is unchanged and the condition is temporary, but the notice says operator recovery is needed. retry_maintenance itself uses 'read-unavailable' or 'native-conflict' for the same conditions.

6. `src/heterodyne/admind/daemon.py:1292`: The hold notice always says 'approval received and held', even when the held attempt is a deny.

   Failure scenario: During maintenance the operator replies '!deny bd-x too risky' and gets HOLD. The threaded notice says 'Ask p4xw: approval received and held (maintenance)', so it looks as if admind took his deny as an approval.

7. `src/heterodyne/admind/daemon.py:1334`: hold_maintenance runs inside run_attempt's try after launched=True, so any exception from defer_maintenance or ask_notice (sqlite locked or busy, RuntimeError at capacity, enqueue ValueError) sends a proven-no-effect HOLD down the ambiguous read-back path, which consumes the decision.

   Failure scenario: The child returns the exact HOLD, then defer_maintenance hits 'database is locked' after the 5s timeout. The transaction rolls back and the except branch skips the stop because launched=True. read_and_settle returns 'untouched', and the ask closes 'open' with the UNTOUCHED reply. The approval that should have been kept is discarded, contrary to D §4 ('do not consume/reject').

8. `src/heterodyne/admind/daemon.py:1567`: Every retry runs two native reads under work_lock (read_and_settle with busy retries, then run_attempt's ab.read), and holds that can never succeed (a closed bead decided natively, or changed-card) keep polling native every 30s forever.

   Failure scenario: With 20 permanently conflicting holds, admind starts at least 40 approve-bead children a minute indefinitely, more when btq is busy, all while holding work_lock. This delays operator messages and adds native load with no possible progress. Holds whose native state is terminal should stop polling.

9. `src/heterodyne/admind/store.py:1015`: maintenance_pending() runs a correlated DELETE (a write transaction) on every call. It is called twice per loop iteration, once more per retry_maintenance (only to find one row by key), once per reconcile_one (once per deciding ask in the !asks backstop), and before every decision in run_attempt.

   Failure scenario: Each !asks command and each operator decision takes a SQLite write lock for the cleanup DELETE, and retry_maintenance scans up to 128 rows to read one due time. Cleanup should run once (at startup, or in close_attempt or defer), and lookups should use an indexed SELECT by (ask_id, message_id).

10. `src/heterodyne/admind/daemon.py:1548`: If an overdue overlay is not moved forward (retry_maintenance raises before defer_maintenance, or returns early on row.bead None), the loop computes a negative delay and spins at 0.1s, taking work_lock and writing a 'maintenance-retry-failed' audit line every iteration.

    Failure scenario: current_attempt or ask raises sqlite3.OperationalError while the DB is locked for a long time. The due time stays in the past, so the loop retries 10 times a second, flooding the audit log and contending for work_lock with the worker. The 30.0 and 128 literals are still repeated across daemon.py and store.py instead of named constants (R1 #9), and the row.digest != a.digest check at line 1583 can never be true.
