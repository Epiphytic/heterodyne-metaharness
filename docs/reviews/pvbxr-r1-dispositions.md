# PV first HIGH author dispositions

Actual raw report is [retained byte-exact](pvbxr-r1-high-actual.md), SHA256 `a8d153385596b6400fb9ac556f2e6281939faa869e3d4c7c4410c6dab760b0fb`. This is author repair evidence, not another review or clearance. The report says no critical findings; all ten are preserved below. Original source revision `5a969accbce40fdef303c0e3b680a8761dad8fd1`; approved design `62e7ecf1433c601a36ad23b350da025b86a4c7dc`.

## Finding 1 — ADDRESSED

1. `src/heterodyne/admind/management.py:145`: The card legend and the !details grammar describe the approval-card parser (verbs.read_reply), not the question parser (asks.map_reply) that actually handles replies on these question-kind cards. So the deny words, emoji text replies and punctuation they list are not what gets recorded.

   Failure scenario: The operator follows the card ('B / deny ... = leave held') or !details ('Denial words: deny, denied, reject, rejected (optional reason)') and replies 'deny' or 'rejected: too broad'. map_reply folds this to a string that is in neither APPROVE_WORDS nor DENY_ALONE, so it is stored as a free answer with choice None, not B. Likewise a text reply of '👍' or 'approved!' is a free answer, not A, because _short does not strip '!' and map_reply never calls emoji_action on reply text. The confirmation the operator sees and the native choice therefore differ from what the card promised.

Author response: V2-only map_reply delegates approval words, punctuation and emoji to verbs.read_reply; A/B letters remain exact and negative words/reactions mean none per approved ADR. Ordinary question mapping remains unchanged.

Regression evidence: `test_v2_reply_uses_approval_grammar_without_changing_legacy`. Finding UTF-8 SHA256 `4c51419af9d4191e9090c7cf59e55e081fde3cd6afbd4d0e27ba42efb346d072`.

## Finding 2 — ADDRESSED

2. `src/heterodyne/admind/management.py:111`: The title and body are not screened for native label text or for lines the renderer owns, so a same-UID poster can show an 'APPROVAL: design' heading or fake 'Audit details:' / 'A = ...' / 'Request:' lines inside the readable APPROVAL card.

   Failure scenario: test_native_approve_bead_rejects_management_metadata asserts that title='APPROVAL: design' passes check(). The card then reads 'APPROVAL: Task management / Ask ... / APPROVAL: design / ...'. A body line 'Audit details: https://github.com/evil/x/blob/<40hex>/a' placed above the real one also passes literal(), since it contains no forbidden characters. A human can be misled about what they are consenting to. The doc's 'Do not repeat these in the body' is not enforced.

Author response: Refuse native labels and renderer-owned line prefixes before insertion. The original native-approval test has exactly two root-authorized neutral title setup replacements, not weaker native rejection assertions. Original bytes and AST/diff evidence retained privately.

Regression evidence: `test_v2_refuses_spoofed_framing_and_nonliteral_text`. Finding UTF-8 SHA256 `c8a50a9f860e9d2941a450ae5cdb430ba0eb68adc14761f9d6032c23534fd001`.

## Finding 3 — ADDRESSED

3. `src/heterodyne/admind/store.py:298`: _ask() now raises ValueError for any row whose render_version is not an int. _ask() decodes rows for every bulk listing, so one malformed row breaks the whole ask subsystem instead of refusing just that ask.

   Failure scenario: A foreign writer or tampered DB leaves render_version='2x' (text that INTEGER affinity cannot convert) on one row. Every later post_ask then calls store.capacity_asks(), which raises ValueError, so all new posts fail. remindable_asks, inactive_asks and recent_asks also raise, which breaks reminders and !asks. management.stored() already refuses non-int versions, so this store-level check is both harmful and redundant.

Author response: Malformed noninteger stored render_version is represented as nonlegacy invalid sentinel -1. Bulk row reads survive; management rendering/answer/repeat refuses that row rather than falling back to legacy. No row is backfilled.

Regression evidence: `test_malformed_version_does_not_break_bulk_asks; test_corrupt_card_cannot_be_answered_or_repeated`. Finding UTF-8 SHA256 `7040c43bb0a9451e3a2a5e25c5235a10ed7afd47a9ae9df3ac27a3097aa0f2d3`.

