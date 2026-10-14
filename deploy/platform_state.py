#!/usr/bin/env python3
"""The platform install's decisions (design btq-v7sgt D4, D6), run as the service user. install.sh passes it
as `python3 -I -c` from its root-only stage, so its bytes are the approved archive's. It reads no
environment: every path is an argument. Messages name checks and units, never paths or digests.

stdlib only: it runs under the system python3, outside any venv. (Not `platform.py`, which would shadow the
stdlib module for anything that put `deploy/` on its path.)"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

MARKER_PREFIX = b"# Rendered by heterodyne's deploy/install.sh for release "
MANAGED = ("heterodyne-admind.service", "heterodyne-wsd.service")
STATES = ("writing", "written")
ORIGINS = ("created", "adopted")
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class RecordError(Exception):
    pass


class Refusal(Exception):
    pass


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(dest: Path, data: bytes, mode: int) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(dest)
    _fsync_dir(dest.parent)


def _private_dir(d: Path) -> None:
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise Refusal("the install directory is not the service user's directory")
    d.chmod(0o700)


def _backup_name(unit: str, name: object) -> bool:
    """A backup is this unit's file directly in the backup directory, as backup() names it."""
    pattern = rf"{re.escape(unit)}\.\d{{8}}T\d{{6}}Z(\.\d+)?"
    return isinstance(name, str) and re.fullmatch(pattern, name) is not None


def _valid(rec: object) -> bool:
    if not isinstance(rec, dict) or set(rec) != {"version", "release", "units"} or rec["version"] != 1:
        return False
    release = rec["release"]
    if release is not None and not (isinstance(release, str) and COMMIT.fullmatch(release)):
        return False
    units = rec["units"]
    if not isinstance(units, dict):
        return False
    for name, e in units.items():
        if name not in MANAGED or not isinstance(e, dict) or e.get("state") not in STATES:
            return False
        if e.get("origin") not in ORIGINS:
            return False
        if e["origin"] == "adopted" and not (_backup_name(name, e.get("backup"))
                                             and isinstance(e.get("backup_sha256"), str)
                                             and SHA256.fullmatch(e["backup_sha256"])):
            return False
    return True


def load_record(path: Path) -> dict:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {"version": 1, "release": None, "units": {}}
    except OSError:
        raise RecordError("the install record can't be read") from None
    try:
        rec = json.loads(data)
    except ValueError:
        raise RecordError("the install record is malformed") from None
    if not _valid(rec):
        raise RecordError("the install record is malformed")
    return rec


def save_record(path: Path, rec: dict) -> None:
    _private_dir(path.parent)
    _atomic_write(path, json.dumps(rec, indent=1, sort_keys=True).encode() + b"\n", 0o600)


def file_kind(path: Path) -> str:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "refuse:the unit file can't be read"
    if not stat.S_ISREG(st.st_mode):
        return "refuse:the unit file is a symlink or not a regular file"
    if st.st_uid != os.getuid():
        return "refuse:the unit file belongs to another user"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return "refuse:the unit file can't be read"
    with os.fdopen(fd, "rb") as f:
        first = f.readline()
    return "marker" if first.startswith(MARKER_PREFIX) else "hand-made"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup_ok(e: dict, backups: Path) -> bool:
    b = backups / e["backup"]
    try:
        st = os.lstat(b)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and _sha256(b) == e["backup_sha256"]


