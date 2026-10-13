# heterodyne-metaharness Plan 4b: accounts in the launcher, usage producers and failover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the accounts change plan's three launcher items on top of plan 4's `SandboxRuntime`:

- **AU-6** (btq-bc7yd): a launch binds the chosen account's login files, named accounts included. The per-credential-identity host lock (D8) serialises every harness refresh and rereads the login under it. The compiled spec check refuses every source in another account's login directory, with no exception (revision 16): the agent CLI comes from plan 4c's managed install, which lies in none. The Other accounts probe covers the default login whenever a named account is chosen.
- **AU-7** (btq-n1ox2): the in-session usage producers (untrusted), the trusted host-side read (Codex only), the limit-reached signals, and the reactive limit flow: defer up to the WIP commit, then the trusted read, then exhausted-and-regate or an untrusted defer on the session's own account.
- **AU-8** (btq-flxmf): launch, resume and relaunch take the gate's account. A switch is a cross-account resume (b) or a handoff relaunch (f) with `uuid5(NS, f"{session_key}:{generation}")`. A confirmed limit relaunches on the next eligible account at once. A freshness refusal moves to the next account of the same permitted list, else `needs-human`.

**Architecture:** the pure gate (AU-5), the launch entries (AU-3), the defer operation (AU-4) and the capability table (AU-2) already exist. This plan adds producers and consumers around them and never changes their rules.

