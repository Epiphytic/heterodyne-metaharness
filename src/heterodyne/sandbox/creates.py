"""Host-only, write-once evidence for sandbox creates, independent of session records.

Absence, observation, and ended or replaced records never settle a create. Only a
valid settlement followed by confirmed deletion makes an entry done.
"""

import json
import os
import re
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from heterodyne.config.secret_scan import sensitive_kind
from heterodyne.fsutil import private_dir

NAME = re.compile(r"hz[0-9a-f]{12}g[1-9][0-9]*")
KINDS = frozenset({"returned", "never_submitted", "operator"})
MAX_BYTES = 4096


def sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_once(path: Path, body: bytes) -> bool:
    """Private temp, file fsync, first-writer-wins link, directory fsync."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
        sync(path.parent)
    except OSError:
        return False
    return True


def read(path: Path, name: str) -> dict[str, Any] | None:
    """Bounded nonblocking regular-file read. Damaged evidence counts as absent."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
                return None
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        data = json.loads(raw)
        if isinstance(data, dict):
            fields = cast(dict[str, Any], data)
            if fields.get("sandbox") == name:
                return fields
    except (OSError, ValueError):
        pass
    return None


def settlement(directory: Path, name: str) -> dict[str, Any] | None:
    data = read(directory / f"{name}.settled.json", name)
    return data if (data is not None and isinstance(data.get("how"), str) and
                    data["how"] in KINDS) else None


def settled(directory: Path, name: str) -> bool:
    return settlement(directory, name) is not None


def gone(directory: Path, name: str) -> bool:
    evidence = settlement(directory, name)
    return evidence is not None and (evidence["how"] == "never_submitted" or
                                    read(directory / f"{name}.gone.json", name) is not None)


class Ledger:
    def __init__(self, directory: Path, alerts: Path) -> None:
        self.directory, self.alerts = directory, alerts
        private_dir(directory)

    def names(self) -> list[str]:
        # Include orphan settlement/gone files left by an interrupted cleanup.
        return sorted({p.name.split(".", 1)[0] for p in self.directory.iterdir()
                       if NAME.fullmatch(p.name.split(".", 1)[0])})

    def submission(self, name: str) -> dict[str, Any] | None:
        data = read(self.directory / f"{name}.json", name)
        if data is None:
            return None
        if not all(isinstance(data.get(k), str) and data[k] for k in ("key", "ws", "bead", "pane")):
            return None
        if not all(type(data.get(k)) is int for k in ("generation", "deadline", "at")):
            return None
        if data["generation"] <= 0 or name != f"{name.split('g')[0]}g{data['generation']}":
            return None
        return data

    def put(self, name: str, kind: str, **fields: Any) -> bool:
        suffix = f".{kind}" if kind else ""
        body = json.dumps({"sandbox": name, **fields}, sort_keys=True).encode()
        return write_once(self.directory / f"{name}{suffix}.json", body)

    def observe(self, name: str, by: str, at: int) -> bool:
        return self.put(name, "observed", by=by, at=at)

    def alert(self, name: str, at: int, *, close: bool = False) -> bool:
        entry = self.submission(name)
        if entry is None:
            return False
        observation = read(self.directory / f"{name}.observed.json", name)
        seen = ("never observed" if observation is None else
                f"observed at least once at {observation.get('at')}")
        sid = name.split("g")[0]
        if close:
            evidence = settlement(self.directory, name)
            text = f"Create {name}: settled: {evidence['how'] if evidence else 'unknown'}; deleted."
        else:
            text = (f"Create {name}, session {sid}, generation {entry['generation']}, "
                    f"workstream {entry['ws']}, bead {entry['bead']}: outcome unknown; "
                    f"the watcher keeps deleting the sandbox; {seen}. "
                    f"Check the gateway, then accept late-create risk with "
                    f"wsctl sandbox settle {entry['ws']} {sid} --generation {entry['generation']}.")
        if sensitive_kind(text) is not None:
            # Untrusted names must not prevent the fixed escalation from reaching admind.
            text = f"Create {name}: outcome unknown; watcher keeps deleting. Inspect the host create ledger."
            if close:
                text = f"Create {name}: settled and deleted."
        ident = f"create-{name}" + ("-settled" if close else "")
        private_dir(self.alerts)
        return write_once(self.alerts / f"{ident}.json", json.dumps({"id": ident,
                          "created_at": datetime.fromtimestamp(at, UTC).isoformat(), "text": text}).encode())

    def cleanup(self, name: str) -> None:
        """Called only for done entries whose watcher is confirmed absent."""
        paths = [self.alerts / f"create-{name}.json", self.alerts / f"create-{name}-settled.json"]
        paths.extend(self.directory / f"{name}{suffix}.json"
                     for suffix in ("", ".observed", ".gone", ".settled"))
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            sync(path.parent)