## Finding 4 — ADDRESSED

4. `src/heterodyne/admind/management.py:33`: management.Refusal subclasses chunk.TooWide with length=0, usable=0. Existing TooWide handlers therefore report management refusals as link-length problems with nonsense numbers.

   Failure scenario: A management row with tampered metadata hits !details: the TooWide handler sends DETAILS_LINK_TOO_LONG, i.e. 'a link of 0 characters, 0 usable; raise chunk_chars', and audits reason='link too long'. !asks repeat skips it with 'a link is longer than this group's message size now allows; raise chunk_chars'. And when a management body line is wider than the configured chunk width, chunk.pack raises a real TooWide, which post_ask reports as ASK_LINK_TOO_LONG even though no link is involved.

Author response: Management Refusal is a distinct ValueError, not TooWide. Renderer width errors become that typed refusal; post/details/repeat use fixed management diagnostics and cannot claim a zero-character link.

Regression evidence: `test_management_refusal_not_a_link_width_error; test_details_corrupt_metadata_fixed_refusal; test_budget_refusal_is_audited_with_fixed_nontext_reason`. Finding UTF-8 SHA256 `879b8898031b50837e681faa80995f24938ec0514b1aa871cf4a79b911f9ccc3`.

## Finding 5 — ADDRESSED

5. `src/heterodyne/admind/daemon.py:973`: When a management card is refused after rendering (unsafe or over budget), nothing is audited. The neighbouring TooWide refusal is audited, and neither the posted audit record nor describe() records presentation or render_version.

   Failure scenario: A producer's over-budget or redacted card is refused before insert, but the audit log shows no 'refused' event for it. Successful v2 posts also cannot be told apart from ordinary choices questions in the audit log, so after-the-fact reconciliation of management consent requests has no audit trail.

Author response: Unsafe/budget/readback refusals have fixed reason codes and presentation/version audit fields; successful V2 posted audit and describe include those fields. No raw body is added to audit.

Regression evidence: `test_valid_ack_and_audit_use_saved_version; test_budget_refusal_is_audited_with_fixed_nontext_reason`. Finding UTF-8 SHA256 `75652ee8dd7684528790ca9638d5dad0f4770d88847812218e3d1fcb10196ee7`.

## Finding 6 — ADDRESSED

6. `src/heterodyne/admind/management.py:24`: The literal() screen misses several Markdown and Unicode forms that change what is displayed, despite the contract 'no Markdown formatting' and 'material literal displayed unchanged'.

   Failure scenario: A body with a 4-space-indented line (Markdown code block), a '---' or '===' line under text (setext heading), or combining marks such as U+0336 strikethrough over 'After: open' (category Mn, not refused) passes literal(). Private-use and unassigned code points (Co/Cn) also pass. A Markdown-rendering or font-dependent client then shows text that differs from the bytes that were bound.

Author response: Refuse indented Markdown code, setext separators, U+0336, private-use and unassigned code points in addition to existing controls/formatting. Refuse whole card without lossy sanitization.

Regression evidence: `test_v2_refuses_spoofed_framing_and_nonliteral_text`. Finding UTF-8 SHA256 `ddefff70c743a655c5ed3c2d24102b8eb361dbfac75298474b5d1efd7fa227aa`.

## Finding 7 — ADDRESSED

7. `src/heterodyne/admind/binding.py:272`: The v2 inputs skip the size budget and are built untyped: MAX_INPUT_BYTES is checked on the v1 inputs before the management metadata is added, and RenderInputsV2 is built from msgspec.to_builtins(inputs), so its nested fields hold plain dicts and lists, not the declared types.

   Failure scenario: The v2 canonical inputs can exceed MAX_INPUT_BYTES by the metadata size (up to about 2 KB) without an 'oversized' refusal. RenderInputsV2.links would hold dicts rather than LinkSpec, and approval_counts a list rather than a tuple, so in-process equality or isinstance checks on v2 inputs differ from the decoded wire form. Building with msgspec.structs.asdict, and checking the size after building, avoids both.

Author response: Build RenderInputsV2 with msgspec.structs.asdict preserving native members and enforce MAX_INPUT_BYTES on final V2 canonical bytes.

