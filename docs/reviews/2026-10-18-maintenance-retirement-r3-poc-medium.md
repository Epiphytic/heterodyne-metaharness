**Verdict: no critical flaws. This is good enough to build as an undeployed offline PoC.**

I read the full `597a1d4..d306f15` diff, the enclosing functions, `Store.transaction`/`close_attempt`/`issue_decision`/`defer_maintenance`, `reap.spawn` and the R2 dispositions. Nothing was run, as instructed.

The R2 repairs hold up on reading:
- **Proof atomicity:** the HOLD or NotSpawned proof commits in the same transaction as the overlay, and the notice goes out afterwards in its own transaction.
- **NotSpawned is structural:** it is only raised before any child process exists.
- **Same-command replay** is checked before the fresh authority check.
- **Terminal cleanup:** `close_attempt` removes the proof and notice mapping on any known settlement.
- **Bounded inspection:** each `!asks` publishes at most one new notice and does one pass.
- **Final check before retirement:** `commit` re-runs `eligible` and `select` under the store lock.

What I found is nonblocking, ranked below. #1 is the only one I would fix before deployment, because it changes ordinary approvals, not just recovery. #2 and #3 lose notifications or make the operator's instructions misleading; #4 to #9 are low or cleanup. Findings #2 and #3 are separate from the 30-second token race and the adapter-changed HOLD that you already disclosed.

10 findings, most severe first:

1. `src/heterodyne/admind/approvals.py:278`: Every ordinary (non-maintenance) decide now refuses with NotSpawned unless a stat-validated content hash of the adapter is cached, so adapters that are valid under settings._executable (X_OK only) but unreadable, empty, or larger than 8 MiB can no longer approve or deny anything.

   Failure scenario: approve_bead points at an exec-only (mode 0711) wrapper or a static binary over 8 MiB. identity() returns '' (OSError or size cap), cached_identity() returns '', and decide raises NotSpawned before spawn. Every !approve or !deny now ends as 'unavailable' where it used to work. Any touch of the adapter's mtime or ctime (chmod, package reinstall) between read() and decide also turns a valid approval into a refusal.

2. `src/heterodyne/admind/daemon.py:1418`: hold_maintenance now commits the overlay and then swallows notice failures (audit only), and nothing ever re-publishes the 'maintenance-<reason>' held notice, so a suspended hold can sit with no operator notification at all.

   Failure scenario: A suspended hold (authority, latched, native-conflict, ...) whose ask_notice enqueue raises sqlite3.Error: the overlay commits with outcome maintenance-held:*, the exception is only audited, and no retry happens because the hold is suspended. If the proof is also ineligible (for example the adapter identity was missing), !asks lists nothing for it either, so the original reply sits held without anyone being told. The maintenance_r2 fixture was switched to ProvenPort only for the notice branch, so this unproven-HOLD case is no longer tested.

3. `src/heterodyne/admind/daemon.py:1109`: !asks prints '!asks retire <current token>' for every eligible overlay but re-publishes at most one notice per inspection; any other overlay's mapped notice still carries an older proof sha, so following the printed instruction is refused.

   Failure scenario: Two suspended overlays A and B, both with sent notices from an older generation. !asks publishes a new notice only for A but prints current tokens for A and B. The operator replies to B's existing notice with B's printed token. select() joins on n.sha256 = p.sha256, B's mapping still holds the old sha, so the reply gets 'Retirement refused'. The same happens when the mapped notice is still pending or delayed: publish_notice returns False, yet the current token is printed anyway.

4. `src/heterodyne/admind/retirement.py:287`: select() matches the token against any proof row for the ask but then returns current_attempt(ask_id) together with that attempt's own eligible proof, without checking that the matched row's message_id is the current attempt or that the returned proof.token equals the supplied token.

   Failure scenario: An 'uncertain' settlement keeps an older attempt's proof and mapping, and a newer attempt on the same ask becomes eligible. A reply to the old notice with the old token makes select return (new attempt, new proof). retire() then does a native read and, on 'recorded', runs settle_maintenance_recorded on the new attempt, authorised by a token that was never issued for it. commit() would refuse an actual retirement, but the reconcile branch has no such re-check.

5. `src/heterodyne/admind/retirement.py:404`: publish_notice treats a mapping whose outbox row is gone (LEFT JOIN gives status None) as an in-flight notice, so it can never be refreshed, and select() also needs that outbox row, which permanently strands the proof.

   Failure scenario: The mapped outbox row is deleted or rewritten under a new key (for example by the outbox redaction pass, which deletes pending rows and re-inserts them). row[3] is None, terminal is False, and every later refresh returns False. select's JOIN to outbox finds nothing, so the attempt stays eligible forever but can never be retired by any token.

6. `src/heterodyne/admind/retirement.py:543`: retire() does not handle the ValueError that commit()/close_attempt can raise (receipt over 16 KiB, or 'already fulfilled'), so the authenticated command gets no reply and its inbound row is left as it was.

   Failure scenario: A long envelope pushes the receipt body past 16384 bytes, or a fulfillment lands between the native read and commit. ValueError propagates out of retire() into the command handler. The transaction rolls back correctly, but the operator gets no clear 'Retirement held' answer the way the other refusal paths give one.

7. `src/heterodyne/admind/retirement.py:72`: maintenance_notice_history is created on every database, including fresh ones, but is never written or read by production code; it is 'legacy' from an R1 commit on this same undeployed branch.

   Failure scenario: Every new admind.db gets a dead table, and tests assert it stays empty. Nothing was ever deployed with R1, so this compatibility schema only adds migration surface and reviewer confusion. Dropping it from migrate() does the same job.

8. `src/heterodyne/admind/retirement.py:196`: The proof decoder still accepts and cross-validates a legacy 'consecutive' field, and record_not_spawned strips it, even though no deployed database can contain proofs from the unreleased R1 format.

   Failure scenario: Extra allowed keys, a validation clause and a pop() keep the derivable-state concern from R2 #10 alive for data that cannot exist outside test fixtures. Every future change to the proof schema has to keep this dead branch consistent.

9. `src/heterodyne/admind/daemon.py:1101`: The !asks inspection now runs inside a BEGIN IMMEDIATE writer transaction and can post to the outbox, so a read-mostly phone command takes the SQLite writer lock and fails entirely if notice publication raises.

   Failure scenario: publish_notice -> post -> enqueue raises (storage or ownership error). The whole list_asks transaction rolls back and the exception propagates, so the operator gets no listing at all. Separately, every !asks serialises against all writers for up to 128 proof decodes while holding the lock.

10. `src/heterodyne/admind/approvals.py:253`: read() now does a to_thread stat and possible content hash on every call, including card posting and read-backs, purely to warm a cache that only decide() and retire() use.

    Failure scenario: Every approval card post and every reconcile read-back pays an extra thread hop and stat, plus an up-to-8 MiB SHA-256 whenever the stamp changes. Warming the cache only in run_attempt and retire, just before the authority fence, would give the same guarantee without the cost on unrelated paths.
