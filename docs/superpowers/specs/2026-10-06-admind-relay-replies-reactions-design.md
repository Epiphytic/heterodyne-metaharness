# admind relay: decide by reply or reaction (design delta)

Status: draft for cross-model review. It amends the [relay spec](2026-10-05-admind-marmot-relay-design.md) (R1–R26), as merged in PR #18 (0711e61). Anything not named here is unchanged.

## 1. Why

The operator's verdict after the first live test, 2026-10-06, verbatim:

> the whole point of replying to a message is that the user shoudn't then also need to tell you what they're approving: it's implicit. And I didn't notice any thumbs up, thumbs down, heart react options here at all. Users shouldn't need to quote a digest id either: the message that they're replying to contains it, so if that message they're replying to contains a different digest id, then you would send them the updated one. Fix it, retest using a temporary admin user, then ask me to test out the thumbs up/thumbs down react and single replies on both laptop and phone, nothing more. I expect that you will have tested the rest of the edge cases yourself, and that the test suite will be inspectable in the codebase.

The live test also showed the failure the typed form invites: a `!deny` naming a different bead was sent as a reply to the card for another bead, and was refused ("no match").

## 2. What changes for the operator

- **An approval card is decided by reacting to it or by a one-word reply.** 👍 or ✅ (or ❤️) approves, 👎 or ❌ denies. A reply of `approve` (or `yes`, `ok`, `lgtm`) approves, and `deny`, optionally followed by a reason, denies. Nothing has to be typed about which bead or which digest: the card is the reference.
- **A changed bead gets a fresh card.** If the bead changed after its card was posted, the decision records nothing, and admind posts a fresh card for the bead's current content, which you then decide.
- **Approval cards are never shortened.** The whole readout is posted, in as many parts as it needs. `!details` is no longer a step before approving.
- **Questions and merge requests can be answered with a reaction too.** The emoji is recorded as the answer.
- `!approve` and `!deny` still work as replies to the card, without arguments. If you give a bead or a digest, they must be the card's.

## 3. The trust change

Revision 1 of the relay used the typed 12-hex digest "in place of the terminal's `[y/N]` confirmation" (relay spec §3). This delta replaces it with **an explicit approve action on admind's own card**: an approve reaction, or a reply that is exactly one approve word. The digest pin itself is unchanged: admind still stores the full digest when it renders the card, and passes it to `approve-bead --expect-digest`. So an approval still binds to exactly the content on the card it answers, and a changed bead is still refused under the per-bead lock (R6).

What the typed digest added beyond the pin was friction: proof that the operator looked at this card's digest, and protection against a mis-tap. What remains:

- a free-form reply never decides: only a reply that is exactly an approve word, or begins with a deny word (R28);
- a reaction other than the listed ones decides nothing;
- every decision is confirmed in the thread as "Approved `<bead>` as `<name>` …", and audited;
- a decision is final: removing the reaction does not undo it (R27).

**Accepted risk:** an accidental 👍 on an approval card records an approval. The operator chose this on 2026-10-06 (§1). Everything else in relay spec §3 still holds: the sender is MLS-authenticated, must be an operator whose name is a btq approver, the card must be admind's own, and the bead must still be open, undecided and unchanged.

## 4. Decisions

Revised decisions keep their numbers. New ones follow R26.

