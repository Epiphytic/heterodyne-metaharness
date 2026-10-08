"""Accounts, profile accounts, failover and [usage] (ADR 0001 §4.1, §4.4 D1, D6, D7, D9).

No error here quotes a login directory or login file path (D10): wsd reload errors may reach the control
group. Filesystem failures are re-raised outside their `except` block, so the original exception, whose
message holds the path, is never attached as `__context__`.
"""

import errno
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, cast

from heterodyne.config import paths, secret_scan
from heterodyne.config.capabilities import DEFAULT_LOGIN_DIRS, LOGIN_FILES, NONE, Capabilities
from heterodyne.config.errors import ConfigError
from heterodyne.config.layers import as_table, string_list, table_at
from heterodyne.config.secret_scan import show

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
ACCOUNT_KEYS = ("adapter", "login_dir")
FAILOVER = ("none", "next")
USAGE_KEYS = ("reserve_percent", "stale_minutes", "unknown_backoff_minutes", "untrusted_max_defer_minutes",
              "min_recheck_seconds", "max_window_hours")


@dataclass(frozen=True)
class Account:
    name: str                       # "default" for the implicit account
    adapter: str
    configured_dir: str             # as configured (or the DEFAULT_LOGIN_DIRS entry), before expansion
    login_dir: Path                 # canonical: ~ expanded, symlinks resolved (strict=False)
    login_files: tuple[Path, ...]   # canonical, in LOGIN_FILES order
    key: str                        # credential key


def credential_key(adapter: str, login_files: tuple[Path, ...]) -> str:
    """The credential key (D1): a digest of the adapter and the canonical login files. 32 hex characters,
    not 64, so it never trips the 64-hex identifier redaction. AU-3 recomputes it at launch with this."""
    identity = "\0".join([adapter, *map(str, login_files)])
    return "ck1-" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def _reason(exc: BaseException) -> str:
    code = exc.errno if isinstance(exc, OSError) else None
    return errno.errorcode.get(code, type(exc).__name__) if code else type(exc).__name__


def canonical_login(name: str, adapter: str, configured_dir: str,
                    env: Mapping[str, str]) -> tuple[Path, tuple[Path, ...]]:
    """The canonical login directory and login files of an account, resolved now. Raises a path-free
    ConfigError if resolution fails (a symlink loop, an embedded NUL, an OS error)."""
    try:
        login_dir = paths.expand(configured_dir, env).resolve(strict=False)
        return login_dir, tuple((login_dir / f).resolve(strict=False) for f in LOGIN_FILES[adapter])
    except (OSError, RuntimeError, ValueError) as exc:
        reason = _reason(exc)
    raise ConfigError(f"account {show(name, False)} ({adapter}): login directory can't be resolved "
                      f"({reason})")


def _file_id(account: Account, path: Path) -> tuple[int, int] | None:
    """(st_dev, st_ino) of a login file, or None if it doesn't exist. Any other failure fails closed."""
    try:
        st = path.stat()
        return st.st_dev, st.st_ino
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        reason = _reason(exc)
    raise ConfigError(f"account {show(account.name, False)}: login file can't be checked for aliases "
                      f"({reason})")


