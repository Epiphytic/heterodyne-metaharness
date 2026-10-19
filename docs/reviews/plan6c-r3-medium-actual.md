**Verdict:** I found no critical flaw that should stop this pure offline slice going ahead as a PoC. It adds no live caller, adapter, persistence or authority. The top three findings are real correctness or design-fidelity gaps in the reducer and should be fixed before any owner is wired to it. This is not an APPROVE token.

I read the full `2c0c5b5..4616419` diff, the Plan6C slice doc, the R2 report and dispositions, the full `src/heterodyne/delivery.py`, and the R2 tests. I made no edits and ran no tests or probes. Every finding comes from reading the code only; none was checked by running anything.

10 findings, most severe first:

1. `src/heterodyne/delivery.py:747`: A correlated receipt for an attempt that is still 'reserved' (no dispatch mark) is rejected with held/contention and its evidence is thrown away, so the owner can then prove the attempt unissued and retry, even though a native send is known to have happened.

   Failure scenario: The part is reserved at ordinal 1 and the owner's stored mark was lost or the barrier was violated. A receipt with matching identity, binding, account, group, native key, text hash and ('e'*64,) arrives. _retained_receipt skips it because only confirmed/proven_unissued phases are retained, and accept_receipt then raises 'held: receipt without dispatch evidence' (contention), so no raw IDs and no contradiction flag are kept. Next, prove_unissued(NonDispatchProof(id,'reserved-no-dispatch')) succeeds, reserve() allows ordinal 2, and a duplicate card goes out. This is the same pattern R2 #2 fixed for proven_unissued, but the reserved phase was missed, and the transient 'contention' code hides that recovery is needed.

2. `src/heterodyne/delivery.py:573`: _effect_policy, reserve and mark_dispatch only look at the part's own history (and part 0 at reserve time), so a contradiction on any other part of the same copy does not stop new effects on the rest. The plan says a contradiction holds the entire copy.

   Failure scenario: A 4-part copy has parts 0 and 1 confirmed, and part 1 then gets a conflicting correlated receipt (contradicted=True, evidence of a duplicate native message). copy_status reports 'uncertain-held', yet reserve(part2, ...) with a valid confirmed part 0 parent, then mark_dispatch(part2), both succeed and more continuation sends go out. Also, if part 0 is contradicted after part 1 is reserved, mark_dispatch(part1) does not re-check the parent and still dispatches. The real fix is a copy-level hold input to the reducer, not just the aggregate check in copy_status.

3. `src/heterodyne/delivery.py:620`: copy_status calls _parent_matches(state, first), which requires part 0 to be not superseded, so a multi-part copy that was fully and cleanly delivered and then superseded reports 'superseded-uncertain-held' instead of 'superseded'.

   Failure scenario: Parts 0 and 1 are both confirmed, with part 1's reply_target equal to part 0's message ID. A normal update supersedes both: copy_status((supersede(p0), supersede(p1))). For part 1, _parent_matches returns False because parent.superseded is True, so wrong_parent=True, unresolved=True, and the result is 'superseded-uncertain-held'. That is a false recovery hold on every normal supersession of multi-part cards, and it disagrees with part_status, which reports 'superseded'. The only existing test supersedes a copy whose part 1 was never reserved.

4. `src/heterodyne/delivery.py:643`: For a reserved attempt, prove_unissued accepts any proof without checking its binding or kind. A mismatched proof.binding then fails later in the constructor as 'integrity' instead of the closed 'binding-conflict' code, and a bare prewrite-refusal (write_not_invoked=False) is accepted on a reserved attempt.

   Failure scenario: On a reserved attempt, prove_unissued(state, NonDispatchProof(id, 'reserved-no-dispatch', binding=<stale binding>)) raises PolicyError(code='integrity') from _validate_settlement, not binding-conflict, so an owner switching on .code treats a stale proof as store corruption. Separately, NonDispatchProof(id,'prewrite-refusal', write_not_invoked=False) settles a reserved attempt and is stored as a prewrite-refusal fact that never showed the write was not invoked. The diagnostic text 'held: ...' also contradicts the raised tag 'refused'.

5. `src/heterodyne/delivery.py:45`: PolicyError silently turns any unknown code into 'invalid-shape' (tag refused), so a typo or a future code added at one call site turns a hold into a terminal refusal instead of failing loudly.

   Failure scenario: A future edit passes code='recovery_required' (underscore) or a new held code that is not yet in HeldCode. The constructor rewrites it to invalid-shape, tag becomes 'refused', and the owner permanently refuses a copy that should only be held. This defeats the R2 #5 claim that every site passes an explicit closed code. It should raise or assert instead of coercing.

6. `src/heterodyne/delivery.py:332`: check_native_collision compares native keys without regard to destination (account/group). Canonical ask:/askr:/askd: keys contain no destination, so the same ask admitted for two destinations always collides and is refused as integrity.

   Failure scenario: Question ask 'k7m2' is admitted as a canonical-v1 initial copy for destination A (group g1). Admitting the same source and revision for destination B (group g2) yields identical 'ask:k7m2:0' keys, and check_duplicate fails because the destination differs: integrity refusal. Native idempotency keys are scoped per account/group, so this is a false conflict, unless the approved design limits canonical asks to a single destination, which the code does not state or enforce.

7. `src/heterodyne/delivery.py:705`: _bounded_ids stops retaining at the first non-string entry and counts bytes that are later dropped when a truncated multi-byte UTF-8 character is discarded, so the bounded raw diagnosis can lose all IDs from a malformed receipt.

   Failure scenario: A correlated contradicting receipt with message_ids=(None, 'e'*64) gets contradicted=True, but raw_ids gains nothing because of the 'break' at the first non-string. The diagnostic evidence of the duplicate message ID is lost, despite the plan's claim that bounded raw diagnosis is retained for malformed/multiple IDs. Skipping the bad entry, or recording a placeholder, would keep the later valid-looking IDs.

8. `src/heterodyne/delivery.py:326`: The check_native_collision docstring says native legacy keys omit revision, but d6c keys hash the full request including revision; only canonical route keys omit it, so the stated invariant is wrong for non-route copies.

   Failure scenario: A future owner relies on the docstring and expects d6c revision changes to collide and be refused. Instead d6c keys for rev-1 and rev-2 differ, check_duplicate is never reached, and both are admitted with separate native keys. That is harmless as written, but the documented contract is misleading for owner-index design.

9. `src/heterodyne/delivery.py:525`: Gates.parent_confirmed duplicates Gates.parent for continuations (both must agree), and accept_receipt recomputes _correlated(state, current, receipt) right after _receipt_matches has already computed it.

   Failure scenario: Callers must supply a parent_confirmed bool that can disagree with the parent PartState: for index 0 the bool is the only check, for index > 0 it is redundant with _parent_matches. That is two sources of truth for one fact. Deriving it (parent_confirmed = index == 0 or _parent_matches) and returning (correlated, valid) from one helper would remove the drift risk R2 #10 was meant to close.

10. `src/heterodyne/delivery.py:816`: copy_status and _parent_matches compare whole FrozenCopy values (including the parts tuple, up to 512KiB) per part, and PartState.binding rebuilds and re-validates an AttemptBinding on every access, including inside every __post_init__ loop.

    Failure scenario: With states rebuilt from storage (equal but different FrozenCopy objects), copy_status over a 1024-part canonical copy does about 2×1024 full payload comparisons, up to about 1GB of string compares. Each transition also re-runs regex validation on the binding once per retained attempt. Comparing copy_digest (already cached) instead of the whole copy would make this O(1) per part.
