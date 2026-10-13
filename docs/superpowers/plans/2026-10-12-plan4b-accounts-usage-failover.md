# heterodyne-metaharness Plan 4b: accounts in the launcher, usage producers and failover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the accounts change plan's three launcher items on top of plan 4's `SandboxRuntime`:

- **AU-6** (btq-bc7yd): a launch binds the chosen account's login files, named accounts included. The per-credential-identity host lock (D8) serialises every harness refresh and rereads the login under it. The compiled spec check takes revision 16's install-root exception. The Other accounts probe covers the default login whenever a named account is chosen.
- **AU-7** (btq-n1ox2): the in-session usage producers (untrusted), the trusted host-side read (Codex only), the limit-reached signals, and the reactive limit flow: defer up to the WIP commit, then the trusted read, then exhausted-and-regate or an untrusted defer on the session's own account.
- **AU-8** (btq-flxmf): launch, resume and relaunch take the gate's account. A switch is a cross-account resume (b) or a handoff relaunch (f) with `uuid5(NS, f"{session_key}:{generation}")`. A confirmed limit relaunches on the next eligible account at once. A freshness refusal moves to the next account of the same permitted list, else `needs-human`.

**Architecture:** the pure gate (AU-5), the launch entries (AU-3), the defer operation (AU-4) and the capability table (AU-2) already exist. This plan adds producers and consumers around them and never changes their rules.

