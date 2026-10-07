# admind: `!asks bump` (design delta)

Status: draft for cross-model review. It amends the relay design (`2026-10-05-admind-marmot-relay-design.md`, R1–R26) and the reply/reaction delta (`2026-10-06-admind-relay-replies-reactions-design.md`, R27–R31). Operator request, 2026-10-07, verbatim:

> What I'd like is for !asks bump to bump up the current asks by replying to them saying that an ask is still outstanding. That way I can easily find the previous ask, even if the client I'm using isn't fully threaded. Each bump must reply to the original ask message, not to a bump, in the case of multiple bumps.

## 1. Requirements

| # | Requirement | Rationale |
|---|---|---|
| B1 | **`!asks bump`** is a new form of `!asks`. It is sent from anywhere in the group, as a reply or not. It runs in the worker under `work_lock`, after `!asks`'s backstop reconcile (spec §8) and the same authorisation check as `!asks`. `!asks` with no argument is unchanged. Any other argument is refused with "Usage: !asks [bump]." | One verb the operator already knows. |
| B2 | **What is bumped:** every ask whose status is `open` or `answered` (question/merge asks that already have an answer stay outstanding until their originator collects them), oldest first. Asks that are `deciding` or `uncertain` are not bumped; they are named in the summary (B5) with their status. | Only asks the operator can act on now. A `deciding` ask has a decision in flight. |
| B3 | **Each bump is threaded to the ask's original card**: the first sent chunk of its card (`store.first_card_message`), never a `!details` chunk, a previous bump, or any other notice. An ask whose card has no sent chunk is not bumped and is named in the summary as "card not delivered". | The operator's exact requirement: repeated bumps all point at the original, so a client without threading shows the quoted original each time. |
| B4 | **Bump text**, one message per ask, quoting enough to recognise it without threading: `Still outstanding: ask <id> · <kind> · <age> · <title, first 80 characters>.` followed by `React 👍 or 👎 on the card above, or reply to it with approve or deny <reason>.` for approval asks, and `Reply to the card above to answer.` for question and merge asks. The text goes through `redact()` and the chunk split like every other notice. | The bump must make the original findable; it is not the card. |
| B5 | **The command's own reply**, threaded to the `!asks bump` message: `Bumped <n> asks: <id>, <id>, ….` plus one line per skipped ask (`<id> is deciding`, `<id> is uncertain`, `<id>: card not delivered`), or "No outstanding asks." when there are none. | The operator sees what happened. |
| B6 | **A bump is not a card.** It is posted as an ask notice (`asknote:<ask>:bump:<command mid>:<i>`), so `ask_for_message` never resolves it (R7 is unchanged): a reply or reaction on a bump never decides or answers anything. A reply or a decision/approve/deny emoji reaction on a bump gets one threaded hint: `That was a reminder. React or reply on ask <id>'s card (the message the reminder replies to).`, and nothing reaches the admin agent. Other reactions on a bump are ignored silently (as on any non-card). | Deciding needs the delivered card (R8); a bump shows only the title. Without the hint, a reply on a bump would be pasted to the admin agent as chat. |
| B7 | **Idempotence and replay:** the bump keys include the command's message ID, so a replayed `!asks bump` (same inbound row) queues nothing new; a new `!asks bump` bumps again, again threaded to each original card. All bump posts, the summary reply and the inbound row's `done` status are written in one transaction. | Same guarantees as every other command. |
| B8 | **Limits:** at most `MAX_OPEN` asks can be active, so a bump posts at most `MAX_OPEN` messages plus the summary. There is no rate limit beyond the operator sending the command. | Bounded by an existing cap. |
| B9 | **Audit:** one `command` record `{command: "asks", sub: "bump", bumped: [<ids>], skipped: [{ask_id, why}]}`, plus the normal `sent` records. | Untruncated audit (r13). |
| B10 | **HELP** shows `!asks [bump]`. `docs/admind.md` §4 command table and §10 describe it. | |

## 2. Tests

### 2.1 Offline (`tests/test_admind_asks_bump.py`)
- B1: `!asks bump` parses; `!asks` unchanged; `!asks foo`, `!asks bump extra` refused with the usage; unauthorised sender refused like `!asks`; backstop reconcile runs first.
- B2/B3: with open, answered, deciding, uncertain and decided asks: only open and answered are bumped, oldest first; each bump's `reply_to` is exactly that ask's first card chunk, **not** a details chunk; a card of several chunks threads to chunk 0; a second and a third `!asks bump` again thread to chunk 0, never to an earlier bump (assert the exact `reply_to`); an ask whose card never sent is skipped and reported.
- B4: wording pinned for approval, question and merge; title cut at 80; redaction applies (a secret-looking title is redacted).
- B5: summary wording for 0, 1 and several asks, with skipped lines; threaded to the command.
- B6: a reply "approve", a 👍 and a 👎 on a bump: nothing decided, no answer stored, no subprocess, no paste to the agent, one hint threaded to the bump; a 🎉 on a bump: nothing at all; `ask_for_message(bump id)` is None.
- B7: replay of the same inbound row queues nothing new; an injected failure inside the transaction leaves no bump, no summary and the inbound row not `done`.
- B9: audit record fields.

### 2.2 Live (`tests/live/`, `HZ_LIVE=1`)
- Post an approval ask (multi-chunk card) and a question ask; tester sends `!asks bump` twice. Both rounds' bumps arrive at tester, tester2 and outsider threaded to each ask's first card chunk (assert `reply_to` equals the card's first chunk message ID, both rounds). tester2's 👍 on a bump decides nothing and gets the hint; tester's 👍 on the card then approves.
