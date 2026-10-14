# cxmkf Amendment 1: review r1 of the implementation (PR #92)

Amends `docs/superpowers/specs/2026-10-13-cxmkf-admind-card-kinds-design.md` (663a9ef). Liam decided items 1
and 2 on 2026-10-14 (ask a5x9, 👍 = 1a + 2a). Item 3 stays within K23 and needs no decision. Each item gives
the text it replaces and the new text. Everything not named here is unchanged.

## 1. A reaction on a yes/no card (r1 B2; amends K17q, §1.2, K16)

With `--yes-no --recommended no`, K17q mapped both 👍 (the recommended choice, `no`) and 👎 (`no`) to `no`,
so a reaction could never record `yes`.

**K17q**, was: "an emoji from the deny set (👎 in every skin tone, ❌) maps to `no` on a yes/no card and to
`none` ("none of these") on a multiple-choice card (Q3)."

**K17q**, now: "an emoji from the deny set (👎 in every skin tone, ❌) maps to the option that is not
recommended on a yes/no card (`no` when `yes` is recommended, `yes` when `no` is), and to `none` ("none of
these") on a multiple-choice card (Q3)." Rationale: 👍 takes the recommendation, 👎 rejects it.

**§1.2 / K16 answer line.** The yes/no answer line names what 👎 records:
`Answer: 👍 = yes (recommended) · 👎 = no · …` (unchanged), and with `--recommended no`:
`Answer: 👍 = no (recommended) · 👎 = yes · or reply with your own answer (!answer p3qz <text>)`.
The footer stays `Choices: yes · no (recommended)`.

K18q (replies) is unchanged: a `yes`/`y` reply is `yes` and a `no`/`n` reply is `no`, whichever is recommended.

## 2. Part counts and a failed continuation (r1 B1; amends K23, adds K22f)

A multi-part card or notice showed no part count on part 1, and a continuation that failed for good was
only audited. The operator could read part 1 (say, "Deploy complete") without knowing more was missing.

**K23**, was: "Each continuation chunk starts with `↳ part <i>/<n>` and a newline; chunk 0 has no marker."

**K23**, now: "Each continuation chunk starts with `↳ part <i>/<n>` and a newline. Chunk 0 of a post with
more than one part ends with a newline and `↳ part 1/<n>`. The label stays chunk 0's first line (K11). Both
markers fit in the `chunk_chars − usable` room (16 characters), so a marked chunk never exceeds `chunk_chars`.
A post of one part has no marker."

**K22f (new). A continuation that fails is reported.** When a continuation part (part `k` ≥ 2 of an ask card
`ask:<id>:…`, a repeat `askr:<id>:<mid>:…` or a notice `notice:<id>:…`) ends `failed` after its retries,
admind queues one fixed reply in lane 1, key `partfail:<the failed part's key>` (so it is queued once):

`part <k>/<n> failed to deliver, ask the poster to repeat`

It is threaded to part 1's message when part 1 was sent with a message ID, and otherwise posted top-level. It
carries no text from the post. The failure is audited as today (`send` `failed`), plus one
`part_failed {key, parent}` record. The reply is an ordinary admind message: it is not a card, so a reaction
or reply on it decides or answers nothing. A failed part 1 is not reported this way: the card is undelivered
(K22d) and `!asks repeat` is the remedy, as today. `!details` parts are replies to the command, not
continuations, and are not reported. Delivery checks (K22d) are unchanged.

## 3. A repeat or `!details` after `chunk_chars` is lowered (r1 B3; within K23)

K23: "So no card, question, notice or approval, is ever sent with a cut URL." A question or merge ask is
re-packed at the current `chunk_chars` when it is repeated or sent in full. If the setting was lowered after
the post so that a link line no longer fits, admind no longer falls back to a plain split, which could cut
the URL:

- `!asks repeat` skips that ask, and its reply says so:
  `<id>: a link is longer than this group's message size now allows; raise chunk_chars to repeat it`.
- `!details` on it is refused: `Ask <id> has a link longer than this group's message size now allows (<n>
  characters, <usable> usable at chunk_chars <c>), so it can't be sent in full; raise chunk_chars. Nothing
  was sent.` This is audited as `ask` `details-refused` with reason `link too long`.

Nothing is queued in either case. Approval asks are unaffected, because their checked chunks are stored when
they are posted (R21).
