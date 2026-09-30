# ADR 0001, response to design review r1

- Reviewer: gpt-6-sol (medium effort). Author: claude-opus-5-5. Mode: cross-model.
- r1 verdict: REJECT, with 11 blocking and 2 non-blocking findings.

| # | Disposition | Where it's addressed |
|---|---|---|
| 1 | **Fixed.** `wsd` uses btq's `Queue` library with an explicit `wsd` agent, workstream and session. The ADR adopts btq's worktree convention (`<repo>-btq-<id>`, branch `btq/<id>`). | §4.3, §5.2 |
| 2 | **Fixed.** `wsd` holds claims under its own stable queue identity and never unclaims. A parked bead stays `in_progress` with a `v2:parked` label and a blocking edge. Resumable beads come from `wsd`'s own query over its claims. Parking is journaled, idempotent and replayed after a crash. An uncertain queue write is read back before any retry. | §4.3, §5.2, §3.3 |
| 3 | **Fixed.** The order is: decision committed, then execution, then result recorded, then close. The approval bead closes only on success. A failure gets `needs-human` and the bead stays open. An uncertain outcome is reconciled against the target's state and never blindly retried. | §5.4 |
| 4 | **Fixed.** No git or other credentials in the sandbox except model auth. Synthetic `$HOME`, an egress proxy allowlist, and a per-session authenticated socket with no control operations. A fail-closed launch self-test, whose probes are S3's acceptance criteria. Model-credential exfiltration is a documented residual risk. | §5.3, §7, §13 |
| 5 | **Fixed.** A closed, typed action registry with fixed fields, `expected_sha` and an idempotency key equal to the approval ID. The operator approves the payload verbatim. A separate `wsd-act` effector user holds the credentials and revalidates immediately before execution. Anything outside the registry is an ask, never an action. | §3.1, §5.3, §5.4 |
| 6 | **Fixed.** A single serialised decision queue in `wsd`, the single writer. The journal commit is keyed on the approval ID. Events are deduplicated on surface plus event ID. A later conflicting decision is logged and has no effect. A forge review counts only for the payload's SHA; a new push invalidates it, and dismissing a review cancels the decision. | §5.4, §5.5, §3.4 |
| 7 | **Fixed.** An approver identity map in `policy.json`. Ingress requires the MLS-authenticated sender pubkey (from `wn-agent`, not from message text), a registered group ID and replay protection. Membership changes suspend approvals until `/trust-group`. admind uses the same check. | §3.4, §8 |
| 8 | **Fixed.** The hook shim fails closed, except for a locally classifiable, sandbox-confined class. Spooled events are untrusted observations only. | §10, §3.3 |
| 9 | **Fixed** by narrowing the claim. A per-record ownership table and a startup recovery order. The SQLite journal is authoritative for operational state and is backed up. Lost message maps mean cards are re-posted. | §3.3, §2 |
| 10 | **Partly rebutted, partly fixed.** See below. | §8 |
| 11 | **Fixed.** Evidence comes from a launched-session record: the configured model and the model the session reported, plus the session ID and the reviewed `BASE..HEAD`, findings and disposition. If the models disagree, the review is invalid. Close is rejected if HEAD moved after the review. | §4.1, §5.8 |
| 12 | **Fixed.** A delivery-receipt timeout raises a control-group alert. If that fails too, a local alert file that admind relays on its own. | §6.2, §8 |
| 13 | **Fixed.** A v1 minimum slice: Linux, both adapters, Marmot-only approvals. Phase 2 covers macOS, Claude channels, GitHub and Radicle, each gated on its spike and its own design round. | §12, §13 |

## Finding 10: admind

**Rebutted in part: the privilege level.** The operator explicitly chose (2026-09-29) that admind runs as `openclaw` with permission prompts bypassed and no root. admind exists to repair anything the harness can break, including `wsd`, `wsd-act`, the sandbox profiles and the Hermes gateway. A least-privilege identity would have to be granted each of those repair paths in turn, and would fail on whichever one was missed during an incident, which is exactly when admind is needed. Requiring per-action confirmation would turn one passthrough message into a second round trip, also during an incident.

**Accepted:**
- "No LLM in between" is reworded to "no *gatekeeper* LLM between the operator and the admin agent".
- `!restart` accepts only units from a fixed allowlist.
- The sender must be MLS-authenticated as the operator (§3.4).
- admind's Marmot keys are readable only by its own unit.
- Every message and action goes to the append-only log.
- The build order no longer says admind is "first": it comes after the spikes, and it doesn't depend on the security boundary being demonstrated, because it deliberately sits outside that boundary.

**Residual risk, accepted by the operator:** anyone who can send authenticated operator messages to the admin group has `openclaw`-level control of the host. The protection is the operator's Marmot key, and nothing else.

## Round 2 fixes (after r2 review)

