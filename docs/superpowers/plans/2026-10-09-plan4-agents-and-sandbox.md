# heterodyne-metaharness Plan 4: wsd core B, agents and sandbox — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the real `AgentRuntime` that plan 3's `NoRuntime` stands in for. Each agent session runs in its own OpenShell sandbox, with its CLI (Claude Code or Codex) in a pane of wsd's own tmux server, behind the mandatory launch self-test (§7). The plan delivers:

- the `claude-code` and `codex` adapters in their managed launch shapes (§4.2, §7; S5 item 1), with the session ID rules of §4.1 (Claude's fixed ID, Codex's server-assigned thread ID);
- the platform-neutral sandbox spec, its OpenShell compilation and the OpenShell backend (§7 Runtime);
- the synthetic home (§7 home isolation), with Codex hook trust seeded per launch (S5 ADR impact 5, S8 open item 2);
- the per-session socket (§7) and the Python `ws-hook` and `ws-request` shims (§3.1, §10);
- the launch self-test on both paths, ported from spike S5: the exec path and the agent's own tool path, with the host-verified result channel (S5 ADR impact 11);
- the maximum-lifetime stop at a turn boundary or by a hard interrupt, followed by the immediate relaunch through the launch guard (§7 freshness gate; S5 ADR impact 8);
- the wiring that replaces `NoRuntime` in `wsd run`.

**Architecture:** `SandboxRuntime` (`src/heterodyne/sandbox/runtime.py`) implements plan 3's `AgentRuntime` protocol, and adds one method, `expire`. Each session has a directory `<wsd state>/sessions/<short id>/` holding:

- its record `session.json`, written atomically before every step it describes;
- the synthetic home, which persists across generations;
- one run directory per generation (`r<n>/`), bound read-only at `/run/hz`.

A launch goes through these steps:

1. validate the spec and clean up any leftovers;
2. decide between resume and create (the resume contract);
3. check the CLI version pin and the login's freshness;
4. prepare the home and the run directory, and start the session socket;
5. compile the spec, check its credentials, and create the sandbox;
6. seed Codex's hook trust;
7. run the exec-path self-test;
8. start the CLI in tmux;
9. run the agent-path self-test;
10. record `running`.

Any failure deletes the sandbox and confirms that it is gone, then raises `LaunchFailed`. If the deletion can't be confirmed, it raises `LaunchUncertain` instead.

`sessions` lists a key until the runtime has confirmed it ended: the pane is gone and the backend no longer lists the sandbox. It cleans up after a dead pane or a launch a dead wsd left behind, and an unattributable sandbox holds every workstream.

Everything a session sends is untrusted. Only three things are read from it:

- the turn state, for the stop at a turn boundary;
- the Codex thread ID, used for that session's own resume;
- the probe results, from a peer the host verifies itself.

Backends hide behind a `Backend` protocol. The tests' `FakeBackend` runs commands on the host, so the runtime, the adapters, tmux and the session socket are tested offline end to end. OpenShell is tested against a recording runner, and for real only in `tests/live`.

**Tech Stack:** Python 3.12+, stdlib (`socket`, `threading`, `subprocess`, `json`, `hashlib`), msgspec stays the only runtime dependency. tmux, git, NVIDIA OpenShell 0.1.2 with the podman compute driver, and podman 5. Tests use pytest, hypothesis, ruff and pyright (strict on `src/`).

**Spec:** ADR 0001 revision 14 is design-repo commit `82b2e4b` in `$DESIGN_REPO`, approved in bead `btq-k942c`. Section numbers (§) refer to it. The parts this plan implements are §4.2, §4.4 D2 (the receipts this runtime returns), §7, §10 (`wsd` down) and §11 (sandbox compilation for both backends, integration fakes).

The plan also draws on these sources:

- Roadmap row 4 (`docs/superpowers/plans/2026-09-29-heterodyne-v1-roadmap.md`) scopes this plan.
- Spike S5 (`docs/spikes/s5-openshell.md`, code `spikes/s5/`) supplies the launch shape, the probes and the ADR impact items 1–11.
- Spike S8's open items (`docs/spikes/s8-marmot-only.md`) cover the Codex launch shape and hook trust.
- ADR revision 15 (being drafted by `s5-spike` when this plan was written) is expected to accept OpenShell's broker answers as §7's direct-network proof. Every task that depends on it is marked **[r15]** (see "ADR revision 15 dependency" below).

**Status:** revision 1, written for the cross-model review. **Design approval is not set.** The §17 decisions it depends on are listed below, with the parts that wait for them. Gap 13 (git in a sandboxed linked worktree) needs an operator decision before the OpenShell backend is enabled on a real host.

## Global Constraints

- **Variables:** `$HZ` is a fresh clone of `Epiphytic/heterodyne-metaharness` (never the live harness or a symlink to it), `$BTQ_REPO` the beads-task-queue checkout, `$DESIGN_REPO` the design repo. "Install-agnostic: no install paths, npubs, tokens or relay URLs." `scripts/check_install_agnostic.py` stays clean.
- "Python 3.12+, matching the repo's tooling (pytest, hypothesis, ruff, pyright)." Managed with `uv`. Runtime dependencies stay exactly `msgspec`.
- **Tests stay offline, except `tests/live`.** "Tests never touch the network, a real wn-agent, the real systemctl, the real claude or codex binaries, ~/.claude, or the real beads database. Use fake executables and temp dirs." Offline tests never run `openshell` or `podman`. They never write to the real home: the real-home canary path is injected. `tests/live/test_live_sandbox.py` is the only test that runs OpenShell or a real CLI. It is skipped unless both `HZ_LIVE=1` and `HZ_LIVE_SANDBOX=1` are set, and it uses a temporary wsd state directory, never the live wsd journal.
- **Never print or commit tokens, npubs or 64-hex values.** Test logins are obvious fakes (`"fake-not-a-token"`, a JWT whose payload holds only `exp`). The fake Codex's hook hashes are 16 hex characters. A login file's sha256 is computed at run time in tests, never written as a literal.
- **No sleeps in tests.** Production code that polls takes an injected `sleep` and `clock`. Tests either inject no-op versions, or wait on a condition with `tests/sandbox_env.py:wait_for`, which polls with a bounded deadline; they never use a fixed delay.
- "Nothing may be specific to Claude or Codex" outside `src/heterodyne/agents/`. `src/heterodyne/wsd/` still names no adapter, model or CLI. `src/heterodyne/sandbox/` names the adapters only through `heterodyne.agents.registry.ADAPTERS`. The one exception is `spec.DENIED_HOSTS`, the §7 deny list.
- "Every recovery path fails closed. Never infer absent, complete or safe from missing or unreadable evidence." In this plan that means:
  - an unreadable session record, an unlistable backend or a tmux error is `RuntimeUnavailable`;
  - a sandbox the backend lists without a record holds every workstream;
  - a launch whose cleanup can't be confirmed is uncertain;
  - a self-test that raises, times out or misses a check fails.
- **Never patch out the self-test, the credential check, the freshness gate or the CLI pin.** No setting disables them, and tests prove each one refuses a launch.
- **`src/` style:** ruff line length 110; pyright strict; `sys.platform` only in `src/heterodyne/platform.py`. Error messages name keys and checks, never paths or secret values. The in-sandbox scripts (`src/heterodyne/sandbox/resources/*.py`, `src/heterodyne/session/shim.py`) use the standard library only.
- **The pre-commit install-agnostic hook rejects absolute `/home` paths** in committed files. Tests build paths from `tmp_path`.
- **Long commands** use `timeout`. The full suite runs in the background with `timeout 3600` (about 27 minutes). Each task's own tests run in the foreground.
- **Review rule:** every task ends with a review by a **different LLM than the implementer** (cross-model), or by a fresh-context adversarial agent when only one LLM is available. The brief names the diff range and the ADR sections, and asks for `[BLOCKING]`/`[NON-BLOCKING]` findings. Every blocking finding is fixed or rebutted. Review runs at most three cycles; the third asks whether the result is good enough for this stage. Close evidence includes `Code-Review: reviewer=<model> author=<model> mode=<cross-model|adversarial> range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`): Tasks 1–14 are `kind:task`. Each carries `metadata.design_approval=<this plan's approval bead>`, `metadata.adr_revision=82b2e4b` (or the r15 commit for an **[r15]** task), and a blocking dependency on the approval bead. **[r15]** tasks are also blocked by the bead that approves ADR revision 15. The dependency order is in "Task order" below.
- **Branches:** integration branch `plan-4-agents` in `$HZ`, from `main`, at or after the merge of AU-4/AU-11 (PR #35). Make one commit or more per task. Merge `origin/main`, never rebase, and never amend a pushed commit.

---

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Where the ADR is silent or loose | Choice | Why |
|---|---|---|---|
| D1 | The sandbox name (S5 ADR impact 7: at most 19 characters). | `hz` + the first 12 hex digits of sha256(session key) + `g<generation>`. Generations above 9999 are refused (`SpecRefused`, so `LaunchFailed`). Session directories and tmux sessions use the same short ID: `sessions/hz…/` and `wsd-hz…`. | It is deterministic and recomputable, and it can't collide within a key. A new generation gets a new sandbox name, so it never meets an old sandbox that is still being deleted. |
| D2 | Where a session's state lives. | `<wsd state>/sessions/<short id>/` holds `session.json`, the persistent `home/`, `bridge/` (Codex) and `r<gen>/` (bound read-only at `/run/hz`). The socket is `r<gen>/s.sock` and the probe channel is `r<gen>/p.sock`. The event spool `r<gen>-events.jsonl`, the policy scratch and the Other accounts canary sit beside these, outside every bind. | The paths stay short enough for `sun_path`; longer than 100 bytes is refused. The home persists because both CLIs keep resumable state there (§7). The spool and the canary must not be reachable from inside. |
| D3 | Which CLI install is bound. | Only the session's own adapter's install root, read-only at its host path (S5 bound both). | It is the least exposure, and only that CLI runs in the session. |
| D4 | The env allowlist (§7 Environment; S5 ADR impact 2). | The launcher sets `HOME`, `PATH`, `LANG`, `TERM`, `USER`, `HZ_SESSION_SOCKET`, the adapter's config variable (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`) and the adapter's fixed extras (Claude: `ENABLE_CLAUDEAI_MCP_SERVERS=false`). The probes also allow OpenShell's fixed injected set, the shell's own (`PWD`, `SHLVL`, `_`, `OLDPWD`), and, on the agent path, the pinned CLI's tool variables. Each adapter carries its pin (Claude Code 2.1.286, codex-cli 0.160.0), and a different host version refuses the launch. | This is S5's allowlist. The tool variables are pinned per CLI version, as S5 ADR impact 11 requires. |
| D5 | The hook command (S5 ADR impact 5; S8 open item 2). | The command is `python3 -I <run>/shim.py hook` for every launch. The token is in `<run>/token` beside the socket. | The command never changes, so Codex's trusted hash stays valid, and the token never appears in a command line. |
| D6 | Codex hook trust. | Before each Codex launch, `codex_trust.py` runs inside the sandbox. It asks `codex app-server` over stdio (`hooks/list`) which hooks are untrusted or modified. The host validates each key and hash, appends `[hooks.state."<key>"] trusted_hash = "<hash>"` to the freshly rewritten `config.toml`, then runs the script again and requires an empty list. | This is S8's demonstrated setup step, run where the CLI and its `CODEX_HOME` are. The output comes from inside, so it is validated before it is written as TOML, and it can only affect the session's own config. |
| D7 | Codex's daemon (S8: it survives `tmux kill-session`). | Codex uses the managed `--remote` shape only: a per-session `codex app-server --listen unix:///run/hz-bridge/app.sock` inside the sandbox, and the TUI attached with `--remote`. Ending a session always deletes its sandbox, which ends every process in it, the app-server included. | Deleting the sandbox is a stronger end than `--no-daemon`, and it is confirmed by the backend's listing. |
| D8 | The Codex thread ID (§4.1, §4.2). | It is taken from the first `SessionStart` hook event of the generation whose `session_id` is a canonical UUID. The agent-path self-test submits the first prompt, which makes Codex fire `SessionStart` (S8 capability 3). It is untrusted, and it is used only as this session's own resume ID (`Started.native_id`). A resume that reports a different thread fails the launch. | Codex assigns the ID itself, and hooks are the only channel that reports it. A forged ID can only point this session's own resume at a thread its own home holds. |
| D9 | The resume contract (plan 3: "resume if it holds state, create if it can establish none ever existed, else RuntimeUnavailable"). | **State held:** the adapter finds the native ID's state in the synthetic home: Claude's `projects/*/<id>.jsonl`, or Codex's `sessions/**/rollout-*<id>.jsonl`. The ID wanted is the spec's `native_id`, else the record's when a generation ran. **None ever existed:** there is no session record, or the record's `ran` is false (`ran` is set when a generation reaches `running`, after the self-test, and is never cleared), or the synthetic home holds no session state of the adapter at all. A fresh start then uses the spec's `native_id` for Claude and none for Codex (`assigns_id`: Codex reports its own thread, D8). **Otherwise** (the session ran and its home holds other session state, but not the wanted ID's) it is `RuntimeUnavailable`. `spec.resume` only matters when there is no state, and a held state is always resumed (so a fresh `--session-id` never collides). | The record is written before anything can start, so its absence is evidence. `ran` is set only after the self-test, so a launch that failed earlier never makes a later one wait for state that was never kept. Plan 8's cleanup must keep records (flagged below). |
| D10 | The credential check (§7 home isolation). | The login binds must be exactly the chosen account's login files, read-only. No other bind may equal or contain a login file of any other account of the adapter (the default login included), or equal or contain such a login directory, or the chosen account's login directory. No bind may contain the real home. | §7's rule, with one refinement flagged below (conflict 3): a source *inside* a login directory that contains no login file is allowed. This is needed because codex-cli installs itself under `~/.codex/packages/`. |
| D11 | Accounts in plan 4 (§4.4 D7; AU-6 binds named accounts). | Plan 4 launches only the `default` account. A launch entry pinning a named account fails with "named accounts are bound by AU-6", which spends the launch budget. The Other accounts probe already covers every configured named account of the adapter, plus the canary. | AU-6 adds named-account binding on top of this launcher. A host with named accounts must not run one silently on the default login. |
| D12 | The freshness gate (§7, §4.4 D8). | The gate reads the chosen login's access-token expiry: Claude's `claudeAiOauth.expiresAt`, or the `exp` of Codex's `tokens.access_token`. It refuses unless more than `max_lifetime + stop_margin` remains, with defaults of 120 + 15 minutes. **Plan 4 never refreshes**, so the launch is refused. AU-6 adds the host refresh under the D8 lock. | §7 allows "refreshes on the host first, or refuses". Refusing is the half that needs no shared-token rules. |
| D13 | The lifetime stop (§7). | `AgentRuntime.expire(ws, now)` is new. It runs in every pickup, just before the sweep. Once the stop window opens (`deadline − stop_margin`), a session at a turn boundary is stopped: its last turn event was a `Stop`. At the deadline, any session is stopped (Escape, then the sandbox deleted). Then the WIP is committed with the mark `lifetime:<key>:<gen>`. The sweep in the same pickup gives the running bead a resume operation, and that relaunches it at once through the launch guard. A relaunch the gate refuses is a `LaunchFailed`, so the existing budget makes the bead `needs-human`. | This is §7's sequence, with no new journal operation: the sweep, the guard and the launch budget already exist. Backstop pickups (every 60 s) bound the delay. A missed `Stop` only makes the stop hard. |
| D14 | The hook shim when wsd is down (§10). | The shim waits up to `[timeouts] hook_wait_seconds`. With no answer, a PreToolUse is allowed only for the local class `worktree_edit`, and only if that class is in the auto-approve tier: an `Edit`, `Write`, `MultiEdit` or `NotebookEdit` whose real path is inside the worktree. Everything else is denied with "control plane unavailable; retry shortly", as the JSON PreToolUse decision (S8 capability 1). Other events are never blocked; with no answer they are spooled to `$HOME/.hz/spool.jsonl`. | This is narrower than §10 allows (running tests and local git are not classified locally), so it fails closed more often, never less. |
| D15 | Tool policy before plan 5. | While wsd answers, every `hook_event` gets `{"ok": true}`, so no tool call is denied by wsd. `ws_request` gets `{"ok": false, "error": "unsupported"}`. | Plan 5 owns the policy engine and `ws-request` processing. The sandbox is the security boundary (§5.3), and the hook is a UX layer. |
| D16 | The self-test's allowlisted control (§7 Egress). | The probe control host (`api.openai.com`, configurable) stays allowed to `/usr/bin/curl` for the session's life. | OpenShell's policy is fixed at create time in 0.1.2. The residual is flagged below (gap 4). |
| D17 | Bubblewrap (roadmap row 4: "bubblewrap from the S3 prototype only if S5 fails"). | **Not in this plan.** If ADR revision 15 is rejected, or S5 is closed as failed, a contingent plan 4b replaces the **[r15]** tasks with a bubblewrap backend behind the same `Backend` and `SelfTest` protocols. Every other task stands unchanged. | S5 passed apart from the direct-network deviation that r15 addresses. Writing both backends now would double the review for a path that should not be needed. Flagged as decision 1 below. |
| D18 | The wsd tmux server. | Socket name `heterodyne-wsd`. On systemd, each start runs in a transient scope with a unique name (`heterodyne-wsd-tmux-<12 hex>`), as admind's does. Panes run `env -i <tool env> openshell sandbox exec … --tty`, so the pane gets no host environment. | It survives a wsd restart (`KillMode=control-group`), so sessions outlive wsd as §10 requires, and break-glass `tmux attach` still works (§4.2). |
| D19 | Turn boundaries after a wsd restart. | A restarted wsd re-binds each live session's socket at the next `sessions` call, with an empty turn state. Until a new `Stop` arrives, the lifetime stop for that session is hard. | Turn state is in-memory and untrusted. Restoring it from the spool would trust a file the agent's events wrote. |
| D20 | Where launch-time waits run. | A launch blocks the workstream's operation lock for up to about 6 minutes in the worst case: sandbox create (300 s at most), the exec probes (180 s), the agent prompt (90 s) and the agent probes (`agent_probe_seconds`, 240 s). Other workstreams are unaffected. | §5.2 serialises launches per workstream anyway. Flagged for plan 8's health checks (gap 6). |
| D21 | Configured backends this plan does not build. | `[platform] sandbox = "bubblewrap"` or `"seatbelt"` makes `wsd run` print a notice ("… is not built yet; no agents will run") and use `NoRuntime`, as `"none"` does. Any other unknown name is a `ConfigError`. The Linux platform default becomes `"openshell"`, so `heterodyne setup` records it on new installs. | Existing configs (the live harness and admind tests carry `bubblewrap`) keep working with no agents, exactly as before plan 4. Failing them would break hosts that never asked for plan 4. |
| D22 | The Other accounts probe with no named accounts. | It runs with an empty list of other accounts. The canary (written outside every bind just before, then read back outside) is always checked inside, so the probe never passes vacuously. | A host with only default logins is the common case, and plan 4 launches only the default. The canary is the positive control that S5's probe lacked. |

## ADR conflicts and gaps, flagged for the operator (not silently resolved)