- The runtime gets the pinned account and key on `LaunchSpec`. Under the D8 lock it checks that the key is unchanged and that the login is fresh. It binds that account's files and protects every other account's files, the default's included.
- Sessions report through two channels that already exist and are untrusted: the per-session socket (Claude's hooks and a status-line shim) and an in-sandbox `codex app-server` read. The runtime turns their reports into `Signal`s, attributed to `(session key, generation)`. `wsd` drains them on every pickup.
- The trusted read is `HostUsageReader`, a short-lived host-side `codex app-server` over stdio. It runs in a scratch `CODEX_HOME` that holds a copy of the account's `auth.json`, under the D8 lock, and only while the access token is valid.
- `wsd` keeps all policy: what is ingested, when a signal defers, whether a read confirms, and which account a relaunch takes. It still names no adapter.

**Every capability stays off.** S7 has demonstrated nothing on a real account, so G2 has not passed and `CAPABILITIES` stays empty. Tasks 1–10 build the machinery and test it with injected `Capabilities`. Task 11 fills the table, one capability at a time, once G2 accepts each S7 finding.

**Tech Stack:** Python 3.12+, `uv`, pytest, hypothesis, ruff, pyright strict, msgspec (the only runtime dependency), SQLite (the existing journal), `fcntl.flock`.

**Spec:**
- ADR 0001, revision 16 draft (design repo `hermes-workstreams-v2`, branch `adr-r16`, commit `0ba5010`, on r15 `28e3df4`): §4.4 D1–D10, §7 Home isolation, the freshness gate, Other accounts and the install-root exception.
- The accounts change plan `docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at `66ad195`: AU-6, AU-7 and AU-8, and gates G1–G3.
- S7, `docs/spikes/s7-accounts-and-usage.md` on `main`: the client-side findings, the L1–L7 runs and the recommended D9 defaults.
- Plan 4: D9–D13, D22, gap 3.

**Status:** draft for review. It needs a cross-model design review, then operator approval (a design-approval bead) before any task starts. Tasks 11 and 12 also wait on G2.

## Global Constraints

- **Variables:**
  - `$HZ` is a fresh clone of `Epiphytic/heterodyne-metaharness`, never the live harness or a symlink to it.
  - `$DESIGN_REPO` is the design repo.
- **Install-agnostic:** "Install-agnostic: no install paths, npubs, tokens or relay URLs." `scripts/check_install_agnostic.py` stays clean.
- **Tooling:** "Python 3.12+, matching the repo's tooling (pytest, hypothesis, ruff, pyright)." Managed with `uv`. Runtime dependencies stay exactly `msgspec`.
- **Tests stay offline, except `tests/live`.** "Tests never touch the network, a real wn-agent, the real systemctl, the real claude or codex binaries, ~/.claude, or the real beads database. Use fake executables and temp dirs."
  - Offline tests never run `openshell`, `podman` or a real CLI. The trusted read is tested against `tests/fakes/fake_agent_cli.py`, which gains an `app-server` stdio mode.
  - `tests/live/test_live_accounts.py` (Task 12) is skipped unless `HZ_LIVE=1`, `HZ_LIVE_SANDBOX=1` and `HZ_LIVE_ACCOUNTS=1` are all set.
- **Live-test model budget (testing only):**
  - Live model calls use Codex `gpt-6-luna` at `low` reasoning effort, and Claude `claude-haiku-4-5-20251001`, with a small context: one short prompt per session.
  - The test sets these through its own temporary profile config. Never change a `src/` default or a shipped config for it.
- **Never print or commit tokens, npubs or 64-hex values.**
  - Test logins are obvious fakes: `"fake-not-a-token"`, or a JWT whose payload holds only `exp`.
  - Credential keys in tests are built as `"ck1-" + 32 copies of one letter`, as `tests/test_wsd_headroom.py` does.
  - Assertions, exception texts and log lines never contain raw subprocess output, home paths, login contents or tokens. They carry only the allowlisted diagnostic categories this plan defines (`DIAG_*` constants and the `Dropped` reasons).
- **No sleeps in tests.**
  - Production code that waits takes an injected `sleep` and `clock`. That covers the lock wait and the trusted read's deadline.
  - Tests inject no-op versions, or wait on a condition with `tests/sandbox_env.py:wait_for`.
- **No adapter names in `wsd/` or `sandbox/`.** "Nothing may be specific to Claude or Codex" outside `src/heterodyne/agents/`.
  - `src/heterodyne/wsd/` names no adapter, model or CLI. Adapter differences reach it only as `Capabilities`, read through `ProfileView.capabilities`.
  - `src/heterodyne/sandbox/` names adapters only through `heterodyne.agents.registry.ADAPTERS`.
  - The existing `adapter == "claude-code"` test in `Parker._pin_and_dispatch` is left as it is. It is pre-existing, and recorded under "Contradictions found in the sources".
- **Fail closed.** "Every recovery path fails closed. Never infer absent, complete or safe from missing or unreadable evidence." In this plan:
  - Each of these gives "unconfirmed" or a refused launch, never "fine": a lock that can't be taken, a login that can't be reread, a key that doesn't match, an install-root walk that errors or hits a bound, a trusted read that times out or returns nothing parseable, and a scratch home that can't be confirmed gone.
  - An untrusted report never moves another bead or another account.
- **Never patch out the self-test, the credential check, the freshness gate, the CLI pin, the key check or the install-root walk.** No setting disables them. Tests prove each one refuses a launch.
- **Negative controls name their exact refusal.**
  - Each refusal test asserts the exact message or errno, through the source's own constant (`spec.ROOT_*`, `runtime.LOGIN_STALE`, `runtime.KEY_CHANGED`, `credlock.LOCK_BUSY`). It is never a hand-typed string, never "any exception" and never "nonzero".
  - Exit 127 or a missing executable is never a refusal: the test fails.
  - A timeout, or no verified run, fails a negative control.
- **Exit statuses are never masked.** Subprocess helpers return or raise on the real status. Nothing ends in `|| true`, and no `check=False` result goes unread.
- **Cleanup is proven.** Every subprocess the code starts (the host app-server, the in-sandbox read) is ended in a `finally`, by its own PID, then confirmed gone (`wait` returns its status). A scratch directory is removed in a `finally` and confirmed absent with `os.path.lexists`. An unconfirmed cleanup is a failed read, never a success.
- **Monitors run until the observed process ends.** A reader thread on a subprocess's stdout is joined after the process is confirmed gone, never left running.
- **`src/` style:**
  - ruff line length 110, and pyright strict.
  - Error messages name keys and checks, never paths or secret values.
  - In-sandbox scripts (`src/heterodyne/sandbox/resources/*.py`, `src/heterodyne/session/shim.py`) use the standard library only.
- **The pre-commit install-agnostic hook rejects absolute `/home` paths** in committed files. Tests build paths from `tmp_path` or `short_dir()`.
- **Long commands** use `timeout`.
  - The full suite runs in the foreground with `timeout 3600`; it takes about 8 minutes now.
  - Each task's own tests run first.
  - New test files that open a `Journal` close it (`test_wsd_gate`'s file-descriptor count fails otherwise).
- **Review rule:**
  - Every task ends with a review by a **different LLM than the implementer** (cross-model), or by a fresh-context adversarial agent when only one LLM is available.
  - The brief names the diff range and the ADR sections, and asks for `[BLOCKING]` and `[NON-BLOCKING]` findings. Every blocking finding is fixed or rebutted.
  - Review runs at most three cycles. The third asks whether the result is good enough for this stage.
  - Close evidence includes `Code-Review: reviewer=<model> author=<model> mode=<cross-model|adversarial> range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`):
  - Tasks 1–12 are `kind:task`.
  - Each carries `metadata.design_approval=<this plan's approval bead>`, `metadata.adr_revision=<the approved r16 commit>` and a blocking dependency on the approval bead.
  - Tasks 11 and 12 also block on the G2 bead.
  - AU-6, AU-7 and AU-8 (btq-bc7yd, btq-n1ox2 and btq-flxmf) close when every task that implements them has closed: Tasks 1–3 and 6 for AU-6; 4, 5, 7, 8 and 9 for AU-7; 10 for AU-8; and 11 for all three.
- **Branches:** each task has its own branch from `main` (`p4b-t<N>`) and its own PR, as plan 4's tasks did. Merge `origin/main`, never rebase, and never amend a pushed commit. Tasks that touch the same file merge in the order the Parallelism section gives.

---

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Where the ADR or the change plan is silent or loose | Choice | Why |
|---|---|---|---|
| E1 | How the runtime knows the pinned credential key (§4.4 D2: "Immediately before the launch, the guard checks that the account's current credential key still equals the journaled one"). | `LaunchSpec.credential_key` carries the entry's key. Under the D8 lock, just before it reads the login, the runtime recomputes `accounts.current_key(adapter, account)`. A mismatch refuses with `KEY_CHANGED`. An empty `credential_key` is refused the same way. | The guard's step 3 check happens before dispatch. The runtime then reads files that may have been repointed in between. Rechecking under the lock closes that window for the files actually bound. |
| E2 | The D8 lock's location and shape. | It is `<wsd state>/credlocks/<credential key>.lock`, opened by `gate.open_lock_file` (0600, no symlink, owner checked) and taken with `flock(LOCK_EX)`. The wait is bounded by `lock_wait_seconds` (default 60), polled with `LOCK_NB` and an injected clock and sleep. A wait past that bound raises `CredentialBusy(LOCK_BUSY)`. The key must match `ck1-[0-9a-f]{32}` before it is used in a path. `heterodyne setup` (AU-10) imports `credlock.credential_lock` and the same directory. | flock is per open file description, so threads in one wsd exclude each other, as `ClaimGate.locked` already relies on. The key is a digest, so the path never leaks a login path. A bounded wait turns a stuck holder into a refused launch, never a hang. |
| E3 | Whether plan 4b refreshes on the host (§7: "the launcher refreshes on the host first, or refuses to launch"; plan 4 D12 never refreshes). | **Plan 4b still never refreshes.** Under the lock, the freshness gate rereads the login and refuses with `LOGIN_STALE` (plan 4's text, moved to `wsd/runtime.py`). AU-8 then tries the next permitted account (E9). A host refresh is added only after S7 L6 records what a refresh writes and whether it rotates the refresh token (open question Q2). | S7 has not observed a completed refresh for either CLI. A harness refresh that rotates the shared refresh token and fails to save it would break the operator's host login, which is the exact hazard §7 guards against. |
| E4 | The trusted Codex read's home (§4.4 D9 (d): "a short-lived host-side app-server using the account's `CODEX_HOME`"). | The read runs in a fresh scratch `CODEX_HOME` (0700, under `<wsd state>/hostread/`). It holds only a copy of the account's `auth.json`, read under the lock. Before it starts, the access token must be valid for at least `read_margin_seconds` (default 300), so Codex never attempts a refresh (S7 (4): it attempts one only once the token has expired). If `auth.json` in the scratch home has changed when the read ends, the new bytes are written back to the login file. That write is atomic, under the lock, and only if the login file still holds the original bytes. Otherwise the change is dropped and logged as `DIAG_ROTATED_DROPPED`. The scratch home is removed in a `finally` and confirmed gone. | The app-server writes sqlite state, `installation_id` and more into its home (S7 (1)). The account's real login directory, the operator's `~/.codex` included, must not collect that. The validity precondition makes a refresh unexpected, and the compare-and-swap write-back keeps a rotated token if one happens anyway. |
| E5 | Codex's in-session usage source (§4.4 D9 (c): "`account/rateLimits/read` at launch and `account/rateLimits/updated` through the per-session app-server"). | A host-side client of the live app-server's WebSocket is not in this plan. Instead, an in-sandbox resource script, `codex_usage.py`, runs `codex app-server` over stdio in a scratch `CODEX_HOME` under `/tmp`. Its `auth.json` is a symlink to the session's read-only bound file. The script sends `account/rateLimits/read` and prints only the `rateLimits` object. The runtime runs it through `backend.exec` once the app-server has started, then every `usage_poll_seconds` (default 600) while the session runs. It runs only if the profile's capabilities enable (c). | This is the pattern `codex_trust.py` already uses: stdio JSON-RPC inside the sandbox, with the output validated on the host. The scratch home keeps the second app-server off the live one's sqlite files. The bind stays read-only, so a refresh attempt inside fails (`EROFS`), as §7 requires. Notification subscription is open question Q4. |
| E6 | Codex's limit-reached signal (§4.4 D9 (e)). | A `rateLimitReachedType` that isn't null in an in-session read is the limit signal (`LimitSeen`), with the latest `resetsAt` of any window at 100% or more as its hint. `codexErrorInfo` (`usageLimitExceeded` / `rateLimitExceeded`) needs a notification stream, which E5 doesn't have (Q4). | The field is structured, and S7 (3) documents its values. Polling it needs no subscription. It is as untrusted as any in-session report, and D6's trusted read confirms it before anything beyond the bead's own defer. |
| E7 | Claude's in-session source and signal (S7 (3), (5)). | **(c):** with (c) enabled, the Claude settings gain `"statusLine": {"type": "command", "command": "python3 -I /run/hz/shim.py status"}`. The shim's new `status` mode sends only `{"hook_event_name": "StatusLine", "session_id", "rate_limits"}`, and only when `rate_limits` differs from the last value it sent (kept in `$HOME/.hz/status.json`). It prints nothing. **(e):** a `StopFailure` hook is always installed. With `error == "rate_limit"` it is the limit signal, with no structured reset (S7: only message text, which is never parsed). **Turn end:** `StopFailure` counts as a turn boundary (S7 finding 1), always. | The status line is S7's only Claude source. It is installed only when (c) is on, so the managed launch shape T14 validated doesn't change while every capability is off. The `StopFailure` boundary is needed whatever the capabilities, or a limited turn looks like a hang to the lifetime stop. |
| E8 | How reactive-limit signals reach `wsd` (§4.4 D6, D9). | `AgentRuntime.signals(ws) -> list[Signal]` drains the reports since the last call: `UsageSeen` and `LimitSeen`, each attributed to `(session key, generation)` by the runtime from the channel it set up. The scheduler calls it in every pickup, just after `expire` and before the sweep. `wsd` ingests a `UsageSeen` only if the profile's capabilities enable (c), and acts on a `LimitSeen` only if they enable (e). | A pull keeps `wsd` the only writer of the journal, as `expire` already does. Latency is up to one backstop interval. That is acceptable: the Claude CLI waits for the reset before it continues (S7 (5)), and the session is stopped before then. |
| E9 | The freshness-refusal path (AU-8: "A freshness-gate refusal moves on to the next account of the same permitted list"). | The runtime refuses a stale login with exactly `LOGIN_STALE`, and the receipt records it as `refused`/`failed` with that error. `Parker._outcome` recognises it by equality with the constant. It adds the entry's key to the op's `stale` data (comma-separated) and forgets the generation. The next pin runs the gate with those keys excluded (`exclude=`). If the gate finds another permitted account, the op returns `FAILED` without spending the launch budget, and the next pickup launches on that account. If it finds none (`AccountChanged`), the op escalates `LAUNCH_FAILED` with the detail `LOGIN_STALE`, so the bead becomes `needs-human`. A `Deadline` defers as usual. | No schema change: `receipts.refusal` keeps its CHECK constraint (`'failed', 'unavailable'`). The exclusion is per operation, so a later operation tries the refreshed login again. Exclusions only grow, so the path ends after at most one try per account. |
| E10 | The handoff relaunch (§4.4 D7). | `LaunchSpec.handoff` is set by the guard when the new entry's key differs from the last launched entry's, and the profile's capabilities have (f) but not (b). Its `resume` is then false. The runtime's `_resume` returns `(False, None)` for a server-assigned adapter (Codex), and `(False, ids.handoff_session(key, generation))` otherwise. The native ID reaches the entry through the receipt, as it already does. With (b), a switch is an ordinary resume of the latest native ID. **The handoff prompt itself** (the §4.1 deterministic handoff) **is not built here** (Q3). | The ID rule is D7's. The runtime already knows `assigns_id`, so `wsd` stays adapter-free. (f) stays off until S7 L4 passes and a real handoff works (S7's D9 table), so nothing ships a fresh session with no handoff. |
| E11 | The reactive limit flow (§4.4 D6). | `Parker.limit(bead, launch, hint)` opens a PARK op with an **untrusted** QUOTA record clamped by D5. The record exists before anything stops, so a crash leaves the untrusted defer, never a trusted one. The op carries `limit_key` and `limit_gen`. The defer tail runs unchanged up to `defer.committed`. A new step, `defer.confirmed`, then asks `Deps.usage_reader`, but only if the profile has (d). **Confirmed** (`TrustedRead.limited`) means: the read's windows are ingested as trusted, `mark_exhausted` runs, and the gate runs again. The result supersedes the record with a trusted one: `Chosen` gives QUOTA until now (due at once), `Deadline` gives QUOTA until its `at`, and `AccountChanged` gives ACCOUNT_CHANGED. That happens in one transaction with the step. **Unconfirmed** keeps the untrusted record. The tail then labels, comments and finishes as before. A due QUOTA record goes through `undefer` in the same pickup, so the relaunch takes the next account through the guard's pin, as a new launch entry. | It reuses AU-4's crash-safe tail and AU-5's gate, and adds no new op kind. A forged signal reaches only its own bead's defer, at most `untrusted_max_defer_minutes`. "Relaunch at once" is the existing due-deferral path, which arms no timer for `until <= now`. |
| E12 | The install-root walk (r16 §7). | `spec.check_install_root(root, login_names, login_files)` runs on the host just before `backend.create`. It applies when the adapter's install root lies inside **any** login directory, the chosen account's included. That is stricter than r16, which exempts only sources inside another account's directory (the r16 follow-up commit says so). The bounds are `ROOT_MAX_ENTRIES = 100_000` and `ROOT_MAX_DEPTH = 64`, using `os.scandir` with `follow_symlinks=False`. The refusal texts are `spec.ROOT_*` constants. `check_credentials(..., install_root=...)` exempts exactly that one source from the other-account-directory rule, and only if it is canonical and bound read-only at its own path. | The same tree is bound under every account, so it gets the same proof under every account. Claude's npm-local install (`~/.claude/local`) is covered too, and that tree is the larger one, so the bound is generous. A walk that errors or hits a bound refuses the launch. |
| E13 | Other accounts with a named account chosen (§7). | `_logins(adapter, account)` puts the chosen account's directory and files in `chosen_*`. Every other configured account goes in `other_*`, and the `default` login goes there too when it isn't the chosen one. `ProbeContext.others` lists the same accounts. | This is §7's rule ("the default login included"), and plan 4's probe already handles any list. |
| E14 | A session's turn-end events (S7 finding 1). | `Adapter.turn_end_events` is `("Stop", "StopFailure")` for Claude and `("Stop",)` for Codex. `SessionServer(turn_ends=...)` counts a turn end for any of them, and `TurnState.idle` is true after one. | The server stays adapter-neutral: it is given the names, and doesn't know them. |
| E15 | Capability gating's single source. | Every capability check reads `accounts.view(profile).capabilities`. The runtime reads it for the producers (no exec without (c), no status line without (c)). `wsd` reads it again before ingesting or acting. AU-2's `config check` already refuses accounts without (a), and `"next"` without (d). | One table, checked again by the consumer. A producer bug can't enable a capability. |

## ADR conflicts and gaps, flagged for the operator (not silently resolved)

### Open questions for the operator

| # | Question | Recommendation |
|---|---|---|
| Q1 | **G2 has not passed.** S7 showed the client side only, and L1–L7 need you. AU-6, AU-7 and AU-8 are formally blocked by G2 in the change plan. | Approve building Tasks 1–10 now, gated on capabilities and tested with injected ones, so the code is ready when L1–L7 run. Task 11 (the table) and Task 12 (live) stay blocked on G2. Nothing changes on a host until Task 11 enables a capability. |
| Q2 | **The host refresh** (§7 "refreshes on the host first, or refuses"). Plan 4b keeps refusing (E3). | Keep refusing in 4b. After L6, add a refresh task. For Codex, `account/read {"refreshToken": true}` in the E4 scratch home, under the lock, with the E4 write-back. For Claude there is no known host mechanism. |
| Q3 | **The handoff prompt.** (f) needs the §4.1 deterministic handoff, and no generator exists yet (S7 L4 defers it to "the AU task that builds it"). | Build the generator with plan 5 (steering delivers the first prompt there), and keep (f) off until then. Plan 4b builds only the ID and launch mechanics (E10). |
| Q4 | **Codex's live notifications.** E5 polls rather than subscribing to `account/rateLimits/updated` and `codexErrorInfo` on the live app-server. | Accept polling for v1. L2 should record whether `updated` fires mid-turn. If it does, a later task can add a host-side client on the bridge socket. |
| Q5 | **Version drift.** S7 ran 2.1.286 and 0.160.0, while the ADR §4.2 table names 2.1.283 and 0.157.0. The code pins 2.1.286 and 0.160.0. | Fix the §4.2 table in the next ADR revision. L1–L7 run on the code's pins. |
| Q6 | **Claude's `.credentials.lock`** sits beside `.credentials.json` (S7 (1)). Under a single-file read-only bind, the CLI's lock goes into the synthetic config directory, which is writable. Whether the CLI needs it to be shared with the host is L6's question. | No change in 4b. The single-file bind already keeps the host's lock out of the sandbox. L6 records the behaviour. |
| Q7 | **Stricter install-root walk** (E12): it also runs when the root is inside the chosen account's own directory. | Accept. The r16 follow-up commit makes the ADR say so. |
| Q8 | **Failover for Claude.** Claude has no (d), so `failover = "next"` is refused for it, and a Claude limit always defers on its own account. | Accept, as S7 recommends. Revisit only if a CLI version adds a host-side read. |

### Contradictions found in the sources

1. The change plan names the spike file `S7-accounts-usage.md`. It is `docs/spikes/s7-accounts-and-usage.md`.
2. `Parker._pin_and_dispatch` tests `adapter == "claude-code"` to choose a first launch's native ID. That breaks the "wsd names no adapter" constraint. It is pre-existing (plan 3 or AU-3), and plan 4b doesn't touch it. A follow-up should move the rule behind `assigns_id` in the runtime, as E10 does for handoffs.
3. The ADR §4.2 version table is behind the code's pins (Q5).
4. Plan 4 D12 says "AU-6 adds the host refresh under the D8 lock". AU-6 in the change plan says only "the freshness gate per account, under a host file lock". E3 defers the refresh itself (Q2).
5. Plan 4's Global Constraints say the suite takes about 27 minutes. It now takes about 8.

## Relation to the accounts plan

| AU item | Scope line (change plan at `66ad195`) | Task |
|---|---|---|
| AU-6 | Bind only the chosen account's login files; `CLAUDE_CONFIG_DIR` / `CODEX_HOME` inside the home; never override `HOME` | 6 (the binds already exist in plan 4; 6 makes them follow the chosen account) |
| AU-6 | The compiled spec check before every launch | 3, 6 |
| AU-6 | The freshness gate per account, under a lock keyed by credential identity, rereading under it | 2, 6 |
| AU-6 | The new self-test probe (Other accounts, default included, canary) | 6 (plan 4's probe; the list now includes the default) |
| AU-6 | The account on the launch entry; the env allowlist | already in AU-3 and plan 4 (`LAUNCHER_ENV`); 1 adds `credential_key` to `LaunchSpec` |
| AU-6 | Fill the table for (a) and (b) | 11 |
| AU-7 | In-session sources, untrusted, attributed to the launch | 4, 5, 7, 9 |
| AU-7 | The trusted host-side read under the D8 lock | 8 |
| AU-7 | The limit-reached signal and the reactive flow | 5, 7, 9 |
| AU-7 | Fill the table for (c), (d) and (e) | 11 |
| AU-8 | Launch, resume and relaunch take the gate's account; switching per D7 | 1, 6, 10 |
| AU-8 | After a confirmed limit with `"next"`, relaunch at once on the next eligible account | 9 |
| AU-8 | A freshness refusal moves to the next account, else `needs-human` | 10 |

## Parallelism

Tasks 1–5 have no dependencies on each other, and five worker sessions can build them at once. Each later task starts once everything it depends on has merged to `main`.

```
Wave 1 (parallel):  T1 interfaces   T2 credlock   T3 install-root walk   T4 turn ends   T5 adapter parsing
                       |  \  \          |   \           |                   |              |    \
Wave 2 (parallel):     |   \  T9 limit  |    \          |                   |              |     \
                       |    T10 failover |     T8 trusted read <-------------+--------------+------ (T1,T2,T5)
                       +------------- T6 named-account binding (T1,T2,T3)
Wave 3:                                  T7 runtime signals (T4,T5,T6)
Wave 4 (G2-gated):                       T11 capability table (T6-T10, G2)  ->  T12 live test (T11)
```

| Task | Depends on | Can run beside | Files it shares (merge order) |
|---|---|---|---|
| 1 | none | 2, 3, 4, 5 | `wsd/runtime.py`, `wsd/headroom.py`, `wsd/usage.py`, `wsd/workstream.py`, `tests/fakes/fake_runtime.py` |
| 2 | none | 1, 3, 4, 5 | none |
| 3 | none | 1, 2, 4, 5 | `sandbox/spec.py` |
| 4 | none | 1, 2, 3, 5 | `session/server.py`, `agents/base.py`, `agents/claude_code.py` (4 before 5) |
| 5 | none | 1, 2, 3, 4 | `agents/base.py`, `agents/claude_code.py`, `agents/codex.py`, `session/shim.py` (merges after 4) |
| 6 | 1, 2, 3 | 8, 9, 10 | `sandbox/runtime.py`, `sandbox/build.py` |
| 8 | 1, 2, 5 | 6, 9, 10 | `wsd/cli.py`, `sandbox/build.py` (merges after 6) |
| 9 | 1 | 6, 8, 10 | `wsd/park.py` (`limit`, `defer_tail`), `wsd/scheduler.py` |
| 10 | 1 | 6, 8, 9 | `wsd/park.py` (`_pin`, `_pin_holds`, `_pin_and_dispatch`, `_outcome`; merges after 9) |
| 7 | 4, 5, 6 | none | `sandbox/runtime.py` |
| 11 | 6–10, G2 | none | `config/capabilities.py` |
| 12 | 11 | none | `tests/live/` |

## File map

| File | Tasks | Responsibility |
|---|---|---|
| `src/heterodyne/wsd/runtime.py` | 1 | `LaunchSpec.credential_key` and `.handoff`, `LOGIN_STALE`, `KEY_CHANGED`, `UsageSeen`, `LimitSeen`, `Signal`, `AgentRuntime.signals`, `NoRuntime.signals` |
| `src/heterodyne/wsd/headroom.py` | 1 | `exclude` on `permitted`, `gate` and `admits` |
| `src/heterodyne/wsd/usage.py` | 1 | `exclude` on `decide` and `admitted`; `TrustedRead`, `UsageReader`, `NoUsageReader` |
| `src/heterodyne/wsd/ids.py` | 1 | `handoff_session` |
| `src/heterodyne/wsd/workstream.py` | 1 | `Deps.usage_reader` |
| `src/heterodyne/wsd/credlock.py` (new) | 2 | the D8 lock |
| `src/heterodyne/sandbox/spec.py` | 3 | `check_install_root`; `check_credentials(install_root=)` |
| `src/heterodyne/session/server.py` | 4 | `turn_ends`, `on_event`, `TurnState.at_boundary` |
| `src/heterodyne/agents/base.py` | 4, 5 | `turn_end_events`, `UsageReport`, `usage_from_event`, `run_files(usage=)` |
| `src/heterodyne/agents/claude_code.py` | 4, 5 | the `StopFailure` hook, the status line, parsing |
| `src/heterodyne/agents/codex.py` | 5 | `parse_rate_limits`, `usage_argv` |
| `src/heterodyne/session/shim.py` | 5 | the `status` mode |
| `src/heterodyne/sandbox/resources/codex_usage.py` (new) | 5 | the in-sandbox read |
| `src/heterodyne/sandbox/runtime.py` | 6, 7 | named-account binding, the key check, the locked freshness gate, the walk, the handoff, `signals` |
| `src/heterodyne/sandbox/settings.py` | 6, 7 | `lock_wait_seconds`, `usage_poll_seconds` |
| `src/heterodyne/sandbox/build.py` | 6, 8 | the lock directory; the reader |
| `src/heterodyne/agents/hostread.py` (new) | 8 | `HostUsageReader` |
| `src/heterodyne/wsd/cli.py`, `src/heterodyne/wsd/daemon.py` | 8 | wiring `Deps.usage_reader` |
| `src/heterodyne/wsd/park.py` | 9, 10 | `limit`, `defer.confirmed`, the pinned key and handoff, the stale path |
| `src/heterodyne/wsd/scheduler.py` | 9 | draining `signals` |
| `src/heterodyne/config/capabilities.py` | 11 | the table |
| `tests/fakes/fake_runtime.py`, `tests/fakes/fake_agent_cli.py` | 1, 5, 8 | scripted signals; the fake `app-server` stdio mode |
| `tests/live/test_live_accounts.py` (new) | 12 | the live test |

---

## Task 1: the wsd seams (spec fields, refusal texts, signals, exclusion, the reader)

Everything later tasks share on the `wsd` side, with no behaviour change while nothing produces a signal, no reader is wired and no key is excluded.

**Files:**
- Modify: `src/heterodyne/wsd/runtime.py` (`LaunchSpec`, the refusal texts, the signals, `AgentRuntime.signals`, `NoRuntime.signals`)
- Modify: `src/heterodyne/wsd/headroom.py` (`permitted`, `gate`, `admits`)
- Modify: `src/heterodyne/wsd/usage.py` (`decide`, `admitted`; `TrustedRead`, `UsageReader`, `NoUsageReader`)
- Modify: `src/heterodyne/wsd/ids.py` (`handoff_session`)
- Modify: `src/heterodyne/wsd/workstream.py` (`Deps.usage_reader`)
- Modify: `tests/fakes/fake_runtime.py` (`report`, `signals`)
- Test: `tests/test_wsd_headroom.py`, `tests/test_wsd_ids.py`, `tests/test_wsd_signals.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `wsd.runtime.LaunchSpec(..., credential_key: str = "", handoff: bool = False)`: two new trailing fields.
  - `wsd.runtime.LOGIN_STALE: str` and `wsd.runtime.KEY_CHANGED: str`: the exact `LaunchFailed` texts for a stale login (E3) and a changed key (E1).
  - `wsd.runtime.UsageSeen(session_key: str, generation: int, observations: tuple[Observation, ...])`, `wsd.runtime.LimitSeen(session_key: str, generation: int, reset_hint: int | None)` and `type Signal = UsageSeen | LimitSeen`.
  - `AgentRuntime.signals(ws: str) -> list[Signal]`; `NoRuntime.signals` returns `[]`.
  - `headroom.permitted(p, previous_key, exclude: frozenset[str] = frozenset())`; `gate(..., s, exclude=frozenset())`; `admits(..., s, exclude=frozenset())`; `usage.decide(..., s, exclude=frozenset())`; `usage.admitted(..., s, exclude=frozenset())`.
  - `usage.TrustedRead(observations: tuple[Observation, ...], limited: bool, reset_hint: int | None)`, the `usage.UsageReader` protocol `read(profile: str, account: str, credential_key: str) -> TrustedRead | None`, and `usage.NoUsageReader`.
  - `ids.handoff_session(session_key: str, generation: int) -> str`.
  - `Deps.usage_reader: UsageReader` (default `NoUsageReader()`).
  - `FakeRuntime.report(ws: str, *signals: Signal)` and `FakeRuntime.signals(ws)`, which drains that workstream's queue; `FakeRuntime.drains: list[str]` records each call.

`wsd/runtime.py` may import `heterodyne.wsd.usage`: `usage` imports `config`, `wsd.accounts`, `wsd.headroom`, `wsd.journal` and `wsd.launches`, and none of them imports `wsd.runtime`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wsd_headroom.py`:

```python
# --- exclusion (plan 4b E9) ---

def test_excluded_keys_are_never_permitted() -> None:
    p = view(A, B, C, caps=SWITCH)
    assert permitted(p, None, frozenset({A.key})) == (B, C)
    assert permitted(p, A.key, frozenset({A.key})) == (B, C)            # affinity, less the stale key
    assert permitted(view(A, B), A.key, frozenset({A.key})) == ()      # can't switch: only A, excluded
    assert permitted(view(A, B, failover="none"), None, frozenset({A.key})) == ()
    assert gate(p, None, UsageCache(), NOW, S, exclude=frozenset({A.key})) == Chosen("b", B.key)
    assert isinstance(gate(view(A), None, UsageCache(), NOW, S, exclude=frozenset({A.key})), AccountChanged)
    entry = LaunchEntry("sk", 1, "alpha", "btq-1", "coder", "p-one", "a", A.key, "", False, "t")
    assert admits(p, None, entry, UsageCache(), NOW, S)
    assert not admits(p, None, entry, UsageCache(), NOW, S, exclude=frozenset({A.key}))


@given(st.sets(st.sampled_from([A.key, B.key, C.key])), st.sampled_from([None, A.key, B.key]),
       st.booleans(), st.sampled_from(["none", "next"]))
def test_exclusion_only_removes_keys(excluded: set[str], previous: str | None, switch: bool,
                                     failover: Failover) -> None:
    p = view(A, B, C, failover=failover, caps=SWITCH if switch else NONE)
    assert permitted(p, previous, frozenset(excluded)) == tuple(
        c for c in permitted(p, previous) if c.key not in excluded)


def test_decide_and_admitted_pass_the_exclusion_through(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}")
    accounts = ConfiguredAccounts({"p-two": ProfileAccounts("codex", failover="next")}, {"HOME": str(home)})
    j = Journal(tmp_path / "wsd.db")
    try:
        key = accounts.current_key("codex", "default")
        assert isinstance(decide(accounts, j, "p-two", None, NOW, S, exclude=frozenset({key})), AccountChanged)
        entry = LaunchEntry("sk", 1, "alpha", "btq-1", "coder", "p-two", "default", key, "", False, "t")
        assert admitted(accounts, j, None, entry, NOW, S)
        assert not admitted(accounts, j, None, entry, NOW, S, exclude=frozenset({key}))
    finally:
        j.close()
```

Append to `tests/test_wsd_ids.py`:

```python
def test_a_handoff_session_is_new_per_generation_and_recomputable() -> None:
    key = ids.role_session("btq-1", "coder", "p-one")
    assert ids.handoff_session(key, 2) == str(uuid.uuid5(ids.NS, f"{key}:2"))
    assert ids.handoff_session(key, 2) != ids.handoff_session(key, 3)
    assert ids.handoff_session(key, 2) != key
```

Create `tests/test_wsd_signals.py`:

```python
"""Plan 4b Task 1: the runtime's signal seam and the trusted-read seam, before anything produces them."""

from pathlib import Path

from fakes.fake_runtime import FakeRuntime

from heterodyne.wsd.runtime import (
    KEY_CHANGED,
    LOGIN_STALE,
    LaunchSpec,
    LimitSeen,
    NoRuntime,
    UsageSeen,
)
from heterodyne.wsd.usage import NoUsageReader, Observation, TrustedRead

KEY = "ck1-" + "a" * 32


def test_a_spec_pins_no_key_and_no_handoff_by_default(tmp_path: Path) -> None:
    spec = LaunchSpec("alpha", "btq-1", "coder", "p-one", "sk", "label", tmp_path, False)
    assert spec.credential_key == "" and spec.handoff is False


def test_the_refusal_texts_are_fixed_and_distinct() -> None:
    assert LOGIN_STALE == "the login expires within the session's maximum lifetime; refresh it on the host"
    assert KEY_CHANGED != LOGIN_STALE and "ck1-" not in KEY_CHANGED


def test_no_runtime_has_no_signals() -> None:
    assert NoRuntime().signals("alpha") == []


def test_the_fake_runtime_drains_each_workstream_once() -> None:
    rt = FakeRuntime()
    usage = UsageSeen("sk", 1, (Observation("w5h", "5h", 50.0, None, "codex.app-server"),))
    rt.report("alpha", usage, LimitSeen("sk", 1, None))
    rt.report("beta", LimitSeen("sk-b", 2, 1_800_000_000))
    assert rt.signals("alpha") == [usage, LimitSeen("sk", 1, None)]
    assert rt.signals("alpha") == []
    assert rt.signals("beta") == [LimitSeen("sk-b", 2, 1_800_000_000)]
    assert rt.drains == ["alpha", "alpha", "beta"]


def test_no_usage_reader_never_confirms() -> None:
    assert NoUsageReader().read("p-one", "default", KEY) is None
    assert TrustedRead((), limited=True, reset_hint=None).limited
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_wsd_headroom.py tests/test_wsd_ids.py tests/test_wsd_signals.py -q`
Expected: FAIL. `tests/test_wsd_signals.py` fails at import (`cannot import name 'KEY_CHANGED'`); the headroom tests fail with `TypeError: permitted() takes 2 positional arguments but 3 were given`; the ids test fails with `AttributeError: module 'heterodyne.wsd.ids' has no attribute 'handoff_session'`.

- [ ] **Step 3: Implement**

In `src/heterodyne/wsd/runtime.py`, add the import and the texts after `SETTLED_ACTIONS`:

```python
from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.usage import Observation
```

```python
# The LaunchFailed texts the guard acts on, compared by equality (plan 4b E1, E3, E9). A runtime that
# refuses for these reasons raises exactly these texts and nothing else with them.
LOGIN_STALE = "the login expires within the session's maximum lifetime; refresh it on the host"
KEY_CHANGED = "the account's credential key changed after the launch was pinned"
```

Add the two fields at the end of `LaunchSpec`, after `repo`:

```python
    # The entry's pinned credential key (plan 4b E1). The runtime recomputes the account's key under the
    # D8 lock and refuses with KEY_CHANGED unless it equals this; an empty key is refused the same way.
    credential_key: str = ""
    # A cross-account relaunch without (b): a fresh native session, never a resume (D7, plan 4b E10).
    handoff: bool = False
```

Add the signals after `Started`:

```python
@dataclass(frozen=True)
class UsageSeen:
    """A session's own usage report (untrusted, D3). The runtime attributes it to the launch whose channel
    carried it, never to anything the payload says."""
    session_key: str
    generation: int
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class LimitSeen:
    """A session reported reaching its usage limit (untrusted, D6). `reset_hint` is the payload's own
    reset time, if it gave one; wsd clamps it."""
    session_key: str
    generation: int
    reset_hint: int | None


type Signal = UsageSeen | LimitSeen
```

Add to `AgentRuntime`, after `expire`:

```python
    def signals(self, ws: str) -> list[Signal]:
        """Drain the reports the workstream's sessions have sent since the last call, oldest first. Each
        is attributed to the (session key, generation) whose channel carried it. Never raises: a report
        the runtime can't attribute is never queued."""
        ...
```

Add to `NoRuntime`:

```python
    def signals(self, ws: str) -> list[Signal]:
        return []                    # it runs nothing
```

In `src/heterodyne/wsd/headroom.py`, replace `permitted`, and add `exclude` to `gate` and `admits`:

```python
def permitted(p: ProfileView, previous_key: str | None,
              exclude: frozenset[str] = frozenset()) -> tuple[Candidate, ...]:
    """D4: the failover mode's list, then credential continuity, less the keys in `exclude` (the logins
    this operation already found stale, plan 4b E9). Excluding only ever removes accounts."""
    listed = p.accounts[:1] if p.failover == "none" else p.accounts
    if previous_key is None:
        found = listed
    elif p.failover == "none":
        found = listed if listed and listed[0].key == previous_key else ()
    else:
        previous = tuple(c for c in listed if c.key == previous_key)[:1]
        found = previous + tuple(c for c in listed if c not in previous) if p.capabilities.can_switch \
            else previous                                                   # affinity
    return tuple(c for c in found if c.key not in exclude)
```

```python
def gate(p: ProfileView, previous_key: str | None, cache: UsageCache, now: int, s: UsageSettings,
         exclude: frozenset[str] = frozenset()) -> Chosen | AccountChanged | Deadline:
    """The first eligible permitted account; `AccountChanged` when nothing is permitted; otherwise the
    earliest time a permitted account clears, never earlier than `now + min_recheck_seconds`."""
    allowed = permitted(p, previous_key, exclude)
```

(the rest of `gate` is unchanged)

```python
def admits(p: ProfileView, previous_key: str | None, entry: LaunchEntry, cache: UsageCache, now: int,
           s: UsageSettings, exclude: frozenset[str] = frozenset()) -> bool:
    """The guard's step 3: the pinned account is still permitted, under the key it was pinned with, and
    nothing blocks it."""
    key = entry.credential_key
    return (any(c.account == entry.account and c.key == key for c in permitted(p, previous_key, exclude))
            and not blocking(key, cache, now, s))
```

In `src/heterodyne/wsd/usage.py`, add `from typing import Protocol`, pass `exclude` through, and add the reader seam after `admitted`:

```python
def decide(accounts: Accounts, j: Journal, profile: str, previous_key: str | None, now: int,
           s: UsageSettings, exclude: frozenset[str] = frozenset()) -> Chosen | AccountChanged | Deadline:
    """The gate on the profile as it resolves now. Raises ConfigError (path-free) if it can't resolve."""
    view = accounts.view(profile)
    return gate(view, previous_key, j.usage_cache(c.key for c in view.accounts), now, s, exclude)


def admitted(accounts: Accounts, j: Journal, previous_key: str | None, entry: LaunchEntry, now: int,
             s: UsageSettings, exclude: frozenset[str] = frozenset()) -> bool:
    """The guard's step 3 for a pinned entry, through the same view and cache as `decide`. A profile or
    key that can't be resolved has changed: not admitted."""
    try:
        view = accounts.view(entry.profile)
    except ConfigError:
        return False
    return admits(view, previous_key, entry, j.usage_cache(c.key for c in view.accounts), now, s, exclude)


@dataclass(frozen=True)
class TrustedRead:
    """A host-side read (D9 (d)), run under the D8 lock on one credential key: the windows it saw, whether
    any limit is reached, and the latest reset of a reached window, if it gave one."""
    observations: tuple[Observation, ...]
    limited: bool
    reset_hint: int | None


class UsageReader(Protocol):
    def read(self, profile: str, account: str, credential_key: str) -> TrustedRead | None:
        """A trusted read of `account`'s usage, attempted only while the account's current key is
        `credential_key`. None when it can't be read now (no reader for the profile's adapter, a changed
        key, a busy lock, a stale login, a timeout, an answer that doesn't parse, a cleanup that can't be
        confirmed): unconfirmed, never "fine". Never raises."""
        ...


class NoUsageReader:
    """No trusted read is wired in: every limit stays unconfirmed (the untrusted defer only)."""

    def read(self, profile: str, account: str, credential_key: str) -> TrustedRead | None:
        return None
```

In `src/heterodyne/wsd/ids.py`, after `role_session`:

```python
def handoff_session(session_key: str, generation: int) -> str:
    """A handoff relaunch's fresh native session ID (D7): new for each generation, recomputable. A
    session key holds no colon-free bead, so this never equals a role_session input."""
    return str(uuid.uuid5(NS, f"{session_key}:{generation}"))
```

In `src/heterodyne/wsd/workstream.py`, import `from heterodyne.wsd.usage import NoUsageReader, UsageReader` and add the field after `clock`:

```python
    # The trusted host-side read (D9 (d), plan 4b Task 8). The default confirms nothing.
    usage_reader: UsageReader = field(default_factory=NoUsageReader)
```

In `tests/fakes/fake_runtime.py`, import `Signal` from `heterodyne.wsd.runtime`, add to `__init__`:

```python
        self.queued: dict[str, list[Signal]] = {}   # reports by workstream, drained by signals()
        self.drains: list[str] = []
```

and add after `expire`:

```python
    def signals(self, ws: str) -> list[Signal]:
        self.drains.append(ws)
        return self.queued.pop(ws, [])
```

and, under the test controls:

```python
    def report(self, ws: str, *signals: Signal) -> None:
        """The workstream's sessions sent these reports (already attributed, as the runtime does)."""
        self.queued.setdefault(ws, []).extend(signals)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_wsd_headroom.py tests/test_wsd_ids.py tests/test_wsd_signals.py tests/test_wsd_usage.py -q`
Expected: PASS.

Then: `timeout 300 uv run pyright src/heterodyne/wsd tests/fakes/fake_runtime.py tests/test_wsd_signals.py` and `uv run ruff check src tests`.
Expected: 0 errors. pyright checks that `FakeRuntime` and `NoRuntime` still satisfy `AgentRuntime` wherever they are passed as one.

- [ ] **Step 5: Run the whole suite, then commit**

Run: `timeout 3600 uv run pytest -q`
Expected: PASS (no behaviour changed).

```bash
git add src/heterodyne/wsd/runtime.py src/heterodyne/wsd/headroom.py src/heterodyne/wsd/usage.py \
        src/heterodyne/wsd/ids.py src/heterodyne/wsd/workstream.py tests/fakes/fake_runtime.py \
        tests/test_wsd_headroom.py tests/test_wsd_ids.py tests/test_wsd_signals.py
git commit -m "plan4b T1: wsd seams for keys, handoffs, signals, exclusion and the trusted read"
```

## Task 2: the D8 credential lock

One host lock per credential identity (§4.4 D8). Every harness read of a login and every write back to one holds it: the runtime's key check and freshness reread (Task 6), the trusted read and its write-back (Task 8), and later `heterodyne setup` (AU-10).

**Files:**
- Create: `src/heterodyne/wsd/credlock.py`
- Modify: `src/heterodyne/wsd/settings.py` (`WsdSettings.credlock_dir`)
- Test: `tests/test_wsd_credlock.py` (new)

**Interfaces:**
- Consumes: `heterodyne.fsutil.private_dir(path)`, `heterodyne.wsd.gate.open_lock_file(path) -> int`.
- Produces:
  - `credlock.credential_lock(lock_dir: Path, key: str, wait_seconds: float, *, clock=time.monotonic, sleep=time.sleep)`: a context manager that holds `flock(LOCK_EX)` on `<lock_dir>/<key>.lock` for its body.
  - `credlock.CredentialBusy(Exception)`, whose text is exactly one of `credlock.LOCK_BUSY`, `credlock.LOCK_FAILED` or `credlock.BAD_KEY`.
  - `credlock.KEY`: the compiled `ck1-[0-9a-f]{32}` pattern.
  - `WsdSettings.credlock_dir -> Path`: `state_dir / "credlocks"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wsd_credlock.py`:

```python
"""Plan 4b Task 2: the D8 lock, one per credential key. Keys are obvious fakes."""

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

from heterodyne.wsd import credlock
from heterodyne.wsd.credlock import BAD_KEY, LOCK_BUSY, LOCK_FAILED, CredentialBusy, credential_lock

KEY = "ck1-" + "a" * 32
OTHER = "ck1-" + "b" * 32
HOLDER = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
print("locked", flush=True)
sys.stdin.read()
"""


class Ticks:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def free(path: Path) -> bool:
    """Whether another open file description can take the lock now."""
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def test_the_lock_is_held_for_the_body_and_released_after(tmp_path: Path) -> None:
    locks = tmp_path / "credlocks"
    with credential_lock(locks, KEY, 0):
        assert not free(locks / f"{KEY}.lock")
    assert free(locks / f"{KEY}.lock")
    assert (os.stat(locks).st_mode & 0o777) == 0o700
    assert (os.stat(locks / f"{KEY}.lock").st_mode & 0o777) == 0o600


def test_a_second_holder_waits_its_bound_then_is_busy(tmp_path: Path) -> None:
    ticks = Ticks()
    with credential_lock(tmp_path, KEY, 0), pytest.raises(CredentialBusy) as busy, \
            credential_lock(tmp_path, KEY, 1.0, clock=ticks.clock, sleep=ticks.sleep):
        pytest.fail("the lock was taken twice")
    assert str(busy.value) == LOCK_BUSY
    assert ticks.slept and sum(ticks.slept) == pytest.approx(1.0)
    assert max(ticks.slept) <= credlock.POLL_SECONDS
    with credential_lock(tmp_path, OTHER, 0):          # another key is another lock
        pass


def test_a_holder_in_another_process_excludes_this_one(tmp_path: Path) -> None:
    lock = tmp_path / f"{KEY}.lock"
    with credential_lock(tmp_path, KEY, 0):            # creates the file
        pass
    proc = subprocess.Popen([sys.executable, "-I", "-c", HOLDER, str(lock)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline() == "locked\n"
        with pytest.raises(CredentialBusy) as busy, credential_lock(tmp_path, KEY, 0):
            pytest.fail("the lock was taken while another process held it")
        assert str(busy.value) == LOCK_BUSY
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0
    with credential_lock(tmp_path, KEY, 0):
        pass


def test_the_lock_is_released_when_the_body_raises(tmp_path: Path) -> None:
    with pytest.raises(ZeroDivisionError), credential_lock(tmp_path, KEY, 0):
        _ = 1 / 0
    assert free(tmp_path / f"{KEY}.lock")


@pytest.mark.parametrize("key", ["", "ck1-" + "A" * 32, "ck1-" + "a" * 31, "../" + KEY, KEY + "/x",
                                 "ck2-" + "a" * 32])
def test_a_key_that_is_not_a_key_is_refused_before_any_path(tmp_path: Path, key: str) -> None:
    with pytest.raises(CredentialBusy) as bad, credential_lock(tmp_path / "credlocks", key, 0):
        pytest.fail("a bad key was locked")
    assert str(bad.value) == BAD_KEY
    assert not (tmp_path / "credlocks").exists()


def test_a_symlinked_lock_file_is_refused_and_its_target_untouched(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    locks = tmp_path / "credlocks"
    locks.mkdir(mode=0o700)
    (locks / f"{KEY}.lock").symlink_to(victim)
    with pytest.raises(CredentialBusy) as failed, credential_lock(locks, KEY, 0):
        pytest.fail("a symlinked lock was taken")
    assert str(failed.value) == LOCK_FAILED
    assert victim.read_text() == "keep"


def test_a_symlinked_lock_dir_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "credlocks").symlink_to(real)
    with pytest.raises(CredentialBusy) as failed, credential_lock(tmp_path / "credlocks", KEY, 0):
        pytest.fail("a symlinked lock directory was used")
    assert str(failed.value) == LOCK_FAILED
    assert list(real.iterdir()) == []


def test_the_texts_name_no_key_or_path() -> None:
    for text in (LOCK_BUSY, LOCK_FAILED, BAD_KEY):
        assert "ck1-" not in text and "/" not in text
```

Append to `tests/test_wsd_settings.py`:

```python
def test_the_credential_locks_live_in_the_state_dir(cfg: Path) -> None:
    s = resolve(env(cfg))
    assert s.credlock_dir == s.state_dir / "credlocks"
    assert s.credlock_dir != s.lock_dir
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_wsd_credlock.py tests/test_wsd_settings.py -q`
Expected: FAIL at import (`No module named 'heterodyne.wsd.credlock'`), and `AttributeError: 'WsdSettings' object has no attribute 'credlock_dir'`.

- [ ] **Step 3: Implement**

Create `src/heterodyne/wsd/credlock.py`:

```python
"""The per-credential-identity host lock (ADR 0001 §4.4 D8; plan 4b E2).

Every harness read of a login and every write back to one holds the lock of the login's credential key:
the runtime's key check and freshness reread, the trusted host-side read and its write-back, and
`heterodyne setup`. A key is a digest of the login's paths (AU-2), so the lock file's name never leaks a
path. flock locks an open file description, so two threads of one process exclude each other as two
processes do. The wait is bounded: a holder that never lets go refuses the operation, never hangs it.
"""

import contextlib
import fcntl
import os
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from heterodyne.fsutil import private_dir
from heterodyne.wsd.gate import open_lock_file

KEY = re.compile(r"ck1-[0-9a-f]{32}")
POLL_SECONDS = 0.05
LOCK_BUSY = "the account's login is locked by another harness operation"
LOCK_FAILED = "the account's login lock can't be opened or taken"
BAD_KEY = "the credential key is not a valid key"


class CredentialBusy(Exception):
    """The lock was not taken; nothing under it ran. The text is LOCK_BUSY, LOCK_FAILED or BAD_KEY."""


@contextlib.contextmanager
def credential_lock(lock_dir: Path, key: str, wait_seconds: float, *,
                    clock: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], None] = time.sleep) -> Iterator[None]:
    """Hold the lock of `key` for the body. The key is checked before it is used in a path. The lock
    directory is made 0700 (a symlink is refused) and the lock file 0600, never through a link. The wait
    polls without blocking, for at most `wait_seconds` in all."""
    if not KEY.fullmatch(key):
        raise CredentialBusy(BAD_KEY)
    try:
        private_dir(lock_dir)
        fd = open_lock_file(lock_dir / f"{key}.lock")
    except OSError:
        raise CredentialBusy(LOCK_FAILED) from None
    try:
        end = clock() + wait_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                left = end - clock()
                if left <= 0:
                    raise CredentialBusy(LOCK_BUSY) from None
                sleep(min(POLL_SECONDS, left))
            except OSError:
                raise CredentialBusy(LOCK_FAILED) from None
        yield
    finally:
        os.close(fd)                # closing the descriptor releases the lock
```

In `src/heterodyne/wsd/settings.py`, add after `lock_dir`:

```python
    @property
    def credlock_dir(self) -> Path:
        """The D8 locks, one file per credential key (plan 4b E2). `heterodyne setup` uses the same one."""
        return self.state_dir / "credlocks"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_wsd_credlock.py tests/test_wsd_settings.py -q`
Expected: PASS. `test_a_holder_in_another_process_excludes_this_one` must pass, not skip: it is the cross-process proof.

Then: `timeout 300 uv run pyright src/heterodyne/wsd/credlock.py tests/test_wsd_credlock.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/wsd/credlock.py src/heterodyne/wsd/settings.py tests/test_wsd_credlock.py \
        tests/test_wsd_settings.py
git commit -m "plan4b T2: the D8 credential lock"
```

## Task 3: the install-root walk and the exemption for the CLI's own bind

r16 §7: the Codex CLI installs under `~/.codex`, the default login's directory. Once a named account is chosen, the default login is another account's, and plan 4's check refuses the CLI's bind ("a bind would reach into another account's login directory"). r16 replaces that refusal, for the CLI's own bind only, with a proof: a bounded walk of the install root that finds nothing that is, names or reaches a login file. Plan 4b runs the walk whenever the root lies inside any login directory, the chosen one's included (E12).

**Files:**
- Modify: `src/heterodyne/sandbox/spec.py` (the `ROOT_*` texts and bounds, `OTHER_ACCOUNT_DIR`, `check_install_root`, `SandboxSpec.cli_root`, `build_spec`, `check_credentials`)
- Test: `tests/test_sandbox_spec.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `spec.check_install_root(root: Path, login_names: Iterable[str], login_files: Iterable[Path]) -> None`, raising `SpecRefused` with exactly one of `spec.ROOT_NOT_CANONICAL`, `ROOT_LOGIN_NAME`, `ROOT_LOGIN_ALIAS`, `ROOT_LINK_OUT`, `ROOT_SPECIAL`, `ROOT_TOO_LARGE` or `ROOT_WALK_FAILED`.
  - `spec.ROOT_MAX_ENTRIES = 100_000` and `spec.ROOT_MAX_DEPTH = 64`, read at call time.
  - `spec.OTHER_ACCOUNT_DIR`: the existing refusal text, now a constant.
  - `SandboxSpec.cli_root: Path | None = None`, set by `build_spec` from `AgentFacts.cli_root`.
  - `check_credentials(spec, protected, real_home)` keeps its signature. It walks `spec.cli_root` when that lies inside a login directory, then exempts exactly the bind `Bind(cli_root, cli_root, True)` from the other-account rule. Task 6 relies on this for named Codex accounts.

The walk cannot be skipped by a caller: it is part of `check_credentials`, which plan 4's runtime already calls on every launch just before `backend.create`, and `build_spec` always records the root.

- [ ] **Step 1: Write the failing tests**

In `tests/test_sandbox_spec.py`, import `heterodyne.sandbox.spec as spec_mod`, `os`, and the new names (`OTHER_ACCOUNT_DIR`, `ROOT_LINK_OUT`, `ROOT_LOGIN_ALIAS`, `ROOT_LOGIN_NAME`, `ROOT_NOT_CANONICAL`, `ROOT_SPECIAL`, `ROOT_TOO_LARGE`, `ROOT_WALK_FAILED`, `check_install_root`). Make `facts` build from the canonical temporary directory, so the CLI root is canonical as `Cli.root` always is (`find_binary` resolves it):

```python
def facts(root: Path) -> AgentFacts:
    cli = root.resolve() / "home" / ".codex" / "packages" / "rel" / "bin"
```

(the rest of `facts` is unchanged). Then append:

```python
# --- the install-root walk (r16 §7, plan 4b E12) ---

def named(root: Path) -> tuple[SpecInput, Protected]:
    """A named account (.codex-b) chosen: the default login, which holds the CLI, is another account's."""
    chosen = root / "home" / ".codex-b"
    chosen.mkdir(parents=True, exist_ok=True)
    (chosen / "auth.json").write_text("{}")
    i = spec_input(root)
    default = root / "home" / ".codex"
    return (replace(i, login_files=(chosen / "auth.json",)),
            Protected(chosen_dir=chosen, chosen_files=(chosen / "auth.json",), other_dirs=(default,),
                      other_files=(default / "auth.json",)))


def cli_root(spec: SandboxSpec) -> Path:
    assert spec.cli_root is not None
    return spec.cli_root


def test_the_spec_records_the_cli_root(tmp_path: Path) -> None:
    i = spec_input(tmp_path)
    assert build_spec(i).cli_root == i.agent.cli_root


def test_the_cli_inside_another_accounts_login_dir_passes_once_walked(tmp_path: Path) -> None:
    i, prot = named(tmp_path)
    spec = build_spec(i)
    check_credentials(spec, prot, tmp_path / "home")
    with pytest.raises(SpecRefused) as refused:            # plan 4's rule, for any other bind
        check_credentials(replace(spec, binds=(*spec.binds, Bind(tmp_path / "home" / ".codex" / "packages",
                                                                Path("/mnt/x"), True))),
                          prot, tmp_path / "home")
    assert str(refused.value) == OTHER_ACCOUNT_DIR


@pytest.mark.parametrize("change", [{"read_only": False}, {"target": Path("/mnt/cli")}])
def test_only_the_cli_bind_read_only_at_its_own_path_is_exempt(tmp_path: Path,
                                                               change: dict[str, object]) -> None:
    i, prot = named(tmp_path)
    spec = build_spec(i)
    root = cli_root(spec)
    binds = tuple(replace(b, **change) if b.source == root else b for b in spec.binds)  # type: ignore[arg-type]
    with pytest.raises(SpecRefused) as refused:
        check_credentials(replace(spec, binds=binds), prot, tmp_path / "home")
    assert str(refused.value) == OTHER_ACCOUNT_DIR


def test_a_walk_refusal_refuses_the_launch_inside_the_chosen_dir_too(tmp_path: Path) -> None:
    spec = build_spec(spec_input(tmp_path))                # the CLI under the chosen ~/.codex
    (cli_root(spec) / "auth.json").write_text("{}")
    with pytest.raises(SpecRefused) as refused:
        check_credentials(spec, protected(tmp_path), tmp_path / "home")
    assert str(refused.value) == ROOT_LOGIN_NAME


def test_a_root_outside_every_login_dir_is_not_walked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    walked: list[Path] = []
    monkeypatch.setattr(spec_mod, "check_install_root", lambda root, names, files: walked.append(root))
    outside = tmp_path / "opt" / "cli"
    outside.mkdir(parents=True)
    spec = build_spec(spec_input(tmp_path))
    check_credentials(replace(spec, cli_root=outside.resolve()), protected(tmp_path), tmp_path / "home")
    assert walked == []
    check_credentials(spec, protected(tmp_path), tmp_path / "home")
    assert walked == [cli_root(spec)]


def tree(tmp_path: Path) -> tuple[Path, Path]:
    """A canonical install root with a little content, and a login file outside it."""
    root = tmp_path / "root"
    root.mkdir()
    root = root.resolve()
    (root / "bin").mkdir()
    (root / "bin" / "cli").write_text("")
    (root / "lib" / "deep").mkdir(parents=True)
    (root / "lib" / "deep" / "x.js").write_text("")
    (root / "lib" / "here").symlink_to(root / "bin" / "cli")             # a link that stays inside
    login = tmp_path / "login" / "auth.json"
    login.parent.mkdir()
    login.write_text("{}")
    return root, login


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    root, login = tree(tmp_path)
    check_install_root(root, ("auth.json",), (login, tmp_path / "missing" / "auth.json"))


def _name(root: Path, login: Path) -> None:
    (root / "lib" / "auth.json").mkdir()


def _alias(root: Path, login: Path) -> None:
    os.link(login, root / "lib" / "deep" / "innocent")


def _link_out(root: Path, login: Path) -> None:
    (root / "lib" / "out").symlink_to(login.parent)


def _dangling_out(root: Path, login: Path) -> None:
    (root / "lib" / "gone").symlink_to(root.parent / "nowhere")


def _fifo(root: Path, login: Path) -> None:
    os.mkfifo(root / "lib" / "pipe")


@pytest.mark.parametrize(("plant", "reason"), [(_name, ROOT_LOGIN_NAME), (_alias, ROOT_LOGIN_ALIAS),
                                                (_link_out, ROOT_LINK_OUT), (_dangling_out, ROOT_LINK_OUT),
                                                (_fifo, ROOT_SPECIAL)])
def test_each_way_to_reach_a_login_is_refused(tmp_path: Path, plant: Callable[[Path, Path], None],
                                              reason: str) -> None:
    root, login = tree(tmp_path)
    plant(root, login)
    with pytest.raises(SpecRefused) as refused:
        check_install_root(root, ("auth.json",), (login,))
    assert str(refused.value) == reason


def test_a_root_that_is_not_canonical_is_refused(tmp_path: Path) -> None:
    root, login = tree(tmp_path)
    (tmp_path / "alias").symlink_to(root)
    with pytest.raises(SpecRefused) as refused:
        check_install_root(tmp_path / "alias", ("auth.json",), (login,))
    assert str(refused.value) == ROOT_NOT_CANONICAL


@pytest.mark.parametrize(("bound", "value"), [("ROOT_MAX_ENTRIES", 4), ("ROOT_MAX_DEPTH", 1)])
def test_a_tree_past_a_bound_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str,
                                        value: int) -> None:
    root, login = tree(tmp_path)                           # 6 entries, 3 levels
    monkeypatch.setattr(spec_mod, bound, value)
    with pytest.raises(SpecRefused) as refused:
        check_install_root(root, ("auth.json",), (login,))
    assert str(refused.value) == ROOT_TOO_LARGE


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_an_unreadable_directory_or_login_refuses(tmp_path: Path) -> None:
    root, login = tree(tmp_path)
    locked = root / "lib" / "deep"
    locked.chmod(0)
    try:
        with pytest.raises(SpecRefused) as refused:
            check_install_root(root, ("auth.json",), (login,))
        assert str(refused.value) == ROOT_WALK_FAILED
    finally:
        locked.chmod(0o700)
    login.parent.chmod(0)
    try:
        with pytest.raises(SpecRefused) as refused:
            check_install_root(root, ("auth.json",), (login,))
        assert str(refused.value) == ROOT_WALK_FAILED
    finally:
        login.parent.chmod(0o700)


def test_the_root_texts_name_no_path() -> None:
    for text in (ROOT_NOT_CANONICAL, ROOT_LOGIN_NAME, ROOT_LOGIN_ALIAS, ROOT_LINK_OUT, ROOT_SPECIAL,
                 ROOT_TOO_LARGE, ROOT_WALK_FAILED, OTHER_ACCOUNT_DIR):
        assert "/" not in text and "auth.json" not in text
```

Also import `Callable` from `collections.abc` and `SandboxSpec` from `heterodyne.sandbox.spec`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_spec.py -q`
Expected: FAIL at import (`cannot import name 'OTHER_ACCOUNT_DIR'`).

- [ ] **Step 3: Implement**

In `src/heterodyne/sandbox/spec.py`, add `import stat` and `from collections.abc import Iterable, Mapping`, then the constants after `LAUNCHER_ENV`:

```python
# The install-root walk (r16 §7, plan 4b E12). Read at call time, so tests can lower the bounds.
ROOT_MAX_ENTRIES = 100_000
ROOT_MAX_DEPTH = 64
ROOT_NOT_CANONICAL = "the CLI install root inside a login directory is not a canonical path"
ROOT_LOGIN_NAME = "the CLI install root holds an entry named like a login file"
ROOT_LOGIN_ALIAS = "the CLI install root holds a hard link to a login file"
ROOT_LINK_OUT = "the CLI install root holds a symbolic link that leaves it"
ROOT_SPECIAL = "the CLI install root holds something other than files, directories and links"
ROOT_TOO_LARGE = "the CLI install root is too large or too deep to check"
ROOT_WALK_FAILED = "the CLI install root can't be read to check it"
OTHER_ACCOUNT_DIR = "a bind would reach into another account's login directory"
```

Add the field at the end of `SandboxSpec`:

```python
    cli_root: Path | None = None        # the CLI's install root, for the install-root walk (plan 4b E12)
```

In `build_spec`, pass it:

```python
    return SandboxSpec(name, i.worktree, binds, tuple(logins), read_only, read_write, tuple(egress), env,
                       i.uid, i.gid, cli_root=i.agent.cli_root)
```

Add `check_install_root` before `check_credentials`:

```python
def check_install_root(root: Path, login_names: Iterable[str], login_files: Iterable[Path]) -> None:
    """r16 §7: the CLI's install root holds nothing that is, names or reaches a login file. The root is
    canonical; no entry is named like a login file; no entry is a hard link to one (same device and
    inode as any of `login_files`, every configured account's); every symbolic link resolves inside the
    root; everything else is a regular file or a directory. Links are never followed by the walk. A tree
    past ROOT_MAX_ENTRIES entries or ROOT_MAX_DEPTH levels, or any error reading it, refuses."""
    if str(root) != os.path.realpath(root):
        raise SpecRefused(ROOT_NOT_CANONICAL)
    names = frozenset(login_names)
    aliases: set[tuple[int, int]] = set()
    for login in login_files:
        try:
            st = os.stat(login)
        except FileNotFoundError:
            continue                            # nothing there to alias
        except OSError:
            raise SpecRefused(ROOT_WALK_FAILED) from None
        aliases.add((st.st_dev, st.st_ino))
    entries = 0
    pending = [(root, 0)]
    try:
        while pending:
            folder, depth = pending.pop()
            with os.scandir(folder) as found:
                for entry in found:
                    entries += 1
                    if entries > ROOT_MAX_ENTRIES:
                        raise SpecRefused(ROOT_TOO_LARGE)
                    if entry.name in names:
                        raise SpecRefused(ROOT_LOGIN_NAME)
                    st = entry.stat(follow_symlinks=False)
                    if (st.st_dev, st.st_ino) in aliases:
                        raise SpecRefused(ROOT_LOGIN_ALIAS)
                    if stat.S_ISLNK(st.st_mode):
                        if not Path(os.path.realpath(entry.path)).is_relative_to(root):
                            raise SpecRefused(ROOT_LINK_OUT)
                    elif stat.S_ISDIR(st.st_mode):
                        if depth >= ROOT_MAX_DEPTH:
                            raise SpecRefused(ROOT_TOO_LARGE)
                        pending.append((Path(entry.path), depth + 1))
                    elif not stat.S_ISREG(st.st_mode):
                        raise SpecRefused(ROOT_SPECIAL)
    except OSError:
        raise SpecRefused(ROOT_WALK_FAILED) from None
```

Replace `check_credentials`:

```python
def check_credentials(spec: SandboxSpec, protected: Protected, real_home: Path) -> None:
    """D10. The login binds are exactly the chosen account's login files, read-only. No other bind's
    source, by its own path or its canonical one, equals or contains the real home, a login directory or
    a login file, or lies inside another account's login directory (r15 §7), by its own path or any the
    kernel walks through to resolve it (`_route`): a symlink out of that directory doesn't excuse a route
    into it.

    The CLI's install root may lie inside a login directory: the Codex CLI installs under the default
    login's. Then the root must first pass `check_install_root`, whichever account is chosen (r16 §7,
    plan 4b E12), and only its own bind, read-only at its canonical path, is exempt from the
    other-account rule: the same tree is bound under every account."""
    want = {_real(p) for p in protected.chosen_files}
    got = [_real(b.source) for b in spec.logins]
    if sorted(got) != sorted(want) or not all(b.read_only for b in spec.logins):
        raise SpecRefused("the login binds are not exactly the chosen account's login files, read-only")
    guarded = {q for p in (protected.chosen_dir, *protected.chosen_files, *protected.other_dirs,
                           *protected.other_files) for q in (p, _real(p))}
    other_dirs = {q for p in protected.other_dirs for q in (p, _real(p))}
    login_dirs = {q for p in (protected.chosen_dir, *protected.other_dirs) for q in (p, _real(p))}
    homes = {real_home, _real(real_home)}
    exempt: Bind | None = None
    root = spec.cli_root
    if root is not None and any(q.is_relative_to(d) for q in (root, _real(root)) for d in login_dirs):
        files = (*protected.chosen_files, *protected.other_files)
        check_install_root(root, {f.name for f in files}, files)
        exempt = Bind(root, root, True)
    for b in spec.binds:
        for source in {b.source, _real(b.source)}:
            if any(h.is_relative_to(source) for h in homes):
                raise SpecRefused("a bind would expose the real home directory")
            if any(p.is_relative_to(source) for p in guarded):
                raise SpecRefused("a bind would expose a login directory or login file")
        if b == exempt:
            continue
        if any(q.is_relative_to(d) for q in (b.source, *_route(b.source)) for d in other_dirs):
            raise SpecRefused(OTHER_ACCOUNT_DIR)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_sandbox_spec.py tests/test_sandbox_runtime.py tests/test_sandbox_openshell_policy.py tests/test_sandbox_openshell_selftest.py -q`
Expected: PASS. Plan 4's existing tests, `test_credentials_pass_with_cli_inside_the_chosen_login_dir` included, still pass: that tree is clean, so the walk passes.

Then: `timeout 300 uv run pyright src/heterodyne/sandbox/spec.py tests/test_sandbox_spec.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/sandbox/spec.py tests/test_sandbox_spec.py
git commit -m "plan4b T3: the install-root walk and the CLI bind's exemption (r16 §7)"
```
