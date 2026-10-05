# heterodyne-metaharness: accounts and usage — Change Plan

> **For the orchestrator:** this is a change plan, not an implementation plan. It says how the [accounts and usage amendment](../../adr/proposals/0001-accounts-and-usage.md) changes the v1 roadmap ([`2026-09-29-heterodyne-v1-roadmap.md`](2026-09-29-heterodyne-v1-roadmap.md)): which beads to create, which to re-scope, and which to leave alone. Each roadmap plan is still written in full with `superpowers:writing-plans` when its predecessor finishes. The work items below (`AU-n`) are inputs to those plans, not replacements for them.

**Goal:** support several logins per adapter, schedule around subscription usage limits deterministically, and defer work when a role has no headroom, without changing the hook-driven, interactive-CLI, outer-sandbox design.

**Spec:** the amendment proposal (decisions D1–D11, section changes in its §3). Until it is approved, no bead may cite it (roadmap rule; ADR §5.9).

**Where we are:** plans 1, 2 and 2b are done. Plan 3 (wsd core A: queue and state) is in progress, and its plan document isn't written yet. This change is designed so that plan 3 doesn't have to wait.

## Gates

| Gate | Blocks | Notes |
|---|---|---|
| **G1.** The amendment passes cross-model review and gets an approval bead | AU-2, AU-6 to AU-13 | Revision 15, or folded into revision 14 (OpenShell) at the operator's choice. The review records go under `docs/reviews/`, as before. |
| **G2.** S6 findings are accepted | AU-6, AU-7, AU-8 | S6 can run before G1. Its findings may change D7 and D9, and if they do, the amendment is revised before G1. |
| **G3.** The operator confirms the provider terms for automatic failover | Enabling `failover = "next"` on the reference install | This is not a code gate. The code ships with `"none"` as the default. |

Plan 3's items (AU-3 to AU-5) don't need G1 if they are scoped as **structure only**: a field, a park kind and a gate interface, with no account behaviour. Before G1 they cite the plan 3 roadmap row, not the amendment. Once G1 passes, the plan 3 document (or a follow-up bead) cites the approved revision.

## Summary by roadmap plan

| Roadmap plan | Change | Items |
|---|---|---|
| 1 Foundations (done) | Config schema follow-up: accounts, `profiles.*.accounts`, `failover`, `[usage]` | AU-2 |
| 2/2b admind (done) | `admind` launches on its profile's first account | AU-11 |
| **3 wsd core A** (in progress) | Launched-session record gains `account`; deferred park kind; usage cache table and headroom-gate interface in pickup | AU-3, AU-4, AU-5 |
| 4 wsd core B | Account binding in the launcher; per-account freshness gate; new self-test probe; usage producers; reactive limit signal; failover at launch | AU-6, AU-7, AU-8 |
| 5 wsd core C | Test that account and usage config is hard-deny for agents; no new behaviour | AU-12 |
| 6 Marmot surface | Headroom in `/workstreams` and `/status`; quota alert; deferred-card rendering | AU-9 |
| 7 Gatekeeper and steering | An exhausted reviewer counts as "unavailable" for `fallback_reviewer` | AU-13 |
| 8 Operations | `heterodyne setup` login per account; runbook; docs | AU-10 |
| 9 Migration | None | n/a |
| P2 | macOS keychain behaviour for Claude accounts (with Seatbelt); ACP adapter, listed with the Paseo adapter | n/a |
| New | Spike S6 | AU-1 |

## Work items

Each item lists its roadmap plan, dependencies, scope and acceptance. Bead titles are suggestions.

### AU-1. Spike S6: accounts and usage

- **Plan:** spike (alongside S5). **Depends on:** nothing. **Gate it feeds:** G2.
- **Scope:** answer the five questions in the amendment's §13 S6 entry. Write the findings to `docs/spikes/S6-accounts-usage.md` in the style of S1–S4, with the evidence (commands run, versions, the redacted shape of each payload).
  1. The login file set for each adapter, with `CLAUDE_CONFIG_DIR` and `CODEX_HOME` set inside a synthetic home: which files must be ro-bound, and which the CLI writes. Linux now; record what is known about the macOS keychain for P2.
  2. Whether a session resumes after its bound login files are swapped for another account of the same adapter (Claude `--resume <id>`, Codex `resume <id>`).
  3. The shapes of Codex `account/rateLimits/read` and `account/rateLimits/updated` through the per-session app-server, on the pinned version.
  4. Structured sources, with no screen scraping:
     - a Claude usage source: the status-line JSON, the transcript entries, or confirmation that neither exists;
     - a limit-reached signal for both CLIs (a hook payload or a transcript entry).
  5. The freshness gate with two accounts of one adapter: the refresh command for each, and confirmation that they don't interfere.
- **Acceptance:** each question is answered yes, no or unknown, with evidence. If D7 or D9 would need to change, the needed change is written down as a proposed amendment edit.
- **Install-agnostic:** no real login paths, account emails or token fragments in the findings.

### AU-2. Config: accounts, profile accounts, failover, usage

