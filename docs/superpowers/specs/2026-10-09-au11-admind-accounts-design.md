# btq-w1elg (AU-11): admind accounts: each admind process on its profile's first account (design r1)

Base: main 1ff70c1.

**Sources:**
- ADR 0001 revision 14 (82b2e4b; approved, btq-k942c): §4.4 D11, §8 "Accounts", §4.4 D1, D7, D9 and D10.
- Change plan §AU-11 and its dependency graph (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195): AU-11 → G1, AU-2.
- AU-2 as merged (`src/heterodyne/config/accounts.py`, `capabilities.py`): `Config.accounts` maps `(adapter, name)` to `Account(name, adapter, configured_dir, login_dir, login_files, key)`, the implicit `default` of each adapter included.
- S7 (`docs/spikes/s7-accounts-and-usage.md`): Claude's login file set is `$CLAUDE_CONFIG_DIR/.credentials.json` and Codex's is `$CODEX_HOME/auth.json`. Claude keeps `.claude.json`, `projects/` and `sessions/` in the same directory.
- S8 (`docs/spikes/s8-marmot-only.md`): Claude's startup dialogs are pre-accepted in `$CLAUDE_CONFIG_DIR/.claude.json`. For Codex, `SessionStart` fires only on the first prompt, and the launch needs `--no-daemon`.

Where the plan and r14 differ, r14 wins. They agree on AU-11.

**Scope:** `src/heterodyne/admind/` (`settings.py`, `agent.py`, `summarize.py`, `reap.py`, `daemon.py`), `src/heterodyne/tmux.py` (`new_session`) and the tests. It is PoC-sized: about 60 lines of code plus tests.

**Not in scope:**
- the headroom gate, usage, failover and launch entries (D11: admind has none of them);
- `wn-agent` and the Marmot paths (unchanged);
- a Codex admin agent or a Codex summarizer (see decision 1);
- dialog pre-acceptance in an account's directory (setup and runbook, AU-10);
- filling the capability table (AU-6).

## 1. What the sources fix

- Each admind process (the admin agent, and the summarizer) resolves its own profile (`[admind] profile`, `[admind] summarizer`) and that profile's adapter. This is already done by `settings._profile`.
- If that profile lists `accounts`, the process runs with the first one: `CLAUDE_CONFIG_DIR` (Claude Code) or `CODEX_HOME` (Codex) is set to that account's `login_dir`. admind isn't sandboxed, so there is no synthetic home and nothing is bound.
- No headroom gate and no failover. admind is the recovery path, and nothing account-related may stop it from launching.
- With no `accounts` on the profile, the launch is byte-for-byte what it is today: no variable added, no argv change, no tmux argument change.

## 2. Today's reachable surface

- `ADMIN_ADAPTERS = ("claude-code",)`: both the admin agent and the summarizer must be Claude Code profiles, and `settings._profile` rejects anything else.
- `capabilities.CAPABILITIES` is empty, so AU-2 rejects every `[accounts.*]` entry ("S7 hasn't demonstrated login binding"). On the reference install AU-11 is therefore dormant until AU-6 enables Claude's login binding. Tests inject a capability table through `config.load(..., capabilities=...)`.

So the live behaviour AU-11 adds, once enabled, is: an admin agent and a summarizer, each on a Claude Code profile, each optionally on its own first account.

## 3. Design

### 3.1 Settings (`settings.py`)

```python
CONFIG_DIR_VAR = {"claude-code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}

@dataclass(frozen=True)
class AdmindAccount:
    name: str          # "default" when the profile lists no accounts
    key: str           # AU-2 credential key (ck1-..., 32 hex: never trips the 64-hex redaction)
    env: Mapping[str, str]   # {} for "default"; else {CONFIG_DIR_VAR[adapter]: str(account.login_dir)}
```

