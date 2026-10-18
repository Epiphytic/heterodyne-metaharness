Review header workspace path normalized for install-agnostic publication; all ten ordered findings unchanged. Exact original report SHA256: 5168a4f9468f2e0317d629ff4cbe6588c0b2a3297af904a3f2a246389147e70f.

**Verdict: REVISE.** The reviewed range is 35176c85..d8d9c88 in <TASK_WORKTREE>, one commit touching approvals.py, daemon.py, store.py and tests/test_admind_maintenance.py. I worked read-only from the diff, the enclosing code and the evidence files: no edits, tests, probes, DB, native or network access.

**What holds up:**
- **Exit-75 check:** only exit 75 plus the exact closed, duplicate-key-rejecting stdout JSON of 256 bytes or less becomes a HOLD.
- **Ambiguous outcomes:** a timeout, malformed output or launched exception still goes through the normal read-back and never becomes a retry.
- **Retry entitlement:** the stored retry row is deleted in a transaction just before the child launches, so a crash after that commit gets the normal read-back.
- **Operator-name race:** in the retry path the name is re-checked after the native read, with no await before the launch.
- **Original attempt kept:** the original Attempt, ref and inbound message survive a restart, because `recover()` and `reconcile_one` skip held attempts.
- **Schema migration:** the change is additive (`CREATE TABLE IF NOT EXISTS`).

**Main blocker:** a held attempt has no way to finish once the card's digest, the gate, the approver list or the operator's authority changes. The operator gets no reply, the bead can't be re-posted, and the 128 retained slots fill up permanently.

**Evidence gaps for PoC suitability:**
- The broad suite (258 passed) ran before the final race and classification changes. Only the 28 focused tests ran on d8d9c88.
- The quality-delta check exited 2 with 26 gating rows and no acknowledgement. `run_attempt` complexity went from 32 to 45.
- No test exercises `maintenance_retry_loop`, the capacity limit, the not-open case in finding 2, or leftover rows after a rollback.

Production activation and the queue-coordinator co-deploy were out of scope, as stated. Nothing here counts as reserved-profile admission proof.

