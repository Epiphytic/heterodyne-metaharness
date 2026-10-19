The composition resolution itself is correct: `capacity_asks`, `fulfilled_asks` and `inactive_asks` now read the full column list inside the read-only transaction, and their predicates and ordering match the fulfillment branch exactly. It is still not ready to implement as a PoC: two composition gaps between management cards and fulfillment look critical.

1. **Metadata is outside the fulfillment hash.** Fulfillment does not bind or watch the management metadata columns, so a recorded fulfillment does not prove which change the operator consented to.
2. **Malformed cards can still be fulfilled.** The fulfillment paths never apply the management check that the answer, remind and details paths enforce.

The other findings are minor, for test coverage or cleanup. Nothing was run; this comes from reading the source only.

7 findings, most severe first:

1. `src/heterodyne/admind/fulfillment_projection.py:66`: The projection's ask field list (ASK_FIELDS), the update trigger fq2_asks_update and bounded_binding all leave out presentation, render_version and management, so a fulfillment receipt on a V2 management card does not bind the consented request_id/descriptor_sha256/audit_url, and later changes to those columns never bump the generation.

   Failure scenario: A V2 card (question kind) is answered and fulfilled via apply and is suppressed from capacity (the composition test's 'fulfilled' case does this). Then any writer runs UPDATE asks SET management='{...other descriptor_sha256...}'. The trigger does not fire, the stored verification stays 'valid' for the current generation, and the card stays in fulfilled_asks with a receipt the projection accepts. The fulfillment record now attests to a descriptor the operator never saw, and the native V2 binding and the fulfillment projection disagree with no conflict raised.

2. `src/heterodyne/admind/fulfillment.py:360`: Lifecycle.validate/binding, used by propose, apply and the !fulfill confirm path, never calls management.stored(row). The card branch's answer_refusal, remind and details paths refuse V2 rows with malformed metadata; the fulfillment paths do not.

   Failure scenario: An answered V2 card whose management column is later corrupted (e.g. '{}', or render_version stored as text and decoded to the -1 sentinel) is refused for answers, reminders and details. Its fulfillment Propose/Apply/confirm still passes validate, writes an immutable receipt and suppresses the card from capacity. A card the renderer refuses as unsafe ends up permanently 'fulfilled (lifecycle)' in !asks.

3. `tests/test_admind_management_projection_composition.py:14`: The composition test's 'dirty' case only changes body (a hashed field); there is no case that changes presentation/render_version/management after fulfillment, and no case that fulfills a malformed-metadata row. These are exactly the two composition seams above.

   Failure scenario: The five-case suite passes while a post-fulfillment metadata rewrite leaves the card suppressed and 'valid', and while a malformed V2 row is fulfillable. The claimed composition evidence does not cover the interaction between the two branches' authority models.

4. `src/heterodyne/admind/daemon.py:970`: AskList builds its result from two separate read-only transactions, capacity_asks() and inactive_asks(20). The fulfillment-verification worker runs verify_pass on its own executor thread against the same Store and can commit between them.

   Failure scenario: Card X is active when capacity_asks() runs. The worker then commits a valid verification, moving X into the NOT-capacity set, and inactive_asks(20) returns X again. The ask.sock list reports X twice, and the count message is too high. The reverse transition can drop X from the list entirely.

5. `src/heterodyne/admind/store.py:938`: remindable_asks still uses raw 'NOT EXISTS ask_fulfillments' and no read-only transaction, while the capacity selectors now use the projection predicate. Cards whose fulfillment is in conflict or unverified show as active but can never be reminded.

   Failure scenario: A fulfilled card goes dirty (e.g. body edited), so verification reports a conflict. capacity_asks/!asks shows it as open again (the test's 'dirty' case asserts this), but remindable_asks excludes it forever because a receipt row exists. The reminder/bump path silently skips an active card, and the operator gets no reminder for a card listed as needing an answer.

6. `src/heterodyne/admind/fulfillment.py:314`: Lifecycle.binding() keeps its own copy of the hashed ask field list, separate from fulfillment_projection.bounded_binding and ASK_FIELDS. The three copies must stay in sync by hand.

   Failure scenario: Adding the management columns to fix the first finding needs coordinated edits in three places (binding(), bounded_binding, the ASK_FIELDS trigger). Missing one makes propose/apply hashes differ from verify_binding hashes, so every new receipt immediately reads as 'conflict'. One shared constant should define the field tuple.

7. `src/heterodyne/admind/store.py:946`: Every capacity selector calls fulfillment.capacity_predicate(), which re-runs projection validate(db) each time, and post_ask also calls require_projection() before capacity_count().

   Failure scenario: !asks runs validate at least twice (capacity_asks plus fulfilled_asks), ask.sock list twice, and post_ask twice, all while holding the store lock. That is repeated schema/meta validation on hot command paths. Computing the predicate once per outer transaction and passing it in would remove the repeats.

The first two are the critical ones. One option is to put the three metadata columns into the trigger and binding field list, accepting that the fulfillment binding hash changes. The other is to have the fulfillment lifecycle refuse V2 management cards entirely until a V2 fulfillment binding exists. Either needs a decision from you before implementation.

The diff is in `<operator-home>/repos/heterodyne-metaharness-v2-btq-btq-pvbxr`. The files involved are under `src/heterodyne/admind/`: `fulfillment_projection.py`, `fulfillment.py`, `store.py` and `daemon.py`, plus the test `tests/test_admind_management_projection_composition.py`.
