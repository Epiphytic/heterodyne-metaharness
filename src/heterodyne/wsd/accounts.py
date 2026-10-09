"""The launch guard's view of accounts (ADR 0001 r14 §4.4 D1, D2, D7; AU-3 design §2.1).

The guard reads accounts only through `Accounts`, so tests can change eligibility and logins between
steps. Keys are AU-2's `credential_key`, and every key is resolved afresh from the account's configured
path (a named account's raw `login_dir`, or `DEFAULT_LOGIN_DIRS[adapter]` for `default`), expanded with
the supplied environment's HOME. The canonical paths AU-2 stored at config load are never rehashed: once
`~/.codex` resolved to one login, repointing it to another would be invisible through them.

`DefaultOnly` is AU-3's implementation: every launch uses `default` until AU-6 enables login binding, and
AU-5 replaces `choose` with its gate at the same call site.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from heterodyne.config import Config, paths
from heterodyne.config.accounts import Account, canonical_login, credential_key
from heterodyne.config.capabilities import CAPABILITIES, DEFAULT_LOGIN_DIRS, LOGIN_FILES, NONE, Capabilities
from heterodyne.config.layers import table_at

DEFAULT = "default"


@dataclass(frozen=True)
class Chosen:
    account: str
    key: str


@dataclass(frozen=True)
class AccountChanged:
    """The session's last launched key is not the key it would launch on now, and the adapter can't
    switch accounts (D7). AU-4 replaces this interim stop with its `account_changed` deferral."""
    detail: str


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

    def eligible(self, profile: str, account: str) -> bool:
        """Whether the account is still permitted for the profile."""
        ...

    def choose(self, profile: str, previous_key: str | None) -> Chosen | AccountChanged:
        """The account a new generation pins. `previous_key` is the key of the session's highest launched
        generation, or None if it never launched."""
        ...


class DefaultOnly:
    """Every launch uses the adapter's `default` login. `profiles` maps each profile to its adapter."""

    def __init__(self, profiles: Mapping[str, str], env: Mapping[str, str],
                 named: Mapping[tuple[str, str], Account] | None = None,
                 capabilities: Mapping[str, Capabilities] | None = None) -> None:
        self.profiles = dict(profiles)
        self.env = dict(env)
        self.named = {k: v for k, v in (named or {}).items() if k[1] != DEFAULT}
        self.capabilities = CAPABILITIES if capabilities is None else capabilities

    @classmethod
    def from_config(cls, cfg: Config, env: Mapping[str, str]) -> "DefaultOnly":
        """From a loaded config: its profiles' adapters and AU-2's resolved accounts."""
        profiles = {name: str(cast(Mapping[str, Any], value)["adapter"])
                    for name, value in table_at(cfg.values, "profiles", "config.toml").items()}
        return cls(profiles, env, cfg.accounts)

    def adapter(self, profile: str) -> str | None:
        return self.profiles.get(profile)

    def _configured_dir(self, adapter: str, account: str) -> str:
        if account == DEFAULT:
            return DEFAULT_LOGIN_DIRS[adapter]
        return self.named[(adapter, account)].configured_dir

    def configured(self, adapter: str) -> tuple[str, ...]:
        return tuple(name for (a, name) in self.named if a == adapter)

    def current_key(self, adapter: str, account: str) -> str:
        _, files = canonical_login(account, adapter, self._configured_dir(adapter, account), self.env)
        return credential_key(adapter, files)

    def login_resolves(self, adapter: str, account: str) -> bool:
        try:
            login_dir = paths.expand(self._configured_dir(adapter, account), self.env)
            return all((login_dir / f).resolve(strict=True).is_file() for f in LOGIN_FILES[adapter])
        except (OSError, RuntimeError, ValueError, KeyError):
            return False

    def eligible(self, profile: str, account: str) -> bool:
        return account == DEFAULT and profile in self.profiles

    def choose(self, profile: str, previous_key: str | None) -> Chosen | AccountChanged:
        adapter = self.profiles[profile]
        key = self.current_key(adapter, DEFAULT)
        can_switch = self.capabilities.get(adapter, NONE).can_switch
        if previous_key is not None and previous_key != key and not can_switch:
            return AccountChanged(f"the {adapter} default login's credential key changed since the session's "
                                  "last launch, and the adapter can't switch accounts")
        return Chosen(DEFAULT, key)

