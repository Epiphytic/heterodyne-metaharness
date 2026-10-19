The review of b4b8c75..bb7779a found 10 issues, listed most severe first. I read the approved ADR at f8a80b8, the full six-file diff, the new modules and the activation tests. I made no edits and ran no tests, migrations, services or network calls; the only command beyond reading was one Python import check, and it failed because msgspec isn't installed. The msgspec exception hierarchy in finding 1 therefore comes from library knowledge, not a check here.

Findings 1 and 7 sit behind `ENABLED`, because the worker only starts when the flag is on. Finding 6 applies to a migrated database even with the flag off. Nothing here supports deployment, migration or activation readiness.

10 findings, most severe first:

1. `src/heterodyne/admind/fulfillment_projection.py:323`: One malformed row can stop all verification or crash the daemon: verify_pass only catches Invalid/ValueError/TypeError/UnicodeError per row, and worker() only catches Invalid/sqlite3.Error/OSError/ValueError. guarded() re-raises anything else into the TaskGroup.

   Failure scenario: A receipt whose JSON is a list, or whose binding has the 5 matching keys but won't convert to Binding, makes msgspec.json.decode(type=dict) or msgspec.convert(Binding) raise msgspec.ValidationError. That is not a ValueError, so the worker raises, the TaskGroup tears down and the daemon exits. On restart the same row (protected by fq2_receipt_delete/immutable) is picked up again, so it crash-loops. Separately, a row with invalid UTF-8 text (sqlite3.OperationalError on fetch), or a receipt with no fulfillment_generations row (raise Invalid), aborts the whole pass. The same row is retried every 30s forever and no other receipt is ever stamped. The ADR says such rows are marked conservatively and 'other IDs continue'.

2. `src/heterodyne/admind/fulfillment.py:569`: commit() no longer stamps the new receipt. fq2_receipt_insert immediately sets valid=0 and only the 30s-polling worker verifies it, yet the immutable receipt still records active_after=before-1 and the reply/group post says 'fulfilled'.

   Failure scenario: Controller apply or !fulfill commits: the reply says 'applied … fulfilled', but capacity_count is unchanged and Get shows lifecycle 'unverified' for up to 30s+ (longer under backlog; never with ENABLED=False). The permanent receipt claims a capacity decrement that never happened at commit time. ADR acceptance item 9 ('Controller apply/confirm stamp only exact current bindings') and §4.3 ('schedule … after a new-code dirty insertion') are not implemented; there is no wake-up on new dirty rows.