1. **Bubblewrap fallback.** D17 leaves it to a contingent plan 4b. The alternative is to build it inside plan 4 now, at about 3 extra tasks and a second live gate. *Proposed:* plan 4b only if r15 is rejected or §17 #2 closes S5 as failed.
2. **§7's direct-network wording.** Under OpenShell a literal-address connect gets EACCES from the seccomp broker, never "unreachable" (S5 item 2). Task 8's probe passes only with the full set of broker answers, plus `Seccomp: 2`/`NoNewPrivs: 1` inside and `NetworkMode=none` checked from outside. **That is what r15 must accept**, and it is why Tasks 3, 4, 8, 9, 12 and 14 are **[r15]**.
3. **§7's credential-source rule vs the Codex install path.** §7 says "a source in any other account's login directory (the adapter's default login included) refuses the launch". codex-cli 0.160.0 installs under `~/.codex/packages/standalone/releases/…`, inside the default login directory. Read literally, every Codex launch with a named account chosen (AU-6) would be refused, and with the default chosen the CLI root would sit inside the chosen account's own login directory. *Proposed (D10):* refuse a source that equals or contains a login file or a login directory, and allow a source strictly inside a login directory only if it contains no login file. The Other accounts probe still proves that each login file is unreachable inside. This needs an ADR sentence, and could go into r15.
4. **The probe control stays reachable** (D16). `/usr/bin/curl` inside every session can reach `api.openai.com` for the session's life, an exfiltration path §7's residual-risk list doesn't name. *Proposed:* record it as residual risk in r15. Alternatively, point `probe_allowed_host` at a host the operator controls. Removing the rule after the self-test needs OpenShell policy updates on a live sandbox, which S5 did not test.
5. **`RuntimeUnavailable` when state is missing** (D9) holds the whole workstream. That happens when a record says a generation ran and the home holds session state, but not the wanted ID's (a transcript or rollout deleted by hand, or a home swapped under the record). Plan 3's contract demands this outcome. *Alternative:* `LaunchFailed`, which spends the bead's launch budget and makes it `needs-human` without holding the other beads. This plan keeps the contract. The operator may prefer the alternative.
6. **Long launches hold the workstream lock** (D20). That is acceptable for v1. Plan 8's health view should show "launching" with its elapsed time.
7. **Git push.** §7 says "there is no git push". Plan 4 relies on there being no credential inside, and egress to a forge host is read-only only by that absence. No L7 rule blocks `git push` itself, because the proxy can't see paths (§7 residual risks). The live test checks that a push fails.
8. **The lifetime WIP commit is not a journaled step** (D13). It is idempotent by its mark, made after the sandbox is confirmed gone, and its failure is recorded in the session record. A failed or lost commit leaves the worktree as it was, and the relaunch resumes in it. §7 says "interrupt, then commit the WIP" but does not make it a journal transition. *Proposed:* accept.
9. **Session records and plan 8's cleanup** (D9). The resume contract treats a missing record as "never existed". Plan 8's garbage collection must keep a tombstone (the record with `phase = ended`) for every key whose bead is not closed. That becomes a requirement on plan 8's row.
10. **OpenShell's supervisor is in the TCB** (S5 ADR impact 3). It terminates TLS and sees bearer tokens in plaintext. That is acceptable while login files are the credential (§17 #5). It is recorded here because a later move to injection makes it central.
11. **The agent-path self-test makes one model call per launch.** Its prompt and output also enter the session's transcript, so a resumed session sees them. S5 accepted this, and §7 requires the self-test before every launch.
12. **S8 open item 1** (Codex `SessionStart` fires only on the first prompt). The agent-path probe prompt *is* that first prompt, so plan 4 gets the thread ID without a separate preflight. Whether admind adopts the same preflight is admind's decision (§8.1), not this plan's.
13. **Git in a sandboxed linked worktree. Blocking: it needs an operator decision before the backend is enabled.**
    - **What's wrong.** §7 says agents commit to their local `btq/<id>` branch. btq worktrees are linked worktrees, though: their `.git` is a file that points at `<repo>/.git/worktrees/<n>`, and from there at the common directory `<repo>/.git`. Plan 4 binds only the worktree, so a commit inside fails. Task 14 records this as a strict `xfail`.
    - **Why the obvious fix is unsafe.** Binding `<repo>/.git` read-write would let the agent write `.git/config` or `worktrees/<n>/{commondir,gitdir,config.worktree}`. Host-side git (plan 3's `gitwip.wip_commit` for park, defer and now the lifetime stop, and btq itself) would then run agent-chosen configuration, for example `core.fsmonitor`, a filter driver or a redirected `commondir`. That is a sandbox escape. `core.hooksPath=/dev/null` doesn't cover it.
    - **It is partly present already.** Host git already trusts one agent-writable file today: the worktree's `.git` file, which says where the git directory is. An agent can point it elsewhere. Plan 3's gitwip needs the same hardening even without a common-directory bind.
    - *Proposed:*
      - Bind the common directory read-only, with read-write binds over `objects/`, `refs/heads/btq/`, `logs/refs/heads/btq/` and `worktrees/<n>/`. Git renames `HEAD.lock` and index locks in place, so those directories must be writable, not just the files.
      - Harden every host-side git call in a worktree:
        - pass `GIT_DIR=<common>/worktrees/<n>`, `GIT_COMMON_DIR=<common>` and `GIT_WORK_TREE`, computed from the trusted repo path in config, never from the worktree's `.git` file or `commondir`;
        - add `-c core.fsmonitor=false` beside `core.hooksPath=/dev/null`;
        - refuse when `extensions.worktreeConfig` is set.
      - The residual risk: an agent can move other beads' `btq/` branches. ADR text is needed for that.
    - This changes plan 3's gitwip contract, so it is not decided silently here. It blocks enabling `[platform] sandbox = "openshell"` on a real host. It doesn't block Tasks 1–13. The resolution is either a plan 4 amendment (one task, before Task 12's backend is enabled) or a plan 3 follow-up.
14. **The live test and a running wsd.** A wsd on the OpenShell runtime finds Task 14's sandboxes in the backend's listing with no record of its own, and holds its workstreams until they are gone (Task 10's fail-closed rule). The live test therefore runs only while no wsd on the host uses OpenShell, and it deletes only the sandboxes it launched, never by name prefix.

## §17 operator decisions this plan depends on

| §17 | Decision | What depends on it | Until it is decided |
|---|---|---|---|
| #2 | S5 fallback and timebox | Every **[r15]** task (3, 4, 8, 9, 12, 14) assumes OpenShell. If S5 is closed as failed, plan 4b replaces them (D17). | The **[r15]** tasks don't start. Tasks 1, 2, 5, 6, 7, 10, 11 and 13 are backend-neutral and can proceed. |
| #3 | Host changes for OpenShell (podman 5) | Task 12's `[sandbox] tool_env` (the side-by-side podman's `PATH` and `CONTAINERS_CONF`), the gateway configuration (`packaging/sandbox/gateway.toml`) and Task 14's live run all assume the S5 host changes stay in place. | Task 12 is code only and doesn't need the decision. Enabling `[platform] sandbox = "openshell"` on the reference host does, and so does running Task 14 there. |
| #5 | Credential injection for model auth | The whole plan assumes v1 keeps read-only login files under the freshness gate (D12). | Nothing waits. If the operator later chooses injection, that is a new ADR revision, and the spec, the gate and the probes change with it. |

These are **not** dependencies:

- #4 (OpenShell on macOS): plan 4 is Linux only, and macOS/Seatbelt stays in phase 2.
- #7 (host exceptions): plan 4 keeps "startup break-glass" possible, because the wsd tmux server can be attached, but it needs no answer.
- #1, #6, #8, #9 and #10.

## ADR revision 15 dependency

r15, being drafted, is expected to accept S5's EACCES-broker evidence as §7's direct-network proof (gap 2), together with S5's ADR impact items. The **[r15]** tasks are:

- **Task 3** (OpenShell policy compilation);
- **Task 4** (OpenShell backend);
- **Task 8** (the probes and the exec-path self-test, including the direct-network criterion);
- **Task 9** (the agent-path self-test, whose peer check uses the workload container's netns);
- **Task 12** (`openshell` becomes the Linux default, and `wsd run` uses the sandbox runtime);
- **Task 14** (the live test).

The backend-neutral tasks (1, 2, 5, 6, 7, 10, 11, 13) do not depend on r15. However, §7 says "Plan 4 (the sandbox runtime) starts only once S5 has either passed or been closed as failed". If the operator reads that as covering the whole plan, nothing starts until r15 is decided. That costs only time: the neutral tasks are needed under bubblewrap too.

## Relation to the accounts plan (AU, `heterodyne-metaharness-v2-accounts` at `66ad195`)

- AU-3/AU-4/AU-5/AU-11 already built the launch entries, dispatch marks, receipts and the headroom gate. Plan 4 does not change them. `SandboxRuntime.launch` returns the `Started` that the guard journals as the generation's receipt.
- **AU-6, AU-7 and AU-8 follow this plan's launcher:**
  - **AU-6** adds named-account binding (replacing D11's refusal), the D8 refresh lock and refresh (extending D12), and the capability entries. Plan 4 already sets `CLAUDE_CONFIG_DIR`/`CODEX_HOME` inside the synthetic home (an item in AU-6's scope that is done here) and runs the Other accounts probe over every configured account.
  - **AU-7** adds the usage producers (in-session reports through the session socket, and trusted host reads).
  - **AU-8** adds failover and the D7 handoff IDs `uuid5(NS, f"{key}:{gen}")`. The plan 4 lifetime relaunch resumes the same native ID and never uses them.
- **AU-14 and AU-15 depend on plan 4.** No AU bead blocks plan 4.

## Task order

1 → 2 → 5 → 6 → 7 → 10 → 11 → 13 are backend-neutral; each is blocked by the one before. 3 → 4 → 8 → 9 are **[r15]**: Task 3 is blocked by Task 2 and the r15 approval, and Tasks 8 and 9 are also blocked by Tasks 7 and 10 (they implement Task 10's `SelfTest` protocol). Task 12 is blocked by 4, 9 and 11. Task 13 is blocked by 11 only (it uses the fake backend). Task 14 is blocked by 12 and 13.

## File map

| Path | Task | Responsibility |
|---|---|---|
| `src/heterodyne/defaults/defaults.toml` | 1 | `[sandbox]` defaults |
| `src/heterodyne/sandbox/__init__.py` | 1 | package docstring |
| `src/heterodyne/sandbox/settings.py` | 1 | `SandboxSettings`, `WsSandbox`, `sandbox_settings` |
| `src/heterodyne/sandbox/spec.py` | 2 | names, `SessionLayout`, `Inside`, `Bind`, `Egress`, `SandboxSpec`, `build_spec`, `check_credentials` |
| `src/heterodyne/wsd/accounts.py` | 2 | `Accounts.login_paths` |
| `src/heterodyne/sandbox/backend.py` | 3 | `Backend` protocol, `BackendUnavailable`, `BackendError` |
| `src/heterodyne/sandbox/openshell.py` | 3, 4 | `policy`, `driver_config`, `create_argv` **[r15]**; `OpenShellBackend` **[r15]** |
| `src/heterodyne/session/__init__.py`, `server.py` | 5 | `SessionServer`, `TurnState` |
| `src/heterodyne/session/shim.py`, `pyproject.toml` | 6 | `ws-hook`, `ws-request` |
| `src/heterodyne/agents/base.py`, `claude_code.py`, `codex.py`, `registry.py` | 7 | `Adapter`, `ClaudeCode`, `Codex`, `ADAPTERS` |
| `src/heterodyne/sandbox/resources/codex_trust.py` | 7 | in-sandbox hook-trust lister |
| `src/heterodyne/sandbox/resources/probes.py` | 8 | in-sandbox probes (port of `spikes/s5/probes.py`) **[r15]** |
| `src/heterodyne/sandbox/openshell_selftest.py` | 8, 9 | `OpenShellSelfTest` **[r15]** |
| `src/heterodyne/sandbox/channel.py`, `src/heterodyne/platform.py` | 9 | `ProbeChannel`, `ProcVerifier`; `peer_pid_checked` **[r15]** |
| `src/heterodyne/tmux.py` | 10 | `pane_info` |
| `src/heterodyne/sandbox/selftest.py` | 10 | `SelfTest`, `ProbeContext`, `SelfTestFailed` |
| `src/heterodyne/sandbox/runtime.py` | 10, 11 | `SandboxRuntime`, `SessionRecord`, `Phase`, `RuntimeConfig` |
| `src/heterodyne/wsd/runtime.py`, `scheduler.py` | 11 | `AgentRuntime.expire`; pickup calls it |
| `src/heterodyne/sandbox/build.py`, `src/heterodyne/wsd/cli.py`, `src/heterodyne/platform.py`, `examples/config.toml`, `packaging/sandbox/*`, `docs/wsd.md`, `docs/configuration.md`, `docs/install.md`, `docs/security-model.md` | 12 | wiring, the Linux default, the image, docs **[r15]** |
| `tests/sandbox_env.py`, `tests/fakes/fake_agent_cli.py`, `tests/fakes/fake_backend.py`, `tests/fakes/scripted_selftest.py`, `tests/fakes/fake_runtime.py` | 1, 7, 10, 11 | test rig and fakes |
| `tests/test_sandbox_*.py` (including `test_sandbox_build.py` and `test_sandbox_scheduler.py`), `tests/test_session_*.py`, `tests/test_agents_*.py`, `tests/test_wsd_lifetime.py`, `tests/test_wsd_accounts.py`, `tests/test_platform.py`, `tests/test_tmux.py` | 1–13 | tests |
| `tests/live/test_live_sandbox.py` | 14 | live OpenShell test **[r15]** |

---

### Task 1: Sandbox settings

**Files:**
- Create: `src/heterodyne/sandbox/__init__.py`, `src/heterodyne/sandbox/settings.py`, `tests/sandbox_env.py`
- Modify: `src/heterodyne/defaults/defaults.toml` (append `[sandbox]`)
- Test: `tests/test_sandbox_settings.py`

**Interfaces:**
- Consumes: `heterodyne.config.load(workstream, env)`, `Config.values`, `Config.policy.tiers` (class → tier), `layers.table_at`, `layers.string_list`, `secret_scan.show`, `paths.config_dir`, `wsd.settings.workstream_names`.
- Produces:
  - `WsSandbox(extra_egress: tuple[str, ...], extra_ro_mounts: tuple[Path, ...], local_classes: frozenset[str])`;
  - `SandboxSettings` (fields below);
  - `sandbox_settings(env) -> SandboxSettings`;
  - `from_config(host: Config, streams: Mapping[str, Config]) -> SandboxSettings`;
  - `HOST`, the host-name regex;
  - `ENV_NAME`, the variable-name regex;
  - in `tests/sandbox_env.py`, `config_env(root, host="", workstreams=None) -> dict[str, str]` and `wait_for(pred, timeout=10.0)`.

The host's `[sandbox]` already carries `egress_approved` and `ro_mounts_approved`, which `layers.check_host` validates. This task adds the runtime keys and rejects any other key. `[platform] sandbox` names the backend. `heterodyne setup` writes it, and a host without it gets `"none"`, so wsd keeps `NoRuntime` until the operator opts in (Task 12). `local_classes` is the shim's locally classifiable set (D14): `worktree_edit`, kept only while the workstream's effective tier for it is `auto_approve`. The tier is computed per workstream, so a workstream's `[restrict]` can remove it.

- [ ] **Step 1: Write the test rig helper**

`tests/sandbox_env.py`:

```python
"""Sandbox test helpers: a scratch config directory, and a bounded wait on a condition."""

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from wsd_env import login_home

BASE_HOST = """
[profiles.p-one]
adapter = "claude-code"

[profiles.p-two]
adapter = "codex"
"""


def config_env(root: Path, host: str = "", workstreams: Mapping[str, str] | None = None) -> dict[str, str]:
    """A config directory at `root/config` holding a minimal host config.toml (two profiles) plus `host`,
    and one `workstreams/<name>.toml` per entry. HOME is `root/home`, with a fake default login per
    adapter. Returns the environment wsd would read it with."""
    config = root / "config"
    (config / "workstreams").mkdir(parents=True, exist_ok=True)
    (config / "config.toml").write_text(BASE_HOST + host)
    for name, text in (workstreams or {}).items():
        (config / "workstreams" / f"{name}.toml").write_text(text)
    home = login_home(root / "home")
    return {"HETERODYNE_CONFIG_DIR": str(config), "HOME": str(home)}


def wait_for(pred: Callable[[], object], timeout: float = 10.0) -> None:
    """Poll `pred` until it is true, failing after `timeout` seconds. A bounded wait on a condition,
    never a fixed delay."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_sandbox_settings.py`:

```python
from pathlib import Path

import pytest
from sandbox_env import config_env

from heterodyne.config import ConfigError
from heterodyne.sandbox.settings import WsSandbox, sandbox_settings


def test_defaults(tmp_path: Path) -> None:
    s = sandbox_settings(config_env(tmp_path))
    assert s.backend == "none"
    assert s.image == "localhost/heterodyne-agent:1"
    assert (s.openshell, s.podman) == ("openshell", "podman")
    assert (s.max_lifetime_seconds, s.stop_margin_seconds) == (7200, 900)
    assert (s.probe_allowed_host, s.probe_denied_host) == ("api.openai.com", "example.org")
    assert s.agent_probe_seconds == 240
    assert s.hook_wait_seconds == 5.0
    assert s.binaries == {"claude-code": "claude", "codex": "codex"}
    assert s.profiles["p-two"]["adapter"] == "codex"
    assert dict(s.tool_env) == {}
    assert s.egress_approved == ()
    assert s.workstreams == {}


def test_backend_and_runtime_keys(tmp_path: Path) -> None:
    env = config_env(tmp_path, """
[platform]
sandbox = "openshell"

[sandbox]
max_lifetime_minutes = 60
stop_margin_minutes = 10
tool_env = { PATH = "/opt/podman5/bin:/usr/bin", CONTAINERS_CONF = "/opt/podman5/containers.conf" }
""")
    s = sandbox_settings(env)
    assert s.backend == "openshell"
    assert (s.max_lifetime_seconds, s.stop_margin_seconds) == (3600, 600)
    assert s.tool_env["PATH"] == "/opt/podman5/bin:/usr/bin"


@pytest.mark.parametrize("table, needle", [
    ("bogus = 1", "unknown keys"),
    ("max_lifetime_minutes = 5", "max_lifetime_minutes"),
    ("max_lifetime_minutes = 30\nstop_margin_minutes = 30", "stop_margin_minutes"),
    ("agent_probe_seconds = 10", "agent_probe_seconds"),
    ('probe_denied_host = "not a host"', "probe_denied_host"),
    ('probe_allowed_host = "mcp-proxy.anthropic.com"', "probe_allowed_host"),
    ('image = ""', "image"),
    ('tool_env = { "lower-case" = "x" }', "tool_env"),
    ('tool_env = { HOME = "/elsewhere" }', "tool_env"),
    ('egress_approved = ["Bad_Host"]', "egress_approved"),
])
def test_bad_values_are_refused_by_key(tmp_path: Path, table: str, needle: str) -> None:
    with pytest.raises(ConfigError, match=needle):
        sandbox_settings(config_env(tmp_path, f"\n[sandbox]\n{table}\n"))


def test_tool_env_values_never_appear_in_errors(tmp_path: Path) -> None:
    env = config_env(tmp_path, '\n[sandbox]\ntool_env = { GOOD = "/opt/fake-value/bin", "bad name" = "x" }\n')
    with pytest.raises(ConfigError) as caught:
        sandbox_settings(env)
    assert "fake-value" not in str(caught.value)


def test_workstream_additions_and_local_classes(tmp_path: Path) -> None:
    mounts = tmp_path / "shared"
    mounts.mkdir()
    env = config_env(tmp_path, f"""
[sandbox]
egress_approved = ["pypi.org", "github.com"]
ro_mounts_approved = ["{mounts}"]
""", {
        "alpha": f'[sandbox]\nextra_egress = ["github.com"]\nextra_ro_mounts = ["{mounts}"]\n',
        "beta": '[restrict]\nescalate = ["worktree_edit"]\n',
    })
    s = sandbox_settings(env)
    assert s.workstreams["alpha"] == WsSandbox(("github.com",), (mounts.resolve(),),
                                               frozenset({"worktree_edit"}))
    assert s.workstreams["beta"] == WsSandbox((), (), frozenset())
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_settings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.sandbox'`.

- [ ] **Step 4: Add the defaults**

Append to `src/heterodyne/defaults/defaults.toml`:

```toml

# The agent sandbox (§7). Host only, apart from each workstream's extra_egress and extra_ro_mounts. The
# backend is `[platform] sandbox` (written by `heterodyne setup`); without it wsd runs no agents.
[sandbox]
egress_approved = []
ro_mounts_approved = []
image = "localhost/heterodyne-agent:1"
openshell = "openshell"
podman = "podman"
max_lifetime_minutes = 120
stop_margin_minutes = 15
probe_allowed_host = "api.openai.com"   # the self-test's allowlisted control (§7 Egress)
probe_denied_host = "example.org"       # the self-test's refused host
agent_probe_seconds = 240

# Variables for the openshell and podman commands only (for example the PATH of a side-by-side podman).
# Never passed into a sandbox.
[sandbox.tool_env]
```

The example's `egress_approved` stays in `examples/config.toml`. The defaults hold empty lists, so a host without them is valid.

- [ ] **Step 5: Write the module**

`src/heterodyne/sandbox/__init__.py`:

```python
"""The agent sandbox (ADR 0001 §7): settings, the platform-neutral spec, backends, the launch self-test
and SandboxRuntime, plan 4's AgentRuntime."""
```

`src/heterodyne/sandbox/settings.py`:

```python
"""Sandbox settings (ADR 0001 §7, §15): the host's `[sandbox]` and `[platform]` tables, each adapter's
binary, the profiles, and each workstream's `[sandbox]` additions and effective tiers. Read once when wsd
starts; a change needs a restart. Errors name keys, never values: `tool_env` may carry paths."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heterodyne.config import Config, ConfigError, load, paths
from heterodyne.config.layers import as_table, string_list, table_at
from heterodyne.config.secret_scan import show

HOST = re.compile(r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
HOST_KEYS = frozenset({"egress_approved", "ro_mounts_approved", "image", "openshell", "podman",
                       "max_lifetime_minutes", "stop_margin_minutes", "probe_allowed_host",
                       "probe_denied_host", "agent_probe_seconds", "tool_env"})
# Variables the tools' own environment may not override: the sandbox launch sets them itself.
RESERVED_ENV = frozenset({"HOME", "USER", "LANG", "TERM"})
DENIED_HOSTS = frozenset({"mcp-proxy.anthropic.com"})   # §7 Connectors; spec.py enforces the same set
LOCAL_CLASSES = frozenset({"worktree_edit"})            # the shim's locally classifiable set (D14)
WHERE = "config.toml: [sandbox]"


@dataclass(frozen=True)
class WsSandbox:
    extra_egress: tuple[str, ...] = ()
    extra_ro_mounts: tuple[Path, ...] = ()
    local_classes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SandboxSettings:
    backend: str                             # `[platform] sandbox`, "none" when unset
    image: str
    openshell: str
    podman: str
    tool_env: Mapping[str, str]
    max_lifetime_seconds: int
    stop_margin_seconds: int
    probe_allowed_host: str
    probe_denied_host: str
    agent_probe_seconds: int
    hook_wait_seconds: float
    egress_approved: tuple[str, ...]
    binaries: Mapping[str, str]              # adapter -> `[adapters.<a>].binary`, as configured
    profiles: Mapping[str, Mapping[str, Any]]
    workstreams: Mapping[str, WsSandbox]


def sandbox_settings(env: Mapping[str, str]) -> SandboxSettings:
    from heterodyne.wsd.settings import workstream_names     # wsd.settings imports nothing from here

    host = load(env=env)
    streams = {name: load(name, env) for name in workstream_names(paths.config_dir(env))}
    return from_config(host, streams)


def from_config(host: Config, streams: Mapping[str, Config]) -> SandboxSettings:
    sb = table_at(host.values, "sandbox", "config")
    unknown = set(sb) - HOST_KEYS
    if unknown:
        raise ConfigError(f"{WHERE}: unknown keys {show(unknown)} (allowed: {sorted(HOST_KEYS)})")
    lifetime = _int(sb, "max_lifetime_minutes", 10, 1440) * 60
    margin = _int(sb, "stop_margin_minutes", 1, 120) * 60
    if margin >= lifetime:
        raise ConfigError(f"{WHERE} stop_margin_minutes must be less than max_lifetime_minutes")
    approved = tuple(string_list(sb.get("egress_approved", []), f"{WHERE} egress_approved"))
    for name in approved:
        if not HOST.fullmatch(name):
            raise ConfigError(f"{WHERE} egress_approved: {show(name)} is not a host name")
    backend = table_at(host.values, "platform", "config").get("sandbox", "none")
    if not isinstance(backend, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", backend):
        raise ConfigError("config.toml: [platform] sandbox must be a backend name")
    hook_wait = table_at(host.values, "timeouts", "config").get("hook_wait_seconds")
    if isinstance(hook_wait, bool) or not isinstance(hook_wait, int | float) or not 0 < hook_wait <= 60:
        raise ConfigError("[timeouts] hook_wait_seconds must be a number of seconds, more than 0 and at "
                          "most 60")
    adapters = table_at(host.values, "adapters", "config")
    binaries: dict[str, str] = {}
    for adapter in string_list(adapters.get("known", []), "adapters.known"):
        binary = table_at(adapters, adapter, "adapters").get("binary")
        if not isinstance(binary, str) or not binary:
            raise ConfigError(f"[adapters.{adapter}] binary must be a command name or path")
        binaries[adapter] = binary
    profiles = {name: dict(cast(Mapping[str, Any], value))
                for name, value in table_at(host.values, "profiles", "config.toml").items()}
    return SandboxSettings(
        backend=backend,
        image=_text(sb, "image"),
        openshell=_text(sb, "openshell"),
        podman=_text(sb, "podman"),
        tool_env=_tool_env(sb.get("tool_env", {})),
        max_lifetime_seconds=lifetime,
        stop_margin_seconds=margin,
        probe_allowed_host=_host(sb, "probe_allowed_host"),
        probe_denied_host=_host(sb, "probe_denied_host"),
        agent_probe_seconds=_int(sb, "agent_probe_seconds", 30, 900),
        hook_wait_seconds=float(hook_wait),
        egress_approved=approved,
        binaries=binaries,
        profiles=profiles,
        workstreams={name: _workstream(name, cfg) for name, cfg in streams.items()})


def _workstream(name: str, cfg: Config) -> WsSandbox:
    """The workstream's additions. `layers.check_workstream` has checked them against the host's
    approved lists when the layer was loaded."""
    where = f"workstreams/{name}.toml: [sandbox]"
    sb = table_at(cfg.values, "sandbox", "config")
    egress = tuple(string_list(sb.get("extra_egress", []), f"{where} extra_egress"))
    mounts = tuple(Path(p).resolve(strict=False)
                   for p in string_list(sb.get("extra_ro_mounts", []), f"{where} extra_ro_mounts"))
    local = frozenset(c for c in LOCAL_CLASSES if cfg.policy.tiers.get(c) == "auto_approve")
    return WsSandbox(egress, mounts, local)


def _int(table: Mapping[str, Any], key: str, low: int, high: int) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{WHERE} {key} must be an integer from {low} to {high}")
    return value


def _text(table: Mapping[str, Any], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value or "\0" in value:
        raise ConfigError(f"{WHERE} {key} must be a non-empty string")
    return value


def _host(table: Mapping[str, Any], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not HOST.fullmatch(value) or value in DENIED_HOSTS:
        raise ConfigError(f"{WHERE} {key} must be a host name (and not a denied host)")
    return value


def _tool_env(value: Any) -> dict[str, str]:
    table = as_table(value)
    if table is None:
        raise ConfigError(f"{WHERE} tool_env must be a table of NAME = \"value\" strings")
    found: dict[str, str] = {}
    for name, text in table.items():
        if not ENV_NAME.fullmatch(name) or name in RESERVED_ENV:
            raise ConfigError(f"{WHERE} tool_env: {show(name, False)} is not an allowed variable name")
        if not isinstance(text, str) or "\0" in text:
            raise ConfigError(f"{WHERE} tool_env.{name} must be a string")
        found[name] = text
    return found
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_sandbox_settings.py tests/test_config*.py -q`
Expected: PASS. The existing config tests still pass with the new defaults, because the default `egress_approved`/`ro_mounts_approved` are empty lists, which `check_host` already accepts.

Run: `timeout 300 uv run ruff check src tests && timeout 300 uv run pyright src/heterodyne/sandbox tests/test_sandbox_settings.py tests/sandbox_env.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/heterodyne/sandbox/__init__.py src/heterodyne/sandbox/settings.py src/heterodyne/defaults/defaults.toml \
  tests/sandbox_env.py tests/test_sandbox_settings.py
git commit -m "plan4 T1: sandbox settings ([sandbox], [platform] sandbox, per-workstream additions)"
```

### Task 2: The sandbox spec and the credential check

**Files:**
- Create: `src/heterodyne/sandbox/spec.py`
- Modify: `src/heterodyne/wsd/accounts.py` (`Accounts.login_paths`, `ConfiguredAccounts.login_paths`)
- Test: `tests/test_sandbox_spec.py`, `tests/test_wsd_accounts.py` (new)

**Interfaces:**
- Consumes: `settings.DENIED_HOSTS`, `config.capabilities.LOGIN_FILES`, `paths.expand`.
- Produces (all in `heterodyne.sandbox.spec`):
  - `SpecRefused(Exception)`, with fixed, path-free wording.
  - `short_id(key) -> str` and `sandbox_name(key, generation) -> str` (D1).
  - `SessionLayout(root)`: `.at(sessions, key)`, `.record`, `.home`, `.bridge`, `.daemon`, `.oa_canary`, `.run(gen)`, `.socket(gen)`, `.probe_socket(gen)`, `.events(gen)`, `.scratch(gen)`.
  - In-sandbox paths: `RUN_INSIDE`, `BRIDGE_INSIDE`, `SOCKET_INSIDE`, `PROBE_SOCKET_INSIDE`, `TOKEN_INSIDE`, `SHIM_INSIDE`, `SHIM_CONFIG_INSIDE`, `PROBES_INSIDE`, `AGENT_PROBE_INSIDE`, `REQUEST_INSIDE`.
  - Value types: `Bind(source, target, read_only)`, `Egress(name, hosts, binaries)`, `AgentFacts(...)`, `SpecInput(...)`, `SandboxSpec(...)`, `Protected(chosen_dir, chosen_files, others)`.
  - `build_spec(SpecInput) -> SandboxSpec` and `check_credentials(spec, protected, real_home) -> None`.
  - `LAUNCHER_ENV`, the variable names a spec may set.
  - `Accounts.login_paths(adapter, account) -> tuple[Path, tuple[Path, ...]]`: the configured login directory and files, `~` expanded, symlinks not resolved.

A spec says everything a backend needs and nothing backend-specific. `AgentFacts` is the adapter's contribution (Task 7 builds it), so `spec.py` names no adapter. Every source path is a host path, and every bind except `/run/hz` is mounted at the same path inside, so the worktree, the home and the CLI keep their paths. That matters because Claude keys its project state by the working directory's path.

- [ ] **Step 1: Write the failing tests**

`tests/test_sandbox_spec.py`:

```python
import re
from dataclasses import replace
from pathlib import Path

import pytest

from heterodyne.sandbox.spec import (
    LAUNCHER_ENV,
    NAME,
    RUN_INSIDE,
    SOCKET_INSIDE,
    AgentFacts,
    Bind,
    Protected,
    SessionLayout,
    SpecInput,
    SpecRefused,
    build_spec,
    check_credentials,
    sandbox_name,
    short_id,
)

KEY = "alpha/bd-1/coder/p-two"


def facts(root: Path) -> AgentFacts:
    cli = root / "home" / ".codex" / "packages" / "rel" / "bin"
    cli.mkdir(parents=True, exist_ok=True)
    (cli / "codex").write_text("")
    layout = SessionLayout.at(root / "sessions", KEY)
    return AgentFacts(cli_root=cli.parent, cli_binary=cli / "codex", config_var="CODEX_HOME",
                      config_dir=layout.home / ".codex", login_names=("auth.json",),
                      model_hosts=("chatgpt.com",), extra_env={},
                      binds=(Bind(layout.bridge, Path("/run/hz-bridge"), False),),
                      read_write=("/run/hz-bridge",))


def spec_input(root: Path, role: str = "coder", **kw: object) -> SpecInput:
    login = root / "home" / ".codex" / "auth.json"
    login.parent.mkdir(parents=True, exist_ok=True)
    login.write_text("{}")
    worktree = root / "repo-btq-bd-1"
    worktree.mkdir(exist_ok=True)
    base = SpecInput(key=KEY, generation=1, role=role, layout=SessionLayout.at(root / "sessions", KEY),
                     worktree=worktree, agent=facts(root), login_files=(login,), extra_egress=(),
                     extra_ro_mounts=(), probe_allowed_host="api.openai.com", uid=1000, gid=1000)
    return replace(base, **kw)  # type: ignore[arg-type]


def protected(root: Path) -> Protected:
    other = root / "home" / ".codex-b"
    chosen = root / "home" / ".codex"
    return Protected(chosen_dir=chosen, chosen_files=(chosen / "auth.json",),
                     others=(other, other / "auth.json"))


def test_names_are_short_and_deterministic() -> None:
    assert short_id(KEY) == short_id(KEY) and re.fullmatch(r"hz[0-9a-f]{12}", short_id(KEY))
    assert NAME.fullmatch(sandbox_name(KEY, 1)) and len(sandbox_name(KEY, 9999)) <= 19
    assert sandbox_name(KEY, 2) != sandbox_name(KEY, 1)
    for bad in (0, 10000):
        with pytest.raises(SpecRefused):
            sandbox_name(KEY, bad)


def test_layout(tmp_path: Path) -> None:
    lay = SessionLayout.at(tmp_path, KEY)
    assert lay.root == tmp_path / short_id(KEY)
    assert lay.socket(3) == lay.root / "r3" / "s.sock" and lay.probe_socket(3) == lay.root / "r3" / "p.sock"
    assert lay.events(3) == lay.root / "r3-events.jsonl" and lay.oa_canary.parent == lay.root
    assert lay.daemon == lay.bridge / "daemon"


def test_coder_spec(tmp_path: Path) -> None:
    i = spec_input(tmp_path)
    spec = build_spec(i)
    by_target = {b.target: b for b in spec.binds}
    assert not by_target[i.worktree].read_only and not by_target[i.layout.home].read_only
    assert by_target[RUN_INSIDE].source == i.layout.run(1) and by_target[RUN_INSIDE].read_only
    assert by_target[i.agent.cli_root].read_only
    [login] = spec.logins
    assert login.target == i.layout.home / ".codex" / "auth.json" and login.read_only
    assert login.source == (tmp_path / "home" / ".codex" / "auth.json").resolve()
    assert str(i.worktree) in spec.read_write and str(i.layout.home) in spec.read_write
    assert {e.name: e.hosts for e in spec.egress} == {"model": ("chatgpt.com",),
                                                     "probe_control": ("api.openai.com",)}
    assert {e.name: e.binaries for e in spec.egress}["probe_control"] == ("/usr/bin/curl",)
    assert set(spec.env) <= LAUNCHER_ENV and spec.env["HZ_SESSION_SOCKET"] == str(SOCKET_INSIDE)
    assert spec.env["CODEX_HOME"] == str(i.layout.home / ".codex")
    assert spec.workdir == i.worktree


def test_reviewer_worktree_is_read_only(tmp_path: Path) -> None:
    i = spec_input(tmp_path, role="reviewer")
    spec = build_spec(i)
    assert {b.target: b for b in spec.binds}[i.worktree].read_only
    assert str(i.worktree) in spec.read_only and str(i.worktree) not in spec.read_write


def test_extra_egress_and_mounts(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    spec = build_spec(spec_input(tmp_path, extra_egress=("github.com",), extra_ro_mounts=(shared,)))
    assert {e.name: e.hosts for e in spec.egress}["extra"] == ("github.com",)
    assert Bind(shared, shared, True) in spec.binds


@pytest.mark.parametrize("change, needle", [
    ({"extra_egress": ("mcp-proxy.anthropic.com",)}, "denied host"),
    ({"generation": 0}, "generation"),
])
def test_refusals(tmp_path: Path, change: dict[str, object], needle: str) -> None:
    with pytest.raises(SpecRefused, match=needle):
        build_spec(spec_input(tmp_path, **change))


def test_long_socket_path_is_refused(tmp_path: Path) -> None:
    deep = tmp_path / ("d" * 90)
    i = spec_input(tmp_path)
    with pytest.raises(SpecRefused, match="socket path"):
        build_spec(replace(i, layout=SessionLayout.at(deep, KEY)))


def test_missing_login_is_refused(tmp_path: Path) -> None:
    i = spec_input(tmp_path)
    (tmp_path / "home" / ".codex" / "auth.json").unlink()
    with pytest.raises(SpecRefused, match="login file"):
        build_spec(i)


def test_credentials_pass_with_cli_inside_the_chosen_login_dir(tmp_path: Path) -> None:
    spec = build_spec(spec_input(tmp_path))      # the CLI root is under ~/.codex/packages, as installed
    check_credentials(spec, protected(tmp_path), tmp_path / "home")


@pytest.mark.parametrize("source", ["home/.codex-b", "home", "home/.codex", "."])
def test_a_bind_exposing_a_login_or_the_home_is_refused(tmp_path: Path, source: str) -> None:
    spec = build_spec(spec_input(tmp_path))
    (tmp_path / "home" / ".codex-b").mkdir(parents=True, exist_ok=True)
    src = (tmp_path / source).resolve()
    bad = replace(spec, binds=(*spec.binds, Bind(src, Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


def test_a_symlinked_route_to_another_login_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "home" / ".codex-b"
    other.mkdir(parents=True)
    (other / "auth.json").write_text("{}")
    link = tmp_path / "innocent"
    link.symlink_to(other)
    spec = build_spec(spec_input(tmp_path))
    bad = replace(spec, binds=(*spec.binds, Bind(link, Path("/mnt/x"), True)))
    with pytest.raises(SpecRefused):
        check_credentials(bad, protected(tmp_path), tmp_path / "home")


def test_login_binds_must_be_exactly_the_chosen_files_read_only(tmp_path: Path) -> None:
    spec = build_spec(spec_input(tmp_path))
    [login] = spec.logins
    for logins in ((), (replace(login, read_only=False),), (login, login)):
        with pytest.raises(SpecRefused, match="login binds"):
            check_credentials(replace(spec, logins=logins), protected(tmp_path), tmp_path / "home")
```

Create `tests/test_wsd_accounts.py`:

```python
from pathlib import Path

from heterodyne.wsd.accounts import ConfiguredAccounts


def test_login_paths_are_configured_not_canonical(tmp_path: Path) -> None:
    accounts = ConfiguredAccounts({"p": "codex"}, {"HOME": str(tmp_path)})
    codex = tmp_path / ".codex"
    assert accounts.login_paths("codex", "default") == (codex, (codex / "auth.json",))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_spec.py tests/test_wsd_accounts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.sandbox.spec'` and `AttributeError: 'ConfiguredAccounts' object has no attribute 'login_paths'`.

- [ ] **Step 3: Add `login_paths`**

In `src/heterodyne/wsd/accounts.py`, add to the `Accounts` protocol, after `login_resolves`:

```python
    def login_paths(self, adapter: str, account: str) -> tuple[Path, tuple[Path, ...]]:
        """The account's login directory and login files as configured: `~` expanded, symlinks not
        resolved. For the sandbox's credential check and Other accounts probe (§7), never hashed. Raises
        ConfigError (path-free) for an unknown account."""
        ...
```

and to `ConfiguredAccounts`, after `login_resolves`:

```python
    def login_paths(self, adapter: str, account: str) -> tuple[Path, tuple[Path, ...]]:
        login_dir = paths.expand(self._configured_dir(adapter, account), self.env)
        return login_dir, tuple(login_dir / f for f in LOGIN_FILES[adapter])
```

Add `from pathlib import Path` to its imports. Every other implementation of `Accounts` in `tests/` subclasses `ConfiguredAccounts` (check with `grep -rn "def login_resolves" tests`, which should print nothing). Any one that doesn't gets the same two-line method.

- [ ] **Step 4: Write `spec.py`**

`src/heterodyne/sandbox/spec.py`:

```python
"""The platform-neutral sandbox spec (ADR 0001 §7): what one session's sandbox binds, may write, may reach
and gets in its environment, and the credential check every launch runs on it. Backends compile a spec
(openshell.py); adapters contribute `AgentFacts`; nothing here names an adapter or a backend.

Every host path is bound at the same path inside, except the generation's run directory, which is
`/run/hz` (read-only). Names follow D1 and paths follow D2 of plan 4.
"""

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from heterodyne.sandbox.settings import DENIED_HOSTS, HOST

RUN_INSIDE = Path("/run/hz")
BRIDGE_INSIDE = Path("/run/hz-bridge")
SOCKET_INSIDE = RUN_INSIDE / "s.sock"
PROBE_SOCKET_INSIDE = RUN_INSIDE / "p.sock"
TOKEN_INSIDE = RUN_INSIDE / "token"
SHIM_INSIDE = RUN_INSIDE / "shim.py"
SHIM_CONFIG_INSIDE = RUN_INSIDE / "shim.json"
PROBES_INSIDE = RUN_INSIDE / "probes.py"
AGENT_PROBE_INSIDE = RUN_INSIDE / "agent-probe.json"
REQUEST_INSIDE = RUN_INSIDE / "ws-request"
SYSTEM_READ_ONLY = ("/usr", "/lib", "/lib64", "/etc", "/proc", "/dev/urandom")
SYSTEM_READ_WRITE = ("/tmp", "/dev/null", "/dev/tty", "/dev/pts")
READ_ONLY_ROLES = frozenset({"reviewer"})
CURL = "/usr/bin/curl"
MAX_GENERATION = 9999
MAX_SOCKET_PATH = 100           # bytes; sun_path holds 108 with its NUL, and leave room
NAME = re.compile(r"hz[0-9a-f]{12}g[0-9]{1,4}")
BASE_PATH = ("/usr/local/bin", "/usr/bin", "/bin")
# Every variable a spec may set (D4). The adapter's config variable and fixed extras are among them.
LAUNCHER_ENV = frozenset({"HOME", "PATH", "LANG", "TERM", "USER", "HZ_SESSION_SOCKET", "CLAUDE_CONFIG_DIR",
                          "CODEX_HOME", "ENABLE_CLAUDEAI_MCP_SERVERS"})


class SpecRefused(Exception):
    """The session can't be given a sandbox as asked. Fixed wording, no paths: it becomes LaunchFailed."""


def short_id(key: str) -> str:
    return "hz" + hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:12]


def sandbox_name(key: str, generation: int) -> str:
    if not 1 <= generation <= MAX_GENERATION:
        raise SpecRefused(f"generation must be from 1 to {MAX_GENERATION}")
    return f"{short_id(key)}g{generation}"


@dataclass(frozen=True)
class SessionLayout:
    """One session's host directory (D2). Only `home`, `bridge` and `run(gen)` are ever bound in; the
    record, the event spools, the scratch and the canary stay outside every bind."""
    root: Path

    @classmethod
    def at(cls, sessions: Path, key: str) -> "SessionLayout":
        return cls(sessions / short_id(key))

    @property
    def record(self) -> Path:
        return self.root / "session.json"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def bridge(self) -> Path:
        return self.root / "bridge"

    @property
    def daemon(self) -> Path:
        return self.bridge / "daemon"

    @property
    def oa_canary(self) -> Path:
        return self.root / "oa-canary"

    def run(self, generation: int) -> Path:
        return self.root / f"r{generation}"

    def socket(self, generation: int) -> Path:
        return self.run(generation) / "s.sock"

    def probe_socket(self, generation: int) -> Path:
        return self.run(generation) / "p.sock"

    def events(self, generation: int) -> Path:
        return self.root / f"r{generation}-events.jsonl"

    def scratch(self, generation: int) -> Path:
        return self.root / f"r{generation}-scratch"


@dataclass(frozen=True)
class Bind:
    source: Path            # host
    target: Path            # inside
    read_only: bool


@dataclass(frozen=True)
class Egress:
    """One allowed network rule: these exact hosts on port 443, for connections whose executable (or an
    executable ancestor, S5 ADR impact 9) is one of `binaries`."""
    name: str
    hosts: tuple[str, ...]
    binaries: tuple[str, ...]


@dataclass(frozen=True)
class AgentFacts:
    """The session's adapter's part of its sandbox (Task 7 builds it)."""
    cli_root: Path                      # the CLI's install root, bound read-only at its host path
    cli_binary: Path                    # the CLI executable: the model rule's binary
    config_var: str                     # the variable naming the CLI's config directory
    config_dir: Path                    # inside the synthetic home
    login_names: tuple[str, ...]        # the adapter's login files, bound read-only into config_dir
    model_hosts: tuple[str, ...]
    extra_env: Mapping[str, str] = field(default_factory=dict[str, str])
    binds: tuple[Bind, ...] = ()
    read_write: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpecInput:
    key: str
    generation: int
    role: str
    layout: SessionLayout
    worktree: Path
    agent: AgentFacts
    login_files: tuple[Path, ...]       # the chosen account's login files, as configured
    extra_egress: tuple[str, ...]
    extra_ro_mounts: tuple[Path, ...]
    probe_allowed_host: str
    uid: int
    gid: int


@dataclass(frozen=True)
class SandboxSpec:
    name: str
    workdir: Path
    binds: tuple[Bind, ...]             # everything but the login files
    logins: tuple[Bind, ...]            # the chosen account's login files, canonical sources
    read_only: tuple[str, ...]          # filesystem policy: paths inside
    read_write: tuple[str, ...]
    egress: tuple[Egress, ...]
    env: Mapping[str, str]
    uid: int
    gid: int


def build_spec(i: SpecInput) -> SandboxSpec:
    name = sandbox_name(i.key, i.generation)
    for sock in (i.layout.socket(i.generation), i.layout.probe_socket(i.generation)):
        if len(os.fsencode(sock)) > MAX_SOCKET_PATH:
            raise SpecRefused("the session socket path is too long; use a shorter state directory")
    hosts = (*i.agent.model_hosts, *i.extra_egress, i.probe_allowed_host)
    if any(h in DENIED_HOSTS or not HOST.fullmatch(h) for h in hosts):
        raise SpecRefused("the egress list names a denied host or an invalid host name")
    if set(i.agent.extra_env) - LAUNCHER_ENV or i.agent.config_var not in LAUNCHER_ENV:
        raise SpecRefused("the adapter sets a variable outside the launcher's allowlist")
    logins: list[Bind] = []
    for configured, login in zip(i.login_files, i.agent.login_names, strict=True):
        try:
            source = configured.resolve(strict=True)
        except (OSError, RuntimeError):
            raise SpecRefused("the chosen account's login file is missing or unresolvable") from None
        if not source.is_file():
            raise SpecRefused("the chosen account's login file is not a regular file")
        logins.append(Bind(source, i.agent.config_dir / login, True))
    worktree_ro = i.role in READ_ONLY_ROLES
    binds = (Bind(i.layout.home, i.layout.home, False),
             Bind(i.worktree, i.worktree, worktree_ro),
             Bind(i.layout.run(i.generation), RUN_INSIDE, True),
             Bind(i.agent.cli_root, i.agent.cli_root, True),
             *i.agent.binds,
             *(Bind(m, m, True) for m in i.extra_ro_mounts))
    read_only = (*SYSTEM_READ_ONLY, str(RUN_INSIDE), str(i.agent.cli_root),
                 *(str(m) for m in i.extra_ro_mounts), *(str(b.target) for b in logins),
                 *((str(i.worktree),) if worktree_ro else ()))
    read_write = (*SYSTEM_READ_WRITE, str(i.layout.home), *(() if worktree_ro else (str(i.worktree),)),
                  *i.agent.read_write)
    cli = (str(i.agent.cli_binary),)
    egress = [Egress("model", i.agent.model_hosts, cli)]
    if i.extra_egress:
        egress.append(Egress("extra", i.extra_egress, cli))
    egress.append(Egress("probe_control", (i.probe_allowed_host,), (CURL,)))
    env = {"HOME": str(i.layout.home), "PATH": ":".join((*BASE_PATH, str(i.agent.cli_binary.parent))),
           "LANG": "C.UTF-8", "TERM": "xterm-256color", "USER": "agent",
           "HZ_SESSION_SOCKET": str(SOCKET_INSIDE), i.agent.config_var: str(i.agent.config_dir),
           **i.agent.extra_env}
    return SandboxSpec(name, i.worktree, binds, tuple(logins), read_only, read_write, tuple(egress), env,
                       i.uid, i.gid)


@dataclass(frozen=True)
class Protected:
    """The login material a session must not see (§7): the chosen account's login directory and files,
    and every other configured account's (the default included), each as configured."""
    chosen_dir: Path
    chosen_files: tuple[Path, ...]
    others: tuple[Path, ...]


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def check_credentials(spec: SandboxSpec, protected: Protected, real_home: Path) -> None:
    """D10. The login binds are exactly the chosen account's login files, read-only. No other bind's
    source, by its own path or its canonical one, equals or contains the real home, a login directory or
    a login file. A source strictly inside a login directory that holds no login file is allowed (the
    Codex CLI installs under its default login directory)."""
    want = {_real(p) for p in protected.chosen_files}
    got = [_real(b.source) for b in spec.logins]
    if sorted(got) != sorted(want) or not all(b.read_only for b in spec.logins):
        raise SpecRefused("the login binds are not exactly the chosen account's login files, read-only")
    guarded = {q for p in (protected.chosen_dir, *protected.chosen_files, *protected.others)
               for q in (p, _real(p))}
    homes = {real_home, _real(real_home)}
    for b in spec.binds:
        for source in {b.source, _real(b.source)}:
            if any(h.is_relative_to(source) for h in homes):
                raise SpecRefused("a bind would expose the real home directory")
            if any(p.is_relative_to(source) for p in guarded):
                raise SpecRefused("a bind would expose a login directory or login file")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_sandbox_spec.py tests/test_wsd_accounts.py -q`
Expected: PASS.

Run: `timeout 300 uv run ruff check src tests && timeout 300 uv run pyright src/heterodyne/sandbox src/heterodyne/wsd/accounts.py tests/test_sandbox_spec.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/heterodyne/sandbox/spec.py src/heterodyne/wsd/accounts.py tests/test_sandbox_spec.py tests/test_wsd_accounts.py
git commit -m "plan4 T2: platform-neutral sandbox spec, session layout and the credential check"
```

### Task 3 [r15]: The backend protocol and OpenShell policy compilation

**Files:**
- Create: `src/heterodyne/sandbox/backend.py`, `src/heterodyne/sandbox/openshell.py` (pure part)
- Test: `tests/test_sandbox_openshell_policy.py`

**Interfaces:**
- Consumes: `spec.SandboxSpec`, `spec.Bind`, `spec.Egress`.
- Produces:
  - `backend.Backend` (Protocol), `backend.ExecResult(returncode, stdout, stderr)`;
  - `backend.BackendUnavailable`: the backend can't be asked, so nothing is known;
  - `backend.BackendError`: a step failed, with fixed wording;
  - `openshell.policy(spec) -> dict[str, Any]`;
  - `openshell.driver_config(spec) -> dict[str, Any]`;
  - `openshell.create_argv(binary, spec, image, policy_file) -> list[str]`.

This is the §11 unit item "sandbox profile compilation" for the OpenShell backend: a pure function from a spec to OpenShell 0.1.2's policy and the podman driver's mount configuration, in the shape S5 ran (`spikes/s5/launch.py`, `Session.policy`/`mounts`/`create`). It is **[r15]** because it commits to OpenShell. If plan 4b replaces OpenShell, its bubblewrap compilation takes the same `SandboxSpec`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sandbox_openshell_policy.py`:

```python
import json
from pathlib import Path

from heterodyne.sandbox.openshell import create_argv, driver_config, policy
from heterodyne.sandbox.spec import Bind, Egress, SandboxSpec


def spec() -> SandboxSpec:
    return SandboxSpec(
        name="hz0123456789abg1", workdir=Path("/w/repo"),
        binds=(Bind(Path("/s/home"), Path("/s/home"), False), Bind(Path("/s/r1"), Path("/run/hz"), True)),
        logins=(Bind(Path("/h/.codex/auth.json"), Path("/s/home/.codex/auth.json"), True),),
        read_only=("/usr", "/run/hz"), read_write=("/tmp", "/s/home"),
        egress=(Egress("model", ("chatgpt.com",), ("/opt/codex/bin/codex",)),
                Egress("probe_control", ("api.openai.com",), ("/usr/bin/curl",))),
        env={"HOME": "/s/home", "PATH": "/usr/bin"}, uid=1000, gid=1001)


def test_policy() -> None:
    assert policy(spec()) == {
        "version": 1,
        "filesystem_policy": {"include_workdir": False, "read_only": ["/usr", "/run/hz"],
                              "read_write": ["/tmp", "/s/home"]},
        "landlock": {"compatibility": "hard_requirement"},
        "process": {"run_as_user": "1000", "run_as_group": "1001"},
        "network_policies": {
            "model": {"endpoints": [{"host": "chatgpt.com", "port": 443}],
                      "binaries": [{"path": "/opt/codex/bin/codex"}]},
            "probe_control": {"endpoints": [{"host": "api.openai.com", "port": 443}],
                              "binaries": [{"path": "/usr/bin/curl"}]},
        },
    }


def test_driver_config_binds_everything_including_logins() -> None:
    mounts = driver_config(spec())["podman"]["mounts"]
    assert mounts == [
        {"type": "bind", "source": "/s/home", "target": "/s/home", "read_only": False},
        {"type": "bind", "source": "/s/r1", "target": "/run/hz", "read_only": True},
        {"type": "bind", "source": "/h/.codex/auth.json", "target": "/s/home/.codex/auth.json",
         "read_only": True},
    ]


def test_create_argv() -> None:
    argv = create_argv("openshell", spec(), "localhost/heterodyne-agent:1", Path("/s/r1-scratch/policy.yaml"))
    assert argv[:12] == ["openshell", "sandbox", "create", "--name", "hz0123456789abg1", "--detach", "--from",
                         "localhost/heterodyne-agent:1", "--policy", "/s/r1-scratch/policy.yaml",
                         "--driver-config-json", json.dumps(driver_config(spec()))]
    assert argv[12:] == ["--no-credential-warnings", "--env", "HOME=/s/home", "--env", "PATH=/usr/bin",
                         "--", "sleep", "infinity"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_openshell_policy.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.sandbox.openshell'`.

- [ ] **Step 3: Write `backend.py`**

`src/heterodyne/sandbox/backend.py`:

```python
"""What SandboxRuntime needs from a sandbox backend (ADR 0001 §7 Runtime). OpenShell implements it
(openshell.py); the tests' FakeBackend runs commands on the host."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from heterodyne.sandbox.spec import SandboxSpec


class BackendUnavailable(Exception):
    """The backend could not be asked (missing, down, timed out). Nothing is known."""


class BackendError(Exception):
    """A backend step failed. Fixed wording, no paths or output."""


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class Backend(Protocol):
    def available(self) -> bool:
        """Whether the backend answers at all (checked before every claim)."""
        ...

    def names(self) -> set[str]:
        """The names of every sandbox the backend lists. Raises BackendUnavailable; never partial."""
        ...

    def create(self, spec: SandboxSpec, scratch: Path) -> None:
        """Create the sandbox and return once commands can run in it. `scratch` is a host directory
        outside every bind, for the backend's own files. Raises BackendError or BackendUnavailable;
        either way the sandbox may exist and is deleted by the caller."""
        ...

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        """Run `argv` inside without a tty, stdin from `input` (else empty). Raises BackendError on a
        timeout and BackendUnavailable when the backend can't be run."""
        ...

    def tty_argv(self, name: str, workdir: Path, argv: Sequence[str]) -> list[str]:
        """A host command that runs `argv` inside with a tty, for a tmux pane. It carries its own
        environment (`env -i ...`), so the pane inherits nothing from the tmux server."""
        ...

    def delete(self, name: str) -> bool:
        """Delete the sandbox, ending every process in it, and wait until the backend no longer lists it.
        True once confirmed; False if it can't confirm (still listed, or the backend didn't answer)."""
        ...

    def logs(self, name: str, since: float) -> list[str]:
        """The sandbox supervisor's log lines stamped at or after `since` - 2 (epoch seconds)."""
        ...

    def network_mode(self, name: str) -> str:
        """The workload container's network mode, as the container runtime reports it from outside."""
        ...

    def workload_pid(self, name: str) -> int:
        """The host PID of the workload container's first process (its netns is the sandbox's)."""
        ...

    def pane_env(self) -> Mapping[str, str]:
        """The environment `tty_argv` gives the host command."""
        ...
```

- [ ] **Step 4: Write the pure part of `openshell.py`**

`src/heterodyne/sandbox/openshell.py`:

```python
"""The OpenShell backend (ADR 0001 §7 Runtime; spike S5): NVIDIA OpenShell 0.1.2 with the podman compute
driver. `policy`, `driver_config` and `create_argv` compile a SandboxSpec (pure); OpenShellBackend runs
the openshell and podman commands.

What OpenShell enforces, as S5 measured it: Landlock filesystem rules (hard requirement), a seccomp
broker that answers every INET socket operation itself, a podman `--network none` netns, and a
TLS-terminating egress proxy keyed by exact host and executable (or executable ancestor). The policy is
fixed at create time.
"""

import json
from pathlib import Path
from typing import Any

from heterodyne.sandbox.spec import SandboxSpec


def policy(spec: SandboxSpec) -> dict[str, Any]:
    return {
        "version": 1,
        "filesystem_policy": {"include_workdir": False, "read_only": list(spec.read_only),
                              "read_write": list(spec.read_write)},
        "landlock": {"compatibility": "hard_requirement"},
        "process": {"run_as_user": str(spec.uid), "run_as_group": str(spec.gid)},
        "network_policies": {
            e.name: {"endpoints": [{"host": h, "port": 443} for h in e.hosts],
                     "binaries": [{"path": b} for b in e.binaries]}
            for e in spec.egress},
    }


def driver_config(spec: SandboxSpec) -> dict[str, Any]:
    return {"podman": {"mounts": [
        {"type": "bind", "source": str(b.source), "target": str(b.target), "read_only": b.read_only}
        for b in (*spec.binds, *spec.logins)]}}


def create_argv(binary: str, spec: SandboxSpec, image: str, policy_file: Path) -> list[str]:
    argv = [binary, "sandbox", "create", "--name", spec.name, "--detach", "--from", image,
            "--policy", str(policy_file), "--driver-config-json", json.dumps(driver_config(spec)),
            "--no-credential-warnings"]
    for name, value in spec.env.items():
        argv += ["--env", f"{name}={value}"]
    return [*argv, "--", "sleep", "infinity"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_sandbox_openshell_policy.py -q && timeout 300 uv run pyright src/heterodyne/sandbox`
Expected: PASS; pyright clean.

- [ ] **Step 6: Commit**

```bash
git add src/heterodyne/sandbox/backend.py src/heterodyne/sandbox/openshell.py tests/test_sandbox_openshell_policy.py
git commit -m "plan4 T3: backend protocol and OpenShell policy compilation"
```

### Task 4 [r15]: The OpenShell backend

**Files:**
- Modify: `src/heterodyne/sandbox/openshell.py` (append `OpenShellBackend`)
- Test: `tests/test_sandbox_openshell_backend.py`

**Interfaces:**
- Consumes: Task 3's protocol and `create_argv`, `policy`; `spec.NAME`.
- Produces: `OpenShellBackend(openshell, podman, image, env, *, runner=None, clock=time.monotonic, sleep=time.sleep)`, which implements `Backend`; `Runner = Callable[[Sequence[str], bytes | None, float], subprocess.CompletedProcess[bytes]]`; `tool_env(base, overrides) -> dict[str, str]`.

Every command gets stdin from `/dev/null` unless it is given input: `openshell sandbox exec` waits on an open stdin (S5 ADR impact 7). Deletion is asynchronous, so `delete` polls `sandbox list` for up to 60 s. The workload container's name is `openshell-default--<sandbox>-…` (S5's `outer_fence`). The offline tests drive a scripted runner and a fake clock; only Task 14 runs the real commands.

- [ ] **Step 1: Write the failing tests**

`tests/test_sandbox_openshell_backend.py`:

```python
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_sandbox_openshell_policy import spec

from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.openshell import OpenShellBackend, policy, tool_env


class Script:
    """A runner that answers by argv prefix, in order, and records every call."""

    def __init__(self, *answers: tuple[tuple[str, ...], int, bytes]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[list[str], bytes | None, float]] = []
        self.now = 0.0

    def __call__(self, argv: Sequence[str], data: bytes | None,
                 timeout: float) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(argv), data, timeout))
        for n, (prefix, rc, out) in enumerate(self.answers):
            if tuple(argv[:len(prefix)]) == prefix:
                if len(self.answers) > 1:
                    del self.answers[n]
                return subprocess.CompletedProcess(list(argv), rc, out, b"")
        raise AssertionError(f"unexpected command {argv}")

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def backend(script: Script) -> OpenShellBackend:
    return OpenShellBackend("openshell", "podman", "img:1", {"PATH": "/usr/bin"}, runner=script,
                            clock=script.clock, sleep=script.sleep)


def test_create_writes_the_policy_privately_and_waits_until_exec_works(tmp_path: Path) -> None:
    script = Script((("openshell", "sandbox", "create"), 0, b""), (("openshell", "sandbox", "exec"), 1, b""),
                    (("openshell", "sandbox", "exec"), 0, b""))
    backend(script).create(spec(), tmp_path / "scratch")
    written = (tmp_path / "scratch" / "policy.yaml")
    assert written.stat().st_mode & 0o777 == 0o600 and (tmp_path / "scratch").stat().st_mode & 0o777 == 0o700
    assert json.loads(written.read_text()) == policy(spec())
    assert script.calls[0][0][:3] == ["openshell", "sandbox", "create"] and script.calls[0][2] == 300
    assert script.calls[-1][0][-2:] == ["--", "true"]


def test_create_failure_is_a_backend_error(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="create"):
        backend(Script((("openshell", "sandbox", "create"), 1, b""),)).create(spec(), tmp_path / "s")


def test_exec_passes_input_and_no_tty() -> None:
    script = Script((("openshell", "sandbox", "exec"), 3, b"out"),)
    r = backend(script).exec("hz0123456789abg1", Path("/w"), ["python3", "-"], input=b"{}", timeout=9)
    assert (r.returncode, r.stdout) == (3, b"out")
    argv, data, timeout = script.calls[0]
    assert argv == ["openshell", "sandbox", "exec", "-n", "hz0123456789abg1", "--no-tty", "--no-login-shell",
                    "--workdir", "/w", "--", "python3", "-"]
    assert (data, timeout) == (b"{}", 9)


def test_exec_timeout_is_a_backend_error() -> None:
    def runner(argv: Sequence[str], data: bytes | None, timeout: float) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(list(argv), timeout)

    b = OpenShellBackend("openshell", "podman", "img:1", {}, runner=runner)
    with pytest.raises(BackendError, match="timed out"):
        b.exec("hz0123456789abg1", Path("/w"), ["true"], timeout=1)


def test_names_keeps_only_our_names_and_fails_closed() -> None:
    listing = b"NAME STATUS\nhz0123456789abg1 Ready\nother-box Ready\nhzffffffffffffg22 Deleting\n"
    assert backend(Script((("openshell", "sandbox", "list"), 0, listing),)).names() == {
        "hz0123456789abg1", "hzffffffffffffg22"}
    with pytest.raises(BackendUnavailable):
        backend(Script((("openshell", "sandbox", "list"), 1, b""),)).names()

    def broken(argv: Sequence[str], data: bytes | None, timeout: float) -> subprocess.CompletedProcess[bytes]:
        raise OSError("no such file")

    with pytest.raises(BackendUnavailable):
        OpenShellBackend("openshell", "podman", "img:1", {}, runner=broken).names()


def test_delete_waits_until_unlisted() -> None:
    script = Script((("openshell", "sandbox", "delete"), 0, b""),
                    (("openshell", "sandbox", "list"), 0, b"hz0123456789abg1 Deleting\n"),
                    (("openshell", "sandbox", "list"), 0, b"\n"))
    assert backend(script).delete("hz0123456789abg1") is True


def test_delete_unconfirmed_after_the_deadline() -> None:
    script = Script((("openshell", "sandbox", "delete"), 0, b""),
                    (("openshell", "sandbox", "list"), 0, b"hz0123456789abg1 Deleting\n"))
    assert backend(script).delete("hz0123456789abg1") is False
    assert script.now >= 60


def test_logs_filters_by_stamp() -> None:
    out = b"[100.5] old\n[199.0] edge\n[250.0] new\nnot a stamp\n"
    assert backend(Script((("openshell", "logs"), 0, out),)).logs("hz0123456789abg1", 201.0) == [
        "[199.0] edge", "[250.0] new"]


def test_network_mode_and_pid_from_the_one_workload_container() -> None:
    script = Script((("podman", "ps"), 0, b"abc123\n"), (("podman", "inspect"), 0, b"none\n"))
    assert backend(script).network_mode("hz0123456789abg1") == "none"
    assert script.calls[0][0] == ["podman", "ps", "--filter", "name=^openshell-default--hz0123456789abg1-",
                                  "--format", "{{.ID}}"]
    two = Script((("podman", "ps"), 0, b"a\nb\n"),)
    assert backend(two).network_mode("hz0123456789abg1") == "containers=2"
    pid = Script((("podman", "ps"), 0, b"abc\n"), (("podman", "inspect"), 0, b"4242\n"))
    assert backend(pid).workload_pid("hz0123456789abg1") == 4242
    with pytest.raises(BackendError):
        backend(Script((("podman", "ps"), 0, b""),)).workload_pid("hz0123456789abg1")


def test_tty_argv_carries_its_own_environment() -> None:
    argv = backend(Script()).tty_argv("hz0123456789abg1", Path("/w"), ["claude", "--resume", "x"])
    assert argv == ["env", "-i", "PATH=/usr/bin", "openshell", "sandbox", "exec", "-n", "hz0123456789abg1",
                    "--tty", "--no-login-shell", "--workdir", "/w", "--", "claude", "--resume", "x"]


def test_tool_env_takes_only_what_the_tools_need() -> None:
    base = {"HOME": "/h", "PATH": "/usr/bin", "XDG_RUNTIME_DIR": "/run/user/1", "SECRET_TOKEN": "x",
            "LANG": "C"}
    assert tool_env(base, {"PATH": "/opt/podman5/bin:/usr/bin", "CONTAINERS_CONF": "/opt/c.conf"}) == {
        "HOME": "/h", "PATH": "/opt/podman5/bin:/usr/bin", "XDG_RUNTIME_DIR": "/run/user/1", "LANG": "C",
        "CONTAINERS_CONF": "/opt/c.conf"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_openshell_backend.py -q`
Expected: FAIL with `ImportError: cannot import name 'OpenShellBackend'`.

- [ ] **Step 3: Implement**

Append to `src/heterodyne/sandbox/openshell.py` (extend its imports to `json, os, subprocess, time`, `from collections.abc import Callable, Mapping, Sequence`, and `from heterodyne.sandbox.backend import BackendError, BackendUnavailable, ExecResult` plus `from heterodyne.sandbox.spec import NAME, SandboxSpec`):

```python
Runner = Callable[[Sequence[str], bytes | None, float], subprocess.CompletedProcess[bytes]]
# The variables the openshell and podman clients need from wsd's own environment; tool_env adds to them.
TOOL_BASE = ("HOME", "PATH", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "LANG")
CREATE_SECONDS = 300
READY_SECONDS = 60
DELETE_SECONDS = 60
QUERY_SECONDS = 30


def tool_env(base: Mapping[str, str], overrides: Mapping[str, str]) -> dict[str, str]:
    return {**{k: base[k] for k in TOOL_BASE if k in base}, **overrides}


def _runner(env: Mapping[str, str]) -> Runner:
    def run(argv: Sequence[str], data: bytes | None, timeout: float) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(list(argv), input=data, stdin=None if data is not None else subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, env=dict(env), check=False)
    return run


class OpenShellBackend:
    def __init__(self, openshell: str, podman: str, image: str, env: Mapping[str, str], *,
                 runner: Runner | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.openshell = openshell
        self.podman = podman
        self.image = image
        self.env = dict(env)
        self.run = runner if runner is not None else _runner(self.env)
        self.clock = clock
        self.sleep = sleep

    def _call(self, argv: Sequence[str], timeout: float = QUERY_SECONDS,
              data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return self.run(argv, data, timeout)
        except subprocess.TimeoutExpired:
            raise BackendError(f"{Path(argv[0]).name} {argv[1]} timed out") from None
        except OSError:
            raise BackendUnavailable(f"{Path(argv[0]).name} can't be run") from None

    def available(self) -> bool:
        try:
            return self._call([self.openshell, "sandbox", "list"]).returncode == 0
        except (BackendError, BackendUnavailable):
            return False

    def names(self) -> set[str]:
        try:
            proc = self._call([self.openshell, "sandbox", "list"])
        except BackendError:
            raise BackendUnavailable("openshell sandbox list timed out") from None
        if proc.returncode != 0:
            raise BackendUnavailable("openshell sandbox list failed")
        return {t for t in proc.stdout.decode("utf-8", "replace").split() if NAME.fullmatch(t)}

    def create(self, spec: SandboxSpec, scratch: Path) -> None:
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        scratch.chmod(0o700)
        policy_file = scratch / "policy.yaml"            # JSON is valid YAML
        fd = os.open(policy_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(policy(spec), fh, indent=1)
        proc = self._call(create_argv(self.openshell, spec, self.image, policy_file), CREATE_SECONDS)
        if proc.returncode != 0:
            raise BackendError("openshell sandbox create failed")
        end = self.clock() + READY_SECONDS
        while self.exec(spec.name, spec.workdir, ["true"], timeout=QUERY_SECONDS).returncode != 0:
            if self.clock() > end:
                raise BackendError("the sandbox did not become ready")
            self.sleep(1)

    def _exec_argv(self, name: str, workdir: Path, tty: bool) -> list[str]:
        return [self.openshell, "sandbox", "exec", "-n", name, "--tty" if tty else "--no-tty",
                "--no-login-shell", "--workdir", str(workdir), "--"]

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        proc = self._call([*self._exec_argv(name, workdir, False), *argv], timeout, input)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def tty_argv(self, name: str, workdir: Path, argv: Sequence[str]) -> list[str]:
        return ["env", "-i", *(f"{k}={v}" for k, v in self.env.items()),
                *self._exec_argv(name, workdir, True), *argv]

    def pane_env(self) -> Mapping[str, str]:
        return dict(self.env)

    def delete(self, name: str) -> bool:
        try:
            self._call([self.openshell, "sandbox", "delete", name])
        except (BackendError, BackendUnavailable):
            pass                 # whether it went is decided only by the listing below
        end = self.clock() + DELETE_SECONDS
        while True:
            try:
                if name not in self.names():
                    return True
            except BackendUnavailable:
                pass
            if self.clock() > end:
                return False
            self.sleep(1)

    def logs(self, name: str, since: float) -> list[str]:
        proc = self._call([self.openshell, "logs", name, "--since", "10m", "--source", "sandbox",
                           "-n", "2000"])
        found: list[str] = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            stamp, sep, _ = line[1:].partition("]")
            try:
                if line.startswith("[") and sep and float(stamp) >= since - 2:
                    found.append(line)
            except ValueError:
                continue
        return found

    def _container(self, name: str) -> list[str]:
        proc = self._call([self.podman, "ps", "--filter", f"name=^openshell-default--{name}-",
                           "--format", "{{.ID}}"])
        return proc.stdout.decode("utf-8", "replace").split() if proc.returncode == 0 else []

    def network_mode(self, name: str) -> str:
        ids = self._container(name)
        if len(ids) != 1:
            return f"containers={len(ids)}"
        proc = self._call([self.podman, "inspect", "-f", "{{.HostConfig.NetworkMode}}", ids[0]])
        return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else "unknown"

    def workload_pid(self, name: str) -> int:
        ids = self._container(name)
        if len(ids) != 1:
            raise BackendError("the workload container is not exactly one container")
        proc = self._call([self.podman, "inspect", "-f", "{{.State.Pid}}", ids[0]])
        try:
            pid = int(proc.stdout.decode().strip())
        except ValueError:
            raise BackendError("the workload container has no PID") from None
        if proc.returncode != 0 or pid <= 1:
            raise BackendError("the workload container has no PID")
        return pid
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_sandbox_openshell_backend.py tests/test_sandbox_openshell_policy.py -q && timeout 300 uv run pyright src/heterodyne/sandbox && timeout 300 uv run ruff check src tests`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/sandbox/openshell.py tests/test_sandbox_openshell_backend.py
git commit -m "plan4 T4: OpenShell backend (create, exec, tty, delete with confirmation, logs, podman facts)"
```

### Task 5: The per-session socket

**Files:**
- Create: `src/heterodyne/session/__init__.py`, `src/heterodyne/session/server.py`
- Test: `tests/test_session_server.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `SessionServer(path, token, events, *, max_spool=4 << 20)` with `.start()`, `.close()`, `.turns() -> TurnState`, `.served -> int`;
  - `TurnState(prompts, stops, last_event, thread_id)` with `.idle`;
  - the reply constants `OK`, `FORBIDDEN`, `UNSUPPORTED`, `MALFORMED`;
  - `UUID`, the canonical-UUID regex.

§7: the socket "accepts only hook events and `ws-request`, each with the launch token. It refuses control operations." A request is one JSON line `{"token", "type", "payload"}` per connection, at most 64 KiB, and the token is compared in constant time. The replies are exact bytes, because the self-test compares them:

| Request | Reply |
|---|---|
| a valid `hook_event` | `{"ok": true}` |
| a valid `ws_request` | `{"ok": false, "error": "unsupported"}` (plan 5 adds processing, D15) |
| any other type, or a bad token | `{"ok": false, "error": "forbidden"}` |
| not a JSON object with a dict payload | `{"ok": false, "error": "malformed"}` |

Every accepted request is spooled, untrusted, to the generation's events file outside the sandbox, up to a cap; plan 7 reads it. The in-memory `TurnState` is what the lifetime stop (D13) and the Codex thread ID (D8) read.

- [ ] **Step 1: Write the failing tests**

`tests/test_session_server.py`:

```python
import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import wait_for

from heterodyne.session.server import SessionServer

TOKEN = "fake-session-token"
THREAD = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[SessionServer]:
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl")
    s.start()
    yield s
    s.close()


def ask(path: Path, raw: bytes) -> str:
    with socket.socket(socket.AF_UNIX) as c:
        c.settimeout(5)
        c.connect(str(path))
        c.sendall(raw)
        return c.makefile("rb").readline().decode().strip()


def req(token: str, kind: str, payload: object) -> bytes:
    return (json.dumps({"token": token, "type": kind, "payload": payload}) + "\n").encode()


def test_replies_are_exact(server: SessionServer, tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"
    assert ask(sock, req(TOKEN, "hook_event", {})) == '{"ok": true}'
    assert ask(sock, req(TOKEN, "approve", {})) == '{"ok": false, "error": "forbidden"}'
    assert ask(sock, req("wrong", "hook_event", {})) == '{"ok": false, "error": "forbidden"}'
    assert ask(sock, req(TOKEN, "ws_request", {"text": "x"})) == '{"ok": false, "error": "unsupported"}'
    assert ask(sock, b"not json\n") == '{"ok": false, "error": "malformed"}'
    assert ask(sock, req(TOKEN, "hook_event", [1])) == '{"ok": false, "error": "malformed"}'


def test_oversized_line_is_refused(server: SessionServer, tmp_path: Path) -> None:
    assert ask(tmp_path / "s.sock", b"x" * (65 * 1024)) == '{"ok": false, "error": "malformed"}'


def test_turn_state_and_thread_id(server: SessionServer, tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"
    assert not server.turns().idle
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "SessionStart", "session_id": "not-a-uuid"}))
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "SessionStart", "session_id": THREAD}))
    other = THREAD.replace("0", "1")
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "SessionStart", "session_id": other}))
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "UserPromptSubmit"}))
    assert not server.turns().idle
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "Stop"}))
    t = server.turns()
    assert (t.prompts, t.stops, t.idle, t.thread_id) == (1, 1, True, THREAD)
    ask(sock, req("wrong", "hook_event", {"hook_event_name": "UserPromptSubmit"}))
    assert server.turns().idle                      # a refused request changes nothing


def test_accepted_requests_are_spooled_with_a_cap(tmp_path: Path) -> None:
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl", max_spool=200)
    s.start()
    try:
        for n in range(10):
            ask(tmp_path / "s.sock", req(TOKEN, "hook_event", {"n": n}))
        ask(tmp_path / "s.sock", req("wrong", "hook_event", {"n": 99}))
    finally:
        s.close()
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert 1 <= len(lines) < 10 and all(json.loads(x)["type"] == "hook_event" for x in lines)
    assert all(json.loads(x)["payload"]["n"] != 99 for x in lines)
    assert (tmp_path / "events.jsonl").stat().st_size <= 200


def test_close_removes_the_socket_and_start_replaces_a_stale_one(tmp_path: Path) -> None:
    path = tmp_path / "s.sock"
    path.write_text("stale")
    s = SessionServer(path, TOKEN, tmp_path / "e.jsonl")
    s.start()
    assert path.is_socket()
    s.close()
    assert not path.exists()


def test_many_concurrent_clients(server: SessionServer, tmp_path: Path) -> None:
    replies: list[str] = []
    def one() -> None:
        replies.append(ask(tmp_path / "s.sock", req(TOKEN, "hook_event", {})))

    threads = [threading.Thread(target=one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    wait_for(lambda: len(replies) == 20 and server.served >= 20)
    assert set(replies) == {'{"ok": true}'}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_session_server.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.session'`.

- [ ] **Step 3: Implement**

`src/heterodyne/session/__init__.py`:

```python
"""The per-session socket (ADR 0001 §7) and the ws-hook/ws-request shims that talk to it (§3.1, §10)."""
```

`src/heterodyne/session/server.py`:

```python
"""The per-session socket (ADR 0001 §7 Session sockets), served by wsd for one session generation.

It is bound inside the generation's run directory (`/run/hz/s.sock` inside). It accepts only `hook_event`
and `ws_request`, each with the launch token, and refuses every other operation with the exact reply
`{"ok": false, "error": "forbidden"}`, which the self-test checks. Until plan 5 every hook event is
allowed and ws-request is unsupported (plan 4 D15).

Everything a session sends is untrusted. Accepted requests are spooled, capped, to the generation's
events file outside the sandbox. `TurnState` keeps only what the runtime reads: turn counts and the last
turn event (the lifetime stop's turn boundary, D13) and the first SessionStart's session ID if it is a
canonical UUID (the Codex thread ID, D8).
"""

import contextlib
import hmac
import json
import os
import re
import socket
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

MAX_LINE = 64 * 1024
CLIENT_SECONDS = 5.0
OK = {"ok": True}
FORBIDDEN = {"ok": False, "error": "forbidden"}
UNSUPPORTED = {"ok": False, "error": "unsupported"}
MALFORMED = {"ok": False, "error": "malformed"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TURN_EVENTS = ("UserPromptSubmit", "Stop")


@dataclass(frozen=True)
class TurnState:
    prompts: int = 0
    stops: int = 0
    last_event: str = ""
    thread_id: str | None = None

    @property
    def idle(self) -> bool:
        """At a turn boundary: the last turn event was a Stop."""
        return self.last_event == "Stop"


class SessionServer:
    def __init__(self, path: Path, token: str, events: Path, *, max_spool: int = 4 << 20) -> None:
        self.path = path
        self.token = token.encode()
        self.events = events
        self.max_spool = max_spool
        self.served = 0
        self._state = TurnState()
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

    def start(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        # The sandbox's user is wsd's own uid (OpenShell run_as_user); the run directory (0700) keeps
        # other host users out.
        self.path.chmod(0o777)
        sock.listen(16)
        self._sock = sock
        threading.Thread(target=self._accept, args=(sock,), name=f"session-{self.path.parent.name}",
                         daemon=True).start()

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def turns(self) -> TurnState:
        with self._lock:
            return self._state

    def _accept(self, sock: socket.socket) -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(CLIENT_SECONDS)
            data = b""
            try:
                while b"\n" not in data and len(data) <= MAX_LINE:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                line = data.split(b"\n", 1)[0]
                reply = MALFORMED if len(line) > MAX_LINE else self._handle(line)
                conn.sendall((json.dumps(reply) + "\n").encode())
            except OSError:
                return
            with self._lock:
                self.served += 1

    def _handle(self, line: bytes) -> dict[str, Any]:
        try:
            req: Any = json.loads(line)
        except ValueError:
            return MALFORMED
        if not isinstance(req, dict):
            return MALFORMED
        body = cast(dict[str, Any], req)
        token = body.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self.token):
            return FORBIDDEN
        kind = body.get("type")
        if kind not in ("hook_event", "ws_request"):
            return FORBIDDEN
        payload = body.get("payload")
        if not isinstance(payload, dict):
            return MALFORMED
        fields = cast(dict[str, Any], payload)
        self._spool(kind, fields)
        if kind == "ws_request":
            return UNSUPPORTED
        self._record(fields)
        return OK

    def _record(self, payload: dict[str, Any]) -> None:
        event = payload.get("hook_event_name")
        with self._lock:
            s = self._state
            if event == "SessionStart" and s.thread_id is None:
                sid = payload.get("session_id")
                if isinstance(sid, str) and UUID.fullmatch(sid):
                    s = replace(s, thread_id=sid)
            elif event == "UserPromptSubmit":
                s = replace(s, prompts=s.prompts + 1, last_event=event)
            elif event == "Stop":
                s = replace(s, stops=s.stops + 1, last_event=event)
            self._state = s

    def _spool(self, kind: str, payload: dict[str, Any]) -> None:
        line = (json.dumps({"type": kind, "payload": payload}) + "\n").encode()
        with self._lock:
            try:
                size = self.events.stat().st_size
            except FileNotFoundError:
                size = 0
            if size + len(line) > self.max_spool:
                return
            fd = os.open(self.events, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as fh:
                fh.write(line)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_session_server.py -q && timeout 300 uv run pyright src/heterodyne/session && timeout 300 uv run ruff check src tests`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/session/__init__.py src/heterodyne/session/server.py tests/test_session_server.py
git commit -m "plan4 T5: per-session socket (hook events and ws-request only, exact replies, turn state)"
```

### Task 6: The ws-hook and ws-request shims

**Files:**
- Create: `src/heterodyne/session/shim.py`
- Modify: `pyproject.toml` (`[project.scripts]`)
- Test: `tests/test_session_shim.py`

**Interfaces:**
- Consumes: Task 5's `SessionServer` (tests only) and its wire format.
- Produces:
  - `shim.run_hook(raw, env, *, clock, sleep) -> tuple[int, str]`: the exit status and stdout;
  - `shim.run_request(words, env, *, clock, sleep) -> tuple[int, str]`;
  - `shim.hook_main()`, `shim.request_main()`, `shim.main(argv)`;
  - the constants `DENY_REASON`, `TOKEN_FILE = "token"`, `CONFIG_FILE = "shim.json"`, `EX_TEMPFAIL = 75`;
  - `shim.REQUEST_WRAPPER`, the `/run/hz/ws-request` shell script text.
  - Console scripts `ws-hook` and `ws-request`.

This is the Python `ws-hook` of roadmap row 4 (the Rust one is phase 2). It must run inside the sandbox with only the image's `python3`, so it is standard library only and imports nothing from `heterodyne`. The runtime copies the file into the run directory, and each hook command is `python3 -I /run/hz/shim.py hook` (D5). The token and `shim.json` are found beside `$HZ_SESSION_SOCKET`, never in argv. `shim.json` holds `wait_seconds`, `local_classes` and `worktree`.

The §10 behaviour while wsd is down (D14):

- The shim retries the socket until `wait_seconds` have passed.
- With no answer, a PreToolUse is allowed (exit 0, no output) only when it is `worktree_edit`: `Edit`, `Write`, `MultiEdit` or `NotebookEdit`, on a path whose real path is inside the worktree, and only when `worktree_edit` is in `local_classes`. Otherwise it is denied through the JSON decision on stdout, with the reason `control plane unavailable; retry shortly`.
- Any other event, with no answer, is appended to `$HOME/.hz/spool.jsonl` (capped at 1 MiB) and exits 0.
- A refused reply (`forbidden`/`malformed`) denies a PreToolUse.
- A reply with `"decision": "deny"` (plan 5's format) is passed through as a deny with its reason.

- [ ] **Step 1: Write the failing tests**

`tests/test_session_shim.py`:

```python
import json
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from heterodyne.session import shim
from heterodyne.session.server import SessionServer

TOKEN = "fake-session-token"


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


@pytest.fixture
def run(tmp_path: Path) -> Path:
    run = tmp_path / "r1"
    run.mkdir()
    (run / "token").write_text(TOKEN)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (run / "shim.json").write_text(json.dumps({"wait_seconds": 5, "local_classes": ["worktree_edit"],
                                               "worktree": str(worktree)}))
    return run


@pytest.fixture
def live(run: Path) -> Iterator[SessionServer]:
    s = SessionServer(run / "s.sock", TOKEN, run.parent / "events.jsonl")
    s.start()
    yield s
    s.close()


def env_for(run: Path) -> dict[str, str]:
    return {"HZ_SESSION_SOCKET": str(run / "s.sock"), "HOME": str(run.parent / "home")}


def hook(run: Path, payload: dict[str, object], t: FakeTime | None = None) -> tuple[int, str]:
    t = t or FakeTime()
    return shim.run_hook(json.dumps(payload).encode(), env_for(run), clock=t.clock, sleep=t.sleep)


def denied(out: str) -> str:
    body = json.loads(out)["hookSpecificOutput"]
    assert body["hookEventName"] == "PreToolUse" and body["permissionDecision"] == "deny"
    return body["permissionDecisionReason"]


def test_answered_hook_is_allowed_silently(run: Path, live: SessionServer) -> None:
    assert hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}) == (0, "")
    assert hook(run, {"hook_event_name": "Stop"}) == (0, "")
    assert live.turns().stops == 1


def test_wrong_token_denies_a_tool_call(run: Path, live: SessionServer) -> None:
    (run / "token").write_text("wrong")
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    assert rc == 0 and "refused" in denied(out)


def test_wsd_down_waits_then_fails_closed(run: Path) -> None:
    t = FakeTime()
    bash = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    rc, out = hook(run, bash, t)
    assert rc == 0 and denied(out) == shim.DENY_REASON and t.now >= 5


def test_wsd_down_allows_a_worktree_edit_only(run: Path, tmp_path: Path) -> None:
    inside = str(tmp_path / "wt" / "src" / "a.py")
    outside = str(tmp_path / "elsewhere.py")
    escape = str(tmp_path / "wt" / ".." / "elsewhere.py")
    assert hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                      "tool_input": {"file_path": inside}}) == (0, "")
    for path in (outside, escape):
        write = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": path}}
        rc, out = hook(run, write)
        assert denied(out) == shim.DENY_REASON
    (tmp_path / "wt" / "link").symlink_to(tmp_path)
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                         "tool_input": {"file_path": str(tmp_path / "wt" / "link" / "x")}})
    assert denied(out) == shim.DENY_REASON


