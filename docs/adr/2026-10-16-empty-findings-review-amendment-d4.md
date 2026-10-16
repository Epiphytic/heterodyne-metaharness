# Ordinary built-in review: bounded automatic capture and readiness (d4)

Planning task: btq-litno. **UNREVIEWED, INACTIVE DESIGN REVISION.**
The preceding design exhausted its three rounds; final R3 was REVISE with 30
findings. This file does not authorize another invocation, implementation,
deployment, or reset. Exactly one additional review would require a genuine
explicit exception for this same lineage. A further failure would hold again.

## Authority, history, and explicit policy changes

Canonical original: `3158263d049ea0a6b5cd398b40df95b704836d4d:docs/designs/2026-10-16-controller-integration-evidence.md`,
SHA256 `350ee897aa5676999f57b34fc42642365605adeeea2731ab7568643fa32919a9`,
approved through btq-xu3ax. Its canonical identity remains distinct from any
byte-identical approval archive. The archive is not a replacement historical
approval or evidence that a new amendment is approved.

Preserve frozen d3 commit `7f73a3259ccff7158d959f28f7c52900d6ea0adc`, its
315 archive, all earlier design revisions, R1–R3 outputs and actual REVISE
outcomes. Private author triage `litno-design-r3-triage/findings.json` preserves
all 30 R3 findings; its `inputs.json` pins the actual report/design/source bytes.
Portable author dispositions and exact input pins are in
`../reviews/2026-10-16-litno-r3-author-dispositions.md` and its adjacent
`2026-10-16-litno-r3-inputs.json`. These are author proposals, never reviewer
clearance. Publish the actual report or genuinely accessible immutable artifact
link before the human ask; a private pathname is not a GitHub publication.

This revision explicitly amends original315's per-record human review-import
requirement: a supported **controller-owned reserved capture** can be ingested
and assessed automatically. Manual, foreign, old and unsupported captures retain
the original authenticated review-import path. It also replaces d3's mandatory
max/Agent/sweep and consumption-certification requirements with ordinary bounded
high review, and replaces proposed per-task amendment edges with direct native
rule-approval validation. None of these changes is currently active.

Original task design pins, native dependencies, ownership, admission/scope,
review budgets/history, integration, testing, delivery and deployment gates all
remain independently required. No routine merge/card approval is added when
those gates and an actual supported clean review pass. New or changed designs
and deployments still require genuine human approvals.

## Accepted assurance and threat boundary

Trust the existing local host, controller and installed Claude CLI as for an
ordinary CLI code review. Record observable settings and tools; do not require
an impossible proof that all managed hooks are absent. Managed settings may
apply. No kernel confinement, provider attestation, guaranteed model cognition,
complete byte consumption, exhaustive search, or semantic theorem is claimed.
Requested Opus is not proof of an inner model. A reported cap is not "uncapped".

The reviewer is asked to assess the complete frozen change and retained prior
concerns. Authentic final empty findings plus structural correction evidence
is accepted ordinary cross-model review assurance, not proof every concern was
understood. Explicit partial/skipped review, required-read failure, target
mismatch, contradiction or capture error holds. Ordinary compaction or unknown
internal cognition alone is not reclassified as an error or proof of success.

## Frozen inputs and one supported launcher profile

Implementation extends the existing Y controller, reconciling its actual source
before changes; inspected historical Y `ed9659` is not a claim of current HEAD.
Create the private neutral directory exclusively (0700, no reuse), pin its
canonical path/device/inode, and reject replacement; no Git/CLAUDE.md ancestry.
Materialize exact admitted base B and final head S from immutable Git objects:
`change.patch` is the full binary-capable B..S diff; `base/` and `head/` contain
tracked source bytes; `concerns.json` and `request.txt` retain prior concerns and
instructions. Reject unsafe/symlink escape paths. Record per-file hashes, Git
blob IDs/modes, changed paths and canonical manifest hash. Include all admitted
deliverable paths, including documentation; no universal "evidence-only" rule.
Base==head or a delta outside admitted scope holds. Auxiliary receipt changes
cannot serve as correction evidence for unrelated code findings.

Preflight verifies executable/version/hash, source objects, files, known CLI
flags and built-in recipe, writable capture directory and actual native gates.
Pin initially the inspected Claude Code 2.1.301 executable SHA256
`bc1aad9982c7b59c7d889ff92595d61cf46e0a6eae074cb551d932a989bcd222`.
Changed executable/recipe requires a reviewed profile update and human approval
when its behavior changes; do not silently fall back to a plugin or another tool.

Use fresh print mode, `--model opus --effort high --output-format stream-json
--verbose --restricted --tools Read,Grep,Glob --strict-mcp-config`, an explicit
empty MCP config, `--setting-sources ''`, `--settings` with
`{"disableAllHooks":true}`, `--permission-mode dontAsk --permission-prompts none
--no-chrome --no-session-persistence`. Use the actual `--disallowedTools` flag to deny Agent, Bash, PowerShell,
Edit, Write, NotebookEdit, WebFetch and WebSearch. No resume/continue/fallback,
external plugin, Agent, verifier or sweep. Restricted mode/settings are
application controls, not OS isolation. Record environment variable names and
non-secret policy values affecting configuration; never archive credentials.
Preserve existing authentication without exporting its value into evidence.

