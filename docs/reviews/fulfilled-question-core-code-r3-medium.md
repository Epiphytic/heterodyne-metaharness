**Verdict: APPROVE as an offline PoC (ENABLED=False). REVISE before deployment.** I found no critical flaw in `2c0c5b5..7a74f02`. This was a static review only: no tests, probes or edits.

The R2 repairs claimed in the brief match the code at 7a74f02:
- **Notice ownership:** quoted answers now carry the senders' durable inbound owners from `inbound_sender`. Missing provenance makes the notice `unknown` (quarantined), and a revoked owner gets it suppressed (checked in `prepare_delivery`).
- **Confirmation:** `confirm` marks its own intent consumed, with `reason=NULL`, before `commit`/`retire_pending`, in one transaction, so no false tombstone is written.
- **Read-only get:** `view` uses a deferred `BEGIN` snapshot.
- **`!fulfill` failures:** database faults and programming errors now get separate fixed replies, and the refusal reply's `finish` is guarded.
- **Legacy socket:** strict JSON parsing now applies only to the fulfillment request types.
- **Startup:** routine opens only validate the schema. `rebuild()` runs in `recover()` before any socket or worker starts. The CLI turns `Invalid` into `StateDirError`.
- **Reconcile:** a fresh apply now reports `applied`, distinct from `already-applied`.
- **R2 cleanup items:** one shared capacity predicate with its exact complement, and ID checks use the shared alphabet and length.

Refusing to stage a notice when the complete context is too big or would be redacted stays as the approved availability limit; I did not count it as a finding. Quality debt is still unwaived, and I infer no pass from this review.

The remaining findings below are deployment risks or minor cleanups. #1 and #2 would block deployment; none blocks the PoC.

8 findings, most severe first:

1. `src/heterodyne/admind/fulfillment.py:605`: A notice that can never be delivered (owner unknown or revoked) is invisible to the controller: propose reports 'confirmation notice queued', get shows a normal pending intent, and an identical re-propose says 'existing notice retained'.

   Failure scenario: Answers recorded before the guard migration have NULL inbound.sender_key (guard_state.py:46 adds the column with no backfill). Any later propose therefore quarantines the notice as 'unknown' and it is never sent, so !fulfill always fails 'unbound-confirmation'. The intent still holds the ask's only pending slot and counts against the global cap of 40 for up to 24h, and nothing in the reply or view tells the controller to dispose it or use the controller-receipt route. Same result when a consumed answer's operator is revoked before delivery (suppressed). This is deployment-relevant because every pre-upgrade answered ask is affected.

2. `src/heterodyne/admind/fulfillment_cli.py:121`: In reconcile --apply, a DecodeError or Invalid from send() part-way through the loop is not caught per entry; only CtlUnavailable is. The whole result list and continuation token are lost even though earlier entries were already committed.

   Failure scenario: Entry 1 applies, then entry 2's reply comes back in the legacy AskReply shape (the socket's refused('malformed request') or a check refusal) or fails to decode. msgspec.DecodeError reaches command(), which prints only 'invalid fulfillment input' and exits 1. The controller never sees that entry 1 returned 'applied' this run, which is exactly the response-loss distinction the R2 #8 fix was meant to keep.

3. `src/heterodyne/admind/store.py:1243`: transaction(read_only=True) lets a later nested transaction() join the deferred snapshot without complaint, so a write nested inside it silently loses BEGIN IMMEDIATE semantics.

   Failure scenario: A future change makes view() call something that writes, for example refresh(). The write runs inside a deferred read transaction and has to upgrade the lock, so under a concurrent writer it fails SQLITE_BUSY at once with no busy-timeout wait, instead of being serialized as every other mutation is. Nesting a writer inside a read-only block should raise.

4. `src/heterodyne/admind/daemon.py:2420`: The operator principal stored in the receipt comes from the in-memory self.senders cache with a '' fallback, not from the durable authenticated inbound.sender_key.

   Failure scenario: If the mid is not in self.senders (cache eviction, or any future path that calls handle() without remember_sender), the immutable receipt records authority {principal: ''}. The design requires verified operator principal identity, and authorised(mid) has just checked the durable inbound_sender(mid), which is available. Use that value.

5. `src/heterodyne/admind/daemon.py:2414`: With ENABLED=False the new '!fulfill' check still runs first and replies 'Fulfillment unavailable under current authorization.', which changes legacy behaviour and wrongly blames authorization when the capability is simply disabled.

   Failure scenario: An operator replies '!fulfill' to a card while the capability is off. Before this change the text went through the existing command/bump/ask routing. Now it is intercepted before bump_for_message and ask_for_message and answered with an authorization message, so a disabled feature reads as a permissions problem.

6. `src/heterodyne/admind/fulfillment.py:587`: The refusal code 'notice-redaction-or-bounds' now carries prose ('...: complete request/answer context cannot be delivered safely'), breaking the documented contract that Invalid holds a fixed public refusal code.

   Failure scenario: A controller or test that matches Reply.message == 'notice-redaction-or-bounds' (as refusal_outcome does for other codes) stops recognising it and falls through to 'unsupported'. Keep the code exact and put the explanation in the docs or a separate field.

7. `src/heterodyne/admind/fulfillment.py:290`: rebuild() re-hashes every retained fulfillment's complete ask and answer set inside one BEGIN IMMEDIATE transaction on every daemon start, and receipts are never pruned.

   Failure scenario: As receipts accumulate (up to about 64KB of answers each), each daemon start holds the SQLite writer lock and blocks before serving for longer and longer. Concurrent CLI writers wait out the 5s busy timeout and then fail. The design allows the rebuild, but it is unbounded for deployment; a check against a stored binding version or digest would bound it.

8. `src/heterodyne/admind/daemon.py:805`: The post-capacity checks still use len(self.store.capacity_asks()), loading every full ask row, although capacity_count() now exists with the same shared predicate.

   Failure scenario: Every ask post and its post-await recheck (lines 805 and 880) materialises all capacity-active rows, including bodies, just to compare a count with MAX_OPEN. capacity_count() gives the same answer with a COUNT(*).

Deployment would still need: a provenance plan or a visible diagnostic for answers that predate the guard migration (#1), a bounded startup rebuild (#7), live and recovery acceptance, a consumer compatibility inventory, and the unwaived quality debt resolved. The captured evidence (freeze.json and the manual observed capture) is not reserved-profile proof, so I did not use it as a pass.
