"""Private exact native approval corroboration for the whole outage packet.

Reuses the unchanged native authentication core and fixed H transport/profile.
This readback is not protected receiver custody, route qualification or permission
to execute a host effect. No public caller or alternate reader is installed.
"""

# pyright: reportPrivateUsage=false
# These private modules form one dormant adapter; no caller selects their paths.

import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from heterodyne.actions import deployment_authority as release_auth
from heterodyne.actions import deployment_runtime as runtime
from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment import Target
from heterodyne.actions.deployment_outage import DeploymentPacket, packet_digest, validate_packet
from heterodyne.harness.digests import ComputedDigest, display

APPROVAL_TITLE = "OUTAGE DEPLOYMENT: exact finite release"


def approval_body(packet: DeploymentPacket, packet_url: str, group: str) -> str:
    validate_packet(packet, (packet.release.target,))
    if (not release_auth._PACKET.fullmatch(packet_url)
        or packet_url.split("/", 7)[-1] != packet.release.packet_path
        or ".." in packet_url.split("/")):
        raise InvalidAction("outer-packet-publication-url")
    original = release_auth.approval_body(
        packet.release, release_auth._packet_url(packet.release), group,
    )
    body = (
        "Approve this exact finite outage deployment and explicitly accept its transport gap?\n"
        + original.split("\n", 1)[1].replace("\nPacket: ", "\nInner Release: ")
        + "\nOuter Packet: " + packet_url
        + "\nProtected deployment SHA256: " + display(ComputedDigest(packet_digest(packet)))
        + "\nOutage steps: disable_stop, stopped_guard, install, held_acceptance"
        + "\nIndependent route: " + packet.outage.route.route_id
        + "\nOutage deadline ns: " + str(packet.outage.deadline_ns)
        + "\nRecovery deadline ns: " + str(packet.outage.recovery_deadline_ns)
        + "\nDuring this outage, a queued but undelivered Marmot reply is not a received revocation."
        + "\nA yes explicitly accepts that gap for this exact finite outage capsule only."
        + "\nOnly the exact packet transitions and independently authenticated recovery are authorized."
    )
    release_auth._check_card(APPROVAL_TITLE, body, packet.release.approval_id)
    return body


def _bindings(packet: DeploymentPacket, native: dict[str, Any]) -> list[str]:
    """Bind the original body before reusing the release membership checks."""
    core = release_auth._core()
    try:
        ask = native["ask"]
        kv = {row["key"]: row["value"] for row in native["kv"]}
        urls = [line.removeprefix("Outer Packet: ") for line in ask["body"].splitlines()
                if line.startswith("Outer Packet: ")]
        if len(urls) != 1:
            core._hold("exact outage publication URL missing")
        url = urls[0]
        group = kv["group_id_hex"]
        if ask["title"] != APPROVAL_TITLE or ask["body"] != approval_body(packet, url, group):
            core._hold("exact outage deployment context changed")
        # The unchanged release binder validates ask shape and native membership.
        # Projection is local only; the core authenticates the original retained
        # native rows/card/reply/CLI, and the full original body was checked above.
        projected = {**native, "ask": {
            **ask, "title": release_auth.APPROVAL_TITLE,
            "body": release_auth.approval_body(
                packet.release, release_auth._packet_url(packet.release), group,
            ),
        }}
        return release_auth._bindings(packet.release, projected)
    except (core.AuthorityHold, InvalidAction):
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise release_auth.AuthorityHold("malformed-outage-native-context") from exc


def _sources(packet: DeploymentPacket, executables: tuple[Path, Path]) -> dict[str, str]:
    # The original role-indexed closure checks the whole harness tree, unchanged
    # core and actual executable sources. These explicit roles additionally bind
    # the wrapper's finite host-store/outage/semantic adapter contract.
    before = release_auth._sources(packet.release, executables)
    root = Path(__file__).absolute().parent
    roles = {
        "host-store": (root / "deployment_host_store.py", packet.store_source_sha256),
        "outage": (root / "deployment_outage.py", packet.outage_source_sha256),
        "outage-semantic-adapter": (Path(__file__).absolute(), packet.semantic_factory_source_sha256),
        "root-helper": (Path("/usr/local/libexec/heterodyne/deployment-install-helper"),
                        packet.root_helper_sha256),
        "root-profile": (Path("/etc/heterodyne/deployment-install-profile.json"),
                         packet.root_profile_sha256),
        "sudo": (Path("/usr/bin/sudo"), packet.sudo_sha256),
    }
    for role in ("root-helper", "root-profile", "sudo"):
        path = roles[role][0]
        for item in (path, *path.parents):
            info = item.stat(follow_symlinks=False)
            if info.st_uid != 0 or info.st_mode & 0o022 or item.is_symlink():
                raise release_auth.AuthorityHold("root helper custody drift")
    observed = {role: runtime.stream_hash(path) for role, (path, _) in roles.items()}
    if any(observed[role] != expected for role, (_, expected) in roles.items()):
        raise release_auth.AuthorityHold("outage authority source drift")
    return {**before, **observed}


def _capture_checked(packet: DeploymentPacket, targets: tuple[Target, ...]) -> dict[str, Any]:
    validate_packet(packet, targets)
    release = packet.release
    if not release.not_before_ns <= time.time_ns() < release.deadline_ns:
        raise release_auth.AuthorityHold("outage approval-window")
    if not re.fullmatch(r"[2-9a-hjkmnp-z]{4}", release.approval_id):
        raise release_auth.AuthorityHold("native-ask-id")
    core = release_auth._core()
    executables = Path(sys.executable).resolve(strict=True), runtime.PYTHON.resolve(strict=True)
    sources = _sources(packet, executables)
    policies = core._read(release_auth.HOST_POLICY), core._read(release_auth.QUEUE_POLICY)
    operators = release_auth._operators_from(*policies)
    native = core._native(release.approval_id, release_auth.DB)
    response = core._json(runtime.fetch(release.approval_id, executables[1]))
    core._validate(packet, native, response, operators, _bindings)
    _sources(packet, executables)
    if policies != (core._read(release_auth.HOST_POLICY), core._read(release_auth.QUEUE_POLICY)):
        raise release_auth.AuthorityHold("outage policy drift")
    if not release.not_before_ns <= time.time_ns() < release.deadline_ns:
        raise release_auth.AuthorityHold("outage approval expired during corroboration")
    return {
        "packet_sha256": packet_digest(packet), "sources": sources, "native": native, "response": response,
    }


def _capture(  # pyright: ignore[reportUnusedFunction] - dormant fixed native readback
    packet: DeploymentPacket, targets: tuple[Target, ...],
) -> dict[str, Any]:
    try:
        return _capture_checked(packet, targets)
    except InvalidAction:
        raise
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        if release_auth._loaded_core is not None and isinstance(exc, release_auth._loaded_core.AuthorityHold):
            raise release_auth.AuthorityHold("outage-authentication-refused") from exc
        raise release_auth.AuthorityHold("outage-authority-unavailable") from exc
