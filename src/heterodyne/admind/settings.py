"""admind settings from the merged host config and the host policy (ADR 0001 §8, §15).

`[admind]` lives in host `config.toml` only: the workstream layer rejects it (it is not one of
`WORKSTREAM_KEYS`), and environment overrides cover locations only. The operators come from
`policy.toml` (`operators`, then `identities.<name>.marmot_npub`), which is host-only; every entry with an
npub is an operator.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from heterodyne.config import Account, Config, ConfigError, paths
from heterodyne.config.layers import as_table, string_list, table_at
from heterodyne.config.secret_scan import show
from heterodyne.marmot.nip19 import Nip19Error, npub_to_hex
from heterodyne.services import UNIT_NAME

ADMIND_KEYS = frozenset({"profile", "workdir", "restart_units", "chunk_chars", "alert_poll_seconds",
                         "group_check_seconds", "start_timeout_seconds", "turn_notice_seconds", "group_name",
                         "summarizer", "reply_verbatim_lines", "reply_verbatim_chars", "marmot",
                         "approve_bead", "ask_bump_hours"})
MARMOT_KEYS = frozenset({"wn_agent", "home", "relays"})
ADMIN_ADAPTERS = ("claude-code",)
MAX_NAME = 128
NAME_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_SOCKET_PATH = 100  # bytes; sun_path is 108 on Linux and 104 on macOS
# The variable that selects each adapter's login directory (ADR §4.4 D11).
SELECTOR = {"claude-code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
# Variables that log the CLI in without its login files (S7 §17 #5). Codex's list is provisional until S7's
# real-account run; Codex is not an admin adapter yet.
LOGIN_OVERRIDES = {"claude-code": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
                   "codex": ("OPENAI_API_KEY", "CODEX_API_KEY")}


@dataclass(frozen=True)
class Operator:
    name: str
    npub: str
    hex: str


@dataclass(frozen=True)
class AdmindAccount:
    """The login one admind process runs on (ADR §4.4 D11): its profile's first account, or `default`."""
    name: str                       # "default" when the profile lists no accounts
    key: str                        # AU-2 credential key (ck1-..., 32 hex: never trips the 64-hex redaction)
    set: Mapping[str, str]          # {} for default; else {SELECTOR[adapter]: canonical login_dir}
    unset: tuple[str, ...]          # SELECTOR[adapter] for default; LOGIN_OVERRIDES[adapter] for an account


@dataclass(frozen=True)
class AdmindSettings:
    profile: Mapping[str, Any]
    adapter_binary: str
    summarizer: Mapping[str, Any] | None
    summarizer_binary: str | None
    reply_verbatim_lines: int
    reply_verbatim_chars: int
    workdir: Path
    restart_units: tuple[str, ...]
    chunk_chars: int
    alert_poll_seconds: float
    group_check_seconds: float
    start_timeout_seconds: float
    turn_notice_seconds: float
    group_name: str
    wn_agent: str
    marmot_home: Path
    relays: tuple[str, ...]
    operators: tuple[Operator, ...]
    state_dir: Path
    alerts_dir: Path
    service_manager: str
    agent_account: AdmindAccount
    summarizer_account: AdmindAccount | None = None
    login_dirs: tuple[str, ...] = ()    # every named account's login directory, as redact matches it
    approve_bead: Path | None = None    # approval asks are refused without it (relay spec R5)
    ask_bump_hours: int = 12            # open asks are bumped after this many quiet hours; 0 never (B14)


