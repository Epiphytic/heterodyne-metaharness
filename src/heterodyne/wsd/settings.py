"""wsd settings from the merged host config and each workstream's layer (ADR 0001 §4.3, §9, §15).

`[wsd]` and `[integrations.beads]` live in host `config.toml` only (the workstream layer rejects both).
`integrations.beads.btq` is the btq checkout; its other keys are btq's locations, passed to its `Queue`
unchanged, and anything not set falls back to btq's own `BTQ_*` environment and defaults. A workstream is
a file `workstreams/<name>.toml` whose stem is a slug; its `[repos]` names its repositories (absolute
paths, one of them `default`) and `roles.coder` its coder profile.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, cast

from heterodyne.config import Config, ConfigError, load, paths, secret_scan
from heterodyne.config.layers import table_at
from heterodyne.config.secret_scan import show
from heterodyne.wsd import ids
from heterodyne.wsd.accounts import Accounts, ConfiguredAccounts
from heterodyne.wsd.headroom import UsageSettings
from heterodyne.wsd.workstream import DEFAULT_REPO, Limits, WorkstreamSettings

WSD_KEYS = frozenset({"backstop_seconds", "reconcile_seconds", "launch_failures_before_human",
                      "park_attempts_before_human", "inbox_attempts_before_human", "coder_role"})
BTQ_KEYS = frozenset({"btq", "config_dir", "repo", "dolt_host", "dolt_port", "dolt_database",
                      "tls_cert", "credentials"})
BTQ_PATHS = ("config_dir", "repo", "tls_cert")
BTQ_TEXT = ("dolt_host", "dolt_port", "dolt_database")
BEADS = "[integrations.beads]"
JOURNAL = "wsd.db"
INSTANCE_LOCK = "wsd.lock"
CTL_SOCKET = "ctl.sock"


@dataclass(frozen=True)
class WsdSettings:
    state_dir: Path
    backstop_seconds: float
    reconcile_seconds: float
    inbox_attempts_before_human: int
    btq_checkout: Path
    btq_locations: dict[str, str]
    workstreams: tuple[WorkstreamSettings, ...]
    accounts: Accounts | None = None     # the host's accounts: the journal upgrade's adapter facts (AU-3 §3)

    @property
    def journal(self) -> Path:
        return self.state_dir / JOURNAL

    @property
    def instance_lock(self) -> Path:
        return self.state_dir / INSTANCE_LOCK

    @property
    def lock_dir(self) -> Path:
        return self.state_dir / "claims"

    @property
    def socket(self) -> Path:
        return self.state_dir / CTL_SOCKET


@dataclass(frozen=True)
class WorkstreamView:
    """A workstream's settings that only a restart may change: everything but `accounts`, `usage` and
    `models` (AU-4 §5.2, O3), as plain values with value equality."""
    name: str
    repos: tuple[tuple[str, str], ...]
    coder_role: str
    coder_profile: str
    profiles: tuple[str, ...]
    limits: Limits


@dataclass(frozen=True)
class RestartView:
    """The settings `wsctl reload` must find unchanged. The host `accounts` are left out: only the journal
    upgrade reads them."""
    state_dir: Path
    backstop_seconds: float
    reconcile_seconds: float
    inbox_attempts_before_human: int
    btq_checkout: Path
    btq_locations: tuple[tuple[str, str], ...]
    workstreams: tuple[WorkstreamView, ...]


def restart_view(s: WsdSettings) -> RestartView:
    streams = tuple(WorkstreamView(w.name, tuple(sorted((k, str(v)) for k, v in w.repos.items())),
                                   w.coder_role, w.coder_profile, tuple(sorted(w.profiles)), w.limits)
                    for w in sorted(s.workstreams, key=lambda w: w.name))
    return RestartView(s.state_dir, s.backstop_seconds, s.reconcile_seconds, s.inbox_attempts_before_human,
                       s.btq_checkout, tuple(sorted(s.btq_locations.items())), streams)


def restart_fields(running: RestartView, loaded: RestartView) -> list[str]:
    """The names of the fields that differ (never their values, which may be paths): a host field by its
    name, a workstream's as `<workstream>.<field>`, and `workstreams` when the set of names differs."""
    found = [f.name for f in fields(RestartView)
             if f.name != "workstreams" and getattr(running, f.name) != getattr(loaded, f.name)]
    if [w.name for w in running.workstreams] != [w.name for w in loaded.workstreams]:
        return [*found, "workstreams"]
    for old, new in zip(running.workstreams, loaded.workstreams, strict=True):
        found += [f"{old.name}.{f.name}" for f in fields(WorkstreamView)
                  if getattr(old, f.name) != getattr(new, f.name)]
    return found


