# Plan7A d4 author disposition of final R3

Actual R3: **REVISE**, all 14 findings preserved verbatim below in actual order. R1 REVISE27 and R2 REVISE18 remain historical records; all three rounds consumed. D4 is an unreviewed author repair, not reviewer acceptance, human approval, implementation authority, or a review-budget exception.

## 1. BLOCKER (privacy): FieldPath must use the input's complete actual keys, and it has no PublicText or redaction rule. So supposedly content-free origin and profile gaps copy untrusted key bytes (secrets, login directories, 64-hex runs) into public grooming output. The 4096-byte bound also conflicts with unbounded raw native keys.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:58`

A native-valid ask has effect={"/home/u/.claude-b/tok_<secret>":"x"}. The key fails the ID grammar, so preparation emits a profile Gap with field "/candidate/ask/effect/~1home~1u~1.claude-b~1tok_<secret>". That gap goes to the originator / needs-grooming label, leaking the login directory and secret that redaction exists to hide. A key longer than 4KiB cannot be addressed at all, and the design gives no fallback. <!-- install-agnostic: allow=home-path; verbatim synthetic adversarial review fixture, not installation config -->

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Split PrivateFieldPath from PublicFieldPath. Public paths contain only local schema names and bounded indices, stop before every dynamic NativeValue key, and fall back to safe ancestor/whole input. Never echo/truncate rejected keys; protected original diagnostics remain private. Public gaps pass the explicit privacy context.

D4 evidence: §3 private/public diagnostic paths; §12 privacy fixtures.

## 2. BLOCKER: model-caused oversize (a raw response over 32KiB, a reason over 4KiB, over 64 groom gaps, or valid-by-type groom output over 32KiB) maps to NeedsGrooming with one content-free whole-input gap. A model malfunction therefore becomes the originator's fault, gives them no specific gap (ADR §5.9 #4 requires the specific gaps listed), and counts toward the two-groom limit.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:66`

A looping model returns 40KiB, or a valid groom with 64 gaps of 1KiB detail each, on a correct card twice in a row. The originator receives a gap at '' with no information twice, groom_failures_after reaches 2, and CannotApproveNotice recommends deny for a card the model never actually judged. Meanwhile a malformed-JSON response from the same model holds as bad_output, so two model faults route differently.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Every model-originated oversize, including raw response, field/list or aggregate output excess, is completed invalid/oversize. Sufficiency holds bad_output with no grooming charge; only a valid judgment causing actual candidate overflow grooms. Intake and permission use their explicit invalid-output mappings.

D4 evidence: §3 total invalid result mapping and bounds; §5; §6; §12.

## 3. BLOCKER: problems with the context items the host assembles are classified as correctable origin gaps routed to NeedsGrooming. The host, not the originator, supplies those items. The cases are tree or partial-excerpt proof, a blob SHA1 that does not match resolved_id, and a ContextItem.ref that matches no candidate ref.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:54`

The future host reads a file at a stale revision, or includes a partial excerpt or an unrelated doc. Preparation returns origin_gap, sufficiency maps it to NeedsGrooming, and the originator is told to fix /context, which they cannot affect. The failure counts toward the two-groom limit, so a correct card ends in CannotApproveNotice. It should be a host failure (stale_digest or invalid_host).

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Tree/partial/unmatched host context is invalid_host; whole-blob/resolved-identity mismatch is stale_digest. These are host assembly/read faults, never origin gaps or grooming failures. Origin-supplied candidate defects remain separately correctable.

D4 evidence: §3 ContextItem proof/preparation; §5 transform; §12.

## 4. BLOCKER: Check.code 'invariant' at the result stage has no disposition mapping. Sufficiency maps only oversize, schema, unsafe and binding; intake (line 87) lists malformed, unsafe, oversize and binding. Yet the invariant checks are exactly the ones the spec requires on model output.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:108`

A model returns enrich with quote != text[start:end], duplicate (source,start,end) tuples, or an unknown alias; or an intake result with a self or duplicate dependency. Those are completed/invalid with code=invariant. One implementer maps that to HoldUnavailable/bad_output and another to NeedsGrooming or RawConfirm, so fixture categories 5–7 cannot be written unambiguously.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Enumerate all five invalid result codes, including invariant, with exact mappings for all three kinds. Unknown aliases, quote mismatch, duplicate tuples and dependency invariant failures use that rule. Unknown model tokens are invalid/schema, not unmapped host enums.

