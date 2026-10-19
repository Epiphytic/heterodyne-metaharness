# PV third actual MEDIUM review: author dispositions

Actual read-only report says no critical flaw blocking an offline PoC, contingent on findings1/2 consent-semantics repairs. Reviewer ran no tests/probes. No fabricated APPROVE token or live clearance. This candidate repair has not yet received the controller-run fourthMED follow-up.

Raw report: [actual report](pvbxr-r3-medium-actual.md). Exact provenance: [record](pvbxr-r3-provenance.json). Structured ordered dispositions: [all ten](pvbxr-r3-dispositions.json).

Reviewed source: `397d40250bfcfbec858220bd7562c97496ed9cf0`. Raw SHA256 `7134a0744c879336098f808b21366bb3340a5010a91c3ed1df1a6fdb56c6b451`. First and second actual FULL HIGH reports/dispositions remain unchanged.

## Finding 1: ADDRESSED

1. `src/heterodyne/admind/management.py:131`: The card legend names only 'A / approved / 👍 ✅ ❤ ♥' as approve and says 'Any other reply ... holds this request', but map_reply's v2 path calls verbs.read_reply, whose APPROVE_WORDS also include yes, y, ok, okay, lgtm and approve; all of these record choice A.

   Failure scenario: The operator replies 'ok' or 'yes' meaning 'seen'. The card says any other reply holds, but answer_ask stores choice 'A' and confirms 'Recorded for X: A = Perform this exact change (from ok)'. The doc tells the queue child to accept a 'literal-A/accepted-positive history', so the change is performed. These words are disclosed only in !details, not on the card the operator consented on.

Author disposition: The on-card legend now lists all seven actual positive words and every positive emoji family with supported skin tones. Denial words with optional reasons and no/n-alone semantics are also visible. The existing parser is untouched.

Evidence/limit: test_card_discloses_every_word_that_records_approval; test_card_discloses_denial_grammar_without_changing_native_choices

## Finding 2: ADDRESSED

2. `src/heterodyne/admind/asks.py:528`: The card states 'a later A cannot override it', but admind enforces nothing: answer_refusal still accepts answers on 'answered' asks, map_reply records a later A, and the confirmation says A was recorded without warning that the request stays held.

   Failure scenario: The operator replies B, then A. Both answers are stored, the second as choice 'A' with the reply 'Recorded for X: A = Perform this exact change (from A).' The operator is told A took effect while the card said it cannot. Any consumer that reads `ask get`/`ask wait` answers and takes the latest choice (the queue child is not implemented yet) would perform the change. Only an unwritten downstream policy upholds the promise.

Author disposition: Removed the unsupported daemon promise that later A cannot override a contrary answer. The card assigns complete-history verification/holds to the queue. Management-only choice confirmations explicitly say a recorded answer is not authorization. Real answer_ask B/negative/ambiguous-then-A regressions retain both authenticated answers in source-owned binding; the daemon remains a recorder, not first-answer-wins enforcement.

Evidence/limit: test_negative_then_a_retains_complete_history_without_claiming_authorization; test_recorded_legacy_confirmation_is_byte_unchanged

## Finding 3: ADDRESSED

3. `src/heterodyne/admind/cli.py:357`: With --version 2, a successful reply carrying a V1 envelope is printed as 'ask.binding.v1 complete' with exit 0; the requested version is enforced only on refusals, never on success.

   Failure scenario: The queue child runs `admind ask binding --json --version 2 <id>` on a legacy question ask (wrong ID, or a lookalike legacy card) and gets exit 0 with result ok. The only signal is the envelope's version 1 and missing presentation, so it relies entirely on each consumer re-checking (the doc says 'consumers must compare'). Refusing a success whose version differs from the requested one under --version 2 would close this.

Author disposition: Explicit CLI --version2 now refuses successful V1 envelopes with invalid-reply/no binding. Internal wire decoding can still identify V1, and default --version1 success bytes/refusal semantics remain unchanged.

Evidence/limit: test_explicit_binding_version_two_rejects_successful_legacy_envelope

## Finding 4: OPEN UNWAIVED

4. `src/heterodyne/admind/daemon.py:1372`: The R2 F4 fix only defers the problem: once a malformed v2 row becomes due, every autobump tick skips it without touching last_activity_at and writes an 'autobump' audit record, indefinitely.

   Failure scenario: A tampered open v2 row passes ask_bump_hours. From then on, each autobump_seconds tick appends ('id', 'management card refused') and writes an audit record, forever, until someone cancels the row. The audit log grows without bound and real bump records get buried.

Author disposition: Malformed due rows can still emit repeated autobump audit entries without advancing activity. No lifecycle or audit policy change was authorized in this narrow repair.

Evidence/limit: Existing due-time regression retained byte exact; repeated-due audit debt remains.

