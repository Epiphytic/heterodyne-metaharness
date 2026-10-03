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

## Revision 12 (spike findings)

The operator approved revision 11. The plan-1 spikes then found facts that contradict parts of it. These changes are not responses to review findings; each comes from a spike finding (S1 and S3 from their "ADR impact" sections, S4 from its findings; spike documents are in the product repository's `docs/spikes/`).

| Change | Section | Source |
|---|---|---|
| `PreToolUse` scoped: every mode in Claude Code, interactive sessions only in Codex; never under `codex exec`. Headless Codex runs must be `-s read-only` or enforced by the outer sandbox alone. | §5.3 | S1 Q1 |
| Hook fail-closed applies only where the hook runs; headless `codex exec` is allowed only as in §5.3. | §10 | S1 Q1 |
| Capability table: no fixed-ID launch flag (record the assigned ID; optional `/rename`); `codex queue` needs a per-session `codex app-server --listen unix://…` with `--remote` on both TUI and queue; Codex pre-tool deny and hook-trust bypass marked unsettled (round 14). | §4.2 | S1 Q1–Q4 |
| The Codex app-server sets the cwd and is inferred to execute tools, so it runs inside the sandbox, and the self-test covers that launch shape. | §7 | S1 Q4 |
| Native sandbox exception: `-s read-only` for headless `codex exec`. | §7 | S1 Q1 |
| The launcher clears the environment; a probe enforces it. | §7 | S3 deviations 2–3 |
| Model OAuth tokens probably reach account MCP connectors (inferred, not tested): deny `mcp-proxy.anthropic.com` at the proxy, disable claude.ai MCP servers, and disable Codex connectors in adapter config. | §7 | S3 |
| Auth files ro-bound; the rest of the synthetic home is a writable per-session copy. | §7 | S3 credential-write finding |
| Shared refresh token: launch freshness gate (refresh on the host first or refuse, if the access token expires within the session's maximum lifetime); sessions are stopped at that lifetime and relaunched through the gate (round 15). Per-sandbox credentials are future work. | §7 | S3 credential-write finding |
| Exact-host allowlist; CONNECT host only (no SNI, no paths) stated as a residual risk, with logs recording hosts only. | §7 | S3 proxy limit |
| Self-test probes must prove specific enforcement (canary checked outside first; proxy-attributed refusal plus allowlisted control; namespace evidence of no route; explicit `forbidden`; env probe). The self-test is mandatory before every launch. | §7 | S3 fix round 1 |
| Package-registry and git-fetch egress, and the reviewer's read-only worktree bind, remain plan-4 verification items. | §7 | S3 "not verified" |
| S1–S4 marked done, with a pointer to the product repository's `docs/spikes/`; S2 gate not passed, Seatbelt and the S4 real-client check still open. | §13 | S1–S4 |

**S4, evaluated against §3.4, §6 and §8.** One fact needed a change (added in round 12, below); the rest fill in detail and contradict nothing.
- The sender is `message.sender.account_id_hex` (and `actor.account_id_hex` for reactions), taken from the authenticated event. That is exactly §3.4's "reported by `wn-agent`, never parsed from message text"; the allowlist's npub is the same key in another encoding.
- The `wn` CLI can't open a home that `wn-agent` holds, so all harness traffic goes through the `wn-agent` control socket. §3.4, §6 and §8 already name `wn-agent` as the source, and §8 already gives `admind` its own identity and connection.
- There is no list-groups operation. Registered group IDs come from config (§3.4, §6.1), and `group_info` gives the member count that §8's two-member check needs.
- Membership changes arrive as `group_state_changed` without the subject's key. §3.4 only requires an alert and a suspension until `/trust-group`, which the change kind alone supports.
- **Changed:** a membership change made by the harness's own identity produces no `group_state_changed` on its own subscription, so §3.4's alert rule had an uncovered case (review r12 finding 4).

## Round 12 fixes (after r12 review)

| # | Finding | Disposition |
|---|---|---|
| 1 | Codex recovery still keyed on the derived `uuid5` | **Fixed.** `uuid5` is the logical key; Codex resume, steering and reconcile use the recorded assigned thread ID or a confirmed post-launch name (§4.1, §4.3, §10). |
| 2 | §5.6 still said "Claude: channel" | **Fixed.** v1 uses `tmux send-keys`; channels are phase 2 (§5.6). |
| 3 | "Bubblewrap passes" overstated S3 | **Fixed.** S3 passed the shape it tested; the managed Codex app-server shape is a plan-4 verification item (§7, §13). |
| 4 | Own-identity membership changes produce no event | **Fixed.** Changes made through the harness identity raise the alert and suspension themselves, and reconcile compares member counts (`group_info`) with the last trusted value (§3.4). |
| 5 | Connector risk overstated | **Fixed.** Qualified as inferred and untested; controls kept (§7). |
| 6 | S4 status wording; S4 has no "ADR impact" section | **Fixed** (§13, this file). |

## Round 13 fixes (after r13 review)

| # | Finding | Disposition |
|---|---|---|
| 1 | A direct control-socket change that keeps the member count unchanged is not detected | **Fixed, with a stated residual risk.** `wn-agent` has no member-list op, so no reliable state check exists. Every membership change by the harness identity must go through `wsd`, which journals it and raises the alert itself; no sandbox can reach the control socket; the member-count comparison stays as a backstop. A count-preserving change made directly on the socket by something running as the service user (such as `admind`'s agent) is a residual risk under `admind`'s accepted one (§3.4). |
| 2 | §13 implied S1 tested the app-server inside the sandbox | **Fixed.** The queue result is stated separately; the in-sandbox placement is marked as required by §7 and unverified (§13). |

## Round 14 fixes (after r14 review)

| # | Finding | Disposition |
|---|---|---|
| 1 | S1's open question: an independent re-run could not reproduce the hook-trust result, and S1 says not to treat Q1 or Q2 as settled | **Fixed.** Codex interactive `PreToolUse` deny and `--dangerously-bypass-hook-trust` are marked unsettled, pending a test with the harness's actual `hooks.json` schema. Until then every Codex session is treated like a headless run, with the outer sandbox as its only enforcement and no hook fail-closed path (§4.2, §5.3, §10, §13). |
| 2 | §3.4 promised an alert for every membership change while admitting an undetected case | **Fixed by narrowing.** §3.4 now guarantees an alert and suspension for *detected* changes, lists what is detected, and presents the count-preserving direct-socket change as a residual risk **for operator acceptance with revision 12**, rather than folding it into `admind`'s earlier acceptance. |

## Round 15 fixes (after r15 review; not yet re-reviewed, the 4-round limit was reached)

| # | Finding | Disposition |
|---|---|---|
| 1 | "Parked at maximum lifetime" has no blocker to clear, so the bead could stay parked forever | **Fixed.** At maximum lifetime the session is stopped at its next turn boundary, its WIP committed, and relaunched at once as the same session through the freshness gate. It is not §4.3 parking: the bead stays claimed and in progress with no blocking edge. A refused relaunch makes the bead `needs-human` (§7). |
| 2 (non-blocking) | §5.3 "recognises operator-only intents early" read as applying to every Codex session | **Fixed.** Scoped to where the hook runs: Claude Code, and Codex once verified (§5.3). |

## Roadmap alignment (operator decision, before r16)

- §14 step 1 ("stabilise the old harness": fix exit 126, point the admin lookup at the live Marmot home) is dropped. The operator decided there is no live cutover and the old harness stays as-is. S4 also showed the `wn` CLI can't open a home that `wn-agent` holds, so the admin-lookup step was infeasible as written. §14 now says the old harness gets no stabilisation fixes and is archived after step 3.
- The S1 "Open question on Q2" paragraph is now committed in the product repository, so it is recorded evidence. The ADR keeps its conservative stance: Codex hook enforcement is unsettled until it is tested against the real `hooks.json` schema, a plan-4 verification item.

## Round 16 fixes (after r16 review)

| # | Finding | Disposition |
|---|---|---|
| 1 | Stopping only at the next turn boundary lets a long turn outlive the token | **Fixed.** The gate requires the token to outlive the maximum lifetime plus a stop margin. At maximum lifetime the session stops at a turn boundary if one comes within the margin, otherwise by a hard interrupt with WIP commit (as `/stop`), so it always stops before expiry, then relaunches through the gate. This supersedes the round 15 wording (§7). |

## Round 17 (after r17 review)

APPROVE, with no findings. Revision 12 is ready for operator approval.

## Revision 13 (rounds r18–r20)

- **r18, membership transition (BLOCKING):** fixed. §8 makes an operator change a journaled transition, serialized with the guard, holding dispatch and posting until it ends.
- **r18, admind ingress (BLOCKING):** fixed. §8 restates sender-key, group-binding and replay checks for admind's own group, reading `policy.toml` directly.
- **r18, redaction (BLOCKING):** fixed. One redaction applies to everything admind posts, to the summarizer's input and to the audit; "unabridged" means nothing omitted apart from redaction markers.
- **r18, batch provenance (BLOCKING):** fixed. Batches head each reply with its origin, post unthreaded, and `!details` returns every included reply.
- **r18, non-blocking:** batch cadence, the ≤50-line case, counting after collapse and pipeline failures are specified; the §3.1 table and §6.2 scope are updated.
- **r19, recovery by count (BLOCKING):** fixed. Commit needs `wn-agent`'s success plus the expected count; a pending change found on startup latches; `admind rearm` is the recovery.
- **r20:** APPROVE, no findings.
