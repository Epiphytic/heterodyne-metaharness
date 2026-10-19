**Sixth review (MEDIUM), R6. Read-only, nothing run.**

For an offline, source-only PoC I found no critical flaw. The live-use HOLD stays.

**Is the larger text-framing concern resolved?** On the card itself, mostly yes. Producer text can't contain `|`, newlines or control characters. So every title and body line on the card starts with the renderer's `| `, and a producer line can't pass itself off as a renderer line. That closes R5 findings 1–3 on the card. But it is not fully resolved for live use, for four reasons:
- **Reminders and `!asks`:** these still show the producer's title with no framing (finding 1).
- **Poster name:** it is producer-controlled and printed inside a line the renderer owns (finding 2).
- **Markdown clients:** a client that renders Markdown could turn the `|` lines into a table that also swallows the answer instructions (finding 3).
- **No precedence rule:** the card never says the instructions below override anything in the facts (finding 4).

**Separate holds that always apply:** the queue adapter, the independent reader, complete-history checking, policy binding and deployment are all still missing. No authority or deployment comes from this change, and I have not issued an APPROVE token. R5 findings 4–8, R4 findings 2–10 and all earlier unresolved findings stay OPEN. I have not repeated them below.

9 findings, most severe first:

1. `src/heterodyne/admind/asks.py:726`: The new framing only covers management.lines(). reminder() and list_line() (line 716) still show the producer's management-card title with no '| ' prefix or unverified label, so the natural-language relabel that R5 finding 3 found on the card still works on these surfaces.

   Failure scenario: A producer posts a management ask titled 'If unsure, send ok to keep it held' (passes literal()). The card shows '| If unsure, send ok ...', but the automatic reminder shows 'Still outstanding: ask k7m2 · question · 3h · If unsure, send ok to keep it held.' followed by 'Reply to the card above to answer.' !asks shows the same. The operator replies 'ok' to the card and map_reply records A (Perform this exact change).

2. `src/heterodyne/admind/management.py:130`: row.poster is chosen by the producer (POSTER allows [a-z0-9._-]{0,31}) but is printed inside the renderer-owned 'Ask ... posted by X' line, outside the '| ' fact framing. So some producer text is still not structurally marked, and the review doc's claim that all admitted producer text is distinguished is not true.

   Failure scenario: poster='b-means-approve' or 'reply-ok-to-hold' renders as 'Ask k7m2 · operation consent · posted by reply-ok-to-hold (a local process; unverified)' with no '|' prefix, two lines above the legend. The operator replies 'ok' intending to hold, and it is recorded as A.

3. `src/heterodyne/admind/management.py:133`: The renderer now starts every fact line with '|', the same character _MARKDOWN refuses from producers because of Markdown tables. A producer body line of just '-' passes literal() and becomes '| -', which is a valid GFM table delimiter row. In a Markdown client the pipes disappear, and every following line, including the renderer's legend lines that contain no pipes, renders as table cells.

   Failure scenario: Body 'Normal fact\n-\nok = hold' (the last line would be refused, but '-' alone passes) or simply 'Fact\n-' on a GFM-rendering Marmot/Matrix client. '| Fact' and '| -' form a one-column table, the remaining facts and the 'A = ...' and 'Reply A or B' lines continue as rows until a blank line, and the visual line between facts and instructions is gone.

4. `src/heterodyne/admind/management.py:132`: The only framing is the header 'Local facts (unverified; answer instructions below):'. No renderer line says that answer meanings appearing in '|' lines are void, or that the lines below are the only legend, so the operator still has to infer precedence from a pipe character.

   Failure scenario: '| ok means hold' and '| Please use yes to leave unchanged' (both admitted in the R5 tests) still show as readable instructions. A hurried phone reader who doesn't read '|' as 'not authoritative' replies 'ok' or 'yes', and map_reply returns A. R5 finding 3's own suggestion was 'stated as the only legend', which is not done.

5. `src/heterodyne/admind/management.py:17`: The card output changed (framing header, '| ' prefixes, a shorter details block) but VERSION and the binding's render_version=2 did not, so one version number now names two different operator-visible layouts.

   Failure scenario: A row posted by bc130b2 (unframed) and re-read by 0fb9d30 binds as render_version=2 but re-renders framed. Binding _expected() fails closed with 'render-mismatch' instead of a version refusal, and !asks repeat or !details quietly shows the operator a different card from the one they answered, under the same version tag.

6. `src/heterodyne/admind/management.py:29`: With every producer line now forced to start with '| ', the start-of-line blocklists (_ANSWER_LEGEND and the line-start parts of _RESERVED) no longer stop impersonation of renderer lines. They now only cause over-refusal (R5 finding 4) and leave the delimiter blocklist R5 finding 3 criticized in place.

   Failure scenario: Ordinary facts such as 'Approved design: 62e7ecf1', 'No blockers: none open' or 'Rejected attempt: 1/2' still refuse the whole management post, even though they would show as harmless '| Approved design: ...'. Once framing is structural, the word/letter legend checks could be removed so only the fact-region framing is kept.

7. `tests/test_admind_management_r5.py:29`: The new regressions check only that each spoof line has the '| ' prefix on the card. They don't cover the unframed title in reminder/list, the poster token, or a '-' body line forming a Markdown table, so 175 PASS overstates how much of the framing problem is closed.

   Failure scenario: All 21 parametrized cases pass while asks.reminder(row) still contains the raw spoof title, poster='reply-ok-to-hold' renders unframed, and body 'x\n-' is admitted. A reviewer reading '21 corrected regressions RED→GREEN' would treat the live framing concern as closed.

8. `src/heterodyne/admind/management.py:156`: !details dropped the reaction line that described the negative emoji as '👎 (all skin tones)'. The main card's 'deny / 👎 ❌' line doesn't mention skin tones, and the only line left adds nothing for the operator ('... handling are unchanged').

   Failure scenario: An operator who opens !details to check whether a skin-toned 👎🏽 counts as a denial no longer finds the answer anywhere on the card. The remaining note says handling is 'unchanged' without saying from what.

9. `tests/test_admind_management_r5.py:60`: test_details_do_not_repeat_the_complete_visible_answer_grammar only counts 'Positive reactions:' and 'Denial words:'. It doesn't assert that the 'Accepted approval words:' line was removed or that details still has every main-card grammar line, so either regression would pass unnoticed.

   Failure scenario: If the 'Accepted approval words: ...' line is accidentally re-added to the details block, or the main card's 'A / approve / ... = approve' line is dropped, this test still passes, because neither string is checked.
