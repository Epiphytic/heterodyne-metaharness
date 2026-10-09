"""The launch guard's and pickup's view of accounts (ADR 0001 r14 §4.4 D1, D2, D4, D7; AU-3 design §2.1;
AU-5 design §3.2).

They read accounts only through `Accounts`, so tests can change logins and capabilities between steps.
Keys are AU-2's `credential_key`, and every key is resolved afresh from the account's configured path (a
named account's raw `login_dir`, or `DEFAULT_LOGIN_DIRS[adapter]` for `default`), expanded with the
supplied environment's HOME. The canonical paths AU-2 stored at config load are never rehashed: once
`~/.codex` resolved to one login, repointing it to another would be invisible through them.

`view` resolves a profile for the headroom gate (`headroom.py`): its adapter, failover mode, accounts in
order with their keys as of now, and the adapter's capabilities.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from heterodyne.config import Config, ConfigError, paths
from heterodyne.config.accounts import Account, canonical_login, credential_key
from heterodyne.config.capabilities import CAPABILITIES, DEFAULT_LOGIN_DIRS, LOGIN_FILES, NONE, Capabilities
from heterodyne.config.layers import table_at
from heterodyne.config.secret_scan import show

DEFAULT = "default"
type Failover = Literal["none", "next"]


@dataclass(frozen=True)
class Chosen:
    account: str
    key: str


@dataclass(frozen=True)
class AccountChanged:
    """The session's last launched key is not the key it would launch on now, and the adapter can't
    switch accounts (D7). AU-4 replaces this interim stop with its `account_changed` deferral."""
    detail: str


@dataclass(frozen=True)
class Candidate:
    account: str
    key: str                       # the current credential key, resolved now


@dataclass(frozen=True)
class ProfileView:
    """A profile as the gate sees it now (D4): its accounts in order, `(default,)` when it lists none."""
    profile: str
    adapter: str
    failover: Failover
    accounts: tuple[Candidate, ...]
    capabilities: Capabilities


@dataclass(frozen=True)
class ProfileAccounts:
    """One profile's account settings: its adapter, `accounts` (empty: the adapter's `default` login) and
    `failover`."""
    adapter: str
    accounts: tuple[str, ...] = ()
    failover: Failover = "none"


class Accounts(Protocol):
    def adapter(self, profile: str) -> str | None:
        """The profile's adapter, or None if the profile no longer exists."""
        ...

    def configured(self, adapter: str) -> tuple[str, ...]:
        """The adapter's named accounts, `default` excluded."""
        ...

    def current_key(self, adapter: str, account: str) -> str:
        """AU-2's credential key, recomputed now from the configured path. Raises ConfigError (path-free)
        if the login can't be resolved."""
        ...

    def login_resolves(self, adapter: str, account: str) -> bool:
        """Adoption only (D2): every login file resolves, strictly, to a regular file."""
        ...

    def view(self, profile: str) -> ProfileView:
        """The profile resolved now. Raises ConfigError (path-free) if the profile is gone or a listed
        account's key can't be resolved."""
        ...


class ConfiguredAccounts:
    """The configured accounts. `profiles` maps each profile to its adapter alone (its `default` login,
    failover "none") or to its full `ProfileAccounts`."""

    def __init__(self, profiles: Mapping[str, str | ProfileAccounts], env: Mapping[str, str],
                 named: Mapping[tuple[str, str], Account] | None = None,
                 capabilities: Mapping[str, Capabilities] | None = None) -> None:
        self.profiles = {p: ProfileAccounts(v) if isinstance(v, str) else v for p, v in profiles.items()}
        self.env = dict(env)
        self.named = {k: v for k, v in (named or {}).items() if k[1] != DEFAULT}
        self.capabilities = CAPABILITIES if capabilities is None else capabilities

    @classmethod
    def from_config(cls, cfg: Config, env: Mapping[str, str]) -> "ConfiguredAccounts":
        """From a loaded config (AU-2 has validated it): each profile's adapter, accounts and failover,
        and AU-2's resolved accounts."""
        profiles: dict[str, str | ProfileAccounts] = {}
        for name, value in table_at(cfg.values, "profiles", "config.toml").items():
            p = cast(Mapping[str, Any], value)
            profiles[name] = ProfileAccounts(str(p["adapter"]), tuple(cast(list[str], p.get("accounts", []))),
                                             cast(Failover, p.get("failover", "none")))
        return cls(profiles, env, cfg.accounts)

    def adapter(self, profile: str) -> str | None:
        found = self.profiles.get(profile)
        return None if found is None else found.adapter

    def _configured_dir(self, adapter: str, account: str) -> str:
        if account == DEFAULT:
            return DEFAULT_LOGIN_DIRS[adapter]
        found = self.named.get((adapter, account))
        if found is None:
            raise ConfigError(f"account {show(account, False)} is not configured for adapter {adapter}")
        return found.configured_dir

    def configured(self, adapter: str) -> tuple[str, ...]:
        return tuple(name for (a, name) in self.named if a == adapter)

    def current_key(self, adapter: str, account: str) -> str:
        _, files = canonical_login(account, adapter, self._configured_dir(adapter, account), self.env)
        return credential_key(adapter, files)

    def login_resolves(self, adapter: str, account: str) -> bool:
        try:
            login_dir = paths.expand(self._configured_dir(adapter, account), self.env)
            return all((login_dir / f).resolve(strict=True).is_file() for f in LOGIN_FILES[adapter])
        except (OSError, RuntimeError, ValueError, KeyError, ConfigError):
            return False

    def view(self, profile: str) -> ProfileView:
        spec = self.profiles.get(profile)
        if spec is None:
            raise ConfigError(f"profile {profile} no longer exists")
        names = spec.accounts or (DEFAULT,)
        return ProfileView(profile, spec.adapter, spec.failover,
                           tuple(Candidate(n, self.current_key(spec.adapter, n)) for n in names),
                           self.capabilities.get(spec.adapter, NONE))
