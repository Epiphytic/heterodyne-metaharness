# PV second HIGH author dispositions

Actual report: **no critical findings**, ten findings. Full raw is retained byte-exact in [pvbxr-r2-high-actual.md](pvbxr-r2-high-actual.md). No invented reviewer APPROVE/REVISE token. This author repair is unreviewed; next actual code review is ordinal 3 MEDIUM. Design/human authority and original R1 history remain unchanged. No deployment, queue-child acceptance or X84 composition claim.

## Finding 1 — ADDRESSED

1. `src/heterodyne/admind/management.py:24`: The _RESERVED anti-spoof screen only matches exact ASCII prefixes, so lines that look just like the renderer's own lines still pass literal() and appear on the readable APPROVAL card.

   Failure scenario: Each of these body lines passes asks.check and post_ask: 'Option A = Leave unchanged and held (recommended)' (the line starts with 'Option', so the bare-letter '[A-Z]\s*=' rule does not fire); 'A ＝ Leave unchanged and held' (fullwidth U+FF1D equals sign); '👍 = leave held; 👎 = perform'; 'A / no / 👎 = leave held' (the legend's 'A /' form is not reserved); 'Audit\u00a0details: https://github.com/attacker/r/blob/<40hex>/x' or 'Audit  details: ...' (NBSP or a double space, because the regex requires exactly one ASCII space); and a Cyrillic 'АPPROVAL:'. The operator sees fake option, reaction-legend or audit-link lines above the real ones and may react 👍 or reply A believing it means 'leave held'. The reserved list is also hand-copied rather than derived from the strings lines() emits, so it has already drifted (the 'A /' legend line is missing).

Author: V2 material facts now require printable ASCII (body newline only). Case-insensitive option-letter/equals or slash prefixes and flexible Audit whitespace refuse; unsupported non-ASCII and confusables refuse unchanged. No lossy normalization or exhaustive Unicode blocklist.

Regression/evidence: `test_unsafe_facts_refuse_before_storage`. Exact finding SHA-256: `f8243122bca7fa1c932db67a50dd324dd82b070c39d6ecf5a421c3ae98c8a84b`.

## Finding 2 — ADDRESSED

2. `src/heterodyne/admind/management.py:55`: The R1 #6 repair blocks only the single code point U+0336. Other overlay and enclosing combining marks, and HTML character references, still change how literal text is displayed.

   Failure scenario: 'After: o\u0335p\u0335e\u0335n\u0335' (U+0335 short stroke overlay), U+0338 long solidus overlay, U+0332 combining low line, or 'A\u20e0' (U+20E0 enclosing circle backslash, category Me) all pass literal() and redact(). '&#65; = Leave unchanged and held' also passes: '&' is allowed, '#' is not at the start of the line, and the line does not begin with a letter followed by '='. A Markdown/HTML-rendering client shows it as 'A = Leave unchanged and held'. Either way the displayed text differs from the bound bytes, against the 'material literal displayed unchanged / no Markdown' contract.

Author: Printable ASCII excludes every combining/enclosing mark and non-ASCII confusable. Ampersand/entity and HTML syntax refuse; no rewriting of displayed facts.

Regression/evidence: `test_unsafe_facts_refuse_before_storage`. Exact finding SHA-256: `a72c3d0e2aa01c451994e05ccc1f931e149fb0a8db6c9ac48d27c60d9703fe01`.

## Finding 3 — ADDRESSED

3. `src/heterodyne/admind/binding.py:268`: stored_inputs now runs validate_stored_request before the MAX_INPUT_BYTES check (it used to run after), which changes which refusal code legacy v1 binding reads return.

   Failure scenario: Take a legacy question or merge row whose canonical RenderInputs exceed MAX_INPUT_BYTES and that also fails asks.check or has redactable context. Before this change `ask binding` returned code 'oversized'; it now returns 'invalid-state' or 'redacted', and it runs full asks.check plus redact(context) on the oversized row first. This contradicts the claim that the v1 default path is unchanged.

Author: Construct exact complete inputs, check canonical byte budget, then validate stored request. This restores legacy oversized precedence and retains the complete V2 metadata budget without serializing legacy inputs twice.

Regression/evidence: `test_legacy_oversize_precedes_invalid_request; test_v2_binding_final_budget_includes_metadata`. Exact finding SHA-256: `10aa87d6950bc9fdef9cab57d47525f92f6598f2a474898edbadcf14c0146184`.

## Finding 4 — ADDRESSED

4. `src/heterodyne/admind/daemon.py:1373`: autobump checks management validity before the due-time check, so one malformed v2 row is reported as skipped, and audited, on every autobump cycle forever.

   Failure scenario: After a tampered or partial metadata row exists with status 'open', every autobump_seconds tick appends ('<id>', 'management card refused') and therefore writes an audit 'autobump' record, even when the row is nowhere near ask_bump_hours due. The audit log grows without bound and real autobump records are buried. Moving the stored() check after the last_activity_at/due test, or recording the refusal once, avoids this.

Author: Due-time eligibility precedes management decode/refusal. Not-due malformed rows produce no recurring autobump audit; genuinely due malformed rows remain explicit skipped refusals without moving activity or sending.

Regression/evidence: `test_malformed_autobump_row_audits_only_when_due`. Exact finding SHA-256: `5e61719f113ef7541718fec7931c633d576eb9b3af30a6649d74c1c0bbebf494`.

## Finding 5 — ADDRESSED

5. `src/heterodyne/admind/management.py:129`: Body lines can include arbitrary unpinned URLs, which clients will autolink, while only the renderer's audit_url goes through PINNED_BLOB/links.why validation.

   Failure scenario: A body line 'Before: full audit at https://evil.example/approve-audit' passes literal() (no forbidden characters) and redact(). The client linkifies it beside the validated 'Audit details:' link, so the operator may follow an unvalidated, mutable link as the audit evidence. This bypasses the doc's 'Audit URLs must pass existing public-link validation' intent.

Author: Fact fields reject URL schemes, www forms and mailto/tel/data/javascript syntax. The separately typed, pinned, validated metadata audit_url remains the sole card link. Facts are refused, never sanitized.

Regression/evidence: `test_unsafe_facts_refuse_before_storage`. Exact finding SHA-256: `0a86e0fd897338514365043676233ed29b2d885181fc572a7716867b1ae07312`.

## Finding 6 — ADDRESSED_WITH_LIMIT

6. `src/heterodyne/admind/binding.py:544`: Refusals for v2 management asks are always returned as AskBindingReply with message 'ask.binding.v1 unavailable', even when the CLI asked for --version 2 on a v2 ask.

   Failure scenario: A v2 ask with tampered metadata, or a render-mismatch, read through `admind ask binding --json --version 2 ID` returns {result: refused, message: 'ask.binding.v1 unavailable', code: invalid-state}. A queue-child consumer that compares the requested version, as the doc requires, gets a reply naming v1 for a v2 request. That is ambiguous evidence about whether it hit a v1-only daemon or a refused v2 card.

Author: Every explicit CLI --version 2 refusal path now returns AskBindingReplyV2 and v2 unavailable; default V1 remains exact. This identifies the locally requested decoder/refusal domain, not daemon V2 capability. Existing socket request has no version selector. Only a complete returned EnvelopeV2 can prove successful V2 read; refusal never permits fallback or supplies consent evidence. No new request wire or peer capability claim.

Regression/evidence: `test_binding_cli_refusal_domain_is_requested_version`. Exact finding SHA-256: `5cd4a3983ddba07908fcdff91d4935c077f3bdd3f1e93e41cef5a0565dea861f`.

## Finding 7 — ADDRESSED

7. `src/heterodyne/admind/management.py:134`: The card legend groups 'deny' and 👎/❌ with B ('B / deny / 👎 ❌ = leave held'), but map_reply/map_reaction record them as 'none', so the confirmation does not match what the card said.

   Failure scenario: The operator replies 'deny' or reacts 👎, expecting B per the legend. answer_ask stores choice 'none' and replies 'Recorded for X: none of these (from deny).', which the card never mentions. A consumer that keys on literal B for 'leave held' sees a different native choice than the operator selected according to the card.

Author: Legend distinguishes literal B from denial words/negative reactions, which record none (none of these) and leave the request held. No V1 parser or native answer mapping changes.

Regression/evidence: `test_legend_distinguishes_literal_b_from_none_of_these`. Exact finding SHA-256: `389a069e149c8d33c5a68904315243418d41b84a1f2eafc2b71e5618e4b43456`.

## Finding 8 — OPEN

8. `src/heterodyne/admind/asks.py:151`: The v2 contract is enforced by two hand-written parallel validators, management_check (request side) and management.stored plus lines (row side), and they already check different fields.

   Failure scenario: management_check does not check digest or refreshed_from; stored() does not check title/body literals (lines() does), and the choices/recommended checks are duplicated in both. A future change to one (for example, allowing a link) silently produces rows that post but then refuse on render or binding, or the reverse. Building the candidate AskRow and validating it once through stored() plus lines() would give a single source of truth.

Author: Parallel request/stored-row validation remains explicit maintenance debt. A broad single-validator rewrite is deferred; existing negative/original tests stay intact. This is not a waiver.

Regression/evidence: `Existing presentation/R1/binding suites retained`. Exact finding SHA-256: `4b272380793fb3d52baac46e2f7d9b9429b17182ac01903688d02311d3ebe249`.

## Finding 9 — OPEN

9. `src/heterodyne/admind/asks.py:450`: Altitude: v2 is wired in through `if management.present(row)` special cases in seven shared asks.py functions, plus per-caller `except management.Refusal` blocks in the daemon, rather than one renderer/answer-mapper dispatch chosen per row.

   Failure scenario: question_card, card_chunks, full_text, full_chunks, checked_card_chunks, map_reaction and map_reply each branch separately, and post_ask, remind, autobump, ask_details and answer_refusal each catch or recheck separately. Any new shared card entry point (for example a future summary or reminder renderer) silently falls back to legacy semantics for v2 rows unless someone remembers to add the branch. Selecting a renderer object per row once would close that gap.

Author: Per-function management dispatch/caller refusal handling remains explicit maintenance debt; no broad renderer architecture refactor in this bounded repair. This is not a waiver.

Regression/evidence: `Existing presentation/R1/binding suites retained`. Exact finding SHA-256: `dd584e8b052cf326dcd66fd1feed18507a316f7f61e27e991f428c506c88bcf5`.

## Finding 10 — OPEN

10. `src/heterodyne/admind/management.py:155`: Repeated work on every render or post: lines() redacts the whole card, then parts() redacts the joined text and each chunk again, details re-packs the base card, and stored() JSON-decodes and regex-validates on every call (R1 #10 residual is still open).

    Failure scenario: One v2 post runs management_check twice (asks.check, then post_ask), stored() three times (lines, readback, ack) and redact() over the full card four or more times. Each reply or reaction calls stored() twice (answer_refusal, then map_reply/map_reaction), and each autobump tick decodes every v2 row. lines() already proves redact(text)==text, and chunk markers are fixed ASCII, so the later whole-card and per-chunk redactions are redundant. The INSERT followed by an UPDATE also remains.

Author: R1 F10 repeated redaction/decoding and INSERT+UPDATE remain OPEN UNWAIVED. Budget ordering avoids introducing an extra legacy canonicalization, but does not claim the broader work eliminated.

Regression/evidence: `Existing suites retained; quality report retained`. Exact finding SHA-256: `2c53de565f83d1066e0b36ad2e36e0383f710ae5d019961e305bc3bf8319013c`.

R1 F10 remains OPEN UNWAIVED. R2 F8–F10 are retained debt, not cleared findings. All pre-existing test files remain byte-identical to signed 72dd8. Static/quality limitations and actual named results are retained in the private freeze evidence. No live/native/model activity or application authority was generated.