Pass the entire prompt as ONE literal argv element after a literal `--`
separator, using Popen argv without a shell. No prompt text becomes CLI flags.
The first prompt line is exactly:
`/code-review high --max-findings all <absolute-review-directory>/change.patch`
The remaining request identifies this FILE as the complete change, the adjacent
frozen trees as context, B/S/manifest hashes and the full concern ledger. Ask for
one final fenced JSON object `{"findings": [...]}` and truthful limitations.
No synthetic APPROVE is requested. The explicit FILE target avoids the built-in
implicit upstream/main/HEAD~1 selection; observed attempts to substitute a
narrower target or unavailable Git/Bash hold, with no silent tool widening.
Internal effort/finding limits are captured when observable; no max or all-sweep
claim follows from the requested arguments. Failure to produce this output
grammar is an actual failed attempt, not permission to author-normalize it.

## Reservation, finite capture, and automatic ingestion

All new artifacts use canonical UTF-8 JSON (sorted keys, compact separators,
newline), SHA256, duplicate-key rejection and original safe private artifact
rules. New controller-owned inputs reject unknown fields; raw native CLI events
are retained intact and parsed only for explicitly supported fields.

New command interfaces after approval:
`review-launch --lane L --profile P` reserves then issues exactly once;
`review-capture-ingest --lane L --reservation R` seals that reserved capture;
`review-assess --lane L` derives readiness. None takes a caller-supplied verdict,
pass flag, capture path, approval identity, ordinal or arbitrary foreign output.
Existing manual `review-import` remains separately authenticated.

Use original controller-to-lane lock order for journal operations. Before issue,
append/fsync reservation with lane/lineage, monotonic ordinal, controller actor,
B/S, input/profile/authority hashes, random nonce, capture ID and deadline. Create
capture files exclusively under that reservation, append an issue-intent event,
then call Popen once. A crash after intent is uncertain issuance: conservatively
retain its consumed reservation; never relaunch or infer that no process ran.
No lane lock is held while waiting for the CLI. Original maximum three actual
review rounds and conservative reservation accounting remain unchanged.

Bound invocation at 1200 seconds; bound native stdout event stream at 256 MiB,
stderr at 16 MiB and final-result text at 16 MiB. Read both pipes concurrently,
hash retained bytes, kill/reap the owned process group on timeout/overflow, and
record the actual reason/exit. Never clip then parse as complete. Preserve
partial captures as failed/unknown evidence. No automatic retry/resume or extra
model call. Static preflight rejection before reservation consumes no model call.

Capture schema `controller-reserved-capture-v2` has exactly: schema,
reservation_digest, nonce, lane, lineage, ordinal, actor, base, head,
input_manifest_digest, profile_digest, issued_at, completed_at, process_exit,
outcome, stdout_sha256, stderr_sha256, stdout_bytes, stderr_bytes,
final_result_sha256 (nullable), session_id (nullable), observed_events_digest,
observed_limitations, reviewer_identity. Identity contains requested_provider,
requested_model, reported_provider (nullable), reported_model (nullable), and
identity_evidence_digest. Keep requested and reported identities separate; never
fill unknown reported identity from request. Known actual author-model identity
violates cross-model review and holds; unknown inner model is disclosed ordinary
trust in this requested Claude reviewer, not a falsely certified distinct model.
Outcomes are completed, failed, timeout, overflow, unknown.
These are local provenance records, not provider-signed identities.

The native stream must consist of complete JSON events and exactly one terminal
`type:result` with `subtype:success`, `is_error:false`, a string `result` and
consistent session identity and process exit0. A pinned finite fixture table
defines supported initialization, assistant, user/tool and terminal events. After
the terminal result allow only documented `prompt_suggestion` events, retaining
them without parsing as findings; reject any second result, later tool/assistant
content or unknown event type. Harmless usage/timing fields are not authority. Error results, malformed/incomplete
streams, failed required tools and explicit target/partial-review errors hold.
Use the actual terminal result string, not an inner assistant/system/tool role,
a ReportFindings call, a prompt echo or nonce-plus-stdout invented transcript.
The adapter's supported native event table must be pinned with recorded fixtures
and checked against actual capture; unexpected schema holds without relaunch.

Under the lane lock, ingestion revalidates reservation/actor/input hashes, source
and profile, seals files, appends exactly one capture record referencing their
hashes, and fsyncs. Identical repeated ingestion returns the existing record;
changed bytes or another capture for that reservation holds. All outcomes are
ingested truthfully; ingestion success is not readiness. Interrupted unsealed
captures can only be inspected/sealed as failed or unknown, not edited into pass.

## Parser, concern ledger, and derived assessment

