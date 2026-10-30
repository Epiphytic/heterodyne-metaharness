"""Dormant native admind reader for exact release approval, never host admission.

The shared H authentication core is included byte-for-byte with a fixed source pin.
A matching receipt is corroboration only: host custody qualification remains required.
"""

import hashlib
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any, cast

from heterodyne.actions import deployment_runtime as runtime
from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment import Release, Target, digest, validate
from heterodyne.admind.asks import fit

CORE_SHA256 = "ad4d4a96632646a5d145feb30c30d402b358d3ecb9caf4717e89394877b5f48d"
APPROVAL_TITLE = "Exact release"
DB = Path(
    "/home/openclaw/.local/state/heterodyne/admind/admind.db"  # install-agnostic: allow=home-path - fixed H
)
HOST_POLICY = Path(
    "/home/openclaw/.config/heterodyne/policy.toml"  # install-agnostic: allow=home-path - fixed H
)
QUEUE_POLICY = Path(
    "/home/openclaw/.config/beads-task-queue/policy.json"  # install-agnostic: allow=home-path - fixed H
)
# Existing fixed H native readback endpoint; no PATH lookup, caller executable or shell.
ADMIND = (
    "/home/openclaw/.local/share/heterodyne/releases/"  # install-agnostic: allow=home-path - fixed H
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
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
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


class AuthorityHold(InvalidAction):
    """A dormant authority reader could not safely establish exact evidence."""


_loaded_core: Any = None


def _core() -> Any:
    global _loaded_core
    if _loaded_core is None:
        try:
            _loaded_core = _load_core()
        except InvalidAction as exc:
            raise AuthorityHold(str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise AuthorityHold("authority-source-unavailable") from exc
    return _loaded_core


def _packet_url(release: Release) -> str:
    return (
        "https://github.com/Epiphytic/heterodyne-metaharness/blob/"
        + release.packet_commit
        + "/"
        + release.packet_path
    )


def approval_body(release: Release, packet_url: str, group: str) -> str:
    # Rendering is also a boundary: reject unsafe direct typed constructions,
    # even when this helper is called before the native capture validates them.
    validate(release, (release.target,))
    if (
        packet_url != _packet_url(release)
        or not _PACKET.fullmatch(packet_url)
        or ".." in packet_url.split("/")
        or not re.fullmatch(r"[0-9a-f]{32,128}", group)
    ):
        raise InvalidAction("approval-context")
    body = (
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
    # admind truncates the complete title+body context, not each deviation.
    # Refuse here instead of producing an ask that can never pass native binding.
    context = APPROVAL_TITLE + "\n\n" + body
    if fit(context) != (context, False):
        raise InvalidAction("release-card-budget")
    return body


def _bindings_checked(release: Release, native: dict[str, Any]) -> list[str]:
    core = _core()
    ask = native["ask"]
    if (
        ask["ask_id"] != release.approval_id
        or ask["title"] != APPROVAL_TITLE
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
    if type(kv["group_id_hex"]) is not str or type(ask["body"]) is not str:
        core._hold("invalid native group context")
    body = ask["body"]
    urls = [line[8:] for line in body.splitlines() if line.startswith("Packet: ")]
    if len(urls) != 1 or body != approval_body(release, urls[0], kv["group_id_hex"]):
        core._hold("exact release context changed")
    return cast(list[str], members)


def _bindings(release: Release, native: dict[str, Any]) -> list[str]:
    core = _core()
    try:
        return _bindings_checked(release, native)
    except (core.AuthorityHold, InvalidAction):
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorityHold("malformed-native-context") from exc


def _operators_from(host: bytes, queue: bytes) -> dict[str, str]:
    # Reuse the unchanged parser, reading ONLY the captured policy bytes.
    with tempfile.TemporaryDirectory(prefix="heterodyne-policy-read-") as directory:
        root = Path(directory)
        paths = root / "host.toml", root / "queue.json"
        for path, raw in zip(paths, (host, queue), strict=True):
            path.write_bytes(raw)
            path.chmod(0o600)
        return cast(dict[str, str], _core()._operators(*paths))


def _sources(release: Release, executables: tuple[Path, Path]) -> dict[str, str]:
    here = Path(__file__).absolute().parent
    pins = release.authority_sources
    required = {
        "adapter": (here / "deployment_authority.py", pins.adapter_sha256),
        "contract": (here / "deployment.py", pins.contract_sha256),
        "codec": (here / "codec.py", pins.codec_sha256),
        "records": (here / "contracts.py", pins.records_sha256),
        "core": (here / "resources/hermes_native_authority.py.txt", pins.core_sha256),
        "admind-wrapper": (Path(ADMIND), pins.admind_sha256),
        "interpreter": (executables[0], pins.interpreter_sha256),
        "runtime-adapter": (here / "deployment_runtime.py", pins.runtime_adapter_sha256),
        "admind-interpreter": (executables[1], pins.admind_interpreter_sha256),
    }
    if pins.core_sha256 != CORE_SHA256:
        raise AuthorityHold("native authority role pin differs")
    observed = {role: runtime.stream_hash(path) for role, (path, _) in required.items()}
    inventories = {
        "admind-package": (runtime.tree_digest(runtime.SITE), pins.admind_package_sha256),
        "admind-source": (runtime.tree_digest(runtime.SOURCE), pins.admind_source_sha256),
        "harness-package": (runtime.tree_digest(here.parent), pins.harness_package_sha256),
    }
    if any(observed[role] != expected for role, (_, expected) in required.items()) or any(
        actual != expected for actual, expected in inventories.values()
    ):
        raise AuthorityHold("approved release authority source drift")
    return {**observed, **{name: pair[0] for name, pair in inventories.items()}}


def _capture_checked(release: Release, targets: tuple[Target, ...]) -> dict[str, Any]:
    validate(release, targets)
    if not release.not_before_ns <= time.time_ns() < release.deadline_ns:
        raise AuthorityHold("approval-window")
    if not re.fullmatch(r"[2-9a-hjkmnp-z]{4}", release.approval_id):
        raise AuthorityHold("native-ask-id")
    core = _core()
    # Resolve symlinks only once: the pinned child executable and the executed
    # child executable must be the same path, even if its original alias changes.
    executables = Path(sys.executable).resolve(strict=True), runtime.PYTHON.resolve(strict=True)
    before = _sources(release, executables)
    policies = core._read(HOST_POLICY), core._read(QUEUE_POLICY)
    operators = _operators_from(*policies)
    native = core._native(release.approval_id, DB)
    response = core._json(runtime.fetch(release.approval_id, executables[1]))
    core._validate(release, native, response, operators, _bindings)
    # Each independent sweep checks every role against its approved pin.
    _sources(release, executables)
    if policies != (core._read(HOST_POLICY), core._read(QUEUE_POLICY)):
        raise AuthorityHold("release authority source or policy drift")
    if not release.not_before_ns <= time.time_ns() < release.deadline_ns:
        raise AuthorityHold("release approval expired during corroboration")
    return {"release_sha256": digest(release), "sources": before, "native": native, "response": response}


def _capture(  # pyright: ignore[reportUnusedFunction] - private dormant fixture seam
    release: Release, targets: tuple[Target, ...]
) -> dict[str, Any]:
    """Private exact native readback; no host admission or injectable reader."""
    try:
        return _capture_checked(release, targets)
    except InvalidAction:
        raise
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        if _loaded_core is not None and isinstance(exc, _loaded_core.AuthorityHold):
            raise AuthorityHold("release-authentication-refused") from exc
        raise AuthorityHold("release-authority-unavailable: " + type(exc).__name__) from exc
