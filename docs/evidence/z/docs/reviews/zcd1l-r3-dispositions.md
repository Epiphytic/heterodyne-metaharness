# Z d4: ordered actual final R3 dispositions (UNREVIEWED)

Final R3 reviewed30a904b10c30a386c5867e6d7b8cd8f494b33ca0 and returned REVISE16. All3/3 rounds are exhausted. These are author fixes/limitations, not reviewer acceptance; d4 is UNREVIEWED and grants no code/deployment/recovery authority. No fourth round without genuine explicit Marmot cap exception. Original d3/R1/R2 bytes are preserved. All questions, approval and recovery consent remain Marmot/admind only, never terminal/chat.

[Proposed d4](../designs/2026-10-16-management-design-task-recovery-d4.md) and [inputs](zcd1l-management-d4-inputs.json).

## 1. FIXED AS EXPLICIT NEW PREREQUISITE

> Installer --deployment-ask must check the stored question body, choices and design links through 'existing supported ask-get', but admind's AskView returns only the summary (ask_id/kind/status/title/bead/digest12) and the answers, with no body, choices or links.

**FIXED AS EXPLICIT NEW PREREQUISITE.** §6 removes false existing ask-get body binding. Proposed same-UID read-only ask.binding.v1/admind ask binding exports bounded stored body/form/links/full canonical render hash/delivery/complete answers/policy read freshness. A separate reviewed/deployed prerequisite must exist before the management installer; its own deployment uses existing supported controller/operator Marmot verification, not self-dependent RPC or title-only automated fallback. Missing seam refuses; no implementation authority exists.

## 2. FIXED STORAGE ISOLATION

> Format2 shares the lane's handoff.json path, and its top-level discriminator makes the unchanged base Handoff.load refuse ALL format2 data, including done/archived records, not just active ones.

**FIXED STORAGE ISOLATION.** §5 places all format2 requests under management/request.json/history; never handoff.json. Active interlocks protect supported routes; durable terminal archive clears active marker, leaving normal Handoff untouched. Legacy format1 stays on retained old route. Rollback refuses active records and terminal-only format2 cannot poison regular handoff.

## 3. FIXED FINITE PER-STEP BUDGET

> Fresh authority is required before every native write including audit, yet only 3 refresh/preflight attempts are allowed per apply invocation, and a later apply 'may retry only unissued exact current request', which contradicts the replace-row resume rule.

**FIXED FINITE PER-STEP BUDGET.** §5 permits at most3 refresh attempts for each exact next unissued step, maximum7 declared effect steps/21 attempts per explicit apply. A later explicit apply resumes verified prefixes only; never reissues issued/unknown. Both native writes and local fence/reservation/current effects require current authority, journal-only observation does not. No infinite automatic refresh.

## 4. ACCEPTED CAPABILITY CHANGE DISCLOSED

> The protection rules forbid removing any approval/confirm/question edge for implementation tasks even with the operator card, removing the approved d2 capability 'Operator authority can correct obsolete/wrong protected edges... Removing a mistaken extra approval edge'. §1 does not disclose this.

**ACCEPTED CAPABILITY CHANGE DISCLOSED.** §1 explicitly states narrower ordinary management behavior for extra approval/confirm/question/protected edges, correcting the false unchanged claim. Primary implementation pin validity remains unchanged; genuine mistaken-protected-edge repair requires separately exact OPS scope, not this classifier card. Acceptance acknowledges changed fixtures; this proposed narrowing itself still needs genuine human design approval.

## 5. FIXED TRUSTED TRANSFER CONTRACT

> Review-history files resolve only beneath the enrolled run's own management-evidence directory, and the ledger is to 'transfer through normal claim and handoff evidence', but unchanged Queue.claim takes no evidence and the previous holder's records live in its own run.

**FIXED TRUSTED TRANSFER CONTRACT.** §3 adds exact origin_run to retained file schema, resolves only native-evidence-enrolled source runs, verifies hashes before immutable provenance-preserving copy, and stores per-task/lineage manifest under fixed workstream evidence root. Missing original/retained provenance HOLD. No Queue.claim parameter is invented; management performs transfer and docs/PICKUP.md explicitly instructs trusted consumers. Enforcement remains cooperative, not universal hostile-launch control.

## 6. FIXED PHONE DISCLOSURE BUDGET

> The card must disclose the complete review source inventory (up to 32 files with sha256, plus native comment IDs/hashes) and the decisive endpoint excerpts (up to 16 entries x 4 x 128 chars), but the body budget is 3200 chars and 36 lines.

**FIXED PHONE DISCLOSURE BUDGET.** §4 shows decisive issued/completed/unresolved counts, verdict/revision/remaining and source kinds/counts; full repetitive filenames/hashes remain in exact descriptor/private evidence rather than phone inventory. Endpoint display is operation-specific, up to target/removed/added, with decisive purpose/protection. Typical six-record/three-review history must fit; pathological material facts still refuse. Full digest authority comes from immutable linked artifact and stored-card binding, never12-hex prefix.

## 7. FIXED NO ORPHAN CLEANUP CLAIM

> held_unissued settle archives as aborted_no_effect with 'no issued intent', but reclaim records in the fenced/reserved phases have already written a holder fence and a parent reservation, and settle is only allowed to append journal transitions.

**FIXED NO ORPHAN CLEANUP CLAIM.** §5 held_unissued settle may call aborted_no_effect only if no local management effect remains as well as exact native no-intent proof. Existing fence/reservation means held_local_cleanup; observational settle does not delete it. Genuine current original authority or new predecessor Marmot card must authorize explicit cleanup effects and readback before another ordinary request.

## 8. FIXED EXPLICIT REOPEN REFUSAL

