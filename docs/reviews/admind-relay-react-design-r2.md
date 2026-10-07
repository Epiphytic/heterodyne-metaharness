Reviewed `ad9c70f` against round 1 and the implementation. The trust change in §3 is stated honestly. Three blocking issues remain.

1. **[BLOCKING] Changed, previously pinned beads still receive no updated card.** [Delta:46](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:46) explicitly requires the originator to repin or repost. That documents the limitation but does not meet the fixed operator requirement. [approvals.py:245](src/heterodyne/admind/approvals.py:245) rejects the replacement, and [approve-bead:616](<BTQ-LIVE>/bin/approve-bead:616) rejects its eventual decision.

   **Fix:** specify a validated, lock-protected operation that refreshes the persisted pin without recording a decision, preserving validation and `--expect-digest`. Amend the btq scope if necessary. Test a real pinned bead edited from D to D2 **without manual repinning**, requiring a fresh actionable card. Round 1 #2 remains unresolved; [delta:77](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:77) currently tests acceptance of the failure.

2. **[BLOCKING] Atomic refresh rejects its own active attempt.** [Delta:46](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:46) keeps the old ask `deciding` during preparation and requires the R22 same-bead checks. Those checks explicitly reject a `deciding` ask: [daemon.py:646](src/heterodyne/admind/daemon.py:646). Thus even a changed bead with a valid renewed pin cannot refresh as specified.

   **Fix:** define a refresh-specific exception for exactly the retiring `(ask_id, attempt)`, verified by R23’s compare-and-set in the settlement transaction. Continue rejecting every other `deciding` or `uncertain` ask. Test successful refresh while the old attempt remains `deciding`, plus rejection of another unresolved attempt.

3. **[BLOCKING] The restored legacy delivery gate contradicts the data model.** [Delta:52](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:52) still says `ask_details` is “no longer read by the approve check,” contradicting R8 at line 41 and its legacy tests. Following §5 would remove the safeguard at [daemon.py:859](src/heterodyne/admind/daemon.py:859).

   **Fix:** state that approval checks continue reading `ask_details` whenever `asks.truncated = 1`, for both reactions and replies. Round 1 #1 is only partially resolved.

4. **[NON-BLOCKING] Exercise rollback inside the new settlement transaction.** [Delta:77](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:77) covers a crash before the transaction and successful joint commitment.

   **Fix:** also inject failures after attempt closure and replacement insertion, before reply enqueue. Assert full rollback and successful recovery; test that a failed compare-and-set inserts no replacement or reply.

5. **[NON-BLOCKING] Remove contradictory operator-fixture wording.** [Delta:86](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:86) defines three operators, then retains “One is a btq approver; the other is not.”

   **Fix:** delete that trailing sentence.

Round 1 #3’s original recovery gap is addressed by atomic settlement, subject to finding 2. Findings #4–#7 are resolved at the design level: sanitized explicit environments and private locations, decoder/actor guard adaptation, recovery threading, canonical audit references, and two authorized race participants are specified.

Code confirms posting takes `ask_post_lock` and never `work_lock`; today `judge_message` reads `message.sender`, and `ReactionAdded` discards `event_id_hex`. R27 correctly requires changes to both. No live tests were run during this read-only review.

REVISE