- `_profile` also returns the profile's `AdmindAccount`: `accounts[0]` if the profile lists any, looked up as `cfg.accounts[(adapter, name)]`; otherwise `cfg.accounts[(adapter, "default")]` with an empty `env`.
- `AdmindSettings` gains `agent_account: AdmindAccount` and `summarizer_account: AdmindAccount | None`.
- The path is AU-2's canonical `login_dir`, resolved once, when admind loads its config. A repointed symlink takes effect at the next admind restart, the same as every other admind setting (admind reloads only its operators).
- `CONFIG_DIR_VAR` has both adapters, so the mapping is complete when a Codex admin adapter lands. Only Claude Code is reachable today.

### 3.2 The admin agent (`agent.py`, `tmux.py`)

- `Tmux.new_session(name, cwd, argv, env=None)`: each entry becomes `-e NAME=value` on the `new-session` command. That is required, not optional: the tmux server outlives admind in its own scope and keeps the environment it started with, so the client's environment does not reach a new pane.
- `AdminAgent.ensure_running` passes `env=self.settings.agent_account.env` only when it is non-empty. With no account the call, and so the tmux argv, is unchanged, and the existing test fakes keep their signature.
- **Resume across a login change.** Claude keeps transcripts under `$CLAUDE_CONFIG_DIR/projects/`, so `--resume SID` under another login directory finds no session, and the three-launch limit ends in `AgentStuck`. The store gets one new key, `agent_account`: the credential key of the launch that started the recorded session. It is written with `agent_session` whenever a fresh ID is chosen. `ensure_running` resumes only if the stored key equals `agent_account.key`. Otherwise it launches a fresh session ID, as `!new` does, and returns the new mode word `launched-login-changed`. `start_agent` already audits every mode (`audit_quietly("agent", action=mode, ...)`), so no new audit call is needed, and no path is involved. A store from before AU-11 has no key, which is read as the adapter's `default` key, because that is what those launches used.
- **Adoption is unchanged.** A live pane is adopted whatever its login, so an admind restart never kills a working agent. A changed account takes effect at the next launch: a crash, `!new`, or a relaunch after no `SessionStart` (decision 3).

### 3.3 The summarizer (`summarize.py`, `reap.py`, `daemon.py`)

- `reap.spawn(argv, *, cwd, feed, stderr, env=None)`: `env` is an overlay. When it is given, the child gets `os.environ | env`; when it is `None`, the child inherits as today.
- `summarize(argv, cwd, reply, timeout, env=None)` passes it through. The daemon passes `settings.summarizer_account.env or None`.
- `headless_argv` is unchanged. Its `--setting-sources project` still keeps user settings out. Under an account's directory, only the credentials and `.claude.json` come from there.

### 3.4 What is never added

- No fallback to the default login when the account's login is missing or invalid: that would be a silent account switch, and D11 says admind never fails over. The CLI then fails the way an invalid default login fails today. The agent never reaches `SessionStart` and ends in the existing not-started hold, and the summarizer fails into the backstop (`failed` or `empty`). Both paths already report to Marmot.
- No login path or email in any Marmot message or audit line (D10): only account names.
- No new lock. admind's CLI refreshes its own login in place under the CLI's own `.credentials.lock`, as it does today on `~/.claude` (see risk 2).

## 4. Open decisions (operator or reviewer)

1. **Acceptance "an agent and a summarizer on different adapters".** This is not reachable: both must be Claude Code (`ADMIN_ADAPTERS`), there is no fake `codex` in `tests/fakes`, and nothing shows a tool-free headless Codex run for B8's "no tools" (S1: `codex exec` runs shell commands, constrained only by its sandbox mode).
   - **Recommendation:** test the cross-adapter mapping at the settings level (a `codex` profile resolves to `CODEX_HOME`, with `ADMIN_ADAPTERS` widened in that test only), test the processes end-to-end as two Claude profiles on different accounts, and leave the cross-adapter process test to the bead that adds a Codex admin adapter (S8 gaps: `SessionStart`, `--no-daemon`, hook trust).
   - Alternative: add a Codex summarizer and a fake `codex` now, which is bigger than the PoC needs and blocked on B8.