10 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:1560`: Retry-path holds (changed-card/stale digest, gate refused, not an approver, revoked/renamed operator) never end: the ask stays 'deciding' for good, is never refreshed or settled, and has no exit path.

   Failure scenario: Operator approves during maintenance and gets HOLD. Maintenance ends, but the bead was edited meanwhile (digest changed). retry_maintenance sees untouched, preflight returns 'stale', and it audits 'changed-card' and defers again, every 30s forever. Normally refresh() would post a fresh card. Because the ask is 'deciding', cancel_ask refuses it, commit_approval won't supersede it, and approval_post_refusal returns ASK_BEAD_BUSY. The bead can't be decided in-band, the ask takes a MAX_OPEN slot, and leftover holds fill the 128-row limit so that later HOLDs raise 'maintenance pending capacity'.

2. `src/heterodyne/admind/daemon.py:1549`: retry_maintenance uses settle(), the after-the-decision classifier, for an attempt that is proven to have written nothing, so a bead that is merely not open (and undecided) ends the ask as terminal 'blocked' instead of the preflight NOT_DECIDABLE result that returns it to 'open'.

   Failure scenario: HOLD, then someone moves the bead to in_progress/deferred without deciding it. Read-back: status != open and decided=False, so settle() returns 'blocked'. close_attempt sets the ask to 'blocked' (terminal) and the reply says a decision blocked it. Normal preflight would have returned ('open', NOT_DECIDABLE) and kept the ask decidable. The design also says a conflict should stay held visibly, not settle.

3. `src/heterodyne/admind/daemon.py:1316`: On HOLD (and on every maintenance-held branch at 1332, 1348, 1543 and 1561) the operator gets no Marmot reply or notice. The only trace is an audit line and asks.outcome='pending-maintenance', which shows only in the CLI 'ask get', so the 'holds visibly' requirement is not met.

   Failure scenario: Liam reacts 👍 on a card during maintenance. The inbound stays 'executing' with no reply, and '!asks' just shows it in the list. Revocation or changed-card holds then repeat silently every 30s. From his phone he can't tell whether the approval was received, held or lost, and can't act on it.

4. `src/heterodyne/admind/daemon.py:1550`: The recorded/blocked settlement in retry_maintenance writes no 'decided'/'reconciled' audit record and ignores a failed close_attempt compare-and-set (no conflict() call), unlike every other settlement path.

   Failure scenario: A held approval is found already recorded natively. The ask closes as 'approved' and the reply is queued, but the audit log has no settlement entry for that ask or message, so the trail from 'pending-maintenance' to the terminal status is broken. A lost compare-and-set is equally silent.

5. `src/heterodyne/admind/store.py:1005`: defer_maintenance's upsert silently does nothing when an existing row for the ask has a different message_id but still returns True, and nothing ever garbage-collects rows whose attempt no longer matches (retry_maintenance just returns early).

   Failure scenario: Rollback to the old admind: its close_attempt doesn't delete ask_maintenance rows, and a new attempt is made. On re-upgrade, a HOLD on the new attempt: the upsert is skipped, outcome is set, True is returned, but the row still names the old message ID. retry_maintenance(old mid) returns because of the attempt mismatch. The new attempt is stuck in 'deciding' until a restart or the !asks backstop, and the stale row takes a capacity slot forever.

6. `src/heterodyne/admind/store.py:1004`: When the 128-row limit is reached, the RuntimeError raised inside run_attempt's try is caught after launch, so a proven-no-effect HOLD goes through the normal read-back and closes the ask 'open' with the generic UNTOUCHED reply. No capacity-specific audit or wording is produced.

   Failure scenario: 128 leftover holds (see finding 1). The next HOLD raises, launched=True, read_and_settle returns untouched, and the ask is closed with exit_status None and outcome 'decided untouched'. The decision is dropped instead of retained, and the operator isn't told it was a capacity problem.

7. `src/heterodyne/admind/daemon.py:1548`: Each retry runs two native reads in a row (retry_maintenance's read_and_settle, then run_attempt's ab.read), each spawning an approve-bead child (and the read-back retries while busy), all under work_lock.

   Failure scenario: With up to 128 held asks during maintenance, when reads may be busy, every 30s cycle spawns twice the needed approve-bead children and holds work_lock longer, delaying operator messages. Passing the readout from retry_maintenance into run_attempt, or skipping the duplicate preflight, would halve this.

8. `src/heterodyne/admind/daemon.py:1519`: The retry cadence is a fixed 30s sleep separate from retry_at, which is itself set to now+30 before the check, so the actual retry interval is 30-60s and a restart waits a full 30s before resuming held items.

   Failure scenario: A HOLD at t=0 sets due=30. The loop wakes at t=29 (not due) and next at t=59, so the retry runs at about 59s, not the designed 30s. After a restart, overdue held asks still wait 30s.

9. `src/heterodyne/admind/daemon.py:1285`: The maintenance_retry flag is threaded through three branches of run_attempt, duplicating the defer+audit hold block (and the literal 30.0, five times) instead of a single hold(a, reason) helper. This raised complexity from 32 to 45, and the quality-delta gate exited 2 with 26 unacknowledged gating rows.

   Failure scenario: Maintenance cost: future edits to the hold semantics must change five call sites the same way. The quality gate currently fails with no acknowledgement in the evidence, so the code-quality gate is not met.

10. `tests/test_admind_maintenance.py:64`: No test exercises maintenance_retry_loop itself, the 128-row capacity limit, a held changed-card/revoked ask ending, the not-open case going to 'blocked', or leftover overlay rows after a rollback. The 258-test broad suite ran before the final race and classification changes.

    Failure scenario: The evidence (implementation-evidence.json: 'broad_prior_to_final_race_regression') shows d8d9c88 was only checked by 28 focused tests, so regressions in the loop or other daemon paths from the final change would go undetected.
