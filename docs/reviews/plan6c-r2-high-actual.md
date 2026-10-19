The review of `2c0c5b5..e2c9921` found 10 issues. The first 9 are correctness or approved-design-fidelity problems in `src/heterodyne/delivery.py`, and #10 is a cleanup. Nothing was edited, run or probed, and nothing was verified beyond reading the code. Per the instructions this is not an APPROVE token.

10 findings, most severe first:

1. `src/heterodyne/delivery.py:640`: On a confirmed attempt, a later correlated receipt with a different single message ID (evidence of a second native send) raises an integrity error. Its IDs are never kept in the bounded diagnosis, and part/copy status stays confirmed/complete.

   Failure scenario: Part is confirmed with receipt ('e'*64,). A second receipt with matching identity/binding/account/group/key/text_hash arrives with ('a'*64,). accept_receipt raises PolicyError('integrity: conflicting terminal receipt'), so no state is returned, nothing is added to raw_ids/uncertainties, and copy_status still reports 'complete' even though there is direct evidence of a duplicate native message. d4 §5 requires multiple or conflicting IDs to be retained and treated as uncertain/invalid-receipt.

2. `src/heterodyne/delivery.py:644`: A receipt that contradicts a retained positive non-dispatch proof is dropped or misattributed. A receipt for a proven_unissued attempt raises 'held: receipt without dispatch evidence', and one for an earlier proven ordinal gets folded into the current attempt as uncorrelated invalid-receipt with its IDs discarded.

   Failure scenario: Ordinal 1 is proven_unissued(prewrite-refusal) and ordinal 2 is dispatch-marked. A late native receipt for ordinal 1 with ('e'*64,) arrives. correlated=False, so raw_ids stay unchanged and only 'invalid-receipt' is added to ordinal 2. When ordinal 2 later confirms, copy_status returns 'complete'. The only evidence that the 'proven' attempt really sent (a duplicate card) is lost. ReceiptClaim has no ordinal, so claims cannot catch this either.

3. `src/heterodyne/delivery.py:387`: PartState/Attempt validation blocks erasing an uncertainty by relabeling the phase, but not erasing a dispatch mark. Attempt keeps no record that it was dispatch-marked, and _validate_settlement accepts a bare prewrite-refusal (write_not_invoked=False, binding=None) or a reserved-no-dispatch fact on any proven_unissued attempt.

   Failure scenario: An owner rebuilds state from storage, or does replace(state, history=(replace(dispatch_marked_attempt, phase='proven_unissued', proof='reserved-no-dispatch', proof_fact=NonDispatchProof(id,'reserved-no-dispatch')),)). __post_init__ accepts it, and reserve() then allows ordinal 2 for a possibly-written send. That is exactly the bare-proof-after-mark path prove_unissued refuses, so the r1 #7 invariant does not hold at the constructor.

4. `src/heterodyne/delivery.py:537`: A continuation's reply_target is never tied to the confirmed part-0 message ID of the same copy. reserve only checks that it is non-None, and copy_status reports 'complete' without checking that every part>0 binding.reply_target equals part 0's confirmed message_id.

   Failure scenario: Part 0 confirms as 'e'*64. Part 1 is built with reply_target='a'*64 (another copy's root or a stale root), reserved with Gates(True,True,True, state.binding), and confirmed. copy_status((p0,p1)) == 'complete' even though the continuation is threaded under a foreign root. d4 §5 says 'Continuation needs confirmed part0 of that same copy/destination/binding'.

5. `src/heterodyne/delivery.py:48`: PolicyError still gets its code by parsing the message text, and every 'held:' message without an explicit code becomes 'recovery-required'. Ordinary contention, refusal and already-terminal cases are therefore mislabeled in the closed vocabulary.

   Failure scenario: reserve() on a part whose attempt is still 'reserved' (the 'hold the SAME attempt' case) or already 'confirmed' raises 'held: no positive non-dispatch for next attempt' with code recovery-required. prove_unissued with a mismatched ordinal/identity gives held/recovery-required instead of refused/binding-conflict. mark_dispatch/accept_receipt on a never-reserved part ('held: no reserved attempt') also gives recovery-required. An owner switching on .code would escalate normal states into operator recovery, contradicting the r1 #6 claim that codes do not depend on diagnostic text.

6. `src/heterodyne/delivery.py:546`: A changed current source token, registration/trust epoch or reply target in the gate binding is returned as a terminal refused/binding-conflict, while the same real-world condition signalled through source_current/trust_current=False is held source-changed/trust-denied.

   Failure scenario: After a membership/trust epoch change, an owner passes Gates(True,True,True, AttemptBinding(..., registration_epoch='epoch-2', ...)). reserve raises tag='refused', code='binding-conflict' (no explicit code, so the prefix parse applies). d4 §4 treats these as held conditions handled through current source/trust checks, so the copy is permanently refused instead of held.

7. `src/heterodyne/delivery.py:143`: The 'recovery' reason is admitted with any occurrence text, even though the plan says recovery epoch linkage is unsupported in this slice and d4 requires an independently sealed loss epoch.

   Failure scenario: CopyRequest(source, dest, 'recovery', 'anything-1', ...) and then 'anything-2' produce two different request keys, so each reconcile pass can admit a fresh recovery copy with a fresh d6c key, and reserve/mark_dispatch accept them. This breaks d4's 'prevents reconcile-created copies'. The reason should be refused as unsupported-policy until the sealed-epoch contract exists.

8. `src/heterodyne/delivery.py:528`: The lifetime reminder budget is not connected to the reducer. reserve() lets a reason='reminder' copy reserve without a consumed ReminderBudget, so the one-reminder-per-logical-decision rule from r1 #10 holds only if the caller remembers to call consume_reminder.

   Failure scenario: Two reminder copies for logical-1 (rev-1 and rev-2, with different request keys) are each admitted by the FakeOwner and each reserved and confirmed through reserve/mark_dispatch/accept_receipt with no budget input. Two reminders go out, against d4 §6 'at most ONE reminder ... Reserve budget atomically before native effect'.

9. `src/heterodyne/delivery.py:230`: The default source_token is written into an init field, so dataclasses.replace(copy, request=...) carries the OLD source's digest into a copy for a different source revision or canonical ID. The 'frozen source token' in AttemptBinding then names the wrong source.

   Failure scenario: updated = replace(original, request=<rev-2 request>) (as in test_same_legacy_native_key... and test_immutable_attempt_payload...) still has source_token == digest(rev-1 source). The binding's source_token claims rev-1, and the 'source binding' test passes only because copy_digest changed, not because the token did. An owner comparing the gate token against the current source digest would refuse the legitimate rev-2 copy or accept a stale one.

10. `src/heterodyne/delivery.py:644`: accept_receipt rebuilds the same six-field header correlation that _receipt_matches has just compared (identity, binding, account, group, native_key, text_hash).

    Failure scenario: The two header checks are duplicated and can drift apart: adding a correlation field to one but not the other would let foreign IDs into the retained diagnosis, or reject valid ones. Have _receipt_matches return (correlated, valid), or factor out one _correlated(state, receipt) helper used by both.