- **r1 #2, one claim per worker:** each bead now gets its own deterministic btq worker session, `uuid5(NS, "{ws}:{bead}")`. This works with btq unchanged, because `claim()` limits active claims per *worker* and `ready()` accepts unlabelled beads from any session (§4.3, §5.2).
- **r1 #6 and #9, decision durability:** the approval bead is now the commit point. The flow is serial, single-writer read, then write, then read back. SQLite holds only a pending-event inbox that is reconciled against the bead (§5.4, §3.3).
- **New: automatic push:** removed. Nothing is pushed except by an approved `push_branch`, `open_pr` or `merge_pr` action run by `wsd-act`, which takes over the integration role PICKUP gives to Bel (§5.3).
- **New, non-blocking: `kind:confirm` routing:** these beads are deliberately unrouted (no `agent:` label), and only `wsd` resolves them (§5.1).

## Round 3 fixes (after r3 review)

- **r1 #2, btq identity:** the ADR now spells out the prerequisite setup: `wsd` in btq's `AGENTS`, a Dolt user and a credentials entry, plus a PICKUP note. btq's claim and ready logic stays unchanged (§4.3).
- **New: pause gate:** there is one shared per-workstream pause flag, on the workstream-session worker. `wsd` re-checks it under a per-workstream claim lock immediately before every claim, and `/pause` takes the same lock (§4.3).
- **New, non-blocking: header:** updated to revision 4.

## Round 4 fixes (after r4 review)

- **Pause race:** the supported pause paths, `/pause` and `wsctl pause`, go through `wsd` and take the claim lock. A direct `btq pause` is documented as best-effort: it takes effect from the next claim check, with at most one claim already in flight (§4.3).
- **New: deny transitions:** there are now explicit transitions for approve and deny, each gated on the committed decision field.
  - A deny never executes anything, and `wsd-act` refuses anything but `approve`.
  - A denied task resumes with an explicit denial instruction.
  - A denied design approval can't satisfy `approval_valid()` (§5.4).

## Revision 6 (operator additions after r5 APPROVE)

The operator added requirements; these are not responses to review findings:
- The product is named heterodyne-metaharness, and replaces the `Epiphytic/heterodyne-metaharness` repo contents while keeping the LICENSE. The build includes the README, docs and setup (§15).
- The repo is install-agnostic, with strict config layering: in-git defaults and examples, out-of-git host config and state, secrets by reference only, and CI enforcement (§15).
- Language: Python 3.12+ for v1, with a Rust `ws-hook` planned as a follow-up (§16).
- Two LLMs are recommended but not required, and single-LLM deployments use adversarial review (§11.1).
- The ADR itself was made install-agnostic: no operator name, user or home paths.

## Round 6 fixes (after r6 review)

- **Precedence:** there is one effective order in §15: defaults, then host, then workstream, then a bead role label, then environment/CLI, which covers locations only. `examples/` is not a layer. §4.1 now refers to §15.
- **btq locations:** the prerequisite btq change now also makes the `Queue` locations configurable (config dir, credentials, certificate, Dolt endpoint, beads repo). The defaults equal today's values, and routing, claim and gate logic are unchanged (§4.3).
- **Install-agnostic wording:** the rule forbids install-specific *values*. OS backends and docs are allowed. The platform is selected at install time, as in §3.2 (§15).

## Round 7 fixes (after r7 review)

- **Policy override:** policy is not layered. It is read only from host `policy.toml`. Lower layers may only tighten it, and the loader rejects any attempt to relax it. The ADR lists exactly which settings a workstream may override (§15).

## Round 8 fixes (after r8 review)

- **Tightening vs. rejected policy keys:** a workstream's only way to tighten is a separate additive `[restrict]` table (`hard_deny`, `escalate`). Effective policy is host policy ∪ `[restrict]`. `policy.toml` keys in lower layers are still rejected (§15).

## Revision 10 (operator requirement after r9 APPROVE)

The operator added a rule: an approval ask is valid evidence only if it carries enough context and pinned references, bound by a digest, so that changes after approval are detected. Vague asks are enriched by the gatekeeper, or sent back to their originator for grooming; they never go to the operator as-is. This is new §5.9, wired into §5.3 (escalation), §5.4 (decision commit and `wsd-act`), §6.2 (cards), §3.4 (forge) and the btq prerequisite.

## Round 10 fixes (after r10 review)

- **Revision binding:** a design approval must pin the ADR in `ask.refs`, with `adr_revision` equal to that commit. The gate requires both the digest and the task's `adr_revision` to match. Any ADR change needs a new approval. Code review checks the implementation against the ADR at `adr_revision` (§5.9).
- **Digest boundary:** the ask is one immutable `metadata.ask` object. The digest covers exactly title, description, `ask` and the resolved refs. Every other field is explicitly excluded, including the digest itself and all decision and workflow fields (§5.9).
- **Hex on cards:** the digest and permalink SHAs are named as the explicit exceptions to the no-hex rule (§6.2).
