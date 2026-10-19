"""Closed installation facts for external scheduling; data is not same-UID authority."""

import os
import re
import stat
from pathlib import Path
from uuid import UUID

import msgspec

from heterodyne.wsd.ids import SLUG
from heterodyne.wsd.maintenance_window import Window
from heterodyne.wsd.maintenance_window import validate as validate_window
from heterodyne.wsd.settings import WsdSettings

MANIFEST_NAME = "schedule-manifest.json"
REGISTRY = frozenset({"pickup", "reconcile", "babysitter", "backup_bundle"})
HEX = re.compile(r"[0-9a-f]{64}")


class ManifestInvalid(ValueError):
    """Installed scheduling facts are missing, incompatible or outside their bounds."""


class Location(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    path: str
    device: int
    inode: int


class ScheduleManifest(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: int
    installation_id: str
    generation: str
    uid: int
    config: Location
    state: Location
    queue: Location
    release_hash: str
    unit_hash: str
    backstop_seconds: int
    reconcile_seconds: int
    jobs: tuple[str, ...]
    workstreams: tuple[str, ...]
    backup_owner_hash: str | None = None
    maintenance_window: Window | None = None


def location(path: Path) -> Location:
    canonical = path.resolve(strict=True)
    info = canonical.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ManifestInvalid("installed root is not a directory")
    return Location(str(canonical), info.st_dev, info.st_ino)


def validate(manifest: ScheduleManifest, settings: WsdSettings) -> None:
    if manifest.maintenance_window is not None:
        validate_window(manifest.maintenance_window, manifest.installation_id, manifest.generation)
    for value in (manifest.installation_id, manifest.generation):
        if str(UUID(value)) != value:
            raise ManifestInvalid("noncanonical manifest identity")
    if (manifest.version != 1 or manifest.uid != os.geteuid()
            or not HEX.fullmatch(manifest.release_hash) or not HEX.fullmatch(manifest.unit_hash)
            or manifest.jobs != tuple(sorted(set(manifest.jobs)))
            or not set(manifest.jobs) <= REGISTRY
            or manifest.workstreams != tuple(sorted(set(manifest.workstreams)))
            or len(manifest.workstreams) > 128
            or any(not SLUG.fullmatch(name) for name in manifest.workstreams)
            or manifest.backstop_seconds != settings.backstop_seconds
            or manifest.reconcile_seconds != settings.reconcile_seconds
            or not 1 <= manifest.backstop_seconds <= 86400
            or not 1 <= manifest.reconcile_seconds <= 86400
            or settings.config_dir is None
            or manifest.config != location(settings.config_dir)
            or manifest.state != location(settings.state_dir)
            or manifest.queue != location(settings.btq_checkout)
            or manifest.workstreams != tuple(sorted(ws.name for ws in settings.workstreams))
            or (manifest.backup_owner_hash is not None and not HEX.fullmatch(manifest.backup_owner_hash))
            or ("backup_bundle" in manifest.jobs and manifest.backup_owner_hash is None)):
        raise ManifestInvalid("manifest does not match installed scheduling facts")


def load(settings: WsdSettings) -> ScheduleManifest:
    path = settings.state_dir / MANIFEST_NAME
    # I/O failures remain restartable; only malformed installed facts are EX_CONFIG.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 65536):
            raise ManifestInvalid("manifest is not a bounded private regular file")
        with os.fdopen(fd, "rb", closefd=False) as source:
            manifest = msgspec.json.decode(source.read(65537), type=ScheduleManifest)
        validate(manifest, settings)
        return manifest
    except (ValueError, msgspec.DecodeError) as exc:
        raise ManifestInvalid("invalid scheduling manifest") from exc
    finally:
        os.close(fd)