def _named_accounts(merged: Mapping[str, Any], known: list[str],
                    capabilities: Mapping[str, Capabilities]) -> dict[str, tuple[str, str]]:
    """Rules 1-9: each [accounts.<name>] as name -> (adapter, configured login_dir)."""
    value = merged.get("accounts", {})
    table = as_table(value)
    if table is None:
        raise ConfigError("accounts must be a table")
    found: dict[str, tuple[str, str]] = {}
    for name, entry in table.items():
        where = f"accounts.{show(name, False)}"
        account = as_table(entry)
        if account is None:
            raise ConfigError(f"{where} must be a table")
        if not NAME.fullmatch(name):
            raise ConfigError(f"{where}: account names are plain identifiers (letters, digits, - and _; "
                              "no @ or path separators)")
        if name.casefold() == "default":
            raise ConfigError(f'{where}: "default" is reserved for the adapter\'s own login; '
                              "omit accounts to use it")
        unknown = set(account) - set(ACCOUNT_KEYS)
        if unknown:
            raise ConfigError(f"{where}: unknown keys {show(sorted(unknown))} (allowed: adapter, login_dir)")
        adapter = account.get("adapter")
        if not isinstance(adapter, str) or adapter not in known:
            raise ConfigError(f"{where}: adapter {show(adapter)} is not one of {sorted(known)}")
        login_dir = account.get("login_dir")
        if secret_scan.is_reference(login_dir):
            raise ConfigError(f"{where}.login_dir is a path, not a secret; a {{ file }} or {{ command }} "
                              "reference isn't allowed")
        if not isinstance(login_dir, str) or not login_dir:
            raise ConfigError(f"{where}.login_dir must be a string")
        if not (Path(login_dir).is_absolute() or login_dir.startswith("~/")):
            raise ConfigError(f"{where}.login_dir must be an absolute path or start with ~/")
        if not capabilities.get(adapter, NONE).login_binding:
            raise ConfigError(f"{where}: accounts aren't supported on adapter {adapter} yet: S7 hasn't "
                              "demonstrated login binding for it (ADR §4.4 D9)")
        found[name] = (adapter, login_dir)
    return found


def _check_profiles(merged: Mapping[str, Any], named: Mapping[str, tuple[str, str]],
                    capabilities: Mapping[str, Capabilities]) -> None:
    """Rules 10-17. `check_profiles` has already checked each profile is a table with a known adapter."""
    for p, value in table_at(merged, "profiles", "config.toml").items():
        profile = cast(Mapping[str, Any], value)
        where = f"profiles.{show(p, False)}"
        adapter = cast(str, profile["adapter"])
        if "accounts" in profile:
            listed = profile["accounts"]
            if not isinstance(listed, list) or not all(isinstance(n, str) for n in cast(list[Any], listed)):
                raise ConfigError(f"{where}.accounts must be a list of account names")
            names = cast(list[str], listed)
            if not names:
                raise ConfigError(f"{where}.accounts is empty; omit it to use the adapter's default login")
            for n in names:
                if names.count(n) > 1:
                    raise ConfigError(f"{where}.accounts lists {show(n, False)} more than once")
                if n == "default":
                    raise ConfigError(f'{where}: "default" can\'t be listed; omit accounts to use the '
                                      "adapter's default login")
                if n not in named:
                    raise ConfigError(f"{where}: unknown account {show(n, False)}")
                if named[n][0] != adapter:
                    raise ConfigError(f"{where}: account {show(n, False)} is for adapter {named[n][0]}, "
                                      f"not {adapter}")
        failover = profile.get("failover", "none")
        if not isinstance(failover, str) or failover not in FAILOVER:
            raise ConfigError(f'{where}.failover must be "none" or "next", not {show(failover)}')
        if failover == "next" and not capabilities.get(adapter, NONE).trusted_read:
            raise ConfigError(f'{where}: failover = "next" isn\'t supported on adapter {adapter}: S7 hasn\'t '
                              "demonstrated a trusted usage read for it (ADR §4.4 D6, D9)")


def _alias(a: Account, b: Account) -> str | None:
    if a.login_dir.is_relative_to(b.login_dir) or b.login_dir.is_relative_to(a.login_dir):
        return "their login directories nest or are equal"
    if set(a.login_files) & set(b.login_files):
        return "a login file resolves to the same file"
    ids_a = {i for f in a.login_files if (i := _file_id(a, f)) is not None}
    ids_b = {i for f in b.login_files if (i := _file_id(b, f)) is not None}
    if ids_a & ids_b:
        return "a login file is hard-linked"
    return None