Regression evidence: `test_v2_binding_final_budget_includes_metadata; test_typed_v2_inputs_keep_native_struct_members`. Finding UTF-8 SHA256 `d26d40d6c771d65cb1529d8a9000b585043742e3cefaac60d41feab9dcd2ce2e`.

## Finding 8 — ADDRESSED

8. `src/heterodyne/admind/daemon.py:993`: The post acknowledgment echoes the request's presentation and metadata rather than reading back the persisted row, so the CLI's acknowledgment check only proves the daemon parsed the request, not what it stored or rendered.

   Failure scenario: If the persisted management JSON or render_version ever differs from req (an encoding change, or a future normalisation in insert_ask), the CLI still accepts the acknowledgment because it compares reply.management with req.management. Building the reply from management.stored(self.store.ask(row.ask_id)) would make the acknowledgment reflect the bound row.

Author response: Read back and validate the persisted row inside the post transaction. Any divergence from the rendered row aborts the transaction before outbox insertion; successful acknowledgment uses validated saved metadata.

Regression evidence: `test_reply_reads_persisted_metadata; test_valid_ack_and_audit_use_saved_version`. Finding UTF-8 SHA256 `ab36175f3185e4d9b2f90856d70bbb478ed7b87a9ada10a3f796b8dbd030ce4c`.

## Finding 9 — ADDRESSED

9. `src/heterodyne/admind/management.py:23`: The audit-URL, hex and poster regexes re-implement validators that already exist: the links blob form (links.PINNED / _OWNER / _REPO / _PATH), binding.HEX_KEY and asks.POSTER.

   Failure scenario: _AUDIT is a looser copy of links' pinned blob form: an owner may start with '-', and the path pattern [^?#]+ is broader. The poster regex at line 106 duplicates asks.POSTER, and _HEX duplicates binding.HEX_KEY. Three copies of the same validation can drift. Exposing a blob-only pattern from links and reusing asks.POSTER and HEX_KEY removes the duplication.

Author response: Reuse binding.HEX_KEY and asks.POSTER with deferred imports; expose links.PINNED_BLOB from the existing URL grammar rather than another looser validator.

Regression evidence: `test_management_url_uses_shared_pinned_owner_grammar`. Finding UTF-8 SHA256 `bee356ac19cd624edf3cd58fa504930b26ce9a05936c5b525449b10c3666fdfa`.

## Finding 10 — PARTIALLY ADDRESSED / RESIDUAL OPEN

10. `src/heterodyne/admind/daemon.py:955`: Redundant work: management_check is re-run in post_ask although asks.check (daemon.py:872) already runs it before dispatch, and management.stored() JSON-decodes and validates the metadata 3-5 times per render or binding.

    Failure scenario: question_card, card_chunks, full_chunks and checked_card_chunks each call stored(), then lines() calls it again. stored_inputs calls stored() and validate_stored_request calls it again. parts(details=True) packs the card twice, and insert_ask does an INSERT followed by an UPDATE where one INSERT with the three columns would do. Each binding read therefore repeats decoding and validation, and the duplicated refusal string in post_ask is dead code.

Author response: Renderer selection uses a metadata-presence predicate instead of repeated JSON decoding, and stored_inputs passes validated metadata once. Direct post validation remains defense for internal callers; !details validates standalone card budget before separate details packing. INSERT plus metadata UPDATE is retained to preserve the original atomic UPDATE-failure rollback oracle; consolidation is deferred. No performance claim.

Regression evidence: `Original management rollback oracle; focused binding/render suites`. Finding UTF-8 SHA256 `055f04df4a10772a37ef8ecbe689eee3abda41bf2ff2867576ab6d057a3bfc81`.

## Limits

No live or native operations; no activation/deployment approval asserted. Queue consumer remains separately gated.

Configured typing and quality debt remain OPEN/unwaived; exact final evidence is private controller handoff.

PV and X84 store.py have a genuine future integration conflict; no sibling merge or composed compatibility claimed.

Exactly two setup title literals in the prior native-approval rejection test were replaced with a neutral title under explicit controller authorization; all remaining source and assertions are unchanged. All other 253 tracked original test files remain byte-identical. This repairs the approved no-spoof renderer boundary and does not broaden native approval authority.
