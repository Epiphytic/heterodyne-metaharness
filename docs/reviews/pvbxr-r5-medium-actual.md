No critical flaw blocks an offline, source-only PoC. The R4 finding 1 repair does not close the spoofing gap, though, so the live hold should stay in place. I read the code only and ran nothing.

What I checked:
- **Old fix (`bc130b2`):** the new `_ANSWER_LEGEND` check works only when an approval or denial word is the first thing on a line. Putting any text before the word gets past it, and so does any separator other than `=`, `/` or `:`. The older choice-letter check (`_RESERVED`) has the same weakness, so a body line can still relabel what A means. Since A and B are the main replies on the card, this is at least as serious as the word case R4 found.
- **No harm to the PoC:** none of this lets a recorded answer authorize anything by itself. The binding envelope still hands complete-history checking to the queue, and that queue component is still missing, so it must stay a hold.
- **Rest of the change (`b4b8c75..bc130b2`):** I found nothing new and critical in `daemon.py` or `binding.py`. Malformed rows still refuse, the binding reply now has a separate V2 shape, and the default V1 reader still refuses V2.
- **Earlier findings:** R4 findings 2–10 and all older unresolved findings are still OPEN and I have not repeated them below. That includes the open ordinary-facts refusal debt that finding 3 adds to.

8 findings, most severe first:

1. `src/heterodyne/admind/management.py:29`: _ANSWER_LEGEND and the letter rule in _RESERVED both match only at the very start of a line, so any non-space prefix (bullet, number, label) gets a fake legend line past both checks, and R4 finding 1 is still exploitable.

   Failure scenario: Body lines '- ok / yes = leave unchanged and held', '1. A = Leave unchanged and held' or 'Legend: ok = hold' all pass literal(). '-' is only refused as a run of 3 or more, and neither regex fires because the line does not start with the word or letter. The card shows this above the real legend; the operator replies 'ok' or 'A' meaning hold, and map_reply records 'A' (Perform this exact change).

2. `src/heterodyne/admind/management.py:25`: The choice-letter rule only treats '=' or '/' after a single letter as reserved, so 'A:', 'A)', 'A -', 'A.' and 'Option A:' lines that relabel the options still pass, and those are the primary answer tokens.

   Failure scenario: Body line 'A: Leave unchanged and held' or 'A) Leave unchanged and held' passes literal() (':' and ')' are not refused anywhere), and nothing on the card contradicts it except the later legend. The operator replies 'A' to hold; map_reply returns 'A' = Perform this exact change. Same spoof class as R4 finding 1 but on the letters, which the new repair does not touch.

3. `src/heterodyne/admind/management.py:33`: The fix is a delimiter blocklist added to free-text producer lines. Natural-language or other-delimiter relabelling still passes, so the root cause (producer text sits unmarked next to the renderer's legend) is not fixed.

   Failure scenario: 'ok - leave unchanged and held', 'ok means leave held', 'To leave held, send ok' and 'If unsure, yes keeps it held' all pass literal(), and the operator's 'ok' or 'yes' is recorded as A. A more general fix is to render producer body lines in a clearly framed, prefixed region (for example every fact line prefixed 'Fact: ' by the renderer, with the legend first or last and stated as the only legend) instead of adding more blocklist patterns.

4. `src/heterodyne/admind/management.py:31`: The new regex refuses any ordinary fact line that starts with no, n, yes, y, ok, approved, rejected or denied and contains ':' or '/' anywhere, making the open over-refusal debt worse.

   Failure scenario: Lines like 'No blockers: none open', 'N/A: holder', 'Approved design: 62e7ecf1', 'Rejected attempt: 1/2' or 'Yes/no gate: pending' each refuse the whole management post. The new test only proves mid-line words ('Reason: approved ...') pass, so this regression is not covered.

5. `tests/test_admind_management_r4.py:13`: The regression tests only try the start-of-line form with three separators. There is no case with a prefix, another separator, or a letter-colon spoof, so the tests give false confidence that R4 finding 1 is closed.

   Failure scenario: All 42 cases pass while '- ok = leave held', 'A: Leave unchanged and held' and 'ok - leave held' are still accepted by literal(). A reviewer relying on '42 new tests pass' would treat finding 1 as fixed.

6. `src/heterodyne/admind/management.py:137`: The card legend and !details word lists are hard-coded strings, while the new filter takes its words from verbs, so the two can drift.

   Failure scenario: Adding a word to verbs.APPROVE_WORDS (for example 'sure') updates the filter and the parser, but the card still lists only 'approve / approved / yes / y / ok / okay / lgtm'. The operator is never told a new approval word exists, and R3 F1's legend-matches-parser repair silently goes stale.

7. `src/heterodyne/admind/management.py:31`: The alternation is built by joining raw words without re.escape, so a regex metacharacter in a future verbs word would quietly change or break the filter.

   Failure scenario: If verbs gains '+1' or 'a.ok', the pattern either fails to compile at import time (admind would not start) or matches the wrong text (the '.' matches any character). '|'.join(map(re.escape, ...)) avoids this.

8. `src/heterodyne/admind/management.py:26`: The hand-written 'deny\s*/' entry in _RESERVED is now fully covered by the derived _ANSWER_LEGEND (which also covers 'deny' followed by ':' or '='), so it is dead duplicate state in the hand-copied reserved list.

   Failure scenario: Two places now encode the denial-word legend rule. A future edit to one (for example narrowing _ANSWER_LEGEND) leaves an inconsistent half-rule, and the hand-copied list is the drift R2 already flagged.
