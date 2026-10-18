# Plan6A structural content framing amendment, incorporated revision

Codex author. ONE actual Claude Opus HIGH design review returned REVISE10 at ccb8e950048d2a911e68e88c971b4fecff902cdf; incorporated once below. No second review or validation was run or requested; no reviewer APPROVE is claimed. Genuine separate human Marmot approval is required before implementation. Code review resumes at actual ordinal9 after the eight retained reviews, using the approved critical-flaw wording. Nothing here authorizes activation or deployment.

## Exact parent and changes

The parent is the published product repository Git object 356a1f3e36a1d363b0a183069e07fa846d1fe4bc, docs/designs/2026-10-16-plan6a-pure-marmot-presentation-d4.md. Its byte-exact source is retained beside this file as 2026-10-16-plan6a-approved-d4-source.md. Human approval btq-ns85p, Liam 2026-10-16T21:08:03Z, Marmot marmot:id:051af1e55b2a, digest36d64bfc2d5eed55b6894b317034b1bd0eee36bdc090c08898c13c4aa7ef3548, admitted native ADR05ddf59853c1c2735199897e47df9b0d825565e3; that external native ADR is not a product Git object. Historical UNREVIEWED/cap language in the retained parent is superseded by that genuine decision and current one-HIGH-review policy. The first review was accidentally given no parent copy; it independently resolved the correct published356a1f3 object. This incorporation supplies that exact copy; no second review is implied.

Replace ONLY parent §4.2 question template strings, §5 protected approval/question templates and protected prose/effect packing, and §6 protected reserved-prefix withholding, as enumerated below. Parent authority, input DTO bounds, classification, sources/freshness, native digest, five mandatory approval parts, complete evidence, privacy and unsupported-reference rules remain. No arbitrary reference I/O, service import, database, native action, send, timer or new admin command is added. Ordinary read layouts remain unchanged and have NO reaction/decision authority: later ingress must bind reactions to an actual protected card identity, never text resemblance.

## Display model and structural rule

All owner content in protected cards, including exact effects, inline evidence, titles, question text, context, exclusions, rationale, answer instruction and descriptions, is emitted only as typed content segments on its own line, with generated strong-LTR prefix `Text │ ` on EVERY physical line. An empty content line is exactly `Text │` without trailing space. A line whose first owner grapheme starts with any Unicode mark category Mn/Mc/Me is withheld, rather than allowed to combine with the gutter. Existing bidi/control/default-ignorable rejection remains; the ASCII T fixes paragraph base direction without adding an unsafe invisible character. User-provided margin glyphs cannot replace the outer generated gutter.

Generated labels/validated metadata, generated content gutter and owner source spans have distinct typed provenance; never infer their origin from the string prefix. No owner literal shares an unframed generated label line. Delete _CARD_CONTINUATION/forbidden_continuation and _card_frame_start protected-prefix blacklists as an authority defense: owner phrases are allowed only if structural framing and all independent privacy/unsafe checks pass. Adapt their tests to assert complete safe framing, not a specific refusal. This does not relax secret, identifier, reserved ordinary multipart or ingress validation.

Future integration must admit a literal-text client display with hard LF and no Markdown/HTML reflow. This is a deployment precondition with recorded client evidence, NOT a new environment lookup or mutable capability in the pure API. Unsupported client display blocks integration/send, without fabricating a new RenderPolicy field or Withheld reason. The pure library emits literal text only. Narrow phones may still soft-wrap long physical lines without repeating the gutter; that visual ambiguity is an explicitly accepted residual risk, not a claim that 120 scalars fit every phone. Only actual bound card identity/native digest authorizes reactions. Golden/visual integration evidence must include narrow phone, Hebrew/Arabic and combining-mark cases before deployment.

Exact effect lines also receive the gutter, with no Markdown fence or fence capability exception. This explicitly replaces §5's byte-exact fenced display and §6's longer-fence exemption for protected effects. The stored exact effect/digest remains unchanged; display copy/paste includes generated gutter and therefore is NOT an executable byte-exact shell snippet. Do not advise executing copied display text. Typed effect public-hash exceptions apply only to original effect spans; framing grants no exemption. Every original byte remains recoverable from the typed source segments, not by blind prefix removal from displayed text. This availability/usability tradeoff is part of human approval.

## Normative templates

Each quoted item below is a complete generated physical line; each <content X> expands to one or more guttered lines under the packing rule. Join lines with exactly LF, no extra blank line and no trailing LF. Every header, label, mention, gutter, content scalar and LF counts toward both mirror budgets. Control adds the configured atomic mention as an independent line immediately after the first header; workstream adds no mention. Long headers follow the atomic token rule, never split identifiers; otherwise withhold the pair. Pair differs only by the mention.

