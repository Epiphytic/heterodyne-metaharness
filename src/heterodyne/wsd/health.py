"""Bounded Plan 8 health projection. Readers never open a WSD journal or an effect database."""

import math
import os
import stat
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.agents.base import write_private

MAX_HEALTH = 256 * 1024
CODES = (
    "unavailable",
    "unknown",
    "stale",
    "recovery_failed",
    "recovery_stalled",
    "wake_overdue",
    "effect_hold",
)
Truth = Literal["true", "false", "unknown"]


class Origin(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    count: int
    first: str | None
    last: str | None
    truncated: bool
    completed_at: float | None = None


class HealthSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: Literal[1]
    installation_id: str
    generation: str
    process_id: str
    boot_id: str
    utc: float
    monotonic: float
    sequence: int
    timer_owner: Literal["internal", "systemd"]
    pickup_seconds: float
    reconcile_seconds: float
    recovery: Literal["in_progress", "complete", "failed"]
    recovery_started: float
    listener_ready: bool
    listener_ready_at: float | None
    last_pickup: float | None
    last_reconcile: float | None
    effect_state: Literal["ready", "unready", "error"]
    lanes: dict[str, dict[str, dict[str, Origin]]]
    snapshot_error: bool = False
    effect_observed_at: float = 0
    effect_revision: int = 0
    effect_backup_state: str = "unavailable"
    effect_held: int = 0
    effect_uncertain: int = 0
    effect_oldest_age: float | None = None


def encode(snapshot: HealthSnapshot) -> bytes:
    invalid = (
        len(snapshot.lanes) > 128
        or any(len(name.encode()) > 128 for name in snapshot.lanes)
        or any(set(kinds) - {"scheduled-pickup", "scheduled-reconcile"} for kinds in snapshot.lanes.values())
        or any(
            set(origins) - {"queued", "running", "latest"}
            for kinds in snapshot.lanes.values()
            for origins in kinds.values()
        )
    )
    payload = msgspec.json.encode(snapshot)
    if invalid or len(payload) > MAX_HEALTH:
        snapshot = msgspec.structs.replace(snapshot, lanes={}, snapshot_error=True)
        payload = msgspec.json.encode(snapshot)
    if len(payload) > MAX_HEALTH:
        raise ValueError("health identity exceeds bound")
    return payload


def publish(path: Path, snapshot: HealthSnapshot) -> None:
    write_private(path, encode(snapshot).decode())


def read(path: Path, installation: str, generation: str) -> HealthSnapshot:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_mode & 0o077
            or st.st_size > MAX_HEALTH
        ):
            raise ValueError("health file outside private bound")
        payload = os.read(fd, MAX_HEALTH + 1)
        if len(payload) > MAX_HEALTH:
            raise ValueError("health size")
    finally:
        os.close(fd)
    result = msgspec.json.decode(payload, type=HealthSnapshot)
    if (
        not all(
            math.isfinite(value)
            for value in (result.utc, result.monotonic, result.pickup_seconds, result.reconcile_seconds)
        )
        or result.sequence < 1
        or result.monotonic < 0
        or result.recovery_started < 0
        or not 1 <= result.pickup_seconds <= 86400
        or not 1 <= result.reconcile_seconds <= 86400
    ):
        raise ValueError("health scalar bounds")
    instants = (
        result.recovery_started,
        result.listener_ready_at,
        result.last_pickup,
        result.last_reconcile,
        result.effect_observed_at,
    )
    if (
        any(value is not None and (not math.isfinite(value) or value < 0) for value in instants)
        or result.effect_revision < 0
        or not 0 <= result.effect_held <= 8
        or not 0 <= result.effect_uncertain <= 9
        or (result.effect_oldest_age is not None
            and (not math.isfinite(result.effect_oldest_age) or result.effect_oldest_age < 0))
        or any(
            not 0 <= origin.count <= (1 << 64) - 1
            or any(value is not None and len(value) > 36 for value in (origin.first, origin.last))
            or (
                origin.completed_at is not None
                and (not math.isfinite(origin.completed_at) or origin.completed_at < 0)
            )
            for kinds in result.lanes.values()
            for phases in kinds.values()
            for origin in phases.values()
        )
        or msgspec.json.decode(encode(result), type=HealthSnapshot) != result
    ):
        raise ValueError("health projection bounds")
    if (result.installation_id, result.generation) != (installation, generation):
        raise ValueError("health identity")
    return result


def predicates(
    snapshot: HealthSnapshot | None,
    *,
    utc: float,
    monotonic: float,
    prior_process: str | None = None,
    prior_sequence: int | None = None,
    sequence_seen_at: float | None = None,
) -> dict[str, Truth]:
    values: dict[str, Truth] = {code: "unknown" for code in CODES}
    values["unavailable"] = "true" if snapshot is None else "false"
    if snapshot is None:
        return values
    age = utc - snapshot.utc
    sane = math.isfinite(age) and math.isfinite(monotonic) and age >= -30 and monotonic >= 0
    values["unknown"] = "true" if snapshot.snapshot_error or not sane else "false"
    if not sane:
        return values
    unchanged = (
        snapshot.process_id == prior_process
        and snapshot.sequence == prior_sequence
        and sequence_seen_at is not None
        and monotonic - sequence_seen_at > 90
    )
    values["stale"] = "true" if age > 90 or unchanged else "false"
    values["recovery_failed"] = "true" if snapshot.recovery == "failed" else "false"
    # Writer monotonic instants can only be compared to each other; elapsed age is UTC here.
    recovering_age = snapshot.monotonic - snapshot.recovery_started + max(0, age)
    values["recovery_stalled"] = (
        "true" if snapshot.recovery == "in_progress" and recovering_age > 600 else "false"
    )
    effect_age = utc - snapshot.effect_observed_at
    if 0 <= effect_age <= 90 and not snapshot.snapshot_error:
        if snapshot.effect_state == "error":
            values["effect_hold"] = "true"  # fresh owner refusal, not an invented zero count
        elif snapshot.effect_state == "ready":
            values["effect_hold"] = "true" if snapshot.effect_held or snapshot.effect_uncertain else "false"
    if snapshot.timer_owner == "internal":
        values["wake_overdue"] = "false"
    elif (
        snapshot.recovery == "complete" and snapshot.listener_ready and snapshot.listener_ready_at is not None
    ):
        now = snapshot.monotonic + max(0, age)
        overdue = any(
            now - max(snapshot.listener_ready_at, last or snapshot.listener_ready_at) > 2 * cadence + 10
            for last, cadence in (
                (snapshot.last_pickup, snapshot.pickup_seconds),
                (snapshot.last_reconcile, snapshot.reconcile_seconds),
            )
        )
        values["wake_overdue"] = "true" if overdue else "false"
    return values
