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

## Task 4: turn ends, the event callback, and Claude's StopFailure hook

S7 finding 1: a Claude turn that ends in an API error fires `StopFailure`, not `Stop`. Plan 4's lifetime stop waits for a turn boundary, so a limited Claude turn would look like a turn that never ends. This task makes the per-session server count any of the adapter's turn-end events as a boundary, and gives the runtime a callback for every accepted event (Task 7 parses usage from it).

**Files:**
- Modify: `src/heterodyne/session/server.py` (`TurnState.boundary`, `SessionServer(turn_ends=, on_event=)`, `_record`)
- Modify: `src/heterodyne/agents/base.py` (`Adapter.turn_end_events`)
- Modify: `src/heterodyne/agents/claude_code.py` (`CLAUDE_HOOK_EVENTS`, `turn_end_events`, `run_files`)
- Modify: `src/heterodyne/agents/codex.py` (`turn_end_events`)
- Test: `tests/test_session_server.py`, `tests/test_agents_adapters.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `TurnState.boundary: bool` (default `False`); `TurnState.idle` returns it. `TurnState.last_event` keeps the real event name.
  - `SessionServer(path, token, events, *, ..., turn_ends: tuple[str, ...] = ("Stop",), on_event: Callable[[dict[str, Any]], None] | None = None)`. `on_event` gets each accepted `hook_event` payload, under the server's lock, never after `close` has returned. It must be quick and must not call back into the server.
  - `Adapter.turn_end_events: tuple[str, ...]`: `("Stop", "StopFailure")` for `ClaudeCode`, `("Stop",)` for `Codex`.
  - `claude_code.CLAUDE_HOOK_EVENTS = (*HOOK_EVENTS, "StopFailure")`.

Codex's hooks are unchanged, so the trusted hashes T14 validated stay valid. `HOOK_EVENTS` itself is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_server.py`:

```python
def test_any_configured_turn_end_is_a_boundary(tmp_path: Path) -> None:
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl", turn_ends=("Stop", "StopFailure"))
    s.start()
    try:
        sock = tmp_path / "s.sock"
        ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "UserPromptSubmit"}))
        assert not s.turns().idle
        ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "StopFailure", "error": "rate_limit"}))
        t = s.turns()
        assert (t.prompts, t.stops, t.idle, t.last_event) == (1, 1, True, "StopFailure")
        ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "UserPromptSubmit"}))
        assert not s.turns().idle
    finally:
        s.close()


def test_an_unconfigured_turn_end_is_not_a_boundary(server: SessionServer, tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "UserPromptSubmit"}))
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "StopFailure"}))
    assert not server.turns().idle and server.turns().stops == 0


def test_each_accepted_event_reaches_the_callback_and_refused_ones_never(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl", on_event=seen.append)
    s.start()
    try:
        sock = tmp_path / "s.sock"
        ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "StatusLine", "rate_limits": {}}))
        ask(sock, req("wrong", "hook_event", {"hook_event_name": "Stop"}))
        ask(sock, req(TOKEN, "ws_request", {"text": "x"}))
        ask(sock, req(TOKEN, "hook_event", [1]))
        assert seen == [{"hook_event_name": "StatusLine", "rate_limits": {}}]
    finally:
        s.close()


def test_a_failing_callback_drops_only_its_report(tmp_path: Path) -> None:
    def boom(payload: dict[str, object]) -> None:
        raise ValueError("parser bug")

    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl", on_event=boom)
    s.start()
    try:
        sock = tmp_path / "s.sock"
        assert ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "Stop"})) == '{"ok": true}'
        assert s.turns().idle                   # the turn state was recorded before the callback
    finally:
        s.close()


def test_nothing_reaches_the_callback_after_close(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl", on_event=seen.append)
    s.start()
    s.close()
    s._record({"hook_event_name": "Stop"})     # a handler still unwinding after close  # noqa: SLF001
    assert seen == [] and not s.turns().idle
```

In `tests/test_agents_adapters.py`, import `CLAUDE_HOOK_EVENTS` from `heterodyne.agents.claude_code`, and in `test_claude_home_merges_its_state_and_hooks_are_read_only` replace the last three lines with:

```python
    assert set(flag["hooks"]) == set(CLAUDE_HOOK_EVENTS) == {*HOOK_EVENTS, "StopFailure"}
    assert {e: [h["command"] for g in gs for h in g["hooks"]] for e, gs in flag["hooks"].items()} == {
        e: [PRE_TOOL_COMMAND if e == "PreToolUse" else HOOK_COMMAND] for e in CLAUDE_HOOK_EVENTS}
```

and append:

```python
def test_turn_end_events_per_adapter() -> None:
    assert ClaudeCode().turn_end_events == ("Stop", "StopFailure")
    assert Codex().turn_end_events == ("Stop",)
    assert "StopFailure" not in HOOK_EVENTS            # Codex's trusted hook set is unchanged
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_session_server.py tests/test_agents_adapters.py -q`
Expected: FAIL: `TypeError: SessionServer.__init__() got an unexpected keyword argument 'turn_ends'`, and `cannot import name 'CLAUDE_HOOK_EVENTS'`.

- [ ] **Step 3: Implement**

In `src/heterodyne/session/server.py`, update the module docstring's `TurnState` sentence to say "turn counts, the last turn event and whether it ended a turn (one of the adapter's turn-end events, D13; plan 4b E14)", and replace `TurnState`:

```python
@dataclass(frozen=True)
class TurnState:
    prompts: int = 0
    stops: int = 0                  # turn ends of any of the session's turn-end events
    last_event: str = ""
    thread_id: str | None = None
    boundary: bool = False          # the last turn event ended a turn

    @property
    def idle(self) -> bool:
        """At a turn boundary: the last turn event was one of the session's turn-end events."""
        return self.boundary
```

Add the two parameters to `SessionServer.__init__` and keep them:

```python
    def __init__(self, path: Path, token: str, events: Path, *, max_spool: int = 4 << 20,
                 max_handlers: int = MAX_HANDLERS, client_seconds: float = CLIENT_SECONDS,
                 clock: Callable[[], float] = time.monotonic, turn_ends: tuple[str, ...] = ("Stop",),
                 on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.path = path
        self.token = token.encode()
        self.events = events
        self.max_spool = max_spool
        self.client_seconds = client_seconds
        self.clock = clock
        self.turn_ends = turn_ends
        self.on_event = on_event
```

(the rest of `__init__` is unchanged). Replace `_record`:

```python
    def _record(self, payload: dict[str, Any]) -> None:
        """Turn state from the event name and session ID alone, then the runtime's callback, both under
        the lock and only while open, so nothing is recorded or reported once close has returned. A
        callback that fails drops its own report: the reply and the turn state are unaffected."""
        event = payload.get("hook_event_name")
        with self._lock:
            if self._closed.is_set():
                return
            s = self._state
            if event == "SessionStart" and s.thread_id is None:
                sid = payload.get("session_id")
                if isinstance(sid, str) and UUID.fullmatch(sid):
                    s = replace(s, thread_id=sid)
            elif event == "UserPromptSubmit":
                s = replace(s, prompts=s.prompts + 1, last_event=event, boundary=False)
            elif isinstance(event, str) and event in self.turn_ends:
                s = replace(s, stops=s.stops + 1, last_event=event, boundary=True)
            self._state = s
            if self.on_event is not None:
                try:
                    self.on_event(payload)
                except Exception:  # noqa: BLE001, S110 - an untrusted report is dropped, never retried
                    pass
```

In `src/heterodyne/agents/base.py`, add to the `Adapter` protocol, beside `assigns_id`:

```python
    turn_end_events: tuple[str, ...]    # hook events that end a turn (plan 4b E14)
```

In `src/heterodyne/agents/claude_code.py`, after `SETTINGS_FILE`:

```python
# Claude Code 2.1.286 ends a turn that fails on an API error, a usage limit included, with StopFailure
# instead of Stop (S7 finding 1): it must reach the session socket, or the turn never ends to wsd.
CLAUDE_HOOK_EVENTS = (*HOOK_EVENTS, "StopFailure")
```

and in `ClaudeCode`:

```python
    turn_end_events: tuple[str, ...] = ("Stop", "StopFailure")
```

```python
    def run_files(self) -> Mapping[str, str]:
        hooks = {e: [hook_group(e, "*" if e == "PreToolUse" else None)] for e in CLAUDE_HOOK_EVENTS}
        return {SETTINGS_FILE: json.dumps({"skipDangerousModePermissionPrompt": True, "hooks": hooks})}
```

In `src/heterodyne/agents/codex.py`, in `Codex`:

```python
    turn_end_events: tuple[str, ...] = ("Stop",)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_session_server.py tests/test_agents_adapters.py tests/test_sandbox_runtime.py -q`
Expected: PASS. `test_sandbox_runtime.py` still passes: the runtime still builds its servers with the default `turn_ends` until Task 7.

Then: `timeout 300 uv run pyright src/heterodyne/session src/heterodyne/agents tests/test_session_server.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/session/server.py src/heterodyne/agents/base.py src/heterodyne/agents/claude_code.py \
        src/heterodyne/agents/codex.py tests/test_session_server.py tests/test_agents_adapters.py
git commit -m "plan4b T4: adapter turn-end events, the event callback and Claude's StopFailure hook"
```

## Task 5: usage parsing in the adapters, the shim's status mode, and the in-sandbox Codex read

The producers' adapter-specific half: what each CLI's payload means, parsed into `Observation`s that `wsd` validates again on ingestion. Nothing here runs in a launch yet: Task 7 wires it, and only for a profile whose capabilities enable (c) or (e).

**Files:**
- Modify: `src/heterodyne/agents/base.py` (`STATUS_COMMAND`, `UsageReport`, the `Adapter` methods)
- Modify: `src/heterodyne/agents/claude_code.py` (`claude_windows`, `usage_from_event`, `usage_argv`, `usage_from_read`, `run_files(usage=)`)
- Modify: `src/heterodyne/agents/codex.py` (`parse_rate_limits`, `USAGE_SCRIPT`, `USAGE_SOURCE`, the same methods)
- Create: `src/heterodyne/sandbox/resources/codex_usage.py`
- Modify: `src/heterodyne/session/shim.py` (`run_status`, `status_main`, `main`)
- Modify: `tests/fakes/fake_agent_cli.py` (`account/rateLimits/read`, faults; `hooks/list` reads its config lazily)
- Test: `tests/test_agents_usage.py` (new), `tests/test_session_shim.py`

