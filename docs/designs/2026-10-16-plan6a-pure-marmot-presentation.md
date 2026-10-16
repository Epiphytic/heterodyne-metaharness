# Plan6A: pure Marmot presentation contracts

Status: proposed design; no human approval or implementation admission is asserted.
Task: btq-0bkbo. Source baseline: `07637745fe75dc614109a0b8ba72d3489f262a97`.
The adjacent source manifest pins governing texts and inspected reuse seams. This document is a new, bounded Plan6 slice; it does not reopen prior capped review cycles.

## 1. Independent scope and admission

Plan6A specifies pure classification, versioned presentation inputs, rendering and an exact golden fixture catalog for **wsd**. A later separately admitted implementation may create a pure module and offline tests only. It may not wire that module into a daemon, register a command, consume messages, read a database, send anything, or authorize any action. This design itself changes no runtime behavior.

The full roadmap Plan6 depends on Plan5 and S4. Approval of this slice must explicitly admit pure 6A development before full Plan5 completion. This sequencing exception gives no permission to deploy a surface or bypass the full Plan6 prerequisite. Plan5 owns the decision writer and execution protocol; Plan5i and its scanner-consumer follow-up retain their existing ownership. No Plan5 endpoint or unresolved effect schema is finalized here.

Excluded: transport normalization/subscription, cryptographic authentication, replay persistence, database projections, decision submission or persistence, effect execution, timers/digests, delivery receipts/outboxes, membership changes/invites, managed-install commands, deployment and live phone acceptance. No callable, shell text, queue update, approval mutation or effect intent crosses a 6A output boundary. Pure classifications describe input, not instructions to dispatch it.

## 2. Governing contract and amendments

ADR16 `93d0e90b121a07db720d92924cda443a1cfc239c`, sections 3.4, 4.4, 5.9 and 6, governs wsd. Sections 8, 9 and the release/acceptance clauses retain their separate owners.

| Requirement | 6A commitment | Remaining integration obligation |
| --- | --- | --- |
| §3.4 native MLS identity, registered group, replay and allowlist | Never derive authority from text, a reaction, a count or a rendered card; ignored/unverified classifications produce no reply | Future ingress verifies native provenance, deduplicates and rechecks policy before effects |
| §6.1 topology and mentions | Explicit workstream/control surface; only control approval/question presentation may include configured operator mention | Real group/thread mapping, mirroring and visibility evidence are later work |
| §6.2 wsd cards | Complete approval parts, bounded ordinary cards, safe identifiers, seven task reactions, explicit recovery wording | Actual digest creation, stored full context and decision authority remain Plan5/integration inputs |
| §6.3 queries | Deterministic rendering of supplied snapshots, freshness and unreachable warnings; query classification only | Cache refresh and sub-second service response require later integration measurements |
| §6.3 mutations | Recognize names as unsupported-in-6A; no handler or executable payload | Existing Parker, managed-install and sole decision writer remain authoritative |
| §6.2 receipt/reminder rules | Catalog the required display facts without calculating due work | 10-minute alert, one 4-hour reminder, daily digest, independent shared-state alert path and Plan8 scheduling remain mandatory |
| §5.9 sufficiency / §8.2 relay migration | No gatekeeper exception or second decision writer | Actual wsd/gatekeeper release must enforce sufficiency and deliberately retire/forward relay approvals |

**Guard d4 is scoped.** Existing `docs/security-model.md` and the Plan2 guard amendment make admind-own membership events/counts observational, retain confirmed-authority intersection with fresh policy, and quarantine unknown provenance. This does not remove §3.4 suspension and `/trust-group` for other workstream/control groups. 6A displays a supplied trust-hold reason; it neither evaluates membership nor clears a hold.

**admind card amendments remain admind contracts.** The cxmkf design at `663a9efb2f1f2dba4c4c8f864bb4da40eabedd77` and its committed Amendment 1 govern kind labels, full hashed content, recommended-choice semantics and multipart notices there. A question remains a question even when linked to a bead; FYI has no answer prompt; approval purpose is never guessed. In particular recommended-no thumbs semantics are not generic approve/deny semantics. 6A does not replace or shorten admind's `approval_card`, reinterpret its answers, or apply wsd's line guideline to admind. No admind reaction parser is reused as a wsd authenticator.

