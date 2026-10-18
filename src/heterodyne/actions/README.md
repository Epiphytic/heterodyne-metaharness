# Pure action contracts (Plan5 Stage 0 / btq-71276)

This package implements only the pure P0 boundary admitted by `btq-l6eeo`.
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
No session socket, native reader, origin verifier, persistence, or model is added.

The fixed JSON limits are 32768 encoded bytes, depth 8, 512 total scalar/container
nodes (including object keys), and 8192 characters per string. Text rejects C0,
DEL/C1, surrogate, and Unicode line-separator controls and reuses `secret_value`.
Unknown fields, duplicate keys, floats, boolean integers, and unsupported versions
refuse with safe fixed diagnostics. Request revisions/generations/PR numbers are
positive integers at most 2^31-1. IDs use the integrated queue grammar
`[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}`; aliases and opaque host identities use separate
bounded grammars. Configuration has at most 64 unique entries in each namespace,
subject also to the total byte/node limits.

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
unique equal meaning is minor, while ambiguity or changed target/requirements/
question/action/outward/destructive meaning requires confirmation. It performs
no parsing, alias resolution, routing, confirmation, or dispatch.

`REGISTRY` has exactly five immutable unavailable entries. `adapter_refusal`
validates and always refuses; there is no enable switch or dispatcher. P1/P2/Q0,
scanner integration, Plan5i consumers, decision writers, executors, credentials,
and deployment remain separate prerequisites. An unavailable adapter remains
unavailable even when a pure policy result is `escalate` or a human answers an ask.

The synthetic named test module is `tests/test_actions_p0.py`. It does not open a
store or run Git, processes, network, native queue, scanner, or model operations.
