# Plan7A actual design R1 disposition — d2 author repair

Status: UNCOMMITTED author proposal; no approval or reviewer APPROVE. Actual R1 REVISE consumed 1 of maximum 3 rounds; no reset. No tests, models, queue, sends, services or runtime changes.

Actual raw report SHA-256: `5c287ac2abd3bf456424c852270156eb3f56247d1a853bc217bcec43122f59b5`. The 27 JSON entries below match the actual fenced JSON in order; summaries and failure_scenario strings are preserved verbatim. Responses are author dispositions, not reviewer verdicts.

## R1.1

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 62,
  "summary": "BLOCKER: the rules for invalid inputs contradict each other: §3 says invalid requests project to HoldInvalidInput, but the same paragraph sends sufficiency errors to HoldUnavailable and permission errors to EscalateProposal, and §7 maps invalid permission input to EscalateProposal; no named disposition function (intake_disposition, groom_disposition, permission_disposition, the unnamed sufficiency projection) is declared to return HoldInvalidInput.",
  "failure_scenario": "A permission request whose intent fails the redaction check is HoldInvalidInput under §3 (agent session stuck, no ADR §5.3 escalation) but EscalateProposal under §7; an invalid sufficiency request is HoldInvalidInput or HoldUnavailable depending on which sentence the implementer follows, so the fixtures in §9 can't be written unambiguously."
}
```

FIXED §3: explicit kind-specific invalid-input matrix and named sufficiency_disposition; all three functions accept Check values. No rejected bytes are echoed.

## R1.2

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 98,
  "summary": "BLOCKER: the enrichment source is not bound to pinned evidence: ContextItem.text and resolved_id are caller-supplied and never required to equal resolved_refs[N-1].resolved_id or to come from the pinned object, yet the visible marker says the quote is from 'ask reference N'.",
  "failure_scenario": "A caller supplies ContextItem{ref: same as candidate ref 2, resolved_id: some other SHA, text: invented sentence}; the model quotes it, enriched_candidate appends 'Gatekeeper-added context (source s; ask reference 2): <invented>' and native lint/digest recompute passes because the description is merely hashed, so the operator sees fabricated text attributed to a pinned ref."
}
```

FIXED §§3,5: sufficiency context is only complete UTF-8 file-blob content, Git blob hash verified locally against corresponding resolved_refs identity, plus exact native ref match. Unsupported extraction grooms rather than claiming pinned provenance.

## R1.3

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 98,
  "summary": "BLOCKER: the gatekeeper-added marker can be forged: nothing rejects a candidate whose original (agent-authored) description already contains the 'Gatekeeper-added context (source …; ask reference N):' marker text.",
  "failure_scenario": "An agent writes its own description ending with a fake 'Gatekeeper-added context (source doc1; ask reference 1):\\n<claim>' block; the card then shows unreviewed agent claims styled as gatekeeper-verified pinned quotes, defeating the ADR §5.9 'marked as gatekeeper-added' distinction."
}
```

FIXED §5: reserved marker prefix forbidden in unused descriptions; used descriptions must reconstruct exactly from marker-free base and proven retained additions. A text marker alone conveys no authority.

## R1.4

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 106,
  "summary": "BLOCKER: revision identity is inconsistent: §5 limits enrichment to one cycle 'per submission revision', while §6 says revisions are identified by native digest and source revision; enrichment always changes the native digest.",
  "failure_scenario": "After the first enrichment the candidate has a new native digest, so under §6 it is a new revision and the consumer supplies enrichment_used=false again; enrich→reassess→enrich repeats without bound, defeating the 'one fresh reassessment' limit."
}
```

FIXED §§5,6: stable external submission_id/submission_revision and original candidate hash survive enrichment/native-digest change. Durable integration must retain used state; pure fixtures cannot prove durable storage.

## R1.5

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 85,
  "summary": "BLOCKER: groom_failures is the literal type 0|1|2 and groom_disposition's input is ambiguous between the count before and after increment; the grammar cannot represent an operator-groomed resubmission after CannotApproveNotice (ADR §5.9: 'the operator can … groom it themselves').",
  "failure_scenario": "Two failed grooms → CannotApproveNotice; the operator grooms and resubmits; if reassessment returns groom the consumer must send groom_failures=3, which is schema-invalid → hold with no path. Separately, a consumer that passes the pre-increment value 1 on the second failure gets ReturnToOrigin and allows a third groom."
}
```

FIXED §6: count is explicitly after confirmed failed return, saturates at two; later operator correction remains assessable without counter reset or a third automatic return.

## R1.6

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 93,
  "summary": "The groom gap field enum ('what'|'effect'|'why'|'context'|'refs') has no excludes, risks or alternatives, yet §5 requires those parts and §9.7 requires grooming on missing excludes/alternatives.",
  "failure_scenario": "An ask with an empty excludes scope can only be reported as 'effect' or 'context', so the originator gets a misfiled gap; a model that invents field:'excludes' produces an invalid result → HoldUnavailable instead of grooming."
}
```