Approval lines, in order:
1. `Approval` (or `Approval (design)` / `Approval (execution)`) followed by one ASCII space and validated bead slug.
2. Optional control mention line.
3. `What:` then <content title>.
4. `Effect:` then <content exact_effect>, each logical source line atomic, no wrapping.
5. `Excludes:` then <content exclusions>.
6. `Why:` then <content rationale>.
7. `Context:` then <content context>.
8. Zero or more complete evidence blocks in supplied order as defined below.
9. `👍 approve · 👎 deny · ❤️ always · reply to steer`.
10. `Context digest: <digest12>` with the validated bound digest.

Question lines, in order:
1. `Question`.
2. Optional control mention line.
3. `Title:` then <content title>.
4. `Context:` then <content context>.
5. `Question text:` then <content question>.
6. Choice variant: for each supplied label, `Choice <label>:` then <content description>, in input order; optional `Recommended choice: <label>` only when it names a supplied validated choice; then `Reply with a choice label.`. Literal yes/no uses exactly yes and no, and recommended-no is exactly `Recommended choice: no`.
7. Plain variant instead: `Answer instruction:` then <content original answer_instruction>. No reaction legend or guessed recommendation.
8. `Context digest: <digest12>`.

Evidence classification is exhaustive for protected cards: every Inline emits generated `Evidence:` then guttered owner title, generated `Evidence text:` then complete guttered full_text. Every BeadContext emits generated `Evidence: <validated slug> · <digest12>`, then its complete File/Inline detail using these same rules. File/Commit/PullRequest/Range emit generated `Evidence:` (and guttered title only where the DTO actually supplies one), then each typed generated URL on an independent atomic line. PR retains separate pinned-head URL. Range adds generated `<full-base>..<full-head>` as an atomic validated metadata line. Unsupported OperatorDoc/forge still withholds. URLs remain the parent512-scalar exception; all other generated/owner physical lines are at most120. No arbitrary owner title may be interpolated into Evidence: or metadata. No invented details-view command or retrieval promise exists: complete source/native ask remains the owner's responsibility.

## Exact wrapping, bounds and availability

Gutter length is7 Unicode scalars; content width is113. Wrap eligible protected prose greedily at the last ASCII space within113, KEEP that space in the preceding source segment, and insert a generated soft-break LF. If none exists, hard-split only ordinary human prose at a grapheme boundary within113. Atomic labels/URLs/typed grants and exact effects never split; oversized atomic content withholds the whole pair. A trailing source space is retained even if a client visually trims it. Logical source LF and generated wrap LF are different segment tags, so tests reconstruct source by concatenating owner segments INCLUDING source LF only. Leading/trailing spaces and empty logical lines remain source data; the rendered empty-line spelling above is generated. No normalization, silent omission, partial card or broad exception expansion.

Whole original literals are screened before splitting; final document is screened again with exact remapped spans. All original public/syntax/template grants remain limited to their source text. Protected cards stay <=36 physical lines and <=min(policy.max_chars,3200) scalars, default2000. Added labels/gutters reduce capacity. With one source line per field and no evidence, approval is13 workstream lines/14 control lines; one File evidence adds2 (15/16), versus parent12 with one evidence. Plain question is10/11; choice question with N one-line choices is9+2N workstream lines, plus optional recommendation and optional control mention. Inline evidence adds4 minimum. Each nonempty owner line costs7 extra scalars; empty costs6; each field previously inline now requires an extra LF/label line. Exact effects previously up to120 now only113 source scalars, so widths114..120 newly withhold. Existing near-line/character budgets and many wrapped choices/inline documents newly withhold even though native asks remain valid. Human approval must disclose these availability and copy/paste changes explicitly. Never sacrifice headings/content to fit.

## Acceptance and rollout

Pure disposable tests cover all templates and source fields including all LF/soft-wrap/empty/trailing-space boundaries, exact effect with fake backtick fences and generated labels, NBSP/EN spacing, Cyrillic/fullwidth/math confusables, Khitan marks, arbitrary user gutters, RTL first strong letters, initial combining marks, all evidence unions, URLs120..512, long atomic labels, choice yes/no/no-recommendation and mention boundary. Assert literal source recovery through typed provenance, complete framing, digest stability, no source deletion, no broad exceptions and fixed complete Withheld pair if needed. Commit literal independent golden bytes; never regenerate expected results from implementation.

Run complete affected presentation/redaction/classification tests, source AND full-project type checks, Ruff/install/hooks and actual unwaived quality checks. Rebuild maximum-budget workloads for this expanded layout: 1000 iterations EACH for ASCII safe/adversarial and representative worst-budget non-ASCII (CJK, RTL and accepted emoji), recording CPU, input/output sizes and exact source. EACH must satisfy p99<=250ms; report maximum separately, not as the acceptance threshold. Prior ASCII p99=220.179067ms/max269.648958ms is historical only. If the new workloads fail, HOLD and propose the parent's reviewed bounded scanner correction; no hidden budget increase or screening weakening.

Merge only after actual next-ordinal Claude critical code review and full hosted CI. Deployment requires separate Marmot card linking this immutable amendment and parent, display/client admission evidence and all deviations; no live send, runtime admission or72-hour clock is authorized here.