**Interfaces:**
- Consumes: `wsd.usage.Observation` (exists), `session.shim.json_depth`, `MAX_DEPTH`, `MAX_LINE` (exist).
- Produces:
  - `agents.base.STATUS_COMMAND = "python3 -I /run/hz/shim.py status"`.
  - `agents.base.UsageReport(windows: tuple[Observation, ...] = (), limited: bool = False, reset_hint: int | None = None)`.
  - On the `Adapter` protocol:
    - `usage_from_event(payload: Mapping[str, Any]) -> UsageReport | None`: a session-socket hook payload;
    - `usage_argv(cli: Cli) -> list[str] | None`: the in-sandbox read's command, or None if the adapter has none;
    - `usage_from_read(output: bytes) -> UsageReport | None`: that command's stdout;
    - `run_files(*, usage: bool = False) -> Mapping[str, str]`: with `usage`, the files the producers need.
  - `codex.parse_rate_limits(data: object, source: str) -> UsageReport | None`, shared with Task 8.
  - `codex.IN_SESSION_SOURCE = "codex.app-server"`, `codex.HOST_SOURCE = "codex.host-read"`, `claude_code.STATUS_SOURCE = "claude.statusline"`.
  - The shim's `status` mode: `python3 -I /run/hz/shim.py status` reads Claude's status-line JSON on stdin and sends `{"hook_event_name": "StatusLine", "session_id", "rate_limits"}` once per change. It prints nothing and exits 0.
  - `codex_usage.py`: `python3 -I codex_usage.py <codex binary> [limit seconds]` prints the `rateLimits` object and exits 0, or exits 1 with nothing on stdout.
  - The fake app-server answers `account/rateLimits/read` from `HZ_FAKE_RATE_LIMITS`; `HZ_FAKE_RATE_FAULT` is `hang`, `error` or `garbage`.

The windows' identities: Claude's are `claude:5h` and `claude:7d`. A Codex window is `codex:<limitId>:<minutes>m`, kind `5h` / `7d` / `<n>d` / `<n>h` / `<n>m` from its duration. They match `usage.LABEL` and never name a slot (`primary`, `five_hour`), as `Observation` requires. Percentages and resets pass through unchecked: `usage._check` validates them, so the parsers never decide a value is safe.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents_usage.py`:

```python
"""Plan 4b Task 5: what the CLIs' usage payloads mean (S7 (3), (5)), and the in-sandbox Codex read."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sandbox_env import install_fake_cli

from heterodyne.agents.base import STATUS_COMMAND, Cli, UsageReport
from heterodyne.agents.claude_code import SETTINGS_FILE, STATUS_SOURCE, ClaudeCode
from heterodyne.agents.codex import HOST_SOURCE, IN_SESSION_SOURCE, USAGE_SCRIPT, Codex, parse_rate_limits
from heterodyne.wsd.usage import LABEL, Observation

RESOURCES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources"
USAGE = RESOURCES / "codex_usage.py"
NOW = 1_800_000_000
SNAPSHOT = {"limitId": "codex",
            "primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": NOW + 3600},
            "secondary": {"usedPercent": 17, "windowDurationMins": 10080, "resetsAt": NOW + 86400},
            "planType": "pro", "rateLimitReachedType": None}


# --- Claude ---

def test_a_status_line_gives_both_windows() -> None:
    report = ClaudeCode().usage_from_event({
        "hook_event_name": "StatusLine", "session_id": "s",
        "rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": NOW + 3600},
                        "seven_day": {"used_percentage": 17, "resets_at": NOW + 86400}}})
    assert report == UsageReport((Observation("claude:5h", "5h", 42, NOW + 3600, STATUS_SOURCE),
                                  Observation("claude:7d", "7d", 17, NOW + 86400, STATUS_SOURCE)))


@pytest.mark.parametrize("limits", [None, [], {}, {"five_hour": "full"}, {"other": {"used_percentage": 1}}])
def test_a_status_line_without_windows_reports_nothing(limits: object) -> None:
    assert ClaudeCode().usage_from_event({"hook_event_name": "StatusLine", "rate_limits": limits}) is None


@pytest.mark.parametrize(("resets", "kept"), [(NOW + 5, NOW + 5), (float(NOW + 5), NOW + 5), (True, None),
                                              ("soon", None), (None, None), (float("nan"), None)])
def test_a_reset_that_is_not_a_number_is_unknown(resets: object, kept: int | None) -> None:
    report = ClaudeCode().usage_from_event({
        "hook_event_name": "StatusLine", "rate_limits": {"five_hour": {"used_percentage": 1, "resets_at": resets}}})
    assert report is not None and report.windows[0].resets_at == kept


def test_a_percentage_is_passed_on_for_ingestion_to_judge() -> None:
    report = ClaudeCode().usage_from_event({
        "hook_event_name": "StatusLine", "rate_limits": {"five_hour": {"used_percentage": "lots"}}})
    assert report is not None and report.windows[0].used_percent == "lots"


def test_only_a_rate_limit_stop_failure_is_a_limit() -> None:
    c = ClaudeCode()
    assert c.usage_from_event({"hook_event_name": "StopFailure", "error": "rate_limit",
                               "last_assistant_message": "resets 12:10pm"}) == UsageReport(limited=True)
    for error in ("overloaded", "authentication_failed", "billing_error", None, 7):
        assert c.usage_from_event({"hook_event_name": "StopFailure", "error": error}) is None
    assert c.usage_from_event({"hook_event_name": "Stop", "error": "rate_limit"}) is None


def test_claude_has_no_in_sandbox_read() -> None:
    cli = Cli(Path("/opt/claude/bin/claude"), Path("/opt/claude"), "2.1.0")
    assert ClaudeCode().usage_argv(cli) is None
    assert ClaudeCode().usage_from_read(b"{}") is None


def test_the_status_line_is_installed_only_for_usage() -> None:
    plain = json.loads(ClaudeCode().run_files()[SETTINGS_FILE])
    assert "statusLine" not in plain
    usage = json.loads(ClaudeCode().run_files(usage=True)[SETTINGS_FILE])
    assert usage["statusLine"] == {"type": "command", "command": STATUS_COMMAND}
    assert usage["hooks"] == plain["hooks"]


# --- Codex ---

def test_a_snapshot_gives_one_observation_per_window() -> None:
    report = parse_rate_limits(SNAPSHOT, IN_SESSION_SOURCE)
    assert report == UsageReport((Observation("codex:codex:300m", "5h", 42, NOW + 3600, IN_SESSION_SOURCE),
                                  Observation("codex:codex:10080m", "7d", 17, NOW + 86400, IN_SESSION_SOURCE)))
    assert all(LABEL.fullmatch(o.window_id) and LABEL.fullmatch(o.kind) for o in report.windows)


def test_a_reached_limit_hints_the_latest_full_windows_reset() -> None:
    snap = {**SNAPSHOT, "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": NOW + 3600},
            "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": NOW + 86400}}
    report = parse_rate_limits(snap, HOST_SOURCE)
    assert report is not None and report.limited and report.reset_hint == NOW + 86400
    partly = parse_rate_limits({**snap, "secondary": SNAPSHOT["secondary"]}, HOST_SOURCE)
    assert partly is not None and (partly.limited, partly.reset_hint) == (True, NOW + 3600)
    nothing_full = parse_rate_limits({**SNAPSHOT, "rateLimitReachedType": "workspace_owner_credits_depleted"},
                                     HOST_SOURCE)
    assert nothing_full is not None and (nothing_full.limited, nothing_full.reset_hint) == (True, None)


@pytest.mark.parametrize("window", [None, "x", {"usedPercent": 1}, {"usedPercent": 1, "windowDurationMins": 0},
                                    {"usedPercent": 1, "windowDurationMins": True},
                                    {"usedPercent": 1, "windowDurationMins": "300"}])
def test_a_window_without_a_duration_is_skipped(window: object) -> None:
    report = parse_rate_limits({"limitId": "codex", "primary": window, "rateLimitReachedType": None}, HOST_SOURCE)
    assert report == UsageReport()


@pytest.mark.parametrize("data", [None, [], "x", 1])
def test_a_snapshot_that_is_not_an_object_is_nothing(data: object) -> None:
    assert parse_rate_limits(data, HOST_SOURCE) is None


@pytest.mark.parametrize("output", [b"", b"not json", b"[" * 200 + b"]" * 200, b"{" + b" " * 70_000 + b"}"])
def test_read_output_that_cannot_be_trusted_to_parse_is_nothing(output: bytes) -> None:
    assert Codex().usage_from_read(output) is None


def test_codex_hooks_carry_no_usage() -> None:
    assert Codex().usage_from_event({"hook_event_name": "Stop", "rate_limits": {}}) is None


def test_the_usage_script_ships_only_for_usage() -> None:
    assert USAGE_SCRIPT.name not in Codex().run_files()
    assert Codex().run_files(usage=True)[USAGE_SCRIPT.name] == USAGE.read_text()


# --- codex_usage.py, run against the fake app-server ---

def run_usage(tmp_path: Path, limit: str = "30", **env: str) -> subprocess.CompletedProcess[bytes]:
    codex = install_fake_cli(tmp_path, "codex")
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    scratch = tmp_path / "tmp"
    scratch.mkdir(exist_ok=True)
    full = {"PATH": os.environ["PATH"], "CODEX_HOME": str(home), "TMPDIR": str(scratch), **env}
    return subprocess.run([sys.executable, "-I", str(USAGE), str(codex), limit], env=full,
                          capture_output=True, timeout=60, check=False)


def test_the_read_prints_the_snapshot_and_leaves_no_scratch_home(tmp_path: Path) -> None:
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "auth.json").write_text("fake-not-a-token")
    proc = run_usage(tmp_path, HZ_FAKE_RATE_LIMITS=json.dumps(SNAPSHOT))
    assert proc.returncode == 0, proc.stderr
    assert Codex().usage_from_read(proc.stdout) == parse_rate_limits(SNAPSHOT, IN_SESSION_SOURCE)
    assert list((tmp_path / "tmp").iterdir()) == []
    assert (tmp_path / "codex-home" / "auth.json").read_text() == "fake-not-a-token"


@pytest.mark.parametrize("fault", ["error", "garbage", "hang", "no-login"])
def test_a_failed_read_exits_1_with_nothing_on_stdout(tmp_path: Path, fault: str) -> None:
    (tmp_path / "codex-home").mkdir()
    if fault != "no-login":
        (tmp_path / "codex-home" / "auth.json").write_text("fake-not-a-token")
    env = {"HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT)}
    if fault != "no-login":
        env["HZ_FAKE_RATE_FAULT"] = fault
    proc = run_usage(tmp_path, limit="2", **env)
    assert proc.returncode == 1 and proc.stdout == b""
    assert proc.stderr.decode().strip() in {"codex_usage: RuntimeError", "codex_usage: JSONDecodeError"}
    assert list((tmp_path / "tmp").iterdir()) == []


def test_a_missing_codex_is_not_a_clean_failure(tmp_path: Path) -> None:
    (tmp_path / "codex-home").mkdir()
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    proc = subprocess.run([sys.executable, "-I", str(USAGE), str(tmp_path / "nowhere" / "codex")],
                          env={"PATH": os.environ["PATH"], "CODEX_HOME": str(tmp_path / "codex-home"),
                               "TMPDIR": str(scratch)}, capture_output=True, timeout=60, check=False)
    assert proc.returncode == 1 and proc.stdout == b""
    assert proc.stderr.decode().strip() == "codex_usage: FileNotFoundError"
    assert list(scratch.iterdir()) == []
```

Append to `tests/test_session_shim.py`:

```python
# --- the status mode (plan 4b E7) ---

LIMITS = {"five_hour": {"used_percentage": 42, "resets_at": 1_800_003_600}}


def status(run: Path, payload: object, t: FakeTime | None = None) -> int:
    t = t or FakeTime()
    return shim.run_status(json.dumps(payload).encode(), env_for(run), clock=t.clock, sleep=t.sleep)


def test_status_sends_each_change_once(run: Path) -> None:
    seen: list[dict[str, object]] = []
    s = SessionServer(run / "s.sock", TOKEN, run.parent / "events.jsonl", on_event=seen.append)
    s.start()
    try:
        assert status(run, {"session_id": "s1"}) == 0                    # the first call has no rate_limits
        assert status(run, {"session_id": "s1", "rate_limits": LIMITS}) == 0
        assert status(run, {"session_id": "s1", "rate_limits": LIMITS}) == 0
        changed = {"five_hour": {"used_percentage": 43, "resets_at": 1_800_003_600}}
        assert status(run, {"session_id": "s1", "rate_limits": changed}) == 0
        assert seen == [{"hook_event_name": "StatusLine", "session_id": "s1", "rate_limits": LIMITS},
                        {"hook_event_name": "StatusLine", "session_id": "s1", "rate_limits": changed}]
    finally:
        s.close()


def test_status_retries_a_change_wsd_never_answered(run: Path) -> None:
    t = FakeTime()
    assert status(run, {"rate_limits": LIMITS}, t) == 0                   # nothing listening
    assert t.now <= shim.STATUS_WAIT
    seen: list[dict[str, object]] = []
    s = SessionServer(run / "s.sock", TOKEN, run.parent / "events.jsonl", on_event=seen.append)
    s.start()
    try:
        assert status(run, {"rate_limits": LIMITS}) == 0
        assert [e["rate_limits"] for e in seen] == [LIMITS]
    finally:
        s.close()


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1]", b"[" * 200 + b"]" * 200])
def test_status_ignores_input_it_cannot_read(run: Path, live: SessionServer, raw: bytes) -> None:
    assert shim.run_status(raw, env_for(run), clock=FakeTime().clock, sleep=FakeTime().sleep) == 0
    assert live.served == 0


def test_status_never_follows_a_planted_link(run: Path, live: SessionServer, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    hz = run.parent / "home" / ".hz"
    hz.mkdir(mode=0o700)
    (hz / shim.STATUS_FILE).symlink_to(victim)
    assert status(run, {"rate_limits": LIMITS}) == 0
    assert victim.read_text() == "keep"


def test_status_runs_standalone_and_prints_nothing(run: Path, live: SessionServer) -> None:
    proc = subprocess.run([sys.executable, "-I", shim.__file__, "status"],
                          input=json.dumps({"rate_limits": LIMITS}).encode(), env=env_for(run),
                          capture_output=True, timeout=30, check=False)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert live.served == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_agents_usage.py tests/test_session_shim.py -q`
Expected: FAIL at import (`cannot import name 'STATUS_COMMAND'`), and `AttributeError: module 'heterodyne.session.shim' has no attribute 'run_status'`.

- [ ] **Step 3: Implement**

In `src/heterodyne/agents/base.py`, import `from heterodyne.wsd.usage import Observation`, and add after `HOOK_EVENTS`:

```python
# Claude's status line, when (c) is on (plan 4b E7): fixed like the hooks, and reading nothing from argv.
STATUS_COMMAND = "python3 -I /run/hz/shim.py status"
```

after `Cli`:

```python
@dataclass(frozen=True)
class UsageReport:
    """What one usage payload says, parsed by its adapter. Untrusted until wsd says otherwise; the account
    is never read from it. Percentages and resets are passed on unchecked: ingestion validates them."""
    windows: tuple[Observation, ...] = ()
    limited: bool = False               # the payload says a usage limit is reached (D9 (e))
    reset_hint: int | None = None       # the latest reset of a reached window, if the payload gave one


def epoch_or_none(value: object) -> int | None:
    """A payload's epoch-seconds time, or None if it is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value == value and abs(value) < 1e15 else None    # NaN != NaN