def test_worktree_edit_needs_its_class(run: Path, tmp_path: Path) -> None:
    cfg = json.loads((run / "shim.json").read_text())
    (run / "shim.json").write_text(json.dumps({**cfg, "local_classes": []}))
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                         "tool_input": {"file_path": str(tmp_path / "wt" / "a.py")}})
    assert denied(out) == shim.DENY_REASON


def test_wsd_down_spools_other_events(run: Path) -> None:
    assert hook(run, {"hook_event_name": "Stop", "last_assistant_message": "done"}) == (0, "")
    spool = run.parent / "home" / ".hz" / "spool.jsonl"
    assert json.loads(spool.read_text())["payload"]["hook_event_name"] == "Stop"


def test_a_missing_config_fails_closed(run: Path) -> None:
    (run / "shim.json").unlink()
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit"})
    assert denied(out) == shim.DENY_REASON


def test_plan5_deny_reply_is_passed_through(run: Path) -> None:
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(run / "s.sock"))
    srv.listen(1)

    def answer() -> None:
        conn, _ = srv.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b'{"ok": true, "decision": "deny", "reason": "needs approval"}\n')

    threading.Thread(target=answer, daemon=True).start()
    try:
        rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    finally:
        srv.close()
    assert denied(out) == "needs approval"


def test_request(run: Path, live: SessionServer) -> None:
    t = FakeTime()
    rc, out = shim.run_request(["please", "push"], env_for(run), clock=t.clock, sleep=t.sleep)
    assert (rc, json.loads(out)) == (1, {"ok": False, "error": "unsupported"})


def test_request_with_wsd_down(run: Path) -> None:
    t = FakeTime()
    rc, out = shim.run_request(["x"], env_for(run), clock=t.clock, sleep=t.sleep)
    assert (rc, out) == (shim.EX_TEMPFAIL, shim.DENY_REASON)


def test_the_file_runs_standalone_in_isolated_mode(run: Path, live: SessionServer) -> None:
    """As inside the sandbox: `python3 -I shim.py hook`, with no heterodyne on the path."""
    proc = subprocess.run([sys.executable, "-I", str(Path(shim.__file__)), "hook"], env=env_for(run),
                          input=b'{"hook_event_name": "Stop"}', capture_output=True, timeout=30, check=False)
    assert (proc.returncode, proc.stdout) == (0, b"")
    assert live.turns().stops == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_session_shim.py -q`
Expected: FAIL with `ImportError: cannot import name 'shim'`.

- [ ] **Step 3: Implement**

`src/heterodyne/session/shim.py`:

```python
"""ws-hook and ws-request (ADR 0001 §3.1, §10): the agent's side of the per-session socket.

Standard library only, with no heterodyne imports: inside the sandbox this file is run as
`python3 -I /run/hz/shim.py hook|request` from the read-only run directory. The socket is
$HZ_SESSION_SOCKET; the token and `shim.json` sit beside it, never in argv (plan 4 D5).

If wsd doesn't answer within `wait_seconds`, a PreToolUse is allowed only for a locally classifiable
`worktree_edit` (an edit tool on a path inside the worktree, while that class is auto-approved), and
denied otherwise; other events are spooled to $HOME/.hz/spool.jsonl, untrusted (plan 4 D14).
"""

import json
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

DENY_REASON = "control plane unavailable; retry shortly"
REFUSED_REASON = "the control plane refused this hook"
TOKEN_FILE = "token"
CONFIG_FILE = "shim.json"
EX_TEMPFAIL = 75
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SPOOL_CAP = 1 << 20
RETRY_SECONDS = 0.1
REQUEST_WRAPPER = '#!/bin/sh\nexec python3 -I /run/hz/shim.py request "$@"\n'

Clock = Callable[[], float]
Sleep = Callable[[float], None]


def _deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})


def _config(env: Mapping[str, str]) -> tuple[Path, str, dict[str, Any]] | None:
    sock = env.get("HZ_SESSION_SOCKET")
    if not sock:
        return None
    try:
        token = (Path(sock).parent / TOKEN_FILE).read_text().strip()
        cfg: Any = json.loads((Path(sock).parent / CONFIG_FILE).read_text())
    except (OSError, ValueError):
        return None
    return (Path(sock), token, cast(dict[str, Any], cfg)) if isinstance(cfg, dict) else None


def _ask(sock: Path, message: dict[str, Any], wait: float, clock: Clock,
         sleep: Sleep) -> dict[str, Any] | None:
    end = clock() + wait
    line = (json.dumps(message) + "\n").encode()
    while True:
        try:
            with socket.socket(socket.AF_UNIX) as c:
                c.settimeout(max(0.5, wait))
                c.connect(str(sock))
                c.sendall(line)
                reply: Any = json.loads(c.makefile("rb").readline())
            return cast(dict[str, Any], reply) if isinstance(reply, dict) else None
        except (OSError, ValueError):
            if clock() >= end:
                return None
            sleep(RETRY_SECONDS)


def _local_edit(payload: Mapping[str, Any], cfg: Mapping[str, Any]) -> bool:
    if payload.get("tool_name") not in EDIT_TOOLS or "worktree_edit" not in cfg.get("local_classes", []):
        return False
    worktree = cfg.get("worktree")
    tool_input = payload.get("tool_input")
    if not isinstance(worktree, str) or not isinstance(tool_input, dict):
        return False
    fields = cast(dict[str, Any], tool_input)
    target = fields.get("file_path", fields.get("notebook_path"))
    if not isinstance(target, str) or not target:
        return False
    root = os.path.realpath(worktree)
    real = os.path.realpath(os.path.join(root, target))
    return os.path.commonpath([real, root]) == root


def _spool(env: Mapping[str, str], payload: Any) -> None:
    home = env.get("HOME")
    if not home:
        return
    try:
        folder = Path(home) / ".hz"
        folder.mkdir(mode=0o700, exist_ok=True)
        spool = folder / "spool.jsonl"
        line = (json.dumps({"spooled": True, "payload": payload}) + "\n").encode()
        if (spool.stat().st_size if spool.exists() else 0) + len(line) <= SPOOL_CAP:
            with spool.open("ab") as fh:
                fh.write(line)
    except OSError:
        pass


