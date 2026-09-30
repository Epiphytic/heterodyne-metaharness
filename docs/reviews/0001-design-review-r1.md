**REJECT**

1. **[BLOCKING] §5.2, §14 — The proposed pickup command does not exist.** Installed `btq` requires `--agent`, `--ws`, and `--session`; `btq ready ws:W` is invalid. Its `worktree` command also creates a different path and branch from §4.3. Specify the exact `btq` integration and change the queue client or ADR before planning implementation.

2. **[BLOCKING] §§4.3, 5.2–5.3, 10 — Park and resume conflict with queue ownership.** `btq ready` returns only unassigned work; a parked bead remains claimed and cannot become ready as described. The queue protocol forbids automatic unclaim and reclaim. Define durable parked states, ownership transfer, and authorized recovery, including what happens after an interrupted commit or an uncertain queue write.

3. **[BLOCKING] §§5.3–5.4 — Closing an approval bead before executing its action can unblock the task on a failed action.** Record the decision first, execute the action idempotently, record its result, and release the dependency only on success. Specify retry and operator handling for uncertain outcomes.

4. **[BLOCKING] §§5.3, 7, 10 — The sole security boundary is not established.** Model credentials, branch push access, package caches, read-only mounts, network routes, and the bound `wsd` socket remain reachable from permissive agents. The ADR does not show that the Linux and macOS profiles prevent credential use, exfiltration, or calls to privileged control operations. Make isolation and socket authorization explicit acceptance criteria for S3; fail launch if either backend cannot enforce them.

5. **[BLOCKING] §§5.3–5.4 — `wsd` executes privileged actions from an underspecified agent request.** A plain-English summary and “exact action” do not define a safe authority boundary. Require a typed, allowlisted action with fixed target, arguments, revision, approver, and idempotency key; show that exact payload to the operator and revalidate it immediately before execution. Isolate action credentials from the general daemon where possible.

6. **[BLOCKING] §§5.4–5.5 — “First decision wins” lacks an atomic commit point.** Marmot events and 60-second forge polls can race, replay, or arrive after a revision changes. Define a single compare-and-set decision record, event deduplication, and a policy for conflicting later decisions. Bind forge approval to the reviewed commit or patch revision; a review of an older revision must not authorize a newer action.

7. **[BLOCKING] §§5.1, 5.4–5.6, 8 — Operator authentication is incomplete.** The ADR does not specify sender verification for ordinary Marmot approvals and commands, forge review revocation, or how `admind` binds an authenticated MLS sender to the configured npub. Require verified sender and group identity at ingress, an operator allowlist, replay protection, and explicit behavior on membership or identity changes.

8. **[BLOCKING] §§5.3, 7, 10 — Hook fail-open is too broad for the claimed policy.** A missing `wsd` decision permits calls that the tier file would hard-deny, and replayed events cannot undo them. Fail closed for operations requiring a decision; allow only a narrowly specified sandbox-confined class during outage. Treat spooled events as untrusted observations, not approval evidence.

9. **[BLOCKING] §§3.1, 5.4, 10 — Beads cannot currently rebuild all stated control state.** Message-to-bead mappings, first-decision order, action execution state, session ownership, outbox receipts, and spool status are described as SQLite state without a complete bead representation. Define durable records and recovery order for each, or narrow the “rebuildable cache” claim.

10. **[BLOCKING] §8 — `admind` is an unsandboxed, permission-bypassed LLM with the `openclaw` user’s access.** “No root” does not constrain access to that user’s credentials and services; the claim of “no LLM in between” also contradicts the superuser-agent design. Run it under a separate least-privilege identity, restrict built-in commands to fixed units, and require explicit operator confirmation for privileged side effects. Do not make this the first recovery dependency until that boundary is demonstrated.

11. **[BLOCKING] §§4.1, 5.8, 11.1 — Review evidence is inferred from configuration, not the session that ran.** A fallback, profile reload, resumed session, or CLI override can make the generated model and review range inaccurate. Persist the actual launched model, session identity, reviewed commit range, findings, and disposition; reject close if code changed after review.

12. **[NON-BLOCKING] §§5.1, 6.2, 10 — Delivery failure can silently strand a required decision.** The outbox may permanently skip an approval card while its bead remains blocked. Define a separate `needs-human` alert and recovery path when no approval surface has a confirmed receipt.

13. **[NON-BLOCKING] §§12–14 — v1 spans too many unproven integrations.** Two operating systems, two CLI adapters, Claude channels, GitHub, Radicle, `admind`, cron, and migration all sit on the initial path. Define a minimum cutover slice and explicit gates for later adapters and approval surfaces; S1–S4 should decide scope before implementation beads are authorized.