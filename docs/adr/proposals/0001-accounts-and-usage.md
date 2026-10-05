# ADR 0001 amendment proposal: accounts and usage-aware scheduling

- Status: **Draft.** Not yet reviewed or approved. Nothing may cite it until it has had a cross-model review and a new approval bead (§5.9; roadmap rule).
- Amends: ADR 0001 revision 13 (§3.3, §4.1, §4.3, §5.2, §6.2, §6.3, §7, §8, §10, §11, §12, §13, §15).
- Target revision: 15. Revision 14 is reserved for the OpenShell runtime (§7). The operator may fold this amendment into revision 14 instead, so that both share one review round.
- Date: 2026-10-05
- Author: Claude Opus 5.5 (with the operator)
- Change plan: [`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md`](../../superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md)

The ADR in this repository is a byte-identical copy of the design repo's ADR (plan 2b, Task 10). This proposal is therefore a separate document. Once approved, its text is applied in the design repo, and the new revision is then copied here.

## 1. Why

- Today each adapter has exactly one login: the operator's own, ro-bound into every sandbox (§7).
- When that subscription reaches a usage limit, every session on that adapter fails. The control plane only learns of this after the fact, and nothing about it is deterministic.
- An operator with more than one subscription for a harness (for example a personal and a team plan) has no way to use the second one.

