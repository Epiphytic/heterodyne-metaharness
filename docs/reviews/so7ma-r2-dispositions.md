# S d3: ordered actual R2 dispositions

Actual R2 reviewed `251d6b5fbb53ab2385195586c9a3dee22d76a6b4` and returned REVISE with24 findings. This is the author response, not reviewer acceptance. Actual R1’s42 findings/dispositions remain unchanged and historical; two issued rounds consumed, only final R3 remains. This docs-only revision ran no models, queue or live/runtime tests.

Primary code anchors: heterodyne07637745, asks.AskPost/_check_form/map_reply/map_reaction, daemon.answer_ask/answer_refusal and command ingress; queue6fe5759 Management._mutate_steps, Handoff.move/check_ask and Queue.bd_op. Proposed Z d3 is pinned as an uncommitted input hash; no deployed APIs are inferred. Exact source/report hashes are in [inputs](bounded-controller-delegation-inputs.json).

## 1. Supported existing form

> Consent rules ('no recommended activation reaction', textual `A <grant_id>` only) cannot be met by the existing question API used for the first-install card: a lettered form must have --recommended, and the old daemon's footer tells the human '👍 = A (recommended)'.

**FIXED.** §4 uses real A/B question with recommended B; existing thumbs-up therefore visibly declines. Literal A is the granting syntax; no unsupported no-recommendation form. An accidental decline conservatively tombstones, disclosed rather than misrepresented.

## 2. Exact answer mapping

> The design never says whether free-text, nonce-less 'A' or reaction answers from mapped approvers count as neutral or as a 'recorded non-A'; literal check_ask semantics count any choice != 'A' as contrary, including the required `A <grant_id>` reply itself.

**FIXED.** §4 explicitly retains conservative mapped-choice semantics: any recorded eligible answer whose choice is not A is contrary, including free text/unknown reactions/A+nonce. No claim A+grant_id maps A. Clarifying questions use a separate Marmot question; no new unanimity requirement.

## 3. Preinstall withdrawal

> On the imported first-install card, the revoke instructions shown (`!grant revoke ...`) do nothing before deployment. The old daemon rejects the unknown command and stores nothing linked to the ask, so a pre-deployment withdrawal is silently lost and the later import activates on the earlier A.

**FIXED.** §4 distinguishes versions: old daemon supports B reply or !answer ASK B with recorded acknowledgement, not !grant. Import reads entire contrary history/status and refuses exhausted/unknown history. New priority withdrawal handler applies only after installation.

## 4. Imported delivery ordering

> The rule that only answers recorded after delivery count, 'based on Store sequence ordering', cannot be checked for the imported card: the existing Store has no delivery event sequence, and answers can be submitted with `!answer` without the card being seen.

**FIXED WITH DISCLOSED FIRST-INSTALL COST.** §4 introduces a proposed ordered delivery/answer event stream for new daemon. Old Store ordering is not inferred from timestamps or unrelated seq columns: imported card records delivered proof plus immutable complete answer baseline, excludes old A, and requires fresh literal A in the same thread strictly after the verified post-install acknowledgement delivery event. Import or queued acknowledgement alone never makes an A eligible. No extra question/card, but one extra first-install consent message is disclosed.

## 5. Z endpoint consent facts

> Delegated dependency removal depends on the Z classifier, but Z accepts an ordinary_work endpoint attestation only when the per-operation management card visibly shows role, decisive excerpt, protected-gate facts and digests and its A decision covers them. The grant card renderer list does not include those facts.

**FIXED.** §3/§4 explicitly require grant card to show every Z decisive endpoint purpose/excerpt/protection inventory/facts binding. The exact full digest remains in the immutable signed descriptor and canonical local reproduction manifest; the card shows its exact commit/path link and only an informational12-hex correlation prefix, preserving the unchanged daemon’s redactor. This is the finite substituted card, not dropping Z’s purpose attestation or allowing gate waiver. Scope must shrink before signing when facts cannot fit.

## 6. Unknown supersession conflict

> Allowing a terminal 'unknown' journal to be archived as `superseded` contradicts the Z prerequisite, which says 'Unknown effects cannot be superseded'.

**FIXED VIA NEWEST PROPOSED Z.** §7 removes generic unknown archive and uses Z d3’s bounded exact predecessor-candidate Marmot reconciliation. Unknown evidence is retained and never replayed/declared absent. Z d3 is proposed/uncommitted, not approved/deployed; compatible real approval/deployment is a prerequisite.

## 7. Legacy issued-before-permit markers

> The permit can only be requested inside the bd_op override, after the legacy write-ahead markers (Management `steps.append({'state':'issued'})`, Handoff phase `moving`). Existing code swallows the override's exception and turns it into a permanent 'unknown, never reissue'. The design fixes resume only for Handoff `moving`, not for Management `issued`.

**FIXED.** §7 requires management-owned format2 override for dependency and reclaim reducers, separate from native legacy issued/moving and bd_op exception handling. Permit refusal before intent is held_unissued, not dispatched native_error. Full implementation must test no false unknown and all inherited legacy call sites.

