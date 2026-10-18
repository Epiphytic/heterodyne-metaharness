# Sole HIGH design review: author incorporation

Actual verdict: **REVISE**. All ten findings incorporated once. No second design review or validation; this is author disposition, not reviewer clearance or human design approval.

Raw report: [fulfilled-question-activation-design-r1-high.md](fulfilled-question-activation-design-r1-high.md), full SHA256 `41e918326981c7a3cf9001b47b43a82e54b823ed90b49c42472bb5c9f21c52a2`. Exact ordered findings and per-block hashes: [fulfilled-question-activation-design-r1-dispositions.json](fulfilled-question-activation-design-r1-dispositions.json). Historical eight-finding code review and original design commit remain retained unchanged.

## 1. Incorporated

1. `docs/designs/2026-10-18-fulfilled-question-activation.md:62`: Adding verified_epoch/verified_generation/verification_state to ask_fulfillments with ALTER TABLE breaks the claimed a73e91d rollback (line 114): a73e91d's Lifecycle.validate_schema compares the stored sqlite_master SQL byte-for-byte against its DDL on every Store() open, and its commit() uses a positional 8-value INSERT.

   Failure scenario: Migrate, then roll back to a73e91d as §4.3 allows. Store.__init__ calls fulfillment.migrate(), which calls validate_schema (fulfillment.py:281-289). SQLite rewrote the stored CREATE TABLE text when the columns were added, so the check raises Invalid('incompatible-schema') and neither the daemon nor the CLI can open the store. Even with that check bypassed, `INSERT INTO ask_fulfillments VALUES (?,?,?,?,?,?,?,1)` (fulfillment.py:478) fails with '11 columns but 8 values'. A database created fresh by the new code would also store different DDL text than a migrated one, so the single 'pinned' expected text in §4.1 cannot match both. Correction: leave the ask_fulfillments DDL byte-identical and keep the stamps in a separate additive table, e.g. fulfillment_verifications(ask_id PK, epoch, generation, state). BUMP then deletes that row instead of nulling columns. Add a forward/backward fixture that actually opens a73e91d's Store on a migrated database.

Author disposition: §4–4.3 retain original core table SQL and eight-column INSERT. Separate additive verification table, deletion on BUMP, and exact-binary forward/backward Store fixtures replace ALTER TABLE.

Finding SHA256: `371e1179598cc5b19534166353f6a823574772c6406977176bb5898e39c126c7`.

## 2. Incorporated

2. `docs/designs/2026-10-18-fulfilled-question-activation.md:109`: The 256 KiB per-ask canonical bound is smaller than ask+answer histories the current source accepts, so a legitimate receipt can be marked too-large permanently. It then stays capacity-active, and the new fq2_receipt_delete trigger removes the only way out.

   Failure scenario: asks.py allows MAX_BODY=16,000 characters and MAX_ANSWER_TOTAL=64,000 characters across 50 rows. With 4-byte UTF-8 characters and JSON escaping, that comes to roughly 64 KB of body plus 256 KB of answers plus per-row metadata, about 330 KB, which exceeds 262,144 bytes. binding.py already budgets HISTORY_BYTES = 64,000*4 + 50*4096 = 460,800. Such an ask is marked too-large, never verifies and is never suppressed, and it cannot be deleted. Twenty such receipts hold every MAX_OPEN slot indefinitely, and the design defers the only remedy to an unapproved future archival design. Correction: derive the per-ask bound from the source limits (at least binding.HISTORY_BYTES plus the ask-field maxima and escaping overhead), and raise the 1 MiB batch bound to match.

Author disposition: §4.3 derives an 8-MiB per-ask /20-MiB pass limit from pinned request/history/envelope limits and escape allowance; maximum accepted UTF-8/escaping fixtures are mandatory. Oversized corrupt/out-of-contract rows remain a disclosed bounded HOLD.

Finding SHA256: `fab7ccacb628322397f3daa98ff7f33527961bc1ee51cb810f1939d5693a5e44`.

## 3. Incorporated

3. `docs/designs/2026-10-18-fulfilled-question-activation.md:99`: Treating projection damage or a missing-ask receipt (line 65) as a posting HOLD also blocks approval-ask posting, because approval_post_refusal uses the same capacity count. That contradicts §5's statement that approval flows remain unchanged.

   Failure scenario: One trigger is missing, the meta row is malformed, or one receipt's ask is absent. capacity_count becomes unavailable. post_ask (daemon.py:805) and approval_post_refusal (daemon.py:880) both depend on it, so every question, merge and native approval ask is refused until a stopped-writer repair. A fault in an optional, disabled-by-default overlay becomes an outage for native approvals. Correction: on gate failure, fall back to a conservative count that applies no suppression at all (status-active asks plus every ask with any receipt). This is computable without the projection and is never lower than the true count. Reserve the HOLD for fulfillment mutations and lifecycle views.

