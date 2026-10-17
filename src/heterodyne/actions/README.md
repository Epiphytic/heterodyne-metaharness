# Pure action contracts (Plan5 Stage 0 / btq-71276)

This package implements the pure P0/P1 boundaries admitted by `btq-l6eeo`.
Its design and governing amendment are archived at revision
`cb180b72c21a157f721be1e4fa57ccfd03d4a11b`, under
`docs/adr/2026-10-16-plan5-actions-and-executor-d4.md` and
`docs/adr/2026-10-16-plan5-governing-amendment-admission.md`.

`decode_intent(bytes)` accepts schema version 1 and exactly one tagged action:
`push_branch`, `open_pr`, `merge_pr`, `deploy`, or `notify`. Agent input contains
only the action and explanatory context. Origin, resolved resources, approval ID,
request revision, commands, environment, and unknown fields are rejected.

The host separately supplies frozen `Origin`, `Resolved`, and `ActionConfig`
records to `assemble_envelope`. `decode_envelope` checks their shapes and exact
configured repository/remote/target/channel bindings, pinned object IDs, and
cross-field consistency. Typed host data is **not authentication**: a later
integration must obtain these facts from verified launch/config/native sources,
check current generation and ownership, and prohibit client-supplied replacements.
Policy revision and permission-widening facts likewise require trusted producers.
No session socket, native reader, origin verifier, production persistence, or model is added.

The fixed JSON limits are 32768 encoded bytes, depth 8, 512 total scalar/container
nodes (including object keys), and 8192 characters per string. Text rejects C0,
DEL/C1, surrogate, Unicode formatting characters, and Unicode line-separator controls and reuses `secret_value`.
Unknown fields, duplicate keys, floats, boolean integers, and unsupported versions
refuse with safe fixed diagnostics. Request revisions/generations/PR numbers are
positive integers at most 2^31-1. IDs use the integrated queue grammar
`[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}`; aliases and opaque host identities use separate
bounded grammars. Configuration has at most 64 unique entries in each namespace,
with separate configuration bounds of 131072 bytes and 8192 nodes.

Repository configuration explicitly selects `sha1` (40 lowercase hex digits) or
`sha256` (64). Pure refs are restricted full `refs/heads/` names at most 255 ASCII
characters; body references are exact commit plus restricted relative object path.
Runtime Git ref/object checks, canonical repository identity, requesting-bead
branch ownership, protected destination policy, forge checks, and verified body
object loading are required by later integrations. This package performs none of
those runtime checks and offers no path or Git access.

`action_data` returns the complete validated action envelope, including immutable
resolved targets, origin, approval ID, and request revision. Future native ask
assembly must retain the canonical title, description, whole ask, and resolved
references and reuse the existing native `context_digest` unchanged. This package
does not implement that digest or claim a projection alone is approval evidence.
Authenticated approver identity is attached only after a genuine decision by the
future integration, never by an agent or inside its own digest.

`evaluate_policy` composes existing `effective_tiers`, then applies the outward
escalation floor to all five kinds. Permission widening is hard denied; host
restrictions cannot lower severity. There is no permission-model fallback.
`denial_key` is a separate SHA256 intent-denial fingerprint, not an approval
digest: it includes kind/effect/resolved facts and logical session scope while
excluding explanatory prose, generation, approval ID, and request revision.
Future persistence must keep its denial across generations of that same session.
`materiality` compares already canonical semantic projections conservatively;
unique equal meaning is minor (uniqueness defaults to false), while ambiguity or changed target/requirements/
question/action/outward/destructive meaning requires confirmation. It performs
no parsing, alias resolution, routing, confirmation, or dispatch.

`REGISTRY` has exactly five immutable unavailable entries. `adapter_refusal`
validates and always refuses; there is no enable switch or dispatcher. P2/Q0,
scanner integration, Plan5i consumers, decision writers, executors, credentials,
and deployment remain separate prerequisites. An unavailable adapter remains
unavailable even when a pure policy result is `escalate` or a human answers an ask.