## 8. No-intent reclaim cleanup

> When a reclaim's permit is refused before intent (revoked, expired, or 3 attempts used), the fence, reservation and lane-cap slot written by Handoff.move stay in place, and there is no defined way to release them: native Handoff.abort refuses in `moving`, and the design allows release only through 'existing authenticated management reconciliation'.

**FIXED.** §7 binds exact no-effect cleanup in original operation scope; under original locks, positive no-intent/non-dispatch and native prestate/local ownership proof may release only this request’s local fence/reservation/capacity/current artifacts. No legacy moving-abort, refund, new native audit or implicit work resume; uncertainty holds for Z reconciliation.

## 9. Closed state/outcome enums

> `intent_not_dispatched` is used but appears neither in the closed durable state list (line 323) nor in the closed settle outcome enum (verified_exact_after/verified_no_intent/unknown). The status enum also has no state for an abandoned or declined registration.

**FIXED.** §5/§7 include intent_not_dispatched plus verified_not_dispatched and abandoned/declined registration states. Positive live-executor proof differs from crash ambiguity. Intent means no automatic second invocation even when known not dispatched; safe local cleanup is not slot refund.

## 10. Failed prefix and suffix

> reserve() accepts only the 'exact next ordered operation', and each signed before_hash assumes every earlier operation succeeded. Nothing defines what happens to later operations when an earlier one is held, settled verified_no_intent or unknown.

**FIXED WITH AVAILABILITY DISCLOSURE.** §3 blocks all later ordered operations if any prefix operation is held/aborted-no-intent/expired/unknown/declined. No skip or incompatible predicted before-state. New exact remaining-scope descriptor needs real authority after safe settlement; grant card discloses serial suffix dependency.

## 11. Revocation visibility/readiness

> Permit preconditions do not require the revocation channel to be live. admind.authorised() denies inbound commands with no reply while membership is `changing`, the subscription is not `observing`, or the group is unverified, so a `!grant revoke` sent then is dropped while permits resume afterwards.

**FIXED.** §5 gives revoke/contrary intake priority before ordinary authorised readiness refusal; unavailable current authority retains bounded authenticated potential-withdrawal HOLD. Any observing/processing/membership visibility gap permanently holds grants, with no automatic permits on recovery. Reserve/permit/renew require healthy ingress/no pending withdrawal.

## 12. Old epoch and guard recovery

> Settling across daemon epochs and under a guard mismatch is not specified. Startup invalidates all prior-epoch permits, the executor 'holds on [epoch] changes', and 'RPC checks require matching finalized guard/journal heads; any crash/mismatch holds all grants'. The crash case that most needs observational settlement may therefore have its settle refused, and the authority and procedure for 'explicit recovery' are undefined.

**FIXED.** §5 allows observational old-epoch settlement after consistency, never new effects. §6 defines proposed read-only recover --inspect and exact current-policy Marmot-backed recover --ask/--expect to retain heads/records and create a new invalidating incarnation. Guard mismatch does not disable ordinary questions. Old grants remain invalid; native ambiguity separately uses Z reconciliation.

## 13. Audit bytes and prediction

> The design says the descriptor predicts the exact audit note/comment delta while reusing the 'bounded existing audit'. The existing audit text includes values unknown at signing: approver names from post-consent answers, observed after-state, steps/native_error and timestamps.

**FIXED.** §7 replaces delegated dynamic legacy audit with deterministic canonical payload from static signed grant/op/index/delta/reason. Names/timestamps/errors/observed state stay private receipt. No descriptor self-hash in predicted native bytes (avoids circularity); receipt binds both hashes. Z associated-timestamp rule handles native-generated time.

## 14. Withdrawal after ask limits/status

> A B/withdrawal reply in the activation thread goes through admind's answer_refusal. That refuses when the ask status is not open/answered, when 50 answers are reached or when 64,000 characters are exceeded, so a withdrawal can be 'Not recorded' unless the new transaction explicitly bypasses those limits.

**FIXED.** §5 handles mapped-principal contrary/withdrawal in one transaction before ordinary answer_refusal status/count/byte checks, stores minimal event and revokes/tombstones without arbitrary oversized text. Terminal/full asks cannot suppress revoke; later A never revives. Old first-install daemon instead uses §4 historical-limit HOLD/import rules.

## 15. Missing negative acceptance

> The acceptance table still lacks R1 #12 cases that the disposition marks as accepted: revoke negatives (unauthorized sender, foreign group, unknown or malformed ID, over 80 characters), not_before enforcement, permit clipping to grant expiry, and grant_id reuse at registration.

**FIXED.** §8 names unauthorized/foreign-group/unknown/malformed/>80-char revoke negatives, not_before, exact expiry/permit clipping and duplicate grant registration. Also adds readiness/full-ask/old-epoch/no-dispatch acceptance branches; these are future tests, not tests performed by this docs revision.

