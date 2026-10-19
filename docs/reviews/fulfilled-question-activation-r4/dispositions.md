# X84 actual R4 MEDIUM dispositions

Actual review: no critical flaws found; good enough for an **offline PoC only**. All ten findings remain **OPEN UNWAIVED**. No source repair, deployment clearance, migration, activation or live qualification is claimed.

## Finding 1 — OPEN UNWAIVED

1. `src/heterodyne/admind/fulfillment_projection.py:389`: verify_pass turns any per-unit exception into a durable 'conflict' stamp, including temporary sqlite3.Error from the separate read snapshot (lock/IO/interrupt). A conflict is only re-checked when the generation or epoch changes.

   Failure scenario: Reading a valid receipt's answers in the worker's 1s-timeout read-only connection hits a temporary SQLITE_BUSY/IOERR. The write phase stamps state='conflict' at the current generation and deletes the dirty row. Fulfilled asks are protected from set_ask/add_answer, so the generation never changes again. The ask stays capacity-active for good (adding toward MAX_OPEN=20), and Get reports lifecycle 'conflict' for a receipt that is actually valid. Only malformed rows should be marked conservatively; temporary errors should leave the row unverified/dirty and use the 30s backoff.

Disposition: Temporary read failures can durably stamp conflict and permanently retain capacity. OPEN activation blocker; no repair or waiver claimed.

## Finding 2 — OPEN UNWAIVED

2. `src/heterodyne/admind/daemon.py:735`: The verification worker starts unconditionally, including on unmigrated v1 stores (every store in production today). There, verify_pass always raises fulfillment-migration-required, so the worker logs 'fulfillment-verification-unavailable' and opens a fresh read-only connection every 30s forever.

   Failure scenario: Deploy this code with ENABLED=False to a store that has not been migrated. Every 30s the daemon opens a new sqlite connection, runs the schema validation queries, fails, and emits a WARNING for the life of the process. ADR 4.3 says to schedule the worker 'after migrated startup', and 4.2 treats an unmigrated store as a distinct mode. The real failure diagnostic is drowned in permanent noise, so operators cannot use it.

Disposition: Unconditional verifier on unmigrated stores can generate persistent connection/log noise. OPEN activation blocker; migrated-start scheduling remains unresolved.

## Finding 3 — OPEN UNWAIVED

3. `src/heterodyne/admind/fulfillment.py:587`: commit() calls stamp_current(), which publishes state 'valid' (capacity suppression) without the bounds the worker enforces: at most 50 answers, ask+answers at most 8 MiB, receipt at most 128 KiB. Get and the worker classify the same ask as too-large or binding-unavailable.

   Failure scenario: Apply or confirm an ask whose answer history or receipt goes over the verifier bounds; validate() uses the unbounded binding(). The receipt is stamped valid and suppressed immediately, but Get returns unavailable/binding-unavailable for it. After any epoch repair or bump, the worker stamps too-large and the slot comes back. ADR 4.3 says over-bound rows 'remain capacity-active', and acceptance item 9 says to 'stamp only exact current bindings'. Fix: call measure() before stamp_current, or refuse.

Disposition: Initial stamp can suppress capacity for over-bound histories that Get/worker reject. OPEN activation blocker; exact bounds parity unresolved.

## Finding 4 — OPEN UNWAIVED

4. `src/heterodyne/admind/daemon.py:944`: The Get catch-all maps every Invalid except 'missing' to unavailable/binding-unavailable, including require_uid's 'unverified-peer', which is raised before any binding is read.

   Failure scenario: A ctl peer with a different UID sends fulfillment.get. Before this change it got the typed refused/'unverified-peer'. Now it gets unavailable/'binding-unavailable', which wrongly blames the binding and makes reconcile stop and retry instead of reporting an authorization refusal.

Disposition: Get maps unverified-peer authorization refusal to binding-unavailable. OPEN; typed authorization mapping unresolved.

## Finding 5 — OPEN UNWAIVED