S4 demonstrated native threads/reactions but left reaction removal untested. New members cannot read old history, and a successful add RPC is not proof of visibility. No 6A output claims joined, delivered, accepted or approved unless the supplied view explicitly represents that independently established fact. Implementing these facts is excluded.

## 3. Pure module boundary

Proposed location: `heterodyne.wsd.presentation`, separate from daemon/control/admind effect code. Public functions:

- `classify(envelope: ClassificationInput) -> Classification`
- `render(view: PresentationView, policy: RenderPolicy) -> RenderResult`

Both are total over valid version-1 inputs, deterministic, and perform no I/O. Invalid schema input returns a typed validation error before classification/rendering. No clock reads, randomness, environment/config lookups, callbacks, databases, subprocesses, network clients, transport handles or mutable singleton state. `now` and configuration-derived display policy are explicit immutable values. No import of the module may register effects. A static boundary test must reject effectful imports and inspect transitive dependencies of reused helpers; moving an effect behind a helper is not compliance.

Versioned closed immutable records reject unknown fields and unknown enum variants; there is no free-form metadata dictionary. UTF-8 decoding occurs before entry and rejects invalid encoding. Newlines are LF; text normalization cannot change approval effect/context. Inputs have a 256-KiB encoded ceiling, individual free-text values at most 16 KiB, and collections at most 100 entries. All timestamps are timezone-aware UTC; counts nonnegative; percentages 0–100 or explicit unknown. Oversize/invalid inputs yield stable reason codes without echoing content. Increasing limits is an explicit contract change.

These are presentation contracts, not native decision schemas. References used internally for correlation are opaque and never automatically displayed. Future adapters must construct views from their actual reviewed owner interfaces; no adapter implementation is included in 6A.

### 3.1 ClassificationInput and Classification

Input fields: `version=1`, `surface` (workstream/control), `eligibility` (verified-for-classification/unverified/rejected/replayed/trust-suspended), `text` (at most 4096 characters), `placement` (unthreaded/threaded), optional `target_kind` (task/approval/question/unknown), optional safe short bead label, and `target_current` boolean. Eligibility is supplied by a future trusted ingress, never parsed from message text. The pure function cannot prove that a caller supplied truthful eligibility; it must never be used as an authorization check.

Output is one closed variant, with a reason enum and at most a query name/validated display label:

| Input | Classification |
| --- | --- |
| Eligibility other than verified-for-classification | `Ignored`; no proposed reply |
| Exact read query below | `ReadQuery`; no fetch function or native target ID |
| Recognized mutation spelling | `UnsupportedIn6A` |
| Unthreaded noncommand text | `DeferredIntake` |
| Threaded noncommand, current task target | `DeferredSteering` |
| Threaded current approval/question text or reaction-like text | `DeferredDecision` (never approve/deny) |
| Missing/stale/unknown threaded target | `UnresolvedTarget` |
| Unknown slash command or malformed arguments | `InvalidCommand` |

Classification consumes the entire string after surrounding whitespace removal. Slash commands are case-sensitive, single-line and split on ASCII spaces; no quoting, interpolation or aliases. Read grammar: `/workstreams`, `/status`, `/status <bead>`, `/approvals`, `/queue`, `/queue <ws>`. Labels are 1–64 ASCII letters/digits plus `-`, `_`, `.`; they are display selectors, not paths. `/status` uses the supplied surface to choose the view. `/queue <ws>` never changes registration. Extra tokens, tabs/newlines in commands, control characters and overlong labels are invalid. Plain text containing a slash later in the string remains noncommand text.

Recognized but unsupported: `/approve`, `/deny`, `/pause`, `/resume`, `/stop`, proposed `/release`, `/harness`, `/trust-group`, including any trailing text. This recognition deliberately does not validate or return executable arguments. `/release` is reserved as the future Plan6 spelling of Parker.release, subject to its later command-integration design; 6A registers nothing. Reaction glyphs never become decisions in this API. Unknown command text is not echoed in errors. No positive acknowledgment is rendered for deferred/unsupported classifications; a later consumer must deliberately choose its response policy.

### 3.2 PresentationView

Common fields: `version=1`, kind (task/workstreams/workstream-status/control-status/bead-status/approvals/queue/approval/question/notice), surface, supplied `now`, `observed_at`, availability (reachable/cached-unreachable), cache state (fresh/refresh-required), and bounded kind-specific payload. Fresh means age at most 30 seconds. Future timestamps or disagreement between age and cache state are invalid. Older reachable snapshots render an explicit stale warning; pure rendering never refreshes them. Cached-unreachable always renders `⚠️ beads unreachable, data as of HH:MM UTC`, including when younger than 30 seconds. Missing snapshot yields a fixed unavailable result, never invented empty queues.

