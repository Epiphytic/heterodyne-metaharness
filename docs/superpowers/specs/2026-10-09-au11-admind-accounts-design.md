# btq-w1elg (AU-11): admind accounts: each admind process on its profile's first account (design r3)

Base: main 1ff70c1.

**r3 changes** (Codex review r2):
- The login-change notice is crash-durable: its outbox key is allocated in the same transaction as the replacement intent, and posting it and clearing it are one transaction, done at every start, independent of the pane (1).
- The fake summarizer checks its stdin for every path form; ordinary `!details` and the backstop are tested explicitly (2).
- The no-path guarantee is qualified to the literal forms of configured named accounts, as an explicit exception to D10 for sign-off (3).

**r2 changes** (Codex review r1; finding numbers in parentheses):
- Login-override variables are removed from both processes, the pane's tmux environment included, whenever an account is configured (1).
- A live pane on another login is replaced through the existing replacement protocol, with a fixed-word notice; it is never adopted (2).
- The default login is AU-2's fixed directory, enforced: admind refuses to start if its own environment sets a login selector, and every pane launch clears the selectors from the tmux server's environment. A store with no recorded login counts as unknown, never as default (3).
- Account login directories are redacted inside `redact`, the one function every outbound, audit and summarizer path already calls (4).
- The Codex process test is an explicit acceptance amendment with a named follow-up (5).

**Sources:**
- ADR 0001 revision 14 (82b2e4b; approved, btq-k942c): §4.4 D11, §8 "Accounts", §4.4 D1, D7, D9 and D10.
- Change plan §AU-11 and its dependency graph (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195): AU-11 → G1, AU-2.
- AU-2 as merged (`src/heterodyne/config/accounts.py`, `capabilities.py`): `Config.accounts` maps `(adapter, name)` to `Account(name, adapter, configured_dir, login_dir, login_files, key)`, the implicit `default` of each adapter included. `DEFAULT_LOGIN_DIRS` deliberately ignores `CLAUDE_CONFIG_DIR` and `CODEX_HOME`: "the environment can't select a login (D1)".
- S7 (`docs/spikes/s7-accounts-and-usage.md`): Claude's login file set is `$CLAUDE_CONFIG_DIR/.credentials.json` and Codex's is `$CODEX_HOME/auth.json`. Claude keeps `.claude.json`, `projects/` and `sessions/` in the same directory. An env-token login (`CLAUDE_CODE_OAUTH_TOKEN`) also works and is not a login file.
- S8 (`docs/spikes/s8-marmot-only.md`): Claude's startup dialogs are pre-accepted in `$CLAUDE_CONFIG_DIR/.claude.json`. For Codex, `SessionStart` fires only on the first prompt, and the launch needs `--no-daemon`.

Where the plan and r14 differ, r14 wins. They agree on AU-11 except the acceptance amendment in §4.

**Scope:** `src/heterodyne/admind/` (`settings.py`, `agent.py`, `summarize.py`, `reap.py`, `daemon.py`, `redact.py`, `cli.py`), `src/heterodyne/tmux.py` (`new_session`) and the tests. PoC-sized.

**Not in scope:**
- the headroom gate, usage, failover and launch entries (D11: admind has none of them);
- `wn-agent` and the Marmot paths (unchanged);
- a Codex admin agent or a Codex summarizer (§4);
- dialog pre-acceptance in an account's directory (setup and runbook, AU-10);
- filling the capability table (AU-6).

## 1. What the sources fix

- Each admind process (the admin agent, and the summarizer) resolves its own profile (`[admind] profile`, `[admind] summarizer`) and that profile's adapter, as `settings._profile` already does.
- If that profile lists `accounts`, the process runs on the first one: `CLAUDE_CONFIG_DIR` (Claude Code) or `CODEX_HOME` (Codex) is that account's `login_dir`. admind isn't sandboxed, so there is no synthetic home and nothing is bound.
- No headroom gate and no failover: admind is the recovery path.
- Otherwise the process runs on the adapter's `default` login, which is AU-2's fixed directory (`~/.claude`, `~/.codex`), and the CLI's own argv is unchanged.

## 2. Today's reachable surface

- `ADMIN_ADAPTERS = ("claude-code",)`: the admin agent and the summarizer must both be Claude Code profiles.
- `capabilities.CAPABILITIES` is empty, so AU-2 rejects every `[accounts.*]` entry. On the reference install AU-11 stays dormant until AU-6 enables Claude's login binding. Tests inject a capability table through `config.load(..., capabilities=...)`.
- On the live install, as of 2026-10-08, neither admind's process environment, nor the user manager's, nor the admin tmux server's global environment sets `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, or any `CLAUDE_*` or `ANTHROPIC_*` variable. Only the variable names were checked. So §3.2's refusal doesn't stop the live admind.

## 3. Design