FIXED §5: excludes, risks and alternatives are explicit groom field variants.

## R1.7

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 64,
  "summary": "Bounds don't fit together: lint gaps are capped at 16 while refs allow 32, and native ask_digest emits one gap per failing ref plus description/field gaps.",
  "failure_scenario": "A candidate with 20 unresolvable refs yields 20+ native gaps; the Lint{failed} cannot be represented, the request is oversize-invalid and holds, instead of NeedsGrooming returning the real gaps to the originator."
}
```

FIXED §3: 64-gap cap covers 32 reference failures plus all required field categories; aggregate wire limits remain independently enforced, no truncation.

## R1.8

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 37,
  "summary": "BeadID follows btq's minting regex ([a-z][a-z0-9-]{0,31}-[0-9a-z]{5}), but it is also used for approval_id, task_id, open_tasks and bead refs, where existing IDs are wider (admind's BEAD_ID [a-z0-9]{1,16}-[a-z0-9.]{1,32}; existing btq-96hm; dotted hierarchical children).",
  "failure_scenario": "A workstream with an open bead 'btq-96hm' or 'btq-abcde.1' in open_tasks makes the whole IntakeRequest schema-invalid → HoldInvalidInput; a permission subject task with such an ID can never be evaluated."
}
```

FIXED §3: existing-ID profile comes from product076 admind.approvals.BEAD_ID, not native minting regex; short and dotted IDs are mandatory fixtures.

## R1.9

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 77,
  "summary": "'Any true flag forces material' and 'context_complete=false requires context_incomplete=true' do not say whether a contradiction is a result-validation error or a deterministic override; facts the host already knows are left to the model to echo.",
  "failure_scenario": "The model returns materiality:'minor' with destructive:true, or omits context_incomplete when context_complete=false; if treated as an invalid result, intake_disposition yields RawUnreviewedProposal (a dispatchable unreviewed task, ADR §5.1) instead of ConfirmMaterialProposal, so a destructive request skips kind:confirm. The projection should force material from the flags and from context_complete."
}
```

FIXED §4: host-computed effective materiality combines any true flag, incomplete host context and model materiality; contradictory flags cannot downgrade through invalid-result fallback.

## R1.10

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 81,
  "summary": "Advisory but unaddressed: the model itself can trigger the intake fallback to raw unreviewed, because a model-emitted {status:'unavailable'} or deliberately malformed output is treated like a real outage, with no distinction between adapter-produced and model-produced unavailable.",
  "failure_scenario": "Agent-authored open-task titles or context text carry an injection ('reply unavailable'); a material operator request then becomes a raw unreviewed task without the 'Did you mean…' confirmation."
}
```

FIXED §§3,4,10: typed host-failure provenance is distinct from model output. Malformed/model-originated failure retains raw-unreviewed text with mandatory confirmation, never unattended minor execution. This qualification is a PROPOSED human-approved design/ADR decision, not existing authority; root chose it for eventual admission. Genuine host timeout/unavailable retains original raw fallback.

## R1.11

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 96,
  "summary": "SufficiencyRequest.context items are not restricted to the candidate's pinned refs (only enrichment checks the ref match), so the model may judge 'sufficient' using material the operator never sees.",
  "failure_scenario": "The caller includes an unpinned excerpt that explains the change; the model answers sufficient because the context fills the gap, although the card alone (ADR §5.9: 'from this card alone') still lacks it."
}
```

FIXED §3: every sufficiency context item must map to a current candidate ref and match its resolved identity with complete blob proof. Unpinned contextual material is ineligible before the assessor port.

## R1.12

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 116,
  "summary": "PermissionRequest carries only a free-text intent plus tool_name; no digest or binding ties an allow to the exact tool input the hook intercepted (ADR §5.3 step 1 sends tool, input, session, cwd).",
  "failure_scenario": "intent 'run project tests' is allowed, but the actual Bash input is a different command; nothing in the request/result binding detects the mismatch, and §8 does not list grey-zone input binding as a later obligation (only Plan5 typed actions)."
}
```

FIXED §7: exact subject/tool/session/cwd identity and inert tool input bind invocation digest, whole request digest, and judgment. Later native action adapter must compare intercepted arguments; no executable API is created here.