def workstream_names(config_dir: Path) -> list[str]:
    folder = config_dir / "workstreams"
    if not folder.is_dir():
        return []
    names = sorted(p.stem for p in folder.glob("*.toml"))
    for name in names:
        if not ids.SLUG.fullmatch(name):
            raise ConfigError(f"workstreams/{show(name, False)}.toml: the file name must be a slug")
    return names


def resolve(env: Mapping[str, str]) -> WsdSettings:
    host = load(env=env)
    wsd = table_at(host.values, "wsd", "config")
    _only(wsd, WSD_KEYS, "[wsd]")
    btq = table_at(table_at(host.values, "integrations", "config"), "beads", "integrations")
    _only(btq, BTQ_KEYS, BEADS)
    limits = Limits(launch_failures_before_human=_int(wsd, "launch_failures_before_human"),
                    park_attempts_before_human=_int(wsd, "park_attempts_before_human"))
    coder_role = wsd.get("coder_role")
    if not isinstance(coder_role, str) or not ids.SLUG.fullmatch(coder_role):
        raise ConfigError("[wsd] coder_role must be a role name")
    usage = UsageSettings.from_table(table_at(host.values, "usage", "config"))
    streams = tuple(_workstream(name, load(name, env), coder_role, limits, usage, env)
                    for name in workstream_names(paths.config_dir(env)))
    return WsdSettings(
        state_dir=paths.state_dir(env) / "wsd",
        backstop_seconds=_seconds(wsd, "backstop_seconds"),
        reconcile_seconds=_seconds(wsd, "reconcile_seconds"),
        inbox_attempts_before_human=_int(wsd, "inbox_attempts_before_human"),
        btq_checkout=_path(btq.get("btq"), env, f"{BEADS} btq"),
        btq_locations=_locations(btq, env),
        workstreams=streams,
        accounts=ConfiguredAccounts.from_config(host, env))


def _workstream(name: str, cfg: Config, coder_role: str, limits: Limits, usage: UsageSettings,
                env: Mapping[str, str]) -> WorkstreamSettings:
    where = f"workstreams/{name}.toml"
    repos: dict[str, Path] = {}
    for repo, value in table_at(cfg.values, "repos", where).items():
        if not ids.SLUG.fullmatch(repo):
            raise ConfigError(f"{where}: repos.{show(repo, False)} must be a slug")
        repos[repo] = _path(value, env, f"{where}: repos.{repo}")
    if DEFAULT_REPO not in repos:
        raise ConfigError(f"{where}: [repos] must name a `{DEFAULT_REPO}` repository")
    profiles = frozenset(table_at(cfg.values, "profiles", "config.toml"))
    coder_profile = table_at(cfg.values, "roles", where).get(coder_role)
    if not isinstance(coder_profile, str) or coder_profile not in profiles:
        raise ConfigError(f"{where}: roles.{coder_role} must name a profile from [profiles]")
    models = {p: str(cast(Mapping[str, Any], v).get("model") or "")
              for p, v in table_at(cfg.values, "profiles", "config.toml").items()}
    return WorkstreamSettings(name, repos, coder_role, coder_profile, profiles, limits, models,
                              ConfiguredAccounts.from_config(cfg, env), usage)


def _locations(btq: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in BTQ_PATHS:
        if key in btq:
            found[key] = str(_path(btq[key], env, f"{BEADS} {key}"))
    for key in BTQ_TEXT:
        if key in btq:
            value = btq[key]
            if isinstance(value, int) and not isinstance(value, bool):
                value = str(value)
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{BEADS} {key} must be a non-empty string")
            found[key] = value
    if "credentials" in btq:
        ref: Any = btq["credentials"]
        if not secret_scan.is_reference(ref) or set(cast(Mapping[str, Any], ref)) != {"file"}:
            raise ConfigError(f'{BEADS} credentials must be {{ file = "<path>" }}')
        where = f"{BEADS} credentials.file"
        found["credentials"] = str(_path(cast(Mapping[str, Any], ref)["file"], env, where))
    return found


def _only(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {show(unknown)} (allowed: {sorted(allowed)})")


def _path(value: Any, env: Mapping[str, str], where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a path")
    path = paths.expand(value, env)
    if not path.is_absolute():
        raise ConfigError(f"{where} must be an absolute path or start with ~/")
    return path


def _int(table: Mapping[str, Any], key: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ConfigError(f"[wsd] {key} must be an integer from 1 to 100")
    return value


def _seconds(table: Mapping[str, Any], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 86400:
        raise ConfigError(f"[wsd] {key} must be a number of seconds, more than 0 and at most 86400")
    return float(value)