## 16. Duplicate recovery grammar

> This design introduces `manage inspect --request ID` / `manage settle --request ID --evidence-file FILE` and its own state names, while the Z prerequisite already proposes `manage ID --inspect` / `manage ID --settle --expect RECORD_DIGEST` and the states held_unissued, aborted_no_effect and superseded_with_verified_effects.

**FIXED.** §7 adopts Z manage ID --inspect / --settle --expect RECORD_DIGEST and predecessor-bound ordinary plan/apply. No alternate request-first grammar or arbitrary evidence CLI resolver. Delegated mode extends the same saved record/dispatcher.

## 17. Unsupported AskPost attachment

> The design binds the card to the descriptor through a `controller-management-grant-v1` structured attachment, but AskPost has forbid_unknown_fields and no attachment field. The first-install card posted through the existing API cannot carry one, and 'unsupported attachment refuses import' conflicts with that.

**FIXED.** §4 sends only supported exact body/form/choices/recommended/links. Local delegation-render-v1 reproduction manifest is not an AskPost attachment or authority. An existing supported immutable40-hex GitHub commit/path link identifies the descriptor; full64-hex digests remain in the linked artifact and exact local manifest, not the old card body. The12-hex display prefix is informational only; no typed-field or generic redaction bypass is claimed. Import reproduces actual stored bytes and immutable references.

## 18. Historical master-go

> Dropping d2's 'explicit btq master go' gate rests on an asserted 'current explicit operator merge authority' with no provenance (no ask, via_ref or date) in the inputs, against the design's own rule that source prose is not approval evidence.

**CLARIFIED; NEW MERGE QUESTION REBUTTED.** Current explicit user authorizes merges after actual Claude pass and genuine required approvals. §1 records supersession; genuine future Marmot approval of this changed mechanism is required. No fabricated native decision or new per-merge gate is introduced; source prose itself is not consent.

## 19. Original d2 approval provenance

> Historical d2 is listed under `approved_inputs` and archived under `approved-inputs/`, yet the note says no approval is inferred, and the design never says whether the per-request base it amends was genuinely approved; the Z prerequisite calls it the 'approved original management d2'.

**FIXED.** §1 and manifest name genuine native btq-ovvzt approve Liam2026-10-15T19:58:20Z, revision/digest/review identity. Historical via=cli records actual user approved/proceed while daemon latched, not Marmot; pending archive prose remains exact. Original scope exclusions are retained, not promoted into deployment/delegation authority.

## 20. Ordinal schema

> The integer domains name `ordinal=0..31`, but the closed field list for `controller-management-grant-v1` has no ordinal field, so it is unclear whether ordinal is required (and otherwise rejected as unknown) or implicit.

**FIXED.** §2 explicitly defines operation array index as implicit ordinal0..31. An explicit ordinal key is unknown/rejected; no canonical-vector divergence.

## 21. Combined card budget and kind

> A combined deployment-plus-grant card must fit deployment disclosures (design link, deviations, release closure) and grant facts in one card limited to 40 lines/3500 characters, or 36/3200 in btq. The budget cut and the ask kind used (approval asks take only --bead) are not specified.

**FIXED WITH BOUNDED ALTERNATIVE.** §4 names actual question-kind A/B deployment+grant card, no targets/native design pin changes. Whole generated body+form/footer must fit36lines3200chars and show deployment links/closure/deviations plus all grant facts. Require typical one-operation positive fixture; reduce scope before signing or use later standalone finite card if even one cannot fit. First-install fresh same-thread reply is disclosed.

## 22. Clock HOLD availability

> A clock divergence over 1s between UTC and boottime permanently holds the grant. On a laptop host, an NTP step after resume can routinely exceed that. This is accepted HOLD-favoring behavior, but the card does not disclose it as likely.

**FIXED DISCLOSURE.** §4/§8 explicitly disclose likely permanent grant holds after NTP/suspend clock correction, restart invalidation and new consent. No weaker clock oracle or silent revival.

## 23. Z prerequisite scope

> The scope says Z is a prerequisite only 'for nonimplementation kinds', but section 3 makes the Z classifier mandatory for every dependency endpoint role decision, implementation tasks included.

**FIXED.** §1 makes compatible approved/deployed Z prerequisite for shared format2 journal/dispatcher and endpoint classification across both implementation and planning; proposed d3 input has no assumed approval/deployment.

## 24. Terminal consent disposition mismatch

> The R1 #38 disposition cites 'Section1/4' for the rule that terminal access covers only diagnostics and already-authorized repair, but neither section says this or reconciles the terminal escalation routes R1 cited.

**FIXED IN NORMATIVE TEXT.** §1 now explicitly permits terminal read-only diagnosis/already-authorized exact repair only, never consent. Questions/design/deployment/recovery decisions exclusively Marmot admind; durable unanswered outbox survives outage. Historical R1 disposition is preserved as author history; d3 supplies the missing normative rule.