### 3.1 Per-adapter variables (`settings.py`)

```python
SELECTOR = {"claude-code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
# Variables that log the CLI in without its login files (S7 §17 #5). Codex's list is provisional until S7's
# real-account run; Codex is not an admin adapter yet.
LOGIN_OVERRIDES = {"claude-code": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
                   "codex": ("OPENAI_API_KEY", "CODEX_API_KEY")}

@dataclass(frozen=True)
class AdmindAccount:
    name: str                  # "default" when the profile lists no accounts
    key: str                   # AU-2 credential key (ck1-..., 32 hex: never trips the 64-hex redaction)
    set: Mapping[str, str]     # {} for default; else {SELECTOR[adapter]: str(account.login_dir)}
    unset: tuple[str, ...]     # SELECTOR[adapter] for default; LOGIN_OVERRIDES[adapter] for a named account
```

- `_profile` also returns the profile's `AdmindAccount`: `accounts[0]` looked up as `cfg.accounts[(adapter, name)]`, or `cfg.accounts[(adapter, "default")]`.
- `AdmindSettings` gains `agent_account: AdmindAccount`, `summarizer_account: AdmindAccount | None`, and `login_dirs: tuple[str, ...]` for §3.5.
- The path is AU-2's canonical `login_dir`, resolved when admind loads its config. A repointed symlink takes effect at the next admind restart, like every other admind setting (admind reloads only its operators).

### 3.2 The default login is the fixed directory (finding 3)