def plan(unit: str, rec: dict, file: Path, render: Path, backups: Path, adopt: bool) -> str:
    kind = file_kind(file)
    if kind.startswith("refuse:"):
        raise Refusal(f"{unit}: {kind.removeprefix('refuse:')}")
    e = rec["units"].get(unit)
    if e is not None and e["origin"] == "adopted" and not _backup_ok(e, backups):
        raise Refusal(f"{unit}: the recorded backup is missing or changed")
    if e is None:
        if kind == "absent":
            return "create"
        if kind == "marker":
            raise Refusal(f"{unit} carries the installer's marker but is not in the install record")
        if not adopt:
            raise Refusal(f"{unit} was not written by the installer: run with --adopt to replace it "
                          "(the old file is kept)")
        return "adopt"
    same = kind != "absent" and file.read_bytes() == render.read_bytes()
    if e["state"] == "written":
        if kind == "hand-made":
            raise Refusal(f"{unit} was replaced by hand after the installer wrote it")
        return "ok" if same else "rewrite"
    # state writing: what the file shows says how far the last run got
    if kind == "marker":
        return "mark-written" if same else "rewrite"
    if e["origin"] == "adopted":
        if kind == "hand-made" and _sha256(file) == e["backup_sha256"]:
            return "resume"
        raise Refusal(f"{unit} changed since its backup")
    if kind == "absent":
        return "resume"
    raise Refusal(f"{unit} appeared by hand while the installer was writing it")


def backup(unit: str, file: Path, backups: Path) -> str:
    _private_dir(backups)
    name = f"{unit}.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    if os.path.lexists(backups / name):
        name = f"{name}.{os.getpid()}"
    _atomic_write(backups / name, file.read_bytes(), 0o600)
    return name


def record_set(unit: str, state: str, path: Path, origin: str | None, backup_name: str | None,
               backups: Path | None) -> None:
    rec = load_record(path)
    e = rec["units"].get(unit)
    if e is None:
        if origin is None:
            raise Refusal(f"{unit} has no install record entry to update")
        e = {"origin": origin}
        if origin == "adopted":
            if backup_name is None or backups is None:
                raise Refusal("an adopted unit needs its backup")
            e |= {"backup": backup_name, "backup_sha256": _sha256(backups / backup_name)}
    e["state"] = state      # an existing entry keeps its origin and backup: an upgrade never resets them
    rec["units"][unit] = e
    save_record(path, rec)


def write(file: Path, render: Path) -> None:
    _atomic_write(file, render.read_bytes(), 0o644)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="platform_state.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    for a in ("--unit", "--record", "--file", "--render", "--backup-dir"):
        p.add_argument(a, required=True)
    p.add_argument("--adopt", action="store_true")
    b = sub.add_parser("backup")
    for a in ("--unit", "--file", "--backup-dir"):
        b.add_argument(a, required=True)
    r = sub.add_parser("record-set")
    r.add_argument("--unit", required=True)
    r.add_argument("--state", required=True, choices=STATES)
    r.add_argument("--record", required=True)
    r.add_argument("--origin", choices=ORIGINS)
    r.add_argument("--backup")
    r.add_argument("--backup-dir")
    w = sub.add_parser("write")
    for a in ("--unit", "--file", "--render"):
        w.add_argument(a, required=True)
    rr = sub.add_parser("record-release")
    rr.add_argument("--record", required=True)
    rr.add_argument("--release", required=True)
    d = sub.add_parser("record-drop")
    d.add_argument("--unit", required=True)
    d.add_argument("--record", required=True)
    args = ap.parse_args(argv)
    if getattr(args, "unit", None) is not None and args.unit not in MANAGED:
        ap.error("not a managed unit")
    try:
        if args.cmd == "plan":
            print(plan(args.unit, load_record(Path(args.record)), Path(args.file), Path(args.render),
                       Path(args.backup_dir), args.adopt))
        elif args.cmd == "backup":
            print(backup(args.unit, Path(args.file), Path(args.backup_dir)))
        elif args.cmd == "record-set":
            record_set(args.unit, args.state, Path(args.record), args.origin, args.backup,
                       Path(args.backup_dir) if args.backup_dir else None)
        elif args.cmd == "write":
            write(Path(args.file), Path(args.render))
        elif args.cmd == "record-release":
            if not COMMIT.fullmatch(args.release):
                ap.error("--release is not a full commit ID")
            rec = load_record(Path(args.record))
            rec["release"] = args.release
            save_record(Path(args.record), rec)
        elif args.cmd == "record-drop":
            rec = load_record(Path(args.record))
            rec["units"].pop(args.unit, None)
            save_record(Path(args.record), rec)
    except (Refusal, RecordError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"failed: {args.cmd} ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
