# Plan7A d3: actual R2 author dispositions

Status: UNREVIEWED d3, two of three actual rounds used. R1 REVISE27, R2 REVISE18. These are author dispositions, not reviewer clearance or human approval. The final R3 has not run.

Actual R2 raw report SHA256 `2d3ea8bd7d484fad7910b762f0ef2d5e83fd478a9b5fb6d6d4d22bd80024e36a`; extracted18 SHA256 `572ae9b919f34f1f4cfaec42509300213413e75ac3e563e4c1d4e594a1d3d16e`; invocation metadata SHA256 `5bbde4ef01247e53da4dc730b4cef2e4a2aebe337749bfc54aad5708259a69f5`.

[Exact ordered JSON ledger](plan7a-d3-r2-dispositions.json). Controller supplies private evidence anchor and repository mappings directly in the reviewer brief; public relative locators are not host absolute paths.

## R2.1

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 64,
  "summary": "BLOCKER (R1.16 incomplete): the design promises a typed profile gap that goes back to the origin for a native ask outside the strict subset, but sufficiency_disposition can only return HoldUnavailable for invalid input. No projection value carries a correctable origin gap, and the 'Correctable input recovery' paragraph is written for intake, which has no NativeAsk.",
  "failure_scenario": "An ask that passes native lint but has a 5KiB effect string, a float, or an unused path key on a commit ref (or an unsafe description) fails sufficiency request validation. sufficiency_disposition returns HoldUnavailable (an infrastructure hold) instead of NeedsGrooming/ReturnToOrigin, so after cutover it is held as an outage and never returned with a usable gap, which is exactly R1.16's scenario."
}
```

addressed_in_unreviewed_d3 — §§3,5: Add Prepared.origin_gap with exact safe Gap values before constructing a strict SufficiencyRequest. All native-valid profile-ineligible or unsafe origin asks produce NeedsGrooming with those usable field/code gaps; infrastructure remains a separate host-failed hold. No current relay enforcement is changed.

Exact finding canonical SHA256 `d14db7477b5c90338d0603ee75f5a382fc511185e8fba83ac4ee80363010ec5a`.

## R2.2

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 52,
  "summary": "BLOCKER: `targets?:[BeadID]` uses the wider admind existing-ID profile, but queue6fe target_ids() requires its minting BEAD_ID `[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}`. So the profile is not a strict subset of native-accepted shapes, and the claim 'exactly queue6fe's ... optional-target rule' is false. The §11 drift check compares only against admind BEAD_ID.",
  "failure_scenario": "An ask with targets:[\"btq-96hm\"] or [\"btq-abcde.1\"] passes Plan7A as a valid NativeAsk, while native ask_digest emits the gap 'ask.targets is malformed'. The two layers disagree on the same bytes, and the drift fixtures never catch it."
}
```

addressed_in_unreviewed_d3 — §§3,11: Define TargetID separately as exact queue6fe minting regex and use it only for native ask.targets; retain broad admind existing BeadID for native reads/refs. Drift fixtures compare both constants and target_ids, including short/dotted negative targets.

Exact finding canonical SHA256 `4e4cb25c27a623b97a07c3e69f5047597f672b1608a5e853aa594a75eec52c23`.

## R2.3

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 100,
  "summary": "BLOCKER (privacy/R1.13): the visible enrichment marker embeds the private `source_id`, which §3 says must be replaced by an alias in egress. The enriched description is card text and is later sent back to the model as card.description.",
  "failure_scenario": "A host source_id such as `ws-acme:adr:host7` is written into 'Gatekeeper-added context (source ws-acme:adr:host7; ask reference 2)'. It is shown on the operator card and sent to the model on the mandatory reassessment, which defeats the locator-free alias mapping."
}
```

addressed_in_unreviewed_d3 — §§3,5: Visible markers contain only frozen public decimal source aliases and root-ask ordinal. Private source IDs remain solely in the host request/retained additions and never reenter card text/model egress.

Exact finding canonical SHA256 `a8e3d59b9d015f6b9e88708797a7db136e986a9cf7bd53c50e7b7be8b1c7d047`.

## R2.4

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 83,
  "summary": "BLOCKER (closed types, host vs model provenance): intake_disposition is declared as (request, verified_result) and accepting Check values, but host-failure provenance needs the separate Attempt union. §3 also says the adapter normalizes exceptions into the result envelope `{status:unavailable,reason_code:timeout}`, the same shape a model can emit.",
  "failure_scenario": "One implementer normalizes a real Hermes timeout into the result envelope, as §3 says. intake_disposition then sees a model-shaped unavailable and returns RawConfirmProposal, so ADR §5.1 #5's raw-unreviewed fallback can never be reached. Another implementer passes Attempt{host_failed}. The two diverge, and fixture 5 cannot be written unambiguously."
}
```