def run_hook(raw: bytes, env: Mapping[str, str], *, clock: Clock = time.monotonic,
             sleep: Sleep = time.sleep) -> tuple[int, str]:
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        payload = {}
    fields = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
    tool_call = fields.get("hook_event_name") == "PreToolUse"
    found = _config(env)
    if found is None:
        return 0, _deny(DENY_REASON) if tool_call else ""
    sock, token, cfg = found
    wait = cfg.get("wait_seconds", 5)
    wait = float(wait) if isinstance(wait, int | float) and not isinstance(wait, bool) else 5.0
    reply = _ask(sock, {"token": token, "type": "hook_event", "payload": fields}, wait, clock, sleep)
    if reply is None:
        if tool_call:
            return 0, "" if _local_edit(fields, cfg) else _deny(DENY_REASON)
        _spool(env, fields)
        return 0, ""
    if not tool_call:
        return 0, ""
    if reply.get("ok") is not True:
        return 0, _deny(REFUSED_REASON)
    if reply.get("decision") == "deny":
        reason = reply.get("reason")
        return 0, _deny(reason if isinstance(reason, str) and reason else REFUSED_REASON)
    return 0, ""


def run_request(words: Sequence[str], env: Mapping[str, str], *, clock: Clock = time.monotonic,
                sleep: Sleep = time.sleep) -> tuple[int, str]:
    found = _config(env)
    if found is None:
        return EX_TEMPFAIL, DENY_REASON
    sock, token, cfg = found
    wait = cfg.get("wait_seconds", 5)
    wait = float(wait) if isinstance(wait, int | float) and not isinstance(wait, bool) else 5.0
    reply = _ask(sock, {"token": token, "type": "ws_request", "payload": {"text": " ".join(words)}},
                 wait, clock, sleep)
    if reply is None:
        return EX_TEMPFAIL, DENY_REASON
    return (0 if reply.get("ok") is True else 1), json.dumps(reply)


def hook_main() -> int:
    rc, out = run_hook(sys.stdin.buffer.read(), os.environ)
    if out:
        print(out)
    return rc


def request_main(argv: Sequence[str] | None = None) -> int:
    rc, out = run_request(list(sys.argv[1:] if argv is None else argv), os.environ)
    print(out)
    return rc


def main(argv: Sequence[str]) -> int:
    if argv[:1] == ["hook"]:
        return hook_main()
    if argv[:1] == ["request"]:
        return request_main(argv[1:])
    print("usage: shim.py hook|request [text...]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

In `pyproject.toml`, add to `[project.scripts]`:

```toml
ws-hook = "heterodyne.session.shim:hook_main"
ws-request = "heterodyne.session.shim:request_main"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 300 uv sync && timeout 300 uv run pytest tests/test_session_shim.py tests/test_session_server.py -q && timeout 300 uv run pyright src/heterodyne/session && timeout 300 uv run ruff check src tests`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
git add src/heterodyne/session/shim.py pyproject.toml uv.lock tests/test_session_shim.py
git commit -m "plan4 T6: ws-hook and ws-request shims (stdlib, fail closed when wsd is down)"
```

(`uv.lock` is only added if `uv sync` changed it.)

### Task 7: The adapters, the synthetic home and Codex hook trust

**Files:**
- Create: `src/heterodyne/agents/base.py`, `src/heterodyne/agents/codex.py`, `src/heterodyne/agents/registry.py`, `src/heterodyne/sandbox/resources/codex_trust.py`, `tests/fakes/fake_agent_cli.py`
- Modify: `src/heterodyne/agents/claude_code.py` (append the `ClaudeCode` class; `interactive_argv` and `headless_argv` stay as they are, admind uses them), `pyproject.toml` (ruff per-file ignores for the resources), `tests/sandbox_env.py` (append `short_dir`, `install_fake_cli`, `fake_jwt`, `fresh_logins`)
- Test: `tests/test_agents_adapters.py`

**Interfaces:**
- Consumes: `spec.AgentFacts`, `spec.Bind`, `spec.SessionLayout`, `spec.RUN_INSIDE`, `spec.BRIDGE_INSIDE`; `session.server.UUID`; `claude_code.interactive_argv`.
- Produces:
  - `base.HOOK_COMMAND = "python3 -I /run/hz/shim.py hook"`, `base.HOOK_EVENTS`;
  - `base.AdapterError(Exception)`, fixed wording;
  - `base.Cli(binary: Path, root: Path, version: str)`;
  - `base.Adapter` (Protocol), with the attributes `name`, `config_var`, `config_subdir`, `login_names`, `model_hosts`, `version_pin`, `tool_env`, `prompt_marker`, `assigns_id` and the methods:
    - `locate(binary: str, path: str) -> Cli`;
    - `facts(layout, cli, uid) -> AgentFacts`;
    - `prepare_home(layout, worktree) -> None`;
    - `run_files() -> Mapping[str, str]` (file name → text, written into each run directory: Claude's hook settings, Codex's `codex_trust.py`);
    - `has_state(home, native_id: str | None) -> bool` and `any_state(home) -> bool`;
    - `access_expiry(login_files) -> float` (epoch seconds);
    - `server_argv(cli) -> list[str] | None` and `server_ready(layout) -> bool`;
    - `tui_argv(cli, profile, *, native_id, resume, label) -> list[str]`;
    - `trust_argv(cli, worktree) -> list[str] | None` and `apply_trust(layout, output: bytes) -> int`;
  - `claude_code.ClaudeCode`, `codex.Codex`, `registry.ADAPTERS: Mapping[str, Adapter]`;
  - in `tests/sandbox_env.py`: `short_dir()` (a context manager yielding a short scratch directory), `install_fake_cli(root, adapter) -> Path`, `fake_jwt(exp) -> str` and `fresh_logins(home, expires_at)`.

Everything specific to one CLI lives here (Global Constraints). The rules each adapter follows:

- **Version** is read without running the CLI (S5): Claude's `package.json` at its install root, Codex's release directory name `<version>-<target>`. The install root is the binary's grandparent when the binary sits in `bin/`, else its parent.
- **Synthetic home** (§7 home isolation). The config directory is `<home>/.claude` or `<home>/.codex`, named by `CLAUDE_CONFIG_DIR` or `CODEX_HOME` (D4). The login file is bound into it read-only by the spec, never copied.
  - Claude: `settings.json` holds only `skipDangerousModePermissionPrompt`. `.claude.json` is merged, not replaced (Claude keeps its own state there), with onboarding done, bypass mode accepted and the worktree trusted. The hooks are in `/run/hz/claude-settings.json`, in the read-only run directory, passed with `--settings`.
  - Codex: `config.toml` is rewritten on every launch (`approval_policy = "never"`, `sandbox_mode = "danger-full-access"`, the worktree trusted, `[features] apps = false`), and so is `hooks.json`. Stale sockets in the bridge directory are removed, so a dead generation's socket can't pass for the new app-server's.
- **Hooks**: `SessionStart`, `UserPromptSubmit`, `Stop` and `PreToolUse`, each with the one fixed command (D5).
- **State** (D9): Claude's `<config>/projects/*/<id>.jsonl`, Codex's `<config>/sessions/**/rollout-*<id>.jsonl`. A native ID that is not a canonical UUID is refused before it reaches a glob.
- **Codex trust** (D6): `codex_trust.py` lists what is untrusted or modified. `apply_trust` accepts only entries whose key is under this session's own `hooks.json` and contains no quote, backslash or control character, and whose hash is hex (optionally `sha256:`-prefixed). Anything else refuses the launch.

- [ ] **Step 1: Write the fake CLI and the rig helpers**

`tests/fakes/fake_agent_cli.py`:

```python
"""A fake `claude` or `codex` for the sandbox tests, chosen by its wrapper's first argument (`--as=claude`
or `--as=codex`; install_fake_cli writes the wrapper).

The sandbox tests run it on the host through FakeBackend, which exports HZ_FAKE_PATHS (inside path ->
host path) so the hook commands' /run/hz paths can be translated here. Modes:
- `codex app-server` (stdio): answers initialize and hooks/list from $CODEX_HOME's hooks.json and
  config.toml. A hook's hash is "sha256:" + the first 16 hex digits of sha256(command).
- `codex app-server --listen unix://PATH`: binds PATH and stays up until PATH is removed.
- the TUI: prints its prompt marker, fires SessionStart (Claude at start, Codex on the first prompt, as
  the real CLIs do), and for each line typed fires UserPromptSubmit and Stop, keeping a transcript
  (Claude) or a rollout (Codex) where the real CLI keeps it, so resume can find it.
"""

import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any

ADAPTER = sys.argv[1].removeprefix("--as=")
ARGS = sys.argv[2:]
PATHS: dict[str, str] = json.loads(os.environ.get("HZ_FAKE_PATHS", "{}"))


def host(text: str) -> str:
    for inside in sorted(PATHS, key=len, reverse=True):
        text = text.replace(inside, PATHS[inside])
    return text


def snake(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def codex_hooks() -> list[tuple[str, str, str]]:
    """(key, event, command) for each hook in $CODEX_HOME/hooks.json."""
    home = Path(os.environ["CODEX_HOME"])
    hooks: Any = json.loads((home / "hooks.json").read_text())["hooks"]
    return [(f"{home / 'hooks.json'}:{snake(event)}:{i}:{j}", event, hook["command"])
            for event, groups in hooks.items() for i, group in enumerate(groups)
            for j, hook in enumerate(group["hooks"])]


def digest(command: str) -> str:
    return "sha256:" + hashlib.sha256(command.encode()).hexdigest()[:16]


def stdio_server() -> None:
    state: Any = tomllib.loads((Path(os.environ["CODEX_HOME"]) / "config.toml").read_text())
    trusted = state.get("hooks", {}).get("state", {})
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            result: Any = {"userAgent": "fake"}
        elif msg.get("method") == "hooks/list":
            listed = []
            for key, event, command in codex_hooks():
                have = trusted.get(key, {}).get("trusted_hash")
                status = "trusted" if have == digest(command) else "modified" if have else "untrusted"
                listed.append({"key": key, "eventName": event, "trustStatus": status,
                               "currentHash": digest(command), "source": "user"})
            result = {"data": [{"cwd": msg["params"]["cwds"][0], "hooks": listed, "errors": []}]}
        else:
            continue                    # a notification
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)


def listen_server(url: str) -> None:
    path = Path(url.removeprefix("unix://"))
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(4)
    srv.settimeout(0.2)
    while path.exists():
        with contextlib.suppress(TimeoutError, OSError):
            srv.accept()[0].close()
    srv.close()


def fire(commands: list[str], event: str, session: str | None, **extra: object) -> None:
    payload = json.dumps({"hook_event_name": event, "session_id": session, **extra}).encode()
    for command in commands:
        subprocess.run(host(command), shell=True, input=payload, check=False)  # noqa: S602


def tui() -> None:
    if ADAPTER == "claude":
        settings: Any = json.loads(Path(host(ARGS[ARGS.index("--settings") + 1])).read_text())
        commands = {e: [h["command"] for g in gs for h in g["hooks"]] for e, gs in settings["hooks"].items()}
        session: str | None = ARGS[ARGS.index("--resume" if "--resume" in ARGS else "--session-id") + 1]
        slug = re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
        log = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / slug / f"{session}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.touch()
        fire(commands.get("SessionStart", []), "SessionStart", session, source="startup")
    else:
        commands = {}
        for _key, event, command in codex_hooks():
            commands.setdefault(event, []).append(command)
        session = ARGS[ARGS.index("resume") + 1] if "resume" in ARGS else None
        log = None
    marker = "❯" if ADAPTER == "claude" else "›"
    print(f"{marker} ", end="", flush=True)
    started = False
    for raw in sys.stdin:
        line = raw.strip().lstrip("\x1b")
        if ADAPTER == "codex" and not started:
            session = session or str(uuid.uuid4())
            day = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "10" / "09"
            day.mkdir(parents=True, exist_ok=True)
            log = day / f"rollout-2026-10-09T00-00-00-{session}.jsonl"
            log.touch()
            fire(commands.get("SessionStart", []), "SessionStart", session, source="startup")
            started = True
        fire(commands.get("UserPromptSubmit", []), "UserPromptSubmit", session, prompt=line)
        if log is not None:
            with log.open("a") as fh:
                fh.write(json.dumps({"user": line}) + "\n")
        fire(commands.get("Stop", []), "Stop", session, last_assistant_message=f"echo: {line}")
        print(f"echo: {line}\n{marker} ", end="", flush=True)


if ARGS[:1] == ["app-server"]:
    if "--listen" in ARGS:
        listen_server(ARGS[ARGS.index("--listen") + 1])
    else:
        stdio_server()
else:
    tui()
```

Append to `tests/sandbox_env.py` (add `import base64`, `import contextlib`, `import json`, `import shutil`, `import sys`, `import tempfile` and `from collections.abc import Iterator` to its imports):

```python
@contextlib.contextmanager
def short_dir() -> Iterator[Path]:
    """A scratch directory with a short path. Unix socket paths must fit sun_path (108 bytes), and
    pytest's tmp_path is often too deep for a session layout's sockets."""
    root = Path(tempfile.mkdtemp(prefix="hz", dir=tempfile.gettempdir()))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


FAKE_CLI = Path(__file__).resolve().parent / "fakes" / "fake_agent_cli.py"
PINS = {"claude-code": "2.1.286", "codex": "0.160.0"}


def install_fake_cli(root: Path, adapter: str) -> Path:
    """A fake CLI install laid out like the real one, at its pinned version. Returns the binary."""
    if adapter == "claude-code":
        top = root / "cli" / "claude"
        binary = top / "bin" / "claude"
        binary.parent.mkdir(parents=True, exist_ok=True)
        (top / "package.json").write_text(json.dumps({"version": PINS[adapter]}))
    else:
        binary = root / "cli" / "codex" / "releases" / f"{PINS[adapter]}-x86_64-fake" / "bin" / "codex"
        binary.parent.mkdir(parents=True, exist_ok=True)
    name = "claude" if adapter == "claude-code" else "codex"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" --as={name} "$@"\n')
    binary.chmod(0o755)
    return binary


def fake_jwt(exp: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"x.{body}.x"


def fresh_logins(home: Path, expires_at: int) -> None:
    """Fake default logins that expire at `expires_at` (epoch seconds). Obvious fakes, no credential."""
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".codex").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"expiresAt": expires_at * 1000, "accessToken": "fake-not-a-token"}}))
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": fake_jwt(expires_at)}}))
```

The wrapper names the test's Python and the fake by absolute path at run time; nothing absolute is committed.

- [ ] **Step 2: Write the failing tests**

`tests/test_agents_adapters.py`:

```python
import json
import socket
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import fake_jwt, install_fake_cli, short_dir

from heterodyne.agents.base import HOOK_COMMAND, HOOK_EVENTS, AdapterError
from heterodyne.agents.claude_code import SETTINGS_FILE, ClaudeCode
from heterodyne.agents.codex import APP_SOCKET, Codex
from heterodyne.agents.registry import ADAPTERS
from heterodyne.sandbox.spec import BRIDGE_INSIDE, RUN_INSIDE, SessionLayout

ID = "0b1d8c5e-3f7a-4c2d-9e6b-7a8f9c0d1e2f"
RESOURCES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources"
TRUST = RESOURCES / "codex_trust.py"


@pytest.fixture
def layout() -> Iterator[SessionLayout]:
    with short_dir() as root:                   # the bridge holds a socket: keep its path short
        layout = SessionLayout(root / "hz0123456789ab")
        layout.home.mkdir(parents=True)
        yield layout


def test_registry_names_both_adapters() -> None:
    assert set(ADAPTERS) == {"claude-code", "codex"}
    assert all(ADAPTERS[name].name == name for name in ADAPTERS)
    assert (ADAPTERS["claude-code"].assigns_id, ADAPTERS["codex"].assigns_id) == (False, True)


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_locate_reads_the_version_without_running_the_cli(tmp_path: Path, adapter: str) -> None:
    binary = install_fake_cli(tmp_path, adapter)
    cli = ADAPTERS[adapter].locate(binary.name, str(binary.parent))
    pin = ADAPTERS[adapter].version_pin
    assert (cli.binary, cli.root, cli.version) == (binary, binary.parent.parent, pin)


def test_locate_refuses_a_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="not found"):
        ClaudeCode().locate("claude", str(tmp_path))


def test_claude_home_merges_its_state_and_hooks_are_read_only(layout: SessionLayout, tmp_path: Path) -> None:
    conf = layout.home / ".claude"
    conf.mkdir()
    (conf / ".claude.json").write_text(json.dumps({"numStartups": 7, "projects": {"/elsewhere": {}}}))
    ClaudeCode().prepare_home(layout, tmp_path / "wt")
    state = json.loads((conf / ".claude.json").read_text())
    assert state["numStartups"] == 7 and state["hasCompletedOnboarding"] is True
    assert state["projects"][str(tmp_path / "wt")]["hasTrustDialogAccepted"] is True
    assert json.loads((conf / "settings.json").read_text()) == {"skipDangerousModePermissionPrompt": True}
    hooks = json.loads(ClaudeCode().run_files()[SETTINGS_FILE])["hooks"]
    assert set(hooks) == set(HOOK_EVENTS)
    assert {h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]} == {HOOK_COMMAND}