3. `tests/test_admind_fulfillment.py:1`: The pre-existing fulfillment suites are left failing (51 of 79 tests, per the author's own run; I did not rerun them), and the new activation tests import helpers from this failing module.

   Failure scenario: Generic CI is red on this commit. Tests that assume constructor-time projection, immediate valid=1 suppression after apply(), and rebuild() refresh (e.g. test_admind_fulfillment_r2.py:79) fail. These are real behavioral regressions against the predecessor contract, not just out-of-date tests, and they are neither updated nor disclosed as approved ADR deviations in code. Also, 'test_actual_legacy_source_writers' only runs raw `s.db.execute(UPDATE asks …)` through the old Store's connection; it never exercises the a73/076 ask/answer writer methods or a73 rebuild/refresh, which the ADR requires.

4. `src/heterodyne/admind/fulfillment.py:446`: Get does not follow the ADR's unavailable contract. A receipt whose binding can't be rebuilt returns result 'ok'/'lifecycle' instead of 'unavailable'/'binding-unavailable'. A malformed stored receipt raises Invalid, which on_fulfillment turns into 'refused'.

   Failure scenario: Malformed stored receipt: Get replies refused/'stored-receipt-unavailable', and reconcile's refusal_outcome maps that to 'unsupported' and moves the cursor forward instead of stopping with 'unavailable' at that entry (ADR §3/§4). Orphan or unbuildable binding: clients see a success reply with binding=null rather than the specified unavailable result. stored() also lets msgspec.ValidationError escape for a non-object receipt, and on_fulfillment does not catch it.

5. `src/heterodyne/admind/fulfillment.py:477`: view() adds a new 'notice' key inside the 'pending' object, changing the v1 Get grammar. The ADR says Get keeps its existing view grammar and reports the notice diagnostic in its message.

   Failure scenario: A fulfillment-view-v1 consumer that decodes 'pending' strictly ({intent_id,digest,status,created_at}) rejects or mis-handles every Get with a pending intent. The Get reply message stays the fixed 'lifecycle', so the required notice-* message diagnostic is never emitted.

6. `src/heterodyne/admind/daemon.py:732`: The verification worker only starts when ENABLED, but the new capacity predicate (FALLBACK, or FALLBACK AND NOT SUPPRESSED) is used unconditionally. On a migrated store with the flag off, receipts are never verified, and every ask with a receipt, including terminal-status asks, permanently counts toward MAX_OPEN.

   Failure scenario: The deployment runs `fulfillment migrate --apply` (which sets valid=0 and dirties every receipt) before turning on a producer, or rolls ENABLED back to False. All historical receipts count as active forever, and with 20 or more of them, question/merge and approval posts get ASK_TOO_MANY. The old CAPACITY_ACTIVE_SQL excluded terminal asks with valid receipts; that constant is now dead code.

7. `src/heterodyne/admind/fulfillment.py:628`: Replacement checks the old intent's state but never requires 'freshly proven current ownership' for the new notice. Expiry takes precedence in notice_state, so an expired quarantined/suppressed intent can always be replaced.

   Failure scenario: The old intent has unknown provenance and is over 24h old, so notice_state returns 'expired'. propose(replace_intent=…) passes the quarantined/suppressed check, retires the old intent and queues a fresh notice whose owners are again unknown (ownership_kind='unknown') or revoked. That creates new quarantined or suppressed rows instead of refusing with notice-owner-unavailable as the ADR requires.

8. `src/heterodyne/admind/fulfillment_projection.py:361`: The scan cursor is set to the last 'rest' ID in reversed order that appears in results. That can be an item processed earlier through the dirty slot, which jumps past unprocessed rest items. The cursor never wraps, and dirty items use up the byte/time budget before any rest item gets a slot.

   Failure scenario: dirty=[Z], rest=[A,B,…,Z]. Z is processed first, then A, then the 20MiB/10s break hits before B, so cursor=Z. B is not dirty and has ask_id < cursor, so `f.ask_id>?` never selects it again and it stays capacity-active forever. Ten large dirty items can also exhaust the 20MiB budget so the epoch queue gets no slot, against the ADR's 'each receive at least one slot'. Today migration dirties every receipt, which hides this, but it makes the rest/cursor machinery mostly redundant.

9. `src/heterodyne/admind/fulfillment_cli.py:97`: The migrate command doesn't handle sqlite3.Error, collapses all Invalid codes into 'invalid fulfillment input', and its --plan output can't say whether migration is needed.

   Failure scenario: Running `migrate --apply` while a writer still holds the DB fails BEGIN EXCLUSIVE after 1s with sqlite3.OperationalError ('database is locked'). That is not caught by command(), so an uncaught traceback is printed. An unknown or damaged layout prints a generic message instead of fulfillment-projection-unavailable or unsupported-schema. --plan returns changed=false with equal hashes for both an unmigrated and an already-migrated store, so the operator gets no 'migration required' signal.

10. `src/heterodyne/admind/fulfillment_projection.py:220`: capacity_predicate() runs the full validate() (validate_schema_db plus about 18 sqlite_master lookups plus meta) on every capacity_count/capacity_asks/inactive_asks/fulfilled_asks call. Lifecycle.refresh() and CAPACITY_ACTIVE_SQL are left as dead code.

    Failure scenario: Each `!asks` list, AskList, post_ask (require_projection plus capacity_count) and approval check repeats about 25 schema queries under the live Store lock on the event loop, often twice per request, for a result that can't change without a schema change. inspect() also does N+1 queries per outbox part (row_owners plus a text fetch per row, with parts allowed up to 3500). The dead helpers suggest refresh/rebuild still keep valid bits correct, which they no longer do.