```

and on the `Adapter` protocol (after `apply_trust`), with `run_files` replaced:

```python
    def run_files(self, *, usage: bool = False) -> Mapping[str, str]:
        """Files for the run directory (read-only inside). With `usage`, also what the profile's usage
        producers need (plan 4b E5, E7)."""
        ...

    def usage_from_event(self, payload: Mapping[str, Any]) -> UsageReport | None:
        """What a session-socket hook payload says about usage, or None if nothing."""
        ...

    def usage_argv(self, cli: Cli) -> list[str] | None:
        """The in-sandbox usage read (plan 4b E5), or None if the adapter has none."""
        ...

    def usage_from_read(self, output: bytes) -> UsageReport | None:
        """The in-sandbox read's stdout, or None if it doesn't parse."""
        ...
```

In `src/heterodyne/agents/claude_code.py`, import `STATUS_COMMAND`, `UsageReport` and `epoch_or_none` from `heterodyne.agents.base` and `Observation` from `heterodyne.wsd.usage`, then add after `CLAUDE_HOOK_EVENTS`:

```python
STATUS_SOURCE = "claude.statusline"
# The status line's windows (S7 (3)): its key, and the window's identity and label.
CLAUDE_WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))


def claude_windows(limits: Mapping[str, Any]) -> tuple[Observation, ...]:
    """The status line's `rate_limits`: one observation per window it has, its values unchecked."""
    found: list[Observation] = []
    for key, kind in CLAUDE_WINDOWS:
        window: Any = limits.get(key)
        if isinstance(window, dict):
            w = cast(dict[str, Any], window)
            found.append(Observation(f"claude:{kind}", kind, w.get("used_percentage"),
                                     epoch_or_none(w.get("resets_at")), STATUS_SOURCE))
    return tuple(found)
```

In `ClaudeCode`, replace `run_files` and add the usage methods:

```python
    def run_files(self, *, usage: bool = False) -> Mapping[str, str]:
        hooks = {e: [hook_group(e, "*" if e == "PreToolUse" else None)] for e in CLAUDE_HOOK_EVENTS}
        settings: dict[str, Any] = {"skipDangerousModePermissionPrompt": True, "hooks": hooks}
        if usage:
            settings["statusLine"] = {"type": "command", "command": STATUS_COMMAND}
        return {SETTINGS_FILE: json.dumps(settings)}

    def usage_from_event(self, payload: Mapping[str, Any]) -> UsageReport | None:
        """S7 (5): a StopFailure with `error == "rate_limit"` is the limit signal; its reset is only in
        message text, which is never parsed. S7 (3): the status line's `rate_limits`."""
        event = payload.get("hook_event_name")
        if event == "StopFailure":
            return UsageReport(limited=True) if payload.get("error") == "rate_limit" else None
        limits: Any = payload.get("rate_limits")
        if event != "StatusLine" or not isinstance(limits, dict):
            return None
        windows = claude_windows(cast(dict[str, Any], limits))
        return UsageReport(windows) if windows else None

    def usage_argv(self, cli: Cli) -> list[str] | None:
        return None

    def usage_from_read(self, output: bytes) -> UsageReport | None:
        return None
```

In `src/heterodyne/agents/codex.py`, import `UsageReport` and `epoch_or_none` from `heterodyne.agents.base`, `Observation` from `heterodyne.wsd.usage`, and `MAX_DEPTH`, `MAX_LINE`, `json_depth` from `heterodyne.session.shim`; add after `TRUST_SOURCE`:

```python
USAGE_SCRIPT = RUN_INSIDE / "codex_usage.py"
USAGE_SOURCE = Path(__file__).resolve().parents[1] / "sandbox" / "resources" / "codex_usage.py"
IN_SESSION_SOURCE = "codex.app-server"
HOST_SOURCE = "codex.host-read"


def _duration(minutes: int) -> str:
    for unit, size in (("d", 1440), ("h", 60)):
        if minutes % size == 0:
            return f"{minutes // size}{unit}"
    return f"{minutes}m"


