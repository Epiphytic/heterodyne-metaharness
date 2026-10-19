I found no critical flaw. The diff looks good enough to build as an offline PoC, as long as the queue child is still required to check the complete history and the binding itself. I read the code and tests only and ran nothing.

**R3 fixes hold up in the code:**
- **F1:** the card legend now matches the parser's real word and emoji lists in `verbs.py`.
- **F2:** the "a later A cannot override" promise is gone, and a confirmation for a management card now adds "Recorded answer is not authorization".
- **F3:** `--version 2` now refuses a successful V1 envelope.
- **F6:** `!details` is packed once, then gets the same final 36-line / 3200-character and redaction check as the card.

**Other paths checked, no defect found:**
- Nested `transaction()` uses an RLock and rejoins the outer transaction, so the new nesting in `insert_ask` cannot deadlock.
- Every `_ask()` caller now reads the extended column list.
- A `management.Refusal` raised during binding becomes `invalid-state`.
- The default V1 reader refuses a V2 envelope.

The findings below are new non-critical issues plus R3 debt that is still open (R3 F4/F5/F7–F10). Finding 1 is the only new consent-semantics issue; I would fix it before any live use, but it does not block an offline PoC.

10 findings, most severe first:

1. `src/heterodyne/admind/management.py:23`: The reserved-prefix filter only blocks single-letter legend lines ('A =', 'B /'), so a producer's body can contain lines shaped like the legend that redefine accepted words, e.g. 'ok = leave held' or 'yes / y = B'.

   Failure scenario: A body line 'yes / ok = leave unchanged and held' passes literal() because [A-Z]\s*(?:=|/) requires '=' or '/' straight after one letter. The card shows it above the real legend; the operator replies 'ok' meaning hold, read_reply records choice A, and the history looks like a clean approval to the queue.

2. `src/heterodyne/admind/management.py:153`: The !details grammar lines repeat content already on the card (a second 'Denial words:' line, approval words, reactions) and add 4 lines that count against the same 36-line limit.

   Failure scenario: A valid card of about 33 or more lines posts fine, but !details then always refuses with 'management card refused'. The operator cannot get details for a legitimate card, and the lines it would add are mostly already on the card.

3. `src/heterodyne/admind/daemon.py:1372`: R3 F4 is still open: once a malformed v2 row becomes due, every autobump tick skips it without advancing last_activity_at and writes another skip/audit record, with no end.

   Failure scenario: A tampered open v2 row is past ask_bump_hours. Each autobump_seconds tick appends ('id', 'management card refused') and an audit entry until someone cancels the row, so the audit log grows without bound.

4. `src/heterodyne/admind/daemon.py:1257`: R3 F10 is still open: answer_refusal runs management.stored() before the fulfilled and status checks, so a malformed v2 row hides its real lifecycle state.

   Failure scenario: A reply to a cancelled or fulfilled malformed v2 ask returns the generic 'management card refused' instead of 'Already fulfilled; original answer retained.' or the not-open wording.

5. `src/heterodyne/admind/management.py:29`: R3 F5 is still open: the unanchored scheme match in _URL (data:, tel:), the blanket '_' and '~' refusal in _MARKDOWN, and the 'Ask '/'Reply '/'x/' prefixes in _RESERVED refuse ordinary task facts.

   Failure scenario: 'Before: metadata: pending', a worktree path '~/.hermes/x', a file name 'test_admind_r2.py' or 'Ask owner to merge' all refuse the whole post, so producers struggle to state real before/after or holder facts.

6. `src/heterodyne/admind/asks.py:151`: R3 F7 is still open: the request-side management_check and the row-side stored()+lines() are two hand-written validators that check different fields.

   Failure scenario: management_check skips digest/refreshed_from and stored() skips the title/body literal checks. A future edit to one side lets a request post and then refuse at render or binding time, or the reverse.

7. `src/heterodyne/admind/asks.py:450`: R3 F8 is still open: v2 routing is a separate management.present() branch in each shared renderer/mapper (question_card, card_chunks, full_text, full_chunks, checked_card_chunks, map_reaction, map_reply, recorded), not one dispatch chosen per row.

   Failure scenario: A new shared entry point (for example a summary or reminder variant that renders the body) that lacks the branch silently applies legacy semantics or rendering to v2 rows.

8. `src/heterodyne/admind/daemon.py:1242`: R3 F9 is still open: each answer decodes and validates stored() three times (answer_refusal, map_reply or map_reaction, recorded), each post runs management_check twice, and insert_ask still does an INSERT followed by an UPDATE.

   Failure scenario: Every v2 reply decodes the management JSON and choices three times and every autobump tick decodes each due row again. The cost is wasted CPU and harder upkeep, not wrong results.

9. `src/heterodyne/admind/store.py:877`: The doc says 'The original fixed-column legacy writer remains unchanged', but insert_ask is now wrapped in its own transaction and conditionally runs an UPDATE.

   Failure scenario: A reviewer or rollback plan that trusts the doc's compatibility claim misses that legacy approval inserts (daemon.py:1147) now go through the changed writer; the doc and the code disagree.

10. `src/heterodyne/admind/asks.py:281`: describe() adds presentation/render_version to the audit only when presentation is not None, so a partial opt-in (render_version or management without presentation) leaves no metadata in the request audit.

    Failure scenario: A producer sends render_version=2 and management but forgets presentation. check() refuses it, but the request audit record shows nothing about the attempted v2 opt-in, so the misconfiguration is hard to diagnose.

There is still no live, queue, enrollment or deployment proof. The queue child (complete-history, current-policy, literal-A checks) is what actually prevents a recorded A from authorizing a change; that hold must stay in place.