The synthetic named test module is `tests/test_actions_p0.py`. It does not open a
store or run Git, processes, network, native queue, scanner, or model operations.


## P1 offline durability and reducers (btq-efddg)

P1 adds no daemon, CLI, configured factory, enabled adapter, production schema
migration, native queue reader/writer, launcher, credential, or transport. Every
boolean observation is a **synthetic port fact**, not a proof of authentication.
A future admitted integration must derive it from authoritative current sources.
`UnavailablePorts` always refuses and all five registry entries remain unavailable.

`requests.new_request` validates the immutable action/origin/resolved tuple before
allocation. `issue_allocation` must be committed before calling even the fake
create port. Its creation key contains workstream, logical session, bead and
sequence. Unknown acknowledgement permits only three read-only lookups, due at
1/5/30 seconds, and unique adoption. No lookup authorizes another create. Every
observed shell remains associated; duplicate history requires an exact, currently
authenticated repair observation with closed-orphan readbacks. That repair also
cannot recreate a shell. The reducer then orders blocker creation, park while
retaining the claim, exact requested-source landing, whole native ask binding,
sufficiency, and filed readback. Source/generation/digest changes cannot skip these
steps. Two same-session grooming revisions clear old ask/judgement bindings, retain
session denial history, record a nondecreasing generation, and require a fresh
park readback before landing. Prior denied canonical identities cannot be groomed
back into an approvable request.

`pin_ask` retains opaque whole canonical native bytes plus its supplied native
digest. It does not compute, replace or authenticate that digest. The later native
adapter must assemble and compare the full action data and native context, using
the existing native algorithm. Passing fixture bytes here grants no authority.

`decisions` separates durable inbox recording, first valid decision, one write intent, exact native
readback, close, pin finalization, and committed state. Close alone is insufficient.
Unknown writes permit readback, not another write. Surface, current principal,
purpose and current digest observations are all required. A heart's optional
follow-up proposal has separate bookkeeping and cannot rewrite the original event.
A revoked/stale prewrite event can be marked invalid; a write-issued event requires
exact authenticated conflicting native readback before replacement. Every incoming
event retains an inbox record, including duplicate/superseded decision handling.
A previously superseded candidate can be reconsidered when the earlier candidate
is invalidated, without duplicating its inbox row. An invalidated event cannot
resurrect via replay. An exact authenticated native-conflict readback is adopted as
native-written, never submitted as another native decision write. Authenticated
groom/deny cards permit denial at ask-pinned/sufficient; they never grant approval
before filing, including after two exhausted grooming revisions.
Denial reasons are bounded to 1024 Unicode scalars, with the same text-safety rules.
Permission widening defaults to true (hard deny); callers must positively establish
non-widening. This is a safety default, not a permission-model implementation.

`dispatch` binds one approval/revision/digest/target/attempt and startup nonce hash.
Preparation has 30 seconds and claims have five seconds, both clipped by the
original fixed 60-second deadline; retries never reset that deadline. At most three
launcher preparations use distinct startup/claim nonces. Conditional claim and
retirement must share the future native atomic boundary. The fixture journal CAS
models that boundary, committing consumed state before acknowledgement. Consumed,
unknown or crashed attempts never become a fresh initial-effect capability.
Reconciliation permits three read-only observations at 1/5/30 seconds; absence is
not replay permission. Confirmed success plus cleanup releases locks. Exhausted
absence needs exact currently authenticated fixed repair, which can settle the
failed attempt and release its locks, but cannot retry it. Actual authentication,
process-tree cleanup and effect evidence remain unavailable adapter obligations.
Unconsumed held attempts also support exact authenticated no-effect settlement;
ambiguous claims first exhaust the bounded readbacks. Persisted `dispatch_kind`
distinguishes the retained initial attempt from separate read-only reconciliation
rows. Each reconciliation has a distinct attempt and startup/claim nonce lineage;
settling its row cannot release the initial attempt's resources or permit an effect.
Reconcile records cannot become ready, receive an effect nonce, or consume a claim.
`finish_reconciliation` only records that read-only invocation's evidence/cleanup;
parent effect observations remain separately bound. The journal permits one
unsettled reconciliation and three total retained invocations per approval.