def parse_rate_limits(data: object, source: str) -> UsageReport | None:
    """A `RateLimitSnapshot` (codex-cli 0.160 `account/rateLimits/read`, S7 (3)): one observation per
    window with a positive integer duration, identified by its limit ID and duration, never its slot. A
    `rateLimitReachedType` other than null is the limit signal (plan 4b E6); its hint is the latest reset
    of a window at 100% or more. None if `data` is not an object."""
    if not isinstance(data, dict):
        return None
    snap = cast(dict[str, Any], data)
    limit: Any = snap.get("limitId")
    limit_id = limit if isinstance(limit, str) else "default"
    windows: list[Observation] = []
    for slot in ("primary", "secondary"):
        window: Any = snap.get(slot)
        if not isinstance(window, dict):
            continue
        w = cast(dict[str, Any], window)
        minutes: Any = w.get("windowDurationMins")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
            continue
        windows.append(Observation(f"codex:{limit_id}:{minutes}m", _duration(minutes), w.get("usedPercent"),
                                   epoch_or_none(w.get("resetsAt")), source))
    limited = snap.get("rateLimitReachedType") is not None
    full = [o.resets_at for o in windows if o.resets_at is not None and not isinstance(o.used_percent, bool)
            and isinstance(o.used_percent, int | float) and o.used_percent >= 100]
    return UsageReport(tuple(windows), limited, max(full) if limited and full else None)
```

In `Codex`, replace `run_files` and add the usage methods:

```python
    def run_files(self, *, usage: bool = False) -> Mapping[str, str]:
        files = {TRUST_SCRIPT.name: TRUST_SOURCE.read_text()}
        if usage:
            files[USAGE_SCRIPT.name] = USAGE_SOURCE.read_text()
        return files

    def usage_from_event(self, payload: Mapping[str, Any]) -> UsageReport | None:
        return None                     # codex 0.160's hooks carry no usage (S7 (5))

    def usage_argv(self, cli: Cli) -> list[str] | None:
        return ["python3", "-I", str(USAGE_SCRIPT), str(cli.binary)]

    def usage_from_read(self, output: bytes) -> UsageReport | None:
        if len(output) > MAX_LINE or json_depth(output) > MAX_DEPTH:
            return None
        try:
            data: Any = json.loads(output)
        except ValueError:
            return None
        return parse_rate_limits(data, IN_SESSION_SOURCE)
```

Create `src/heterodyne/sandbox/resources/codex_usage.py`:

```python
# pyright: basic
"""Inside a Codex sandbox while its session runs (plan 4b E5): ask a second `codex app-server`, over
stdio, for the account's rate limits. It runs in a scratch CODEX_HOME holding only a link to the
session's read-only login, so it never touches the live app-server's state, and a refresh it attempts
fails on the read-only bind (§7). No model request is made. Prints the `rateLimits` object as JSON and
exits 0. Any failure exits 1 with nothing on stdout and only the error's type on stderr. The scratch
home is removed whatever happens. The host parses and validates the output. Standard library only.

Usage: python3 -I codex_usage.py <codex binary> [limit seconds]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

LIMIT_SECONDS = 30.0


def main(argv):
    codex = argv[0]
    limit = float(argv[1]) if len(argv) > 1 else LIMIT_SECONDS
    home = tempfile.mkdtemp(prefix="hz-usage-")
    try:
        os.symlink(os.path.join(os.environ["CODEX_HOME"], "auth.json"), os.path.join(home, "auth.json"))
        limits = read(codex, home, limit)
    finally:
        shutil.rmtree(home)
    if not isinstance(limits, dict):
        raise RuntimeError("the answer has no rate limits")
    print(json.dumps(limits))
    return 0


def read(codex, home, limit):
    proc = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=dict(os.environ, CODEX_HOME=home))
    stdin, stdout = proc.stdin, proc.stdout
    if stdin is None or stdout is None:
        raise RuntimeError("codex app-server has no pipes")
    timer = threading.Timer(limit, proc.kill)
    timer.start()

    def send(message):
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()

    def call(id_, method, params):
        send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        while True:
            line = stdout.readline()
            if not line:
                raise RuntimeError("codex app-server closed its output")
            message = json.loads(line)
            if message.get("id") == id_:
                if "error" in message:
                    raise RuntimeError("codex app-server returned an error")
                return message["result"]

    try:
        call(0, "initialize", {"clientInfo": {"name": "heterodyne", "version": "1"}})
        send({"jsonrpc": "2.0", "method": "initialized"})
        result = call(1, "account/rateLimits/read", None)
    finally:
        timer.cancel()
        proc.kill()
        proc.wait(5)
    return result.get("rateLimits") if isinstance(result, dict) else None


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure is "no reading", decided on the host
        print(f"codex_usage: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
```

In `src/heterodyne/session/shim.py`, update the module docstring's first paragraph to name `hook|request|status`, and add before `json_depth`:

```python
STATUS_FILE = "status.json"     # in $HOME/.hz: the last rate limits wsd accepted
STATUS_WAIT = 2.0               # the status line redraws often: never hold it longer than this


def _status_dir(env: Mapping[str, str]) -> int | None:
    """$HOME/.hz, opened without following a link; None if $HOME isn't absolute."""
    home = env.get("HOME")
    if not home or not Path(home).is_absolute():
        return None
    folder = Path(home) / ".hz"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _last_status(env: Mapping[str, str]) -> str | None:
    try:
        dir_fd = _status_dir(env)
        if dir_fd is None:
            return None
        try:
            fd = os.open(STATUS_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        try:
            return os.read(fd, MAX_LINE + 1).decode() if stat.S_ISREG(os.fstat(fd).st_mode) else None
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return None


def _save_status(env: Mapping[str, str], text: str) -> None:
    try:
        dir_fd = _status_dir(env)
        if dir_fd is None:
            return
        try:
            fd = os.open(STATUS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.write(fd, text.encode())
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass


def run_status(raw: bytes, env: Mapping[str, str], *, clock: Clock = time.monotonic,
               sleep: Sleep = time.sleep) -> int:
    """Claude's status line (plan 4b E7): send the input's `rate_limits` as a StatusLine hook event,
    once per change, and print nothing. A change wsd didn't accept is sent again on the next redraw.
    Always exits 0: the status line decides nothing."""
    try:
        payload: Any = json.loads(raw) if len(raw) <= MAX_LINE and json_depth(raw) <= MAX_DEPTH else None
        if not isinstance(payload, dict):
            return 0
        fields = cast(dict[str, Any], payload)
        limits: Any = fields.get("rate_limits")
        if not isinstance(limits, dict):
            return 0                    # the first call, before any API response, has none (S7)
        text = json.dumps(limits, sort_keys=True)
        if _last_status(env) == text:
            return 0
        found = _config(env)
        if found is None:
            return 0
        sock, token, cfg = found
        event = {"hook_event_name": "StatusLine", "session_id": fields.get("session_id"), "rate_limits": limits}
        message = _bounded(lambda p: {"token": token, "type": "hook_event", "payload": p}, event)
        if message is None:
            return 0
        reply = _ask(sock, message, min(_wait(cfg), STATUS_WAIT), clock, sleep)
        if reply is not None and reply.get("ok") is True:
            _save_status(env, text)
    except Exception:  # noqa: BLE001, S110 - a status line never fails the session
        pass
    return 0
```

and replace `main`, adding `status_main`:

```python
def status_main() -> int:
    return run_status(sys.stdin.buffer.read(), os.environ)


def main(argv: Sequence[str]) -> int:
    if argv[:1] == ["hook"]:
        return hook_main()
    if argv[:1] == ["request"]:
        return request_main(argv[1:])
    if argv[:1] == ["status"]:
        return status_main()
    print("usage: shim.py hook|request|status [text...]", file=sys.stderr)
    return 2
```

In `tests/fakes/fake_agent_cli.py`, add to the module docstring's `codex app-server` (stdio) item: "and `account/rateLimits/read` with `HZ_FAKE_RATE_LIMITS` (a snapshot), after an `account/rateLimits/updated` notification; with no `auth.json` in `$CODEX_HOME` it answers -32600, as codex 0.160 does with no login. `HZ_FAKE_RATE_FAULT` is `hang` (never answers), `error` or `garbage` (a line that isn't JSON)." Then replace `stdio_server`:

```python
def rate_limits(msg: dict[str, Any]) -> str | None:
    """The answer line to account/rateLimits/read, or None to never answer."""
    fault = os.environ.get("HZ_FAKE_RATE_FAULT")
    if fault == "hang":
        return None
    if fault == "garbage":
        return "not json"
    if fault == "error" or not (Path(os.environ["CODEX_HOME"]) / "auth.json").is_file():
        return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {
            "code": -32600, "message": "codex account authentication required to read rate limits"}})
    snapshot = json.loads(os.environ.get("HZ_FAKE_RATE_LIMITS", "null"))
    return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"rateLimits": snapshot}})


def stdio_server() -> None:
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            result: Any = {"userAgent": "fake"}
        elif msg.get("method") == "hooks/list":
            trusted = codex_trusted()
            listed = []
            for key, event, command in codex_hooks():
                have = trusted.get(key, {}).get("trusted_hash")
                status = "trusted" if have == digest(command) else "modified" if have else "untrusted"
                listed.append({"key": key, "eventName": camel(event), "command": command, "enabled": True,
                               "trustStatus": status, "currentHash": digest(command), "source": "user"})
            result = {"data": [{"cwd": msg["params"]["cwds"][0], "hooks": listed, "warnings": [],
                                "errors": []}]}
            if fault := os.environ.get("HZ_FAKE_HOOKS_FAULT"):
                result = faulty(result, fault)
        elif msg.get("method") == "account/rateLimits/read":
            print(json.dumps({"jsonrpc": "2.0", "method": "account/rateLimits/updated", "params": {}}),
                  flush=True)
            answer = rate_limits(msg)
            if answer is None:
                sys.stdin.read()        # hang until killed
                return
            print(answer, flush=True)
            continue
        else:
            continue                    # a notification
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_agents_usage.py tests/test_session_shim.py tests/test_agents_adapters.py tests/test_sandbox_runtime.py -q`
Expected: PASS. The `hang` case passes in about 2 seconds (its limit), never 60: a timeout of the test's own `subprocess.run` is a failure. `test_a_missing_codex_is_not_a_clean_failure` proves a missing executable exits with its own error, never with something that reads as an answer.

Then: `timeout 300 uv run pyright src/heterodyne/agents src/heterodyne/session tests/test_agents_usage.py tests/test_session_shim.py` and `uv run ruff check src tests`.
Expected: 0 errors. `codex_usage.py` is `# pyright: basic`, like `codex_trust.py`.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/agents/base.py src/heterodyne/agents/claude_code.py src/heterodyne/agents/codex.py \
        src/heterodyne/sandbox/resources/codex_usage.py src/heterodyne/session/shim.py \
        tests/fakes/fake_agent_cli.py tests/test_agents_usage.py tests/test_session_shim.py
