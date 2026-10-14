"""`harnesses.toml` (ADR 0001 §7 Managed installs): the host's selected install per adapter and the ones it
used before, newest first. Only `wsd` writes it (`wsctl harness use` and `rollback`); `write` replaces it
atomically, so a reader sees the old file or the new one, never a mix. Errors name keys, never values."""

import os
import re
import secrets
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heterodyne.config import ConfigError, layers
from heterodyne.config.secret_scan import show

FILE = "harnesses.toml"
MAX_PREVIOUS = 10
ADAPTER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
INSTALL = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?-[0-9a-f]{12}")   # `<version>-<12 hex>`
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
KEYS = frozenset({"use", "digest", "previous"})


@dataclass(frozen=True)
class InstallPin:
    install: str
    digest: str


@dataclass(frozen=True)
class Selection:
    adapter: str
    current: InstallPin | None
    previous: tuple[InstallPin, ...] = ()


def load(config_dir: Path) -> Mapping[str, Selection]:
    """The selections in `config_dir/harnesses.toml`; none when the file doesn't exist."""
    path = config_dir / FILE
    if not os.path.lexists(path):
        return {}
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        # An entry that exists but can't be read (a dangling link, no permission) is not "no selection".
        raise ConfigError(f"{FILE} exists but can't be read") from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{FILE}: {show(str(exc), False)}") from None
    found: dict[str, Selection] = {}
    for adapter, value in raw.items():
        where = f"{FILE}: [{show(adapter, False)}]"
        if not ADAPTER.fullmatch(adapter):
            raise ConfigError(f"{where} is not an adapter name")
        table = layers.as_table(value)
        if table is None:
            raise ConfigError(f"{where} must be a table")
        unknown = set(table) - KEYS
        if unknown:
            raise ConfigError(f"{where} unknown keys {show(sorted(unknown))} (allowed: {sorted(KEYS)})")
        current = None
        if "use" in table or "digest" in table:
            current = _pin(where, {k: v for k, v in table.items() if k != "previous"})
        previous = table.get("previous", [])
        if not isinstance(previous, list) or len(cast(list[Any], previous)) > MAX_PREVIOUS:
            raise ConfigError(f"{where} previous must be an array of at most {MAX_PREVIOUS} tables")
        rows = cast(list[Any], previous)
        found[adapter] = Selection(adapter, current,
                                   tuple(_pin(f"{where} previous[{n}]", row) for n, row in enumerate(rows)))
    return found


def _pin(where: str, value: Any) -> InstallPin:
    table = layers.as_table(value)
    if table is None or set(table) != {"use", "digest"}:
        raise ConfigError(f"{where}: use and digest must be set together, and nothing else")
    install, digest = table["use"], table["digest"]
    if not isinstance(install, str) or not INSTALL.fullmatch(install):
        raise ConfigError(f"{where}: use must be an install name")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ConfigError(f"{where}: digest must be a manifest digest (sha256:<64 hex>)")
    return InstallPin(install, digest)


def write(config_dir: Path, selections: Mapping[str, Selection], *,
          replace: Callable[[Path, Path], None] = os.replace) -> None:
    """Replace `harnesses.toml` with `selections`, keeping the newest `MAX_PREVIOUS` previous installs: a
    0600 temp file in the same directory, fsynced, renamed over the file, then the directory fsynced. A
    failure before the rename leaves the old file and removes the temp file."""
    text = _render(selections)
    if any(adapter != sel.adapter for adapter, sel in selections.items()):
        raise ValueError("a selection's key is not its adapter")
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = config_dir / f".{FILE}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        replace(temp, config_dir / FILE)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    dir_fd = os.open(config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _render(selections: Mapping[str, Selection]) -> str:
    """TOML for `selections`. Every value is checked against the patterns `load` uses first, so each is a
    plain ASCII string that needs no escaping."""
    out: list[str] = []
    for adapter in sorted(selections):
        sel = selections[adapter]
        pins = ([sel.current] if sel.current else []) + list(sel.previous[:MAX_PREVIOUS])
        if not ADAPTER.fullmatch(adapter) or not all(
                INSTALL.fullmatch(p.install) and DIGEST.fullmatch(p.digest) for p in pins):
            raise ValueError("a selection holds a malformed adapter, install name or digest")
        out.append(f"[{adapter}]")
        if sel.current:
            out += [f'use = "{sel.current.install}"', f'digest = "{sel.current.digest}"']
        rows = [f'  {{ use = "{p.install}", digest = "{p.digest}" }},' for p in sel.previous[:MAX_PREVIOUS]]
        out += ["previous = [", *rows, "]", ""]
    return "\n".join(out)