Parse only the complete terminal result string. Require exactly one fenced JSON
object with exactly `findings` whose value is an array. Reject duplicate keys,
multiple candidate blocks, bare arrays, absent findings, malformed/mixed output,
conflicting explicit REVISE, errors, skipped/partial claims or stated unresolved
concerns. Retain all surrounding prose; ordinary explanation is permitted and
cannot be discarded to hide conflicts. Nonempty arrays record findings and hold.
Unknown/ambiguous prose holds rather than invoking author normalization. A typed
parser and deterministic conflict rules/negative fixtures are part of the pinned
profile; implementations must not make a second unbudgeted model classification.
A genuine empty array under this grammar can pass without the word APPROVE.

Prior nonempty or explicit REVISE reviews of the same S block unchanged-source
rerolls, including REVISE with an empty array. Every prior concern has immutable
ID, original review/source/path anchor and one correction record binding the
original source, correcting commit/tree, final blob/path, and actual named test
command/environment/log/result hashes. Require correction delta within admitted
deliverables and retained final corrective content; hashes do not prove semantics.
For design/docs work the real document correction is eligible. The final request
includes every concern, proposed fix and evidence; ordinary reviewer assessment
of that package is the semantic check, without inventing a guaranteed per-finding
output channel. Disputed, accepted-risk, missing or unresolved concerns remain
holds requiring the original separately authenticated disposition path. Author
labels alone cannot clear them. Preserve failed rounds and exact final source.

Keep existing reviewer records `explicit-verdict` or `finding-only`; there is no
existing acceptance sum type to extend. Add a separate derived record schema
`controller-review-assessment-v2` with exactly schema, kind
(`review-complete-no-open-findings`), actor, lane, lineage, base, head, ordinal,
review_record_digest, capture_digest, input_manifest_digest, concern_ledger_digest,
profile_digest, rule_approval_id, rule_revision, rule_ask_digest, assessment_digest,
reviewer_verdict (null for this route), operator_disposition_acceptance (null).
Assessment digest hashes the other canonical fields; no self-hash recursion.
No reviewer APPROVE or human acceptance is manufactured. Missing/unknown fields,
wrong versions, mutable inputs or unsupported parser/profile refuse this route.
Ready and close independently recompute from current artifacts/native gates and
include this digest in existing delivery evidence. They retain truthful review
lines with null verdict; old explicit and authenticated paths remain distinct.

## Genuine activation and approval-card consequences

Before any rule activation, publish this exact revision, original canonical315
reference plus byte-identical archive, actual new review result and all30 history.
Create an approval-compatible byte-identical copy of this revision under the
actual configured ADR path glob (default `docs/adr*/*.md`) in a real Git commit.
Keep separate source/design and archive-commit identities with exact hashes.
Native ask.refs must bind archive file kind/path and that commit ID; adr_revision
must equal that independent expected archive revision. Root runs the genuine
approval precheck and retains PASS before sending. No fake approved_* fields.

Installed profile pins the real amendment approval ID, archive revision/hash,
original315 identity and profile/parser/schema digest. Before launch, assessment,
ready and close call actual `approval_valid(approval_id, expected_archive_rev)`
and verify the exact ask refs/digests and selected profile independently. Denial,
revocation, mismatch or unavailable native state holds the new route. This is an
additional controller-enforced native design-rule check, not an absent native
edge magically enforced by the queue. Attach no new per-task block edge, overwrite
no primary approval, mutate no task notes/dependencies and require no parked-lane
ack or management card solely to activate this rule. Existing lane scope/claim
and original task gates still apply honestly.

The human card must disclose automated ingestion replacing routine per-record
imports, bounded ordinary high review trust, no exhaustive/context attestation,
null verdict plus derived clean assessment, direct rule validation, preserved
primary gates and manual-risk path. Approve enables reviewed implementation of
this rule; deny leaves old behavior. It is not deployment approval. Future valid
lanes use the installed approved version and their own complete admission/history,
not a closed Y task's ownership. No silent legacy migration. Changing semantics
requires a new genuine design decision. Selecting a superseding installed rule
requires that rule's explicit approval; native approval_valid has no implicit
supersession feature. No permanent denied edge is introduced.

## Required offline implementation acceptance

Test clean real-shaped stream/fenced empty output with null verdict; nonempty,
REVISE-empty, missing/duplicate/mixed output, echoed prompt, inner-only output,
unknown events, read denial, target mismatch, timeout/overflow/interruption,
uncertain launch and no replay. Test reservation/actor/nonce/source tampering,
foreign/manual ingestion refusal, idempotency and concurrent seal races; no caller
pass flag. Test changed-source concern anchoring/reverted fixes/missing evidence,
same-source reroll and all prior ledgers. Test native approval wrong revision/ref,
unapproved/denied/missing, primary gates unchanged, ready/close recomputation,
old record compatibility and no fake verdict. Use offline fake processes and
recorded fixtures. Any real acceptance invocation consumes its admitted lineage
round. No implementation or deployment occurs until genuine approval and the
separate reviewed-source/host rollout gates are satisfied.
