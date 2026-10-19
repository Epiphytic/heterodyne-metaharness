**Verdict:** I found no critical flaw that blocks a PoC. The R1/R2 core claims I checked hold up in the code:
- **Budget before insert:** the 36-line / 3200-character limit is checked on the real framed chunks before anything is stored.
- **Atomic post:** the metadata and the outbox insert commit in one transaction, and the stored row is read back and compared exactly.
- **Closed metadata:** partial or malformed metadata refuses. A malformed `render_version` becomes the sentinel -1, so bulk reads keep working.
- **V1 unchanged:** V1 bytes and decoding are unchanged, and the default V1 CLI refuses (invalid-reply) on a v2 envelope instead of misreading it.
- **Approval authority:** v2 cards stay `question` kind and never reach the approval decide path.
- **R2 fixes:** the printable-ASCII, URL and entity refusals and the legend split between B and none are present as claimed.

The most serious remaining problem is a consent-semantics mismatch: the card's wording does not match what the daemon actually records (findings 1 and 2). It needs fixing, or the queue child must explicitly compensate, before any live use. I did not run tests or probes. This was a read-only inspection of the diff, the enclosing functions and the R1/R2 reports.

10 findings, most severe first:

1. `src/heterodyne/admind/management.py:131`: The card legend names only 'A / approved / 👍 ✅ ❤ ♥' as approve and says 'Any other reply ... holds this request', but map_reply's v2 path calls verbs.read_reply, whose APPROVE_WORDS also include yes, y, ok, okay, lgtm and approve; all of these record choice A.

   Failure scenario: The operator replies 'ok' or 'yes' meaning 'seen'. The card says any other reply holds, but answer_ask stores choice 'A' and confirms 'Recorded for X: A = Perform this exact change (from ok)'. The doc tells the queue child to accept a 'literal-A/accepted-positive history', so the change is performed. These words are disclosed only in !details, not on the card the operator consented on.

2. `src/heterodyne/admind/asks.py:528`: The card states 'a later A cannot override it', but admind enforces nothing: answer_refusal still accepts answers on 'answered' asks, map_reply records a later A, and the confirmation says A was recorded without warning that the request stays held.

   Failure scenario: The operator replies B, then A. Both answers are stored, the second as choice 'A' with the reply 'Recorded for X: A = Perform this exact change (from A).' The operator is told A took effect while the card said it cannot. Any consumer that reads `ask get`/`ask wait` answers and takes the latest choice (the queue child is not implemented yet) would perform the change. Only an unwritten downstream policy upholds the promise.

3. `src/heterodyne/admind/cli.py:357`: With --version 2, a successful reply carrying a V1 envelope is printed as 'ask.binding.v1 complete' with exit 0; the requested version is enforced only on refusals, never on success.

   Failure scenario: The queue child runs `admind ask binding --json --version 2 <id>` on a legacy question ask (wrong ID, or a lookalike legacy card) and gets exit 0 with result ok. The only signal is the envelope's version 1 and missing presentation, so it relies entirely on each consumer re-checking (the doc says 'consumers must compare'). Refusing a success whose version differs from the requested one under --version 2 would close this.

4. `src/heterodyne/admind/daemon.py:1372`: The R2 F4 fix only defers the problem: once a malformed v2 row becomes due, every autobump tick skips it without touching last_activity_at and writes an 'autobump' audit record, indefinitely.

   Failure scenario: A tampered open v2 row passes ask_bump_hours. From then on, each autobump_seconds tick appends ('id', 'management card refused') and writes an audit record, forever, until someone cancels the row. The audit log grows without bound and real bump records get buried.

5. `src/heterodyne/admind/management.py:29`: The fail-closed literal filters are too broad: _URL matches 'data:' and 'tel:' as unanchored substrings, _MARKDOWN refuses any '_' or '~', and _RESERVED refuses any line starting with 'Ask ', 'Reply ' or a single letter followed by '/' or '='.

   Failure scenario: Ordinary management facts are refused outright: 'Before: metadata: pending', 'Hotel:', a worktree path '~/.hermes/...', a file such as 'test_admind_r2.py', 'Ask owner to merge', or 'x/y holder'. This fails safe but makes it hard for producers to state real before/after or holder facts for a PoC.

6. `src/heterodyne/admind/management.py:160`: The details=True path packs the extra grammar lines and returns them without the line/char budget or redaction re-check that the card path applies.

   Failure scenario: !details on a card near the 36-line / 3200-character limit sends a longer, unchecked message. The grammar is static today, so the risk is low, but any future edit to these lines skips every guard the main card path has.

7. `src/heterodyne/admind/asks.py:151`: R2 F8 is still open and unwaived: request-side management_check and row-side stored()+lines() are parallel hand-written validators that check different fields.

   Failure scenario: management_check does not check digest/refreshed_from, and stored() does not check title/body literals. A change to one side lets requests post that then refuse at render or binding time, or the reverse.

8. `src/heterodyne/admind/asks.py:450`: R2 F9 is still open and unwaived: v2 is wired in through separate management.present() branches in seven shared renderers and mappers, plus per-caller Refusal handling, instead of one dispatch chosen per row.

   Failure scenario: A new shared card or summary entry point that lacks the branch silently applies legacy semantics to v2 rows.

9. `src/heterodyne/admind/management.py:155`: R1 F10 / R2 F10 are still open and unwaived: redact() runs repeatedly, stored() decodes repeatedly, management_check runs twice, and insert_ask still does INSERT followed by UPDATE.

   Failure scenario: One v2 post runs management_check twice, stored() three times and redact() over the full card four or more times. Each answer decodes stored() twice, and every autobump tick re-decodes each v2 row. The cost is wasted CPU and harder maintenance, not wrong results.

10. `src/heterodyne/admind/daemon.py:1257`: answer_refusal runs the management stored() check before the fulfilled and status checks, so a malformed v2 row hides its real state.

    Failure scenario: A reply to a cancelled or fulfilled malformed v2 ask returns the generic 'management card refused' instead of 'Already fulfilled' or the not-open wording, which hides the ask's real lifecycle from the operator.

**For the PoC:** it is suitable as an offline PoC if findings 1 and 2 are fixed. Either make the legend list the real accepted approval words and soften or remove the "later A cannot override" claim, or have the queue-child contract explicitly require literal A only and first-answer-wins. I'd also make `--version 2` refuse a V1 success (finding 3). R2 F8–F10 and R1 F10 remain open debt as documented. There is no live or deployment proof.