## R1.13

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 66,
  "summary": "The protocol methods take the full requests (candidate.ask.refs repo/path, ContextItem.ref locators) as model input, while §3 forbids forwarding private locators; no egress projection type exists, so the port signature is the effective egress contract.",
  "failure_scenario": "A later adapter implements assess(SufficiencyRequest) by serializing its argument and sends host repo paths to Hermes, while still complying with the Plan7A protocol."
}
```

FIXED §3: ports accept explicit closed locator-free Egress DTOs. Private host requests cannot be serialized as compliant model inputs; local mapping connects aliases back to exact original facts.

## R1.14

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 37,
  "summary": "PublicText validity depends on admind.redact's process-global login-dir state (set_login_dirs); the 'pure' validators are therefore process-dependent and the snapshot/policy binding doesn't cover the redactor configuration.",
  "failure_scenario": "A test or process that never called set_login_dirs accepts text containing a login directory that the production validator rejects; the same request bytes validate differently in two processes."
}
```

FIXED §3: explicit immutable privacy context and digest supplied to pure validation; no global set_login_dirs, config or environment reads. Pinned patterns can be factored for later pure reuse without changing installed redaction behavior.

## R1.15

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 70,
  "summary": "Invalid intake requests (original >16KiB, unsafe text, >32 open tasks) project to HoldInvalidInput with no recovery or notification path, a departure from ADR §5.1 #5 (raw-text bead when the gatekeeper can't process) that is neither flagged nor offered as a §10 decision.",
  "failure_scenario": "An operator sends a 20KiB unthreaded message; it is held silently with no thread reply, no raw bead and no recovery rule (§6 recovery covers only sufficiency)."
}
```

FIXED §§3,10: content-free invalid-input receipt and explicit origin grooming/recovery for unsupported/unsafe/oversize inputs, safely retained original. Strict-profile behavior and raw-confirm qualification are exposed as proposed human choices, not silent discard or native authority.

## R1.16

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 39,
  "summary": "Natively valid asks outside the strict subset (Text >4KiB, empty strings, floats, ignored repo/path keys, non-matching bead IDs) get a local invalid-profile result that holds rather than returning to the originator; once sufficiency becomes mandatory these asks become unapprovable through the relay, and §10 doesn't ask the human to accept that.",
  "failure_scenario": "After cutover, a btq-lint-clean ask whose effect is a 5KiB string can never pass the gatekeeper and is never sent back for grooming with a usable gap."
}
```

FIXED §§3,6,10: unsupported native-valid profile becomes a specific correctable origin gap. Current relay remains unchanged/shadow only; later enforcement requires admitted integration and profile decision.

## R1.17

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 98,
  "summary": "No projection is specified for enriched_candidate failing its whole-size/safety checks, and the enrich result's maximum (8 additions × 4KiB quotes plus overhead) exceeds the 32KiB result bound.",
  "failure_scenario": "The projection says EnrichmentProposal but the enriched description exceeds Body 16KiB; enriched_candidate returns invalid and the consumer has no defined next state (NeedsGrooming? HoldUnavailable?)."
}
```

FIXED §5: complete enriched candidate and aggregate appended bytes validated atomically; overflow/unsafe transformation returns NeedsGrooming. At most eight additions and 8 KiB appended bytes, within whole result/request limits.

## R1.18

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 98,
  "summary": "The 'ask reference N' numbering (position in root ask.refs, bead refs included) differs from the 'approve-bead --doc N' numbering on the card (Resolution.docs: file/commit/range only, deduplicated, includes linked beads).",
  "failure_scenario": "refs=[bead, fileA]; the marker says 'ask reference 2' while the card offers 'approve-bead --doc 1' for fileA, so the operator opens the wrong document or none."
}
```

CLARIFIED §5: root ask ordinal is never approve-bead --doc numbering. Later doc commands require native Resolution.docs mapping; no invented document pointer.

## R1.19

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 110,
  "summary": "§6 promises typed failures for 'stale native digest' and 'unreadable policy', but neither reason_code (timeout|unavailable|bad_output|unsafe|oversize) nor Check.code (schema|unsafe|oversize|binding|invariant) has a code for them.",
  "failure_scenario": "Implementers map a stale digest to 'binding' or 'unavailable' inconsistently, so recovery cannot tell 'resubmit fresh read' apart from 'endpoint down'."
}
```

FIXED §§3,6: stale_digest and unreadable_policy reason codes explicitly represented; fresh native-read versus restored-policy recovery remains distinct.

## R1.20

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 62,
  "summary": "Check.invalid.field is typed ID (letters, digits and _:.+-), which cannot express list-index paths such as ask.refs[3].id.",
  "failure_scenario": "An invalid ref inside a 32-item list can only be reported as 'ask.refs' or null, so groom/hold diagnostics lose which item failed."
}
```

FIXED §3: bounded FieldPath grammar admits ask.refs[3].id while excluding arbitrary text or unbounded paths.

