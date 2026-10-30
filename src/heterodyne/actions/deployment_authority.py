"""Dormant native admind reader for exact release approval, never host admission.

The shared H authentication core is included byte-for-byte with a fixed source pin.
A matching receipt is corroboration only: host custody qualification remains required.
"""

import hashlib
import os
import re
import stat
import sys
import time
import types
from pathlib import Path
from typing import Any, cast

from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment import Release, Target, digest, validate

CORE_SHA256 = "ad4d4a96632646a5d145feb30c30d402b358d3ecb9caf4717e89394877b5f48d"
DB = Path("/home/openclaw/.local/state/heterodyne/admind/admind.db")
HOST_POLICY = Path("/home/openclaw/.config/heterodyne/policy.toml")
QUEUE_POLICY = Path("/home/openclaw/.config/beads-task-queue/policy.json")
# Existing fixed H native readback endpoint; no PATH lookup, caller executable or shell.
ADMIND = (
    "/home/openclaw/.local/share/heterodyne/releases/"
    "5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156/.venv/bin/admind"
)
_PACKET = re.compile(
    r"https://github\.com/Epiphytic/heterodyne-metaharness/blob/[0-9a-f]{40}/docs/[A-Za-z0-9_./-]+\.json"
)


def _load_core():
    path = Path(__file__).absolute().parent / "resources/hermes_native_authority.py.txt"
    for parent in path.parents:
        if parent.is_symlink() or parent.stat().st_mode & 0o022:
            raise InvalidAction("unsafe-authority-source-chain")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size > 65536
        ):
            raise InvalidAction("unsafe-authority-source")
        raw = os.read(fd, before.st_size + 1)
        fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        for after in (os.fstat(fd), path.stat(follow_symlinks=False)):
            if any(getattr(before, key) != getattr(after, key) for key in fields):
                raise InvalidAction("authority-source-changed")
        if len(raw) != before.st_size or hashlib.sha256(raw).hexdigest() != CORE_SHA256:
            raise InvalidAction("authority-source-pin")
    finally:
        os.close(fd)
    module = types.ModuleType("_checked_release_native_authority")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102 - exact checked source only
    return module


core: Any = _load_core()


def approval_body(release: Release, packet_url: str, group: str) -> str:
    if (
        not _PACKET.fullmatch(packet_url)
        or ".." in packet_url.split("/")
        or not re.fullmatch(r"[0-9a-f]{32,128}", group)
    ):
        raise InvalidAction("approval-context")
    return (
        "Approve this exact finite heterodyne release deployment?\n"
        f"Release SHA256: {digest(release)}\nApproval: {release.approval_id}\n"
        f"Execution: {release.execution_id}\nNonce: {release.nonce}\n"
        f"Target: {release.target.target_id}\nPacket: {packet_url}\nApproval group: {group}\n"
        "Designs:\n"
        + "\n".join(release.design_links)
        + "\nDeployment deviations:\n"
        + "\n".join("- " + d for d in release.deviations)
        + "\nOne use only. No standing approval, heart reaction, generic action or host custody claim."
    )


def _bindings(release: Release, native: dict[str, Any]) -> list[str]:
    ask = native["ask"]
    if (
        ask["ask_id"] != release.approval_id
        or ask["kind"] != "question"
        or ask["status"] != "answered"
        or ask["question_form"] != "yes-no"
        or type(ask["truncated"]) is not int
        or ask["truncated"] != 0
        or type(ask["card_parts"]) is not int
        or ask["card_parts"] != 1
    ):
        core._hold("exact release deployment question required")
    kv = {row["key"]: row["value"] for row in native["kv"]}
    if len(native["kv"]) != 2 or set(kv) != {"group_id_hex", "group_operators"}:
        core._hold("native group membership unavailable")
    members: Any = core._json(kv["group_operators"])
    if (
        type(members) is not list
        or not members
        or any(type(v) is not str or not core.HEX.fullmatch(v) for v in cast(list[object], members))
    ):
        core._hold("invalid native membership")
    body = ask["body"]
    urls = [line[8:] for line in body.splitlines() if line.startswith("Packet: ")]
    if len(urls) != 1 or body != approval_body(release, urls[0], kv["group_id_hex"]):
        core._hold("exact release context changed")
    return cast(list[str], members)


def _capture(  # pyright: ignore[reportUnusedFunction] - dormant authenticated reader
    release: Release, targets: tuple[Target, ...]
) -> dict[str, Any]:
    """Fixed native readers, two unchanged policy/source sweeps; no activation API."""
    validate(release, targets)
    if not release.not_before_ns <= time.time_ns() < release.deadline_ns:
        raise InvalidAction("approval-window")
    if not re.fullmatch(r"[2-9a-hjkmnp-z]{4}", release.approval_id):
        raise InvalidAction("native-ask-id")
    here = Path(__file__).absolute().parent
    pins = release.authority_sources
    required = {
        str(here / "deployment_authority.py"): pins.adapter_sha256,
        str(here / "deployment.py"): pins.contract_sha256,
        str(here / "codec.py"): pins.codec_sha256,
        str(here / "contracts.py"): pins.records_sha256,
        str(here / "resources/hermes_native_authority.py.txt"): pins.core_sha256,
        ADMIND: pins.admind_sha256,
        str(Path(sys.executable).resolve()): pins.interpreter_sha256,
    }
    if pins.core_sha256 != CORE_SHA256:
        core._hold("native authority role pin differs")
    before = {
        str(p): hashlib.sha256(core._read(p, limit=16 * 1024 * 1024)).hexdigest()
        for p in (*map(Path, required), HOST_POLICY, QUEUE_POLICY)
    }
    if any(before[name] != expected for name, expected in required.items()):
        core._hold("approved release authority source drift")
    operators = core._operators(HOST_POLICY, QUEUE_POLICY)
    native = core._native(release.approval_id, DB)
    response = core._fetch(release.approval_id, ADMIND)
    core._validate(release, native, response, operators, _bindings)
    after = {
        name: hashlib.sha256(core._read(Path(name), limit=16 * 1024 * 1024)).hexdigest() for name in before
    }
    if before != after or core._operators(HOST_POLICY, QUEUE_POLICY) != operators:
        core._hold("release authority source or policy drift")
    if not release.not_before_ns <= time.time_ns() < release.deadline_ns:
        core._hold("release approval expired during corroboration")
    return {"release_sha256": digest(release), "sources": before, "native": native, "response": response}
