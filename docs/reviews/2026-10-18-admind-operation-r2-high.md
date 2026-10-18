**Verdict: REVISE**

I reviewed the full range 597a1d4..711a240 against approved design e613fd9 (sections 2, 5 and 7), the protocol doc and the r1 dispositions. This was read-only: no tests, probes or edits. The findings below are deduplicated but not separately verified, ranked most severe first.

- **Finding 1** contradicts design §5. A withdrawn lineage can end up with an open card that operators can still answer.
- **Findings 2 and 3** are protocol and liveness gaps against the same section.
- **Findings 4, 5 and 7** are audit and reply-shape gaps.
- **Finding 6** means the wire doc a client author would follow does not match the code.
- **Findings 8 to 10** are efficiency and cleanup.

10 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:1523`: After a withdrawal, an untouched readback with the same digest (ordinary run_attempt settlement here, and reconcile_one at line 1683 on restart or stranded attempts) reopens the ask as 'open'. Design §5 requires that every open sibling in a withdrawn lineage is cancelled and none stays answerable. The refresh path cancels in this situation, but these paths reopen.

   Failure scenario: Operator approves; issue_decision passes; during `await ab.decide` the source withdraws (deciding row skipped, decision-in-flight). approve-bead refuses, readback is untouched with an unchanged digest, so settlement returns 'open'. The withdrawn lineage now has a live, answerable card that counts toward MAX_OPEN. Every new reaction gets 'Ask X is already open. Nothing recorded.' because begin_attempt's decision_allowed refuses it. status reports result='withdrawn' and nothing tells the source to withdraw again, so the zombie card stays until a client nobody has implemented repeats the withdraw. The existing test test_positive_untouched_reconciliation_then_repeat_withdraw_cancels codifies this gap instead of closing it.

2. `src/heterodyne/admind/operations.py:248`: The lineage outbox projection only matches 'ask:', 'asknote:' and 'askd:' keys. It omits 'askr:{ask_id}:{mid}:{i}' parts (repeat_card resends, daemon.py:1213), so status and withdraw replies under-report the durable outbox parts the design says the lineage mapping binds.

   Failure scenario: An operator asks for the card again on an enrolled bead, which queues askr:abcd:<mid>:0..n. A status reply lists lineage[0].outbox without those keys. A client reconciling the delivered or pending effects of the operation sees an incomplete set, and the MAX_PARTS capacity check also undercounts.

3. `src/heterodyne/admind/operations.py:344`: Adopting existing legacy history either refuses permanently (lineage-context-conflict) when the newest row's digest differs from the current native digest, or enrolls a lineage with no live card when the newest row is terminal. Either way a bead with any stale, cancelled or denied legacy history can never get a card through approval.operation.v1, and legacy posting is forbidden for source clients.

   Failure scenario: Legacy ask A for bead B (digest D1) went stale with its refresh refused at capacity. Native is now D2. The source posts with context D2: the readout matches, but rows[-1].digest is D1, so it refuses on every retry indefinitely. Alternatively, if A was cancelled at D2, the post enrolls and returns ok/status with only a cancelled row, and every exact retry returns the same view. No approval card can ever be created for the operation.

4. `src/heterodyne/admind/operations.py:382`: withdraw() cancels lineage rows with set_ask and ask_notice but writes no audit.jsonl 'cancelled' record, unlike cancel_ask (daemon.py:1006). The refresh withdrawn branch (daemon.py:1570-1576) also returns before its 'refused'/'decided' audit write. This is the same class of gap as r1 finding 8, which was fixed only for posting.

   Failure scenario: The source withdraws a bead with two open siblings. audit.jsonl shows both cards posted but never shows them cancelled; only hashes in approval_operation_events exist. Separately, an attempt settled as cancelled by refresh after withdrawal leaves a 'deciding' audit entry with no settlement record, so audit-based tracking shows a permanently in-flight decision.

5. `src/heterodyne/admind/daemon.py:1458`: close_refused is wrapped in an extra outer transaction, so its audit.write runs inside that transaction. An audit failure rolls back the cancellation and escapes into run_attempt's generic `except Exception`, which then closes the attempt as 'open' with PREFLIGHT_FAILED.

   Failure scenario: An approval is withdrawn during preflight, issue_decision returns False, and close_refused commits close_attempt plus finish inside the outer block. Then audit.write raises OSError (disk full). The whole outer transaction rolls back, launched is False, and stop=('open', PREFLIGHT_FAILED) reopens the ask as 'open' in a withdrawn lineage with a misleading 'preflight failed, try again' reply. retry_maintenance (line 1797) has the same redundant wrapper.

6. `docs/admind-operation-protocol.md:13`: The wire doc is stale compared with the code. It states bead grammar `[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}`, but the code now uses approvals.BEAD_ID `[a-z0-9]{1,16}-[a-z0-9.]{1,32}`. Line 23 still says only legacy approval posts are refused, but every legacy AskPost kind is now refused. Line 25 does not describe the new withdrawn-stale and pending-maintenance cancellation settlement.

   Failure scenario: A Journal/T10 client written against the 'full wire docs' validates beads locally with the documented regex. It rejects legitimate beads such as 'abc-1.2' or 'a-1' that the server accepts, and accepts 'heterodyne-metaharness-ab12c', which the server refuses. It also expects legacy question asks on bound beads to still work.

7. `src/heterodyne/admind/ctl.py:181`: The request-length check, the readline timeout and the untyped envelope decode all run before operation_request is set. Operation requests that fail at those stages get the legacy AskReply shape instead of operations.Reply, so the closed reply contract is only partly fixed.

   Failure scenario: A client sends an approval.operation.v1 line over 262,144 bytes, or sends slowly past READ_SECONDS. The reply is {result:'refused', message:'malformed request'} with no `code`. A client decoding operations.Reply (forbid_unknown_fields, code required) hits DecodeError and must treat a definite pre-effect refusal as unknown.

8. `src/heterodyne/admind/operations.py:249`: The outbox projection forces a full outbox scan with CROSS JOIN and a computed CASE on every view(). view() runs twice inside the BEGIN IMMEDIATE of each withdraw and on every status poll, all under the Store lock.

   Failure scenario: With a large retained outbox history, each status poll and each withdraw (twice) scans the whole outbox while holding the store lock, which stalls the sender and the decision worker. Indexed prefix range lookups per lineage ask_id (key >= 'ask:abcd:' AND key < 'ask:abcd;', and the same for askr/asknote/askd) cost O(lineage·log n) and do not grow with history.

9. `src/heterodyne/admind/operations.py:173`: There is redundant and dead logic. The `row.kind != 'approval'` check can never fire because Store.approval_lineage already filters kind='approval'. operations.check runs three times per socket request (decode, the ctl check_ask_wire hook, handle). refresh calls before_commit, and commit_approval immediately calls it again, re-reading the lineage. The uid fallback expression is repeated five times in post().

   Failure scenario: Each refresh on a bound bead runs lineage() (up to 257 rows) twice in one transaction, and every request is validated three times. The dead kind check suggests protection that is actually enforced elsewhere, which makes the true invariant harder to see.

10. `src/heterodyne/admind/store.py:1062`: The Store layer reaches up into the protocol module through function-local imports (SCHEMA in __init__, decision_allowed in begin_attempt and issue_decision), while operations imports Store for type checking. This is a circular dependency papered over with local imports, instead of keeping the tombstone guard and schema in the store layer.

    Failure scenario: Any future change to operations' module-level imports, or a move of decision_allowed, can create a runtime import cycle inside the decision-claim CAS path. A tombstone guard in Store (for example Store.operation_withdrawn(ask_id)), with operations calling it, would remove the cycle.