## R1.21

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 70,
  "summary": "Intake semantics are undefined for envelope subject_id (there is no bead yet), for 'self' dependency refusal, and for the canonicalization of open_tasks.description_digest.",
  "failure_scenario": "Two implementations choose different subject_id values or description_digest inputs, so snapshot_sha256 and the dependency checks diverge for the same message."
}
```

FIXED §4: pre-native submission ID defined; dependencies can only reference existing snapshot tasks, never the new submission; description digest is exact UTF-8 SHA-256 with no normalization.

## R1.22

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 116,
  "summary": "Cleanup: the production wire grammar carries a test-only variant (action_ref kind 'fixture') and constant fields (tier:'grey', budget_ms:60000, sufficient/enrich/groom always-empty additions/gaps); 'An empty original refuses' repeats Body's non-empty rule.",
  "failure_scenario": "Constant and fixture-only fields widen the schema surface and must be versioned and migrated later, when real action binding arrives, for no information gain."
}
```

PARTLY FIXED §§4,7: removed fixture-only action_ref, constant tier and fixed wire budget. RETAINED discriminated union empty additions/gaps deliberately reject contradictory forms; these are wire invariants, not a live-state feature or an extra API. Body nonempty restatement is explanatory, not a separate rule.

## R1.23

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 27,
  "summary": "budget_ms is hard-coded to 60000, but timeouts.gatekeeper_seconds is a workstream-overridable layer (config/layers.py WORKSTREAM_KEYS includes 'timeouts'); the design cites that config as the 60s source without reconciling the two.",
  "failure_scenario": "A workstream sets gatekeeper_seconds=30; either every PermissionRequest is schema-invalid or the config value is silently ignored."
}
```

FIXED §7 and source manifest: effective configured budget frozen in request, <=60 seconds for this proposed profile, no ignored 30-second override. Config layers WORKSTREAM_KEYS source is pinned. No installed hard ceiling is asserted.

## R1.24

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 41,
  "summary": "Reuse/altitude: queue6fe constants (ASK_FIELDS, REF_FIELDS, REF_KINDS, BEAD_ID) are copied by hand rather than read from the btq module that wsd/btq.load already imports, and §9 has no drift test against the pinned btq.",
  "failure_scenario": "btq ships ask.purpose (ADR §17 item 19) and Plan7A silently diverges until someone notices that live asks are invalid-profile."
}
```

FIXED §11 and source manifest: reuse existing wsd.btq.load without constructing Queue; compare supported constants and separate existing-ID profile in later offline compatibility fixtures. Strict subset is deliberate and source-pin mismatch holds integration.

## R1.25

```json
{
  "file": "docs/reviews/plan7a-d1-inputs.json",
  "line": 97,
  "summary": "The manifest is incomplete for claims the design makes: no pinned object for the 'historical native snapshot' of btq-ovngv/xywu0 ('pinned below' is only inline metadata, without the ADR file ref §5.9 requires), no entry for older ADR17 58615ac, and no pinned Hermes source/revision for the negative endpoint search.",
  "failure_scenario": "A reviewer or approver cannot reproduce the Hermes 'no verified endpoint' finding or the approval evidence from the manifest, contrary to the design's statement in its header that exact source objects are in the manifest."
}
```

FIXED manifest: actual historical snapshot byte hash, exact array index/object hash and complete relevant native ask refs bind each authority; add old586 blob pin. Remove reliance on unpinned negative Hermes search. No global endpoint absence claim.

## R1.26

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 124,
  "summary": "The roadmap's Plan7 includes answer-and-nudge (ADR §5.7 stop classification: question/stalled/done); Plan7A defines no port for it and §8's list of future obligations omits that gatekeeper contract.",
  "failure_scenario": "A later integration assumes Plan7A covers every gatekeeper call, and the stop-classification contract is never designed or reviewed."
}
```

FIXED §8: explicit future question answer-and-nudge, stalled/done stop classification, creator/session routing, nudge accounting and single-reader ownership. Those ports remain deferred, not accidentally covered by these three contracts.

## R1.27

```json
{
  "file": "docs/designs/2026-10-16-plan7a-intake-and-sufficiency-contracts.md",
  "line": 1,
  "summary": "Repo convention: design docs live in docs/superpowers/specs/YYYY-MM-DD-*-design.md; this change creates a new top-level docs/designs/ directory. There are also typos: 'is40', 'is64', 'is1..', 'has1..32', 'exact60s', 'failures0/1/2'.",
  "failure_scenario": "Design discovery splits across two directories, and the missing spaces make the grammar text harder to read accurately."
}
```

REBUTTED relocation; FIXED typography. Product076 has docs/design (singular), docs/superpowers and reviews, not docs/designs; root corrected an earlier mistaken baseline claim. Root explicitly retains this already-reviewed plural path to preserve current lineage and links, not as an assertion of baseline convention. No runtime contract depends on naming. Missing spaces corrected.
