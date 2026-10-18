**Verdict: REVISE.** The new shared invisible-character check is sound and consistent, but the line-forgery protection still has a critical bypass. A Unicode space inside a frame label (for example a no-break space) is not refused, so a question can still show a fake `Context digest:` or `Recommended:` line that looks identical to the real one. A quick fix is to fold `Zs` to an ASCII space and drop marks and invisible characters across the whole compared prefix, not only its leading run, before matching `_CARD_CONTINUATION`. The structural fix is finding 4: prefix or indent every physical line of user content so it can never start a frame line.

This was a read-only static review of `07637745..be7c316`, ignoring my own deferred-items list. Following findings 1–3 and 5 (homoglyph, multiline context, Khitan filler, VS16) is my reading of how the code behaves; none of them were run.

10 findings, most severe first:

1. `src/heterodyne/wsd/presentation/rendering.py:32`: _CARD_CONTINUATION needs a literal ASCII space inside multi-word labels ("Context digest:", "Approval "), and no Zs space is refused by invisible() or folded before matching, so the same label with a no-break or en space inside it looks identical but gets through.

   Failure scenario: PlainQuestion.answer_instruction='Context\u00a0digest: 0123456789ab' (or question='Approval\u2002(merge) btq-x'): invisible() is False for U+00A0/U+2002 (Zs), safe() passes, _card_frame_start reaches 'C' and the regex fails on the NBSP, so render_card_pair returns a Pair with a line that looks exactly like the generated digest line. The same works on a wrapped choice continuation ('x'*114+' Context\u00a0digest: ...'). This is the same forged-frame class as R4-R7 critical 1.

2. `src/heterodyne/wsd/presentation/rendering.py:441`: The frame comparison is an exact ASCII regex with no confusable or compatibility folding, so look-alike letters (Cyrillic, fullwidth, mathematical alphanumerics) or a fullwidth colon forge protected frame lines.

   Failure scenario: question='R\u0435commended: yes' (Cyrillic ie) or 'Recommended\uff1a yes' or a choice description wrapping to 'n\u043e: Approve anyway': all characters are assigned letters or punctuation, so safe() and _card_frame_start pass and the protected card shows a forged recommendation or choice line. Checking against NFKC plus a skeleton, or refusing non-ASCII in the label-length prefix of unprefixed and continuation lines, would close it.

3. `src/heterodyne/wsd/presentation/rendering.py:467`: Question context (PlainQuestion and ChoiceQuestion) is exempt from the single-line rule, and wrap() splits on LF without running forbidden_continuation on the lines after each LF, so context can inject whole frame lines. The deferral names only Inline.full_text, not question context.

   Failure scenario: ChoiceQuestion(context='Costs one slot\nRecommended: no', recommended_label=None): _protected_line_fields skips context, safe(multiline=True) allows the LF, wrap() never calls _card_frame_start on 'Recommended: no', and the Pair shows a forged 'Recommended: no' line before the real choices. The same works with '\nContext digest: 0123456789ab' on a PlainQuestion.

4. `src/heterodyne/wsd/presentation/rendering.py:467`: Altitude: frame-forgery defense is still a blacklist of known label prefixes checked on selected lines, and each round finds another way to make text look like a label. The root fix is to make it impossible for user content to start a physical line that looks like framing.

   Failure scenario: Findings 1-3 and the deferred Inline.full_text case are all variants of one cause: unprefixed user text can begin a physical line. Emitting every user-content physical line with a fixed generated prefix (e.g. an indent or a '│ ' quote marker, also on wrapped and LF lines), or requiring every generated frame line to carry a non-forgeable marker, removes the whole class instead of growing the regex and skip sets.

5. `src/heterodyne/wsd/presentation/safety.py:48`: invisible() refuses Default_Ignorable code points but not invisible combining marks outside that property (e.g. U+16FE4 KHITAN SMALL SCRIPT FILLER, Mn, zero-width). safe() never refuses category M in the middle of text, and _card_frame_start skips M only at the start.

   Failure scenario: question='Recom\U00016fe4mended: yes': category Mn is not C and is not in _DEFAULT_IGNORABLE, so safe() passes. _card_frame_start stops at 'R', the regex fails on the filler, and a renderer that draws the filler as nothing shows 'Recommended: yes'.

6. `src/heterodyne/wsd/presentation/safety.py:38`: Adding U+FE00-U+FE0F and the E0000 tag block to the global refusal set means any ordinary or protected literal containing emoji presentation selector VS16 (U+FE0F) or a tag-sequence flag is now withheld. Before this change VS16 (category Mn) was allowed.

   Failure scenario: Task title '⚠️ deploy blocked', FYI body '✔️ done', or approval what='Ship ❤️ fix': literal() returns None, so read views show '[withheld]' with complete=False and protected cards return Withheld('unsafe-content'). Common emoji in owner text now blocks approval cards, and the cause is easy to miss.

7. `src/heterodyne/wsd/presentation/safety.py:128`: safe() now calls unicodedata.category plus a regex fullmatch for every non-ASCII character, in a Python loop, and runs on each literal, the joined document and every page or layout. That repeated per-character work sits on the render hot path while the disclosed benchmark max (269.6ms) is already over the 250ms limit.

   Failure scenario: Non-ASCII-heavy cards (CJK or emoji text near 3200 chars) pay the per-character regex at least four times per render_card_pair, which pushes tail latency further past the limit. A single precompiled character-class regex search over the whole text (template-covered matches filtered afterwards), or a frozenset/bisect range table, does the same check in C.

8. `src/heterodyne/wsd/presentation/safety.py:44`: The Unicode 15 pinning holds only for the Default_Ignorable regex; the Category C half depends on the host's unicodedata version, so the refusal set and the golden outputs change with the Python minor version (3.13 is Unicode 15.1, 3.14 is 16.0).

   Failure scenario: Text with U+2FFC (an IDS character, unassigned (Cn) in 15.0, assigned in 15.1) is refused on CPython 3.12 but rendered on 3.13. The same card is Withheld on one host and a Pair on another, which breaks the 'independent of host Unicode tables' determinism that classification.py's _WHITE explicitly guards.

9. `src/heterodyne/wsd/presentation/classification.py:26`: Ingress classification keeps its own partial control check (Cc/Cf/Cs) and strips only White_Space before looking for '/', instead of reusing the new shared invisible() boundary. Commands hidden behind a Default_Ignorable or Hangul filler are routed as ordinary text.

   Failure scenario: Verified thread text '\u3164/approve' or '\u2065/approve' displays as '/approve', but candidate.startswith('/') is False, so it is classified DeferredSteering 'thread-text' instead of UnsupportedIn6A 'mutation'. A downstream consumer then sees an apparent approval command routed as steering text.

10. `src/heterodyne/wsd/presentation/rendering.py:438`: Simplification: the invisible(char) skip in _card_frame_start can never take effect. Unprefixed fields and wrapped continuations only reach it after safe() has refused every invisible character outside the template, so the skip set suggests a second line of defense that does not exist.

    Failure scenario: A maintainer reading the frame check assumes it protects alone and relaxes safe() for some field, believing frame start still covers it. In fact the regex only ever sees text safe() has already screened, so the overlap hides which check actually does the work.