Payload definitions:

- **Task:** safe short bead label, safe title, agent/role display labels, branch display label, state enum, optional running duration; exactly one state reason variant. State/reaction mapping: received 👀, running ⏳, review 🔍, parked/deferred ⏸️, needs-operator ❓, closed ✅, failed ❌. Held uses ⏸️ with explicit held wording. Quota has `resume_at`; account-changed has a recovery condition and forbids `resume_at`; held has reason and the fixed release-only instruction. Unknown quota reset is displayed unknown, never a fabricated time.
- **Workstreams:** ordered rows with name, running/idle/all-blocked/deferred/paused state, current safe bead label, ready/parked/held/deferred/open-approval counts, and per-role headroom known percentage or unknown. Deferred reason uses the same tagged variants as Task.
- **Workstream/control status:** running duration, queue and parked/held/deferred rows with reasons, open approvals; control additionally includes workstream rows and needs-human items. Account observations have display name, window name, used/remaining unknown-or-percent, reset unknown-or-time, observation time and trusted/untrusted enum. Account-changed recovery text never adopts a window reset as its resume time.
- **Bead status:** state, branch, configured safe session label, last at most five ordered events, decision summaries, configured safe transcript display location and launch entries with account display names. Raw UUID/session tokens, opaque record IDs, credentials, pane dumps and host-specific private paths are invalid public fields. A missing safe transcript location renders `transcript: unavailable`, not a raw fallback.
- **Approvals:** supplied canonical ordering key `(created_at, opaque stable tie-breaker)` determines oldest-first; only safe labels/title/state are displayed. **Queue:** supplied pickup rank and safe blockers; renderer preserves rank and never computes readiness.
- **Notice:** fixed kind (all-blocked/delivery-unconfirmed/reminder/digest-summary/trust-suspended/unavailable), supplied facts only. Reminder text contains a reference to the original card, not its full content; no timer or due computation. A reaction/receipt is not authority.

Collections may overflow a physical message but not the input ceiling. Render all supplied rows in deterministic pages, preserving order. If the source has more than 100 rows, the producer must supply an explicitly incomplete view with shown/total counts and an opaque continuation availability flag; display `showing N of M; more available`. 6A does not issue a cursor or fetch later pages. It never silently presents an incomplete set as complete.

### 3.3 Approval and question payloads

**Wsd approval** requires safe bead label; explicit purpose design/execution; what; exact effect; why; context summary; a nonempty list of pinned full-context/design/effect links; a supplied 12-lowercase-hex context digest; and explicit `answer_policy=wsd-standard`. `wsd-standard` means a future owner has established that the ADR footer, including always, is appropriate; unsupported or unknown policies withhold the card. 6A neither grants always authority nor constructs/verifies the native digest. Full context remains on the approval bead and must be covered by the owner's genuine digest. A summary is permissible only for context, never for the exact effect.

Render five labeled parts in order, effect in a closed code block, then the exact ADR footer `👍 approve · 👎 deny · ❤️ always · reply to steer`, then the supplied short digest. Missing purpose is withheld rather than guessed. Any missing part/link or stale/superseded approval is withheld; no decision prompt survives on a partial card. A safe fixed status notice may point to details without claiming a valid approval card exists.

**Question** requires title/context, 2–6 uniquely labeled choices, exactly one explicit recommendation, and explicit answer semantics; yes/no uses literal yes/no labels. A recommended-no question states explicitly that the recommendation is no; no generic thumbs-to-approval conversion exists. This version renders textual choices and `reply with the choice label`; it does not render an actionable reaction legend for questions. **FYI** uses notice, never question/approval, with `FYI: no reply needed` and no prompt. These are new wsd templates, not a replacement for admind templates.

## 4. Render policy, safety and bounds

`RenderPolicy` supplies max characters per message (200–4000, default 2000), configured operator mention or none, and a fixed safe display-location mapping. It contains no file handles or policy-authority callback. Mention appears only in control approval/question cards; absence means no mention, not a live lookup. The transport later validates that a supplied mention came from configuration. Ordinary task/status notices never mention the operator.