## Finding 5: OPEN UNWAIVED

5. `src/heterodyne/admind/management.py:29`: The fail-closed literal filters are too broad: _URL matches 'data:' and 'tel:' as unanchored substrings, _MARKDOWN refuses any '_' or '~', and _RESERVED refuses any line starting with 'Ask ', 'Reply ' or a single letter followed by '/' or '='.

   Failure scenario: Ordinary management facts are refused outright: 'Before: metadata: pending', 'Hotel:', a worktree path '~/.hermes/...', a file such as 'test_admind_r2.py', 'Ask owner to merge', or 'x/y holder'. This fails safe but makes it hard for producers to state real before/after or holder facts for a PoC.

Author disposition: Conservative printable-ASCII/URL/Markdown/reserved-prefix filters still refuse some ordinary task facts. No lossy rewriting, broad relaxation or compatibility claim is introduced. The new truthful queue-owned legend prefix is also reserved against spoofing.

Evidence/limit: Original adversarial literal tests remain byte exact.

## Finding 6: ADDRESSED

6. `src/heterodyne/admind/management.py:160`: The details=True path packs the extra grammar lines and returns them without the line/char budget or redaction re-check that the card path applies.

   Failure scenario: !details on a card near the 36-line / 3200-character limit sends a longer, unchecked message. The grammar is static today, so the risk is low, but any future edit to these lines skips every guard the main card path has.

Author disposition: Details grammar is included before the single native pack and the same final redaction/whole-output36-line3200-character guard as cards. No widened budget, truncation or separate unguarded return. Native continuation framing counts toward both limits.

Evidence/limit: test_details_refuses_when_added_grammar_crosses_whole_packed_budget; test_details_rechecks_redaction_after_final_native_packing; test_details_budget_counts_actual_continuation_framing

## Finding 7: OPEN UNWAIVED

7. `src/heterodyne/admind/asks.py:151`: R2 F8 is still open and unwaived: request-side management_check and row-side stored()+lines() are parallel hand-written validators that check different fields.

   Failure scenario: management_check does not check digest/refreshed_from, and stored() does not check title/body literals. A change to one side lets requests post that then refuse at render or binding time, or the reverse.

Author disposition: Request-side and persisted-row validators remain parallel hand-written contracts. This repair does not consolidate or claim equivalence; the existing fail-closed checks remain.

Evidence/limit: R2F8 and predecessor findings retained.

## Finding 8: OPEN UNWAIVED

8. `src/heterodyne/admind/asks.py:450`: R2 F9 is still open and unwaived: v2 is wired in through separate management.present() branches in seven shared renderers and mappers, plus per-caller Refusal handling, instead of one dispatch chosen per row.

   Failure scenario: A new shared card or summary entry point that lacks the branch silently applies legacy semantics to v2 rows.

Author disposition: Management routing remains separate branches across shared render/map entrypoints. The truthful management-only confirmation adds another explicit branch rather than a central dispatcher.

Evidence/limit: R2F9 retained; no architectural cleanup claimed.

## Finding 9: OPEN UNWAIVED

9. `src/heterodyne/admind/management.py:155`: R1 F10 / R2 F10 are still open and unwaived: redact() runs repeatedly, stored() decodes repeatedly, management_check runs twice, and insert_ask still does INSERT followed by UPDATE.

   Failure scenario: One v2 post runs management_check twice, stored() three times and redact() over the full card four or more times. Each answer decodes stored() twice, and every autobump tick re-decodes each v2 row. The cost is wasted CPU and harder maintenance, not wrong results.

Author disposition: Repeated validation/redaction/decoding and INSERT-then-UPDATE remain. Details no longer packs twice, but that narrow change does not resolve the broader finding; recorded() also validates stored metadata before its management warning.

Evidence/limit: R1F10/R2F10 retained; no CPU/packing performance claim.

## Finding 10: OPEN UNWAIVED

10. `src/heterodyne/admind/daemon.py:1257`: answer_refusal runs the management stored() check before the fulfilled and status checks, so a malformed v2 row hides its real state.

    Failure scenario: A reply to a cancelled or fulfilled malformed v2 ask returns the generic 'management card refused' instead of 'Already fulfilled' or the not-open wording, which hides the ask's real lifecycle from the operator.

Author disposition: Malformed metadata still takes refusal precedence over fulfilled/cancelled lifecycle messaging. No change to lifecycle answer eligibility/refusal ordering in this scope.

Evidence/limit: Original malformed-row protections remain.

## Remaining boundaries

- Question-kind presentation remains operation consent, not approve-bead authority.
- Queue child must verify complete delivered/current-policy history and exact binding; it is separate, not implemented here.
- No posted card or native answer is edited, resent, deleted or manufactured.
- R1/R2 findings, prior typing/format/quality debt and genuine deployment gates remain unwaived.
