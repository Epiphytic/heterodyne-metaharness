# heterodyne-metaharness v1 — Roadmap

> Sequencing document for ADR 0001 (revision `e36f6d09563897198cb7641cad8c9e33bb02e6d3`, design approval bead `btq-96hm`). Each numbered plan is a separate, detailed implementation plan, written with `superpowers:writing-plans` **when its predecessor finishes**. Later plans depend on spike results, so writing them in full now would be guesswork.

**Rule for every plan:** any finding that requires changing the ADR stops the plan. The ADR amendment then goes through a cross-model review and a **new** approval bead (§5.9), before any bead may cite the new revision.

## Variables used by all plans

The plans are published in the public repo, so they never contain install paths. Set these in your shell (the reference install keeps its values in host-local notes, not in git):

| Variable | Meaning |
|---|---|
| `$HZ` | Fresh clone of `Epiphytic/heterodyne-metaharness` (the new product repo). **Not** any existing checkout or symlink of the old harness. |
| `$DESIGN_REPO` | The local design repo that holds the ADR evidence pinned by `btq-96hm`. Read-only after plan 1. |
| `$BTQ_REPO` | The beads-task-queue checkout that provides `bin/btq`. |

## Sequence

| # | Plan | Delivers | Gate to start |
|---|---|---|---|
| 1 | **Foundations** (`2026-09-29-heterodyne-plan-1-foundations.md`) | Fresh repo (replacement PR); spikes S1–S4 with findings; btq prerequisites (`wsd` identity, configurable locations, shared `context_digest` gate, `approve-bead` moved into btq); package skeleton with platform seam, config layering, host-only policy with `[restrict]`, install-agnostic checker, CI; README and docs | `btq-96hm` approved ✅ |
| 2 | **admind** | Independent admin channel: its own Marmot identity, a 2-member group check, authenticated sender, passthrough to the superuser admin agent, `!restart` allowlist, append-only log, alert-directory relay (§8, §6.2) | Plan 1 spike gate (S4) passed |
| 3 | **wsd core A: queue and state** | SQLite journal and inbox (§3.3); beads adapter with the `wsd` identity and per-bead workers; shared pause gate and claim lock; pickup; park/resume journal; startup recovery order (§4.3, §5.2) | Plan 1 btq tasks merged |
| 4 | **wsd core B: agents and sandbox** | `AgentRuntime` with `codex` and `claude-code` adapters; session ID derivation; launch entries with dispatch marks and **launch receipts**, reconciled against the receipt only, and legacy plan 3 sessions adopted by the journal upgrade (§4.4 D2, §3.3); OpenShell sandbox runtime, falling back to the bubblewrap backend from the S3 prototype on Linux only if S5 fails (§7); synthetic home, egress proxy, session sockets, launch self-test; Python `ws-hook` shim (§4, §7) | S1 and S3 findings accepted; ADR revision 14 approved (`btq-k942c`); S5 for the sandbox runtime; S8's minimum capabilities demonstrated for any adapter that is to run as admind's admin agent (§13) |
| 5 | **wsd core C: policy, approvals and actions** | Policy engine (tiers from plan 1 config); `ws-request` and the typed action registry; §5.9 ask gate (lint plus digest); serial decision queue; approve/deny transitions; `wsd-act` as a separate user with a sudoers rule; reconciliation (§5.3, §5.4, §5.9) | Plans 3 and 4 |
| 6 | **Marmot surface** | Ingress authentication (§3.4); router; renderer with golden tests; outbox with receipts; cards, threads and reactions; commands `/status`, `/workstreams`, `/pause`, `/approve`, `/deny`, `/trust-group`; delivery alerts (§6) | Plan 5; S4 findings |
| 7 | **Gatekeeper and steering** | Typed Hermes gatekeeper endpoint; intake with rewrite and materiality (`kind:confirm`); grey-zone permissions; context sufficiency judgement, enrich/groom; answer-and-nudge with the 3-attempt limit; review role and close gates (§5.1, §5.6–5.9) | Plan 6 |
| 8 | **Operations** | cron and reconcile; failure-table behaviour (§10); `heterodyne setup` in full (interactive, import from the existing install, service-unit generation); runbooks; docs complete; scheduling the wsd journal backup (`Journal.backup(dest)` from plan 3, timed with the beads backups) | Plan 7 |
| 9 | **Migration and acceptance** | Bead cull (close superseded beads, citing the ADR) and rewrite (v2 terms, `agent:wsd`, re-route); first workstream on v2; 3-day acceptance run; then add the other workstreams; archive the old harness repos (§14) | Plan 8 |
| P2 | Phase 2 (each its own design round and approval) | macOS/Seatbelt; Claude channels (S2); GitHub approvals; Radicle approvals; Rust `ws-hook`; Radicle mirror of `$HZ` | v1 accepted |

**ADR revision 14** ([ADR 0001](../../adr/0001-workstreams-v2.md), approved as `btq-k942c`) changes the plans from 4 on:

- Plan 4: the row above (§4.4 D2, §7, §13 S5 and S8).
- Plan 7: the gatekeeper's sufficiency judgement becomes **mandatory** for asks posted through admind's relay in the release that ships both `wsd` and the gatekeeper; before that release it may run only in shadow (§5.9, interim exception).
- §17 lists the 10 decisions revision 14 leaves to the operator. A plan that depends on one of them states which, and does not start that part until the operator has decided it.

## Not planned, and why

- **ADR §14 step 1, "stabilise the old harness now"** (exit 126, admin lookup). Its purpose was an early confirmation of S4 on the live harness. The operator has since decided that the old harness stays deadlocked and there is no live cutover, so S4 is confirmed directly against `wn`/`wn-agent` in plan 1. The operator was told about this omission when the plan was presented. If they want the step, it becomes a separate bead.