Author disposition: §4/4.2/5 use unsuppressed base counting when derived projection is damaged. Native approval posting retains unchanged capacity limits. Orphans remain diagnostics, not fabricated active asks or projection-driven global posting refusal.

Finding SHA256: `55ac35e3feaf7962c62840ee5decc7612e7e2bc6041ad2d96123aedcd3a9a08a`.

## 4. Incorporated

4. `docs/designs/2026-10-18-fulfilled-question-activation.md:51`: Idempotent-replay classification still depends on the derived valid bit and verification state, not on the immutable request_digest. After a BUMP or migration, an identical retry reports conflict or unavailable instead of already-applied.

   Failure scenario: Under the design, Get returns typed unavailable for an unverified binding, so reconcile plan() stops with 'unavailable' even when the stored receipt's request_digest matches. Lifecycle.duplicate() (fulfillment.py:450-451) raises Invalid('conflict') whenever valid=0, and every BUMP forces valid=0, including the AFTER INSERT receipt trigger before stamping. So an identical controller re-apply, or a lost-response continuation, gets refusal_outcome 'conflict', which is a false HOLD on the controller's own committed receipt. Correction: classify already-applied versus conflict from the trigger-protected receipt/request_digest alone, independent of verification. Get should expose the immutable receipt identity even while validity is unavailable.

Author disposition: §3/4 classify identical immutable request_digest as already-applied independent of valid/stamps. Get exposes readable immutable receipt identity with explicitly disclosed opt-in lifecycle unverified; eligibility remains false.

Finding SHA256: `6075a796ac63e30f8298444b10d97f151ba569ddaa465b758c5ae3a11fae7204`.

## 5. Incorporated

5. `docs/designs/2026-10-18-fulfilled-question-activation.md:107`: The verification pass has no defined cadence or trigger, and no defined execution context. That leaves convergence time unbounded (or a hot loop), and the hashing may run under Store.lock on the event loop.

   Failure scenario: Right after migration, each of N historical receipts (N≥20 is exactly the accumulation case behind finding 7) is unverified and capacity-active. With no stated interval, wake source or timeout, posting is blocked for an unknown number of 20-ID passes. Line 111 says 'deferred snapshot' but does not name Store.read_snapshot() in a worker thread. Using Store.transaction(read_only=True) instead holds the RLock while hashing up to 1 MiB per pass, which stalls all event-loop Store calls. Correction: specify the trigger (startup, then on dirty insert, plus a fixed backoff), a maximum interval, Store.read_snapshot() on a worker thread for the read and hash, and a bound on the time to verify N receipts.

Author disposition: §4.3 names Store.read_snapshot on one worker thread, startup/dirty wakes, 30-second idle poll/backoff, one-second backlog cadence and cooperative deadline. Conditional 30+11*N healthy convergence is stated honestly without claiming hard OS latency bounds.

Finding SHA256: `2ab11efd3982ee279f2fcdfec9206a91d0446c7e7381e30edc2d74c5763442d4`.

## 6. Incorporated

6. `docs/designs/2026-10-18-fulfilled-question-activation.md:101`: The design says migration is an explicit quiesced deployment step, but Store.__init__ runs Lifecycle.migrate() on every open. It does not say what the new binary does when it opens an unmigrated v1 database. It also does not list replacing the predecessor's 'rebuild before serving' rule as an explicit deviation.

   Failure scenario: The new code is installed and any CLI command or the daemon constructs Store(). Either it auto-migrates, which breaks the writer-quiescence, backup and rollback-receipt requirement, or it refuses, which breaks every read path, including `admind ask get`. The predecessor (§'capacity', line 62) requires rebuilding the validity index before serving posts. §4.2 replaces that with asynchronous verification while §1 says predecessor rules 'remain binding', so the human approver is not told about the change. Correction: state that an unmigrated v1 store is refused by the new binary for fulfillment and posting paths, with read-only legacy inspection still allowed, and that migration runs only through a dedicated command. List the asynchronous-verification change as a named predecessor deviation.

Author disposition: §1/4.2 specify unmigrated read-only mode, no ordinary-open migration, dedicated plan/apply deployment step, and explicit deviation from predecessor rebuild-before-serving. Actual deployment authority/quiescence remain prerequisites, not implemented claims.