| # | Decision | Why |
|---|---|---|
| R7 (revised) | **A decision is a reply or a reaction to an approval card**: to any chunk of the card, or any chunk of that ask's `!details`. The bead and the digest are the card's: `asks.bead` and the stored full `asks.digest`. `!approve` and `!deny` as replies take no arguments. If arguments are given, the first must equal the card's bead and, for `!approve`, the second, when present, its 12-hex digest; otherwise the command is refused with "This card is for `<bead>`. React 👍 to approve or 👎 to deny, or reply approve / deny `<reason>`. Nothing recorded." `!approve`/`!deny` sent unthreaded, or as a reply to anything that is not a card, are refused with "To decide, react to the approval card or reply to it. Nothing recorded." | The card already pins bead and digest. An argument that disagrees with the card shows the operator meant something else, so it is refused, not overridden (§1's live failure). |
| R8 (revised) | **The whole context must have been delivered**, and an approval card is always delivered whole. An approval card is never shortened: its whole readout, digest line and decision lines are posted as the card, split into `chunk_chars` chunks. An approve, by any route, is refused until every chunk of the card is `sent` ("Ask `<id>` has not been fully delivered yet. Wait for every part, then react or reply again. Nothing recorded."). The `!details`-before-approve rule is gone. A readout longer than `MAX_APPROVAL_CARD` (24,000 characters, after the R21 check) is refused at post time with "this bead is too long to decide from the phone; decide it at the terminal". Deny needs no delivery check. | The guarantee is the same as before: every hashed line reached the approver before an approval counts. Posting it whole removes the step the operator found fiddly. The cap keeps a card readable and the outbox bounded. |
| R13 (revised) | A plain reply to a `question` or `merge` card is its answer. A reply to an `approval` card is read by R28: an approve or a deny, or otherwise a **note** the poster sees, answered with "Noted on ask `<id>`; this is not a decision. React 👍 to approve or 👎 to deny, or reply approve / deny `<reason>`." | The reply itself is now the decision when it says so unambiguously. |
| R15 (revised) | The 40-line and 3,500-character card budget applies to `question` and `merge` cards only. Approval cards are bounded by `MAX_APPROVAL_CARD` (R8). | Approval cards are never shortened. |
| R27 (new) | **Reactions.** A `reaction_added` event is handled like an inbound message, by the same guard and in the same worker under `work_lock`. It is ignored if `actor.is_self`. It is dropped and audited, as `guard.judge_message` would, if it comes from another group, from a sender who is not an operator, or while admind is latched. Its `event_id_hex` (64 hex) is claimed in `inbound` under the key `r:<event_id_hex>`, so a replayed reaction is dropped. A missing or malformed event ID drops it. The target is `target_message_id_hex`. **On an approval card** (a sent chunk of the card or of its `!details`): an approve emoji approves, a deny emoji denies with no reason, any other emoji is ignored (audited `ask/reaction-ignored`, no reply). **On a question or merge card**, any emoji is stored as an answer whose text is the emoji. **On anything else** the reaction is ignored, as before. The reply to a reaction is threaded to the reacted message, never posted top level. `reaction_removed` is ignored: a recorded decision is final, and an answer stays. Approve emojis are 👍 (with any skin-tone modifier), ✅, ❤️ (with or without U+FE0F) and ♥️. Deny emojis are 👎 (any skin tone) and ❌. A reaction is never pasted to the admin agent. | A reaction is an MLS application message from an authenticated member, like a reply, so the same ingress rules apply. wn-agent gives each reaction its own event ID, which makes replay detection exact. Ignoring other emojis avoids decisions by accident. |
| R28 (new) | **Reading a reply on an approval card.** The text is trimmed, Unicode-casefolded, and stripped of trailing `.`, `!` and whitespace. It is an **approve** when the result is exactly one of `approve`, `approved`, `yes`, `y`, `ok`, `okay`, `lgtm`, or exactly one approve emoji (R27). It is a **deny** when its first word, compared the same way, is `deny`, `denied`, `reject`, `rejected`, or a deny emoji, or the whole result is exactly `no` or `n`. The rest of the text after the first word, trimmed, is the deny reason (R19), and may be empty. `!approve` and `!deny` with valid arguments (R7) are the same. Anything else is a note (R13). So "yes, but what about X?" is a note, and "no idea" is a note. | Only an unambiguous reply decides. Approving needs the whole reply to be the word, so that no sentence starting with "ok" or "yes" approves. A deny is less dangerous (it changes nothing that an approval would gate), so a deny word may carry a reason. |
| R29 (new) | **A stale card gets a fresh one.** When the decision read (step 5) finds the bead's digest differs from the card's, the ask goes to `stale` as before, with nothing recorded. Then, still in the worker, admind posts a fresh approval ask for the bead by the normal post path (`--from` copied from the stale ask; R4, R9, R15, R17, R21, R22 all apply). The reply in the stale card's thread says: "`<bead>` changed after this card was posted (shown `<a>`, now `<b>`). Nothing recorded. A fresh card follows: ask `<new>`." If the fresh post is refused (the bead closed or decided, gaps, redaction, limits, latch), the reply says "Nothing recorded, and admind could not post a fresh card: `<reason>`. The poster must post it again." The decision is not carried over: the operator decides the fresh card. Lock order: the worker holds `work_lock` and then takes `ask_post_lock`. The socket's post path takes `ask_post_lock` and never `work_lock`. | The operator asked for exactly this (§1). Not carrying the decision over means only content the operator saw is ever approved. Reusing the post path keeps every post-time check. |
| R30 (new) | **Card wording.** The approval card is headed `🛂 Approval ask <id> · bead <bead> · posted by <label> (a local process; unverified)`, then `👍 approve · 👎 deny — react to any part of this card, or reply approve / deny <reason>`, then `digest <digest12>`, then the readout, and it ends with "Approving records your approval of `<bead>` in btq, as you, via Marmot. Any other reply is a note for the poster." Question cards end with "Answer by replying or reacting to this message, or send !answer `<id>` `<text>`". Merge cards end with "Merging is yours to do in GitHub; admind never merges. React 👍 or reply when it is merged, or reply with what to change." | The available actions are on the card itself. The digest line stays, so a fresh card visibly differs from a stale one. |
| R31 (new) | **Two operators.** The first decision on an open ask wins (begin_attempt's compare-and-set, R22, R23). A later reaction or reply on that card is answered in the thread with the existing "Ask `<id>` is already approved by `<name>`. Nothing recorded." A reaction on a stale or superseded card says which newer ask to use, when there is one. | Both of the operator's devices are approvers. |

## 5. Data model

No new tables. `inbound.message_id` takes the `r:<64 hex>` form for reactions. The `asks.truncated` column stays, and is always 0 for new approval asks. `ask_details` is still written by `!details`, and is no longer read by the approve check. A reaction's reply target is the reacted message ID, stored with the inbound row (the column choice is the implementer's), so `finish()` threads to it.