- The runtime gets the pinned account and key on `LaunchSpec`. Under the D8 lock it checks that the key is unchanged and that the login is fresh. It binds that account's files and protects every other account's files, the default's included.
- Sessions report through two channels that already exist and are untrusted: the per-session socket (Claude's hooks and a status-line shim) and an in-sandbox `codex app-server` read. The runtime turns their reports into `Signal`s, attributed to `(session key, generation)`. `wsd` drains them on every pickup.
- The trusted read is `HostUsageReader`, a short-lived host-side `codex app-server` over stdio. It runs in a scratch `CODEX_HOME` that holds a copy of the account's `auth.json`, under the D8 lock, and only while the access token is valid.
- `wsd` keeps all policy: what is ingested, when a signal defers, whether a read confirms, and which account a relaunch takes. It still names no adapter.

**Every capability stays off.** S7 has demonstrated nothing on a real account, so G2 has not passed and `CAPABILITIES` stays empty. Tasks 1–10 build the machinery and test it with injected `Capabilities`. Task 11 Part A adds the evidence record and the table's invariants, and enables nothing. Part B fills the table, one capability at a time, once G2 accepts each S7 finding.

**Tech Stack:** Python 3.12+, `uv`, pytest, hypothesis, ruff, pyright strict, msgspec (the only runtime dependency), SQLite (the existing journal), `fcntl.flock`.

**Spec:**
- ADR 0001, revision 16 draft (design repo `hermes-workstreams-v2`, branch `adr-r16`, commits `0ba5010` and `5def9ef`, rewritten by `54a808d` for the operator's decision of 2026-10-13, on r15 `28e3df4`): §4.4 D1–D10, §7 Home isolation, Managed installs, the freshness gate and Other accounts. The first draft's install-root exception is gone (Appendix A.3).
- The accounts change plan `docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at `66ad195`: AU-6, AU-7 and AU-8, and gates G1–G3.
- S7, `docs/spikes/s7-accounts-and-usage.md` on `main`: the client-side findings, the L1–L7 runs and the recommended D9 defaults.
- Plan 4: D9–D13, D22, gap 3.

**Status:** draft for review. The managed install store that r16 now requires is plan 4c (Task 3; Plan 4c, below). It needs a cross-model design review, then operator approval (a design-approval bead) before any task starts. Task 11 Part B and Task 12 also wait on G2.

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
  - Each of these gives "unconfirmed" or a refused launch, never "fine": a lock that can't be taken, a login that can't be reread, a key that doesn't match, a CLI that isn't at its pin, a trusted read that times out or returns nothing parseable, and a scratch home that can't be confirmed gone.
  - An untrusted report never moves another bead or another account.
- **Never patch out the self-test, the credential check, the freshness gate, the CLI pin or the key check.** No setting disables them. Tests prove each one refuses a launch.
- **Negative controls name their exact refusal.**
  - Each refusal test asserts the exact message or errno, through the source's own constant (`runtime.LOGIN_STALE`, `runtime.KEY_CHANGED`, `credlock.LOCK_BUSY`). It is never a hand-typed string, never "any exception" and never "nonzero".
  - Exit 127 or a missing executable is never a refusal: the test fails.
  - A timeout, or no verified run, fails a negative control.
- **Exit statuses are never masked.** Subprocess helpers return or raise on the real status. Nothing ends in `|| true`, and no `check=False` result goes unread.
- **Cleanup is proven.** Every subprocess the code starts (the host app-server, the in-sandbox read) is ended in a `finally`, by its own handle, then confirmed gone (`wait` returns its status within a bound, and that status is its clean exit or the kill; anything else fails the read). Tests prove it: by the fake's recorded PID, or by the handle's `returncode` and closed pipes. A scratch directory is removed in a `finally` and confirmed absent with `os.path.lexists`. An unconfirmed cleanup is a failed read, never a success.
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
  - Tasks 1, 2 and 4–12 are `kind:task`. Task 3 is retired and has no bead.
  - Each carries `metadata.design_approval=<this plan's approval bead>`, `metadata.adr_revision=<the approved r16 commit>` and a blocking dependency on the approval bead.
  - Task 11 is two beads: Part A (`kind:task`, no G2 dependency) and Part B (one bead per enabling commit, each blocking on the G2 bead and on plan 4c's launcher bead). Task 12 blocks on the G2 bead, on Part B and on plan 4c's launcher bead.
  - AU-6, AU-7 and AU-8 (btq-bc7yd, btq-n1ox2 and btq-flxmf) close when every task that implements them has closed: Tasks 1, 2 and 6 for AU-6; 4, 5, 7, 8 and 9 for AU-7; 10 for AU-8; and 11 for all three. AU-6 also waits for plan 4c's launcher task, without which a named Codex account is refused (Task 3).
- **Branches:** each task has its own branch from `main` (`p4b-t<N>`) and its own PR, as plan 4's tasks did. Merge `origin/main`, never rebase, and never amend a pushed commit. Tasks that touch the same file merge in the order the Parallelism section gives.

---

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Where the ADR or the change plan is silent or loose | Choice | Why |
|---|---|---|---|
| E1 | How the runtime knows the pinned credential key (§4.4 D2: "Immediately before the launch, the guard checks that the account's current credential key still equals the journaled one"). | `LaunchSpec.credential_key` carries the entry's key. Under the D8 lock, just before it reads the login, the runtime recomputes `accounts.current_key(adapter, account)`. A mismatch refuses with `KEY_CHANGED`. An empty `credential_key` is refused the same way. | The guard's step 3 check happens before dispatch. The runtime then reads files that may have been repointed in between. Rechecking under the lock closes that window for the files actually bound. |
| E2 | The D8 lock's location and shape. | It is `<wsd state>/credlocks/<credential key>.lock`, opened by `gate.open_lock_file` (0600, no symlink, owner checked) and taken with `flock(LOCK_EX)`. The wait is bounded by `lock_wait_seconds` (default 60), polled with `LOCK_NB` and an injected clock and sleep. A wait past that bound raises `CredentialBusy(LOCK_BUSY)`. The key must match `ck1-[0-9a-f]{32}` before it is used in a path. `heterodyne setup` (AU-10) imports `credlock.credential_lock` and the same directory. | flock is per open file description, so threads in one wsd exclude each other, as `ClaimGate.locked` already relies on. The key is a digest, so the path never leaks a login path. A bounded wait turns a stuck holder into a refused launch (`LaunchFailed(LOCK_BUSY)`, a `failed` refusal that spends the launch budget, unlike E9's `LOGIN_STALE`), never a hang. |
| E3 | Whether plan 4b refreshes on the host (§7: "the launcher refreshes on the host first, or refuses to launch"; plan 4 D12 never refreshes). | **Plan 4b still never refreshes.** Under the lock, the freshness gate rereads the login and refuses with `LOGIN_STALE` (plan 4's text, moved to `wsd/runtime.py`). AU-8 then tries the next permitted account (E9). A host refresh is added only after S7 L6 records what a refresh writes and whether it rotates the refresh token (open question Q2). | S7 has not observed a completed refresh for either CLI. A harness refresh that rotates the shared refresh token and fails to save it would break the operator's host login, which is the exact hazard §7 guards against. |
| E4 | The trusted Codex read's home (§4.4 D9 (d): "a short-lived host-side app-server using the account's `CODEX_HOME`"). | The read runs in a fresh scratch `CODEX_HOME` (0700, under `<wsd state>/hostread/`). It holds only a copy of the account's `auth.json`, read under the lock. Before it starts, the access token must be valid for at least `read_margin_seconds` (default 300), so Codex never attempts a refresh (S7 (4): it attempts one only once the token has expired). Nothing is ever written to the account's login: if `auth.json` in the scratch home has changed when the read ends, the change is dropped and logged as `DIAG_ROTATED_DROPPED`. The scratch home is removed in a `finally` and confirmed gone. A read that a crash interrupted is cleaned up at wsd's next start, under its instance lock: its app-servers are found by the `CODEX_HOME` in their environment and killed through a pidfd, and its scratch home is removed once none of them is confirmed left; otherwise it is kept for the next start (`hostread.recover`). | The app-server writes sqlite state, `installation_id` and more into its home (S7 (1)). The account's real login directory, the operator's `~/.codex` included, must not collect that. The validity precondition makes a refresh unexpected. A write-back can't be a compare-and-swap, because the operator's CLI and admind replace the login without the harness lock (D8), so the harness never writes the login. If a refresh does happen, the warning says so, and a login it left stale is refused at the next pickup (Task 10's `pickup.stale`). |
| E5 | Codex's in-session usage source (§4.4 D9 (c): "`account/rateLimits/read` at launch and `account/rateLimits/updated` through the per-session app-server"). | A host-side client of the live app-server's WebSocket is not in this plan. Instead, an in-sandbox resource script, `codex_usage.py`, runs `codex app-server` over stdio in a scratch `CODEX_HOME` under `/tmp`. Its `auth.json` is a symlink to the session's read-only bound file. The script sends `account/rateLimits/read` and prints only the `rateLimits` object. The runtime runs it through `backend.exec` once the app-server has started, then every `usage_poll_seconds` (default 600) while the session runs. It runs only if the profile's capabilities enable (c). | This is the pattern `codex_trust.py` already uses: stdio JSON-RPC inside the sandbox, with the output validated on the host. The scratch home keeps the second app-server off the live one's sqlite files. The bind stays read-only, so a refresh attempt inside fails (`EROFS`), as §7 requires. Notification subscription is open question Q4. |
| E6 | Codex's limit-reached signal (§4.4 D9 (e)). | A `rateLimitReachedType` that isn't null in an in-session read is the limit signal (`LimitSeen`). The read runs when (c) or (e) is on, so (e) doesn't depend on (c), with the latest `resetsAt` of any window at 100% or more as its hint. `codexErrorInfo` (`usageLimitExceeded` / `rateLimitExceeded`) needs a notification stream, which E5 doesn't have (Q4). | The field is structured, and S7 (3) documents its values. Polling it needs no subscription. It is as untrusted as any in-session report, and D6's trusted read confirms it before anything beyond the bead's own defer. |
| E7 | Claude's in-session source and signal (S7 (3), (5)). | **(c):** with (c) enabled, the Claude settings gain `"statusLine": {"type": "command", "command": "python3 -I /run/hz/shim.py status"}`. The shim's new `status` mode sends only `{"hook_event_name": "StatusLine", "session_id", "rate_limits"}`, and only when `rate_limits` differs from the last value this launch's `wsd` accepted, when that value went to another launch (generations share `$HOME`; the launch is named by a digest of its session token, never the token), or when `STATUS_RESEND` (30 s) has passed, so an unchanged reading never goes stale (kept in `$HOME/.hz/status.json`). It prints nothing. **(e):** a `StopFailure` hook is always installed. With `error == "rate_limit"` it is the limit signal, with no structured reset (S7: only message text, which is never parsed). **Turn end:** `StopFailure` counts as a turn boundary (S7 finding 1), always. | The status line is S7's only Claude source. It is installed only when (c) is on, so the managed launch shape T14 validated doesn't change while every capability is off. The `StopFailure` boundary is needed whatever the capabilities, or a limited turn looks like a hang to the lifetime stop. |
| E8 | How reactive-limit signals reach `wsd` (§4.4 D6, D9). | `AgentRuntime.signals(ws) -> list[Signal]` drains the reports since the last call: `UsageSeen` and `LimitSeen`, each attributed to `(session key, generation)` by the runtime from the channel it set up. The scheduler calls it in every pickup, just after `expire` and before the sweep. `wsd` ingests a `UsageSeen` only if the profile's capabilities enable (c), and acts on a `LimitSeen` only if they enable (e). | A pull keeps `wsd` the only writer of the journal, as `expire` already does. Latency is up to one backstop interval. That is acceptable: the Claude CLI waits for the reset before it continues (S7 (5)), and the session is stopped before then. |
| E9 | The freshness-refusal path (AU-8: "A freshness-gate refusal moves on to the next account of the same permitted list"). | The runtime refuses a stale login with exactly `LOGIN_STALE`, and the receipt records it as `refused`/`failed` with that error. `Parker._outcome` recognises it by equality with the constant. It adds the entry's key to the op's `stale` data (comma-separated) and forgets the generation. The next pin runs the gate with those keys excluded (`exclude=`). If the gate finds another permitted account, the op returns `FAILED` without spending the launch budget, and the next pickup launches on that account. If it finds none (`AccountChanged`), the op escalates `LAUNCH_FAILED` with the detail `LOGIN_STALE`, so the bead becomes `needs-human`. A `Deadline` defers as usual. | No schema change: `receipts.refusal` keeps its CHECK constraint (`'failed', 'unavailable'`). The exclusion is per operation, so a later operation tries the refreshed login again. Exclusions only grow, so the path ends after at most one try per account. |
| E10 | The handoff relaunch (§4.4 D7). | `LaunchSpec.handoff` is set by the guard when the new entry's key differs from the last launched entry's, and the profile's capabilities have (f) but not (b). Its `resume` is then false. The runtime's `_resume` returns `(False, None)` for a server-assigned adapter (Codex), and `(False, ids.handoff_session(key, generation))` otherwise. The native ID reaches the entry through the receipt, as it already does. With (b), a switch is an ordinary resume of the latest native ID. **The handoff prompt itself** (the §4.1 deterministic handoff) **is not built here** (Q3). | The ID rule is D7's. The runtime already knows `assigns_id`, so `wsd` stays adapter-free. (f) stays off until S7 L4 passes and a real handoff works (S7's D9 table), so nothing ships a fresh session with no handoff. |
| E11 | The reactive limit flow (§4.4 D6). | `Parker.limit(session_key, generation, hint, now)` opens a PARK op with an **untrusted** QUOTA record clamped by D5. The record exists before anything stops, so a crash leaves the untrusted defer, never a trusted one. The op carries `limit_key` and `limit_gen`. The defer tail runs unchanged up to `defer.stopped`. The tail then commits the WIP unchanged (`defer.committed`, D6's first durable step). Two new steps sit after it (`LIMIT_STEPS`): `defer.read!`, after `Deps.usage_reader` answers (asked only if the profile has (d)), and `defer.confirmed`. **Confirmed** (`TrustedRead.limited`) means: the read's windows are ingested as trusted, `mark_exhausted` runs, and the gate runs again. The result supersedes the record with a trusted one: `Chosen` gives QUOTA until now (due at once), `Deadline` gives QUOTA until its `at`, and `AccountChanged` gives ACCOUNT_CHANGED. That happens in one transaction with `defer.confirmed`. **Unconfirmed** keeps the untrusted record. The tail then labels, comments and finishes as before. A failed WIP commit stops the tail before the read, so nothing is marked exhausted and no trusted record exists for a bead whose work isn't saved. A due QUOTA record goes through `undefer` in the same pickup, so the relaunch takes the next account through the guard's pin, as a new launch entry. | It reuses AU-4's crash-safe tail and AU-5's gate, and adds no new op kind. A forged signal reaches only its own bead's defer, at most `untrusted_max_defer_minutes`. "Relaunch at once" is the existing due-deferral path, which arms no timer for `until <= now`. |
| E12 | The CLI's install root inside a login directory (r16 §7, first draft). | **Superseded.** r16 as rewritten (`54a808d`) has no install-root exception, so plan 4b has no walk and no exemption. Plan 4's `check_credentials` refuses a CLI bound from another account's login directory, unchanged. The agent CLI moves to a managed install in plan 4c (Task 3; Plan 4c, above). | The operator chose a harness-owned, versioned, authenticated install store over proving a live install clean (2026-10-13). The walk's three blocking review findings (the tree isn't pinned after the walk, mounts can hide it, a hard link survives a refresh by rename) are closed by the store's design, not by a stronger walk. |
| E13 | Other accounts with a named account chosen (§7). | `_logins(adapter, account)` puts the chosen account's directory and files in `chosen_*`. Every other configured account goes in `other_*`, and the `default` login goes there too when it isn't the chosen one. `ProbeContext.others` lists the same accounts. | This is §7's rule ("the default login included"), and plan 4's probe already handles any list. |
| E14 | A session's turn-end events (S7 finding 1). | `Adapter.turn_end_events` is `("Stop", "StopFailure")` for Claude and `("Stop",)` for Codex. `SessionServer(turn_ends=...)` counts a turn end for any of them, and `TurnState.idle` is true after one. | The server stays adapter-neutral: it is given the names, and doesn't know them. |
| E15 | Capability gating's single source. | Every capability check reads `accounts.view(profile).capabilities`. The runtime reads it for the producers (no exec without (c), no status line without (c)). `wsd` reads it again before ingesting or acting. AU-2's `config check` already refuses accounts without (a), and `"next"` without (d). | One table, checked again by the consumer. A producer bug can't enable a capability. |

## ADR conflicts and gaps, flagged for the operator (not silently resolved)

### Open questions for the operator

| # | Question | Recommendation |
|---|---|---|
| Q1 | **G2 has not passed.** S7 showed the client side only, and L1–L7 need you. AU-6, AU-7 and AU-8 are formally blocked by G2 in the change plan. | Approve building Tasks 1–10 now, gated on capabilities and tested with injected ones, so the code is ready when L1–L7 run. Task 11 Part A (the invariants) can merge too. Task 11 Part B (the table entries) and Task 12 (live) stay blocked on G2. Nothing changes on a host until Task 11 enables a capability. |
| Q2 | **The host refresh** (§7 "refreshes on the host first, or refuses"). Plan 4b keeps refusing (E3). | Keep refusing in 4b. After L6, add a refresh task. For Codex, `account/read {"refreshToken": true}` in the E4 scratch home, under the lock. Saving its result needs a write that is safe against D8's writers that don't take the lock (E4 has none), so that task designs one first. For Claude there is no known host mechanism. |
| Q3 | **The handoff prompt.** (f) needs the §4.1 deterministic handoff, and no generator exists yet (S7 L4 defers it to "the AU task that builds it"). | Build the generator with plan 5 (steering delivers the first prompt there), and keep (f) off until then. Plan 4b builds only the ID and launch mechanics (E10). |
| Q4 | **Codex's live notifications.** E5 polls rather than subscribing to `account/rateLimits/updated` and `codexErrorInfo` on the live app-server. | Accept polling for v1. L2 should record whether `updated` fires mid-turn. If it does, a later task can add a host-side client on the bridge socket. |
| Q5 | **Version drift.** S7 ran 2.1.286 and 0.160.0, while the ADR §4.2 table names 2.1.283 and 0.157.0. The code pins 2.1.286 and 0.160.0. | Fix the §4.2 table in the next ADR revision. L1–L7 run on the code's pins. |
| Q6 | **Claude's `.credentials.lock`** sits beside `.credentials.json` (S7 (1)). Under a single-file read-only bind, the CLI's lock goes into the synthetic config directory, which is writable. Whether the CLI needs it to be shared with the host is L6's question. | No change in 4b. The single-file bind already keeps the host's lock out of the sandbox. L6 records the behaviour. |
| Q7 | **The managed install store is its own plan (4c).** r16's store adds an installer, manifests and verification, a selection file, launch-entry fields, `wsctl harness` commands with Marmot approval asks, a new self-test probe, and changes to both adapters' launch shapes. | Approve the split. Plan 4b's Tasks 1, 2 and 4–10 and 11a don't need the store and can proceed. Named Codex accounts, Task 11b and Task 12 wait for plan 4c. |
| Q8 | **Failover for Claude.** Claude has no (d), so `failover = "next"` is refused for it, and a Claude limit always defers on its own account. | Accept, as S7 recommends. Revisit only if a CLI version adds a host-side read. |
| Q9 | **`failover = "next"` without (b) or (f).** The config check requires only (d), and S7 recommends (d) plus (b) or (f). | Accept as it is: the gate already keeps a running session on its account without (b) or (f), so the only effect is that new sessions fail over. If you want S7's stricter rule, it is a two-line change to `config/accounts.py` in Task 11 Part B. |

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
| AU-6 | The compiled spec check before every launch | 6 (plan 4's check, unchanged: no exception, r16); the CLI's managed install is plan 4c |
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

## Plan 4c: what plan 4b consumes (r16 §7 Managed installs)

The managed install store is too large for this plan and reaches past it, so it is **plan 4c**, written next. It holds the installer and its manifest, the per-launch verification and taint, the Managed install probe, `harnesses.toml` and `[harnesses.<adapter>]`, the install and digest on launch entries (D2), `wsctl harness` and `/harness` with the install's approval ask, and each adapter's layout, content presentation and updater switches. It touches the config loader, `wsd`'s commands and journal, the admind approval flow, the self-test and both adapters. Plan 4b's tasks need none of it to be built and tested offline:
- the offline rig's fake CLI lies outside every login directory, so Task 6's named-account tests run on plan 4's check unchanged;
- on a host, before plan 4c lands, a named Codex account whose CLI lies in `~/.codex` is refused by plan 4's check, which is the r16 rule. Nothing ships with an exception.

Plan 4b consumes from plan 4c:
- the runtime's CLI resolution moved onto the selected, verified install (plan 4's `SandboxRuntime._cli`). Task 6 calls `_cli` as it is and needs no change.
- the same resolution for host-side use, so Task 8's `HostUsageReader` runs the verified install's entry point, and `build_usage_reader` enables the reader when Codex has a selected install, not when `[adapters.codex] binary` is set. Until then Task 8 locates the CLI as plan 4 does, and its pin check holds. Plan 4c makes that one change to `hostread.py` and `build.py`, and the (d) capability stays off until it has (Task 11b waits on plan 4c).
- Task 12 runs on the managed installs, so it waits for plan 4c, as Task 11b does.

## Parallelism

Tasks 1, 2 and 4 have no dependencies on each other, and three worker sessions can build them at once. Task 3 is retired (plan 4c). Task 5 uses Task 4's `CLAUDE_HOOK_EVENTS` and `SessionServer(on_event=)`, so it starts once Task 4 has merged. Each later task starts once everything it depends on has merged to `main`.

```
Wave 1 (parallel):  T1 interfaces, T2 credlock, T4 turn ends
Wave 2 (parallel):  T5 adapter parsing (T4)
                    T6 named-account binding (T1,T2)
                    T9 limit flow (T1)
Wave 3 (parallel):  T7 runtime signals (T4,T5,T6)
                    T8 trusted read (T1,T2,T5,T6)
                    T10 handoff and stale path (T1,T6,T9)
Any wave:           T11a table invariants (none)
Wave 4 (G2-gated):  T11b capability table (T6-T10, T11a, G2, plan 4c)  ->  T12 live test (T11b)
```

| Task | Depends on | Can run beside | Files it shares (merge order) |
|---|---|---|---|
| 1 | none | 2, 4, 5 | `wsd/runtime.py`, `wsd/headroom.py`, `wsd/usage.py`, `wsd/workstream.py`, `sandbox/runtime.py` (the empty `signals` only), `tests/fakes/fake_runtime.py` |
| 2 | none | 1, 4, 5 | none |
| 3 | retired (plan 4c) | | |
| 4 | none | 1, 2 | `session/server.py`, `agents/base.py`, `agents/claude_code.py` (4 before 5) |
| 5 | 4 | 1, 2, 6, 9 | `agents/base.py`, `agents/claude_code.py`, `agents/codex.py`, `session/shim.py` (merges after 4) |
| 6 | 1, 2 | 5, 8, 9 | `sandbox/runtime.py`, `sandbox/build.py`, `wsd/park.py` (one line in `_pin_and_dispatch`; merges before 10) |
| 8 | 1, 2, 5, 6 | 7, 9, 10 | `wsd/cli.py`, `wsd/daemon.py`, `sandbox/build.py` (merges after 6) |
| 9 | 1 | 5, 6, 8 | `wsd/park.py` (`capabilities`, `limit`, `defer_tail`), `wsd/scheduler.py`, `tests/wsd_env.py` |
| 10 | 1, 6, 9 | 7, 8 | `wsd/park.py` (`_pin`, `_pin_holds`, `_pin_and_dispatch`, `_outcome`; merges after 6 and 9), `tests/fakes/fake_runtime.py`, `tests/test_wsd_defer.py` (uses 9's helpers) |
| 7 | 4, 5, 6 | 10 | `sandbox/runtime.py` |
| 11a | none | any | `config/capabilities.py`, `tests/test_config_accounts.py` |
| 11b | 6–10, 11a, G2, plan 4c | none | `config/capabilities.py` |
| 12 | 11b | none | `tests/live/`, `docs/wsd.md` |

## File map

| File | Tasks | Responsibility |
|---|---|---|
| `src/heterodyne/wsd/runtime.py` | 1 | `LaunchSpec.credential_key` and `.handoff`, `LOGIN_STALE`, `KEY_CHANGED`, `UsageSeen`, `LimitSeen`, `Signal`, `AgentRuntime.signals`, `NoRuntime.signals` |
| `src/heterodyne/wsd/headroom.py` | 1 | `exclude` on `permitted`, `gate` and `admits` |
| `src/heterodyne/wsd/usage.py` | 1 | `exclude` on `decide` and `admitted`; `TrustedRead`, `UsageReader`, `NoUsageReader` |
| `src/heterodyne/wsd/ids.py` | 1 | `handoff_session` |
| `src/heterodyne/wsd/workstream.py` | 1 | `Deps.usage_reader` |
| `src/heterodyne/wsd/credlock.py` (new) | 2 | the D8 lock |
| `src/heterodyne/session/server.py` | 4 | `turn_ends`, `on_event`, `TurnState.boundary` |
| `src/heterodyne/agents/base.py` | 4, 5 | `turn_end_events`, `UsageReport`, `usage_from_event`, `run_files(usage=, limits=)` |
| `src/heterodyne/agents/claude_code.py` | 4, 5 | the `StopFailure` hook, the status line, parsing |
| `src/heterodyne/agents/codex.py` | 5 | `parse_rate_limits`, `usage_argv` |
| `src/heterodyne/session/shim.py` | 5 | the `status` mode |
| `src/heterodyne/sandbox/resources/codex_usage.py` (new) | 5 | the in-sandbox read |
| `src/heterodyne/sandbox/runtime.py` | 1, 6, 7 | named-account binding, the key check, the locked freshness gate, the handoff, `signals` |
| `src/heterodyne/sandbox/settings.py` | 6, 7 | `lock_wait_seconds`, `usage_poll_seconds` |
| `src/heterodyne/sandbox/build.py` | 6, 8 | the lock directory; the reader |
| `src/heterodyne/sandbox/hostread.py` (new) | 8 | `HostUsageReader` |
| `src/heterodyne/wsd/cli.py` (`run`, `wsd_main`), `src/heterodyne/wsd/daemon.py` (`assemble`) | 8 | wiring `Deps.usage_reader` |
| `src/heterodyne/wsd/park.py` | 6, 9, 10 | 6: `credential_key` on the spec; 9: `capabilities`, `limit`, `defer.confirmed`; 10: `stale_keys`, the handoff, the stale path |
| `src/heterodyne/wsd/scheduler.py` | 9 | draining `signals` |
| `src/heterodyne/config/capabilities.py` | 11 | `Evidence`, `EVIDENCE`, `table_problems`, then the table |
| `tests/fakes/fake_runtime.py`, `tests/fakes/fake_agent_cli.py` | 1, 5, 8, 10 | scripted signals; the fake `app-server` stdio mode; `stale_keys` |
| `tests/wsd_env.py` | 9 | `Rig.usage_reader`, passed to `Deps` |
| `tests/test_sandbox_build.py`, `tests/test_wsd_daemon.py` | 8 | `build_usage_reader`; the reader reaches `Deps` |
| `tests/test_wsd_defer.py` | 9, 10 | the limit flow; the handoff and the stale path |
| `tests/test_config_capabilities.py` (new) | 11 | the table's invariants |
| `docs/wsd.md` | 12 | the live accounts checks |
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
- Modify: `src/heterodyne/sandbox/runtime.py` (`SandboxRuntime.signals`, empty until Task 7)
- Modify: `tests/fakes/fake_runtime.py` (`report`, `signals`)
- Test: `tests/test_wsd_headroom.py`, `tests/test_wsd_ids.py`, `tests/test_wsd_signals.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `wsd.runtime.LaunchSpec(..., credential_key: str = "", handoff: bool = False)`: two new trailing fields.
  - `wsd.runtime.LOGIN_STALE: str` and `wsd.runtime.KEY_CHANGED: str`: the exact `LaunchFailed` texts for a stale login (E3) and a changed key (E1).
  - `wsd.runtime.UsageSeen(session_key: str, generation: int, observations: tuple[Observation, ...])`, `wsd.runtime.LimitSeen(session_key: str, generation: int, reset_hint: int | None)` and `type Signal = UsageSeen | LimitSeen`.
  - `AgentRuntime.signals(ws: str) -> list[Signal]`; `NoRuntime.signals` returns `[]`; `SandboxRuntime.signals` returns `[]` until Task 7 replaces it, so the real runtime satisfies the protocol from this commit on.
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
        found = decide(accounts, j, "p-two", None, NOW, S, exclude=frozenset({key}))
        assert isinstance(found, AccountChanged)
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
from typing import cast

from fakes.fake_runtime import FakeRuntime

from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.wsd.runtime import (
    KEY_CHANGED,
    LOGIN_STALE,
    AgentRuntime,
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


def test_the_sandbox_runtime_has_the_seam() -> None:
    assert "signals" in vars(SandboxRuntime)               # until Task 7, an empty drain; pyright checks
    runtime: AgentRuntime = cast(SandboxRuntime, None)     # that the class satisfies the protocol
    assert runtime is None


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

In `src/heterodyne/sandbox/runtime.py`, add `Signal` to the `heterodyne.wsd.runtime` import and add to `SandboxRuntime`, after `expire`:

```python
    def signals(self, ws: str) -> list[Signal]:
        return []                    # nothing produces a signal yet; Task 7 replaces this
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
    """A handoff relaunch's fresh native session ID (D7): new for each generation, recomputable. Its
    uuid5 name has one colon (`<session key>:<generation>`); role_session's has two, so they never meet."""
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

Then: `timeout 300 uv run pyright src/heterodyne/wsd src/heterodyne/sandbox/runtime.py tests/fakes/fake_runtime.py tests/test_wsd_signals.py` and `uv run ruff check src tests`.
Expected: 0 errors. pyright checks that `FakeRuntime` and `NoRuntime` still satisfy `AgentRuntime` wherever they are passed as one.

- [ ] **Step 5: Run the whole suite, then commit**

Run: `timeout 3600 uv run pytest -q`
Expected: PASS (no behaviour changed).

```bash
git add src/heterodyne/wsd/runtime.py src/heterodyne/wsd/headroom.py src/heterodyne/wsd/usage.py \
        src/heterodyne/wsd/ids.py src/heterodyne/wsd/workstream.py src/heterodyne/sandbox/runtime.py \
        tests/fakes/fake_runtime.py tests/test_wsd_headroom.py tests/test_wsd_ids.py tests/test_wsd_signals.py
git commit -m "plan4b T1: wsd seams for keys, handoffs, signals, exclusion and the trusted read"
```

## Task 2: the D8 credential lock

One host lock per credential identity (§4.4 D8). Every harness read of a login holds it: the runtime's key check and freshness reread (Task 6), the trusted read (Task 8, which never writes the login), and later `heterodyne setup` (AU-10).

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
    assert (locks.stat().st_mode & 0o777) == 0o700
    assert ((locks / f"{KEY}.lock").stat().st_mode & 0o777) == 0o600


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
the runtime's key check and freshness reread, the trusted host-side read, and
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

## Task 3: retired (the managed install store is plan 4c)

This task was the install-root walk and the exemption for the CLI's own bind. Revision 16, as rewritten for the operator's decision of 2026-10-13 (`54a808d`), drops that exception: the agent CLI and the content it loads come from a managed, versioned, authenticated install that lies inside no login directory (§7 Managed installs). The store is its own plan, **plan 4c** (see "Plan 4c: what plan 4b consumes", above). The heading stays so the task numbers hold. There is no Task 3 bead.

Nothing in plan 4b needs a replacement here:
- plan 4's `check_credentials` stays as it is, with no exemption. With a named account chosen, the default login is another account's, so a CLI bound from inside `~/.codex` is refused with plan 4's existing text. That is the r16 rule;
- plan 4c moves the CLI's bind onto the selected install, which lies outside every login directory, and turns plan 4's `test_credentials_pass_with_cli_inside_the_chosen_login_dir` into a refusal.

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
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl",
                      turn_ends=("Stop", "StopFailure"))
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
- Modify: `src/heterodyne/agents/claude_code.py` (`claude_windows`, `usage_from_event`, `usage_argv`, `usage_from_read`, `run_files(usage=, limits=)`)
- Modify: `src/heterodyne/agents/codex.py` (`parse_rate_limits`, `USAGE_SCRIPT`, `USAGE_SOURCE`, the same methods)
- Create: `src/heterodyne/sandbox/resources/codex_usage.py`
- Modify: `src/heterodyne/session/shim.py` (`run_status`, `status_main`, `main`)
- Modify: `tests/fakes/fake_agent_cli.py` (`account/rateLimits/read`, faults; `hooks/list` reads its config lazily)
- Test: `tests/test_agents_usage.py` (new), `tests/test_session_shim.py`

**Interfaces:**
- Consumes: `wsd.usage.Observation` (exists), `session.shim.json_depth`, `MAX_DEPTH`, `MAX_LINE` (exist); from Task 4, which must have merged first, `claude_code.CLAUDE_HOOK_EVENTS` and `SessionServer(..., on_event=)`.
- Produces:
  - `agents.base.STATUS_COMMAND = "python3 -I /run/hz/shim.py status"`.
  - `agents.base.UsageReport(windows: tuple[Observation, ...] = (), limited: bool = False, reset_hint: int | None = None)`.
  - On the `Adapter` protocol:
    - `usage_from_event(payload: Mapping[str, Any]) -> UsageReport | None`: a session-socket hook payload;
    - `usage_argv(cli: Cli) -> list[str] | None`: the in-sandbox read's command, or None if the adapter has none;
    - `usage_from_read(output: bytes) -> UsageReport | None`: that command's stdout;
    - `run_files(*, usage: bool = False, limits: bool = False) -> Mapping[str, str]`: with `usage` ((c) on) or `limits` ((e) on), the files that capability's producers need. Each flag installs what its own capability needs: Codex's read serves both, so either flag installs it (Codex plan 4b r2).
  - `codex.parse_rate_limits(data: object, source: str) -> UsageReport | None`, shared with Task 8.
  - `codex.IN_SESSION_SOURCE = "codex.app-server"`, `codex.HOST_SOURCE = "codex.host-read"`, `claude_code.STATUS_SOURCE = "claude.statusline"`.
  - The shim's `status` mode: `python3 -I /run/hz/shim.py status` reads Claude's status-line JSON on stdin and sends `{"hook_event_name": "StatusLine", "session_id", "rate_limits"}` once per change. It prints nothing and exits 0.
  - `codex_usage.py`: `python3 -I codex_usage.py <codex binary> [limit seconds]` prints the `rateLimits` object and exits 0, or exits 1 with nothing on stdout and exactly `codex_usage: <type>` on stderr. Its `read(codex, env, limit)` raises `TimeoutError`, `AnswerError`, `ClosedOutput`, `ExitStatus`, `NotGone`, `json.JSONDecodeError` or the `OSError` of a missing executable; `main` adds `NoRateLimits` and `ScratchKept`. All but the builtins are classes in the script.
  - The fake app-server answers `account/rateLimits/read` from `HZ_FAKE_RATE_LIMITS`; `HZ_FAKE_RATE_FAULT` is `hang`, `error`, `garbage`, `close` or `exit`; `HZ_FAKE_PID_FILE` receives its PID.

The windows' identities: Claude's are `claude:5h` and `claude:7d`. A Codex window is `codex:<limitId>:<minutes>m`, kind `5h` / `7d` / `<n>d` / `<n>h` / `<n>m` from its duration. They match `usage.LABEL` and never name a slot (`primary`, `five_hour`), as `Observation` requires. Percentages and resets pass through unchecked: `usage._check` validates them, so the parsers never decide a value is safe.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents_usage.py`:

```python
"""Plan 4b Task 5: what the CLIs' usage payloads mean (S7 (3), (5)), and the in-sandbox Codex read."""

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
    limits = {"five_hour": {"used_percentage": 1, "resets_at": resets}}
    report = ClaudeCode().usage_from_event({"hook_event_name": "StatusLine", "rate_limits": limits})
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
    assert json.loads(ClaudeCode().run_files(limits=True)[SETTINGS_FILE]) == plain    # StopFailure is a hook


# --- Codex ---

def test_a_snapshot_gives_one_observation_per_window() -> None:
    report = parse_rate_limits(SNAPSHOT, IN_SESSION_SOURCE)
    src = IN_SESSION_SOURCE
    assert report == UsageReport((Observation("codex:codex:300m", "5h", 42, NOW + 3600, src),
                                  Observation("codex:codex:10080m", "7d", 17, NOW + 86400, src)))
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


@pytest.mark.parametrize("window", [None, "x", {"usedPercent": 1},
                                    {"usedPercent": 1, "windowDurationMins": 0},
                                    {"usedPercent": 1, "windowDurationMins": True},
                                    {"usedPercent": 1, "windowDurationMins": "300"}])
def test_a_window_without_a_duration_is_skipped(window: object) -> None:
    snap = {"limitId": "codex", "primary": window, "rateLimitReachedType": None}
    report = parse_rate_limits(snap, HOST_SOURCE)
    assert report == UsageReport()


@pytest.mark.parametrize("data", [None, [], "x", 1])
def test_a_snapshot_that_is_not_an_object_is_nothing(data: object) -> None:
    assert parse_rate_limits(data, HOST_SOURCE) is None


@pytest.mark.parametrize("output", [b"", b"not json", b"[" * 200 + b"]" * 200, b"{" + b" " * 70_000 + b"}"])
def test_read_output_that_cannot_be_trusted_to_parse_is_nothing(output: bytes) -> None:
    assert Codex().usage_from_read(output) is None


def test_codex_hooks_carry_no_usage() -> None:
    assert Codex().usage_from_event({"hook_event_name": "Stop", "rate_limits": {}}) is None


def test_the_usage_script_ships_for_usage_or_limits() -> None:
    assert USAGE_SCRIPT.name not in Codex().run_files()
    assert Codex().run_files(usage=True)[USAGE_SCRIPT.name] == USAGE.read_text()
    assert Codex().run_files(limits=True)[USAGE_SCRIPT.name] == USAGE.read_text()


# --- codex_usage.py, run against the fake app-server ---

def diagnostic(proc: subprocess.CompletedProcess[bytes]) -> str:
    """The script's one stderr line, if it is `codex_usage: <type>`; never its raw text otherwise."""
    found = re.fullmatch(rb"codex_usage: ([A-Za-z]+)\n", proc.stderr)
    return found.group(1).decode() if found else "<unrecognised>"


def gone(pid_file: Path) -> bool:
    """The fake app-server that wrote `pid_file` has ended and been reaped: no such process is left."""
    try:
        os.kill(int(pid_file.read_text()), 0)
    except ProcessLookupError:
        return True
    return False


def run_usage(tmp_path: Path, limit: str = "30", **env: str) -> subprocess.CompletedProcess[bytes]:
    codex = install_fake_cli(tmp_path, "codex")
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    scratch = tmp_path / "tmp"
    scratch.mkdir(exist_ok=True)
    full = {"PATH": os.environ["PATH"], "CODEX_HOME": str(home), "TMPDIR": str(scratch),
            "HZ_FAKE_PID_FILE": str(tmp_path / "app-server.pid"), **env}
    return subprocess.run([sys.executable, "-I", str(USAGE), str(codex), limit], env=full,
                          capture_output=True, timeout=60, check=False)


def test_the_read_prints_the_snapshot_and_leaves_nothing(tmp_path: Path) -> None:
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "auth.json").write_text("fake-not-a-token")
    proc = run_usage(tmp_path, HZ_FAKE_RATE_LIMITS=json.dumps(SNAPSHOT))
    assert (proc.returncode, diagnostic(proc) if proc.stderr else "") == (0, "")
    assert Codex().usage_from_read(proc.stdout) == parse_rate_limits(SNAPSHOT, IN_SESSION_SOURCE)
    assert gone(tmp_path / "app-server.pid")
    assert list((tmp_path / "tmp").iterdir()) == []
    assert (tmp_path / "codex-home" / "auth.json").read_text() == "fake-not-a-token"


@pytest.mark.parametrize(("fault", "reason"), [
    ("error", "AnswerError"), ("garbage", "JSONDecodeError"), ("hang", "TimeoutError"),
    ("close", "ClosedOutput"), ("exit", "ExitStatus"), ("no-login", "AnswerError"),
    ("no-limits", "NoRateLimits")])
def test_a_failed_read_exits_1_with_its_own_diagnostic(tmp_path: Path, fault: str, reason: str) -> None:
    (tmp_path / "codex-home").mkdir()
    if fault != "no-login":
        (tmp_path / "codex-home" / "auth.json").write_text("fake-not-a-token")
    env = {"HZ_FAKE_RATE_LIMITS": "null" if fault == "no-limits" else json.dumps(SNAPSHOT)}
    if fault in ("error", "garbage", "hang", "close", "exit"):
        env["HZ_FAKE_RATE_FAULT"] = fault
    proc = run_usage(tmp_path, limit="2", **env)      # a TimeoutExpired here fails the test
    assert (proc.returncode, proc.stdout, diagnostic(proc)) == (1, b"", reason)
    assert gone(tmp_path / "app-server.pid")
    assert list((tmp_path / "tmp").iterdir()) == []


def test_a_missing_codex_is_its_own_error_never_an_answer(tmp_path: Path) -> None:
    (tmp_path / "codex-home").mkdir()
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    proc = subprocess.run([sys.executable, "-I", str(USAGE), str(tmp_path / "nowhere" / "codex")],
                          env={"PATH": os.environ["PATH"], "CODEX_HOME": str(tmp_path / "codex-home"),
                               "TMPDIR": str(scratch)}, capture_output=True, timeout=60, check=False)
    assert (proc.returncode, proc.stdout, diagnostic(proc)) == (1, b"", "FileNotFoundError")
    assert list(scratch.iterdir()) == []


# --- codex_usage.read and main, in this process, with their seams forced ---

POPEN = subprocess.Popen        # the class itself, before the `started` fixture wraps it

def usage_module() -> Any:
    spec = importlib.util.spec_from_file_location("codex_usage_under_test", USAGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[str]]:
    """Every app-server the read starts, recorded so a test can prove each was reaped."""
    made: list[subprocess.Popen[str]] = []

    def recording(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        proc = POPEN(*args, **kwargs)
        made.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", recording)
    return made


def ended(made: list[subprocess.Popen[str]]) -> bool:
    """One app-server was started, and it was reaped and its pipes closed."""
    return len(made) == 1 and all(p.returncode is not None and p.stdin is not None and p.stdin.closed
                                  and p.stdout is not None and p.stdout.closed for p in made)


def read_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text("fake-not-a-token")
    return {"PATH": os.environ["PATH"], "CODEX_HOME": str(home), "HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT)}


def test_read_answers_and_reaps_the_app_server(tmp_path: Path, started: list[subprocess.Popen[str]]) -> None:
    codex = install_fake_cli(tmp_path, "codex")
    assert usage_module().read(str(codex), read_env(tmp_path), 5) == SNAPSHOT
    assert ended(started)


def test_a_timer_that_cannot_start_still_ends_the_app_server(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")

    def refuse(_self: threading.Timer) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module.threading.Timer, "start", refuse)
    with pytest.raises(RuntimeError, match="^can't start new thread$"):
        module.read(str(codex), read_env(tmp_path), 5)
    assert ended(started)


class LateTimer:
    """A deadline that passes just as the answer arrives: it fires when it is cancelled."""

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.function = function

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.function()

    def join(self, timeout: float | None = None) -> None:
        pass


def test_an_answer_that_races_the_deadline_is_a_timeout(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")
    monkeypatch.setattr(module.threading, "Timer", LateTimer)
    with pytest.raises(TimeoutError):
        module.read(str(codex), read_env(tmp_path), 5)
    assert ended(started)


def test_an_app_server_that_cannot_be_reaped_fails_the_read(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")
    real_wait = POPEN.wait

    def stuck(self: subprocess.Popen[str], timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(self.args, timeout or 0)

    monkeypatch.setattr(POPEN, "wait", stuck)
    with pytest.raises(module.NotGone):
        module.read(str(codex), read_env(tmp_path), 5)
    monkeypatch.setattr(POPEN, "wait", real_wait)
    assert [real_wait(p, 10) for p in started] == [-signal.SIGKILL]      # it was killed; now reaped here
    assert ended(started)


def test_a_scratch_home_that_stays_is_no_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str],
                                                 started: list[subprocess.Popen[str]]) -> None:
    module = usage_module()
    codex = install_fake_cli(tmp_path, "codex")
    env = read_env(tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(module.tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(module.shutil, "rmtree", lambda *_a, **_k: None)
    with pytest.raises(module.ScratchKept):
        module.main([str(codex), "5"])
    assert capsys.readouterr().out == ""                   # nothing that reads as an answer
    assert ended(started)
    assert len(list(scratch.iterdir())) == 1               # the home it could not remove
```

Append to `tests/test_session_shim.py`:

```python
# --- the status mode (plan 4b E7) ---

LIMITS = {"five_hour": {"used_percentage": 42, "resets_at": 1_800_003_600}}


WALL = 1_800_000_000.0


def status(run: Path, payload: object, t: FakeTime | None = None, wall: float = WALL) -> int:
    t = t or FakeTime()
    return shim.run_status(json.dumps(payload).encode(), env_for(run), clock=t.clock, sleep=t.sleep,
                           wall=lambda: wall)


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


def test_status_resends_an_unchanged_value_before_it_could_go_stale(run: Path) -> None:
    seen: list[dict[str, object]] = []
    s = SessionServer(run / "s.sock", TOKEN, run.parent / "events.jsonl", on_event=seen.append)
    s.start()
    try:
        for at in (WALL, WALL + shim.STATUS_RESEND - 1, WALL + shim.STATUS_RESEND, WALL - 5):
            assert status(run, {"session_id": "s1", "rate_limits": LIMITS}, wall=at) == 0
        assert len(seen) == 3       # the first; once the interval had passed; after the clock went back
    finally:
        s.close()


def test_a_relaunch_sends_the_same_value_to_its_own_wsd_session(run: Path) -> None:
    seen: list[dict[str, object]] = []
    s = SessionServer(run / "s.sock", TOKEN, run.parent / "events.jsonl", on_event=seen.append)
    s.start()
    try:
        assert status(run, {"rate_limits": LIMITS}) == 0
    finally:
        s.close()
    run2 = run.parent / "r2"                    # the next generation: its own run dir and token, same $HOME
    run2.mkdir()
    (run2 / "token").write_text(TOKEN + "-2")
    (run2 / "shim.json").write_text((run / "shim.json").read_text())
    s2 = SessionServer(run2 / "s.sock", TOKEN + "-2", run.parent / "events.jsonl", on_event=seen.append)
    s2.start()
    try:
        assert status(run2, {"rate_limits": LIMITS}) == 0
        assert status(run2, {"rate_limits": LIMITS}) == 0                 # and then once only
    finally:
        s2.close()
    assert [e["rate_limits"] for e in seen] == [LIMITS, LIMITS]
    kept = (run.parent / "home" / ".hz" / shim.STATUS_FILE).read_text()
    assert TOKEN not in kept and json.loads(kept)["launch"] == shim._launch(TOKEN + "-2")


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
    def run_files(self, *, usage: bool = False, limits: bool = False) -> Mapping[str, str]:
        """Files for the run directory (read-only inside). With `usage` ((c)) or `limits` ((e)), also
        what that capability's producers need (plan 4b E5, E7)."""
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
    def run_files(self, *, usage: bool = False, limits: bool = False) -> Mapping[str, str]:
        """The limit signal is the StopFailure hook, always installed, so `limits` adds nothing."""
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
    def run_files(self, *, usage: bool = False, limits: bool = False) -> Mapping[str, str]:
        files = {TRUST_SCRIPT.name: TRUST_SOURCE.read_text()}
        if usage or limits:                # the one read gives both the windows and the limit
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
exits 0. Any failure exits 1 with nothing on stdout and only the error's type on stderr. The host
parses and validates the output. Standard library only.

A reading counts only if all of these hold: the app-server answered before its deadline (an answer
that races the deadline is a timeout), it was then ended and reaped by its own handle with the status
of its own clean exit or of the kill (anything else is ExitStatus), and the scratch home is confirmed
gone (ScratchKept). An error in ending the app-server or removing the scratch home replaces any
earlier error: the cleanup's failure is the one reported.

Usage: python3 -I codex_usage.py <codex binary> [limit seconds]
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

LIMIT_SECONDS = 30.0
END_SECONDS = 5.0           # how long a killed app-server may take to be reaped
MAX_ANSWER = 1 << 20        # characters in one line of the app-server's output
ENDED = (0, -signal.SIGKILL)  # its own clean exit, or the kill


class AnswerError(Exception):
    """The app-server answered with an error, or with something that isn't a JSON-RPC object."""


class ClosedOutput(Exception):
    """The app-server's output ended before it answered."""


class ExitStatus(Exception):
    """The app-server ended with a status that is neither its clean exit nor the kill."""


class NotGone(Exception):
    """The app-server could not be reaped after the kill."""


class NoRateLimits(Exception):
    """The answer holds no `rateLimits` object."""


class ScratchKept(Exception):
    """The scratch home is still there after its removal."""


def main(argv):
    codex = argv[0]
    limit = float(argv[1]) if len(argv) > 1 else LIMIT_SECONDS
    home = tempfile.mkdtemp(prefix="hz-usage-")
    try:
        os.symlink(os.path.join(os.environ["CODEX_HOME"], "auth.json"), os.path.join(home, "auth.json"))
        limits = read(codex, dict(os.environ, CODEX_HOME=home), limit)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        if os.path.lexists(home):
            raise ScratchKept("the scratch home is still there")
    if not isinstance(limits, dict):
        raise NoRateLimits("the answer has no rate limits")
    print(json.dumps(limits))
    return 0


def read(codex, env, limit):
    """The `rateLimits` of the app-server run with `env` (its CODEX_HOME included), or None if the
    answer has none. Raises on any failure, TimeoutError past `limit` seconds. The app-server is ended
    in a `finally` whatever happens, the timer that bounds it included. Shared with the host's trusted
    read (sandbox/hostread.py), which loads this file."""
    proc = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env)
    expired = threading.Event()

    def expire():
        expired.set()
        proc.kill()

    timer = threading.Timer(limit, expire)
    started = False
    result = None
    try:
        try:
            timer.start()
            started = True
            result = _converse(proc)
        finally:
            timer.cancel()
            if started:
                timer.join()            # from here on, `expired` can't change
            if expired.is_set():
                raise TimeoutError("codex app-server did not answer within its limit")
    finally:
        _end(proc)
    return result.get("rateLimits") if isinstance(result, dict) else None


def _converse(proc):
    stdin, stdout = proc.stdin, proc.stdout
    if stdin is None or stdout is None:
        raise ClosedOutput("codex app-server has no pipes")

    def send(message):
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()

    def call(id_, method, params):
        send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        while True:
            line = stdout.readline(MAX_ANSWER)
            if not line:
                raise ClosedOutput("codex app-server closed its output")
            if not line.endswith("\n"):
                raise AnswerError("codex app-server sent an overlong line")
            message = json.loads(line)
            if not isinstance(message, dict):
                raise AnswerError("codex app-server sent something other than an object")
            if message.get("id") == id_:
                if "error" in message or "result" not in message:
                    raise AnswerError("codex app-server returned an error")
                return message["result"]

    call(0, "initialize", {"clientInfo": {"name": "heterodyne", "version": "1"}})
    send({"jsonrpc": "2.0", "method": "initialized"})
    return call(1, "account/rateLimits/read", None)


def _end(proc):
    """Kill the app-server by its own handle, reap it and close its pipes. NotGone if it isn't reaped
    within END_SECONDS; ExitStatus unless it ended by its own clean exit or the kill."""
    try:
        proc.kill()
        try:
            status = proc.wait(END_SECONDS)
        except subprocess.TimeoutExpired:
            raise NotGone("codex app-server is still running") from None
    finally:
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
    if status not in ENDED:
        raise ExitStatus("codex app-server ended abnormally")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure is "no reading", decided on the host
        print(f"codex_usage: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
```

In `src/heterodyne/session/shim.py`, add `import hashlib`, update the module docstring's first paragraph to name `hook|request|status`, and add before `json_depth` (the helpers call `json_depth` only at run time, so the order is free):

```python
STATUS_FILE = "status.json"     # in $HOME/.hz: what wsd last accepted, from which launch, and when
STATUS_WAIT = 2.0               # the status line redraws often: never hold it longer than this
STATUS_RESEND = 30.0            # an unchanged value is sent again this often (wall seconds): well inside
                                # the smallest stale window wsd allows (usage.stale_minutes >= 1)


def _launch(token: str) -> str:
    """Which launch the status was sent to, without keeping its token: each generation has its own."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _recent(sent: object, now: float) -> bool:
    if isinstance(sent, bool) or not isinstance(sent, int | float):
        return False
    return 0 <= now - sent < STATUS_RESEND          # a clock that went back is never recent


def _status_dir(env: Mapping[str, str]) -> int | None:
    """$HOME/.hz, opened without following a link; None if $HOME isn't absolute."""
    home = env.get("HOME")
    if not home or not Path(home).is_absolute():
        return None
    folder = Path(home) / ".hz"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _last_status(env: Mapping[str, str]) -> dict[str, Any] | None:
    try:
        dir_fd = _status_dir(env)
        if dir_fd is None:
            return None
        try:
            fd = os.open(STATUS_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        try:
            raw = os.read(fd, MAX_LINE + 1) if stat.S_ISREG(os.fstat(fd).st_mode) else b""
        finally:
            os.close(fd)
        data: Any = json.loads(raw) if raw and len(raw) <= MAX_LINE and json_depth(raw) <= MAX_DEPTH else None
    except (OSError, ValueError):
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


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
               sleep: Sleep = time.sleep, wall: Clock = time.time) -> int:
    """Claude's status line (plan 4b E7): send the input's `rate_limits` as a StatusLine hook event and
    print nothing. A value is sent when it differs from what this launch's wsd last accepted, when it
    went to another launch (a relaunch shares $HOME), or when STATUS_RESEND has passed since, so an
    unchanged reading never goes stale in wsd's cache. A value wsd didn't accept is sent again on the
    next redraw. Always exits 0: the status line decides nothing."""
    try:
        payload: Any = json.loads(raw) if len(raw) <= MAX_LINE and json_depth(raw) <= MAX_DEPTH else None
        if not isinstance(payload, dict):
            return 0
        fields = cast(dict[str, Any], payload)
        limits: Any = fields.get("rate_limits")
        if not isinstance(limits, dict):
            return 0                    # the first call, before any API response, has none (S7)
        text = json.dumps(limits, sort_keys=True)
        found = _config(env)
        if found is None:
            return 0
        sock, token, cfg = found
        launch, now = _launch(token), wall()
        last = _last_status(env)
        if (last is not None and last.get("launch") == launch and last.get("limits") == text
                and _recent(last.get("sent"), now)):
            return 0
        event = {"hook_event_name": "StatusLine", "session_id": fields.get("session_id"),
                 "rate_limits": limits}
        message = _bounded(lambda p: {"token": token, "type": "hook_event", "payload": p}, event)
        if message is None:
            return 0
        reply = _ask(sock, message, min(_wait(cfg), STATUS_WAIT), clock, sleep)
        if reply is not None and reply.get("ok") is True:
            _save_status(env, json.dumps({"launch": launch, "limits": text, "sent": now}))
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

In `tests/fakes/fake_agent_cli.py`, add to the module docstring's `codex app-server` (stdio) item: "and `account/rateLimits/read` with `HZ_FAKE_RATE_LIMITS` (a snapshot), after an `account/rateLimits/updated` notification; with no `auth.json` in `$CODEX_HOME` it answers -32600, as codex 0.160 does with no login. `HZ_FAKE_RATE_FAULT` is `hang` (never answers), `error`, `garbage` (a line that isn't JSON), `close` (closes its output and waits) or `exit` (exits 3 without answering). With `HZ_FAKE_PID_FILE` set, it first writes its PID there, so a test can prove it is gone." Then replace `stdio_server`:

```python
def rate_limits(msg: dict[str, Any]) -> str | None:
    """The answer line to account/rateLimits/read, or None to never answer."""
    fault = os.environ.get("HZ_FAKE_RATE_FAULT")
    if fault == "hang":
        return None
    if fault == "garbage":
        return "not json"
    if fault == "close":
        os.close(1)
        return None
    if fault == "exit":
        os._exit(3)
    if fault == "error" or not (Path(os.environ["CODEX_HOME"]) / "auth.json").is_file():
        return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {
            "code": -32600, "message": "codex account authentication required to read rate limits"}})
    snapshot = json.loads(os.environ.get("HZ_FAKE_RATE_LIMITS", "null"))
    return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"rateLimits": snapshot}})


def stdio_server() -> None:
    if pid_file := os.environ.get("HZ_FAKE_PID_FILE"):
        Path(pid_file).write_text(str(os.getpid()))
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
                sys.stdin.read()        # hang (or, its output closed, wait) until killed
                return
            print(answer, flush=True)
            continue
        else:
            continue                    # a notification
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_agents_usage.py tests/test_session_shim.py tests/test_agents_adapters.py tests/test_sandbox_runtime.py -q`
Expected: PASS. The `hang` case passes in about 2 seconds (its limit), never 60: a timeout of the test's own `subprocess.run` is a failure. Each fault is named by its own diagnostic, so a fault that fails for another reason (a missing executable's `FileNotFoundError` among them) fails its test. Every case that started an app-server proves it was reaped: by its PID for the script, and by its handle's `returncode` and closed pipes in this process.

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
  - Plan 4's `check_credentials`, unchanged (Task 3 is retired). With a named account chosen, the default login is another account's, so its directory joins the protected set and no bind may reach into it (r16 §7, no exception).
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
    account = Account(name, adapter, configured, login_dir, files, credential_key(adapter, files))
    named = {**current.named, (adapter, name): account}
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
            fresh = None if adapter.assigns_id else ids.handoff_session(spec.session_key, spec.generation)
            return False, fresh
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
  - `runtime.DIAG_USAGE_READ`: the warning a read that gave no reading logs, as `"<DIAG_USAGE_READ>: <reason>"`. The reason is one of:
    - the script's own `codex_usage: <type>` name;
    - `exit <status>` for any other nonzero status (127 included);
    - `unparsed` for exit 0 with output that isn't a reading;
    - the exception's type name when the exec itself failed (`BackendError` for its timeout).

    The line never carries the read's output.
  - `FakeBackend.host_env(name) -> dict[str, str]`: the environment the fake runs a sandbox's commands with.
  - `sandbox_env.with_caps(rig, adapter, caps)`.

Which report becomes which signal:

| Report | Capability | Signal |
|---|---|---|
| a status line's or a read's windows | (c) `in_session_usage` | `UsageSeen(key, generation, windows)` |
| `StopFailure` with `error == "rate_limit"`, or a read whose `rateLimitReachedType` isn't null | (e) `limit_signal` | `LimitSeen(key, generation, hint)` |

Each signal needs only its own capability. Codex's in-sandbox read gives both, so it is installed and polled when either (c) or (e) is on, and each signal it yields is still emitted only under its own capability (Codex plan 4b r2).

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
            fed = json.dumps({"session_id": session, "rate_limits": json.loads(limits or "null")})
            subprocess.run(host(status), shell=True, check=True, input=fed.encode())  # noqa: S602
```

`check=True` is deliberate. The shim's status mode always exits 0, so any other status (127 for a command that can't run among them) is a broken status line, and the fake TUI dies on it. The test waiting on that turn then fails, rather than passing on a status line that never ran.

In `tests/sandbox_env.py`, import `Capabilities` from `heterodyne.config.capabilities`, and add after `with_named`:

```python
def with_caps(rig: "RuntimeRig", adapter: str, caps: Capabilities) -> None:
    """Give `adapter` these capabilities in the runtime's accounts, keeping any named accounts."""
    current = rig.runtime.c.accounts
    assert isinstance(current, ConfiguredAccounts)
    rig.runtime.c = replace(rig.runtime.c, accounts=ConfiguredAccounts(
        ADAPTERS, current.env, current.named, {**current.capabilities, adapter: caps}))
```

In `tests/test_sandbox_runtime.py`, import `logging`, `Sequence` from `collections.abc`, `from heterodyne.sandbox import runtime as runtime_module`, `from heterodyne.sandbox.backend import ExecResult`, `from heterodyne.sandbox.runtime import DIAG_USAGE_READ` (beside `MAX_QUEUED`), `LaunchSpec` beside `LaunchFailed` from `heterodyne.wsd.runtime`, `Capabilities`, `from sandbox_env import with_caps`, `from heterodyne.agents.codex import IN_SESSION_SOURCE`, `from heterodyne.agents.claude_code import STATUS_SOURCE`, `from heterodyne.sandbox.runtime import MAX_QUEUED`, `from heterodyne.wsd.runtime import LimitSeen, Signal, UsageSeen` and `from heterodyne.wsd.usage import Observation`, then add:

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


@pytest.mark.parametrize(("caps", "kinds"), [(LIMITS, [LimitSeen]), (USAGE, [UsageSeen])])
def test_the_codex_read_serves_each_capability_alone(rig: RuntimeRig, caps: Capabilities,
                                                     kinds: list[type]) -> None:
    """(e) without (c) still installs and polls the read; each signal comes only under its own one."""
    with_caps(rig, "codex", caps)
    rig.backend.env_extra["HZ_FAKE_RATE_LIMITS"] = json.dumps(SNAPSHOT)
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    assert (rig.runtime.layout(spec.session_key).run(1) / "codex_usage.py").exists()
    codex_login_inside(rig, spec.session_key)
    assert [type(sig) for sig in rig.runtime.signals("alpha")] == kinds and reads(rig) == 1


def read_failures(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(DIAG_USAGE_READ)]


def pid_gone(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text()), 0)
    except ProcessLookupError:
        return True
    return False


def failing_read(rig: RuntimeRig, fault: str | None, login: bool = True) -> tuple[LaunchSpec, Path]:
    """A Codex session with (c) and (e) on, whose read will meet `fault`. The PID file is emptied after
    the launch, whose own app-server writes it too, so only the read's app-server can fill it."""
    with_caps(rig, "codex", BOTH)
    pid_file = rig.root / "read.pid"
    rig.backend.env_extra |= {"HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT), "HZ_FAKE_PID_FILE": str(pid_file)}
    if fault is not None:
        rig.backend.env_extra["HZ_FAKE_RATE_FAULT"] = fault
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    if login:
        codex_login_inside(rig, spec.session_key)
    pid_file.unlink(missing_ok=True)
    return spec, pid_file


@pytest.mark.parametrize(("fault", "login", "reason"), [
    ("error", True, "AnswerError"), ("garbage", True, "JSONDecodeError"), ("close", True, "ClosedOutput"),
    ("exit", True, "ExitStatus"), (None, False, "AnswerError")])
def test_a_read_that_fails_reports_nothing_and_names_why(rig: RuntimeRig, caplog: pytest.LogCaptureFixture,
                                                         fault: str | None, login: bool, reason: str) -> None:
    caplog.set_level(logging.WARNING, logger=runtime_module.__name__)
    _, pid_file = failing_read(rig, fault, login)
    assert rig.runtime.signals("alpha") == []
    assert reads(rig) == 1                                      # it ran and failed: not skipped
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: {reason}"]
    assert pid_file.is_file() and pid_gone(pid_file)           # its app-server started, and was reaped


def test_a_read_past_its_bound_is_a_failure_and_its_server_ends(rig: RuntimeRig,
                                                                caplog: pytest.LogCaptureFixture,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.WARNING, logger=runtime_module.__name__)
    monkeypatch.setattr(runtime_module, "USAGE_SECONDS", 2)
    _, pid_file = failing_read(rig, "hang")
    assert rig.runtime.signals("alpha") == []
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: BackendError"]
    assert pid_file.is_file()                                   # the read's own server started
    wait_for(lambda: pid_gone(pid_file))       # the script was killed: its server reads EOF and exits


def test_a_read_whose_script_is_missing_is_its_exit_status(rig: RuntimeRig,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=runtime_module.__name__)
    spec, pid_file = failing_read(rig, None)
    (rig.runtime.layout(spec.session_key).run(1) / "codex_usage.py").unlink()
    assert rig.runtime.signals("alpha") == []
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: exit 2"]      # python3 can't open the file
    assert not pid_file.exists()


@pytest.mark.parametrize(("status", "reason"), [(127, "exit 127"), (1, "exit 1"), (0, "unparsed")])
def test_only_a_clean_exit_with_a_reading_is_a_reading(rig: RuntimeRig, caplog: pytest.LogCaptureFixture,
                                                       monkeypatch: pytest.MonkeyPatch, status: int,
                                                       reason: str) -> None:
    """127 is a command that never ran, and its output is no reading, even one that parses."""
    caplog.set_level(logging.WARNING, logger=runtime_module.__name__)
    failing_read(rig, None)
    out = json.dumps(SNAPSHOT).encode() if status else b"not a reading"

    def answered(name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
                 timeout: float) -> ExecResult:
        return ExecResult(status, out, b"")

    monkeypatch.setattr(rig.backend, "exec", answered)
    assert rig.runtime.signals("alpha") == []
    assert read_failures(caplog) == [f"{DIAG_USAGE_READ}: {reason}"]


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
usage_poll_seconds = 600                # how often a session's in-sandbox usage read runs, when (c) or (e) is on
```

In `src/heterodyne/sandbox/settings.py`, add `"usage_poll_seconds"` to `HOST_KEYS`, the field after `lock_wait_seconds`:

```python
    usage_poll_seconds: int                  # E5: the in-sandbox usage read's interval, when (c) or (e) is on
```

and in `from_config`, after `lock_wait_seconds=...`:

```python
        usage_poll_seconds=_int(sb, "usage_poll_seconds", 60, 86_400),
```

In `src/heterodyne/sandbox/runtime.py`:

- import `logging`, `re` and `threading`; `ExecResult` beside `Backend` from `heterodyne.sandbox.backend`; `from heterodyne.agents.base import UsageReport` (beside the existing names); `from heterodyne.config.capabilities import NONE, Capabilities`; and `LimitSeen`, `Signal`, `UsageSeen` from `heterodyne.wsd.runtime`;
- add after `RECORD_LOST`:

```python
MAX_QUEUED = 256        # reports a workstream keeps between drains: a flooding session keeps only its newest
USAGE_SECONDS = 45      # the in-sandbox usage read's exec bound (codex_usage.py's own limit is 30)
DIAG_USAGE_READ = "an in-sandbox usage read gave no reading"
READ_REASON = re.compile(rb"codex_usage: ([A-Za-z]+)\n")       # the script's one stderr line (Task 5)

log = logging.getLogger(__name__)
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
        """E5: run the session's in-sandbox usage read if (c) or (e) is on and it is due; `_report`
        emits each signal only under its own capability. A read that fails reports nothing and is not
        retried before the next interval."""
        adapter = self.c.adapters.get(rec.adapter)
        caps = self._caps(rec.profile)
        if adapter is None or not (caps.in_session_usage or caps.limit_signal):
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
        except (LaunchFailed, BackendError, BackendUnavailable) as exc:
            log.warning("%s: %s", DIAG_USAGE_READ, type(exc).__name__)
            return
        report = adapter.usage_from_read(r.stdout) if r.returncode == 0 else None
        if report is None:
            log.warning("%s: %s", DIAG_USAGE_READ, _read_reason(r))
            return
        self._report(rec.ws, rec.key, rec.generation, caps, report)
```

and, at module level after `SandboxRuntime`:

```python
def _read_reason(r: ExecResult) -> str:
    """Why a read gave no reading, from its status and the script's own diagnostic only: never its
    output. Only exit 0 is a reading, so 127 (nothing ran) is a failure like any other."""
    if r.returncode == 0:
        return "unparsed"
    found = READ_REASON.fullmatch(r.stderr)
    return found.group(1).decode() if found and r.returncode == 1 else f"exit {r.returncode}"
```

- in `_start`, read the capabilities once, before the run files are written, and use them for the files and the server:

```python
        caps = self._caps(spec.profile)
        files = {shim.TOKEN_FILE: token, SHIM_SOURCE.name: SHIM_SOURCE.read_text(),
                 shim.CONFIG_FILE: json.dumps(shim_config), REQUEST_INSIDE.name: shim.REQUEST_WRAPPER,
                 **adapter.run_files(usage=caps.in_session_usage, limits=caps.limit_signal),
                 **self.selftest.files()}
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
Expected: PASS. In the failed-read tests, three things prove the read ran and failed for the reason named:
- `reads(rig) == 1` (a skipped read would also pass the `== []` check);
- the exact diagnostic: a fault that fails for another reason fails its test, a missing executable's `exit 127` or `FileNotFoundError` among them;
- the PID file, and that its process is gone.

The timeout case takes about 2 s. A test that waits on `wait_for`'s own bound fails, never passes.

Then: `timeout 600 uv run pyright src tests/test_sandbox_runtime.py tests/fakes` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/sandbox/runtime.py src/heterodyne/sandbox/settings.py src/heterodyne/defaults/defaults.toml \
        tests/fakes/fake_agent_cli.py tests/fakes/fake_backend.py tests/sandbox_env.py \
        tests/test_sandbox_runtime.py tests/test_sandbox_settings.py
git commit -m "plan4b T7: the runtime's usage and limit signals, gated by capability"
```

## Task 8: the trusted host-side read

D9 (d) for Codex (E4): a short-lived `codex app-server` on the host, over stdio, in a scratch `CODEX_HOME` holding a copy of the account's login. It runs under the account's D8 lock, while the key is the pinned one, and only while the token has at least `read_margin_seconds` left. Nothing is written to the account's login: a copy the read changed is dropped, with a warning. The scratch home is removed and confirmed gone, and a read a crash interrupted is cleaned up when wsd next starts. Claude has no host read (S7), so a Claude profile always reads None. The read finds its CLI as plan 4's runtime does (`[adapters.codex] binary`, `adapter.locate`, then the pin). r16 requires the selected managed install, verified, for every host-side use (§7 Managed installs): plan 4c makes that change here, and (d) stays off until it has (Plan 4c, above).

**Files:**
- Create: `src/heterodyne/sandbox/hostread.py`
- Modify: `src/heterodyne/sandbox/build.py` (`build_usage_reader`)
- Modify: `src/heterodyne/wsd/daemon.py` (`assemble(..., usage_reader=)`), `src/heterodyne/wsd/cli.py` (`run(..., usage_reader=)`, `wsd_main`)
- Modify: `tests/fakes/fake_agent_cli.py` (`HZ_FAKE_ROTATE`)
- Test: `tests/test_sandbox_hostread.py` (new), `tests/test_sandbox_build.py`, `tests/test_wsd_daemon.py`

**Interfaces:**
- Consumes:
  - `wsd.usage.TrustedRead`, `UsageReader`, `NoUsageReader`, `Deps.usage_reader` (Task 1).
  - `wsd.credlock.credential_lock`, `CredentialBusy`; `WsdSettings.credlock_dir` (Task 2).
  - `codex.parse_rate_limits`, `codex.HOST_SOURCE`, `codex.USAGE_SOURCE` (Task 5).
  - `codex_usage.read(codex, env, limit)` (Task 5). It raises `TimeoutError`, `AnswerError`, `ClosedOutput`, `ExitStatus`, `NotGone`, `json.JSONDecodeError`, or the `OSError` of a missing executable. Each is logged here by its type name.
  - `SandboxSettings.lock_wait_seconds` (Task 6).
  - `Accounts.adapter`, `current_key`, `login_paths` (exist).
- Produces:
  - `sandbox.hostread.HostUsageReader(accounts, binary, path, credlocks, scratch_root, lock_wait_seconds, *, env, read_margin_seconds=300, limit_seconds=30.0, clock=utc_now, wait_clock=time.monotonic, sleep=time.sleep)`, a `UsageReader`.
  - Log texts with no path, key or token in them:
    - `hostread.DIAG_ROTATED_DROPPED` and `hostread.DIAG_SCRATCH_KEPT`, logged alone;
    - `hostread.DIAG_RECOVERED` and `hostread.DIAG_RECOVERY_KEPT`, logged alone by `recover`;
    - `hostread.DIAG_READ_FAILED`, logged as `"<DIAG_READ_FAILED>: <exception type name>"` (a warning);
    - `hostread.DIAG_NOT_READ`, logged as `"<DIAG_NOT_READ>: <reason>"` (info) when the read stops before it starts anything. The reason is `NOT_PINNED_KEY`, `TOO_CLOSE`, `NOT_AT_PIN`, or the `CredentialBusy` text (`LOCK_BUSY`, `LOCK_FAILED` or `BAD_KEY`).
  - `hostread.NoRateLimits`: the failure of an answer that held no `rateLimits`.
  - `hostread.recover(scratch_root: Path) -> None`: kills the app-servers of any `read-*` home left under `scratch_root` and, once none is confirmed left, removes those homes. It never raises. `cli.run` calls it with `s.state_dir / "hostread"` first thing inside the `try` whose `finally` releases the instance lock, whichever reader is configured.
  - `sandbox.build.build_usage_reader(s: WsdSettings, env: Mapping[str, str]) -> UsageReader`.
  - `daemon.assemble(..., usage_reader: UsageReader | None = None)` and `cli.run(..., *, usage_reader: UsageReader | None = None)`. None means `NoUsageReader()`.

The read's steps, all inside one `credential_lock(credlocks, credential_key, ...)`:

1. The profile's adapter must be `codex`; otherwise None, with nothing logged (a Claude profile is never a failure).
2. `accounts.current_key("codex", account)` must equal `credential_key`; otherwise `NOT_PINNED_KEY`.
3. The login file's real path is read without following a final link, at most `MAX_LOGIN` bytes, and must be a regular file.
4. Its access token must be valid for `read_margin_seconds` more; otherwise `TOO_CLOSE`. Codex refreshes only an expired token (S7 (4)), so the read is not expected to write the login. The CLI must be at its pin; otherwise `NOT_AT_PIN`.
5. The scratch home is made under `scratch_root` (0700): `auth.json` is the copy, written 0600. Every byte is written (a short `os.write` is continued) and flushed; on any failure the file is unlinked before the error propagates.
6. `codex_usage.read(binary, env | {"CODEX_HOME": scratch}, limit_seconds)` gives the `rateLimits`, parsed with `parse_rate_limits(..., HOST_SOURCE)`. An answer with none raises `NoRateLimits`.
7. Once the answer is parsed, the copy is compared with the original bytes. In a `finally`, the scratch home is removed and checked with `os.path.lexists`. A scratch home that is still there makes the read None, with `DIAG_SCRATCH_KEPT`. A copy that changed is dropped with `DIAG_ROTATED_DROPPED`, and the reading is still returned: it was taken under the lock, from the pinned key. The account's login is never written.

A crash (wsd killed, the host losing power) can interrupt a read after step 5. The copy stays under `scratch_root`, and the app-server, once orphaned, outlives its timer. wsd is the only reader, and it holds its instance lock (`gate.instance_lock`) for its whole life, so at its next start every `read-*` home there is a crashed read's. `recover` runs before anything else can start a read:

- It looks in `/proc` for the processes whose environment holds `CODEX_HOME=<that home>`. Each one is opened as a pidfd first, then its `environ` is read, then signal 0 is sent through the pidfd: if that succeeds, the environment was read from the process the pidfd holds, not from one that reused its PID. A process of another user can't be read and is skipped (it can't have been started by this wsd).
- A process that is gone, or whose `environ` is another user's, is skipped. Any other failure (a pidfd that can't be opened, say at the file limit, or `/proc` that can't be listed) makes the scan incomplete: it is not taken as "none left".
- Each one found is sent SIGKILL through its pidfd. The scan repeats until a complete scan finds none, for at most `RECOVER_SECONDS` (10). A killed orphan is reaped by init or the session's subreaper; its zombie has an empty `environ`, so it is no longer found. Every pidfd is closed, whatever fails.
- The homes are the only record of which processes to look for. They are removed (`rmtree`, checked with `lexists`) only once a complete scan finds no process left. An incomplete scan, a failed kill or a process that outlives the deadline keeps every home and logs `DIAG_RECOVERY_KEPT`, so the next start looks again. `DIAG_RECOVERED` is logged once all the homes are gone. Nothing is logged when there was nothing to recover.

Any failure is None. The log records only the exception's type name, never its text, which may hold a path. Every None but the Claude profile's logs exactly one line, so each test names the reason it expects and a read that stopped for another reason fails it.

- [ ] **Step 1: Write the failing tests**

In `tests/fakes/fake_agent_cli.py`, add to the docstring's stdio item: "`HZ_FAKE_ROTATE=<text>` replaces `$CODEX_HOME/auth.json` with `<text>` (a temporary file, then a rename, as a CLI saving a refreshed login would) before it answers `account/rateLimits/read`." In `rate_limits(msg)`, add as its first lines:

```python
    if rotated := os.environ.get("HZ_FAKE_ROTATE"):
        auth = Path(os.environ["CODEX_HOME"]) / "auth.json"
        (auth.parent / "auth.json.tmp").write_text(rotated)
        os.replace(auth.parent / "auth.json.tmp", auth)
```

Create `tests/test_sandbox_hostread.py`:

```python
"""Plan 4b Task 8: the trusted host-side read (D9 (d), E4), against the fake app-server."""

import errno
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sandbox_env import fake_jwt, install_fake_cli
from wsd_env import Clock, accounts_at

from heterodyne.agents.codex import HOST_SOURCE
from heterodyne.config import ConfigError
from heterodyne.sandbox import hostread
from heterodyne.sandbox.hostread import (
    DIAG_NOT_READ,
    DIAG_READ_FAILED,
    DIAG_RECOVERED,
    DIAG_RECOVERY_KEPT,
    DIAG_ROTATED_DROPPED,
    DIAG_SCRATCH_KEPT,
    NOT_PINNED_KEY,
    TOO_CLOSE,
    HostUsageReader,
    recover,
)
from heterodyne.wsd.accounts import ConfiguredAccounts
from heterodyne.wsd.credlock import LOCK_BUSY, credential_lock
from heterodyne.wsd.usage import Observation, TrustedRead

DAY = 86_400
RESET = 1_800_003_600
SNAPSHOT = {"limitId": "codex", "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": RESET}}
WANTED = TrustedRead((Observation("codex:codex:300m", "5h", 100, RESET, HOST_SOURCE),), True, RESET)
POPEN = subprocess.Popen        # the class itself, before a test wraps or patches it


class Rig:
    def __init__(self, tmp_path: Path, **env: str) -> None:
        self.clock = Clock()
        self.home = tmp_path / "home"
        self.login = self.home / ".codex" / "auth.json"
        self.login.parent.mkdir(parents=True)
        self.original = json.dumps({"tokens": {"access_token": fake_jwt(self.clock() + DAY)}})
        self.login.write_text(self.original)
        self.login.chmod(0o600)
        self.accounts = accounts_at(self.home)
        self.key = self.accounts.current_key("codex", "default")
        self.codex = install_fake_cli(tmp_path / "i", "codex")
        self.scratch = tmp_path / "hostread"
        self.credlocks = tmp_path / "k"
        self.pid_file = tmp_path / "read.pid"
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
                    "HZ_FAKE_PID_FILE": str(self.pid_file), "HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT),
                    **env}
        self.reader = HostUsageReader(self.accounts, str(self.codex), os.environ["PATH"], self.credlocks,
                                      self.scratch, 1, env=self.env, limit_seconds=5, clock=self.clock)

    def read(self, profile: str = "p-two", key: str | None = None) -> TrustedRead | None:
        return self.reader.read(profile, "default", self.key if key is None else key)

    def scratch_empty(self) -> bool:
        return not self.scratch.exists() or list(self.scratch.iterdir()) == []

    def server_gone(self) -> bool:
        """The read's app-server started and is gone. It was this process's child, so a process that
        was killed but not reaped would still answer signal 0 as a zombie."""
        try:
            os.kill(int(self.pid_file.read_text()), 0)
        except ProcessLookupError:
            return True
        return False


@pytest.fixture
def logged(caplog: pytest.LogCaptureFixture) -> Callable[[], list[str]]:
    """Every line hostread logs, info and up, in order."""
    caplog.set_level(logging.INFO, logger=hostread.__name__)
    return lambda: [r.getMessage() for r in caplog.records if r.name == hostread.__name__]


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[str]]:
    """Every app-server the read starts, recorded so a test can reap it by its own handle."""
    made: list[subprocess.Popen[str]] = []

    def recording(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        proc = POPEN(*args, **kwargs)
        made.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", recording)
    return made


def test_a_read_gives_the_accounts_windows_and_leaves_nothing(tmp_path: Path,
                                                             logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    assert rig.read() == WANTED
    assert rig.scratch_empty() and rig.server_gone()
    assert rig.login.read_text() == rig.original
    assert logged() == []


def test_a_claude_profile_has_no_host_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    assert rig.read("p-one", "ck1-" + "0" * 32) is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()      # nothing was started
    assert logged() == []


def test_a_key_that_changed_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    assert rig.read(key="ck1-" + "0" * 32) is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()
    assert logged() == [f"{DIAG_NOT_READ}: {NOT_PINNED_KEY}"]


def test_a_login_too_close_to_expiry_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    rig.login.write_text(json.dumps({"tokens": {"access_token": fake_jwt(rig.clock() + 299)}}))
    assert rig.read() is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()
    assert logged() == [f"{DIAG_NOT_READ}: {TOO_CLOSE}"]


def test_a_busy_lock_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    with credential_lock(rig.credlocks, rig.key, 0):
        assert rig.read() is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()
    assert logged() == [f"{DIAG_NOT_READ}: {LOCK_BUSY}"]
    assert rig.read() == WANTED                                 # released


def test_a_refreshed_login_is_dropped_and_never_written(tmp_path: Path,
                                                         logged: Callable[[], list[str]]) -> None:
    rotated = json.dumps({"tokens": {"access_token": fake_jwt(Clock()() + 2 * DAY)}})
    rig = Rig(tmp_path, HZ_FAKE_ROTATE=rotated)
    before = rig.login.stat()
    assert rig.read() == WANTED                                 # taken under the lock, from the pinned key
    after = rig.login.stat()
    assert rig.login.read_text() == rig.original
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert [p.name for p in rig.login.parent.iterdir()] == ["auth.json"]       # nothing written beside it
    assert logged() == [DIAG_ROTATED_DROPPED]
    assert rig.scratch_empty() and rig.server_gone()


def test_short_writes_still_copy_the_whole_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    real_write = os.write
    copied: list[bytes] = []

    def one_byte(fd: int, data: bytes) -> int:
        copied.append(bytes(data[:1]))
        return real_write(fd, data[:1])

    monkeypatch.setattr(hostread.os, "write", one_byte)
    assert rig.read() == WANTED
    assert b"".join(copied) == rig.original.encode()            # one byte at a time, every byte
    assert logged() == []
    assert rig.scratch_empty() and rig.server_gone()


@pytest.mark.parametrize("call", ["write", "fsync"])
def test_a_copy_that_fails_leaves_no_file_and_starts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            logged: Callable[[], list[str]], call: str) -> None:
    rig = Rig(tmp_path)

    def fails(*_a: object) -> int:
        raise OSError("injected")

    monkeypatch.setattr(hostread.os, call, fails)
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: OSError"]
    assert rig.scratch_empty() and not rig.pid_file.exists()    # the copy unlinked, its home removed
    assert rig.login.read_text() == rig.original
    assert [p.name for p in rig.login.parent.iterdir()] == ["auth.json"]


CRASHING_READER = """
import os, signal, sys
sys.path[:0] = [sys.argv[1]]
from test_sandbox_hostread import Rig
from heterodyne.sandbox import hostread
from pathlib import Path
rig = Rig(Path(sys.argv[2]), HZ_FAKE_RATE_FAULT="hang")
rig.reader.limit_seconds = 600
if sys.argv[3] == "copied":
    hostread._usage_module = lambda: os.kill(os.getpid(), signal.SIGKILL)
rig.read()
"""


def wait_for(done: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}/environ").read_bytes() != b""     # a zombie, not yet reaped, has none


@pytest.mark.parametrize("crash", ["copied", "started"])
def test_a_crashed_read_is_recovered_at_the_next_start(tmp_path: Path, logged: Callable[[], list[str]],
                                                       crash: str) -> None:
    """The reader is killed after the copy is written, or once its app-server runs. Its home, holding
    the copy, outlives it; so does the orphaned app-server. `recover` finds and removes both."""
    rig = Rig(tmp_path / "rig")
    reader = POPEN([sys.executable, "-c", CRASHING_READER, str(Path(__file__).parent), str(tmp_path / "crash"),
                    crash], env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    scratch = tmp_path / "crash" / "hostread"
    pid_file = tmp_path / "crash" / "read.pid"
    if crash == "started":
        assert wait_for(pid_file.exists)
        assert wait_for(lambda: pid_file.read_text().strip() != "")
        reader.kill()
    assert reader.wait(30) == -signal.SIGKILL
    homes = list(scratch.iterdir())
    assert len(homes) == 1 and (homes[0] / "auth.json").exists()            # the crash left the copy
    orphan = int(pid_file.read_text()) if crash == "started" else None
    if orphan is not None:
        assert alive(orphan)                                    # its timer died with the reader
    recover(scratch)
    assert list(scratch.iterdir()) == []
    if orphan is not None:
        assert wait_for(lambda: not alive(orphan))
    assert logged() == [DIAG_RECOVERED]
    assert rig.read() == WANTED                                 # another rig's reads are untouched


def test_recover_leaves_other_processes_and_reports_a_kept_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                logged: Callable[[], list[str]]) -> None:
    scratch = tmp_path / "hostread"
    (scratch / "read-abc").mkdir(parents=True)
    bystander = POPEN([sys.executable, "-c", "import time; time.sleep(60)"],      # a near miss: not killed
                      env={**os.environ, "CODEX_HOME": str(scratch / "read-abc") + "-not"})
    try:
        monkeypatch.setattr(hostread.shutil, "rmtree", lambda *_a, **_k: None)
        recover(scratch)
        assert logged() == [DIAG_RECOVERY_KEPT]
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(10)
    recover(tmp_path / "missing")                               # nothing there: nothing done, nothing logged
    assert logged() == [DIAG_RECOVERY_KEPT]


def open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


@pytest.mark.parametrize("fault", ["pidfd", "proc", "signal", "deadline"])
def test_an_unconfirmed_recovery_keeps_the_homes_and_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  logged: Callable[[], list[str]],
                                                                  fault: str) -> None:
    """A pidfd that can't be opened, `/proc` that can't be listed, a kill that fails, or an app-server
    that outlives the deadline: recover can't confirm the app-server is gone. It keeps the home, the
    only way the next start can find that app-server, leaks no pidfd and raises nothing. The next
    recover, with the fault gone, finishes the job."""
    scratch = tmp_path / "hostread"
    home = scratch / "read-abc"
    home.mkdir(parents=True)
    orphan = POPEN([sys.executable, "-c", "import time; time.sleep(60)"], env={**os.environ, "CODEX_HOME": str(home)})
    try:
        real_scandir, real_signal = os.scandir, signal.pidfd_send_signal

        def no_pidfd(_pid: int) -> int:
            raise OSError(errno.EMFILE, "too many open files")

        def no_proc(path: Any) -> Any:
            if path == "/proc":
                raise OSError(errno.EIO, "unreadable")
            return real_scandir(path)

        def no_kill(fd: int, sig: int) -> None:
            if sig == signal.SIGKILL:
                if fault == "signal":
                    raise PermissionError(errno.EPERM, "not permitted")
                return                                          # "deadline": the kill never lands
            real_signal(fd, sig)

        if fault == "pidfd":
            monkeypatch.setattr(hostread.os, "pidfd_open", no_pidfd)
        elif fault == "proc":
            monkeypatch.setattr(hostread.os, "scandir", no_proc)
        else:
            monkeypatch.setattr(hostread.signal, "pidfd_send_signal", no_kill)
        before = open_fds()
        recover(scratch, seconds=0.3)
        assert open_fds() == before
        assert logged() == [DIAG_RECOVERY_KEPT]
        assert home.exists() and orphan.poll() is None
        monkeypatch.undo()
        recover(scratch)
        assert orphan.wait(10) == -signal.SIGKILL
        assert not os.path.lexists(home)
        assert logged() == [DIAG_RECOVERY_KEPT, DIAG_RECOVERED]
    finally:
        orphan.kill()
        orphan.wait(10)


@pytest.mark.parametrize(("env", "reason"), [
    ({"HZ_FAKE_RATE_FAULT": "error"}, "AnswerError"), ({"HZ_FAKE_RATE_FAULT": "garbage"}, "JSONDecodeError"),
    ({"HZ_FAKE_RATE_FAULT": "hang"}, "TimeoutError"), ({"HZ_FAKE_RATE_FAULT": "close"}, "ClosedOutput"),
    ({"HZ_FAKE_RATE_FAULT": "exit"}, "ExitStatus"), ({"HZ_FAKE_RATE_LIMITS": "null"}, "NoRateLimits")])
def test_a_failed_read_is_none_names_why_and_leaves_nothing(tmp_path: Path, logged: Callable[[], list[str]],
                                                            env: dict[str, str], reason: str) -> None:
    rig = Rig(tmp_path, **env)
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: {reason}"]
    assert rig.scratch_empty() and rig.server_gone()           # started, killed and reaped
    assert rig.login.read_text() == rig.original


def test_a_missing_cli_stops_before_anything_starts(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    shutil.rmtree(tmp_path / "i")
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: AdapterError"]     # locate's, never a reading or a refusal
    assert not rig.scratch.exists() and not rig.pid_file.exists()


def test_an_app_server_that_cannot_be_reaped_is_no_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           logged: Callable[[], list[str]],
                                                           started: list[subprocess.Popen[str]]) -> None:
    rig = Rig(tmp_path)
    real_wait = POPEN.wait

    def stuck(self: subprocess.Popen[str], timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(self.args, timeout or 0)

    monkeypatch.setattr(POPEN, "wait", stuck)
    try:
        assert rig.read() is None
    finally:
        monkeypatch.setattr(POPEN, "wait", real_wait)
        reaped = [real_wait(p, 10) for p in started]
    assert reaped == [-signal.SIGKILL]                          # it was killed; reaped here, by its handle
    assert logged() == [f"{DIAG_READ_FAILED}: NotGone"]
    assert rig.scratch_empty() and rig.server_gone()


def test_a_scratch_home_that_cant_be_removed_is_no_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    monkeypatch.setattr(hostread.shutil, "rmtree", lambda *_a, **_k: None)
    assert rig.read() is None
    assert logged() == [DIAG_SCRATCH_KEPT]
    assert rig.server_gone()


def test_a_read_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)

    def broken(_self: ConfiguredAccounts, _adapter: str, _account: str) -> str:
        raise ConfigError("account default is not configured for adapter codex")

    monkeypatch.setattr(ConfiguredAccounts, "current_key", broken)
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: ConfigError"]      # the type name: no text, path or key


def test_a_linked_login_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    elsewhere = tmp_path / "elsewhere.json"
    rig.login.rename(elsewhere)
    rig.login.symlink_to(elsewhere)
    rig.key = rig.accounts.current_key("codex", "default")      # the key follows the link (D1)
    assert rig.read() == WANTED                                 # the real path is read, not the link
    assert rig.login.is_symlink() and elsewhere.read_text() == rig.original
    assert logged() == []
```

`server_gone` reads the PID file the fake app-server writes as it starts, so it also proves the read started one. `hostread.os` is the `os` module itself, so the short-write and failure tests patch `os.write` or `os.fsync` for the whole process while the read runs. Nothing else on the read's path calls them: the client writes to the app-server through its pipe's file object. The crash test runs the reader in a child process that imports this test module's `Rig`; the child kills itself just after the copy (`copied`), or the test kills it once the app-server has written its PID (`started`). Every check of it runs after `read` has returned, so a server that is still running, or killed but never reaped, fails the test.

In `tests/test_sandbox_build.py`, import `build_usage_reader`, `HostUsageReader` and `NoUsageReader`, then add:

```python
def test_the_reader_needs_the_sandbox_runtime_and_a_codex_binary(tmp_path: Path) -> None:
    env = config_env(tmp_path, "")
    assert isinstance(build_usage_reader(settings(tmp_path, env), env), NoUsageReader)
    env = config_env(tmp_path, '[platform]\nsandbox = "openshell"\n')
    reader = build_usage_reader(settings(tmp_path, env), env)
    assert isinstance(reader, HostUsageReader)
    assert reader.credlocks == settings(tmp_path, env).credlock_dir
    assert reader.scratch_root == settings(tmp_path, env).state_dir / "hostread"
```

and in `test_wsd_run_uses_the_built_runtime`, replace the inner `run` with:

```python
    def run(_s: WsdSettings, _f: object, runtime: AgentRuntime, *,
            usage_reader: UsageReader | None = None) -> int:
        seen.append(runtime)
        readers.append(usage_reader)
        return 0
```

adding `readers: list[UsageReader | None] = []` beside `seen`, `monkeypatch.setattr(cli, "build_usage_reader", lambda _s, _env: NoUsageReader())` beside the `build_runtime` patch, and `assert isinstance(readers[0], NoUsageReader)` after the first `wsd_main` assertion. Import `UsageReader` from `heterodyne.wsd.usage`.

In `tests/test_wsd_daemon.py`, add:

```python
def test_assemble_hands_the_reader_to_every_workstream(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")

    class Reader(NoUsageReader):
        pass

    reader = Reader()
    parts = assemble(s, Journal(s.journal), factory(world), FakeRuntime(), usage_reader=reader)
    assert all(sch.d.usage_reader is reader for sch in parts.schedulers.values())
    plain = assemble(s, Journal(s.journal), factory(world), FakeRuntime())
    assert all(isinstance(sch.d.usage_reader, NoUsageReader) for sch in plain.schedulers.values())
```

and:

```python
def test_run_recovers_crashed_reads_once_it_holds_the_lock(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(tmp_path)
    recovered: list[Path] = []

    def recording(root: Path) -> None:
        with pytest.raises(AlreadyRunning):
            instance_lock(s.instance_lock)                  # recover runs under the instance lock
        recovered.append(root)

    async def served(_d: Wsd) -> None:
        return None

    monkeypatch.setattr(cli.hostread, "recover", recording)
    monkeypatch.setattr(cli, "_serve", served)
    assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 0
    assert recovered == [s.state_dir / "hostread"]
    held = instance_lock(s.instance_lock)
    try:
        assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 1
    finally:
        os.close(held)
    assert recovered == [s.state_dir / "hostread"]          # a second instance recovers nothing
```

`settings(tmp_path)`, `factory` and `World` are this file's existing helpers, and `cli`, `Wsd`, `AlreadyRunning` and `instance_lock` are already imported (add `import os` if it is missing). If its settings helper has another name, use that one. `Scheduler.d` is the scheduler's `Deps`, as `Parker` reads it. Import `NoUsageReader` from `heterodyne.wsd.usage`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 uv run pytest tests/test_sandbox_hostread.py tests/test_sandbox_build.py tests/test_wsd_daemon.py -q`
Expected: FAIL at import (`No module named 'heterodyne.sandbox.hostread'`, `cannot import name 'build_usage_reader'`), and `TypeError: assemble() got an unexpected keyword argument 'usage_reader'`.

- [ ] **Step 3: Implement**

Create `src/heterodyne/sandbox/hostread.py`:

```python
"""The trusted host-side usage read (ADR 0001 §4.4 D9 (d), plan 4b E4). Codex only: S7 found no host
read for Claude. A short-lived `codex app-server` over stdio, in a scratch CODEX_HOME holding a copy of
the account's login, under the account's D8 lock. The account's own login directory never collects the
app-server's state. Logs carry no path, key or token: only fixed texts and exception type names."""

import contextlib
import importlib.util
import json
import logging
import os
import shutil
import signal
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from heterodyne.agents.base import AdapterError, jwt_exp
from heterodyne.agents.codex import HOST_SOURCE, USAGE_SOURCE, Codex, parse_rate_limits
from heterodyne.fsutil import private_dir
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.credlock import CredentialBusy, credential_lock
from heterodyne.wsd.usage import TrustedRead
from heterodyne.wsd.workstream import utc_now

log = logging.getLogger(__name__)

ADAPTER = "codex"
LOGIN = "auth.json"
MAX_LOGIN = 1 << 20
RECOVER_SECONDS = 10.0
DIAG_ROTATED_DROPPED = ("a trusted usage read's app-server rewrote its copy of the login; the copy was dropped "
                        "and the account's login left as it was. If the account's next session can't sign in, "
                        "log it in again")
DIAG_SCRATCH_KEPT = "a trusted usage read's scratch home could not be removed; the read is discarded"
DIAG_RECOVERED = "an interrupted trusted usage read was cleaned up: its app-server ended, its scratch home removed"
DIAG_RECOVERY_KEPT = ("an interrupted trusted usage read could not be cleaned up; its scratch home under the "
                      "state directory's hostread folder holds a copy of a login")
DIAG_READ_FAILED = "a trusted usage read failed"
DIAG_NOT_READ = "a trusted usage read was not run"
NOT_PINNED_KEY = "the account's key is not the pinned one"
TOO_CLOSE = "the login expires within the read margin"
NOT_AT_PIN = "the codex CLI is not at its pinned version"


class NoRateLimits(Exception):
    """The app-server answered, but with no rate limits."""


class _NotRead(Exception):
    """The read stopped before it started anything; its text is one of the fixed reasons above."""


def _usage_module() -> Any:
    """codex_usage.py, the in-sandbox read's file: the host read runs the same client."""
    spec = importlib.util.spec_from_file_location("heterodyne_codex_usage", USAGE_SOURCE)
    if spec is None or spec.loader is None:
        raise ImportError("codex_usage.py can't be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_regular(path: Path) -> bytes:
    """`path`'s bytes, refusing a final link, a non-regular file and anything over MAX_LOGIN."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LOGIN:
            raise OSError("not a login file")
        data = os.read(fd, MAX_LOGIN + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_LOGIN:
        raise OSError("not a login file")
    return data


def _expiry(login: bytes) -> float:
    """The access token's expiry, from the bytes the read copies: the same bytes, so no second read."""
    try:
        tokens: Any = json.loads(login).get("tokens")
    except (ValueError, AttributeError) as exc:
        raise AdapterError("the login's expiry can't be read") from exc
    return jwt_exp(tokens.get("access_token") if isinstance(tokens, dict) else None)


def _write_new(folder: Path, name: str, data: bytes) -> None:
    """A new 0600 file in `folder` holding all of `data`, flushed to disk. On any failure the file is
    unlinked before the error goes on: no part of a login is left behind."""
    path = folder / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        raise


def _holders(marks: set[bytes]) -> list[int]:
    """pidfds of the live processes whose environment holds one of `marks`. The pidfd is opened before
    the environment is read, and signal 0 through it afterwards proves the PID wasn't reused meanwhile.
    A zombie's environment is empty, so a killed process stops being found once it ends. A process that
    is gone or another user's is skipped; any other failure (a pidfd that can't be opened, `/proc` that
    can't be listed) means the scan is incomplete, so it raises, with every pidfd it opened closed."""
    found: list[int] = []
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                fd = os.pidfd_open(int(entry.name))
            except ProcessLookupError:
                continue
            try:
                with open(f"/proc/{entry.name}/environ", "rb") as f:
                    environ = set(f.read().split(b"\0"))
                signal.pidfd_send_signal(fd, 0)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                os.close(fd)             # gone, or another user's (not this wsd's)
                continue
            except BaseException:
                os.close(fd)
                raise
            if marks.isdisjoint(environ):
                os.close(fd)
            else:
                found.append(fd)
    except BaseException:
        for fd in found:
            os.close(fd)
        raise
    return found


def _end_holders(marks: set[bytes], seconds: float) -> None:
    """SIGKILL, through their pidfds, the processes `marks` find, until a complete scan finds none.
    Raises TimeoutError if one is still found after `seconds`, or what a failed scan or signal raised."""
    deadline = time.monotonic() + seconds
    while fds := _holders(marks):
        try:
            for fd in fds:
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
        finally:
            for fd in fds:
                os.close(fd)
        if time.monotonic() > deadline:
            raise TimeoutError("an app-server outlived the recovery deadline")
        time.sleep(0.05)


def recover(scratch_root: Path, *, seconds: float = RECOVER_SECONDS) -> None:
    """E4: clean up the reads a crash interrupted. Called at wsd's start under its instance lock, when
    no read can be running, so every `read-*` home is a crashed read's: its app-servers (found by their
    CODEX_HOME) are killed through a pidfd, then the home is removed. The homes are the only record of
    which processes to look for, so they are removed only once a complete scan finds none of those
    processes left; otherwise all of them are kept for the next start. Never raises."""
    try:
        homes = [p for p in scratch_root.iterdir() if p.name.startswith("read-")]
    except FileNotFoundError:
        return
    except Exception:
        log.warning(DIAG_RECOVERY_KEPT)
        return
    if not homes:
        return
    try:
        _end_holders({f"CODEX_HOME={home}".encode() for home in homes}, seconds)
    except Exception:
        log.warning(DIAG_RECOVERY_KEPT)      # unconfirmed: the next start must still find its app-server
        return
    for home in homes:
        shutil.rmtree(home, ignore_errors=True)
    kept = any(os.path.lexists(home) for home in homes)
    log.warning(DIAG_RECOVERY_KEPT if kept else DIAG_RECOVERED)


class HostUsageReader:
    def __init__(self, accounts: Accounts, binary: str, path: str, credlocks: Path, scratch_root: Path,
                 lock_wait_seconds: float, *, env: Mapping[str, str], read_margin_seconds: int = 300,
                 limit_seconds: float = 30.0, clock: Callable[[], int] = utc_now,
                 wait_clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.accounts = accounts
        self.binary = binary                 # `[adapters.codex] binary`, located on each read
        self.path = path
        self.credlocks = credlocks
        self.scratch_root = scratch_root
        self.lock_wait_seconds = lock_wait_seconds
        self.env = dict(env)                 # the app-server's environment, CODEX_HOME aside
        self.read_margin_seconds = read_margin_seconds
        self.limit_seconds = limit_seconds
        self.clock = clock
        self.wait_clock = wait_clock
        self.sleep = sleep
        self.adapter = Codex()

    def read(self, profile: str, account: str, credential_key: str) -> TrustedRead | None:
        if self.accounts.adapter(profile) != ADAPTER:
            return None
        try:
            with credential_lock(self.credlocks, credential_key, self.lock_wait_seconds,
                                 clock=self.wait_clock, sleep=self.sleep):
                return self._locked(account, credential_key)
        except (CredentialBusy, _NotRead) as why:
            log.info("%s: %s", DIAG_NOT_READ, why)      # fixed texts only (credlock's and this module's)
            return None
        except Exception as exc:  # noqa: BLE001 - never raises (UsageReader); the type name only
            log.warning("%s: %s", DIAG_READ_FAILED, type(exc).__name__)
            return None

    def _locked(self, account: str, credential_key: str) -> TrustedRead | None:
        if self.accounts.current_key(ADAPTER, account) != credential_key:
            raise _NotRead(NOT_PINNED_KEY)
        _, files = self.accounts.login_paths(ADAPTER, account)
        login = Path(os.path.realpath(files[0]))
        original = _read_regular(login)
        if _expiry(original) - self.clock() < self.read_margin_seconds:
            raise _NotRead(TOO_CLOSE)         # S7 (4): only an expired token is refreshed; keep clear of it
        cli = self.adapter.locate(self.binary, self.path)
        if cli.version != self.adapter.version_pin:
            raise _NotRead(NOT_AT_PIN)
        private_dir(self.scratch_root)
        scratch = Path(tempfile.mkdtemp(prefix="read-", dir=self.scratch_root))
        try:
            _write_new(scratch, LOGIN, original)
            limits = _usage_module().read(str(cli.binary), {**self.env, "CODEX_HOME": str(scratch)},
                                          self.limit_seconds)
            report = parse_rate_limits(limits, HOST_SOURCE)
            if report is None:
                raise NoRateLimits("the answer held no rate limits")
            try:
                changed = _read_regular(scratch / LOGIN) != original
            except OSError:
                changed = True                # replaced by something that isn't a login file
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            kept = os.path.lexists(scratch)
            if kept:
                log.warning(DIAG_SCRATCH_KEPT)
        if kept:
            return None
        if changed:
            log.warning(DIAG_ROTATED_DROPPED)  # E4: never written back; D8's writers don't take the lock
        return TrustedRead(report.windows, report.limited, report.reset_hint)
```

In `_locked`, the scratch home is made only once the lock is held, the key matches, the login is read, its token has the margin left and the CLI is at its pin. So the tests that expect `not rig.scratch.exists()` prove the read stopped before it started anything. Nothing in `_locked` writes outside the scratch home. A failed copy unlinks its own file, and the `finally` then removes the empty home, so the failure tests see an empty `scratch_root` and no app-server's PID file. A login whose expiry can't be read raises `AdapterError` from `_expiry`, which is logged as a failure: it is not one of the expected stops. When the read itself raised, `kept` is never consulted: the exception leaves `_locked` from the `finally`, after the scratch home's removal was tried and `DIAG_SCRATCH_KEPT` logged if it failed.

In `src/heterodyne/sandbox/build.py`, import `from heterodyne.sandbox.hostread import HostUsageReader` and `from heterodyne.wsd.usage import NoUsageReader, UsageReader`, and add:

```python
def build_usage_reader(s: WsdSettings, env: Mapping[str, str]) -> UsageReader:
    """D9 (d)'s trusted read, when the sandbox runtime runs and a Codex binary is configured. The
    capability table decides whether wsd ever asks it (E15)."""
    settings = sandbox_settings(env)
    binary = settings.binaries.get("codex")
    if settings.backend != "openshell" or s.accounts is None or not binary:
        return NoUsageReader()
    path = env.get("PATH", os.defpath)
    base = {"PATH": path, "HOME": env.get("HOME", ""), "LANG": "C.UTF-8"}
    return HostUsageReader(s.accounts, binary, path, s.credlock_dir, s.state_dir / "hostread",
                           settings.lock_wait_seconds, env=base)
```

In `src/heterodyne/wsd/daemon.py`, import `NoUsageReader` and `UsageReader` from `heterodyne.wsd.usage` and replace `assemble`:

```python
def assemble(s: WsdSettings, journal: Journal, factory: QueueFactory, runtime: AgentRuntime,
             reconciler: Callable[[BeadsAdapter], ActionReconciler] = HoldingReconciler,
             cp: Checkpoint = nothing, usage_reader: UsageReader | None = None) -> Parts:
    beads = BeadsAdapter(factory)
    gate = ClaimGate(s.lock_dir, beads, cp)
    deps = Deps(journal, beads, gate, runtime, reconciler(beads), cp,
                usage_reader=usage_reader if usage_reader is not None else NoUsageReader())
    return Parts(journal, beads, gate, {w.name: Scheduler(w, deps) for w in s.workstreams})
```

In `src/heterodyne/wsd/cli.py`, import `build_usage_reader` beside `build_runtime` and `UsageReader` from `heterodyne.wsd.usage`. Import `from heterodyne.sandbox import hostread`. Give `run` the keyword `*, usage_reader: UsageReader | None = None` after `exit_now`, and pass `usage_reader=usage_reader` to `assemble(...)`. In `run`, add `hostread.recover(s.state_dir / "hostread")` as the first line of the `try:` that follows `held = False`, before `journal = _open(...)`. It never raises, and it sits inside the `finally` that releases the instance lock in any case. It runs whichever reader is configured, so a crashed read is cleaned up even if the sandbox runtime was switched off since. In `wsd_main`, replace the `run` line with:

```python
            return run(s, _factory(s), build_runtime(s, os.environ),
                       usage_reader=build_usage_reader(s, os.environ))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 900 uv run pytest tests/test_sandbox_hostread.py tests/test_sandbox_build.py tests/test_wsd_daemon.py tests/test_agents_usage.py -q`
Expected: PASS. The `hang` case ends in about `limit_seconds` (5), because the client's timer kills the app-server; a test run that waits longer is a failure of the bound, not a slow pass. Each failure is named by its own diagnostic, so a fault that fails for another reason (a missing executable among them) fails its test.

Then: `timeout 600 uv run pyright src tests/test_sandbox_hostread.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/sandbox/hostread.py src/heterodyne/sandbox/build.py src/heterodyne/wsd/daemon.py \
        src/heterodyne/wsd/cli.py tests/fakes/fake_agent_cli.py tests/test_sandbox_hostread.py \
        tests/test_sandbox_build.py tests/test_wsd_daemon.py
git commit -m "plan4b T8: the trusted host-side Codex read, under the account's lock"
```

## Task 9: draining the signals, and the reactive limit flow

E8 and E11. Each pickup drains the runtime's signals just after `expire`. A `UsageSeen` is stored untrusted if the profile has (c). A `LimitSeen` defers its own bead if the profile has (e): an untrusted QUOTA record first, then the AU-4 tail. Once the session is stopped, a new tail step asks the trusted read, if the profile has (d). A read that confirms the limit supersedes the record with the gate's trusted verdict, so a profile that can fail over relaunches on its next account in the same pickup.

**Files:**
- Modify: `src/heterodyne/wsd/park.py` (`capabilities`, `LIMIT_STEPS`, `Parker.limit`, `_confirm`, `_supersede`, the tail's new step)
- Modify: `src/heterodyne/wsd/scheduler.py` (`held`, `_signals`, `_signal`, called from `_pickup`)
- Modify: `tests/wsd_env.py` (`Rig.usage_reader`)
- Test: `tests/test_wsd_defer.py`

**Interfaces:**
- Consumes:
  - `UsageSeen`, `LimitSeen`, `AgentRuntime.signals`, `TrustedRead`, `Deps.usage_reader`, `FakeRuntime.report` / `drains` (Task 1).
  - `usage.ingest_untrusted`, `ingest_trusted`, `mark_exhausted`, `decide`, `untrusted_defer_until` (exist).
  - `Parker._record_defer`, `defer_tail`, `_previous_key` (exist).
- Produces:
  - `park.capabilities(accounts: Accounts | None, profile: str) -> Capabilities`: E15's one read on the `wsd` side. It returns `NONE` without accounts, or for a profile that can't resolve.
  - `park.LIMIT_STEPS`: the tail's points for a limit's defer, `defer.read!` and `defer.confirmed` between `defer.committed` and `defer.labelled!` (D6: the WIP commit comes first).
  - `Parker.limit(session_key: str, generation: int, hint: int | None, now: int) -> BeadState | None`. The caller holds `entry()`.
  - A PARK op opened by `limit` carries `limit_key` and `limit_gen` in its data.
  - `Rig.usage_reader` (tests): the reader the rig's `Deps` get, kept across `restart`.

The confirming step sits after the WIP commit, as D6 orders it (Codex plan 4b r2). If the commit fails, the tail stops before the read: no trusted read, no `mark_exhausted`, and the bead keeps only its untrusted record. A superseding record has a new number. The WIP commit's mark (`defer:<key>:<n>`, written and never read back) names the untrusted record, and `defer.confirmed` keeps that number as `wip_defer`, while `v2:deferred` and the comment use the number current after `defer.confirmed`, so the comment names the record the bead actually waits on. The read runs after the stop, so the limited session can't refresh the login while the host reads it.

A limit report is held by the scheduler until `limit` has handled it (Codex plan 4b r2). If handling it raises, `BeadsUnavailable` for one, that report and every limit report after it in the batch stay held. The next pickup handles them first, oldest first, before anything newly drained, so a Beads outage loses no report while at most `MAX_HELD` (256) are held. Past that, the oldest are dropped, the failed one possibly among them (Codex plan 4b r3); a workstream runs one coder session at a time, so that many reports means the outage outlasted hundreds of pickups, and it is accepted for the proof of concept. Claude reports its limit once and then waits for the reset (S7 (5)), so a lost report would mean no failover. Handling one again is safe: `limit` opens nothing for a launch that is no longer the bead's running one, for a bead that isn't running, or while an op is open. Once the op is opened, its replay finishes the defer. A usage report is not held: the next one replaces it. The held reports live in memory, as the runtime's queue does, so a wsd crash loses both. That loss is accepted: the session then waits for the reset itself, as it would with (e) off.

- [ ] **Step 1: Write the failing tests**

In `tests/wsd_env.py`, import `NoUsageReader` and `UsageReader` from `heterodyne.wsd.usage`. Add the field to `Rig`, after `clock`:

```python
    usage_reader: UsageReader = field(default_factory=NoUsageReader)
```

and in `Rig.restart`, replace the `Deps(...)` call with:

```python
        self.deps = Deps(self.journal, self.beads, self.gate, self.runtime,
                         self.reconciler or HoldingReconciler(self.beads), self.cp, self.clock,
                         usage_reader=self.usage_reader)
```

In `tests/test_wsd_defer.py`, add `Account`, `ConfiguredAccounts` and `ProfileAccounts` (from `heterodyne.wsd.accounts`), `Capabilities` (from `heterodyne.config.capabilities`), `LIMIT_STEPS` and `TRUSTED` (to the `heterodyne.wsd.park` import), `LimitSeen` and `UsageSeen` (to the `heterodyne.wsd.runtime` import), and `Observation` and `TrustedRead` (from `heterodyne.wsd.usage`), `contextlib`, and `Bead`, `BeadsAdapter` and `BeadsUnavailable` (from `heterodyne.wsd.beads`, where the file doesn't import them already) to the imports. Replace `wip_of` with the version below: a confirmed read moves `defer` to the superseding record, while the WIP keeps the mark of the record it was committed under (`wip_defer`, Codex plan 4b r3):

```python
def wip_of(rig: Rig, rec: DeferralRow) -> str | None:
    """The SHA a tail journaled for `rec`, checked against git: the mark it was committed under finds it."""
    found = [(d["sha"], d.get("wip_defer", d["defer"])) for _, _, _, d in ops(rig)
             if d.get("defer_key") == rec.session_key and d.get("defer") == str(rec.number) and "sha" in d]
    if not found:
        return None
    [(sha, committed)] = found
    assert gitwip.find_wip(gitwip.pin(rig.repo, rig.worktree(rec.bead), f"btq/{rec.bead}"),
                           f"defer:{rec.session_key}:{committed}") == sha
    return sha
```

Then append:

```python
# --- the reactive limit (plan 4b Task 9) ---

NOW = 1_800_000_000                   # the rig's clock (wsd_env.Clock)
RESET = NOW + HOUR
HOST = "codex.host-read"
LIMIT_POINTS = tuple(f"park.{s}" for s in LIMIT_STEPS)


class Reader:
    """A scripted trusted read: `answer` every time, each call recorded, `then` run during each call."""

    def __init__(self, answer: TrustedRead | None, then: Callable[[], object] = lambda: None) -> None:
        self.answer = answer
        self.then = then
        self.calls: list[tuple[str, str, str]] = []

    def read(self, profile: str, account: str, credential_key: str) -> TrustedRead | None:
        self.calls.append((profile, account, credential_key))
        self.then()
        return self.answer


def reading(limited: bool = True) -> TrustedRead:
    return TrustedRead((Observation("codex:codex:300m", "5h", 100, RESET, HOST),), limited, RESET)


def caps(rig: Rig, **on: bool) -> None:
    accounts = rig.ws.accounts
    assert isinstance(accounts, ConfiguredAccounts)
    accounts.capabilities = {"codex": Capabilities(**on)}


def spare(rig: Rig) -> None:
    """p-two on its `default` codex login, then a named `spare` one with its own file, failing over."""
    accounts = rig.ws.accounts
    assert isinstance(accounts, ConfiguredAccounts)
    login = Path(accounts.env["HOME"]) / ".codex-spare"
    login.mkdir(parents=True, exist_ok=True)
    (login / "auth.json").write_text("{}")
    accounts.named[("codex", "spare")] = Account("spare", "codex", str(login), login, (), "")
    accounts.profiles["p-two"] = ProfileAccounts("codex", ("default", "spare"), "next")


def limited(rig: Rig, reader: Reader | None = None, *, two: bool = False, **on: bool) -> Reader:
    """B launched on p-two's default login, with capabilities `on` and `reader` as the trusted read."""
    if two:
        spare(rig)
    caps(rig, **on)
    used = reader if reader is not None else Reader(None)
    rig.usage_reader = used
    rig.restart(rig.cp)
    launched(rig)
    return used


def signal(rig: Rig, hint: int | None = RESET, generation: int = 1) -> Outcome:
    rig.runtime.report(WS, LimitSeen(rig.key(B), generation, hint))
    return rig.pickup()


def first_key(rig: Rig) -> str:
    entry = rig.journal.launch(rig.key(B), 1)
    assert entry is not None
    return entry.credential_key


def untrusted_rows(rig: Rig) -> int:
    return rig.journal.db.execute("SELECT COUNT(*) FROM account_usage_untrusted").fetchone()[0]


def test_a_limit_without_e_is_drained_and_ignored(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    reader = limited(rig, Reader(reading()), trusted_read=True)
    assert signal(rig) is Outcome.BUSY
    assert rig.runtime.drains[-1] == WS
    assert records(rig) == [] and reader.calls == [] and rig.state(B) == BeadState.RUNNING.value


def test_a_usage_report_is_stored_untrusted_and_only_with_c(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    limited(rig)
    seen = UsageSeen(rig.key(B), 1, (Observation("codex:codex:300m", "5h", 40, None, "codex.app-server"),))
    rig.runtime.report(WS, seen)
    rig.pickup()
    assert untrusted_rows(rig) == 0
    caps(rig, in_session_usage=True)
    rig.runtime.report(WS, seen, UsageSeen(rig.key(B), 7, seen.observations))     # no generation 7
    rig.pickup()
    assert untrusted_rows(rig) == 1
    assert not rig.journal.usage_cache([first_key(rig)]).windows.get(first_key(rig))    # never trusted


def test_a_limit_without_d_defers_untrusted_and_clamped(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    reader = limited(rig, Reader(reading()), limit_signal=True)
    assert signal(rig, hint=NOW + 5 * HOUR) is Outcome.DEFERRED
    [rec] = records(rig)
    assert (rec.reason, rec.trust, rec.defer_until) == (QUOTA, UNTRUSTED, str(NOW + HOUR))   # 60 minutes
    assert reader.calls == [] and rig.runtime.coders() == []
    assert rig.state(B) == BeadState.DEFERRED.value and DEFERRED in rig.world.beads[B].labels
    completed(rig)


@pytest.mark.parametrize("answer", [None, reading(limited=False)], ids=["no-reading", "not-limited"])
def test_a_read_that_doesnt_confirm_keeps_the_untrusted_record(tmp_path: Path,
                                                               answer: TrustedRead | None) -> None:
    rig = rig_for(tmp_path)
    reader = limited(rig, Reader(answer), trusted_read=True, limit_signal=True)
    assert signal(rig) is Outcome.DEFERRED
    [rec] = records(rig)
    assert (rec.reason, rec.trust, rec.defer_until) == (QUOTA, UNTRUSTED, str(RESET))
    assert reader.calls == [("p-two", "default", first_key(rig))]
    cache = rig.journal.usage_cache([first_key(rig)])
    assert not cache.marks and not cache.windows.get(first_key(rig))
    completed(rig)


def test_a_failed_wip_commit_reads_nothing_and_marks_nothing(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """D6 (Codex plan 4b r2): the WIP commit comes before the trusted read. A commit that fails stops the
    tail with only the untrusted record: no read, no exhaustion mark, no trusted record."""
    rig = rig_for(tmp_path)
    reader = limited(rig, Reader(reading()), two=True, trusted_read=True, limit_signal=True)

    def fails(*_: object) -> str:
        raise gitwip.GitFailed("injected")

    monkeypatch.setattr(gitwip, "wip_commit", fails)
    with contextlib.suppress(gitwip.GitFailed):
        signal(rig)
    assert reader.calls == []
    assert [(r.reason, r.trust) for r in records(rig)] == [(QUOTA, UNTRUSTED)]
    assert not rig.journal.usage_cache([first_key(rig)]).marks


def test_a_confirmed_limit_relaunches_on_the_next_account(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    limited(rig, Reader(reading()), two=True, cross_account_resume=True, trusted_read=True, limit_signal=True)
    key = first_key(rig)
    assert signal(rig) is Outcome.RESUMED
    assert [(r.number, r.reason, r.trust, r.defer_until) for r in records(rig)] == [
        (1, QUOTA, UNTRUSTED, str(RESET)), (2, QUOTA, TRUSTED, str(NOW))]
    assert [s.account for s in rig.runtime.launches] == ["default", "spare"]
    cache = rig.journal.usage_cache([key])
    assert cache.marks[key].until == RESET
    assert [(w.window_id, w.source) for w in cache.windows[key]] == [("codex:codex:300m", HOST)]
    assert rig.state(B) == BeadState.RUNNING.value and DEFERRED not in rig.world.beads[B].labels
    assert len(defer_comments(rig)) == 1 and defer_comments(rig)[0].startswith(defer_mark(rig.key(B), 2))
    completed(rig)


def test_a_limit_held_through_a_beads_outage_is_handled_after_it(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    rig = rig_for(tmp_path)
    limited(rig, Reader(reading()), two=True, cross_account_resume=True, trusted_read=True, limit_signal=True)
    real_show = BeadsAdapter.show

    def down(_self: BeadsAdapter, _ws: str, _bead: str) -> Bead:
        raise BeadsUnavailable("injected")

    monkeypatch.setattr(BeadsAdapter, "show", down)
    with contextlib.suppress(BeadsUnavailable):
        signal(rig)                                             # the report is drained, and can't be handled
    assert records(rig) == [] and [s.account for s in rig.runtime.launches] == ["default"]
    monkeypatch.setattr(BeadsAdapter, "show", real_show)
    assert rig.pickup() is Outcome.RESUMED                      # no new report: the held one is handled
    assert [(r.number, r.reason, r.trust) for r in records(rig)] == [(1, QUOTA, UNTRUSTED), (2, QUOTA, TRUSTED)]
    assert [s.account for s in rig.runtime.launches] == ["default", "spare"]
    rig.pickup()                                                # handled once: nothing is held any more
    assert len(records(rig)) == 2 and len(rig.runtime.launches) == 2
    completed(rig)


def test_a_held_limit_whose_launch_moved_on_is_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaying a held report is safe: by then its launch is no longer the bead's running one."""
    rig = rig_for(tmp_path)
    limited(rig, Reader(reading()), two=True, cross_account_resume=True, trusted_read=True, limit_signal=True)
    rig.runtime.report(WS, LimitSeen(rig.key(B), 1, RESET))
    rig.runtime.report(WS, LimitSeen(rig.key(B), 1, RESET))     # the same report twice in one batch
    assert rig.pickup() is Outcome.RESUMED
    assert len(records(rig)) == 2 and [s.account for s in rig.runtime.launches] == ["default", "spare"]
    completed(rig)


def test_a_confirmed_limit_on_the_only_account_waits_for_the_reset(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    limited(rig, Reader(reading()), trusted_read=True, limit_signal=True)
    assert signal(rig) is Outcome.DEFERRED
    assert [(r.number, r.trust, r.defer_until) for r in records(rig)] == [
        (1, UNTRUSTED, str(RESET)), (2, TRUSTED, str(RESET))]
    assert rig.sched.wake_at == RESET
    rig.clock.advance(HOUR)
    assert rig.pickup() is Outcome.RESUMED
    assert [s.account for s in rig.runtime.launches] == ["default", "default"]
    completed(rig)


def test_a_confirmed_limit_on_a_repointed_login_waits_for_the_operator(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    limited(rig, Reader(reading(), then=lambda: repoint_codex(rig, "other")), trusted_read=True,
            limit_signal=True)
    assert signal(rig) is Outcome.NOTHING
    assert [(r.number, r.reason, r.trust) for r in records(rig)] == [(1, QUOTA, UNTRUSTED),
                                                                    (2, ACCOUNT_CHANGED, TRUSTED)]
    assert alerts(rig) == [f"{rig.key(B)}:2"]
    row = rig.journal.state(WS, B)
    assert row is not None and (row.state, row.reason) == (BeadState.DEFERRED, Reason.ACCOUNT_CHANGED)
    completed(rig)


def test_a_limit_for_another_launch_or_a_bead_not_running_is_ignored(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    limited(rig, limit_signal=True)
    key = rig.key(B)
    rig.runtime.report(WS, LimitSeen(key, 2, RESET), LimitSeen("sk-never-journaled", 1, RESET))
    assert rig.pickup() is Outcome.BUSY
    assert records(rig) == [] and rig.journal.ops_open(WS) == []
    assert signal(rig) is Outcome.DEFERRED
    assert signal(rig) is Outcome.DEFERRED                      # again, now that B is deferred
    assert len(records(rig)) == 1
    assert [k for k, _, _, d in ops(rig) if "limit_key" in d] == ["park"]


FAILOVER = Scenario(lambda r: limited(r, Reader(reading()), two=True, cross_account_resume=True,
                                      trusted_read=True, limit_signal=True),
                    lambda r: signal(r))


def test_a_crash_at_every_limit_point_replays_to_the_relaunch(tmp_path: Path) -> None:
    clean = same_end(tmp_path, FAILOVER, LIMIT_POINTS)
    assert [s.account for s in clean.rig.runtime.launches] == ["default", "spare"]
```

`defer_mark` is imported from `heterodyne.wsd.park` beside `DEFER_MARK`. `Reason` and `BeadState` are already imported. Add `import contextlib` if the module lacks it; `gitwip` is already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 uv run pytest tests/test_wsd_defer.py -q`
Expected: FAIL at import, with `cannot import name 'LIMIT_STEPS' from 'heterodyne.wsd.park'`.

- [ ] **Step 3: Implement**

In `src/heterodyne/wsd/park.py`, add to the imports:

```python
from heterodyne.config.capabilities import NONE, Capabilities
from heterodyne.wsd.usage import (
    TrustedRead,
    admitted,
    decide,
    ingest_trusted,
    mark_exhausted,
    untrusted_defer_until,
)
```

replacing the existing `from heterodyne.wsd.usage import admitted, decide, untrusted_defer_until`. After `DEFER_STEPS`, add:

```python
# A limit's defer (plan 4b E11): the tail with the trusted read after the WIP commit (D6), before the label.
LIMIT_STEPS = (*DEFER_STEPS[:5], "defer.read!", "defer.confirmed", *DEFER_STEPS[5:])
```

After `resumable`, add:

```python
def capabilities(accounts: Accounts | None, profile: str) -> Capabilities:
    """E15: the profile's capabilities as it resolves now; none without accounts, or if it can't resolve."""
    if accounts is None:
        return NONE
    try:
        return accounts.view(profile).capabilities
    except ConfigError:
        return NONE
```

In the module docstring, add after the Defer item:

```
- Limit (plan 4b E11): a session's own limit report opens a PARK op with an untrusted record, then the
  tail, which asks the trusted read once the WIP is committed (`defer.confirmed`). A confirmed read
  supersedes the record with the gate's trusted verdict before the label and comment.
```

In `defer_tail`, replace the `if op.step == "defer.committed":` line (the label's block) with:

```python
            if op.step == "defer.committed" and "limit_key" in op.data:
                op = self._confirm(op)
                n = int(op.data["defer"])          # a confirmed read may have superseded the record
            if op.step in ("defer.committed", "defer.confirmed"):
```

Add after `convert_quota`:

```python
    # --- the reactive limit (plan 4b E11) ---

    def limit(self, session_key: str, generation: int, hint: int | None, now: int) -> BeadState | None:
        """A session reported its usage limit (D6) on the channel of launch (`session_key`, `generation`).
        The caller (pickup) holds `entry()` and has checked (e). Only the running bead's latest launch is
        deferred: an untrusted QUOTA record clamped by D5, recorded before anything stops, then the tail.
        None: nothing deferred (another launch, a bead not running, an operation open, or no readable
        record of this session)."""
        j, ws = self.d.journal, self.ws.name
        entry = j.launch(session_key, generation)
        launched = [e.generation for e in j.launches(session_key) if e.outcome == LAUNCHED]
        if entry is None or entry.ws != ws or not launched or launched[-1] != generation:
            return None
        row = j.state(ws, entry.bead)
        if row is None or row.state is not BeadState.RUNNING or j.op_for(ws, entry.bead) is not None:
            return None
        try:
            rec = self.d.beads.show(ws, entry.bead).record()
        except RecordUnreadable:
            return None
        if rec is None or rec.session_key != session_key:
            return None
        until = untrusted_defer_until(hint, now, self.ws.usage)
        with j.transaction():
            op = j.op_open(OpKind.PARK, ws, entry.bead,
                           {"ref": "", "limit_key": session_key, "limit_gen": str(generation)})
            op = self._record_defer(op, rec, QUOTA, until, UNTRUSTED)
        self.d.cp("park.defer.recorded")
        return self.defer_tail(op)

    def _confirm(self, op: Op) -> Op:
        """The `defer.confirmed` step, once the limited session is stopped and its WIP committed: with (d), the trusted read of
        the limited launch's account. A read that confirms the limit supersedes the untrusted record, in
        one transaction with the step (`_supersede`). Anything else keeps it: no (d), no reading, a
        reading that doesn't confirm, or a record superseded meanwhile (the tail's check escalates that)."""
        j, kind = self.d.journal, op.kind.value
        key, n = op.data["defer_key"], int(op.data["defer"])
        entry = j.launch(op.data["limit_key"], int(op.data["limit_gen"]))
        accounts = self.ws.accounts
        read = None
        if entry is not None and accounts is not None and capabilities(accounts, entry.profile).trusted_read:
            read = self.d.usage_reader.read(entry.profile, entry.account, entry.credential_key)
        self.d.cp(f"{kind}.defer.read!")
        current = j.deferral_current(key)
        with j.transaction():
            if (read is not None and read.limited and entry is not None and accounts is not None
                    and current is not None and current.number == n):
                n = self._supersede(op, current, accounts, entry, read)
            op = j.op_step(op.op_id, "defer.confirmed", {"defer": str(n), "wip_defer": op.data["defer"]})
        self.d.cp(f"{kind}.defer.confirmed")
        return op

    def _supersede(self, op: Op, current: DeferralRow, accounts: Accounts, entry: LaunchEntry,
                   read: TrustedRead) -> int:
        """`_confirm`'s confirmed half, inside its transaction. The read's windows are stored as trusted
        under the launch's key, the key is marked exhausted, and the gate runs again: `Chosen` is due now
        (the same pickup's undefer relaunches through the guard's pin), a `Deadline` waits for its time,
        `AccountChanged` for the operator. Returns the record the tail goes on with: the new trusted one,
        or `current` if the profile can't resolve now (the undefer's gate escalates that)."""
        j, s, now = self.d.journal, self.ws.usage, self.d.clock()
        for obs in read.observations:
            ingest_trusted(j, entry.credential_key, obs, now, s)
        mark_exhausted(j, entry.credential_key, read.reset_hint, now, s)
        try:
            verdict = decide(accounts, j, current.profile, self._previous_key(current.session_key), now, s)
        except ConfigError:
            return current.number
        if isinstance(verdict, AccountChanged):
            reason, until = ACCOUNT_CHANGED, None
        else:
            reason, until = QUOTA, verdict.at if isinstance(verdict, Deadline) else now
        n = j.deferral_next(current.session_key)
        j.deferral_insert(DeferralRow(current.session_key, n, op.bead, current.role, current.profile, reason,
                                      None if until is None else str(until), TRUSTED))
        return n
```

`LAUNCHED` joins the `heterodyne.wsd.launches` import.

In `src/heterodyne/wsd/scheduler.py`, add `capabilities` to the `heterodyne.wsd.park` import, `LimitSeen`, `Signal` and `UsageSeen` to the `heterodyne.wsd.runtime` import, and `ingest_untrusted` to the `heterodyne.wsd.usage` import. Add `MAX_HELD = 256` after the imports, and in `Scheduler.__init__`, after `self.wake_at`:

```python
        # Plan 4b E8: limit reports a failed pickup didn't handle, retried first by the next; never journaled.
        self.held: list[LimitSeen] = []
```
 In `_pickup`, replace the `expire` line with:

```python
            self.d.runtime.expire(name, now)      # §7: a session at its lifetime ends; the sweep resumes it
            self._signals(now)                    # plan 4b E8: after the lifetime stop, before the sweep
```

Add after `_pickup`:

```python
    def _signals(self, now: int) -> None:
        """Plan 4b E8: the limit reports held from a failed pickup, then the reports the workstream's
        sessions sent since the last pickup, oldest first. If handling one raises, it and the limit
        reports after it are held for the next pickup, and the error goes on."""
        batch: list[Signal] = [*self.held, *self.d.runtime.signals(self.ws.name)]
        self.held = []
        for i, signal in enumerate(batch):
            try:
                self._signal(signal, now)
            except BaseException:
                self.held = [s for s in batch[i:] if isinstance(s, LimitSeen)][-MAX_HELD:]
                raise

    def _signal(self, signal: Signal, now: int) -> None:
        """A usage report is stored untrusted only for a profile with (c); a limit report defers its own
        bead only with (e). A report for a launch the journal doesn't have as this workstream's is
        dropped. Handling a limit report twice is safe (`Parker.limit` opens nothing the second time)."""
        j, name = self.d.journal, self.ws.name
        entry = j.launch(signal.session_key, signal.generation)
        if entry is None or entry.ws != name:
            return
        caps = capabilities(self.ws.accounts, entry.profile)
        if isinstance(signal, UsageSeen):
            if caps.in_session_usage:
                for obs in signal.observations:
                    ingest_untrusted(j, entry.ident, obs, now, self.ws.usage)
        elif caps.limit_signal:
            self.parker.limit(signal.session_key, signal.generation, signal.reset_hint, now)
```

In the module docstring, change step 3 to begin: "3. the sessions' own reports are drained (plan 4b E8), then the sweep (`sweep.py`) reconciles beads, sessions and the journal: ...".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 1200 uv run pytest tests/test_wsd_defer.py tests/test_wsd_pickup.py tests/test_wsd_usage.py tests/test_wsd_headroom.py -q`
Expected: PASS. AU-4's crash tests still enumerate `DEFER_STEPS`, which is unchanged, so a tail without `limit_key` never reaches the two new points.

Then: `timeout 600 uv run pyright src tests/test_wsd_defer.py tests/wsd_env.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/wsd/park.py src/heterodyne/wsd/scheduler.py tests/wsd_env.py tests/test_wsd_defer.py
git commit -m "plan4b T9: drain the sessions' signals; defer on a limit and confirm it with the trusted read"
```

## Task 10: the account switch (handoff) and the stale-login path

AU-8, E9 and E10. When the guard pins a key other than the session's last launched key, and the profile has (f) but not (b), the spec asks for a handoff: no resume and no native ID, so the runtime starts fresh with D7's ID (Task 6). With (b), a switch stays an ordinary resume. A refusal that is exactly `LOGIN_STALE` spends no budget. Its key joins the op's `stale` keys, and every later pin of that op gates without them. Another permitted account gives `FAILED`, and the next pickup's replay launches on it. None left escalates `LAUNCH_FAILED` with the detail `LOGIN_STALE`.

**Files:**
- Modify: `src/heterodyne/wsd/park.py` (`stale_keys`, `_outcome`, `_stale`, `_pin`, `_pin_holds`, `_pin_and_dispatch`)
- Modify: `tests/fakes/fake_runtime.py` (`stale_keys`)
- Test: `tests/test_wsd_defer.py`

**Interfaces:**
- Consumes:
  - `LaunchSpec.credential_key` / `.handoff`, `LOGIN_STALE`, `decide(..., exclude=)`, `admitted(..., exclude=)` (Task 1).
  - The runtime's handoff and its `LOGIN_STALE` refusal (Task 6).
  - `park.capabilities` and the Task 9 test helpers `limited`, `spare`, `caps`, `Reader`, `reading`, `signal` (Task 9).
- Produces:
  - `park.stale_keys(op: Op) -> frozenset[str]`: the op's `stale` data, comma-separated keys.
  - `FakeRuntime.stale_keys: set[str]`: a launch pinned to one of these keys is refused with `LaunchFailed(LOGIN_STALE)`.

`LOGIN_STALE` is matched only as the whole error text of a `failed` refusal, the text Task 6's `_pinned` raises. A refusal with any other text spends the budget as before. `_stale` records the key and forgets the generation in one transaction, together with the row's reason. A crash after that transaction leaves the op open with its `stale` keys and no generation, so the replay's `_pin` gates without them. `_pin` applies the same "none left" rule as `_stale`, so a crash between the transaction and the escalation still escalates. The keys belong to the operation, so a later operation tries a refreshed login again.

- [ ] **Step 1: Write the failing tests**

In `tests/fakes/fake_runtime.py`, import `LOGIN_STALE` from `heterodyne.wsd.runtime`. In `__init__`, after `failing_beads`, add:

```python
        self.stale_keys: set[str] = set()        # a launch pinned to one of these keys: refused as stale
        self.attempts: list[LaunchSpec] = []     # every spec `launch` was given, refused ones included
```

In `launch`, before the `if self.launch_failures or spec.bead in self.failing_beads:` line, add:

```python
        self.attempts.append(spec)
        if spec.credential_key in self.stale_keys:
            raise LaunchFailed(LOGIN_STALE)
```

In `tests/test_wsd_defer.py`, add `LOGIN_STALE` to the `heterodyne.wsd.runtime` import, then append:

```python
# --- the account switch (plan 4b Task 10) ---

def key_of(rig: Rig, account: str) -> str:
    accounts = rig.ws.accounts
    assert isinstance(accounts, ConfiguredAccounts)
    return accounts.current_key("codex", account)


@pytest.mark.parametrize(("on", "handoff"), [
    ({"handoff_relaunch": True}, True),
    ({"cross_account_resume": True}, False),
    ({"cross_account_resume": True, "handoff_relaunch": True}, False),
], ids=["f", "b", "b-and-f"])
def test_a_switch_without_b_is_a_handoff_and_with_b_a_resume(tmp_path: Path, on: dict[str, bool],
                                                             handoff: bool) -> None:
    rig = rig_for(tmp_path)
    limited(rig, Reader(reading()), two=True, trusted_read=True, limit_signal=True, **on)
    assert signal(rig) is Outcome.RESUMED
    first, second = rig.runtime.launches
    assert (first.account, first.handoff, second.account) == ("default", False, "spare")
    assert (second.credential_key, second.handoff) == (key_of(rig, "spare"), handoff)
    assert second.resume is (not handoff)
    entry = rig.journal.launch(rig.key(B), 1)
    assert entry is not None and entry.native_id is not None
    assert second.native_id == (None if handoff else entry.native_id)
    completed(rig)


def test_a_relaunch_on_the_same_key_is_never_a_handoff(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    limited(rig, Reader(reading()), handoff_relaunch=True, trusted_read=True, limit_signal=True)
    assert signal(rig) is Outcome.DEFERRED
    rig.clock.advance(HOUR)
    assert rig.pickup() is Outcome.RESUMED
    second = rig.runtime.launches[1]
    assert (second.handoff, second.resume, second.credential_key) == (False, True, key_of(rig, "default"))


def fresh_only(rig: Rig, *stale: str, two: bool = True) -> None:
    """B ready on p-two (on `default` then `spare`, able to switch, if `two`), with the runtime refusing the
    logins of the `stale` accounts as stale."""
    if two:
        spare(rig)
        caps(rig, cross_account_resume=True)
    rig.restart(rig.cp)
    rig.runtime.stale_keys = {key_of(rig, a) for a in stale}
    rig.world.add(B)
    on_profile(rig, B, "p-two")


def pickup_ops(rig: Rig) -> list[tuple[str, dict[str, str]]]:
    return [(status, data) for kind, _, status, data in ops(rig) if kind == "pickup"]


def attempts(rig: Rig) -> list[int]:
    return [r[0] for r in rig.journal.db.execute("SELECT attempts FROM ops WHERE kind = 'pickup'")]


def test_a_stale_login_moves_on_to_the_next_account_without_spending_the_budget(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    fresh_only(rig, "default")
    assert rig.pickup() is Outcome.NOTHING
    row = rig.journal.state(WS, B)
    assert row is not None and (row.reason, row.detail) == (Reason.LAUNCH_FAILED, LOGIN_STALE)
    [(status, data)] = pickup_ops(rig)
    assert status == "open" and data["stale"] == key_of(rig, "default") and "generation" not in data
    assert rig.pickup() is Outcome.BUSY
    assert [s.account for s in rig.runtime.launches] == ["spare"] and rig.runtime.calls == 2
    assert attempts(rig) == [0] and rig.state(B) == BeadState.RUNNING.value
    completed(rig)


def test_a_stale_login_with_no_other_account_needs_a_human(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    fresh_only(rig, "default", two=False)
    rig.pickup()
    row = rig.journal.state(WS, B)
    assert row is not None
    assert (row.state, row.reason, row.detail) == (BeadState.STUCK, Reason.LAUNCH_FAILED, LOGIN_STALE)
    assert NEEDS_HUMAN in rig.world.beads[B].labels
    assert rig.runtime.calls == 1 and attempts(rig) == [0] and rig.journal.ops_open(WS) == []


def test_each_stale_login_is_tried_once_then_a_human(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    fresh_only(rig, "default", "spare")
    rig.pickup()
    rig.pickup()
    rig.pickup()
    row = rig.journal.state(WS, B)
    assert row is not None
    assert (row.state, row.reason, row.detail) == (BeadState.STUCK, Reason.LAUNCH_FAILED, LOGIN_STALE)
    assert rig.runtime.calls == 2 and rig.runtime.launches == []
    [(status, data)] = pickup_ops(rig)
    assert status == "stuck"
    assert data["stale"] == ",".join(sorted([key_of(rig, "default"), key_of(rig, "spare")]))


def test_another_refusal_still_spends_the_budget_and_marks_nothing_stale(tmp_path: Path) -> None:
    rig = rig_for(tmp_path)
    fresh_only(rig)
    rig.runtime.launch_failures = 1
    rig.pickup()
    assert attempts(rig) == [1]
    [(_, data)] = pickup_ops(rig)
    assert "stale" not in data


def spec_view(rig: Rig) -> list[tuple[object, ...]]:
    """What each launch attempt asked for: the account, whether the key is that account's in this rig (keys
    are derived from each rig's own paths, so two rigs' keys never compare equal), a handoff, a resume
    and the native ID."""
    return [(a.account, a.credential_key == key_of(rig, a.account), a.handoff, a.resume, a.native_id,
             a.generation) for a in rig.runtime.attempts]


def stale_points(tmp_path: Path, scenario: Scenario) -> tuple[str, ...]:
    """The stale step and the outcome points around it, in the order the uncrashed run passes them."""
    seen = run(tmp_path / "points", scenario).seen
    points = tuple(dict.fromkeys(p for p in seen if p.rsplit(".", 1)[1] in ("outcome", "outcome!", "stale")))
    assert any(p.endswith(".stale") for p in points)
    return points


def same_specs(tmp_path: Path, scenario: Scenario) -> Run:
    """`same_end` at each stale point, and every replay attempts exactly the uncrashed run's launches."""
    points = stale_points(tmp_path, scenario)
    clean = same_end(tmp_path / "end", scenario, points)
    want = spec_view(clean.rig)
    for i, point in enumerate(points):
        assert spec_view(run(tmp_path / f"specs-{i}", scenario, point).rig) == want, point
    return clean


def stale_handoff(rig: Rig) -> None:
    """B limited on `default`, able to switch only by a handoff, and `spare`'s login stale."""
    limited(rig, Reader(reading()), two=True, handoff_relaunch=True, trusted_read=True, limit_signal=True)
    rig.runtime.stale_keys = {key_of(rig, "spare")}


STALE = Scenario(lambda r: fresh_only(r, "default"), lambda r: r.pickup())
STALE_ALONE = Scenario(lambda r: fresh_only(r, "default", two=False), lambda r: r.pickup())
ALL_STALE = Scenario(lambda r: fresh_only(r, "default", "spare"), lambda r: [r.pickup() for _ in range(3)])
HANDOFF_STALE = Scenario(stale_handoff, lambda r: (signal(r), r.pickup()))


def test_a_crash_after_a_stale_refusal_replays_to_the_next_account(tmp_path: Path) -> None:
    clean = same_specs(tmp_path, STALE)
    assert [s.account for s in clean.rig.runtime.launches] == ["spare"]
    assert [(a.account, a.handoff, a.resume) for a in clean.rig.runtime.attempts] == [
        ("default", False, False), ("spare", False, False)]


def test_a_crash_after_a_stale_refusal_with_no_other_account_still_needs_a_human(tmp_path: Path) -> None:
    clean = same_specs(tmp_path, STALE_ALONE)
    row = clean.rig.journal.state(WS, B)
    assert row is not None and (row.state, row.detail) == (BeadState.STUCK, LOGIN_STALE)
    assert [a.account for a in clean.rig.runtime.attempts] == ["default"]


def test_a_crash_when_every_login_is_stale_tries_each_once(tmp_path: Path) -> None:
    clean = same_specs(tmp_path, ALL_STALE)
    assert [a.account for a in clean.rig.runtime.attempts] == ["default", "spare"]
    assert clean.rig.runtime.launches == []


def test_a_crash_after_a_stale_handoff_resumes_nothing(tmp_path: Path) -> None:
    """The handoff to `spare` is refused as stale, and `default` is at its limit, so the bead waits for
    the reset; nothing runs on `spare`. Every replay asked for the same handoff: fresh, with no resume
    and no native ID."""
    clean = same_specs(tmp_path, HANDOFF_STALE)
    first, handoff = clean.rig.runtime.attempts
    assert (first.account, first.handoff) == ("default", False)
    assert (handoff.account, handoff.handoff, handoff.resume, handoff.native_id) == ("spare", True, False, None)
    assert [s.account for s in clean.rig.runtime.launches] == ["default"]
    assert records(clean.rig)[-1].reason == QUOTA
```

The rig's p-two is a Codex profile, so a handoff's native ID is None; the Claude form of D7's fresh ID is Task 6's to test. `run`, `Run.seen` and `same_end` are this file's existing crash helpers.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 uv run pytest tests/test_wsd_defer.py -q -k "switch or handoff or stale or refusal"`
Expected: FAIL. The switch test fails on `second.handoff` (False where True is wanted), and the stale tests fail on `data["stale"]` (`KeyError: 'stale'`) and on the budget (`attempts(rig) == [1]`).

- [ ] **Step 3: Implement**

In `src/heterodyne/wsd/park.py`, add `LOGIN_STALE` to the `heterodyne.wsd.runtime` import. After `capabilities`, add:

```python
def stale_keys(op: Op) -> frozenset[str]:
    """The keys whose logins the runtime refused as stale during this operation (plan 4b E9)."""
    return frozenset(filter(None, op.data.get("stale", "").split(",")))
```

In `_outcome`, after the `if receipt.refusal == "unavailable":` block, add:

```python
        if receipt.refusal == "failed" and receipt.error == LOGIN_STALE:
            return self._stale(op, entry)
```

Add after `_outcome`:

```python
    def _stale(self, op: Op, entry: LaunchEntry) -> Launch:
        """E9: the runtime's freshness gate refused the pinned login. Its key joins the op's `stale` keys as
        the generation is forgotten, in one transaction with the row's reason (`{kind}.stale`), so every
        later pin of this op gates without it. No budget is spent: another permitted account gives FAILED,
        and the next pickup's replay pins it; none left escalates (`_pin` applies the same rule on a
        replay)."""
        j, ws = self.d.journal, self.ws.name
        stale = stale_keys(op) | {entry.credential_key}
        with j.transaction():
            op = j.op_forget(op.op_id, "generation")
            op = j.op_step(op.op_id, op.step, {"stale": ",".join(sorted(stale))})
            row = j.state(ws, op.bead)
            j.set_state(ws, op.bead, row.state if row else BeadState.STUCK, Reason.LAUNCH_FAILED, LOGIN_STALE)
        self.d.cp(f"{op.kind.value}.stale")
        accounts = self.ws.accounts
        try:
            previous = self._previous_key(entry.session_key)
            verdict = None if accounts is None else decide(
                accounts, j, entry.profile, previous, self.d.clock(), self.ws.usage, exclude=stale)
        except ConfigError:
            verdict = None                   # the replay's pin escalates it as CONFIG_INVALID
        if isinstance(verdict, AccountChanged):
            self.escalate_from(op, Reason.LAUNCH_FAILED, LOGIN_STALE)
            return Launch.ENDED
        return Launch.FAILED
```

In `_pin`, replace the `try: chosen = decide(...)` block and the `AccountChanged | Deadline` check with:

```python
        stale = stale_keys(op)
        try:
            chosen = decide(accounts, j, rec.profile, self._previous_key(rec.session_key), self.d.clock(),
                            self.ws.usage, exclude=stale)
        except ConfigError as exc:
            self.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
            return Launch.ENDED
        if isinstance(chosen, AccountChanged) and stale:      # E9: every permitted login was stale
            self.escalate_from(op, Reason.LAUNCH_FAILED, LOGIN_STALE)
            return Launch.ENDED
        if isinstance(chosen, AccountChanged | Deadline):
            until = chosen.at if isinstance(chosen, Deadline) else None
            self._defer_within(op, rec, QUOTA if until is not None else ACCOUNT_CHANGED, until)
            return Launch.ENDED
```

Replace `_pin_holds` with:

```python
    def _pin_holds(self, op: Op, accounts: Accounts, rec: SessionRecord, entry: LaunchEntry) -> bool:
        """Step 3, immediately before dispatch: the pinned account is still permitted under its pinned key,
        less the op's stale keys, and nothing blocks it (`admits`, through the same view and cache as step
        2). A key that can't be resolved has changed."""
        return admitted(accounts, self.d.journal, self._previous_key(rec.session_key), entry, self.d.clock(),
                        self.ws.usage, exclude=stale_keys(op))
```

and its call in `_pin_and_dispatch` with `if self._pin_holds(op, accounts, rec, entry):           # step 3`.

In `_pin_and_dispatch`, replace the lines from `launched = [e for e in j.launches(rec.session_key) if e.outcome == LAUNCHED]` to the end of the `spec = LaunchSpec(...)` call with:

```python
        launched = [e for e in j.launches(rec.session_key) if e.outcome == LAUNCHED]
        previous = launched[-1].credential_key if launched else None
        caps = capabilities(accounts, rec.profile)
        # D7, plan 4b E10: a new key without (b) is a handoff, which starts fresh and resumes nothing.
        handoff = (previous is not None and previous != entry.credential_key and caps.handoff_relaunch
                   and not caps.cross_account_resume)
        # A resume continues the latest launched generation's native session; Claude's first launch uses
        # the session key as its native ID, and Codex's is unknown until its thread starts.
        first = rec.session_key if adapter == "claude-code" else None
        native = None if handoff else launched[-1].native_id if launched else first
        spec = LaunchSpec(ws, op.bead, rec.role, rec.profile, rec.session_key, label(shown, rec.role),
                          worktree, resume=op.kind is OpKind.RESUME and not handoff,
                          ref=op.data.get("ref") or None, generation=entry.generation, native_id=native,
                          account=entry.account, credential_key=entry.credential_key, handoff=handoff,
                          model=entry.model_passed, repo=Path(rec.repo))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 1200 uv run pytest tests/test_wsd_defer.py tests/test_wsd_park.py tests/test_wsd_pickup.py tests/test_wsd_recovery.py -q`
Expected: PASS.

Then: `timeout 600 uv run pyright src tests/test_wsd_defer.py tests/fakes/fake_runtime.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/wsd/park.py tests/fakes/fake_runtime.py tests/test_wsd_defer.py
git commit -m "plan4b T10: handoff relaunches without (b), and a stale login moves on to the next account"
```

## Task 11: the capability table (G2-gated)

AU-6 and AU-7 "fill the table". D9: only a capability S7 **demonstrated on a real account**, on the adapter's pinned CLI version, is enabled. This task has two parts.

- **Part A** can merge before G2, because it enables nothing. It adds the evidence record and an invariant check that every later table edit must pass.
- **Part B** is one commit per accepted finding, made only after the G2 bead records that the operator accepted it.

**Files:**
- Modify: `src/heterodyne/config/capabilities.py` (`Evidence`, `EVIDENCE`, `table_problems` and the `PROBLEM_*` texts; then the table entries)
- Test: `tests/test_config_capabilities.py` (new); `tests/test_config_accounts.py` (`test_capability_table_is_empty` replaced)

**Interfaces:**
- Consumes:
  - `Capabilities`, `CAPABILITIES` and `LOGIN_FILES`, as they are now.
  - `Adapter.version_pin` through `heterodyne.agents.registry.ADAPTERS`, in the test only. `config/` doesn't import `agents/`, and this task keeps it that way: the pins are a parameter.
- Produces:
  - `Evidence(runs: str, version: str)`: the S7 runs that showed one capability (for example `"L3"`), and the CLI version they ran on.
  - `EVIDENCE: Mapping[str, Mapping[str, Evidence]]`: adapter, then `Capabilities` field name.
  - `table_problems(table, evidence, pins) -> tuple[str, ...]`: an empty tuple, or one line per broken rule, each starting with a `PROBLEM_*` text.

The rules `table_problems` enforces:

1. Every adapter in the table or in the evidence has a pin, and so is a known adapter.
2. Every enabled field has evidence, and no evidence names a field that is off.
3. The evidence's version equals the adapter's pin. D9 is per pinned version, so a pin bump fails the suite until the runs are repeated, or the capability is turned off.
4. Any enabled field needs (a). Without a working login binding nothing else was demonstrated (S7 "Recommended D9 defaults": "This is the first gate").
5. Claude never has (d): S7 found no source, and `get_usage` is ruled out by §2. Only a new ADR revision can change this rule.
6. Nobody has (f) yet: a handoff needs the §4.1 generator (Q3), which plan 5 builds. Plan 5 removes this rule when it lands.

### Part A: the evidence record and the invariants

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_capabilities.py`:

```python
"""The D9 capability table's invariants (plan 4b Task 11): evidence per enabled field, on the pinned CLI."""

import pytest

from heterodyne.agents.registry import ADAPTERS
from heterodyne.config import capabilities
from heterodyne.config.capabilities import (
    CAPABILITIES,
    EVIDENCE,
    PROBLEM_NEEDS_BINDING,
    PROBLEM_NO_EVIDENCE,
    PROBLEM_NO_GENERATOR,
    PROBLEM_NO_SOURCE,
    PROBLEM_STALE_EVIDENCE,
    PROBLEM_UNKNOWN_ADAPTER,
    PROBLEM_WRONG_VERSION,
    Capabilities,
    Evidence,
    table_problems,
)

PINS = {"claude-code": "2.1.286", "codex": "0.160.0"}
BOUND = Evidence("L1,L2", "0.160.0")


def test_the_shipped_table_keeps_every_rule() -> None:
    pins = {name: adapter.version_pin for name, adapter in ADAPTERS.items()}
    assert table_problems(CAPABILITIES, EVIDENCE, pins) == ()


def test_the_pins_here_are_the_adapters_pins() -> None:
    """The fixtures below use literal pins; this keeps them honest."""
    assert {name: adapter.version_pin for name, adapter in ADAPTERS.items()} == PINS


def test_an_empty_table_needs_no_evidence() -> None:
    assert table_problems({}, {}, PINS) == ()


def test_a_bound_login_with_its_evidence_is_accepted() -> None:
    table = {"codex": Capabilities(login_binding=True)}
    assert table_problems(table, {"codex": {"login_binding": BOUND}}, PINS) == ()


@pytest.mark.parametrize(("table", "evidence", "problem"), [
    ({"gemini": Capabilities()}, {}, PROBLEM_UNKNOWN_ADAPTER),
    ({}, {"gemini": {"login_binding": BOUND}}, PROBLEM_UNKNOWN_ADAPTER),
    ({"codex": Capabilities(login_binding=True)}, {}, PROBLEM_NO_EVIDENCE),
    ({"codex": Capabilities(login_binding=True)},
     {"codex": {"login_binding": BOUND, "trusted_read": Evidence("L7", "0.160.0")}}, PROBLEM_STALE_EVIDENCE),
    ({"codex": Capabilities(login_binding=True)}, {"codex": {"login_binding": Evidence("L1,L2", "0.157.0")}},
     PROBLEM_WRONG_VERSION),
    ({"codex": Capabilities(in_session_usage=True)},
     {"codex": {"in_session_usage": Evidence("L2", "0.160.0")}}, PROBLEM_NEEDS_BINDING),
    ({"claude-code": Capabilities(login_binding=True, trusted_read=True)},
     {"claude-code": {"login_binding": Evidence("L1,L2", "2.1.286"),
                      "trusted_read": Evidence("L7", "2.1.286")}}, PROBLEM_NO_SOURCE),
    ({"codex": Capabilities(login_binding=True, handoff_relaunch=True)},
     {"codex": {"login_binding": BOUND, "handoff_relaunch": Evidence("L4", "0.160.0")}},
     PROBLEM_NO_GENERATOR),
], ids=["unknown-in-table", "unknown-in-evidence", "no-evidence", "stale-evidence", "wrong-version",
        "needs-binding", "claude-trusted-read", "handoff"])
def test_each_broken_rule_is_named(table: dict[str, Capabilities], evidence: dict[str, dict[str, Evidence]],
                                   problem: str) -> None:
    found = table_problems(table, evidence, PINS)
    assert len(found) == 1 and found[0].startswith(problem), found


def test_a_problem_names_the_adapter_and_the_field_only() -> None:
    [found] = table_problems({"codex": Capabilities(login_binding=True)}, {}, PINS)
    assert found == f"{PROBLEM_NO_EVIDENCE}: codex.login_binding"


def test_every_field_is_a_capability_letter() -> None:
    """The rules name fields by their dataclass names; a renamed field must fail here, not pass silently."""
    assert capabilities.FIELDS == ("login_binding", "cross_account_resume", "in_session_usage",
                                   "trusted_read", "limit_signal", "handoff_relaunch")
```

In `tests/test_config_accounts.py`, replace `test_capability_table_is_empty` with:

```python
def test_capability_switching() -> None:
    """The table's contents are checked by tests/test_config_capabilities.py (plan 4b Task 11)."""
    assert not capabilities.NONE.can_switch
    assert Capabilities(handoff_relaunch=True).can_switch
    assert Capabilities(cross_account_resume=True).can_switch
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_config_capabilities.py -q`
Expected: FAIL with `ImportError: cannot import name 'EVIDENCE' from 'heterodyne.config.capabilities'`.

- [ ] **Step 3: Implement**

In `src/heterodyne/config/capabilities.py`, change `from dataclasses import dataclass` to `from dataclasses import dataclass, fields`. Replace the `CAPABILITIES` comment and assignment with:

```python
@dataclass(frozen=True)
class Evidence:
    """The S7 real-account runs that demonstrated one capability, and the CLI version they ran on (D9)."""
    runs: str            # S7's run ids, e.g. "L1,L2"
    version: str         # must equal the adapter's version pin


# Each entry is enabled by its own commit, after the G2 bead records the operator's acceptance of the
# finding; the commit message names the G2 bead and the S7 commit (plan 4b Task 11 Part B).
CAPABILITIES: Mapping[str, Capabilities] = {}
EVIDENCE: Mapping[str, Mapping[str, Evidence]] = {}
NONE = Capabilities()
FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Capabilities))

PROBLEM_UNKNOWN_ADAPTER = "no pinned adapter"
PROBLEM_NO_EVIDENCE = "enabled without evidence"
PROBLEM_STALE_EVIDENCE = "evidence for a capability that is off"
PROBLEM_WRONG_VERSION = "evidence from another CLI version"
PROBLEM_NEEDS_BINDING = "enabled without (a) login binding"
PROBLEM_NO_SOURCE = "claude-code has no (d) source"
PROBLEM_NO_GENERATOR = "(f) needs the handoff generator"


def table_problems(table: Mapping[str, Capabilities], evidence: Mapping[str, Mapping[str, Evidence]],
                   pins: Mapping[str, str]) -> tuple[str, ...]:
    """The D9 rules every table edit keeps (plan 4b Task 11). Each line names an adapter and a field."""
    found: list[str] = []
    for adapter in sorted(set(table) | set(evidence)):
        caps, shown = table.get(adapter, NONE), evidence.get(adapter, {})
        if adapter not in pins:
            found.append(f"{PROBLEM_UNKNOWN_ADAPTER}: {adapter}")
            continue
        on = {name for name in FIELDS if getattr(caps, name)}
        found += [f"{PROBLEM_NO_EVIDENCE}: {adapter}.{name}" for name in sorted(on - set(shown))]
        found += [f"{PROBLEM_STALE_EVIDENCE}: {adapter}.{name}" for name in sorted(set(shown) - on)]
        found += [f"{PROBLEM_WRONG_VERSION}: {adapter}.{name}" for name in sorted(shown)
                  if shown[name].version != pins[adapter]]
        if on and not caps.login_binding:
            found.append(f"{PROBLEM_NEEDS_BINDING}: {adapter}")
        if adapter == "claude-code" and caps.trusted_read:
            found.append(f"{PROBLEM_NO_SOURCE}: {adapter}.trusted_read")
        if caps.handoff_relaunch:
            found.append(f"{PROBLEM_NO_GENERATOR}: {adapter}.handoff_relaunch")
    return tuple(found)
```

`config/` already names `claude-code` in `LOGIN_FILES` and `DEFAULT_LOGIN_DIRS`, so rule 5 follows the existing style. The "no adapter names" constraint covers `wsd/` and `sandbox/` only.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_config_capabilities.py tests/test_config_accounts.py -q`
Expected: PASS.

Then: `timeout 600 uv run pyright src/heterodyne/config tests/test_config_capabilities.py` and `uv run ruff check src tests`.
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/config/capabilities.py tests/test_config_capabilities.py tests/test_config_accounts.py
git commit -m "plan4b T11a: capability evidence and the D9 table invariants"
```

### Part B: enabling accepted findings (only after G2)

Each step below is one commit and one PR. A step starts only when the G2 bead's close evidence lists that finding as accepted, on the adapter's current pin. Skip a step whose finding was not accepted. The order matters, because rule 4 requires (a) to be enabled first.

The S7 runs map to fields as follows:

| Field | Claude (`claude-code`) | Codex (`codex`) |
|---|---|---|
| `login_binding` (a) | L1, L2 | L1, L2 |
| `cross_account_resume` (b) | L3 | L3 |
| `in_session_usage` (c) | L2 | L2 |
| `trusted_read` (d) | never (rule 5) | L7, with L6's refresh outcome recorded in S7 |
| `limit_signal` (e) | L5 | L5 |
| `handoff_relaunch` (f) | after plan 5 (rule 6) | after plan 5 (rule 6) |

- [ ] **Step 6: Enable (a) for each accepted adapter**

For Codex, set the table and evidence in `src/heterodyne/config/capabilities.py` to:

```python
CAPABILITIES: Mapping[str, Capabilities] = {"codex": Capabilities(login_binding=True)}
EVIDENCE: Mapping[str, Mapping[str, Evidence]] = {"codex": {"login_binding": Evidence("L1,L2", "0.160.0")}}
```

For Claude, add `"claude-code": Capabilities(login_binding=True)` and `"claude-code": {"login_binding": Evidence("L1,L2", "2.1.286")}` beside the Codex entries. Each later step adds `True` fields to the same `Capabilities(...)` call and an `Evidence` to the same inner mapping. For example, the Codex end state, if every Codex finding is accepted, is:

```python
CAPABILITIES: Mapping[str, Capabilities] = {
    "codex": Capabilities(login_binding=True, cross_account_resume=True, in_session_usage=True,
                          trusted_read=True, limit_signal=True),
}
EVIDENCE: Mapping[str, Mapping[str, Evidence]] = {
    "codex": {"login_binding": Evidence("L1,L2", "0.160.0"),
              "cross_account_resume": Evidence("L3", "0.160.0"),
              "in_session_usage": Evidence("L2", "0.160.0"), "trusted_read": Evidence("L7", "0.160.0"),
              "limit_signal": Evidence("L5", "0.160.0")},
}
```

Then, for every step:

Run: `timeout 300 uv run pytest tests/test_config_capabilities.py tests/test_config_accounts.py -q`
Expected: PASS. `test_the_shipped_table_keeps_every_rule` is the gate.

Then run the full suite: `timeout 3600 uv run pytest -q`.
Expected: PASS. Enabling (a) makes `config check` accept `accounts` for that adapter. The tests that monkeypatch `CAPABILITIES` keep their own tables. A test that relied on the shipped table being empty fails here. Fix that test to inject `capabilities=NONE`-style tables, as `tests/test_config_accounts.py` already does with `ON`. Never weaken the rule it checked.

Commit, one per step:

```bash
git add src/heterodyne/config/capabilities.py
git commit -m "plan4b T11b: enable codex (a) login binding (S7 L1,L2 on 0.160.0; G2 <bead id>)"
```

- [ ] **Step 7: Enable (c), then (e), then (b), then Codex (d), each as its own commit, as in Step 6.** Codex `failover = "next"` passes `config check` once (d) is on: the existing `config/accounts.py` check requires `trusted_read` only. S7 says more: "Codex gets `next` only once (d) and either (b) or (f) are on". Without (b) or (f), `permitted` keeps a session on its previous account (`can_switch` is false), so `"next"` moves only new sessions. This task doesn't change the check, and the open question Q9 asks whether it should.

## Task 12: the live accounts test (G2-gated, after Task 11 and plan 4c)

The real-account counterpart of Tasks 6–10, run on the shipped capability table and on the managed installs plan 4c selects. Each test needs one capability. A test is skipped when that capability is off in `CAPABILITIES`, so the suite proves what the table claims, never more. It is opt-in: `HZ_LIVE=1` (the conftest), `HZ_LIVE_SANDBOX=1` and `HZ_LIVE_ACCOUNTS=1`. It also has the S5 host setup and the `HZ_LIVE_SANDBOX_*` variables that `tests/live/test_live_sandbox.py` reads.

The operator names a second login for each adapter under test. `HZ_LIVE_ACCOUNTS_CODEX` and `HZ_LIVE_ACCOUNTS_CLAUDE` each hold a configured login directory as `config.toml` would write it, such as `~/.codex-b`. The test never opens, hashes or prints a login file. Only the runtime and the reader read them. The test checks a host login file's identity (`st_dev`, `st_ino`) and nothing else. Evidence lines carry session keys, window IDs and percentages, never a path, a model reply or a token.

Model budget (testing only): `test_live_sandbox.MODELS`. That is Claude `claude-haiku-4-5-20251001`, and Codex `gpt-6-luna` through the `p-live-codex` profile at `model_reasoning_effort="low"`. Every prompt is one short line. The opportunistic (e) test runs only on an account the operator says is already at its limit (`HZ_LIVE_ACCOUNTS_LIMITED`). It never spends quota to get there.

**Files:**
- Create: `tests/live/test_live_accounts.py`
- Modify: `docs/wsd.md` (§5 Runtime, after the paragraph on plan 4's live gate: the variables and what each test shows)

**Interfaces:**
- Consumes:
  - From `tests/live/test_live_sandbox.py`: `Live`, `PinnedSelfTest`, `preflight`, `host_config`, `tiny_repo`, `teardown`, `wait_until`, `MODELS`, `PROFILES` and `PROMPT_SECONDS`, imported by name. `tests/live` is on `sys.path` (the conftest), and importing by name brings in no test and no fixture.
  - `RuntimeConfig.credlocks`, `LaunchSpec.account` and `.credential_key` (Tasks 1 and 6); `SandboxRuntime.signals` (Task 7); `HostUsageReader` (Task 8); `CAPABILITIES` (Task 11).
  - From `tests/sandbox_env.py`: `sandbox_teardown(runtime, keys, listed)` (exists), the module teardown's check, run here per test.
- Produces: nothing later tasks use.

Every session a test launches is stopped in a `finally` (`running`) and checked by `sandbox_teardown` before the test can pass. A wait that runs out fails the test, because each wait's result is asserted. Failure messages carry statuses, exception type names, account names and hostread's fixed diagnostics, never a pane, a reply or a login.

- [ ] **Step 1: Write the live test**

Create `tests/live/test_live_accounts.py`:

```python
"""Live checks of plan 4b on real accounts: a named login bound, its usage read in the session and on the
host, and a resume on another account. Needs HZ_LIVE=1 (the conftest), HZ_LIVE_SANDBOX=1 and
HZ_LIVE_ACCOUNTS=1, the S5 host setup, and a second login per adapter under test (HZ_LIVE_ACCOUNTS_CODEX,
HZ_LIVE_ACCOUNTS_CLAUDE: a configured login directory, such as ~/.codex-b). Each test runs only if the
shipped capability table enables what it checks. It makes real model calls on the live-test models, one
short prompt each, and prints nothing from any login.

Run it only while no wsd on this host uses the OpenShell runtime (see test_live_sandbox)."""

import contextlib
import logging
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))     # tests/: sandbox_env, wsd_env

from probe_verdicts import SELFTEST_FAILED, launch_shown  # noqa: E402
from sandbox_env import config_env, sandbox_teardown  # noqa: E402
from test_live_sandbox import (  # noqa: E402
    MODELS,
    PROFILES,
    PROMPT_SECONDS,
    Live,
    PinnedSelfTest,
    host_config,
    models_used,
    preflight,
    teardown,
    tiny_repo,
    wait_until,
)

from heterodyne.agents.codex import HOST_SOURCE  # noqa: E402
from heterodyne.config import ConfigError  # noqa: E402
from heterodyne.config.accounts import Account, canonical_login, credential_key  # noqa: E402
from heterodyne.config.capabilities import CAPABILITIES, NONE  # noqa: E402
from heterodyne.sandbox import hostread  # noqa: E402
from heterodyne.sandbox.backend import BackendError, BackendUnavailable  # noqa: E402
from heterodyne.sandbox.hostread import HostUsageReader  # noqa: E402
from heterodyne.sandbox.openshell import OpenShellBackend, tool_env  # noqa: E402
from heterodyne.sandbox.runtime import RuntimeConfig, SandboxRuntime  # noqa: E402
from heterodyne.sandbox.settings import sandbox_settings  # noqa: E402
from heterodyne.tmux import Tmux  # noqa: E402
from heterodyne.wsd.accounts import ConfiguredAccounts, ProfileAccounts  # noqa: E402
from heterodyne.sandbox.spec import sandbox_name  # noqa: E402
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, LimitSeen, Signal, Started, UsageSeen  # noqa: E402
from heterodyne.wsd.workstream import utc_now  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("HZ_LIVE_SANDBOX") != "1" or os.environ.get("HZ_LIVE_ACCOUNTS") != "1",
    reason="live accounts checks: set HZ_LIVE_ACCOUNTS=1 (and HZ_LIVE=1, HZ_LIVE_SANDBOX=1)")
ADAPTER = {"claude": "claude-code", "codex": "codex"}
SECOND = {"claude": "HZ_LIVE_ACCOUNTS_CLAUDE", "codex": "HZ_LIVE_ACCOUNTS_CODEX"}
OTHER = "b"                                   # the second login's account name
LOGIN_FILE = {"claude": ".credentials.json", "codex": "auth.json"}
EXEC_SECONDS = 60


def need(cli: str, capability: str, second: bool = False) -> None:
    """Skip unless the shipped table enables `capability` for `cli` (and, if `second`, a second login is
    named). A skip says which; nothing here enables a capability."""
    if not getattr(CAPABILITIES.get(ADAPTER[cli], NONE), capability):
        pytest.skip(f"{ADAPTER[cli]}.{capability} is off in the capability table")
    if second and not os.environ.get(SECOND[cli]):
        pytest.skip(f"{SECOND[cli]} names no second login")


def named(env: dict[str, str]) -> dict[tuple[str, str], Account]:
    """The second logins, as AU-2 resolves them. A named directory that doesn't resolve fails the module,
    naming the variable only."""
    found: dict[tuple[str, str], Account] = {}
    for cli, var in SECOND.items():
        configured = os.environ.get(var)
        if not configured:
            continue
        adapter = ADAPTER[cli]
        try:
            login_dir, files = canonical_login(OTHER, adapter, configured, env)
        except ConfigError:
            pytest.fail(f"{var} doesn't resolve to a login directory")
        if not all(f.is_file() for f in files):
            pytest.fail(f"{var} has no {LOGIN_FILE[cli]}")
        found[(adapter, OTHER)] = Account(OTHER, adapter, configured, login_dir, files,
                                          credential_key(adapter, files))
    return found


@pytest.fixture(scope="module")
def live() -> Iterator[Live]:
    real_home = Path.home()
    canary = real_home / ".heterodyne-live-canary"
    root = Path(tempfile.mkdtemp(prefix="hz", dir=tempfile.gettempdir()))
    live: Live | None = None
    try:
        env = config_env(root / "c", host_config(),
                         {"alpha": '[sandbox]\nextra_egress = ["github.com", "pypi.org"]\n'})
        settings = sandbox_settings(env)
        tmux = Tmux(f"hz-live-ac-{uuid.uuid4().hex[:8]}")
        backend = OpenShellBackend(settings.openshell, settings.podman, settings.image,
                                   tool_env(os.environ, settings.tool_env))
        artifact = preflight(settings, backend.env)
        print("\n".join(artifact.lines))
        home = {"HOME": str(real_home)}
        logins = named(home)
        profiles: dict[str, str | ProfileAccounts] = {
            PROFILES[cli]: ProfileAccounts(ADAPTER[cli], ("default", OTHER) if (ADAPTER[cli], OTHER) in logins
                                           else ("default",), "none")
            for cli in ADAPTER}
        accounts = ConfiguredAccounts(profiles, home, logins)       # the shipped capability table
        config = RuntimeConfig(
            sessions=root / "s", credlocks=root / "k", settings=settings, accounts=accounts,
            real_home=real_home, real_home_canary=canary, wsd_socket=root / "ctl.sock", uid=os.getuid(),
            gid=os.getgid(), tmux=tmux, path=os.environ.get("PATH", os.defpath), clock=utc_now)
        live = Live(root, SandboxRuntime(config, backend, PinnedSelfTest()), backend, tmux,
                    tiny_repo(root / "r"), artifact)
        yield live
    finally:
        uncertain = teardown(live) if live is not None else []
        if uncertain:                   # the scratch's own name only: never a host path
            pytest.fail(f"live teardown is uncertain; kept the temp directory's {root.name} and tmux server "
                        f"{live.tmux.socket_name if live else None}: " + "; ".join(uncertain))
        if live is not None:
            live.tmux.kill_server()
        canary.unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)
        if os.path.lexists(root):
            pytest.fail(f"the live scratch {root.name} in the temp directory could not be removed")


def accounts_of(live: Live) -> ConfiguredAccounts:
    found = live.runtime.c.accounts
    assert isinstance(found, ConfiguredAccounts)
    return found


def on(live: Live, cli: str, bead: str, account: str) -> LaunchSpec:
    """`Live.spec`, pinned to `account` under its current key, as the guard pins it."""
    key = accounts_of(live).current_key(ADAPTER[cli], account)
    return replace(live.spec(cli, bead), account=account, credential_key=key)


def identity(live: Live, cli: str, account: str) -> tuple[int, int]:
    """The host login file's device and inode: metadata only, never its contents."""
    _, [login] = accounts_of(live).login_paths(ADAPTER[cli], account)
    st = login.resolve(strict=True).stat()
    return st.st_dev, st.st_ino


def signals_for(live: Live, key: str, kind: type[Signal], seconds: float) -> list[Signal]:
    """Drain the runtime's signals until one of `kind` arrives for `key`, or `seconds` pass."""
    seen: list[Signal] = []
    wait_until(lambda: bool(seen.extend(live.runtime.signals("alpha")) or
                            any(isinstance(s, kind) and s.session_key == key for s in seen)), seconds)
    return [s for s in seen if isinstance(s, kind) and s.session_key == key]


def say(live: Live, key: str, text: str) -> None:
    live.tmux.paste(live.record(key).tmux_session, text)


def answered(live: Live, key: str, text: str) -> bool:
    """Say `text` and wait, up to PROMPT_SECONDS, for the turn it starts to end, by the session's own
    turn-end count. The pane is never the evidence: it shows the prompt itself, and a resumed
    session's history."""
    before = live.runtime.servers[key].turns().stops
    say(live, key, text)
    return wait_until(lambda: live.runtime.servers[key].turns().stops > before, PROMPT_SECONDS)


@contextlib.contextmanager
def running(live: Live, spec: LaunchSpec) -> Iterator[Started]:
    """Launch `spec` and, whatever the test does, stop it. The module's own teardown check then runs
    on this session alone: the end is confirmed, no create is unresolved, no watcher is left, and its
    sandboxes are gone from the backend's list. Any of those uncertain fails the test."""
    try:
        yield live.launch(spec)
    finally:
        uncertain = sandbox_teardown(live.runtime, [spec.session_key], live.backend.names)
        if uncertain:
            pytest.fail(f"{spec.session_key}'s teardown is uncertain: " + "; ".join(uncertain))


def launch_limited(live: Live, spec: LaunchSpec) -> Started | None:
    """A launch on an account already at its limit, observed, never forced. Its self-test's one model
    turn is the provider's to refuse, so either outcome is expected: the launch fails its self-test
    (None; the failure must be the self-test's, and no sandbox may be left), or it starts. `Live.launch`
    isn't used, because its model check needs a turn the provider answered."""
    live.keys.append(spec.session_key)
    try:
        return live.runtime.launch(spec)
    except LaunchFailed as exc:
        said = str(exc)
        if not said.startswith(SELFTEST_FAILED):
            pytest.fail(f"the limited launch failed before its self-test: {launch_shown(said)}")
        assert sandbox_name(spec.session_key, spec.generation) not in live.backend.names(), "the sandbox was left"
        return None


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_a_named_account_launches_on_its_own_login(live: Live, cli: str) -> None:
    """(a), AU-6: the launch binds the named login and passes the full self-test, Other accounts
    included. The host login file is the same file afterwards."""
    need(cli, "login_binding", second=True)
    before = identity(live, cli, OTHER)
    spec = on(live, cli, f"btq-live-a{cli[0]}", OTHER)
    with running(live, spec) as started:
        rec = live.record(spec.session_key)
        status = ["codex", "login", "status"] if cli == "codex" else ["claude", "auth", "status", "--json"]
        try:
            ran = live.backend.exec(rec.sandbox, Path(rec.worktree), status, timeout=EXEC_SECONDS)
        except (BackendError, BackendUnavailable) as exc:      # its timeout among them: never a pass
            pytest.fail(f"{cli}'s login status could not be run: {type(exc).__name__}")
        # Only 0 is a login: 127 (no such command) and every other status fail, by the status alone.
        assert ran.returncode == 0, f"{cli} reports no login inside the sandbox (status {ran.returncode})"
        assert identity(live, cli, OTHER) == before
        assert started.native_id is not None
    print(f"EVIDENCE accounts (a) {cli} {spec.session_key} account={OTHER} ok")


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_in_session_usage_arrives_as_an_attributed_signal(live: Live, cli: str) -> None:
    """(c), AU-7: the session's own report reaches wsd's queue, attributed to its launch."""
    need(cli, "in_session_usage")
    spec = on(live, cli, f"btq-live-c{cli[0]}", "default")
    with running(live, spec):
        say(live, spec.session_key, "Reply with only the word ok.")
        found = signals_for(live, spec.session_key, UsageSeen, PROMPT_SECONDS)
    assert found, f"no usage signal from {cli} in {PROMPT_SECONDS}s"
    for s in found:
        assert isinstance(s, UsageSeen) and s.generation == spec.generation
        assert all(0 <= o.used_percent <= 100 for o in s.observations)
    windows = sorted({(o.window_id, o.used_percent) for s in found if isinstance(s, UsageSeen)
                      for o in s.observations})
    print(f"EVIDENCE accounts (c) {cli} {spec.session_key} windows={windows}")


def test_the_trusted_read_sees_each_codex_account_and_leaves_no_scratch(
        live: Live, caplog: pytest.LogCaptureFixture) -> None:
    """(d), AU-7: the host read, under the D8 lock, on each Codex login. Its scratch home is gone after
    each read. A read that gives nothing fails with hostread's own fixed diagnostic, never its output."""
    need("codex", "trusted_read")
    caplog.set_level(logging.INFO, logger=hostread.__name__)
    settings = live.runtime.c.settings
    binary = settings.binaries.get("codex")
    assert binary, "no codex binary configured"
    path = os.environ.get("PATH", os.defpath)
    scratch = live.root / "hostread"
    reader = HostUsageReader(accounts_of(live), binary, path, live.runtime.c.credlocks, scratch,
                             settings.lock_wait_seconds,
                             env={"PATH": path, "HOME": str(Path.home()), "LANG": "C.UTF-8"})
    names = ["default", *([OTHER] if os.environ.get(SECOND["codex"]) else [])]
    for account in names:
        key = accounts_of(live).current_key("codex", account)
        caplog.clear()
        read = reader.read(PROFILES["codex"], account, key)
        why = [r.getMessage() for r in caplog.records if r.name == hostread.__name__]
        assert read is not None, f"no trusted read on codex account {account}: {why}"
        assert read.observations and all(o.source == HOST_SOURCE for o in read.observations)
        assert all(0 <= o.used_percent <= 100 for o in read.observations)
        assert not scratch.exists() or list(scratch.iterdir()) == []
        print(f"EVIDENCE accounts (d) codex account={account} limited={read.limited} "
              f"windows={sorted((o.window_id, o.used_percent) for o in read.observations)}")


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_a_session_resumes_on_the_other_account(live: Live, cli: str) -> None:
    """(b), AU-8: generation 2 on the second login resumes generation 1's native session, and the
    conversation carries over: the model recalls a word it was given under the first login."""
    need(cli, "cross_account_resume", second=True)
    spec = on(live, cli, f"btq-live-b{cli[0]}", "default")
    word = f"hz{uuid.uuid4().hex[:6]}"
    with running(live, spec) as first:
        assert first.native_id is not None
        assert answered(live, spec.session_key, f"Remember the word {word}. Reply with only the word ok."), \
            f"{cli}'s first turn did not end in {PROMPT_SECONDS}s"
    moved = replace(spec, generation=2, resume=True, native_id=first.native_id, account=OTHER,
                    credential_key=accounts_of(live).current_key(ADAPTER[cli], OTHER))
    with running(live, moved) as second:                # `Live.launch` checks the pinned model too
        assert second.native_id == first.native_id
        assert answered(live, spec.session_key,
                        "Reply with only the word you were asked to remember, in upper case."), \
            f"{cli}'s second turn did not end in {PROMPT_SECONDS}s"
        # Upper case: the pane holds the word in lower case already, in the resumed history.
        pane = live.tmux.capture(live.record(spec.session_key).tmux_session, 50)
        recalled = word.upper() in pane
    assert recalled, f"{cli} did not recall the word after the move to account {OTHER}"
    print(f"EVIDENCE accounts (b) {cli} {spec.session_key} g1=default g2={OTHER} recalled")


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_a_reached_limit_arrives_as_a_limit_signal(live: Live, cli: str) -> None:
    """(e), AU-7, opportunistic: only on an account the operator says is already at its limit
    (HZ_LIVE_ACCOUNTS_LIMITED=default or b). Never spend quota to get there."""
    need(cli, "limit_signal")
    account = os.environ.get("HZ_LIVE_ACCOUNTS_LIMITED")
    if account not in ("default", OTHER) or (account == OTHER and not os.environ.get(SECOND[cli])):
        pytest.skip("HZ_LIVE_ACCOUNTS_LIMITED names no account at its limit")
    spec = on(live, cli, f"btq-live-e{cli[0]}", account)
    assert spec.model == MODELS[cli]                            # the testing pin, low effort for Codex
    try:
        started = launch_limited(live, spec)
        if started is not None:
            say(live, spec.session_key, "Reply with only the word ok.")
        elif cli == "codex":            # Codex's signal comes from polling a running session (E6)
            pytest.skip("the limited account's codex self-test was refused: no running session to poll")
        found = signals_for(live, spec.session_key, LimitSeen, PROMPT_SECONDS)
        # The provider refused the turns, so the log may record no model; any it does record is the pin.
        native = started.native_id if started is not None and started.native_id else "*"
        used = models_used(live.runtime.layout(spec.session_key).home, cli, native)
    finally:
        uncertain = sandbox_teardown(live.runtime, [spec.session_key], live.backend.names)
        if uncertain:
            pytest.fail(f"{spec.session_key}'s teardown is uncertain: " + "; ".join(uncertain))
    assert found, f"no limit signal from {cli} in {PROMPT_SECONDS}s"
    assert all(isinstance(s, LimitSeen) and s.generation == spec.generation for s in found)
    assert used <= {MODELS[cli]}, f"{cli} ran on {sorted(used)}, not the pinned {MODELS[cli]}"
    hints = [s.reset_hint for s in found if isinstance(s, LimitSeen)]
    outcome = "started" if started is not None else "self-test refused"
    print(f"EVIDENCE accounts (e) {cli} {spec.session_key} launch={outcome} reset-hints={hints} "
          f"models={sorted(used)} pin={MODELS[cli]}")
```

- [ ] **Step 2: Check that it is skipped offline and type-checks**

Run: `timeout 300 uv run pytest tests/live/test_live_accounts.py -q`
Expected: every test is skipped (no `HZ_LIVE`), with none collected as an error.

Then: `timeout 600 uv run pyright tests/live/test_live_accounts.py` and `uv run ruff check tests/live`.
Expected: 0 errors.

- [ ] **Step 3: Run it live (operator present, after Task 11's accepted commits)**

```bash
HZ_LIVE=1 HZ_LIVE_SANDBOX=1 HZ_LIVE_ACCOUNTS=1 HZ_LIVE_ACCOUNTS_CODEX='~/.codex-b' \
  timeout 3600 uv run pytest tests/live/test_live_accounts.py -v -s
```

Expected: every test whose capability is on passes. The rest are skipped, each naming the capability or the variable it lacks. Check the `EVIDENCE` lines for paths, replies and tokens before you paste them into `docs/wsd.md`.

- [ ] **Step 4: Document and commit**

In `docs/wsd.md` §5, after the paragraph that begins "**The live gate (plan 4 Task 14).**", add a paragraph naming `HZ_LIVE_ACCOUNTS`, `HZ_LIVE_ACCOUNTS_CODEX`, `HZ_LIVE_ACCOUNTS_CLAUDE` and `HZ_LIVE_ACCOUNTS_LIMITED`. It should say that each test runs only for an enabled capability and that the (e) test never spends quota. Then paste the run's `EVIDENCE` lines with the CLI versions.

```bash
git add tests/live/test_live_accounts.py docs/wsd.md
git commit -m "plan4b T12: live accounts checks on the shipped capability table"
```

## Execution handoff

This plan is complete once it has passed its cross-model design review and the operator has approved it. Nothing is executed before then. After approval, there are two ways to run it:

1. **Subagent-driven (recommended).** Use superpowers:subagent-driven-development. Give each task a fresh worker session on its own `p4b-t<N>` branch, in the waves of the Parallelism section, and claim each worker's bead with that worker's own session. Each task ends with the cross-model review in Global Constraints before its PR merges.
2. **Inline.** Use superpowers:executing-plans. Run the tasks in wave order in one session, with a checkpoint and the same review after each task.

Either way, Task 11 Part B and Task 12 start only after the G2 bead records the operator's accepted findings, and after plan 4c's launcher task has merged. They run on the CLI versions those findings name.
