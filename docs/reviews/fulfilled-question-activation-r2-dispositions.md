# Fulfilled-question activation: SECOND HIGH author dispositions

Actual SECOND FULL HIGH returned REVISE with ten deduplicated candidates, not separately verified by the reviewer. Author offline regressions and dispositions below are not a new review or clearance. Producer activation remains disabled; no live migration or deployment is claimed.

## Finding 1: FIXED

1. `src/heterodyne/admind/store.py:1324`: The new post-commit dirty check and verification_changed() call run inside the same try block as COMMIT, so if they raise after a successful commit, the caller sees a committed transaction as failed (the guarded ROLLBACK quietly does nothing).

   Failure scenario: Any writer, e.g. finish(), commits. Then the extra autocommit SELECT on sqlite_master or fulfillment_dirty raises sqlite3.OperationalError (busy/IO/schema-changed). The exception reaches the caller even though inbound status and the reply were already committed. Paths like the !fulfill handler then run their 'store unavailable' fallback and queue a second reply. Separately, every daemon write transaction now pays two extra queries, even on unmigrated stores with ENABLED=False.

Store transaction commits before advisory wake; connection-local TEMP dirty hooks record only writes in the committing transaction, and wake failure cannot reclassify the commit. No post-COMMIT SQL queries remain. Rollback and unrelated-backlog commit regressions preserve outcome and notification behavior.

## Finding 2: FIXED

2. `src/heterodyne/admind/fulfillment_projection.py:443`: Store.transaction wakes the verifier whenever any fulfillment_dirty row exists, not only when this commit inserted one, so the wake cuts short both the 30s failure backoff and the 1s gap between passes.

   Failure scenario: The projection gets damaged (e.g. a trigger dropped) while dirty rows remain. Every verify_pass now raises fulfillment-projection-unavailable, so the worker sets delay=30. But each unrelated daemon commit (outbox send, inbound claim, touch) still sees dirty rows and sets wake, so a failing pass of about 25 schema queries runs on every commit. That breaks the ADR's 'never spin / fixed 30-second backoff' rule. A backlog does the same to the 1s spacing.

Mandatory one-second healthy spacing and fixed thirty-second failure backoff cannot be shortened by coalesced notifications. Only actual dirty insertion/update on this Store connection wakes, and external legacy writers remain covered by the thirty-second idle poll.

## Finding 3: FIXED

3. `src/heterodyne/admind/daemon.py:937`: Every Invalid raised during Get, including binding()'s ordinary 'missing' refusal for a nonexistent ask with no receipt, is now returned as unavailable/binding-unavailable instead of the predecessor's typed refusal.

   Failure scenario: A reconcile manifest has a receipt for an ask ID that has no row and no receipt in this store. Get used to return refused/'missing', which refusal_outcome mapped to 'unsupported', and reconcile moved on. Now plan() returns 'unavailable', reconcile breaks and the continuation offset never passes that entry, so every later entry is blocked for good. This contradicts ADR section 3: 'a valid server refusal maps to its existing typed outcome and advances'.

Ordinary Get missing with no immutable receipt remains refused/missing, retaining unsupported/advance behavior. Malformed or unavailable binding remains unavailable.

## Finding 4: FIXED WITH AUTHORIZED ORACLE CORRECTION

4. `src/heterodyne/admind/fulfillment_cli.py:166`: plan() returns refusal_outcome whenever result != 'ok', so an unavailable/binding-unavailable Get whose view still carries the readable immutable receipt is never checked against the request digest.

   Failure scenario: A receipt was applied earlier, and then its ask's binding can no longer be rebuilt (ask row missing, more than 50 answers, or receipt over 128 KiB where measure() refuses). Re-running reconcile with the identical receipt should report already-applied, because the ADR says 'reconcile checks immutable receipt identity before requiring a fresh binding'. Instead it reports 'unavailable' and stops at that entry on every run.

CLI plan checks readable immutable request identity before requiring a successful fresh binding. Matching identity is already-applied; mismatch is conflict. Controller explicitly authorized only the missing-ask case in the named existing test to expect already-applied and cursor :1; malformed receipt remains unavailable/:0. Exact original bytes and mechanical/AST differences are retained privately.

## Finding 5: FIXED

5. `src/heterodyne/admind/fulfillment.py:632`: Replacement only proves the OLD intent's owners are current. The successor notice's owners, computed later from the new declaration's consumed answers, can be unknown or revoked, and the code still creates a quarantined or suppressed successor instead of refusing with notice-owner-unavailable.

   Failure scenario: The old pending intent is owned by a current operator. propose(replace_intent=old_digest) sends a declaration consuming an answer whose inbound.sender_key is NULL, or belongs to a revoked operator. The check passes, retire() drops the good intent, and line 695 computes owners=(), so a new quarantined notice is queued. The ADR requires 'freshly proven current ownership' for the replacement.

Replacement proves both the old intent and the new consumed answers have durable current owners before retiring or publishing anything. Unknown and revoked successor-owner regressions retain the original pending intent and create no successor.

## Finding 6: FIXED

