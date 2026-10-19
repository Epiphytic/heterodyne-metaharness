I reviewed the full diff `2c0c5b5..5b84f87` without editing, running anything, or touching any live system. It adds three files: `src/heterodyne/delivery.py` (501 lines), `tests/test_delivery_policy.py` and `docs/plans/plan6c-pure-policy-slice.md`. I checked them against the scope doc, approved d4 §§1 and 3–5, and the existing `ask:`/`askr:`/`askd:` key pattern in `store._CARD_KEY`.

The module is stdlib-only, nothing imports it, and it has no I/O, so it stays inside the slice's boundary. I found 10 issues, listed most severe first:

10 findings, most severe first:

1. `src/heterodyne/delivery.py:175`: A CanonicalRoute is not tied to the copy's reason or occurrence: any canonical-v1 copy (reminder, digest, update, recovery) can carry an `ask:` route, and the request_id of an askr/askd route is never checked against the occurrence or inbound request ID.

   Failure scenario: FrozenCopy(reminder_request, ..., 'canonical-v1', parts, ..., route=CanonicalRoute('ask','k7m2')) gets native key 'ask:k7m2:0', the same as the initial card. `_CARD_KEY`/`ask_for_message` treat that as a decidable card, so a reminder becomes an answerable card (design: 'A bump is not a card'). An initial copy and an update copy (rev-1 and rev-2, different copy_ids) on the same route also share native keys, so native same-key dedup can drop the second copy. Likewise an askd copy whose request_id differs from its join/reissue occurrence (the inbound request ID that d4 makes the copy's idempotency key) is accepted.

2. `src/heterodyne/delivery.py:442`: Receipt has no shape validation. accept_receipt always calls `_bounded_ids(receipt.message_ids)`, which raises TypeError (not PolicyError) when message_ids is None or another non-sliceable value; a list is quietly treated as invalid; a str is split into characters.

   Failure scenario: After mark_dispatch, a decoder hands over Receipt(..., message_ids=None) for a missing field. `ids[:64]` raises TypeError, so no uncertain/invalid-receipt transition is produced. The owner sees a generic exception on a possibly-sent attempt instead of the closed uncertain tag. A decoder that returns `['f'*64]` (a list) can never confirm even a correct receipt.

3. `src/heterodyne/delivery.py:443`: Uncertainty evidence is replaced, not kept: a second mark_uncertain overwrites the earlier uncertainty kind, each invalid receipt replaces the earlier raw_ids, and a mismatched (foreign-copy or wrong-ordinal) receipt's raw IDs are still stored on this attempt.

   Failure scenario: The attempt goes uncertain('recovered-dispatch-mark'), then gets receipt A (two IDs, kept as raw_ids). A later empty or misrouted receipt sets raw_ids=() or another copy's IDs. The original multi-ID diagnosis that d4 §5 says to 'retain privately' is lost, and the attempt now holds a foreign copy's IDs.

4. `src/heterodyne/delivery.py:217`: AttemptIdentity and Attempt do not bind the reply target (thread root / part-0 message ID), source token or trust/registration epoch, which d4 §3 says each attempt binds; parent_confirmed is a bare bool with no tie to which part-0 message is the parent.

   Failure scenario: Part 1 is reserved with Gates(parent_confirmed=True) under part-0 message X. Part 0 is later re-resolved or another copy's root is supplied, and the retry or receipt for part 1 still correlates because nothing records the target. A continuation threaded under the wrong root, or sent under a changed trust epoch, still settles as confirmed.

5. `src/heterodyne/delivery.py:490`: copy_status collapses distinct states: a fully cancelled never-issued copy and a copy whose parts are all exhausted-held both come back as 'partial-held', and a superseded copy with an unresolved part reports 'superseded', hiding the uncertainty.

   Failure scenario: copy_status((cancel(p0), cancel(p1))) returns 'partial-held' rather than cancelled. Three proven-prewrite failures on every part also return 'partial-held', so the d4 requirement that 'exhausted-held stays visible in the digest/health' is lost. A superseded copy with a dispatch-marked part hides a possibly-sent native message from health views.

6. `src/heterodyne/delivery.py:363`: The closed held/refused codes from d4 §4 are not implemented. Outcomes are free-form PolicyError strings, reserve merges source-changed / trust-denied / parent-incomplete into one message, and 'not-due', 'exhausted-held' and 'clock-unready' do not follow the same prefix scheme.

   Failure scenario: A future owner cannot tell a source-changed hold from a trust-denied hold (d4 handles them differently) without parsing message text. 'exhausted-held' does not match the design code 'exhausted', and 'not-due' is not in the closed set, so string matching against the design vocabulary is brittle.

7. `src/heterodyne/delivery.py:387`: prove_unissued accepts a prewrite-refusal only while the attempt is still 'reserved'. d4 puts the dispatch-mark barrier before writer.write and separately classifies `_open` failure before the write as prewrite, so a correlated `_open` failure after the mark can never become proven_unissued.

   Failure scenario: The owner commits mark_dispatch and then the transport's `_open` fails before any byte is written (a positive prewrite-refusal). prove_unissued raises 'held: proof conflicts', the part is stuck uncertain-held, and the 3-attempt automatic budget never runs for ordinary connect failures. The narrowing is documented in the scope doc, but it changes how d4's retry design works in practice.

8. `src/heterodyne/delivery.py:281`: Every PartState construction, including every replace() in each reducer step, calls copy.native_key, which recomputes parts_hash (a JSON dump plus SHA-256 over all parts); _receipt_matches computes it again. The value is not cached on the frozen copy.

   Failure scenario: A canonical-v1 copy with 1024 parts and 512 KiB total: each transition on each part rehashes about 512 KiB, so one reserve/mark/receipt round over the copy hashes about 1.5 GiB. A cached parts_hash/native_key on FrozenCopy would remove the repeat work.

9. `src/heterodyne/delivery.py:243`: The allowed values of the Literal aliases (Reason, Phase, ProofKind, Uncertainty) are repeated as hand-written sets in CopyRequest, _validate_attempt, NonDispatchProof and mark_uncertain, and the inline 64-hex regex in _receipt_matches duplicates `_hex`.

   Failure scenario: Adding a kind such as 'healthy-absent' to ProofKind needs edits in 3–4 places; missing one lets the type and the runtime check disagree. `typing.get_args(ProofKind)` and `_hex(ids[0], 64)` would give one source of truth. `attempt.proof in {...}` at line 258 is also redundant after the check just above it (it is the same as `attempt.proof is not None`).

10. `src/heterodyne/delivery.py:130`: The reminder copy request key also hashes source.revision and canonical_id, so request-key dedup gives a different reminder copy after every source refresh or new ask_id instead of one per logical decision.

    Failure scenario: A reminder for logical-1 at rev-1 and another at rev-2 (or after a canonical ask refresh with a new canonical_id) get different keys, so FakeOwner admits two reminder copies. The test at line 302 treats lifetime uniqueness as deferred, but nothing in this slice stops the request key from being used as if it gave 'one reminder per card'.

This is not an APPROVE token.