Finding SHA256: `1ef7d9094d125c72cca82579d91fa3128f647a6a6b83b5276075677553674005`.

## 7. Incorporated

7. `docs/designs/2026-10-18-fulfilled-question-activation.md:71`: BUMP upserts a fulfillment_generations row for every ask insert, status change and answer on every ask, receipt or not. That is permanent write amplification and unbounded table growth on hot paths.

   Failure scenario: insert_ask, set_ask and add_answer fire on every question, merge and approval. Each one now writes a generation row that is never pruned, even though fewer than one ask in many will ever get a receipt. Generations only matter once a receipt exists, and the receipt-insert trigger already bumps at that point. Correction: add `WHEN EXISTS (SELECT 1 FROM ask_fulfillments WHERE ask_id=…)` to the asks and answers triggers, and let fq2_receipt_insert create the generation row.

Author disposition: §4.1 guards every BUMP with receipt existence. Receipt AFTER INSERT initializes its generation; unrelated/approval asks create no generation/dirty rows.

Finding SHA256: `a0a1b5cb51200bede5be6569c1bde63ad157b8171b7922b2f2327a0b085b485e`.

## 8. Incorporated

8. `docs/designs/2026-10-18-fulfilled-question-activation.md:40`: Inspect's pagination, continuation and byte-budget machinery can never trigger, and the 3,500-chunk scan bound is wider than needed. The intent row already records `parts`.

   Failure scenario: The global cap is 40 pending intents and N is at most 40. Each entry is under 400 bytes, so a full listing is about 16 KB, well under 128 KiB, and the keyset cursor, 'second page' semantics and budget stop are dead code that still has to be tested. Each intent's group check scans up to 3,500 rows through `key LIKE 'fulfill-intent:…'`, which cannot use the BINARY UNIQUE index, so that is up to 40 full outbox scans per call. Correction: return all current pending intents in one bounded reply with no continuation. Bound each group with `LIMIT parts+1` and use a range predicate (key >= 'fulfill-intent:X:' AND key < 'fulfill-intent:X;').

Author disposition: §2 uses an exact no-cursor inspect request and complete <=40 reply, indexed literal BINARY prefix bounds and LIMIT parts+1 with recorded-part/byte/reassembly checks.

Finding SHA256: `134ea802a7cc9f762b58ab9b4e26a00ae7fbdc545caa09c47a50e7dbe8a17c7a`.

## 9. Incorporated

9. `docs/designs/2026-10-18-fulfilled-question-activation.md:34`: The table lists `replace` as a supported next action for quarantined (and suppressed) intents. Replacement takes its owners from the same durable inbound.sender_key, so it is always quarantined again.

   Failure scenario: The controller follows the inspect guidance and replaces a quarantined intent. propose recomputes owners from inbound_sender (fulfillment.py:617-621), which is still NULL, so the new notice is quarantined again. Each attempt retires an intent and leaves another audit tombstone and outbox row with no chance of delivery. The design itself says 'unknown provenance remains unknown on the replacement'. Correction: remove `replace` from the quarantined row. Allow it for suppressed only while the owner's policy is current, which in practice means dispose or controller-receipt.

Author disposition: §2 removes quarantined/suppressed replace guidance and refuses replacement absent fresh current proven ownership. Dispose/genuine controller receipt remain; no provenance backfill or quarantine churn.

Finding SHA256: `30455909884126bd736b36b4b0d98db3af4865e6ad815b39f64a7c6cce137599`.

## 10. Incorporated

10. `docs/designs/2026-10-18-fulfilled-question-activation.md:54`: With fulfillment enabled, an unauthorised or latched !fulfill still goes through finish() with a reply (daemon.py:2414-2416). The design keeps this path instead of following the daemon's deny() convention: mark dropped, and send no reply when latched.

    Failure scenario: The daemon is latched or the group is unverified, and an operator replies !fulfill. The handler marks the inbound 'done' and queues 'Fulfillment unavailable under current authorization.', whereas deny() (daemon.py:580-591) records 'dropped' and queues nothing when latched ('a latch is final'). Correction: specify that an authorization failure goes through deny_rows() inside the same transaction, keeping the fixed UNVERIFIED wording and no reply when latched.

Author disposition: §3 requires transactional deny_rows/dropped for unauthorized/missing-principal/latched fulfill, existing UNVERIFIED only when unlatched, no latched response and no finish/done path.

Finding SHA256: `2c457362364da65ead32810aa6ed6af7812f65549b32ee9f89f9c83da86333ee`.

