**Verdict: REVISE.** I found 10 problems in 597a1d4..e8bf0b9 (the single commit e8bf0b9). Four of them break approved design §5 directly:
- an unrelated legacy ask can stop a source-owned bead from ever being withdrawn;
- a fulfilled ask anywhere in the bead's history blocks the durable withdrawal record (the tombstone);
- a withdrawal can leave decision attempts stuck in `deciding`, and stuck maintenance retries repeat forever;
- refusals sent over the real socket do not use the documented reply shape.

This was a read-only review. I ran no tests or probes and made no edits. Findings were deduplicated but not separately verified, so treat them as candidates, ranked most severe first.

10 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:814`: Only approval-kind AskPosts are refused for a source-owned bead. A question or merge AskPost that carries a bound `bead` is still inserted, and lineage() then rejects that non-approval row with `lineage-incomplete`, so status, withdraw and exact-retry post for the operation fail permanently.

   Failure scenario: Key K is enrolled for bead btq-abc12. Any same-UID client posts AskPost(kind='question', title=..., body=..., bead='btq-abc12'), which asks.check allows. lineage() hits row.kind != 'approval' (operations.py:173), so status, withdraw and post all return refused/lineage-incomplete. The tombstone can never be set, and open approval siblings can never be cancelled through approval.operation.v1. The same row inserted before enrollment makes enrollment impossible.

2. `src/heterodyne/admind/daemon.py:1562`: When the bead is withdrawn, refresh() returns early without closing the attempt or finishing the inbound message. The ask stays `deciding` with no reply, even though no native decision was issued (stale preflight or untouched readback).

   Failure scenario: An operator reacts, begin_attempt claims the ask, and the source then withdraws (reply: decision-in-flight). Preflight then sees a changed digest, so run_attempt calls refresh(), which hits the tombstone and returns. close_attempt never runs, the inbound message stays `executing` and the operator gets no reply. status reports `decision-in-flight` indefinitely, and approval_post_refusal treats the bead as busy until a daemon restart reconciles it. test_refresh_after_withdraw_does_not_emit_descendant asserts this stuck `deciding` state.

3. `src/heterodyne/admind/store.py:1164`: When the tombstone makes issue_decision return False, run_attempt treats it as an ordinary CAS conflict and leaves the ask `deciding` with its attempt set. Nothing was issued, but the row is never settled; the maintenance-retry path loops forever.

   Failure scenario: An ask is held in maintenance (deciding, pending-maintenance) and the source withdraws. Every MAINTENANCE_RETRY_SECONDS, retry_maintenance calls defer_maintenance, reads native back (untouched), runs run_attempt, and issue_decision returns False. It then posts CONFLICT via finish() and leaves the ask_maintenance row in place, so the cycle repeats with a native read each time and never resolves. In the ordinary path the ask also stays `deciding` and is reported as decision-in-flight although no decision was issued.

4. `src/heterodyne/admind/operations.py:358`: withdraw() raises `fulfillment-protected` and rolls back the whole transaction, including the tombstone, if any open or answered lineage row is fulfillment-protected. commit_approval and supersession skip such rows instead.

   Failure scenario: A bound bead has an answered ask with a confirmed fulfillment, plus an open refreshed sibling. Every withdraw returns refused/fulfillment-protected, so the tombstone is never written, the open sibling stays answerable and decidable, and the source cannot get the durable withdrawal the design requires.

5. `src/heterodyne/admind/operations.py:222`: before_commit raises Invalid (lineage-capacity at 256 rows, or lineage-incomplete) inside refresh()'s transaction. close_attempt is rolled back and the exception escapes run_attempt, leaving the attempt stuck and the inbound message unanswered.

   Failure scenario: A bound bead whose lineage has reached MAX_LINEAGE (or contains a stray question row) gets a digest change during a decision. In refresh(), commit_approval raises Invalid('lineage-capacity'), the transaction rolls back the stale close, and a ValueError propagates out of the decision worker. The ask stays `deciding` with no reply or audit, instead of closing as stale with a refusal notice.

6. `src/heterodyne/admind/ctl.py:186`: approval.operation.v1 requests that fail validation are answered with asks.refused (an AskReply with `message` and no `code`) instead of operations.Reply. The documented codes (identity, binding, bead-required, malformed) cannot be returned over the socket.

   Failure scenario: A post with a non-canonical UUID or a null bead goes through operations.decode, which raises Invalid (a ValueError), and the client receives {"result":"refused","message":"malformed request"}. A client decoding operations.Reply (forbid_unknown_fields, `code` required) gets a DecodeError and must treat a definite pre-effect refusal as unknown. The socket test only checks result=='refused', so this is not caught.

7. `src/heterodyne/admind/operations.py:307`: post() checks approval_post_refusal (latch, MAX_OPEN capacity, hourly rate, same-bead deciding) before the enrollment-only branch, which creates no card. Adopting existing history is refused whenever the store is full or a lineage row is deciding.

   Failure scenario: A legacy card for bead X is mid-decision (deciding), or the open-ask capacity is full. The source's first approval.operation.v1 post for X returns refused/post-gate, although enrollment would only map existing rows. The source cannot bind its operation, and therefore cannot withdraw it, until unrelated capacity frees up.

8. `src/heterodyne/admind/operations.py:340`: The operations post path never writes the daemon audit.jsonl `posted` record (audit_posted) that legacy post_approval writes, so the IDs of asks superseded by commit_approval go unaudited.

   Failure scenario: A source post supersedes an older open card for the bead. commit_approval returns the superseded IDs, post() discards them, and audit.jsonl shows neither the new ask nor the supersession. Only hashes in approval_operation_events remain, which breaks existing audit-based tracking of card creation and supersession.

9. `src/heterodyne/admind/operations.py:26`: A new bead regex is introduced instead of the existing approvals.BEAD_ID. The two grammars disagree in both directions, contrary to design §2's requirement to use existing closed bead grammars.

   Failure scenario: A bead like 'heterodyne-metaharness-ab12c' passes operations.check and reaches approve_bead.read, but legacy asks.check rejects it. A legacy-valid bead like 'abc-1.2' (suffix not 5 characters, or containing '.') can never be enrolled, so its existing history cannot be adopted or withdrawn.

10. `src/heterodyne/admind/operations.py:242`: view() makes 4+ queries per lineage row: store.ask for each row in lineage(), a link check, answers, attempts, and a three-way OR-GLOB outbox query. It runs repeatedly per request (existing via handle, enroll, view, and view twice in withdraw).

    Failure scenario: With a 256-row lineage and a large outbox, each status poll does about 1,500 queries, including 256 multi-GLOB outbox scans, all while holding the Store lock. A withdraw does this twice inside a BEGIN IMMEDIATE, stalling the sender and the decision worker. One joined query per table over the lineage ask IDs would do the same job.