D4 evidence: §3 total projection rule; §4/5/7; §12.

## 5. BLOCKER (canonical identity): the rule that assigns an Alias is never defined: its base (0 or 1), what order it follows, and whether context, RefView and open_tasks aliases share one namespace. Alias <A> is written into the description, which native ask_digest hashes, and used reconstruction must reproduce it byte for byte.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:104`

One implementation numbers context aliases from 1 in context order; another numbers from 0, or shares the counter with RefView aliases. The same enrichment produces different marker text, so base_candidate and native digests differ and fixtures diverge. On reassessment with reordered context, the rederived alias differs from the marker already in the description, so a valid used enrichment is rejected.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Context aliases are 1-based sorted source_id indices, tasks use a separate 1-based sorted task-ID map, refs use candidate order. Retain exact context_source_ids in used evidence, prohibit namespace crossing, and reconstruct exact suffixes. Same-set reordering preserves alias bytes; changed sets are invalid_host.

D4 evidence: §3 Alias/PublicBindings; §5 used enrichment; §12.

## 6. NativeRef.path is only a private Locator (it may contain 64-hex, CR, or login-directory forms), but RefView.path must be PublicText. An origin-correctable unsafe path therefore surfaces only at egress as host_failed/egress_unsafe and becomes a HoldUnavailable, which is the misrouting R2.1 was about.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:68`

A design ask pins a file ref at 'artifacts/<sha256>.json', a common content-addressed path. Request validation passes because the path is a valid Locator. egress() returns egress_unsafe, sufficiency holds as an outage, and the originator never receives a gap telling them to re-pin a safe path.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Validate exact origin candidate file paths against the public relative-path/Text profile before readiness. Return safe indexed origin gap without the actual unsafe path. Host label/mapping faults remain host faults. No redaction of native path bytes or pretending private paths were shown.

D4 evidence: §3 origin-owned preparation and RefView; §12.

## 7. An undisclosed second departure from a27 §5.1 #5 ('gatekeeper unavailable: create the bead from the raw text, labelled unreviewed'). Intake host failures other than timeout/unavailable (egress_unsafe, egress_oversize, unreadable_policy, invalid_host) and origin profile gaps hold instead of producing a raw unreviewed bead. The manifest's explicit_adr_departure records only RawConfirm for model-originated failure.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:87`

An operator message containing a SHA-256 digest or CRLF fails PublicText, or a target label fails egress. The gatekeeper effectively cannot run, yet intake returns HoldInvalidInput/HoldUnavailable rather than the ADR's raw unreviewed bead. A reviewer checking against a27 finds a departure that no amendment or human decision row covers.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Disclose every intake departure from a27 §5.1 #5: model RawConfirm, origin-profile/input holds, and other host/missing-attempt holds. All remain inactive until a genuine separately reviewed/human-approved ADR amendment and runtime/deployment admission. Pure contract approval is not that amendment.

D4 evidence: §1 all-departures authority; §4; §10; manifest explicit_adr_departures.

## 8. The per-item caps cannot all be met within the 128KiB whole-request bound: 32 context items of 4KiB already total 128KiB before the 16KiB description, the NativeAsk (up to 512 nodes) and the envelope are added. The design also does not say whether an over-bound request built from host-supplied context is an origin gap or a host failure.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:66`

The host attaches 30 safe 4KiB blobs to a 10KiB card. Every field is individually valid, but the request is over 128KiB. Depending on the implementer it becomes an origin oversize gap (NeedsGrooming, counted against the originator) or an unspecified host failure.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Independent maxima are ceilings, not simultaneously attainable capacities. Origin-owned field bounds are checked independently; host context/envelope causing aggregate excess is host_failed/egress_oversize, no truncation or grooming. Do not hide missing required context as complete.

D4 evidence: §3 independent bounds and typed provenance; §12.

