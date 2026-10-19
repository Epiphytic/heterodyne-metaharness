I reviewed the full b4b8c75..5a969ac diff (`management.py`, `asks.py`, `binding.py`, `cli.py`, `daemon.py`, `store.py`, the new test file and the DTO doc) against ADR 62e7ecf. Per your instructions this was read-only: I edited no files, ran no tests or probes, and did no queue, native, service, DB or network work.

These parts hold up:
- **Refuse-before-insert:** it works, and the line/character budget counts the real ask ID and the continuation markers.
- **Atomicity:** metadata writes are atomic, because the nested transaction joins the outer one.
- **Old fixed writer:** it still writes NULL metadata, and the positional full-row writer fails as a whole.
- **Legacy post bytes:** unchanged, because `AskPost` uses `omit_defaults`.
- **v1 decoders and the acceptance adapter:** both stay closed against v2 envelopes.
- **Native approve-bead authority:** none is added; the kind stays `question`.
- **Queue child:** I did not treat it as current product authority.

There are no critical findings. Here are the 10, most severe first:

10 findings, most severe first:

1. `src/heterodyne/admind/management.py:145`: The card legend and the !details grammar describe the approval-card parser (verbs.read_reply), not the question parser (asks.map_reply) that actually handles replies on these question-kind cards. So the deny words, emoji text replies and punctuation they list are not what gets recorded.

   Failure scenario: The operator follows the card ('B / deny ... = leave held') or !details ('Denial words: deny, denied, reject, rejected (optional reason)') and replies 'deny' or 'rejected: too broad'. map_reply folds this to a string that is in neither APPROVE_WORDS nor DENY_ALONE, so it is stored as a free answer with choice None, not B. Likewise a text reply of '👍' or 'approved!' is a free answer, not A, because _short does not strip '!' and map_reply never calls emoji_action on reply text. The confirmation the operator sees and the native choice therefore differ from what the card promised.

2. `src/heterodyne/admind/management.py:111`: The title and body are not screened for native label text or for lines the renderer owns, so a same-UID poster can show an 'APPROVAL: design' heading or fake 'Audit details:' / 'A = ...' / 'Request:' lines inside the readable APPROVAL card.

   Failure scenario: test_native_approve_bead_rejects_management_metadata asserts that title='APPROVAL: design' passes check(). The card then reads 'APPROVAL: Task management / Ask ... / APPROVAL: design / ...'. A body line 'Audit details: https://github.com/evil/x/blob/<40hex>/a' placed above the real one also passes literal(), since it contains no forbidden characters. A human can be misled about what they are consenting to. The doc's 'Do not repeat these in the body' is not enforced.

3. `src/heterodyne/admind/store.py:298`: _ask() now raises ValueError for any row whose render_version is not an int. _ask() decodes rows for every bulk listing, so one malformed row breaks the whole ask subsystem instead of refusing just that ask.

   Failure scenario: A foreign writer or tampered DB leaves render_version='2x' (text that INTEGER affinity cannot convert) on one row. Every later post_ask then calls store.capacity_asks(), which raises ValueError, so all new posts fail. remindable_asks, inactive_asks and recent_asks also raise, which breaks reminders and !asks. management.stored() already refuses non-int versions, so this store-level check is both harmful and redundant.

4. `src/heterodyne/admind/management.py:33`: management.Refusal subclasses chunk.TooWide with length=0, usable=0. Existing TooWide handlers therefore report management refusals as link-length problems with nonsense numbers.

   Failure scenario: A management row with tampered metadata hits !details: the TooWide handler sends DETAILS_LINK_TOO_LONG, i.e. 'a link of 0 characters, 0 usable; raise chunk_chars', and audits reason='link too long'. !asks repeat skips it with 'a link is longer than this group's message size now allows; raise chunk_chars'. And when a management body line is wider than the configured chunk width, chunk.pack raises a real TooWide, which post_ask reports as ASK_LINK_TOO_LONG even though no link is involved.