The recorded key must name the directory the CLI really uses. For `default` that is AU-2's fixed directory, so nothing may select another one:
- **admind's own environment.** `settings.resolve` raises `ConfigError` if `env` sets `SELECTOR[adapter]` for the agent's or the summarizer's adapter. The message is path-free: `admind's environment sets CLAUDE_CONFIG_DIR; logins are chosen by [accounts] in config.toml (ADR §4.4 D1). Unset it, or configure an account`. admind exits through its existing config-error path (exit 78, `RestartPreventExitStatus=78`), so it isn't restarted in a loop. This is the compatibility decision: a selector set in the environment is refused, not tracked.
- **A stale tmux server.** The admin tmux server lives in its own scope and outlives admind, and a new pane takes the server's global environment, not admind's. So every pane launch clears it, in the same tmux invocation that creates the pane. `Tmux.new_session(name, cwd, argv, set=None, unset=())` puts `set-environment -g -u VAR` for each `unset` name before `new-session`, and `-e VAR=value` for each `set` entry on `new-session` itself. They are chained with `;`, as the existing `set-option -g remain-on-exit on` already is. Unsetting a variable that isn't set exits 0 (checked on tmux 3.4).
- With no account, the CLI argv is unchanged. The tmux command gains only `set-environment -g -u CLAUDE_CONFIG_DIR`, which changes nothing unless a stale selector was there.
- An inherited `CLAUDE_CODE_OAUTH_TOKEN` under `default` is today's behaviour and stays. It doesn't move the config directory, so the recorded key still names where transcripts live (see §5, risk 4).

### 3.3 Overrides removed when an account is configured (finding 1)

- **Pane:** `unset` for a named account is `LOGIN_OVERRIDES[adapter]`, cleared from the tmux server's global environment as in §3.2. `set` adds the selector with `-e`. tmux's `update-environment` list doesn't include any of these names, so the client can't bring them back.
- **Summarizer:** `reap.spawn(argv, *, cwd, feed, stderr, env=None)` passes `env` to `Popen` as the child's complete environment. `summarize(..., account=None)` builds it as `os.environ` minus `account.unset`, plus `account.set`. With no summarizer account, `env` is `None` and the child inherits as today. The selector is already refused at startup by §3.2.

### 3.4 Launch key, adoption and resume (findings 2, 3)

- **Store key `agent_account`:** the credential key of the current pane's launch. `ensure_running` writes it with `launch_nonce`, before `new_session`. A pane is resumed only under an equal key (below), so the key also names the login directory that holds the recorded session's transcript.
- `AdminAgent.login_matches()` returns `store.get("agent_account") == settings.agent_account.key`. A missing key (a store from before AU-11) **does not match**: it is unknown, never assumed to be the default.
- **Resume:** `ensure_running` resumes only if `session_started == sid` and `login_matches()`. Otherwise it picks a fresh session ID, as `!new` does. Claude keeps transcripts under `$CLAUDE_CONFIG_DIR/projects/`, so a resume under another directory can't succeed and would end in `AgentStuck`.
- **Adoption (startup):** in `start_agent(startup=True)`, before `ensure_running`, if a pane is alive and `not login_matches()`, the daemon runs the existing replacement protocol:
  1. `abandon_in_flight(LOGIN_CHANGED_WHY, idle=True, replace="login-changed", notice=True)`. Transaction (a) commits the replacement intent together with the abandonment, as today, so a crash can't later adopt the old pane. With `notice=True` the same transaction also allocates the notice's outbox key, `login-changed:<next_seq("login_seq")>`, and stores it as `pending_notice`. The in-flight message, if any, is answered as abandoned, as today.
  2. `ensure_running` sees `replace_pending` and kills the old pane; it never adopts it. The login doesn't match, so the new pane gets a fresh session.
  3. **Notice:** `flush_pending_notice()` runs one transaction: if `pending_notice` is set, `post(key, LOGIN_CHANGED_NOTICE, None)` and delete `pending_notice`. The text is `The admin agent was restarted on a new session because its login changed. Resend anything unanswered.` It names no account and no path.
- **Durability of the notice.** `flush_pending_notice()` runs in `start_agent` after `ensure_running` on every path, whether launched, resumed, adopted or failed. So the notice no longer depends on the old pane still mismatching:
  - A crash after the spawn, or after `ensure_running` cleared `replace_pending`, leaves a matching pane, which the restart adopts (under the usual adopted hold). `pending_notice` is still set, so that start posts it.
  - A crash before the enqueue transaction commits posts it on the next start, under the same key.
  - A crash after the commit has nothing left to post.
  - The key is fixed when the intent is written and `store.enqueue` is `INSERT OR IGNORE`, so the notice can never be posted twice.
- The mode word `launched-login-changed` from `ensure_running` is audited by the existing `audit_quietly("agent", action=mode, ...)`.
- admind's config is read only at start, so the check runs only at startup. Later relaunches (a crash, `!new`, a ready timeout) already launch under the current key.
- **Upgrade:** the first start after AU-11 finds no `agent_account` and replaces the live pane once, with the notice, whatever the config says. That costs the agent's context once and avoids guessing what the old pane ran on.

### 3.5 Configured login paths don't leave admind (finding 4; D10 exception, r2 finding 3)

**Guarantee (qualified):** no literal form of a *configured named account's* login directory leaves admind. This is narrower than D10's "no message shows a login path". The `default` directories (`~/.claude`, `~/.codex`) stay visible, and a path spelled another way isn't caught (§5, risk 5). That exception needs Liam's sign-off with the design approval.


- `redact` is the one function every outbound post, `!tail`, `!details` (both modes), the backstop, every audit field (`audit.clean` → `redact`), and the summarizer's input and output already pass through. It gains a login-directory pass, run first:
  - `redact.set_login_dirs(paths)` installs, once at startup (`cli.run`, before the daemon posts anything), the literal forms of every **named** account's login directory in the config: the configured string as written (`~/...` included), its `~`-expanded form, and AU-2's canonical form.
  - Each occurrence is replaced by `<redacted login dir>`. Matching is literal, longest form first, and the next character must not be a name character (`(?![A-Za-z0-9._-])`), so `/x/.claude-b` doesn't match inside `/x/.claude-bb`. A trailing `/.credentials.json` stays after the marker, which is harmless.
  - It covers every named account, not just admind's own, because the agent can read the whole config.
- `default` directories (`~/.claude`, `~/.codex`) aren't added, so no-account installs keep their exact output today.
- The redaction loop (`MAX_PASSES`, idempotence) is unchanged: a marker contains no login path, so the new pass can't feed itself.
- Module state is a tuple set once. Tests reset it through `set_login_dirs(())`.

### 3.6 What is never added

- No fallback to the default login when the account's login is missing or invalid (D11: no failover). The CLI fails as an invalid default login does today. The agent ends in the existing not-started hold, and the summarizer in the backstop. Both already report to Marmot.
- No new lock. admind's CLI refreshes its own login in place under the CLI's `.credentials.lock` (§5, risk 2).

## 4. Acceptance amendment and open decisions

**Acceptance amendment (finding 5; this needs Liam's approval in the design ask).**
- The plan's case "an agent and a summarizer on different adapters", tested on processes, is **not claimed** by AU-11. It can't be reached: both must be Claude Code (`ADMIN_ADAPTERS`), there is no fake `codex`, and nothing shows a tool-free headless Codex run for B8 (S1: `codex exec` runs shell commands, limited only by its sandbox mode).
- AU-11 covers the cross-adapter mapping at the settings level instead: a `codex` profile resolves to `CODEX_HOME` and the Codex override list, with `ADMIN_ADAPTERS` widened in that test only.
- The process-level case moves to a follow-up: **"admind: Codex as admin agent and summarizer"**. It is gated on S8 open items 1 and 2 (the `SessionStart` alternative, `--no-daemon`, hook trust), and adds the fake `codex` and the cross-adapter process test. Team-lead files the bead with the approval ask.

**Open decisions** (recommendation first):
1. **A separate admind capability?** No. AU-2 already refuses accounts on an adapter without `login_binding` (a), and AU-11 inherits that.
2. **Fresh-session notice wording.** Use the fixed sentence in §3.4, posted once per replacement.
3. **Redact `default` directories too?** No for the PoC: it would change no-account output, and `~/.claude` is the CLI's well-known location, not configuration. This is the D10 exception in §3.5, for Liam's sign-off.

## 5. Risks (accepted for the PoC; runbook text by AU-10)

1. **An account directory is a whole Claude config directory:** `.claude.json` (dialogs, workdir trust), user `settings.json`, `CLAUDE.md`, plugins and transcripts. Dialogs must be pre-accepted in each account directory the admin agent uses. Until setup automates that, a missing key shows up as the not-started hold.
2. **A shared login refreshes from two sides.** If admind's account is also used by `wsd` sessions, admind's CLI and `wsd`'s freshness gate (D8 lock) can both refresh it. A provider that rotates refresh tokens can invalidate one side. The runbook recommends a dedicated account for admind.
3. **A login change or the upgrade costs the agent's context** (§3.4). That's preferable to a resume that can't succeed, or to an agent on the wrong login.
4. **A token variable under `default`** still logs the CLI in by token, as today. Only a configured account strips it (§3.3).
5. **Limitation: redaction is literal, not path confidentiality.** A path the agent rewrites (relative, `..`, another symlink spelling) and the `default` directories aren't caught (§3.5). The secret patterns still catch credential content.

## 6. Tests (offline; fake `claude`, fake and real tmux on a private socket)

Settings (`test_admind_settings.py`, with an injected capability table that sets `login_binding` for both adapters):
- no accounts: `set == {}`, `unset == ("CLAUDE_CONFIG_DIR",)`, and the key is the default key;
- the agent profile lists `[b, c]`: `set == {"CLAUDE_CONFIG_DIR": <b canonical>}`, `unset ==` the Claude overrides;
- the summarizer on `c` gets its own directory;
- a `codex` profile (with `ADMIN_ADAPTERS` widened) gets `CODEX_HOME` and the Codex overrides;
- `CLAUDE_CONFIG_DIR` in `env` refuses with the fixed message, and the message contains no path; `CODEX_HOME` with a Claude-only admind is accepted.

Agent (`test_admind_agent.py`, fake tmux recording `set`/`unset`):
- no account: the same CLI argv as before, `unset == ("CLAUDE_CONFIG_DIR",)`, `set == {}`;
- account `b`: `set` and `unset` as above, and `agent_account` is b's key;
- a session started under key K, config now on L: a fresh `--session-id`, and mode `launched-login-changed`;
- a pre-AU-11 store (no key) with no accounts: a fresh session, not a resume.

Daemon (`test_admind_daemon.py` style, fake `claude`):
- startup with a live pane under key K and config on L: the in-flight message is answered as abandoned, `replace_pending` and `pending_notice` are committed before the kill, the pane is replaced (not adopted), and the notice is posted exactly once;
- crash points, each followed by a fresh daemon start on the same store and pane, counting outbox rows with the `login-changed:` prefix:
  - after the abandonment, before the kill: the old pane isn't adopted, and one notice;
  - after the spawn, before `replace_pending` is cleared: one notice;
  - after `replace_pending` is cleared, before the flush (the new pane is adopted under the adopted hold): one notice;
  - inside the flush transaction, before commit (rolled back): one notice on the next start;
  - after the flush commit: no second notice.

Process environment, end to end:
- **Real tmux, stale server.** Start the private server with `CLAUDE_CONFIG_DIR=/stale` and `CLAUDE_CODE_OAUTH_TOKEN=x` in its environment. Then `AdminAgent.ensure_running` with account `b`: the fake `claude` logs its `CLAUDE_CONFIG_DIR` (b's directory) and no token. With no account, it logs `CLAUDE_CONFIG_DIR` unset; the token is kept.
- **Summarizer.** admind's environment has `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`; a fake `claude -p` prints the names of its login variables, never values or paths. With the agent on `b` and the summarizer on `c`, each process sees only its own `CLAUDE_CONFIG_DIR` (checked by comparing inside the test, not by printing), and the summarizer sees no override variable.
- **Summarizer input.** The fake summarizer reads its stdin and exits non-zero if it contains any configured, expanded or canonical login-directory form, so an unredacted input fails the summary into the backstop and the test fails.
- **Redaction.** Through each surface, emit a login directory in configured, expanded and canonical forms: an agent reply (verbatim and summarized), `!tail`, ordinary `!details`, `!details full`, a backstop batch (summarizer forced to fail), summarizer output, and an audit field. Assert the captured Marmot posts and audit lines contain `<redacted login dir>` and no form of the path. Also check the `.claude-b`/`.claude-bb` boundary.

Unchanged: the existing admind suites pass. Their fake tmux `new_session` gains the two optional parameters.