[t3code](https://github.com/pingdotgg/t3code) (MIT) solves the same problem for an interactive UI. The ideas this proposal takes from it, as ideas rather than code (t3code is TypeScript on Effect; §16 rules out a second runtime):

- **Several logins per harness**, each a separate config directory:
  - Claude Code through `CLAUDE_CONFIG_DIR` rather than `HOME` (`apps/server/src/provider/Drivers/ClaudeHome.ts`). Overriding `HOME` on macOS moves the keychain lookup, and the CLI then reports "Not logged in".
  - Codex through a home whose `auth.json` is private while its session store is shared (`Drivers/CodexHomeLayout.ts`, "authOverlay").
- **Subscription usage windows** read from the harness:
  - Codex: `account/rateLimits/read` and the `account/rateLimits/updated` notification on the app-server (`Layers/codexUsageLimits.ts`).
  - Claude: the Agent SDK's `get_usage` (`Layers/claudeUsageLimits.ts`). We can't use the Agent SDK (§2, rejected alternatives), so this proposal finds a different Claude source through spike S6.

Not taken:
- t3code's harness transport (Agent SDK, headless app-server, ACP);
- its permission model (per-turn approval and sandbox policies enforced by each harness's native sandbox, with the turn blocked until the user answers);
- its thread and checkpoint task model.

These conflict with §2 (vanilla interactive CLIs, hooks for state), §5.3 (park and continue, typed actions) and §7 (one outer sandbox).

Two more t3code ideas are in the change plan as later, non-blocking tasks rather than ADR decisions: its driver-registry shape (AU-14) and its Codex app-server protocol schemas and replay transcripts (AU-15).

## 2. Decisions

- **D1. Accounts are host configuration.**
  - An *account* is a named login for one adapter: `[accounts.<name>]` with `adapter` and `login_dir`, the directory that holds that login.
  - A profile may list accounts in order: `profiles.<p>.accounts = [...]`. Each must use the profile's adapter.
  - A profile without `accounts` uses the adapter's default login, which is today's behaviour.
  - Accounts live only in the host layer (§15 layer 2). Workstream config, bead labels and the environment can't name or select an account.
- **D2. An account is not part of session identity.**
  - The session key stays `uuid5(NS, f"{bead}:{role}:{profile}")` (§4.1).
  - The launched-session record gains `account`, recorded like the model.
  - A relaunch on a different account is still the same logical session.
- **D3. Usage is an advisory cache.**
  - `wsd` keeps the last observed usage windows per account: window ID, kind, percent used, reset time, when it was observed, and the source.
  - Observations that come from inside a sandbox are **untrusted** (as with the §3.3 hook spool).
  - Usage only ever affects *scheduling*. It never affects policy, approvals, review evidence or any decision field.
  - An observation older than `usage.stale_minutes` is treated as unknown.
- **D4. The headroom gate is deterministic.**
  - An account is *eligible* unless either of these holds:
    - a known window has `used_percent >= 100 - usage.reserve_percent`;
    - it is marked exhausted until a time that hasn't passed.
  - Unknown usage counts as eligible, so the gate never blocks on missing data.
  - The gate runs at pickup, launch, resume and relaunch.
- **D5. Deferred parking.**
  - When a role's profile has no eligible account, the bead is **deferred**, not blocked. It stays claimed by `wsd` and `in_progress`, labelled `v2:deferred`, with no blocking edge.
  - The journal records `defer_until`: the earliest known reset among the profile's accounts, or a fixed backoff (`usage.unknown_backoff_minutes`) when no reset time is known.
  - It becomes resumable when `defer_until` has passed. Its session resumes then.
  - Hitting the limit mid-turn (a reactive signal, D7) is handled like `/stop`: interrupt, commit the WIP, then defer.
- **D6. Failover is opt-in.**
  - `profiles.<p>.failover = "none" | "next"`, default `"none"`.
  - With `"none"`, only the profile's first account is used, and usage only defers work.
  - With `"next"`, a launch or resume uses the first eligible account in order.
  - Whether a provider's terms allow switching accounts automatically when one hits its limit is the operator's call. The docs say so; the code doesn't check.
  - `config check` warns when a profile lists more than one account with `failover = "none"`.
- **D7. Account binding reuses the §7 synthetic home.**
  - Each session already has a synthetic home: a writable per-session copy, with the login files ro-bound into it.
  - An account only chooses *which* login files are bound. The session's transcripts and state stay in its synthetic home whichever account it ran under.
    - Claude: point `CLAUDE_CONFIG_DIR` at the synthetic home's config directory; never override `HOME` for it.
    - Codex: point `CODEX_HOME` at the synthetic Codex home, with the account's `auth.json` ro-bound into it.
  - Only the chosen account's login files are mounted. Other accounts' login directories are not visible inside the sandbox; a new self-test probe checks this.
  - S6 confirms exactly which files make up a login for each adapter, and whether a session resumes cleanly after its login files change. Where a cross-account resume doesn't work, the relaunch uses the §4.1 deterministic handoff instead.
- **D8. The freshness gate runs per account.**
  - The §7 launch freshness gate checks and refreshes the token of the account being launched, under a host-side lock per account.
  - The shared-refresh-token risk is now per account instead of global.
- **D9. Usage sources.**
  - **Codex:** the per-session app-server already exists (§4.2). `wsd` reads `account/rateLimits/read` at launch and takes `account/rateLimits/updated` notifications. S6 verifies both on the pinned Codex version.
  - **Claude:** there is no interactive equivalent of the SDK's `get_usage`. S6 evaluates the candidates:
    - (a) the status-line JSON, which may carry rate-limit windows (unverified);
    - (b) structured rate-limit entries in the session transcript;
    - (c) no proactive source.

    Under (c), Claude accounts get only the reactive signal: the session hits its limit, `wsd` sees it through a hook or the transcript, and the account is marked exhausted until its reset time.
  - Screen scraping stays out of bounds (§2), so a signal that appears only in the terminal can't be used.
- **D10. Visibility.**
  - `/workstreams` and `/status` show each role's account and its headroom.
  - A workstream whose roles are all deferred shows "deferred: quota until HH:MM".
  - When every account of a role is exhausted, the control group gets one alert per episode. The daily digest lists accounts that hit a limit.
- **D11. `admind`.**
  - The admin agent uses its profile's first account (`CLAUDE_CONFIG_DIR` set to that account's `login_dir`; `admind` isn't sandboxed).
  - It ignores the headroom gate and never fails over. It is the recovery path, and a gate must never stop it from launching.

## 3. Section-by-section changes

Text in quote blocks is proposed ADR text. Everything else is guidance for the editor.

### §3.3 State ownership and recovery

Add two rows:

> | Account usage cache (windows per account, exhausted-until marks) | SQLite journal. Advisory; observations from inside a sandbox are untrusted (§4.1). | Lost. Every account is unknown, which is eligible, until the next observation. |
> | Deferred-park record (`defer_until`, reason, account) | SQLite journal plus the `v2:deferred` label and a bead comment | Rebuilt from the label and comment. A missing `defer_until` is treated as already passed, so the bead resumes and the headroom gate re-checks. |

In the startup order, "resume park journals" covers deferred parks as well.

### §4.1 Roles and interchangeability

Add to the config example:

```toml
# Accounts: a named login for one adapter (host config only, §15).
[accounts.codex-main]
adapter   = "codex"
login_dir = "<directory holding this login>"

[accounts.codex-team]
adapter   = "codex"
login_dir = "<directory holding this login>"

[profiles.gpt-sol]
adapter  = "codex"
model    = "gpt-6-sol"
accounts = ["codex-main", "codex-team"]   # order is preference
failover = "next"                         # default "none": defer only (§4.3)

[usage]
reserve_percent         = 5    # an account is ineligible at >= 100 - reserve_percent used
stale_minutes           = 30
unknown_backoff_minutes = 30
```

Add the bullets:

> - **Accounts.** A profile may name an ordered list of accounts, each a login for the profile's adapter. Accounts are host configuration only. The session key never includes the account. The launched-session record does, alongside the model.
> - **Usage-aware launch.** Every launch, resume and relaunch goes through the headroom gate (§5.2). With `failover = "next"` it takes the first eligible account; with `"none"` it uses the first account or defers.

Validation at startup: every account names a known adapter and a `login_dir`. Every account a profile lists exists and uses the profile's adapter. `failover` is `none` or `next`.

### §4.3 Session model

After **Parking**, add:

> - **Deferring** is parking on time rather than on a bead.
>   - When no account for the role is eligible, the bead stays claimed by `wsd` (`in_progress`), is labelled `v2:deferred`, and gets no blocking edge.
>   - The park journal records `defer_until` and the reason. A mid-turn limit is handled like `/stop`: interrupt, commit the WIP, then defer.
>   - A deferred bead is resumable once `defer_until` has passed. It then goes through the headroom gate again.

Extend **Resumable beads** to: "beads it has claimed that carry `v2:parked` with all blocking edges closed, or that carry `v2:deferred` whose `defer_until` has passed".

### §5.2 Pickup

- **Triggers:** add "a `defer_until` passes".
- Before claiming new work for a role, pickup checks the role's profile has an eligible account. If none does, the role takes no new work until the earliest `defer_until`. Claiming a bead only to defer it straight away would churn the queue.
- Source 1 ("its own resumable parked beads") includes deferred beads that are due. Ties between them go to the one parked earliest.
- **Idle:** a workstream whose remaining work waits only on quota reports as "deferred: quota until HH:MM", not idle.

### §6.2 Rendering rules

- The task-card reaction for a deferred bead is ⏸️. The card's state line gives the reason, "quota", and the resume time.

### §6.3 Commands

- `/workstreams`: add each role's headroom (for example "coder 62% · reviewer unknown"), and "deferred until HH:MM" when relevant.
- `/status`: add a section listing each account in use, its windows, their reset times, and the age of the observation.
- `/status <bead>`: add the account from the launched-session record.

### §7 Sandboxing

Replace the **Home isolation** bullet's second sentence with:

> The **auth files are ro-bound**, and only those of the account chosen for this launch (§4.1); no other account's login directory is mounted. Claude Code finds its config through `CLAUDE_CONFIG_DIR` and Codex through `CODEX_HOME`, both pointing inside the synthetic home, so a session's state stays in its synthetic home whichever account it runs under.

In the **shared refresh token** bullet, replace "the access token" with "the chosen account's access token". Add: "Refreshes are serialised by a host-side lock per account."

Add a launch self-test probe:

> - **Other accounts:** every configured account's `login_dir` other than the chosen one gives ENOENT or EACCES inside the sandbox, and is readable outside it immediately before the launch.

Add a bullet:

> - **Usage observations** that reach `wsd` from inside the sandbox (the Codex app-server, a Claude status line or transcript) are untrusted. A compromised agent could forge them, but that only affects scheduling: it can defer its own work, or move it to another of the profile's accounts.

### §8 `admind`

Add:

> - The admin agent runs on its profile's first account (`CLAUDE_CONFIG_DIR` set to that account's `login_dir`). It ignores the headroom gate and never fails over.

### §10 Failure handling

Add rows:

> | All accounts of a role exhausted | The role defers (§4.3) until the earliest reset; other roles and workstreams continue. One alert per episode in the control group. |
> | Usage source unavailable or stale | The account's usage is unknown, which is eligible. A mid-turn limit is still caught by the reactive signal and deferred. |
> | Account login invalid (the freshness gate refuses) | With `failover = "next"`, try the next eligible account; otherwise the bead becomes `needs-human`, as today. |

### §11 Testing

- **Unit:** add these:
  - the headroom gate, as a pure function of (accounts, usage, now), with hypothesis properties: unknown is eligible, `failover = "none"` never picks a later account, and a deferral always has a `defer_until` in the future;
  - deferred-park replay after a crash;
  - the session key staying the same when the account changes.
- **Integration:** the fake `claude` and `codex` gain scripted usage windows and a limit-reached signal.

### §12 Scope

v1 minimum slice: add "accounts with the headroom gate and deferred parking; the Codex usage source; the Claude usage source if S6 finds one, otherwise reactive only". Out of scope: add "an ACP adapter (revisit with the Paseo adapter; a design note is change-plan item AU-16)".

### §13 Spikes

Add:

> - **S6, accounts and usage:** (1) which files make up a login for each adapter, with `CLAUDE_CONFIG_DIR` and `CODEX_HOME` set inside the synthetic home, on Linux and macOS (keychain); (2) whether a session resumes after its bound login files change to another account of the same adapter, for both CLIs; (3) Codex `account/rateLimits/read` and `account/rateLimits/updated` on the pinned version, through the per-session app-server; (4) a structured Claude usage source from the interactive CLI (status-line JSON, transcript) and a structured limit-reached signal for both CLIs, with no screen scraping; (5) the freshness gate against two accounts of one adapter. Required before plan 4's account work starts.

### §15 Configuration

- Layer 2 description: add "accounts and usage settings" to `config.toml`.
- **What workstream config may override:** unchanged. Workstreams choose profiles, not accounts.
- The secret-name check (`docs/configuration.md`) flags any key with an `auth` segment, so the key is `login_dir`, not `auth_dir`. `login_dir` is a path, not a secret, and must not hold a secret reference.

## 4. Rejected alternatives

| Option | Why rejected |
|---|---|
| Use t3code as the execution layer, or port its drivers | Its Claude driver uses the Agent SDK (rejected in §2). It also runs every harness headless, which loses `tmux attach` and hook-derived state. It is TypeScript (§16). |
| Make the account part of the session key | A failover would become a new session, which loses resume and the session's recorded history. The account is an attribute of the launch, like the model. |
| Fail over automatically by default | The provider's terms may not allow it. Deferring is always safe. |
| A separate state-store home per account (t3code's Claude approach) | §7 already gives each session its own synthetic home. Binding only the login files avoids having to share or copy transcripts between accounts. |
| Block a bead on an "account" bead instead of deferring | It would put a non-task bead into the queue and make btq ready logic carry quota state. A time-based defer stays inside `wsd`. |
