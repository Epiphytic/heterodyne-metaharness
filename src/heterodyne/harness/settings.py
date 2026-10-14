"""`[harnesses]` and `[harnesses.<adapter>]` (ADR 0001 §7 Managed installs, §15): where the store is, how
`wsd` reaches its helper, and each adapter's recipe. Host only: a workstream layer that sets any of them is
refused (`layers.check_workstream`). Errors name keys, never values."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heterodyne.config import Config, ConfigError, paths
from heterodyne.config.layers import as_table, string_list, table_at
from heterodyne.config.secret_scan import show

WHERE = "[harnesses]"
HOST_KEYS = frozenset({"store", "fallback", "owner", "helper_socket", "helper_copy", "staging_hours",
                       "approval_workstream"})
RECIPE_KEYS = frozenset({"version", "target", "content", "defaults"})
SOURCE_KEYS = frozenset({"kind", "name", "git", "commit", "npm", "version"})
STORE = Path("/var/lib/heterodyne/harnesses")   # the store owner's store; the fallback's is in data_dir
FALLBACK_WARNING = ("managed installs use the fallback: a host process of the service user can change an "
                    "install after its checks (accepted risk, ADR §17 item 13)")
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?")
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")          # a full sha1 or sha256 commit ID
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")            # a content kind or name: one path component
TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
OWNER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")              # a POSIX user name
WORKSTREAM = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")     # wsd.ids.SLUG


@dataclass(frozen=True)
class ContentSource:
    """One content item: a git commit (`git` is the repository, `commit` its full ID) or an npm package
    (`npm`) at an exact `version`."""
    kind: str
    name: str
    git: str | None
    commit: str | None
    npm: str | None
    version: str | None


@dataclass(frozen=True)
class Recipe:
    version: str
    target: str
    content: tuple[ContentSource, ...]
    defaults: str | None


@dataclass(frozen=True)
class HarnessSettings:
    store: Path
    fallback: bool
    owner: str
    helper_socket: Path
    helper_copy: Path
    staging_hours: int
    approval_workstream: str                 # "" when unset: `install` refuses without it (Q4)
    recipes: Mapping[str, Recipe]


def from_config(cfg: Config, env: Mapping[str, str]) -> HarnessSettings:
    table = table_at(cfg.values, "harnesses", "config")
    known = string_list(table_at(cfg.values, "adapters", "config").get("known", []), "adapters.known")
    scalars = {key for key, value in table.items() if as_table(value) is None}
    unknown = (scalars - HOST_KEYS) | (set(table) - scalars - set(known))
    if unknown:
        raise ConfigError(f"{WHERE} unknown keys {show(sorted(unknown))} (allowed: {sorted(HOST_KEYS)}, "
                          "and one table per adapter in adapters.known)")
    fallback = table.get("fallback", False)
    if not isinstance(fallback, bool):
        raise ConfigError(f"{WHERE} fallback must be true or false")
    if "store" in table:
        store = _path(table, "store", env)
    else:
        store = paths.data_dir(env) / "harnesses" if fallback else STORE
    hours = table.get("staging_hours")
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 168:
        raise ConfigError(f"{WHERE} staging_hours must be an integer from 1 to 168")
    owner = table.get("owner")
    if not isinstance(owner, str) or not OWNER.fullmatch(owner):
        raise ConfigError(f"{WHERE} owner must be a user name")
    workstream = table.get("approval_workstream", "")
    if not isinstance(workstream, str) or (workstream and not WORKSTREAM.fullmatch(workstream)):
        raise ConfigError(f"{WHERE} approval_workstream must be a workstream name")
    return HarnessSettings(
        store=store,
        fallback=fallback,
        owner=owner,
        helper_socket=_path(table, "helper_socket", env),
        helper_copy=_path(table, "helper_copy", env),
        staging_hours=hours,
        approval_workstream=workstream,
        recipes={adapter: _recipe(adapter, table[adapter]) for adapter in sorted(set(table) - scalars)})


def _path(table: Mapping[str, Any], key: str, env: Mapping[str, str]) -> Path:
    value = table.get(key)
    path = paths.expand(value, env) if isinstance(value, str) and "\0" not in value else None
    if path is None or not path.is_absolute():
        raise ConfigError(f"{WHERE} {key} must be an absolute path")
    return path


def _recipe(adapter: str, value: Any) -> Recipe:
    where = f"[harnesses.{adapter}]"
    table = as_table(value) or {}
    unknown = set(table) - RECIPE_KEYS
    if unknown:
        raise ConfigError(f"{where} unknown keys {show(sorted(unknown))} (allowed: {sorted(RECIPE_KEYS)})")
    version = table.get("version")
    if not isinstance(version, str) or not EXACT_VERSION.fullmatch(version):
        raise ConfigError(f"{where} version must be an exact version (1.2.3 or 1.2.3-pre)")
    target = table.get("target")
    if not isinstance(target, str) or not TARGET.fullmatch(target):
        raise ConfigError(f"{where} target must be a platform target name")
    defaults = table.get("defaults")
    if defaults is not None and not _text(defaults):
        raise ConfigError(f"{where} defaults must be a non-empty string")
    content = table.get("content", [])
    if not isinstance(content, list):
        raise ConfigError(f"{where} content must be an array of tables")
    sources = tuple(_source(f"{where} content[{n}]", item) for n, item in enumerate(cast(list[Any], content)))
    return Recipe(version, target, sources, defaults)


def _source(where: str, value: Any) -> ContentSource:
    table = as_table(value)
    if table is None:
        raise ConfigError(f"{where} must be a table")
    unknown = set(table) - SOURCE_KEYS
    if unknown:
        raise ConfigError(f"{where} unknown keys {show(sorted(unknown))} (allowed: {sorted(SOURCE_KEYS)})")
    kind, name = table.get("kind"), table.get("name")
    if not isinstance(kind, str) or not NAME.fullmatch(kind):
        raise ConfigError(f"{where}: kind must be a content kind name")
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in (".", ".."):
        raise ConfigError(f"{where}: name must be one path component")
    git, commit, npm, version = (table.get(k) for k in ("git", "commit", "npm", "version"))
    if (git is None) == (npm is None):
        raise ConfigError(f"{where}: exactly one of git and npm must be set")
    if git is not None:
        if not _text(git) or version is not None:
            raise ConfigError(f"{where}: git must be a repository, with a commit and no version")
        if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
            raise ConfigError(f"{where}: commit must be a full commit ID (40 or 64 lowercase hex)")
        return ContentSource(kind, name, git, commit, None, None)
    if not _text(npm) or commit is not None:
        raise ConfigError(f"{where}: npm must be a package name, with a version and no commit")
    if not isinstance(version, str) or not EXACT_VERSION.fullmatch(version):
        raise ConfigError(f"{where}: version must be an exact package version (1.2.3 or 1.2.3-pre)")
    return ContentSource(kind, name, None, None, npm, version)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value.isprintable() and value.strip() == value