- **Plan:** a foundations follow-up (its own bead). **Depends on:** G1.
- **Scope:**
  - `src/heterodyne/config/layers.py`: validate `[accounts.<name>]` (`adapter` in `adapters.known`, `login_dir` a string), `profiles.<p>.accounts` (a list of existing accounts with the profile's adapter), `profiles.<p>.failover` (`none` or `next`, default `none`), and `[usage]` (`reserve_percent` 0–50, `stale_minutes` > 0, `unknown_backoff_minutes` > 0).
  - Reject `[accounts]` and `[usage]` in workstream files. Reject account selection in every layer except the host layer.
  - `config check` warns about a profile with more than one account and `failover = "none"`.
  - Defaults: `[usage]` values in `src/heterodyne/defaults/defaults.toml`; no accounts.
  - `examples/config.toml`: one commented `[accounts.*]` example with placeholders.
  - `docs/configuration.md`: reference entries, including why the key is `login_dir` (the secret-name check flags `auth`).
- **Acceptance:** the tests cover every rejection above. `config check` prints the new keys with their source layers. `scripts/check_install_agnostic.py` stays clean. No model names in `src/`.

### AU-3. Plan 3: account on the launched-session record

- **Plan:** 3. **Depends on:** nothing (structure only).
- **Scope:** the launched-session record (ADR §4.1) gets an optional `account` field, `null` when the profile has no accounts. Session-key derivation is unchanged and doesn't take the account as input.
- **Acceptance:** a unit test shows the same `(bead, role, profile)` gives the same session key with different `account` values. The record round-trips through the journal and the bead.

### AU-4. Plan 3: deferred parking

- **Plan:** 3. **Depends on:** the plan 3 park journal.
- **Scope:**
  - Add a park kind `deferred` alongside the existing blocked park. It uses the same journaled sequence (intent, then WIP commit SHA, then label, then bead comment), but sets the label `v2:deferred` with **no blocking edge**, and records `defer_until` and `reason` in the journal.
  - The resumable-beads query includes `v2:deferred` beads whose `defer_until` has passed. Ties go to the earliest parked.
  - The pickup triggers include the earliest pending `defer_until`, and the 60s backstop also catches it.
  - Startup recovery: rebuild deferred parks from the label and comment. A missing `defer_until` is treated as already passed.
  - Before AU-6 exists, nothing triggers a deferral. A test-only entry point exercises it.
- **Acceptance:**
  - hypothesis tests on park and replay: a crash at each step replays to the same end state;
  - a deferred bead never becomes resumable before `defer_until`;
  - a deferred bead stays claimed by `wsd` throughout, and is never unclaimed (PICKUP.md).

### AU-5. Plan 3: usage cache and the headroom-gate interface

- **Plan:** 3. **Depends on:** the plan 3 journal schema.
- **Scope:**
  - A journal table `account_usage(account, window_id, kind, used_percent, resets_at, observed_at, source, trusted)`, plus `account_exhausted(account, until)`.
  - A pure function `eligible_accounts(profile, usage, now, settings) -> list[account]` that implements D4 and D6.
  - Pickup calls it before claiming new work for a role, and doesn't claim when the list is empty. Before AU-2 exists, a profile has no accounts, so the function returns the default login as eligible and behaviour is unchanged.
- **Acceptance:** hypothesis properties:
  - unknown or stale usage is eligible;
  - `failover = "none"` never returns any account other than the first;
  - a result is always ordered as configured;
  - an exhausted-until mark in the past is ignored.

  Losing the table recovers to "unknown".

### AU-6. Plan 4: account binding, per-account freshness gate, self-test probe

- **Plan:** 4. **Depends on:** G1, G2, AU-2, AU-3.
- **Scope:**
  - The launcher ro-binds only the chosen account's login files (the file set comes from S6) into the session's synthetic home.
  - It sets `CLAUDE_CONFIG_DIR` and `CODEX_HOME` inside the synthetic home, and never overrides `HOME` for Claude account selection.
  - It records the account on the launched-session record.
  - The freshness gate runs per account under a host-side lock.
  - New self-test probe: every other configured account's `login_dir` gives ENOENT or EACCES inside the sandbox, and is readable outside it immediately before launch.
  - The environment allowlist gains `CLAUDE_CONFIG_DIR` and `CODEX_HOME`, set only to the synthetic paths.
- **Acceptance:**
  - the self-test fails closed when another account's login is visible;
  - two concurrent launches on the same account serialise their refresh;
  - a relaunch on a different account keeps the session key, and either resumes or (where S6 says resume doesn't work) uses the §4.1 deterministic handoff.

### AU-7. Plan 4: usage producers and the reactive limit signal

- **Plan:** 4. **Depends on:** G2, AU-5.
- **Scope:**
  - **Codex:** read `account/rateLimits/read` at launch, and subscribe to `account/rateLimits/updated`, through the per-session app-server.
  - **Claude:** the source S6 found, if any.
  - Both write to `account_usage` with `trusted = false`.
  - The limit-reached signal (S6) marks the account exhausted until its reset time (or `unknown_backoff_minutes`), interrupts, commits the WIP, and defers the bead (AU-4), exactly as `/stop` does.
- **Acceptance:** the fake `codex` and `claude` (ADR §11) gain scripted usage windows and a limit-reached event. An integration test covers: usage observed, the gate closes, the role defers, the reset time passes, and the bead resumes. No producer parses terminal output.

### AU-8. Plan 4: failover at launch and resume

- **Plan:** 4. **Depends on:** AU-5, AU-6.
- **Scope:**
  - Launch, resume and relaunch take the first account from `eligible_accounts`; when it is empty, they defer.
  - With `failover = "next"`, a freshness-gate refusal moves on to the next eligible account before falling back to `needs-human`.
- **Acceptance:** tests cover both failover modes, the freshness-gate refusal path, and the session key staying unchanged across a failover.

### AU-9. Plan 6: commands, cards and alerts

- **Plan:** 6. **Depends on:** AU-5, plan 6 renderer.
- **Scope:**
  - `/workstreams` shows headroom per role and "deferred: quota until HH:MM".
  - `/status` lists accounts in use, with their windows, reset times and the age of each observation.
  - `/status <bead>` shows the account.
  - Deferred task cards use ⏸️ with the reason "quota".
  - One control-group alert per all-accounts-exhausted episode.
  - The daily digest lists accounts that hit a limit.
  - No account login path or email appears in any message.
- **Acceptance:** golden fixtures for each new render, within the 8-line limit.

### AU-10. Plan 8: setup, runbook and docs

- **Plan:** 8. **Depends on:** AU-2, AU-6.
- **Scope:**
  - `heterodyne setup` offers to add accounts, and prints the per-adapter login command for each `login_dir`, using the commands S6 found (it never runs a login inside a sandbox).
  - A runbook section covers adding, rotating and removing an account, and recovering from an invalid login.
  - `docs/security-model.md`: one paragraph on per-account auth binding and on usage observations being untrusted.
  - The provider-terms note for `failover = "next"`.

### AU-11. admind on its profile's first account

- **Plan:** an admind follow-up (its own small bead). **Depends on:** AU-2.
- **Scope:** when the admin profile lists accounts, `admind` launches the agent and the summarizer with `CLAUDE_CONFIG_DIR` set to the first account's `login_dir`. No headroom gate and no failover. The `wn-agent` and Marmot paths are unchanged.
- **Acceptance:** a test with the fake `claude` checks the environment it was launched with. With no accounts, the launch is unchanged.

### AU-12. Plan 5: policy coverage

- **Plan:** 5. **Depends on:** AU-2.
- **Scope:** check that changing `[accounts]`, `profiles.*.accounts`, `failover` or `[usage]` is classified as `modify_harness_config` (hard deny). Add a test. No new tier.

### AU-13. Plan 7: an exhausted reviewer is unavailable

- **Plan:** 7. **Depends on:** AU-5.
- **Scope:**
  - When the reviewer profile has no eligible account, review selection treats it as unavailable and uses `fallback_reviewer`.
  - If that profile is also ineligible, the review defers.
  - Review mode is still derived from the recorded models (§4.1). The account doesn't affect it.
- **Acceptance:** tests cover each step of this chain. The `Code-Review:` line is unchanged.

## Proposed roadmap edits (apply once G1 passes)

- **Plan 3 row, "Delivers":** add "launched-session `account` field; deferred park kind; usage cache and headroom-gate interface (AU-3–AU-5)".
- **Plan 4 row:** add "per-account auth binding, freshness gate and self-test probe; usage producers; reactive limit signal; failover (AU-6–AU-8)". Gate: add "S6 findings accepted".
- **Plan 5, 6, 7 and 8 rows:** add AU-12, AU-9, AU-13 and AU-10 respectively.
- **New rows:** "1b Config: accounts (AU-2)" and "2c admind accounts (AU-11)", each gated on G1.
- **Spikes:** add S6 beside S5.

## Bead refactor checklist

For the orchestrator, against the current plan 3+ beads:

1. Create the S6 spike bead (AU-1). It has no blockers.
2. Create the amendment review bead (G1), blocked by nothing. When it is approved, create the approval bead that later beads cite.
3. **Plan 3 beads:** add AU-3, AU-4 and AU-5 as tasks, or fold them into the existing journal, park and pickup beads. Keep them structure-only, so that none waits on G1.
4. **Plan 4 beads:** add AU-6, AU-7 and AU-8, each blocked by G1 and the S6 bead.
5. Create AU-2 and AU-11 as their own beads, blocked by G1. AU-11 is also blocked by AU-2.
6. Add AU-9, AU-10, AU-12 and AU-13 to their plans' beads, or note them for when those plans are written.
7. Leave every other plan 3+ bead's scope unchanged.

## Not changed

- The hook-driven state model, interactive CLIs in tmux, the outer sandbox as the only boundary, the typed action registry, and the decision queue.
- The session-key derivation.
- btq: no new `ready()` or claim logic (deferring is `wsd`'s own query).
- Policy tiers, approvals and review evidence.