def resolve(cfg: Config, env: Mapping[str, str]) -> AdmindSettings:
    admind = table_at(cfg.values, "admind", "config")
    _only(admind, ADMIND_KEYS, "[admind]")
    marmot = table_at(admind, "marmot", "config: admind")
    _only(marmot, MARMOT_KEYS, "[admind.marmot]")

    profile_name = admind.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        raise ConfigError("[admind] profile must name a profile from [profiles]")
    profile, binary, agent_account = _profile(cfg, profile_name, "profile")
    summarizer_name = admind.get("summarizer")
    summarizer, summarizer_binary, summarizer_account = (None, None, None) if summarizer_name is None else \
        _profile(cfg, summarizer_name, "summarizer")
    for p, account in ((profile, agent_account), (summarizer, summarizer_account)):
        if p is not None and account is not None:
            _check_selector(p["adapter"], account, env)

    units = tuple(string_list(admind.get("restart_units", []), "[admind] restart_units"))
    for unit in units:
        if not UNIT_NAME.fullmatch(unit):
            raise ConfigError(f"[admind] restart_units: {show(unit)} is not a unit name")

    relays = tuple(string_list(marmot.get("relays", []), "[admind.marmot] relays"))
    if not relays or not all(_relay_ok(r) for r in relays):
        raise ConfigError("[admind.marmot] relays must be a non-empty list of ws:// or wss:// URLs")

    state = paths.state_dir(env)
    home_value = marmot.get("home")
    home = (state / "admind" / "marmot" if home_value is None
            else _abs(home_value, env, "[admind.marmot] home"))
    if len(str(home / "ctl" / "wn-agent.sock").encode()) > MAX_SOCKET_PATH:
        raise ConfigError(f"[admind.marmot] home is too long for a Unix socket path "
                          f"(max {MAX_SOCKET_PATH} bytes including ctl/wn-agent.sock)")

    service_manager = cfg.get("platform.service_manager")
    if not isinstance(service_manager, str) or not service_manager:
        raise ConfigError("[platform] service_manager is not set; run `heterodyne setup`")

    return AdmindSettings(
        profile=profile, adapter_binary=binary,
        summarizer=summarizer, summarizer_binary=summarizer_binary,
        reply_verbatim_lines=_int(admind, "reply_verbatim_lines", 1, 200),
        reply_verbatim_chars=_int(admind, "reply_verbatim_chars", 50, 60000),
        workdir=_abs(admind.get("workdir", "~"), env, "[admind] workdir"),
        restart_units=units,
        chunk_chars=_int(admind, "chunk_chars", 200, 60000),
        alert_poll_seconds=_seconds(admind, "alert_poll_seconds"),
        group_check_seconds=_seconds(admind, "group_check_seconds"),
        start_timeout_seconds=_seconds(admind, "start_timeout_seconds"),
        turn_notice_seconds=_seconds(admind, "turn_notice_seconds"),
        group_name=_text(admind, "group_name", "[admind]"),
        wn_agent=_text(marmot, "wn_agent", "[admind.marmot]"),
        marmot_home=home, relays=relays,
        operators=operators(cfg),
        state_dir=state / "admind", alerts_dir=state / "alerts",
        service_manager=service_manager,
        agent_account=agent_account, summarizer_account=summarizer_account,
        login_dirs=login_dirs(cfg, env),
        approve_bead=_executable(admind.get("approve_bead")),
        ask_bump_hours=_int(admind, "ask_bump_hours", 0, 720),
    )


def _profile(cfg: Config, name: Any, key: str) -> tuple[Mapping[str, Any], str, AdmindAccount]:
    """The profile `[admind] {key}` names, its adapter's binary (Claude Code only, so far) and the login it
    runs on: its first account, or the adapter's `default` (D11: no headroom gate, no failover)."""
    if not isinstance(name, str) or not name:
        raise ConfigError(f"[admind] {key} must name a profile from [profiles]")
    profile = as_table(table_at(cfg.values, "profiles", "config").get(name))
    if profile is None:
        raise ConfigError(f"[admind] {key} {show(name)} is not defined in [profiles]")
    adapter = profile.get("adapter")
    if adapter not in ADMIN_ADAPTERS:
        raise ConfigError(f"[admind] {key} {show(name)} uses adapter {show(adapter)}; "
                          f"the admin agent supports {list(ADMIN_ADAPTERS)} so far")
    binary = cfg.get(f"adapters.{adapter}.binary")
    if not isinstance(binary, str) or not binary:
        raise ConfigError(f"adapters.{adapter}.binary must be a non-empty string")
    listed = profile.get("accounts")
    first: object = cast(list[Any], listed)[0] if isinstance(listed, list) and listed else "default"
    if not isinstance(first, str):      # config.load already refuses this
        raise ConfigError(f"[admind] {key} {show(name)}: its accounts must be names")
    account = cfg.accounts.get((adapter, first))
    if account is None:     # config.load resolves every listed account and each adapter's default
        raise ConfigError(f"[admind] {key} {show(name)}: its login was not resolved")
    return profile, binary, admind_account(account)