All ordinary messages have at most eight physical lines including continuation markers. Approval messages target about eight lines but may use up to twelve to preserve the five mandatory parts and exact answer footer; this explicit interpretation of “about 8” is part of the new design approval, not an alleged old permission. The exact effect and its complete code block are atomic: if they cannot fit one part, withhold the card. Other complete labeled sections may occupy further parts. Each approval part stays within twelve lines and the character cap; parts constitute one complete logical card. A renderer returns **all parts atomically**, or Withheld; no incomplete approval result is valid. No answer footer appears before the final part. Each part identifies its index/count; the first keeps its kind/title first, with `↳ part 1/n` at the end; later parts start `↳ part i/n`. Section boundaries are the only split points for approvals; never cut the effect, footer, digest or an atomic link. A maximum of 20 parts is enforced for every logical view; beyond it return `NeedsDetails` for ordinary views or Withheld for approval/question. The transport/outbox required to deliver such a card safely is expressly deferred; this function's success proves no delivery.

Links are atomic and never split or shortened. Approval links are HTTPS commit-pinned repository permalinks containing a full 40-hex commit and a file path, optional line range. URL credentials, query/fragment payloads other than a line range, whitespace and control characters are invalid. Structural validation is not proof of repository existence, operator access or source provenance; later admission must validate those separately. No fabricated URL is substituted for a missing link. The only displayed hex identifiers are the approval digest and commit identifiers inside approval pinned links. Ordinary views show safe human display labels rather than raw internal identifiers.

Every visible string is checked using the existing redaction policy before output. Ordinary unsafe free text is replaced by a fixed `[withheld]` field, retaining an explicit incomplete marker; it cannot inject a command, mention, code fence, Markdown link or line break into a fixed field. Approval/question semantics may not be changed by redaction: if any protected text would change, withhold the whole logical card. Code-block content containing an unescaped closing fence is withheld rather than modified. Control characters, bidirectional overrides and invisible formatting controls are rejected. Renderer errors contain reason codes only. Titles/labels are plain text; output escaping is deterministic. Details cannot be used as a fallback to expose unsafe raw text.

`RenderResult` is exactly one of: `Rendered(parts, kind, complete, shown_count, total_count)`, `NeedsDetails(reason)`, `Withheld(reason)`, `Unavailable(reason)`. A part contains only text and an optional reaction **display** enum; no destination, native reply ID, effect, receipt, send callback or executable action. Pure output cannot attest to approval, delivery or completion. Incomplete ordinary results visibly say so; approvals/questions require complete=true. Reason enums are finite: invalid-input, unsafe-content, missing-required, stale-approval, unsupported-answer-policy, atomic-link-too-wide, excessive-parts, unavailable-snapshot. Internal debugging uses these codes without raw payloads.

## 5. Reuse and future integration

At source076, `Wsd.status` in `wsd/daemon.py` returns derived state/count strings; it lacks the full account, decision and display-safe DTOs above. `CtlRequest`/`CtlReply` in `wsd/ctl.py` are actual daemon control messages, not this presentation API. Do not widen them in 6A. A later read adapter must explicitly validate/project their fields; passing arbitrary status dictionaries to the renderer is forbidden.

`admind.commands.parse` handles admind's `!` grammar and effects; it is not the slash classifier. `admind.asks.approval_card` has its own whole-content checks. Reuse the principles, not that effect-bearing pipeline. `admind.chunk.pack` preserves atomic links/closing blocks and Amendment 1 markers, but allows arbitrary body-line splits and has no eight-line bound. A later implementation may reuse its pure atomic/marker primitives only after testing the stricter wsd bounds; using pack unchanged would not satisfy this contract. Existing redaction must be reused through a pure dependency path, with any dependency effects causing admission failure rather than a second divergent redaction policy.

No 6A type replaces native Beads, Plan5 decision/execution records, Parker holds, managed-install state or authenticated requester provenance. Integration needs a separate design pinned to actual then-current schemas, including one decision writer, mixed-mirror convergence, revocation/replay, release semantics and failures. It must preserve the source076 config compatibility change; this historical baseline is not a claim that canonical main has stopped changing.

## 6. Exact offline acceptance catalog

A later implementation must commit input/output fixtures with stable IDs below; every fixture has the full typed input, exact expected text/parts or exact result/reason, and no actual credentials/host identifiers. Synthetic display values only. Run deterministic tests without Marmot, native task DB, network, models or real user tmux. No placeholder “golden updated” acceptance: a changed expected value requires a reviewed contract explanation.

