**Verdict: REVISE.** This was a read-only review of 597a1d4..83f0bf0 against design aa860fe7, with dedup and no verify pass, so these findings are unverified. I ran no tests, probes or edits.

What holds up:
- **Spawn and authority:** the cached-identity miss and the spawn `OSError` both fire before any child exists. Nothing is awaited between the final authority check, `issue_decision`, `cached_identity()` and `reap.spawn`.
- **Proof chain:** the ABA and generation checks look sound, and coverage cannot heal after a missed invocation.
- **Retirement path:** the receipt is retained in `maintenance_retirements`, and the commit compare-and-set re-checks `eligible`/`select`. I found no native writer.

The two most serious problems: the not-spawned evidence is not committed together with the overlay restore, and a notice that is only delayed or quarantined in the outbox is treated as failed.

10 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:1467`: record_not_spawned commits in its own transaction inside the except handler, and the overlay restore (hold_maintenance -> defer_maintenance) runs later in a separate transaction, so 'atomically mark that generation not-spawned and restore the previous eligible chain' (ADR §23) is not atomic.

   Failure scenario: A maintenance retry gets a cold or drifted cached_identity, so BtqError('unavailable') is raised with launched=False. record_not_spawned commits the proof at invocations=n+1. Then the process dies, or hold_maintenance raises (for example RuntimeError('maintenance pending capacity') from defer_maintenance, or an enqueue/notice error), before the overlay transaction commits. The ask is left at outcome='maintenance-issuing' with no ask_maintenance row. eligible() needs pending-maintenance or maintenance-held, and reconcile_issuing settles only an exact native decision, so a fully proven not-issued attempt holds its slot permanently. A sqlite error inside record_not_spawned also escapes the except block and skips the hold.

2. `src/heterodyne/admind/retirement.py:376`: publish_notice counts only 'pending' or 'sent with message_id' as usable, so transient outbox states ('delayed', 'quarantined', 'suppressed') are treated like 'failed'. A refreshing `!asks` then repairs a notice that will still be delivered, and moves the current mapping away from it.

   Failure scenario: A suspended hold's notice N1 is in a rate-limited group with status='delayed'. The operator runs `!asks`: usable=False, so a repair notice N2 (key ...:repair:<seq>) is posted and maintenance_recovery_notices is rebound to N2. release_delayed then sends N1 and the operator replies to it with the correct token. select() joins only the current mapping (N2), so they get 'Retirement refused: reply to the current eligible held notice'. Each further `!asks` while N2 is also delayed or held adds another repair notice and another history row.

3. `src/heterodyne/admind/daemon.py:1479`: record_hold now shares one transaction with hold_maintenance, which also runs the new publish_notice. Any notice-side exception rolls back the proof row after mark_maintenance_issuing has already committed in a separate transaction.

   Failure scenario: An exact HOLD returns with invocations=n. mark_maintenance_issuing commits. Inside the joint transaction, publish_notice -> daemon.post/enqueue or bind_notice raises (an IntegrityError on maintenance_recovery_notices.outbox_key UNIQUE, a storage error, or a capacity RuntimeError in defer). The whole block rolls back, so the n-th HOLD proof is lost and the attempt stays maintenance-issuing with proof n-1 < generation n. Every later record_hold or eligible check fails, so the attempt can never be retired. Notice publication failure should not erase already-observed evidence; the ADR only requires the proof to be atomic with defer, not with notice I/O.

4. `src/heterodyne/admind/daemon.py:1095`: Plain `!asks` now, as a side effect, publishes a fresh recovery notice for every retryable overlay whose generation advanced since its last notice. Each one also adds a row to the never-pruned maintenance_notice_history table, so inspection causes unbounded fan-out and growth.

   Failure scenario: With up to MAINTENANCE_CAPACITY=128 retryable overlays retrying every 30s, each `!asks` posts up to 128 new threaded notices and 128 new history rows (new token, new key retirement:<ask>:<token>). Repeated phone inspections flood the group and grow the outbox and history without limit. This contradicts the bounded one-active-proof/one-receipt storage the ADR (§33) and docs describe, and an inspection command should not have mass-post side effects.

5. `src/heterodyne/admind/daemon.py:1466`: Positive not-spawned evidence is inferred in a broad `except Exception` from `issued and not launched and exc.word == "unavailable"`, instead of a dedicated exception type raised only at the two proven pre-spawn sites.

   Failure scenario: Any future or refactored code between `issued = True` and spawn that raises BtqError('unavailable') before on_launch would be recorded as proven not-spawned and extend the retirement chain. Examples: an extra read, an adapter pre-check, or reusing read() logic (`code != 0 -> BtqError('unavailable')`). The security evidence relies on a reused generic error word. A distinct NotSpawned raised only by decide's cache-miss guard and _run's Popen OSError branch would make the proof structural.

6. `src/heterodyne/admind/retirement.py:419`: The existing-receipt and same-command idempotency lookup runs only after the fresh authorised() check and the inbound-thread check. Redelivery of the SAME command whose sender has since lost authority gets a deny reply and audit, not the existing receipt with no effects.

   Failure scenario: Operator X retires an attempt (the receipt commits) and is then removed from the group. A recovery path, or redelivery that bypasses inbound dedup, replays X's command_id. retire() calls daemon.deny(command, 'retirement'), which emits a denial reply/audit for a command that already succeeded. ADR §33 requires that 'Repeated delivery of the SAME authenticated command returns its existing receipt, without new notice/native calls/effects'.

7. `src/heterodyne/admind/retirement.py:477`: The reconciled branch of retire, and every ordinary close path (settle_maintenance_recorded, reconcile, refresh, close_refused), leave the attempt's maintenance_proofs row and maintenance_recovery_notices mapping in place. Only the retirement commit deletes them.

   Failure scenario: An eligible attempt is reconciled as 'recorded' through `!asks retire`, or settled by a later retry or readback. Its proof row and current mapping stay forever. That contradicts 'remove only its exact maintenance overlay/proof' and the one-active-proof claim, and orphan rows accumulate per attempt. Correctness currently depends only on current_attempt() checks rather than on the state being cleaned up.

8. `src/heterodyne/admind/daemon.py:1104`: list_asks walks maintenance_pending() twice and calls current_attempt plus eligible (proof JSON parse, sha256 and 8-way validation) twice per overlay, once inside publish_notice and again for the listing line.

   Failure scenario: With 128 overlays, each `!asks` does about 256 proof decodes and validations and two full overlay scans under the store lock on the event loop. Computing (attempt, proof) once in the first loop and reusing it for both publication and the listing line would halve the work and avoid the two passes disagreeing.

9. `src/heterodyne/admind/retirement.py:369`: publish_notice runs raw `daemon.store.db.execute` SQL from a daemon-level module function instead of a locked Store or Retirement accessor, repeating the R1 finding-10 pattern that Store.inbound_thread was added to fix.

   Failure scenario: Both current callers happen to wrap publish_notice in store.transaction(), so the lock is held. Any new caller, such as a future reconcile or startup notice pass, would query outbox and mapping state without the store lock and could interleave with an enqueue/mark_sent in another thread. Moving the query into a @_locked Retirement method keeps lock discipline local.

10. `src/heterodyne/admind/retirement.py:133`: The proof body stores `consecutive`, which is always invocations - not_spawned and is validated only as that sum, so it is derivable state that carries no independent evidence.

    Failure scenario: _proof() rejects any row where consecutive + not_spawned != invocations, so the field can never say anything the other two fields do not. It adds a hashed field, a validation clause and a misleading suggestion of separate chain tracking (the R1 #4/#10 concern) that future readers must keep in sync. Dropping it, or deriving it at read time, shrinks the canonical body and the checks.