addressed_in_unreviewed_d3 — §§3–4: One closed Attempt union carries trusted host failure separately from completed Check[Judgment]. No host exception becomes a model result. All dispositions take Prepared plus Attempt|null and share the explicit precheck/null rules; model unavailable has no reason_code.

Exact finding canonical SHA256 `1027630c8aae55c499fe5b75a6a3c95d6b3696938242a8fee26c6c9c34140fb1`.

## R2.5

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 118,
  "summary": "BLOCKER (R1.19/R1.23 incomplete): a budget above 60000 or an unreadable one is promised as a 'typed unreadable_policy hold/escalation', but the field is PosInt bounded to 1..60000, so it fails schema validation. Check.code has no unreadable_policy or stale_digest. Those codes exist only as result reason_codes that a model can choose.",
  "failure_scenario": "A workstream sets gatekeeper_seconds=90. The request becomes Check{invalid,code:schema}, not unreadable_policy, so recovery cannot tell a policy problem from malformed input. Meanwhile a model can answer reason_code:stale_digest and steer consumers toward a 'fresh native read' recovery."
}
```

addressed_in_unreviewed_d3 — §§3,6–7: Classify unavailable/out-of-range raw host configuration before bounded request construction as HostCode unreadable_policy; stale native readback is host-only stale_digest. Models cannot choose either code, and recovery preserves their trusted host provenance.

Exact finding canonical SHA256 `43eee178fed651e71df2ceaa2e557bc21b61a58a19d319dbd048ba123713c7b1`.

## R2.6

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 68,
  "summary": "The intake egress DTO omits `target` (workstream_id/repo_id), yet the model must set `target_changed`, and a target change is an ADR §5.1 materiality criterion.",
  "failure_scenario": "An operator writes 'do this in repo-b instead' in a repo-a workstream. The model cannot see the current target, so it cannot judge target_changed. If it returns false with materiality minor, the projection is CreateMinorProposal, a missed material change caused by the contract rather than by advisory model risk."
}
```

addressed_in_unreviewed_d3 — §§3–4: Intake egress includes meaningful public current workstream/repository target labels through a frozen trusted registry projection. Target_changed can be judged against actual public target rather than unavailable opaque host context.

Exact finding canonical SHA256 `e244f8f8264a995a390afa3a918070db11e81978972c2a69134ff7aa3b437b93`.

## R2.7

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 89,
  "summary": "Lint.failed gaps must be PublicText Text of at most 4KiB, but native gap strings embed repr() of arbitrary ask values and join nested linked-bead gaps. A genuine failed lint can therefore make the whole request invalid.",
  "failure_scenario": "A ref like `ask.refs is not a list: {...}` containing a 64-hex value, or a nested bead gap chain longer than 4KiB, fails the redaction-equality or size check. The request becomes invalid, so the result is HoldUnavailable instead of the specified failed => NeedsGrooming."
}
```

addressed_in_unreviewed_d3 — §§3,5: Represent failed native lint with bounded content-free Gap codes/JSON Pointers and null detail. Preserve full arbitrary native repr/nested diagnostics privately with protected evidence discovery; fold excess safe gaps without converting a real lint failure into invalid host input or outage.

Exact finding canonical SHA256 `22c690ff1aa8bd05d404f1578a9f825ad8391460f2b434d820344e2eb59eceaa`.

## R2.8

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 54,
  "summary": "The whole-blob proof combined with ContextItem.text as a 4KiB PublicText (no CR, no 64-hex runs, no login dirs) excludes nearly every real pinned document. Automatic enrichment and context-aware sufficiency become close to unusable, and this limit is not stated in the §10 'declared limits' decision.",
  "failure_scenario": "The ADR (~100KB), this design (~45KB), any manifest containing a sha256, and any CRLF file are all ineligible. The human approves 'bounded quoted-only enrichment' without being told it effectively never applies to ADR/design refs."
}
```

addressed_in_unreviewed_d3 — §§3,10: Retain directly verifiable <=4KiB full-blob proof rather than invent an unreviewed partial-excerpt attestation. Explicitly disclose large ADRs/designs, CRLF, 64hex manifests and sensitive sources are not eligible automatic context; origin-authored safe card or grooming is required. This limitation is a human design choice.

Exact finding canonical SHA256 `eb4e728dc5fb6db93e6b6964895e5c7bdf3ca647a8f5fd39fc5dd261141b2602`.