| ID | Required assertion |
| --- | --- |
| A01 | Same typed input twice, locale/timezone/environment changed, byte-identical output; import/calls perform no I/O |
| A02 | Reject unknown version/field/enum, invalid UTC, invalid percentages/counts, oversize input, forbidden controls and contradictory freshness |
| A03 | Unverified/rejected/replayed/trust-suspended all Ignored with no reply, regardless of forged operator text |
| A04 | All six read grammar forms classify correctly on workstream/control; extra tokens, tab, newline, path-like selector invalid |
| A05 | All listed mutation names yield UnsupportedIn6A without arguments/handler; text/reactions never become executable decisions |
| A06 | Intake/steering/decision/unknown-stale target variants; no classification acknowledges acceptance |
| A07 | All seven specified task reactions and held state; safe title/role/branch labels and no internal identifiers |
| A08 | Quota known/unknown reset, account-changed no time plus exact recovery condition, held reason plus release-only text |
| A09 | Workstream states/counts/headroom with unknown preserved; no missing-data-as-zero conversion |
| A10 | Workstream versus control status fields, needs-human, account windows/reset/age/trust; no inferred capability/failover |
| A11 | Bead status last five events in supplied order, decisions, launch accounts and safe transcript location; unavailable safe location stays unavailable |
| A12 | Approvals oldest-first stable tie-break; queue preserves supplied pickup rank and blockers |
| A13 | Fresh boundary 30 seconds, stale 31 seconds, future time rejected; cached-unreachable warning includes observation UTC time |
| A14 | Complete five-part approval, exact effect/code fence, exact footer, digest and atomic pinned links; context-only summary remains linked |
| A15 | Missing purpose/part/link, stale approval, unknown answer policy or changed protected text -> whole Withheld, zero prompt parts |
| A16 | Question choice bounds/uniqueness/recommendation and literal recommended-no wording; FYI never has answer prompt |
| A17 | Mention only control approval/question, never ordinary notice/workstream; configured absence causes no lookup |
| A18 | Eight-line ordinary and twelve-line approval bounds, character cap includes markers, deterministic multi-part boundaries and final-only approval footer |
| A19 | Link exactly fits/too wide, invalid/unpinned/credential URL, no link shortening/splitting or false validity claim |
| A20 | Multiline effect/fence injection, Markdown/mention injection, redaction ordinary versus protected content; no secret in errors |
| A21 | 100 rows, explicit source incompleteness and >20 parts; ordinary NeedsDetails versus protected Withheld; no silent omission |
| A22 | Fixed unconfirmed-delivery, reminder, all-blocked and trust-suspended notices; no receipt/clock/membership effect or unauthorized release wording |
| A23 | Unknown provenance/admind count observation cannot manufacture eligible/authenticated status; no admind guard policy mutation/import |
| A24 | Static import/API boundary: no transport/client/DB/subprocess/environment/clock/callback, action/destination/native-target fields or side-effect registration; reused helpers audited transitively |

Acceptance also requires named affected existing pure tests, formatter/lint/type checks and exact source/test evidence, scoped by the later implementation review. No tests or implementation are authorized by this planning commit. Full Plan6 integration/latency, actual delivery and recovery, both reference adapters, gatekeeper, cron/reconcile and three days with no operator intervention except approvals remain unfulfilled release obligations.

## 7. Approval, follow-up boundaries and rollback

The controller must obtain cross-model review (at most three rounds for this change), publish the exact signed revision with a direct commit-pinned GitHub link, then obtain a genuine human design approval that explicitly includes the early pure-slice sequence and twelve-line approval-part interpretation. A closed planning task or this document's existence is not approval. No deployment approval is requested by this design. A later implementation gets its own real claim, review and source pins.

Future design scopes: 6B authenticated read path/cache projection; 6C durable delivery/mirrors/rejoin/deadlines with Plan8 ownership; 6D supported mutation/decision cutover after Plan5 and release gatekeeper contracts. These are scope boundaries, not newly created tasks. Existing invite-recovery, Plan4c managed-install, Plan5/5i and scanner-consumer tasks are neither duplicated nor closed. No live rollback is needed for documentation or a standalone pure library; integration rollout/rollback requires a separate approved design retaining unknown outcomes and authentic history.