## 6. What must not break

- Every rule in relay spec §11.
- `approve-bead` and btq are unchanged: this delta changes only admind.
- A reply to any message that is not a card still goes to the admin agent, as before.
- Recovery (R26), reconcile and the `!asks` backstop cover attempts started by a reaction exactly as by a reply.

## 7. Testing

### 7.1 Offline (the normal suite, in the repo)

Every R27–R31 rule and the revised R7, R8 and R13 get unit tests at the daemon level, with the existing fakes, under `tests/test_admind_reactions.py` and `tests/test_admind_approvals.py`. At least:

- every approve word, emoji and skin tone; each deny word with and without a reason; near-misses that must be notes ("yes but", "ok?" followed by text, "no idea", "approve it later");
- `!approve`/`!deny` with no arguments, matching arguments and mismatching arguments;
- reactions: an approve, a deny, an ignored emoji, a reaction from a non-operator, from another group, while latched, a replayed event ID, a missing event ID, `is_self`, on a `!details` chunk, on a non-card, on a question card (answer stored), on a merge card;
- a decision before the card is fully delivered, by reply and by reaction;
- stale: a fresh card is posted, the reply names it, and the fresh card's refusal paths (closed bead, gaps, redaction, limits, latch);
- two operators: the first wins and the second is told;
- not a btq approver, by reaction;
- a long readout posted whole, and one over `MAX_APPROVAL_CARD` refused;
- recovery after a restart mid-decision started by a reaction;
- the card wording (R30), pinned.

### 7.2 Live (opt-in, in the repo)

`tests/live/` holds an end-to-end suite that is skipped unless `HZ_LIVE=1`. It never touches the production admind, its Marmot home, the production beads database or btq policy. It builds:

- **an isolated admind**: the real `Admind` daemon with its real `WnAgent` child in a temporary home, on the configured relays, with a stub admin agent (no tmux, no Claude). Its config and state directories are temporary;
- **two temporary operators**, each a throwaway identity in its own temporary home (a private `wn-agent` driven through `ControlClient`, or the `wn` CLI with a private `wnd`, as in spike S4), joined to the isolated admind's group. One is a btq approver; the other is not;
- **an isolated btq**: a temporary `BTQ_CONFIG_DIR` whose `policy.json` names only the test approver, and a throwaway beads database (for example a private `dolt sql-server` on a loopback port with a temporary data directory). The isolated admind's `approve_bead` runs with that environment.

It runs every edge case in §7.1 that needs the real transport or real btq, plus relay plan §4.5's list: the question reply and reaction, multi-line `!answer`, the merge card, `!asks`, `cancel`, approve by reaction, approve by reply, deny by reaction and by reply with a reason, the stale fresh-card flow, the non-approver, the redaction refusal, two operators deciding the same card, and the audit containing no npub or token. Teardown kills every process it started by PID and deletes the temporary homes. The run's results are written to `docs/reviews/` as a dated report.

### 7.3 The operator's check

After deployment, the operator does only what §1 asks: on each device, a 👍 reaction, a 👎 reaction and a single-word reply.

## 8. Relationship to revision 14

This delta replaces the relay spec's "typed digest" confirmation. The r14 editor carries R7, R8, R13 and R27–R31 into ADR §8, in place of the relay spec §10 text they supersede.