6. `src/heterodyne/admind/daemon.py:923`: The Get handler calls lifecycle.notice_state() after view() has returned, outside any store lock or read snapshot. notice_state/delivery run raw self.store.db.execute calls on the shared connection while the always-on verifier thread may hold store.lock with BEGIN IMMEDIATE open on that same connection.

   Failure scenario: The verifier's write phase holds the RLock on the executor thread and has a transaction open on store.db. Meanwhile a Get on the event loop runs unlocked SELECTs on the outbox/intents rows through the same connection. Those reads land inside the worker's uncommitted transaction, which breaks the Store's 'serialized by self.lock' rule. The view and the notice message also come from different snapshots, so a just-retired intent shows up as pending with message notice-unavailable.

Get view, projection check, and notice-state observation share one Store read transaction under the reentrant Store lock. The observation regression proves the notice query joins that transaction.

## Finding 7: FIXED CONTAINMENT; ENABLED CLAIM PARTIALLY REBUTTED

7. `src/heterodyne/admind/daemon.py:734`: The verification worker starts unconditionally (even with ENABLED=False) and is not wrapped in supervised() like every other daemon loop, so any exception outside its narrow catch list (Invalid/DecodeError/sqlite3/OSError/ValueError/RuntimeError) tears down the TaskGroup and stops the daemon.

   Failure scenario: A KeyError, AttributeError, OverflowError or MemoryError escapes verify_pass's write phase or cursor logic on a malformed meta/cursor row. guarded() re-raises it, the TaskGroup cancels inbound, outbox and the other loops, and admind exits. On restart the same durable row triggers it again. A disabled feature can now crash-loop the production daemon.

Verification pass exceptions including KeyError are contained with a fixed non-payload diagnostic and thirty-second retry; the daemon also supervises the loop with base/cap thirty seconds. Approved ADR section 4.3 and the retained disabled-producer regression require verification independent of producer ENABLED, so that independence is preserved. No activation is performed.

## Finding 8: FIXED

8. `src/heterodyne/admind/fulfillment.py:469`: view() makes eligible depend on the projection being valid, but returns result 'ok' with lifecycle 'unfulfilled'. Reconcile therefore can't tell 'projection unavailable/migration required' (a HOLD) from a genuinely unsupported ask.

   Failure scenario: On an unmigrated store or one with a damaged projection, every eligible receipt's Get returns ok/eligible=false, plan() returns 'unsupported', and reconcile advances its continuation past all entries without attempting Apply. Once the projection is repaired, the operator's continuation has already skipped receipts that were never applied. The ADR treats projection damage as a fulfillment-mutation HOLD (unavailable), not as a terminal refusal.

Get returns unavailable with the explicit migration/projection refusal when no fresh validated projection is available, retaining the readable immutable receipt/view. New bindings on unmigrated or damaged stores HOLD; inspection does not migrate. A readable identical receipt still wins by finding 4.

## Finding 9: FIXED

9. `src/heterodyne/admind/daemon.py:940`: The new except clause turns any msgspec.DecodeError/UnicodeError/TypeError/ValueError from any fulfillment request type (Apply, Propose, Dispose) into Reply('unavailable', 'binding-unavailable'). That includes Store.transaction's new ValueError('read-only-transaction') and ordinary programming errors.

   Failure scenario: Propose with a pending intent whose created_at fails datetime.fromisoformat, or an Apply that hits an internal TypeError, now replies 'binding-unavailable' for a request that isn't a binding read. That hides bugs that used to surface through the server's failed handler, and gives operators a wrong, unrelated diagnostic code.

Decode/Unicode/type/value containment is restricted to Get. A programming TypeError from Apply propagates to the existing server failed boundary rather than being mislabeled binding-unavailable.

## Finding 10: FIXED BEFORE DEPLOYMENT

10. `src/heterodyne/admind/fulfillment_projection.py:811`: fq2_asks_update always runs bump(OLD.ask_id)+bump(NEW.ask_id), and fq2_answers_update runs answer_bumps()+bump(OLD)+bump(NEW), so an ordinary same-ID update bumps the generation 2-3 times and repeats every invalidation statement, unlike the ADR's 'BUMP distinct OLD/NEW ask_id'.

    Failure scenario: Each status or answer edit on a fulfilled ask runs 8-12 trigger statements instead of 4 and advances the generation by 2-3. Because this literal DDL is hash-pinned and becomes compatibility-frozen once migrated, fixing it after deployment needs a repair/epoch migration. It should be corrected before any store is migrated.

Literal trigger SQL builds distinct OLD/NEW/unique-key-victim sets before bumping. Ordinary ask and answer updates increment a generation once; existing replacement/old-writer tests remain. Changed literal DDL is not silently accepted by an already migrated predecessor: mismatch remains HOLD and requires an authorized deployment/repair procedure.

## Remaining limits

- No migration, activation, deployment, native effects or model review by author.
- Predecessor migrated literal DDL mismatch remains HOLD; no automatic repair or epoch rewrite.
- R1 detailed migration diagnostic and repeated validation/inspection query debt remain OPEN UNWAIVED.
- Quality and existing baseline formatting debt remain UNWAIVED.