git commit -m "plan4b T5: usage parsing per adapter, the status-line shim and the in-sandbox Codex read"
```

## Task 6: named-account binding, the key check and the locked freshness gate

AU-6 proper: the runtime launches on the account the entry pinned, never only `default`. Under the D8 lock it checks that the account's key is still the pinned one, then rereads the login for the freshness gate (E1, E3). The other accounts, `default` included, are protected and probed (E13). A handoff relaunch starts fresh with D7's ID (E10).

**Files:**
- Modify: `src/heterodyne/sandbox/runtime.py` (`launch`, `_resume`, `_logins`, `_fresh`, new `_pinned`; `RuntimeConfig.credlocks`)
- Modify: `src/heterodyne/sandbox/settings.py` (`lock_wait_seconds`), `src/heterodyne/defaults/defaults.toml`
- Modify: `src/heterodyne/sandbox/build.py` (`credlocks=s.credlock_dir`)
- Modify: `src/heterodyne/wsd/park.py` (`_pin_and_dispatch` passes `credential_key`; one line)
- Modify: `tests/sandbox_env.py` (`write_login`, `with_named`, `launch_spec` pins the key, the rig's `credlocks`), `tests/fakes/scripted_selftest.py` (records `ctx.others`), `tests/live/test_live_sandbox.py` (the key and `credlocks`)
- Test: `tests/test_sandbox_runtime.py`, `tests/test_sandbox_settings.py`, `tests/test_wsd_pickup.py`

**Interfaces:**
- Consumes:
  - `wsd.runtime.LOGIN_STALE`, `KEY_CHANGED`, `LaunchSpec.credential_key`, `LaunchSpec.handoff` (Task 1).
  - `wsd.ids.handoff_session(session_key, generation)` (Task 1).
  - `wsd.credlock.credential_lock(lock_dir, key, wait_seconds, *, clock, sleep)`, `CredentialBusy` (Task 2).
  - `WsdSettings.credlock_dir` (Task 2).
  - `SandboxSpec.cli_root` and the walk inside `check_credentials` (Task 3): it now runs for named accounts too, with no change here.
- Produces:
  - `RuntimeConfig.credlocks: Path`, a required field placed after `sessions`. Every construction uses keywords.
  - `SandboxSettings.lock_wait_seconds: int`, from `[sandbox] lock_wait_seconds` (1 to 600, default 60).
  - Refusals:
    - an empty or changed key gives `LaunchFailed(KEY_CHANGED)`;
    - a stale login gives `LaunchFailed(LOGIN_STALE)`, the exact text Task 10 matches;
    - a busy or unopenable lock gives `LaunchFailed(LOCK_BUSY)` or `LaunchFailed(LOCK_FAILED)`.
  - `SandboxRuntime._logins(adapter, account)`, which Task 7 reuses unchanged.

A busy lock refuses the launch, as E2 says. It never holds the workstream: the lock's holders are short (Task 8's read, AU-10's setup), and a refused launch is retried by the next pickup. The lock covers the key check and the login read only. The bind is of the file itself, and nothing in the launch writes it.

- [ ] **Step 1: Write the failing tests**

In `tests/sandbox_env.py`, add the imports `from dataclasses import replace`, `from heterodyne.config import paths`, `from heterodyne.config.accounts import Account, canonical_login, credential_key`, `from heterodyne.wsd.accounts import ConfiguredAccounts`, and `ADAPTERS` from `wsd_env`. Then replace `fresh_logins` and `launch_spec`, add `write_login` and `with_named`, and give the rig's `RuntimeConfig` `credlocks=root / "k"`:

```python
def write_login(folder: Path, adapter: str, expires_at: int) -> None:
    """A fake login for `adapter` in `folder` that expires at `expires_at` (epoch seconds). No credential."""
    folder.mkdir(parents=True, exist_ok=True)
    if adapter == "claude-code":
        (folder / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"expiresAt": expires_at * 1000, "accessToken": "fake-not-a-token"}}))
    else:
        (folder / "auth.json").write_text(json.dumps({"tokens": {"access_token": fake_jwt(expires_at)}}))


def fresh_logins(home: Path, expires_at: int) -> None:
    """Fake default logins that expire at `expires_at` (epoch seconds). Obvious fakes, no credential."""
    write_login(home / ".claude", "claude-code", expires_at)
    write_login(home / ".codex", "codex", expires_at)


def with_named(rig: "RuntimeRig", adapter: str, name: str, expires_at: int) -> Path:
    """Configure the named account `name` for `adapter` at `~/.hz-<name>`, its fake login expiring at
    `expires_at`, as AU-2 would resolve it, and give the runtime the accounts. Returns the directory."""
    configured = f"~/.hz-{name}"
    env = {"HOME": str(rig.home)}
    write_login(paths.expand(configured, env), adapter, expires_at)
    login_dir, files = canonical_login(name, adapter, configured, env)
    current = rig.runtime.c.accounts
    assert isinstance(current, ConfiguredAccounts)
    named = {**current.named,
             (adapter, name): Account(name, adapter, configured, login_dir, files, credential_key(adapter, files))}
    rig.runtime.c = replace(rig.runtime.c, accounts=ConfiguredAccounts(ADAPTERS, env, named))
    return paths.expand(configured, env)


def launch_spec(rig: RuntimeRig, profile: str, bead: str = "btq-1", *, account: str = "default",
                resume: bool = False) -> LaunchSpec:
    """A first launch, as plan 3's guard builds it: Claude's native ID is the session key, Codex's is
    unknown until its thread starts. The credential key is the account's, pinned now (AU-3)."""
    key = ids.role_session(bead, "coder", profile)
    native = key if profile == "p-one" else None
    return LaunchSpec("alpha", bead, "coder", profile, key, f"{bead} · coder", rig.worktree, resume=resume,
                      native_id=native, account=account, repo=rig.repo,
                      credential_key=rig.runtime.c.accounts.current_key(ADAPTERS[profile], account))
```

`RuntimeRig` is a dataclass defined below `with_named`, so its annotation there is a string. `with_named` must not be called before the rig exists.

In `tests/fakes/scripted_selftest.py`, add `self.others: list[tuple[tuple[str, Path], ...]] = []` to `__init__` (importing `Path`), and `self.others.append(ctx.others)` as the first line of `exec_path`.

In `tests/test_sandbox_runtime.py`, import `errno` and `fcntl`, `from sandbox_env import with_named, write_login` (beside the existing names), `from heterodyne.wsd import ids`, `from heterodyne.wsd.accounts import ConfiguredAccounts`, `from heterodyne.wsd.credlock import LOCK_BUSY, credential_lock`, and `KEY_CHANGED, LOGIN_STALE` from `heterodyne.wsd.runtime`. Then replace `test_a_named_account_is_refused` and `test_the_freshness_gate_refuses_a_login_that_expires_too_soon` with:

```python
DAY = 86_400


def logins_bound(rig: RuntimeRig, key: str) -> list[Path]:
    return [b.source for b in rig.backend.boxes[record(rig, key).sandbox].spec.logins]


@pytest.mark.parametrize(("profile", "adapter", "login"), [("p-one", "claude-code", ".credentials.json"),
                                                           ("p-two", "codex", "auth.json")])
def test_a_named_account_binds_only_its_own_login(rig: RuntimeRig, profile: str, adapter: str,
                                                  login: str) -> None:
    folder = with_named(rig, adapter, "work", rig.clock() + DAY)
    spec = launch_spec(rig, profile, account="work")
    rig.runtime.launch(spec)
    assert logins_bound(rig, spec.session_key) == [(folder / login).resolve()]
    default = rig.home / (".claude" if adapter == "claude-code" else ".codex") / login
    assert rig.selftest.others == [(("default", default),)]     # §7: the default login is another account


def test_the_default_launch_still_protects_every_named_account(rig: RuntimeRig) -> None:
    folder = with_named(rig, "codex", "work", rig.clock() + DAY)
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    assert logins_bound(rig, spec.session_key) == [(rig.home / ".codex" / "auth.json").resolve()]
    assert rig.selftest.others == [(("work", folder / "auth.json"),)]


@pytest.mark.parametrize("pinned", ["", "ck1-" + "0" * 32])
def test_a_key_that_is_not_the_accounts_refuses_the_launch(rig: RuntimeRig, pinned: str) -> None:
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(replace(launch_spec(rig, "p-two"), credential_key=pinned))
    assert str(caught.value) == KEY_CHANGED
    assert rig.backend.created == []


def test_a_login_repointed_after_the_pin_refuses_the_launch(rig: RuntimeRig) -> None:
    """D2: the account's configured path now resolves to other files, so its key is not the pinned one."""
    write_login(rig.root / "login-a", "codex", rig.clock() + DAY)
    write_login(rig.root / "login-b", "codex", rig.clock() + DAY)
    (rig.home / ".hz-work").symlink_to(rig.root / "login-a")
    with_named(rig, "codex", "work", rig.clock() + DAY)         # writes through the link, into login-a
    spec = launch_spec(rig, "p-two", account="work")
    (rig.home / ".hz-work").unlink()
    (rig.home / ".hz-work").symlink_to(rig.root / "login-b")
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(spec)
    assert str(caught.value) == KEY_CHANGED
    assert rig.backend.created == []