def admind_account(account: Account) -> AdmindAccount:
    selector = SELECTOR[account.adapter]
    if account.name == "default":
        return AdmindAccount(account.name, account.key, {}, (selector,))
    return AdmindAccount(account.name, account.key, {selector: str(account.login_dir)},
                         LOGIN_OVERRIDES[account.adapter])


def _check_selector(adapter: str, account: AdmindAccount, env: Mapping[str, str]) -> None:
    """A process on `default` runs on AU-2's fixed directory, so admind's own environment may not select
    another one: the recorded credential key would name the wrong directory (§4.4 D1). A named account
    sets the selector itself. The message names the variable, never its value."""
    selector = SELECTOR[adapter]
    if account.name == "default" and selector in env:
        raise ConfigError(f"admind's environment sets {selector}; logins are chosen by [accounts] in "
                          "config.toml (ADR §4.4 D1). Unset it, or configure an account")


def login_dirs(cfg: Config, env: Mapping[str, str]) -> tuple[str, ...]:
    """The literal forms of every named account's login directory (configured, `~`-expanded, canonical),
    longest first, for `redact`. Every account, not only admind's: the agent can read the whole config.
    The `default` directories are not included (a D10 exception, approved with the design)."""
    forms: set[str] = set()
    for account in cfg.accounts.values():
        if account.name != "default":
            forms |= {account.configured_dir, str(paths.expand(account.configured_dir, env)),
                      str(account.login_dir)}
    return tuple(sorted(forms, key=lambda f: (-len(f), f)))


def _relay_ok(r: str) -> bool:
    if any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in r):
        return False
    try:
        parts = urlsplit(r)
        parts.port  # noqa: B018 - raises ValueError on a non-numeric or out-of-range port
        return parts.scheme in ("ws", "wss") and bool(parts.hostname)
    except ValueError:
        return False


def operators(cfg: Config) -> tuple[Operator, ...]:
    """Every `policy.toml` operator with `identities.<name>.marmot_npub` (ADR 0001 §8, revision 13).
    Its name must be one `admind operators add|remove NAME` can carry: 1 to `MAX_NAME` characters and no
    control characters (the control server checks the same, Task 6)."""
    found: list[Operator] = []
    seen: set[str] = set()
    for name in cfg.policy.operators:
        npub = cfg.policy.identities.get(name, {}).get("marmot_npub")
        if not npub:
            continue
        if not 1 <= len(name) <= MAX_NAME or NAME_CONTROLS.search(name):
            raise ConfigError(f"policy.toml: operator name {show(name, False)!r} must be 1-{MAX_NAME} "
                              "characters with no control characters")
        where = f"policy.toml: identities.{show(name, False)}.marmot_npub"
        try:
            key = npub_to_hex(npub).lower()
        except Nip19Error as exc:
            raise ConfigError(f"{where} is not a valid npub ({exc})") from None
        if key in seen:
            raise ConfigError(f"{where} is the same marmot_npub as another operator's")
        seen.add(key)
        found.append(Operator(name, npub, key))
    if not found:
        raise ConfigError("policy.toml: admind needs at least one entry in operators with "
                          "identities.<name>.marmot_npub")
    return tuple(found)


def _only(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {show(unknown)} (allowed: {sorted(allowed)})")


def _abs(value: Any, env: Mapping[str, str], where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a path")
    path = paths.expand(value, env)
    if not path.is_absolute():
        raise ConfigError(f"{where} must be an absolute path or start with ~/")
    return path


def _executable(value: Any) -> Path | None:
    """`[admind] approve_bead`: optional; an absolute path to an existing executable file. The value is
    never echoed."""
    if value is None:
        return None
    path = Path(value) if isinstance(value, str) and value else None
    if path is None or not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ConfigError("[admind] approve_bead must be an absolute path to an executable")
    return path


def _int(table: Mapping[str, Any], key: str, lo: int, hi: int) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise ConfigError(f"[admind] {key} must be an integer from {lo} to {hi}")
    return value


def _seconds(table: Mapping[str, Any], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 3600:
        raise ConfigError(f"[admind] {key} must be a number of seconds, more than 0 and at most 3600")
    return float(value)


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} {key} must be a non-empty string")
    return value