2. **Should admind require a demonstrated capability of its own?** AU-2 already refuses accounts on an adapter without `login_binding` (a), so AU-11 inherits that gate. (a) is defined for the sandboxed synthetic home; admind needs only "the variable selects the login", which S7 showed client-side.
   - **Recommendation:** no separate capability for the PoC. admind accounts switch on when AU-6 sets Claude's (a).
3. **A login change with a live agent.** On the next admind start, should admind replace a live pane that runs on the previous login?
   - **Recommendation:** no. Adopt it, and switch at the next launch. Replacing a live admin agent could cut off the operator's recovery path mid-turn.
   - Alternative: replace it on start with a Marmot notice, which is stricter about "runs on the first account".
4. **Notice for a fresh session after a login change.** Audit only, or audit plus one control-group line ("admin agent: new session, its login changed")?
   - **Recommendation:** both, because the operator otherwise sees lost context with no reason. The line uses only fixed words.
5. **Inherited login variables.** A `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` in admind's environment would override the account's file login (S7 §17 #5). The systemd unit passes only `PATH` and location variables (`unit.PASSED_THROUGH`), so this can't happen under the rendered unit.
   - **Recommendation:** document it, and do no stripping in the PoC.
   - Alternative: refuse to start when such a variable is set and an account is configured.

## 5. Risks (accepted for the PoC, documented in the runbook by AU-10)

1. **An account directory is a whole Claude config directory.** Under `CLAUDE_CONFIG_DIR` the admin agent reads that directory's `.claude.json` (onboarding, theme, bypass acceptance and workdir trust), its user `settings.json`, `CLAUDE.md` and plugins, and writes its transcripts there. The operator must pre-accept the dialogs in each account directory the admin agent uses. The S8 preflight covers this, but setup doesn't automate it yet. Until then a missing key shows up as the not-started hold.
2. **A shared login refreshes from two sides.** If the admin agent's account is also used by `wsd` sessions, admind's CLI may refresh it in place while `wsd`'s freshness gate (D8 lock) refreshes it too. A provider that rotates refresh tokens can then invalidate one side. The runbook should recommend a dedicated account for admind, or accept the risk.
3. **Under a different login the agent loses context.** That is the fresh session in §3.2. It is preferable to a resume that can't succeed.

## 6. Tests (offline; fake `claude`, fake tmux and real tmux, as today)

Settings (`test_admind_settings.py`, with an injected capability table that sets `login_binding` for both adapters):
- agent and summarizer profiles with no accounts: both `env` are `{}`, and the keys are the default keys;
- the agent profile lists `[b, c]`: `env == {"CLAUDE_CONFIG_DIR": <b's canonical login_dir>}`, `name == "b"`;
- the summarizer on another account (`c`) gets its own directory, not the agent's;
- a `codex` profile (with `ADMIN_ADAPTERS` widened in the test) gets `CODEX_HOME`, not `CLAUDE_CONFIG_DIR` (decision 1).

Agent (`test_admind_agent.py`, fake tmux recording `env`):
- no account: `new_session` is called exactly as before, with no `env` and the same argv;
- account `b`: `env == {"CLAUDE_CONFIG_DIR": ...}`, and the store's `agent_account` is b's key;
- a recorded started session under key K, with config now on another key: a fresh `--session-id`, not `--resume`, and the mode `launched-login-changed` (and the notice, if decision 4 is accepted);
- a store from before AU-11 (no `agent_account`) and no accounts: it resumes as today;
- a live pane with a nonce is adopted after a login change (decision 3).

Process environment, end to end:
- `Tmux.new_session` with `env` on a real private tmux server: the fake `claude` writes its `CLAUDE_CONFIG_DIR` and `CODEX_HOME` (or `<unset>`) to `$FAKE_CLAUDE_LOG`. This holds even when the server was started earlier without them, which is the reason for `-e`.
- The summarizer, through `summarize()` with a fake `claude -p` that prints its `CLAUDE_CONFIG_DIR`: the agent and the summarizer on different accounts each see their own directory. With no account the variable is inherited unchanged (set or unset as in the test's environment).
- No Marmot message or audit line contains the login directory (grep the captured outputs for the tmp path).

Unchanged: the existing admind suites pass with no edits to their fakes.
