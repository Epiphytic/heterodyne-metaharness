# btq-wnbdp: AU-2 config: accounts, profile accounts, failover and `[usage]` (design r2)

Base: main b136b30.

**r2 changes** (review r1, `docs/reviews/au2-config-design-r1.md` is not committed; findings by number):
- `config check` hides `login_dir` (1);
- every canonicalization and alias step raises only a path-free `ConfigError` (2);
- `usage` must be a table (3);
- the three open questions are now decisions (4–6).

 `src/heterodyne/config/` (`layers.py`, `__init__.py`), `src/heterodyne/cli.py` (`config check`), `src/heterodyne/defaults/defaults.toml`.

**Sources:**
- ADR 0001 revision 14 (approved, btq-k942c): §4.1 "Validation at startup and on reload", §4.4 D1, D6, D7 and D9, and §15.
- Change plan §AU-2 (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md`).
- The S7 outcome (`docs/spikes/s7-accounts-and-usage.md`): no capability is demonstrated on either adapter, so every capability is off and `failover` is `none`.

Where the plan and r14 differ, r14 wins (one case, under "Capabilities").

**Scope:** config schema, validation, defaults, credential identities, `config check` output and the docs.

**Not in scope:**
- launch entries (AU-3) and the headroom gate (AU-4);
- reading usage, and any runtime use of the capability table;
- `admind`'s use of accounts (D11);
- filling the capability table (AU-6, AU-7).

## Schema

```toml
[accounts.<name>]              # host config.toml only
adapter   = "<adapter>"        # one of adapters.known
login_dir = "<path>"           # absolute, or ~/...; a path, never a secret reference

[profiles.<p>]
accounts = ["<name>", ...]     # optional, ordered by preference; omit for the adapter's default login
failover = "none"              # optional: "none" (default) or "next"

[usage]                        # defaults.toml; host config.toml may override
reserve_percent             = 5
stale_minutes               = 30
unknown_backoff_minutes     = 30
untrusted_max_defer_minutes = 60
min_recheck_seconds         = 60
max_window_hours            = 192
```

- `defaults.toml` gets the `[usage]` block above, with the r14 example values, and no accounts.
- `failover` has no entry in defaults. It is per profile, so an absent key means `"none"`.
- The values above satisfy every `[usage]` bound below: 60 ≤ 60 × 30, and 30 and 60 ≤ 60 × 192.

## Capabilities (new module `config/capabilities.py`)

```python
@dataclass(frozen=True)
class Capabilities:
    """What S7 demonstrated for one adapter on its pinned CLI version (ADR §4.4 D9)."""
    login_binding: bool = False          # (a)
    cross_account_resume: bool = False   # (b)
    in_session_usage: bool = False       # (c)
    trusted_read: bool = False           # (d)
    limit_signal: bool = False           # (e)
    handoff_relaunch: bool = False       # (f)

    @property
    def can_switch(self) -> bool:        # D7: moving a session to another account needs (b) or (f)
        return self.cross_account_resume or self.handoff_relaunch

# Empty: S7 demonstrated nothing on a real account. AU-6 and AU-7 add entries from accepted findings.
CAPABILITIES: Mapping[str, Capabilities] = {}
NONE = Capabilities()

# Login file set per adapter, relative to its login directory (S7 (1), client side shown).
LOGIN_FILES: Mapping[str, tuple[str, ...]] = {"claude-code": (".credentials.json",), "codex": ("auth.json",)}
# The implicit `default` account's login directory, relative to HOME.
DEFAULT_LOGIN_DIRS: Mapping[str, str] = {"claude-code": "~/.claude", "codex": "~/.codex"}
```

- An adapter missing from `CAPABILITIES` has `NONE`.
- Config uses only two capabilities:
  - `login_binding` gates any `[accounts.*]` entry for the adapter, and any profile `accounts` on it (D7).
  - `trusted_read` gates `failover = "next"` (D6).
- `can_switch` is for the AU-4 gate. Config doesn't use it.
- **Plan vs r14.** S7 recommends that Codex get `"next"` only with (d) and also (b) or (f). r14 D6 rejects `"next"` only when (d) is missing. D7 says that without (b) or (f), `"next"` only chooses a session's first account, and that is the gate's concern. Following r14, config requires (d) alone.
- `LOGIN_FILES` records S7's client-side result, the file each CLI builds its login path from.
  - It is used only for the identity and alias checks below.
  - It enables nothing: while `login_binding` is off, no account is accepted.
  - AU-6 or AU-7 confirms it, or changes it, when (a) is demonstrated.
- An adapter in `adapters.known` but missing from `LOGIN_FILES` or `DEFAULT_LOGIN_DIRS` is a `ConfigError` raised by `load()`: "adapter <a> has no login file set". This can only happen if a host adds an adapter that the code doesn't know.

## Credential identity (new module `config/accounts.py`)

```python
@dataclass(frozen=True)
class Account:
    name: str                       # "default" for the implicit account
    adapter: str
    login_dir: Path                 # canonical: ~ expanded, symlinks resolved (strict=False)
    login_files: tuple[Path, ...]   # canonical, in LOGIN_FILES order
    key: str                        # credential key

def resolve_accounts(merged, env, capabilities=CAPABILITIES) -> dict[tuple[str, str], Account]:
    """All accounts keyed by (adapter, name), the implicit default of every known adapter included.
    Raises ConfigError on the first violation, in the order of the rules below."""
```

- **Expansion:** a named account's `login_dir` and each implicit default (`DEFAULT_LOGIN_DIRS[a]`) are both expanded with `paths.expand(value, env)`, so `~/` means the supplied environment's `HOME` in both cases.
- **Identity:** the adapter plus the canonical login files. A login file is canonical when `Path(login_dir, f).resolve(strict=False)`, which resolves a symlinked file too.
- **Key:**
  - `"ck1-" + sha256("\0".join([adapter, *map(str, login_files)])).hexdigest()[:32]`.
  - The full hex is not used. It is 64 hex characters, which `secret_scan.show` redacts as an identifier and the logging rules forbid printing. 128 bits is enough to tell apart a host's handful of logins.
  - `ck1` versions the formula.
  - The key depends only on paths. It is computed at load, and AU-3 recomputes it at launch, so repointing a symlink gives a new key (D1: "starts from unknown usage").
- **Aliases** (D1). Every pair of accounts is checked, across adapters too, the implicit defaults included. A pair is an alias when:
  1. their canonical login directories are equal, or one `is_relative_to` the other;
  2. or any login file of one has the same canonical path as any login file of the other;
  3. or both files exist and `os.stat` gives the same `(st_dev, st_ino)` (a hard link).
  - Missing files and directories are allowed (r14 §7: an absent login never refuses), so check 3 is skipped for them.
  - A `stat` error other than ENOENT (EACCES) is a `ConfigError`: "account <n>: login file can't be checked for aliases (<errno name>)". An identity that can't be verified fails closed.
- **Path-free failures.** Every filesystem step (expanding, `resolve` of the directory and of each login file, `stat`, and the alias comparisons) runs inside one helper that catches `OSError`, `RuntimeError` and `ValueError`.
  - `OSError` covers ELOOP, EACCES and ENAMETOOLONG. `RuntimeError` is Python 3.12's symlink-loop error from `resolve(strict=False)`. `ValueError` covers an embedded NUL.
  - The helper re-raises as `ConfigError("account <n> (<a>): login directory can't be resolved (<errno name or exception type>)") from None`.
  - The message never includes `str(exc)`, which contains the path. `from None` keeps the original out of a printed chain.
  - So `config check` and wsd reload, which catch only `ConfigError`, never see an exception that carries a login path.

## Validation rules and error messages

All of these run in `load()` on the merged config, after `check_profiles`, through `accounts.check(merged, env, capabilities)`. The secret scan has already run on each layer.

**Error-message rules:**
- Errors name the account and the profile through `show(…, False)`. **No error quotes a `login_dir` or a login file path.** wsd reload errors may reach the control group, and D10 says no message shows a login path.
- Errors quote other user values through `show`.

| # | Rule | Message (`ConfigError`) |
|---|---|---|
| 1 | `[accounts]` is a table of tables | `accounts.<n> must be a table` |
| 2 | Name: `^[A-Za-z][A-Za-z0-9_-]{0,63}$`, so no `@`, `/`, `.` or spaces | `accounts.<n>: account names are plain identifiers (letters, digits, - and _; no @ or path separators)` |
| 3 | Name isn't `default` (compared case-insensitively) | `accounts.default: "default" is reserved for the adapter's own login; omit accounts to use it` |
| 4 | Keys are only `adapter` and `login_dir` | `accounts.<n>: unknown keys [...] (allowed: adapter, login_dir)` |
| 5 | `adapter` in `adapters.known` | `accounts.<n>: adapter <v> is not one of [...]` |
| 6 | `login_dir` is a secret reference | `accounts.<n>.login_dir is a path, not a secret; a { file } or { command } reference isn't allowed` |
| 7 | `login_dir` is a non-empty string | `accounts.<n>.login_dir must be a string` |
| 8 | `login_dir` is absolute or starts with `~/` | `accounts.<n>.login_dir must be an absolute path or start with ~/` |
| 9 | `login_binding` is on for the adapter (D7, D9) | `accounts.<n>: accounts aren't supported on adapter <a> yet: S7 hasn't demonstrated login binding for it (ADR §4.4 D9)` |
| 10 | `profiles.<p>.accounts` is a list of strings | `profiles.<p>.accounts must be a list of account names` |
| 11 | ...and not empty | `profiles.<p>.accounts is empty; omit it to use the adapter's default login` |
| 12 | ...and has no duplicates | `profiles.<p>.accounts lists <n> more than once` |
| 13 | ...and no name is `default` (decided: an explicit `default` is rejected; omit `accounts` instead) | `profiles.<p>: "default" can't be listed; omit accounts to use the adapter's default login` |
| 13a | ...and every name is a configured `[accounts.*]` entry | `profiles.<p>: unknown account <n>` |
| 14 | ...and every account has the profile's adapter | `profiles.<p>: account <n> is for adapter <a>, not <profile adapter>` |
| 15 | (No separate check.) A profile can only list accounts that passed 9 and match its adapter (14), so its adapter has `login_binding` | none |
| 16 | `failover` is `"none"` or `"next"` | `profiles.<p>.failover must be "none" or "next", not <v>` |
| 17 | `"next"` needs `trusted_read` (D6) | `profiles.<p>: failover = "next" isn't supported on adapter <a>: S7 hasn't demonstrated a trusted usage read for it (ADR §4.4 D6, D9)` |
| 18 | No two credential identities alias | `accounts <n1> (<a1>) and <n2> (<a2>) share a login: <their login directories nest or are equal \| a login file resolves to the same file \| a login file is hard-linked>` |
| 18a | `usage` in the merged config is a table. A host leaf such as `usage = 1` would otherwise replace the defaults table | `usage must be a table` |
| 19 | `[usage]` keys are only the six above | `usage: unknown keys [...]` |
| 20 | `reserve_percent` is an `int` (not a `bool`), 0–50 | `usage.reserve_percent must be an integer from 0 to 50, not <v>` |
| 21 | The other five are positive `int`s (not `bool`s) | `usage.<k> must be a positive integer, not <v>` |
| 22 | `min_recheck_seconds ≤ 60 × min(stale_minutes, unknown_backoff_minutes, untrusted_max_defer_minutes)` | `usage.min_recheck_seconds (<v>) must be at most 60 × the smallest of stale_minutes, unknown_backoff_minutes and untrusted_max_defer_minutes (<bound>)` |
| 23 | `untrusted_max_defer_minutes` and `unknown_backoff_minutes` are each ≤ 60 × `max_window_hours` | `usage.<k> (<v>) must be at most 60 × max_window_hours (<bound>)` |

**Order of checks.** The `[accounts]` table runs first: 1–8, then 9. Then profiles, 10–13a, 14, 16 and 17; then aliases, 18; then usage, 18a and 19–23.

**Rule 9 fires now.** The table is empty, so any `[accounts.*]` entry fails at rule 9 today, which is D9's default ("`config check` rejects `accounts` on that adapter"). Rule 18 is reachable now in one case: the two implicit defaults alias each other, for example when `~/.codex` is a symlink to `~/.claude`.

**The existing secret scan applies to account names.** A name with a secret-word segment, such as `codex-auth` or `team-session`, is rejected by the secret scan before any of these rules ("secrets must be a reference"). This isn't special-cased. The docs say to pick another name.

## Layers (D1, §15)

- **Workstream files:** `[accounts]`, `[usage]` and `[profiles]` are already outside `WORKSTREAM_KEYS`. AU-2 adds a dedicated message, checked before the generic one:
  - `workstreams/<ws>.toml: [accounts] and [usage] are host-only (config.toml); workstreams choose profiles, never accounts`.
- **policy.toml:** these keys are already rejected as unknown policy keys. No change.
- **Environment:** `ENV_KEYS` already allows only the three location variables. No change, but a test pins it.
- **Default login location:** `CLAUDE_CONFIG_DIR` and `CODEX_HOME` in the loader's environment are **not** read when locating the implicit default. That would let the environment select a login (D1). The default is fixed relative to `HOME`. A host whose own login lives elsewhere declares it as a named account once (a) is on.
- **Bead labels** are outside config. AU-3 and AU-4 handle them.

## `Config` and `config check`

- `Config` gets the field `accounts: dict[tuple[str, str], Account]`, which the new keyword argument `load(..., capabilities=CAPABILITIES)` fills. Nothing else reads it in AU-2.
- `config check` keeps printing every leaf with its source layer, so the new keys appear like any other: `usage.reserve_percent = 5 (defaults)`.
  - The one exception is `accounts.<n>.login_dir`, which prints as `accounts.<n>.login_dir = <hidden>    (host:config.toml)`, so the key and its source still show (D10: no message shows a login path). `_flatten`'s caller matches on the key shape (`accounts`, any name, `login_dir`).
- `config check` also prints warnings, after the values, from `accounts.warnings(cfg)`:
  - D6, more than one account with failover none: `warning: profiles.<p> lists <k> accounts with failover = "none"; only the first is used (ADR §4.4 D6)`.
  - Failover next without accounts: `warning: profiles.<p> has failover = "next" but no accounts; it only ever uses the default login`.
- Warnings never change the exit code.

## Files

- `src/heterodyne/config/capabilities.py` and `src/heterodyne/config/accounts.py` (new).
- `src/heterodyne/config/__init__.py`: call `accounts.check` and `accounts.resolve_accounts`; add `Config.accounts` and the `capabilities` argument.
- `src/heterodyne/config/layers.py`: the workstream host-only message.
- `src/heterodyne/defaults/defaults.toml`: `[usage]`.
- `src/heterodyne/cli.py`: print the warnings.
- `examples/config.toml`:
  - one commented `[accounts.<name>]` block with `<placeholder>` values and the commented `accounts` and `failover` lines;
  - a note that accounts are refused until S7 demonstrates login binding for the adapter.
  - It must stay commented: `test_examples_load` loads the example.
- `docs/configuration.md`:
  - reference entries for `[accounts.*]`, `profiles.*.accounts`, `profiles.*.failover` and `[usage]`;
  - why the key is `login_dir` (the secret-name check flags `auth`);
  - that names with secret words are refused;
  - that every capability is currently off.

## Tests (`tests/test_config_accounts.py`, offline, `tmp_path` only)

Unit tests call `accounts.check` and `resolve_accounts` with an injected capability table:

```python
ON = {"codex": Capabilities(login_binding=True, trusted_read=True), "claude-code": Capabilities(login_binding=True)}
```

Integration tests go through `load(env=…)` with `HOME` set to `tmp_path`. Fake names only, such as `acct-a`, `acct-b` and `/x/login-a`. No real login dir is read.

| Test | Proves |
|---|---|
| `test_usage_defaults_load` | defaults give the six values, with source `defaults`; no accounts; `Config.accounts` holds only the two implicit defaults |
| `test_accounts_refused_while_capabilities_empty` | with the real (empty) table, one valid `[accounts.x]` is refused with the rule 9 message, and a profile listing it adds no second error |
| `test_failover_next_refused_without_trusted_read` | rule 17 on the real table, and with `ON` for `claude-code` (binding but no read) |
| `test_failover_none_accepted_everywhere` | `failover = "none"` and an absent key both load on the real table |
| `test_account_name_rules` (parametrized) | rules 2–3: `a@b`, `a/b`, `a.b`, `default`, `Default`, `1abc` refused; `acct-a`, `acct_b` accepted |
| `test_secret_word_account_name_refused_by_scan` | `codex-auth` is refused by the secret scan, not by rule 2 |
| `test_usage_not_a_table` | `usage = 1` in the host config, through `load()`, gives rule 18a |
| `test_account_shape_rules` (parametrized) | rules 1, 4–8: not a table, an unknown key, an unknown adapter, a reference `login_dir`, a non-string, empty, relative |
| `test_profile_accounts_rules` (parametrized) | rules 10–14 and 16: not a list, empty, duplicate, unknown, `default`, wrong adapter, a bad `failover` value |
| `test_alias_equal_and_nested_dirs` | rule 18: equal dirs; nested both ways; a named account nested in the implicit default (`~/.codex/team`); two accounts of different adapters on one dir; siblings `/x/login` and `/x/login-other` accepted |
| `test_alias_symlinked_dir_and_file` | rule 18 through a symlinked login dir, and through a login file that is a symlink to another account's file |
| `test_alias_hard_link` | rule 18 when two login files are hard links (`os.link`); missing files never alias by inode |
| `test_implicit_defaults_alias` | `~/.codex` symlinked to `~/.claude` is refused on the real table |
| `test_alias_stat_error_fails_closed` | an EACCES on `stat` (a 000-mode directory; skipped as root) gives the "can't be checked" error |
| `test_credential_key` | `ck1-` plus 32 hex characters; deterministic; different for another adapter or path; equal through a symlinked spelling of the same dir; changes when the symlink is repointed |
| `test_errors_never_show_login_paths` | a marker in `login_dir` (`/x/MARKER-login`) is absent from the message, and from `str(exc.__cause__)` and `exc.__context__`'s rendering, for: every rule 6–9 and 18 case; a symlink loop as the login dir; a symlink loop as a login file; a `resolve` that raises `OSError` naming the path (monkeypatched); and a NUL in `login_dir` |
| `test_config_check_hides_login_dir` (cli) | with `ON` patched in, `config check` prints `accounts.acct-a.login_dir = <hidden>` with its source, and the marker path appears nowhere in stdout or stderr; a symlink-loop login dir gives exit 1 and a path-free `config error:` line, not a traceback |
| `test_usage_rules` (parametrized) | rules 19–23: an unknown key; `reserve_percent` -1, 51, 5.0, `true`; each other key 0, -1, `"30"`, `true`; the boundaries (min_recheck = 60 × min passes, +1 fails; each defer key at 60 × max_window_hours passes, +1 fails); a host override is merged before checking |
| `test_workstream_rejects_accounts_and_usage` | `[accounts.x]` and `[usage]` in a workstream file give the host-only message; `[profiles.p]` keeps the existing message |
| `test_env_cannot_select_account` | `HETERODYNE_ACCOUNT=…` is refused by `ENV_KEYS`; `CLAUDE_CONFIG_DIR` and `CODEX_HOME` in `env` don't move the implicit defaults |
| `test_config_check_warnings` (cli) | with `ON` patched in: the D6 warning for two accounts with failover none, the next-without-accounts warning, none for one account; exit code 0; new keys printed with their source layers |
| `test_examples_load` (existing) | still passes with the commented example |

`scripts/check_install_agnostic.py` stays clean, and there are no model names in `src/`.

## Additions beyond r14 (for the reviewer)

These are small, and each fails closed or only warns:
- duplicate names in `profiles.<p>.accounts` (rule 12);
- unknown keys in `[accounts.*]` and `[usage]` (rules 4 and 19);
- `default` can't be listed in a profile (rule 13);
- `usage` must be a table (rule 18a);
- the "next without accounts" warning;
- the stat-error fail-closed rule.

## Decisions (open questions in r1; the r1 reviewer agreed with each)

1. **A profile can't list `default`** (rule 13). Omitting `accounts` already gives the default login. Mixed lists of named accounts and `default` would add D2 adoption and D7 continuity cases, so they are left out of the PoC. Allowing them later is additive.
2. **The default login location is fixed** at `~/.claude` and `~/.codex`, expanded with `paths.expand(value, env)`, the same as a named account's `~/`. `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are ignored (see Layers). A host-config key `adapters.<a>.default_login_dir` can be added later if needed.
3. **The credential key is `ck1-` plus 32 hex characters**, by the formula under "Credential identity". `config/accounts.py` holds the one function that computes it (`credential_key(adapter, login_files)`). AU-3's launch-time recomputation calls that same function, so the formula is identical at config resolution and at launch.
