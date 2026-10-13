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