## R2.9

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 16,
  "summary": "The raw-confirmation qualification departs from ADR a27 §5.1 #5 (gatekeeper unavailable means a raw `unreviewed` bead). It is labelled a 'design/ADR decision' without saying whether design approval alone is enough or an ADR amendment is required. ADR §5.9 says reviewers treat departures from adr_revision as blocking.",
  "failure_scenario": "A later pure implementation cites adr_revision a27 and implements RawConfirmProposal for model-originated failures. The code reviewer, judging against a27 §5.1, finds a blocking departure that no ADR revision covers. Alternatively, the human approves the design believing it amends the ADR."
}
```

addressed_in_unreviewed_d3 — §§1,4,9–10: State the raw-confirm qualification departs from a27 §5.1 #5 and cannot enforce under ordinary Plan7 approval. Require an actual separately reviewed/human-approved ADR amendment and matching native integration/deployment admission. Pure inactive contracts do not amend the ADR.

Exact finding canonical SHA256 `812c408c15b3a15501d6c38c053ea10c6782fa572bbf227014e4de58a3fb7718`.

## R2.10

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 37,
  "summary": "The design says it 'reuses the pinned redaction patterns' with an explicit context and never calls set_login_dirs. But admind/redact.py compiles login dirs into a module global used by _pass/redact, and the listed implementation files are only gatekeeper/*.py.",
  "failure_scenario": "The implementer has two choices. Option 1: copy the _pass/MAX_PASSES/UNSTABLE loop into gatekeeper/validate.py, a duplicate that drifts when redact.py changes. Option 2: refactor admind/redact.py, which is outside the stated pure scope and pins and risks changing installed redaction."
}
```

addressed_in_unreviewed_d3 — §§2–3,9: Explicitly include narrow shared-kernel/directory-compiler extraction in pinned admind/redact.py, not copied gatekeeper patterns or global mutation. Existing APIs/default callers/continuation/spans retain behavior, with byte-for-byte pinned076 regression fixtures and interleaved explicit-context isolation tests.

Exact finding canonical SHA256 `cf3c5718c47dab471724846c86c84a347087edc8c3b451c6584d6796ab46815d`.

## R2.11

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 66,
  "summary": "Enrich outcomes can still exceed the 32KiB result bound (8 additions times 4KiB quotes plus overhead), so too much enrichment ends in different states depending on size (R1.17 incomplete).",
  "failure_scenario": "An enrich of 9KiB in total gets NeedsGrooming atomically. An enrich of 33KiB fails the result bound, becomes bad_output, and maps to HoldUnavailable (an outage hold) for the same semantic condition."
}
```

addressed_in_unreviewed_d3 — §§3,5: Reduce enrich quote to <=1KiB with <=8 additions, retain full result32KiB and atomic appended8KiB/description16KiB bounds. Every semantic or raw-result oversize yields typed NeedsGrooming/oversize, not HoldUnavailable; no parsing/truncation of overbound raw response.

Exact finding canonical SHA256 `f56d6610971affd166d5f3e733c398a22a8cf20296b085b60d1503ab823571ca`.

## R2.12

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 58,
  "summary": "Several hash and identity inputs are not specified. The envelope `revision` has no defined meaning for any kind. The canonicalization for base_candidate_sha256 and PrivacyContext.sha256 is not given. rule_revision is a single 40-hex SHA covering patterns that span two files (redact.py and secret_scan.py) without saying which object it names.",
  "failure_scenario": "Two implementations hash base_candidate with different JSON separators, or set revision differently, so binding fixtures and replay checks diverge for the same request."
}
```

addressed_in_unreviewed_d3 — §3; §5 base candidate; §7 invocation: Define single exact J canonicalization, snapshot revision meaning/all-input-change rule, PrivacyContext and candidate hashing, and product-commit rule_revision containing both exact pinned redaction source blobs. Request/native/action/delivery identity namespaces remain distinct.

Exact finding canonical SHA256 `d2d363bd9c23f622022d3c1eec40d075c40bff2d66d5efe0a2b02fa87959ff65`.

## R2.13

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 100,
  "summary": "Two dead or contradictory paths. 'No match => NeedsGrooming' cannot happen, because §3 already refuses sufficiency context that does not match a candidate ref before assessment (that refusal maps to HoldUnavailable). Egress refusal leading to RawConfirm is likewise unreachable, because every egress field is already PublicText-validated in the request.",
  "failure_scenario": "Implementers write two different outcomes (HoldUnavailable and NeedsGrooming) for the same ref-mismatch condition. The extra branches and fixtures cost effort without covering any reachable state."
}
```

addressed_in_unreviewed_d3 — §§3–5: Context/ref mismatch is one correctable pre-invocation origin gap; remove unreachable transform no-match branch. Egress host configuration/projection failures have host provenance and hold/escalate, never RawConfirm; model-controlled failure alone follows the proposed inactive route.

Exact finding canonical SHA256 `2b8f6d77984bb4251f3fc838883d31238b54214ef1440a183b41948ffa571c17`.

## R2.14

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 68,
  "summary": "The sufficiency egress sends refs as `{alias,kind}` only, without id/path or ask.targets. The permission egress omits cwd. The model therefore judges a different card or invocation than the operator sees or than ADR §5.3 step 1 specifies.",
  "failure_scenario": "The model cannot check that a design ask pins the ADR file at the right commit, or that `rm -rf ../x` is confined to the worktree. It rates the card 'sufficient' or allows the call on incomplete information."
}
```

