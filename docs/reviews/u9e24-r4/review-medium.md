I read through `878b50e5..131fa103` and found 10 issues. None is a crash or data-loss bug. Two of them are gaps in how a permit gets issued, and I'd fix those before the executor is wired in. I ran no tests or probes, as the request said, and I didn't re-check the findings in a separate pass.

**Verdict:** it's good enough as a PoC while the executor stays separately gated. The permit path in `src/heterodyne/admind/recovery.py` has two gaps that matter once something consumes permits:
- **Enrollment not rechecked:** `permit()` never checks that the proposal's enrollment and source closure still match the current enrollment.
- **No window check on reissue:** issuing a new generation doesn't require the previous permit's 30-second window to have expired. Superseding does require it. So the queue's tombstone is the only thing stopping two live permits.

Two R3 claims hold only in part (findings 3 and 5): the final policy check happens outside the transaction, and the conflict record is skipped when the group check fails.

10 findings, most severe first:

1. `src/heterodyne/admind/recovery.py:865`: permit() calls self.enrollment() but never checks obj.enrollment_sha256 or obj.source_closure against the current enrollment (only register() does), so a proposal consented under an old source closure can still get a permit after the enrollment or sources change.

   Failure scenario: Proposal P is registered and consented under enrollment E1. The coordinator is redeployed with enrollment E2, which is internally consistent so enrollment() passes. `admind maintenance-recovery permit P` then issues a first-generation permit bound to E1's source closure under E2 code, defeating the source-drift fence that register enforces.

2. `src/heterodyne/admind/recovery.py:898`: A new permit generation (certificate path) checks the boot ID and certificate but not that the previous permit's initiate_before_boottime_ns has passed, unlike supersede(), which raises permit-active; nothing on the admind side stops two generations from being live at once.

   Failure scenario: Generation 1 is issued at time T. The queue writes a nonconsumption certificate at T+5s. Permit is called with that certificate at T+6s and generation 2 is published while generation 1's window runs to T+30s. If the queue's superseded tombstone isn't honoured exactly (it lives in a mutable coordinator that isn't frozen evidence), both permits can start recovery.

3. `src/heterodyne/admind/recovery.py:901`: The final authority check runs before the writer transaction. Inside the transaction, the compare-and-set only compares the stored consent row (including its old policy hash), not current host policy or operators, so the claim that the transaction rechecks policy and operators is only partly true.

   Failure scenario: The worker thread calls authority() and it matches. The event loop then processes a policy change or operator removal. The worker enters the transaction, finds row == observed, and issues and publishes the permit under the consent of an operator who has just been removed.

4. `src/heterodyne/admind/recovery.py:533`: Once any permit generation has been issued, a reboot permanently wedges the operation: supersede() raises boot-changed and permit() reissue raises boot-changed. The 'terminal' state and the terminal column are never written anywhere, so supersede's terminal branch is dead code.

   Failure scenario: A permit is issued, the host reboots before the queue consumes it or certifies nonconsumption, and every later register or reissue for that operation is refused forever. Recovering requires manually editing the database.

5. `src/heterodyne/admind/daemon.py:835`: recovery_message returns before service.message/refusal when check_group() fails (or the service is unavailable), so an observed replay-mismatch conflict is never written to maintenance_recovery_conflicts. This contradicts the R3 claim that conflict denials are persisted even when policy is unreadable.

   Failure scenario: A redelivered message ID arrives with different content while the group check is failing. The conflict is only audited, nothing is persisted, and a later permit() for that proposal does not see the conflict and is not refused.

6. `src/heterodyne/admind/operations.py:344`: withdraw() on an operation that was never posted enrolls it, permanently tombstones the bead, adopts the legacy asks for that bead, and cancels their open or answered asks.

   Failure scenario: A same-UID source sends withdraw for a new operation key on a bead with legacy asks and a matching context_digest. The operator's open legacy ask is cancelled, later legacy posts are refused with 'source-owned bead', and the operation can never be re-posted.

7. `src/heterodyne/admind/daemon.py:808`: register and permit run Service.enrollment(), which hashes up to 256 source files of up to 16 MiB each, while holding the daemon's work_lock, so operator message processing stalls for the whole hash.

   Failure scenario: With a large source closure, a permit request holds work_lock for many seconds. Operator !approve/!deny messages queue behind it, and the CLI's 10-second timeout reports 'unavailable' while the server still goes on to issue the permit.

8. `src/heterodyne/admind/store.py:406`: executescript(OPERATION_SCHEMA) implicitly COMMITs and runs its statements outside any transaction, and a failure there or in Retirement.migrate() closes the Store, so a schema problem in the new features blocks the whole daemon, unlike the isolated recovery service.

   Failure scenario: A trigger creation fails partway through, leaving tables without triggers. Store() raises and admind does not start at all, even though the maintenance-recovery feature was designed to fail without taking the daemon down.

9. `src/heterodyne/admind/ctl.py:185`: Every request on the ask socket is now JSON-decoded twice (an untyped envelope decode, then the typed decode).

   Failure scenario: Every legacy ask or fulfillment request up to MAX_REQUEST pays a redundant full parse. Peeking only at the tag, or decoding once into the tagged union, would do the same job.

10. `src/heterodyne/admind/recovery.py:393`: The proposals schema and the supersede() code carry 'terminal' handling, but no code path ever moves a proposal to terminal or writes terminal data, so that branch is unreachable state.

    Failure scenario: A reviewer or operator assumes a consumed recovery becomes terminal and blocks supersede. In practice a consumed proposal looks the same as a consented one, and only the certificate's consumed/issuing/receipt file probes stop it.