`settle_dispatch` additionally accepts an exact authenticated, explicitly
operator-attested success after exhausted unknown readbacks and cleanup proof.
Its `recovery_operator_event` distinguishes that disposition from machine evidence;
it must not be rendered as machine-verified or attributed execution. Conflicting
absent/success attestations, stale pins and missing cleanup refuse. The compatibility
`settle_failed_dispatch` accepts only absent outcomes. Neither can grant a claim.
An already consumed attempt retains its consumed flag forever.

`FixtureJournal` only creates/opens explicitly private temporary fixture databases,
with a separate application ID and exact schema. Its v1-to-v2 migration is an
explicit rollback-able **fixture** migration, not a WSD/native migration. Requests,
versions and audit events commit together under BEGIN IMMEDIATE. Capacity is two
outstanding requests per workstream/bead across sessions/generations, 128 globally,
4096 retained requests and 16384 audit events. Full storage holds for explicit
recovery; history is not silently pruned. Terminal capacity is freed only after
all associated shells and blockers have consistent terminal readbacks. Phase-specific
settlement additionally requires a committed deny, a settled successful initial
dispatch, a known-unposted cancellation, or exact authenticated allocation abandonment.
Denial settlement and the session denial row commit atomically. Dispatch insertion
corroborates the same journal's committed execution approve and exact digest/revision
in its transaction; constructor booleans alone cannot authorize it. Repeated exact
terminal reports/settlements are acknowledged without new events, including after
restart and lost acknowledgements. Native
Beads remain authoritative in any future integration.

Sorted resource, canonical-effect and approval locks survive restart and uncertain
outcomes. The approval's dispatch row is retained permanently, preventing initial
replay even after settlement. A denied canonical intent is blocked before shell
allocation across generations of its logical session; alias spelling is excluded
from the identity while real effect fields remain. A changed intent must retain
its session's prior denial identities (at most 64, otherwise hold) rather than
pretend that the previous denial did not occur. These fingerprints and fixture
row locators are separate from native approval digests. Existing request progress
refreshes denial history from the same journal transaction; a later unrelated
denial cannot freeze cleanup or grooming. A revised or newly filed denied effect
still refuses. The journal recomputes canonical identities and reconstructs legal
Request, Decision and Dispatch transitions through the closed pure reducers.
Arbitrary callback output cannot skip phases, erase immutable pins/history or
release an unknown/filed request via cancellation. These checks corroborate
synthetic observations; they do not authenticate future real adapter facts.

Named synthetic offline tests are `test_actions_p0.py`, `test_actions_p1.py`,
`test_actions_p1_dispatch.py`, `test_actions_p1_hardening.py`, `test_actions_p1_r1.py`, and `test_actions_p1_r2.py`. SQLite operations
only target temporary test files. Fake ports are in-memory and cannot reach a
queue, session socket, effect target, model, service or network. Passing them does
not establish live adapter readiness or authorize deployment.

Remaining integration obligations: no production authentication, cleanup, timer or
expiry/reboot observer is implemented. An uncertain dispatch retains its locks;
clock expiry alone never proves launcher exit. `denied` is a reserved dispatch
state; authenticated request/decision denial currently prevents dispatch insertion.
The unchanged strict text codec rejects Unicode format characters (including
ZWJ/ZWNJ); denial reasons now consistently convert that failure to `Refused`.
The exact v2 fixture schema has no session-key denial index; its bounded scan is
not presented as constant-time. Audit capacity uses the integer-primary-key maximum
rather than a repeated full event count. No historical fixture rows are deleted.