addressed_in_unreviewed_d3 — §§3,7: Sufficiency sends exact Git/ref identities, public verified repo/path views and actual native targets. Permission includes meaningful public cwd plus unchanged tool/input. Frozen registry mapping and private binding are trusted host facts; unavailable/unsafe mapping refuses before call, never a degraded invented invocation.

Exact finding canonical SHA256 `e20bc698afcace2c8f7d4b9d769c701c4685bedacf22a2499e31339332cb2e2f`.

## R2.15

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 62,
  "summary": "FieldPath allows at most 8 components and an unspecified `.name` grammar. It cannot address NativeValue keys containing `:.+-` or uppercase letters, or depth beyond 8 under candidate.ask.<field>.",
  "failure_scenario": "An invalid scalar at ask.effect.a.b.c.d.e.f.g.h, or under the key `x-y`, can only be reported as null or truncated, so the origin-correction receipt loses its location (R1.20 incomplete)."
}
```

addressed_in_unreviewed_d3 — §3: Replace ad-hoc dot path/depth8 grammar with full bounded RFC6901-style JSON Pointers, including escaped keys and canonical array indexes; every allowed nested key/depth is addressable without truncation.

Exact finding canonical SHA256 `18548108b72feaf19140de2543b41234e129fd45007b262a04233843ab851874`.

## R2.16

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 87,
  "summary": "The SufficiencyRequest.groom_failures field is never used by sufficiency_disposition. groom_disposition takes the post-failure count separately, and the request field's before/after meaning is not defined.",
  "failure_scenario": "A consumer puts the post-increment count in the request and the pre-increment count in groom_disposition, or the reverse. The redundant state invites the off-by-one that R1.5 described."
}
```

addressed_in_unreviewed_d3 — §§5–6: Remove redundant SufficiencyRequest.groom_failures. The only projection input is groom_failures_after with explicit initial0/returned-failure1/second2 saturation; durable owner and delivery/nudge histories remain required, no retry increment/reset.

Exact finding canonical SHA256 `1911c8aaa920e1f9f5224da6fe545ce659617c7bf7f72cfb90706654a1d71923`.

## R2.17

```json
{
  "file": "docs/reviews/plan7a-design-r1-disposition.md",
  "line": 3,
  "summary": "Evidence bookkeeping problems. The committed disposition still says 'Status: UNCOMMITTED author proposal'. The manifest's private_handoff_relative_path and raw_report_private_path have no base anchor, so a reviewer cannot locate the R1 log or the approval snapshot (R1.25 reproducibility only partly met).",
  "failure_scenario": "This round 2 review cannot independently verify the 27-verbatim claim or the btq-ovngv/xywu0 snapshot hashes. The approver sees a status line that contradicts the commit."
}
```

addressed_in_unreviewed_d3 — §11; d3 manifest; historical R1 status note: Correct R1 status to historical committed d2 author record while preserving verbatim findings/raw reports. Define machine-neutral PRIVATE_HANDOFF/repository anchor policy; private controller locator maps supply actual paths to reviewer, with exact input hashes. No public host absolute path or new runtime service.

Exact finding canonical SHA256 `96aef287d7ffb38ad6611de61d3c49dc1ef85be70d7b26e0f8a7e246dbfc204b`.

## R2.18

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 161,
  "summary": "Advisory nit (stable path): the design creates a new plural docs/designs/ directory while product 0763774 has docs/design (singular) and docs/superpowers. The R1.27 rebuttal keeps the path for lineage, which is acceptable but splits discovery.",
  "failure_scenario": "Future design discovery has to search docs/design, docs/designs and docs/superpowers/specs. This costs findability only, not correctness."
}
```

deferred_advisory_directory_nit — §11: Defer directory-only advisory nit. Retain already-reviewed same docs/designs lineage and links; explicitly state baseline singular docs/design convention, no runtime contract depends on relocating documents.

Exact finding canonical SHA256 `e1577b3de900f936810706709c7a4b6beef3fa5548f1ba0dcf444a49643b0515`.