5. `src/heterodyne/admind/fulfillment_cli.py:139`: HOLD conditions raised during Apply come back as refused(...), and refusal_outcome maps them to 'unsupported'. Reconcile then moves its continuation past an entry that was never applied. Affected: fulfillment-migration-required / fulfillment-projection-unavailable from commit(), and stored-receipt-unavailable from duplicate().

   Failure scenario: Get sees a valid projection and returns eligible. Before Apply, the projection is damaged or found invalid, so commit() raises Invalid('fulfillment-projection-unavailable'). on_fulfillment only maps Get Invalids to unavailable, so Apply replies refused and plan yields 'unsupported'. offset += 1 and the receipt is skipped for good, against ADR section 3: a HOLD must stay at the entry.

Disposition: Apply HOLD is mapped to unsupported and can advance reconcile continuation. OPEN activation blocker; no receipt/application success inferred.

## Finding 6 — OPEN UNWAIVED

6. `src/heterodyne/admind/fulfillment_projection.py:396`: Results with gen None (no fulfillment_generations row) or a generation that changes every time are skipped in the write phase but still count toward len(results). The worker therefore keeps a 1s cadence and re-runs the same failing units with no backoff.

   Failure scenario: A dirty or epoch-unverified receipt has no generation row (partial repair or corruption). Every pass appends (ask_id, None, 'conflict'), skips it on write, returns count>0, and the worker loops every second for ever, re-hashing the same rows. That breaks ADR 4.3's 'never spin' and fixed 30s failure backoff.

Disposition: Missing/churning generation can report work despite no stamp and spin at healthy cadence. OPEN activation blocker; failure backoff unresolved.

## Finding 7 — OPEN UNWAIVED

7. `src/heterodyne/admind/fulfillment_projection.py:441`: A pass that hits the 10s cooperative deadline, or exhausts the byte budget, returns count>0 and is rescheduled after 1s. The ADR requires that a timeout 'reports degraded and backs off' with an inspectable verification-unavailable diagnostic. The only diagnostic is a logging.warning on exceptions, and Inspect does not expose it.

   Failure scenario: Under a contended store each pass runs past 10s. The worker keeps going at about 1s spacing with no degraded signal, and the operator's Inspect output shows nothing about verification health. Liveness and diagnostics differ from the approved design.

Disposition: Deadline/byte-budget exhaustion lacks degraded diagnostic and failure backoff. OPEN activation blocker; no deadline compliance claimed.

## Finding 8 — OPEN UNWAIVED

8. `src/heterodyne/admind/fulfillment.py:762`: notice_state() calls notice_authority() (confirmed_operators JSON parse plus load_operators policy-file read) and then one outbox text SELECT per part, all under the Store lock on the event loop. Inspect does this for up to 40 entries, and Get once per call.

   Failure scenario: Inspect with 40 pending intents of many parts each does 40 policy-file loads and hundreds of single-row queries inside one read transaction, blocking every other daemon store operation while it runs. Load authority once per request and fetch part texts in one keyed range query. This adds to the open R1 #10 query debt.

Disposition: Per-entry authority reads and per-part queries under Store lock remain repeated-query/event-loop debt. OPEN performance/availability debt.

## Finding 9 — OPEN UNWAIVED

9. `src/heterodyne/admind/daemon.py:2710`: The !fulfill refusal reply dropped its 'Fulfillment refused: ' prefix, so the operator sees only a bare internal code.

   Failure scenario: An operator replies !fulfill to a stale or replaced notice. The group reply is now just 'stale-intent' or 'fulfillment-migration-required' with no context, where before it read 'Fulfillment refused: stale-intent'. This is a visible regression in operator-facing text.

Disposition: Operator refusal loses contextual prefix. OPEN operator-facing diagnostic regression.

## Finding 10 — OPEN UNWAIVED

10. `src/heterodyne/admind/fulfillment.py:479`: Leftover from removing the 'notice' key: '{**dict(zip(...))}' is a redundant copy. CAPACITY_ACTIVE_SQL (store.py:37) and Lifecycle.refresh() also remain as dead code.

    Failure scenario: Maintenance cost: the extra dict copy, plus dead capacity and refresh helpers that suggest valid bits are still kept in line by refresh. They are not; only the projection worker and stamp_current set them. Simplify to dict(zip(...)) and delete the dead helpers.

Disposition: Redundant copy and dead helpers remain. OPEN maintenance debt; no cleanup claimed.
