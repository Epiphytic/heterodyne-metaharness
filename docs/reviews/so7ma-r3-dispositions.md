# so7ma final R3 author dispositions — d4 UNREVIEWED

Actual final report: REVISE15; all three issued rounds consumed. These are author corrections, not a review verdict or approval. Historical files are unchanged. No fourth review was invoked. All new questions/consent exclusively Marmot/admind.

## 1. Actual finding

Every daemon startup 'atomically invalidates all prior-epoch grants/permits', which contradicts §2's promise (line 120) that one deployment decision may approve both installation and grant, with activation waiting for the deployment receipt; the design never says whether a registered-but-not-yet-active grant, or its delivery and A events, survive the restart that the approved deployment itself causes.

**Disposition:** Accepted: §2/§4 remove combined deployment/grant and every first-install import/baseline exception. Install/restart is deployment-only; a separate real grant question after healthy new epoch is mandatory. Pending and active authority both invalidate at restart.

## 2. Actual finding

The delegated reclaim keeps the native Handoff 'note+assignee' write, but at 6fe5759 Handoff's note_line is 'claimed by {worker} at {stamp}' plus 'handoff-request={nonce}', with a wall-clock stamp and a random 12-char nonce produced at execution prep; the signed descriptor's after_hash and predicted progression cannot bind those bytes, and the design makes only the audit comment deterministic (R2#13), not the note line.

**Disposition:** Accepted: §2/§7 freeze claim_stamp and claim_nonce inside the signed reclaim delta; exact native note bytes derive only from those fields and receiving worker. Actual dispatch time stays in private journal; no fresh runtime note generation.

## 3. Actual finding

The closed executor/settle enums (prepared…settled, intent_not_dispatched; settle outcomes verified_exact_after/verified_no_intent/verified_not_dispatched/unknown) leave out states the text itself uses or needs: held_unissued (line 416), the 'partial state' after an add-then-revoke (line 487), and verified-main-effect-with-audit-unpermitted, the delegated version of Z's held_reclaim_completion/post_effect_conflict.

**Disposition:** Accepted: §7 separates closed operation and step enums. Verified prefix, missing audit, reclaim completion, local cleanup and post-effect conflict have distinct held states. Settlement is per-step; new exact Marmot reconciliation cannot replay a known main effect or unlock old suffix automatically.

## 4. Actual finding

The design requires the whole combined card to fit 36 lines/3200 characters, yet the mandatory fixed disclosures alone are about 20 items: same-UID trust, serial-suffix blocking, restart invalidation, clock holds, the 5s limit, both policies and eligible approvers, exclusions, grant ID/prefix/link, design link, release closure, deviations, first-install deployment-A versus fresh-A instructions, pre-install B/!answer instructions, '!grant unavailable' and the clarifying-question warning. Z d3 alone spends a full 36-line card on one operation's endpoint purpose, 128-char excerpts and 4×160 inventory. The design leaves feasibility to an unwritten future fixture.

**Disposition:** Accepted: §4 gives a complete 33-line ordinary removal example plus exact seven-line reclaim substitution, including native framing and immutable links. Static counts are checked in author evidence and manifest; arbitrary oversized facts still refuse without partial post.

## 5. Actual finding

The design relies on 'the separately deployed Z classifier', but Z d3 (30a904b lines 63, 90, 200) accepts ordinary_work only from the per-operation card's attestation, with a full 64-hex facts digest shown through a dedicated typed renderer field. This design shows only a 12-hex prefix plus a link and calls the grant card an 'explicit finite alternative', but it specifies no Z classifier input or amendment that accepts a grant-card attestation.

**Disposition:** Accepted: §1 pins exact proposed Z d4 bytes and specifies the closed per_request/delegated classifier authority union. Full hashes, daemon receipt, visible complete facts and native gate override remain mandatory. Both unresolved designs require real compatibility approval/deployment; no installed acceptance inferred.

## 6. Actual finding

The design says 'All body/choices/links use existing checks; no secret-scanner bypass', but the existing AskPost checks refuse redaction only for choices and links. A question card body is silently redacted in asks._shown/_packed (login dirs, sk-/gh tokens, 64-hex runs, per-chunk re-redaction), so the bytes the operator sees can differ from the render manifest that import and activation bind.

**Disposition:** Accepted: §4 requires no-op whole-card and every final chunk redaction, untruncated source reproduction, exact stored/rendered/delivered bytes and current redactor/config identity. Any mutation refuses posting/activation; no typed hash bypass.

## 7. Actual finding

With recommended B, existing asks.map_reply also maps every verbs.APPROVE_WORDS reply ('approve', 'approved', 'yes', 'y', 'ok', 'okay', 'lgtm') to B. The design discloses only that thumbs-up declines, and the Consent acceptance row doesn't test approve-word replies.

**Disposition:** Accepted: §4 explicitly lists all seven approve words as B, warns on card that approve/yes/ok and thumbs-up decline, and adds future acceptance cases. Only literal A grants; contrary history requires a new question.

## 8. Actual finding

After installation, only '!grant revoke' and 'B/withdrawal in the activation thread' are put ahead of answer_refusal. The design itself teaches '!answer ASK B' as the withdrawal route (line 256), but that command, and possibly reactions, still go through the ordinary bang_answer→answer_ask→answer_refusal path with its status, 50-answer and 64,000-char limits.

**Disposition:** Accepted: §5 routes all authenticated thread/reaction/!answer contrary inputs through transactional withdrawal before bang_answer/readiness/terminal/count/byte refusals. Unknown auth holds; no ordinary refusal may leave a grant silently active.

## 9. Actual finding

The first-install card must say '!grant is explicitly unavailable', while line 319 requires 'the activation card' to give exact revoke instructions. The post-import acknowledgement is specified to carry only the baseline and fresh-reply instruction, not the now-working revoke syntax.

**Disposition:** Accepted by simplification: §2/§4 remove old-daemon activation entirely. Every grant card is posted by the installed new daemon and carries working priority revoke syntax.

## 10. Actual finding

The step plan says reclaim is 'one atomic update plus its bounded existing audit'. That contradicts lines 472-479, which replace the legacy dynamic audit with canonical 'management-delegation-v1' bytes. Handoff.finish's existing comment embeds the ask, operators, observed after-state and steps, none of which can be predicted.

**Disposition:** Accepted: §7 enumerates only canonical delegated audit; explicitly forbids legacy runtime Handoff note/audit generation and removes bounded-existing-audit reuse from step plan.

## 11. Actual finding

The first-install path needs a large one-time mechanism used exactly once: an offline candidate renderer, import that reproduces the stored ask, an immutable answer baseline, old-daemon B tombstone handling, answer-limit exhaustion handling, a new acknowledgement key/thread mapping and an ack-delivery ordering event. Its human cost (one extra literal A) equals what a post-deployment standalone grant card from the new daemon would cost, and that card would get native delivery ordering for free.

**Disposition:** Accepted: §2/§4 remove offline renderer/import/baseline/ack-key and first-install old answers. Separate post-install grant question uses native new delegation delivery ordering and is an explicit human decision.

## 12. Actual finding

The design requires 'its genuinely approved/deployed compatible revision' of Z, but never defines compatible or lists the exact Z d3 contracts it depends on (format2 states, held_unissued, manage --inspect/--settle/--predecessor grammar, associated-delta normalization, classifier inputs). Z's d4 is unreviewed, and all its rounds are used.

**Disposition:** Accepted: §1 enumerates exact Z d4 journal, grammar, role schema, binding, delta normalization, locks, states and budget contracts plus delegated extension. Changes to these require design reconciliation, not author-only compatibility judgement.

## 13. Actual finding

The combined question offers only A = deployment plus grant, B = decline everything, so the operator can't approve the deployment while declining the delegated grant, even though question forms allow up to 6 choices.

**Disposition:** Accepted: installation card is deployment-only. The later grant decision may be declined independently without undoing deployment. The extra actual question is explicitly disclosed as changed design.

## 14. Actual finding

The Status operation (blocked/unassigned to open/unassigned, which makes the task claimable) has no derived step plan in §7 (line 469 lists only dependency and reclaim). Unlike the Dependency row, it doesn't restate d2's rule to verify the metadata pin and native approval edge before a task becomes claimable.

**Disposition:** Accepted: §7 defines status update+canonical audit and repeated metadata/native approval checks; planning review-budget rules retain unknown/exhausted REVISE HOLD. No owner/pause/pickup bypass.

## 15. Actual finding

The header reads as if both R1 (42) and R2 (24) reviewed 251d6b5; per the inputs manifest, R1 reviewed fb8ce83. The Consent acceptance row (line 522) still says 'nonce-less' mapping, left over from the withdrawn 'A <grant_id>' syntax, although a nonce-less literal A is now the granting syntax.

**Disposition:** Accepted: header binds R1/R2/R3 to their actual separate heads and counts3/3 issued. Consent fixture explicitly makes literal nonce-less A positive and nonce-bearing A negative. D4 remains unreviewed.
