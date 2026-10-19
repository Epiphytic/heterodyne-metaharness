I reviewed the full b4b8c75..72dd8f0 diff (source, the two new test files, the DTO doc, the R1 report and both dispositions files). I only read files and ran git: no edits, tests, probes, or queue, native, service, DB or network operations, and I make no activation or deployment claim. I did not treat the queue child as current product authority.

**There are no critical findings.** These parts hold up:
- **Refuse-before-insert:** the 36-line / 3200-character budget is checked on the real framed chunks, which include the actual ask ID and the continuation markers.
- **Atomicity:** the nested transaction joins the post transaction, and the persisted row is read back before the outbox insert.
- **v1 compatibility:** v1 `RenderInputs`/`Envelope` bytes are unchanged, and the v1 decoder stays closed against v2 replies.
- **Malformed rows:** a malformed `render_version` is isolated as -1, so bulk reads keep working.
- **No native approval authority:** the kind stays `question`, and the approval read path is never reached for these cards.
- **Original tests:** the only change to the original tests since the first head is the two-title swap, matching the claim.

The main remaining weakness is the R1 #2 and #6 repairs. They are fixed-prefix and single-code-point blocklists, so close variants still pass.

10 findings, most severe first:

1. `src/heterodyne/admind/management.py:24`: The _RESERVED anti-spoof screen only matches exact ASCII prefixes, so lines that look just like the renderer's own lines still pass literal() and appear on the readable APPROVAL card.

   Failure scenario: Each of these body lines passes asks.check and post_ask: 'Option A = Leave unchanged and held (recommended)' (the line starts with 'Option', so the bare-letter '[A-Z]\s*=' rule does not fire); 'A ＝ Leave unchanged and held' (fullwidth U+FF1D equals sign); '👍 = leave held; 👎 = perform'; 'A / no / 👎 = leave held' (the legend's 'A /' form is not reserved); 'Audit\u00a0details: https://github.com/attacker/r/blob/<40hex>/x' or 'Audit  details: ...' (NBSP or a double space, because the regex requires exactly one ASCII space); and a Cyrillic 'АPPROVAL:'. The operator sees fake option, reaction-legend or audit-link lines above the real ones and may react 👍 or reply A believing it means 'leave held'. The reserved list is also hand-copied rather than derived from the strings lines() emits, so it has already drifted (the 'A /' legend line is missing).

2. `src/heterodyne/admind/management.py:55`: The R1 #6 repair blocks only the single code point U+0336. Other overlay and enclosing combining marks, and HTML character references, still change how literal text is displayed.

   Failure scenario: 'After: o\u0335p\u0335e\u0335n\u0335' (U+0335 short stroke overlay), U+0338 long solidus overlay, U+0332 combining low line, or 'A\u20e0' (U+20E0 enclosing circle backslash, category Me) all pass literal() and redact(). '&#65; = Leave unchanged and held' also passes: '&' is allowed, '#' is not at the start of the line, and the line does not begin with a letter followed by '='. A Markdown/HTML-rendering client shows it as 'A = Leave unchanged and held'. Either way the displayed text differs from the bound bytes, against the 'material literal displayed unchanged / no Markdown' contract.

3. `src/heterodyne/admind/binding.py:268`: stored_inputs now runs validate_stored_request before the MAX_INPUT_BYTES check (it used to run after), which changes which refusal code legacy v1 binding reads return.

   Failure scenario: Take a legacy question or merge row whose canonical RenderInputs exceed MAX_INPUT_BYTES and that also fails asks.check or has redactable context. Before this change `ask binding` returned code 'oversized'; it now returns 'invalid-state' or 'redacted', and it runs full asks.check plus redact(context) on the oversized row first. This contradicts the claim that the v1 default path is unchanged.

4. `src/heterodyne/admind/daemon.py:1373`: autobump checks management validity before the due-time check, so one malformed v2 row is reported as skipped, and audited, on every autobump cycle forever.

   Failure scenario: After a tampered or partial metadata row exists with status 'open', every autobump_seconds tick appends ('<id>', 'management card refused') and therefore writes an audit 'autobump' record, even when the row is nowhere near ask_bump_hours due. The audit log grows without bound and real autobump records are buried. Moving the stored() check after the last_activity_at/due test, or recording the refusal once, avoids this.

5. `src/heterodyne/admind/management.py:129`: Body lines can include arbitrary unpinned URLs, which clients will autolink, while only the renderer's audit_url goes through PINNED_BLOB/links.why validation.

   Failure scenario: A body line 'Before: full audit at https://evil.example/approve-audit' passes literal() (no forbidden characters) and redact(). The client linkifies it beside the validated 'Audit details:' link, so the operator may follow an unvalidated, mutable link as the audit evidence. This bypasses the doc's 'Audit URLs must pass existing public-link validation' intent.

6. `src/heterodyne/admind/binding.py:544`: Refusals for v2 management asks are always returned as AskBindingReply with message 'ask.binding.v1 unavailable', even when the CLI asked for --version 2 on a v2 ask.

   Failure scenario: A v2 ask with tampered metadata, or a render-mismatch, read through `admind ask binding --json --version 2 ID` returns {result: refused, message: 'ask.binding.v1 unavailable', code: invalid-state}. A queue-child consumer that compares the requested version, as the doc requires, gets a reply naming v1 for a v2 request. That is ambiguous evidence about whether it hit a v1-only daemon or a refused v2 card.

7. `src/heterodyne/admind/management.py:134`: The card legend groups 'deny' and 👎/❌ with B ('B / deny / 👎 ❌ = leave held'), but map_reply/map_reaction record them as 'none', so the confirmation does not match what the card said.

   Failure scenario: The operator replies 'deny' or reacts 👎, expecting B per the legend. answer_ask stores choice 'none' and replies 'Recorded for X: none of these (from deny).', which the card never mentions. A consumer that keys on literal B for 'leave held' sees a different native choice than the operator selected according to the card.

8. `src/heterodyne/admind/asks.py:151`: The v2 contract is enforced by two hand-written parallel validators, management_check (request side) and management.stored plus lines (row side), and they already check different fields.

   Failure scenario: management_check does not check digest or refreshed_from; stored() does not check title/body literals (lines() does), and the choices/recommended checks are duplicated in both. A future change to one (for example, allowing a link) silently produces rows that post but then refuse on render or binding, or the reverse. Building the candidate AskRow and validating it once through stored() plus lines() would give a single source of truth.

9. `src/heterodyne/admind/asks.py:450`: Altitude: v2 is wired in through `if management.present(row)` special cases in seven shared asks.py functions, plus per-caller `except management.Refusal` blocks in the daemon, rather than one renderer/answer-mapper dispatch chosen per row.

   Failure scenario: question_card, card_chunks, full_text, full_chunks, checked_card_chunks, map_reaction and map_reply each branch separately, and post_ask, remind, autobump, ask_details and answer_refusal each catch or recheck separately. Any new shared card entry point (for example a future summary or reminder renderer) silently falls back to legacy semantics for v2 rows unless someone remembers to add the branch. Selecting a renderer object per row once would close that gap.

10. `src/heterodyne/admind/management.py:155`: Repeated work on every render or post: lines() redacts the whole card, then parts() redacts the joined text and each chunk again, details re-packs the base card, and stored() JSON-decodes and regex-validates on every call (R1 #10 residual is still open).

    Failure scenario: One v2 post runs management_check twice (asks.check, then post_ask), stored() three times (lines, readback, ack) and redact() over the full card four or more times. Each reply or reaction calls stored() twice (answer_refusal, then map_reply/map_reaction), and each autobump tick decodes every v2 row. lines() already proves redact(text)==text, and chunk markers are fixed ASCII, so the later whole-card and per-chunk redactions are redundant. The INSERT followed by an UPDATE also remains.
