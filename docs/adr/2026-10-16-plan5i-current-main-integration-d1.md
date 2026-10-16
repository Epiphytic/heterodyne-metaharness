# Plan 5i integration with the widened scanner — draft d1

Planning task: btq-fu366. This draft grants no implementation, integration,
host-change or live-test authority. The scanner candidate is still executing its
approved gates, so the exact receiving source commit is deliberately unresolved.
Do not submit an executable approval with that placeholder.

## Inputs and boundary

The integration map is `docs/reviews/plan5i-integration/source-map-d1.json`.
Historical source is `50fc42fb95dde0cdf38410c54e4e89bc3227f37b`, common base
`2c0c5b5b775ee652d2ce32c192091aa2314e5b32`, and deployed main is
`07637745fe75dc614109a0b8ba72d3489f262a97`. Closed btq-bswl5 approves the four
original implementation plans, explicitly excluding merges, host changes and
part 4 live execution. Closed btq-pxtig approves scanner implementation btq-1wu2i
against 076, without importing these historical consumers. Neither approval is
extended here. ADR revision16 remains governing at
`93d0e90b121a07db720d92924cda443a1cfc239c` (btq-xywu0).

The source contains more than asks/notices: it brings session events, brief,
review, journal, harness-management and runtime integration. The complete
base-to-source delta must be mapped to its original plans and actual code-review
records before selecting a landing sequence. No blanket source-tree checkout,
blind cherry-pick, fabricated review verdict or implicit live authorization.

## Receiving-base admission

Before freezing d2, pin the scanner's actual signed atomic implementation head,
its retained test results and actual review/disposition records, plus its
admitted merge output. Record the exact current source receiving each historical
component. If unrelated main changes arrive, revise the manifest and obtain an
appropriate amended approval instead of treating future HEAD as already reviewed.
The first approval must name actual implementation tasks and exact source refs.

The existing controller integration protocol records source base/head, native
original design approval, actual review output and every disposition separately
from integration and final-test attestations. Findings-only review output keeps
its actual verdict null; any human disposition is a separately authenticated
native authority. Adopting old reviews does not reset the three-round counter.
Approval for one component does not authorize another component's changes.

## Preserve current behavior while landing

Use the common-base-to-historical-source delta, overlaying it on the exact
receiving source. The map identifies thirteen paths changed on both sides;
resolve each explicitly. In particular preserve guard_state/membership authority,
guard quarantine and migration behavior, sandbox create intent and settlement
ledger, process-identity checks, bounded reaping and generated unit metadata.
Retain every current regression. Files absent from the historical tree are not
permission to delete current implementations or their tests.

Reconcile Plan4c T8/T9/T10 and T11 source/provenance before the Plan5i pieces
that depend on them. Preserve original dispatch order for briefs/events,
questions/approvals, close/review, then separately authorized end-to-end execution.
Do not infer completion from a branch name or old task title.

## Scanner consumer contract

Retain the complete approved scanner/redactor regression corpus and expected
classification; do not retune it to the integration's failures. Every newly
present consumer has nonempty actual test selection recorded before execution.
Run all asks question/approval/release tests, notices/state notices, harness,
approvals, brief, relay, review, bridge-flow and session-events tests, together
with current create-lifetime/guard/config compatibility regressions.

`asks.shown_text`, `reshown`, `shown_title`, first-line and choice validation
scan original and displayed forms where required. Add private tests for newly
recognized embedded vendor tokens and npub/nsec forms in original text,
truncation boundaries, escaped controls, titles, choices, refusal reasons and
answers. Secret-bearing questions must use fixed withholding text without raw
secret persistence, and remain actionable through the approved refusal/answer
flow. Tests inspect actual private journal/bead/relay payloads, not merely helper
return values. Never send fixture secrets or test operator messages live.

`Notices.send` checks complete original title/body before fit/truncation. Test
that a sensitive suffix beyond display bounds cannot evade withholding, that
withheld rows retain no raw text, and that fixed replacement notices retain the
approved at-most-once/unknown-outcome behavior across restart.

Harness install approval composition scans title, description and serialized ask
before native creation/posting. Managed manifest and source digests are legitimate
metadata; use the existing approved digest representation, preserve their exact
binding and do not globally exempt secret-looking text. Test ordinary installs
and newly detected malicious fields through the real private fake queue/relay.

Assert install-check detection is a semantic superset of runtime scanner
recognition for all retained forms, including consumer-added forms and config
metadata. Embedded/suffixed/uppercase filename coverage belongs to separately
tracked 4lsqy/xpcnj scopes; do not silently import an unfinished retention design.

## Final verification and release gates

After source reconciliation run formatting, typing, required install-agnostic and
secret checks, focused nonempty consumer suites, then the unfiltered hosted full
suite with required tmux and actual timeout/status recorded. Preserve private
local environmental limitations separately. No skipped required test is a pass.
A changed expected classification or raw-text retention finding stops this
integration for a concrete reviewed amendment; it is not automatically waived.

Any source changes receive actual Claude Opus built-in `/code-review`, maximum
three rounds per change. Retain candidate hashes, complete raw outputs, review
provenance and resolved/rebutted findings. Source admission, integration,
applicability, final-tested, ready and closure records remain distinct.

Live part4 and deployment are separate concrete execution decisions over the
final source and installed state, requested through Marmot where required.
Working admind alone does not establish wsd acceptance. Full release also needs
runtime/adapters, gatekeeper/cron/reconciliation and the governing three-day
workstream acceptance. Explicitly deferred operator-cleanup work (qdyj7) remains
tracked without inventing a release blocker or silently marking it complete.