5. `src/heterodyne/admind/daemon.py:973`: When a management card is refused after rendering (unsafe or over budget), nothing is audited. The neighbouring TooWide refusal is audited, and neither the posted audit record nor describe() records presentation or render_version.

   Failure scenario: A producer's over-budget or redacted card is refused before insert, but the audit log shows no 'refused' event for it. Successful v2 posts also cannot be told apart from ordinary choices questions in the audit log, so after-the-fact reconciliation of management consent requests has no audit trail.

6. `src/heterodyne/admind/management.py:24`: The literal() screen misses several Markdown and Unicode forms that change what is displayed, despite the contract 'no Markdown formatting' and 'material literal displayed unchanged'.

   Failure scenario: A body with a 4-space-indented line (Markdown code block), a '---' or '===' line under text (setext heading), or combining marks such as U+0336 strikethrough over 'After: open' (category Mn, not refused) passes literal(). Private-use and unassigned code points (Co/Cn) also pass. A Markdown-rendering or font-dependent client then shows text that differs from the bytes that were bound.

7. `src/heterodyne/admind/binding.py:272`: The v2 inputs skip the size budget and are built untyped: MAX_INPUT_BYTES is checked on the v1 inputs before the management metadata is added, and RenderInputsV2 is built from msgspec.to_builtins(inputs), so its nested fields hold plain dicts and lists, not the declared types.

   Failure scenario: The v2 canonical inputs can exceed MAX_INPUT_BYTES by the metadata size (up to about 2 KB) without an 'oversized' refusal. RenderInputsV2.links would hold dicts rather than LinkSpec, and approval_counts a list rather than a tuple, so in-process equality or isinstance checks on v2 inputs differ from the decoded wire form. Building with msgspec.structs.asdict, and checking the size after building, avoids both.

8. `src/heterodyne/admind/daemon.py:993`: The post acknowledgment echoes the request's presentation and metadata rather than reading back the persisted row, so the CLI's acknowledgment check only proves the daemon parsed the request, not what it stored or rendered.

   Failure scenario: If the persisted management JSON or render_version ever differs from req (an encoding change, or a future normalisation in insert_ask), the CLI still accepts the acknowledgment because it compares reply.management with req.management. Building the reply from management.stored(self.store.ask(row.ask_id)) would make the acknowledgment reflect the bound row.

9. `src/heterodyne/admind/management.py:23`: The audit-URL, hex and poster regexes re-implement validators that already exist: the links blob form (links.PINNED / _OWNER / _REPO / _PATH), binding.HEX_KEY and asks.POSTER.

   Failure scenario: _AUDIT is a looser copy of links' pinned blob form: an owner may start with '-', and the path pattern [^?#]+ is broader. The poster regex at line 106 duplicates asks.POSTER, and _HEX duplicates binding.HEX_KEY. Three copies of the same validation can drift. Exposing a blob-only pattern from links and reusing asks.POSTER and HEX_KEY removes the duplication.

10. `src/heterodyne/admind/daemon.py:955`: Redundant work: management_check is re-run in post_ask although asks.check (daemon.py:872) already runs it before dispatch, and management.stored() JSON-decodes and validates the metadata 3-5 times per render or binding.

    Failure scenario: question_card, card_chunks, full_chunks and checked_card_chunks each call stored(), then lines() calls it again. stored_inputs calls stored() and validate_stored_request calls it again. parts(details=True) packs the card twice, and insert_ask does an INSERT followed by an UPDATE where one INSERT with the three columns would do. Each binding read therefore repeats decoding and validation, and the duplicated refusal string in post_ask is dead code.

On your stated evidence (37 RED / 111 PASS, 2273/2310, types 6/6, Ruff 12/12, format 312/312): I did not re-run any of it, so this review neither confirms nor contradicts those numbers. It makes no activation or deployment claim, and the quality gate stays OPEN and unwaived.