def resolve_accounts(merged: Mapping[str, Any], env: Mapping[str, str],
                     capabilities: Mapping[str, Capabilities]) -> dict[tuple[str, str], Account]:
    """All accounts keyed by (adapter, name), the implicit default of every known adapter included.
    Raises ConfigError on the first violation of rules 1-18."""
    known = string_list(table_at(merged, "adapters", "config").get("known", []), "adapters.known")
    for adapter in known:
        if adapter not in LOGIN_FILES or adapter not in DEFAULT_LOGIN_DIRS:
            raise ConfigError(f"adapter {show(adapter, False)} has no login file set")
    named = _named_accounts(merged, known, capabilities)
    _check_profiles(merged, named, capabilities)
    configured = [("default", a, DEFAULT_LOGIN_DIRS[a]) for a in sorted(known)]
    configured += [(n, a, d) for n, (a, d) in named.items()]
    found: dict[tuple[str, str], Account] = {}
    for name, adapter, configured_dir in configured:
        login_dir, login_files = canonical_login(name, adapter, configured_dir, env)
        found[(adapter, name)] = Account(name, adapter, configured_dir, login_dir, login_files,
                                         credential_key(adapter, login_files))
    for a, b in combinations(found.values(), 2):
        why = _alias(a, b)
        if why:
            raise ConfigError(f"accounts {show(a.name, False)} ({a.adapter}) and {show(b.name, False)} "
                              f"({b.adapter}) share a login: {why}")
    return found


def _check_usage(merged: Mapping[str, Any]) -> None:
    """Rules 18a-23."""
    usage = as_table(merged.get("usage", {}))
    if usage is None:
        raise ConfigError("usage must be a table")
    unknown = set(usage) - set(USAGE_KEYS)
    if unknown:
        raise ConfigError(f"usage: unknown keys {show(sorted(unknown))}")
    for key in USAGE_KEYS:
        value = usage.get(key)
        is_int = isinstance(value, int) and not isinstance(value, bool)
        if key == "reserve_percent":
            if not is_int or not 0 <= cast(int, value) <= 50:
                raise ConfigError(f"usage.reserve_percent must be an integer from 0 to 50, not {show(value)}")
        elif not is_int or cast(int, value) <= 0:
            raise ConfigError(f"usage.{key} must be a positive integer, not {show(value)}")
    u = cast(Mapping[str, int], usage)
    bound = 60 * min(u["stale_minutes"], u["unknown_backoff_minutes"], u["untrusted_max_defer_minutes"])
    if u["min_recheck_seconds"] > bound:
        raise ConfigError(f"usage.min_recheck_seconds ({u['min_recheck_seconds']}) must be at most 60 × the "
                          "smallest of stale_minutes, unknown_backoff_minutes and "
                          f"untrusted_max_defer_minutes ({bound})")
    for key in ("untrusted_max_defer_minutes", "unknown_backoff_minutes"):
        if u[key] > 60 * u["max_window_hours"]:
            raise ConfigError(f"usage.{key} ({u[key]}) must be at most 60 × max_window_hours "
                              f"({60 * u['max_window_hours']})")


def check(merged: Mapping[str, Any], env: Mapping[str, str],
          capabilities: Mapping[str, Capabilities]) -> dict[tuple[str, str], Account]:
    """Every AU-2 rule, in order; returns the resolved accounts."""
    found = resolve_accounts(merged, env, capabilities)
    _check_usage(merged)
    return found


def warnings(values: Mapping[str, Any]) -> list[str]:
    """`config check` warnings (never errors)."""
    out: list[str] = []
    for p, value in table_at(values, "profiles", "config.toml").items():
        profile = cast(Mapping[str, Any], value)
        listed = cast(list[str], profile.get("accounts", []))
        failover = profile.get("failover", "none")
        if len(listed) > 1 and failover == "none":
            out.append(f'warning: profiles.{show(p, False)} lists {len(listed)} accounts with failover = '
                       '"none"; only the first is used (ADR §4.4 D6)')
        if failover == "next" and not listed:
            out.append(f'warning: profiles.{show(p, False)} has failover = "next" but no accounts; it only '
                       "ever uses the default login")
    return out
