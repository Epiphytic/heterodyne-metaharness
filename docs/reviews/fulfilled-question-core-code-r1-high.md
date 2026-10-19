**Verdict: REVISE.** I reviewed the source diff from 2c0c5b5..5186ff2 against the approved design at e8b5db3. This was a read-only static review: no tests, probes or edits. None of the CLAUDE.md files apply to this Python code, so there are no convention findings. The most serious problem is #1: questions answered by an emoji reaction can never be fulfilled. #2 is next: pending intents that are left behind use up the 40-intent cap.

10 findings, most severe first:

1. `src/heterodyne/admind/fulfillment.py:216`: validate_binding requires every consumed_answers entry to be 64 lowercase hex, but answers recorded from reactions are stored with message_id 'r:<64hex>' (store.REACTION_PREFIX). So a reaction answer can never be cited as a consumed answer, by either route.

   Failure scenario: Operator answers a yes/no question card with a thumbs-up (K17q). ask_answers.message_id = 'r:abcd…'. binding().answer_ids lists 'r:abcd…', but any Completion or Declaration that cites it fails check() with 'answer-reference'. A cite that leaves it out fails consumed_answers>=1. The question can never be fulfilled and keeps holding a MAX_OPEN slot, even though the design treats a reaction with its retained authenticated source as valid.

2. `src/heterodyne/admind/fulfillment.py:514`: Pending operator intents are never retired or consumed when their ask is fulfilled by controller apply, cancelled, superseded or answered again. They keep counting toward the global 40-intent cap, and view() keeps reporting them as pending.

   Failure scenario: propose for ask A creates intent I (pending). A controller then applies a receipt for A. commit() inserts the fulfillment but leaves I 'pending'. get A shows lifecycle=fulfilled with pending=I, and a later !fulfill on I answers 'conflict'. Once about 40 such orphans build up (also from cancelled or re-answered asks), every new propose fails with 'pending-cap'. The only fix is a manual dispose per intent, which needs each exact digest.

3. `src/heterodyne/admind/daemon.py:2080`: The !fulfill branch only catches fulfillment.Invalid. A sqlite3.Error (for example a 5s busy timeout) or any other exception escapes handle() with no fixed 'unavailable' reply. Also, confirm() and finish() run as two separate transactions.

   Failure scenario: The DB is locked by a CLI or other process during BEGIN IMMEDIATE in confirm(). OperationalError reaches the worker's generic except, which writes only a 'handler failed' audit line. The operator gets no reply and the inbound row stays 'received'. A crash between confirm's commit and finish() leaves the receipt committed with the inbound row unsettled. The design requires a fixed unavailable outcome with no state change.

4. `src/heterodyne/admind/fulfillment.py:199`: validate_binding only caps evidence at 8 and allows zero. Controller apply on a kind=merge ask is accepted with no terminal integration evidence, against design §2.

   Failure scenario: A same-UID controller applies an operation-completion-v1 receipt for a merge ask with evidence=[]. validate() passes because the kind is merge and the status is answered. The merge is marked fulfilled and its capacity is released without any reference to the integration artifact.

5. `src/heterodyne/admind/fulfillment.py:506`: propose returns an existing pending intent with identical declaration bytes as 'pending' (exit 0) even after its 24h confirm window has passed. The caller is told a confirmable notice exists when it does not.

   Failure scenario: Intent proposed at T. At T+25h the controller re-runs the same propose. It gets 'pending: existing notice retained' with the old intent_id. The operator replies !fulfill and gets 'stale-intent'. Nothing in the propose result tells the controller it must use --replace-intent.

6. `src/heterodyne/admind/fulfillment.py:367`: view() computes 'eligible' from kind, status=answered and having answers only. Fulfilled asks keep status=answered, so an ask that is already fulfilled (or in conflict) is reported as eligible=true, against the design's 'no prior fulfillment' rule.

   Failure scenario: get on a fulfilled ask returns lifecycle='fulfilled' together with eligible=true. A consumer or adapter that reads 'eligible' (the documented diagnostic projection) without first checking 'receipt' will try to apply again or propose. That attempt gets conflict or protected refusals, or the consumer misreports the state.

7. `src/heterodyne/admind/ctl.py:190`: On Linux, peer_credentials_checked(sock) is called without peer_pid's None guard, so a missing socket raises AttributeError (not caught by 'except OSError') for every ask.sock request, legacy ones included. It also unpacks uid/gid as signed '3i'.

   Failure scenario: writer.get_extra_info('socket') returns None. Before this change Peer(None) went to on_ask. Now None.getsockopt raises AttributeError, the outer except turns it into handler-failed, and every post/get/list/cancel fails. Separately, a service UID of 2^31 or more unpacks as negative, raises OSError, and falls back to uid=None, so a legitimate peer is refused as 'unverified-peer'.

8. `src/heterodyne/admind/fulfillment.py:344`: Lifecycle.protected(), stored() and binding() run queries on store.db without Store's _locked RLock. The daemon calls them outside a transaction (in !asks, reminders, !details, repeat), so they skip the serialization that Store needs because worker threads share the same connection.

   Failure scenario: A worker thread holds BEGIN IMMEDIATE on the shared connection and inserts a fulfillment row that is later rolled back. An event-loop call to self.store.fulfillment.protected(...) on the same connection, without the lock, reads that uncommitted row. A !details or reminder decision is then based on a fulfillment that never committed.

9. `src/heterodyne/admind/fulfillment.py:565`: commit() and propose() call store.enqueue directly. This re-implements Admind.post_parts (chunk 0 top-level, thread_key for later chunks) and skips Admind.post's central redaction (B1) and wake.set().

   Failure scenario: A socket-originated propose or apply queues notices that wait up to 5s for the outbox poll, because the wake event is never set. Each path also has its own redaction check instead of the B1 choke point, so a future change to the notice text can bypass central redaction. post_parts already does this job.

10. `src/heterodyne/admind/daemon.py:1022`: There is redundant per-request and per-row work. fulfillment.check runs three times per request (server check lambda, on_fulfillment, apply/propose) and require_uid twice. The Apply|Propose|Dispose|Get union is spelled out three times instead of using fulfillment.Request. !asks and reminders call protected() once per row, despite the design's 'no per-row N+1 reads'. commit() loads every capacity row just to count them, and binding() is rehashed up to three times per apply.

    Failure scenario: Each lifecycle request encodes and decodes the whole payload (up to 64KiB) three times on the event loop. !asks makes 20 extra queries and reminders make one per open ask, where a NOT EXISTS join or a COUNT(*) would do. The union is duplicated in daemon.py at the check, describe and on_ask_wire sites.
