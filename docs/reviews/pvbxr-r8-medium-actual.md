I found no critical flaws, so this is good enough to implement as an offline, source-only PoC. Both R7 critical gaps are closed. What's left is moderate or minor.

**How the R7 gaps are closed.**
- **Single check:** `require_legacy_presentation` (`fulfillment.py:45`) refuses fulfillment whenever any of the three metadata columns is non-NULL.
- **Where it runs:** it guards `binding()`, which covers validate, apply, propose and confirm, and also `duplicate()` and `bounded_binding()`.
- **Selectors:** the suppression predicate checks the same condition against the current read, so `capacity_asks`, `fulfilled_asks`, `inactive_asks` and `capacity_count` stay consistent with each other.
- **Atomicity:** apply and confirm run the check inside the writer transaction, so it can't race with the receipt insert.
- **Failure handling:** in `verify_pass` the refusal becomes "conflict", and `on_fulfillment` turns a refused Get into "binding-unavailable".
- **No name clashes:** the unqualified column names don't collide with any of the joined tables today.

I only read the source; I ran no tests or probes. All R7 findings 4–7, R6 holds 1–9 and the earlier whole-change debt stay OPEN. Nothing here amounts to an APPROVE token.

10 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:1211`: An old V1 receipt followed by any metadata write leaves a card that stays active but has no way out: protected() still blocks cancel, answer, reminder and retire, and the new guard blocks fulfillment and duplicate, yet the card keeps counting toward MAX_OPEN.

   Failure scenario: A card is fulfilled under V1 (receipt row exists), then an external writer sets management='{}'. capacity_count() goes up by 1 for good. cancel_ask refuses because the card is protected, remindable_asks leaves it out, apply/confirm raise unsupported-management-fulfillment, and no repair path exists. Enough of these exhaust MAX_OPEN, and post_ask (daemon.py:1008/1110) then refuses every new card.

2. `src/heterodyne/admind/fulfillment.py:332`: Answered V2 management cards can no longer be fulfilled, so the only way out of capacity is a manual cancel; status 'answered' keeps them inside FALLBACK's active set forever.

   Failure scenario: Each answered management-consent card stays in capacity_asks and !asks after its operation completes. Once enough of them pile up, capacity_count() >= asks.MAX_OPEN and new asks are refused until someone cancels them one by one. The PoC loses the fulfillment-driven capacity release the projection was built for.

3. `src/heterodyne/admind/fulfillment_projection.py:215`: Suppression depends only on the current NULL state of the metadata, and the unchanged fq2_asks_update trigger does not watch those columns. Clearing the metadata back to NULL brings back the old 'valid' stamp with no re-verification.

   Failure scenario: A legacy receipt is valid at generation G. A writer sets management=X: the card goes active and the generation stays at G. Later the writer sets management=NULL: SUPPRESSED is true again using the stale 'valid' stamp, and the card drops out of capacity without a pass ever checking that the round-trip left the card as the receipt attested.

4. `src/heterodyne/admind/fulfillment.py:898`: confirm() raises inside the transaction for a pending intent whose card gained metadata after propose, so the rollback leaves the intent 'pending' and its notice keeps asking the operator to confirm something that can never succeed.

   Failure scenario: Propose on a V1 card, the notice is delivered, then metadata is written. Every operator !fulfill confirm returns unsupported-management-fulfillment. The intent is never retired or tombstoned, so it sits in inspect()/view()['pending'] with a live confirm prompt until it expires after 24h.

5. `src/heterodyne/admind/fulfillment_projection.py:25`: The repair lives only in Python; SCHEME, DDL and triggers are unchanged. A pre-repair (8effbd4) daemon or tool on the same DB still validates the projection and will fulfill and suppress V2 rows.

   Failure scenario: During a rollout or rollback, an 8effbd4 process sees the same 'fulfillment-projection-v2' scheme and applies a receipt to a V2 management card; its SUPPRESSED has no metadata check, so it hides the card. Nothing in the DB refuses the old writer. This is acceptable for an offline PoC but blocks any deployment claim.

6. `tests/test_admind_management_projection_composition.py:22`: The 'fulfilled' and 'dirty' cases now behave the same as active-answered: apply raises and no receipt exists, so the 'dirty' body edit tests nothing and no test exercises a V2 row going through fulfilled_asks.

   Failure scenario: A regression that wrongly suppressed or un-suppressed V2 rows that carry receipts would still pass the composition suite. Only test_admind_management_fulfillment_r7.py covers the receipt-plus-metadata path, and only through direct SQL.

7. `src/heterodyne/admind/fulfillment.py:45`: require_legacy_presentation runs an extra SELECT, when binding() already loads the three columns via store.ask() (_ASK_READ includes presentation/render_version/management). The same predicate is now kept as an SQL string and also checked as a separate query.

   Failure scenario: Every validate/apply/propose/confirm/view issues one more query under the store lock (view() runs it twice through bounded_binding/binding). Checking row.presentation/render_version/management on the row binding() already fetched would do the same job with one fewer round-trip and one fewer place to keep in sync.

8. `src/heterodyne/admind/fulfillment.py:42`: LEGACY_PRESENTATION uses unqualified column names and is pasted into a correlated subquery that joins four tables. It is correct only because none of those tables has a presentation/render_version/management column today.

   Failure scenario: If a later migration adds a column with one of these names to ask_fulfillments or fulfillment_verifications, SQLite binds the name to the inner table. The guard then checks the wrong row and V2 cards could be suppressed again. Writing asks.presentation etc. prevents this.

9. `src/heterodyne/admind/daemon.py:970`: Still OPEN from R7: ask.sock list joins capacity_asks() and inactive_asks(20) from two separate read transactions, while verify_pass can commit in between.

   Failure scenario: The worker stamps card X 'valid' between the two reads, so X appears in both lists and the count is off by one. The reverse transition drops X from the list entirely.

10. `src/heterodyne/admind/store.py:939`: Still OPEN from R7: remindable_asks uses a raw NOT EXISTS ask_fulfillments check with no read-only transaction. Every card that has a receipt but is still active, including the new 'legacy receipt + metadata' case, is listed as active but never reminded.

    Failure scenario: A legacy receipt card gains metadata and shows in !asks as needing attention, but the reminder path skips it permanently, so the operator gets no reminder for a card they are told is open.