## 9. The pure sanitizer that turns native lint into content-free Gaps has no defined input-to-FieldPath mapping. Native ask_digest returns bare strings, and ref gaps carry no ref index ('ref: repo x does not exist'), so the required per-ref JSON Pointers cannot be derived from what native lint returns.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:93`

With three refs, one failing, native lint returns ['ref: ... not found at abc']. One implementer emits /candidate/ask/refs; another re-resolves each ref to emit /candidate/ask/refs/2. The '32 failed refs within the 64-gap cap' fixture has no single expected output.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Native lint exposes unindexed arbitrary strings. Normalize any nonempty native diagnostics to one constant /candidate/ask native_lint gap, detail null. Retain originals privately; never parse text for ref indices, invoke a second resolver or claim current structured native diagnostics.

D4 evidence: §5 Lint; §12 native fixtures; pinned Queue6fe ask_digest.

## 10. A permission origin_gap maps to EscalateProposal with reason_code 'unavailable', a HostCode that means the host adapter is unavailable. This mislabels an input or profile defect as an infrastructure outage, against the provenance separation R2.4/R2.5 established.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:64`

A tool input containing a float is profile-ineligible. The audit records an escalation with reason 'unavailable', so operations treat it as a gatekeeper outage, and the escalation never says the input could not be judged.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Permission origin_gap maps EscalateProposal/input_profile, distinct from host unavailable; no origin/model capability failure is recorded as infrastructure outage.

D4 evidence: §3 Projection input_profile; §7; §12.

## 11. For a ready, eligible request with a null Attempt, the design says only 'holds/escalates'. It names no Projection reason_code for intake, for sufficiency with passed lint, or for permission before the deadline.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:64`

intake_disposition(ready, null) returns HoldUnavailable with reason_code chosen ad hoc ('unavailable', 'invalid_host' or 'deadline'), and implementations diverge on a reachable input.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Give exact eligible-ready/null outcomes for every kind: HoldUnavailable/missing_attempt for intake/sufficiency, EscalateProposal/missing_attempt for permission. Failed/unavailable lint and elapsed budget are explicit deterministic prechecks. No implicit success or ad hoc reason.

D4 evidence: §3 precheck precedence and missing_attempt; §12.

## 12. sufficiency_disposition must run all the atomic enrichment checks to choose between EnrichmentProposal and NeedsGrooming, yet EnrichmentProposal carries no candidate, so callers must run enriched_candidate again. That is two entry points for one transform.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:64`

The enrichment transform and bounds checks are computed twice per enrich result, and the two call sites can drift if one is changed. Returning the candidate inside EnrichmentProposal, or having the projection call enriched_candidate, removes the duplication.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Sufficiency projection performs the atomic transform once. EnrichmentProposal includes the entire exact Candidate; consumers do not call a second projection/transform. Corrupted host proof holds; candidate-limit failure returns atomic grooming.

D4 evidence: §3 atomic internal transform/Projection; §5; §12.

## 13. The new gatekeeper type 'Attempt[J]' reuses the name of the existing product class admind/approvals.py::Attempt, which records decision attempts, in the same code family.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:62`

Later integration code that imports both (the Plan5 decision path plus gatekeeper) must alias one of them. Readers can also confuse a decision attempt with a model-invocation attempt in review and audit text.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Rename the proposed model-invocation type to JudgmentAttempt throughout d4. Leave installed admind decision Attempt unchanged, with no new import alias burden or implementation changes.

D4 evidence: §3 JudgmentAttempt; pinned product076 approvals.Attempt.

## 14. Reserving the literal 'Gatekeeper-added context (' anywhere in an unused description rejects any legitimate card that discusses the marker.

Reported location: `docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md:104`

The future Plan7A implementation approval ask quotes the marker format in its description, so it is classified invalid and groomed. The gatekeeper's own implementation cannot be approved through the gate without paraphrasing.

Author disposition: ADDRESSED_IN_UNREVIEWED_D4. Remove global reserved-substring rule. Marker-like text in unused/base descriptions is ordinary untrusted text. Trust only typed used evidence and exact appended suffix reconstruction, never inferred textual markers. Forged used evidence holds invalid_host.

D4 evidence: §5 marker/reconstruction; §12.