def test_codex_home_is_rewritten_and_stale_sockets_go(layout: SessionLayout, tmp_path: Path) -> None:
    Codex().prepare_home(layout, tmp_path / "wt")
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(layout.bridge / "app.sock"))
    stale.close()
    (layout.home / ".codex" / "config.toml").write_text("model = 'x'\n")
    Codex().prepare_home(layout, tmp_path / "wt")
    config = tomllib.loads((layout.home / ".codex" / "config.toml").read_text())
    assert config == {"approval_policy": "never", "sandbox_mode": "danger-full-access",
                      "projects": {str(tmp_path / "wt"): {"trust_level": "trusted"}},
                      "features": {"apps": False}}
    assert not (layout.bridge / "app.sock").exists()
    assert layout.daemon.stat().st_mode & 0o777 == 0o700
    hooks = json.loads((layout.home / ".codex" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == set(HOOK_EVENTS)


def test_codex_facts_bind_the_bridge_and_daemon(layout: SessionLayout, tmp_path: Path) -> None:
    binary = install_fake_cli(tmp_path, "codex")
    facts = Codex().facts(layout, Codex().locate("codex", str(binary.parent)), 1234)
    assert [(b.source, str(b.target), b.read_only) for b in facts.binds] == [
        (layout.bridge, str(BRIDGE_INSIDE), False),
        (layout.daemon, "/tmp/codex-daemon-1234", False)]  # noqa: S108
    assert facts.config_var == "CODEX_HOME" and facts.model_hosts == ("chatgpt.com",)


def test_state_is_found_where_each_cli_keeps_it(layout: SessionLayout) -> None:
    for adapter in (ClaudeCode(), Codex()):
        assert not adapter.has_state(layout.home, ID) and not adapter.any_state(layout.home)
    (layout.home / ".claude" / "projects" / "-w").mkdir(parents=True)
    (layout.home / ".claude" / "projects" / "-w" / f"{ID}.jsonl").write_text("")
    rollouts = layout.home / ".codex" / "sessions" / "2026" / "10" / "09"
    rollouts.mkdir(parents=True)
    (rollouts / f"rollout-2026-10-09T00-00-00-{ID}.jsonl").write_text("")
    for adapter in (ClaudeCode(), Codex()):
        assert adapter.has_state(layout.home, ID) and adapter.any_state(layout.home)
        assert not adapter.has_state(layout.home, None)
        with pytest.raises(AdapterError, match="native ID"):
            adapter.has_state(layout.home, "*")


def test_access_expiry(tmp_path: Path) -> None:
    claude, codex = tmp_path / ".credentials.json", tmp_path / "auth.json"
    claude.write_text(json.dumps({"claudeAiOauth": {"expiresAt": 1_900_000_000_000}}))
    codex.write_text(json.dumps({"tokens": {"access_token": fake_jwt(1_900_000_000)}}))
    assert ClaudeCode().access_expiry([claude]) == 1_900_000_000
    assert Codex().access_expiry([codex]) == 1_900_000_000
    for adapter, path in ((ClaudeCode(), claude), (Codex(), codex)):
        path.write_text("{}")
        with pytest.raises(AdapterError, match="expiry"):
            adapter.access_expiry([path])


def test_tui_argv(tmp_path: Path) -> None:
    claude = ClaudeCode().locate("claude", str(install_fake_cli(tmp_path, "claude-code").parent))
    codex = Codex().locate("codex", str(install_fake_cli(tmp_path, "codex").parent))
    first = ClaudeCode().tui_argv(claude, {"model": "m-1"}, native_id=ID, resume=False, label="bd-1 · coder")
    assert first[1:3] == ["--session-id", ID] and first[-2:] == ["--name", "bd-1 · coder"]
    assert str(RUN_INSIDE / SETTINGS_FILE) in first
    assert ClaudeCode().tui_argv(claude, {}, native_id=ID, resume=True, label="x")[1:3] == ["--resume", ID]
    assert Codex().tui_argv(codex, {}, native_id=None, resume=False, label="x") == [
        str(codex.binary), "--remote", APP_SOCKET, "--dangerously-bypass-approvals-and-sandbox"]
    assert Codex().tui_argv(codex, {}, native_id=ID, resume=True, label="x")[1:3] == ["resume", ID]
    with pytest.raises(AdapterError):
        Codex().tui_argv(codex, {}, native_id=None, resume=True, label="x")
    with pytest.raises(AdapterError):
        ClaudeCode().tui_argv(claude, {}, native_id=None, resume=False, label="x")


def trust_listing(layout: SessionLayout, codex: Path, worktree: Path) -> bytes:
    env = {"CODEX_HOME": str(layout.home / ".codex"), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, "-I", str(TRUST), str(codex), str(worktree)], env=env,
                          capture_output=True, timeout=60, check=True)
    return proc.stdout


def test_codex_run_files_ship_the_trust_lister() -> None:
    assert Codex().run_files()["codex_trust.py"] == TRUST.read_text()


def test_codex_trust_round_trip(layout: SessionLayout, tmp_path: Path) -> None:
    codex = install_fake_cli(tmp_path, "codex")
    Codex().prepare_home(layout, tmp_path)
    listed = trust_listing(layout, codex, tmp_path)
    assert len(json.loads(listed)) == len(HOOK_EVENTS)
    assert Codex().apply_trust(layout, listed) == len(HOOK_EVENTS)
    assert json.loads(trust_listing(layout, codex, tmp_path)) == []
    Codex().prepare_home(layout, tmp_path)            # a new launch rewrites config.toml: trust again
    assert len(json.loads(trust_listing(layout, codex, tmp_path))) == len(HOOK_EVENTS)


@pytest.mark.parametrize("entry", [
    {"key": "elsewhere/hooks.json:stop:0:0", "hash": "0123456789abcdef"},
    {"key": "HOOKS:stop:0:0\"]\nevil = 1", "hash": "0123456789abcdef"},
    {"key": "HOOKS:stop:0:0", "hash": "not-hex"},
    {"key": "HOOKS:stop:0:0"},
    "a string",
])
def test_apply_trust_refuses_anything_unexpected(layout: SessionLayout, tmp_path: Path,
                                                 entry: object) -> None:
    Codex().prepare_home(layout, tmp_path)
    hooks = str(layout.home / ".codex" / "hooks.json")
    text = json.dumps([entry]).replace("HOOKS", hooks.replace("\\", "\\\\"))
    before = (layout.home / ".codex" / "config.toml").read_text()
    with pytest.raises(AdapterError, match="trust"):
        Codex().apply_trust(layout, text.encode())
    assert (layout.home / ".codex" / "config.toml").read_text() == before
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_agents_adapters.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.agents.base'`.

- [ ] **Step 4: Write `base.py`**

`src/heterodyne/agents/base.py`:

```python
"""The adapter contract of the sandbox runtime (ADR 0001 §4.2, §7). An adapter says how its CLI is found
and pinned, what its synthetic home holds, where it keeps resumable state, how its login's expiry is read,
and how it is started in its managed shape. Nothing outside `heterodyne.agents` names a CLI."""

import base64
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from heterodyne.sandbox.spec import AgentFacts, SessionLayout
from heterodyne.session.server import UUID

# One fixed hook command for every launch (plan 4 D5): Codex's trusted hash stays valid, and the
# session token is read from the file beside the socket, never from a command line.
HOOK_COMMAND = "python3 -I /run/hz/shim.py hook"
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "PreToolUse")


class AdapterError(Exception):
    """The adapter can't do this for the launch. Fixed wording, no paths or secrets: LaunchFailed."""


@dataclass(frozen=True)
class Cli:
    binary: Path        # canonical
    root: Path          # the install root, bound read-only
    version: str


def find_binary(binary: str, path: str) -> Path:
    found = binary if os.sep in binary else shutil.which(binary, path=path)
    if not found:
        raise AdapterError("the CLI binary was not found")
    real = Path(os.path.realpath(found))
    if not real.is_file() or not os.access(real, os.X_OK):
        raise AdapterError("the CLI binary was not found")
    return real


def install_root(binary: Path) -> Path:
    return binary.parent.parent if binary.parent.name == "bin" else binary.parent


def hook_group(matcher: str | None = None) -> dict[str, Any]:
    group: dict[str, Any] = {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    return group if matcher is None else {"matcher": matcher, **group}


def write_private(path: Path, text: str) -> None:
    """Write `text` atomically, mode 0600."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    tmp.replace(path)


def checked_id(native_id: str | None) -> str | None:
    if native_id is not None and not UUID.fullmatch(native_id):
        raise AdapterError("the native ID is not a canonical UUID")
    return native_id


def read_json(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def jwt_exp(token: object) -> float:
    if not isinstance(token, str) or token.count(".") != 2:
        raise AdapterError("the login's expiry can't be read")
    body = token.split(".")[1]
    try:
        claims: Any = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        raise AdapterError("the login's expiry can't be read") from None
    exp = cast(dict[str, Any], claims).get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, int | float) or isinstance(exp, bool):
        raise AdapterError("the login's expiry can't be read")
    return float(exp)


class Adapter(Protocol):
    name: str
    config_var: str
    config_subdir: str
    login_names: tuple[str, ...]
    model_hosts: tuple[str, ...]
    version_pin: str
    tool_env: frozenset[str]          # what the pinned CLI adds to its tools' environment (S5 item 11)
    prompt_marker: str                # its ready prompt, as tmux captures it
    assigns_id: bool                  # the CLI assigns the native ID itself (Codex's thread), else wsd does

    def locate(self, binary: str, path: str) -> Cli: ...

    def facts(self, layout: SessionLayout, cli: Cli, uid: int) -> AgentFacts: ...

    def prepare_home(self, layout: SessionLayout, worktree: Path) -> None: ...

    def run_files(self) -> Mapping[str, str]: ...

    def has_state(self, home: Path, native_id: str | None) -> bool:
        """The synthetic home holds resumable state for this native ID. Raises AdapterError for an ID
        that is not a canonical UUID, and OSError if the home can't be read."""
        ...

    def any_state(self, home: Path) -> bool:
        """The synthetic home holds resumable state for any session at all."""
        ...

    def access_expiry(self, login_files: Sequence[Path]) -> float: ...

    def server_argv(self, cli: Cli) -> list[str] | None: ...

    def server_ready(self, layout: SessionLayout) -> bool: ...

    def tui_argv(self, cli: Cli, profile: Mapping[str, Any], *, native_id: str | None, resume: bool,
                 label: str) -> list[str]: ...

    def trust_argv(self, cli: Cli, worktree: Path) -> list[str] | None: ...

    def apply_trust(self, layout: SessionLayout, output: bytes) -> int:
        """Trust what `trust_argv`'s output lists; return how many entries it listed."""
        ...


def profile_args(profile: Mapping[str, Any]) -> list[str]:
    args = profile.get("args", [])
    return [str(a) for a in cast(list[Any], args)] if isinstance(args, list) else []
```

- [ ] **Step 5: Append `ClaudeCode` to `claude_code.py`**

Extend the imports of `src/heterodyne/agents/claude_code.py` to:

```python
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from heterodyne.agents.base import (
    HOOK_EVENTS,
    AdapterError,
    Cli,
    checked_id,
    find_binary,
    hook_group,
    install_root,
    read_json,
    write_private,
)
from heterodyne.sandbox.spec import RUN_INSIDE, AgentFacts, SessionLayout
```

and append:

```python
SETTINGS_FILE = "claude-settings.json"          # in the run directory: read-only inside


class ClaudeCode:
    name = "claude-code"
    config_var = "CLAUDE_CONFIG_DIR"
    config_subdir = ".claude"
    login_names = (".credentials.json",)
    model_hosts = ("api.anthropic.com",)
    version_pin = "2.1.286"
    tool_env = frozenset({
        "AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_EFFORT", "CLAUDE_PID", "COREPACK_ENABLE_AUTO_PIN", "GIT_EDITOR",
        "NoDefaultCurrentDirectoryInExePath"})
    prompt_marker = "❯"
    assigns_id = False

    def locate(self, binary: str, path: str) -> Cli:
        real = find_binary(binary, path)
        root = install_root(real)
        version = read_json(root / "package.json").get("version")
        if not isinstance(version, str) or not version:
            raise AdapterError("the claude-code CLI version can't be read")
        return Cli(real, root, version)

    def facts(self, layout: SessionLayout, cli: Cli, uid: int) -> AgentFacts:
        return AgentFacts(cli_root=cli.root, cli_binary=cli.binary, config_var=self.config_var,
                          config_dir=layout.home / self.config_subdir, login_names=self.login_names,
                          model_hosts=self.model_hosts, extra_env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"})

    def prepare_home(self, layout: SessionLayout, worktree: Path) -> None:
        conf = layout.home / self.config_subdir
        conf.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(conf / "settings.json", json.dumps({"skipDangerousModePermissionPrompt": True}))
        state = read_json(conf / ".claude.json")
        projects: Any = state.get("projects")
        projects = cast(dict[str, Any], projects) if isinstance(projects, dict) else {}
        project: Any = projects.get(str(worktree))
        project = cast(dict[str, Any], project) if isinstance(project, dict) else {}
        projects[str(worktree)] = {**project, "hasTrustDialogAccepted": True,
                                   "hasCompletedProjectOnboarding": True}
        state.update(hasCompletedOnboarding=True, bypassPermissionsModeAccepted=True, projects=projects)
        state.setdefault("theme", "dark")
        write_private(conf / ".claude.json", json.dumps(state))

    def run_files(self) -> Mapping[str, str]:
        hooks = {e: [hook_group("*" if e == "PreToolUse" else None)] for e in HOOK_EVENTS}
        return {SETTINGS_FILE: json.dumps({"hooks": hooks})}

    def _projects(self, home: Path) -> Path:
        return home / self.config_subdir / "projects"

    def has_state(self, home: Path, native_id: str | None) -> bool:
        found = checked_id(native_id)
        projects = self._projects(home)
        return found is not None and projects.is_dir() and any(projects.glob(f"*/{found}.jsonl"))

    def any_state(self, home: Path) -> bool:
        projects = self._projects(home)
        return projects.is_dir() and any(projects.glob("*/*.jsonl"))

    def access_expiry(self, login_files: Sequence[Path]) -> float:
        oauth: Any = read_json(login_files[0]).get("claudeAiOauth")
        expires: Any = cast(dict[str, Any], oauth).get("expiresAt") if isinstance(oauth, dict) else None
        if not isinstance(expires, int | float) or isinstance(expires, bool):
            raise AdapterError("the login's expiry can't be read")
        return expires / 1000

    def server_argv(self, cli: Cli) -> list[str] | None:
        return None

    def server_ready(self, layout: SessionLayout) -> bool:
        return True

    def tui_argv(self, cli: Cli, profile: Mapping[str, Any], *, native_id: str | None, resume: bool,
                 label: str) -> list[str]:
        session = checked_id(native_id)
        if session is None:
            raise AdapterError("a claude-code launch needs its native ID")
        return interactive_argv(str(cli.binary), profile, session_id=session, resume=resume,
                                settings_file=RUN_INSIDE / SETTINGS_FILE, name=label)

    def trust_argv(self, cli: Cli, worktree: Path) -> list[str] | None:
        return None

    def apply_trust(self, layout: SessionLayout, output: bytes) -> int:
        return 0
```

- [ ] **Step 6: Write `codex.py`, `registry.py` and `codex_trust.py`**

`src/heterodyne/agents/codex.py`:

```python
"""The `codex` adapter in its managed shape (ADR 0001 §4.2; spikes S5 and S8; codex-cli 0.160.0): a
per-session `codex app-server` inside the sandbox, and the TUI attached to it with `--remote`. The
thread ID is assigned by Codex and reported by the first SessionStart hook (plan 4 D8)."""

import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from heterodyne.agents.base import (
    HOOK_EVENTS,
    AdapterError,
    Cli,
    checked_id,
    find_binary,
    hook_group,
    install_root,
    jwt_exp,
    profile_args,
    read_json,
    write_private,
)
from heterodyne.sandbox.spec import BRIDGE_INSIDE, RUN_INSIDE, AgentFacts, Bind, SessionLayout

APP_SOCKET = f"unix://{BRIDGE_INSIDE}/app.sock"
HOOKS_FILE = "hooks.json"
TRUST_SCRIPT = RUN_INSIDE / "codex_trust.py"
TRUST_SOURCE = Path(__file__).resolve().parents[1] / "sandbox" / "resources" / "codex_trust.py"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
KEY_TEXT = re.compile(r'[^"\\\x00-\x1f\x7f]{1,400}')
HASH = re.compile(r"(sha256:)?[0-9a-f]{16,64}")


def _socket(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(os.lstat(path).st_mode)
    except OSError:
        return False


class Codex:
    name = "codex"
    config_var = "CODEX_HOME"
    config_subdir = ".codex"
    login_names = ("auth.json",)
    model_hosts = ("chatgpt.com",)
    version_pin = "0.160.0"
    tool_env = frozenset({"CODEX_CI", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_VERSION", "COLORTERM",
                          "GH_PAGER", "GIT_PAGER", "LC_ALL", "LC_CTYPE", "NO_COLOR", "PAGER"})
    prompt_marker = "›"
    assigns_id = True

    def locate(self, binary: str, path: str) -> Cli:
        real = find_binary(binary, path)
        root = install_root(real)
        version = root.name.split("-")[0]           # .../releases/<version>-<target>
        if not VERSION.fullmatch(version):
            raise AdapterError("the codex CLI version can't be read")
        return Cli(real, root, version)

    def facts(self, layout: SessionLayout, cli: Cli, uid: int) -> AgentFacts:
        # codex 0.160 binds the real app-server socket in /tmp/codex-daemon-<uid>/ and leaves only a
        # symlink at the --listen path, so that directory is bound from the session too (S5).
        return AgentFacts(cli_root=cli.root, cli_binary=cli.binary, config_var=self.config_var,
                          config_dir=layout.home / self.config_subdir, login_names=self.login_names,
                          model_hosts=self.model_hosts,
                          binds=(Bind(layout.bridge, BRIDGE_INSIDE, False),
                                 Bind(layout.daemon, Path(f"/tmp/codex-daemon-{uid}"), False)),  # noqa: S108
                          read_write=(str(BRIDGE_INSIDE),))

    def prepare_home(self, layout: SessionLayout, worktree: Path) -> None:
        conf = layout.home / self.config_subdir
        conf.mkdir(mode=0o700, parents=True, exist_ok=True)
        for folder in (layout.bridge, layout.daemon):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.chmod(0o700)                # codex refuses a socket directory that is not 0700
            for entry in folder.iterdir():
                if _socket(entry) or entry.is_symlink():
                    entry.unlink()
        # JSON string escapes are valid TOML basic-string escapes.
        write_private(conf / "config.toml",
                      'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n'
                      f"[projects.{json.dumps(str(worktree))}]\ntrust_level = \"trusted\"\n"
                      "[features]\napps = false\n")
        hooks = {e: [hook_group()] for e in HOOK_EVENTS}
        write_private(conf / HOOKS_FILE, json.dumps({"hooks": hooks}))

    def run_files(self) -> Mapping[str, str]:
        return {TRUST_SCRIPT.name: TRUST_SOURCE.read_text()}

    def _sessions(self, home: Path) -> Path:
        return home / self.config_subdir / "sessions"

    def has_state(self, home: Path, native_id: str | None) -> bool:
        found = checked_id(native_id)
        sessions = self._sessions(home)
        return found is not None and sessions.is_dir() and any(sessions.rglob(f"rollout-*{found}.jsonl"))

    def any_state(self, home: Path) -> bool:
        sessions = self._sessions(home)
        return sessions.is_dir() and any(sessions.rglob("rollout-*.jsonl"))

    def access_expiry(self, login_files: Sequence[Path]) -> float:
        tokens: Any = read_json(login_files[0]).get("tokens")
        return jwt_exp(cast(dict[str, Any], tokens).get("access_token") if isinstance(tokens, dict) else None)

    def server_argv(self, cli: Cli) -> list[str] | None:
        return [str(cli.binary), "app-server", "--listen", APP_SOCKET]

    def server_ready(self, layout: SessionLayout) -> bool:
        if _socket(layout.bridge / "app.sock"):
            return True
        return layout.daemon.is_dir() and any(_socket(p) for p in layout.daemon.iterdir())

    def tui_argv(self, cli: Cli, profile: Mapping[str, Any], *, native_id: str | None, resume: bool,
                 label: str) -> list[str]:
        argv = [str(cli.binary)]
        if resume:
            thread = checked_id(native_id)
            if thread is None:
                raise AdapterError("a codex resume needs its thread ID")
            argv += ["resume", thread]
        argv += ["--remote", APP_SOCKET, "--dangerously-bypass-approvals-and-sandbox"]
        model = profile.get("model")
        if isinstance(model, str) and model:
            argv += ["--model", model]
        return argv + profile_args(profile)

    def trust_argv(self, cli: Cli, worktree: Path) -> list[str] | None:
        return ["python3", "-I", str(TRUST_SCRIPT), str(cli.binary), str(worktree)]

    def apply_trust(self, layout: SessionLayout, output: bytes) -> int:
        conf = layout.home / self.config_subdir
        prefix = f"{conf / HOOKS_FILE}:"
        try:
            listed: Any = json.loads(output)
        except ValueError:
            raise AdapterError("the hook trust listing is not JSON") from None
        if not isinstance(listed, list):
            raise AdapterError("the hook trust listing is not a list")
        lines: list[str] = []
        for item in cast(list[Any], listed):
            entry = cast(dict[str, Any], item) if isinstance(item, dict) else {}
            key, digest = entry.get("key"), entry.get("hash")
            if not (isinstance(key, str) and isinstance(digest, str) and key.startswith(prefix)
                    and KEY_TEXT.fullmatch(key) and HASH.fullmatch(digest)):
                raise AdapterError("the hook trust listing has an invalid entry")
            lines.append(f'\n[hooks.state."{key}"]\ntrusted_hash = "{digest}"\n')
        if lines:
            with (conf / "config.toml").open("a") as fh:
                fh.writelines(lines)
        return len(lines)
```

`src/heterodyne/agents/registry.py`:

```python
"""The adapters the sandbox runtime can launch, by the name profiles give as `adapter`."""

from collections.abc import Mapping

from heterodyne.agents.base import Adapter
from heterodyne.agents.claude_code import ClaudeCode
from heterodyne.agents.codex import Codex

ADAPTERS: Mapping[str, Adapter] = {"claude-code": ClaudeCode(), "codex": Codex()}
```

`src/heterodyne/sandbox/resources/codex_trust.py`:

```python
# pyright: basic
"""Inside a Codex sandbox, before its TUI starts (plan 4 D6; spike S8): ask `codex app-server` over stdio
which hooks it would not run yet. No model request is made. Prints a JSON list of {key, hash, event,
status} for every untrusted or modified hook and exits 0; any failure exits 1 with nothing on stdout.
The host validates every entry before writing it. Standard library only.

Usage: python3 -I codex_trust.py <codex binary> <worktree>
"""

import json
import subprocess
import sys
import threading

LIMIT_SECONDS = 60


def main(argv):
    codex, work = argv
    proc = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(LIMIT_SECONDS, proc.kill)
    timer.start()

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def call(id_, method, params):
        send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        while True:
            line = proc.stdout.readline()
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
        data = call(1, "hooks/list", {"cwds": [work]})["data"]
    finally:
        timer.cancel()
        proc.kill()
        proc.wait(5)
    out = [{"key": h["key"], "hash": h["currentHash"], "event": h["eventName"], "status": h["trustStatus"]}
           for entry in data for h in entry["hooks"] if h["trustStatus"] in ("untrusted", "modified")]
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure refuses the launch on the host
        print(f"codex_trust: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
```

In `pyproject.toml`, add to `[tool.ruff.lint.per-file-ignores]`:

```toml
"src/heterodyne/sandbox/resources/**" = ["S", "PTH"]
```

The resources run inside the sandbox with only the image's Python, so they are written like the spike's scripts; `# pyright: basic` at the top of each keeps pyright's strict mode off them alone.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_agents_adapters.py tests/test_admind_agent.py -q && timeout 300 uv run pyright src/heterodyne/agents src/heterodyne/sandbox && timeout 300 uv run ruff check src tests`
Expected: PASS (admind's agent tests still pass: `interactive_argv` is unchanged); clean.

- [ ] **Step 8: Commit**

```bash
git add src/heterodyne/agents/base.py src/heterodyne/agents/claude_code.py src/heterodyne/agents/codex.py \
  src/heterodyne/agents/registry.py src/heterodyne/sandbox/resources/codex_trust.py pyproject.toml \
  tests/fakes/fake_agent_cli.py tests/sandbox_env.py tests/test_agents_adapters.py
git commit -m "plan4 T7: claude-code and codex adapters, synthetic home, Codex hook trust"
```

### Task 8 [r15]: The probes and the exec-path self-test

**Files:**
- Create: `src/heterodyne/sandbox/resources/probes.py` (port of `spikes/s5/probes.py`), `src/heterodyne/sandbox/openshell_selftest.py`
- Test: `tests/test_sandbox_openshell_selftest.py`

**Interfaces:**
- Consumes:
  - Task 10's `selftest.ProbeContext`, `selftest.SelfTestFailed` (Task 8 is blocked by Task 10 for this reason);
  - `backend.Backend`, `backend.BackendError`; `spec.LAUNCHER_ENV`, `spec.PROBES_INSIDE`, `spec.Bind`;
  - the adapter's `model_hosts`, `tool_env`.
- Produces (in `heterodyne.sandbox.openshell_selftest`):
  - `PROBE_EXE = "/usr/bin/python3.12"`, `PROBE_ARGV`, `OPENSHELL_ENV`, `SHELL_ENV`, `EXEC_CHECKS`, `AGENT_CHECKS`;
  - `classify(path) -> str` (`present`, `absent` or `unknown`);
  - `other_accounts(others, logins) -> list[dict[str, Any]]`;
  - `env_allowed(adapter, path) -> frozenset[str]`;
  - `probe_config(ctx, path) -> dict[str, Any]`;
  - `log_needles(ctx, path) -> list[tuple[str, str]]`;
  - `OpenShellSelfTest(*, wall=time.time, sleep=time.sleep)`, implementing `SelfTest`: `files()`, `exec_path(ctx)`; Task 9 adds `agent_path(ctx)`.

This is §7's launch self-test, as S5 ran it (`spikes/s5/launch.py`, `_selftest`; `spikes/s5/probes.py`). The checks are S5's. What changes in the port:

- The token is read from `/run/hz/token`, never passed in the probe's input (D5). The probe's input holds no secret.
- The sockets are `/run/hz/s.sock` and `/run/hz/p.sock` (D2).
- `wsd-socket-absent` checks wsd's real control socket path (`wsd_socket` in the input), and that `/run/hz` holds no socket but the session's two.
- `direct-network-blocked` keeps S5's exact criterion: the kernel fence (no interface but `lo`, no route), `Seccomp: 2`, `NoNewPrivs: 1`, no effective capability, and exactly the broker's answers (TCP/UDP connect `EACCES`, a non-DNS UDP send `EDESTADDRREQ`, raw and ICMP sockets `EPROTONOSUPPORT`). **This is the criterion r15 must accept** (gap 2), so the "DEVIATION … ADR amendment needed" text is dropped from the evidence.
- `other-accounts` no longer requires a non-empty account list. Plan 4 launches the default account, and a host with no named accounts has no other account to probe; the Other accounts canary (outside every bind, readable outside just before) is always checked, so the check never passes vacuously.
- The ip:port probe targets keep their `# install-agnostic: allow=ip-port` comments.

The host side runs in this order, each failure raising `SelfTestFailed(<check>)`:

1. **canary-precondition**: a fresh random value is written to the real-home canary and read back outside.
2. **outer-fence-network-none**: `backend.network_mode(name)` must be `none`.
3. **The probes**, by a separate `sandbox exec` with their input on stdin, within 180 s. A non-zero exit fails with the names of the checks that printed `FAIL`.
4. **The supervisor log**, 3 s later: this run's refusal of the denied host, its allow of the probe control (policy `probe_control`), its refusal of the literal address, and the model host refused to curl outside the agent's process tree (`exec-path-not-agent`).

- [ ] **Step 1: Write the failing tests**

`tests/test_sandbox_openshell_selftest.py`:

```python
import ast
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from sandbox_env import config_env

from heterodyne.agents.base import Cli
from heterodyne.agents.codex import Codex
from heterodyne.sandbox.backend import Backend, BackendError, ExecResult
from heterodyne.sandbox.openshell_selftest import (
    AGENT_CHECKS,
    EXEC_CHECKS,
    OpenShellSelfTest,
    classify,
    env_allowed,
    log_needles,
    other_accounts,
    probe_config,
)
from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.sandbox.spec import Bind, SandboxSpec, SessionLayout
from heterodyne.session.server import TurnState
from heterodyne.tmux import Tmux

PROBES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources" / "probes.py"


@dataclass
class StubBackend:
    """Answers the self-test's backend calls; records what it was asked."""
    mode: str = "none"
    rc: int = 0
    out: bytes = b""
    lines: list[str] = field(default_factory=list[str])
    raises: bool = False
    calls: list[tuple[list[str], bytes | None]] = field(default_factory=list[tuple[list[str], bytes | None]])

    def network_mode(self, name: str) -> str:
        return self.mode

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        self.calls.append((list(argv), input))
        if self.raises:
            raise BackendError("openshell sandbox timed out")
        return ExecResult(self.rc, self.out, b"")

    def logs(self, name: str, since: float) -> list[str]:
        return self.lines


def context(tmp_path: Path, backend: StubBackend, others: tuple[tuple[str, Path], ...] = ()) -> ProbeContext:
    login = tmp_path / "home" / ".codex" / "auth.json"
    login.parent.mkdir(parents=True, exist_ok=True)
    login.write_text('{"fake": "not-a-token"}')
    layout = SessionLayout(tmp_path / "sessions" / "hz0123456789ab")
    layout.root.mkdir(parents=True, exist_ok=True)
    spec = SandboxSpec(name="hz0123456789abg1", workdir=tmp_path, binds=(),
                       logins=(Bind(login.resolve(), layout.home / ".codex" / "auth.json", True),),
                       read_only=(), read_write=(), egress=(), env={}, uid=1000, gid=1000)
    settings = sandbox_settings(config_env(tmp_path / "cfg"))
    (tmp_path / "real-home").mkdir(exist_ok=True)
    return ProbeContext(backend=cast(Backend, backend), tmux=cast(Tmux, None),
                        tmux_session="wsd-hz0123456789ab",
                        spec=spec, layout=layout, generation=1, adapter=Codex(),
                        cli=Cli(tmp_path / "codex", tmp_path, "0.160.0"), token="fake-session-token",
                        others=others, real_home_canary=tmp_path / "real-home" / ".heterodyne-canary",
                        wsd_socket=tmp_path / "state" / "wsd" / "ctl.sock", settings=settings,
                        turns=TurnState)


def passing_logs(ctx: ProbeContext) -> list[str]:
    return [f"[1800000000.0] [ocsf] {needle} ..." for _, needle in log_needles(ctx, "exec")]


def selftest() -> OpenShellSelfTest:
    return OpenShellSelfTest(wall=lambda: 1_800_000_000.0, sleep=lambda s: None)


def test_probes_file_is_stdlib_only_and_names_every_check() -> None:
    tree = ast.parse(PROBES.read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"errno", "hashlib", "ipaddress", "json", "os", "socket", "stat", "subprocess", "sys"}
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for check in {*AGENT_CHECKS, *EXEC_CHECKS} - {"model-host-agent-path", "model-host-exec-path"}:
        assert check in literals
    assert "/run/hz/token" in PROBES.read_text()        # the token is read inside, never sent in the input


def test_classify(tmp_path: Path) -> None:
    present = tmp_path / "a" / "auth.json"
    present.parent.mkdir()
    present.write_text("{}")
    dangling = tmp_path / "a" / "dangling.json"
    dangling.symlink_to(tmp_path / "nowhere")
    assert classify(present) == "present"
    assert classify(tmp_path / "a" / "missing.json") == "absent"
    assert classify(dangling) == "unknown"
    assert classify(tmp_path / "no-dir" / "auth.json") == "unknown"


def test_other_accounts_skip_an_alias_of_the_chosen_login(tmp_path: Path) -> None:
    chosen = tmp_path / "default" / "auth.json"
    chosen.parent.mkdir()
    chosen.write_text("{}")
    alias = tmp_path / "alias"
    alias.symlink_to(chosen.parent)
    other = tmp_path / "work" / "auth.json"
    other.parent.mkdir()
    other.write_text("{}")
    found = other_accounts((("alias", alias / "auth.json"), ("work", other)),
                           (Bind(chosen.resolve(), Path("/inside/auth.json"), True),))
    assert found == [{"account": "work", "class": "present", "paths": [str(other)]}]


def test_env_allowed_adds_the_cli_tool_env_on_the_agent_path_only() -> None:
    assert "CODEX_THREAD_ID" in env_allowed(Codex(), "agent")
    assert "CODEX_THREAD_ID" not in env_allowed(Codex(), "exec")
    assert {"HOME", "OPENSHELL_SANDBOX", "PWD"} <= env_allowed(Codex(), "exec")


def test_probe_config_holds_no_token_and_pins_the_chosen_hash(tmp_path: Path) -> None:
    ctx = context(tmp_path, StubBackend())
    cfg = probe_config(ctx, "exec")
    assert "fake-session-token" not in json.dumps(cfg)
    [chosen] = cfg["chosen"]
    login = ctx.spec.logins[0]
    digest = hashlib.sha256(login.source.read_bytes()).hexdigest()
    assert chosen == {"path": str(login.target), "sha256": digest}
    assert cfg["model_host"] == "chatgpt.com" and cfg["wsd_socket"] == str(ctx.wsd_socket)
    assert ctx.layout.oa_canary.read_text()                         # written fresh, readable outside


def test_exec_path_passes(tmp_path: Path) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    selftest().exec_path(ctx)
    [(argv, data)] = backend.calls
    assert argv == ["python3", "-I", "/run/hz/probes.py"]
    assert data is not None and json.loads(data)["path"] == "exec"
    assert ctx.real_home_canary.read_text()


@pytest.mark.parametrize("change, check", [
    ({"mode": "bridge"}, "outer-fence-network-none"),
    ({"rc": 1, "out": b"PASS a [x]\nFAIL direct-network-blocked [y]\n"}, "direct-network-blocked"),
    ({"raises": True}, "probes-timeout"),
])
def test_exec_path_failures(tmp_path: Path, change: dict[str, object], check: str) -> None:
    backend = StubBackend(**change)  # type: ignore[arg-type]
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    with pytest.raises(SelfTestFailed, match=check):
        selftest().exec_path(ctx)


def test_a_missing_log_line_fails(tmp_path: Path) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)[1:]
    with pytest.raises(SelfTestFailed, match="proxy-logged-refusal"):
        selftest().exec_path(ctx)


def test_an_unwritable_canary_fails_the_precondition(tmp_path: Path) -> None:
    ctx = context(tmp_path, StubBackend())
    (tmp_path / "real-home").rmdir()
    (tmp_path / "real-home").write_text("not a directory")
    with pytest.raises(SelfTestFailed, match="canary-precondition"):
        selftest().exec_path(ctx)


def test_files_ship_the_probes() -> None:
    assert selftest().files() == {"probes.py": PROBES.read_text()}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_openshell_selftest.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.sandbox.openshell_selftest'`.

- [ ] **Step 3: Write the probes**

`src/heterodyne/sandbox/resources/probes.py`:

```python
# pyright: basic
"""Inside the sandbox: the §7 launch self-test probes under OpenShell (ported from spike S5).

Each probe prints PASS/FAIL with its evidence, and only the specific enforcement passes: a generic
failure (missing canary, dead socket, timeout) FAILs. Under OpenShell an egress refusal is a connect()
EACCES from the supervisor's interception, so the marker is the policy-DNS synthetic address, and the
host checks the supervisor's own log afterwards.

Run twice per launch: by a separate `sandbox exec` (path "exec", input on stdin) and by the agent itself
through its own tool (path "agent", input from the read-only /run/hz/agent-probe.json). On the agent path
every result also goes to the host over /run/hz/p.sock, where the host verifies this process before it
counts anything it sends. The session token is read from /run/hz/token; the input holds no secret.

Usage: python3 -I /run/hz/probes.py < input        (exec path)
       python3 -I /run/hz/probes.py --agent        (agent path)
Standard library only.
"""
import errno
import hashlib
import ipaddress
import json
import os
import socket
import stat
import subprocess
import sys

RUN = '/run/hz'
AGENT = sys.argv[1:] == ['--agent']
if AGENT:
    with open(f'{RUN}/agent-probe.json') as f:
        cfg = json.load(f)
    channel = socket.socket(socket.AF_UNIX)
    channel.connect(f'{RUN}/p.sock')
else:
    cfg = json.load(sys.stdin)
with open(f'{RUN}/token') as f:
    TOKEN = f.read().strip()
PATH = cfg.get('path', 'exec')
SYNTHETIC = ipaddress.ip_network('198.18.0.0/15')   # OpenShell policy DNS answers from this range
rc = 0


def report(msg):
    if AGENT:
        channel.sendall((json.dumps(msg) + '\n').encode())


def check(name, ok, evidence):
    global rc
    print(f"{'PASS' if ok else 'FAIL'} {name} [{evidence}]", flush=True)
    report({'check': name, 'ok': ok, 'evidence': evidence})
    rc = rc or (0 if ok else 1)


def open_result(path, mode='rb'):
    try:
        open(path, mode).close()
        return 'READABLE' if 'r' in mode else 'WRITABLE'
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))


def connect(addr, family=socket.AF_INET, kind=socket.SOCK_STREAM):
    s = socket.socket(family, kind)
    s.settimeout(3)
    try:
        if kind == socket.SOCK_DGRAM:
            s.sendto(b'x', addr)
            return 'SENT'
        s.connect(addr)
        return 'CONNECTED'
    except TimeoutError:
        return 'TIMEOUT'
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))
    finally:
        s.close()


def sock(req):
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(3)
        s.connect(f'{RUN}/s.sock')
        s.sendall((json.dumps(req) + '\n').encode())
        return s.makefile().readline().strip()
    except OSError as e:
        return f'socket error {errno.errorcode.get(e.errno, e.errno)}'


def sock_create(fam, kind, proto):
    try:
        socket.socket(fam, kind, proto).close()
        return 'CREATED'
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))


def curl(host):
    cu = subprocess.run(['curl', '-sv', '-o', '/dev/null', '--max-time', '10', '-w', 'http=%{http_code}',
                         f'https://{host}/'], capture_output=True, text=True)
    issuer = next((ln.split('issuer:')[1].strip() for ln in cu.stderr.splitlines() if 'issuer:' in ln), '')
    return cu, issuer


# 1. Real home: the canary (readable outside just before) gives ENOENT or EACCES/EPERM here.
r = open_result(cfg['real_home_canary'])
check('real-home-canary-unreadable', r in ('ENOENT', 'EACCES', 'EPERM'), f"open(canary) -> {r}")

# 2. Egress: the denied host resolves through OpenShell's policy DNS (synthetic address = marker), and
#    the connect is refused with EACCES by the supervisor's interception. The host then requires the
#    supervisor log's DENIED line for this binary and host.
host = cfg['denied']
try:
    addrs = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)})
except OSError as e:
    addrs = [f'resolve-error:{e}']
v4 = [a for a in addrs if not a.startswith('resolve') and ':' not in a]
marker = bool(v4) and len(v4) == len(addrs) and all(ipaddress.ip_address(a) in SYNTHETIC for a in v4)
c = connect((addrs[0], 443)) if marker else 'not-tried'
cu = subprocess.run(['curl', '-sv', '-o', '/dev/null', '--max-time', '8', f'https://{host}/'],
                    capture_output=True, text=True)
ok = marker and c == 'EACCES' and cu.returncode == 7 and 'Permission denied' in cu.stderr
check('non-allowlisted-host-blocked', ok,
      f'{host} -> {",".join(addrs)} (policy-DNS synthetic={marker}) connect -> {c}; curl rc={cu.returncode}')

# 3. Direct network, two layers, both required (the criterion ADR revision 15 accepts):
#    - the kernel fence: the workload's netns has only lo and no route (podman --network none, also
#      checked from outside as outer-fence-network-none);
#    - OpenShell's seccomp user-notification broker (Seccomp: 2) answers every INET socket operation
#      first: TCP and UDP connect() -> EACCES, a destination-bearing UDP send that is not DNS ->
#      EDESTADDRREQ, any other INET socket type (raw, ICMP) -> EPROTONOSUPPORT at socket().
#    Only these exact answers pass.
with open('/proc/net/dev') as f:
    ifs = [ln.split(':')[0].strip() for ln in f.readlines()[2:]]
non_lo = [i for i in ifs if i != 'lo']
with open('/proc/net/route') as f:
    routes = len(f.readlines()) - 1
with open('/proc/net/ipv6_route') as f:
    routes6 = [ln.split()[-1] for ln in f if ln.split()[-1] != 'lo']
with open('/proc/self/status') as f:
    status = dict(ln.split(':', 1) for ln in f if ':' in ln)
seccomp = status.get('Seccomp', '').strip()
nnp = status.get('NoNewPrivs', '').strip()
capeff = status.get('CapEff', '').strip() or 'f'
c4 = connect(('1.1.1.1', 443))            # install-agnostic: allow=ip-port (public anycast probe target)
c6 = connect(('2606:4700:4700::1111', 443), family=socket.AF_INET6)
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    u.connect(('1.1.1.1', 53))           # install-agnostic: allow=ip-port
    uc = 'CONNECTED'
except OSError as e:
    uc = errno.errorcode.get(e.errno, str(e.errno))
finally:
    u.close()
us = connect(('1.1.1.1', 53), kind=socket.SOCK_DGRAM)   # install-agnostic: allow=ip-port
raw = sock_create(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
icmp = sock_create(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
ok = (not non_lo and routes == 0 and not routes6 and seccomp == '2' and nnp == '1' and int(capeff, 16) == 0
      and c4 == 'EACCES' and c6 == 'EACCES' and uc == 'EACCES' and us == 'EDESTADDRREQ'
      and raw == 'EPROTONOSUPPORT' and icmp == 'EPROTONOSUPPORT')
check('direct-network-blocked', ok,
      f"non-lo interfaces={non_lo or 'none'} ipv4 routes={routes} ipv6 non-lo routes={len(routes6)} "
      f"Seccomp={seccomp} NoNewPrivs={nnp} CapEff={capeff} "
      f"tcp4 connect -> {c4} tcp6 connect -> {c6} udp connect -> {uc} udp sendto -> {us} "
      f"raw socket -> {raw} icmp socket -> {icmp}")

# 4. A control operation on the session socket, with a valid token, gets the explicit forbidden reply.
r = sock({'token': TOKEN, 'type': 'approve', 'payload': {}})
check('control-op-rejected', r == '{"ok": false, "error": "forbidden"}', f'reply={r}')

# 5. wsd's control socket is not reachable, and /run/hz holds no socket but the session's own two.
sockets = []
for entry in sorted(os.listdir(RUN)):
    try:
        if stat.S_ISSOCK(os.lstat(f'{RUN}/{entry}').st_mode):
            sockets.append(entry)
    except OSError:
        sockets.append(f'{entry}?')
gone = open_result(cfg['wsd_socket']) in ('ENOENT', 'EACCES', 'EPERM')
check('wsd-socket-absent', gone and set(sockets) <= {'s.sock', 'p.sock'},
      f"wsd socket reachable={not gone}; sockets in {RUN}: {' '.join(sockets) or 'none'}")

# 6. The allowlisted control is reachable, through OpenShell's TLS-terminating proxy (its CA issues).
cu, issuer = curl(cfg['allowed'])
ok = cu.returncode == 0 and 'OpenShell Sandbox CA' in issuer and 'http=000' not in cu.stdout
check('allowlisted-host-reachable', ok, f"curl rc={cu.returncode} {cu.stdout} issuer='{issuer}'")

# 6b. The model host's policy names only the CLI binary, and OpenShell also authorizes a binary's
#     executable ancestors. So curl reaches it only from inside the agent's own process tree: on the
#     agent path it must be reachable (through the proxy), on the separate exec path refused. The host
#     checks the supervisor log for the matching ALLOWED/DENIED line.
cu, issuer = curl(cfg['model_host'])
if PATH == 'agent':
    ok = cu.returncode == 0 and 'OpenShell Sandbox CA' in issuer
else:
    ok = cu.returncode == 7 and 'Permission denied' in cu.stderr
check(f'model-host-{PATH}-path', ok,
      f"{cfg['model_host']}: curl rc={cu.returncode} {cu.stdout} issuer='{issuer}' "
      f"(expect {'reachable via the CLI ancestor' if PATH == 'agent' else 'refused: no CLI ancestor'})")

# 7. A hook event with the valid token is accepted.
r = sock({'token': TOKEN, 'type': 'hook_event', 'payload': {}})
check('hook-event-accepted', r == '{"ok": true}', f'reply={r}')

# 8. Environment: only the launcher's allowlist, OpenShell's fixed secret-free set and the shell's own;
#    on the agent path also the pinned CLI's tool variables (all from the host, by name).
extra = sorted(set(os.environ) - set(cfg['env_allowed']))
user_env = set(json.loads(os.environ.get('OPENSHELL_USER_ENVIRONMENT', '{}')))
extra += sorted(f'USER_ENVIRONMENT:{k}' for k in user_env - set(cfg['env_user']))
check('host-env-not-inherited', not extra, f"{PATH} path, unexpected vars: {' '.join(extra) or 'none'}")

# 9. OpenShell's own control material is not readable by the workload.
paths = ['/.openshell/channel/sandbox/bootstrap.json', '/.openshell/channel/sandbox/server.key',
         '/run/secrets', '/etc/openshell/auth/sandbox.jwt', '/etc/openshell/tls/client/tls.key']
res = {p: open_result(p) for p in paths}
check('openshell-control-material-unreadable', all(v in ('ENOENT', 'EACCES', 'EPERM', 'EISDIR') and
      (v != 'EISDIR' or open_result(p + '/x') != 'READABLE') for p, v in res.items()),
      ' '.join(f'{p}->{v}' for p, v in res.items()))

# 10. Other accounts (§7): each present or unknown login file, by configured and canonical path, gives
#     ENOENT/EACCES; an absent one gives ENOENT; the canary outside every bind gives ENOENT/EACCES; each
#     chosen login file matches the host's and is read-only here.
ev, ok = [], True
for o in cfg['other_accounts']:
    want = ('ENOENT',) if o['class'] == 'absent' else ('ENOENT', 'EACCES', 'EPERM')
    for p in o['paths']:
        r = open_result(p)
        ok &= r in want
        ev.append(f"{o['account']}/{o['class']}:{p}->{r}")
r = open_result(cfg['oa_canary'])
ok &= r in ('ENOENT', 'EACCES', 'EPERM')
ev.append(f"canary->{r}")
for c in cfg['chosen']:
    try:
        with open(c['path'], 'rb') as f:
            same = hashlib.sha256(f.read()).hexdigest() == c['sha256']
    except OSError as e:
        same = f'error {errno.errorcode.get(e.errno)}'
    w = open_result(c['path'], 'ab')
    ok &= same is True and w in ('EROFS', 'EACCES', 'EPERM')
    ev.append(f"chosen:{c['path']} sha256-match={same} write->{w}")
check('other-accounts', ok and bool(cfg['chosen']), '; '.join(ev))
report({'done': rc})
sys.exit(rc)
```

- [ ] **Step 4: Write `openshell_selftest.py`**

`src/heterodyne/sandbox/openshell_selftest.py`:

```python
"""The §7 launch self-test under OpenShell (ADR 0001 §7 as amended by revision 15; spike S5).

Both paths run the same probes (resources/probes.py) inside the sandbox, with host-side preconditions
before and the OpenShell supervisor's own log after. The exec path runs them by a separate `sandbox
exec`; the agent path (Task 9) has the agent run them through its own tool and counts only results sent
by a peer the host verifies itself. Every failure raises SelfTestFailed naming the check; the runtime
then deletes the sandbox and the launch fails.
"""

import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from heterodyne.agents.base import Adapter
from heterodyne.sandbox.backend import BackendError
from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed
from heterodyne.sandbox.spec import LAUNCHER_ENV, PROBES_INSIDE, Bind

PROBES_SOURCE = Path(__file__).resolve().with_name("resources") / "probes.py"
# The agent-path probe as the host requires to see it in /proc: the image's python, isolated mode (no
# PYTHON* variables, no user site), the read-only script.
PROBE_EXE = "/usr/bin/python3.12"
PROBE_ARGV = ("python3", "-I", str(PROBES_INSIDE), "--agent")
# What OpenShell 0.1.2 itself puts in a workload's environment (S5; none carries a secret).
OPENSHELL_ENV = frozenset({"OPENSHELL_SANDBOX", "OPENSHELL_USER_ENVIRONMENT", "SSL_CERT_FILE",
                           "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "GIT_SSL_CAINFO", "NODE_EXTRA_CA_CERTS",
                           "DENO_CERT",
                           "container", "HOSTNAME", "DEBIAN_FRONTEND", "SHELL"})
SHELL_ENV = frozenset({"PWD", "SHLVL", "_", "OLDPWD"})
COMMON_CHECKS = ("real-home-canary-unreadable", "non-allowlisted-host-blocked", "direct-network-blocked",
                 "control-op-rejected", "wsd-socket-absent", "allowlisted-host-reachable")
TAIL_CHECKS = ("hook-event-accepted", "host-env-not-inherited", "openshell-control-material-unreadable",
               "other-accounts")
EXEC_CHECKS = (*COMMON_CHECKS, "model-host-exec-path", *TAIL_CHECKS)
AGENT_CHECKS = (*COMMON_CHECKS, "model-host-agent-path", *TAIL_CHECKS)
PROBE_SECONDS = 180
LOG_SETTLE_SECONDS = 3
DIRECT_TARGET = "1.1.1.1:443"       # install-agnostic: allow=ip-port (the probes' literal-address target)


def classify(path: Path) -> str:
    """present: it opens. absent: its directory lists and the name is not in it. unknown: anything else,
    such as an unlistable directory, or a listed name that doesn't open (a dangling symlink, EACCES)."""
    try:
        path.open("rb").close()
        return "present"
    except OSError:
        pass
    try:
        return "unknown" if path.name in os.listdir(path.parent) else "absent"
    except OSError:
        return "unknown"


def other_accounts(others: Sequence[tuple[str, Path]], logins: Sequence[Bind]) -> list[dict[str, Any]]:
    """Every login file of every other account, by configured and canonical path. An account whose
    canonical login file is a chosen one (an alias of the chosen account) is not "other"."""
    chosen = {os.path.realpath(b.source) for b in logins}
    found: list[dict[str, Any]] = []
    for account, configured in others:
        canonical = os.path.realpath(configured)
        if canonical in chosen:
            continue
        found.append({"account": account, "class": classify(configured),
                      "paths": sorted({str(configured), canonical})})
    return found


def env_allowed(adapter: Adapter, path: str) -> frozenset[str]:
    base = LAUNCHER_ENV | OPENSHELL_ENV | SHELL_ENV
    return base | adapter.tool_env if path == "agent" else base


def probe_config(ctx: ProbeContext, path: str) -> dict[str, Any]:
    """The probes' input, after the host-side preconditions: a fresh Other accounts canary readable
    outside, each chosen login file's hash, and the other accounts' classes. It holds no secret."""
    ctx.layout.oa_canary.write_text(secrets.token_hex(8))
    ctx.layout.oa_canary.read_bytes()
    chosen = [{"path": str(b.target), "sha256": hashlib.sha256(b.source.read_bytes()).hexdigest()}
              for b in ctx.spec.logins]
    return {"path": path, "real_home_canary": str(ctx.real_home_canary),
            "oa_canary": str(ctx.layout.oa_canary),
            "other_accounts": other_accounts(ctx.others, ctx.spec.logins), "chosen": chosen,
            "allowed": ctx.settings.probe_allowed_host, "denied": ctx.settings.probe_denied_host,
            "model_host": ctx.adapter.model_hosts[0], "wsd_socket": str(ctx.wsd_socket),
            "env_allowed": sorted(env_allowed(ctx.adapter, path)),
            "env_user": sorted(LAUNCHER_ENV | OPENSHELL_ENV)}


def log_needles(ctx: ProbeContext, path: str) -> list[tuple[str, str]]:
    """The supervisor log lines this run must have produced. The model host's rule names only the CLI
    binary, and OpenShell also authorizes a connection whose executable ancestor is listed: ALLOWED
    proves the agent path ran inside the agent's tree, DENIED proves the exec path did not."""
    model, denied = ctx.adapter.model_hosts[0], ctx.settings.probe_denied_host
    refused = "[reason:transparent_tcp_policy_denied]"
    ancestry = (("agent-ancestry-allowed", f"ALLOWED /usr/bin/curl(0) -> {model}:443 [policy:model")
                if path == "agent" else
                ("exec-path-not-agent", f"DENIED /usr/bin/curl(0) -> {model}:443 {refused}"))
    return [("proxy-logged-refusal", f"DENIED /usr/bin/curl(0) -> {denied}:443 {refused}"),
            ("proxy-logged-allow",
             f"ALLOWED /usr/bin/curl(0) -> {ctx.settings.probe_allowed_host}:443 [policy:probe_control"),
            ("proxy-logged-direct-deny", f"-> {DIRECT_TARGET} {refused}"),
            ancestry]


class OpenShellSelfTest:
    def __init__(self, *, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.wall = wall
        self.sleep = sleep

    def files(self) -> Mapping[str, str]:
        return {"probes.py": PROBES_SOURCE.read_text()}

    def _canary(self, ctx: ProbeContext) -> None:
        try:
            ctx.real_home_canary.write_text(secrets.token_hex(8))
            ctx.real_home_canary.read_bytes()
        except OSError:
            raise SelfTestFailed("canary-precondition") from None

    def _logs(self, ctx: ProbeContext, since: float, path: str) -> None:
        self.sleep(LOG_SETTLE_SECONDS)       # outside: the supervisor's log must show this run's lines
        lines = ctx.backend.logs(ctx.spec.name, since)
        missing = [check for check, needle in log_needles(ctx, path) if not any(needle in ln for ln in lines)]
        if missing:
            raise SelfTestFailed(", ".join(missing))

    def exec_path(self, ctx: ProbeContext) -> None:
        self._canary(ctx)
        if ctx.backend.network_mode(ctx.spec.name) != "none":
            raise SelfTestFailed("outer-fence-network-none")
        data = json.dumps(probe_config(ctx, "exec")).encode()
        since = self.wall()
        try:
            r = ctx.backend.exec(ctx.spec.name, ctx.spec.workdir, ["python3", "-I", str(PROBES_INSIDE)],
                                 input=data, timeout=PROBE_SECONDS)
        except BackendError:
            raise SelfTestFailed("probes-timeout") from None
        if r.returncode != 0:
            failed = [ln.split()[1] for ln in r.stdout.decode("utf-8", "replace").splitlines()
                      if ln.startswith("FAIL ") and len(ln.split()) > 1]
            raise SelfTestFailed(", ".join(failed) or "probes")
        self._logs(ctx, since, "exec")

    def agent_path(self, ctx: ProbeContext) -> None:
        raise SelfTestFailed("agent-path-not-built")      # Task 9 replaces this
```

Task 9 replaces the `agent_path` stub (which fails closed, so a runtime wired before Task 9 never launches).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_sandbox_openshell_selftest.py -q && timeout 300 uv run pyright src/heterodyne/sandbox && timeout 300 uv run ruff check src tests`
Expected: PASS; clean.

- [ ] **Step 6: Commit**

```bash
git add src/heterodyne/sandbox/resources/probes.py src/heterodyne/sandbox/openshell_selftest.py \
  tests/test_sandbox_openshell_selftest.py
git commit -m "plan4 T8: in-sandbox probes and the exec-path self-test (OpenShell, r15 criterion)"
```

### Task 9 [r15]: The agent-path self-test and its verified channel

**Files:**
- Create: `src/heterodyne/sandbox/channel.py`
- Modify: `src/heterodyne/platform.py` (add `peer_pid_checked`), `src/heterodyne/sandbox/openshell_selftest.py` (replace the `agent_path` stub)
- Test: `tests/test_sandbox_channel.py`, `tests/test_platform.py` (one test added), `tests/test_sandbox_openshell_selftest.py` (agent-path tests added)

**Interfaces:**
- Consumes:
  - Task 8's `PROBE_EXE`, `PROBE_ARGV`, `AGENT_CHECKS`, `env_allowed`, `probe_config`, `log_needles`;
  - Task 10's `ProbeContext`, whose `tmux` is a `Tmux` (`capture`, `paste`);
  - `spec.AGENT_PROBE_INSIDE`, `SessionLayout.run(gen)`, `.probe_socket(gen)`, `.home`; `agents.base.write_private`.
- Produces:
  - `heterodyne.platform.peer_pid_checked(sock: socket.socket) -> int`: the peer's host PID by SO_PEERCRED. It raises `OSError` on any failure, and on any platform but Linux. Unlike `peer_pid`, a decision may rest on it.
  - `heterodyne.sandbox.channel`:
    - `ProcVerifier(netns: str, cli_binary: str, allowed_env: frozenset[str], *, proc: Path = Path("/proc"))`, callable as `verifier(pid) -> str`: `""` when the PID is the probe, else the reason.
    - `ProbeChannel(path: Path, verify: Callable[[int], str], *, peer_pid: Callable[[socket.socket], int] = peer_pid_checked)`, with `start()`, `close()`, `done() -> bool` and `verdict(expected: Sequence[str]) -> str` (`""` on a pass, else a fixed reason).
  - `OpenShellSelfTest(*, wall, sleep, proc=Path("/proc"), peer_pid=peer_pid_checked)`, whose `agent_path(ctx)` is now real.

The agent path is the only one the agent runs: the agent itself runs the probes through its own tool, so they run inside the agent's process tree, where the model host's policy (CLI binary only) also allows curl. What the agent writes or says is untrusted, and so is anything else that reaches the channel. A result counts only from a peer the host checks itself:

- SO_PEERCRED gives the peer's host PID.
- `/proc/<pid>` must show:
  - the image's python (`PROBE_EXE`) running exactly `PROBE_ARGV`;
  - untraced (`TracerPid: 0`);
  - in the workload container's network namespace;
  - with an environment inside the agent-path allowlist;
  - with the CLI binary as an ancestor inside that namespace.
- The same checks are repeated when the probe reports done.

Any other peer is recorded as rejected, and a rejected peer alone fails the gate. So does more than one verified run, a missing or failing check, or a non-zero `done`. Peers that arrive up to 5 s after the probe finished are still counted (S5's forge test).

The steps, each failure raising `SelfTestFailed`:

1. The canary precondition.
2. `agent-probe.json` is written to the run directory (`/run/hz`, read-only inside).
3. The agent's prompt marker appears in the pane, within 90 s (`agent-prompt`).
4. The channel starts on `p.sock`. The host reads the workload's netns from outside (`/proc/<workload_pid>/ns/net`).
5. The prompt is pasted: "Run exactly this shell command, then reply with only its exit status: …".
6. The host waits for the verified probe's `done`, for up to `agent_probe_seconds`, and then 5 s more.
7. The verdict (`agent-path-channel: <reason>`).
8. The supervisor log: the agent path's needles, including the model host ALLOWED for curl (`agent-ancestry-allowed`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_platform.py`:

```python
import socket as _socket

from heterodyne.platform import peer_pid_checked


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SO_PEERCRED is Linux-only")
def test_peer_pid_checked_reads_so_peercred() -> None:
    a, b = _socket.socketpair(_socket.AF_UNIX)
    with a, b:
        assert peer_pid_checked(a) == os.getpid()


def test_peer_pid_checked_raises_on_a_closed_socket() -> None:
    a, b = _socket.socketpair(_socket.AF_UNIX)
    b.close()
    a.close()
    with pytest.raises(OSError):
        peer_pid_checked(a)
```

(Add `import os`, `import sys` and `import pytest` at the top of the file if it lacks them.)

`tests/test_sandbox_channel.py`:

```python
import json
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import short_dir, wait_for

from heterodyne.sandbox.channel import ProbeChannel, ProcVerifier
from heterodyne.sandbox.openshell_selftest import PROBE_ARGV, PROBE_EXE

NETNS = "net:[4026531999]"
CLI = "/opt/codex/bin/codex"
ALLOWED = frozenset({"HOME", "PATH"})


def fake_proc(root: Path, pid: int, *, exe: str, argv: tuple[str, ...], ppid: int, netns: str = NETNS,
              env: tuple[str, ...] = ("HOME=/s/home",), tracer: int = 0) -> None:
    d = root / str(pid)
    (d / "ns").mkdir(parents=True)
    (d / "exe").symlink_to(exe)
    (d / "ns" / "net").symlink_to(netns)
    (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
    (d / "environ").write_bytes(b"".join(e.encode() + b"\0" for e in env))
    (d / "status").write_text(f"Name:\tpython3\nPPid:\t{ppid}\nTracerPid:\t{tracer}\nUid:\t1000\n")


def probe_tree(root: Path, **probe: object) -> None:
    """pid 30: the CLI (in the workload netns), 31: a shell, 32: the probe."""
    fake_proc(root, 30, exe=CLI, argv=("codex",), ppid=1)
    fake_proc(root, 31, exe="/usr/bin/bash", argv=("bash", "-c", "..."), ppid=30)
    fields = {"exe": PROBE_EXE, "argv": PROBE_ARGV, "ppid": 31, **probe}
    fake_proc(root, 32, **fields)  # type: ignore[arg-type]


def verifier(root: Path) -> ProcVerifier:
    return ProcVerifier(NETNS, CLI, ALLOWED, proc=root)


def test_the_real_probe_verifies(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    assert verifier(tmp_path)(32) == ""


@pytest.mark.parametrize("probe, reason", [
    ({"exe": "/usr/bin/python3"}, "not the probe"),
    ({"argv": ("python3", "/run/hz/probes.py", "--agent")}, "not the probe"),
    ({"tracer": 77}, "traced"),
    ({"netns": "net:[1]"}, "netns"),
    ({"env": ("HOME=/s/home", "LD_PRELOAD=/x.so")}, "environment beyond the allowlist: LD_PRELOAD"),
])
def test_a_wrong_peer_is_rejected(tmp_path: Path, probe: dict[str, object], reason: str) -> None:
    probe_tree(tmp_path, **probe)
    assert reason in verifier(tmp_path)(32)


def test_no_cli_ancestor_inside_the_netns_is_rejected(tmp_path: Path) -> None:
    fake_proc(tmp_path, 30, exe=CLI, argv=("codex",), ppid=1, netns="net:[1]")   # the CLI, but outside
    fake_proc(tmp_path, 31, exe="/usr/bin/bash", argv=("bash",), ppid=30)
    fake_proc(tmp_path, 32, exe=PROBE_EXE, argv=PROBE_ARGV, ppid=31)
    assert "no /opt/codex/bin/codex ancestor" in verifier(tmp_path)(32)


def test_a_vanished_pid_is_rejected(tmp_path: Path) -> None:
    assert "unreadable" in verifier(tmp_path)(99)


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    with short_dir() as d:
        yield d


def send(path: Path, *lines: dict[str, object]) -> None:
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(path))
        s.sendall(b"".join((json.dumps(m) + "\n").encode() for m in lines))


def all_pass(expected: tuple[str, ...]) -> list[dict[str, object]]:
    return [*({"check": c, "ok": True, "evidence": "x"} for c in expected), {"done": 0}]


EXPECTED = ("a", "b")


def channel(path: Path, pids: list[int], verdicts: dict[int, str]) -> ProbeChannel:
    ch = ProbeChannel(path, lambda pid: verdicts.get(pid, "not the probe"), peer_pid=lambda s: pids.pop(0))
    ch.start()
    return ch


def test_one_verified_run_passes(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(ch.done)
        assert ch.verdict(EXPECTED) == ""
    finally:
        ch.close()
    assert not (sock_dir / "p.sock").exists()


def test_a_forger_after_the_probe_fails_the_gate(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32, 40], {32: ""})
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(ch.done)
        send(sock_dir / "p.sock", *all_pass(EXPECTED))          # pid 40: not the probe
        wait_for(lambda: ch.verdict(EXPECTED) != "")
        assert ch.verdict(EXPECTED) == "a peer that is not the probe connected"
    finally:
        ch.close()


@pytest.mark.parametrize("lines, reason", [
    ([{"check": "a", "ok": True, "evidence": ""}, {"done": 0}], "missing checks: b"),
    ([{"check": "a", "ok": True, "evidence": ""}, {"check": "b", "ok": False, "evidence": ""}, {"done": 1}],
     "failed checks: b"),
    ([{"check": "a", "ok": True, "evidence": ""}, {"check": "b", "ok": True, "evidence": ""}, {"done": 1}],
     "the probe did not finish cleanly"),
    (["not json"], "malformed result"),
])
def test_bad_results_fail(sock_dir: Path, lines: list[object], reason: str) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(sock_dir / "p.sock"))
            body = b"".join((ln if isinstance(ln, str) else json.dumps(ln)).encode() + b"\n" for ln in lines)
            s.sendall(body)
        wait_for(lambda: ch.done() or ch.verdict(EXPECTED) == "malformed result")
        assert ch.verdict(EXPECTED) == reason
    finally:
        ch.close()


def test_a_peer_that_changes_before_done_fails(sock_dir: Path) -> None:
    calls: list[int] = []

    def verify(pid: int) -> str:
        calls.append(pid)
        return "" if len(calls) == 1 else "traced"

    ch = ProbeChannel(sock_dir / "p.sock", verify, peer_pid=lambda s: 32)
    ch.start()
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(ch.done)
        assert ch.verdict(EXPECTED) == "the probe changed before it finished"
    finally:
        ch.close()


def test_no_run_at_all_fails(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [], {})
    try:
        assert ch.verdict(EXPECTED) == "no verified probe run"
    finally:
        ch.close()


def test_a_peer_pid_failure_counts_as_rejected(sock_dir: Path) -> None:
    def broken(s: socket.socket) -> int:
        raise OSError("no peer credentials")

    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer_pid=broken)
    ch.start()
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(lambda: ch.verdict(EXPECTED) == "a peer that is not the probe connected")
    finally:
        ch.close()
```

Append to `tests/test_sandbox_openshell_selftest.py`:

```python
import socket
import threading

from sandbox_env import short_dir

from heterodyne.sandbox.openshell_selftest import PROBE_ARGV, PROBE_EXE


@dataclass
class StubTmux:
    """The pane: shows the prompt marker, and on a paste runs `on_paste` (the 'agent')."""
    screen: str = "› "
    on_paste: list[Callable[[], None]] = field(default_factory=list[Callable[[], None]])
    pasted: list[str] = field(default_factory=list[str])

    def capture(self, name: str, lines: int) -> str:
        return self.screen

    def paste(self, name: str, text: str) -> None:
        self.pasted.append(text)
        for action in self.on_paste:
            threading.Thread(target=action, daemon=True).start()


def fake_probe_proc(proc: Path, cli: Path) -> None:
    for pid, exe, argv, ppid in ((30, str(cli), ("codex",), 1), (32, PROBE_EXE, PROBE_ARGV, 30)):
        d = proc / str(pid)
        (d / "ns").mkdir(parents=True)
        (d / "exe").symlink_to(exe)
        (d / "ns" / "net").symlink_to("net:[4026531999]")
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
        (d / "environ").write_bytes(b"HOME=/s/home\0")
        (d / "status").write_text(f"PPid:\t{ppid}\nTracerPid:\t0\n")
    (proc / "7").mkdir()
    (proc / "7" / "ns").mkdir()
    (proc / "7" / "ns" / "net").symlink_to("net:[4026531999]")            # the workload's first process


@dataclass
class AgentBackend(StubBackend):
    def workload_pid(self, name: str) -> int:
        return 7


def agent_context(root: Path, tmux: StubTmux) -> ProbeContext:
    backend = AgentBackend()
    ctx = context(root, backend)
    ctx = dataclasses.replace(ctx, tmux=cast(Tmux, tmux), cli=Cli(root / "codex", root, "0.160.0"))
    backend.lines = [f"[1800000000.0] [ocsf] {needle}" for _, needle in log_needles(ctx, "agent")]
    return ctx


def run_probe(ctx: ProbeContext, results: list[dict[str, object]]) -> None:
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(ctx.layout.probe_socket(ctx.generation)))
        s.sendall(b"".join((json.dumps(m) + "\n").encode() for m in results))


def agent_selftest(proc: Path) -> OpenShellSelfTest:
    """Real time, with every wait cut to a bounded 20 ms poll (the probe 'runs' on another thread)."""
    return OpenShellSelfTest(sleep=lambda s: time.sleep(min(s, 0.02)), proc=proc, peer_pid=lambda s: 32)


def test_agent_path_passes_on_one_verified_run() -> None:
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        good = [*({"check": c, "ok": True, "evidence": ""} for c in AGENT_CHECKS), {"done": 0}]
        tmux.on_paste.append(lambda: run_probe(ctx, good))
        agent_selftest(root / "proc").agent_path(ctx)
        [prompt] = tmux.pasted
        assert " ".join(PROBE_ARGV) in prompt and "fake-session-token" not in prompt
        cfg = json.loads((ctx.layout.run(1) / "agent-probe.json").read_text())
        assert cfg["path"] == "agent" and "fake-session-token" not in json.dumps(cfg)
        assert not ctx.layout.probe_socket(1).exists()


def test_agent_path_fails_without_the_prompt() -> None:
    with short_dir() as root:
        ctx = agent_context(root, StubTmux(screen="loading"))
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        clock = iter(range(1_800_000_000, 1_800_001_000))
        st = OpenShellSelfTest(wall=lambda: float(next(clock)), sleep=lambda s: None, proc=root / "proc",
                               peer_pid=lambda s: 32)
        with pytest.raises(SelfTestFailed, match="agent-prompt"):
            st.agent_path(ctx)


def test_agent_path_fails_when_the_agent_never_runs_the_probe() -> None:
    with short_dir() as root:
        ctx = agent_context(root, StubTmux())
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        clock = iter(range(1_800_000_000, 1_800_010_000))
        st = OpenShellSelfTest(wall=lambda: float(next(clock)), sleep=lambda s: None, proc=root / "proc",
                               peer_pid=lambda s: 32)
        with pytest.raises(SelfTestFailed, match="agent-path-channel: no verified probe run"):
            st.agent_path(ctx)
```

(Add `import dataclasses`, `import time` and `from collections.abc import Callable` to that file's imports.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_platform.py tests/test_sandbox_channel.py tests/test_sandbox_openshell_selftest.py -q`
Expected: FAIL with `ImportError: cannot import name 'peer_pid_checked'` and `No module named 'heterodyne.sandbox.channel'`.

- [ ] **Step 3: Add `peer_pid_checked`**

Append to `src/heterodyne/platform.py`:

```python
def peer_pid_checked(sock: socket.socket) -> int:
    """The host PID of the process at the other end of a connected Unix socket, by SO_PEERCRED, for a
    decision (the agent-path probe channel). Raises OSError on any failure and off Linux: there is no
    fallback, and the caller treats a failure as an unverified peer."""
    if not sys.platform.startswith("linux"):
        raise OSError("peer credentials need Linux")
    try:
        cred = sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i"))
        pid = int(struct.unpack("3i", cred)[0])
    except struct.error:
        raise OSError("peer credentials unreadable") from None
    if pid <= 0:
        raise OSError("no peer process")
    return pid
```

- [ ] **Step 4: Write `channel.py`**

`src/heterodyne/sandbox/channel.py`:

```python
"""The agent-path result channel (ADR 0001 §7 launch self-test, S5): run/p.sock, /run/hz/p.sock inside.

A result counts only from a peer the host verifies itself, from outside: its host PID by SO_PEERCRED,
and /proc showing the image's python running the read-only probes as PROBE_ARGV, untraced, in the
workload container's netns, inside the environment allowlist, with the CLI binary as an ancestor inside
that netns. Any other peer is rejected, and a rejected peer alone fails the gate.
"""

import json
import os
import socket
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from heterodyne.platform import peer_pid_checked
from heterodyne.sandbox.openshell_selftest import PROBE_ARGV, PROBE_EXE

MAX_LINE = 64 << 10
MAX_PEERS = 16


class ProcVerifier:
    def __init__(self, netns: str, cli_binary: str, allowed_env: frozenset[str], *,
                 proc: Path = Path("/proc")) -> None:
        self.netns = netns
        self.cli = cli_binary
        self.allowed = allowed_env
        self.proc = proc

    def _ppid(self, pid: int) -> int:
        status = (self.proc / str(pid) / "status").read_text()
        return int(status.split("\nPPid:\t", 1)[1].split("\n", 1)[0])

    def __call__(self, pid: int) -> str:
        p = self.proc / str(pid)
        try:
            exe = os.readlink(p / "exe")
            argv = tuple(a.decode("utf-8", "replace") for a in (p / "cmdline").read_bytes().split(b"\0")[:-1])
            status = (p / "status").read_text()
            netns = os.readlink(p / "ns" / "net")
            env = {e.split(b"=", 1)[0].decode("utf-8", "replace")
                   for e in (p / "environ").read_bytes().split(b"\0") if e}
        except OSError as exc:
            return f"/proc/{pid} unreadable ({exc.strerror})"
        if exe != PROBE_EXE or argv != PROBE_ARGV:
            return "not the probe"
        if "TracerPid:\t0\n" not in status:
            return "traced"
        if netns != self.netns:
            return "netns is not the workload container's"
        if env - self.allowed:
            return f"environment beyond the allowlist: {' '.join(sorted(env - self.allowed))}"
        q = pid
        while q > 1:
            try:
                q = self._ppid(q)
                if os.readlink(self.proc / str(q) / "ns" / "net") != self.netns:
                    break
                if os.readlink(self.proc / str(q) / "exe") == self.cli:
                    return ""
            except (OSError, IndexError, ValueError):
                break
        return f"no {self.cli} ancestor inside the workload netns"


@dataclass
class _Run:
    pid: int
    checks: dict[str, bool] = field(default_factory=dict[str, bool])
    done: int | None = None
    changed: bool = False
    malformed: bool = False


class ProbeChannel:
    def __init__(self, path: Path, verify: Callable[[int], str], *,
                 peer_pid: Callable[[socket.socket], int] = peer_pid_checked) -> None:
        self.path = path
        self.verify = verify
        self.peer_pid = peer_pid
        self.runs: list[_Run] = []
        self.rejected: list[str] = []          # reasons, kept for the audit
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self.path.unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(self.path))
        # As the session socket: the run directory (0700) keeps other host users out, and the sandbox's
        # user may map to another uid inside (S5 used the same mode).
        self.path.chmod(0o777)
        sock.listen(MAX_PEERS)
        self._sock = sock
        threading.Thread(target=self._accept, args=(sock,), daemon=True).start()

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()
        self.path.unlink(missing_ok=True)

    def done(self) -> bool:
        with self._lock:
            return any(r.done is not None or r.changed or r.malformed for r in self.runs)

    def verdict(self, expected: Sequence[str]) -> str:
        with self._lock:
            if self.rejected:
                return "a peer that is not the probe connected"
            if not self.runs:
                return "no verified probe run"
            if len(self.runs) > 1:
                return "more than one probe run"
            run = self.runs[0]
            if run.malformed:
                return "malformed result"
            if run.changed:
                return "the probe changed before it finished"
            failed = [c for c in expected if run.checks.get(c) is False]
            missing = [c for c in expected if c not in run.checks]
            if failed:
                return f"failed checks: {', '.join(failed)}"
            if missing:
                return f"missing checks: {', '.join(missing)}"
            if run.done != 0:
                return "the probe did not finish cleanly"
            return ""

    def _accept(self, sock: socket.socket) -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            try:
                pid = self.peer_pid(conn)
            except OSError:
                with self._lock:
                    self.rejected.append("peer credentials unreadable")
                return
            why = self.verify(pid)
            if why:
                with self._lock:
                    self.rejected.append(why)
                return
            run = _Run(pid)
            with self._lock:
                self.runs.append(run)
            with conn.makefile("rb") as stream:
                for line in stream:
                    if not self._record(run, pid, line):
                        return

    def _record(self, run: _Run, pid: int, line: bytes) -> bool:
        try:
            if len(line) > MAX_LINE:
                raise ValueError(line[:16])
            msg: Any = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError(type(msg))
        except ValueError:
            with self._lock:
                run.malformed = True
            return False
        if "done" in msg:
            again = self.verify(pid)          # the same verified process, still untraced, at completion
            with self._lock:
                run.changed = bool(again)
                run.done = msg["done"] if isinstance(msg["done"], int) else -1
            return False
        check, ok = msg.get("check"), msg.get("ok")
        with self._lock:
            if isinstance(check, str) and isinstance(ok, bool):
                run.checks[check] = ok and run.checks.get(check, True)
            else:
                run.malformed = True
        return not run.malformed
```

`_ppid` splits on `"\nPPid:\t"` (with the newline) so that it can't match inside another field; `/proc` always puts `Name` first, as the tests' `status` does.

- [ ] **Step 5: Replace the `agent_path` stub**

In `src/heterodyne/sandbox/openshell_selftest.py`, add the imports:

```python
import socket
from pathlib import Path

from heterodyne.agents.base import write_private
from heterodyne.platform import peer_pid_checked
from heterodyne.sandbox.spec import AGENT_PROBE_INSIDE
```

add the constants:

```python
PROMPT_SECONDS = 90
LATE_PEER_SECONDS = 5
POLL_SECONDS = 0.5
AGENT_OUTPUT = "$HOME/.hz-agent-probe.out"
```

extend `__init__`:

```python
    def __init__(self, *, wall: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 proc: Path = Path("/proc"),
                 peer_pid: Callable[[socket.socket], int] = peer_pid_checked) -> None:
        self.wall = wall
        self.sleep = sleep
        self.proc = proc
        self.peer_pid = peer_pid
```

add the helper:

```python
    def _until(self, pred: Callable[[], bool], seconds: float) -> bool:
        end = self.wall() + seconds
        while not pred():
            if self.wall() >= end:
                return False
            self.sleep(POLL_SECONDS)
        return True
```

and replace the stub with:

```python
    def agent_path(self, ctx: ProbeContext) -> None:
        from heterodyne.sandbox.channel import ProbeChannel, ProcVerifier   # channel imports this module

        self._canary(ctx)
        run = ctx.layout.run(ctx.generation)
        write_private(run / AGENT_PROBE_INSIDE.name, json.dumps(probe_config(ctx, "agent")))
        marker = ctx.adapter.prompt_marker
        if not self._until(lambda: marker in ctx.tmux.capture(ctx.tmux_session, 50), PROMPT_SECONDS):
            raise SelfTestFailed("agent-prompt")
        try:
            netns = os.readlink(self.proc / str(ctx.backend.workload_pid(ctx.spec.name)) / "ns" / "net")
        except OSError:
            raise SelfTestFailed("workload-netns") from None
        verify = ProcVerifier(netns, str(ctx.cli.binary), env_allowed(ctx.adapter, "agent"), proc=self.proc)
        channel = ProbeChannel(ctx.layout.probe_socket(ctx.generation), verify, peer_pid=self.peer_pid)
        channel.start()
        try:
            since = self.wall()
            out = f'"{AGENT_OUTPUT}"'
            command = f'{" ".join(PROBE_ARGV)} > {out} 2>&1; echo "probes-rc=$?" >> {out}'
            ctx.tmux.paste(ctx.tmux_session,
                           f"Run exactly this shell command, then reply with only its exit status: {command}")
            self._until(channel.done, ctx.settings.agent_probe_seconds)
            self.sleep(LATE_PEER_SECONDS)     # late peers (a forger after the probe) are still counted
            reason = channel.verdict(AGENT_CHECKS)
        finally:
            channel.close()
        if reason:
            raise SelfTestFailed(f"agent-path-channel: {reason}")
        self._logs(ctx, since, "agent")
```

`channel.py` imports `PROBE_ARGV`/`PROBE_EXE` from this module, so the import in `agent_path` is local to break the cycle. `AGENT_OUTPUT` is the agent's own file in the synthetic home. Nothing reads it: it is there so the prompt has a well-formed command whose result the agent can report.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `timeout 300 uv run pytest tests/test_platform.py tests/test_sandbox_channel.py tests/test_sandbox_openshell_selftest.py -q && timeout 300 uv run pyright src/heterodyne/sandbox src/heterodyne/platform.py && timeout 300 uv run ruff check src tests`
Expected: PASS; clean.

- [ ] **Step 7: Commit**

```bash
git add src/heterodyne/platform.py src/heterodyne/sandbox/channel.py src/heterodyne/sandbox/openshell_selftest.py \
  tests/test_platform.py tests/test_sandbox_channel.py tests/test_sandbox_openshell_selftest.py
git commit -m "plan4 T9: agent-path self-test with a host-verified probe channel"
```

### Task 10: The sandbox runtime

**Files:**
- Create: `src/heterodyne/sandbox/selftest.py`, `src/heterodyne/sandbox/runtime.py`, `tests/fakes/fake_backend.py`, `tests/fakes/scripted_selftest.py`
- Modify: `src/heterodyne/tmux.py` (add `pane_info`), `tests/sandbox_env.py` (append the runtime rig)
- Test: `tests/test_sandbox_runtime.py`, `tests/test_tmux.py` (one test added)

**Interfaces:**
- Consumes:
  - plan 3's `wsd.runtime` (`LaunchSpec`, `Started`, `Session`, `Liveness`, `RuntimeUnavailable`, `LaunchFailed`, `LaunchUncertain`);
  - `wsd.accounts.Accounts` (`adapter`, `configured`, `login_paths`);
  - Task 1's `SandboxSettings`, `WsSandbox`;
  - Task 2's `SessionLayout`, `SpecInput`, `build_spec`, `check_credentials`, `Protected`, `SpecRefused`, `sandbox_name`, `short_id`, `NAME`, `BRIDGE_INSIDE`, `REQUEST_INSIDE`;
  - Task 3's `Backend`, `BackendError`, `BackendUnavailable`;
  - Task 5's `SessionServer`;
  - Task 6's `shim` (`TOKEN_FILE`, `CONFIG_FILE`, `REQUEST_WRAPPER`);
  - Task 7's `Adapter`, `AdapterError`, `Cli`, `write_private`, `ADAPTERS`.
- Produces:
  - `Tmux.pane_info(name) -> tuple[str, int]` (pane ID, pane PID), raising `TmuxError`.
  - `heterodyne.sandbox.selftest`:
    - `SelfTestFailed(Exception)`, whose text names the failed check;
    - `ProbeContext` (frozen dataclass): `backend`, `tmux`, `tmux_session`, `spec`, `layout`, `generation`, `adapter`, `cli`, `token`, `others: tuple[tuple[str, Path], ...]`, `real_home_canary`, `wsd_socket`, `settings`, `turns: Callable[[], TurnState]`;
    - `SelfTest` (Protocol): `files() -> Mapping[str, str]`, `exec_path(ctx)`, `agent_path(ctx)`.
  - `heterodyne.sandbox.runtime`:
    - `Phase` (StrEnum): `creating`, `testing`, `starting`, `running`, `stopping`, `ended`;
    - `SessionRecord` (msgspec Struct): `key`, `ws`, `bead`, `role`, `profile`, `adapter`, `generation`, `phase`, `sandbox`, `tmux_session`, `ran`, `native_id`, `worktree`, `started_at`, `deadline`, `error`;
    - `read_record(layout) -> SessionRecord | None`, `write_record(layout, rec)`;
    - `RuntimeConfig` (frozen dataclass): `sessions`, `settings`, `accounts`, `real_home`, `real_home_canary`, `wsd_socket`, `uid`, `gid`, `tmux`, `path`, `clock`, `wait_clock`, `sleep`, `adapters`;
    - `SandboxRuntime(config, backend, selftest)`, implementing `AgentRuntime`, with `tmux_name(key) -> str` and `layout(key) -> SessionLayout`; Task 11 adds `expire`.
  - `tests/fakes/fake_backend.py`: `FakeBackend`. `tests/fakes/scripted_selftest.py`: `ScriptedSelfTest`.
  - In `tests/sandbox_env.py`: `RuntimeRig` and `runtime_rig(root, tmux, clock) -> RuntimeRig`, `launch_spec(rig, profile, bead="btq-1", **changes) -> LaunchSpec`.

This is where the launch sequence of the Architecture section lives. The rules it keeps:

- **The record comes first.** `session.json` is written (atomically, 0600) before each step it describes. A record in any phase but `running` or `ended` is a launch that stopped part way, by an error or a dead wsd, and its sandbox and pane are cleaned up before anything else uses the key. `ran` is set when a generation reaches `running` and never cleared.
- **The resume contract (D9, refined).** The native ID to look for is the spec's, or else the record's when a generation ran.
  - If the home holds that ID's state, the session resumes it.
  - It starts fresh if there is no record, or no generation ever reached `running`, or the home holds no state for any session. A generation that never reached `running` was a launch that failed, or was cut off, before wsd got its receipt; the only turn it held was the self-test's prompt.
  - Anything else is `RuntimeUnavailable`.
  - A fresh Codex start passes no ID (Codex assigns the thread); a fresh Claude start passes the spec's.
- **Failure.** Any exception after the first record write deletes the sandbox, kills the pane, closes the socket and confirms the end: then `LaunchFailed` with a fixed reason. If the end can't be confirmed, it is `LaunchUncertain`, and the record stays `stopping` so `sessions` keeps the key listed as `unknown`. Known failures keep their own fixed text; anything else becomes "launch step failed (<type>)".
- **`sessions`** fails closed. A backend that can't list, an unreadable record, or a sandbox named like ours (`hz…g…`) whose short ID has no record is `RuntimeUnavailable`. A running record is live only if its sandbox is listed and its pane is alive; it then gets its socket re-bound if this wsd hasn't one (D19). Any other non-ended record is cleaned up; one that can't be is listed `unknown`.
- **`stop`** interrupts (Escape), kills the pane and deletes every listed sandbox of the key, then records `ended`. It is idempotent, and `RuntimeUnavailable` if the end can't be confirmed.

`FakeBackend` runs each sandbox's commands on the host, so the offline tests exercise the real adapters, shim, socket and tmux. It maps every bind whose target differs from its source (`/run/hz`, `/run/hz-bridge`, the Codex daemon directory) back to its source in argv and the environment, and passes the map as `HZ_FAKE_PATHS` for the fake CLI's hook commands. `ScriptedSelfTest` stands in for the OpenShell self-test. Its agent path does what the real one needs of the agent: it waits for the prompt, submits one line and waits for the `Stop`, so Codex reports its thread ID.

- [ ] **Step 1: Add `pane_info` (test first)**

Append to `tests/test_tmux.py`:

```python
def test_pane_info_reports_the_pane_id_and_pid(tmp_path: Path) -> None:
    t = new_test_tmux()
    try:
        t.new_session("pi", tmp_path, ["sh", "-c", "sleep 30"])
        pane, pid = t.pane_info("pi")
        assert pane.startswith("%") and pid > 1
        t.kill("pi")
        with pytest.raises(TmuxError):
            t.pane_info("pi")
    finally:
        t.kill_server()
```

(Use the imports the file already has; add `TmuxError` to its `heterodyne.tmux` import if missing.)

Run: `timeout 300 uv run pytest tests/test_tmux.py -q -k pane_info`
Expected: FAIL with `AttributeError: 'GuardedTmux' object has no attribute 'pane_info'`.

Add to `Tmux` in `src/heterodyne/tmux.py`, after `pane_dead`:

```python
    def pane_info(self, name: str) -> tuple[str, int]:
        """The ID (`%N`) and process PID of the session's pane. Raises TmuxError if there is none."""
        proc = self._run("display-message", "-p", "-t", f"={name}:", "#{pane_id} #{pane_pid}")
        pane, _, pid = proc.stdout.decode("utf-8", "replace").strip().partition(" ")
        if not re.fullmatch(r"%[0-9]+", pane) or not pid.isdigit():
            raise TmuxError(f"tmux reported no pane for {name!r}")
        return pane, int(pid)
```

Run: `timeout 300 uv run pytest tests/test_tmux.py -q -k pane_info`
Expected: PASS.

- [ ] **Step 2: Write `selftest.py`**

`src/heterodyne/sandbox/selftest.py`:

```python
"""The launch self-test's contract (ADR 0001 §7). A backend's self-test runs the probes on two paths:
by a separate exec (`exec_path`, before the agent starts) and by the agent through its own tool
(`agent_path`, after its pane starts, before normal work). Either raises SelfTestFailed naming the check,
and the runtime then deletes the sandbox and fails the launch."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from heterodyne.agents.base import Adapter, Cli
from heterodyne.sandbox.backend import Backend
from heterodyne.sandbox.settings import SandboxSettings
from heterodyne.sandbox.spec import SandboxSpec, SessionLayout
from heterodyne.session.server import TurnState
from heterodyne.tmux import Tmux


class SelfTestFailed(Exception):
    """A self-test check failed. The text names the check (fixed wording, no paths or output)."""


@dataclass(frozen=True)
class ProbeContext:
    backend: Backend
    tmux: Tmux
    tmux_session: str
    spec: SandboxSpec
    layout: SessionLayout
    generation: int
    adapter: Adapter
    cli: Cli
    token: str                                   # never sent to the sandbox by the self-test
    others: tuple[tuple[str, Path], ...]         # (account, login file as configured), other accounts
    real_home_canary: Path
    wsd_socket: Path
    settings: SandboxSettings
    turns: Callable[[], TurnState]


class SelfTest(Protocol):
    def files(self) -> Mapping[str, str]:
        """File name -> text, written into each run directory (read-only at /run/hz inside)."""
        ...

    def exec_path(self, ctx: ProbeContext) -> None: ...

    def agent_path(self, ctx: ProbeContext) -> None: ...
```

- [ ] **Step 3: Write the fakes and the rig**

`tests/fakes/fake_backend.py`:

```python
"""A sandbox backend for offline tests: each 'sandbox' is a record, and its commands run on the host.

Paths a spec binds somewhere else inside (/run/hz, /run/hz-bridge, the Codex daemon directory) are mapped
back to their host sources in argv and in the environment, and the map is exported as HZ_FAKE_PATHS
for the fake CLI's hook commands. Login binds are not mapped: nothing in the fake reads them. The test's
Python directory leads PATH, so the hook command's `python3` is the test interpreter."""

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from heterodyne.sandbox.backend import BackendError, BackendUnavailable, ExecResult
from heterodyne.sandbox.spec import SandboxSpec


@dataclass
class _Box:
    spec: SandboxSpec
    paths: dict[str, str]


@dataclass
class FakeBackend:
    up: bool = True
    boxes: dict[str, _Box] = field(default_factory=dict[str, _Box])
    extra: set[str] = field(default_factory=set[str])         # listed names the runtime didn't create
    create_failures: int = 0
    delete_unconfirmed: int = 0
    list_failures: int = 0
    created: list[str] = field(default_factory=list[str])
    deleted: list[str] = field(default_factory=list[str])
    execs: list[list[str]] = field(default_factory=list[list[str]])

    def available(self) -> bool:
        return self.up

    def names(self) -> set[str]:
        if not self.up or self.list_failures:
            self.list_failures = max(0, self.list_failures - 1)
            raise BackendUnavailable("the fake backend is down")
        return set(self.boxes) | self.extra

    def create(self, spec: SandboxSpec, scratch: Path) -> None:
        if not self.up:
            raise BackendUnavailable("the fake backend is down")
        paths = {str(b.target): str(b.source) for b in spec.binds if b.target != b.source}
        self.boxes[spec.name] = _Box(spec, paths)
        self.created.append(spec.name)
        if self.create_failures:
            self.create_failures -= 1
            raise BackendError("sandbox create failed")

    def _host(self, box: _Box, text: str) -> str:
        if not box.paths:
            return text
        pattern = "|".join(re.escape(p) for p in sorted(box.paths, key=len, reverse=True))
        return re.sub(pattern, lambda m: box.paths[m.group(0)], text)

    def _env(self, box: _Box) -> dict[str, str]:
        env = {k: self._host(box, v) for k, v in box.spec.env.items()}
        env["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), env.get("PATH", "")))
        env["HZ_FAKE_PATHS"] = json.dumps(box.paths)
        return env

    def _box(self, name: str) -> _Box:
        box = self.boxes.get(name)
        if box is None or not self.up:
            raise BackendError("no such sandbox")
        return box

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        box = self._box(name)
        host = [self._host(box, a) for a in argv]
        self.execs.append(host)
        try:
            proc = subprocess.run(host, cwd=workdir, env=self._env(box), input=input or b"",
                                  capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise BackendError("sandbox exec timed out") from None
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def tty_argv(self, name: str, workdir: Path, argv: Sequence[str]) -> list[str]:
        box = self._box(name)
        env = [f"{k}={v}" for k, v in self._env(box).items()]
        return ["env", "-i", *env, "sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", str(workdir),
                *(self._host(box, a) for a in argv)]

    def delete(self, name: str) -> bool:
        if not self.up:
            return False
        if self.delete_unconfirmed:
            self.delete_unconfirmed -= 1
            return False
        box = self.boxes.pop(name, None)
        self.extra.discard(name)
        self.deleted.append(name)
        if box is not None:             # ends the fake app-server, which runs until its socket goes
            for folder in (Path(source) for source in box.paths.values()):
                if folder.name in ("bridge", "daemon") and folder.is_dir():
                    for entry in folder.iterdir():
                        if entry.is_socket():
                            entry.unlink()
        return True

    def logs(self, name: str, since: float) -> list[str]:
        return []

    def network_mode(self, name: str) -> str:
        return "none"

    def workload_pid(self, name: str) -> int:
        return os.getpid()

    def pane_env(self) -> Mapping[str, str]:
        return {}
```

The `delete` loop removes only the sockets in the bridge directory and its `daemon` subdirectory: the fake app-server's.

`tests/fakes/scripted_selftest.py`:

```python
"""A stand-in for the OpenShell self-test. Its exec path records and can be made to fail; its agent path
does what the real one needs of the agent (waits for the prompt, submits one line, waits for its Stop),
so Codex fires SessionStart and reports its thread ID, as with the real probe prompt."""

import time
from collections.abc import Callable, Mapping

from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed

WAIT_SECONDS = 20


def _wait(pred: Callable[[], bool]) -> None:
    end = time.monotonic() + WAIT_SECONDS
    while not pred():
        if time.monotonic() > end:
            raise SelfTestFailed("scripted-agent-path: no reply")
        time.sleep(0.02)


class ScriptedSelfTest:
    def __init__(self) -> None:
        self.exec_fail = ""
        self.agent_fail = ""
        self.exec_runs: list[str] = []
        self.agent_runs: list[str] = []

    def files(self) -> Mapping[str, str]:
        return {"probes.py": "# scripted self-test: no probes\n"}

    def exec_path(self, ctx: ProbeContext) -> None:
        self.exec_runs.append(ctx.spec.name)
        if self.exec_fail:
            raise SelfTestFailed(self.exec_fail)

    def agent_path(self, ctx: ProbeContext) -> None:
        self.agent_runs.append(ctx.spec.name)
        if self.agent_fail:
            raise SelfTestFailed(self.agent_fail)
        _wait(lambda: ctx.adapter.prompt_marker in ctx.tmux.capture(ctx.tmux_session, 50))
        before = ctx.turns().stops
        ctx.tmux.paste(ctx.tmux_session, "self-test")
        _wait(lambda: ctx.turns().stops > before)
```

`ScriptedSelfTest` keeps its own bounded wait so that it doesn't import `sandbox_env`, which imports it.

Append to `tests/sandbox_env.py` (add `import os`, `from dataclasses import dataclass` and the imports below to its import block):

```python
from fakes.fake_backend import FakeBackend
from fakes.scripted_selftest import ScriptedSelfTest
from wsd_env import Clock, accounts_at, git_repo

from heterodyne.sandbox.runtime import RuntimeConfig, SandboxRuntime
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.tmux import Tmux
from heterodyne.wsd import ids
from heterodyne.wsd.runtime import LaunchSpec


@dataclass
class RuntimeRig:
    root: Path
    runtime: SandboxRuntime
    backend: FakeBackend
    selftest: ScriptedSelfTest
    tmux: Tmux
    clock: Clock
    worktree: Path
    home: Path


def runtime_rig(root: Path, tmux: Tmux, clock: Clock, host: str = "") -> RuntimeRig:
    """A SandboxRuntime on the fake backend, the scripted self-test and a guarded tmux, with both fake
    CLIs at their pins and default logins valid for a day. `root` must be short (short_dir)."""
    env = config_env(root / "c", host)
    home = Path(env["HOME"])
    fresh_logins(home, clock() + 86_400)
    claude, codex = install_fake_cli(root / "i", "claude-code"), install_fake_cli(root / "i", "codex")
    backend, selftest = FakeBackend(), ScriptedSelfTest()
    config = RuntimeConfig(
        sessions=root / "s", settings=sandbox_settings(env), accounts=accounts_at(home), real_home=home,
        real_home_canary=root / "canary", wsd_socket=root / "ctl.sock", uid=os.getuid(), gid=os.getgid(),
        tmux=tmux, path=os.pathsep.join((str(claude.parent), str(codex.parent))), clock=clock)
    runtime = SandboxRuntime(config, backend, selftest)
    return RuntimeRig(root, runtime, backend, selftest, tmux, clock, git_repo(root / "w"), home)


def launch_spec(rig: RuntimeRig, profile: str, bead: str = "btq-1", *, account: str = "default",
                resume: bool = False) -> LaunchSpec:
    """A first launch, as plan 3's guard builds it: Claude's native ID is the session key, Codex's is
    unknown until its thread starts."""
    key = ids.role_session(bead, "coder", profile)
    native = key if profile == "p-one" else None
    return LaunchSpec("alpha", bead, "coder", profile, key, f"{bead} · coder", rig.worktree, resume=resume,
                      native_id=native, account=account)
```

- [ ] **Step 4: Write the failing runtime tests**

`tests/test_sandbox_runtime.py`:

```python
import contextlib
import json
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from sandbox_env import RuntimeRig, launch_spec, runtime_rig, short_dir, wait_for
from tmux_guard import new_test_tmux
from wsd_env import Clock

from heterodyne.agents.base import HOOK_EVENTS
from heterodyne.sandbox.runtime import Phase, SessionRecord, read_record, write_record
from heterodyne.sandbox.spec import sandbox_name
from heterodyne.wsd.runtime import LaunchFailed, LaunchUncertain, Liveness, RuntimeUnavailable

needs_tools = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                 reason="tmux and setsid are needed")
pytestmark = needs_tools


@pytest.fixture
def rig() -> Iterator[RuntimeRig]:
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = runtime_rig(root, tmux, Clock())
        try:
            yield rig
        finally:
            rig.backend.delete_unconfirmed = 0
            for key in list(rig.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    rig.runtime.stop(key)
            tmux.kill_server()


def record(rig: RuntimeRig, key: str) -> SessionRecord:
    rec = read_record(rig.runtime.layout(key))
    assert rec is not None
    return rec


def test_a_claude_launch_runs_and_is_listed(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    started = rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.ran, rec.generation, rec.native_id) == (Phase.RUNNING, True, 1, spec.session_key)
    assert started.native_id == spec.session_key and started.tmux_session == rec.tmux_session
    assert started.tmux_pane.startswith("%") and started.pane_pid > 1
    assert rec.deadline == rec.started_at + rig.runtime.c.settings.max_lifetime_seconds
    assert rig.selftest.exec_runs == rig.selftest.agent_runs == [rec.sandbox]
    [session] = rig.runtime.sessions("alpha")
    assert (session.key, session.bead, session.liveness) == (spec.session_key, "btq-1", Liveness.LIVE)
    assert rig.runtime.sessions("beta") == []
    run = rig.runtime.layout(spec.session_key).run(1)
    assert {p.name for p in run.iterdir()} >= {"token", "shim.py", "shim.json", "ws-request", "probes.py",
                                               "claude-settings.json", "s.sock"}
    assert all(p.stat().st_mode & 0o077 == 0 for p in run.iterdir() if p.name != "s.sock")


def test_a_codex_launch_reports_its_thread_and_trusts_its_hooks(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    started = rig.runtime.launch(spec)
    assert started.native_id is not None and started.native_id == record(rig, spec.session_key).native_id
    config = (rig.runtime.layout(spec.session_key).home / ".codex" / "config.toml").read_text()
    assert config.count("trusted_hash") == len(HOOK_EVENTS)
    trust_runs = [argv for argv in rig.backend.execs if any(a.endswith("codex_trust.py") for a in argv)]
    assert len(trust_runs) == 2                         # seed, then confirm nothing is left untrusted


def test_launch_is_idempotent_on_a_live_session(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    assert rig.runtime.launch(spec) == first
    assert len(rig.backend.created) == 1


def test_stop_ends_the_session_and_is_idempotent(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.runtime.stop(spec.session_key)
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert not rig.tmux.has_session(rec.tmux_session) and rec.sandbox in rig.backend.deleted
    assert rig.runtime.sessions("alpha") == []
    rig.runtime.stop(spec.session_key)
    rig.runtime.stop("never-launched")


def test_an_unconfirmed_stop_is_unavailable_and_stays_listed(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.backend.delete_unconfirmed = 2
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.stop(spec.session_key)
    [session] = rig.runtime.sessions("alpha")
    assert session.liveness is Liveness.UNKNOWN
    assert rig.runtime.sessions("alpha") == []          # the next cleanup is confirmed


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_a_relaunch_resumes_the_same_native_session(rig: RuntimeRig, profile: str) -> None:
    spec = launch_spec(rig, profile)
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    second = rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert second.native_id == first.native_id
    assert record(rig, spec.session_key).sandbox.endswith("g2")


def test_a_resume_that_holds_no_record_starts_fresh(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one", resume=True)
    assert rig.runtime.launch(spec).native_id == spec.session_key


def test_state_that_ran_but_is_gone_is_unavailable(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    first = rig.runtime.launch(spec)
    rig.runtime.stop(spec.session_key)
    sessions = rig.runtime.layout(spec.session_key).home / ".codex" / "sessions"
    for rollout in sessions.rglob("rollout-*.jsonl"):
        rollout.rename(rollout.with_name("rollout-other.jsonl"))       # some state, but not this thread's
    with pytest.raises(RuntimeUnavailable, match="state"):
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))


def test_a_named_account_is_refused(rig: RuntimeRig) -> None:
    with pytest.raises(LaunchFailed, match="AU-6"):
        rig.runtime.launch(launch_spec(rig, "p-one", account="work"))
    assert rig.backend.created == []


def test_the_cli_pin_refuses_another_version(rig: RuntimeRig) -> None:
    package = rig.root / "i" / "cli" / "claude" / "package.json"
    package.write_text(json.dumps({"version": "9.9.9"}))
    with pytest.raises(LaunchFailed, match="pinned version"):
        rig.runtime.launch(launch_spec(rig, "p-one"))
    assert rig.backend.created == []


def test_the_freshness_gate_refuses_a_login_that_expires_too_soon(rig: RuntimeRig) -> None:
    s = rig.runtime.c.settings
    rig.clock.advance(86_400 - (s.max_lifetime_seconds + s.stop_margin_seconds))
    with pytest.raises(LaunchFailed, match="expires"):
        rig.runtime.launch(launch_spec(rig, "p-two"))
    assert rig.backend.created == []


@pytest.mark.parametrize("fail", ["exec", "agent", "create"])
def test_a_failed_step_deletes_the_sandbox(rig: RuntimeRig, fail: str) -> None:
    if fail == "exec":
        rig.selftest.exec_fail = "direct-network-blocked"
    elif fail == "agent":
        rig.selftest.agent_fail = "agent-path-channel: no verified probe run"
    else:
        rig.backend.create_failures = 1
    spec = launch_spec(rig, "p-two")
    with pytest.raises(LaunchFailed) as exc:
        rig.runtime.launch(spec)
    assert {"exec": "direct-network-blocked", "agent": "no verified probe run",
            "create": "sandbox create failed"}[fail] in str(exc.value)
    rec = record(rig, spec.session_key)
    assert rec.phase is Phase.ENDED and rec.error == str(exc.value) and not rec.ran
    assert rig.backend.boxes == {} and not rig.tmux.has_session(rec.tmux_session)
    assert not rig.runtime.layout(spec.session_key).socket(1).exists()
    assert rig.runtime.sessions("alpha") == []


def test_a_failed_step_whose_cleanup_is_unconfirmed_is_uncertain(rig: RuntimeRig) -> None:
    rig.selftest.exec_fail = "outer-fence-network-none"
    rig.backend.delete_unconfirmed = 2                 # the launch's cleanup, then the next listing's
    spec = launch_spec(rig, "p-one")
    with pytest.raises(LaunchUncertain):
        rig.runtime.launch(spec)
    assert record(rig, spec.session_key).phase is Phase.STOPPING
    [session] = rig.runtime.sessions("alpha")
    assert session.liveness is Liveness.UNKNOWN


def test_a_dead_pane_is_cleaned_up(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.tmux.send_key(rec.tmux_session, "C-d")                   # the fake CLI exits at end of input
    wait_for(lambda: rig.tmux.pane_dead(rec.tmux_session))
    assert rig.runtime.sessions("alpha") == []
    assert record(rig, spec.session_key).phase is Phase.ENDED and rig.backend.boxes == {}


def test_a_launch_a_dead_wsd_left_behind_is_cleaned_up_first(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    layout = rig.runtime.layout(spec.session_key)
    stale = SessionRecord(key=spec.session_key, ws="alpha", bead="btq-1", role="coder", profile="p-one",
                          adapter="claude-code", generation=1, phase=Phase.TESTING,
                          sandbox=sandbox_name(spec.session_key, 1),
                          tmux_session=rig.runtime.tmux_name(spec.session_key), worktree=str(rig.worktree))
    write_record(layout, stale)
    rig.backend.extra.add(stale.sandbox)
    assert rig.runtime.sessions("alpha") == []
    assert stale.sandbox in rig.backend.deleted


def test_an_unattributable_sandbox_holds_every_workstream(rig: RuntimeRig) -> None:
    rig.backend.extra.add("hz00000000abcdg1")
    with pytest.raises(RuntimeUnavailable, match="no session record"):
        rig.runtime.sessions("alpha")
    rig.backend.extra = {"someone-elses"}
    assert rig.runtime.sessions("alpha") == []


def test_an_unlistable_backend_or_record_is_unavailable(rig: RuntimeRig) -> None:
    rig.backend.list_failures = 1
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.sessions("alpha")
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.runtime.layout(spec.session_key).record.write_text("{not json")
    with pytest.raises(RuntimeUnavailable, match="record"):
        rig.runtime.sessions("alpha")


def test_a_restarted_runtime_rebinds_the_live_session(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    old = rig.runtime.servers.pop(spec.session_key)
    old.close()                                                   # wsd died: the socket went with it
    [session] = rig.runtime.sessions("alpha")
    assert session.liveness is Liveness.LIVE
    server = rig.runtime.servers[spec.session_key]
    rec = record(rig, spec.session_key)
    rig.tmux.paste(rec.tmux_session, "after the restart")
    wait_for(lambda: server.turns().stops == 1)
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_runtime.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.sandbox.runtime'`.

- [ ] **Step 6: Write `runtime.py`**

`src/heterodyne/sandbox/runtime.py`:

```python
"""SandboxRuntime: plan 3's AgentRuntime, each session in its own sandbox with its CLI in a pane of
wsd's tmux server (ADR 0001 §7 Runtime, §4.2, §10).

Every step is described by the session's record before it is taken, so a dead wsd's half-done launch is
found and cleaned up. Everything fails closed: what can't be listed, read or confirmed is
RuntimeUnavailable or LaunchUncertain, never assumed absent."""

import json
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import msgspec

from heterodyne.agents.base import Adapter, AdapterError, Cli, write_private
from heterodyne.agents.registry import ADAPTERS
from heterodyne.config import ConfigError
from heterodyne.sandbox.backend import Backend, BackendError, BackendUnavailable
from heterodyne.sandbox.selftest import ProbeContext, SelfTest, SelfTestFailed
from heterodyne.sandbox.settings import SandboxSettings, WsSandbox
from heterodyne.sandbox.spec import (
    BRIDGE_INSIDE,
    NAME,
    REQUEST_INSIDE,
    Protected,
    SandboxSpec,
    SessionLayout,
    SpecInput,
    SpecRefused,
    build_spec,
    check_credentials,
    sandbox_name,
    short_id,
)
from heterodyne.session import shim
from heterodyne.session.server import SessionServer
from heterodyne.tmux import Tmux, TmuxError
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.runtime import (
    LaunchFailed,
    LaunchSpec,
    LaunchUncertain,
    Liveness,
    RuntimeUnavailable,
    Session,
    Started,
)

DEFAULT_ACCOUNT = "default"
TRUST_SECONDS = 90
SERVER_SECONDS = 20
POLL_SECONDS = 0.1
TMUX_ERRORS = (TmuxError, OSError, subprocess.TimeoutExpired)
SHIM_SOURCE = Path(shim.__file__)


class Phase(StrEnum):
    CREATING = "creating"
    TESTING = "testing"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ENDED = "ended"


class SessionRecord(msgspec.Struct, frozen=True, kw_only=True):
    key: str
    ws: str
    bead: str
    role: str
    profile: str
    adapter: str
    generation: int
    phase: Phase
    sandbox: str
    tmux_session: str
    ran: bool = False                 # some generation reached `running`; never cleared
    native_id: str | None = None      # the latest running generation's
    worktree: str = ""
    started_at: int = 0
    deadline: int = 0
    error: str = ""


def read_record(layout: SessionLayout) -> SessionRecord | None:
    try:
        data = layout.record.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeUnavailable("a session record can't be read") from None
    try:
        return msgspec.json.decode(data, type=SessionRecord)
    except msgspec.MsgspecError:
        raise RuntimeUnavailable("a session record is unreadable") from None


def write_record(layout: SessionLayout, rec: SessionRecord) -> None:
    layout.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(layout.record, msgspec.json.encode(rec).decode())


class _StepFailed(Exception):
    """A launch step failed. Fixed wording."""


KNOWN_FAILURES = (SpecRefused, AdapterError, BackendError, _StepFailed, ConfigError)


@dataclass(frozen=True)
class RuntimeConfig:
    sessions: Path                    # <wsd state>/sessions
    settings: SandboxSettings
    accounts: Accounts
    real_home: Path
    real_home_canary: Path
    wsd_socket: Path                  # wsd's control socket: the probes check it is unreachable
    uid: int
    gid: int
    tmux: Tmux
    path: str                         # where CLI binaries are looked up
    clock: Callable[[], int]          # UTC epoch seconds: the scheduler's clock
    wait_clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    adapters: Mapping[str, Adapter] = field(default_factory=lambda: ADAPTERS)


class SandboxRuntime:
    def __init__(self, config: RuntimeConfig, backend: Backend, selftest: SelfTest) -> None:
        self.c = config
        self.backend = backend
        self.selftest = selftest
        self.servers: dict[str, SessionServer] = {}      # key -> its running generation's socket

    # --- names ---

    def layout(self, key: str) -> SessionLayout:
        return SessionLayout.at(self.c.sessions, key)

    def tmux_name(self, key: str) -> str:
        return f"wsd-{short_id(key)}"

    # --- AgentRuntime ---

    def available(self) -> bool:
        return self.backend.available()

    def sessions(self, ws: str) -> list[Session]:
        try:
            names = self.backend.names()
        except BackendUnavailable:
            raise RuntimeUnavailable("the sandbox backend can't list its sandboxes") from None
        records = self._records()
        known = {short_id(r.key) for r in records}
        if any(NAME.fullmatch(n) and n.rsplit("g", 1)[0] not in known for n in names):
            raise RuntimeUnavailable("a sandbox has no session record")
        listed: list[Session] = []
        for rec in records:
            if rec.ws != ws or rec.phase is Phase.ENDED:
                continue
            if rec.phase is Phase.RUNNING and self._alive(rec, names):
                self._rebind(rec)
                listed.append(Session(rec.key, rec.ws, rec.bead, rec.role, Liveness.LIVE))
            elif not self._end(rec, interrupt=False):
                listed.append(Session(rec.key, rec.ws, rec.bead, rec.role, Liveness.UNKNOWN))
        return listed

    def launch(self, spec: LaunchSpec) -> Started:
        adapter = self.c.adapters.get(self.c.accounts.adapter(spec.profile) or "")
        if adapter is None:
            raise LaunchFailed("the profile's adapter can't be run in a sandbox")
        if spec.account != DEFAULT_ACCOUNT:
            raise LaunchFailed("named accounts are bound by AU-6")
        layout = self.layout(spec.session_key)
        prev = read_record(layout)
        if prev is not None and prev.phase is not Phase.ENDED:
            if prev.phase is Phase.RUNNING and self._alive(prev, self._names()):
                return self._started(prev)              # idempotent: a live session is left alone
            if not self._end(prev, interrupt=False):
                raise RuntimeUnavailable("an earlier launch of this session could not be cleaned up")
            prev = read_record(layout)
        resume, native = self._resume(adapter, layout, prev, spec)
        cli = self._cli(adapter)
        protected, others = self._logins(adapter)
        self._fresh(adapter, protected.chosen_files)
        try:
            name = sandbox_name(spec.session_key, spec.generation)
        except SpecRefused as exc:
            raise LaunchFailed(str(exc)) from None
        rec = SessionRecord(key=spec.session_key, ws=spec.ws, bead=spec.bead, role=spec.role,
                            profile=spec.profile, adapter=adapter.name, generation=spec.generation,
                            phase=Phase.CREATING, sandbox=name, tmux_session=self.tmux_name(spec.session_key),
                            ran=prev.ran if prev else False, native_id=prev.native_id if prev else None,
                            worktree=str(spec.worktree))
        write_record(layout, rec)
        try:
            return self._start(spec, adapter, cli, layout, rec, resume, native, protected, others)
        except Exception as exc:  # noqa: BLE001 - every failure ends the sandbox, then is reported
            raise self._fail(layout, rec, exc) from None

    def stop(self, session_key: str) -> None:
        rec = read_record(self.layout(session_key))
        if rec is None or rec.phase is Phase.ENDED:
            return
        if not self._end(rec, interrupt=True):
            raise RuntimeUnavailable("the session's end could not be confirmed")

    # --- the launch ---

    def _resume(self, adapter: Adapter, layout: SessionLayout, prev: SessionRecord | None,
                spec: LaunchSpec) -> tuple[bool, str | None]:
        """D9: (resume, native ID). Resume held state; start fresh when none can ever have been kept;
        otherwise RuntimeUnavailable."""
        wanted = spec.native_id or (prev.native_id if prev is not None and prev.ran else None)
        try:
            if adapter.has_state(layout.home, wanted):
                return True, wanted
            if prev is None or not prev.ran or not adapter.any_state(layout.home):
                return False, None if adapter.assigns_id else spec.native_id
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        except OSError:
            raise RuntimeUnavailable("the session's home can't be read") from None
        raise RuntimeUnavailable("the session ran before, but its resumable state can't be found")

    def _cli(self, adapter: Adapter) -> Cli:
        binary = self.c.settings.binaries.get(adapter.name)
        if not binary:
            raise LaunchFailed(f"no binary is configured for {adapter.name}")
        try:
            cli = adapter.locate(binary, self.c.path)
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        if cli.version != adapter.version_pin:
            raise LaunchFailed(f"the {adapter.name} CLI is not the pinned version {adapter.version_pin}")
        return cli

    def _logins(self, adapter: Adapter) -> tuple[Protected, tuple[tuple[str, Path], ...]]:
        try:
            chosen_dir, chosen = self.c.accounts.login_paths(adapter.name, DEFAULT_ACCOUNT)
            others: list[tuple[str, Path]] = []
            dirs: list[Path] = []
            for account in self.c.accounts.configured(adapter.name):
                folder, files = self.c.accounts.login_paths(adapter.name, account)
                dirs.append(folder)
                others += [(account, f) for f in files]
        except ConfigError as exc:
            raise LaunchFailed(str(exc)) from None
        return Protected(chosen_dir, chosen, (*dirs, *(f for _, f in others))), tuple(others)

    def _fresh(self, adapter: Adapter, login_files: tuple[Path, ...]) -> None:
        """§7 freshness gate (D12): plan 4 never refreshes, so a login that would expire within the
        session's maximum lifetime and stop margin refuses the launch."""
        try:
            expiry = adapter.access_expiry(login_files)
        except AdapterError as exc:
            raise LaunchFailed(str(exc)) from None
        s = self.c.settings
        if expiry - self.c.clock() <= s.max_lifetime_seconds + s.stop_margin_seconds:
            raise LaunchFailed("the login expires within the session's maximum lifetime; refresh it on "
                               "the host")

    def _start(self, spec: LaunchSpec, adapter: Adapter, cli: Cli, layout: SessionLayout, rec: SessionRecord,
               resume: bool, native: str | None, protected: Protected,
               others: tuple[tuple[str, Path], ...]) -> Started:
        c, gen = self.c, spec.generation
        run, scratch = layout.run(gen), layout.scratch(gen)
        for folder in (run, scratch):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(mode=0o700, parents=True)
        layout.home.mkdir(mode=0o700, exist_ok=True)
        adapter.prepare_home(layout, spec.worktree)
        ws = c.settings.workstreams.get(spec.ws, WsSandbox())
        token = secrets.token_hex(16)
        shim_config = {"wait_seconds": c.settings.hook_wait_seconds,
                       "local_classes": sorted(ws.local_classes), "worktree": str(spec.worktree)}
        files = {shim.TOKEN_FILE: token, SHIM_SOURCE.name: SHIM_SOURCE.read_text(),
                 shim.CONFIG_FILE: json.dumps(shim_config), REQUEST_INSIDE.name: shim.REQUEST_WRAPPER,
                 **adapter.run_files(), **self.selftest.files()}
        for fname, text in files.items():
            write_private(run / fname, text)
        (run / REQUEST_INSIDE.name).chmod(0o700)
        server = SessionServer(layout.socket(gen), token, layout.events(gen))
        server.start()
        self.servers[spec.session_key] = server
        sp = build_spec(SpecInput(spec.session_key, gen, spec.role, layout, spec.worktree,
                                  adapter.facts(layout, cli, c.uid), protected.chosen_files, ws.extra_egress,
                                  ws.extra_ro_mounts, c.settings.probe_allowed_host, c.uid, c.gid))
        check_credentials(sp, protected, c.real_home)
        self.backend.create(sp, scratch)
        self._trust(adapter, cli, sp, layout)
        rec = self._phase(layout, rec, phase=Phase.TESTING)
        ctx = ProbeContext(self.backend, c.tmux, rec.tmux_session, sp, layout, gen, adapter, cli, token,
                           others, c.real_home_canary, c.wsd_socket, c.settings, server.turns)
        self.selftest.exec_path(ctx)
        self._app_server(adapter, cli, sp, layout)
        rec = self._phase(layout, rec, phase=Phase.STARTING)
        profile: dict[str, Any] = dict(c.settings.profiles.get(spec.profile, {}))
        if spec.model:
            profile["model"] = spec.model
        tui = adapter.tui_argv(cli, profile, native_id=native, resume=resume, label=spec.label)
        c.tmux.kill(rec.tmux_session)
        c.tmux.new_session(rec.tmux_session, spec.worktree, self.backend.tty_argv(sp.name, sp.workdir, tui))
        pane, pid = c.tmux.pane_info(rec.tmux_session)
        self.selftest.agent_path(ctx)
        native = self._native(server, native)
        now = c.clock()
        rec = self._phase(layout, rec, phase=Phase.RUNNING, ran=True, native_id=native, started_at=now,
                          deadline=now + c.settings.max_lifetime_seconds, error="")
        return Started(rec.tmux_session, pane, pid, native)

    def _phase(self, layout: SessionLayout, rec: SessionRecord, **changes: Any) -> SessionRecord:
        rec = msgspec.structs.replace(rec, **changes)
        write_record(layout, rec)
        return rec

    def _trust(self, adapter: Adapter, cli: Cli, sp: SandboxSpec, layout: SessionLayout) -> None:
        """D6: seed the hook trust, then require that nothing is left untrusted."""
        argv = adapter.trust_argv(cli, sp.workdir)
        if argv is None:
            return
        for attempt in range(2):
            r = self.backend.exec(sp.name, sp.workdir, argv, timeout=TRUST_SECONDS)
            if r.returncode != 0:
                raise _StepFailed("the hook trust listing failed")
            if adapter.apply_trust(layout, r.stdout) and attempt:
                raise _StepFailed("hooks are still untrusted after seeding")

    def _app_server(self, adapter: Adapter, cli: Cli, sp: SandboxSpec, layout: SessionLayout) -> None:
        argv = adapter.server_argv(cli)
        if argv is None:
            return
        detach = f'setsid "$0" "$@" </dev/null >{BRIDGE_INSIDE}/app-server.log 2>&1 &'
        r = self.backend.exec(sp.name, sp.workdir, ["sh", "-c", detach, *argv], timeout=30)
        if r.returncode != 0 or not self._until(lambda: adapter.server_ready(layout), SERVER_SECONDS):
            raise _StepFailed("the agent's server did not start")

    def _native(self, server: SessionServer, native: str | None) -> str:
        """D8: the native ID the agent reported, checked against the one it was started with."""
        thread = server.turns().thread_id
        if native is None:
            if thread is None:
                raise _StepFailed("the agent reported no session ID")
            return thread
        if thread is not None and thread != native:
            raise _StepFailed("the agent reported a different session ID")
        return native

    def _until(self, pred: Callable[[], bool], seconds: float) -> bool:
        end = self.c.wait_clock() + seconds
        while not pred():
            if self.c.wait_clock() >= end:
                return False
            self.c.sleep(POLL_SECONDS)
        return True

    def _fail(self, layout: SessionLayout, rec: SessionRecord, exc: Exception) -> Exception:
        if isinstance(exc, SelfTestFailed):
            reason = f"launch self-test failed: {exc}"
        elif isinstance(exc, KNOWN_FAILURES):
            reason = str(exc)
        else:
            reason = f"launch step failed ({type(exc).__name__})"
        current = read_record(layout) if layout.record.exists() else None
        rec = msgspec.structs.replace(current or rec, error=reason)
        if self._end(rec, interrupt=False):
            return LaunchFailed(reason)
        return LaunchUncertain(reason)

    # --- the end of a session ---

    def _names(self) -> set[str]:
        try:
            return self.backend.names()
        except BackendUnavailable:
            raise RuntimeUnavailable("the sandbox backend can't list its sandboxes") from None

    def _alive(self, rec: SessionRecord, names: set[str]) -> bool:
        try:
            return (rec.sandbox in names and self.c.tmux.has_session(rec.tmux_session)
                    and not self.c.tmux.pane_dead(rec.tmux_session))
        except TMUX_ERRORS:
            raise RuntimeUnavailable("the session's pane can't be checked") from None

    def _started(self, rec: SessionRecord) -> Started:
        try:
            pane, pid = self.c.tmux.pane_info(rec.tmux_session)
        except TMUX_ERRORS:
            raise RuntimeUnavailable("the session's pane can't be read") from None
        return Started(rec.tmux_session, pane, pid, rec.native_id)

    def _rebind(self, rec: SessionRecord) -> None:
        """D19: a restarted wsd serves a live session's socket again, with an empty turn state."""
        if rec.key in self.servers:
            return
        layout = self.layout(rec.key)
        try:
            token = (layout.run(rec.generation) / shim.TOKEN_FILE).read_text().strip()
            server = SessionServer(layout.socket(rec.generation), token, layout.events(rec.generation))
            server.start()
        except OSError:
            raise RuntimeUnavailable("a live session's socket can't be served") from None
        self.servers[rec.key] = server

    def _end(self, rec: SessionRecord, *, interrupt: bool) -> bool:
        """End the session: interrupt it if asked, kill its pane, delete every sandbox of its key. True
        (recorded `ended`) only once the pane is gone and the backend no longer lists any of them."""
        layout = self.layout(rec.key)
        try:
            rec = self._phase(layout, rec, phase=Phase.STOPPING)
        except OSError:
            return False
        server = self.servers.pop(rec.key, None)
        if server is not None:
            server.close()
        tmux = self.c.tmux
        try:
            if interrupt and tmux.has_session(rec.tmux_session) and not tmux.pane_dead(rec.tmux_session):
                tmux.send_key(rec.tmux_session, "Escape")
            tmux.kill(rec.tmux_session)
            pane_gone = not tmux.has_session(rec.tmux_session)
        except TMUX_ERRORS:
            pane_gone = False
        prefix = f"{short_id(rec.key)}g"
        try:
            mine = [n for n in self.backend.names() if n.startswith(prefix)]
            confirmed = [self.backend.delete(n) for n in mine]
            deleted = all(confirmed) and not any(n.startswith(prefix) for n in self.backend.names())
        except (BackendUnavailable, BackendError):
            deleted = False
        if not (pane_gone and deleted):
            return False
        try:
            self._phase(layout, rec, phase=Phase.ENDED)
        except OSError:
            return False
        return True

    def _records(self) -> list[SessionRecord]:
        if not self.c.sessions.exists():
            return []
        try:
            folders = sorted(p for p in self.c.sessions.iterdir() if p.is_dir())
        except OSError:
            raise RuntimeUnavailable("the session records can't be listed") from None
        found = [read_record(SessionLayout(p)) for p in folders]
        return [r for r in found if r is not None]
```

Two notes for the implementer:

- `_fail` re-reads the record because the failing step may have advanced it (to `testing` or `starting`). Its `error` is the reason the operator sees in the launch receipt.
- `KNOWN_FAILURES` texts are fixed by contract (each class's docstring says so). `TmuxError` is deliberately not among them: its text can carry a path.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `timeout 600 uv run pytest tests/test_sandbox_runtime.py tests/test_tmux.py -q && timeout 300 uv run pyright src/heterodyne/sandbox src/heterodyne/tmux.py && timeout 300 uv run ruff check src tests`
Expected: PASS; clean.

- [ ] **Step 8: Commit**

```bash
git add src/heterodyne/tmux.py src/heterodyne/sandbox/selftest.py src/heterodyne/sandbox/runtime.py \
  tests/fakes/fake_backend.py tests/fakes/scripted_selftest.py tests/sandbox_env.py \
  tests/test_sandbox_runtime.py tests/test_tmux.py
git commit -m "plan4 T10: SandboxRuntime, session records, the resume contract and fail-closed cleanup"
```


### Task 11: The lifetime stop

**Files:**
- Modify: `src/heterodyne/wsd/runtime.py` (`AgentRuntime.expire`, `NoRuntime.expire`), `src/heterodyne/wsd/scheduler.py` (`_pickup` calls it), `tests/fakes/fake_runtime.py` (records it), `src/heterodyne/sandbox/runtime.py` (`SandboxRuntime.expire`)
- Test: `tests/test_wsd_lifetime.py`

**Interfaces:**
- Consumes:
  - Task 10's `SandboxRuntime` (`_records`, `_end`, `_phase`, `servers`, `layout`), `Phase`, `read_record`;
  - Task 5's `TurnState.idle`;
  - plan 3's `gitwip.wip_commit(worktree, mark, summary)`, `GitFailed`, and `Scheduler._pickup`.
- Produces:
  - `AgentRuntime.expire(ws: str, now: int) -> None`: stop every session of the workstream whose lifetime is up. Raises RuntimeUnavailable if a stop can't be confirmed.
  - `FakeRuntime.expires: list[tuple[str, int]]` and `FakeRuntime.expire_failures: int`.

This is D13.

- **When it stops a session.** `expire` runs in every pickup, just before the sweep. A running session whose stop window is open (`now ≥ deadline − stop_margin`) is stopped if it is at a turn boundary: its socket's last turn event was a `Stop`. At the deadline it is stopped whatever it is doing, with Escape first.
- **After the stop.**
  - The WIP is committed with the mark `lifetime:<key>:<generation>`, after the sandbox is confirmed gone. A failed commit is recorded in the session record's `error`, and nothing else changes (gap 8).
  - The sweep that follows in the same pickup sees a running bead with no session and gives it a resume operation. The guard then relaunches it through the freshness gate.
  - This needs no new journal operation.
- **Restarts and timing.**
  - A session re-bound after a wsd restart has an empty turn state, so its stop is hard (D19).
  - Backstop pickups (every 60 s) bound how late a stop can be. Task 1 allows a stop margin down to one minute, so the window always spans at least one backstop. A hard stop can come up to one backstop after the deadline, which is still before the login expires: the gate required `max_lifetime + stop_margin` of it.

- [ ] **Step 1: Write the failing tests**

`tests/test_wsd_lifetime.py`:

```python
import contextlib
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import RuntimeRig, launch_spec, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import WS, Clock, make_rig

from heterodyne.sandbox.runtime import Phase, SessionRecord, read_record
from heterodyne.wsd.runtime import NoRuntime, RuntimeUnavailable
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.states import Reason

needs_tools = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                 reason="tmux and setsid are needed")


@pytest.fixture
def rig() -> Iterator[RuntimeRig]:
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = runtime_rig(root, tmux, Clock())
        try:
            yield rig
        finally:
            rig.backend.delete_unconfirmed = 0
            for key in list(rig.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    rig.runtime.stop(key)
            tmux.kill_server()


def record(rig: RuntimeRig, key: str) -> SessionRecord:
    rec = read_record(rig.runtime.layout(key))
    assert rec is not None
    return rec


def wip_marks(rig: RuntimeRig) -> list[str]:
    out = subprocess.run(["git", "-C", str(rig.worktree), "log", "--format=%B"], capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line.startswith("wsd-park: lifetime:")]


@needs_tools
def test_nothing_stops_before_the_window(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.runtime.expire("alpha", rec.deadline - rig.runtime.c.settings.stop_margin_seconds - 1)
    assert record(rig, spec.session_key).phase is Phase.RUNNING and wip_marks(rig) == []


@needs_tools
def test_an_idle_session_stops_softly_once_the_window_opens(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)                    # the self-test's turn ended with a Stop: idle
    rec = record(rig, spec.session_key)
    rig.runtime.expire("beta", rec.deadline)
    assert record(rig, spec.session_key).phase is Phase.RUNNING          # another workstream's call
    rig.runtime.expire("alpha", rec.deadline - rig.runtime.c.settings.stop_margin_seconds)
    after = record(rig, spec.session_key)
    assert after.phase is Phase.ENDED and after.error == "" and rig.backend.boxes == {}
    assert wip_marks(rig) == [f"wsd-park: lifetime:{spec.session_key}:1"]
    assert rig.runtime.sessions("alpha") == []


@needs_tools
def test_a_session_with_no_turn_boundary_stops_hard_at_the_deadline(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.runtime.servers.pop(spec.session_key).close()       # a wsd restart: the turn state is gone
    assert len(rig.runtime.sessions("alpha")) == 1           # re-bound, not idle
    rec = record(rig, spec.session_key)
    rig.runtime.expire("alpha", rec.deadline - 1)
    assert record(rig, spec.session_key).phase is Phase.RUNNING
    rig.runtime.expire("alpha", rec.deadline)
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert not rig.tmux.has_session(rec.tmux_session)


@needs_tools
def test_an_unconfirmed_lifetime_stop_is_unavailable(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.backend.delete_unconfirmed = 1
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.expire("alpha", record(rig, spec.session_key).deadline)
    assert record(rig, spec.session_key).phase is Phase.STOPPING and wip_marks(rig) == []


@needs_tools
def test_a_failed_wip_commit_is_recorded(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    shutil.rmtree(rig.worktree / ".git")
    rig.runtime.expire("alpha", record(rig, spec.session_key).deadline)
    rec = record(rig, spec.session_key)
    assert rec.phase is Phase.ENDED and rec.error == "the lifetime WIP commit failed"


def test_no_runtime_expires_nothing() -> None:
    NoRuntime().expire("alpha", 0)


def test_pickup_expires_before_it_sweeps(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.runtime.expires == [(WS, rig.clock())]
    rig.runtime.expire_failures = 1
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.RUNTIME_UNAVAILABLE: ""}
```


- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_wsd_lifetime.py -q`
Expected: FAIL with `AttributeError: 'SandboxRuntime' object has no attribute 'expire'` (and `'NoRuntime'`, `'FakeRuntime'`).

- [ ] **Step 3: Add `expire` to the protocol, `NoRuntime`, the fake and the pickup**

In `src/heterodyne/wsd/runtime.py`, add to `AgentRuntime`, after `stop`:

```python
    def expire(self, ws: str, now: int) -> None:
        """Stop each of the workstream's sessions whose maximum lifetime is up (§7): at a turn boundary
        once the stop window opens, by a hard interrupt at the deadline. `now` is the scheduler's clock.
        Raises RuntimeUnavailable if a stop can't be confirmed. The sweep then resumes the bead."""
        ...
```

and to `NoRuntime`:

```python
    def expire(self, ws: str, now: int) -> None:
        return None                  # it runs nothing
```

In `tests/fakes/fake_runtime.py`, add to `__init__`:

```python
        self.expires: list[tuple[str, int]] = []
        self.expire_failures = 0
```

and after `stop`:

```python
    def expire(self, ws: str, now: int) -> None:
        self.expires.append((ws, now))
        if self.expire_failures:
            self.expire_failures -= 1
            raise RuntimeUnavailable("lifetime stop not confirmed")
```

In `src/heterodyne/wsd/scheduler.py`, `_pickup`, the second `try:` becomes:

```python
        try:
            self.d.runtime.expire(name, now)      # §7: a session at its lifetime ends; the sweep resumes it
            sweep(self.parker)
```

- [ ] **Step 4: Add `SandboxRuntime.expire`**

In `src/heterodyne/sandbox/runtime.py`, add `from heterodyne.wsd import gitwip` to the imports and this method after `stop`:

```python
    def expire(self, ws: str, now: int) -> None:
        margin = self.c.settings.stop_margin_seconds
        for rec in self._records():
            if rec.ws != ws or rec.phase is not Phase.RUNNING or now < rec.deadline - margin:
                continue
            hard = now >= rec.deadline
            server = self.servers.get(rec.key)
            if not hard and (server is None or not server.turns().idle):
                continue                         # not at a turn boundary: wait for one, or the deadline
            if not self._end(rec, interrupt=hard):
                raise RuntimeUnavailable("a session at its maximum lifetime could not be stopped")
            try:
                gitwip.wip_commit(Path(rec.worktree), f"lifetime:{rec.key}:{rec.generation}",
                                  "maximum lifetime reached")
            except gitwip.GitFailed:
                layout = self.layout(rec.key)
                ended = read_record(layout)
                if ended is not None:
                    self._phase(layout, ended, error="the lifetime WIP commit failed")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `timeout 900 uv run pytest tests/test_wsd_lifetime.py tests/test_wsd_pickup.py tests/test_wsd_sweep.py tests/test_wsd_runtime.py -q && timeout 300 uv run pyright src/heterodyne/sandbox src/heterodyne/wsd && timeout 300 uv run ruff check src tests`
Expected: PASS; clean. The pickup and sweep suites are the regression check: a `FakeRuntime` that records `expire` changes nothing else.

- [ ] **Step 6: Commit**

```bash
git add src/heterodyne/wsd/runtime.py src/heterodyne/wsd/scheduler.py src/heterodyne/sandbox/runtime.py \
  tests/fakes/fake_runtime.py tests/test_wsd_lifetime.py
git commit -m "plan4 T11: the maximum-lifetime stop at a turn boundary or the deadline, before the sweep"
```


### Task 12 [r15]: Wiring wsd to the sandbox runtime

**Files:**
- Create: `src/heterodyne/sandbox/build.py`, `packaging/sandbox/Containerfile`, `packaging/sandbox/gateway.toml.example`, `packaging/sandbox/README.md`
- Modify:
  - `src/heterodyne/wsd/cli.py` (`wsd run` builds its runtime);
  - `src/heterodyne/platform.py` (the Linux backend is `openshell`);
  - `tests/test_platform.py`;
  - `examples/config.toml`;
  - `docs/wsd.md`, `docs/configuration.md`, `docs/install.md`, `docs/security-model.md`.
- Test: `tests/test_sandbox_build.py`

**Interfaces:**
- Consumes:
  - Task 1's `sandbox_settings(env)`;
  - Task 4's `OpenShellBackend(openshell, podman, image, env)`;
  - Tasks 8 and 9's `OpenShellSelfTest()`;
  - Task 10's `RuntimeConfig`, `SandboxRuntime`;
  - plan 3's `WsdSettings` (`state_dir`, `socket`, `accounts`), `NoRuntime`, `workstream.utc_now`, `Tmux(socket_name, launcher=...)`.
- Produces:
  - `build_runtime(s: WsdSettings, env: Mapping[str, str], *, notice: Callable[[str], None] = <stderr>) -> AgentRuntime`;
  - `TMUX_SOCKET = "heterodyne-wsd"`, `TMUX_SCOPE = "heterodyne-wsd-tmux"`, `CANARY = ".heterodyne-canary"`.

This is **[r15]** because the only backend it can build is OpenShell. The mapping from `[platform] sandbox` to a runtime:

| `[platform] sandbox` | Runtime |
|---|---|
| missing or `"none"` | `NoRuntime`: wsd holds every workstream, as in plan 3 |
| `"openshell"` | `SandboxRuntime(OpenShellBackend, OpenShellSelfTest)` |
| `"bubblewrap"`, `"seatbelt"` | `NoRuntime`, with a one-line notice on stderr that plan 4 builds no such backend |
| anything else | `ConfigError`: `wsd run` exits with EX_CONFIG |

The `bubblewrap` row matters because `heterodyne setup` has recorded `bubblewrap` on every Linux host so far (the reference host included). Mapping it to `NoRuntime` keeps those hosts exactly as they are. An operator opts in by writing `sandbox = "openshell"`, which §17 #3 gates on the reference host. `setup` records `openshell` from now on (the `platform.py` change); a host set up afresh therefore runs agents as soon as the backend answers.

wsd's tmux server is started in a transient systemd scope, as admind's is, so a wsd restart leaves the agents running (§10). The real-home canary is `$HOME/.heterodyne-canary`, which the exec-path self-test writes fresh before each run.

- [ ] **Step 1: Write the failing tests**

`tests/test_sandbox_build.py`:

```python
from collections.abc import Mapping
from pathlib import Path

import pytest
from sandbox_env import config_env
from wsd_env import accounts_at

from heterodyne.config import ConfigError
from heterodyne.sandbox.build import CANARY, TMUX_SOCKET, build_runtime
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.wsd import cli
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings


def settings(tmp_path: Path, env: dict[str, str]) -> WsdSettings:
    return WsdSettings(tmp_path / "state" / "wsd", 60, 3600, 3, tmp_path / "btq", {}, (),
                       accounts_at(Path(env["HOME"])))


def build(tmp_path: Path, host: str) -> tuple[AgentRuntime, list[str]]:
    env = config_env(tmp_path, host)
    notices: list[str] = []
    return build_runtime(settings(tmp_path, env), env, notice=notices.append), notices


@pytest.mark.parametrize("host", ["", '[platform]\nsandbox = "none"\n'])
def test_no_backend_is_no_runtime(tmp_path: Path, host: str) -> None:
    runtime, notices = build(tmp_path, host)
    assert isinstance(runtime, NoRuntime) and notices == []


@pytest.mark.parametrize("backend", ["bubblewrap", "seatbelt"])
def test_a_backend_plan_4_does_not_build_is_no_runtime_with_a_notice(tmp_path: Path, backend: str) -> None:
    runtime, notices = build(tmp_path, f'[platform]\nsandbox = "{backend}"\n')
    assert isinstance(runtime, NoRuntime)
    assert len(notices) == 1 and backend in notices[0] and "no agents" in notices[0]


def test_an_unknown_backend_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="sandbox"):
        build(tmp_path, '[platform]\nsandbox = "firecracker"\n')


def test_openshell_builds_the_sandbox_runtime(tmp_path: Path) -> None:
    runtime, notices = build(tmp_path, '[platform]\nsandbox = "openshell"\nservice_manager = "systemd"\n'
                                       '[sandbox]\nimage = "localhost/heterodyne-agent:1"\n')
    assert notices == [] and isinstance(runtime, SandboxRuntime)
    assert isinstance(runtime.backend, OpenShellBackend) and isinstance(runtime.selftest, OpenShellSelfTest)
    assert runtime.backend.image == "localhost/heterodyne-agent:1"
    c = runtime.c
    assert c.sessions == tmp_path / "state" / "wsd" / "sessions"
    assert c.wsd_socket == settings(tmp_path, {"HOME": str(c.real_home)}).socket
    assert c.real_home_canary == c.real_home / CANARY
    assert c.tmux.socket_name == TMUX_SOCKET and c.tmux.launcher is not None
    assert c.tmux.launcher()[1:4] == ("--user", "--scope", "--collect")


def test_openshell_without_systemd_starts_tmux_directly(tmp_path: Path) -> None:
    runtime, _ = build(tmp_path, '[platform]\nsandbox = "openshell"\nservice_manager = "launchd"\n'
                                 '[sandbox]\nimage = "localhost/heterodyne-agent:1"\n')
    assert isinstance(runtime, SandboxRuntime) and runtime.c.tmux.launcher is None



def test_wsd_run_uses_the_built_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = config_env(tmp_path, "")
    s = settings(tmp_path, env)
    seen: list[AgentRuntime] = []

    def run(_s: WsdSettings, _f: object, runtime: AgentRuntime) -> int:
        seen.append(runtime)
        return 0

    def refused(_s: WsdSettings, _env: Mapping[str, str]) -> AgentRuntime:
        raise ConfigError("config.toml: [platform] sandbox must be openshell, bubblewrap, seatbelt or none")

    monkeypatch.setattr(cli, "_settings", lambda: s)
    monkeypatch.setattr(cli, "_factory", lambda _s: None)
    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(cli, "build_runtime", lambda _s, _env: NoRuntime())
    assert cli.wsd_main(["run"]) == 0 and isinstance(seen[0], NoRuntime)
    monkeypatch.setattr(cli, "build_runtime", refused)
    assert cli.wsd_main(["run"]) == cli.EX_CONFIG
```

In `tests/test_platform.py`, the Linux expectation in `test_backends_per_platform` becomes `{"service_manager": "systemd", "sandbox": "openshell"}`. The tests give `[sandbox] image` explicitly whatever Task 1's default is: it documents what the backend is given.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 300 uv run pytest tests/test_sandbox_build.py tests/test_platform.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'heterodyne.sandbox.build'`, and `test_backends_per_platform` on the Linux row.

- [ ] **Step 3: Write `build.py`**

`src/heterodyne/sandbox/build.py`:

```python
"""wsd's agent runtime from the host config's `[platform] sandbox` (ADR 0001 §3.2, §7). Plan 4 builds
OpenShell only. A backend it doesn't build leaves wsd on NoRuntime, so a host whose setup recorded one
(every Linux host before plan 4 recorded `bubblewrap`) runs no agents until the operator opts in."""

import os
import shutil
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from heterodyne.config import ConfigError, load
from heterodyne.config.layers import table_at
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import RuntimeConfig, SandboxRuntime
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.tmux import Tmux
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import utc_now

TMUX_SOCKET = "heterodyne-wsd"
TMUX_SCOPE = "heterodyne-wsd-tmux"      # prefix of the transient systemd scopes that hold the tmux server
CANARY = ".heterodyne-canary"           # in the real home: the self-test proves it unreadable inside
NOT_BUILT = frozenset({"bubblewrap", "seatbelt"})


def _stderr(text: str) -> None:
    print(text, file=sys.stderr)


def _launcher(service_manager: object) -> Callable[[], tuple[str, ...]] | None:
    """On systemd the wsd unit kills its whole cgroup on a stop, so the tmux server (and every agent)
    is started in a transient scope of its own, with a unique name per start."""
    if service_manager != "systemd":
        return None
    return lambda: (shutil.which("systemd-run") or "systemd-run", "--user", "--scope", "--collect",
                    "--quiet", f"--unit={TMUX_SCOPE}-{uuid.uuid4().hex[:12]}",
                    "--description=heterodyne wsd tmux server (agent sessions)")


def build_runtime(s: WsdSettings, env: Mapping[str, str], *,
                  notice: Callable[[str], None] = _stderr) -> AgentRuntime:
    settings = sandbox_settings(env)
    if settings.backend == "none":
        return NoRuntime()
    if settings.backend in NOT_BUILT:
        notice(f"wsd: [platform] sandbox = {settings.backend!r} is not built yet; no agents will run "
               "(set it to \"openshell\" to use the sandbox runtime)")
        return NoRuntime()
    if settings.backend != "openshell":
        raise ConfigError("config.toml: [platform] sandbox must be openshell, bubblewrap, seatbelt or none")
    if s.accounts is None:
        raise ConfigError("the sandbox runtime needs the host's accounts")
    home = env.get("HOME")
    if not home:
        raise ConfigError("the sandbox runtime needs HOME")
    real_home = Path(home)
    service_manager = table_at(load(env=env).values, "platform", "config").get("service_manager")
    config = RuntimeConfig(
        sessions=s.state_dir / "sessions", settings=settings, accounts=s.accounts, real_home=real_home,
        real_home_canary=real_home / CANARY, wsd_socket=s.socket, uid=os.getuid(), gid=os.getgid(),
        tmux=Tmux(TMUX_SOCKET, launcher=_launcher(service_manager)), path=env.get("PATH", os.defpath),
        clock=utc_now)
    backend = OpenShellBackend(settings.openshell, settings.podman, settings.image, settings.tool_env)
    return SandboxRuntime(config, backend, OpenShellSelfTest())
```

`Tmux` keeps its constructor arguments as the attributes `socket_name` and `launcher`, which the test reads.

- [ ] **Step 4: Wire `wsd run` and the platform default**

In `src/heterodyne/wsd/cli.py`:
- add `import os` if missing (it is there) and `from heterodyne.sandbox.build import build_runtime`;
- change the `NoRuntime` import to `from heterodyne.wsd.runtime import AgentRuntime`;
- in `wsd_main`, replace `return run(s, _factory(s), NoRuntime())` with:

```python
        if args.cmd == "run":
            return run(s, _factory(s), build_runtime(s, os.environ))
```

The existing `except ConfigError` turns a bad backend into EX_CONFIG.

In `src/heterodyne/platform.py`, the Linux row becomes:

```python
    "linux": {"service_manager": "systemd", "sandbox": "openshell"},
```

- [ ] **Step 5: The image, the gateway example and the docs**

`packaging/sandbox/Containerfile`:

```dockerfile
# The heterodyne agent workload image for the OpenShell backend (ADR 0001 §7; spike S5). The agent CLIs
# are not baked in: the runtime binds the host's pinned install read-only at its host path. The probes
# and hooks need python3 (3.12: the probe channel checks /usr/bin/python3.12), curl and git.
FROM nvcr.io/nvidia/base/ubuntu:24.04
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl python3 git iproute2 procps \
 && rm -rf /var/lib/apt/lists/* \
 && (getent group "${AGENT_GID}" || groupadd -g "${AGENT_GID}" agent) \
 && (getent passwd "${AGENT_UID}" && userdel -f "$(getent passwd "${AGENT_UID}" | cut -d: -f1)" || true) \
 && useradd -u "${AGENT_UID}" -g "${AGENT_GID}" -M -d /sandbox -s /bin/bash agent
USER ${AGENT_UID}:${AGENT_GID}
WORKDIR /sandbox
```

The `userdel` line removes the base image's own uid-1000 user when the host uid is 1000, so the workload's uid is always the `agent` user (S5's uid notes).

`packaging/sandbox/gateway.toml.example`:

```toml
# OpenShell gateway configuration for the podman compute driver (spike S5). Install it as
# ~/.config/openshell/gateway.toml and point OPENSHELL_GATEWAY_CONFIG at it in gateway.env.
[openshell]
version = 2

[openshell.gateway]
compute_driver = "podman"

[openshell.drivers.podman]
socket_path = "<rootless podman 5 user socket>"
allow_driver_config = true
enable_bind_mounts = true
userns = "keep-id"                 # the workload's uid is the host uid, so bound files keep their owner

[openshell.drivers.podman.resource_admission]
enabled = false                    # with admission on, the gateway refuses bind mounts
```

`packaging/sandbox/README.md`:

```markdown
# The OpenShell sandbox backend

wsd runs each agent session in an OpenShell sandbox when the host config says
`[platform] sandbox = "openshell"` (ADR 0001 §7). This needs, once per host:

1. OpenShell 0.1.2 and rootless podman 5 (the podman compute driver needs podman 5). On a host whose
   system podman is older, install podman 5 side by side and give wsd its `PATH` and `CONTAINERS_CONF`
   through `[sandbox] tool_env`.
2. The gateway on the podman driver: copy `gateway.toml.example` to `~/.config/openshell/gateway.toml`,
   fill in the podman user socket, and restart the gateway's user unit.
3. The workload image, built with your uid and gid:

       podman build --build-arg AGENT_UID="$(id -u)" --build-arg AGENT_GID="$(id -g)" \
         -t localhost/heterodyne-agent:1 packaging/sandbox

   and `[sandbox] image = "localhost/heterodyne-agent:1"` in the host config.
4. The pinned agent CLIs installed on the host (`[adapters.<name>] binary`), with a default login each.

Every launch runs the §7 self-test before the agent does any work; a failed check refuses the launch
and names the check. `docs/wsd.md` describes what the runtime does.
```

Docs:
- `examples/config.toml`: the `sandbox` comment becomes `# openshell (Linux), or none; written by \`heterodyne setup\` (§3.2, §7)`, and add a commented `[sandbox]` block naming `image`, `max_lifetime_minutes`, `stop_margin_minutes` and `tool_env`, with placeholders only.
- `docs/configuration.md`, `[platform]`: setup records `openshell` on Linux. A host that recorded `bubblewrap` (any setup before plan 4) runs no agents until it is changed to `openshell`. Document Task 1's `[sandbox]` keys.
- `docs/install.md`: the backend table row becomes "OpenShell (plan 4); see `packaging/sandbox/README.md`", and drop "no sandbox runs yet".
- `docs/wsd.md`: a "Runtime" section covering:
  - the session directory and its record phases;
  - the launch sequence and the self-test;
  - the resume contract (D9);
  - the lifetime stop (D13);
  - what `RuntimeUnavailable` holds;
  - how an operator attaches to a session (`tmux -L heterodyne-wsd attach -t wsd-<short id>`), read-only by convention.
- `docs/security-model.md`: the profile paragraph says the OpenShell runtime is built (plan 4), the outer fence is `NetworkMode=none` plus the broker, and the residual risks of gaps 4 and 10.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `timeout 600 uv run pytest tests/test_sandbox_build.py tests/test_platform.py tests/test_wsd_daemon.py -q && timeout 300 uv run pyright src && timeout 300 uv run ruff check src tests`
Expected: PASS; clean.

- [ ] **Step 7: Commit**

```bash
git add src/heterodyne/sandbox/build.py src/heterodyne/wsd/cli.py src/heterodyne/platform.py \
  tests/test_sandbox_build.py tests/test_platform.py examples/config.toml packaging/sandbox \
  docs/wsd.md docs/configuration.md docs/install.md docs/security-model.md
git commit -m "plan4 T12: wsd builds the OpenShell runtime from [platform] sandbox; image and docs"
```


### Task 13: The scheduler on the sandbox runtime

**Files:**
- Test: `tests/test_sandbox_scheduler.py`

**Interfaces:**
- Consumes:
  - plan 3's scheduler rig (`wsd_env.make_rig`, `Rig`, `on_profile`, `Limits`, `Deps`, `Parker`, `Scheduler`);
  - Task 10's `runtime_rig`, `SandboxRuntime`, `read_record`, `Phase`;
  - Task 11's `expire`;
  - Task 7's `fresh_logins`.
- Produces: nothing new (an integration test).

Tasks 10 and 11 test the runtime alone, and plan 3 tests the scheduler against `FakeRuntime`. This task runs plan 3's real scheduler, parker and launch guard on Task 10's `SandboxRuntime`, using the fake backend, the scripted self-test, the fake CLIs and a real tmux. It proves that the pieces meet:

- **Launch.** A pickup launches the bead in a sandbox and journals the runtime's receipt, the Codex thread ID included.
- **Busy.** A second pickup is busy.
- **The lifetime path.**
  1. Advancing the shared clock into the stop window ends the idle session (Task 11).
  2. The same pickup's sweep opens the resume.
  3. The guard relaunches generation 2 with the same native ID.
- **The gate on relaunch.** If the login would expire within the next lifetime, the relaunch is refused (`LaunchFailed`), and the bead goes to `needs-human` through the existing budget.

The rig is plan 3's `make_rig`, with `Deps.runtime` replaced. `Rig.restart` would put a `FakeRuntime` back, so these tests never call it.

- [ ] **Step 1: Write the tests**

`tests/test_sandbox_scheduler.py`:

```python
import contextlib
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, replace

import pytest
from sandbox_env import RuntimeRig, fresh_logins, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import WS, Rig, make_rig, on_profile

from heterodyne.sandbox.runtime import Phase, SessionRecord, read_record
from heterodyne.wsd.beads import NEEDS_HUMAN
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import Liveness, RuntimeUnavailable
from heterodyne.wsd.scheduler import Outcome, Scheduler
from heterodyne.wsd.workstream import Limits

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                reason="tmux and setsid are needed")


@dataclass
class Both:
    rig: Rig            # plan 3's scheduler, journal and fake btq
    rt: RuntimeRig      # the sandbox runtime it drives

    def record(self, bead: str) -> SessionRecord:
        rec = read_record(self.rt.runtime.layout(self.rig.key(bead)))
        assert rec is not None
        return rec


@pytest.fixture
def both(request: pytest.FixtureRequest) -> Iterator[Both]:
    limits: Limits = getattr(request, "param", Limits())
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = make_rig(root / "w", limits=limits)
        rt = runtime_rig(root / "x", tmux, rig.clock)
        rig.deps = replace(rig.deps, runtime=rt.runtime)
        rig.parker = Parker(rig.ws, rig.deps)
        rig.sched = Scheduler(rig.ws, rig.deps, rig.parker)
        try:
            yield Both(rig, rt)
        finally:
            for key in list(rt.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    rt.runtime.stop(key)
            tmux.kill_server()


def lifetime_marks(rig: Rig, bead: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(rig.worktree(bead)), "log", "--format=%B"], capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line.startswith("wsd-park: lifetime:")]


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_pickup_runs_the_bead_in_a_sandbox(both: Both, profile: str) -> None:
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    on_profile(rig, "btq-1", profile)
    assert rig.pickup() is Outcome.STARTED
    rec = both.record("btq-1")
    assert (rec.phase, rec.generation, rec.worktree) == (Phase.RUNNING, 1, str(rig.worktree("btq-1")))
    assert rec.native_id is not None
    if profile == "p-one":
        assert rec.native_id == rig.key("btq-1")         # Claude: the session key
    [session] = rt.runtime.sessions(WS)
    assert session.liveness is Liveness.LIVE and session.bead == "btq-1"
    assert rig.state("btq-1") == "running"
    assert rig.pickup() is Outcome.BUSY
    assert len(rt.backend.created) == 1


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_the_lifetime_stop_relaunches_the_same_session(both: Both, profile: str) -> None:
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    on_profile(rig, "btq-1", profile)
    assert rig.pickup() is Outcome.STARTED
    first = both.record("btq-1")
    rig.clock.advance(first.deadline - rt.runtime.c.settings.stop_margin_seconds - rig.clock())
    assert rig.pickup() is Outcome.BUSY             # stopped, resumed and relaunched in one pickup
    second = both.record("btq-1")
    assert (second.phase, second.generation, second.native_id) == (Phase.RUNNING, 2, first.native_id)
    assert second.sandbox != first.sandbox and first.sandbox in rt.backend.deleted
    assert lifetime_marks(rig, "btq-1") == [f"wsd-park: lifetime:{rig.key('btq-1')}:1"]
    assert rig.state("btq-1") == "running"
    assert len(rt.runtime.sessions(WS)) == 1


@pytest.mark.parametrize("both", [Limits(launch_failures_before_human=1)], indirect=True)
def test_a_relaunch_the_gate_refuses_needs_a_human(both: Both) -> None:
    rig, rt = both.rig, both.rt
    s = rt.runtime.c.settings
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    first = both.record("btq-1")
    # Valid for this generation, but not for another full lifetime from the stop window.
    fresh_logins(rt.home, first.started_at + s.max_lifetime_seconds + s.stop_margin_seconds + 600)
    rig.clock.advance(first.deadline - s.stop_margin_seconds - rig.clock())
    rig.pickup()
    rec = both.record("btq-1")
    assert rec.phase is Phase.ENDED and rec.generation == 1     # generation 2 never got a record write
    assert rt.backend.created == [first.sandbox]
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rt.runtime.sessions(WS) == []
```

The last test's record is still generation 1: the freshness gate refuses the launch before the record is written (Task 10's order), so nothing of generation 2 exists to clean up.

- [ ] **Step 2: Run the tests**

Run: `timeout 900 uv run pytest tests/test_sandbox_scheduler.py -q`
Expected: PASS. Nothing in `src` changes in this task. A failure here is a seam between plan 3 and Tasks 10–11, so fix it where the contract is broken, not in the test. The likely seams:

- the guard's `native_id` for a Codex resume;
- the generation number the guard passes;
- the sweep's reading of an ended session.

- [ ] **Step 3: Lint, type-check and commit**

Run: `timeout 300 uv run pyright tests/test_sandbox_scheduler.py && timeout 300 uv run ruff check tests`
Expected: clean.

```bash
git add tests/test_sandbox_scheduler.py
git commit -m "plan4 T13: the scheduler, parker and guard on the sandbox runtime, lifetime relaunch included"
```


### Task 14 [r15]: The live sandbox test

**Files:**
- Create: `tests/live/test_live_sandbox.py` (the only change under `tests/live/`; `conftest.py` is unchanged)

**Interfaces:**
- Consumes:
  - Task 4's `OpenShellBackend`;
  - Tasks 8 and 9's `OpenShellSelfTest`;
  - Task 10's `SandboxRuntime`, `RuntimeConfig`, `read_record`, `Phase`;
  - Task 11's `expire`;
  - Task 1's `sandbox_settings`, through `tests/sandbox_env.config_env`;
  - `wsd_env.accounts_at`, `git_repo`.
- Produces: nothing (a live test).

This is the roadmap's plan 4 live gate. It is **[r15]** because its pass criteria are the OpenShell ones.

**When it runs.** The live conftest already skips everything under `tests/live/` unless `HZ_LIVE=1`. This module also needs `HZ_LIVE_SANDBOX=1`, so an admind live run never starts sandboxes.

**What the host needs:**
- the S5 host changes (§17 #3);
- the agent image (`packaging/sandbox/README.md`);
- both pinned CLIs on `PATH`;
- a valid default login for each.

**Its inputs**, all from the environment:
- `HZ_LIVE_SANDBOX_IMAGE`;
- `HZ_LIVE_SANDBOX_PATH`, a `PATH` that holds podman 5 and openshell;
- optionally `HZ_LIVE_SANDBOX_CONTAINERS_CONF`.

**What it touches.**
- It reads the operator's real default logins, which are bound read-only as in S5, and prints nothing from them.
- It writes only:
  - a scratch directory;
  - a private tmux server;
  - the canary `$HOME/.heterodyne-live-canary`, removed at the end.
- It never uses wsd's state, config or tmux server, and it deletes only the sandboxes it created.

**Running it beside wsd.** Run it only while no wsd on this host uses the OpenShell runtime. Such a wsd would find these sandboxes in `openshell sandbox list` with no record of its own, and would hold its workstreams until they are gone. That is the fail-closed rule of Task 10 working as designed.

**Cost.** Each launch makes one model call (gap 11). The module makes six launches and one extra prompt.

It checks what the offline suite can't:
- **Shapes and self-test:** both managed shapes launch on the real backend, and the full self-test passes on both paths.
- **Egress:** a non-allowlisted host is refused.
- **No push credential:** none is inside, and a push fails (gap 7).
- **Read-only reviewer:** the reviewer's worktree is read-only.
- **wsd down:** with the session socket closed, a Codex `PreToolUse` is denied with the fail-closed reason (D14, S8 capability 1).
- **Resume:** both CLIs resume their own session in a new generation, Codex under `--remote`.
- **Lifetime:** the lifetime stop ends a real sandbox.

One test is a strict `xfail` that documents gap 13: an agent can't commit in a btq linked worktree, because the git common directory isn't bound. When gap 13's resolution lands, that test passes, and its `xfail` comes off in the same change.

- [ ] **Step 1: Write the live test**

`tests/live/test_live_sandbox.py`:

```python
"""Live checks of the OpenShell sandbox runtime: plan 4's live gate. Needs HZ_LIVE=1 (the conftest) and
HZ_LIVE_SANDBOX=1, the S5 host setup, the agent image and both pinned CLIs with a default login. It makes
real model calls: one per launch, plus one prompt. It prints nothing from any login.

Run it only while no wsd on this host uses the OpenShell runtime: that wsd would see these sandboxes
without a record and hold its workstreams until they are gone."""

import contextlib
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))     # tests/: sandbox_env, wsd_env

from sandbox_env import config_env, short_dir  # noqa: E402
from wsd_env import accounts_at, git_repo  # noqa: E402

from heterodyne.sandbox.openshell import OpenShellBackend  # noqa: E402
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest  # noqa: E402
from heterodyne.sandbox.runtime import (  # noqa: E402
    Phase, RuntimeConfig, SandboxRuntime, SessionRecord, read_record)
from heterodyne.sandbox.settings import sandbox_settings  # noqa: E402
from heterodyne.tmux import Tmux  # noqa: E402
from heterodyne.wsd import ids  # noqa: E402
from heterodyne.wsd.runtime import LaunchSpec, Liveness, RuntimeUnavailable, Started  # noqa: E402
from heterodyne.wsd.workstream import utc_now  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("HZ_LIVE_SANDBOX") != "1",
                                reason="live sandbox checks: set HZ_LIVE_SANDBOX=1 (and HZ_LIVE=1)")
PROFILES = {"claude": "p-one", "codex": "p-two"}
PROMPT_SECONDS = 180
EXEC_SECONDS = 60


@dataclass
class Live:
    root: Path
    runtime: SandboxRuntime
    backend: OpenShellBackend
    tmux: Tmux
    worktree: Path
    keys: list[str] = field(default_factory=list[str])      # every session this module launched
    first: dict[str, tuple[LaunchSpec, Started]] = field(
        default_factory=dict[str, tuple[LaunchSpec, Started]])     # cli -> its first launch

    def spec(self, cli: str, bead: str, role: str = "coder", worktree: Path | None = None) -> LaunchSpec:
        profile = PROFILES[cli]
        key = ids.role_session(bead, role, profile)
        return LaunchSpec("alpha", bead, role, profile, key, f"{bead} · {role}", worktree or self.worktree,
                          resume=False, native_id=key if cli == "claude" else None)

    def launch(self, spec: LaunchSpec) -> Started:
        self.keys.append(spec.session_key)
        return self.runtime.launch(spec)

    def record(self, key: str) -> SessionRecord:
        rec = read_record(self.runtime.layout(key))
        assert rec is not None
        return rec

    def run(self, key: str, *argv: str) -> int:
        rec = self.record(key)
        return self.backend.exec(rec.sandbox, Path(rec.worktree), list(argv), timeout=EXEC_SECONDS).returncode


def wait_until(pred: Callable[[], bool], seconds: float) -> bool:
    end = time.monotonic() + seconds
    while not pred():
        if time.monotonic() > end:
            return False
        time.sleep(1)
    return True


def host_config() -> str:
    tool_env = {"PATH": os.environ["HZ_LIVE_SANDBOX_PATH"]}
    if os.environ.get("HZ_LIVE_SANDBOX_CONTAINERS_CONF"):
        tool_env["CONTAINERS_CONF"] = os.environ["HZ_LIVE_SANDBOX_CONTAINERS_CONF"]
    pairs = ", ".join(f'{k} = "{v}"' for k, v in tool_env.items())
    image = os.environ["HZ_LIVE_SANDBOX_IMAGE"]
    return (f'\n[platform]\nsandbox = "openshell"\n\n[sandbox]\nimage = "{image}"\n'
            f'egress_approved = ["github.com"]\ntool_env = {{ {pairs} }}\n')


@pytest.fixture(scope="module")
def live() -> Iterator[Live]:
    real_home = Path.home()
    canary = real_home / ".heterodyne-live-canary"
    with short_dir() as root:
        env = config_env(root / "c", host_config(), {"alpha": '[sandbox]\nextra_egress = ["github.com"]\n'})
        settings = sandbox_settings(env)
        tmux = Tmux(f"hz-live-sb-{uuid.uuid4().hex[:8]}")
        backend = OpenShellBackend(settings.openshell, settings.podman, settings.image, settings.tool_env)
        config = RuntimeConfig(
            sessions=root / "s", settings=settings, accounts=accounts_at(real_home), real_home=real_home,
            real_home_canary=canary, wsd_socket=root / "ctl.sock", uid=os.getuid(), gid=os.getgid(),
            tmux=tmux, path=os.environ.get("PATH", os.defpath), clock=utc_now)
        live = Live(root, SandboxRuntime(config, backend, OpenShellSelfTest()), backend, tmux,
                    git_repo(root / "w"))
        try:
            yield live
        finally:
            for key in live.keys:
                with contextlib.suppress(RuntimeUnavailable):
                    live.runtime.stop(key)
            tmux.kill_server()
            canary.unlink(missing_ok=True)


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_both_shapes_launch_through_the_full_self_test(live: Live, cli: str) -> None:
    spec = live.spec(cli, "btq-live-1")
    started = live.launch(spec)
    live.first[cli] = (spec, started)
    rec = live.record(spec.session_key)
    assert rec.phase is Phase.RUNNING and started.native_id is not None
    assert {s.key: s.liveness for s in live.runtime.sessions("alpha")}[spec.session_key] is Liveness.LIVE


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_egress_is_refused_and_no_push_credential_is_inside(live: Live, cli: str) -> None:
    key = live.first[cli][0].session_key
    assert live.run(key, "curl", "-sS", "-m", "10", "-o", "/dev/null", "https://example.org") != 0
    assert live.run(key, "sh", "-c", 'test ! -e "$HOME/.git-credentials" && test ! -e "$HOME/.config/gh" '
                                     '&& test -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}"') == 0
    assert live.run(key, "env", "GIT_TERMINAL_PROMPT=0", "git", "push", "https://github.com/example/none.git",
                    "HEAD:refs/heads/hz-live") != 0


def test_the_reviewer_worktree_is_read_only(live: Live) -> None:
    spec = live.spec("claude", "btq-live-2", role="reviewer")
    live.launch(spec)
    assert live.run(spec.session_key, "touch", str(live.worktree / "hz-live-ro")) != 0
    assert not (live.worktree / "hz-live-ro").exists()
    live.runtime.stop(spec.session_key)


def test_codex_tool_calls_fail_closed_while_wsd_is_down(live: Live) -> None:
    spec, _ = live.first["codex"]
    rec = live.record(spec.session_key)
    live.runtime.servers.pop(spec.session_key).close()          # wsd gone: the hook gets no answer
    live.tmux.paste(rec.tmux_session, "Run the shell command `echo hz-live-probe` and show its output.")
    assert wait_until(lambda: "control plane unavailable" in live.tmux.capture(rec.tmux_session, 200),
                      PROMPT_SECONDS)


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_a_new_generation_resumes_the_same_session(live: Live, cli: str) -> None:
    spec, first = live.first[cli]
    live.runtime.stop(spec.session_key)
    second = live.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
    assert second.native_id == first.native_id
    assert live.record(spec.session_key).sandbox.endswith("g2")


def test_the_lifetime_stop_ends_a_real_sandbox(live: Live) -> None:
    spec, _ = live.first["claude"]
    rec = live.record(spec.session_key)
    live.runtime.expire("alpha", rec.deadline)
    assert live.record(spec.session_key).phase is Phase.ENDED
    assert rec.sandbox not in live.backend.names()


@pytest.mark.xfail(strict=True, reason="gap 13: the git common directory of a linked worktree is not bound")
def test_an_agent_can_commit_in_a_btq_linked_worktree(live: Live) -> None:
    linked = live.root / "w-btq-live-3"
    subprocess.run(["git", "-C", str(live.worktree), "worktree", "add", "-q", "-b", "btq/btq-live-3",
                    str(linked)], check=True, capture_output=True)
    spec = live.spec("claude", "btq-live-3", worktree=linked)
    live.launch(spec)
    try:
        assert live.run(spec.session_key, "git", "-c", "user.name=agent", "-c",
                        "user.email=agent@example.org", "commit", "--allow-empty", "-q", "-m", "hz-live") == 0
    finally:
        live.runtime.stop(spec.session_key)
```

The tests depend on file order (pytest's default): the first test launches the two sessions the rest use. A test that finds `live.first` missing fails with a `KeyError`, which points back at the first test's failure.

- [ ] **Step 2: Check it offline**

Run: `timeout 300 uv run pytest tests/live/test_live_sandbox.py -q && timeout 300 uv run pyright tests/live/test_live_sandbox.py && timeout 300 uv run ruff check tests/live`
Expected: every test skipped (no `HZ_LIVE`), and clean.

- [ ] **Step 3: Run it live (the operator, or a worker the operator has cleared for the host)**

Ack team-lead first; this makes model calls on the operator's logins.

Run: `HZ_LIVE=1 HZ_LIVE_SANDBOX=1 HZ_LIVE_SANDBOX_IMAGE=localhost/heterodyne-agent:1 HZ_LIVE_SANDBOX_PATH="<podman 5 bin>:$PATH" HZ_LIVE_SANDBOX_CONTAINERS_CONF="<podman 5 containers.conf>" timeout 3600 uv run pytest tests/live/test_live_sandbox.py -v`
Expected:
- every test passes, and the linked-worktree test reports `XFAIL`;
- `openshell sandbox list` shows none of the module's sandboxes afterwards.

Record the run in `docs/wsd.md`'s Runtime section: the date, the CLI versions and the pass/xfail line. Paste no output that holds a path under the home directory.

- [ ] **Step 4: Commit**

```bash
git add tests/live/test_live_sandbox.py docs/wsd.md
git commit -m "plan4 T14: the live sandbox gate, both shapes, egress, reviewer, fail-closed hook, resume"
```

---

## Execution

- **Approach:** subagent-driven (superpowers:subagent-driven-development), one fresh implementer per task, with a review before each task's close. The review is cross-model, at most 3 cycles.
- **Prerequisites:**
  - design approval of this plan;
  - for the **[r15]** tasks, ADR revision 15 approved;
  - before Task 14's live run, the §17 #3 host changes and the agent image in place.
- **Order:** Tasks 1, 2, 5, 6, 7, 10, 11 and 13 can start as soon as the plan is approved (see "Task order"). The **[r15]** tasks wait for r15.
- **Before enabling the backend:** gap 13's resolution has to land before `[platform] sandbox = "openshell"` is set on any real host.
- **Suite:** run the whole suite once per task, in the background with `timeout 3600`.