def test_the_key_and_the_login_are_read_under_the_accounts_lock(rig: RuntimeRig,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-two")
    lock = rig.runtime.c.credlocks / f"{spec.credential_key}.lock"
    held: list[int] = []
    real = ConfiguredAccounts.current_key

    def probing(self: ConfiguredAccounts, adapter: str, account: str) -> str:
        fd = os.open(lock, os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(0)                                      # taken: the runtime did not hold it
        except BlockingIOError as exc:
            held.append(exc.errno)
        finally:
            os.close(fd)
        return real(self, adapter, account)

    monkeypatch.setattr(ConfiguredAccounts, "current_key", probing)
    rig.runtime.launch(spec)
    assert held == [errno.EWOULDBLOCK]


def test_a_held_lock_refuses_the_launch_within_its_bound(rig: RuntimeRig) -> None:
    rig.runtime.c = replace(rig.runtime.c, settings=replace(rig.runtime.c.settings, lock_wait_seconds=1))
    spec = launch_spec(rig, "p-two")
    with credential_lock(rig.runtime.c.credlocks, spec.credential_key, 0):
        with pytest.raises(LaunchFailed) as caught:
            rig.runtime.launch(spec)
    assert str(caught.value) == LOCK_BUSY
    assert rig.backend.created == []
    rig.runtime.launch(spec)                                    # released: the next pickup launches


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_the_freshness_gate_refuses_a_login_that_expires_too_soon(rig: RuntimeRig, profile: str) -> None:
    s = rig.runtime.c.settings
    rig.clock.advance(DAY - (s.max_lifetime_seconds + s.stop_margin_seconds))
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(launch_spec(rig, profile))
    assert str(caught.value) == LOGIN_STALE
    assert rig.backend.created == []


def test_the_freshness_gate_reads_the_chosen_accounts_login(rig: RuntimeRig) -> None:
    s = rig.runtime.c.settings
    soon = rig.clock() + s.max_lifetime_seconds + s.stop_margin_seconds
    with_named(rig, "codex", "work", soon)
    with pytest.raises(LaunchFailed) as caught:
        rig.runtime.launch(launch_spec(rig, "p-two", account="work"))
    assert str(caught.value) == LOGIN_STALE
    write_login(rig.home / ".codex", "codex", soon)             # now the default is the stale one
    write_login(rig.home / ".hz-work", "codex", rig.clock() + DAY)
    rig.runtime.launch(launch_spec(rig, "p-two", account="work"))
    assert len(rig.backend.created) == 1


def test_a_claude_handoff_starts_the_d7_session_fresh(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    second = rig.runtime.launch(replace(spec, generation=2, resume=False, handoff=True,
                                        native_id=first.native_id))
    assert second.native_id == ids.handoff_session(spec.session_key, 2) != first.native_id
    pane = rig.backend.ttys[-1]
    assert "--resume" not in pane and pane[pane.index("--session-id") + 1] == second.native_id


def test_a_codex_handoff_starts_a_new_thread(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    second = rig.runtime.launch(replace(spec, generation=2, resume=False, handoff=True,
                                        native_id=first.native_id))
    assert second.native_id is not None and second.native_id != first.native_id
    assert "resume" not in rig.backend.ttys[-1]
```

The fake Codex TUI makes a fresh `uuid4` thread ID on every start that resumes nothing (`fake_agent_cli.py`), so `test_a_codex_handoff_starts_a_new_thread` fails if the runtime passes `resume` or the old thread to it.

In `tests/test_sandbox_settings.py`, add to `test_defaults`:

```python
    assert s.lock_wait_seconds == 60
```

and to the `test_bad_values_are_refused_by_key` parameters:

```python
    ("lock_wait_seconds = 0", "lock_wait_seconds"),
    ("lock_wait_seconds = 601", "lock_wait_seconds"),
```

In `tests/test_wsd_pickup.py`, add:

```python
def test_the_launch_carries_the_pinned_credential_key(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    [spec] = rig.runtime.launches
    assert rig.ws.accounts is not None
    assert spec.credential_key == rig.ws.accounts.current_key("claude-code", "default")
    assert spec.handoff is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 900 uv run pytest tests/test_sandbox_runtime.py tests/test_sandbox_settings.py tests/test_wsd_pickup.py -q`
Expected: FAIL. `RuntimeConfig` has no `credlocks` (a `TypeError` in the rig), `SandboxSettings` has no `lock_wait_seconds`, and the pickup test's `credential_key` is `""`.

- [ ] **Step 3: Implement**

In `src/heterodyne/defaults/defaults.toml`, add to `[sandbox]` after `agent_probe_seconds`:

```toml
lock_wait_seconds = 60                  # the longest a launch waits for an account's login lock (D8)
```

In `src/heterodyne/sandbox/settings.py`, add `"lock_wait_seconds"` to `HOST_KEYS`, the field after `agent_probe_seconds`:

```python
    lock_wait_seconds: int                   # D8: the longest a launch waits for an account's login lock
```

and in `from_config`, after `agent_probe_seconds=...`:

```python
        lock_wait_seconds=_int(sb, "lock_wait_seconds", 1, 600),
```

In `src/heterodyne/sandbox/runtime.py`:

- import `from heterodyne.wsd import gitwip, ids` (replacing the `gitwip` import), `from heterodyne.wsd.credlock import CredentialBusy, credential_lock`, and add `KEY_CHANGED` and `LOGIN_STALE` to the `heterodyne.wsd.runtime` import;
- add `credlocks: Path` to `RuntimeConfig` after `sessions`, with the comment `# <wsd state>/credlocks: the D8 lock directory (wsd.credlock)`;
- in `launch`, delete the two lines that refuse a named account, and replace the two lines

```python
        protected, others = self._logins(adapter)
        expiry = self._fresh(adapter, protected.chosen_files)
```

with:

```python
        protected, others = self._logins(adapter, spec.account)
        expiry = self._pinned(adapter, spec, protected.chosen_files)
```

- replace `_resume`'s docstring and first line with:

```python
        """D9: (resume, native ID). Resume held state; start fresh only when the record shows no generation
        ever ran; otherwise RuntimeUnavailable. Missing state is never evidence that none existed. D7: a
        handoff starts fresh whatever is held: a server-assigned ID is learned from the session, and any
        other is the handoff's own (`ids.handoff_session`)."""
        if spec.handoff:
            return False, None if adapter.assigns_id else ids.handoff_session(spec.session_key, spec.generation)
        wanted = spec.native_id or (prev.native_id if prev is not None and prev.ran else None)
```

- replace `_logins` and `_fresh` with:

```python
    def _logins(self, adapter: Adapter, account: str) -> tuple[Protected, tuple[tuple[str, Path], ...]]:
        """The chosen account's login, and every other account's as configured: `default` is another
        account whenever a named one is chosen (§7)."""
        try:
            chosen_dir, chosen = self.c.accounts.login_paths(adapter.name, account)
            others: list[tuple[str, Path]] = []
            dirs: list[Path] = []
            for name in (DEFAULT_ACCOUNT, *self.c.accounts.configured(adapter.name)):
                if name == account:
                    continue
                folder, files = self.c.accounts.login_paths(adapter.name, name)
                dirs.append(folder)
                others += [(name, f) for f in files]
        except ConfigError as exc:
            raise LaunchFailed(str(exc)) from None
        return Protected(chosen_dir, chosen, tuple(dirs), tuple(f for _, f in others)), tuple(others)

    def _pinned(self, adapter: Adapter, spec: LaunchSpec, login_files: tuple[Path, ...]) -> int:
        """E1, D8: under the account's lock, the key the entry pinned must still be the account's, and
        then the login is read for the freshness gate. Returns the expiry it checked."""
        if not spec.credential_key:
            raise LaunchFailed(KEY_CHANGED)
        try:
            with credential_lock(self.c.credlocks, spec.credential_key, self.c.settings.lock_wait_seconds,
                                 clock=self.c.wait_clock, sleep=self.c.sleep):
                try:
                    current = self.c.accounts.current_key(adapter.name, spec.account)
                except ConfigError as exc:
                    raise LaunchFailed(str(exc)) from None
                if current != spec.credential_key:
                    raise LaunchFailed(KEY_CHANGED)
                return self._fresh(adapter, login_files)
        except CredentialBusy as exc:
            raise LaunchFailed(str(exc)) from None

    def _fresh(self, adapter: Adapter, login_files: tuple[Path, ...]) -> int:
        """§7 freshness gate (D12, E3): plan 4b never refreshes, so a login that would expire within the
        session's maximum lifetime and stop margin refuses the launch with LOGIN_STALE, which AU-8 reads
        as "try the next account". Returns the expiry it checked."""
        try:
            expiry = adapter.access_expiry(login_files)
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        s = self.c.settings
        if expiry - self.c.clock() <= s.max_lifetime_seconds + s.stop_margin_seconds:
            raise LaunchFailed(LOGIN_STALE)
        return int(expiry)
```

`credential_lock` refuses a key that isn't `ck1-` and 32 hex digits with `BAD_KEY` before it touches a path. An empty key is caught first, as `KEY_CHANGED`, because an entry with no key was never pinned. A `BAD_KEY` refusal means a malformed key reached the runtime, which only a bug can do, and it refuses the launch like any other lock failure.

In `src/heterodyne/sandbox/build.py`, add `credlocks=s.credlock_dir,` after `sessions=s.state_dir / "sessions",`.

In `src/heterodyne/wsd/park.py`, `_pin_and_dispatch`, add `credential_key=entry.credential_key` to the `LaunchSpec(...)` call, after `account=entry.account`. Task 10 adds `handoff` beside it.

In `tests/live/test_live_sandbox.py`:
- add `credlocks=root / "k",` to its `RuntimeConfig(...)`, after `sessions=root / "s",`;
- in `Live.spec`, add `credential_key=self.runtime.c.accounts.current_key(self.runtime.c.accounts.adapter(profile) or "", "default")` to the `LaunchSpec(...)` call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 900 uv run pytest tests/test_sandbox_runtime.py tests/test_sandbox_settings.py tests/test_wsd_pickup.py tests/test_sandbox_live_teardown.py tests/test_sandbox_build.py -q`
Expected: PASS. `tests/test_sandbox_build.py` covers `build_runtime`'s new `credlocks` argument.

Then: `timeout 600 uv run pyright src tests/sandbox_env.py tests/test_sandbox_runtime.py tests/live/test_live_sandbox.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/sandbox/runtime.py src/heterodyne/sandbox/settings.py src/heterodyne/defaults/defaults.toml \
        src/heterodyne/sandbox/build.py src/heterodyne/wsd/park.py tests/sandbox_env.py \
        tests/fakes/scripted_selftest.py tests/live/test_live_sandbox.py tests/test_sandbox_runtime.py \
        tests/test_sandbox_settings.py tests/test_wsd_pickup.py
git commit -m "plan4b T6: launch on the pinned account, under its lock, with the key rechecked"
```

## Task 7: the runtime's signals

The producers, wired: each session's socket counts its adapter's turn ends and hands its hook payloads to the adapter's parser. The Codex read is polled through `backend.exec`. `signals(ws)` drains what the workstream's sessions reported. Every producer is off unless the profile's capabilities enable it (E15): with every capability off, the launch shape is plan 4's except for `StopFailure` (E7).

**Files:**
- Modify: `src/heterodyne/sandbox/runtime.py` (`signals`, `_caps`, `_server`, `_report`, `_poll`; `_start` and `_rebind` use `_server`; `_start` passes `usage=`)
- Modify: `src/heterodyne/sandbox/settings.py` (`usage_poll_seconds`), `src/heterodyne/defaults/defaults.toml`
- Modify: `tests/fakes/fake_agent_cli.py` (the TUI's `HZ_FAKE_STOP_FAILURE` and `HZ_FAKE_STATUS_LINE`), `tests/fakes/fake_backend.py` (`host_env`), `tests/sandbox_env.py` (`with_caps`)
- Test: `tests/test_sandbox_runtime.py`, `tests/test_sandbox_settings.py`

**Interfaces:**
- Consumes:
  - `UsageSeen`, `LimitSeen`, `Signal`, `AgentRuntime.signals` (Task 1).
  - `SessionServer(turn_ends=, on_event=)`, `Adapter.turn_end_events` (Task 4).
  - `Adapter.usage_from_event`, `usage_argv`, `usage_from_read`, `run_files(usage=)`, `UsageReport` (Task 5).
  - `SandboxRuntime._logins(adapter, account)`, `RuntimeConfig.credlocks` (Task 6).
  - `Accounts.view(profile).capabilities` (exists).
- Produces:
  - `SandboxRuntime.signals(ws) -> list[Signal]`: never raises.
  - `SandboxSettings.usage_poll_seconds: int`, from `[sandbox] usage_poll_seconds` (60 to 86400, default 600).
  - `runtime.MAX_QUEUED = 256`: the most reports a workstream keeps between drains, the newest kept.
  - `runtime.USAGE_SECONDS = 45`: the read's exec timeout. `codex_usage.py`'s own limit is 30.
  - `FakeBackend.host_env(name) -> dict[str, str]`: the environment the fake runs a sandbox's commands with.
  - `sandbox_env.with_caps(rig, adapter, caps)`.

Which report becomes which signal:

| Report | Capability | Signal |
|---|---|---|
| a status line's or a read's windows | (c) `in_session_usage` | `UsageSeen(key, generation, windows)` |
| `StopFailure` with `error == "rate_limit"`, or a read whose `rateLimitReachedType` isn't null | (e) `limit_signal` | `LimitSeen(key, generation, hint)` |

The key and generation are the ones the runtime gave the channel: the socket it served for that generation, or the sandbox it ran the read in. Nothing in a payload names them. The capabilities are read when the channel is set up (at launch or rebind), and `wsd` reads them again before it uses a signal (Task 9).

- [ ] **Step 1: Write the failing tests**

In `tests/fakes/fake_backend.py`, add after `_env`:

```python
    def host_env(self, name: str) -> dict[str, str]:
        """The environment this fake runs the sandbox's commands with, its paths on the host."""
        return self._env(self._box(name))
```

In `tests/fakes/fake_agent_cli.py`, add to the module docstring's TUI item: "With `HZ_FAKE_STOP_FAILURE=<error>` (Claude), each turn ends with a StopFailure hook carrying that `error` instead of Stop. With `HZ_FAKE_STATUS_LINE=<json>` (Claude), after each turn it runs the `--settings` layer's `statusLine` command, if it has one, with `{"session_id", "rate_limits": <json>}` on stdin, as 2.1.286 runs it after an API response." Add after `claude_hooks`:

```python
def claude_status_line() -> str | None:
    """The --settings layer's status-line command, which is where the harness puts it."""
    layer: Any = json.loads(Path(host(ARGS[ARGS.index("--settings") + 1])).read_text())
    status: Any = layer.get("statusLine")
    return status.get("command") if isinstance(status, dict) else None
```

and in `tui()`, replace the line that fires Stop with:

```python
        failure = os.environ.get("HZ_FAKE_STOP_FAILURE") if ADAPTER == "claude" else None
        if failure:
            fire(commands.get("StopFailure", []), "StopFailure", session, error=failure,
                 last_assistant_message="You've hit your limit · resets 12:10pm")
        else:
            fire(commands.get("Stop", []), "Stop", session, last_assistant_message=f"echo: {line}")
        limits = os.environ.get("HZ_FAKE_STATUS_LINE") if ADAPTER == "claude" else None
        status = claude_status_line() if limits else None
        if status:
            subprocess.run(host(status), shell=True, check=False,  # noqa: S602
                           input=json.dumps({"session_id": session, "rate_limits": json.loads(limits or "null")}).encode())
```

In `tests/sandbox_env.py`, import `Capabilities` from `heterodyne.config.capabilities`, and add after `with_named`:

```python
def with_caps(rig: "RuntimeRig", adapter: str, caps: Capabilities) -> None:
    """Give `adapter` these capabilities in the runtime's accounts, keeping any named accounts."""
    current = rig.runtime.c.accounts
    assert isinstance(current, ConfiguredAccounts)
    rig.runtime.c = replace(rig.runtime.c, accounts=ConfiguredAccounts(
        ADAPTERS, current.env, current.named, {**current.capabilities, adapter: caps}))
```

In `tests/test_sandbox_runtime.py`, import `Capabilities`, `from sandbox_env import with_caps`, `from heterodyne.agents.codex import IN_SESSION_SOURCE`, `from heterodyne.agents.claude_code import STATUS_SOURCE`, `from heterodyne.sandbox.runtime import MAX_QUEUED`, `from heterodyne.wsd.runtime import LimitSeen, Signal, UsageSeen` and `from heterodyne.wsd.usage import Observation`, then add:

```python
USAGE = Capabilities(in_session_usage=True)
LIMITS = Capabilities(limit_signal=True)
BOTH = Capabilities(in_session_usage=True, limit_signal=True)
RESET = 1_800_003_600
STATUS = {"five_hour": {"used_percentage": 42, "resets_at": RESET}}
SNAPSHOT = {"limitId": "codex", "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": RESET}}


def drained(rig: RuntimeRig, want: int) -> list[Signal]:
    """Drain until `want` signals have come, then once more to prove no more are queued."""
    got: list[Signal] = []

    def enough() -> bool:
        got.extend(rig.runtime.signals("alpha"))
        return len(got) >= want

    wait_for(enough)
    got.extend(rig.runtime.signals("alpha"))
    return got
```

The tests, after `drained`:

```python
def reads(rig: RuntimeRig) -> int:
    return sum(1 for argv in rig.backend.execs if any(a.endswith("codex_usage.py") for a in argv))


def codex_login_inside(rig: RuntimeRig, key: str) -> None:
    """The fake has no login bind: put a fake login where the session's CODEX_HOME is on the host."""
    home = Path(rig.backend.host_env(record(rig, key).sandbox)["CODEX_HOME"])
    (home / "auth.json").write_text("fake-not-a-token")


def test_with_every_capability_off_no_producer_runs(rig: RuntimeRig) -> None:
    rig.backend.env_extra |= {"HZ_FAKE_STATUS_LINE": json.dumps(STATUS),
                              "HZ_FAKE_STOP_FAILURE": "rate_limit",
                              "HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT)}
    one, two = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(one)                                     # the self-test's turn ends on StopFailure
    rig.runtime.launch(two)
    settings = json.loads((rig.runtime.layout(one.session_key).run(1) / "claude-settings.json").read_text())
    assert "statusLine" not in settings and "StopFailure" in settings["hooks"]
    assert not (rig.runtime.layout(two.session_key).run(1) / "codex_usage.py").exists()
    rig.clock.advance(rig.runtime.c.settings.usage_poll_seconds)
    assert rig.runtime.signals("alpha") == [] and reads(rig) == 0


def test_a_claude_status_line_is_usage_from_its_own_session(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", USAGE)
    rig.backend.env_extra["HZ_FAKE_STATUS_LINE"] = json.dumps(STATUS)
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    pane = record(rig, spec.session_key).tmux_session
    rig.tmux.paste(pane, "another turn")
    # The fake echoes only after its status line has run: the unchanged limits were not sent again.
    wait_for(lambda: "echo: another turn" in rig.tmux.capture(pane, 50))
    assert drained(rig, 1) == [UsageSeen(spec.session_key, 1,
                                         (Observation("claude:5h", "5h", 42, RESET, STATUS_SOURCE),))]


def test_a_claude_rate_limit_is_a_limit_and_ends_the_turn(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", LIMITS)
    rig.backend.env_extra["HZ_FAKE_STOP_FAILURE"] = "rate_limit"
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)                                    # the self-test waits for a turn end
    assert rig.runtime.servers[spec.session_key].turns().idle
    assert drained(rig, 1) == [LimitSeen(spec.session_key, 1, None)]


@pytest.mark.parametrize(("caps", "error"), [(USAGE, "rate_limit"), (LIMITS, "overloaded")])
def test_a_stop_failure_is_a_limit_only_with_e_and_rate_limit(rig: RuntimeRig, caps: Capabilities,
                                                              error: str) -> None:
    with_caps(rig, "claude-code", caps)
    rig.backend.env_extra["HZ_FAKE_STOP_FAILURE"] = error
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.tmux.paste(record(rig, spec.session_key).tmux_session, "again")
    wait_for(lambda: rig.runtime.servers[spec.session_key].turns().stops >= 2)
    assert rig.runtime.signals("alpha") == []


def test_the_codex_read_is_polled_when_due(rig: RuntimeRig) -> None:
    with_caps(rig, "codex", BOTH)
    rig.backend.env_extra["HZ_FAKE_RATE_LIMITS"] = json.dumps(SNAPSHOT)
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    codex_login_inside(rig, spec.session_key)
    window = Observation("codex:codex:300m", "5h", 100, RESET, IN_SESSION_SOURCE)
    assert rig.runtime.signals("alpha") == [UsageSeen(spec.session_key, 1, (window,)),
                                            LimitSeen(spec.session_key, 1, RESET)]
    assert rig.runtime.signals("alpha") == [] and reads(rig) == 1      # not due again yet
    rig.clock.advance(rig.runtime.c.settings.usage_poll_seconds)
    assert len(rig.runtime.signals("alpha")) == 2 and reads(rig) == 2
    assert rig.runtime.signals("beta") == []                           # another workstream's: none


@pytest.mark.parametrize("fault", ["error", "garbage", "no-login"])
def test_a_read_that_fails_reports_nothing(rig: RuntimeRig, fault: str) -> None:
    with_caps(rig, "codex", BOTH)
    rig.backend.env_extra["HZ_FAKE_RATE_LIMITS"] = json.dumps(SNAPSHOT)
    if fault != "no-login":
        rig.backend.env_extra["HZ_FAKE_RATE_FAULT"] = fault
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    if fault != "no-login":
        codex_login_inside(rig, spec.session_key)
    assert rig.runtime.signals("alpha") == []
    assert reads(rig) == 1                                      # it ran and failed: not skipped


def test_a_rebound_session_still_reports(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", LIMITS)
    rig.backend.env_extra["HZ_FAKE_STOP_FAILURE"] = "rate_limit"
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    assert drained(rig, 1) == [LimitSeen(spec.session_key, 1, None)]
    rig.runtime.servers.pop(spec.session_key).close()          # wsd died
    rig.runtime.sessions("alpha")                              # rebinds
    rig.tmux.paste(record(rig, spec.session_key).tmux_session, "after the restart")
    assert drained(rig, 1) == [LimitSeen(spec.session_key, 1, None)]


def test_a_flood_of_reports_keeps_only_the_newest(rig: RuntimeRig) -> None:
    with_caps(rig, "claude-code", LIMITS)
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    on_event = rig.runtime.servers[spec.session_key].on_event
    assert on_event is not None
    for _ in range(MAX_QUEUED + 10):
        on_event({"hook_event_name": "StopFailure", "error": "rate_limit"})
    assert len(rig.runtime.signals("alpha")) == MAX_QUEUED


def test_signals_never_raise(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    with_caps(rig, "claude-code", LIMITS)
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    on_event = rig.runtime.servers[spec.session_key].on_event
    assert on_event is not None
    on_event({"hook_event_name": "StopFailure", "error": "rate_limit"})

    def unlistable() -> list[SessionRecord]:
        raise RuntimeUnavailable("the session records can't be listed")

    monkeypatch.setattr(rig.runtime, "_records", unlistable)
    assert rig.runtime.signals("alpha") == [LimitSeen(spec.session_key, 1, None)]
```

In `tests/test_sandbox_settings.py`, add to `test_defaults`:

```python
    assert s.usage_poll_seconds == 600
```

and to the `test_bad_values_are_refused_by_key` parameters:

```python
    ("usage_poll_seconds = 59", "usage_poll_seconds"),
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 900 uv run pytest tests/test_sandbox_runtime.py tests/test_sandbox_settings.py -q`
Expected: FAIL. `SandboxSettings` has no `usage_poll_seconds`, the runtime has no `signals` of its own (`AgentRuntime`'s protocol body returns None), `MAX_QUEUED` can't be imported, and the StopFailure self-test times out because the server doesn't yet count `StopFailure` as a turn end.

- [ ] **Step 3: Implement**

In `src/heterodyne/defaults/defaults.toml`, add to `[sandbox]` after `lock_wait_seconds`:

```toml
usage_poll_seconds = 600                # how often a session's in-sandbox usage read runs, when (c) is on
```

In `src/heterodyne/sandbox/settings.py`, add `"usage_poll_seconds"` to `HOST_KEYS`, the field after `lock_wait_seconds`:

```python
    usage_poll_seconds: int                  # E5: the in-sandbox usage read's interval, when (c) is on
```

and in `from_config`, after `lock_wait_seconds=...`:

```python
        usage_poll_seconds=_int(sb, "usage_poll_seconds", 60, 86_400),
```

In `src/heterodyne/sandbox/runtime.py`:

- import `threading`; `from heterodyne.agents.base import UsageReport` (beside the existing names); `from heterodyne.config.capabilities import NONE, Capabilities`; and `LimitSeen`, `Signal`, `UsageSeen` from `heterodyne.wsd.runtime`;
- add after `RECORD_LOST`:

```python
MAX_QUEUED = 256        # reports a workstream keeps between drains: a flooding session keeps only its newest
USAGE_SECONDS = 45      # the in-sandbox usage read's exec bound (codex_usage.py's own limit is 30)
```

- in `SandboxRuntime.__init__`, add:

```python
        self.queued: dict[str, list[Signal]] = {}         # ws -> reports since the last drain
        self.queue_lock = threading.Lock()                # the servers' threads add, signals() drains
        self.polled: dict[tuple[str, int], int] = {}      # (key, generation) -> when its read last ran
```

- add after `expire`:

```python
    def signals(self, ws: str) -> list[Signal]:
        """E8: what the workstream's sessions reported since the last call, oldest first. Each running
        session's usage read is run first when it is due (E5). Never raises: a report that can't be had
        is no report."""
        try:
            running = [r for r in self._records() if r.ws == ws and r.phase is Phase.RUNNING]
        except RuntimeUnavailable:
            running = []
        for rec in running:
            if rec.key in self.servers:
                self._poll(rec)
        with self.queue_lock:
            return self.queued.pop(ws, [])
```

- in `_end`, after `rec = self._phase(layout, rec, phase=Phase.ENDED)`, add `self.polled = {k: v for k, v in self.polled.items() if k[0] != rec.key}`, so no poll time outlives its session;
- add after `_app_server`:

```python
    def _caps(self, profile: str) -> Capabilities:
        """E15: the profile's capabilities now; none if the profile can't be resolved."""
        try:
            return self.c.accounts.view(profile).capabilities
        except ConfigError:
            return NONE

    def _server(self, rec: SessionRecord, adapter: Adapter, token: str, caps: Capabilities) -> SessionServer:
        """The generation's socket: it counts the adapter's turn ends and hands each hook payload to the
        adapter's parser, attributed to this generation (E8, E14)."""
        layout, key, gen = self.layout(rec.key), rec.key, rec.generation

        def heard(payload: dict[str, Any]) -> None:
            self._report(rec.ws, key, gen, caps, adapter.usage_from_event(payload))

        return SessionServer(layout.socket(gen), token, layout.events(gen), turn_ends=adapter.turn_end_events,
                             on_event=heard)

    def _report(self, ws: str, key: str, gen: int, caps: Capabilities, report: UsageReport | None) -> None:
        if report is None:
            return
        found: list[Signal] = []
        if caps.in_session_usage and report.windows:
            found.append(UsageSeen(key, gen, report.windows))
        if caps.limit_signal and report.limited:
            found.append(LimitSeen(key, gen, report.reset_hint))
        if found:
            with self.queue_lock:
                queue = self.queued.setdefault(ws, [])
                queue.extend(found)
                del queue[:-MAX_QUEUED]

    def _poll(self, rec: SessionRecord) -> None:
        """E5: run the session's in-sandbox usage read if (c) is on and it is due. A read that fails
        reports nothing and is not retried before the next interval."""
        adapter = self.c.adapters.get(rec.adapter)
        caps = self._caps(rec.profile)
        if adapter is None or not caps.in_session_usage:
            return
        now, ident = self.c.clock(), (rec.key, rec.generation)
        last = self.polled.get(ident)
        if last is not None and now - last < self.c.settings.usage_poll_seconds:
            return
        self.polled[ident] = now
        try:
            argv = adapter.usage_argv(self._cli(adapter))
            if argv is None:
                return
            r = self.backend.exec(rec.sandbox, Path(rec.worktree), argv, timeout=USAGE_SECONDS)
        except (LaunchFailed, BackendError, BackendUnavailable):
            return
        if r.returncode == 0:
            self._report(rec.ws, rec.key, rec.generation, caps, adapter.usage_from_read(r.stdout))
```

- in `_start`, read the capabilities once, before the run files are written, and use them for the files and the server:

```python
        caps = self._caps(spec.profile)
        files = {shim.TOKEN_FILE: token, SHIM_SOURCE.name: SHIM_SOURCE.read_text(),
                 shim.CONFIG_FILE: json.dumps(shim_config), REQUEST_INSIDE.name: shim.REQUEST_WRAPPER,
                 **adapter.run_files(usage=caps.in_session_usage), **self.selftest.files()}
```

and replace `server = SessionServer(layout.socket(gen), token, layout.events(gen))` with `server = self._server(rec, adapter, token, caps)`.

- in `_rebind`, replace the `try` block's server line with:

```python
            adapter = self.c.adapters[rec.adapter]
            server = self._server(rec, adapter, token, self._caps(rec.profile))
```

and widen its `except OSError:` to `except (OSError, KeyError):`. A record names its adapter from the same registry, so a `KeyError` means a record this build can't serve, which is a hold like an unreadable token.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 900 uv run pytest tests/test_sandbox_runtime.py tests/test_sandbox_settings.py tests/test_sandbox_live_teardown.py tests/test_session_server.py -q`
Expected: PASS. In `test_a_read_that_fails_reports_nothing`, `reads(rig) == 1` is the negative control's proof that the read ran: a read that was skipped would pass the `== []` check too.

Then: `timeout 600 uv run pyright src tests/test_sandbox_runtime.py tests/fakes` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/sandbox/runtime.py src/heterodyne/sandbox/settings.py src/heterodyne/defaults/defaults.toml \
        tests/fakes/fake_agent_cli.py tests/fakes/fake_backend.py tests/sandbox_env.py \
        tests/test_sandbox_runtime.py tests/test_sandbox_settings.py
git commit -m "plan4b T7: the runtime's usage and limit signals, gated by capability"
```