> d3 no longer says a planning reopen refuses when the review history is exhausted-REVISE or unknown (R1.6 promised 'Unknown/exhausted history cannot reopen'). It only says reopen 'is no authority' to review.

**FIXED EXPLICIT REOPEN REFUSAL.** §3 planning status blocked→open refuses unknown history and exhausted REVISE. Exact current final APPROVE at cap permits publication-only with retained ledger/no model invocation. This closes ordinary ready-pickup exposure without claiming global adversarial launcher enforcement or inventing another review budget.

## 9. FIXED NATIVE-ONLY SCOPE BOUND

> Pinned-planning approval scope must be bound 'using its retained exact ask/artifact', but the artifact resolver was removed (R2 #24) and ask-get returns no body, so there is no defined input for this scope proof.

**FIXED NATIVE-ONLY SCOPE BOUND.** §2 defines finite native approval description/design/acceptance/notes/comments, genuine decision metadata, exact target edge/pins and explicit task authorization as scope evidence. No removed artifact resolver or unavailable ask body is presumed. Ambiguous native scope holds; exceptional clarification needs genuine Marmot/native inspectable authority, not metadata invention.

## 10. CLARIFIED REAL COMPLETENESS LIMIT

> The design claims the module 'compares the complete record/reference inventory', but it only enumerates target/endpoint fields and comments, so an approval that names the endpoint as its authorization subject only in the approval's own text is invisible if the caller leaves it out of protection_records.

**CLARIFIED REAL COMPLETENESS LIMIT.** §2 limits automatic reference inventory to enumerated target/endpoint native fields/comments. It cannot discover unrelated approvals elsewhere; authenticated principal attests no omitted protection record using exact supplied sources. Known outside protection overrides ordinary; uncertainty refuses. No misleading claim of global reverse-text/NLP proof remains. Trusted factual-attestation residual is explicit.

## 11. FIXED ROUTING CONTRADICTION

> One paragraph says that after an authentic decision 'journal settle' marks superseded_unknown_reconciled, while the predecessor paragraph says only an authenticated --apply on the successor archives the predecessor and that --settle 'cannot approve or perform this transition'. Settle also forbids --ask.

**FIXED ROUTING CONTRADICTION.** §5 only authenticated successor --apply may archive/link unknown predecessor. Observational --settle cannot accept that consent or perform supersession. Original unknown evidence remains; no repeat of predecessor writes.

## 12. FIXED ACTIVE DIAGNOSTIC OUTPUT CONTRACT

> The legacy-diagnostic override only covers workstream-manage's own output, but the shim's error 'next' points at 'controller doctor', and the pinned controller doctor prints 'obtain scoped operator direction in the active conversation or terminal'.

**FIXED ACTIVE DIAGNOSTIC OUTPUT CONTRACT.** §6 shim replaces every management error/next/resume hint with its own safe inspect/settle/Marmot route and never points consent recovery at controller doctor. Unchanged doctor may separately supply read-only health only. Pinned controller source is untouched and its terminal language is never inherited as active management guidance.

## 13. FIXED COMPLETE HOLD NOT EXCERPT

> Reason/hold inputs over 128 chars must refuse before post, while §3 requires quoting the current native blocking note. It is unclear whether a caller-chosen excerpt of a longer note counts as a 'silent truncation'.

**FIXED COMPLETE HOLD NOT EXCERPT.** §3/§4 require complete literal native hold/source up to512 characters, reason up to256. A200-character actual hold fits; longer material/ambiguous holds refuse rather than caller-picked excerpts. Broader clarifying scope remains through genuine Marmot, never editing away the old restriction.

## 14. FIXED SERVER-BOUND BYTES

> The design says it validates the 'same real ask/card/full digest', but admind stores and returns only title and digest12; the full 64-hex digest is checked only against the local record, never against the real ask.

**FIXED SERVER-BOUND BYTES.** §4/§6 full facts are in immutable signed descriptor, whose exact commit/path link and rendered decision body are read back through proposed daemon binding. Installer/apply compares exact stored representation with reproduced expected bytes and verifies full descriptor/facts hashes. Title/nonce/digest12 is insufficient. Existing daemon has no such endpoint; explicit prerequisite is mandatory, no local tampering equivalence claimed.

## 15. FIXED TIMED CRITICAL SECTION

> Every step reacquires the parent lock and rereads all required facts (target, comments, up to 16 endpoints, git worktree inspection, review inventory hashing up to 32x256KiB) inside a 5-second window.

**FIXED TIMED CRITICAL SECTION.** §5 moves complete Git inspection/immutable evidence hashing outside parent locks before authority refresh. Final locked compare revalidates file identities/stat guards and all mutable native/worker/endpoint/record facts; drift forces full reinspection before another attempt. Retained immutable reports use exact file guards rather than8MiB rehash in the locked window; required mutable Git predicates still recompute and may remain slow. Slow final Git/native reads therefore still safely HOLD; this measured availability risk is disclosed rather than falsely solved by a weaker freshness oracle.

## 16. FIXED COMPOSITION BOUNDARY

> Because bin/btq is pinned, the design overrides nearly every phase-dependent Handoff method in an extension loaded through bin/btq's globals(), creating a parallel state machine that silently depends on base internals.

**FIXED COMPOSITION BOUNDARY.** §5 replaces format2 Handoff inheritance/phase overrides/globals mutation with independent management-owned journal dispatcher composing pure native builders/readers and a scoped exact mutation boundary. Pin exact base hashes, inventory composed calls and test regular handoff after done. Unknown future base version refuses; no claim of universal internal API stability. Refactoring root controller remains outside byte-identical baseline scope.
