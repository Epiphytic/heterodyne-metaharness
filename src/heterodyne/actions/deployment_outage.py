"""Closed pre-outage/recovery values; none authenticates or executes an effect.

A future protected semantic factory must bind this entire packet in the genuine
native card. A Release approval alone never approves its added outage fields.
"""

import hashlib
from typing import Literal

from heterodyne.actions.codec import (
    InvalidAction,
    _decode,  # pyright: ignore[reportPrivateUsage] - shared bounded closed codec
    encode_record,
)
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment import Release, Target, digest, validate

OUTAGE_STEPS = ("disable_stop", "stopped_guard", "install", "held_acceptance")


class Transition(Record, frozen=True):
    step: Literal["disable_stop", "stopped_guard", "install", "held_acceptance"]
    before_sha256: str
    after_sha256: str


class IndependentRoute(Record, frozen=True):
    route_id: str
    epoch_sha256: str
    policy_sha256: str
    credential_sha256: str


class OutageCapsule(Record, frozen=True):
    schema: Literal["heterodyne-outage-capsule.v1"]
    release_sha256: str
    execution_id: str
    nonce: str
    not_before_ns: int
    deadline_ns: int
    recovery_deadline_ns: int
    transitions: tuple[Transition, ...]
    route: IndependentRoute


class DeploymentPacket(Record, frozen=True):
    schema: Literal["heterodyne-protected-deployment.v1"]
    release: Release
    outage: OutageCapsule
    store_source_sha256: str
    outage_source_sha256: str
    semantic_factory_source_sha256: str
    root_helper_sha256: str
    root_profile_sha256: str
    sudo_sha256: str


class RecoveryCommand(Record, frozen=True):
    schema: Literal["heterodyne-independent-recovery.v1"]
    packet_sha256: str
    execution_id: str
    route_epoch_sha256: str
    command_nonce: str
    operation: Literal["cancel", "safe-stop", "held-restore"]
    expected_observation_sha256: str
    not_before_ns: int
    deadline_ns: int


def _hashes(*values: str) -> None:
    if any(len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in values):
        raise InvalidAction("outage-digest")


def validate_packet(packet: DeploymentPacket, targets: tuple[Target, ...]) -> None:
    """Validate finite data only, never manufacture a route-ready observation."""
    _decode(encode_record(packet), DeploymentPacket)
    release, capsule = packet.release, packet.outage
    validate(release, targets)
    if (
        capsule.release_sha256 != digest(release)
        or capsule.execution_id != release.execution_id
        or capsule.nonce != release.nonce
        or capsule.route.route_id != release.recovery.independent_route_id
        or tuple(step.step for step in capsule.transitions) != OUTAGE_STEPS
        or not release.not_before_ns <= capsule.not_before_ns < capsule.deadline_ns <= release.deadline_ns
        or capsule.recovery_deadline_ns != release.recovery.deadline_ns
    ):
        raise InvalidAction("outage-release-binding")
    _hashes(
        capsule.release_sha256, capsule.route.epoch_sha256,
        capsule.route.policy_sha256, capsule.route.credential_sha256,
        packet.store_source_sha256, packet.outage_source_sha256,
        packet.semantic_factory_source_sha256, packet.root_helper_sha256,
        packet.root_profile_sha256, packet.sudo_sha256,
    )
    for ordinal, transition in enumerate(capsule.transitions):
        _hashes(transition.before_sha256, transition.after_sha256)
        if transition.before_sha256 == transition.after_sha256:
            raise InvalidAction("outage-transition-no-change")
        if ordinal and capsule.transitions[ordinal - 1].after_sha256 != transition.before_sha256:
            raise InvalidAction("outage-transition-disconnected")


def packet_digest(packet: DeploymentPacket) -> str:
    return hashlib.sha256(encode_record(packet)).hexdigest()


def decode_packet(raw: bytes, targets: tuple[Target, ...]) -> DeploymentPacket:
    packet = _decode(raw, DeploymentPacket)
    validate_packet(packet, targets)
    return packet


def decode_recovery(raw: bytes, packet: DeploymentPacket, targets: tuple[Target, ...]) -> RecoveryCommand:
    """Bound exact independent command; authentication and one-use custody remain required.

    A typed cancel records a stop request; it does not release the target. That
    requires source-specific cessation and retained newest-state reconciliation.
    No database restore operation exists in this interface.
    """
    validate_packet(packet, targets)
    command = _decode(raw, RecoveryCommand)
    _hashes(command.packet_sha256, command.route_epoch_sha256, command.expected_observation_sha256)
    if (
        command.packet_sha256 != packet_digest(packet)
        or command.execution_id != packet.release.execution_id
        or command.route_epoch_sha256 != packet.outage.route.epoch_sha256
        or not command.command_nonce
        or len(command.command_nonce) > 128
        or not command.command_nonce.isascii()
        or not command.command_nonce.isalnum()
        or not packet.outage.not_before_ns <= command.not_before_ns < command.deadline_ns
        <= packet.outage.recovery_deadline_ns
        or (command.operation == "held-restore" and packet.release.recovery.scope != "held-restore")
    ):
        raise InvalidAction("independent-recovery-binding")
    return command
